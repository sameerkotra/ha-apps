"""Voice and video calls (SPEC §15.12): one-to-one calls in direct chats and group calls of up to four people
in groups — signalling only.

The sound and picture go straight between the browsers (WebRTC, a mesh: each person in the call connects to
each other). The app only passes their descriptions (`sdp`) and network candidates between pairs, rings the
people called, and writes a note in the chat when the call ends.

Calls that are on live in memory, like the live-update hub (live.py): the app runs ONE process (uvicorn with
1 worker, see the Dockerfile), so a dict is the whole picture. A restart ends them; `close_unfinished()` closes
their rows at start-up. The `calls` table keeps the history (one row per call that rang) and `call_members` one
row per person in it, how it ended for them.

A call: POST /calls makes it, with the caller **joined** and everyone invited **ringing**; each person
answers (joined), declines, or is missed when the ringing stops. Two people joined is a call; it ends when fewer
than two remain and nobody is still ringing. Live `call` events go only to the people in the call; each says
which call it is about, and a page ignores calls it isn't handling. Signals (offer, answer, candidate) go to
one person each, and are also kept for a page whose live updates are down (it polls GET /calls/current).
"""
import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from fastapi import HTTPException

from . import chats, config, db, notifier, settings
from .common import ha_notify
from .live import hub

logger = logging.getLogger("calls")

MAX_SDP = 32000
MAX_CANDIDATE = 1000
MAX_SIGNALS = 300           # kept per person for the polling fallback
MAX_PARTICIPANTS = 4        # a mesh: each phone sends to every other; beyond four it would need a media server
GONE_SECONDS = 60           # someone with no live connection this long has left
KINDS = ("audio", "video")
OUTCOMES = ("answered", "missed", "declined", "busy", "failed")
MEMBER_STATES = ("ringing", "joined", "left", "declined", "missed", "busy", "failed")


@dataclass
class Member:
    state: str = "ringing"                  # ringing | joined | left | declined | missed | busy | failed
    joined_at: str | None = None
    left_at: str | None = None
    gone_since: float | None = None         # monotonic: first seen without a live connection
    pushed: bool = False                    # a ringing notification went to their phone
    signals: list = field(default_factory=list)     # for them, until their page fetched them


@dataclass
class Call:
    id: str
    conversation_id: str
    kind: str                               # audio | video
    group: bool
    caller: str
    members: dict = field(default_factory=dict)      # user → Member (the caller too)
    ring_until: float = 0.0
    started_at: str | None = None
    answered_at: str | None = None          # the first join after the caller
    ended: bool = False
    seq: int = 0

    def joined(self) -> list:
        return [u for u, m in self.members.items() if m.state == "joined"]

    def ringing(self) -> list:
        return [u for u, m in self.members.items() if m.state == "ringing"]

    def in_call(self) -> list:
        """Everyone who may still act on it: joined or ringing."""
        return [u for u, m in self.members.items() if m.state in ("joined", "ringing")]


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


# ---------- STUN and relay (§15.13): what the browsers get as iceServers ----------
CF_TTL = 4 * 3600                       # Cloudflare credentials last this long (longer than any call)
CF_CACHE = 3600                         # and are fetched at most this often
TURN_TTL = 4 * 3600
_cf = {"servers": None, "until": 0.0, "key": None, "error": None}


