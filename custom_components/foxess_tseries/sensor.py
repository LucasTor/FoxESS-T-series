"""FoxESS T Series sensor platform."""
import logging
from typing import Optional
from .const import DOMAIN
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass,
    config_entry,
    async_add_entities,
):
    def make_sensor(id, unit, device_class=None, state_class=None):
        return FoxESSTSeriesSensorEntity(config_entry.entry_id, id, unit, device_class, state_class)

    inverter_sensors = {
        'grid_power': make_sensor('grid_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'gen_power': make_sensor('gen_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'load_power': make_sensor('load_power', 'w', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_R': make_sensor('grid_voltage_R', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_R': make_sensor('grid_current_R', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_R': make_sensor('grid_frequency_R', 'Hz', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_R': make_sensor('grid_power_R', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_S': make_sensor('grid_voltage_S', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_S': make_sensor('grid_current_S', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_S': make_sensor('grid_frequency_S', 'Hz', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_S': make_sensor('grid_power_S', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_T': make_sensor('grid_voltage_T', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_T': make_sensor('grid_current_T', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_T': make_sensor('grid_frequency_T', 'Hz', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_T': make_sensor('grid_power_T', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV1_voltage': make_sensor('PV1_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV1_current': make_sensor('PV1_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV1_power': make_sensor('PV1_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV2_voltage': make_sensor('PV2_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV2_current': make_sensor('PV2_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV2_power': make_sensor('PV2_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV3_voltage': make_sensor('PV3_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV3_current': make_sensor('PV3_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV3_power': make_sensor('PV3_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV4_voltage': make_sensor('PV4_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV4_current': make_sensor('PV4_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV4_power': make_sensor('PV4_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'boost_temperature': make_sensor('boost_temperature', '°C', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'inverter_temperature': make_sensor('inverter_temperature', '°C', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'ambient_temperature': make_sensor('ambient_temperature', '°C', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'todays_yield': make_sensor('todays_yield', 'kWh', SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
        'total_yield': make_sensor('total_yield', 'kWh', SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)
    }

    sensors_to_zero_on_lost = [
        'grid_power',
        'gen_power',
        'load_power',
        'grid_voltage_R',
        'grid_current_R',
        'grid_frequency_R',
        'grid_power_R',
        'grid_voltage_S',
        'grid_current_S',
        'grid_frequency_S',
        'grid_power_S',
        'grid_voltage_T',
        'grid_current_T',
        'grid_frequency_T',
        'grid_power_T',
        'PV1_voltage',
        'PV1_current',
        'PV1_power',
        'PV2_voltage',
        'PV2_current',
        'PV2_power',
        'PV3_voltage',
        'PV3_current',
        'PV3_power',
        'PV4_voltage',
        'PV4_current',
        'PV4_power'
    ]

    reader = hass.data[DOMAIN][config_entry.entry_id]

    def on_payload(parsed_payload):
        for (sensor_key, sensor) in inverter_sensors.items():
            sensor.received_message(parsed_payload[sensor_key])

    def on_lost():
        _LOGGER.debug("Zeroing %s live sensors after loss of data", len(sensors_to_zero_on_lost))
        for sensor_key in sensors_to_zero_on_lost:
            inverter_sensors[sensor_key].received_message(0)

    reader.on_payload = on_payload
    reader.on_lost = on_lost

    _LOGGER.debug("Adding %s FoxESS T Series sensors to Home Assistant", len(inverter_sensors))
    async_add_entities(inverter_sensors.values(), update_before_add=True)

def sensor_unique_id(entry_id: str, sensor_key: str) -> str:
    """Unique ID of a sensor, scoped to its config entry."""
    return f"{entry_id}_{sensor_key}"


class FoxESSTSeriesSensorEntity(SensorEntity):
    """Representation of a FoxESS T Series sensor."""

    def __init__(self, entry_id, id, unit, device_class = None, state_class = None):
        super().__init__()
        self.id = id
        self._attr_unique_id = sensor_unique_id(entry_id, id)
        self._name = ' '.join([part.title() for part in id.split('_')])
        self._state = None
        self._available = True
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def state(self) -> Optional[str]:
        return self._state
    
    @property
    def should_poll(self):
        return False
    
    def update(self):
        return self._state
    
    def received_message(self, val):
        """Handle a new value. Called from the event loop."""
        _LOGGER.debug(f'Received {self.id} state: {str(val)}')
        self._state = str(val)
        if self.hass is not None:
            self.async_write_ha_state()
