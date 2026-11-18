"""FoxESS T Series integration."""
from __future__ import annotations

import logging

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

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


def get_payload_version(entry) -> int:
    """Payload version from the options flow, falling back to the setup data."""
    return int(entry.options.get(
        CONF_PAYLOAD_VERSION,
        entry.data.get(CONF_PAYLOAD_VERSION, DEFAULT_PAYLOAD_VERSION),
    ))


async def async_migrate_entry(hass, entry) -> bool:
    """Migrate old config entries."""
    _LOGGER.debug("Migrating entry %s from version %s", entry.entry_id, entry.version)

    if entry.version == 1:
        # Version 1 used the bare sensor key (e.g. "grid_power") as unique ID,
        # so a second entry's sensors collided with the first one's and were
        # dropped. Scope them to the entry, keeping entity IDs and history.
        prefix = f"{entry.entry_id}_"

        @callback
        def _migrate_unique_id(entity_entry):
            if entity_entry.unique_id.startswith(prefix):
                return None
            new_unique_id = f"{prefix}{entity_entry.unique_id}"
            _LOGGER.debug(
                "Migrating %s unique ID %s -> %s",
                entity_entry.entity_id, entity_entry.unique_id, new_unique_id,
            )
            return {"new_unique_id": new_unique_id}

        await er.async_migrate_entries(hass, entry.entry_id, _migrate_unique_id)
        hass.config_entries.async_update_entry(entry, version=2)

    _LOGGER.debug("Entry %s is at version %s", entry.entry_id, entry.version)
    return True


async def async_setup_entry(hass, entry) -> bool:
    """Set up platform from a ConfigEntry."""
    _LOGGER.debug("Setting up entry %s with data %s", entry.entry_id, dict(entry.data))
    reader = InverterReader(
        host=entry.data.get(CONF_IP_ADDRESS),
        port=entry.data.get(CONF_PORT),
        serial_port=entry.data.get(CONF_SERIAL_PORT) or None,
        baudrate=entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
        payload_version=get_payload_version(entry),
    )

    if reader.uses_serial:
        # A serial port (local USB or ESPHome serial proxy) should always be
        # openable, so fail setup and let Home Assistant retry. An ESPHome proxy
        # raises ConfigEntryNotReady itself while the ESPHome integration is
        # still starting up.
        try:
            await reader.async_connect()
        except ConfigEntryNotReady as error:
            _LOGGER.debug("Serial port not ready yet, Home Assistant will retry: %s", error)
            raise
        except Exception as error:  # noqa: BLE001
            _LOGGER.debug("Serial port could not be opened, Home Assistant will retry: %s", error)
            raise ConfigEntryNotReady(
                f"Unable to open serial port {entry.data.get(CONF_SERIAL_PORT)}: {error}"
            ) from error

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = reader

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    reader.start()
    _LOGGER.debug("Entry %s set up using %s", entry.entry_id, reader.describe())
    return True


async def _async_options_updated(hass, entry) -> None:
    """Reload the entry so a changed payload version takes effect."""
    _LOGGER.debug("Options for entry %s changed to %s, reloading", entry.entry_id, dict(entry.options))
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    reader = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if reader is not None:
        await reader.async_stop()
    _LOGGER.debug("Entry %s unloaded (platforms ok=%s)", entry.entry_id, unload_ok)
    return unload_ok
