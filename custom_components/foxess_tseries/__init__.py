"""FoxESS T Series integration."""
from __future__ import annotations

import logging

from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_BAUDRATE,
    CONF_IP_ADDRESS,
    CONF_PAYLOAD_VERSION,
    CONF_PORT,
    CONF_SERIAL_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_PAYLOAD_VERSION,
    DOMAIN,
)
from .reader import InverterReader

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass, entry) -> bool:
    """Set up platform from a ConfigEntry."""
    reader = InverterReader(
        host=entry.data.get(CONF_IP_ADDRESS),
        port=entry.data.get(CONF_PORT),
        serial_port=entry.data.get(CONF_SERIAL_PORT) or None,
        baudrate=entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
        payload_version=entry.data.get(CONF_PAYLOAD_VERSION, DEFAULT_PAYLOAD_VERSION),
    )

    if reader.uses_serial:
        # A serial port (local USB or ESPHome serial proxy) should always be
        # openable, so fail setup and let Home Assistant retry. An ESPHome proxy
        # raises ConfigEntryNotReady itself while the ESPHome integration is
        # still starting up.
        try:
            await reader.async_connect()
        except ConfigEntryNotReady:
            raise
        except Exception as error:  # noqa: BLE001
            raise ConfigEntryNotReady(
                f"Unable to open serial port {entry.data.get(CONF_SERIAL_PORT)}: {error}"
            ) from error

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = reader

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    reader.start()
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    reader = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if reader is not None:
        await reader.async_stop()
    return unload_ok