def cf_fetch(key_id: str, token: str, ttl: int = CF_TTL) -> list:
    """Short-lived TURN credentials from Cloudflare's API (tests replace this). Straight to Cloudflare, never
    through Home Assistant. → the iceServers list."""
    req = urllib.request.Request(
        f"https://rtc.live.cloudflare.com/v1/turn/keys/{urllib.parse.quote(key_id, safe='')}/credentials/generate-ice-servers",
        data=json.dumps({"ttl": ttl}).encode(), method="POST",
        # Cloudflare's bot filter bans Python's default user agent ("error code: 1010"): say who's asking
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": f"HouseholdChat/{config.APP_VERSION} (Home Assistant app; +https://github.com/sameerkotra/ha-apps)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read(64 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as e:
        # what Cloudflare said (its status and a line of its message: never the token, which isn't in an answer)
        text = e.read(2000).decode("utf-8", "replace").replace("\n", " ").strip()[:200]
        raise RuntimeError(f"HTTP {e.code} from Cloudflare: {text or e.reason}") from None
    servers = body.get("iceServers") if isinstance(body, dict) else None
    if isinstance(servers, dict):
        servers = [servers]
    if not isinstance(servers, list) or not all(isinstance(s, dict) and s.get("urls") for s in servers):
        raise ValueError("unexpected answer")
    return [{"urls": s["urls"], "username": s.get("username"), "credential": s.get("credential")} for s in servers]


def cloudflare_servers(key_id: str, token: str) -> list:
    """Cached for an hour; a failure is logged and the call goes ahead without a relay."""
    now = time.monotonic()
    with _lock:
        if _cf["servers"] is not None and now < _cf["until"] and _cf["key"] == (key_id, token):
            return list(_cf["servers"])
    try:
        servers = cf_fetch(key_id, token)
    except Exception as e:                        # noqa: BLE001 — logged, the call goes on
        why = str(e) if isinstance(e, RuntimeError) else type(e).__name__
        logger.warning("Couldn't get call relay credentials from Cloudflare: %s", why)
        with _lock:
            _cf["error"] = why
        return []
    with _lock:
        _cf.update(servers=servers, until=now + CF_CACHE, key=(key_id, token), error=None)
    return list(servers)


def turn_servers(urls: str, secret: str, call_id: str) -> list:
    """coturn's shared-secret credentials (use-auth-secret): user "<expiry>:<call id>", password the base64
    HMAC-SHA1 of it — made per call, so no long-lived password ever reaches a phone."""
    user = f"{int(time.time()) + TURN_TTL}:{call_id}"
    password = base64.b64encode(hmac.new(secret.encode(), user.encode(), hashlib.sha1).digest()).decode()
    return [{"urls": [u.strip() for u in urls.split(",") if u.strip()], "username": user, "credential": password}]


def ice_servers(values: dict, call_id: str = "test") -> list:
    """STUN / relay addresses for the browsers, from the App settings (`settings.all_values()`, read before —
    this may call Cloudflare, so never while a DB connection is held). Empty: the home network only."""
    out = []
    if values.get("calls_stun"):
        out.append({"urls": values["calls_stun"]})
    relay = values.get("calls_relay")
    if relay == "cloudflare" and values.get("calls_cf_key_id") and values.get("calls_cf_api_token"):
        out += cloudflare_servers(values["calls_cf_key_id"], values["calls_cf_api_token"])
    elif relay == "turn" and values.get("calls_turn_url") and values.get("calls_turn_secret"):
        out += turn_servers(values["calls_turn_url"], values["calls_turn_secret"], call_id)
    return out


def reset_relay_cache() -> None:
    with _lock:
        _cf.update(servers=None, until=0.0, key=None, error=None)


def relay_error() -> str | None:
    """Why the last Cloudflare request failed (for Test calling), or None."""
    with _lock:
        return _cf["error"]


# ---------- lookups ----------
def busy(uid: str) -> bool:
    with _lock:
        return uid in _by_user


def _get(cid: str, uid: str) -> Call:
    """The call, if `uid` is in it — 404 otherwise (as for chats)."""
    with _lock:
        c = _calls.get(cid) if isinstance(cid, str) else None
    if c is None or c.ended or uid not in c.members or c.members[uid].state not in ("joined", "ringing"):
        raise HTTPException(404, "That call has ended.")
    return c


def _event(c: Call, uids, data: dict) -> None:
    hub.publish(uids, "call", {"id": c.id, "conversationId": c.conversation_id, **data})


def _names(conn, uids) -> dict:
    return {u: chats.shown_name(chats.user_row(conn, u)) for u in uids}


def _members_out(c: Call, names: dict) -> list:
    return [{"id": u, "name": names.get(u, "Someone"), "state": m.state} for u, m in c.members.items()]


def _sdp(v) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > MAX_SDP:
        raise HTTPException(422, f"A call description is 1–{MAX_SDP} characters.")
    return v


# ---------- starting ----------
def start(user: dict, conversation_id: str, kind: str = "audio") -> dict:
    """POST /calls: checks who may call whom here, then the call rings — the caller joined, everyone invited
    ringing. A direct chat invites the other person (busy: a missed call for them, 409 for the caller); a group
    invites every enabled member who isn't in a call."""
    if kind not in KINDS:
        raise HTTPException(422, "A call is audio or video.")
    with db.get_conn() as conn:
        if not enabled(conn):
            raise HTTPException(403, "Voice calls are turned off. An admin can turn them on in App settings.")
        conv, _m = chats.access(conn, conversation_id, user)
        chats.not_personal(conv)
        chats.require_post(conn, conv, user["id"])
        chats.message_limit.take(user["id"])        # a call can leave a note and a push: counted like a message
        group = conv["kind"] == "group"
        invitees = [u for u in chats.member_ids(conn, conv["id"]) if u != user["id"]]
        values = settings.all_values(conn)
        names = _names(conn, invitees + [user["id"]])
        conv_name = conv["name"] if group else None
    if not invitees:
        raise HTTPException(409, "There's nobody else in this group to call.")
    ring = values["calls_ring_seconds"]
    now = config.now_iso()
    c = Call(id=db.new_id(), conversation_id=conv["id"], kind=kind, group=group, caller=user["id"], started_at=now)
    c.members[user["id"]] = Member(state="joined", joined_at=now)
    servers = ice_servers(values, c.id)          # after the connection: may ask Cloudflare
    with _lock:
        if user["id"] in _by_user:
            raise HTTPException(409, "You're already in a call.")
        busy_ones = [u for u in invitees if u in _by_user]
        other_busy = not group and bool(busy_ones)
        if not other_busy:
            for u in invitees:
                c.members[u] = Member(state="busy" if u in busy_ones else "ringing")
            c.ring_until = time.monotonic() + ring
            _calls[c.id] = c
            _by_user[user["id"]] = c.id
            for u in invitees:
                if u not in busy_ones:
                    _by_user[u] = c.id
    if other_busy:
        # a missed call for them, noted in the chat; the caller hears the busy tone
        c.members[invitees[0]] = Member(state="busy")
        c.ended = True
        _finish(c, "busy")
        raise HTTPException(409, f"{names[invitees[0]]} is on another call.")
    if group and len(busy_ones) == len(invitees):
        _take(c.id)
        raise HTTPException(409, "Everyone else is on another call.")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO calls (id, conversation_id, caller_id, callee_id, started_at, kind, is_group) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (c.id, c.conversation_id, c.caller, "" if group else invitees[0],
                                                       c.started_at, kind, 1 if group else 0))
    members = _members_out(c, names)
    ringing = c.ringing()
    _event(c, ringing, {"state": "ringing", "kind": kind, "group": group, "name": conv_name or names[user["id"]],
                        "peerId": user["id"], "peerName": names[user["id"]], "ringSeconds": ring, "members": members})
    _event(c, [user["id"]], {"state": "ringing", "members": members})
    for u in ringing:
        _spawn(_ring_push, c.id, u)
    return {"id": c.id, "kind": kind, "group": group, "name": conv_name or names[invitees[0]],
            "iceServers": servers, "ringSeconds": ring, "members": members,
            "peerId": None if group else invitees[0], "peerName": None if group else names[invitees[0]]}


# ---------- joining ----------
def answer(user: dict, call_id: str) -> dict:
    """The person called joins: they then send an offer to each person already in (the page does that from the
    `peers` returned here — the newcomer always offers, so two sides never offer at once)."""
    c = _get(call_id, user["id"])
    with db.get_conn() as conn:
        values = settings.all_values(conn)
        names = _names(conn, list(c.members))
    servers = ice_servers(values, c.id)
    with _lock:
        m = c.members[user["id"]]
        if m.state != "ringing":
            raise HTTPException(409, "That call can't be answered now.")
        if len(c.joined()) >= MAX_PARTICIPANTS:
            raise HTTPException(409, f"The call is full ({MAX_PARTICIPANTS} people).")
        m.state, m.joined_at = "joined", config.now_iso()
        first = c.answered_at is None
        if first:
            c.answered_at = m.joined_at
        peers = [u for u in c.joined() if u != user["id"]]
        members = _members_out(c, names)
    if first:
        with db.get_conn() as conn:
            conn.execute("UPDATE calls SET answered_at = ? WHERE id = ?", (c.answered_at, c.id))
    _event(c, [u for u in c.in_call() if u != user["id"]],
           {"state": "member", "userId": user["id"], "memberState": "joined", "members": members})
    _event(c, [user["id"]], {"state": "joined", "members": members})      # the person's other tabs stop ringing
    _clear_push(c, user["id"])
    return {"id": c.id, "kind": c.kind, "group": c.group, "iceServers": servers, "members": members,
            "peers": [{"id": u, "name": names.get(u, "Someone")} for u in peers]}


def signal(user: dict, call_id: str, to: str, kind: str, payload) -> dict:
    """An offer, answer or candidate from one person in the call to another (both joined)."""
    c = _get(call_id, user["id"])
    if kind in ("offer", "answer"):
        data = {"sdp": _sdp(payload)}
    elif kind == "candidate":
        if not isinstance(payload, dict) or len(str(payload)) > MAX_CANDIDATE:
            raise HTTPException(422, "That isn't a network candidate.")
        data = {"candidate": payload}
    else:
        raise HTTPException(422, "A signal is an offer, an answer or a candidate.")
    with _lock:
        if c.members[user["id"]].state != "joined":
            raise HTTPException(409, "Join the call first.")
        target = c.members.get(to) if isinstance(to, str) else None
        if target is None or target.state != "joined":
            raise HTTPException(409, "They aren't in the call.")
        c.seq += 1
        sig = {"id": c.seq, "from": user["id"], "type": kind, **data}
        target.signals.append(sig)
        if len(target.signals) > MAX_SIGNALS:
            del target.signals[:-MAX_SIGNALS]
    _event(c, [to], {"state": "signal", "signal": sig})
    return {"ok": True}


def current(user: dict) -> dict | None:
    """GET /calls/current: my call — ringing for me, or the one I'm in — with the signals waiting for me (a page
    whose live updates are down polls this; pages ignore signals they've seen), or None."""
    with _lock:
        cid = _by_user.get(user["id"])
        c = _calls.get(cid) if cid else None
        if c is None or c.ended or user["id"] not in c.members:
            return None
        m = c.members[user["id"]]
        if m.state not in ("joined", "ringing"):
            return None
        signals = list(m.signals)
        m.signals.clear()
        data = {"id": c.id, "conversationId": c.conversation_id, "kind": c.kind, "group": c.group, "state": m.state,
                "role": "caller" if c.caller == user["id"] else "callee",
                "peerId": c.caller if c.caller != user["id"] else None,
                "ringLeft": max(0, round(c.ring_until - time.monotonic())) if m.state == "ringing" else None,
                "signals": signals}
        uids = list(c.members)
    with db.get_conn() as conn:
        names = _names(conn, uids)
        conv = chats.conv_row(conn, c.conversation_id)
        values = settings.all_values(conn)
    with _lock:
        data["members"] = _members_out(c, names)
    if data["peerId"] is None and not c.group:
        data["peerId"] = next((u for u in uids if u != user["id"]), None)
    data["peerName"] = names.get(data["peerId"], "Someone")
    data["name"] = (conv["name"] if conv and c.group else None) or data["peerName"]
    data["iceServers"] = ice_servers(values, c.id)
    return data


# ---------- leaving and ending ----------
def _take(call_id: str) -> Call | None:
    """Remove a call from what's on (once: a second ending finds nothing)."""
    with _lock:
        c = _calls.pop(call_id, None)
        if c is not None:
            c.ended = True
            for u in c.members:
                if _by_user.get(u) == c.id:
                    _by_user.pop(u, None)
    return c


def _release(c: Call, uid: str) -> None:
    """Someone who declined, was missed or left is free for other calls."""
    with _lock:
        if _by_user.get(uid) == c.id:
            _by_user.pop(uid, None)


def decline(user: dict, call_id: str) -> dict:
    c = _get(call_id, user["id"])
    with _lock:
        m = c.members[user["id"]]
        if m.state != "ringing":
            raise HTTPException(409, "That call can't be declined now.")
        m.state = "declined"
    _release(c, user["id"])
    _event(c, [user["id"]], {"state": "ended", "outcome": "declined", "by": user["id"]})     # their other tabs
    _clear_push(c, user["id"])
    _after_change(c, "declined", by=user["id"])
    return {"ok": True}


def hang_up(user: dict, call_id: str, reason: str | None = None) -> dict:
    """POST /calls/{id}/end: leave (the call goes on for the others while two remain). reason "failed": the
    connection couldn't be made; "no_microphone": Answer couldn't use the microphone (told to the others)."""
    c = _get(call_id, user["id"])
    with _lock:
        m = c.members[user["id"]]
        was = m.state
        m.state = "failed" if reason else ("left" if was == "joined" else "declined")
        m.left_at = config.now_iso()
    _release(c, user["id"])
    _clear_push(c, user["id"])
    _event(c, [user["id"]], {"state": "ended", "outcome": None, "by": user["id"], "reason": reason})
    if was == "ringing" and not reason:
        _after_change(c, "declined", by=user["id"])
    else:
        _after_change(c, "left", by=user["id"], reason=reason)
    return {"ok": True}


def _after_change(c: Call, what: str, by: str | None = None, reason: str | None = None) -> None:
    """After someone declined, left or was missed: tell the others, or end the call when fewer than two remain
    and nobody is still ringing."""
    with _lock:
        joined, ringing = c.joined(), c.ringing()
        uids = list(c.members)
    if joined and (len(joined) >= 2 or ringing):
        with db.get_conn() as conn:
            names = _names(conn, uids)
        with _lock:
            members = _members_out(c, names)
            data = {"state": "member", "userId": by, "memberState": c.members[by].state if by else None, "members": members}
            stay = c.in_call()
        if reason:
            data["reason"] = reason
        _event(c, stay, data)
        return
    # the end: how it went
    if reason and not c.answered_at:
        outcome = "failed"
    elif c.answered_at:
        outcome = "answered"
    elif not c.group and what == "declined" and by != c.caller:
        outcome = "declined"
    elif not c.group and by == c.caller:
        outcome = "missed"                      # the caller gave up while it rang
    else:
        states = {m.state for u, m in c.members.items() if u != c.caller}
        outcome = "declined" if states and states <= {"declined", "busy"} else "missed"
    _end(c.id, outcome, by=by, reason=reason)


def _end(call_id: str, outcome: str, by: str | None = None, reason: str | None = None) -> None:
    c = _take(call_id)
    if c is None:
        return
    for u, m in c.members.items():
        if m.state == "ringing":
            m.state = "missed"
        _clear_push(c, u)       # a missed call's notification follows as a message (§7)
    _finish(c, outcome)
    data = {"state": "ended", "outcome": outcome, "by": by}      # after the note: pages see both
    if reason:
        data["reason"] = reason
    _event(c, list(c.members), data)


def _finish(c: Call, outcome: str) -> None:
    """The call's rows are closed and its note posted in the chat (as the caller's message)."""
    out = chats.Outbox()
    now = config.now_iso()
    with db.get_conn() as conn:
        conv = chats.conv_row(conn, c.conversation_id)
        if conv is None:
            return
        mid = chats.insert_message(conn, conv, c.caller, kind="call", body="", expires_in=conv["disappear_seconds"])
        if conn.execute("UPDATE calls SET ended_at = ?, outcome = ?, message_id = ?, answered_at = ? WHERE id = ?",
                        (now, outcome, mid, c.answered_at, c.id)).rowcount == 0:
            conn.execute("INSERT INTO calls (id, conversation_id, caller_id, callee_id, started_at, answered_at, ended_at, "
                         "outcome, message_id, kind, is_group) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (c.id, c.conversation_id, c.caller, "" if c.group else next((u for u in c.members if u != c.caller), ""),
                          c.started_at or now, c.answered_at, now, outcome, mid, c.kind, 1 if c.group else 0))
        conn.execute("DELETE FROM call_members WHERE call_id = ?", (c.id,))
        for u, m in c.members.items():
            state = "left" if m.state == "joined" else m.state
            conn.execute("INSERT INTO call_members (call_id, user_id, state, joined_at, left_at) VALUES (?, ?, ?, ?, ?)",
                         (c.id, u, state, m.joined_at, m.left_at or (now if m.joined_at else None)))
        chats.publish_message(conn, out, conv, chats.message_row(conn, mid))
        if any(m.state in ("missed", "busy") for m in c.members.values()):
            out.job(notifier.new_message, mid)
        for u in c.members:
            if u != c.caller:
                out.event([u], "unread", {"conversationId": conv["id"]})
    out.flush()


