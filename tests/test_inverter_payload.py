"""Tests for payload decoding and the payload version check."""
import struct

from custom_components.foxess_tseries.helpers.inverter_payload import (
    calculate_crc,
    parse_inverter_payload,
    suggest_payload_version,
    validate_inverter_payload,
)

# Values from a single-phase SIW200G (issue #13), in payload version 0 layout:
# grid/gen/load power, then R/S/T voltage, current, frequency, power, then
# PV1-4 voltage, current, power, then 3 temperatures and today's yield.
SHORTS = [
    0, 495, 0,
    2401, 20, 6004, 495,
    0, 0, 0, 0,
    0, 0, 0, 0,
    2002, 9, 180,
    2737, 10, 273,
    0, 0, 0,
    0, 0, 0,
    33, 33, 30,
    165,
]
TOTAL_YIELD = 277696


def build_frame(payload_version: int) -> bytes:
    extra = b"\x00" * 6 if payload_version == 1 else b""
    data = extra + struct.pack(">31hi", *SHORTS, TOTAL_YIELD) + b"\x00" * 88
    body = b"\x02" + struct.pack(">i", 0x6AFC165A) + struct.pack(">H", len(data)) + data
    return b"\x7e\x7e" + body + calculate_crc(body) + b"\xe7\xe7"


def test_frame_is_valid():
    for version in (0, 1):
        assert validate_inverter_payload(build_frame(version))


def test_decodes_with_matching_version():
    for version in (0, 1):
        parsed = parse_inverter_payload(build_frame(version), version)
        assert parsed["grid_voltage_R"] == 240.1
        assert parsed["grid_frequency_R"] == 60.04
        assert parsed["PV2_voltage"] == 273.7
        assert parsed["inverter_temperature"] == 33
        assert parsed["todays_yield"] == 16.5
        assert parsed["total_yield"] == 27769.6


def test_suggests_other_version_when_misconfigured():
    assert suggest_payload_version(build_frame(0), 1) == 0
    assert suggest_payload_version(build_frame(1), 0) == 1


def test_no_suggestion_when_version_is_right():
    assert suggest_payload_version(build_frame(0), 0) is None
    assert suggest_payload_version(build_frame(1), 1) is None


def _reader(payload_version):
    from custom_components.foxess_tseries.reader import InverterReader

    return InverterReader(
        host="192.168.0.50", port=502, serial_port=None, baudrate=9600,
        payload_version=payload_version,
    )


def test_reader_warns_once_on_wrong_version(caplog):
    reader = _reader(1)
    reader._handle_frame(build_frame(0))
    reader._handle_frame(build_frame(0))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "payload version 1 but do with version 0" in warnings[0].getMessage()


def test_reader_does_not_warn_on_right_version(caplog):
    reader = _reader(0)
    reader._handle_frame(build_frame(0))
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
