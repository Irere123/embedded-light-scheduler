import paho.mqtt.client as mqtt
import serial
import time
import json
import logging
import os
from datetime import datetime
import sys

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Serial port configuration (adjust '/dev/ttyUSB0' as needed)
SERIAL_PORT = os.getenv('SERIAL_PORT', '/dev/ttyUSB0')
BAUD_RATE = int(os.getenv('BAUD_RATE', 9600))
SERIAL_TIMEOUT = 1 # seconds

# MQTT Configuration (should match the backend server)
MQTT_BROKER_HOST = os.getenv('MQTT_BROKER_HOST', '157.173.101.159')
MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', 1883))
MQTT_TOPIC_SCHEDULE = os.getenv('MQTT_SCHEDULE_TOPIC', 'relay/set_schedule')
MQTT_CLIENT_ID = "arduino_subscriber"

# --- State Variables ---
current_schedule = {
    "on_time": None, # Format "HH:MM"
    "off_time": None # Format "HH:MM"
}
last_command_sent = None # '1' or '0'
ser = None # Serial object

# --- Serial Communication Functions ---
def connect_serial():
    global ser
    while True:
        try:
            logging.info(f"Attempting to connect to serial port {SERIAL_PORT} at {BAUD_RATE} baud...")
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=SERIAL_TIMEOUT)
            logging.info(f"Successfully connected to serial port {SERIAL_PORT}.")
            time.sleep(2) # Allow time for Arduino to reset after connection
            # Optional: read initial message from Arduino
            # initial_message = ser.readline().decode().strip()
            # if initial_message:
            #    logging.info(f"Arduino says: {initial_message}")
            return True
        except serial.SerialException as e:
            logging.error(f"Serial connection error on {SERIAL_PORT}: {e}. Retrying in 5 seconds...")
            ser = None
            time.sleep(5)
        except Exception as e:
            logging.error(f"An unexpected error occurred during serial connection: {e}. Retrying in 5 seconds...")
            ser = None
            time.sleep(5)

def send_serial_command(command):
    global last_command_sent, ser
    if command == last_command_sent:
        # logging.debug(f"Command '{command}' is the same as the last command sent. Skipping.")
        return # Avoid sending redundant commands

    if ser and ser.is_open:
        try:
            logging.info(f"Sending command '{command}' to {SERIAL_PORT}")
            ser.write(command.encode('ascii')) # Send '1' or '0' as bytes
            ser.flush() # Ensure data is sent
            last_command_sent = command
            # Optional: Read acknowledgment from Arduino
            # ack = ser.readline().decode().strip()
            # if ack:
            #    logging.info(f"Arduino acknowledged: {ack}")
            # else:
            #    logging.warning("No acknowledgment received from Arduino.")
            return True
        except serial.SerialException as e:
            logging.error(f"Serial write error: {e}. Attempting to reconnect...")
            ser.close()
            ser = None
            connect_serial() # Try to reconnect immediately
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred during serial write: {e}")
            return False
    else:
        logging.warning("Serial port not available or not open. Cannot send command.")
        if not ser:
            connect_serial() # Try to reconnect if serial object doesn't exist
        return False

# --- MQTT Callback Functions ---
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logging.info(f"Connected to MQTT Broker: {MQTT_BROKER_HOST}")
        # Subscribe to the schedule topic upon connection
        client.subscribe(MQTT_TOPIC_SCHEDULE)
        logging.info(f"Subscribed to topic: {MQTT_TOPIC_SCHEDULE}")
    else:
        logging.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, rc, properties=None):
    logging.warning(f"Disconnected from MQTT Broker, return code {rc}. Attempting reconnection...")
    # Note: The Paho client handles automatic reconnection by default if loop_start/loop_forever is used

