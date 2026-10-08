"""Tests for Gang Link sync rules, setup and unload."""

from datetime import timedelta

import pytest

from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.gang_link import DOMAIN, PANEL_PATH

GANG = "switch.gang_1"
TARGETS = ["light.a", "light.b"]


@pytest.fixture
def calls(hass: HomeAssistant):
    """Record homeassistant.turn_on/turn_off calls as (service, entity_ids, context)."""
    recorded = []
    for service in ("turn_on", "turn_off"):
        async_mock_service(hass, "homeassistant", service)
    hass.bus.async_listen(
        "call_service",
        lambda event: recorded.append(
            (
                event.data["service"],
                event.data["service_data"]["entity_id"],
                event.context,
            )
        )
        if event.data["domain"] == "homeassistant"
        else None,
    )
    return recorded


@pytest.fixture
async def entry(hass: HomeAssistant, hass_storage, calls) -> MockConfigEntry:
    """Set up Gang Link with GANG linked to TARGETS, everything off."""
    hass_storage[DOMAIN] = {
        "version": 1,
        "key": DOMAIN,
        "data": {"links": {GANG: TARGETS}},
    }
    for entity_id in [GANG, *TARGETS]:
        hass.states.async_set(entity_id, "off")
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def advance(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_press_drives_targets(hass: HomeAssistant, entry, calls) -> None:
    hass.states.async_set(GANG, "on", context=Context())
    await hass.async_block_till_done()
    assert [c[:2] for c in calls] == [("turn_on", TARGETS)]


async def test_gang_follows_targets_without_echo(
    hass: HomeAssistant, entry, calls
) -> None:
    hass.states.async_set("light.a", "on")
    await advance(hass, 1.5)
    assert [c[:2] for c in calls] == [("turn_on", [GANG])]

    # The gang turning on from our own call must not be pushed to targets.
    hass.states.async_set(GANG, "on", context=calls[0][2])
    await hass.async_block_till_done()
    assert len(calls) == 1

    hass.states.async_set("light.a", "off")
    await advance(hass, 1.5)
    assert [c[:2] for c in calls[1:]] == [("turn_off", [GANG])]


async def test_hold_after_press(hass: HomeAssistant, entry, calls) -> None:
    for entity_id in [GANG, *TARGETS]:
        hass.states.async_set(entity_id, "on")
    await advance(hass, 1.5)
    calls.clear()

    hass.states.async_set(GANG, "off", context=Context())
    await hass.async_block_till_done()
    assert [c[:2] for c in calls] == [("turn_off", TARGETS)]

    # Targets report one by one. light.b is still on after the settle time,
    # but the gang must not bounce back on during the hold.
    hass.states.async_set("light.a", "off")
    await advance(hass, 1.5)
    hass.states.async_set("light.b", "off")
    await advance(hass, 6)
    assert len(calls) == 1


async def test_new_link_adopts_target_state(
    hass: HomeAssistant, entry, calls, hass_ws_client
) -> None:
    hass.states.async_set("switch.gang_2", "off")
    hass.states.async_set("light.c", "on")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "gang_link/set", "gang": "switch.gang_2", "targets": ["light.c"]}
    )
    result = await client.receive_json()
    assert result["success"]
    assert result["result"]["links"]["switch.gang_2"] == ["light.c"]

    await advance(hass, 0.5)
    assert [c[:2] for c in calls] == [("turn_on", ["switch.gang_2"])]


async def test_returning_gang_adopts_target_state(
    hass: HomeAssistant, entry, calls
) -> None:
    hass.states.async_set(GANG, "unavailable")
    hass.states.async_set("light.a", "on")
    await advance(hass, 1.5)
    assert calls == []

    # The gang boots "off" while a target is on: the gang turns on instead of
    # turning the light off.
    hass.states.async_set(GANG, "off", context=Context())
    await advance(hass, 0.5)
    assert [c[:2] for c in calls] == [("turn_on", [GANG])]


async def test_unload_and_reload(hass: HomeAssistant, entry, calls) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert PANEL_PATH not in hass.data["frontend_panels"]

    hass.states.async_set(GANG, "on", context=Context())
    hass.states.async_set("light.a", "on")
    await advance(hass, 1.5)
    assert calls == []

    assert await hass.config_entries.async_setup(entry.entry_id)
    assert PANEL_PATH in hass.data["frontend_panels"]


async def test_leftover_yaml_does_not_break_setup(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: None})
