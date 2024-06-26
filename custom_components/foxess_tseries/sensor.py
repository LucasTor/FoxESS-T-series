"""GitHub sensor platform."""
import logging
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
import socket
import threading
import json
import serial
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

    host = config_entry.data.get("ip_address", None)
    port = config_entry.data.get("port", None)
    serial_port = config_entry.data.get("serial_port", None)
    payload_version = config_entry.data.get("payload_version", 0)

    inverter_socket = None
    connected = False
    connecting = False
    zero_all_thread = None
    empty_comms = 0

    def zero_all_values():
        _LOGGER.debug("No message received in the last 5 minutes, zeroing values.")
        for sensor_key in sensors_to_zero_on_lost:
            sensor = inverter_sensors[sensor_key]
            sensor.received_message(0)
                        
    def create_zero_all_thread():
        nonlocal zero_all_thread
        zero_all_thread = threading.Timer(300, zero_all_values)
        zero_all_thread.start()

    def reset_zero_all_thread():
        _LOGGER.debug("Resetting zero values timer.")
        nonlocal zero_all_thread
        zero_all_thread.cancel()
        create_zero_all_thread()

    create_zero_all_thread()

    def create_socket():
        nonlocal connected
        nonlocal connecting
        nonlocal inverter_socket

        if(serial_port):
            _LOGGER.debug("Creating socket as serial port...")

            inverter_socket = serial.Serial(serial_port, 9600)
            connected = True

            _LOGGER.debug("Socket created as serial port!")

            return

        inverter_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        inverter_socket.settimeout(5)

        try:
            _LOGGER.debug(f'Trying connection to FoxESS T Series on IP {host} and port {port}...')
            connecting = True
            inverter_socket.connect((host, port))
            inverter_socket.setblocking(False)
            connected = True
            connecting = False
            _LOGGER.debug('Socket connected!')
        except:
            connected = False
            connecting = False
            _LOGGER.debug('Socket unreachable...')

    create_socket()

    def handle_receive():
        nonlocal connected
        nonlocal empty_comms
        nonlocal connecting
        nonlocal inverter_socket

        def get_raw_data():
            if(not serial_port):
                _LOGGER.debug("Getting raw data from websocket")
                return inverter_socket.recv(512)

            _LOGGER.debug("Getting raw data from usb socket")

            start_marker = b'\x7e\x7e'
            end_marker = b'\xe7\xe7'

            if(inverter_socket.in_waiting > 0):
                data_buffer = b''
                data = inverter_socket.read()
                read_count = 0
                start_index = -1

                while data:
                    data_buffer += data
                    read_count += 1

                    if(read_count >= 1000):
                        _LOGGER.warn("Serial port flooding, skipping...")
                        return None

                    if start_marker in data_buffer:            
                        start_index = data_buffer.index(start_marker)

                    if end_marker in data_buffer:
                        if(start_index == -1):
                            _LOGGER.warn("Message end marker hit with no start marker.")
                            return None

                        complete_message = data_buffer[start_index:]
                        return complete_message

                    data = inverter_socket.read()

            else:
                _LOGGER.debug("Empty serial port buffer.")
                return None

        def receive_msg():
            nonlocal connected
            nonlocal empty_comms
            nonlocal inverter_socket

            try:
                data = get_raw_data()
                empty_comms = 0
                
                if(not data):
                    _LOGGER.debug("Empty data.")
                    if(not serial_port):
                        connected = False
                    return

                is_payload_valid = validate_inverter_payload(data)
                if(not is_payload_valid):
                    _LOGGER.debug("Invalid payload.")
                    return 

                parsed_payload = parse_inverter_payload(data, payload_version)
                if(not parsed_payload):
                    _LOGGER.debug("Empty parsed payload?")
                    return
                
                _LOGGER.debug(f'Received new inverter payload at {parsed_payload["timestamp"]}')
                _LOGGER.debug(data.hex())

                for (sensor_key, sensor) in inverter_sensors.items():
                        sensor.received_message(parsed_payload[sensor_key])

                reset_zero_all_thread()

            except BlockingIOError:
                _LOGGER.debug("No data received from socket.")
                empty_comms += 1
                if(empty_comms > 300):
                    _LOGGER.debug("Socket has been empty for too long, considering disconnected.")
                    empty_comms = 0
                    connected = False
                    try:
                        inverter_socket.close()
                    except:
                        pass
                pass

            except Exception as error:
                connected = False
                _LOGGER.error('Unknow error')
                _LOGGER.error(error)

        if connected:
            _LOGGER.debug('Trying to receive message.')
            receive_msg()
        elif not connecting:
            _LOGGER.debug('Trying to reconnect to socket.')
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
        self.schedule_update_ha_state()
