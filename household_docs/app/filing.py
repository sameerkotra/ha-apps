"""Filing rules and clean-up rules (SPEC §17.18, §17.19), per folder.

**Filing rules** — when a file *arrives* in a folder (uploaded, scanned, or added outside the app and found by the
index; never a document someone makes or copies in the app), the folder's rules are tried in order and the first
that matches acts: rename (a pattern with {date} {yyyy} {mm} {dd} {name} {n}) and/or move to a folder in the same
space. A rule matches by name (wildcards as in search, §10.2), type and size. It acts as the person who made it,
with their rights at that moment (nothing happens if they can no longer change the file or the destination).
Every action is one 🕑 Activity row ("filed") with Undo for 7 days (`filing_log`). A dry run lists what a rule
would do with the files already in the folder before it is saved. Arrivals wait in `filing_queue` and are handled
straight after an upload, after an index scan and every housekeeping tick — never while read-only mode is on.

**Clean-up rules** — one per folder: move items to Trash (recoverable for `trash_days`) or into an "Archive"
subfolder N days after their last change. A daily job; folders, favourites (anyone's) and pinned items are
skipped; nothing in a folder without a rule is ever touched; paused in read-only mode (§5.7).
"""
import logging
import re

from fastapi import HTTPException

from . import activity, config, db, docops, sharing
from .search import pattern as pattern_mod
from .store import moving, nodes, paths, roots

logger = logging.getLogger("filing")
TYPES = {"note": "Notes", "checklist": "Checklists", "sheet": "Sheets", "pdf": "PDFs", "image": "Pictures",
         "file": "Other files"}
IMAGE_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff")
PLACEHOLDERS = ("date", "yyyy", "mm", "dd", "name", "n")
PLACEHOLDER_RE = re.compile(r"\{([a-z]*)\}")
MAX_RULES = 20
UNDO_DAYS = 7
ARCHIVE = "Archive"
QUEUE_PER_RUN = 50
MAX_N = 999


# ---------------------------------------------------------------- places
def _target(conn, user: dict, ref: str, wanted: str = "editor"):
    """(root, folder or None, canonical ref) of a folder rules can be set on: a folder you can edit or the top of
    an admin shared folder you may write in (§17.18). Your My docs top has no rules (*decision*: a rule there would
    act on everything you make). Changing rules waits while read-only mode is on (like tags, §5.7)."""
    if not ref:
        raise HTTPException(422, "Rules are set on a folder.")
    root, folder, _role = docops.place(conn, user, ref, wanted)
    if wanted == "editor":
        from .store import fileio
        fileio.ensure_writable()
    if folder is None and root["kind"] != "shared":
        raise HTTPException(422, "Rules are set on a folder — make one (for example Inbox) and set them there.")
    return root, folder, (folder["id"] if folder is not None else "root:" + root["id"])


def _ref_of_parent(node, root_kind: str) -> str | None:
    if node["parent_id"]:
        return node["parent_id"]
    return "root:" + node["root_id"] if root_kind == "shared" else None


# ---------------------------------------------------------------- matching and naming
def type_of(node) -> str:
    k = node["kind"]
    if k in ("note", "markdown"):
        return "note"
    if k in ("checklist", "sheet"):
        return k
    ext = (node["ext"] or "").lower()
    if ext == "pdf":
        return "pdf"
    if ext in IMAGE_EXTS or ("preview" in node.keys() and node["preview"]):
        return "image"
    return "file"


def matches(rule, node) -> bool:
    if node["kind"] == "folder":
        return False
    if not re.match(pattern_mod.wildcard_to_regex(rule["pattern"]), node["name"], re.I):
        return False
    if rule["type"] and type_of(node) != rule["type"]:
        return False
    size = node["size"] or 0
    if rule["min_kb"] is not None and size < rule["min_kb"] * 1024:
        return False
    if rule["max_kb"] is not None and size > rule["max_kb"] * 1024:
        return False
    return True


