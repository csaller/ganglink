"""Config flow for Gang Link. There is nothing to configure."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from . import DOMAIN


class GangLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Confirm and create the single Gang Link entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Gang Link", data={})
        return self.async_show_form(step_id="user")
