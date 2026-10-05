"""Nodes: the index rows for files and folders (SPEC §5.4), and what the API says about them.

A node's location is its root plus `rel`; its id never changes — renames and moves (in the app, or outside it
matched by inode, §5.5) update `rel`, so shares, ticks, favourites and versions (keyed by id) follow.
"""
import os

from .. import config
from ..search import fts
from . import kinds, paths, roots

LIVE = "n.gone_at IS NULL AND n.trash_id IS NULL"


def get(conn, node_id: str, *, live: bool = True):
    if not isinstance(node_id, str) or len(node_id) > 64:
        return None
    sql = "SELECT * FROM nodes n WHERE n.id = ?" + (f" AND {LIVE}" if live else "")
    return conn.execute(sql, (node_id,)).fetchone()


def by_rel(conn, root_id: str, rel: str):
    return conn.execute("SELECT * FROM nodes WHERE root_id = ? AND rel = ?", (root_id, rel)).fetchone()


def real_path(conn, node) -> tuple[object, str]:
    """(root row, the node's real path) — realpath-checked inside its root on every call."""
    root = roots.root_row(conn, node["root_id"])
    base = roots.root_real(root)
    real = paths.resolve(base, node["rel"])
    roots.check_unmarked(base, real)
    return root, real


def folder_real(conn, root, folder) -> str:
    """The real path of a folder node, or of the root itself when `folder` is None."""
    base = roots.root_real(root)
    if folder is None:
        return base
    real = paths.resolve(base, folder["rel"])
    roots.check_unmarked(base, real)
    return real


def children(conn, root_id: str, parent_id: str | None):
    if parent_id is None:
        return conn.execute(f"SELECT * FROM nodes n WHERE n.root_id = ? AND n.parent_id IS NULL AND {LIVE}",
                            (root_id,)).fetchall()
    return conn.execute(f"SELECT * FROM nodes n WHERE n.parent_id = ? AND {LIVE}", (parent_id,)).fetchall()


def ancestors(conn, node) -> list:
    """The folders above `node`, top first."""
    out = []
    pid = node["parent_id"]
    seen = set()
    while pid and pid not in seen:
        seen.add(pid)
        p = conn.execute("SELECT * FROM nodes WHERE id = ?", (pid,)).fetchone()
        if p is None:
            break
        out.append(p)
        pid = p["parent_id"]
    out.reverse()
    return out


def descendant_ids(conn, node) -> list[str]:
    if node["kind"] != "folder":
        return []
    return [r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE root_id = ? AND substr(rel, 1, ?) = ?",
        (node["root_id"], len(node["rel"]) + 1, node["rel"] + "/"))]


def name_parts(name: str) -> tuple[str, str | None]:
    _stem, ext = paths.split_ext(name)
    return fts.fold(name), (ext or None)


def insert(conn, *, root_id: str, rel: str, parent_id: str | None, kind: str, st=None, sha=None,
           created_by=None, updated_by=None, node_id: str | None = None, body: str | None = None,
           stats: dict | None = None) -> str:
    from .. import db
    nid = node_id or db.new_id()
    name = paths.base_name(rel)
    folded, ext = name_parts(name)
    if kind == "folder":
        ext = None
    conn.execute(
        "INSERT INTO nodes (id, root_id, rel, parent_id, name, name_folded, ext, kind, size, mtime, mtime_ns, ctime, "
        "inode, dev, sha256, created_by, updated_by, content_indexed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (nid, root_id, rel, parent_id, name, folded, ext, kind,
         None if (st is None or kind == "folder") else st.st_size,
         config.iso_of(st.st_mtime) if st is not None else None, st.st_mtime_ns if st is not None else None,
         config.now_iso(), st.st_ino if st is not None else None, st.st_dev if st is not None else None,
         sha, created_by, updated_by, 1 if body is not None else 0))
    fts.set_entry(conn, nid, name, body)
    if stats:
        set_stats(conn, nid, stats)
    return nid


