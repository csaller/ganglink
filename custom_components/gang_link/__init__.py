"""Gang Link: let wall-switch gangs control smart lights without cutting their power.

A link maps a gang (one switch entity of a multi-gang wall switch, whose relay
is left disconnected) to one or more target lights or switches:

- Pressing the gang turns its targets on or off.
- When targets change elsewhere (app, voice, automations), the gang follows:
  on while any target is on, off otherwise.

Links are edited in the "Gang Links" sidebar panel and stored in
.storage/gang_link. Set up from Settings > Devices & services.
"""

from __future__ import annotations

from functools import partial
import logging
from pathlib import Path
from time import monotonic
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

_LOGGER = logging.getLogger(__name__)

DOMAIN = "gang_link"
STORAGE_VERSION = 1
PANEL_PATH = "gang-link"
PANEL_URL = "/gang_link/gang-link-panel.js"

# After a press, ignore target updates for this long so targets that report
# their new state one by one don't bounce the gang back.
HOLD_SECONDS = 5
# Wait for target updates to settle before syncing the gang.
SETTLE_SECONDS = 1

# Setup is UI only. A leftover `gang_link:` YAML key logs an error and raises
# a repair issue asking to remove it, but doesn't block startup.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the panel file and websocket API.

    Runs once per Home Assistant start. Neither can be unregistered, so they
    stay in place across config entry reloads.
    """
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_set)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_URL,
                str(Path(__file__).parent / "frontend" / "gang-link-panel.js"),
                cache_headers=False,
            )
        ]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load links, start syncing, and add the sidebar panel."""
    links = GangLinks(hass)
    await links.async_load()
    hass.data[DOMAIN] = links

    # The version in the URL makes browsers fetch the panel again after updates.
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name="gang-link-panel",
        sidebar_title="Gang Links",
        sidebar_icon="mdi:light-switch",
        module_url=f"{PANEL_URL}?v={integration.version}",
        require_admin=True,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove the panel and stop syncing. Stored links are kept."""
    frontend.async_remove_panel(hass, PANEL_PATH)
    hass.data.pop(DOMAIN).async_stop()
    return True


class GangLinks:
    """Stores gang -> targets links and keeps both sides in sync."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.links: dict[str, list[str]] = {}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, DOMAIN)
        self._unsub: CALLBACK_TYPE | None = None
        self._unsub_started: CALLBACK_TYPE | None = None
        # Context of the last gang change we made, so it isn't pushed to targets.
        self._own_context: dict[str, str] = {}
        self._hold_until: dict[str, float] = {}
        self._pending: dict[str, CALLBACK_TYPE] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.links = data.get("links", {})
        self._track()
        self._unsub_started = async_at_started(self.hass, self._reconcile_all)

    @callback
    def async_stop(self) -> None:
        """Stop listening and cancel pending syncs."""
        if self._unsub_started:
            self._unsub_started()
        if self._unsub:
            self._unsub()
            self._unsub = None
        for cancel in self._pending.values():
            cancel()
        self._pending.clear()

    async def async_set(self, gang: str, targets: list[str]) -> None:
        """Replace a gang's targets. An empty list removes the link."""
        targets = [t for t in dict.fromkeys(targets) if t != gang]
        if targets:
            self.links[gang] = targets
        else:
            self.links.pop(gang, None)
        await self._store.async_save({"links": self.links})
        self._track()
        # A new link adopts the targets' current state instead of changing them.
        self._schedule_reconcile(gang, 0)

    @callback
    def _track(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        entities = set(self.links).union(*self.links.values())
        if entities:
            self._unsub = async_track_state_change_event(
                self.hass, list(entities), self._on_state_change
            )

    @callback
    def _reconcile_all(self, _hass: HomeAssistant) -> None:
        self._unsub_started = None
        for gang in self.links:
            self._schedule_reconcile(gang, 0)

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None:
            return
        if entity_id in self.links:
            self._on_gang_change(entity_id, old, new, event.context)
        for gang, targets in self.links.items():
            if entity_id in targets:
                self._schedule_reconcile(gang, SETTLE_SECONDS)

    @callback
    def _on_gang_change(
        self, gang: str, old: State | None, new: State, context: Context
    ) -> None:
        if new.state not in (STATE_ON, STATE_OFF):
            return
        if old is None or old.state not in (STATE_ON, STATE_OFF):
            # The gang came back (HA start, power cut): match it to the
            # targets instead of driving them with whatever state it booted in.
            self._schedule_reconcile(gang, 0)
            return
        if old.state == new.state:
            return
        if self._own_context.get(gang) == context.id:
            del self._own_context[gang]
            return

        self._hold_until[gang] = monotonic() + HOLD_SECONDS
        self.hass.async_create_task(
            self._call(f"turn_{new.state}", self.links[gang], Context())
        )

    @callback
    def _schedule_reconcile(self, gang: str, delay: float) -> None:
        if cancel := self._pending.pop(gang, None):
            cancel()
        delay = max(delay, self._hold_until.get(gang, 0) - monotonic())
        self._pending[gang] = async_call_later(
            self.hass, delay, partial(self._reconcile, gang)
        )

    @callback
    def _reconcile(self, gang: str, _now: Any = None) -> None:
        """Set the gang to on if any target is on, off if all are off."""
        self._pending.pop(gang, None)
        gang_state = self.hass.states.get(gang)
        targets = self.links.get(gang)
        if not targets or gang_state is None:
            return
        if gang_state.state not in (STATE_ON, STATE_OFF):
            return
        states = [
            state.state
            for target in targets
            if (state := self.hass.states.get(target))
            and state.state in (STATE_ON, STATE_OFF)
        ]
        if not states:
            return
        wanted = STATE_ON if STATE_ON in states else STATE_OFF
        if gang_state.state == wanted:
            return

        context = Context()
        self._own_context[gang] = context.id
        self.hass.async_create_task(self._call(f"turn_{wanted}", [gang], context))

    async def _call(self, service: str, entity_ids: list[str], context: Context) -> None:
        try:
            await self.hass.services.async_call(
                "homeassistant",
                service,
                {"entity_id": entity_ids},
                blocking=True,
                context=context,
            )
        except Exception:  # noqa: BLE001 - a failing light must not break syncing
            _LOGGER.exception("Failed to %s %s", service, ", ".join(entity_ids))


@websocket_api.websocket_command({vol.Required("type"): "gang_link/list"})
@callback
def ws_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return all links."""
    if (links := _loaded(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], {"links": links.links})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "gang_link/set",
        vol.Required("gang"): cv.entity_id,
        vol.Required("targets"): [cv.entity_id],
    }
)
@websocket_api.async_response
async def ws_set(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Replace one gang's targets and return all links."""
    if (links := _loaded(hass, connection, msg)) is None:
        return
    await links.async_set(msg["gang"], msg["targets"])
    connection.send_result(msg["id"], {"links": links.links})


def _loaded(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> GangLinks | None:
    """Return the links, or send an error if the integration isn't set up."""
    links: GangLinks | None = hass.data.get(DOMAIN)
    if links is None:
        connection.send_error(msg["id"], "not_loaded", "Gang Link is not set up")
    return links
