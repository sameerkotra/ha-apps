"""Household Phones on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py) and Home
Assistant's events (one shared WebSocket, ha_ws.HAWebSocket).

- **Answers** `phones.forwarding_into` (the contract with Household Chat): the phones that forward into a chat and
  that the asking person may Check now — from the database only, answered at once (Chat waits 4 s, caches 60 s).
- **Answers** the Household Assistant (`assist.tools.list`, `assist.tool.call`; tools.py).
- **Asks** Chat `chat.chats.list` (the owner's chats for the forwarding target, and the members of the chosen chat,
  for Check-now rights) and posts with `chat.message.post`:
  - alerts (delivery "Household Chat"): through the bus's outbox like any message;
  - **forwards: never through the outbox**, which keeps every message body in the database for days. A forward's
    envelope is built and posted directly (`post_forward`), held in memory only until Chat answers (re-sent at most
    3 times; Chat drops a repeated id), and Chat's ack or nack is read from the same WebSocket. A nack is kept as
    the phone's `fwd_error` (shown on My phone) — `not_allowed app` means Chat's *Phones may post forwarded
    messages* is off.
- **Events**: `state_changed` / `state_reported` (check-ins and forwarding, monitor.on_state_event), the registries'
  `*_registry_updated` (discovery again), `household_phones_check_now` (Check now from a script), and Home
  Assistant's start / started / stop plus every reconnection (monitor's restart grace: restores aren't check-ins).
"""
import json
import logging
import re
import secrets
import threading
from datetime import timedelta

from . import config, db, settings
from .common import app_bus as bus
from .common import ha_ws

logger = logging.getLogger("app_messages")

SLUG = config.SLUG
CHAT = "household_chat"
ASSISTANT = "household_assistant"
WANTS = ["chat.message.post", "chat.chats.list"]
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LIST_EXPIRES = timedelta(minutes=2)
FORWARD_EXPIRES = timedelta(minutes=10)
RESEND_AFTER = (30, 90, 240)                 # seconds after the first post
NACK_TEXT = {
    ("not_allowed", "app"): "Household Chat doesn't allow Phones to post: an admin turns on \"Phones may post "
                            "forwarded messages\" in Chat's Admin → App settings.",
    ("not_allowed", "no_access"): "Household Chat doesn't know you yet — open Household Chat once.",
    ("not_allowed", "read_only"): "You can't post in that chat right now.",
    ("not_found", "chat"): "That chat isn't there any more, or you're no longer in it.",
}

WS = {"client": None}
_pending: dict = {}                         # forward envelope id -> {"phone", "env", "tries", "next", "expires"}
_pending_lock = threading.Lock()
POST = {"fn": None}                         # tests: replace the direct post (env -> bool)


def nack_text(reason: str | None, detail: str | None) -> str:
    return NACK_TEXT.get((reason, detail)) or {
        "busy": "Household Chat is busy; it will be tried again.", "expired": "Household Chat didn't answer in time.",
        "invalid": f"Household Chat refused it ({detail or 'invalid'}).",
        "unsupported_kind": "This Household Chat is too old for forwarded messages — update it.",
        "unsigned": "Household Chat needs the App-messages signing key (the same in both apps)."}.get(
        reason or "", f"Household Chat said no ({reason or 'no reason'}).")


# --------------------------------------------------------------------------------------------- life cycle

def start() -> None:
    """In the lifespan (nothing connects without a Supervisor token, as in the tests)."""
    from . import monitor, phones, tools
    if bus.default.started:
        return
    tools.tools.install(bus)
    ws = None
    if config.SUPERVISOR_TOKEN:
        ws = ha_ws.HAWebSocket(config.SUPERVISOR_CORE_WS, config.SUPERVISOR_TOKEN, name="phones-ws")
        ws.subscribe("state_changed", monitor.on_state_event)
        ws.subscribe("state_reported", monitor.on_state_event)
        for t in ("homeassistant_start", "homeassistant_started", "homeassistant_stop"):
            ws.subscribe(t, monitor.on_ha_event)        # restores and rewrites while it (re)starts aren't reports
        ws.on_connect(monitor.on_ws_connect)
        for t in ("device_registry_updated", "entity_registry_updated"):
            ws.subscribe(t, _registry_changed)
        ws.subscribe("household_phones_check_now", on_check_event)
        ws.subscribe(bus.EVENT_TYPE, on_answer_event)
        WS["client"] = ws
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, wants=WANTS, db=db.get_conn, ws=ws,
              outbox_thread=config.BACKGROUND_LOOPS, api_base=config.SUPERVISOR_CORE_API,
              ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN,
              signing_key=lambda: settings.REGISTRY.get("bus_signing_key"))
    if ws is not None:
        ws.start()
    _ = phones


