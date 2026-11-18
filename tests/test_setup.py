"""Tests for setting up, reloading and unloading config entries."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foxess_tseries.const import DOMAIN

from .test_inverter_payload import build_frame

TCP_DATA = {"connection_type": "tcp", "ip_address": "192.168.0.50", "port": 502, "payload_version": 0}
SERIAL_DATA = {"connection_type": "serial", "serial_port": "/dev/ttyUSB0", "baudrate": 9600, "payload_version": 0}
CONNECT = "custom_components.foxess_tseries.reader.InverterReader.async_connect"


async def setup_entry(hass, data, **kwargs):
    entry = MockConfigEntry(domain=DOMAIN, data=data, version=2, **kwargs)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize("error", [OSError("no such device"), ConfigEntryNotReady("esphome loading")])
async def test_serial_setup_retries_when_port_unavailable(hass, no_reader, error):
    with patch(CONNECT, side_effect=error):
        entry = await setup_entry(hass, SERIAL_DATA)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_serial_setup_opens_port_first(hass, no_reader):
    with patch(CONNECT) as connect:
        entry = await setup_entry(hass, SERIAL_DATA)
    assert entry.state is ConfigEntryState.LOADED
    connect.assert_awaited_once()


async def test_old_entry_data_still_loads(hass, no_reader):
    # Entries created by v1.0.x have no connection type or baud rate.
    with patch(CONNECT):
        entry = await setup_entry(
            hass, {"ip_address": "192.168.0.129", "port": 502, "serial_port": "/dev/ttyUSB0"}
        )
    reader = hass.data[DOMAIN][entry.entry_id]
    assert reader.uses_serial
    assert reader._baudrate == 9600
    assert reader._payload_version == 0


async def test_unload_stops_reader(hass, no_reader):
    entry = await setup_entry(hass, TCP_DATA)
    reader = hass.data[DOMAIN][entry.entry_id]
    with patch.object(reader, "async_stop") as stop:
        assert await hass.config_entries.async_unload(entry.entry_id)
    stop.assert_awaited_once()
    assert entry.entry_id not in hass.data[DOMAIN]
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_migration_skips_already_scoped_ids(hass, no_reader):
    entry = MockConfigEntry(domain=DOMAIN, data=TCP_DATA, version=1)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    scoped = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_gen_power", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert registry.async_get(scoped.entity_id).unique_id == f"{entry.entry_id}_gen_power"


async def test_sensors_update_and_zero(hass, no_reader):
    entry = await setup_entry(hass, TCP_DATA)
    reader = hass.data[DOMAIN][entry.entry_id]

    reader._handle_frame(build_frame(0))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.grid_voltage_r").state == "240.1"
    assert hass.states.get("sensor.gen_power").state == "495"
    assert hass.states.get("sensor.inverter_temperature").state == "33"
    assert hass.states.get("sensor.total_yield").state == "27769.6"

    reader.on_lost()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.grid_voltage_r").state == "0"
    assert hass.states.get("sensor.gen_power").state == "0"
    # Temperatures and yields keep their last value overnight.
    assert hass.states.get("sensor.inverter_temperature").state == "33"
    assert hass.states.get("sensor.total_yield").state == "27769.6"