def set_stats(conn, node_id: str, stats: dict) -> None:
    """The columns a kind keeps for search (kinds.stats_for*): a checklist's open and ticked items."""
    cols = [c for c in kinds.STAT_COLUMNS if c in stats]
    if cols:
        conn.execute(f"UPDATE nodes SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                     (*[stats[c] for c in cols], node_id))


def update_stat(conn, node_id: str, st, sha, *, updated_by=None, kind: str | None = None,
                body: str | None = None, set_body: bool = False, stats: dict | None = None) -> None:
    sets = ["size = ?", "mtime = ?", "mtime_ns = ?", "inode = ?", "dev = ?", "sha256 = ?", "updated_by = ?"]
    vals = [st.st_size if kind != "folder" else None, config.iso_of(st.st_mtime), st.st_mtime_ns, st.st_ino,
            st.st_dev, sha, updated_by]
    if kind is not None:
        sets.append("kind = ?")
        vals.append(kind)
    if set_body:
        sets.append("content_indexed = ?")
        vals.append(1 if body is not None else 0)
    conn.execute(f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", (*vals, node_id))
    if set_body:
        r = conn.execute("SELECT name FROM nodes WHERE id = ?", (node_id,)).fetchone()
        fts.set_entry(conn, node_id, r["name"], body)
    if stats is not None:
        set_stats(conn, node_id, stats)


def relocate(conn, node, *, root_id: str, rel: str, parent_id: str | None) -> None:
    """Point `node` (and, for a folder, everything inside it) at a new place: same ids, new rel / root."""
    old_rel, old_root = node["rel"], node["root_id"]
    name = paths.base_name(rel)
    folded, ext = name_parts(name)
    if node["kind"] == "folder":
        ext = None
    if node["kind"] == "folder":
        inner = conn.execute("SELECT id, rel FROM nodes WHERE root_id = ? AND substr(rel, 1, ?) = ?",
                             (old_root, len(old_rel) + 1, old_rel + "/")).fetchall()
        # two steps, so a rename to a name that sorts inside the old one never collides on UNIQUE(root_id, rel)
        for r in inner:
            conn.execute("UPDATE nodes SET rel = ? WHERE id = ?", (".moving/" + r["id"], r["id"]))
    conn.execute("UPDATE nodes SET root_id = ?, rel = ?, parent_id = ?, name = ?, name_folded = ?, ext = ? WHERE id = ?",
                 (root_id, rel, parent_id, name, folded, ext, node["id"]))
    if node["kind"] == "folder":
        for r in inner:
            conn.execute("UPDATE nodes SET root_id = ?, rel = ? WHERE id = ?", (root_id, rel + r["rel"][len(old_rel):], r["id"]))
    fts.set_name(conn, node["id"], name)


def delete_rows(conn, ids) -> None:
    """Remove index rows for good (their shares, ticks, favourites and version rows go with them); children
    first, so parent links never dangle."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return
    rows = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += conn.execute(f"SELECT id, rel FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    rows.sort(key=lambda r: r["rel"].count("/"), reverse=True)
    idset = {r["id"] for r in rows}
    for r in rows:
        conn.execute("UPDATE nodes SET parent_id = NULL WHERE parent_id = ? AND id NOT IN (SELECT value FROM json_each(?))",
                     (r["id"], _json(list(idset))))
        conn.execute("DELETE FROM nodes WHERE id = ?", (r["id"],))
    fts.remove(conn, idset)


def _json(v) -> str:
    import json
    return json.dumps(v)


def free_rel(conn, root_id: str, rel: str) -> None:
    """Before something new takes `rel`: drop a stale row there (a node found gone from disk)."""
    r = by_rel(conn, root_id, rel)
    if r is not None and (r["gone_at"] is not None or r["trash_id"] is not None):
        delete_rows(conn, [r["id"]] + descendant_ids(conn, r))


def kind_info(kind_id: str) -> dict:
    k = kinds.get(kind_id)
    return {"label": k.label, "document": k.document, "icon": k.icon}


def roots_info(conn) -> dict:
    """root id → (kind, label) for every root (a small table)."""
    return {r["id"]: (r["kind"], r["label"]) for r in conn.execute("SELECT id, kind, label FROM roots")}


def parent_ref(row, kinds_of: dict) -> str | None:
    """How the browser names the folder an item is in: its parent's id, "root:<id>" at the top of an admin
    shared folder, None at the top of a person's folder."""
    if row["parent_id"]:
        return row["parent_id"]
    kind = kinds_of.get(row["root_id"], ("person",))[0]
    return "root:" + row["root_id"] if kind == "shared" else None


def display_path(conn, node) -> str:
    root = roots.root_row(conn, node["root_id"])
    return config.share_display(paths.join_rel(root["path"], node["rel"]))


def node_json(row, *, role: str | None = None, extra: dict | None = None) -> dict:
    out = {"id": row["id"], "name": row["name"], "kind": row["kind"], "ext": row["ext"], "size": row["size"],
           "modified": row["mtime"], "rootId": row["root_id"], "parentId": row["parent_id"],
           "createdBy": row["created_by"], "updatedBy": row["updated_by"],
           "outside": row["created_by"] is None, "document": kinds.is_document(row["kind"]),
           "preview": bool(row["preview"]) if "preview" in row.keys() else False,
           "color": row["color"] if "color" in row.keys() else None}
    if role is not None:
        out["role"] = role
    if extra:
        out.update(extra)
    return out


def stat_quietly(path: str):
    try:
        return os.stat(path)
    except OSError:
        return None
