"""Following (SPEC §17.4): a phone notification when something changes in a document, a folder or an admin
shared folder a person follows.

- **Batched**: `dispatch()` (housekeeping, every minute) looks at the 🕑 Activity rows newer than each follow's
  `cursor`. It sends at most one notification per followed item per BATCH_MINUTES, and only once the newest of
  those changes is SETTLE_SECONDS old (a burst of uploads or a typing session becomes one message: "3 new files
  in House papers › Taxes").
- **Quiet hours** per person (Settings: `quietFrom`–`quietTo`, default 22:00–07:00, Home Assistant's time zone):
  nothing is sent then; what happened is told in one message afterwards.
- **Never your own changes**, never anything you can't open (the access CTE is part of the query), never any
  content — names only. Turned off (`notifyFollows`), or no phone linked: the changes are passed over.
- Sending is blocking HTTP: it happens with no database connection open.
"""
import json
import logging
from datetime import timedelta

from fastapi import HTTPException

from . import activity, config, db, notify, sharing
from .common import ha_notify
from .store import nodes, roots

logger = logging.getLogger("follows")

BATCH_MINUTES = 15
SETTLE_SECONDS = 60
MAX_FOLLOWS = 200
DEFAULT_QUIET = ("22:00", "07:00")


# ---------------------------------------------------------------- following
def _target_row(conn, user: dict, target: str):
    """(kind, node or root) of a follow target the person can open; 404 otherwise."""
    if not isinstance(target, str) or not target or len(target) > 70:
        raise HTTPException(422, "Follow a document, a folder or a shared folder.")
    if target.startswith("root:"):
        root = roots.root_row(conn, target[5:])
        if root is None or root["kind"] != "shared" or sharing.root_role(conn, user, root) is None:
            raise HTTPException(404, sharing.NOT_FOUND)
        return "root", root
    node, _role = sharing.require(conn, user, target, "viewer")
    return "node", node


def follow(conn, user: dict, target: str) -> dict:
    _target_row(conn, user, target)
    n = conn.execute("SELECT COUNT(*) FROM follows WHERE user_id = ?", (user["id"],)).fetchone()[0]
    exists = conn.execute("SELECT 1 FROM follows WHERE user_id = ? AND target = ?", (user["id"], target)).fetchone()
    if not exists and n >= MAX_FOLLOWS:
        raise HTTPException(409, f"You can follow up to {MAX_FOLLOWS} things — unfollow some first.")
    now = config.now_iso()
    conn.execute("INSERT INTO follows (user_id, target, created_at, cursor) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id, target) DO NOTHING", (user["id"], target, now, now))
    return {"following": True}


def unfollow(conn, user: dict, target: str) -> dict:
    conn.execute("DELETE FROM follows WHERE user_id = ? AND target = ?", (user["id"], (target or "")[:70]))
    return {"following": False}


def targets(conn, user_id: str) -> list[str]:
    return [r["target"] for r in conn.execute("SELECT target FROM follows WHERE user_id = ?", (user_id,))]


