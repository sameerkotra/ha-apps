"""Send to Chat and Checklist → Todo (SPEC §17.15–§17.16, APP_MESSAGES_SPEC §6.3–§6.5), and Admin → Connected apps.

Each action sends one message through the app bus (app_messages.py) and answers a request id; the page polls
`GET /api/bus/requests/{id}` until Chat or Todo has answered (or the message expired). Nothing here decides what
Chat or Todo allow — they check the person themselves; Docs checks its own side (who may see, share or change
the item) before anything is sent, and again when an answer asks Docs to do something (member shares, Move).
"""
import json

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import app_messages as am
from .. import config, db, documents, kids, sharing
from ..auth import require_admin, require_user
from ..formats import checklist_md, text as text_fmt
from ..store import fileio, moving, nodes

router = APIRouter(prefix="/api", tags=["connect"])
CARD_TYPES = {"note": "note", "markdown": "note", "checklist": "checklist", "sheet": "sheet", "folder": "folder",
              "file": "file"}


def _title(node) -> str:
    name = node["name"]
    if nodes.kind_info(node["kind"])["document"] and node["ext"] and name.lower().endswith("." + node["ext"].lower()):
        name = name[:-(len(node["ext"]) + 1)]
    return name[:200] or "Untitled"


def _target(node) -> str:
    if node["kind"] == "folder":
        return f"/folder/{node['id']}"
    return f"/doc/{node['id']}" if nodes.kind_info(node["kind"])["document"] else f"/file/{node['id']}"


# ---------------------------------------------------------------- requests
def _who_cant_open(conn, node, member_ids) -> tuple[list[dict], int]:
    """Members (other than you) who can't open the item now: ([{id, name}] of Docs users, how many aren't Docs
    users)."""
    from ..auth import user_dict
    people, outside = [], 0
    for uid in member_ids or []:
        u = sharing.user_row(conn, uid)
        if u is None:
            outside += 1
        elif u["disabled"] or sharing.role_of(conn, user_dict(u), node) is None:
            people.append({"id": u["id"], "name": u["name"], "off": bool(u["disabled"])})
    return people, outside


@router.get("/bus/requests/{request_id}")
def request_state(request_id: str, user: dict = Depends(require_user)):
    """A request this person made: pending, done (with the answer) or failed (with why, in plain words)."""
    with db.get_conn() as conn:
        r = am.get_request(conn, user, request_id)
        out = {"id": r["id"], "kind": r["kind"], "state": r["state"], "error": r["error"],
               "since": r["created_at"], "result": json.loads(r["result"]) if r["result"] else None}
        if r["kind"] == "members" and out["result"]:
            node = nodes.get(conn, r["node_id"]) if r["node_id"] else None
            ids = out["result"].pop("memberIds", None) or []
            out["result"]["cantOpen"], out["result"]["notInDocs"] = _who_cant_open(conn, node, ids) if node else ([], 0)
        if r["kind"] == "todo" and r["state"] == "pending":
            s = conn.execute("SELECT * FROM todo_sends WHERE request_id = ?", (r["id"],)).fetchone()
            if s is not None:
                out["progress"] = {"sent": s["items_sent"], "total": s["items_total"]}
        return out


# ---------------------------------------------------------------- Send to chat (§17.15)
def _chat_item(conn, user: dict, node_id: str):
    if kids.is_child(user):
        raise HTTPException(403, kids.MESSAGE)
    node, role = sharing.require(conn, user, node_id, "viewer")
    return node, role


@router.post("/nodes/{node_id}/chat/chats")
def chat_chats(node_id: str, body: dict | None = Body(default=None), user: dict = Depends(require_user)):
    """Ask Chat for the chats you may post in (an answer within 2 minutes, or none) — names and sizes only. With
    {chatId}: who is in that one chat (for *Also give the chat's members access*; APP_MESSAGES_SPEC §6.3)."""
    body = body or {}
    if not isinstance(body, dict) or set(body) - {"chatId"}:
        raise HTTPException(422, "Send chatId, or nothing.")
    chat_id = body.get("chatId")
    if chat_id is not None and (not isinstance(chat_id, str) or not am.ID_RE.match(chat_id)):
        raise HTTPException(422, "Choose a chat.")
    with db.get_conn() as conn:
        node, _role = _chat_item(conn, user, node_id)
        if chat_id is None:
            rid, ref = am.new_request(conn, user, "chats", node["id"])
            data = {"requested_by": user["id"], "limit": 50}
        else:
            rid, ref = am.new_request(conn, user, "members", node["id"], {"chatId": chat_id})
            data = {"requested_by": user["id"], "chat_id": chat_id}
    try:
        am.send(am.CHAT, "chat.chats.list", data, ref, expires_in=am.LIST_EXPIRES)
    except HTTPException as e:
        with db.get_conn() as conn:
            am._finish(conn, rid, error=str(e.detail))
        raise
    return {"request": rid, "expiresIn": int(am.LIST_EXPIRES.total_seconds())}


