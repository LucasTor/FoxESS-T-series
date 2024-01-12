"""GitHub sensor platform."""
import logging
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
import socket
import threading
import json
from .helpers.inverter_payload import parse_inverter_payload, validate_inverter_payload
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass
)

_LOGGER = logging.getLogger(__name__)

# TODO: Get this data from user ui cfg
HOST = "192.168.0.129" 
PORT = 502

async def async_setup_entry(
    hass,
    config_entry,
    async_add_entities,
):
    inverter_sensors = {
        'grid_power': FoxESSTSeriesSensor('grid_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'gen_power': FoxESSTSeriesSensor('gen_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'load_power': FoxESSTSeriesSensor('load_power', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_R': FoxESSTSeriesSensor('grid_voltage_R', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_R': FoxESSTSeriesSensor('grid_current_R', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_R': FoxESSTSeriesSensor('grid_frequency_R', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_R': FoxESSTSeriesSensor('grid_power_R', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_S': FoxESSTSeriesSensor('grid_voltage_S', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_S': FoxESSTSeriesSensor('grid_current_S', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_S': FoxESSTSeriesSensor('grid_frequency_S', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_S': FoxESSTSeriesSensor('grid_power_S', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_T': FoxESSTSeriesSensor('grid_voltage_T', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_T': FoxESSTSeriesSensor('grid_current_T', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_T': FoxESSTSeriesSensor('grid_frequency_T', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_T': FoxESSTSeriesSensor('grid_power_T', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV1_voltage': FoxESSTSeriesSensor('PV1_voltage', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV1_current': FoxESSTSeriesSensor('PV1_current', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV1_power': FoxESSTSeriesSensor('PV1_power', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV2_voltage': FoxESSTSeriesSensor('PV2_voltage', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV2_current': FoxESSTSeriesSensor('PV2_current', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV2_power': FoxESSTSeriesSensor('PV2_power', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV3_voltage': FoxESSTSeriesSensor('PV3_voltage', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV3_current': FoxESSTSeriesSensor('PV3_current', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV3_power': FoxESSTSeriesSensor('PV3_power', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV4_voltage': FoxESSTSeriesSensor('PV4_voltage', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV4_current': FoxESSTSeriesSensor('PV4_current', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV4_power': FoxESSTSeriesSensor('PV4_power', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'boost_temperature': FoxESSTSeriesSensor('boost_temperature', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'inverter_temperature': FoxESSTSeriesSensor('inverter_temperature', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'ambient_temperature': FoxESSTSeriesSensor('ambient_temperature', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'todays_yield': FoxESSTSeriesSensor('todays_yield',SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
        'total_yield': FoxESSTSeriesSensor('total_yield', SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)
    }

    host = config_entry.data["ip_address"]
    port = config_entry.data["port"]

    _LOGGER.debug(f'Trying connection to FoxESS T Series on IP {host} and port {port}...')
    inverter_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    inverter_socket.connect((host, port))
    inverter_socket.setblocking(False)

    def handle_receive():
        def receive_msg():
            try:
                data = inverter_socket.recv(512)
                if(not data):
                    return

                is_payload_valid = validate_inverter_payload(data)
                if(not is_payload_valid):
                    return 

                parsed_payload = parse_inverter_payload(data)
                if(not parsed_payload):
                    return
                
                _LOGGER.debug(f'Received new inverter payload at {parsed_payload["timestamp"]}')

                for (sensor_key, sensor) in inverter_sensors.items():
                        sensor.received_message(parsed_payload[sensor_key])

            except (BlockingIOError):
                return # BlockingIOError is fired when no data is received by the socket

        receive_msg()
        timer = threading.Timer(1, handle_receive)
        timer.start()

    _LOGGER.debug("Adding FoxESS T Series sensors to Home Assistant")
    async_add_entities(inverter_sensors.values(), update_before_add=True)

    handle_receive()

class FoxESSTSeriesSensor(SensorEntity):
    """Representation of a FoxESS T Series sensor."""

    def __init__(self, id, unit, device_class = None, state_class = None):
        super().__init__()
        self.id = id
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
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return self.id

    @property
    def state(self) -> Optional[str]:
        return self._state
    
    @property
    def should_poll(self):
        return False
    
    def update(self):
        return self._state
    
    def received_message(self, val):
        _LOGGER.debug(f'Received {self.id} state: {str(val)}')
        self._state = str(val)
        self.async_schedule_update_ha_state()
