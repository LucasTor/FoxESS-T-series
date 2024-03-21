import struct
from datetime import datetime
import logging

_LOGGER = logging.getLogger(__name__)

keys = [
    'timestamp',
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
    'PV4_power',
    'boost_temperature',
    'inverter_temperature',
    'ambient_temperature',
    'todays_yield',
    'total_yield'
]

value_resolution = {
    'grid_voltage_R': 0.1,
    'grid_current_R': 0.1,
    'grid_frequency_R': 0.01,
    'grid_power_R': 0.01,
    'grid_voltage_S': 0.1,
    'grid_current_S': 0.1,
    'grid_frequency_S': 0.01,
    'grid_power_S': 0.01,
    'grid_voltage_T': 0.1,
    'grid_current_T': 0.1,
    'grid_frequency_T': 0.01,
    'grid_power_T': 0.01,
    'PV1_voltage': 0.1,
    'PV1_current': 0.1,
    'PV1_power': 0.01,
    'PV2_voltage': 0.1,
    'PV2_current': 0.1,
    'PV2_power': 0.01,
    'PV3_voltage': 0.1,
    'PV3_current': 0.1,
    'PV3_power': 0.01,
    'PV4_voltage': 0.1,
    'PV4_current': 0.1,
    'PV4_power': 0.01,
    'todays_yield': 0.1,
    'total_yield': 0.1,
}

def parse_inverter_payload(payload, payload_version):
    try:
        msg_len = int(payload[7:9].hex(), 16)
        garbage_bytes = msg_len - 62 if payload_version == 0 else msg_len - 68

        values = struct.unpack(
            f'> 3x i 2x 31h i {garbage_bytes}x' if payload_version == 0 else f'> 3x i 8x 31h i {garbage_bytes}x',
            payload
        )

        result = dict(zip(keys, values))

        result['timestamp'] = datetime.fromtimestamp(result['timestamp']).isoformat()
        result['grid_power_R'] = round(result['grid_voltage_R'] * result['grid_current_R'], 2)
        result['grid_power_S'] = round(result['grid_voltage_S'] * result['grid_current_S'], 2)
        result['grid_power_T'] = round(result['grid_voltage_T'] * result['grid_current_T'], 2)

        result['PV1_power'] = round(result['PV1_voltage'] * result['PV1_current'], 2)
        result['PV2_power'] = round(result['PV2_voltage'] * result['PV2_current'], 2)
        result['PV3_power'] = round(result['PV3_voltage'] * result['PV3_current'], 2)
        result['PV4_power'] = round(result['PV4_voltage'] * result['PV4_current'], 2)

        def set_resolution (value, resolution):
            return value / (1 / resolution)

        for (attribute, resolution) in value_resolution.items():
            result[attribute] = set_resolution(result[attribute], resolution)

        return result
    except Exception as e:
        _LOGGER.debug(e)
        return None

def calculate_crc(msg) -> int:
    crc = 0xFFFF
    for n in range(len(msg)):
        crc ^= msg[n]
        for i in range(8):
            if crc & 1:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1

    return crc.to_bytes(2, byteorder='little')

def validate_inverter_payload(payload):
    received_function_code = payload[2]
    expected_function_code = 2

    function_code_valid = received_function_code == expected_function_code

    if(not function_code_valid):
        _LOGGER.debug("Invalid message function code.")
        return False

    expected_header = bytes.fromhex("7E7E")
    received_header = payload[0:2]

    header_valid = received_header == expected_header

    if(not header_valid):
        _LOGGER.debug("Invalid message header.")
        return False
    
    expected_footer = bytes.fromhex("E7E7")
    received_footer = payload[-2:]
    
    footer_valid = received_footer == expected_footer
    
    if(not footer_valid):
        _LOGGER.debug("Invalid message footer.")
        return False

    received_crc = payload[-4:-2]
    calculated_crc = calculate_crc(payload[2:-4])
    
    crc_valid = received_crc == calculated_crc

    if(not crc_valid):
        _LOGGER.error("Invalid message checksum.")
        return False

    return True
