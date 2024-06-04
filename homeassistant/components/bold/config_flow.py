"""Config flow for Bold integration."""

from __future__ import annotations

import logging
import struct
from typing import Any

from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo, BluetoothServiceInfoBleak
import voluptuous as vol

from homeassistant.components.bluetooth.api import async_discovered_service_info
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN

SESAM_MANUFACTURER_ID = 0x065B
SESAM_SERVICE_UID = "fd30"

_LOGGER = logging.getLogger(__name__)


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Bold OAuth2 authentication."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_device: BoldBluetoothDeviceData | None = None
        self._discovered_devices: dict[str, str] = {}
        super().__init__()

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Start oauth authentication with bold cloud."""
        data["device"] = self.context["device"]
        return self.async_create_entry(title=self.context["device"]["name"], data=data)

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)

        self._abort_if_unique_id_configured()
        device = BoldBluetoothDeviceData()
        if not device.supported(discovery_info):
            return self.async_abort(reason="not_supported")

        title = device.title or device.get_device_name() or discovery_info.name
        self.context["device"] = {
            "name": title,
            "address": discovery_info.address,
            "id": device.device_identifier,
            "model": device.model,
        }
        self._discovery_info = discovery_info
        self._discovered_device = device
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        if user_input is not None:
            current_entries = self._async_current_entries()

            if current_entries:
                return await self.async_oauth_create_entry(
                    data=current_entries[0].as_dict()
                )
            # Do OAuth flow
            return await super().async_step_user()

        self.context["title_placeholders"] = {"name": self.context["device"]["name"]}
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["device"],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered_devices[address], data={}
            )

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass, True):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            device = BoldBluetoothDeviceData()
            if device.supported(discovery_info):
                self._discovered_devices[address] = (
                    device.title or device.get_device_name() or discovery_info.name
                )

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered_devices)}
            ),
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


# Move this class to library
class BoldBluetoothDeviceData(BluetoothData):
    """Data update for Bold Bluetooth devices."""

    model: str
    device_identifier: str

    def _start_update(self, data: BluetoothServiceInfo) -> None:
        """Update from BLE advertisement data."""
        _LOGGER.debug("Parsing BOLD BLE advertisement data: %s", data)
        manufacturer_data: bytes = data.manufacturer_data.get(
            SESAM_MANUFACTURER_ID, b""
        )

        if not manufacturer_data:
            return

        model_id = manufacturer_data[3]
        self.model = "Unknown model"
        # 194
        # 83906
        if model_id == 101:
            self.model = "Bold Cylinder"
        else:
            return
        self.device_identifier = struct.unpack("<L", manufacturer_data[3:7])[0]

        self.set_device_name(f"{self.model} ({self.device_identifier})")

        self.set_title(f"{self.model} ({self.device_identifier})")

        self.set_device_type(self.model)

        self.set_device_manufacturer("Bold")