def end_for_user(uid: str, outcome: str = "failed") -> None:
    """Disabled (or gone): they leave their call."""
    with _lock:
        cid = _by_user.get(uid)
        c = _calls.get(cid) if cid else None
        if c is None or uid not in c.members:
            return
        m = c.members[uid]
        was = m.state
        if was not in ("joined", "ringing"):
            return
        m.state = "failed" if was == "joined" else "missed"
        m.left_at = config.now_iso()
    _release(c, uid)
    _clear_push(c, uid)
    _event(c, [uid], {"state": "ended", "outcome": None, "by": uid, "reason": "gone"})
    _after_change(c, "left", by=uid, reason="gone" if was == "joined" else None)


def tick() -> int:
    """Every 2 s: rings that time out become missed, and someone joined with no live connection for a minute has
    left. → how many calls changed."""
    now = time.monotonic()
    changed = 0
    with _lock:
        calls = list(_calls.values())
    for c in calls:
        if c.ended:
            continue
        timed_out, gone = [], []
        with _lock:
            if c.ringing() and now >= c.ring_until:
                for u in c.ringing():
                    c.members[u].state = "missed"
                    timed_out.append(u)
            for u in c.joined():
                m = c.members[u]
                if hub.is_online(u):
                    m.gone_since = None
                elif m.gone_since is None:
                    m.gone_since = now
                elif now - m.gone_since > GONE_SECONDS:
                    m.state, m.left_at = "left", config.now_iso()
                    gone.append(u)
        for u in timed_out + gone:
            _release(c, u)
            _clear_push(c, u)
            _event(c, [u], {"state": "ended", "outcome": None, "by": None, "reason": "gone" if u in gone else "missed"})
        if timed_out or gone:
            changed += 1
            _after_change(c, "left" if gone else "missed")
    return changed


