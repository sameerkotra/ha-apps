"""🕑 Activity (SPEC §17.4): what changed — created, edited, renamed, moved, deleted, restored, shared with you —
in everything a person can open, by whom ("outside the app" when the index found it). Names only, never any
content.

- `record()` is called where the change happens (docops, documents, sheets, uploads, trash, sharing) and by the
  index for changes it finds on disk (actor NULL). The index doesn't report the first scan of a folder (that is
  everything already there) and reports at most INDEX_MAX_EVENTS per scan of a root.
- **Edits are grouped**: a save by the same person on the same item as its latest activity row, on the same
  day (Home Assistant's time zone), adds to that row — within GROUP_MINUTES it is the same editing session (only
  the time moves on), later it counts as one more edit ("Alex edited 'Budget' 4 times").
- **The feed** (`feed()`) joins the access CTE, so a row shows only while its item is something the person can
  open (a deleted item: while it is in a Trash they could restore from or a folder they can see — its row
  still has its place). "Shared" rows show only to the person it was shared with (Everyone: everyone who can
  open it). Rows older than `activity_days` are removed by housekeeping.
- `HOOKS`: functions f(conn, action, node, actor_id) run after every record (Home Assistant sensors refresh).
"""
import json
from datetime import timedelta

from . import config, db, settings, sharing
from .store import nodes

ACTIONS = ("created", "edited", "renamed", "moved", "deleted", "restored", "shared", "filed")   # filed: §17.18
GROUP_MINUTES = 10
INDEX_MAX_EVENTS = 100
PAGE = 50
HOOKS: list = []


def _local_day(iso: str):
    t = config.parse_iso(iso)
    return config.ZONE.now(t).date() if t else None


def record(conn, action: str, actor_id: str | None, node, *, detail: dict | None = None,
           target_user: str | None = None) -> None:
    """Write down one change (or add an edit to the person's last row on that item)."""
    if node is None or action not in ACTIONS:
        return
    if action in ("renamed", "filed") and detail is not None and "acl" not in detail:
        detail = dict(detail, acl=sharing.who_can_open(conn, node))   # who may see the old name (feed_detail)
    now = config.now_iso()
    if action == "edited":
        last = conn.execute("SELECT * FROM activity WHERE node_id = ? ORDER BY last_at DESC, rowid DESC LIMIT 1",
                            (node["id"],)).fetchone()
        if (last is not None and last["action"] == "edited" and last["actor_id"] == actor_id
                and _local_day(last["last_at"]) == _local_day(now)):
            gap = config.utcnow() - (config.parse_iso(last["last_at"]) or config.utcnow())
            if gap <= timedelta(minutes=GROUP_MINUTES):
                conn.execute("UPDATE activity SET last_at = ? WHERE id = ?", (now, last["id"]))
            else:
                conn.execute("UPDATE activity SET last_at = ?, count = count + 1 WHERE id = ?", (now, last["id"]))
            _after(conn, action, node, actor_id)
            return
    conn.execute("INSERT INTO activity (id, actor_id, node_id, root_id, parent_id, action, detail, target_user, count, "
                 "created_at, last_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                 (db.new_id(), actor_id, node["id"], node["root_id"], node["parent_id"], action,
                  json.dumps(detail, ensure_ascii=False) if detail else None, target_user, now, now))
    _after(conn, action, node, actor_id)


def _after(conn, action, node, actor_id) -> None:
    for fn in HOOKS:
        try:
            fn(conn, action, node, actor_id)
        except Exception:                  # a hook never breaks the change itself
            import logging
            logging.getLogger("activity").exception("An activity hook failed")


def record_id(conn, action: str, actor_id: str | None, node_id: str, **kw) -> None:
    record(conn, action, actor_id, nodes.get(conn, node_id, live=False), **kw)


def folder_name(conn, folder_id: str | None, root_id: str) -> str:
    """A folder's name for a move's from / to ("" = the top of the space)."""
    if folder_id:
        r = conn.execute("SELECT name FROM nodes WHERE id = ?", (folder_id,)).fetchone()
        return r["name"] if r else ""
    return ""


