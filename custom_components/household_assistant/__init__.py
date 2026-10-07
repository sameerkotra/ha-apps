"""Household Assistant: Home Assistant's Assist asks the Household Assistant app (HOUSEHOLD_ASSISTANT_SPEC.md §2.2).

A conversation agent: pick "Household Assistant" as the conversation agent of an Assist pipeline (Settings → Voice
assistants) and what is said is asked in the app as the person speaking, over the household apps bus (protocol.py);
the app's answer is spoken. The app must run, with App settings → *Answer Assist* turned on.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from . import protocol
from .const import VERSION

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.bus.async_fire(protocol.EVENT_TYPE, protocol.hello(VERSION))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
