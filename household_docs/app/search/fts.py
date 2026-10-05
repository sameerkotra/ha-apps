"""The search index (SPEC §5.4 `fts`): one row per node with its name and searchable text. FTS5 when the SQLite
has it; otherwise a plain table searched with LIKE (db.has_fts). The index is rebuilt from the files at any
time (store/index.py); it is never the only copy of anything."""
import re
import unicodedata

from .. import db


def fold(text: str) -> str:
    """Lower case without accents (names are matched on this)."""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def fold_map(text: str) -> tuple[str, list[int]]:
    """fold(text) and, for each character of it, the index of the character of `text` it came from (to put a
    match found in the folded name back on the name as written)."""
    out, idx = [], []
    for i, ch in enumerate(text or ""):
        f = fold(ch)
        out.append(f)
        idx.extend([i] * len(f))
    return "".join(out), idx


def set_entry(conn, node_id: str, name: str, body: str | None) -> None:
    conn.execute("DELETE FROM fts WHERE node_id = ?", (node_id,))
    conn.execute("INSERT INTO fts (node_id, name, body) VALUES (?, ?, ?)", (node_id, name, body or ""))


def set_name(conn, node_id: str, name: str) -> None:
    r = conn.execute("SELECT body FROM fts WHERE node_id = ?", (node_id,)).fetchone()
    set_entry(conn, node_id, name, r["body"] if r else "")


def remove(conn, node_ids) -> None:
    ids = list(node_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        conn.execute(f"DELETE FROM fts WHERE node_id IN ({','.join('?' * len(chunk))})", chunk)


_WORD = re.compile(r"\w+", re.UNICODE)


def words(q: str) -> list[str]:
    return [w for w in _WORD.findall(q or "")][:12]


def match_expr(ws: list[str]) -> str:
    """Every word, each as a prefix: "tri pla" → "tri"* "pla"* (quoted, so nothing is FTS syntax)."""
    return " ".join('"' + w.replace('"', '""') + '"*' for w in ws)


def content_condition(conn, ws: list[str]) -> tuple[str, list]:
    """A WHERE fragment over `fts` (aliased f) matching every word in the body, and its parameters."""
    if db.has_fts(conn):
        return "f.body MATCH ?", [match_expr(ws)]   # column filter: only the body
    return " AND ".join("f.body LIKE ? ESCAPE '\\'" for _ in ws), ["%" + like_escape(w) + "%" for w in ws]


def like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