def feed_detail(conn, user: dict, action: str, detail, root_owner: str | None):
    """A row's detail as this person may see it: folder names (a move's from / to, a filing rule's folders) only
    for folders they can open, and an old name (renamed, filed) only if they could open the item when it was
    renamed. The item's owner sees everything. Never the stored access list itself."""
    if not isinstance(detail, dict):
        return detail
    d = dict(detail)
    acl = d.pop("acl", None)
    ids = {k: d.pop(k, None) for k in ("fromId", "toId", "fromFolderId", "toFolderId")}
    if root_owner is not None and root_owner == user["id"]:
        return d

    def folder_ok(key: str) -> bool:
        fid = ids.get(key)
        if fid == "":                        # the top of the space
            return True
        if not fid:
            return False
        f = nodes.get(conn, fid, live=False)
        return f is not None and sharing.role_of(conn, user, f) is not None

    if action == "moved":
        if not folder_ok("fromId"):
            d["from"] = None
        if not folder_ok("toId") and not d.get("transfer"):
            d["to"] = None
    if action in ("renamed", "filed") and not sharing.could_open(conn, user, acl):
        d["from"] = None
    if action == "filed":
        if not folder_ok("fromFolderId"):
            d["fromFolder"] = None
        if not folder_ok("toFolderId"):
            d["toFolder"] = None
    return d


def folder_id(folder_id: str | None) -> str:
    """How a move's detail names a folder for feed_detail: its id, "" for the top of the space."""
    return folder_id or ""


def purge(conn=None) -> int:
    """Rows past `activity_days`, and rows whose item has gone for good."""
    def run(c):
        days = int(settings.get("activity_days", c))
        n = c.execute("DELETE FROM activity WHERE last_at < ?", (config.ago_iso(days=days),)).rowcount
        n += c.execute("DELETE FROM activity WHERE node_id NOT IN (SELECT id FROM nodes)").rowcount
        return n
    if conn is not None:
        return run(conn)
    with db.get_conn() as c:
        return run(c)


# ---------------------------------------------------------------- the feed
TYPE_KINDS = {"note": ("note", "markdown"), "checklist": ("checklist",), "sheet": ("sheet",), "folder": ("folder",),
              "file": ("file",)}


def _subtree(conn, folder) -> list[str]:
    return [folder["id"]] + [i for i in nodes.descendant_ids(conn, folder)]


