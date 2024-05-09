"""Constants for the Bold integration."""

DOMAIN = "bold"

OAUTH2_AUTHORIZE = "https://auth.boldsmartlock.com/"
OAUTH2_TOKEN = "https://api.boldsmartlock.com/v2/oauth/token"

BLE_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
BLE_READ_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

START_HANDSHAKE = 0xA0
HANDSHAKE_RESPONSE = 0xA1
HANDSHAKE_CLIENT_RESPONSE = 0xA2
HANDSHAKE_FINISHED_RESPONSE = 0xA3

COMMAND = 0xA4
COMMAND_ACK = 0xA5