def render(template: str, name: str, when, n: int | None = None) -> str:
    """The new name from a rename pattern. The original extension is kept (added when the pattern has none)."""
    stem, ext = paths.split_ext(name)
    values = {"date": when.strftime("%Y-%m-%d"), "yyyy": when.strftime("%Y"), "mm": when.strftime("%m"),
              "dd": when.strftime("%d"), "name": stem or name, "n": str(n or 1)}
    out = PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template).strip()
    if ext and not out.lower().endswith("." + ext.lower()):
        out = f"{out}.{ext}"
    return out


def _final_name(template: str | None, node, folder_real: str, when, own_real: str | None = None) -> str:
    """The name in the destination folder: the pattern (with the first free {n}) or the current name; " (2)" …
    when something else there has it (the file itself doesn't count)."""
    if not template:
        name = node["name"]
        return name if not _taken(folder_real, name, own_real) else paths.unique_name(folder_real, name)
    if "{n}" in template:
        for n in range(1, MAX_N + 1):
            name = paths.check_name(render(template, node["name"], when, n))
            if not _taken(folder_real, name, own_real):
                return name
        raise HTTPException(409, "No free number for {n}.")
    name = paths.check_name(render(template, node["name"], when))
    return name if not _taken(folder_real, name, own_real) else paths.unique_name(folder_real, name)


def _taken(folder_real: str, name: str, own_real: str | None) -> bool:
    """Something other than the file itself has this name (any case, as on Windows) in the folder."""
    import os
    try:
        return any(x.casefold() == name.casefold() and os.path.join(folder_real, x) != own_real
                   for x in os.listdir(folder_real))
    except OSError:
        return False


