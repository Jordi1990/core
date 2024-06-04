"""Bold lock."""

from datetime import datetime, timedelta
import logging
import time
from typing import Any

from bold_smart_lock.ble.client import BoldBleDevice

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.api import async_address_present
from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
import homeassistant.util.dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    @callback
    def _async_device_unavailable(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle device not longer being seen by the bluetooth stack."""
        self._attr_available = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.async_on_remove(
            bluetooth.async_track_unavailable(
                self.hass, self._async_device_unavailable, self.bold_device.address
            )
        )
        self.async_on_remove(self.bold_device.register_callback(self.update_state))
        return await super().async_added_to_hass()

    def __init__(self, device: BoldBleDevice) -> None:
        """Init."""
        self.bold_device = device
        self._is_activating = False
        self._activation_end_time = dt_util.utcnow()
        self._attr_should_poll = False
        self._attr_unique_id = f"{device.address}_{device.device_id}"
        self._attr_name = device.name
        self.entity_description = LockEntityDescription(
            key="lock", translation_key="lock"
        )
        self._attr_device_info = DeviceInfo(
            name=device.name,
            serial_number=device.device_id,
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="Bold",
            model=device.model,
        )

    @property
    def available(self) -> bool:
        """Determine if the entity is available."""
        return async_address_present(self.hass, self.bold_device.address)

    @property
    def is_unlocking(self) -> bool:
        """Return true if lock is unlocking."""
        return self._is_activating

    @property
    def is_locked(self) -> bool:
        """Return true if lock is locked."""
        return dt_util.utcnow() >= self._activation_end_time

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock is automatically locked."""

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock method."""
        self._is_activating = True
        self.async_write_ha_state()

        start_time = time.time()
        activation_time = await self.bold_device.activate_lock()
        _LOGGER.debug(
            "Activating took %d seconds, activation time is %d seconds",
            time.time() - start_time,
            activation_time,
        )
        self._is_activating = False
        self._activation_end_time = dt_util.utcnow() + timedelta(
            seconds=activation_time
        )
        self.async_write_ha_state()
        async_track_point_in_utc_time(
            self.hass, self.update_state, self._activation_end_time
        )

    @callback
    def update_state(self, _: datetime | None = None):
        """Update the entity."""
        self.async_write_ha_state()
        _LOGGER.debug("State updated")
