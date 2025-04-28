import asyncio
import websockets
import json
import paho.mqtt.client as mqtt
import logging
import os

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

WEBSOCKET_HOST = os.getenv('WEBSOCKET_HOST', '0.0.0.0') # Listen on all available interfaces
WEBSOCKET_PORT = int(os.getenv('WEBSOCKET_PORT', 8765))

# Use the MQTT broker IP from the assignment, but make it configurable via environment variables
MQTT_BROKER_HOST = os.getenv('MQTT_BROKER_HOST', '157.173.101.159')
MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', 1883))
MQTT_TOPIC = os.getenv('MQTT_SCHEDULE_TOPIC', 'relay/set_schedule') # Topic to publish schedule
MQTT_CLIENT_ID = "websocket_scheduler_server"

# Keep track of connected clients (optional, but can be useful)
connected_clients = set()

# --- MQTT Client Setup ---
def on_connect(client, userdata, flags, rc, properties=None): # Adjusted for paho-mqtt v2
    if rc == 0:
        logging.info(f"Connected to MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    else:
        logging.error(f"Failed to connect to MQTT Broker, return code {rc}")

def on_disconnect(client, userdata, rc, properties=None): # Adjusted for paho-mqtt v2
    logging.warning(f"Disconnected from MQTT Broker, return code {rc}")

def on_publish(client, userdata, mid, properties=None): # Adjusted for paho-mqtt v2
    logging.info(f"MQTT Message Published (MID: {mid}) to topic {MQTT_TOPIC}")

mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_publish = on_publish

try:
    logging.info(f"Attempting to connect to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
    mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
    mqtt_client.loop_start() # Start network loop in background thread
except Exception as e:
    logging.error(f"MQTT connection failed: {e}")
    # Decide if the server should exit or run without MQTT
    # For now, let it run and log errors when publishing

# --- WebSocket Handler ---
async def handle_websocket(websocket, path):
    remote_address = websocket.remote_address
    logging.info(f"WebSocket client connected: {remote_address}")
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            logging.info(f"Received message from {remote_address}: {message}")
            try:
                schedule_data = json.loads(message)
                # Basic validation
                if isinstance(schedule_data, dict) and \
                   'on_time' in schedule_data and \
                   'off_time' in schedule_data and \
                   isinstance(schedule_data['on_time'], str) and \
                   isinstance(schedule_data['off_time'], str):

                    # Format validation (HH:MM)
                    def is_valid_time_format(time_str):
                        try:
                            hours, minutes = map(int, time_str.split(':'))
                            return 0 <= hours <= 23 and 0 <= minutes <= 59
                        except ValueError:
                            return False

                    if is_valid_time_format(schedule_data['on_time']) and \
                       is_valid_time_format(schedule_data['off_time']):

                        payload = json.dumps(schedule_data) # Publish the schedule as JSON

                        # Publish to MQTT
                        if mqtt_client.is_connected():
                            result = mqtt_client.publish(MQTT_TOPIC, payload)
                            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                                logging.info(f"Successfully published schedule to {MQTT_TOPIC}: {payload}")
                                await websocket.send(f"Schedule received and published.")
                            else:
                                logging.error(f"Failed to publish schedule to MQTT: {mqtt.error_string(result.rc)}")
                                await websocket.send("Error: Failed to publish schedule via MQTT.")
                        else:
                            logging.error("MQTT client is not connected. Cannot publish schedule.")
                            await websocket.send("Error: Backend MQTT connection issue.")
                    else:
                         logging.warning(f"Invalid time format received: {schedule_data}")
                         await websocket.send("Error: Invalid time format. Use HH:MM.")
                else:
                    logging.warning(f"Invalid schedule format received: {message}")
                    await websocket.send("Error: Invalid message format. Expected JSON with 'on_time' and 'off_time'.")
            except json.JSONDecodeError:
                logging.warning(f"Received non-JSON message: {message}")
                await websocket.send("Error: Invalid message format. Send JSON.")
            except Exception as e:
                logging.error(f"Error processing message: {e}")
                try:
                    await websocket.send("Error: Internal server error.")
                except websockets.exceptions.ConnectionClosed:
                    pass # Client already disconnected

    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"WebSocket client disconnected normally: {remote_address}")
    except websockets.exceptions.ConnectionClosedError as e:
        logging.error(f"WebSocket client connection closed with error: {remote_address} - {e}")
    finally:
        connected_clients.remove(websocket)
        logging.info(f"Client {remote_address} removed. Connected clients: {len(connected_clients)}")

# --- Start Server ---
async def main():
    logging.info(f"Starting WebSocket server on {WEBSOCKET_HOST}:{WEBSOCKET_PORT}")
    server = await websockets.serve(handle_websocket, WEBSOCKET_HOST, WEBSOCKET_PORT)
    logging.info("WebSocket server running.")
    await server.wait_closed()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped manually.")
    finally:
        if mqtt_client.is_connected():
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            logging.info("MQTT client disconnected.") 