# ---------------------------------------------------------------- rules (the dialog)
def clean_rule(conn, user: dict, root, body: dict) -> dict:
    allowed = {"pattern", "type", "minKb", "maxKb", "rename", "moveTo"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise HTTPException(422, "A rule has pattern, type, minKb, maxKb, rename and moveTo.")
    pat = body.get("pattern")
    if not isinstance(pat, str) or not 1 <= len(pat.strip()) <= 200 or "/" in pat or "\\" in pat:
        raise HTTPException(422, "The name pattern is 1–200 characters, like Power-bill*.pdf (no folders).")
    t = body.get("type")
    if t is not None and t not in TYPES:
        raise HTTPException(422, "Type is " + ", ".join(TYPES) + ".")
    out = {"pattern": pat.strip(), "type": t, "min_kb": None, "max_kb": None, "rename": None, "move_to": None}
    for k, col in (("minKb", "min_kb"), ("maxKb", "max_kb")):
        v = body.get(k)
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100_000_000:
                raise HTTPException(422, "Sizes are whole KB.")
            out[col] = v
    rn = body.get("rename")
    if rn is not None and rn != "":
        if not isinstance(rn, str) or len(rn) > 200:
            raise HTTPException(422, "The new name is at most 200 characters.")
        bad = [m for m in PLACEHOLDER_RE.findall(rn) if m not in PLACEHOLDERS]
        if bad:
            raise HTTPException(422, f"Unknown {{{bad[0]}}} — use " + " ".join("{" + p + "}" for p in PLACEHOLDERS) + ".")
        try:
            paths.check_name(render(rn, "Example.pdf", config.now(), 1))
        except paths.PathError as e:
            raise HTTPException(422, f"The new name: {e}")
        out["rename"] = rn.strip()
    mv = body.get("moveTo")
    if mv is not None and mv != "":
        if not isinstance(mv, str) or len(mv) > 70:
            raise HTTPException(422, "Choose the folder to move to.")
        droot, dfolder, _r = docops.place(conn, user, mv, "editor")
        if droot["id"] != root["id"]:
            raise HTTPException(422, "Files move only within the same space (My docs, or one shared folder).")
        out["move_to"] = dfolder["id"] if dfolder is not None else "root:" + droot["id"]
    if not out["rename"] and not out["move_to"]:
        raise HTTPException(422, "A rule renames, moves, or both.")
    return out


def rule_json(conn, r) -> dict:
    dest = None
    if r["move_to"]:
        if r["move_to"].startswith("root:"):
            rr = roots.root_row(conn, r["move_to"][5:])
            dest = rr["label"] if rr and rr["kind"] == "shared" else "My docs"
        else:
            d = nodes.get(conn, r["move_to"])
            dest = d["name"] if d else None
    who = conn.execute("SELECT name FROM users WHERE id = ?", (r["created_by"],)).fetchone()
    return {"id": r["id"], "pattern": r["pattern"], "type": r["type"], "minKb": r["min_kb"], "maxKb": r["max_kb"],
            "rename": r["rename"], "moveTo": r["move_to"], "moveToName": dest, "destMissing": bool(r["move_to"] and dest is None),
            "by": who["name"] if who else None, "position": r["position"]}


def rules_json(conn, user: dict, ref: str) -> dict:
    root, folder, canon = _target(conn, user, ref, "viewer")
    role = docops.place(conn, user, ref, "viewer")[2]
    rows = conn.execute("SELECT * FROM filing_rules WHERE target = ? ORDER BY position, created_at", (canon,)).fetchall()
    c = conn.execute("SELECT * FROM cleanup_rules WHERE target = ?", (canon,)).fetchone()
    return {"ref": canon, "name": folder["name"] if folder is not None else root["label"],
            "canEdit": sharing.at_least(role, "editor") and not moving.is_read_only(conn),
            "filing": [rule_json(conn, r) for r in rows],
            "cleanup": cleanup_json(conn, c), "types": TYPES, "placeholders": list(PLACEHOLDERS)}


def add_rule(conn, user: dict, ref: str, body: dict) -> dict:
    root, _folder, canon = _target(conn, user, ref)
    rule = clean_rule(conn, user, root, body)
    n = conn.execute("SELECT COUNT(*), COALESCE(MAX(position), 0) FROM filing_rules WHERE target = ?", (canon,)).fetchone()
    if n[0] >= MAX_RULES:
        raise HTTPException(409, f"A folder can have at most {MAX_RULES} filing rules.")
    rid = db.new_id()
    conn.execute("INSERT INTO filing_rules (id, target, position, pattern, type, min_kb, max_kb, rename, move_to, "
                 "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (rid, canon, n[1] + 1, rule["pattern"], rule["type"], rule["min_kb"], rule["max_kb"], rule["rename"],
                  rule["move_to"], user["id"], config.now_iso()))
    db.audit(conn, "filing_rule_added", user["id"], None if canon.startswith("root:") else canon)
    return rules_json(conn, user, canon)


def _rule_row(conn, canon: str, rule_id: str):
    r = conn.execute("SELECT * FROM filing_rules WHERE id = ? AND target = ?", (rule_id[:64], canon)).fetchone()
    if r is None:
        raise HTTPException(404, "That rule isn't there any more.")
    return r


def change_rule(conn, user: dict, ref: str, rule_id: str, body: dict) -> dict:
    root, _folder, canon = _target(conn, user, ref)
    _rule_row(conn, canon, rule_id)
    rule = clean_rule(conn, user, root, body)
    conn.execute("UPDATE filing_rules SET pattern = ?, type = ?, min_kb = ?, max_kb = ?, rename = ?, move_to = ?, "
                 "created_by = ? WHERE id = ?", (rule["pattern"], rule["type"], rule["min_kb"], rule["max_kb"],
                                                  rule["rename"], rule["move_to"], user["id"], rule_id))
    return rules_json(conn, user, canon)


def remove_rule(conn, user: dict, ref: str, rule_id: str) -> dict:
    _root, _folder, canon = _target(conn, user, ref)
    _rule_row(conn, canon, rule_id)
    conn.execute("DELETE FROM filing_rules WHERE id = ?", (rule_id,))
    return rules_json(conn, user, canon)


