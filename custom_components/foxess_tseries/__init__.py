import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .sensor import async_setup_platform

async def async_setup(_hass: HomeAssistant, _config: Config) -> bool:
    """Setting up this integration using YAML is not supported."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""

    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})

    controller = await async_setup_platform()

    hass.data[DOMAIN][entry.entry_id]["controller"] = controller

    return True
