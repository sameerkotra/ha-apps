"""Messages from the other household apps (APP_MESSAGES_SPEC.md §6.3; SPEC §15.11).

Household Docs asks Chat for the chats a person may post in (`chat.chats.list`) and posts a card for them
(`chat.card`). Every request is checked against Chat's own rules exactly as if that person did it in the app
(`requested_by` is the actor; who sent the message never matters), inside the transaction that records the
answer (app/common/app_bus.py). Live updates and notifications go out after that commit.

The bus shares Chat's one WebSocket to Home Assistant with the notification buttons (ha_events.py); the
outbox runs in the 20-second housekeeping tick.
"""
import json
import logging
import re

from fastapi import HTTPException

from . import chats, config, db, tools
from .common import app_bus as bus

logger = logging.getLogger("app_messages")

SLUG = "household_chat"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_ANSWER = 7000          # bytes of the ack's result; the whole event must stay under 8 KB
LIST_MAX = 50


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


# ---------- the Household Assistant (tools.py; HOUSEHOLD_ASSISTANT_SPEC §5–6) ----------
tools.tools.install(bus)


# ---------- life cycle ----------
def start(ws=None) -> None:
    """In the lifespan, before `ws` (ha_events' connection) starts. Off without a Supervisor token."""
    if bus.default.started:
        return
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, db=db.get_conn, ws=ws, outbox_thread=False,
              api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


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


def assistant_page() -> str | None:
    """The Household Assistant's sidebar page for the ➕ menu's "Ask the assistant" (HOUSEHOLD_ASSISTANT_SPEC §10,
    phase 5), or None: only while the assistant is on the bus (said hello in the last 24 hours), and only when this
    app knows its own page — apps installed from one repository share its id, so the assistant's page is
    "/<that id>_household_assistant"."""
    if not bus.default.started:
        return None
    if not any(a["slug"] == ASSISTANT and a["active"] for a in bus.apps()):
        return None
    m = _OWN_PAGE.fullmatch(config.INGRESS_URL or "")
    return f"/{m.group(1)}_{ASSISTANT}" if m else None