def reorder(conn, user: dict, ref: str, ids: list) -> dict:
    _root, _folder, canon = _target(conn, user, ref)
    have = [r["id"] for r in conn.execute("SELECT id FROM filing_rules WHERE target = ? ORDER BY position", (canon,))]
    if not isinstance(ids, list) or sorted(ids) != sorted(have):
        raise HTTPException(409, "The rules changed meanwhile — reload and try again.")
    for i, rid in enumerate(ids, start=1):
        conn.execute("UPDATE filing_rules SET position = ? WHERE id = ?", (i, rid))
    return rules_json(conn, user, canon)


def dry_run(conn, user: dict, ref: str, body: dict) -> dict:
    """What a rule would do with the files already in the folder (nothing changes)."""
    root, folder, canon = _target(conn, user, ref)
    rule = clean_rule(conn, user, root, body)
    rows = nodes.children(conn, root["id"], folder["id"] if folder is not None else None)
    when = config.now()
    dest_name, dest_real = _dest(conn, root, rule["move_to"], folder)
    out = []
    count = 0
    taken: set = set()
    for n in sorted(rows, key=lambda x: x["name"].casefold()):
        if not matches(rule, n):
            continue
        count += 1
        if len(out) >= 100:
            continue
        try:
            own = nodes.real_path(conn, n)[1]
            name = _final_name(rule["rename"], n, dest_real, when, own) if rule["rename"] else n["name"]
            if rule["rename"] and "{n}" in rule["rename"]:
                k = 1
                while name.casefold() in taken and k < MAX_N:
                    k += 1
                    name = paths.check_name(render(rule["rename"], n["name"], when, k))
            taken.add(name.casefold())
            out.append({"id": n["id"], "name": n["name"], "newName": name, "to": dest_name})
        except (HTTPException, paths.PathError) as e:
            out.append({"id": n["id"], "name": n["name"], "error": str(getattr(e, "detail", e))})
    return {"matches": out, "count": count, "note": "Rules act on files that arrive after they're saved — these are "
                                                     "already here, so they stay as they are."}


def _dest(conn, root, move_to: str | None, folder):
    """(folder name, real path) of where a rule puts files (its own folder when it doesn't move)."""
    target = move_to
    if target is None:
        return (folder["name"] if folder is not None else root["label"]), nodes.folder_real(conn, root, folder)
    if target.startswith("root:"):
        return (root["label"] if root["kind"] == "shared" else "My docs"), nodes.folder_real(conn, root, None)
    d = nodes.get(conn, target)
    if d is None or d["kind"] != "folder":
        raise HTTPException(409, "The folder this rule moves files to isn't there any more.")
    return d["name"], nodes.folder_real(conn, root, d)


# ---------------------------------------------------------------- arrivals
def queue(conn, node_id: str, how: str) -> None:
    conn.execute("INSERT OR IGNORE INTO filing_queue (node_id, how, queued_at) VALUES (?, ?, ?)",
                 (node_id, how, config.now_iso()))


def on_activity(conn, action, node, actor_id) -> None:
    """activity.HOOKS: a file the index found ("Someone outside the app" added it) waits for the folder's rules."""
    if action == "created" and actor_id is None and node is not None and node["kind"] != "folder":
        queue(conn, node["id"], "found")


def run_queue(limit: int = QUEUE_PER_RUN) -> int:
    """Apply the rules to files that arrived. Returns how many were filed."""
    if moving.is_read_only() or db.RESTORING.is_set():
        return 0
    with db.get_conn() as conn:
        ids = [r["node_id"] for r in conn.execute("SELECT node_id FROM filing_queue ORDER BY queued_at LIMIT ?", (limit,))]
    done = 0
    for nid in ids:
        try:
            with db.get_conn() as conn:
                conn.execute("DELETE FROM filing_queue WHERE node_id = ?", (nid,))
                if apply_rules(conn, nid):
                    done += 1
        except Exception:
            logger.exception("Filing an arrived file failed")
            with db.get_conn() as conn:
                conn.execute("DELETE FROM filing_queue WHERE node_id = ?", (nid,))
    return done


