"""The conversation agent: one Assist turn is one question in the Household Assistant app."""
from __future__ import annotations

import asyncio
import time

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol
from .const import ACK_SECONDS, CONF_TIMEOUT, CONF_VOICE_USER, DEFAULT_TIMEOUT, DOMAIN, VERSION


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HouseholdAssistantAgent(entry)])


class HouseholdAssistantAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Household Assistant",
                                            manufacturer="Household apps", sw_version=VERSION,
                                            entry_type=DeviceEntryType.SERVICE)

    @property
    def supported_languages(self) -> list[str] | str:
        return MATCH_ALL

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(await self._ask(user_input))
        return conversation.ConversationResult(response=response, conversation_id=user_input.conversation_id)

    async def _ask(self, user_input: conversation.ConversationInput) -> str:
        text = (user_input.text or "").strip()
        if len(text) > protocol.MAX_TEXT:
            return protocol.TOO_LONG_QUESTION
        user_id = user_input.context.user_id or self.entry.options.get(CONF_VOICE_USER)
        if not user_id:
            return protocol.NO_USER
        user = await self.hass.auth.async_get_user(user_id)
        env = protocol.ask(text, user_id, user.name if user else None)
        exchange = protocol.Exchange(env)
        changed = asyncio.Event()

        @callback
        def on_event(event: Event) -> None:
            reply = exchange.feed(event.data)
            if reply is not None:
                self.hass.bus.async_fire(protocol.EVENT_TYPE, reply)
            changed.set()

        unsubscribe = self.hass.bus.async_listen(protocol.EVENT_TYPE, on_event)
        try:
            self.hass.bus.async_fire(protocol.EVENT_TYPE, env)
            start = time.monotonic()
            limit = float(self.entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))
            while exchange.outcome is None:
                waited = time.monotonic() - start
                left = (ACK_SECONDS if not exchange.acked else limit) - waited
                if left <= 0:
                    break
                changed.clear()
                try:
                    await asyncio.wait_for(changed.wait(), left)
                except asyncio.TimeoutError:
                    pass
        finally:
            unsubscribe()
        return exchange.words()
