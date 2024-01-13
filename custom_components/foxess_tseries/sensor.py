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
        'load_power': FoxESSTSeriesSensor('load_power', 'w', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_R': FoxESSTSeriesSensor('grid_voltage_R', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_R': FoxESSTSeriesSensor('grid_current_R', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_R': FoxESSTSeriesSensor('grid_frequency_R', 'Hz', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_R': FoxESSTSeriesSensor('grid_power_R', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_S': FoxESSTSeriesSensor('grid_voltage_S', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_S': FoxESSTSeriesSensor('grid_current_S', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_S': FoxESSTSeriesSensor('grid_frequency_S', 'Hz', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_S': FoxESSTSeriesSensor('grid_power_S', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'grid_voltage_T': FoxESSTSeriesSensor('grid_voltage_T', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'grid_current_T': FoxESSTSeriesSensor('grid_current_T', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'grid_frequency_T': FoxESSTSeriesSensor('grid_frequency_T', 'Hz', SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT),
        'grid_power_T': FoxESSTSeriesSensor('grid_power_T', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV1_voltage': FoxESSTSeriesSensor('PV1_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV1_current': FoxESSTSeriesSensor('PV1_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV1_power': FoxESSTSeriesSensor('PV1_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV2_voltage': FoxESSTSeriesSensor('PV2_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV2_current': FoxESSTSeriesSensor('PV2_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV2_power': FoxESSTSeriesSensor('PV2_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV3_voltage': FoxESSTSeriesSensor('PV3_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV3_current': FoxESSTSeriesSensor('PV3_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV3_power': FoxESSTSeriesSensor('PV3_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'PV4_voltage': FoxESSTSeriesSensor('PV4_voltage', 'V', SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        'PV4_current': FoxESSTSeriesSensor('PV4_current', 'A', SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT),
        'PV4_power': FoxESSTSeriesSensor('PV4_power', 'W', SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
        'boost_temperature': FoxESSTSeriesSensor('boost_temperature', '°C', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'inverter_temperature': FoxESSTSeriesSensor('inverter_temperature', '°C', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'ambient_temperature': FoxESSTSeriesSensor('ambient_temperature', '°C', SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT),
        'todays_yield': FoxESSTSeriesSensor('todays_yield', 'kWh', SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
        'total_yield': FoxESSTSeriesSensor('total_yield', 'kWh', SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)
    }

    host = config_entry.data["ip_address"]
    port = config_entry.data["port"]
    failed_attempts_before_disconnected = 300

    inverter_socket = None
    connected = False
    connecting = False
    empty_attempts = 0

    def create_socket():
        nonlocal connected
        nonlocal connecting
        nonlocal inverter_socket
        nonlocal empty_attempts

        inverter_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        inverter_socket.settimeout(5)

        try:
            _LOGGER.debug(f'Trying connection to FoxESS T Series on IP {host} and port {port}...')
            connecting = True
            inverter_socket.connect((host, port))
            inverter_socket.setblocking(False)
            connected = True
            connecting = False
            empty_attempts = 0
            _LOGGER.debug('Socket connected!')
        except:
            connected = False
            connecting = False
            _LOGGER.debug('Socket unreachable...')

    create_socket()

    def handle_receive():
        nonlocal connected
        nonlocal connecting
        nonlocal inverter_socket
        nonlocal empty_attempts

        def receive_msg():
            nonlocal connected
            nonlocal inverter_socket
            nonlocal empty_attempts
            try:
                data = inverter_socket.recv(512)
                if(not data):
                    connected = False
                    _LOGGER.debug("Empty data.")
                    return

                is_payload_valid = validate_inverter_payload(data)
                if(not is_payload_valid):
                    _LOGGER.debug("Invalid payload.")
                    return 

                parsed_payload = parse_inverter_payload(data)
                if(not parsed_payload):
                    _LOGGER.debug("Empty parsed payload?")
                    return
                
                _LOGGER.debug(f'Received new inverter payload at {parsed_payload["timestamp"]}')

                empty_attempts = 0

                for (sensor_key, sensor) in inverter_sensors.items():
                        sensor.received_message(parsed_payload[sensor_key])

            except BlockingIOError: # BlockingIOError is fired when no data is received by the socket
                _LOGGER.debug("No data received from socket.")
                return
            except OSError as error:
                _LOGGER.debug("Disconnected.")
                connected = False
                socket.close()
                if(error.errno == 57):
                    _LOGGER.debug('Socket connection lost.')
                else:
                    _LOGGER.debug(f'Unknow error ${error.errno}')
                    _LOGGER.debug(error)


        empty_attempts += 1
        if(empty_attempts > failed_attempts_before_disconnected):
            _LOGGER.debug("Socket has been empty for too long, considering disconnected and zeroing values.")
            #TODO: zero values
            connected = False
            try:
                socket.close()
            except:
                pass

        if connected:
            _LOGGER.debug('Trying to receive message')
            receive_msg()
        elif not connecting:
            _LOGGER.debug('Trying to reconnect to socket')
            create_socket()

        timer = threading.Timer(1 if connected else 60, handle_receive)
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