def apply_rules(conn, node_id: str) -> str | None:
    """The first matching rule of the file's folder acts. Returns the filing_log id, or None."""
    from .auth import user_dict
    node = nodes.get(conn, node_id)
    if node is None or node["kind"] == "folder":
        return None
    root = roots.root_row(conn, node["root_id"])
    target = _ref_of_parent(node, root["kind"])
    if target is None:
        return None
    for rule in conn.execute("SELECT * FROM filing_rules WHERE target = ? ORDER BY position, created_at", (target,)).fetchall():
        if not matches(rule, node):
            continue
        u = conn.execute("SELECT * FROM users WHERE id = ?", (rule["created_by"],)).fetchone()
        if u is None or u["disabled"]:
            logger.info("A filing rule's maker can't use the app any more; the rule did nothing.")
            return None
        actor = user_dict(u)
        conn.execute("SAVEPOINT filing")
        try:
            log_id = _file(conn, actor, rule, node, root)
        except (HTTPException, paths.PathError, OSError) as e:
            conn.execute("ROLLBACK TO filing")
            conn.execute("RELEASE filing")
            logger.info("A filing rule couldn't act on a file: %s", getattr(e, "detail", e))
            return None
        conn.execute("RELEASE filing")
        return log_id
    return None


def _file(conn, actor: dict, rule, node, root) -> str:
    """Rename and/or move one file by a rule, as its maker; one "filed" Activity row (instead of a rename and a move)."""
    sharing.require(conn, actor, node["id"], "editor")
    folder = nodes.get(conn, node["parent_id"]) if node["parent_id"] else None
    _dname, dest_real = _dest(conn, root, rule["move_to"], folder)
    own_real = nodes.real_path(conn, node)[1]
    name = _final_name(rule["rename"], node, dest_real, config.now(), own_real) if rule["rename"] else None
    before = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM activity").fetchone()[0]
    from_parent, from_name = _ref_of_parent(node, root["kind"]), node["name"]
    from_folder = activity.folder_name(conn, node["parent_id"], node["root_id"])
    from_folder_id, acl = activity.folder_id(node["parent_id"]), sharing.who_can_open(conn, node)
    if rule["move_to"] and rule["move_to"] != from_parent:
        docops.move(conn, actor, node["id"], rule["move_to"])
    node = nodes.get(conn, node["id"])
    if name and name != node["name"]:
        docops.rename(conn, actor, node["id"], name)
    node = nodes.get(conn, node["id"])
    if node["name"] == from_name and _ref_of_parent(node, root["kind"]) == from_parent:
        return None
    conn.execute("DELETE FROM activity WHERE rowid > ? AND node_id = ? AND action IN ('moved', 'renamed')",
                 (before, node["id"]))
    log_id = db.new_id()
    conn.execute("INSERT INTO filing_log (id, node_id, rule_id, actor_id, from_parent, from_name, to_parent, to_name, "
                 "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (log_id, node["id"], rule["id"], actor["id"], from_parent, from_name,
                  _ref_of_parent(node, root["kind"]), node["name"], config.now_iso()))
    activity.record(conn, "filed", actor["id"], node, detail={
        "from": from_name, "to": node["name"], "fromFolder": from_folder,
        "toFolder": activity.folder_name(conn, node["parent_id"], node["root_id"]) or
        (root["label"] if root["kind"] == "shared" else "My docs"), "log": log_id, "pattern": rule["pattern"],
        "fromFolderId": from_folder_id, "toFolderId": activity.folder_id(node["parent_id"]), "acl": acl})
    db.audit(conn, "filed", actor["id"], node["id"], node["root_id"])
    return log_id


def prune(conn) -> int:
    """Hourly: filing records past their Undo time (30 days kept), queued files that are gone, rules of folders
    that are gone for good."""
    n = conn.execute("DELETE FROM filing_log WHERE created_at < ?", (config.ago_iso(days=30),)).rowcount
    n += conn.execute("DELETE FROM filing_queue WHERE node_id NOT IN (SELECT id FROM nodes)").rowcount
    for table in ("filing_rules", "cleanup_rules"):
        n += conn.execute(f"DELETE FROM {table} WHERE target NOT LIKE 'root:%' AND target NOT IN (SELECT id FROM nodes)"
                          ).rowcount
        n += conn.execute(f"DELETE FROM {table} WHERE target LIKE 'root:%' AND substr(target, 6) NOT IN "
                          "(SELECT id FROM roots)").rowcount
    return n