def feed(conn, user: dict, *, person: str | None = None, place: str | None = None, type_: str | None = None,
         before: str | None = None, limit: int = PAGE) -> dict:
    """The person's activity, newest first: rows they may see, grouped for reading."""
    from fastapi import HTTPException
    from . import docops
    days = int(settings.get("activity_days", conn))
    since = config.ago_iso(days=days)
    acc_sql, acc_params = sharing.access_cte(user["id"])
    where = ["a.last_at >= ?", "(a.action != 'shared' OR a.target_user IN (?, '*'))", "(r.kind = 'person' OR r.missing = 0)"]
    params: list = [since, user["id"]]
    if before:
        where.append("a.last_at < ?")
        params.append(before)
    if person:
        if person == "me":
            where.append("a.actor_id = ?")
            params.append(user["id"])
        elif person == "others":
            where.append("(a.actor_id IS NULL OR a.actor_id != ?)")
            params.append(user["id"])
        elif person == "outside":
            where.append("a.actor_id IS NULL")
        else:
            where.append("a.actor_id = ?")
            params.append(person[:128])
    if type_:
        kinds_ = TYPE_KINDS.get(type_)
        if kinds_ is None:
            raise HTTPException(422, "Type is note, checklist, sheet, folder or file.")
        where.append(f"n.kind IN ({','.join('?' * len(kinds_))})")
        params += list(kinds_)
    if place:
        if place == "mine":
            root = docops.my_root(conn, user)
            where.append("n.root_id = ?")
            params.append(root["id"])
        elif place == "shared":
            where.append("r.kind = 'person' AND r.user_id != ?")
            params.append(user["id"])
        elif place == "folders":
            where.append("r.kind = 'shared'")
        else:
            proot, pfolder, _role = docops.place(conn, user, place, "viewer")
            if pfolder is None:
                where.append("n.root_id = ?")
                params.append(proot["id"])
            else:
                ids = _subtree(conn, pfolder)
                where.append("(a.node_id IN (SELECT value FROM json_each(?)) OR a.parent_id IN (SELECT value FROM json_each(?)))")
                params += [json.dumps(ids), json.dumps(ids)]
    limit = max(1, min(int(limit or PAGE), 200))
    rows = conn.execute(
        acc_sql + "SELECT a.*, acc.rank AS acc_rank, n.name, n.kind, n.ext, n.trash_id, n.gone_at, n.parent_id AS cur_parent, n.rel, "
        "n.root_id AS cur_root, r.kind AS root_kind, r.label AS root_label, r.user_id AS root_owner, "
        "u.name AS actor_name, tu.name AS target_name "
        "FROM activity a JOIN nodes n ON n.id = a.node_id JOIN acc ON acc.node_id = a.node_id "
        "JOIN roots r ON r.id = n.root_id LEFT JOIN users u ON u.id = a.actor_id "
        "LEFT JOIN users tu ON tu.id = a.target_user "
        f"WHERE {' AND '.join(where)} ORDER BY a.last_at DESC, a.rowid DESC LIMIT ?",
        (*acc_params, *params, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    from .search.engine import location
    items = []
    for r in rows:
        loc = location(conn, _LocRow(r), user)
        detail = None
        if r["detail"]:
            try:
                detail = feed_detail(conn, user, r["action"], json.loads(r["detail"]), r["root_owner"])
            except ValueError:
                detail = None
        undo = None
        if r["action"] == "filed" and isinstance(detail, dict) and r["actor_id"] is not None:
            from . import filing
            if filing.undo_state(conn, detail.get("log")) and (r["acc_rank"] or 0) >= 2:
                undo = detail.get("log")
        items.append({
            "id": r["id"], "action": r["action"], "undo": undo, "actorId": r["actor_id"],
            "actorName": ("You" if r["actor_id"] == user["id"] else r["actor_name"]) if r["actor_id"] else None,
            "outside": r["actor_id"] is None, "count": r["count"], "at": r["last_at"], "firstAt": r["created_at"],
            "detail": detail, "sharedWith": ("you" if r["target_user"] == user["id"] else
                                              "Everyone" if r["target_user"] == "*" else r["target_name"]),
            "parentId": r["parent_id"],
            "item": {"id": r["node_id"], "name": r["name"], "kind": r["kind"], "ext": r["ext"],
                     "document": nodes.kind_info(r["kind"])["document"] if r["kind"] in _KNOWN else False,
                     "inTrash": bool(r["trash_id"]), "gone": bool(r["gone_at"]), "parentRef": r["cur_parent"]},
            "location": " › ".join(loc), "locationParts": loc})
    return {"items": group(items), "more": more, "next": rows[-1]["last_at"] if more and rows else None,
            "days": days}


_KNOWN = ("folder", "note", "markdown", "checklist", "sheet", "file")


class _LocRow(dict):
    """A feed row as engine.location expects it."""

    def __init__(self, r):
        super().__init__(trash_id=r["trash_id"], root_kind=r["root_kind"], root_label=r["root_label"],
                         root_owner=r["root_owner"], parent_id=r["cur_parent"])

    def __getitem__(self, k):
        return dict.get(self, k)


def group(items: list[dict]) -> list[dict]:
    """Adjacent rows of the same person adding (or deleting) things in the same folder within GROUP_MINUTES
    become one entry: "Alex added 12 files to Taxes"."""
    out: list[dict] = []
    for it in items:
        prev = out[-1] if out else None
        if (prev is not None and it["action"] in ("created", "deleted") and prev["action"] == it["action"]
                and prev["actorId"] == it["actorId"] and prev["parentId"] == it["parentId"]
                and _minutes_between(prev.get("firstAt") or prev["at"], it["at"]) <= GROUP_MINUTES):
            prev.setdefault("items", [prev["item"]]).append(it["item"])
            prev["firstAt"] = it["firstAt"]
            continue
        out.append(it)
    for it in out:
        if "items" in it:
            it["groupCount"] = len(it["items"])
    return out


def _minutes_between(a: str, b: str) -> float:
    ta, tb = config.parse_iso(a), config.parse_iso(b)
    if not ta or not tb:
        return 1e9
    return abs((ta - tb).total_seconds()) / 60
