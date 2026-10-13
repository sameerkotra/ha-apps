"""The conversation agent: one Assist turn is one question in the Household Assistant app.

On Home Assistant with chat logs (the conversation entity's `_async_handle_message`), the answer goes into the
conversation's chat log, and an answer that is a question back ("Which month?") keeps the conversation open, so the
reply is heard without the wake word again (`continue_conversation`). Older Home Assistant versions get the same
answer through `async_process`, without the follow-up.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol
from .const import (ACK_SECONDS, CONF_SIGNING_KEY, CONF_TIMEOUT, CONF_VOICE_PERSON_ONLY, CONF_VOICE_USER,
                    DEFAULT_TIMEOUT, DOMAIN, VERSION)

# Home Assistant 2025 and newer: the entity answers through `_async_handle_message(user_input, chat_log)`.
CHAT_LOG = hasattr(conversation.ConversationEntity, "_async_handle_message") and hasattr(conversation, "ChatLog")
CONTINUES = "continue_conversation" in {f.name for f in dataclasses.fields(conversation.ConversationResult)}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([HouseholdAssistantAgent(entry)])


def asks_back(speech: str) -> bool:
    """An answer that is a question back keeps the conversation open (as Home Assistant's own chat log decides)."""
    return speech.strip().endswith(("?", ";", "？", "；"))


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

    def _result(self, user_input: conversation.ConversationInput, speech: str, conversation_id, more: bool):
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_speech(speech)
        extra = {"continue_conversation": more} if CONTINUES else {}
        return conversation.ConversationResult(response=response, conversation_id=conversation_id, **extra)

    if CHAT_LOG:
        async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                        chat_log: conversation.ChatLog) -> conversation.ConversationResult:
            speech = await self._ask(user_input)
            chat_log.async_add_assistant_content_without_tools(conversation.AssistantContent(
                agent_id=user_input.agent_id or self.entity_id, content=speech))
            more = bool(getattr(chat_log, "continue_conversation", asks_back(speech)))
            return self._result(user_input, speech, chat_log.conversation_id, more)
    else:                                                         # older Home Assistant: no chat log
        async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
            speech = await self._ask(user_input)
            return self._result(user_input, speech, user_input.conversation_id, asks_back(speech))

    async def _ask(self, user_input: conversation.ConversationInput) -> str:
        text = (user_input.text or "").strip()
        if len(text) > protocol.MAX_TEXT:
            return protocol.TOO_LONG_QUESTION
        voice = not user_input.context.user_id                   # a voice satellite: asked as the chosen person
        user_id = user_input.context.user_id or self.entry.options.get(CONF_VOICE_USER)
        if not user_id:
            return protocol.NO_USER
        user = await self.hass.auth.async_get_user(user_id)
        if user is None or not user.is_active:          # removed or turned off in Home Assistant: never asked as them
            return protocol.UNKNOWN_USER
        key = self.entry.options.get(CONF_SIGNING_KEY) or None
        person_only = voice and bool(self.entry.options.get(CONF_VOICE_PERSON_ONLY))
        exchange = await self._exchange(protocol.ask(text, user_id, user.name, key=key, person_only=person_only), key)
        if exchange.busy:                               # the app was busy: once more, a moment later
            await asyncio.sleep(protocol.BUSY_RETRY_SECONDS)
            exchange = await self._exchange(protocol.ask(text, user_id, user.name, key=key, person_only=person_only),
                                            key)
        return exchange.words()

    async def _exchange(self, env: dict, key) -> protocol.Exchange:
        """Fire one assist.ask and wait for its ack, then its answer (or a refusal), within the options' wait."""
        exchange = protocol.Exchange(env, key)
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
        return exchange
