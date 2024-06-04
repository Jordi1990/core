"""The Bold integration."""

from __future__ import annotations

import logging

from bold_smart_lock.bold_smart_lock import BoldSmartLock
from home_assistant_bluetooth import BluetoothServiceInfoBleak

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.api import async_discovered_service_info
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow

from . import api
from .const import DOMAIN
from .lock import BoldBleDevice

PLATFORMS: list[Platform] = [Platform.LOCK]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bold from a config entry."""

    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    auth_manager = api.AsyncConfigEntryAuth(
        aiohttp_client.async_get_clientsession(hass), session
    )

    bold_api = BoldSmartLock(auth_manager)
    devices = await bold_api.get_device_permissions()
    address = entry.unique_id
    assert address is not None

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}

    device_details: dict[str, str]
    for device in devices:
        if str(device["id"]) == str(entry.data["device"]["id"]):
            device_details = device

    # Check expiry
    handshake: dict[str, str] = await bold_api.get_device_handshake(
        int(device_details["id"])
    )
    activate_payload: dict[str, str] = await bold_api.get_activate_device_payload(
        int(device_details["id"])
    )

    device = BoldBleDevice(
        device_details["name"],
        activate_payload["payload"],
        handshake["handshakeKey"],
        handshake["payload"],
        device_details["id"],
        address,
        entry.data["device"]["model"],
    )

    hass.data[DOMAIN][entry.entry_id] = device

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a ble callback."""
        device.update_advertisement(service_info.device, service_info.advertisement)

    # We may already have the advertisement, so check for it.
    if service_info := async_find_existing_service_info(hass, address):
        device.update_advertisement(service_info.device, service_info.advertisement)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher(
                {
                    ADDRESS: address,
                }
            ),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def async_find_existing_service_info(
    hass: HomeAssistant, address: str
) -> BluetoothServiceInfoBleak | None:
    """Return the service info for the given local_name and address."""
    for service_info in async_discovered_service_info(hass):
        device = service_info.device
        if device.address == address:
            return service_info
    return None


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
