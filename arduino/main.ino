const int relayPin = 7;

void setup() {
  // Initialize serial communication at 9600 bits per second:
  Serial.begin(9600);
  // Set the relay pin as an output:
  pinMode(relayPin, OUTPUT);
  // Ensure the relay starts in the OFF state (HIGH signal for the described
  // module)
  digitalWrite(relayPin, HIGH);
  Serial.println("Arduino Ready. Waiting for commands...");
}

void loop() {
  // Check if data is available to read from serial port
  if (Serial.available()) {
    // Read the incoming byte:
    char cmd = Serial.read();

    // Control the relay based on the command
    if (cmd == '1') {
      digitalWrite(relayPin, LOW); // Turn relay ON (Light ON)
      Serial.println("Received '1': Light ON");
    } else if (cmd == '0') {
      digitalWrite(relayPin, HIGH); // Turn relay OFF (Light OFF)
      Serial.println("Received '0': Light OFF");
    } else {
      // Optional: Handle unexpected commands
      Serial.print("Received unexpected command: ");
      Serial.println(cmd);
    }
  }
  // No delay needed here as we continuously check Serial.available()
}
