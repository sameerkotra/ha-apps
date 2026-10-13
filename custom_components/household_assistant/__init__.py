"""Household Assistant: Home Assistant's Assist asks the Household Assistant app (HOUSEHOLD_ASSISTANT_SPEC.md §2.2).

A conversation agent: pick "Household Assistant" as the conversation agent of an Assist pipeline (Settings → Voice
assistants) and what is said is asked in the app as the person speaking, over the household apps bus (protocol.py);
the app's answer is spoken. The app must run, with App settings → *Answer Assist* turned on.

The integration says hello when it starts and again whenever the app says hello (it restarted), so the app's
Connected apps list keeps showing it. When Home Assistant's recorder keeps the household_apps events (the apps'
answers would stay in its history), a Repairs issue says how to exclude them.
"""
from __future__ import annotations

import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from . import protocol
from .const import CONF_SIGNING_KEY, DOMAIN, HELLO_EVERY, RECORDER_ISSUE, VERSION

PLATFORMS = [Platform.CONVERSATION]
LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    key = entry.options.get(CONF_SIGNING_KEY) or None
    hass.bus.async_fire(protocol.EVENT_TYPE, protocol.hello(VERSION, key=key))
    last = {"at": 0.0}

    @callback
    def on_event(event: Event) -> None:
        # the app started again (its hello to everyone) or asks who is there: say hello to it, at most once a minute
        if protocol.wants_hello(event.data, key) and time.monotonic() - last["at"] >= HELLO_EVERY:
            last["at"] = time.monotonic()
            hass.bus.async_fire(protocol.EVENT_TYPE, protocol.hello(VERSION, key=key, to=protocol.APP))

    entry.async_on_unload(hass.bus.async_listen(protocol.EVENT_TYPE, on_event))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    _check_recorder_when_started(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def recorder_keeps_messages(hass: HomeAssistant) -> bool | None:
    """True when the recorder runs and doesn't exclude household_apps events; None when it can't be told."""
    if "recorder" not in hass.config.components:
        return False
    instance = hass.data.get("recorder_instance")
    excluded = getattr(instance, "exclude_event_types", None)
    if excluded is None:
        return None
    return protocol.EVENT_TYPE not in excluded


def check_recorder(hass: HomeAssistant) -> None:
    """A Repairs issue while the recorder keeps the apps' messages (best effort: nothing when it can't be told)."""
    try:
        keeps = recorder_keeps_messages(hass)
    except Exception:  # noqa: BLE001 — a check that can't be made is no reason to fail setting up
        LOGGER.debug("Couldn't read the recorder's settings", exc_info=True)
        return
    if keeps:
        ir.async_create_issue(hass, DOMAIN, RECORDER_ISSUE, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                              translation_key=RECORDER_ISSUE, translation_placeholders={"event": protocol.EVENT_TYPE},
                              learn_more_url="https://www.home-assistant.io/integrations/recorder/")
    elif keeps is False:
        ir.async_delete_issue(hass, DOMAIN, RECORDER_ISSUE)


def _check_recorder_when_started(hass: HomeAssistant) -> None:
    try:
        from homeassistant.helpers.start import async_at_started
    except ImportError:                                  # very old Home Assistant: check now
        check_recorder(hass)
        return

    @callback
    def started(_hass: HomeAssistant) -> None:
        check_recorder(hass)

    async_at_started(hass, started)
