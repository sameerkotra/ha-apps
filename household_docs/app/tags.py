"""Tags and colours (SPEC §17.1).

Any number of tags per file or folder (up to MAX_TAGS), and one optional colour (COLOURS). Both are stored by node
id (`node_tags`, `nodes.color`), so they follow renames and moves — in the app, and outside it when the index
matches the file by inode. Everyone who can see an item sees its tags and colour; editors change them (not
while documents are being moved). A tag is kept as first typed; "Taxes" and "taxes" are the same tag.

Search: `tag:taxes`, `-tag:taxes`, `color:red` (and the Search page's Tag and Colour filters) — search/engine.py
asks `tag_sql`. The sidebar's 🏷 Tags lists the tags on things the person can open, with counts.
"""
import re

from fastapi import HTTPException

from . import config, db, sharing
from .search import fts
from .store import moving, nodes

COLOURS = ("red", "orange", "yellow", "green", "teal", "blue", "purple", "grey")
MAX_TAGS = 20
MAX_LEN = 40
_BAD = re.compile(r'[\x00-\x1f\x7f",]')


def clean_tag(value) -> str:
    """A tag as stored: trimmed, inner spaces collapsed, no leading '#'; 1–40 characters, no quotes or commas."""
    if not isinstance(value, str):
        raise HTTPException(422, "A tag is text.")
    t = " ".join(value.strip().lstrip("#").split())
    if not t:
        raise HTTPException(422, "A tag can't be empty.")
    if len(t) > MAX_LEN:
        raise HTTPException(422, f"A tag can be at most {MAX_LEN} characters.")
    if _BAD.search(t):
        raise HTTPException(422, "A tag can't hold quotes or commas.")
    return t


def fold(tag: str) -> str:
    return fts.fold(tag)


def clean_colour(value) -> str | None:
    if value in (None, "", "none"):
        return None
    if not isinstance(value, str) or value.lower() not in COLOURS:
        raise HTTPException(422, "The colour is one of " + ", ".join(COLOURS) + ".")
    return value.lower()


def of(conn, node_id: str) -> list[str]:
    return [r["tag"] for r in conn.execute("SELECT tag FROM node_tags WHERE node_id = ? ORDER BY tag_folded", (node_id,))]


def for_ids(conn, ids) -> dict:
    """{node id: [tags]} for many items (one query per 500)."""
    ids = list(dict.fromkeys(ids))
    out: dict = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT node_id, tag FROM node_tags WHERE node_id IN ({q}) ORDER BY tag_folded", chunk):
            out.setdefault(r["node_id"], []).append(r["tag"])
    return out


def _known_spelling(conn, user: dict, folded: str) -> str | None:
    """How this tag is already spelled on something the person can open (so "divorce" joins their "Divorce") —
    never a spelling from items they can't open (that would say the tag exists there)."""
    acc_sql, acc_params = sharing.access_cte(user["id"])
    r = conn.execute(acc_sql + "SELECT t.tag FROM node_tags t JOIN acc ON acc.node_id = t.node_id "
                     f"JOIN nodes n ON n.id = t.node_id WHERE t.tag_folded = ? AND {nodes.LIVE} "
                     "ORDER BY t.added_at LIMIT 1", (*acc_params, folded)).fetchone()
    return r["tag"] if r else None


def change(conn, user: dict, node_id: str, *, tags=None, add=None, remove=None, color="keep") -> dict:
    """Replace the tags (`tags`), or add / remove some; set or clear the colour (`color`, "keep" = unchanged).
    Editors only. Returns {tags, color}."""
    node, _role = sharing.require(conn, user, node_id, "editor")
    moving.guard()
    current = {fold(t): t for t in of(conn, node_id)}
    if tags is not None:
        if not isinstance(tags, list):
            raise HTTPException(422, "Send the tags as a list.")
        wanted = {}
        for t in tags:
            c = clean_tag(t)
            wanted.setdefault(fold(c), c)
    else:
        wanted = dict(current)
        for t in add or []:
            c = clean_tag(t)
            wanted.setdefault(fold(c), c)
        for t in remove or []:
            wanted.pop(fold(clean_tag(t)), None)
    if len(wanted) > MAX_TAGS:
        raise HTTPException(422, f"An item can have at most {MAX_TAGS} tags.")
    for f in set(current) - set(wanted):
        conn.execute("DELETE FROM node_tags WHERE node_id = ? AND tag_folded = ?", (node_id, f))
    for f in set(wanted) - set(current):
        spelling = _known_spelling(conn, user, f) or wanted[f]
        conn.execute("INSERT INTO node_tags (node_id, tag, tag_folded, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
                     (node_id, spelling, f, user["id"], config.now_iso()))
    if color != "keep":
        conn.execute("UPDATE nodes SET color = ? WHERE id = ?", (clean_colour(color), node_id))
    if set(wanted) != set(current) or color != "keep":
        db.audit(conn, "tags_changed", user["id"], node_id, node["root_id"])
    row = nodes.get(conn, node_id)
    return {"tags": of(conn, node_id), "color": row["color"]}


def listing(conn, user: dict) -> list[dict]:
    """Every tag on something the person can open (live), with how many items carry it."""
    acc_sql, acc_params = sharing.access_cte(user["id"])
    rows = conn.execute(acc_sql + "SELECT MIN(t.tag) AS tag, t.tag_folded AS f, COUNT(DISTINCT t.node_id) AS n "
                        "FROM node_tags t JOIN acc ON acc.node_id = t.node_id JOIN nodes n ON n.id = t.node_id "
                        "JOIN roots r ON r.id = n.root_id "
                        f"WHERE {nodes.LIVE} AND (r.kind = 'person' OR r.missing = 0) "
                        "GROUP BY t.tag_folded ORDER BY t.tag_folded", acc_params).fetchall()
    return [{"tag": r["tag"], "count": r["n"]} for r in rows]


def tag_sql(value: str) -> tuple[str, list]:
    """The search condition for one tag (an item carrying it)."""
    return "n.id IN (SELECT node_id FROM node_tags WHERE tag_folded = ?)", [fold(" ".join(value.strip().lstrip("#").split()))]