@router.post("/nodes/{node_id}/chat/card")
def chat_card(node_id: str, body: dict = Body(...), user: dict = Depends(require_user)):
    """{chatId, share?: null | "viewer" | "editor", members?: <members request id>, memberIds?: [ids]} — Chat posts
    "<you> shared <title>" with Open in Docs. With `share`, the people the sender confirmed (`memberIds`, from the
    members Chat named for that chat in the `members` request) get a normal Docs share once Chat has answered
    (owners and managers only); nobody else, whatever an answer says."""
    if not isinstance(body, dict) or set(body) - {"chatId", "share", "members", "memberIds"}:
        raise HTTPException(422, "Send chatId, share, members and memberIds.")
    chat_id, share = body.get("chatId"), body.get("share")
    if not isinstance(chat_id, str) or not am.ID_RE.match(chat_id):
        raise HTTPException(422, "Choose a chat.")
    if share not in (None, "viewer", "editor"):
        raise HTTPException(422, "Share is viewer or editor.")
    picked = body.get("memberIds")
    if share and (not isinstance(body.get("members"), str) or not isinstance(picked, list) or len(picked) > 200
                  or not all(isinstance(x, str) and 0 < len(x) <= 128 for x in picked)):
        raise HTTPException(422, "Choose who in the chat gets access.")
    with db.get_conn() as conn:
        node, role = _chat_item(conn, user, node_id)
        owner = sharing.owner_id(conn, node)
        if share and (owner is None or not sharing.at_least(role, "manager")):
            raise HTTPException(403, "Only its owner or a manager can give the chat's members access."
                                if owner is not None else "Items in admin shared folders are shared through the "
                                                          "folder's access, not one by one.")
        if share and moving.is_read_only(conn):
            fileio.ensure_writable()
        o = sharing.user_row(conn, owner) if owner else None
        data = {"requested_by": user["id"], "chat_id": chat_id, "title": _title(node),
                "type": CARD_TYPES.get(node["kind"], "file"), "item_id": node["id"], "target": _target(node),
                "share_with_members": bool(share), "badge": am.BADGE}
        if o is not None:
            data["owner_name"] = o["name"][:80]
        if am.panel():
            data["panel"] = am.panel()
        members: list[str] = []
        if share:
            mr = am.get_request(conn, user, body["members"])
            res = json.loads(mr["result"]) if mr["result"] else None
            if (mr["kind"] != "members" or mr["state"] != "done" or mr["node_id"] != node["id"] or not res
                    or res.get("chatId") != chat_id):
                raise HTTPException(409, "Ask Household Chat who is in that chat first.")
            named = set(res.get("memberIds") or [])
            members = [x for x in dict.fromkeys(picked) if x in named]
        rid, ref = am.new_request(conn, user, "card", node["id"], {"share": share, "chatId": chat_id, "members": members})
        db.audit(conn, "sent_to_chat", user["id"], node["id"], node["root_id"])
    try:
        am.send(am.CHAT, "chat.card", data, ref)
    except HTTPException as e:
        with db.get_conn() as conn:
            am._finish(conn, rid, error=str(e.detail))
        raise
    return {"request": rid}


# ---------------------------------------------------------------- Checklist → Todo (§17.16)
def _checklist(conn, user: dict, node_id: str):
    node, role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] != "checklist":
        raise HTTPException(422, "Only checklists go to Todo.")
    _root, _real, data, _etag, _st, _sha = documents._read(conn, node)
    try:
        text, _m = text_fmt.decode(data)
        items = checklist_md.parse(text)
    except (text_fmt.NotText, ValueError):
        raise HTTPException(409, "This file isn't a checklist any more (it was changed outside the app).")
    return node, role, items


@router.post("/docs/{node_id}/todo/lists")
def todo_lists(node_id: str, user: dict = Depends(require_user)):
    """Ask Todo for the lists you may add to."""
    with db.get_conn() as conn:
        node, _role, _items = _checklist(conn, user, node_id)
        rid, ref = am.new_request(conn, user, "lists", node["id"])
    try:
        am.send(am.TODO, "todo.lists.list", {"requested_by": user["id"]}, ref, expires_in=am.LIST_EXPIRES)
    except HTTPException as e:
        with db.get_conn() as conn:
            am._finish(conn, rid, error=str(e.detail))
        raise
    return {"request": rid, "expiresIn": int(am.LIST_EXPIRES.total_seconds())}


