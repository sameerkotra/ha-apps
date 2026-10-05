"""Saved and recent searches (SPEC §10.6). Each person's own: a saved search keeps the box (`query`) and the page's
options and filters (`filters`, JSON); pinned ones show in the sidebar. Recent searches are the last RECENT_KEPT
searches someone ran (the same search again moves to the top). Never anyone else's."""
import json

from fastapi import HTTPException

from .. import config, db

MAX_SAVED = 50
RECENT_KEPT = 20
MAX_NAME = 80


def _filters_json(filters) -> str:
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise HTTPException(422, "filters must be an object.")
    text = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    if len(text) > 4000:
        raise HTTPException(422, "Those filters are too long to keep.")
    return text


def _row_json(r) -> dict:
    try:
        f = json.loads(r["filters"] or "{}")
    except ValueError:
        f = {}
    out = {"q": r["query"], "filters": f if isinstance(f, dict) else {}}
    for k in ("id", "name", "pinned", "created_at", "used_at"):
        if k in r.keys():
            out[{"created_at": "createdAt", "used_at": "usedAt"}.get(k, k)] = bool(r[k]) if k == "pinned" else r[k]
    return out


def listing(conn, user_id: str) -> list[dict]:
    return [_row_json(r) for r in conn.execute(
        "SELECT * FROM saved_searches WHERE user_id = ? ORDER BY pinned DESC, name COLLATE NOCASE", (user_id,))]


def pinned(conn, user_id: str) -> list[dict]:
    return [x for x in listing(conn, user_id) if x["pinned"]]


def clean_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(422, "Give the search a name.")
    name = " ".join(name.split())
    if len(name) > MAX_NAME:
        raise HTTPException(422, f"A name can be at most {MAX_NAME} characters.")
    return name


def save(conn, user_id: str, name, q, filters, pinned_flag: bool = False) -> dict:
    name = clean_name(name)
    q = str(q or "")[:400]
    f = _filters_json(filters)
    if not q.strip() and f == "{}":
        raise HTTPException(422, "There's nothing to save yet — search for something first.")
    n = conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ?", (user_id,)).fetchone()[0]
    if n >= MAX_SAVED:
        raise HTTPException(409, f"You can keep {MAX_SAVED} saved searches — delete one first.")
    sid = db.new_id()
    conn.execute("INSERT INTO saved_searches (id, user_id, name, query, filters, pinned, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (sid, user_id, name, q, f, 1 if pinned_flag else 0, config.now_iso()))
    return _row_json(conn.execute("SELECT * FROM saved_searches WHERE id = ?", (sid,)).fetchone())


def _mine(conn, user_id: str, sid: str):
    r = conn.execute("SELECT * FROM saved_searches WHERE id = ? AND user_id = ?", (sid, user_id)).fetchone()
    if r is None:
        raise HTTPException(404, "That saved search isn't there.")
    return r


def change(conn, user_id: str, sid: str, name=None, pinned_flag=None) -> dict:
    _mine(conn, user_id, sid)
    if name is not None:
        conn.execute("UPDATE saved_searches SET name = ? WHERE id = ?", (clean_name(name), sid))
    if pinned_flag is not None:
        conn.execute("UPDATE saved_searches SET pinned = ? WHERE id = ?", (1 if pinned_flag else 0, sid))
    return _row_json(_mine(conn, user_id, sid))


def delete(conn, user_id: str, sid: str) -> None:
    _mine(conn, user_id, sid)
    conn.execute("DELETE FROM saved_searches WHERE id = ?", (sid,))


def record_recent(conn, user_id: str, q, filters) -> None:
    q = str(q or "").strip()[:400]
    f = _filters_json(filters)
    if not q and f == "{}":
        return
    conn.execute("INSERT INTO recent_searches (user_id, query, filters, used_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id, query, filters) DO UPDATE SET used_at = excluded.used_at",
                 (user_id, q, f, config.now_iso()))
    conn.execute("DELETE FROM recent_searches WHERE user_id = ? AND rowid NOT IN (SELECT rowid FROM recent_searches "
                 "WHERE user_id = ? ORDER BY used_at DESC, rowid DESC LIMIT ?)", (user_id, user_id, RECENT_KEPT))


def recent(conn, user_id: str) -> list[dict]:
    return [_row_json(r) for r in conn.execute(
        "SELECT * FROM recent_searches WHERE user_id = ? ORDER BY used_at DESC, rowid DESC LIMIT ?", (user_id, RECENT_KEPT))]


def clear_recent(conn, user_id: str) -> None:
    conn.execute("DELETE FROM recent_searches WHERE user_id = ?", (user_id,))
