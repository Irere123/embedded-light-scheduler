import asyncio
import websockets
import paho.mqtt.client as mqtt
import json
import logging
from datetime import datetime
import signal # Added for graceful shutdown

# --- Configuration ---
MQTT_BROKER_HOST = "157.173.101.159"
MQTT_BROKER_PORT = 1883
MQTT_TOPIC = "relay/schedule"
WEBSOCKET_HOST = "0.0.0.0"  # Listen on all available interfaces
WEBSOCKET_PORT = 8765

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Global MQTT Client ---
mqtt_client = None
mqtt_connected = False
mqtt_connection_lock = asyncio.Lock() # Lock to prevent race conditions during connect/reconnect

# --- MQTT Callbacks ---
def on_connect(client, userdata, flags, rc):
    """Callback for when the client connects to the MQTT broker."""
    global mqtt_connected
    if rc == 0:
        logging.info(f"Connected successfully to MQTT Broker: {MQTT_BROKER_HOST}")
        mqtt_connected = True
    else:
        logging.error(f"Failed to connect to MQTT Broker, return code {rc}")
        mqtt_connected = False

def on_disconnect(client, userdata, rc):
    """Callback for when the client disconnects from the MQTT broker."""
    global mqtt_connected
    logging.warning(f"Disconnected from MQTT Broker with result code {rc}. Attempting reconnection...")
    mqtt_connected = False
    # Note: paho-mqtt handles automatic reconnection if loop_start() is used

# --- MQTT Publishing (Modified) ---
async def publish_to_mqtt(client: mqtt.Client, message_payload: str) -> bool:
    """Publishes a message using the provided client instance. Returns True on success, False on failure."""
    global mqtt_connected
    async with mqtt_connection_lock: # Ensure connection status doesn't change mid-publish
        if not client or not mqtt_connected:
            logging.error("MQTT client not connected. Cannot publish.")
            return False
    try:
        result = client.publish(MQTT_TOPIC, message_payload)
        result.wait_for_publish(timeout=5.0) # Wait for publish confirmation (optional, adds slight block)
        if result.is_published():
            logging.info(f"Published to MQTT topic '{MQTT_TOPIC}': {message_payload}")
            return True
        else:
            logging.error(f"Failed to publish to MQTT topic '{MQTT_TOPIC}'. Mid = {result.mid}")
            # Attempt to reconnect might be needed here if is_published() is False
            return False
    except ValueError as e: # Raised if disconnected during publish
         logging.error(f"MQTT publish error (ValueError - likely disconnected): {e}")
         mqtt_connected = False # Mark as disconnected
         return False
    except Exception as e:
        logging.error(f"Failed to publish to MQTT: {e}", exc_info=True)
        return False


# --- WebSocket Handler (Modified) ---
async def handle_connection(websocket: websockets.WebSocketServerProtocol, path: str):
    """Handles incoming WebSocket connections and messages."""
    global mqtt_client # Need access to the global client
    remote_address = websocket.remote_address
    logging.info(f"WebSocket client connected: {remote_address}")
    try:
        async for message in websocket:
            logging.info(f"Received message from {remote_address}: {message}")
            response_status = "error"
            response_message = "An unexpected error occurred."
            try:
                schedule_data = json.loads(message)
                # Basic validation
                if isinstance(schedule_data, dict) and "on_time" in schedule_data and "off_time" in schedule_data:
                    # TODO: Add time format validation here if desired
                    # Publish to MQTT
                    publish_successful = await publish_to_mqtt(mqtt_client, message)

                    if publish_successful:
                        response_status = "success"
                        response_message = "Schedule received and published to MQTT."
                    else:
                        response_status = "error"
                        response_message = "Schedule received but failed to publish to MQTT."

                    await websocket.send(json.dumps({"status": response_status, "message": response_message}))
                else:
                    logging.warning(f"Invalid message format from {remote_address}: {message}")
                    await websocket.send(json.dumps({"status": "error", "message": "Invalid format. Expected JSON with 'on_time' and 'off_time' keys."}))
            except json.JSONDecodeError:
                logging.error(f"Invalid JSON received from {remote_address}: {message}")
                await websocket.send(json.dumps({"status": "error", "message": "Invalid JSON format."}))
            except Exception as e:
                logging.error(f"Error processing message from {remote_address}: {e}", exc_info=True)
                try:
                   await websocket.send(json.dumps({"status": "error", "message": "Internal server error."}))
                except websockets.exceptions.ConnectionClosed:
                    pass # Client might have already disconnected

    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"WebSocket client disconnected normally: {remote_address}")
    except websockets.exceptions.ConnectionClosedError as e:
        logging.warning(f"WebSocket client connection closed with error: {remote_address} - {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred with client {remote_address}: {e}", exc_info=True)
    finally:
        logging.info(f"WebSocket connection closed for: {remote_address}")


# --- Server Start & MQTT Setup (Modified) ---
async def start_server():
    """Initializes MQTT client and starts the WebSocket server."""
    global mqtt_client
    global mqtt_connected

    stop = asyncio.Future() # Future to signal server stop

    # Setup MQTT Client
    client_id = f"websocket_server_{datetime.now().timestamp()}"
    mqtt_client = mqtt.Client(client_id=client_id)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect

    try:
        logging.info(f"Attempting to connect to MQTT Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        mqtt_client.connect_async(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        mqtt_client.loop_start() # Start network loop in background thread
        # Wait briefly for connection attempt (or use lock/event)
        await asyncio.sleep(2)
        async with mqtt_connection_lock:
             if not mqtt_connected:
                  logging.warning("Initial MQTT connection failed. Server starting, will retry.")

    except Exception as e:
        logging.error(f"Failed to initialize or connect MQTT client: {e}", exc_info=True)
        # Decide if server should run without MQTT? For now, it will.

    # Setup WebSocket Server
    websocket_server = await websockets.serve(handle_connection, WEBSOCKET_HOST, WEBSOCKET_PORT)
    logging.info(f"WebSocket server started on ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}")

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, lambda: stop.set_result(None))
    loop.add_signal_handler(signal.SIGTERM, lambda: stop.set_result(None))

    await stop # Wait until stop signal is received

    # Cleanup
    logging.info("Shutting down servers...")
    websocket_server.close()
    await websocket_server.wait_closed()
    logging.info("WebSocket server stopped.")

    if mqtt_client:
        mqtt_client.loop_stop() # Stop MQTT background thread
        mqtt_client.disconnect()
        logging.info("MQTT client disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        # This is now handled by the signal handlers within start_server
        logging.info("Server shutdown initiated.")
    except Exception as e:
         logging.critical(f"Server failed to run: {e}", exc_info=True) 