"""GitHub sensor platform."""
import logging
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

import voluptuous as vol

from random import randint
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)

CONF_REPOS = "repositories"
CONF_ACCESS_TOKEN = 'access_token'
CONF_URL = 'url'
CONF_PATH = 'path'
CONF_NAME = 'name'

REPO_SCHEMA = vol.Schema(
    {vol.Required(CONF_PATH): cv.string, vol.Optional(CONF_NAME): cv.string}
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_ACCESS_TOKEN): cv.string,
        vol.Required(CONF_REPOS): vol.All(cv.ensure_list, [REPO_SCHEMA]),
        vol.Optional(CONF_URL): cv.url,
    }
)

async def async_setup_platform() -> None:
    """Set up the sensor platform."""
    print("SETTING UP")
    print("CONFIG")

    sensors = [FoxESSTSeriesSensor('Measured Power')]

    return sensors

class FoxESSTSeriesSensor(SensorEntity):
    """Representation of a FoxESS T Series Inverter sensor."""

    def __init__(self, name):
        super().__init__()
        self._name = name
        self._state = None
        self._available = True

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return 1

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return True

    @property
    def state(self) -> Optional[str]:
        return self._state
    
    @property
    def should_poll(self):
        return False
    
    def update(self) -> None:
        self._state = randint(0, 100)
        self.schedule_update_ha_state()