import paho.mqtt.client as mqtt
import serial
import time
from datetime import datetime
import json
import logging
import sys
import os
from typing import Union

# --- Configuration ---
MQTT_BROKER_HOST = "157.173.101.159"
MQTT_BROKER_PORT = 1883
MQTT_TOPIC = "relay/schedule"
# Read Arduino port from environment variable or use default
SERIAL_PORT = os.environ.get("ARDUINO_SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUDRATE = 9600
SERIAL_TIMEOUT = 1 # Serial read timeout
WRITE_TIMEOUT = 1 # Serial write timeout (added)
CHECK_INTERVAL_SECONDS = 1
SERIAL_RECONNECT_DELAY_SECONDS = 10 # How often to retry serial connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global State ---
current_schedule = {"on_time": None, "off_time": None}
arduino: Union[serial.Serial, None] = None # Type hint added
last_command_sent_minute = None
last_known_state = None # '0' or '1' or None
last_serial_connect_attempt = 0 # Track time of last connection attempt

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info(f"Connected to MQTT Broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        try:
            client.subscribe(MQTT_TOPIC)
            logging.info(f"Subscribed to topic: {MQTT_TOPIC}")
        except Exception as e:
            logging.error(f"Error subscribing to topic {MQTT_TOPIC}: {e}")
    else:
        logging.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_message(client, userdata, msg):
    global current_schedule, last_command_sent_minute
    try:
        payload = msg.payload.decode("utf-8")
        logging.info(f"Received message on topic '{msg.topic}': {payload}")
        schedule_data = json.loads(payload)

        if isinstance(schedule_data, dict) and "on_time" in schedule_data and "off_time" in schedule_data:
            # Validate time format (basic check)
            try:
                datetime.strptime(schedule_data["on_time"], "%H:%M")
                datetime.strptime(schedule_data["off_time"], "%H:%M")
                # Use a lock or simply overwrite (assuming single thread access for schedule update)
                current_schedule["on_time"] = schedule_data["on_time"]
                current_schedule["off_time"] = schedule_data["off_time"]
                logging.info(f"Updated schedule: ON at {current_schedule['on_time']}, OFF at {current_schedule['off_time']}")
                # Reset last command minute to apply new schedule immediately if needed
                last_command_sent_minute = None
                last_known_state = None # Reset known state as schedule changed
            except ValueError:
                 logging.error(f"Invalid time format in schedule: {payload}. Use HH:MM.")
        else:
            logging.warning(f"Invalid schedule format received: {payload}")

    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from payload: {msg.payload.decode('utf-8')}")
    except Exception as e:
        logging.error(f"Error processing message: {e}", exc_info=True)

# --- Serial Communication (Modified for Reconnection) ---
def setup_serial(port: str, baudrate: int, timeout: int, write_timeout: int) -> Union[serial.Serial, None]:
    """Initializes and returns the serial connection object. Closes existing if necessary."""
    global arduino, last_known_state
    # Close existing port if open
    if arduino and arduino.is_open:
        try:
            arduino.close()
            logging.info(f"Closed existing serial port {arduino.port}")
        except Exception as e:
            logging.warning(f"Error closing existing serial port: {e}")
    arduino = None # Ensure it's None before attempting connection
    last_known_state = None # State is unknown after disconnection/reconnection

    logging.info(f"Attempting to connect to Arduino on {port} at {baudrate} baud...")
    try:
        ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout, write_timeout=write_timeout)
        # Note: Opening the port might reset the Arduino, hence the sleep.
        time.sleep(2)  # Allow time for Arduino reset and initialization
        if ser.is_open: # Double check
            logging.info(f"Serial connection established on {port}.")
            # Optional: Query initial state here if Arduino supports it
            # try:
            #     ser.write(b'?') # Example query character
            #     response = ser.readline().decode().strip()
            #     if response in ['0', '1']:
            #         last_known_state = response
            #         logging.info(f"Queried initial Arduino state: {last_known_state}")
            # except Exception as query_e:
            #     logging.warning(f"Could not query initial Arduino state: {query_e}")
            return ser
        else:
            logging.error(f"Serial port {port} opened but status is not 'is_open'.")
            return None
    except serial.SerialException as e:
        # Don't log stack trace for common permission/not found errors
        if "Permission denied" in str(e) or "No such file or directory" in str(e):
             logging.error(f"Failed to connect to serial port {port}: {e}")
             logging.error("Ensure device is connected, port is correct, and permissions are set (e.g., user in 'dialout' group).")
             logging.error(f"To specify a different port, set the ARDUINO_SERIAL_PORT environment variable.")
        else:
             logging.error(f"Serial connection error on {port}: {e}", exc_info=True)
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during serial setup: {e}", exc_info=True)
        return None

def send_to_arduino(command: str):
    """Sends a command ('0' or '1') to the Arduino via serial. Handles errors and signals for reconnection."""
    global arduino, last_known_state
    if not arduino or not arduino.is_open:
        logging.warning("Serial port not available or not open. Cannot send command.")
        # Do not try to reconnect here, main loop handles it.
        return # Signal main loop to attempt reconnect

    try:
        bytes_written = arduino.write(command.encode('utf-8'))
        arduino.flush() # Ensure data is sent
        logging.info(f"Sent command '{command}' to Arduino ({bytes_written} bytes).")
        last_known_state = command # Update known state only on successful send
    except serial.SerialTimeoutException as e:
         logging.error(f"Serial write timeout on port {arduino.port}: {e}")
         # Treat timeout as a potential disconnection
         close_serial_safely()
         arduino = None # Signal main loop to reconnect
    except serial.SerialException as e:
        logging.error(f"Serial write error on port {arduino.port}: {e}. Signaling for reconnection.")
        close_serial_safely()
        arduino = None # Signal main loop to reconnect
    except Exception as e:
         logging.error(f"Unexpected error sending command: {e}", exc_info=True)
         close_serial_safely()
         arduino = None # Signal main loop to reconnect

def close_serial_safely():
    """Closes the global serial port safely."""
    global arduino
    if arduino and arduino.is_open:
        port = arduino.port
        try:
            arduino.close()
            logging.info(f"Closed serial port {port} due to error or shutdown.")
        except Exception as e:
            logging.warning(f"Error closing serial port {port}: {e}")
    arduino = None # Ensure it's marked as None

# --- Main Loop (Modified for Reconnection) ---
def main():
    global arduino, last_command_sent_minute, last_known_state, last_serial_connect_attempt

    # Initial Serial Setup (Attempt 1)
    arduino = setup_serial(SERIAL_PORT, SERIAL_BAUDRATE, SERIAL_TIMEOUT, WRITE_TIMEOUT)
    last_serial_connect_attempt = time.time()
    if not arduino:
        logging.warning("Initial serial connection failed. Will retry periodically.")
        # Do not exit, allow MQTT to connect and retries to happen

    # Setup MQTT Client
    client_id = f"subscriber_{datetime.now().timestamp()}"
    client = mqtt.Client(client_id=client_id)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
    except Exception as e:
        logging.error(f"Failed to connect to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}: {e}")
        # Close serial if it happened to be open
        close_serial_safely()
        sys.exit("Exiting due to MQTT connection failure.")

    client.loop_start() # Start network loop in background thread

    logging.info("Starting main control loop...")
    try:
        while True:
            now = time.time()
            now_str = datetime.now().strftime("%H:%M")

            # --- Serial Connection Check and Reconnect --- 
            if not arduino or not arduino.is_open:
                if now - last_serial_connect_attempt > SERIAL_RECONNECT_DELAY_SECONDS:
                    logging.info("Serial disconnected. Attempting reconnect...")
                    last_serial_connect_attempt = now
                    arduino = setup_serial(SERIAL_PORT, SERIAL_BAUDRATE, SERIAL_TIMEOUT, WRITE_TIMEOUT)
                    if arduino:
                         logging.info("Successfully reconnected to serial port.")
                         last_command_sent_minute = None # Reset to allow immediate command send if needed
                         # last_known_state is reset in setup_serial
                    else:
                         logging.warning(f"Serial reconnection failed. Will retry in {SERIAL_RECONNECT_DELAY_SECONDS}s.")
                else:
                     # Optional: Log that we are waiting to retry serial
                     # logging.debug("Serial disconnected, waiting for reconnect interval.")
                     pass # Do nothing until reconnect delay passes

            # --- Schedule Logic (Only run if serial connected) ---
            if arduino and arduino.is_open:
                # Check ON condition
                if current_schedule["on_time"] == now_str:
                    if last_command_sent_minute != now_str: # Check if command for this minute already sent
                        if last_known_state != '1':
                            logging.info(f"Time {now_str} matches ON schedule ({current_schedule['on_time']}). Turning ON.")
                            send_to_arduino('1') # This might set arduino to None if it fails
                            if arduino: # Check if send_to_arduino succeeded
                                last_command_sent_minute = now_str
                        else:
                            # logging.debug(f"Time {now_str} matches ON schedule, but state is already '1'.")
                            last_command_sent_minute = now_str # Still update minute check

                # Check OFF condition
                elif current_schedule["off_time"] == now_str:
                    if last_command_sent_minute != now_str: # Check if command for this minute already sent
                        if last_known_state != '0':
                            logging.info(f"Time {now_str} matches OFF schedule ({current_schedule['off_time']}). Turning OFF.")
                            send_to_arduino('0') # This might set arduino to None if it fails
                            if arduino: # Check if send_to_arduino succeeded
                                last_command_sent_minute = now_str
                        else:
                            # logging.debug(f"Time {now_str} matches OFF schedule, but state is already '0'.")
                            last_command_sent_minute = now_str # Still update minute check

            # --- Loop Delay ---
            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received. Shutting down.")
    except Exception as e:
        logging.error(f"An error occurred in the main loop: {e}", exc_info=True)
    finally:
        logging.info("Stopping MQTT loop.")
        client.loop_stop()
        logging.info("Disconnecting MQTT client.")
        try:
             client.disconnect()
        except Exception as e:
             logging.warning(f"Error disconnecting MQTT client: {e}")

        logging.info("Closing serial port.")
        close_serial_safely()

        logging.info("Shutdown complete.")

if __name__ == "__main__":
    main() 