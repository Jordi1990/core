"""Bold lock."""
import asyncio
import base64
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import os
import time
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_LOCKED, STATE_UNLOCKED, STATE_UNLOCKING
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    BLE_READ_UUID,
    BLE_WRITE_UUID,
    COMMAND,
    DOMAIN,
    HANDSHAKE_CLIENT_RESPONSE,
    START_HANDSHAKE,
)

_LOGGER = logging.getLogger(__name__)


class BoldBleDevice:
    """Bold device."""

    def __init__(
        self,
        name: str,
        activate_payload: str,
        handshake_key: str,
        handshake_payload: str,
        device_id: str,
        address: str,
        model: str,
    ) -> None:
        """Init."""
        self.name: str = name
        self.model: str = model
        self.activate_payload: str = activate_payload
        self.handshake_key: str = handshake_key
        self.handshake_payload: str = handshake_payload
        self.device_id: str = device_id
        self.address: str = address
        self.ble_device: BLEDevice
        self.available_callback: Callable[[], None]
        self.unavailable_callback: Callable[[], None]

    def on_available(self, available_callback) -> None:
        """On device available callback."""
        self.available_callback = available_callback

    def on_unavailable(self, unavailable_callback) -> None:
        """On device unavailable callback."""
        self.unavailable_callback = unavailable_callback

    def update_advertisement(
        self, ble_device: BLEDevice, ad: AdvertisementData
    ) -> None:
        """Update the device after a ble advertisement."""
        self.ble_device = ble_device
        if callable(self.available_callback):
            self.available_callback()

    def reset_advertisement_state(self):
        """Device unavailable."""
        if callable(self.unavailable_callback):
            self.unavailable_callback()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Perform the setup for Bold locks."""
    entities = []
    boldDevice = hass.data[DOMAIN][config_entry.entry_id]

    entities.append(BoldLock(boldDevice))
    async_add_entities(entities)


class BoldLock(LockEntity):
    """Block lock entity."""

    payload: bytes
    handshake_key: bytes
    activate_payload: bytes

    def __init__(self, device: BoldBleDevice) -> None:
        """Init."""
        self.bold_device = device
        self._state = STATE_LOCKED
        self.set_keys()

        # Homeassistant entity setup
        self._attr_should_poll = False
        self._attr_available = False
        self._attr_unique_id = device.device_id
        self._attr_name = device.name
        self.entity_description = LockEntityDescription(
            key="lock", translation_key="lock"
        )
        self._attr_device_info = DeviceInfo(
            name=device.name,
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="Bold",
            model=device.model,
        )

        # Subscribe to availability callbacks from BLE advertisements
        def set_available() -> None:
            self._attr_available = True
            self.schedule_update_ha_state()

        def set_unavailable() -> None:
            self._attr_available = False
            self.async_write_ha_state()

        self.bold_device.on_available(set_available)
        self.bold_device.on_unavailable(set_unavailable)

    def set_keys(self):
        """Decode the activation and handshake payload and handshake key."""
        self.payload = base64.b64decode(self.bold_device.handshake_payload)
        self.handshake_key = base64.b64decode(self.bold_device.handshake_key)
        self.activate_payload = base64.b64decode(self.bold_device.activate_payload)

    async def set_locked(self, _: datetime) -> None:
        """Set to locked state."""
        self._state = STATE_LOCKED
        self.async_write_ha_state()

    @property
    def is_unlocking(self) -> bool:
        """Return true if lock is unlocking."""
        return self._state == STATE_UNLOCKING

    @property
    def is_locked(self) -> bool:
        """Return true if lock is locked."""
        return self._state == STATE_LOCKED

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock method."""

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock method."""
        self._state = STATE_UNLOCKING
        self.async_write_ha_state()

        start_time = time.time()

        async with BleakClient(self.bold_device.ble_device, timeout=10) as client:
            _LOGGER.debug("Connect done within %d s", time.time() - start_time)
            start_time = time.time()
            ble_command = BoldBleCommand()
            await ble_command.start_listening(client)

            # Initiate handshake
            handshake_response = await ble_command.send(
                client, START_HANDSHAKE, self.payload
            )
            _LOGGER.debug(
                "header: %d, nonce: %s",
                handshake_response.identifier,
                handshake_response.data.hex(),
            )

            # Generate handshake response for lock
            # Check response header code for HANDSHAKE_RESPONSE
            nonce = handshake_response.data[:13]
            server_challenge = handshake_response.data[13:]
            _LOGGER.debug("nonce: %s", nonce.hex())
            _LOGGER.debug("server_challenge: %s", server_challenge.hex())
            cryptor = BoldCryptor(self.handshake_key, nonce)
            encrypted_server_challenge = cryptor.process(server_challenge)
            random_challenge = cryptor.random(8)
            client_response = encrypted_server_challenge + random_challenge
            _LOGGER.debug("client_response: %s", client_response.hex())
            encrypted_client_response = cryptor.process(client_response)

            handshake_response_encrypted = await ble_command.send(
                client, HANDSHAKE_CLIENT_RESPONSE, encrypted_client_response
            )

            # Use encryption with negotiated challenge from now on
            cryptor = BoldCryptor(client_response, nonce)
            handshake_response_decrypted = cryptor.process(
                handshake_response_encrypted.data
            )
            _LOGGER.debug("Handshake response: %s", handshake_response_decrypted.hex())
            activate_payload_encrypted = cryptor.process(self.activate_payload)

            activate_response_encrypted = await ble_command.send(
                client, COMMAND, activate_payload_encrypted
            )
            activate_response = cryptor.process(activate_response_encrypted.data)

            activation_time = int.from_bytes(activate_response[1:3], byteorder="little")
            _LOGGER.debug("activate response: %s", activate_response.hex())
            _LOGGER.debug("Handshake done within %d ", time.time() - start_time)
        self._state = STATE_UNLOCKED
        self.async_write_ha_state()

        # Schedule a callback to set the state back to locked after {activation_time} seconds
        callback = HassJob(self.set_locked, cancel_on_shutdown=True)
        async_call_later(self.hass, timedelta(seconds=activation_time), callback)


class BoldBleCommandResult:
    """BLE Command result."""

    def __init__(self, data: bytearray) -> None:
        """Init."""
        self.data = data[3:]
        self.identifier = data[0]


class BoldBleCommand:
    """Bold ble command."""

    def __init__(self) -> None:
        """Init."""
        self.receiver = NotificationReceiver()

    async def start_listening(self, client: BleakClient):
        """Start listening for notifications."""
        await client.start_notify(BLE_READ_UUID, self.receiver)

    async def send(
        self, client: BleakClient, command_prefix, data: bytes
    ) -> BoldBleCommandResult:
        """Send command to bold lock."""
        payload = b"".join(
            [
                command_prefix.to_bytes(1, "little"),
                len(data).to_bytes(2, "little"),
                data,
            ]
        )
        await client.write_gatt_char(BLE_WRITE_UUID, payload, response=False)
        try:
            await asyncio.wait_for(self.receiver.wait_for_message(), 10)
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout getting command data")
            raise

        result = BoldBleCommandResult(self.receiver.message)
        self.receiver.reset()
        return result


class NotificationReceiver:
    """Receiver for a single notification message."""

    message: bytearray

    def __init__(self) -> None:
        """Init."""
        self.message = bytearray()
        self._event = asyncio.Event()

    def __call__(self, _: Any, data: bytearray) -> None:
        """TBD."""
        self.message = data
        self._event.set()

    async def wait_for_message(self) -> None:
        """Wait for reply."""
        await self._event.wait()

    def reset(self):
        """Reset to receive the next message."""
        self._event.clear()
        self.message = bytearray()


class BoldCryptor:
    """Bold lock BLE encryption routines."""

    def __init__(self, key, nonce) -> None:
        """Init."""
        self.key = key
        self.nonce = nonce
        self.counter = 0

    @staticmethod
    def random(size):
        """Return a randomized sequence."""
        return os.urandom(size)

    def process(self, data):
        """Pass the data through the cryptor."""
        iv = self.nonce + bytes([0, 0, self.counter])
        cipher = Cipher(
            algorithms.AES(self.key), modes.CTR(iv), backend=default_backend()
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(data) + encryptor.finalize()
        self.counter += -(-len(data) // 16)
        return ciphertext
