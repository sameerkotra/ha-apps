"""Items shown in Home Assistant (SPEC §17.9): ⋯ → Show in Home Assistant on

- a **checklist** → `sensor.docs_<slug>_open`: its open items; attributes total, done and (unless *Hide item
  text*) the first 10 open items;
- a **sheet cell or range** → `sensor.docs_<slug>`: the cell's value (a range: the sum of its numbers), with a
  unit and `state_class: measurement` for numbers;
- a **folder** (or an admin shared folder's top) → `sensor.docs_<slug>_files`: how many files are in it (all
  levels), the newest file's name and time.

Published with POST /api/states through the shared `sensor_publisher` (app/common/sensor_publisher.py): a
changed item is posted on the next tick (🕑 Activity marks it — saves in the app and changes the index finds),
everything is re-posted every REPOST_SECONDS (Home Assistant forgets these states when it restarts), and a
sensor is removed (DELETE) when it is turned off, its item is deleted, the person who turned it on can no longer
open it (checked before every publish; they are told), or the `ha_sensors` App setting is off
(then every sensor is removed; turning it back on posts them again). Only owners and editors turn one on or
off; everyone who can see the item is told it is on the dashboard (`haSensor` on the item).

Blocking HTTP — `tick()` runs in a worker thread with no database connection open while it posts.
"""
import json
import logging
import re
import threading

from fastapi import HTTPException

from . import config, db, settings, sharing
from .common import sensor_publisher
from .store import nodes, roots

logger = logging.getLogger("ha_sensors")

REPOST_SECONDS = 300
TICK_SECONDS = 15
MAX_ITEMS = 10
MAX_SENSORS = 200
SUFFIX = {"checklist": "_open", "sheet": "", "folder": "_files"}

SENSORS = sensor_publisher.Publisher()
REFRESH = sensor_publisher.Refresh(REPOST_SECONDS)
_dirty: set = set()                  # node ids / "root:<id>" whose sensors need posting
_gone: set = set()                   # entity ids to remove from Home Assistant
_lock = threading.Lock()
_state = {"published_off": False, "count": None}


def reset() -> None:
    """Tests: forget what was posted and what is waiting."""
    SENSORS.forget()
    REFRESH.reset()
    with _lock:
        _dirty.clear()
        _gone.clear()
    _state.update(published_off=False, count=None)


# ---------------------------------------------------------------- ids
def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s[:40].strip("_")) or "item"


def _unique_entity(conn, base: str, suffix: str, keep: str | None = None) -> str:
    n = 1
    while True:
        eid = f"sensor.docs_{base}{'' if n == 1 else f'_{n}'}{suffix}"
        r = conn.execute("SELECT 1 FROM ha_sensors WHERE entity_id = ?", (eid,)).fetchone()
        if r is None or eid == keep:
            return eid
        n += 1


# ---------------------------------------------------------------- which items
def _target(conn, user: dict, ref: str, wanted: str):
    """(kind, node or None, root, role) of an item that can be a sensor."""
    if ref.startswith("root:"):
        root = roots.root_row(conn, ref[5:])
        role = sharing.root_role(conn, user, root) if root is not None and root["kind"] == "shared" else None
        if role is None:
            raise HTTPException(404, sharing.NOT_FOUND)
        if not sharing.at_least(role, wanted):
            raise HTTPException(403, "Only people who can change this folder can do this.")
        return "folder", None, root, role
    node, role = sharing.require(conn, user, ref, wanted)
    if node["kind"] not in ("checklist", "sheet", "folder"):
        raise HTTPException(422, "Checklists, sheets and folders can be shown in Home Assistant.")
    return node["kind"], node, roots.root_row(conn, node["root_id"]), role


def row_of(conn, ref: str):
    return conn.execute("SELECT * FROM ha_sensors WHERE node_id = ?", (ref,)).fetchone()


def entity_for(conn, ref: str | None) -> str | None:
    if not ref:
        return None
    r = conn.execute("SELECT entity_id FROM ha_sensors WHERE node_id = ?", (ref,)).fetchone()
    return r["entity_id"] if r else None


def for_ids(conn, ids) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT node_id, entity_id FROM ha_sensors WHERE node_id IN ({q})", chunk):
            out[r["node_id"]] = r["entity_id"]
    return out


