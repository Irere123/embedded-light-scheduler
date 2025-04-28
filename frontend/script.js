const onTimeInput = document.getElementById("on-time");
const offTimeInput = document.getElementById("off-time");
const submitButton = document.getElementById("submit-schedule");
const statusDiv = document.getElementById("status");

// --- WebSocket Configuration ---
// Defaults to ws://localhost:8765 if served locally, or ws://<your-host>:8765 if served from a domain
const wsUrl = `ws://${window.location.hostname || "localhost"}:8765`;
let websocket;
let reconnectTimer = null;
const reconnectInterval = 5000; // 5 seconds

/**
 * Updates the status message display.
 * @param {string} message - The message to display.
 * @param {'success' | 'error' | 'info' | 'warning' | 'default'} type - The type of message (affects styling).
 */
const setStatus = (message, type = "default") => {
	statusDiv.textContent = message;
	let color = "#666"; // Default color
	switch (type) {
		case "success":
			color = "green";
			break;
		case "error":
			color = "red";
			break;
		case "info":
			color = "blue";
			break;
		case "warning":
			color = "orange";
			break;
	}
	statusDiv.style.color = color;
};

/**
 * Enables or disables the submit button.
 * @param {boolean} enabled - Whether the button should be enabled.
 */
const setSubmitButtonState = (enabled) => {
	submitButton.disabled = !enabled;
};

/**
 * Establishes or re-establishes the WebSocket connection.
 */
const connectWebSocket = () => {
	if (websocket && websocket.readyState === WebSocket.OPEN) {
		console.log("WebSocket already open.");
		return;
	}

	// Prevent multiple concurrent connection attempts
	if (reconnectTimer) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}

	console.log(`Attempting to connect to ${wsUrl}...`);
	setStatus("Connecting to server...", "default");
	setSubmitButtonState(false);

	// Explicitly close any previous potentially broken connection
	if (websocket) {
		websocket.close();
	}

	websocket = new WebSocket(wsUrl);

	websocket.onopen = () => {
		console.log("WebSocket connection opened");
		setStatus("Connected to server.", "success");
		setSubmitButtonState(true);
		// Clear any pending reconnect timer upon successful connection
		if (reconnectTimer) {
			clearTimeout(reconnectTimer);
			reconnectTimer = null;
			console.log("Cleared reconnect timer on open.");
		}
	};

	websocket.onmessage = (event) => {
		console.log("Message from server:", event.data);
		// Display server confirmation/status messages
		setStatus(`Server: ${event.data}`, "info");
		// Re-enable button if it was disabled waiting for confirmation
		setSubmitButtonState(true);
	};

	websocket.onerror = (error) => {
		console.error("WebSocket Error:", error);
		// Status is updated in onclose, which always follows onerror
	};

	websocket.onclose = (event) => {
		console.log("WebSocket connection closed:", event.code, event.reason);
		setStatus("Disconnected. Attempting to reconnect...", "warning");
		setSubmitButtonState(false);
		websocket = null; // Clean up the old object

		// Schedule reconnection attempt if not already scheduled
		if (!reconnectTimer) {
			console.log(
				`Scheduling reconnect in ${reconnectInterval / 1000} seconds.`,
			);
			reconnectTimer = setTimeout(connectWebSocket, reconnectInterval);
		}
	};
};

/**
 * Handles the click event for the submit button.
 * Reads time inputs, validates them, and sends the schedule via WebSocket.
 */
const handleSubmitSchedule = () => {
	const onTime = onTimeInput.value;
	const offTime = offTimeInput.value;

	if (!onTime || !offTime) {
		setStatus("Please select both ON and OFF times.", "error");
		return;
	}

	if (onTime === offTime) {
		setStatus("ON time and OFF time cannot be the same.", "error");
		return;
	}

	if (websocket && websocket.readyState === WebSocket.OPEN) {
		const schedule = {
			on_time: onTime,
			off_time: offTime,
		};
		try {
			websocket.send(JSON.stringify(schedule));
			console.log("Sent schedule:", schedule);
			setStatus(
				`Schedule (ON: ${onTime}, OFF: ${offTime}) sent. Waiting for confirmation...`,
				"info",
			);
			setSubmitButtonState(false); // Disable button until server confirms receipt
		} catch (error) {
			console.error("Error sending message:", error);
			setStatus("Error sending schedule. Check console.", "error");
		}
	} else {
		console.error("WebSocket is not connected.");
		setStatus("Error: Not connected to server. Please wait.", "error");
		// Attempt to reconnect immediately if trying to send while disconnected
		if (!reconnectTimer) {
			connectWebSocket();
		}
	}
};

// --- Event Listeners ---
submitButton.addEventListener("click", handleSubmitSchedule);

// --- Initial Connection ---
connectWebSocket();
