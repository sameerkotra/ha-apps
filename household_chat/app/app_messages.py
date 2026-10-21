"""Messages from the other household apps (APP_MESSAGES_SPEC.md §6.3; SPEC §15.11).

Household Docs asks Chat for the chats a person may post in (`chat.chats.list`) and posts a card for them
(`chat.card`); Household Phones posts the texts a phone forwards (`chat.message.post`), and Chat asks it which
phones forward into a chat (`phones.forwarding_into`, for the chat menu's "📱 Check <phone>"). Every request is checked against Chat's own rules exactly as if that person did it in the app
(`requested_by` is the actor; who sent the message never matters), inside the transaction that records the
answer (app/common/app_bus.py). Live updates and notifications go out after that commit.

The bus shares Chat's one WebSocket to Home Assistant with the notification buttons (ha_events.py); the
outbox runs in the 20-second housekeeping tick.
"""
import json
import logging
import re
import secrets
import threading
import time
from datetime import timedelta

from fastapi import HTTPException

from . import chats, config, db, settings, tools
from .common import app_bus as bus

logger = logging.getLogger("app_messages")

SLUG = "household_chat"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ANSWER = 7000          # bytes of the ack's result; the whole event must stay under 8 KB
LIST_MAX = 50
POST_MAX = 4000            # characters of a chat.message.post text
LABEL_MAX = 80
DISAPPEAR = {"1h": 3600, "1d": 86400, "7d": 604800, "30d": 2592000}
# the apps that may post plain-text messages (chat.message.post): slug → (its Admin → App settings switch, its icon)
POSTERS = {"household_phones": ("phones_may_post", "📱")}


# ---------- checks ----------
def _restoring() -> None:
    if db.RESTORING.is_set():
        raise bus.Nack("busy", "restoring")


def _actor(conn, data: dict) -> dict:
    """`requested_by` as Chat's user dict — an enabled Chat user, else nack."""
    uid = data.get("requested_by")
    if not isinstance(uid, str) or not uid or len(uid) > 64:
        raise bus.Nack("invalid", "requested_by")
    u = chats.user_row(conn, uid)
    if u is None or u["disabled"]:
        raise bus.Nack("not_allowed", "no_access")
    return {"id": u["id"], "name": u["name"], "is_admin": False, "is_child": bool(u["is_child"])}


def _text(data: dict, key: str, most: int, required: bool = False):
    v = data.get(key)
    if v is None and not required:
        return None
    if not isinstance(v, str):
        raise bus.Nack("invalid", key)
    v = " ".join(v.split())
    if not v and not required:
        return None
    if not 1 <= len(v) <= most:
        raise bus.Nack("invalid", key)
    return v


def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _fit(result: dict, items_key: str, trim_key: str | None) -> dict:
    """Keep an answer under MAX_ANSWER: drop `trim_key` from the last entries first, then entries (more)."""
    items = result[items_key]
    if trim_key:
        i = len(items) - 1
        while _size(result) > MAX_ANSWER and i >= 0:
            items[i].pop(trim_key, None)
            i -= 1
    while _size(result) > MAX_ANSWER and items:
        items.pop()
        result["more"] = True
    return result


def _badge(conn, msg, data: dict) -> str:
    b = _text(data, "badge", 20)
    if b:
        return b
    r = conn.execute("SELECT name FROM bus_apps WHERE slug = ?", (msg.from_app,)).fetchone()
    name = (r["name"] if r and r["name"] else msg.from_app) or "another app"
    return name[len("Household "):] if name.startswith("Household ") else name[:20]


# ---------- handlers ----------
@bus.handler("chat.chats.list")
def on_chats_list(msg, conn):
    """The chats `requested_by` may post in — without who is in them (the answer goes through Home Assistant's
    event history). With `chat_id`: only that chat, with `member_ids` (its enabled members except
    `requested_by`), for Docs' "give the chat's members access" (APP_MESSAGES_SPEC §6.3)."""
    _restoring()
    user = _actor(conn, msg.data)
    one = msg.data.get("chat_id")
    if one is not None and (not isinstance(one, str) or not ID_RE.match(one)):
        raise bus.Nack("invalid", "chat_id")
    limit = msg.data.get("limit", LIST_MAX)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIST_MAX:
        raise bus.Nack("invalid", "limit")
    out = []
    for c in chats.list_conversations(conn, user["id"]):          # Chat's own order and visibility
        if c["readOnly"] or (one is not None and c["id"] != one):
            continue
        ids = chats.member_ids(conn, c["id"])                       # enabled members
        item = {"id": c["id"], "kind": c["kind"], "name": c["name"], "members": len(ids)}
        if one is not None:
            item["member_ids"] = [u for u in ids if u != user["id"]]
        if c["icon"]:
            item["icon"] = c["icon"]
        if c["isHousehold"]:
            item["household"] = True
        out.append(item)
    if one is not None:
        if not out:
            raise bus.Nack("not_found", "chat")
        return _fit({"chats": out, "more": False}, "chats", "member_ids")
    more = len(out) > limit
    return _fit({"chats": out[:limit], "more": more}, "chats", None)


