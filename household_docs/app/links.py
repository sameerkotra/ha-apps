"""Links between documents (SPEC §17.3).

A note (plain or Markdown) links to a file or folder with `[[Title]]` in its text — readable in any editor.
Which item a link means is kept in `node_links(from_id, text, to_id)` by node id, so a link keeps working after
the target is renamed or moved. When a link has no row (typed by hand, written outside the app, or the database
lost it) it is found by title: an item the person can open whose name, with or without its extension, is the
text — in the same space first. The `[[` picker sends the id it picked (`hints`), so two items with the same
title are told apart.

What the reader sees (`describe`): a link to something they can open shows its name and opens it; one they
can't open shows "🔒 No access" (and nothing about it); a target in Trash or gone shows "Deleted"; text that
matches nothing, "Not found". **Linked from** lists the documents linking here that the reader can open — the
access check is inside the query.
"""
import re

from . import sharing
from .search import fts
from .store import nodes

LINK = re.compile(r"\[\[([^\[\]\n|]{1,200})\]\]")
MAX_LINKS = 200
TEXT_KINDS = ("note", "markdown")


def texts_of(text: str) -> list[str]:
    """The link texts in a note, in order, each once."""
    seen = []
    for m in LINK.finditer(text or ""):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.append(t)
            if len(seen) >= MAX_LINKS:
                break
    return seen


def by_title(conn, user: dict, title: str, near_root: str | None = None) -> str | None:
    """An item the person can open called `title` (with or without its extension)."""
    t = fts.fold(title.strip())
    if not t:
        return None
    acc_sql, acc_params = sharing.access_cte(user["id"])
    row = conn.execute(acc_sql + "SELECT n.id FROM nodes n JOIN acc ON acc.node_id = n.id JOIN roots r ON r.id = n.root_id "
                       f"WHERE {nodes.LIVE} AND (r.kind = 'person' OR r.missing = 0) AND (n.name_folded = ? OR "
                       "(n.ext IS NOT NULL AND n.name_folded = ? || '.' || lower(n.ext))) "
                       "ORDER BY n.root_id = ? DESC, n.kind = 'folder', length(n.rel), n.rel LIMIT 1",
                       (*acc_params, t, t, near_root or "")).fetchone()
    return row["id"] if row else None


def _target_lost(conn, to_id) -> bool:
    return not to_id or conn.execute("SELECT 1 FROM nodes WHERE id = ?", (to_id,)).fetchone() is None


def update(conn, user: dict, node, text: str, hints: dict | None = None) -> None:
    """Bring a note's link rows in line with its text (after a save by `user`, an editor)."""
    if node["kind"] not in TEXT_KINDS:
        conn.execute("DELETE FROM node_links WHERE from_id = ?", (node["id"],))
        return
    wanted = texts_of(text)
    have = {r["text"]: r["to_id"] for r in conn.execute("SELECT text, to_id FROM node_links WHERE from_id = ?", (node["id"],))}
    for t in set(have) - set(wanted):
        conn.execute("DELETE FROM node_links WHERE from_id = ? AND text = ?", (node["id"], t))
    for t in wanted:
        to = have.get(t)
        hint = (hints or {}).get(t)
        if isinstance(hint, str) and hint != to:
            target = nodes.get(conn, hint)
            if target is not None and sharing.role_of(conn, user, target) is not None:
                to = hint
        if t not in have or _target_lost(conn, to):
            to = to if not _target_lost(conn, to) else by_title(conn, user, t, node["root_id"])
        if t in have:
            if to != have[t]:
                conn.execute("UPDATE node_links SET to_id = ? WHERE from_id = ? AND text = ?", (to, node["id"], t))
        else:
            conn.execute("INSERT INTO node_links (from_id, text, to_id) VALUES (?, ?, ?)", (node["id"], t, to))


def _title(row) -> str:
    if row["ext"] and row["kind"] != "folder" and row["kind"] != "file" and row["name"].lower().endswith("." + row["ext"].lower()):
        return row["name"][:-(len(row["ext"]) + 1)]
    return row["name"]


def describe(conn, user: dict, node, text: str | None = None) -> dict:
    """{links: {text: {state, id?, name?, kind?}}, backlinks: [...]} as `user` may see them."""
    rows = {r["text"]: r["to_id"] for r in conn.execute("SELECT text, to_id FROM node_links WHERE from_id = ?", (node["id"],))}
    texts = texts_of(text) if text is not None else sorted(rows)
    out = {}
    for t in texts:
        to = rows.get(t)
        if t not in rows or _target_lost(conn, to):
            found = by_title(conn, user, t, node["root_id"])       # not stored: looked up for this reader only
            to = found if found else to
        if not to:
            out[t] = {"state": "missing"}
            continue
        target = nodes.get(conn, to, live=False)
        if target is None or target["gone_at"] or target["trash_id"]:
            out[t] = {"state": "deleted"}
            continue
        role = sharing.role_of(conn, user, target)
        if role is None:
            out[t] = {"state": "noaccess"}
            continue
        out[t] = {"state": "ok", "id": target["id"], "name": target["name"], "title": _title(target),
                  "kind": target["kind"]}
    acc_sql, acc_params = sharing.access_cte(user["id"])
    back = conn.execute(acc_sql + "SELECT DISTINCT n.* FROM node_links l JOIN nodes n ON n.id = l.from_id "
                        "JOIN acc ON acc.node_id = n.id JOIN roots r ON r.id = n.root_id "
                        f"WHERE l.to_id = ? AND l.from_id != ? AND {nodes.LIVE} AND (r.kind = 'person' OR r.missing = 0) "
                        "ORDER BY n.name_folded LIMIT 100", (*acc_params, node["id"], node["id"])).fetchall()
    return {"links": out, "backlinks": [{"id": b["id"], "name": b["name"], "title": _title(b), "kind": b["kind"],
                                         "ext": b["ext"]} for b in back]}