def stop() -> None:
    bus.stop()
    ws = WS["client"]
    if ws is not None:
        ws.stop()
    WS["client"] = None


REFRESH = threading.Event()


def _registry_changed(event: dict) -> None:
    REFRESH.set()                           # the discovery loop reads the registries again soon


def on_check_event(event: dict) -> None:
    """`household_phones_check_now` {phone: <id or Home Assistant device name>} from a script or automation, while
    Sensors in Home Assistant is on."""
    if not settings.get("ha_sensors"):
        return
    data = event.get("data") if isinstance(event, dict) else None
    want = str((data or {}).get("phone") or "").strip().lower()
    if not want:
        return

    def run():
        from . import checknow, phones
        with db.get_conn() as conn:
            rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1").fetchall()
            hit = next((p for p in rows if p["id"] == want or phones_slug(conn, p) == want
                        or (p["ha_name"] or "").lower() == want), None)
        if hit is None:
            logger.info("household_phones_check_now: no phone %r.", want[:40])
            return
        try:
            checknow.start(hit["id"], None, via="home_assistant", by_name="Home Assistant")
        except Exception as e:                                       # noqa: BLE001 — a limit, a paused phone
            logger.info("household_phones_check_now for %s: %s", hit["id"], getattr(e, "detail", e))
        _ = phones
    threading.Thread(target=run, daemon=True, name="phones-check-event").start()


def phones_slug(conn, p) -> str:
    from . import ha_sensors
    return ha_sensors.slug(conn, p)


# --------------------------------------------------------------------------------------------- phones.forwarding_into

@bus.handler("phones.forwarding_into")
def forwarding_into(msg, conn):
    """Chat's ⋯ menu: the phones forwarding into this chat that the person may Check now (the contract §2)."""
    from . import auth, checknow, phones
    if msg.from_app != CHAT:
        raise bus.Nack("not_allowed", "sender")
    d = msg.data
    uid, chat_id, my_room = d.get("requested_by"), d.get("chat_id"), d.get("my_room")
    if not isinstance(uid, str) or not 0 < len(uid) <= 64:
        raise bus.Nack("invalid", "requested_by")
    if chat_id is not None and (not isinstance(chat_id, str) or not ID_RE.match(chat_id)):
        raise bus.Nack("invalid", "chat_id")
    if my_room is not None and not isinstance(my_room, bool):
        raise bus.Nack("invalid", "my_room")
    if not my_room and chat_id is None:
        raise bus.Nack("invalid", "chat_id")
    out = []
    if settings.get("forwarding_allowed"):
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        user = auth.user_dict(row) if row else {"id": uid, "name": "", "username": None, "is_child": False,
                                                 "is_admin": auth.is_admin(uid, "", None)}
        if my_room:
            rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1 AND fwd_on = 1 AND fwd_target = 'my_room' "
                                "AND owner_id = ?", (uid,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1 AND fwd_on = 1 AND fwd_target = 'chat' "
                                "AND fwd_chat_id = ?", (chat_id,)).fetchall()
        for p in rows:
            if checknow.may_check(p, user):
                out.append({"id": p["id"], "name": phones.display_name(conn, p)[:80],
                            "owner_name": phones.first_name(phones.owner_name(conn, p["owner_id"]))})
    return {"phones": out[:20], "page": "#/check/"}


# --------------------------------------------------------------------------------------------- asking Chat

def chat_ready(kind: str = "chat.message.post") -> bool:
    return bool(bus.default.started and bus.available(CHAT, kind))