def info(conn, user: dict, ref: str) -> dict:
    kind, node, root, role = _target(conn, user, ref, "viewer")
    r = row_of(conn, ref)
    s = None
    if r is not None:
        opts = _opts(r)
        who = conn.execute("SELECT name FROM users WHERE id = ?", (r["created_by"],)).fetchone()
        s = {"entityId": r["entity_id"], "kind": r["kind"], "cell": r["cell_ref"], "name": opts.get("name"),
             "unit": opts.get("unit"), "hideItems": bool(opts.get("hideItems")), "createdAt": r["created_at"],
             "createdByName": who["name"] if who else None}
    on = bool(settings.get("ha_sensors", conn))
    return {"sensor": s, "kind": kind, "enabled": on, "canChange": on and sharing.at_least(role, "editor"),
            "suggested": f"sensor.docs_{slug(_title(node, root))}{SUFFIX[kind]}"}


def _title(node, root) -> str:
    if node is None:
        return root["label"]
    name = node["name"]
    return name.rsplit(".", 1)[0] if node["kind"] != "folder" and "." in name else name


def _opts(r) -> dict:
    try:
        o = json.loads(r["options"] or "{}")
    except ValueError:
        o = {}
    return o if isinstance(o, dict) else {}


CELL_RE = re.compile(r"^(?:(?P<tab>[^!]{1,31})!)?(?P<a>\$?[A-Z]{1,3}\$?[1-9][0-9]{0,5})(?::(?P<b>\$?[A-Z]{1,3}\$?[1-9][0-9]{0,5}))?$")


def clean_cell(cell: str) -> str:
    cell = (cell or "").strip()
    m = CELL_RE.match(cell.replace("'", "") if cell.count("'") == 2 and cell.startswith("'") else cell)
    if not m:
        raise HTTPException(422, "Choose a cell like Sheet1!B4 (or a range like Sheet1!B2:B9).")
    tab = m.group("tab") or ""
    ref = m.group("a").replace("$", "") + (":" + m.group("b").replace("$", "") if m.group("b") else "")
    return f"{tab}!{ref}" if tab else ref