@bus.handler("chat.card")
def on_card(msg, conn):
    _restoring()
    d = msg.data
    user = _actor(conn, d)
    cid = d.get("chat_id")
    if not isinstance(cid, str) or not ID_RE.match(cid):
        raise bus.Nack("invalid", "chat_id")
    conv = chats.conv_row(conn, cid)
    if conv is None or chats.member_row(conn, cid, user["id"]) is None:
        raise bus.Nack("not_found", "chat")
    if not chats.can_post(conn, conv, user["id"]):
        raise bus.Nack("not_allowed", "read_only")
    title = _text(d, "title", 200, required=True)
    kind = d.get("type")
    if kind not in chats.CARD_TYPES:
        raise bus.Nack("invalid", "type")
    item_id = d.get("item_id")
    if not isinstance(item_id, str) or not ID_RE.match(item_id):
        raise bus.Nack("invalid", "item_id")
    owner = _text(d, "owner_name", 80)
    panel, target = d.get("panel"), d.get("target")
    if panel is not None and not chats.panel_ok(panel, msg.from_app):       # only the sender's own page
        raise bus.Nack("invalid", "panel")
    if target is not None and not chats.target_ok(target):
        raise bus.Nack("invalid", "target")
    share = d.get("share_with_members", False)
    if not isinstance(share, bool):
        raise bus.Nack("invalid", "share_with_members")
    badge = _badge(conn, msg, d)
    try:
        chats.message_limit.take(user["id"])                       # the same 30 a minute as typing
    except HTTPException:
        raise bus.Nack("busy", "rate") from None
    out = chats.Outbox()
    mid = chats.post_card(conn, out, conv, user, {
        "app": msg.from_app, "badge": badge, "type": kind, "item_id": item_id, "title": title,
        "owner_name": owner, "panel": panel, "target": target, "share_with_members": share}, bus_id=msg.id)
    db.audit(conn, "card_from_app", user["id"], cid, user["id"])
    msg.after_commit(out.flush)
    result = {"message_id": mid, "chat_id": cid}
    if share:
        result["member_ids"] = [u for u in chats.member_ids(conn, cid) if u != user["id"]]
    return result


@bus.handler("chat.message.post")
def on_message_post(msg, conn):
    """One plain-text message from another app (Household Phones' forwarded texts) into a chat, as `requested_by`
    — checked like a message they type, at the same rate limit as cards. `disappear_after` may be set even for a
    child (their phone forwarding into their own room must work); in a disappearing chat the shorter time applies."""
    _restoring()
    poster = POSTERS.get(msg.from_app)
    if poster is None or not settings.get(poster[0], conn):
        raise bus.Nack("not_allowed", "app")
    d = msg.data
    user = _actor(conn, d)
    cid, mine = d.get("chat_id"), d.get("my_room", False)
    if mine is None:
        mine = False
    if not isinstance(mine, bool):
        raise bus.Nack("invalid", "my_room")
    if mine == (cid is not None):                                   # exactly one of the two
        raise bus.Nack("invalid", "chat_id")
    if mine:
        cid = chats.ensure_personal(conn, user["id"])
    elif not isinstance(cid, str) or not ID_RE.match(cid):
        raise bus.Nack("invalid", "chat_id")
    conv = chats.conv_row(conn, cid)
    if conv is None or chats.member_row(conn, cid, user["id"]) is None:
        raise bus.Nack("not_found", "chat")
    if not chats.can_post(conn, conv, user["id"]):
        raise bus.Nack("not_allowed", "read_only")
    label = _text(d, "label", LABEL_MAX, required=True)
    text = d.get("text")
    if not isinstance(text, str):
        raise bus.Nack("invalid", "text")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not 1 <= len(text) <= POST_MAX or "\x00" in text:
        raise bus.Nack("invalid", "text")
    after = d.get("disappear_after")
    if after is not None and after not in DISAPPEAR:
        raise bus.Nack("invalid", "disappear_after")
    times = [t for t in (DISAPPEAR.get(after), conv["disappear_seconds"]) if t]
    try:
        chats.message_limit.take(user["id"])                       # the same 30 a minute as typing (and cards)
    except HTTPException:
        raise bus.Nack("busy", "rate") from None
    out = chats.Outbox()
    mid = chats.post_app_message(conn, out, conv, user, text, min(times) if times else None, {
        "app": msg.from_app, "badge": _badge(conn, msg, {}), "icon": poster[1], "label": label}, bus_id=msg.id)
    db.audit(conn, "message_from_app", user["id"], cid, user["id"])
    msg.after_commit(out.flush)
    return {"message_id": mid, "chat_id": cid}


# ---------- asking Household Phones (the chat menu's "📱 Check <phone>") ----------
PHONES = "household_phones"
FORWARDING_INTO = "phones.forwarding_into"
PHONES_WAIT = 4.0          # seconds the page's request waits for the answer; then nothing is shown
PHONES_CACHE = 60          # seconds an answer is reused, per (person, chat)
PHONE_PAGE_RE = re.compile(r"^#/[A-Za-z0-9_/-]{0,60}$")
_phones_lock = threading.Lock()
_phones_cache: dict = {}   # (user id, chat id) → (monotonic time, [phones])
_phones_waiting: dict = {}  # ref → (answered, holder)


