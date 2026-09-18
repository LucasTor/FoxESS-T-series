"""Config flow for the FoxESS T Series integration."""
from __future__ import annotations

import logging
import socket
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SerialPortSelector,
)

from .const import (
    CONF_BAUDRATE,
    CONF_CONNECTION_TYPE,
    CONF_IP_ADDRESS,
    CONF_PAYLOAD_VERSION,
    CONF_PORT,
    CONF_SERIAL_PORT,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_BAUDRATE,
    DEFAULT_IP_ADDRESS,
    DEFAULT_PAYLOAD_VERSION,
    DEFAULT_PORT,
    DOMAIN,
)
from .reader import InverterReader

_LOGGER = logging.getLogger(__name__)

CONNECTION_TYPE_SCHEMA = vol.Schema({
    vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_TCP): SelectSelector(
        SelectSelectorConfig(
            options=[CONNECTION_TYPE_TCP, CONNECTION_TYPE_SERIAL],
            translation_key="connection_type",
        )
    ),
})

TCP_SCHEMA = vol.Schema({
    vol.Required(CONF_IP_ADDRESS, default=DEFAULT_IP_ADDRESS): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    vol.Optional(CONF_PAYLOAD_VERSION, default=DEFAULT_PAYLOAD_VERSION): int,
})

# The serial port selector lists local USB adapters and ESPHome serial proxies
# (https://esphome.io/components/serial_proxy/) side by side.
SERIAL_SCHEMA = vol.Schema({
    vol.Required(CONF_SERIAL_PORT): SerialPortSelector(),
    vol.Optional(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional(CONF_PAYLOAD_VERSION, default=DEFAULT_PAYLOAD_VERSION): int,
})


def ping_server(server: str, port: int, timeout=3):
    """Check the inverter is reachable. Blocking, run in an executor."""
    try:
        s = socket.create_connection((server, port), timeout=timeout)
    except OSError:
        return False
    else:
        s.close()
        return True


async def validate_serial_port(serial_port: str, baudrate: int) -> str | None:
    """Try opening the serial port. Returns an error key or None when it works."""
    reader = InverterReader(
        host=None,
        port=None,
        serial_port=serial_port,
        baudrate=baudrate,
        payload_version=DEFAULT_PAYLOAD_VERSION,
    )
    try:
        await reader.async_connect()
    except ConfigEntryNotReady:
        # Raised by the ESPHome serial proxy stub while ESPHome is still loading.
        return "esphome_not_ready"
    except Exception as error:  # noqa: BLE001
        _LOGGER.debug("Unable to open serial port %s: %s", serial_port, error)
        return "cannot_connect"
    finally:
        await reader.async_disconnect()
    return None


class CustomFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=CONNECTION_TYPE_SCHEMA)

        if user_input[CONF_CONNECTION_TYPE] == CONNECTION_TYPE_SERIAL:
            return await self.async_step_serial()
        return await self.async_step_tcp()

    async def async_step_tcp(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            reachable = await self.hass.async_add_executor_job(
                ping_server, user_input[CONF_IP_ADDRESS], user_input[CONF_PORT]
            )
            if reachable:
                return self.async_create_entry(
                    title="FoxESS T Series",
                    data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_TCP, **user_input},
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="tcp",
            data_schema=self.add_suggested_values_to_schema(TCP_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_serial(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await validate_serial_port(user_input[CONF_SERIAL_PORT], user_input[CONF_BAUDRATE])
            if error is None:
                return self.async_create_entry(
                    title="FoxESS T Series",
                    data={CONF_CONNECTION_TYPE: CONNECTION_TYPE_SERIAL, **user_input},
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="serial",
            data_schema=self.add_suggested_values_to_schema(SERIAL_SCHEMA, user_input),
            errors=errors,
        )
