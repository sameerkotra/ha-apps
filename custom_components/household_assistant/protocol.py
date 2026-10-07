"""The integration's side of the household apps bus (APP_MESSAGES_SPEC.md §6.7): no Home Assistant imports here, so it
can be tested with the standard library alone.

    assist.ask     here → household_assistant   {requested_by: <HA user id>, name?, text}
                   answered at once with ack {} (or nack: not_allowed "off" while the app's *Answer Assist* is off,
                   invalid <field>, busy, unsupported_kind from an older app)
    assist.answer  household_assistant → here   a reply to the ask, when the question has ended:
                   {question, state: done|failed|stopped, answer?, error?, sources: [app names]} — acked, since the
                   app re-sends it until it is

Messages are household_apps events; this side keeps no outbox: a question that gets no ack is told to the person.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

EVENT_TYPE = "household_apps"
SLUG = "ha_assist"                       # this integration on the bus
APP = "household_assistant"              # the Household Assistant app
ASK_KIND = "assist.ask"
ANSWER_KIND = "assist.answer"
ENVELOPE_VERSION = 1
MAX_TEXT = 1000                          # the app's longest question

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

NO_USER = ("I don't know who is asking. In the Household Assistant integration's options, choose the person who "
           "answers for voice satellites.")
NO_APP = "The Household Assistant app isn't answering. Is it installed and running?"
TOO_LONG = "The Household Assistant is taking too long. The answer will be on its page."
TOO_LONG_QUESTION = f"That question is too long: at most {MAX_TEXT} characters."


def new_id(now: datetime | None = None) -> str:
    """A ULID, as every app on the bus makes them: 48-bit milliseconds and 80 random bits, Crockford base-32."""
    ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    n = (ms << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def envelope(kind: str, data: dict, *, to: str = APP, ref: str | None = None, reply_to: str | None = None,
             now: datetime | None = None, expires_in: timedelta = timedelta(minutes=5)) -> dict:
    now = now or datetime.now(timezone.utc)
    return {"id": new_id(now), "v": ENVELOPE_VERSION, "from": SLUG, "to": to, "kind": kind, "kv": 1,
            "reply_to": reply_to, "ref": ref, "sent": now.isoformat(timespec="seconds"),
            "expires": (now + expires_in).isoformat(timespec="seconds"), "data": data}


def ask(text: str, user_id: str, name: str | None = None, now: datetime | None = None) -> dict:
    """The assist.ask for one question; its ref is "assist:<its id>"."""
    data = {"requested_by": user_id, "text": text}
    if name:
        data["name"] = name[:80]
    env = envelope(ASK_KIND, data, now=now)
    env["ref"] = f"assist:{env['id']}"
    return env


def hello(version: str, now: datetime | None = None) -> dict:
    """Said once when the integration starts, so the app's Connected apps list shows it."""
    return envelope("hello", {"name": "Home Assistant Assist", "version": version, "can": [ANSWER_KIND],
                              "wants": []}, to="*", now=now)


def ack(env: dict, now: datetime | None = None) -> dict:
    return envelope("ack", {}, to=env["from"], ref=env.get("ref"), reply_to=env["id"], now=now,
                    expires_in=timedelta(days=1))


class Exchange:
    """One question's messages. Feed it every household_apps event's data; `acked` once the app took the question,
    `outcome` once it has ended: ("answer", data) or ("refused", reason, detail)."""

    def __init__(self, ask_env: dict):
        self.ask = ask_env
        self.acked = False
        self.outcome: tuple | None = None

    def feed(self, env) -> dict | None:
        """Read one message; returns an ack to fire when it was the answer (also when it comes again)."""
        if not isinstance(env, dict) or env.get("to") != SLUG or env.get("from") != APP \
                or env.get("reply_to") != self.ask["id"] or not isinstance(env.get("id"), str):
            return None
        data = env.get("data") if isinstance(env.get("data"), dict) else {}
        kind = env.get("kind")
        if kind == "ack":
            self.acked = True
        elif kind == "nack" and self.outcome is None:
            self.outcome = ("refused", str(data.get("reason") or ""), data.get("detail"))
        elif kind == ANSWER_KIND:
            self.acked = True
            if self.outcome is None:
                self.outcome = ("answer", data)
            return ack(env)
        return None

    def words(self) -> str:
        """What Assist says for the outcome."""
        if self.outcome is None:
            return TOO_LONG if self.acked else NO_APP
        if self.outcome[0] == "refused":
            return refusal_words(self.outcome[1], self.outcome[2])
        return answer_words(self.outcome[1])


def answer_words(data: dict) -> str:
    state = data.get("state")
    if state == "done":
        return plain(str(data.get("answer") or "")) or "The apps answered, but there were no words to say."
    if state == "stopped":
        return "The question was stopped."
    return str(data.get("error") or "Something went wrong while answering. Try again.")


def refusal_words(reason: str, detail) -> str:
    if reason == "not_allowed" and detail == "off":
        return ("The Household Assistant doesn't answer Assist yet. An admin can turn on Answer Assist in its "
                "App settings.")
    if reason == "unsupported_kind":
        return "This version of the Household Assistant app can't answer Assist. Update it."
    if reason == "busy":
        return "The Household Assistant is busy. Try again in a moment."
    if reason == "invalid":
        return "The Household Assistant couldn't read that question."
    return f"The Household Assistant didn't take the question ({reason or 'no reason given'})."


_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)
_HEADING = re.compile(r"^#+\s*", re.M)


def plain(markdown: str) -> str:
    """The answer as speech: no bold marks, list marks or headings; list items and paragraphs as sentences."""
    lines = [line.strip() for line in _HEADING.sub("", _LIST.sub("", _BOLD.sub(r"\1", markdown))).splitlines()]
    out = []
    for line in lines:
        if not line:
            continue
        if out and out[-1][-1] not in ".!?:;":
            out[-1] += "."
        out.append(line)
    return " ".join(out)