def undo_state(conn, log_id: str | None) -> bool:
    """Can this filing still be undone (7 days, not undone yet, the file still there)?"""
    if not log_id:
        return False
    r = conn.execute("SELECT * FROM filing_log WHERE id = ?", (log_id,)).fetchone()
    return bool(r and r["undone_at"] is None and r["created_at"] >= config.ago_iso(days=UNDO_DAYS)
                and nodes.get(conn, r["node_id"]) is not None)


def undo(conn, user: dict, log_id: str) -> dict:
    """Put a filed file back where it arrived, under the name it had (anyone who can change it, for 7 days)."""
    r = conn.execute("SELECT * FROM filing_log WHERE id = ?", (log_id[:64],)).fetchone()
    if r is None:
        raise HTTPException(404, "That isn't there any more.")
    node, _role = sharing.require(conn, user, r["node_id"], "editor")
    if r["undone_at"] is not None:
        raise HTTPException(409, "That was undone already.")
    if r["created_at"] < config.ago_iso(days=UNDO_DAYS):
        raise HTTPException(409, f"Filing can be undone for {UNDO_DAYS} days.")
    root = roots.root_row(conn, node["root_id"])
    back = r["from_parent"]
    if back and not back.startswith("root:") and nodes.get(conn, back) is None:
        back = None if root["kind"] == "person" else "root:" + root["id"]
    if back != _ref_of_parent(node, root["kind"]):
        docops.move(conn, user, node["id"], back)
        node = nodes.get(conn, node["id"])
    if r["from_name"] != node["name"]:
        folder_real = nodes.folder_real(conn, root, nodes.get(conn, node["parent_id"]) if node["parent_id"] else None)
        own_real = nodes.real_path(conn, node)[1]
        name = r["from_name"] if not _taken(folder_real, r["from_name"], own_real) else \
            paths.unique_name(folder_real, r["from_name"])
        docops.rename(conn, user, node["id"], name)
    conn.execute("UPDATE filing_log SET undone_at = ?, undone_by = ? WHERE id = ?", (config.now_iso(), user["id"], r["id"]))
    db.audit(conn, "filing_undone", user["id"], node["id"], node["root_id"])
    return {"ok": True, "id": node["id"]}


# ---------------------------------------------------------------- clean-up rules (§17.19)
def cleanup_json(conn, c) -> dict | None:
    if c is None:
        return None
    who = conn.execute("SELECT name FROM users WHERE id = ?", (c["created_by"],)).fetchone()
    what = "move to Trash" if c["action"] == "trash" else f"move into “{ARCHIVE}”"
    return {"action": c["action"], "days": c["days"], "by": who["name"] if who else None, "lastRunAt": c["last_run_at"],
            "text": f"Items here {what} {c['days']} day{'s' if c['days'] != 1 else ''} after their last change."}


def set_cleanup(conn, user: dict, ref: str, body: dict) -> dict:
    _root, _folder, canon = _target(conn, user, ref)
    if not isinstance(body, dict) or set(body) - {"action", "days"}:
        raise HTTPException(422, "Send action and days.")
    action, days = body.get("action"), body.get("days")
    if action not in ("trash", "archive"):
        raise HTTPException(422, "Action is trash or archive.")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650:
        raise HTTPException(422, "Days is 1 to 3650.")
    conn.execute("INSERT INTO cleanup_rules (target, action, days, created_by, created_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(target) DO UPDATE SET action = excluded.action, days = excluded.days, "
                 "created_by = excluded.created_by", (canon, action, days, user["id"], config.now_iso()))
    db.audit(conn, "cleanup_rule_set", user["id"], None if canon.startswith("root:") else canon)
    return rules_json(conn, user, canon)


