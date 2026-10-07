"""Setting up the Household Assistant integration: one entry; options for voice satellites and the wait."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (NumberSelector, NumberSelectorConfig, NumberSelectorMode, SelectOptionDict,
                                            SelectSelector, SelectSelectorConfig, SelectSelectorMode)

from .const import CONF_TIMEOUT, CONF_VOICE_USER, DEFAULT_TIMEOUT, DOMAIN


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Household Assistant", data={},
                                           options={CONF_TIMEOUT: DEFAULT_TIMEOUT})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            options = {CONF_TIMEOUT: int(user_input[CONF_TIMEOUT])}
            if user_input.get(CONF_VOICE_USER):
                options[CONF_VOICE_USER] = user_input[CONF_VOICE_USER]
            return self.async_create_entry(data=options)
        users = [u for u in await self.hass.auth.async_get_users() if u.is_active and not u.system_generated]
        choices = [SelectOptionDict(value="", label="Nobody: say who is asking isn't known")]
        choices += [SelectOptionDict(value=u.id, label=u.name or u.id) for u in sorted(users, key=lambda u: u.name or "")]
        current = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_VOICE_USER, default=current.get(CONF_VOICE_USER, "")): SelectSelector(
                SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)),
            vol.Required(CONF_TIMEOUT, default=current.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): NumberSelector(
                NumberSelectorConfig(min=30, max=900, step=10, unit_of_measurement="s", mode=NumberSelectorMode.BOX)),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
