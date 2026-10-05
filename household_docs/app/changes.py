"""What changed since you last looked (SPEC §17.21).

Each person's last look at an item is `user_state.seen_at` (when) and `seen_sha` (the content they saw: the
file's SHA-256 then). An item is **changed** for a person when it isn't a folder, someone else (or something
outside the app) changed it last, its file is newer than their last look, and its content isn't what they saw.
Items they never opened count from `users.seen_from` (when the feature started for them), so the whole past
isn't "new".

- **Dots** in lists and search results (`changed: true` per item, a ROW_EXTRAS hook); `is:new` in search.
- **Opening** a changed document you had looked at before returns `changedSince` — who changed it, and what you
  last saw, when History
  still has that version (§7.3, matched by SHA-256): a note's old text (the page highlights the changed lines
  for that visit), a checklist's changed or new items (their keys), a sheet's changed cells. Then the look is
  recorded: the next open shows no changes.
- **Saving** counts as looking (your own changes are never "new" to you).
- **Mark all as seen** on a folder records a look at everything in it you can open.
"""
from . import config, sharing
from .formats import checklist_md, text as text_fmt
from .store import nodes, roots, versions

MAX_CELLS = 2000


def saw(conn, user_id: str, node_id: str) -> None:
    """Record a look at an item (opening it, saving it, Mark all as seen)."""
    conn.execute("INSERT INTO user_state (user_id, node_id, seen_at, seen_sha) VALUES (?, ?, ?, "
                 "(SELECT sha256 FROM nodes WHERE id = ?)) ON CONFLICT(user_id, node_id) DO UPDATE SET "
                 "seen_at = excluded.seen_at, seen_sha = excluded.seen_sha", (user_id, node_id, config.now_iso(), node_id))


CHANGED_SQL = ("n.kind != 'folder' AND COALESCE(n.updated_by, '') != {u} AND n.mtime > COALESCE("
               "(SELECT us2.seen_at FROM user_state us2 WHERE us2.user_id = {u} AND us2.node_id = n.id), "
               "(SELECT COALESCE(u2.seen_from, u2.created_at) FROM users u2 WHERE u2.id = {u})) "
               "AND NOT EXISTS (SELECT 1 FROM user_state us3 WHERE us3.user_id = {u} AND us3.node_id = n.id "
               "AND us3.seen_sha = n.sha256)")


def changed_sql(user_id: str) -> tuple[str, list]:
    """A condition on `n` (a nodes row): changed since this person last looked."""
    return CHANGED_SQL.format(u="?"), [user_id] * 4


def row_extras(conn, user: dict, rows) -> dict:
    ids = [r["id"] for r in rows if r["kind"] != "folder"]
    out = {}
    sql, params = changed_sql(user["id"])
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT n.id FROM nodes n WHERE n.id IN ({q}) AND {sql}", (*chunk, *params)):
            out[r["id"]] = {"changed": True}
    return out


def is_changed(conn, user: dict, node_id: str) -> bool:
    sql, params = changed_sql(user["id"])
    return conn.execute(f"SELECT 1 FROM nodes n WHERE n.id = ? AND {sql}", (node_id, *params)).fetchone() is not None


def _who(conn, user: dict, node, since: str | None) -> list[str]:
    rows = conn.execute("SELECT DISTINCT a.actor_id, u.name FROM activity a LEFT JOIN users u ON u.id = a.actor_id "
                        "WHERE a.node_id = ? AND a.last_at > ? AND a.action IN ('edited', 'created', 'restored') "
                        "AND (a.actor_id IS NULL OR a.actor_id != ?)", (node["id"], since or "", user["id"])).fetchall()
    out = []
    for r in rows:
        name = r["name"] if r["actor_id"] else "outside the app"
        if name and name not in out:
            out.append(name)
    if not out and node["updated_by"] != user["id"]:
        u = conn.execute("SELECT name FROM users WHERE id = ?", (node["updated_by"],)).fetchone() if node["updated_by"] else None
        out.append(u["name"] if u else "outside the app")
    return out[:5]


