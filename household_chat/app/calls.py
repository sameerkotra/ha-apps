"""Voice calls (SPEC §15.12): one-to-one calls in direct chats, signalling only.

The sound goes straight between the two browsers (WebRTC). The app only passes
their descriptions (`sdp`) and network candidates between them, rings the
person called, and writes a note in the chat when the call ends.

Calls that are on live in memory, like the live-update hub (live.py): the app
runs ONE process (uvicorn with 1 worker, see the Dockerfile), so a dict is the
whole picture. A restart ends them; `close_unfinished()` closes their rows at
start-up. The `calls` table keeps the history, one row per call that rang.

A call: `new` (made by POST /calls, waiting for the caller's offer) → `ringing`
(the offer is here; the other person is rung) → `active` (answered) → ended.
Live `call` events go only to the two people; each says which call it is about,
and a page ignores calls it isn't handling.
"""
import logging
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException

from . import chats, config, db, notifier, settings
from .common import ha_notify
from .live import hub

logger = logging.getLogger("calls")

MAX_SDP = 16000
MAX_CANDIDATE = 1000
MAX_CANDIDATES = 50
OFFER_SECONDS = 20          # a call whose offer never comes is dropped (no note)
GONE_SECONDS = 60           # an answered call ends when one side has had no live connection this long
OUTCOMES = ("answered", "missed", "declined", "busy", "failed")


@dataclass
class Call:
    id: str
    conversation_id: str
    caller: str
    callee: str
    state: str = "new"                      # new | ringing | active
    made: float = field(default_factory=time.monotonic)
    offer: str | None = None
    answer: str | None = None
    candidates: dict = field(default_factory=dict)   # user → [candidate] sent before the other side could take them
    ring_until: float = 0.0
    started_at: str | None = None           # ISO, when it started ringing
    answered_at: str | None = None
    pushed: bool = False                    # a ringing notification went to the person called
    gone_since: dict = field(default_factory=dict)   # user → monotonic time they were first seen offline

    def other(self, uid: str) -> str:
        return self.callee if uid == self.caller else self.caller


_lock = threading.Lock()
_calls: dict[str, Call] = {}
_by_user: dict[str, str] = {}               # user → the call they're in (one at a time)


def _spawn(fn, *args) -> None:
    """Phone notifications go out from a thread, never from the request (tests run them inline)."""
    threading.Thread(target=fn, args=args, daemon=True, name="calls").start()


def reset() -> None:
    with _lock:
        _calls.clear()
        _by_user.clear()


def enabled(conn=None) -> bool:
    return bool(settings.get("calls_enabled", conn))


def ice_servers(conn=None) -> list:
    """STUN / relay addresses for the browsers. None yet: calls work on the home network (§15.12)."""
    return []


def busy(uid: str) -> bool:
    with _lock:
        return uid in _by_user


def _get(cid: str, uid: str) -> Call:
    """The call, if `uid` is in it — 404 otherwise (as for chats)."""
    with _lock:
        c = _calls.get(cid) if isinstance(cid, str) else None
    if c is None or uid not in (c.caller, c.callee):
        raise HTTPException(404, "That call has ended.")
    return c


def _event(c: Call, uids, data: dict) -> None:
    hub.publish(uids, "call", {"id": c.id, "conversationId": c.conversation_id, **data})


def _sdp(v) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > MAX_SDP:
        raise HTTPException(422, f"A call description is 1–{MAX_SDP} characters.")
    return v