def remove_cleanup(conn, user: dict, ref: str) -> dict:
    _root, _folder, canon = _target(conn, user, ref)
    conn.execute("DELETE FROM cleanup_rules WHERE target = ?", (canon,))
    return rules_json(conn, user, canon)


def run_cleanup(force: bool = False) -> dict:
    """The daily job: each rule at most once a day. Paused in read-only mode."""
    stats = {"rules": 0, "trashed": 0, "archived": 0, "skipped": 0}
    if moving.is_read_only() or db.RESTORING.is_set():
        return stats
    with db.get_conn() as conn:
        due = conn.execute("SELECT * FROM cleanup_rules WHERE ? OR last_run_at IS NULL OR last_run_at <= ?",
                           (1 if force else 0, config.ago_iso(hours=23))).fetchall()
    for c in due:
        try:
            with db.get_conn() as conn:
                _cleanup_one(conn, c, stats)
                conn.execute("UPDATE cleanup_rules SET last_run_at = ? WHERE target = ?", (config.now_iso(), c["target"]))
            stats["rules"] += 1
        except Exception:
            logger.exception("A clean-up rule failed")
    return stats


def _cleanup_one(conn, c, stats: dict) -> None:
    from .auth import user_dict
    u = conn.execute("SELECT * FROM users WHERE id = ?", (c["created_by"],)).fetchone()
    if u is None or u["disabled"]:
        return
    actor = user_dict(u)
    try:
        root, folder, _role = docops.place(conn, actor, c["target"], "editor")
    except HTTPException:
        return                                           # the folder is gone, or its maker can't change it any more
    cutoff = config.ago_iso(days=c["days"])
    rows = nodes.children(conn, root["id"], folder["id"] if folder is not None else None)
    keep = {r["node_id"] for r in conn.execute(
        "SELECT node_id FROM user_state WHERE favourite = 1 OR pinned_order IS NOT NULL")}
    archive = None
    for n in sorted(rows, key=lambda x: x["name"].casefold()):
        if n["kind"] == "folder" or n["id"] in keep or not n["mtime"] or n["mtime"] > cutoff:
            if n["kind"] != "folder" and n["id"] in keep:
                stats["skipped"] += 1
            continue
        conn.execute("SAVEPOINT cleanup")
        try:
            if c["action"] == "trash":
                docops.delete(conn, actor, n["id"])
                stats["trashed"] += 1
            else:
                if archive is None:
                    archive = _archive_folder(conn, actor, root, folder)
                docops.move(conn, actor, n["id"], archive)
                stats["archived"] += 1
            conn.execute("RELEASE cleanup")
        except (HTTPException, OSError, paths.PathError) as e:
            conn.execute("ROLLBACK TO cleanup")
            conn.execute("RELEASE cleanup")
            stats["skipped"] += 1
            logger.info("A clean-up rule skipped an item: %s", getattr(e, "detail", e))


def _archive_folder(conn, actor: dict, root, folder) -> str:
    """The folder's "Archive" subfolder (made when missing)."""
    for r in nodes.children(conn, root["id"], folder["id"] if folder is not None else None):
        if r["kind"] == "folder" and r["name"].casefold() == ARCHIVE.casefold():
            return r["id"]
    parent = folder["id"] if folder is not None else "root:" + root["id"]
    return docops.create(conn, actor, "folder", ARCHIVE, parent)


# ---------------------------------------------------------------- on the folder
def folder_extras(conn, ids: list[str]) -> dict:
    """{folder ref: {"filingRules": n, "cleanup": text}} for the folder view's head and rows."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out: dict = {}
    for r in conn.execute(f"SELECT target, COUNT(*) AS n FROM filing_rules WHERE target IN ({q}) GROUP BY target", ids):
        out.setdefault(r["target"], {})["filingRules"] = r["n"]
    for r in conn.execute(f"SELECT * FROM cleanup_rules WHERE target IN ({q})", ids):
        out.setdefault(r["target"], {})["cleanup"] = cleanup_json(conn, r)["text"]
    return out

