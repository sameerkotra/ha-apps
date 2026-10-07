"""The integration as Home Assistant runs it: set up, options, and Assist turns answered by a stand-in for the
Household Assistant app on the event bus (the app's own side is tested in household_assistant/tests)."""
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.household_assistant import protocol
from custom_components.household_assistant.const import CONF_TIMEOUT, CONF_VOICE_USER, DOMAIN

AGENT = "conversation.household_assistant"


async def set_up(hass: HomeAssistant, options=None):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    if options:
        hass.config_entries.async_update_entry(entry, options={**entry.options, **options})
    await hass.async_block_till_done()
    return entry


class FakeApp:
    """The Household Assistant app on the bus: takes assist.ask, then answers with `answer` (or refuses)."""

    def __init__(self, hass, answer=None, nack=None, silent=False):
        self.hass, self.answer, self.nack, self.silent = hass, answer, nack, silent
        self.asked, self.acks = [], []
        hass.bus.async_listen(protocol.EVENT_TYPE, self.on_event)

    def reply(self, env, kind, data):
        self.hass.bus.async_fire(protocol.EVENT_TYPE, {
            "id": protocol.new_id(), "v": 1, "from": protocol.APP, "to": env["from"], "kind": kind, "kv": 1,
            "reply_to": env["id"], "ref": env["ref"], "sent": env["sent"], "expires": env["expires"], "data": data})

    @callback
    def on_event(self, event):
        env = event.data
        if env.get("to") == protocol.APP and env.get("kind") == protocol.ASK_KIND:
            self.asked.append(env)
            if self.silent:
                return
            if self.nack:
                self.reply(env, "nack", self.nack)
                return
            self.reply(env, "ack", {"result": {}})
            self.reply(env, protocol.ANSWER_KIND, self.answer)
        elif env.get("to") == protocol.APP and env.get("kind") == "ack":
            self.acks.append(env)


async def ask(hass, text="What's on my list today?", user_id=None):
    result = await conversation.async_converse(hass, text, None, Context(user_id=user_id), agent_id=AGENT)
    return result.response.speech["plain"]["speech"]


async def test_set_up_once_and_options(hass: HomeAssistant, hass_admin_user):
    entry = await set_up(hass)
    assert hass.states.get(AGENT) is not None
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "already_configured")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_VOICE_USER: hass_admin_user.id, CONF_TIMEOUT: 120})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_VOICE_USER: hass_admin_user.id, CONF_TIMEOUT: 120}


async def test_a_question_answered(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    app = FakeApp(hass, answer={"question": "q1", "state": "done", "answer": "Two tasks today:\n- **Bins**\n- Call plumber",
                                "sources": ["Household Todo"]})
    assert await ask(hass, user_id=hass_admin_user.id) == "Two tasks today: Bins. Call plumber"
    sent = app.asked[0]
    assert (sent["from"], sent["data"]) == (protocol.SLUG, {"requested_by": hass_admin_user.id,
                                                             "name": hass_admin_user.name,
                                                             "text": "What's on my list today?"})
    await hass.async_block_till_done()
    assert len(app.acks) == 1                             # the answer is acked, so the app stops re-sending it


async def test_voice_satellites_need_a_person(hass: HomeAssistant, hass_admin_user):
    entry = await set_up(hass)
    app = FakeApp(hass, answer={"state": "done", "answer": "Hi."})
    assert await ask(hass) == protocol.NO_USER
    assert app.asked == []
    hass.config_entries.async_update_entry(entry, options={CONF_VOICE_USER: hass_admin_user.id, CONF_TIMEOUT: 60})
    await hass.async_block_till_done()
    assert await ask(hass) == "Hi."
    assert app.asked[0]["data"]["requested_by"] == hass_admin_user.id


async def test_refusals_and_silence(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    FakeApp(hass, nack={"reason": "not_allowed", "detail": "off"})
    assert "turn on Answer Assist" in await ask(hass, user_id=hass_admin_user.id)


async def test_no_app(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    FakeApp(hass, silent=True)
    with patch("custom_components.household_assistant.conversation.ACK_SECONDS", 0.2):
        assert await ask(hass, user_id=hass_admin_user.id) == protocol.NO_APP


async def test_failed_question_in_words(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    FakeApp(hass, answer={"state": "failed", "error": "That took too long — try a narrower question."})
    assert await ask(hass, user_id=hass_admin_user.id) == "That took too long — try a narrower question."