# ---------- starting ----------
def start(user: dict, conversation_id: str) -> dict:
    """POST /calls: checks who may call whom here, then a `new` call waiting for the offer."""
    with db.get_conn() as conn:
        if not enabled(conn):
            raise HTTPException(403, "Voice calls are turned off. An admin can turn them on in App settings.")
        conv, _m = chats.access(conn, conversation_id, user)
        chats.not_personal(conv)
        if conv["kind"] != "direct":
            raise HTTPException(409, "Calls are only possible in a direct chat.")
        chats.require_post(conn, conv, user["id"])
        other = chats.other_member(conn, conv, user["id"])
        ring = settings.get("calls_ring_seconds", conn)
        servers = ice_servers(conn)
        other_name = chats.shown_name(other)
    c = Call(id=db.new_id(), conversation_id=conv["id"], caller=user["id"], callee=other["id"])
    with _lock:
        if user["id"] in _by_user:
            raise HTTPException(409, "You're already in a call.")
        other_busy = other["id"] in _by_user
        if not other_busy:
            _calls[c.id] = c
            _by_user[c.caller] = c.id
            _by_user[c.callee] = c.id
    if other_busy:
        # a missed call for them, noted in the chat; the caller hears the busy tone
        c.started_at = config.now_iso()
        _finish_note(c, "busy")
        raise HTTPException(409, f"{other_name} is on another call.")
    return {"id": c.id, "iceServers": servers, "ringSeconds": ring, "peerId": c.callee, "peerName": other_name}


def offer(user: dict, call_id: str, sdp) -> dict:
    """The caller's offer: the call starts ringing."""
    sdp = _sdp(sdp)
    c = _get(call_id, user["id"])
    with db.get_conn() as conn:
        ring = settings.get("calls_ring_seconds", conn)
        caller_name = chats.shown_name(chats.user_row(conn, c.caller))
    with _lock:
        if c.caller != user["id"] or c.state != "new":
            raise HTTPException(409, "That call has already started.")
        c.offer, c.state = sdp, "ringing"
        c.ring_until = time.monotonic() + ring
        c.started_at = config.now_iso()
    with db.get_conn() as conn:
        conn.execute("INSERT INTO calls (id, conversation_id, caller_id, callee_id, started_at) VALUES (?, ?, ?, ?, ?)",
                     (c.id, c.conversation_id, c.caller, c.callee, c.started_at))
    _event(c, [c.callee], {"state": "ringing", "peerId": c.caller, "peerName": caller_name, "ringSeconds": ring})
    _event(c, [c.caller], {"state": "ringing"})
    _spawn(_ring_push, c.id)
    return {"ok": True}


# ---------- answering, candidates ----------
def answer(user: dict, call_id: str, sdp) -> dict:
    sdp = _sdp(sdp)
    c = _get(call_id, user["id"])
    with _lock:
        if c.callee != user["id"] or c.state != "ringing":
            raise HTTPException(409, "That call can't be answered now.")
        c.answer, c.state = sdp, "active"
        c.answered_at = config.now_iso()
        early = c.candidates.pop(c.callee, [])
    with db.get_conn() as conn:
        conn.execute("UPDATE calls SET answered_at = ? WHERE id = ?", (c.answered_at, c.id))
    _event(c, [c.caller], {"state": "answered", "sdp": sdp, "candidates": early})
    _event(c, [c.callee], {"state": "answered"})        # the person's other tabs stop ringing
    _clear_push(c)
    return {"ok": True}


def candidate(user: dict, call_id: str, cand) -> dict:
    """A network candidate found after the offer or answer went: passed on now, or kept until the other side
    has the call (the person called fetches the caller's with the offer)."""
    if not isinstance(cand, dict) or len(str(cand)) > MAX_CANDIDATE:
        raise HTTPException(422, "That isn't a network candidate.")
    c = _get(call_id, user["id"])
    with _lock:
        if c.state == "new" or (c.state == "ringing" and user["id"] == c.caller):
            kept = c.candidates.setdefault(user["id"], [])
            if len(kept) >= MAX_CANDIDATES:
                raise HTTPException(429, "Too many network candidates.")
            kept.append(cand)
            return {"ok": True}
        if c.state == "ringing":
            raise HTTPException(409, "Answer the call first.")
    _event(c, [c.other(user["id"])], {"state": "candidate", "candidate": cand})
    return {"ok": True}