def _on_phones_answer(msg, conn):
    with _phones_lock:
        w = _phones_waiting.get(msg.ref)
    if w:
        w[1]["msg"] = msg
        w[0].set()


bus.on_reply("phones:", _on_phones_answer)


def _phones_out(result, panel: str) -> list:
    """Phones' answer as the page gets it, each with its page in Home Assistant; anything malformed left out."""
    if not isinstance(result, dict) or not isinstance(result.get("phones"), list):
        return []
    page = result.get("page")
    if not isinstance(page, str) or not PHONE_PAGE_RE.match(page):
        return []
    out = []
    for p in result["phones"][:20]:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not ID_RE.match(p["id"]):
            continue
        name = p.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        owner = p.get("owner_name")
        out.append({"id": p["id"], "name": " ".join(name.split())[:LABEL_MAX],
                    "ownerName": " ".join(owner.split())[:LABEL_MAX] if isinstance(owner, str) else None,
                    "url": panel + page + p["id"]})
    return out


def phones_forwarding_into(user_id: str, cid: str, my_room: bool) -> list:
    """The phones that forward into chat `cid` and that `user_id` may check, asked of Household Phones as that
    person: [] when Phones isn't on the bus or doesn't answer in PHONES_WAIT seconds. Answers are kept 60 s."""
    key = (user_id, cid)
    now = time.monotonic()
    with _phones_lock:
        hit = _phones_cache.get(key)
        if hit and now - hit[0] < PHONES_CACHE:
            return hit[1]
    panel = app_page(PHONES)
    if panel is None or not bus.available(PHONES, FORWARDING_INTO):
        return []
    ref = "phones:" + secrets.token_hex(8)
    answered, holder = threading.Event(), {}
    with _phones_lock:
        _phones_waiting[ref] = (answered, holder)
    try:
        try:
            bus.send(PHONES, FORWARDING_INTO, {"requested_by": user_id, "chat_id": cid, "my_room": bool(my_room)},
                     ref=ref, expires_in=timedelta(seconds=30))
            bus.run_outbox_once()                                   # now, not at the next housekeeping tick
        except (bus.BusError, ValueError) as e:
            logger.info("Couldn't ask Household Phones: %s", e)
            return []
        if not answered.wait(PHONES_WAIT):
            return []
    finally:
        with _phones_lock:
            _phones_waiting.pop(ref, None)
    answer = holder["msg"]
    phones = _phones_out(answer.result, panel) if answer.kind == "ack" else []
    with _phones_lock:
        if len(_phones_cache) > 500:
            _phones_cache.clear()
        _phones_cache[key] = (time.monotonic(), phones)
    return phones


def forget_phones() -> None:
    with _phones_lock:
        _phones_cache.clear()


# ---------- the Household Assistant (tools.py; HOUSEHOLD_ASSISTANT_SPEC §5–6) ----------
tools.tools.install(bus)


# ---------- life cycle ----------
def start(ws=None) -> None:
    """In the lifespan, before `ws` (ha_events' connection) starts. Off without a Supervisor token."""
    if bus.default.started:
        return
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, db=db.get_conn, ws=ws, outbox_thread=False,
              api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN,
              signing_key=_signing_key)


def _signing_key() -> str:
    """Admin → App settings → Messages between apps (the same key in every household app; empty: unsigned)."""
    from . import settings
    return settings.get("bus_signing_key") or ""


def stop() -> None:
    bus.stop()


def run_outbox() -> None:
    """From housekeeping: re-sends, expiry, pruning and the six-hourly hello."""
    if bus.default.started:
        bus.run_outbox_once()


def connected_apps() -> dict:
    """Admin → Connected apps (APP_MESSAGES_SPEC §5)."""
    on = bool(bus.default.started and bus.default.token)
    return {"on": on, "connected": bus.default.connected, "apps": bus.apps() if bus.default.started else []}


ASSISTANT = "household_assistant"
_OWN_PAGE = re.compile(r"/(?:app/)?([0-9a-f]{8}|local)_household_chat")


def app_page(slug: str) -> str | None:
    """Another household app's sidebar page, or None: only while that app is on the bus (said hello in the last 24
    hours), and only when this app knows its own page — apps installed from one repository share its id, so the
    other app's page is "/<that id>_<its slug>"."""
    if not bus.default.started:
        return None
    if not any(a["slug"] == slug and a["active"] for a in bus.apps()):
        return None
    m = _OWN_PAGE.fullmatch(config.INGRESS_URL or "")
    return f"/{m.group(1)}_{slug}" if m else None


def assistant_page() -> str | None:
    """The Household Assistant's sidebar page for the ➕ menu's "Ask the assistant" (HOUSEHOLD_ASSISTANT_SPEC §10,
    phase 5), or None (app_page)."""
    return app_page(ASSISTANT)
