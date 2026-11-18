"""Tests for the config flow, options flow and entry migration."""
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