def close_unfinished() -> int:
    """At start-up: calls that were on when the app stopped are closed as "couldn't connect", with their note."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM calls WHERE ended_at IS NULL").fetchall()
    for r in rows:
        c = Call(id=r["id"], conversation_id=r["conversation_id"], kind=r["kind"] or "audio", group=bool(r["is_group"]),
                 caller=r["caller_id"], started_at=r["started_at"], answered_at=r["answered_at"], ended=True)
        c.members[r["caller_id"]] = Member(state="left", joined_at=r["started_at"])
        if r["callee_id"]:
            c.members[r["callee_id"]] = Member(state="left" if r["answered_at"] else "missed")
        _finish(c, "failed")
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


def _ring_push(call_id: str, uid: str) -> None:
    """"📞 Asha is calling" with Answer (opens the app) and Decline (handled without opening it)."""
    with _lock:
        c = _calls.get(call_id)
    if c is None or uid not in c.members:
        return
    with db.get_conn() as conn:
        user = chats.user_row(conn, uid)
        m = chats.member_row(conn, c.conversation_id, uid)
        if not _wants_ring(conn, user, m):
            return
        services = ha_notify.services_for({"id": uid}, conn)
        if not services:
            return
        level = notifier.preview_level(user, conn)
        caller_name = chats.shown_name(chats.user_row(conn, c.caller))
        conv = chats.conv_row(conn, c.conversation_id)
        token = notifier.new_token(conn, uid, c.conversation_id)
    icon = "📹" if c.kind == "video" else "📞"
    what = "video call" if c.kind == "video" else "call"
    title = config.APP_TITLE if level == "none" else caller_name
    if level == "none":
        text = f"{icon} Incoming {what}"
    elif c.group:
        text = f"{icon} {caller_name} is starting a group {what} in {conv['name'] if conv else 'a group'}"
    else:
        text = f"{icon} {caller_name} is calling" + (" (video)" if c.kind == "video" else "")
    link = config.call_link(c.id)
    data = {"url": link, "clickAction": link, "tag": f"hchat_call_{c.id}",
            "group": "household_chat", "ttl": 0, "priority": "high",
            "push": {"interruption-level": "time-sensitive"},
            "actions": [{"action": "URI", "title": "Answer", "uri": link},
                        {"action": f"HCHAT_DECLINE_{token}", "title": "Decline"}]}
    with _lock:
        if c.ended or c.members[uid].state != "ringing":
            return
        c.members[uid].pushed = True
    notifier.deliver(services, title, text, data)


def _clear_push(c: Call, uid: str) -> None:
    """Take the ringing notification off their phone (Companion app phones only: other services would show the
    words)."""
    with _lock:
        m = c.members.get(uid)
        if m is None or not m.pushed:
            return
        m.pushed = False
    with db.get_conn() as conn:
        services = [s for s in ha_notify.services_for({"id": uid}, conn) if s.startswith("mobile_app_")]
    if services:
        _spawn(notifier.deliver, services, "", "clear_notification", {"tag": f"hchat_call_{c.id}"})


def decline_from_phone(uid: str, conversation_id: str) -> str:
    """The Decline button on the phone (notifier.handle_action)."""
    with _lock:
        cid = _by_user.get(uid)
        c = _calls.get(cid) if cid else None
        if c is None or c.ended or c.conversation_id != conversation_id or uid not in c.members \
                or c.members[uid].state != "ringing":
            return "no call"
    decline({"id": uid}, c.id)
    return "declined"