def ask_chats(user_id: str, phone_id: str | None) -> str:
    """Ask Chat for the chats this person may post in → a request id the page polls. ValueError when Chat isn't
    there."""
    if not chat_ready("chat.chats.list"):
        raise ValueError("Household Chat isn't available — it isn't installed, or it hasn't said hello in the last "
                         "day (is it running?).")
    rid = secrets.token_hex(8)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO bus_requests (id, user_id, phone_id, kind, created) VALUES (?,?,?,?,?)",
                     (rid, user_id, phone_id, "chats", config.clock()))
    bus.send(CHAT, "chat.chats.list", {"requested_by": user_id, "limit": 50}, ref=f"chats:{rid}",
             expires_in=LIST_EXPIRES)
    _flush()
    return rid


def ask_members(phone_id: str) -> bool:
    """Ask Chat who is in the chat a phone forwards into (Check-now rights for its members)."""
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (phone_id,)).fetchone()
    if p is None or p["fwd_target"] != "chat" or not p["fwd_chat_id"] or not p["owner_id"] or not chat_ready("chat.chats.list"):
        return False
    bus.send(CHAT, "chat.chats.list", {"requested_by": p["owner_id"], "chat_id": p["fwd_chat_id"]},
             ref=f"members:{phone_id}", expires_in=LIST_EXPIRES)
    _flush()
    return True


def refresh_members() -> int:
    """Hourly: every forwarding chat's members again."""
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM phones WHERE retired = 0 AND fwd_on = 1 AND "
                                             "fwd_target = 'chat'")]
    return sum(ask_members(i) for i in ids)


def _flush() -> None:
    try:
        bus.run_outbox_once()
    except Exception:
        logger.exception("Sending a message failed (the outbox tries again)")


def _clean_chat(c) -> dict | None:
    if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not ID_RE.match(c["id"]):
        return None
    kind = c.get("kind") if c.get("kind") in ("group", "direct", "personal") else "group"
    members = c.get("members") if isinstance(c.get("members"), int) and not isinstance(c.get("members"), bool) else 0
    return {"id": c["id"], "kind": kind, "name": str(c.get("name") or "")[:120], "icon": str(c.get("icon") or "")[:8] or None,
            "members": members}


def on_reply(msg, conn) -> None:
    ref = msg.ref or ""
    if ref.startswith("chats:"):
        rid = ref[6:]
        if msg.kind == "ack":
            res = msg.result if isinstance(msg.result, dict) else {}
            chats = [c for c in (_clean_chat(x) for x in (res.get("chats") or [])[:50]) if c]
            conn.execute("UPDATE bus_requests SET state = 'done', result = ? WHERE id = ?", (json.dumps({"chats": chats}), rid))
        elif msg.kind == "nack":
            conn.execute("UPDATE bus_requests SET state = 'failed', error = ? WHERE id = ?",
                         (nack_text(msg.data.get("reason"), msg.data.get("detail")), rid))
    elif ref.startswith("members:"):
        pid = ref[8:]
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None:
            return
        if msg.kind == "ack":
            res = msg.result if isinstance(msg.result, dict) else {}
            hit = [c for c in res.get("chats") or [] if isinstance(c, dict) and c.get("id") == p["fwd_chat_id"]]
            if len(hit) == 1 and isinstance(hit[0].get("member_ids"), list):
                ids = [x for x in hit[0]["member_ids"] if isinstance(x, str) and 0 < len(x) <= 64][:200]
                conn.execute("UPDATE phones SET fwd_members = ?, fwd_members_at = ?, fwd_chat_name = COALESCE(?, fwd_chat_name) "
                             "WHERE id = ?", (json.dumps(ids), config.clock(), str(hit[0].get("name") or "")[:120] or None, pid))
        elif msg.kind == "nack" and msg.data.get("reason") == "not_found":
            conn.execute("UPDATE phones SET fwd_members = '[]', fwd_error = ? WHERE id = ?",
                         (nack_text("not_found", "chat"), pid))
    elif ref.startswith("alert:") and msg.kind == "nack":
        logger.info("Household Chat refused an alert: %s %s", msg.data.get("reason"), msg.data.get("detail") or "")


for _prefix in ("chats:", "members:", "alert:"):
    bus.on_reply(_prefix, on_reply)