def current(user: dict) -> dict | None:
    """GET /calls/current: my call (with the caller's offer while it rings for me), or None."""
    with _lock:
        cid = _by_user.get(user["id"])
        c = _calls.get(cid) if cid else None
        if c is None:
            return None
        mine = c.caller == user["id"]
        data = {"id": c.id, "conversationId": c.conversation_id, "state": c.state,
                "role": "caller" if mine else "callee", "peerId": c.other(user["id"]),
                "ringLeft": max(0, round(c.ring_until - time.monotonic())) if c.state == "ringing" else None}
        if not mine and c.state == "ringing":
            data["offer"] = c.offer
            data["candidates"] = list(c.candidates.get(c.caller, []))
    with db.get_conn() as conn:
        data["peerName"] = chats.shown_name(chats.user_row(conn, data["peerId"]))
        data["iceServers"] = ice_servers(conn)
    return data


# ---------- ending ----------
def _take(call_id: str) -> Call | None:
    """Remove a call from what's on (once: a second ending finds nothing)."""
    with _lock:
        c = _calls.pop(call_id, None)
        if c is not None:
            for u in (c.caller, c.callee):
                if _by_user.get(u) == c.id:
                    _by_user.pop(u, None)
    return c


def decline(user: dict, call_id: str) -> dict:
    c = _get(call_id, user["id"])
    if c.callee != user["id"] or c.state != "ringing":
        raise HTTPException(409, "That call can't be declined now.")
    _end(c.id, "declined", by=user["id"])
    return {"ok": True}


def hang_up(user: dict, call_id: str, reason: str | None = None) -> dict:
    """POST /calls/{id}/end. reason "failed": the connection couldn't be made (or the microphone couldn't be
    used); "no_microphone" says the latter to the other side."""
    c = _get(call_id, user["id"])
    if reason in ("failed", "no_microphone"):
        outcome = "failed"
    elif c.state == "active":
        outcome = "answered"
    elif c.state == "ringing":
        outcome = "declined" if user["id"] == c.callee else "missed"
    else:
        outcome = None                          # the offer never went: nothing to note
    _end(c.id, outcome, by=user["id"], reason=reason)
    return {"ok": True}


def _end(call_id: str, outcome: str | None, by: str | None = None, reason: str | None = None) -> None:
    c = _take(call_id)
    if c is None:
        return
    data = {"state": "ended", "outcome": outcome, "by": by}
    if reason:
        data["reason"] = reason
    _event(c, [c.caller, c.callee], data)
    if outcome is None or c.started_at is None:
        return
    _clear_push(c)              # a missed call's notification follows as a message (§7)
    _finish_note(c, outcome)


def _finish_note(c: Call, outcome: str) -> None:
    """The call's row is closed and its note posted in the chat (as the caller's message)."""
    out = chats.Outbox()
    now = config.now_iso()
    with db.get_conn() as conn:
        conv = chats.conv_row(conn, c.conversation_id)
        if conv is None:
            return
        mid = chats.insert_message(conn, conv, c.caller, kind="call", body="", expires_in=conv["disappear_seconds"])
        if conn.execute("UPDATE calls SET ended_at = ?, outcome = ?, message_id = ? WHERE id = ?",
                        (now, outcome, mid, c.id)).rowcount == 0:
            conn.execute("INSERT INTO calls (id, conversation_id, caller_id, callee_id, started_at, answered_at, ended_at, "
                         "outcome, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (c.id, c.conversation_id, c.caller, c.callee, c.started_at or now, c.answered_at, now, outcome, mid))
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid))
        if outcome in ("missed", "busy"):
            out.job(notifier.new_message, mid)
        out.event([c.callee], "unread", {"conversationId": conv["id"]})
    out.flush()


def end_for_user(uid: str, outcome: str = "failed") -> None:
    """Disabled (or gone): their call ends."""
    with _lock:
        cid = _by_user.get(uid)
    if cid:
        _end(cid, outcome)


