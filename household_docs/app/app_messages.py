"""Messages to the other household apps (APP_MESSAGES_SPEC.md §6.3–§6.5; SPEC §17.15–§17.16).

Household Docs asks Household Chat for the chats a person may post in (`chat.chats.list`) and has it post a card
(`chat.card`); it asks Household Todo for the lists a person may add to (`todo.lists.list`) and turns checklist
items into tasks (`todo.items.add`). Docs only sends: it has no handlers of its own (`can` is empty), and every
request names the person (`requested_by`) — Chat and Todo check everything against their own rules.

- **Requests** (`bus_requests`): each send is a row the page polls (`GET /api/bus/requests/{id}`). Answers find
  their row by the message's `ref` ("chats:<id>", "card:<id>", "lists:<id>", "todo:<send id>:<part>"). The reply
  callbacks run inside the bus's own transaction, so they only write down what happened; anything else — the
  next part of a long checklist, taking moved items out of the checklist, telling people about new shares —
  is done by `pump()` straight after (a small worker thread, and every housekeeping tick).
- **Envelopes never carry document content**: a card has the title, type, owner's name and ids; a Todo send has
  the checklist items the person chose (the one exception the spec allows, §3 there). Nothing is logged with them.
- **Delivery**: the bus's outbox (re-sends, expiry) is run right after every send, so lists come back while the
  dialog waits, and every housekeeping tick (APP_MESSAGES_SPEC §4).
- **"Open in Docs"**: Docs learns its own sidebar page (`panel`) from the Supervisor (`GET /addons/self/info`:
  slug and ingress_panel), falling back to the HOSTNAME the Supervisor sets (§6.5 there).
"""
import json
import logging
import os
import re
import threading
from datetime import timedelta

from fastapi import HTTPException

from . import config, db
from .common import app_bus as bus

logger = logging.getLogger("app_messages")

