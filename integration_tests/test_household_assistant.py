"""The integration as Home Assistant runs it: set up, options, and Assist turns answered by a stand-in for the
Household Assistant app on the event bus (the app's own side is tested in household_assistant/tests): signed messages
with a key, a question back keeps the conversation open, voice satellites limited to the person's own tools, a busy
app asked once more, hello again when the app starts, and the recorder's Repairs issue."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from homeassistant.helpers import issue_registry as ir

from custom_components.household_assistant import check_recorder, protocol
from custom_components.household_assistant.const import (CONF_SIGNING_KEY, CONF_TIMEOUT, CONF_VOICE_PERSON_ONLY,
                                                         CONF_VOICE_USER, DOMAIN, RECORDER_ISSUE)

KEY = "an-example-signing-key"

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

    def __init__(self, hass, answer=None, nack=None, silent=False, key=None, nacks=()):
        self.hass, self.answer, self.nack, self.silent, self.key = hass, answer, nack, silent, key
        self.nacks = list(nacks)                          # refusals for the first asks, then answers
        self.asked, self.acks, self.hellos = [], [], []
        self.stop = hass.bus.async_listen(protocol.EVENT_TYPE, self.on_event)

    def reply(self, env, kind, data):
        self.hass.bus.async_fire(protocol.EVENT_TYPE, protocol.signed({
            "id": protocol.new_id(), "v": 1, "from": protocol.APP, "to": env["from"], "kind": kind, "kv": 1,
            "reply_to": env["id"], "ref": env["ref"], "sent": env["sent"], "expires": env["expires"], "data": data},
            self.key))

    @callback
    def on_event(self, event):
        env = event.data
        if env.get("to") == protocol.APP and env.get("kind") == protocol.ASK_KIND:
            self.asked.append(env)
            if self.silent:
                return
            if self.nacks:
                self.reply(env, "nack", self.nacks.pop(0))
                return
            if self.nack:
                self.reply(env, "nack", self.nack)
                return
            self.reply(env, "ack", {"result": {}})
            self.reply(env, protocol.ANSWER_KIND, self.answer)
        elif env.get("to") == protocol.APP and env.get("kind") == "ack":
            self.acks.append(env)
        elif env.get("kind") == "hello" and env.get("from") == protocol.SLUG:
            self.hellos.append(env)


async def converse(hass, text="What's on my list today?", user_id=None, conversation_id=None):
    return await conversation.async_converse(hass, text, conversation_id, Context(user_id=user_id), agent_id=AGENT)


async def ask(hass, text="What's on my list today?", user_id=None):
    result = await converse(hass, text, user_id)
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


async def test_unknown_or_inactive_users_are_not_asked_for(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    app = FakeApp(hass, answer={"state": "done", "answer": "Hi."})
    assert await ask(hass, user_id="0123456789abcdef0123456789abcdef") == protocol.UNKNOWN_USER   # no such user
    hass_admin_user.is_active = False
    assert await ask(hass, user_id=hass_admin_user.id) == protocol.UNKNOWN_USER                  # turned off
    assert app.asked == []


async def test_signed_messages_with_a_key(hass: HomeAssistant, hass_admin_user):
    app = FakeApp(hass, answer={"state": "done", "answer": "Signed hello."}, key=KEY)
    await set_up(hass, {CONF_SIGNING_KEY: KEY})                          # set up, then reloaded with the key
    assert await ask(hass, user_id=hass_admin_user.id) == "Signed hello."
    assert protocol.verify(app.asked[0], KEY) and app.asked[0]["sig"]
    await hass.async_block_till_done()
    assert protocol.verify(app.acks[0], KEY) and "sig" in app.acks[0]
    assert app.hellos[-1]["sig"] and protocol.verify(app.hellos[-1], KEY)   # the hello at start-up too


async def test_unsigned_or_forged_replies_are_ignored(hass: HomeAssistant, hass_admin_user):
    await set_up(hass, {CONF_SIGNING_KEY: KEY})
    FakeApp(hass, answer={"state": "done", "answer": "Forged."}, key="another-key")
    with patch("custom_components.household_assistant.conversation.ACK_SECONDS", 0.2):
        assert await ask(hass, user_id=hass_admin_user.id) == protocol.NO_APP


async def test_a_key_the_app_wants_is_explained(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    FakeApp(hass, nack={"reason": "unsigned", "detail": "no valid sig"})
    assert await ask(hass, user_id=hass_admin_user.id) == protocol.UNSIGNED


async def test_a_question_back_keeps_the_conversation_open(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    app = FakeApp(hass, answer={"state": "done", "answer": "Which month?"})
    result = await converse(hass, "How much did we spend?", hass_admin_user.id)
    assert result.response.speech["plain"]["speech"] == "Which month?"
    assert result.continue_conversation is True
    assert result.conversation_id
    app.stop()
    FakeApp(hass, answer={"state": "done", "answer": "Spent 1,234.50."})
    result = await converse(hass, "September", hass_admin_user.id, result.conversation_id)
    assert result.continue_conversation is False


async def test_voice_satellites_only_the_persons_own_tools(hass: HomeAssistant, hass_admin_user):
    await set_up(hass, {CONF_VOICE_USER: hass_admin_user.id, CONF_VOICE_PERSON_ONLY: True})
    app = FakeApp(hass, answer={"state": "done", "answer": "Hi."})
    assert await ask(hass) == "Hi."                                       # a satellite: no user
    assert app.asked[0]["data"]["scope"] == "person"
    assert await ask(hass, user_id=hass_admin_user.id) == "Hi."           # the person themselves: not limited
    assert "scope" not in app.asked[1]["data"]


async def test_a_busy_app_is_asked_once_more(hass: HomeAssistant, hass_admin_user):
    await set_up(hass)
    app = FakeApp(hass, answer={"state": "done", "answer": "Now."}, nacks=[{"reason": "busy"}])
    with patch.object(protocol, "BUSY_RETRY_SECONDS", 0.01):
        assert await ask(hass, user_id=hass_admin_user.id) == "Now."
    assert len(app.asked) == 2
    app.stop()
    app = FakeApp(hass, nacks=[{"reason": "busy"}] * 3)
    with patch.object(protocol, "BUSY_RETRY_SECONDS", 0.01):
        assert await ask(hass, user_id=hass_admin_user.id) == "The Household Assistant is busy. Try again in a moment."


async def test_hello_again_when_the_app_starts(hass: HomeAssistant):
    app = FakeApp(hass)
    await set_up(hass)
    assert [h["to"] for h in app.hellos] == ["*"]
    app_hello = {"id": protocol.new_id(), "v": 1, "from": protocol.APP, "to": "*", "kind": "hello", "kv": 1,
                 "reply_to": None, "ref": None, "sent": "2026-10-13T08:00:00+00:00",
                 "expires": "2026-10-14T08:00:00+00:00", "data": {"name": "Household Assistant", "can": []}}
    hass.bus.async_fire(protocol.EVENT_TYPE, app_hello)
    await hass.async_block_till_done()
    assert [h["to"] for h in app.hellos] == ["*", protocol.APP]
    hass.bus.async_fire(protocol.EVENT_TYPE, {**app_hello, "id": protocol.new_id()})    # again at once: once a minute
    hass.bus.async_fire(protocol.EVENT_TYPE, {**app_hello, "id": protocol.new_id(), "from": "household_todo"})
    await hass.async_block_till_done()
    assert len(app.hellos) == 2


async def test_recorder_repairs_issue(hass: HomeAssistant):
    await set_up(hass)
    registry = ir.async_get(hass)
    hass.config.components.add("recorder")
    hass.data["recorder_instance"] = SimpleNamespace(exclude_event_types=set())
    check_recorder(hass)
    assert registry.async_get_issue(DOMAIN, RECORDER_ISSUE) is not None
    hass.data["recorder_instance"] = SimpleNamespace(exclude_event_types={protocol.EVENT_TYPE})
    check_recorder(hass)
    assert registry.async_get_issue(DOMAIN, RECORDER_ISSUE) is None
    hass.data.pop("recorder_instance")


async def test_options_signing_key_and_voice_switch(hass: HomeAssistant, hass_admin_user):
    entry = await set_up(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_VOICE_USER: hass_admin_user.id, CONF_TIMEOUT: 60, CONF_SIGNING_KEY: "has a space"})
    assert (result["type"], result["errors"]) == (FlowResultType.FORM, {CONF_SIGNING_KEY: "bad_key"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_VOICE_USER: hass_admin_user.id, CONF_TIMEOUT: 60, CONF_SIGNING_KEY: f" {KEY} ",
                            CONF_VOICE_PERSON_ONLY: True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_VOICE_USER: hass_admin_user.id, CONF_TIMEOUT: 60, CONF_SIGNING_KEY: KEY,
                             CONF_VOICE_PERSON_ONLY: True}
