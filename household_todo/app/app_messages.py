"""Messages from the other household apps (APP_MESSAGES_SPEC.md §6.4; SPEC §17).

Household Docs asks Todo for the lists a person may add to (`todo.lists.list`) and turns a checklist into
tasks (`todo.items.add`). Every request is checked against Todo's own rules exactly as if that person did it
in the app (`requested_by` is the actor; "acting as" never applies; who sent the message never matters),
inside the transaction that records the answer (app/common/app_bus.py).

The bus opens its own WebSocket to Home Assistant (Todo has no other); the outbox runs in the housekeeping
loop (every 60 s).
"""
import json
import logging

from . import config, db, taskview
from .common import app_bus as bus

logger = logging.getLogger("app_messages")

SLUG = "household_todo"
MAX_ANSWER = 7000          # bytes of the ack's result; the whole event must stay under 8 KB
MAX_ITEMS = 200            # per message
MAX_SUBITEMS = 100         # checklist items under one task (the app's own limit, routers/tasks.py)


# ---------- checks ----------
def _restoring() -> None:
    if not db._import_lock.acquire(blocking=False):            # a backup is being restored right now
        raise bus.Nack("busy", "restoring")
    db._import_lock.release()


def _actor(conn, data: dict) -> dict:
    uid = data.get("requested_by")
    if not isinstance(uid, str) or not uid or len(uid) > 64:
        raise bus.Nack("invalid", "requested_by")
    u = conn.execute("SELECT id, name, disabled FROM users WHERE id = ?", (uid,)).fetchone()
    if u is None or u["disabled"]:
        raise bus.Nack("not_allowed", "no_access")
    return {"id": u["id"], "name": u["name"]}


def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _source(conn, msg, data: dict) -> str:
    v = data.get("source")
    if v is not None:
        if not isinstance(v, str) or not 1 <= len(v.strip()) <= 20:
            raise bus.Nack("invalid", "source")
        return v.strip()
    r = conn.execute("SELECT name FROM bus_apps WHERE slug = ?", (msg.from_app,)).fetchone()
    name = (r["name"] if r and r["name"] else msg.from_app) or "another app"
    return (name[len("Household "):] if name.startswith("Household ") else name)[:20]


# ---------- handlers ----------
@bus.handler("todo.lists.list")
def on_lists_list(msg, conn):
    _restoring()
    user = _actor(conn, msg.data)
    rows = conn.execute(
        "SELECT l.*, (SELECT COUNT(*) FROM tasks t WHERE t.list_id = l.id AND t.completed = 0) AS open_count "
        "FROM lists l WHERE l.kind = 'shared' OR l.owner_user_id = ? "
        "ORDER BY (l.kind = 'personal'), l.position, l.created_at", (user["id"],)).fetchall()   # GET /api/lists
    out = []
    for r in rows:
        item = {"id": r["id"], "name": r["name"], "kind": r["kind"], "open": r["open_count"]}
        if r["role"] == "maintenance":
            item["maintenance"] = True
        out.append(item)
    result = {"lists": out, "more": False}
    while _size(result) > MAX_ANSWER and out:
        out.pop()
        result["more"] = True
    return result


@bus.handler("todo.items.add")
def on_items_add(msg, conn):
    _restoring()
    d = msg.data
    user = _actor(conn, d)
    list_id, new = d.get("list_id"), d.get("new")
    if (list_id is None) == (new is None):
        raise bus.Nack("invalid", "list_id")                   # exactly one of list_id / new
    items = d.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise bus.Nack("invalid", "items")
    clean = []
    for it in items:
        if not isinstance(it, dict):
            raise bus.Nack("invalid", "items")
        text = it.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise bus.Nack("invalid", "text")
        done = it.get("done", False)
        if not isinstance(done, bool):
            raise bus.Nack("invalid", "done")
        level = it.get("level", 0)
        if isinstance(level, bool) or level not in (0, 1):
            raise bus.Nack("invalid", "level")
        clean.append((text.strip(), done, level))
    source = _source(conn, msg, d)
    now = config.now_iso()
    created = False
    if list_id is not None:
        if not isinstance(list_id, str) or not 1 <= len(list_id) <= 64:
            raise bus.Nack("invalid", "list_id")
        lst = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
        if lst is None or (lst["kind"] == "personal" and lst["owner_user_id"] != user["id"]):
            raise bus.Nack("not_found", "list")              # someone else's private list looks like none
        lst = dict(lst)
    else:
        if not isinstance(new, dict):
            raise bus.Nack("invalid", "new")
        name = new.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise bus.Nack("invalid", "name")
        shared = new.get("shared", False)
        if not isinstance(shared, bool):
            raise bus.Nack("invalid", "shared")
        kind = "shared" if shared else "personal"
        owner = None if shared else user["id"]
        lid = db.new_id()
        conn.execute("INSERT INTO lists (id, name, kind, owner_user_id, created_at, position) VALUES (?, ?, ?, ?, ?, ?)",
                     (lid, name.strip(), kind, owner, now, taskview.next_list_position(conn, kind, owner)))
        lst = dict(conn.execute("SELECT * FROM lists WHERE id = ?", (lid,)).fetchone())
        created = True
    ids, tasks, subitems = [], 0, 0
    position = taskview.next_task_position(conn, lst["id"])
    task_id, sub_pos = None, 0
    for text, done, level in clean:
        if level == 1 and task_id is not None:
            if sub_pos >= MAX_SUBITEMS:
                raise bus.Nack("invalid", "too_many_subitems")
            iid = db.new_id()
            conn.execute("INSERT INTO task_items (id, task_id, text, done, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (iid, task_id, text, 1 if done else 0, sub_pos, now))
            sub_pos += 1
            subitems += 1
            ids.append(iid)
            continue
        task_id, sub_pos = db.new_id(), 0
        conn.execute(
            "INSERT INTO tasks (id, list_id, title, completed, completed_at, completed_by, created_by, created_at, position, "
            "source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, lst["id"], text, 1 if done else 0, now if done else None, user["id"] if done else None,
             user["id"], now, position, source))
        position += 1
        tasks += 1
        ids.append(task_id)
    logger.info("%s added %d tasks and %d checklist items to a list (from %s)", user["id"], tasks, subitems, msg.from_app)
    result = {"list_id": lst["id"], "list_name": lst["name"], "created": created, "tasks": tasks,
              "subitems": subitems, "ids": ids}
    if _size(result) > MAX_ANSWER:
        del result["ids"]
    return result


# ---------- life cycle ----------
def start() -> None:
    """In the lifespan. Off (nothing connects) without a Supervisor token, e.g. in the tests."""
    if bus.default.started:
        return
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, db=db.get_conn, outbox_thread=False,
              api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


def stop() -> None:
    bus.stop()


def run_outbox() -> None:
    """From the housekeeping loop: re-sends, expiry, pruning and the six-hourly hello."""
    if bus.default.started:
        bus.run_outbox_once()


def connected_apps() -> dict:
    """Admin → Connected apps (APP_MESSAGES_SPEC §5)."""
    on = bool(bus.default.started and bus.default.token)
    return {"on": on, "connected": bus.default.connected, "apps": bus.apps() if bus.default.started else []}