def on_message(client, userdata, msg):
    global current_schedule
    logging.info(f"Received message on topic '{msg.topic}': {msg.payload.decode()}")
    if msg.topic == MQTT_TOPIC_SCHEDULE:
        try:
            payload_str = msg.payload.decode('utf-8')
            new_schedule = json.loads(payload_str)

            # Basic validation of the received schedule
            if isinstance(new_schedule, dict) and \
               'on_time' in new_schedule and 'off_time' in new_schedule and \
               isinstance(new_schedule['on_time'], str) and \
               isinstance(new_schedule['off_time'], str):

                 # Further validation (HH:MM format)
                 def is_valid_time_format(time_str):
                     try:
                         hours, minutes = map(int, time_str.split(':'))
                         return 0 <= hours <= 23 and 0 <= minutes <= 59
                     except (ValueError, TypeError):
                         return False

                 if is_valid_time_format(new_schedule['on_time']) and is_valid_time_format(new_schedule['off_time']):
                     if new_schedule['on_time'] != new_schedule['off_time']:
                        logging.info(f"Updating schedule: ON at {new_schedule['on_time']}, OFF at {new_schedule['off_time']}")
                        current_schedule = new_schedule
                        # Force immediate check after receiving a new schedule
                        check_schedule_and_send_command()
                     else:
                         logging.warning("Received schedule where ON time equals OFF time. Ignoring.")
                 else:
                     logging.warning(f"Received schedule with invalid time format: {payload_str}. Ignoring.")
            else:
                logging.warning(f"Received invalid schedule format: {payload_str}. Ignoring.")
        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON schedule: {msg.payload.decode()}")
        except Exception as e:
            logging.error(f"Error processing received message: {e}")

# --- Scheduling Logic ---
def check_schedule_and_send_command():
    global last_command_sent
    if not current_schedule["on_time"] or not current_schedule["off_time"]:
        # logging.debug("No valid schedule set yet.")
        return

    try:
        now_str = datetime.now().strftime("%H:%M")
        on_time_str = current_schedule["on_time"]
        off_time_str = current_schedule["off_time"]

        # logging.debug(f"Checking time: Now={now_str}, ON={on_time_str}, OFF={off_time_str}, LastCmd={last_command_sent}")

        # Determine the desired state based on time comparison
        # This handles overnight schedules correctly (e.g., ON 22:00, OFF 06:00)
        on_time_dt = datetime.strptime(on_time_str, "%H:%M").time()
        off_time_dt = datetime.strptime(off_time_str, "%H:%M").time()
        now_dt = datetime.now().time()

        is_on_period = False
        if on_time_dt < off_time_dt: # Simple case: ON and OFF on the same day
            is_on_period = on_time_dt <= now_dt < off_time_dt
        else: # Overnight case: ON today, OFF tomorrow
            is_on_period = now_dt >= on_time_dt or now_dt < off_time_dt

        target_command = '1' if is_on_period else '0'

        # log state change detection
        if target_command != last_command_sent:
            logging.info(f"Time check: Now={now_str}, Schedule ON={on_time_str}, OFF={off_time_str}. Target state: {'ON' if target_command == '1' else 'OFF'}")

        # Send command if the state needs to change
        if target_command != last_command_sent:
            send_serial_command(target_command)

    except Exception as e:
        logging.error(f"Error during schedule check: {e}")

# --- Main Execution ---
if __name__ == "__main__":
    # Initial serial connection attempt
    if not connect_serial():
        logging.error("Failed to establish initial serial connection. Exiting.")
        sys.exit(1)

    # Setup MQTT Client
    mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    # Connect to MQTT Broker (with retry logic inherent in paho-mqtt)
    try:
        logging.info(f"Connecting to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
    except Exception as e:
        logging.error(f"Initial MQTT connection failed: {e}. The client will attempt to reconnect automatically.")

    # Start the MQTT network loop in a non-blocking way
    mqtt_client.loop_start()

    logging.info("MQTT Subscriber running. Waiting for schedule and checking time...")

    # Main loop to periodically check the schedule
    try:
        while True:
            check_schedule_and_send_command()
            # Check every 30 seconds (adjust as needed for responsiveness vs resources)
            # A shorter interval makes it react faster to the exact minute change.
            time.sleep(30)
    except KeyboardInterrupt:
        logging.info("Subscriber stopped manually.")
    finally:
        logging.info("Cleaning up...")
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logging.info("MQTT client disconnected.")
        if ser and ser.is_open:
            # Optionally send a final OFF command?
            # send_serial_command('0')
            ser.close()
            logging.info("Serial port closed.")
        logging.info("Cleanup complete. Exiting.") 