def turn_on(conn, user: dict, ref: str, body: dict) -> dict:
    if not settings.get("ha_sensors", conn):
        raise HTTPException(403, "An admin has turned off showing items in Home Assistant.")
    kind, node, root, _role = _target(conn, user, ref, "editor")
    name = (body.get("name") or "").strip()[:80] or _title(node, root)
    unit = (body.get("unit") or "").strip()[:20] or None
    hide = bool(body.get("hideItems"))
    cell = None
    if kind == "sheet":
        cell = clean_cell(body.get("cell") or "")
    cur = row_of(conn, ref)
    opts = {"name": name, "unit": unit, "hideItems": hide}
    if cur is None:
        n = conn.execute("SELECT COUNT(*) FROM ha_sensors").fetchone()[0]
        if n >= MAX_SENSORS:
            raise HTTPException(409, f"There are already {MAX_SENSORS} items in Home Assistant — turn some off first.")
        eid = _unique_entity(conn, slug(name), SUFFIX[kind])
        conn.execute("INSERT INTO ha_sensors (entity_id, kind, node_id, cell_ref, options, created_by, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (eid, kind, ref, cell, json.dumps(opts), user["id"], config.now_iso()))
        db.audit(conn, "ha_sensor_on", user["id"], node["id"] if node else None, root["id"])
    else:
        eid = cur["entity_id"]
        conn.execute("UPDATE ha_sensors SET cell_ref = ?, options = ? WHERE entity_id = ?", (cell, json.dumps(opts), eid))
    _state["count"] = None
    mark(ref)
    return info(conn, user, ref)


def turn_off(conn, user: dict, ref: str) -> dict:
    _kind, node, root, _role = _target(conn, user, ref, "editor")
    cur = row_of(conn, ref)
    if cur is not None:
        conn.execute("DELETE FROM ha_sensors WHERE entity_id = ?", (cur["entity_id"],))
        db.audit(conn, "ha_sensor_off", user["id"], node["id"] if node else None, root["id"])
        with _lock:
            _gone.add(cur["entity_id"])
        _state["count"] = None
    return info(conn, user, ref)


# ---------------------------------------------------------------- changes
def _count(conn) -> int:
    if _state["count"] is None:
        _state["count"] = conn.execute("SELECT COUNT(*) FROM ha_sensors").fetchone()[0]
    return _state["count"]


def mark(ref: str) -> None:
    with _lock:
        _dirty.add(ref)


def on_activity(conn, action, node, _actor_id) -> None:
    """🕑 Activity hook: the item, the folders above it and its admin shared folder may be sensors."""
    if not _count(conn):
        return
    refs = [node["id"], "root:" + node["root_id"]] + [a["id"] for a in nodes.ancestors(conn, node)]
    if action == "deleted":                       # its own sensor and any inside it go
        ids = [node["id"]] + nodes.descendant_ids(conn, node)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT entity_id FROM ha_sensors WHERE node_id IN ({q})", chunk).fetchall():
                conn.execute("DELETE FROM ha_sensors WHERE entity_id = ?", (r["entity_id"],))
                with _lock:
                    _gone.add(r["entity_id"])
        _state["count"] = None
    with _lock:
        _dirty.update(refs)


# ---------------------------------------------------------------- states
def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def _fmt(v):
    if isinstance(v, float):
        return str(round(v, 6)).rstrip("0").rstrip(".") if "." in str(round(v, 6)) else str(v)
    return str(v)


def state_of(conn, r) -> tuple | None:
    """(state, attributes) of one sensor, or None when its item is gone (the sensor is then removed)."""
    opts = _opts(r)
    ref = r["node_id"]
    name = opts.get("name") or "Item"
    attrs = {"friendly_name": name, "source": config.APP_TITLE}
    if ref.startswith("root:"):
        root = roots.root_row(conn, ref[5:])
        if root is None:
            return None
        where, params = "n.root_id = ?", [root["id"]]
    else:
        node = nodes.get(conn, ref)
        if node is None:
            return None
        if r["kind"] == "folder":
            where = "n.root_id = ? AND substr(n.rel, 1, ?) = ?"
            params = [node["root_id"], len(node["rel"]) + 1, node["rel"] + "/"]
    if r["kind"] == "folder":
        cnt = conn.execute(f"SELECT COUNT(*) FROM nodes n WHERE {where} AND n.kind != 'folder' AND {nodes.LIVE}",
                           params).fetchone()[0]
        newest = conn.execute(f"SELECT n.name, n.mtime FROM nodes n WHERE {where} AND n.kind != 'folder' AND {nodes.LIVE} "
                              "ORDER BY n.mtime DESC LIMIT 1", params).fetchone()
        attrs.update({"unit_of_measurement": "files", "state_class": "measurement", "icon": "mdi:folder-outline",
                      "newest_file": newest["name"] if newest else None,
                      "newest_modified": newest["mtime"] if newest else None})
        return str(cnt), attrs
    from . import documents
    if r["kind"] == "checklist":
        from .formats import checklist_md, text as text_fmt
        _root, _real, data, _e, _st, _sha = documents._read(conn, node)
        try:
            text, _m = text_fmt.decode(data)
            items = checklist_md.parse(text)
        except Exception:
            items = []
        open_ = [it for it in items if not it.done]
        attrs.update({"unit_of_measurement": "items", "state_class": "measurement",
                      "icon": "mdi:checkbox-marked-outline", "total": len(items), "done": len(items) - len(open_)})
        if not opts.get("hideItems"):
            attrs["open_items"] = [it.text[:100] for it in open_[:MAX_ITEMS]]
        return str(len(open_)), attrs
    # a sheet cell or range
    from . import sheets
    _root, _real, data, _e, _st, _sha = documents._read(conn, node)
    p = sheets.parse(data, (node["ext"] or "xlsx").lower())
    value = cell_value(p["sheet"], r["cell_ref"] or "")
    attrs["icon"] = "mdi:table"
    attrs["cell"] = r["cell_ref"]
    n = _num(value)
    if n is not None:
        attrs["state_class"] = "measurement"
        if opts.get("unit"):
            attrs["unit_of_measurement"] = opts["unit"]
        return _fmt(n), attrs
    if value is None or value == "":
        return "unknown", attrs
    return str(value)[:255], attrs


def _addr(ref: str) -> tuple[int, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    col = 0
    for ch in m.group(1):
        col = col * 26 + ord(ch) - 64
    return col, int(m.group(2))


def _letters(col: int) -> str:
    s = ""
    while col:
        col, rem = divmod(col - 1, 26)
        s = chr(65 + rem) + s
    return s


def _shown(c: dict):
    v = c.get("v")
    if isinstance(v, str) and v.startswith("=") and not c.get("q"):
        return c.get("c")
    return v


def cell_value(sheet: dict, cell_ref: str):
    """What a cell shows (a formula: its computed value); a range: the sum of its numbers."""
    tab_name, _sep, ref = cell_ref.rpartition("!")
    grids = [t for t in sheet.get("tabs", []) if t.get("kind") == "grid"]
    tab = next((t for t in grids if t.get("name") == tab_name), None) if tab_name else (grids[0] if grids else None)
    if tab is None:
        return None
    cells = tab.get("cells") or {}
    if ":" not in ref:
        return _shown(cells.get(ref) or {})
    a, b = ref.split(":")
    (c1, r1), (c2, r2) = _addr(a), _addr(b)
    total, any_ = 0.0, False
    for c in range(min(c1, c2), max(c1, c2) + 1):
        for rr in range(min(r1, r2), max(r1, r2) + 1):
            v = _num(_shown(cells.get(f"{_letters(c)}{rr}") or {}))
            if v is not None:
                total += v
                any_ = True
    if not any_:
        return None
    return int(total) if total == int(total) else total


# ---------------------------------------------------------------- publishing
def creator_may(conn, r) -> bool:
    """Is the sensor's maker still allowed to see its item (turned on, and able to open it)? Checked before
    every publish, so a sensor never keeps showing a checklist's items or a folder's newest file to Home
    Assistant after its maker lost access (security review)."""
    from . import auth
    u = conn.execute("SELECT * FROM users WHERE id = ?", (r["created_by"],)).fetchone() if r["created_by"] else None
    if u is None or u["disabled"]:
        return False
    user = auth.user_dict(u)
    ref = r["node_id"]
    if ref.startswith("root:"):
        root = roots.root_row(conn, ref[5:])
        return root is not None and sharing.root_role(conn, user, root) is not None
    node = nodes.get(conn, ref)
    if node is None:
        return True                                # gone: state_of removes it as before
    return sharing.role_of(conn, user, node) is not None


def _tell_lost(user_id: str | None, name: str) -> None:
    if not user_id:
        return
    try:
        from .common import ha_notify
        with db.get_conn() as conn:
            services = ha_notify.services_for({"id": user_id}, conn)
        if services:
            ha_notify.send_to_services(services, config.APP_TITLE,
                                       f"“{name}” isn't shown in Home Assistant any more: you can no longer open it.",
                                       {"url": config.INGRESS_URL, "clickAction": config.INGRESS_URL})
    except Exception:
        logger.exception("Telling someone their Home Assistant item was turned off failed")


def tick(force: bool = False) -> dict:
    """Post what changed (everything when the re-post is due, or with force); remove what went."""
    if db.RESTORING.is_set():
        return {}
    on = bool(settings.get("ha_sensors"))
    with _lock:
        gone = set(_gone)
        _gone.clear()
        dirty = set(_dirty)
        _dirty.clear()
    out = {"pushed": 0, "failed": 0, "removed": 0}
    if gone:
        out["removed"] += SENSORS.remove(sorted(gone))["removed"]
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM ha_sensors ORDER BY entity_id").fetchall()
    if not on:
        if not _state["published_off"]:
            out["removed"] += SENSORS.remove([r["entity_id"] for r in rows])["removed"]
            _state["published_off"] = True
            REFRESH.reset()
        return out
    _state["published_off"] = False
    full = force or REFRESH.due(config.now().date())
    items, missing, lost = [], [], []
    with db.get_conn() as conn:
        for r in rows:
            if not full and r["node_id"] not in dirty:
                continue
            if not creator_may(conn, r):            # the person who turned it on can't open it any more: off
                missing.append(r["entity_id"])
                conn.execute("DELETE FROM ha_sensors WHERE entity_id = ?", (r["entity_id"],))
                db.audit(conn, "ha_sensor_off_access_lost", None, None if r["node_id"].startswith("root:") else r["node_id"])
                lost.append((r["created_by"], _opts(r).get("name") or "Item"))
                continue
            try:
                st = state_of(conn, r)
            except HTTPException:
                st = ("unavailable", {"friendly_name": _opts(r).get("name") or "Item"})
            except Exception:
                logger.exception("Working out %s failed", r["entity_id"])
                continue
            if st is None:
                missing.append(r["entity_id"])
                conn.execute("DELETE FROM ha_sensors WHERE entity_id = ?", (r["entity_id"],))
                continue
            items.append((r["entity_id"], st[0], st[1]))
    if missing:
        _state["count"] = None
        out["removed"] += SENSORS.remove(missing)["removed"]
    for uid, name in lost:
        _tell_lost(uid, name)
    out["accessLost"] = len(lost)
    res = SENSORS.publish(items, force=full, stop_after=3)
    out["pushed"] += res["pushed"]
    out["failed"] += res["failed"]
    if full and not res["failed"]:
        REFRESH.done(config.now().date())
    return out


async def loop_tick() -> None:
    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(tick)