@router.post("/docs/{node_id}/todo")
def to_todo(node_id: str, body: dict = Body(...), user: dict = Depends(require_user)):
    """{listId? + listKind?, new?: {name, shared}, keys?: [item keys] (default: every item), includeTicked: bool,
    move: bool}. Ticked items stay out unless included. Long checklists go in several messages (the first makes
    the list). Move takes the items out of the checklist only once Todo has acked them."""
    allowed = {"listId", "listKind", "listName", "new", "keys", "includeTicked", "move"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise HTTPException(422, "Send listId or new, keys, includeTicked and move.")
    list_id, new = body.get("listId"), body.get("new")
    if (list_id is None) == (new is None):
        raise HTTPException(422, "Choose a list, or make a new one.")
    if list_id is not None and (not isinstance(list_id, str) or not 0 < len(list_id) <= 64):
        raise HTTPException(422, "Choose a list.")
    if new is not None:
        if not isinstance(new, dict) or set(new) - {"name", "shared"} or not isinstance(new.get("name"), str) \
                or not 1 <= len(new["name"].strip()) <= 60 or not isinstance(new.get("shared", False), bool):
            raise HTTPException(422, "A new list needs a name (1–60 characters).")
        new = {"name": " ".join(new["name"].split()), "shared": bool(new.get("shared", False))}
    keys = body.get("keys")
    if keys is not None and (not isinstance(keys, list) or len(keys) > 2000 or not all(isinstance(k, str) for k in keys)):
        raise HTTPException(422, "keys is a list of items.")
    include_ticked, move = body.get("includeTicked", False), body.get("move", False)
    if not isinstance(include_ticked, bool) or not isinstance(move, bool):
        raise HTTPException(422, "includeTicked and move are true or false.")
    with db.get_conn() as conn:
        node, role, items = _checklist(conn, user, node_id)
        if move:
            if not sharing.at_least(role, "editor"):
                raise HTTPException(403, "Only people who can edit this checklist can move items out of it — "
                                         "choose Keep in Docs.")
            fileio.ensure_writable()
        wanted = set(keys) if keys is not None else None
        if wanted is not None and not wanted <= {it.key for it in items}:
            raise HTTPException(409, "Some of those items aren't there any more — the checklist changed. Try again.")
        chosen = [it for it in items if (wanted is None or it.key in wanted) and (include_ticked or not it.done)]
        if not chosen:
            raise HTTPException(422, "Nothing to send — every item you chose is ticked (tick “Include ticked items”).")
        out, last_task = [], None
        parent_of = {}
        for it in items:                               # the task each sub-item belongs to in the checklist
            if it.level == 0:
                last_task = it.key
            parent_of[it.key] = last_task if it.level == 1 else None
        sent_tasks = {it.key for it in chosen if it.level == 0}
        for it in chosen:
            level = 1 if it.level == 1 and parent_of[it.key] in sent_tasks else 0
            out.append({"text": am.clean_text(it.text) or "(empty)", "done": it.done, "level": level, "key": it.key})
        parts = am.todo_parts([{k: v for k, v in x.items() if k != "key"} | {"_k": x["key"]} for x in out])
        stored = []
        for p in parts:
            stored.append({"n": len(p), "keys": [x.pop("_k") for x in p], "items": p, "state": "waiting"})
        sid = db.new_id()
        rid, _ref = am.new_request(conn, user, "todo", node["id"], {"sendId": sid})
        target = {"listId": list_id} if list_id else {"new": new}
        target["order"] = [it.key for it in items]
        list_kind = ("shared" if new["shared"] else "personal") if new else (
            body.get("listKind") if body.get("listKind") in ("shared", "personal") else None)
        list_name = new["name"] if new else (str(body.get("listName") or "")[:80] or None)
        conn.execute("INSERT INTO todo_sends (id, node_id, user_id, request_id, target, list_name, list_kind, move, parts, "
                     "items_total, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sending', ?, ?)",
                     (sid, node["id"], user["id"], rid, json.dumps(target), list_name, list_kind, 1 if move else 0,
                      json.dumps(stored), len(out), config.now_iso(), config.now_iso()))
        db.audit(conn, "sent_to_todo", user["id"], node["id"], node["root_id"])
    if not am.bus.default.started or not (am.bus.available(am.TODO, "todo.items.add")):
        with db.get_conn() as conn:
            msg = ("Household Todo can't be reached: app messages work only when Household Docs runs inside Home Assistant."
                   if not am.bus.default.started else "Household Todo isn't available — it isn't installed, or it "
                                                      "hasn't said hello in the last day (is it running?).")
            conn.execute("UPDATE todo_sends SET state = 'failed', error = ?, parts = '[]' WHERE id = ?", (msg, sid))
            am._finish(conn, rid, error=msg)
        raise HTTPException(409, msg)
    am.send_next(sid)
    return {"request": rid, "parts": len(parts), "items": len(out)}


# ---------------------------------------------------------------- Admin → Connected apps
@router.get("/admin/connected-apps")
def connected_apps(admin: dict = Depends(require_admin)):
    return am.connected_apps()
