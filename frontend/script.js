// --- Configuration ---
const WEBSOCKET_URL = `ws://${window.location.hostname}:8765`; // Assumes server is on same host, change if needed

// --- DOM Elements ---
const scheduleForm = document.getElementById("schedule-form");
const onTimeInput = document.getElementById("onTimeInput");
const offTimeInput = document.getElementById("offTimeInput");
const submitButton = document.getElementById("submit-button");
const connectionStatusDiv = document.getElementById("connection-status");
const responseMessageDiv = document.getElementById("response-message");

// --- WebSocket Logic ---
let websocket = null;
const reconnectInterval = 5000; // Reconnect every 5 seconds
let reconnectTimer = null;

const connectWebSocket = () => {
	if (reconnectTimer) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}

	console.log(`Attempting to connect to WebSocket: ${WEBSOCKET_URL}`);
	setStatus("Connecting to server...", "yellow");
	setSubmitButtonState(false); // Disable form while connecting

	websocket = new WebSocket(WEBSOCKET_URL);

	websocket.onopen = () => {
		console.log("WebSocket connection established.");
		setStatus("Connected", "green");
		setSubmitButtonState(true); // Enable form on successful connection
		if (reconnectTimer) {
			clearTimeout(reconnectTimer);
			reconnectTimer = null;
		}
	};

	websocket.onmessage = (event) => {
		console.log("Message from server:", event.data);
		try {
			const response = JSON.parse(event.data);
			if (response.status === "success") {
				setResponseMessage(response.message, "green");
			} else {
				setResponseMessage(`Server Error: ${response.message}`, "red");
			}
		} catch (error) {
			console.error("Failed to parse server message:", error);
			// Display raw message if not JSON or malformed
			setResponseMessage(`Received: ${event.data}`, "blue");
		}
	};

	websocket.onerror = (error) => {
		console.error("WebSocket Error:", error);
		// Don't set status here, onclose will handle it
	};

	websocket.onclose = (event) => {
		console.log(
			`WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`,
		);
		setStatus("Disconnected. Trying to reconnect...", "red");
		setSubmitButtonState(false);
		websocket = null;
		// Schedule reconnection attempt
		if (!reconnectTimer) {
			reconnectTimer = setTimeout(connectWebSocket, reconnectInterval);
		}
	};
};

// --- UI Helper Functions ---
const setStatus = (message, color) => {
	connectionStatusDiv.textContent = message;
	// Remove old color classes
	connectionStatusDiv.classList.remove(
		"bg-yellow-100",
		"text-yellow-700",
		"bg-green-100",
		"text-green-700",
		"bg-red-100",
		"text-red-700",
	);
	// Add new color class
	switch (color) {
		case "yellow":
			connectionStatusDiv.classList.add("bg-yellow-100", "text-yellow-700");
			break;
		case "green":
			connectionStatusDiv.classList.add("bg-green-100", "text-green-700");
			break;
		case "red":
			connectionStatusDiv.classList.add("bg-red-100", "text-red-700");
			break;
	}
};

const setResponseMessage = (message, color) => {
	responseMessageDiv.textContent = message;
	responseMessageDiv.classList.remove(
		"hidden",
		"bg-green-100",
		"text-green-700",
		"bg-red-100",
		"text-red-700",
		"bg-blue-100",
		"text-blue-700",
	);
	let bgClass = "";
	let textClass = "";
	switch (color) {
		case "green":
			bgClass = "bg-green-100";
			textClass = "text-green-700";
			break;
		case "red":
			bgClass = "bg-red-100";
			textClass = "text-red-700";
			break;
		case "blue": // For general info/raw messages
			bgClass = "bg-blue-100";
			textClass = "text-blue-700";
			break;
	}
	if (bgClass) {
		responseMessageDiv.classList.add(bgClass, textClass);
	}
	// Clear the message after a few seconds
	setTimeout(() => {
		responseMessageDiv.classList.add("hidden");
		responseMessageDiv.textContent = "";
	}, 5000);
};

const setSubmitButtonState = (enabled) => {
	submitButton.disabled = !enabled;
};

// --- Event Handlers ---
const handleSubmit = (event) => {
	event.preventDefault(); // Prevent default form submission

	const onTimeValue = onTimeInput.value;
	const offTimeValue = offTimeInput.value;

	if (!onTimeValue || !offTimeValue) {
		setResponseMessage("Please select both ON and OFF times.", "red");
		return;
	}

	if (!websocket || websocket.readyState !== WebSocket.OPEN) {
		setResponseMessage("Not connected to the server. Please wait.", "red");
		return;
	}

	const schedule = {
		on_time: onTimeValue, // Ensure keys match backend expectation
		off_time: offTimeValue,
	};

	try {
		const message = JSON.stringify(schedule);
		websocket.send(message);
		console.log("Schedule sent:", message);
		setResponseMessage("Sending schedule...", "blue"); // Optimistic UI
		// Server response will update this message via onmessage
	} catch (error) {
		console.error("Error sending schedule:", error);
		setResponseMessage("Error sending schedule. Check console.", "red");
	}
};

// --- Event Listeners ---
scheduleForm.addEventListener("submit", handleSubmit);

// --- Initialisation ---
connectWebSocket(); // Initial connection attempt
