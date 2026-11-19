"""Tests for the config flow, options flow and entry migration."""
import socket
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foxess_tseries.config_flow import ping_server, validate_serial_port
from custom_components.foxess_tseries.const import DOMAIN

TCP_DATA = {
    "connection_type": "tcp",
    "ip_address": "192.168.0.50",
    "port": 502,
    "payload_version": 0,
}


async def test_duplicate_tcp_entry_aborts(hass):
    MockConfigEntry(domain=DOMAIN, data=TCP_DATA, version=2).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"connection_type": "tcp"}
    )
    with patch("custom_components.foxess_tseries.config_flow.ping_server", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"ip_address": "192.168.0.50", "port": 502, "payload_version": 0},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_different_tcp_entry_is_created(hass, no_reader):
    MockConfigEntry(domain=DOMAIN, data=TCP_DATA, version=2).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"connection_type": "tcp"}
    )
    with patch("custom_components.foxess_tseries.config_flow.ping_server", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"ip_address": "192.168.0.51", "port": 502, "payload_version": 1},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["payload_version"] == 1


async def test_options_flow_changes_payload_version(hass, no_reader):
    entry = MockConfigEntry(domain=DOMAIN, data=TCP_DATA, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][entry.entry_id]._payload_version == 0

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"payload_version": 1}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.options["payload_version"] == 1
    # The entry reloads with the new version.
    assert hass.data[DOMAIN][entry.entry_id]._payload_version == 1


async def test_migrates_unique_ids_keeping_entity_ids(hass, no_reader):
    entry = MockConfigEntry(domain=DOMAIN, data=TCP_DATA, version=1)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, "grid_power", config_entry=entry, suggested_object_id="grid_power"
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    migrated = registry.async_get(old.entity_id)
    assert migrated.unique_id == f"{entry.entry_id}_grid_power"
    assert migrated.entity_id == "sensor.grid_power"


async def test_two_entries_get_separate_sensors(hass, no_reader):
    first = MockConfigEntry(domain=DOMAIN, data=TCP_DATA, version=2)
    second = MockConfigEntry(
        domain=DOMAIN, data={**TCP_DATA, "ip_address": "192.168.0.51"}, version=2
    )
    for entry in (first, second):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    for entry in (first, second):
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 32


SERIAL_INPUT = {"serial_port": "/dev/ttyUSB0", "baudrate": 9600, "payload_version": 0}


async def start_flow(hass, connection_type):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"connection_type": connection_type}
    )


async def test_tcp_unreachable_shows_error(hass):
    result = await start_flow(hass, "tcp")
    assert result["step_id"] == "tcp"
    with patch("custom_components.foxess_tseries.config_flow.ping_server", return_value=False):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"ip_address": "192.168.0.50", "port": 502, "payload_version": 0},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_serial_entry_is_created(hass, no_reader):
    result = await start_flow(hass, "serial")
    assert result["step_id"] == "serial"
    with patch(
        "custom_components.foxess_tseries.config_flow.validate_serial_port", return_value=None
    ), patch("custom_components.foxess_tseries.reader.InverterReader.async_connect"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], SERIAL_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"connection_type": "serial", **SERIAL_INPUT}


@pytest.mark.parametrize("error", ["cannot_connect", "esphome_not_ready"])
async def test_serial_errors_are_shown(hass, error):
    result = await start_flow(hass, "serial")
    with patch(
        "custom_components.foxess_tseries.config_flow.validate_serial_port", return_value=error
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], SERIAL_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_duplicate_serial_entry_aborts(hass):
    MockConfigEntry(
        domain=DOMAIN, data={"connection_type": "serial", **SERIAL_INPUT}, version=2
    ).add_to_hass(hass)
    result = await start_flow(hass, "serial")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SERIAL_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_old_serial_entry_does_not_block_tcp(hass, no_reader):
    # Entries from the old form always stored the default IP, even for USB.
    MockConfigEntry(
        domain=DOMAIN,
        data={"ip_address": "192.168.0.50", "port": 502, "serial_port": "/dev/ttyUSB0"},
        version=2,
    ).add_to_hass(hass)
    result = await start_flow(hass, "tcp")
    with patch("custom_components.foxess_tseries.config_flow.ping_server", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"ip_address": "192.168.0.50", "port": 502, "payload_version": 0},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_payload_version_out_of_range_is_rejected(hass):
    result = await start_flow(hass, "tcp")
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"ip_address": "192.168.0.50", "port": 502, "payload_version": 2},
        )


async def test_options_form_defaults_to_current_version(hass, no_reader):
    entry = MockConfigEntry(domain=DOMAIN, data={**TCP_DATA, "payload_version": 1}, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    default = next(key.default() for key in schema if key == "payload_version")
    assert default == 1


def test_ping_server(socket_enabled):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        assert ping_server("127.0.0.1", port, timeout=1) is True
    finally:
        server.close()
    assert ping_server("127.0.0.1", port, timeout=1) is False


async def test_validate_serial_port_results():
    target = "custom_components.foxess_tseries.reader.InverterReader.async_connect"
    with patch(target):
        assert await validate_serial_port("/dev/ttyUSB0", 9600) is None
    with patch(target, side_effect=OSError("no such device")):
        assert await validate_serial_port("/dev/ttyUSB0", 9600) == "cannot_connect"
    with patch(target, side_effect=ConfigEntryNotReady("esphome loading")):
        assert await validate_serial_port("esphome-hass://x/0", 9600) == "esphome_not_ready"