def tick() -> int:
    """Every 2 s: rings that time out become missed calls, offers that never came are dropped, and an answered
    call ends when one side has had no live connection for a minute. → how many calls ended."""
    now = time.monotonic()
    ends = []
    with _lock:
        calls = list(_calls.values())
    for c in calls:
        if c.state == "new" and now - c.made > OFFER_SECONDS:
            ends.append((c.id, None))
        elif c.state == "ringing" and now >= c.ring_until:
            ends.append((c.id, "missed"))
        elif c.state == "active":
            for u in (c.caller, c.callee):
                if hub.is_online(u):
                    c.gone_since.pop(u, None)
                elif now - c.gone_since.setdefault(u, now) > GONE_SECONDS:
                    ends.append((c.id, "answered"))
                    break
    for cid, outcome in ends:
        _end(cid, outcome)
    return len(ends)


def close_unfinished() -> int:
    """At start-up: calls that were on when the app stopped are closed as "couldn't connect", with their note."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM calls WHERE ended_at IS NULL").fetchall()
    for r in rows:
        c = Call(id=r["id"], conversation_id=r["conversation_id"], caller=r["caller_id"], callee=r["callee_id"],
                 started_at=r["started_at"], answered_at=r["answered_at"])
        _finish_note(c, "failed")
    return len(rows)


# ---------- the phone ----------
def _wants_ring(conn, user, m) -> bool:
    """Ring their phone? Not when their level is off, the chat is muted, or it's their quiet hours (§15.12)."""
    if user is None or user["disabled"] or m is None or user["notify_level"] == "off" or m["notify"] == "off":
        return False
    muted = config.parse_iso(m["muted_until"])
    if muted and muted > config.utcnow():
        return False
    return not notifier.in_quiet_hours(user)


def _ring_push(call_id: str) -> None:
    """"📞 Asha is calling" with Answer (opens the app) and Decline (handled without opening it)."""
    with _lock:
        c = _calls.get(call_id)
    if c is None:
        return
    with db.get_conn() as conn:
        user = chats.user_row(conn, c.callee)
        m = chats.member_row(conn, c.conversation_id, c.callee)
        if not _wants_ring(conn, user, m):
            return
        services = ha_notify.services_for({"id": c.callee}, conn)
        if not services:
            return
        level = notifier.preview_level(user, conn)
        caller_name = chats.shown_name(chats.user_row(conn, c.caller))
        token = notifier.new_token(conn, c.callee, c.conversation_id)
    title = config.APP_TITLE if level == "none" else caller_name
    text = "📞 Incoming call" if level == "none" else f"📞 {caller_name} is calling"
    data = {"url": config.INGRESS_URL, "clickAction": config.INGRESS_URL, "tag": f"hchat_call_{c.id}",
            "group": "household_chat", "ttl": 0, "priority": "high",
            "push": {"interruption-level": "time-sensitive"},
            "actions": [{"action": "URI", "title": "Answer", "uri": config.INGRESS_URL},
                        {"action": f"HCHAT_DECLINE_{token}", "title": "Decline"}]}
    with _lock:
        if c.id not in _calls or c.state != "ringing":
            return
        c.pushed = True
    notifier.deliver(services, title, text, data)


def _clear_push(c: Call) -> None:
    """Take the ringing notification off the phone (Companion app phones only: other services would show the
    words)."""
    with _lock:
        if not c.pushed:
            return
        c.pushed = False
    with db.get_conn() as conn:
        services = [s for s in ha_notify.services_for({"id": c.callee}, conn) if s.startswith("mobile_app_")]
    if services:
        _spawn(notifier.deliver, services, "", "clear_notification", {"tag": f"hchat_call_{c.id}"})


def decline_from_phone(uid: str, conversation_id: str) -> str:
    """The Decline button on the phone (notifier.handle_action)."""
    with _lock:
        cid = _by_user.get(uid)
        c = _calls.get(cid) if cid else None
    if c is None or c.callee != uid or c.conversation_id != conversation_id or c.state != "ringing":
        return "no call"
    _end(c.id, "declined", by=uid)
    return "declined"
