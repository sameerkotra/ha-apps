"""Trash (SPEC §7.4). Deleting moves the file or folder to `.trash/<owner folder>/<trash id>/` with an
`.item.json` beside it (original path, node id, who, when); its nodes stay in the index, marked with the trash
id, so shares, ticks and history come back on restore. Restore puts it back into the folder it was in (that same
folder, by id — never another folder now at the old path), or the top of the owner's folder (or the admin shared
folder) when that folder is gone or in Trash, with " (restored)" added on a name clash. Emptied after `trash_days`, or by the
owner (Empty Trash); then the files, their versions and their nodes go for good.
"""
import json
import os
import shutil

from .. import config, db, settings
from . import nodes, paths, roots, versions

TRASH_REL_PREFIX = ".trash/"     # a trashed node's rel while in Trash: hidden, so never a real path in a root


def _owner_folder(conn, root) -> str:
    if root["kind"] != "person":
        return "_shared"
    r = conn.execute("SELECT folder FROM users WHERE id = ?", (root["user_id"],)).fetchone()
    return r["folder"] if r and r["folder"] else "_person"


def trash_node(conn, user_id: str, node) -> str:
    """Move a live node (and everything inside it) to Trash. Returns the trash id."""
    from . import fileio
    fileio.ensure_writable()
    root, real = nodes.real_path(conn, node)
    if not os.path.lexists(real):
        raise FileNotFoundError(real)
    tid = db.new_id()
    folder = _owner_folder(conn, root)
    base = roots.hidden_dir(root, ".trash")
    tdir = os.path.join(base, folder, tid)
    os.makedirs(tdir)
    os.rename(real, os.path.join(tdir, node["name"]))
    item = {"name": node["name"], "originalPath": node["rel"], "nodeId": node["id"], "rootId": root["id"],
            "deletedBy": user_id, "deletedAt": config.now_iso()}
    with open(os.path.join(tdir, ".item.json"), "w", encoding="utf-8") as f:
        json.dump(item, f, indent=1)
    ids = [node["id"]] + nodes.descendant_ids(conn, node)
    nodes.relocate(conn, node, root_id=root["id"], rel=f"{TRASH_REL_PREFIX}{tid}/{node['name']}", parent_id=node["parent_id"])
    q = ",".join("?" * len(ids))
    conn.execute(f"UPDATE nodes SET trash_id = ? WHERE id IN ({q})", (tid, *ids))
    conn.execute("INSERT INTO trash (id, node_id, root_id, original_rel, trash_rel, owner_id, deleted_by, deleted_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (tid, node["id"], root["id"], node["rel"], f"{folder}/{tid}",
                                                      roots.owner_of(root), user_id, item["deletedAt"]))
    db.audit(conn, "deleted", user_id, node["id"], root["id"])
    from .. import activity
    activity.record(conn, "deleted", user_id, node)
    return tid


def _tdir(conn, root, t) -> str:
    base = roots.hidden_dir(root, ".trash")
    p = os.path.realpath(os.path.join(base, t["trash_rel"]))
    if not paths.inside(os.path.realpath(base), p):
        raise paths.PathError("That Trash item isn't where it should be.")
    return p


def restore(conn, user_id: str, t) -> str:
    """Put a Trash item back. Returns the node id."""
    root = roots.root_row(conn, t["root_id"])
    base = roots.root_real(root)
    node = nodes.get(conn, t["node_id"], live=False)
    if node is None:
        raise FileNotFoundError(t["id"])
    tdir = _tdir(conn, root, t)
    src = os.path.join(tdir, node["name"])
    if not os.path.lexists(src):
        raise FileNotFoundError(src)
    # Back into the folder it was in — that same folder (by id, wherever it is now), never whatever now sits at
    # the old path (another folder, perhaps shared with Everyone); else the top of the owner's folder.
    parent, prel = None, ""
    if node["parent_id"]:
        p = nodes.get(conn, node["parent_id"])
        if p is not None and p["kind"] == "folder" and p["root_id"] == root["id"]:
            try:
                ok = os.path.isdir(paths.resolve(base, p["rel"]))
            except paths.PathError:
                ok = False
            if ok:
                parent, prel = p, p["rel"]
    folder_real = paths.resolve(base, prel)
    name = paths.unique_name(folder_real, node["name"], suffix_word="restored")
    rel = paths.join_rel(prel, name)
    dest = paths.resolve(base, rel)
    nodes.free_rel(conn, root["id"], rel)
    os.rename(src, dest)
    ids = [node["id"]] + nodes.descendant_ids(conn, node)
    nodes.relocate(conn, node, root_id=root["id"], rel=rel, parent_id=parent["id"] if parent else None)
    q = ",".join("?" * len(ids))
    conn.execute(f"UPDATE nodes SET trash_id = NULL, gone_at = NULL WHERE id IN ({q})", ids)
    conn.execute("DELETE FROM trash WHERE id = ?", (t["id"],))
    shutil.rmtree(tdir, ignore_errors=True)
    db.audit(conn, "restored", user_id, node["id"], root["id"])
    from .. import activity
    activity.record_id(conn, "restored", user_id, node["id"])
    return node["id"]


def _purge_item(conn, t) -> None:
    root = roots.root_row(conn, t["root_id"])
    node = nodes.get(conn, t["node_id"], live=False) if t["node_id"] else None
    ids = []
    if node is not None:
        ids = [node["id"]] + nodes.descendant_ids(conn, node)
    if root is not None:
        try:
            shutil.rmtree(_tdir(conn, root, t), ignore_errors=True)
        except (paths.PathError, roots.DocsUnavailable, roots.FolderMissing):
            return
        for nid in ids:
            versions.remove_all(root, nid)
    nodes.delete_rows(conn, ids)
    conn.execute("DELETE FROM trash WHERE id = ?", (t["id"],))


def listing(conn, owner_id: str) -> list:
    return conn.execute("SELECT t.*, n.name, n.kind, n.size, u.name AS deleted_by_name FROM trash t "
                        "LEFT JOIN nodes n ON n.id = t.node_id LEFT JOIN users u ON u.id = t.deleted_by "
                        "WHERE t.owner_id = ? ORDER BY t.deleted_at DESC", (owner_id,)).fetchall()


def listing_root(conn, root_id: str) -> list:
    """An admin shared folder's Trash (its own hidden .trash): what people with Read and write deleted there."""
    return conn.execute("SELECT t.*, n.name, n.kind, n.size, u.name AS deleted_by_name FROM trash t "
                        "LEFT JOIN nodes n ON n.id = t.node_id LEFT JOIN users u ON u.id = t.deleted_by "
                        "WHERE t.root_id = ? AND t.owner_id IS NULL ORDER BY t.deleted_at DESC", (root_id,)).fetchall()


def empty(conn, owner_id: str) -> int:
    from . import fileio
    fileio.ensure_writable()
    rows = conn.execute("SELECT * FROM trash WHERE owner_id = ?", (owner_id,)).fetchall()
    for t in rows:
        _purge_item(conn, t)
    return len(rows)


def purge_old(days: int | None = None) -> int:
    """Housekeeping: empty items older than `trash_days` (paused while the documents are moved, §5.7)."""
    from . import moving
    if moving.is_read_only():
        return 0
    with db.get_conn() as conn:
        days = days if days is not None else int(settings.get("trash_days", conn))
        cutoff = config.ago_iso(days=days)
        rows = conn.execute("SELECT * FROM trash WHERE deleted_at < ?", (cutoff,)).fetchall()
        for t in rows:
            _purge_item(conn, t)
    return len(rows)
