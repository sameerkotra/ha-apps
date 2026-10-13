"""The integration's side of the household apps bus (APP_MESSAGES_SPEC.md §6.7): no Home Assistant imports here, so it
can be tested with the standard library alone.

    assist.ask     here → household_assistant   {requested_by: <HA user id>, name?, text, scope?: "person"}
                   answered at once with ack {} (or nack: not_allowed "off" while the app's *Answer Assist* is off,
                   invalid <field>, busy, unsupported_kind from an older app, unsigned when the app has a signing
                   key and this side hasn't). scope "person": only tools about the person (voice satellites, when
                   the integration's *household-wide tools off for voice* is on).
    assist.answer  household_assistant → here   a reply to the ask, when the question has ended:
                   {question, state: done|failed|stopped, answer?, error?, sources: [app names]} — acked, since the
                   app re-sends it until it is

Messages are household_apps events; this side keeps no outbox: a question that gets no ack is told to the person.

Signing: with the same *app-messages signing key* as the household apps (the integration's options; App settings in
each app), every message from here carries `sig` = HMAC-SHA256 (hex) over
`id|from|to|kind|sent|expires|json(data, sort_keys, compact, ensure_ascii=False)` — exactly the shared app_bus's
`sign()` — and only replies with a valid `sig` are believed. Without a key, nothing is signed or checked (as before).
"""
from __future__ import annotations

import hashlib
import hmac
import json
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
UNKNOWN_USER = ("I don't know who is asking: that Home Assistant user doesn't exist or is turned off. In the Household "
                "Assistant integration's options, choose the person who answers for voice satellites.")
NO_APP = "The Household Assistant app isn't answering. Is it installed and running?"
UNSIGNED = ("The Household Assistant app wants signed messages. Enter its app-messages signing key in the Household "
            "Assistant integration's options.")
SIGNED_FIELDS = ("id", "from", "to", "kind", "sent", "expires")
BUSY_RETRY_SECONDS = 2.0                  # a "busy" app is asked once more after this
TOO_LONG = "The Household Assistant is taking too long. The answer will be on its page."
TOO_LONG_QUESTION = f"That question is too long: at most {MAX_TEXT} characters."


def new_id(now: datetime | None = None) -> str:
    """A ULID, as every app on the bus makes them: 48-bit milliseconds and 80 random bits, Crockford base-32."""
    ms = int((now or datetime.now(timezone.utc)).timestamp() * 1000)
    n = (ms << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def _key_bytes(key) -> bytes | None:
    if not key:
        return None
    return key.encode("utf-8") if isinstance(key, str) else bytes(key)


def sign(env: dict, key) -> str:
    """HMAC-SHA256 (hex) of id|from|to|kind|sent|expires|canonical json(data) — the shared app_bus's sign()."""
    k = _key_bytes(key)
    if k is None:
        raise ValueError("no signing key")
    data = env.get("data") if isinstance(env.get("data"), dict) else {}
    text = ("|".join(str(env.get(f) or "") for f in SIGNED_FIELDS) + "|"
            + json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return hmac.new(k, text.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(env: dict, key) -> bool:
    """True when `env` carries the signature `key` would have made (always True without a key)."""
    if not _key_bytes(key):
        return True
    sig = env.get("sig")
    if not isinstance(sig, str) or len(sig) != 64:
        return False
    try:
        return hmac.compare_digest(sign(env, key), sig.lower())
    except (ValueError, TypeError):
        return False


def signed(env: dict, key) -> dict:
    """`env` with its `sig` when there is a key; unchanged without one."""
    if _key_bytes(key):
        env["sig"] = sign(env, key)
    return env


def envelope(kind: str, data: dict, *, to: str = APP, ref: str | None = None, reply_to: str | None = None,
             now: datetime | None = None, expires_in: timedelta = timedelta(minutes=5), key=None) -> dict:
    now = now or datetime.now(timezone.utc)
    return signed({"id": new_id(now), "v": ENVELOPE_VERSION, "from": SLUG, "to": to, "kind": kind, "kv": 1,
                   "reply_to": reply_to, "ref": ref, "sent": now.isoformat(timespec="seconds"),
                   "expires": (now + expires_in).isoformat(timespec="seconds"), "data": data}, key)


def ask(text: str, user_id: str, name: str | None = None, now: datetime | None = None, *, key=None,
        person_only: bool = False) -> dict:
    """The assist.ask for one question; its ref is "assist:<its id>". `person_only`: only tools about the person."""
    data = {"requested_by": user_id, "text": text}
    if name:
        data["name"] = name[:80]
    if person_only:
        data["scope"] = "person"
    now = now or datetime.now(timezone.utc)
    env = envelope(ASK_KIND, data, now=now)
    env["ref"] = f"assist:{env['id']}"
    return signed(env, key)                      # signed after the ref is set (the ref isn't signed, but kept)


def hello(version: str, now: datetime | None = None, *, key=None, to: str = "*") -> dict:
    """Said when the integration starts, and to the app when it says hello (it started again), so the app's
    Connected apps list shows it."""
    return envelope("hello", {"name": "Home Assistant Assist", "version": version, "can": [ANSWER_KIND],
                              "wants": []}, to=to, now=now, key=key)


def ack(env: dict, now: datetime | None = None, *, key=None) -> dict:
    return envelope("ack", {}, to=env["from"], ref=env.get("ref"), reply_to=env["id"], now=now,
                    expires_in=timedelta(days=1), key=key)


def wants_hello(env, key=None) -> bool:
    """True for the app's own hello to everyone (it has just started) or its `who`: answer with ours, to it."""
    return (isinstance(env, dict) and env.get("from") == APP and env.get("kind") in ("hello", "who")
            and env.get("to") in ("*", SLUG) and env.get("v") == ENVELOPE_VERSION and verify(env, key))


class Exchange:
    """One question's messages. Feed it every household_apps event's data; `acked` once the app took the question,
    `outcome` once it has ended: ("answer", data) or ("refused", reason, detail)."""

    def __init__(self, ask_env: dict, key=None):
        self.ask = ask_env
        self.key = key or None
        self.acked = False
        self.outcome: tuple | None = None

    def feed(self, env) -> dict | None:
        """Read one message; returns an ack to fire when it was the answer (also when it comes again). With a key,
        a reply without a valid signature is ignored (anyone on the bus could have fired it)."""
        if not isinstance(env, dict) or env.get("to") != SLUG or env.get("from") != APP \
                or env.get("reply_to") != self.ask["id"] or not isinstance(env.get("id"), str):
            return None
        if not verify(env, self.key):
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
            return ack(env, key=self.key)
        return None

    @property
    def busy(self) -> bool:
        """The app said it was busy (worth asking once more)."""
        return self.outcome is not None and self.outcome[0] == "refused" and self.outcome[1] == "busy"

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
    if reason == "unsigned":
        return UNSIGNED
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