SLUG = "household_docs"
CHAT, TODO = "household_chat", "household_todo"
WANTS = ["chat.card", "chat.chats.list", "todo.items.add", "todo.lists.list"]
LIST_EXPIRES = timedelta(minutes=2)            # a list is answered at once or not at all (§6 there)
MAX_DATA = 6800                                # bytes of a message's data: the whole event stays under 8 KB
TODO_MAX_ITEMS = 200                           # per message (Todo's own limit)
TODO_MAX_SUBITEMS = 100                        # under one task
TODO_TEXT = 200                                # characters per item
BADGE = "Docs"
PANEL_SLUG_RE = re.compile(r"^([0-9a-f]{8}|local)_household_docs$")
PANEL_RE = re.compile(r"^/[a-z0-9_]{1,80}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_panel = {"value": None, "known": False}
_wake = threading.Event()
_stop = threading.Event()
_worker: dict = {"thread": None}
_send_lock = threading.Lock()


# ---------------------------------------------------------------- the page path ("Open in Docs")
def fetch_self_info() -> dict | None:
    """GET {SUPERVISOR_API}/addons/self/info (allowed for every app with the Supervisor token) → its data."""
    if not config.SUPERVISOR_TOKEN:
        return None
    import urllib.request
    req = urllib.request.Request(f"{config.SUPERVISOR_API}/addons/self/info",
                                 headers={"Authorization": f"Bearer {config.SUPERVISOR_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read(256 * 1024).decode("utf-8"))
    except Exception as e:                                       # noqa: BLE001 — the fallback below
        logger.info("Couldn't read this app's info from the Supervisor (%s); using HOSTNAME.", type(e).__name__)
        return None
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else None


def learn_panel(info: dict | None = None, hostname: str | None = None) -> str | None:
    """The app's sidebar page ("/a1b2c3d4_household_docs"), or None (no sidebar page, or not known)."""
    if info is None:
        info = fetch_self_info()
    panel = None
    if isinstance(info, dict) and isinstance(info.get("slug"), str):
        slug = info["slug"]
        if info.get("ingress_panel") is not False and PANEL_RE.match("/" + slug):
            panel = "/" + slug
    elif info is None:
        host = (hostname if hostname is not None else os.environ.get("HOSTNAME", "")).strip()
        slug = host.replace("-", "_")
        if PANEL_SLUG_RE.match(slug):
            panel = "/" + slug
    _panel.update(value=panel, known=True)
    return panel


def panel() -> str | None:
    return _panel["value"]


# ---------------------------------------------------------------- life cycle
def start(thread: bool = True, **options) -> None:
    """In the lifespan (off without a Supervisor token, e.g. in the tests). `options` go to app_bus.start
    (tests: api_base, ws_url, token)."""
    if bus.default.started:
        return
    try:
        learn_panel()
    except Exception:
        logger.exception("Learning this app's page path failed")
    opts = dict(api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)
    opts.update(options)
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, can=[], wants=WANTS, db=db.get_conn,
              outbox_thread=False, **opts)
    _stop.clear()
    if thread:
        t = threading.Thread(target=_loop, name="docs-bus-pump", daemon=True)
        _worker["thread"] = t
        t.start()


def stop() -> None:
    _stop.set()
    _wake.set()
    t = _worker.get("thread")
    if t is not None and t is not threading.current_thread():
        t.join(5)
    _worker["thread"] = None
    bus.stop()


def run_outbox() -> None:
    """From housekeeping: re-sends, expiry, pruning and the six-hourly hello; then what answers left to do."""
    if bus.default.started:
        bus.run_outbox_once()
        pump()


def _flush() -> None:
    """Send what was just queued now (a dialog is waiting for the answer)."""
    try:
        bus.run_outbox_once()
    except Exception:
        logger.exception("Sending a message failed (the outbox tries again)")


def _loop() -> None:
    """The answers' follow-ups. A reply callback wakes it from inside the bus's transaction — possibly before that
    commits — so while a send is still under way it looks again every half second."""
    busy = False
    while not _stop.is_set():
        _wake.wait(0.5 if busy else 30)
        _wake.clear()
        if _stop.is_set():
            break
        try:
            busy = bool(pump().get("busy"))
        except Exception:
            busy = False
            logger.exception("Finishing what an answer asked for failed")


def connected_apps() -> dict:
    """Admin → Connected apps (APP_MESSAGES_SPEC §5)."""
    on = bool(bus.default.started and bus.default.token)
    return {"on": on, "connected": bus.default.connected, "apps": bus.apps() if bus.default.started else []}


def status() -> dict:
    """For /api/me: which connections are offered now, and the page path."""
    started = bus.default.started
    return {"chat": bool(started and bus.available(CHAT, "chat.chats.list") and bus.available(CHAT, "chat.card")),
            "todo": bool(started and bus.available(TODO, "todo.lists.list") and bus.available(TODO, "todo.items.add")),
            "panel": panel()}


# ---------------------------------------------------------------- requests (the page polls them)
def _now() -> str:
    return config.now_iso()


def new_request(conn, user: dict, kind: str, node_id: str | None, data: dict | None = None) -> tuple[str, str]:
    rid = db.new_id()
    ref = f"{kind}:{rid}"
    conn.execute("INSERT INTO bus_requests (id, user_id, node_id, kind, ref, state, data, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                 (rid, user["id"], node_id, kind, ref, json.dumps(data or {}), _now(), _now()))
    return rid, ref


def _finish(conn, rid: str, *, result: dict | None = None, error: str | None = None, data: dict | None = None) -> None:
    sets, params = ["state = ?", "updated_at = ?"], ["failed" if error else "done", _now()]
    if result is not None:
        sets.append("result = ?")
        params.append(json.dumps(result))
    if error is not None:
        sets.append("error = ?")
        params.append(error[:400])
    if data is not None:
        sets.append("data = ?")
        params.append(json.dumps(data))
    conn.execute(f"UPDATE bus_requests SET {', '.join(sets)} WHERE id = ?", (*params, rid))


def get_request(conn, user: dict, rid: str):
    r = conn.execute("SELECT * FROM bus_requests WHERE id = ? AND user_id = ?", (rid[:64], user["id"])).fetchone()
    if r is None:
        raise HTTPException(404, "That request isn't there.")
    return r


def send(to: str, kind: str, data: dict, ref: str, *, expires_in=timedelta(hours=24)) -> str:
    """Queue and send at once. HTTPException 409 when the app isn't there (or the message can't go)."""
    if not bus.default.started:
        raise HTTPException(409, f"{APP_NAMES[to]} can't be reached: app messages work only when Household Docs runs "
                                 "inside Home Assistant.")
    try:
        mid = bus.send(to, kind, data, ref=ref, expires_in=expires_in)
    except bus.NotAvailable:
        raise HTTPException(409, f"{APP_NAMES[to]} isn't available — it isn't installed, or it hasn't said hello in "
                                 "the last day (is it running?).")
    except bus.TooLarge:
        raise HTTPException(413, "That's too much to send in one message.")
    _flush()
    return mid


APP_NAMES = {CHAT: "Household Chat", TODO: "Household Todo"}


def nack_text(app: str, reason: str | None, detail: str | None) -> str:
    """A nack in plain words (§6 there: reasons and details)."""
    name = APP_NAMES.get(app, "The other app")
    if reason == "not_allowed" and detail == "no_access":
        return (f"Open {name} once first (or ask an admin to turn it on for you) — it doesn't know you yet."
                if app == TODO else f"{name} says you can't use it — open it once first, or ask an admin to turn it "
                                    "on for you.")
    if reason == "not_allowed" and detail == "read_only":
        return "You can't post in that chat right now."
    if reason == "not_found" and detail == "chat":
        return "That chat isn't there any more, or you're no longer in it."
    if reason == "not_found" and detail == "list":
        return "That Todo list isn't there any more (or it's someone else's personal list)."
    if reason == "invalid" and detail == "too_many_subitems":
        return "A task in Todo can have at most 100 items under it."
    if reason == "invalid":
        return f"{name} refused it ({detail or 'something it couldn’t read'})."
    if reason in ("unsupported_kind", "unsupported_version"):
        return f"This {name} is too old for that — update it."
    if reason == "expired":
        return f"{name} didn't answer in time — is it running?"
    if reason == "not_allowed":
        return f"{name} didn't allow it."
    if reason == "not_found":
        return f"{name} couldn't find it."
    return f"{name} said no ({reason or 'no reason given'})."


def _answer_parts(msg) -> tuple[str, str | None, str | None]:
    """(kind of answer, nack reason, nack detail)."""
    if msg.kind == "nack":
        return "nack", msg.data.get("reason"), msg.data.get("detail")
    return msg.kind, None, None


def _request_of(conn, msg, prefix: str):
    ref = msg.ref or ""
    if not ref.startswith(prefix + ":"):
        return None
    return conn.execute("SELECT * FROM bus_requests WHERE ref = ?", (ref,)).fetchone()


# ---------------------------------------------------------------- answers (inside the bus's transaction)
def _clean_chat(c) -> dict | None:
    if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not ID_RE.match(c["id"]):
        return None
    return {"id": c["id"], "kind": c.get("kind") if c.get("kind") in ("group", "direct", "personal") else "group",
            "name": str(c.get("name") or "")[:120], "icon": str(c.get("icon") or "")[:8] or None,
            "household": bool(c.get("household")),
            "members": c.get("members") if isinstance(c.get("members"), int) and not isinstance(c.get("members"), bool) else 0}


def on_chats(msg, conn) -> None:
    """The chats the person may post in (no member ids: they aren't asked for, and any that come are dropped)."""
    r = _request_of(conn, msg, "chats")
    if r is None or r["state"] != "pending":
        return
    kind, reason, detail = _answer_parts(msg)
    if kind == "ack":
        res = msg.result if isinstance(msg.result, dict) else {}
        chats = res.get("chats") if isinstance(res.get("chats"), list) else []
        clean = [c for c in (_clean_chat(x) for x in chats[:50]) if c is not None]
        _finish(conn, r["id"], result={"chats": clean, "more": bool(res.get("more"))})
    elif kind == "nack":
        _finish(conn, r["id"], error=nack_text(CHAT, reason, detail))


def on_members(msg, conn) -> None:
    """The members of the one chat the person picked (for *Also give the chat's members access*). Kept with the
    request; only an answer naming exactly that chat counts."""
    r = _request_of(conn, msg, "members")
    if r is None or r["state"] != "pending":
        return
    kind, reason, detail = _answer_parts(msg)
    want = json.loads(r["data"] or "{}").get("chatId")
    if kind == "ack":
        res = msg.result if isinstance(msg.result, dict) else {}
        chats = res.get("chats") if isinstance(res.get("chats"), list) else []
        hit = [c for c in chats if isinstance(c, dict) and c.get("id") == want]
        if len(chats) != 1 or not hit:
            _finish(conn, r["id"], error="Household Chat didn't say who is in that chat.")
            return
        ids = hit[0].get("member_ids")
        if not isinstance(ids, list):
            _finish(conn, r["id"], error="That chat has too many members to give them access from here.")
            return
        c = _clean_chat(hit[0])
        c["chatId"] = c["id"]
        c["memberIds"] = list(dict.fromkeys(x for x in ids if isinstance(x, str) and 0 < len(x) <= 128))[:200]
        _finish(conn, r["id"], result=c)
    elif kind == "nack":
        _finish(conn, r["id"], error=nack_text(CHAT, reason, detail))


def on_card(msg, conn) -> None:
    r = _request_of(conn, msg, "card")
    if r is None or r["state"] != "pending":
        return
    kind, reason, detail = _answer_parts(msg)
    data = json.loads(r["data"] or "{}")
    if kind == "nack":
        _finish(conn, r["id"], error=nack_text(CHAT, reason, detail))
        return
    if kind != "ack":
        return
    res = msg.result if isinstance(msg.result, dict) else {}
    out = {"chatId": res.get("chat_id"), "messageId": res.get("message_id"), "shared": 0, "notShared": []}
    role = data.get("share")
    if role in ("viewer", "editor"):
        if res.get("chat_id") != data.get("chatId"):
            out["notShared"] = ["everyone (Chat's answer was about another chat)"]
        else:
            # Only the people the sender confirmed in the dialog (from that chat's members, asked before) — an
            # answer can narrow that (someone left the chat), never widen it.
            confirmed = [x for x in data.get("members") or [] if isinstance(x, str)]
            ids = res.get("member_ids")
            if isinstance(ids, list):
                now_in = {x for x in ids if isinstance(x, str)}
                confirmed = [x for x in confirmed if x in now_in]
            added, refused, notify = _share_with_members(conn, r, role, confirmed[:200])
            out.update(shared=added, notShared=refused)
            data["notify"] = notify
    _finish(conn, r["id"], result=out, data=data)
    _wake.set()


def _share_with_members(conn, req, role: str, member_ids: list[str]) -> tuple[int, list[str], list[str]]:
    """Normal Docs shares (§6.3) for the chat's members who are Docs users — only after Chat's ack, and only while
    the person who sent the card may still share the item (owner or manager). → (added, names not shared,
    people to tell)."""
    from fastapi import HTTPException as E
    from . import auth, sharing
    from .store import nodes
    urow = conn.execute("SELECT * FROM users WHERE id = ?", (req["user_id"],)).fetchone()
    node = nodes.get(conn, req["node_id"])
    if urow is None or node is None:
        return 0, ["everyone (the item isn't there any more)"], []
    actor = auth.user_dict(urow)
    actor_role = sharing.role_of(conn, actor, node)
    if actor["is_child"] or not sharing.at_least(actor_role, "manager") or sharing.owner_id(conn, node) is None:
        return 0, ["everyone (you can no longer share it)"], []
    added, refused, notify = 0, [], []
    owner = sharing.owner_id(conn, node)
    for uid in dict.fromkeys(member_ids):
        if uid == actor["id"] or uid == owner:
            continue
        u = sharing.user_row(conn, uid)
        if u is None:
            continue                                   # not a Docs user: nothing to share with
        if sharing.at_least(sharing.role_of(conn, auth.user_dict(u), node), role):
            continue                                   # can already open it as asked
        try:
            if sharing.set_share(conn, actor, actor_role, node, uid, role, None):
                notify.append(uid)
            added += 1
        except E as e:
            refused.append(f"{u['name']} ({e.detail})" if isinstance(e.detail, str) else u["name"])
    return added, refused, notify


def on_lists(msg, conn) -> None:
    r = _request_of(conn, msg, "lists")
    if r is None or r["state"] != "pending":
        return
    kind, reason, detail = _answer_parts(msg)
    if kind == "ack":
        res = msg.result if isinstance(msg.result, dict) else {}
        lists = []
        for x in (res.get("lists") if isinstance(res.get("lists"), list) else [])[:200]:
            if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not 0 < len(x["id"]) <= 64:
                continue
            lists.append({"id": x["id"], "name": str(x.get("name") or "")[:80],
                          "kind": "personal" if x.get("kind") == "personal" else "shared",
                          "open": x.get("open") if isinstance(x.get("open"), int) else None,
                          "maintenance": bool(x.get("maintenance"))})
        _finish(conn, r["id"], result={"lists": lists, "more": bool(res.get("more"))})
    elif kind == "nack":
        _finish(conn, r["id"], error=nack_text(TODO, reason, detail))


def on_todo(msg, conn) -> None:
    """An answer to one part of a checklist → Todo send ("todo:<send id>:<part>")."""
    parts_ref = (msg.ref or "").split(":")
    if len(parts_ref) != 3:
        return
    sid, n = parts_ref[1], parts_ref[2]
    s = conn.execute("SELECT * FROM todo_sends WHERE id = ?", (sid,)).fetchone()
    if s is None or not n.isdigit():
        return
    n = int(n)
    parts = json.loads(s["parts"])
    if n >= len(parts) or parts[n]["state"] != "sent":
        return
    kind, reason, detail = _answer_parts(msg)
    if kind == "nack":
        parts[n]["state"] = "failed"
        for p in parts:                                   # nothing more goes: the texts aren't needed
            p.pop("items", None)
        error = nack_text(TODO, reason, detail)
        if s["items_sent"]:
            error = f"Sent {s['items_sent']} items, then: {error}"
        conn.execute("UPDATE todo_sends SET parts = ?, state = 'failed', error = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(parts), error, _now(), sid))
        if s["request_id"]:
            _finish(conn, s["request_id"], error=error, result=_send_result(conn, sid))
        if any(p.get("remove") for p in parts):
            _wake.set()                               # parts acked before this one still leave the checklist
        return
    if kind != "ack":
        return
    res = msg.result if isinstance(msg.result, dict) else {}
    parts[n]["state"] = "acked"
    parts[n].pop("items", None)                       # the texts aren't needed any more
    list_id = res.get("list_id") if isinstance(res.get("list_id"), str) else s["list_id"]
    list_name = str(res.get("list_name") or s["list_name"] or "")[:80]
    created = 1 if res.get("created") is True else s["created_list"]
    if s["move"]:
        parts[n]["remove"] = True                     # pump() takes them out of the checklist
    more = any(p["state"] == "waiting" for p in parts)
    sent = s["items_sent"] + int(parts[n]["n"])
    state = "sending" if more or any(p.get("remove") for p in parts) else "done"
    conn.execute("UPDATE todo_sends SET parts = ?, list_id = ?, list_name = ?, created_list = ?, items_sent = ?, "
                 "state = ?, updated_at = ? WHERE id = ?", (json.dumps(parts), list_id, list_name, created, sent, state,
                                                            _now(), sid))
    if state == "done" and s["request_id"]:
        _finish(conn, s["request_id"], result=_send_result(conn, sid))
    _wake.set()


def on_answer(msg, conn) -> None:
    """Every answer goes through here: route by ref."""
    ref = msg.ref or ""
    if ref.startswith("chats:"):
        on_chats(msg, conn)
    elif ref.startswith("members:"):
        on_members(msg, conn)
    elif ref.startswith("card:"):
        on_card(msg, conn)
    elif ref.startswith("lists:"):
        on_lists(msg, conn)
    elif ref.startswith("todo:"):
        on_todo(msg, conn)


for _prefix in ("chats:", "members:", "card:", "lists:", "todo:"):
    bus.on_reply(_prefix, on_answer)


# ---------------------------------------------------------------- after the answers
def pump() -> dict:
    """What answers asked for, outside the bus's transaction: tell people about new shares, take moved items out
    of the checklist, send the next part of a long checklist. Safe to run any time (and twice)."""
    stats = {"notified": 0, "removed": 0, "sent": 0, "busy": 0}
    with db.get_conn() as conn:
        reqs = conn.execute("SELECT * FROM bus_requests WHERE kind = 'card' AND state = 'done' AND data LIKE '%\"notify\": [\"%'"
                            ).fetchall()
    for r in reqs:
        stats["notified"] += _notify_shared(r)
    with db.get_conn() as conn:
        # a send that failed later (a part not answered in time) still takes out the parts Todo did take
        sends = conn.execute("SELECT * FROM todo_sends WHERE state = 'sending' "
                             "OR (state = 'failed' AND parts LIKE '%\"remove\": true%')").fetchall()
    for s in sends:
        parts = json.loads(s["parts"])
        if any(p.get("remove") for p in parts):
            stats["removed"] += _remove_moved(s["id"])
        with db.get_conn() as conn:
            s = conn.execute("SELECT * FROM todo_sends WHERE id = ?", (s["id"],)).fetchone()
        parts = json.loads(s["parts"])
        if s["state"] == "sending" and not any(p["state"] == "sent" for p in parts) \
                and any(p["state"] == "waiting" for p in parts):
            stats["sent"] += send_next(s["id"])
        elif s["state"] == "sending" and all(p["state"] in ("acked",) and not p.get("remove") for p in parts):
            with db.get_conn() as conn:
                conn.execute("UPDATE todo_sends SET state = 'done', updated_at = ? WHERE id = ?", (_now(), s["id"]))
                if s["request_id"]:
                    _finish(conn, s["request_id"], result=_send_result(conn, s["id"]))
    with db.get_conn() as conn:
        # an answer recorded just now may still be committing: look again shortly (not for a whole day of waiting)
        stats["busy"] = conn.execute("SELECT COUNT(*) FROM todo_sends WHERE (state = 'sending' OR (state = 'failed' "
                                     "AND parts LIKE '%\"remove\": true%')) AND updated_at >= ?",
                                     (config.ago_iso(seconds=15),)).fetchone()[0]
        stats["busy"] += conn.execute("SELECT COUNT(*) FROM bus_requests WHERE kind = 'card' AND state = 'pending' "
                                      "AND created_at >= ?", (config.ago_iso(seconds=60),)).fetchone()[0]
    return stats


def _notify_shared(r) -> int:
    from . import notify
    data = json.loads(r["data"] or "{}")
    who = data.pop("notify", [])
    with db.get_conn() as conn:
        cur = conn.execute("SELECT data FROM bus_requests WHERE id = ?", (r["id"],)).fetchone()
        if cur is None or '"notify": ["' not in (cur["data"] or ""):
            return 0
        conn.execute("UPDATE bus_requests SET data = ? WHERE id = ?", (json.dumps(data), r["id"]))
        actor = conn.execute("SELECT name FROM users WHERE id = ?", (r["user_id"],)).fetchone()
        node = conn.execute("SELECT name FROM nodes WHERE id = ?", (r["node_id"],)).fetchone()
    n = 0
    for uid in who:
        try:
            n += bool(notify.send_shared(uid, actor["name"] if actor else "Someone", node["name"] if node else "a document"))
        except Exception:
            logger.exception("Telling someone about a share failed")
    return n


def _remove_moved(sid: str) -> int:
    """Move (§17.16): the items Todo acked leave the Docs checklist, as the person who sent them."""
    from . import auth, documents
    with db.get_conn() as conn:
        s = conn.execute("SELECT * FROM todo_sends WHERE id = ?", (sid,)).fetchone()
        parts = json.loads(s["parts"])
        keys = [k for p in parts if p.get("remove") for k in p.get("keys", [])]
        urow = conn.execute("SELECT * FROM users WHERE id = ?", (s["user_id"],)).fetchone()
    removed, error = 0, None
    if keys and urow is not None:
        order = {k: i for i, k in enumerate(json.loads(s["target"]).get("order", []))}
        ops = [{"op": "delete", "key": k} for k in sorted(keys, key=lambda k: -order.get(k, 0))]
        try:
            for i in range(0, len(ops), 200):
                with db.get_conn() as conn:
                    _out, conflicts = documents.checklist_ops(conn, auth.user_dict(urow), s["node_id"], ops[i:i + 200])
                removed += len(ops[i:i + 200]) - len(conflicts)
        except HTTPException as e:
            error = f"Sent, but the items stay in Docs: {e.detail if isinstance(e.detail, str) else 'not allowed'}"
        except Exception:
            logger.exception("Taking moved items out of a checklist failed")
            error = "Sent, but taking the items out of the checklist failed — they stay in Docs."
    with db.get_conn() as conn:
        s = conn.execute("SELECT * FROM todo_sends WHERE id = ?", (sid,)).fetchone()
        parts = json.loads(s["parts"])
        for p in parts:
            if p.get("remove"):
                p.pop("remove", None)
                p.pop("keys", None)
        more = any(p["state"] in ("waiting", "sent") for p in parts)
        state = "failed" if s["state"] == "failed" else ("sending" if more else "done")
        conn.execute("UPDATE todo_sends SET parts = ?, items_removed = items_removed + ?, error = COALESCE(?, error), "
                     "state = ?, updated_at = ? WHERE id = ?", (json.dumps(parts), removed, error, state, _now(), sid))
        if state == "done" and s["request_id"]:
            _finish(conn, s["request_id"], result=_send_result(conn, sid))
    return removed


def send_next(sid: str) -> int:
    """Send the next part of a checklist → Todo send (the first with `new` or the chosen list, the rest with the
    list id from the first answer), one at a time: only when no part is waiting for its answer."""
    with _send_lock:
        with db.get_conn() as conn:
            s = conn.execute("SELECT * FROM todo_sends WHERE id = ?", (sid,)).fetchone()
            if s is None or s["state"] != "sending":
                return 0
            parts = json.loads(s["parts"])
            if any(p["state"] == "sent" for p in parts):
                return 0
            n = next((i for i, p in enumerate(parts) if p["state"] == "waiting"), None)
            if n is None:
                return 0
            data = {"requested_by": s["user_id"], "items": parts[n]["items"], "source": BADGE}
            target = json.loads(s["target"])
            if s["list_id"]:
                data["list_id"] = s["list_id"]
            elif target.get("listId"):
                data["list_id"] = target["listId"]
            else:
                data["new"] = target["new"]
            parts[n]["state"] = "sent"
            conn.execute("UPDATE todo_sends SET parts = ?, updated_at = ? WHERE id = ?", (json.dumps(parts), _now(), sid))
        try:
            send(TODO, "todo.items.add", data, f"todo:{sid}:{n}")
        except HTTPException as e:
            msg = e.detail if isinstance(e.detail, str) else "Household Todo can't be reached."
            with db.get_conn() as conn:
                for p in parts:
                    p.pop("items", None)
                parts[n]["state"] = "failed"
                conn.execute("UPDATE todo_sends SET parts = ?, state = 'failed', error = ?, updated_at = ? WHERE id = ?",
                             (json.dumps(parts), msg, _now(), sid))
                if s["request_id"]:
                    _finish(conn, s["request_id"], error=msg, result=_send_result(conn, sid))
            return 0
        return 1


def _send_result(conn, sid: str) -> dict:
    s = conn.execute("SELECT * FROM todo_sends WHERE id = ?", (sid,)).fetchone()
    return {"sendId": sid, "listId": s["list_id"], "listName": s["list_name"], "created": bool(s["created_list"]),
            "sent": s["items_sent"], "total": s["items_total"], "removed": s["items_removed"], "move": bool(s["move"])}


def prune(conn) -> int:
    """Hourly: requests older than 7 days (the page stopped asking long ago); sends of checklists that are gone."""
    n = conn.execute("DELETE FROM bus_requests WHERE created_at < ?", (config.ago_iso(days=7),)).rowcount
    n += conn.execute("DELETE FROM todo_sends WHERE node_id NOT IN (SELECT id FROM nodes)").rowcount
    return n


# ---------------------------------------------------------------- checklist → Todo: what to send
def todo_parts(items: list[dict]) -> list[list[dict]]:
    """Split [{text, done, level}] into messages: at most 200 items and MAX_DATA bytes each, a level-1 item always
    in the same message as its task. HTTPException 422 when a task has too much under it to send."""
    groups: list[list[dict]] = []
    for it in items:
        if it["level"] == 1 and groups:
            groups[-1].append(it)
        else:
            groups.append([dict(it, level=0)])
    for g in groups:
        if len(g) - 1 > TODO_MAX_SUBITEMS:
            raise HTTPException(422, f"“{g[0]['text'][:60]}” has more than {TODO_MAX_SUBITEMS} items under it — Todo "
                                     f"takes at most {TODO_MAX_SUBITEMS} under one task.")
    size = lambda x: len(json.dumps(x, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))   # noqa: E731
    parts: list[list[dict]] = []
    cur: list[dict] = []
    for g in groups:
        if size(g) > MAX_DATA - 400:
            raise HTTPException(422, f"“{g[0]['text'][:60]}” and the items under it are too long to send in one go — "
                                     "split them up first.")
        if cur and (len(cur) + len(g) > TODO_MAX_ITEMS or size(cur + g) > MAX_DATA - 400):
            parts.append(cur)
            cur = []
        cur = cur + g
    if cur:
        parts.append(cur)
    return parts


def clean_text(text: str) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= TODO_TEXT else t[:TODO_TEXT - 1].rstrip() + "…"


def todo_notes(conn, user: dict, node_id: str) -> list[dict]:
    """The note on a checklist: where its items went (the last three sends). A personal list's name only for the
    person who sent to it."""
    out = []
    for s in conn.execute("SELECT t.*, u.name AS who FROM todo_sends t LEFT JOIN users u ON u.id = t.user_id "
                          "WHERE t.node_id = ? ORDER BY t.created_at DESC LIMIT 3", (node_id,)):
        mine = s["user_id"] == user["id"]
        name = s["list_name"] if (mine or s["list_kind"] != "personal") else None
        out.append({"at": s["created_at"], "by": "You" if mine else (s["who"] or "Someone"), "mine": mine,
                    "listName": name, "items": s["items_sent"], "total": s["items_total"], "state": s["state"],
                    "move": bool(s["move"]), "removed": s["items_removed"],
                    "error": s["error"] if mine else None})
    return out