def listing(conn, user: dict) -> list[dict]:
    """What the person follows (only what they can still open)."""
    out = []
    for f in conn.execute("SELECT * FROM follows WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)):
        try:
            kind, row = _target_row(conn, user, f["target"])
        except HTTPException:
            continue
        if kind == "root":
            out.append({"target": f["target"], "name": row["label"], "kind": "folder", "isRoot": True,
                        "since": f["created_at"]})
        else:
            out.append({"target": f["target"], "name": row["name"], "kind": row["kind"], "ext": row["ext"],
                        "since": f["created_at"], "parentRef": row["parent_id"]})
    return out


# ---------------------------------------------------------------- quiet hours
def _hm(text: str) -> int | None:
    try:
        h, m = str(text).split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def quiet_now(prefs: dict, local_now=None) -> bool:
    """Is it the person's quiet time (in Home Assistant's time zone)? From == To: no quiet hours."""
    start = _hm(prefs.get("quietFrom", DEFAULT_QUIET[0]))
    end = _hm(prefs.get("quietTo", DEFAULT_QUIET[1]))
    if start is None or end is None or start == end:
        return False
    t = local_now or config.now()
    m = t.hour * 60 + t.minute
    return start <= m < end if start < end else (m >= start or m < end)


# ---------------------------------------------------------------- what to say
def _ids_of(conn, kind: str, row) -> list[str] | None:
    if kind == "root":
        return None
    if row["kind"] == "folder":
        return [row["id"]] + nodes.descendant_ids(conn, row)
    return [row["id"]]


def _target_any(conn, user: dict, target: str):
    """Like _target_row, but a followed item that was deleted meanwhile still counts (its deletion is told)."""
    if target.startswith("root:"):
        return _target_row(conn, user, target)
    node = nodes.get(conn, target, live=False)
    if node is None or sharing.role_of(conn, user, node) is None:
        raise HTTPException(404, sharing.NOT_FOUND)
    return "node", node


def pending(conn, user: dict, f) -> tuple[list, str]:
    """(activity rows not yet told, the followed item's label) — others' changes the person can open."""
    try:
        kind, row = _target_any(conn, user, f["target"])
    except HTTPException:
        return [], ""
    acc_sql, acc_params = sharing.access_cte(user["id"])
    where = ["a.last_at > ?", "(a.actor_id IS NULL OR a.actor_id != ?)", "a.action != 'shared'"]
    params: list = [f["cursor"], user["id"]]
    if kind == "root":
        where.append("a.root_id = ?")
        params.append(row["id"])
        label = row["label"]
    else:
        ids = _ids_of(conn, kind, row)
        where.append("(a.node_id IN (SELECT value FROM json_each(?)) OR a.parent_id IN (SELECT value FROM json_each(?)))")
        params += [json.dumps(ids), json.dumps(ids)]
        label = _label(conn, row, user)
    rows = conn.execute(acc_sql + "SELECT a.*, n.name, n.kind, u.name AS actor_name, r.user_id AS root_owner FROM activity a "
                        "JOIN nodes n ON n.id = a.node_id JOIN acc ON acc.node_id = a.node_id "
                        "JOIN roots r ON r.id = n.root_id "
                        f"LEFT JOIN users u ON u.id = a.actor_id WHERE {' AND '.join(where)} "
                        "ORDER BY a.last_at", (*acc_params, *params)).fetchall()
    out = []
    for r in rows:                     # folder names and old names only as far as this person may know them
        d = dict(r)
        if d["detail"]:
            try:
                d["detail"] = json.dumps(activity.feed_detail(conn, user, d["action"], json.loads(d["detail"]),
                                                              d["root_owner"]))
            except ValueError:
                d["detail"] = None
        out.append(d)
    return out, label


def _label(conn, row, user: dict | None = None) -> str:
    """"House papers › Taxes" for a folder (its space's name first; only folders the person can open), the name
    for a document."""
    if row["kind"] != "folder":
        return row["name"]
    root = roots.root_row(conn, row["root_id"])
    above = sharing.visible_ancestors(conn, user, row) if user is not None else nodes.ancestors(conn, row)
    parts = [a["name"] for a in above] + [row["name"]]
    if root is not None and root["kind"] == "shared":
        parts = [root["label"]] + parts
    return " › ".join(parts)


def message(rows, label: str, followed_kind: str) -> str:
    """One line, names only (never any content)."""
    def who(r):
        return r["actor_name"] or "Someone outside the app"
    if len(rows) == 1:
        r = rows[0]
        name = f"“{r['name']}”"
        times = f" ({r['count']} times)" if r["action"] == "edited" and r["count"] > 1 else ""
        try:
            detail = json.loads(r["detail"]) if r["detail"] else {}
        except ValueError:
            detail = {}
        if r["action"] == "created":
            return f"{who(r)} added {name}" + (f" to {label}" if followed_kind == "folder" else "")
        if r["action"] == "edited":
            return f"{who(r)} edited {name}{times}"
        if r["action"] == "renamed":
            if not detail.get("from"):
                return f"{who(r)} renamed something to “{detail.get('to') or r['name']}”"
            return f"{who(r)} renamed “{detail['from']}” to “{detail.get('to') or r['name']}”"
        if r["action"] == "moved":
            return f"{who(r)} moved {name}" + (f" to {detail['to']}" if detail.get("to") else "")
        if r["action"] == "deleted":
            return f"{who(r)} deleted {name}"
        if r["action"] == "restored":
            return f"{who(r)} restored {name}"
        if r["action"] == "filed":
            return (f"A filing rule filed “{detail.get('from') or r['name']}” as {name}"
                    + (f" in {detail['toFolder']}" if detail.get("toFolder") else ""))
    if all(r["action"] == "created" for r in rows):
        n = len(rows)
        return f"{n} new files in {label}" if all(r["kind"] != "folder" for r in rows) else f"{n} new items in {label}"
    actors = []
    for r in rows:
        if who(r) not in actors:
            actors.append(who(r))
    by = ", ".join(actors[:3]) + (" and others" if len(actors) > 3 else "")
    return f"{len(rows)} changes in {label} — by {by}"


# ---------------------------------------------------------------- sending
def dispatch(now=None) -> int:
    """Send what is due. Returns the number of notifications sent."""
    now = now or config.utcnow()
    now_iso = now.isoformat(timespec="seconds")
    settle = (now - timedelta(seconds=SETTLE_SECONDS)).isoformat(timespec="seconds")
    batch = (now - timedelta(minutes=BATCH_MINUTES)).isoformat(timespec="seconds")
    plan = []                                       # (user id, target, services, message, new cursor)
    with db.get_conn() as conn:
        follows_ = conn.execute("SELECT f.*, u.prefs, u.disabled FROM follows f JOIN users u ON u.id = f.user_id").fetchall()
        for f in follows_:
            if f["last_sent_at"] and f["last_sent_at"] > batch:
                continue
            user = {"id": f["user_id"], "disabled": bool(f["disabled"])}
            rows, label = pending(conn, user, f)
            if not rows:
                continue
            newest = max(r["last_at"] for r in rows)
            prefs = notify.prefs_of(f)
            if f["disabled"] or prefs.get("notifyFollows", True) is False:
                plan.append((f["user_id"], f["target"], [], None, newest))
                continue
            if newest > settle or quiet_now(prefs, config.ZONE.now(now)):
                continue                            # still happening, or quiet hours: later, in one message
            kind = "folder" if f["target"].startswith("root:") else (nodes.get(conn, f["target"], live=False) or {"kind": "file"})["kind"]
            services = ha_notify.services_for({"id": f["user_id"]}, conn)
            plan.append((f["user_id"], f["target"], services, message(rows, label, kind), newest))
    sent = 0
    for uid, target, services, text, cursor in plan:
        ok = False
        if services and text:
            try:
                result = ha_notify.send_to_services(services, config.APP_TITLE, text,
                                                    {"url": config.INGRESS_URL, "clickAction": config.INGRESS_URL})
                ok = any(result.values())
            except Exception:
                logger.exception("Sending a follow notification failed")
        with db.get_conn() as conn:
            conn.execute("UPDATE follows SET cursor = ?, last_sent_at = CASE WHEN ? THEN ? ELSE last_sent_at END "
                         "WHERE user_id = ? AND target = ?", (cursor, 1 if ok else 0, now_iso, uid, target))
        sent += ok
    return sent


def purge(conn) -> int:
    """Follows of items gone for good."""
    return conn.execute("DELETE FROM follows WHERE target NOT LIKE 'root:%' AND target NOT IN (SELECT id FROM nodes) "
                        "OR (target LIKE 'root:%' AND substr(target, 6) NOT IN (SELECT id FROM roots))").rowcount