def post_alert(user_id: str, title: str, message: str) -> bool:
    """An alert into the person's 📌 My room, as them (alerts hold no forwarded text: the outbox is fine)."""
    if not chat_ready():
        return False
    try:
        bus.send(CHAT, "chat.message.post", {"requested_by": user_id, "my_room": True, "label": title[:80],
                                             "text": message[:4000], "disappear_after": None},
                 ref=f"alert:{secrets.token_hex(4)}")
    except bus.BusError:
        return False
    _flush()
    return True


# --------------------------------------------------------------------------------------------- forwards (no storage)

def post_forward(phone_id: str, data: dict) -> bool:
    """Post one forward to Chat without writing it anywhere: the envelope lives in memory until Chat answers."""
    if POST["fn"] is not None:
        return bool(POST["fn"](dict(data)))
    if not chat_ready():
        _set_error(phone_id, "Household Chat isn't available (not installed, or not running).")
        return False
    b = bus.default
    try:
        env = b._envelope(CHAT, "chat.message.post", data, ref=f"fwd:{phone_id}", expires_in=FORWARD_EXPIRES)
    except (TypeError, ValueError):
        return False
    if bus.event_size(env) > bus.MAX_EVENT_BYTES:
        data = dict(data, text=data["text"][:2500].rstrip() + "…\n(shortened — see the phone)")
        env = b._envelope(CHAT, "chat.message.post", data, ref=f"fwd:{phone_id}", expires_in=FORWARD_EXPIRES)
    now = config.clock()
    with _pending_lock:
        _pending[env["id"]] = {"phone": phone_id, "env": env, "tries": 1, "first": now,
                               "expires": now + FORWARD_EXPIRES.total_seconds()}
    ok = b._post(env)
    logger.info("bus forward id=%s to=%s kind=chat.message.post posted=%s", env["id"], CHAT, ok)
    return True


def resend_forwards(now: float | None = None) -> int:
    """Every few seconds: post again what Chat hasn't answered (the same id: Chat acts once); forget the expired."""
    now = config.clock() if now is None else now
    out = []
    with _pending_lock:
        for mid, item in list(_pending.items()):
            if now >= item["expires"] or item["tries"] > len(RESEND_AFTER):
                _pending.pop(mid, None)
                _set_error(item["phone"], "Household Chat didn't answer — is it running?")
                continue
            if now - item["first"] >= RESEND_AFTER[item["tries"] - 1]:
                item["tries"] += 1
                out.append(item["env"])
    for env in out:
        bus.default._post(env)
    return len(out)


def on_answer_event(event: dict) -> None:
    """Chat's ack / nack of a forward (the same household_apps events the bus reads; its own outbox doesn't know
    these ids and ignores them)."""
    env = event.get("data") if isinstance(event, dict) else None
    if not isinstance(env, dict) or env.get("to") != SLUG or env.get("from") != CHAT or env.get("kind") not in ("ack", "nack"):
        return
    with _pending_lock:
        item = _pending.get(env.get("reply_to"))
    if item is None:
        return
    key = bus.default._key()
    if key is not None and not bus.verify(env, key):
        return
    data = env.get("data") if isinstance(env.get("data"), dict) else {}
    if env["kind"] == "nack" and data.get("reason") == "busy":
        return                                                   # tried again by resend_forwards
    with _pending_lock:
        _pending.pop(env.get("reply_to"), None)
    if env["kind"] == "ack":
        with db.get_conn() as conn:
            conn.execute("UPDATE phones SET fwd_error = NULL, fwd_ok_at = ? WHERE id = ?", (config.clock(), item["phone"]))
    else:
        _set_error(item["phone"], nack_text(data.get("reason"), data.get("detail")))


def _set_error(phone_id: str, text: str) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE phones SET fwd_error = ? WHERE id = ?", (text[:300], phone_id))


def pending_count() -> int:
    with _pending_lock:
        return len(_pending)


def connected_apps() -> dict:
    on = bool(bus.default.started and bus.default.token)
    return {"on": on, "connected": bus.default.connected, "chat": chat_ready(),
            "apps": bus.apps() if bus.default.started else []}


def reset() -> None:
    """Tests."""
    with _pending_lock:
        _pending.clear()