def _seen_version(conn, node, seen_sha: str | None) -> bytes | None:
    """The content the person last saw, if History has it."""
    if not seen_sha:
        return None
    r = conn.execute("SELECT n FROM versions WHERE node_id = ? AND sha256 = ? ORDER BY n DESC LIMIT 1",
                     (node["id"], seen_sha)).fetchone()
    if r is None:
        return None
    try:
        return versions.read(conn, roots.root_row(conn, node["root_id"]), node["id"], r["n"])
    except (FileNotFoundError, OSError):
        return None


def since_last_look(conn, user: dict, node, *, text: str | None = None, items=None, sheet: dict | None = None,
                    ext: str | None = None) -> dict | None:
    """`changedSince` for an item being opened (before the look is recorded), or None when nothing changed."""
    if not is_changed(conn, user, node["id"]):
        return None
    st = conn.execute("SELECT seen_at, seen_sha FROM user_state WHERE user_id = ? AND node_id = ?",
                      (user["id"], node["id"])).fetchone()
    seen_at, seen_sha = (st["seen_at"], st["seen_sha"]) if st else (None, None)
    if not seen_sha:
        return None                         # the first look: it's all new (the lists' dot said so), nothing to compare
    out = {"since": seen_at, "by": _who(conn, user, node, seen_at)}
    old = _seen_version(conn, node, seen_sha)
    if old is None:
        return out
    try:
        if node["kind"] in ("note", "markdown") and text is not None:
            out["before"], _m = text_fmt.decode(old)
        elif node["kind"] == "checklist" and items is not None:
            before, _m = text_fmt.decode(old)
            try:
                was = {it.key: it.done for it in checklist_md.parse(before)}
            except ValueError:
                was = {}
            out["items"] = [it.key for it in items if it.key not in was or was[it.key] != it.done]
        elif node["kind"] == "sheet" and sheet is not None:
            from . import sheets
            from .formats import sheet_model as M
            prev = sheets.parse(old, ext or "xlsx")["sheet"]
            cells: dict = {}
            n = 0
            prev_tabs = {t["name"].casefold(): t for t in prev["tabs"] if t.get("kind") == "grid"}
            for t in sheet["tabs"]:
                if t.get("kind") != "grid":
                    continue
                p = prev_tabs.get(t["name"].casefold(), {"cells": {}})
                refs = [ref for ref in set(t["cells"]) | set(p["cells"])
                        if M.content(t["cells"].get(ref)) != M.content(p["cells"].get(ref))]
                if refs:
                    cells[t["name"]] = sorted(refs)[:MAX_CELLS - n]
                    n += len(cells[t["name"]])
                if n >= MAX_CELLS:
                    break
            out["cells"] = cells
    except Exception:                       # a version that can't be read: just the dot and who
        return out
    return out


def mark_seen(conn, user: dict, ref: str | None) -> int:
    """Mark all as seen: everything you can open in a folder (and the folders inside it)."""
    from . import docops
    root, folder, _role = docops.place(conn, user, ref, "viewer")
    acc_sql, acc_params = sharing.access_cte(user["id"])
    if folder is None:
        where, params = "n.root_id = ?", [root["id"]]
    else:
        where, params = "n.root_id = ? AND substr(n.rel, 1, ?) = ?", [root["id"], len(folder["rel"]) + 1, folder["rel"] + "/"]
    ids = [r["id"] for r in conn.execute(acc_sql + f"SELECT n.id FROM nodes n JOIN acc ON acc.node_id = n.id WHERE {where} "
                                         f"AND {nodes.LIVE} AND n.kind != 'folder'", (*acc_params, *params))]
    for nid in ids:
        saw(conn, user["id"], nid)
    return len(ids)
