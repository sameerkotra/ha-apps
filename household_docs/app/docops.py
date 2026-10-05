"""Folder operations (SPEC §6.2, §6.3, §7.4): create, rename, move, copy, delete (to Trash), transfer ownership.
Each checks the person's role first (sharing.py), works on the real files through store/ (paths realpath-checked,
writes through fileio), then updates the index in the same transaction so the next scan sees no change.

Ownership: in people folders the owner is the person whose folder it is. Things an editor creates inside
someone's shared folder are written into that person's folder, so that person owns them; `created_by` says
who made them. Transfer moves the item into the new owner's folder (top level); its id stays, so shares,
ticks, favourites and history move with it, and the previous owner keeps Can edit on it (they can leave).
"""
import os
import shutil

from fastapi import HTTPException

from . import activity, config, db, settings, sharing
from .store import fileio, index, kinds, nodes, paths, roots, trash

MAX_DEPTH = 10
MAX_COPY_ENTRIES = 500


def my_root(conn, user: dict):
    root = roots.ensure_person_root(conn, user["id"])
    if root is None:
        raise roots.DocsUnavailable()
    return root


ROOT_REF = "root:"


def place(conn, user: dict, ref: str | None, wanted: str = "viewer"):
    """(root, folder node or None for the root's top, role) of a place named by `ref`: None / "" → your own My
    docs top; "root:<id>" → the top of a root (your own folder, or an admin shared folder you can use); anything
    else → a folder node. 404 when the person can't see it, 403 when they can see it but not do `wanted`."""
    if not ref:
        return my_root(conn, user), None, "owner"
    if ref.startswith(ROOT_REF):
        root = roots.root_row(conn, ref[len(ROOT_REF):][:64])
        role = sharing.root_role(conn, user, root)
        if role is None:
            raise HTTPException(404, sharing.NOT_FOUND)
        if not sharing.at_least(role, wanted):
            raise HTTPException(403, "This shared folder is read only for you." if root["kind"] == "shared"
                                else sharing.DENIED[wanted])
        if root["kind"] == "person" and root["user_id"] == user["id"]:
            root = my_root(conn, user)
        return root, None, role
    node, role = sharing.require(conn, user, ref, wanted)
    if node["kind"] != "folder":
        raise HTTPException(422, "That isn't a folder.")
    return roots.root_row(conn, node["root_id"]), node, role


def place_ref(root, folder, user: dict | None = None) -> str | None:
    """How the browser names a place: a folder's id, "root:<id>" for an admin shared folder's top (or a child's My
    docs top, for a parent), None for your own My docs' top."""
    if folder is not None:
        return folder["id"]
    if root["kind"] == "shared" or (user is not None and root["user_id"] != user["id"]):
        return ROOT_REF + root["id"]
    return None


def target_folder(conn, user: dict, parent_id: str | None):
    """(root, folder node or None for the root's top, role) of a place to create something in: your own My docs
    top when parent_id is None, an admin shared folder's top ("root:<id>", read and write), else a folder you
    can edit."""
    return place(conn, user, parent_id, "editor")


def _depth(rel: str) -> int:
    return rel.count("/") + 1 if rel else 0


def check_quota(conn, root, delta: int) -> None:
    gb = int(settings.get("quota_gb", conn))
    if not gb or root["kind"] != "person" or delta <= 0:
        return
    used = conn.execute("SELECT COALESCE(SUM(size), 0) FROM nodes WHERE root_id = ? AND gone_at IS NULL",
                        (root["id"],)).fetchone()[0]
    if used + delta > gb * 1024 ** 3:
        who = conn.execute("SELECT name FROM users WHERE id = ?", (root["user_id"],)).fetchone()
        raise HTTPException(507, f"{(who['name'] + chr(39) + 's') if who else 'This'} folder is at its limit of {gb} GB. "
                                 "Delete something (and empty Trash) first.")


def doc_name(kind_id: str, name: str, ext: str | None = None) -> str:
    """A document's file name from its title: the kind's extension (or `ext`, one of the kind's) is added when
    it's missing."""
    name = (name or "").strip()
    k = kinds.get(kind_id)
    if k.exts and k.document:
        ext = ext if ext in k.exts else k.exts[0]
        if paths.split_ext(name)[1] != ext:
            name = f"{name}.{ext}"
    return paths.check_name(name)


def create(conn, user: dict, kind: str, name: str, parent_id: str | None, data: bytes | None = None,
           ext: str | None = None) -> str:
    """Make a document or folder. `ext`: which of the kind's file types (a sheet: "xlsx" or "csv")."""
    try:
        k = kinds.get(kind)
    except KeyError:
        raise HTTPException(422, f"Unknown type {kind!r}.")
    if not k.creatable:
        raise HTTPException(422, f"A {k.label.lower()} can't be made here.")
    from . import kids
    kids.refuse_sheet(user, kind)
    try:
        if ext is not None and ext not in k.exts:
            raise HTTPException(422, f"A {k.label.lower()} can be {' or '.join('.' + e for e in k.exts)}.")
        fname = doc_name(kind, name, ext) if kind != "folder" else paths.check_name((name or "").strip())
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    root, folder, _role = target_folder(conn, user, parent_id)
    fileio.ensure_writable()
    base = roots.root_real(root)
    prel = folder["rel"] if folder else ""
    if _depth(prel) + 1 > MAX_DEPTH:
        raise HTTPException(422, f"Folders can be at most {MAX_DEPTH} deep.")
    folder_real = paths.resolve(base, prel)
    if not os.path.isdir(folder_real):
        raise HTTPException(409, "That folder isn't there any more.")
    fname = paths.unique_name(folder_real, fname)
    rel = paths.join_rel(prel, fname)
    dest = paths.resolve(base, rel)
    nodes.free_rel(conn, root["id"], rel)
    pid = folder["id"] if folder else None
    if kind == "folder":
        st = fileio.make_dir(dest)
        nid = nodes.insert(conn, root_id=root["id"], rel=rel, parent_id=pid, kind="folder", st=st,
                           created_by=user["id"], updated_by=user["id"])
    else:
        fext = paths.split_ext(fname)[1]
        content = data if data is not None else (k.new_file(fext) if k.new_file else k.new_content)
        check_quota(conn, root, len(content))
        try:
            st = fileio.write_atomic(root, dest, content, exclusive=True)
        except FileExistsError:
            raise HTTPException(409, "Something with that name appeared just now — try again.")
        if k.info:                 # not a text file (a sheet): its text and columns come from the kind
            limit = int(settings.get("content_index_mb", conn)) * 1024 * 1024
            text = kinds.index_text_for(kind, dest, fname, st.st_size, limit)
            stats = kinds.stats_for(kind, dest, st.st_size, limit)
        else:
            text = content.decode("utf-8", "replace")
            stats = kinds.stats_for_text(kind, text)
        nid = nodes.insert(conn, root_id=root["id"], rel=rel, parent_id=pid, kind=kind, st=st,
                           sha=fileio.sha256_bytes(content), created_by=user["id"], updated_by=user["id"],
                           body=text, stats=stats)
    db.audit(conn, "created", user["id"], nid, root["id"])
    activity.record_id(conn, "created", user["id"], nid)
    return nid


def rename(conn, user: dict, node_id: str, name: str) -> None:
    node, _role = sharing.require(conn, user, node_id, "editor")
    try:
        if kinds.is_document(node["kind"]) and node["ext"]:
            stem, ext = paths.split_ext((name or "").strip())
            new = paths.check_name(((stem if ext == node["ext"] else (name or "").strip())) + "." + node["ext"])
        else:
            new = paths.check_name((name or "").strip())
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    if new == node["name"]:
        return
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        root, real = nodes.real_path(conn, node)
        base = roots.root_real(root)
        prel = paths.parent_rel(node["rel"])
        rel = paths.join_rel(prel, new)
        dest = paths.resolve(base, rel)
        if os.path.lexists(dest) and new.casefold() != node["name"].casefold():
            raise HTTPException(409, f"There is already something called “{new}” there.")
        if any(n.casefold() == new.casefold() and n != node["name"] for n in os.listdir(os.path.dirname(dest))):
            raise HTTPException(409, f"There is already something called “{new}” there.")
        nodes.free_rel(conn, root["id"], rel)
        os.rename(real, dest)
        nodes.relocate(conn, node, root_id=root["id"], rel=rel, parent_id=node["parent_id"])
        if not kinds.is_document(node["kind"]) and node["kind"] != "folder":
            st = os.stat(dest)
            conn.execute("UPDATE nodes SET kind = ? WHERE id = ?",
                         (kinds.kind_for_file(dest, new, st.st_size, node["kind"]), node["id"]))
    db.audit(conn, "renamed", user["id"], node["id"], node["root_id"])
    activity.record_id(conn, "renamed", user["id"], node["id"], detail={"from": node["name"], "to": new})


def _inside(child_rel: str, parent_rel: str) -> bool:
    return child_rel == parent_rel or child_rel.startswith(parent_rel + "/")


def move(conn, user: dict, node_id: str, dest_id: str | None) -> None:
    """Move an item into a folder (`dest_id` a folder id; None or "root:<id>" the top of the item's own root —
    your My docs top for owners and managers, an admin shared folder's top for people who may write there)."""
    node, role = sharing.require(conn, user, node_id, "editor")
    root = roots.root_row(conn, node["root_id"])
    shared_root = root["kind"] == "shared"
    if dest_id and not dest_id.startswith(ROOT_REF):
        dest, _r = sharing.require(conn, user, dest_id, "editor")
        if dest["kind"] != "folder":
            raise HTTPException(422, "Move into a folder.")
        if dest["root_id"] != node["root_id"]:
            raise HTTPException(422, "That folder is in another space. Items move only within the same space — "
                                     "use Make a copy (or, for your own things, Transfer ownership) instead.")
        if node["kind"] == "folder" and _inside(dest["rel"], node["rel"]):
            raise HTTPException(422, "A folder can't be moved into itself.")
    else:
        dest = None
        if dest_id and dest_id[len(ROOT_REF):] != root["id"]:
            raise HTTPException(422, "That folder is in another space. Items move only within the same space — "
                                     "use Make a copy instead.")
        if not shared_root and role not in ("owner", "manager"):
            raise HTTPException(403, "Only its owner or a manager can move this out of the shared folder.")
    if role == "editor" and not shared_root:
        top = sharing.shared_ancestor(conn, user, node)
        if top is None or top["id"] == node["id"]:
            raise HTTPException(403, "Only its owner or a manager can move this.")
        if dest is None or not _inside(dest["rel"], top["rel"]):
            raise HTTPException(403, "You can move this only within the shared folder.")
    prel = dest["rel"] if dest else ""
    if paths.parent_rel(node["rel"]) == prel:
        return
    depth_inside = max([_depth(r["rel"]) - _depth(node["rel"]) for r in conn.execute(
        "SELECT rel FROM nodes WHERE root_id = ? AND (rel = ? OR substr(rel, 1, ?) = ?)",
        (node["root_id"], node["rel"], len(node["rel"]) + 1, node["rel"] + "/"))] or [0])
    if _depth(prel) + 1 + depth_inside > MAX_DEPTH:
        raise HTTPException(422, f"Folders can be at most {MAX_DEPTH} deep.")
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        _root, real = nodes.real_path(conn, node)
        base = roots.root_real(root)
        folder_real = paths.resolve(base, prel)
        name = paths.unique_name(folder_real, node["name"])
        rel = paths.join_rel(prel, name)
        nodes.free_rel(conn, root["id"], rel)
        os.rename(real, paths.resolve(base, rel))
        nodes.relocate(conn, node, root_id=root["id"], rel=rel, parent_id=dest["id"] if dest else None)
    db.audit(conn, "moved", user["id"], node["id"], node["root_id"])
    activity.record_id(conn, "moved", user["id"], node["id"], detail={
        "from": activity.folder_name(conn, node["parent_id"], node["root_id"]),
        "to": dest["name"] if dest else "",
        "fromId": activity.folder_id(node["parent_id"]), "toId": activity.folder_id(dest["id"] if dest else None)})


def copy(conn, user: dict, node_id: str, dest_id: str | None) -> str:
    """Make a copy (viewers too) into My docs or a folder you can edit."""
    node, _role = sharing.require(conn, user, node_id, "viewer")
    root, folder, _r = target_folder(conn, user, dest_id)
    src_root, src = nodes.real_path(conn, node)
    if not os.path.lexists(src):
        raise HTTPException(404, sharing.NOT_FOUND)
    entries = [node] + ([nodes.get(conn, i) for i in nodes.descendant_ids(conn, node)] if node["kind"] == "folder" else [])
    entries = [e for e in entries if e is not None]
    owner = sharing.owner_id(conn, node)
    if owner is not None and owner != user["id"] and sharing.is_parent_of(conn, user["id"], owner):
        # a parent's view of a child's My docs reaches only some items (§17.20): copy only those
        entries = [entries[0]] + [e for e in entries[1:] if sharing.role_of(conn, user, e) is not None]
    from . import kids
    if kids.is_child(user) and any(e["kind"] == "sheet" for e in entries):
        raise HTTPException(403, "Sheets aren't available in Kids' space.")     # no sheets by copying either
    if len(entries) > MAX_COPY_ENTRIES:
        raise HTTPException(413, f"That folder has more than {MAX_COPY_ENTRIES} items — copy smaller parts.")
    check_quota(conn, root, sum((e["size"] or 0) for e in entries))
    fileio.ensure_writable()
    base = roots.root_real(root)
    prel = folder["rel"] if folder else ""
    folder_real = paths.resolve(base, prel)
    name = paths.unique_name(folder_real, node["name"] if root["id"] != node["root_id"] or prel != paths.parent_rel(node["rel"])
                             else _copy_name(node["name"]))
    top_rel = paths.join_rel(prel, name)
    new_ids = {}
    for e in sorted(entries, key=lambda x: x["rel"].count("/")):
        sub = e["rel"][len(node["rel"]):]
        rel = top_rel + sub
        if _depth(rel) > MAX_DEPTH:
            continue
        _r2, s_real = nodes.real_path(conn, e)
        d_real = paths.resolve(base, rel)
        parent_new = new_ids.get(e["parent_id"]) if e["id"] != node["id"] else (folder["id"] if folder else None)
        nodes.free_rel(conn, root["id"], rel)
        if e["kind"] == "folder":
            if not os.path.isdir(s_real):
                continue
            st = fileio.make_dir(d_real)
            new_ids[e["id"]] = nodes.insert(conn, root_id=root["id"], rel=rel, parent_id=parent_new, kind="folder",
                                            st=st, created_by=user["id"], updated_by=user["id"])
        else:
            try:
                with fileio.open_read(s_real) as f:
                    data = f.read()
            except OSError:
                continue
            st = fileio.write_atomic(root, d_real, data)
            limit = int(settings.get("content_index_mb", conn)) * 1024 * 1024
            body = kinds.index_text_for(e["kind"], d_real, paths.base_name(rel), st.st_size, limit)
            new_ids[e["id"]] = nodes.insert(conn, root_id=root["id"], rel=rel, parent_id=parent_new, kind=e["kind"],
                                            st=st, sha=fileio.sha256_bytes(data), created_by=user["id"],
                                            updated_by=user["id"], body=body,
                                            stats=kinds.stats_for(e["kind"], d_real, st.st_size, limit))
    db.audit(conn, "copied", user["id"], new_ids.get(node["id"]), root["id"])
    activity.record_id(conn, "created", user["id"], new_ids[node["id"]])
    return new_ids[node["id"]]


def _copy_name(name: str) -> str:
    stem, ext = paths.split_ext(name)
    return f"{stem} (copy)" + (f".{ext}" if ext else "")


def delete(conn, user: dict, node_id: str) -> str:
    node, role = sharing.require(conn, user, node_id, "editor")
    if role == "editor":
        ids = [node["id"]] + nodes.descendant_ids(conn, node)
        q = ",".join("?" * len(ids))
        if conn.execute(f"SELECT 1 FROM shares WHERE node_id IN ({q}) LIMIT 1", ids).fetchone():
            raise HTTPException(403, "Only its owner or a manager can delete a shared item.")
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        try:
            return trash.trash_node(conn, user["id"], node)
        except FileNotFoundError:
            raise HTTPException(404, "That isn't there any more.")


def transfer(conn, user: dict, node_id: str, target_id: str) -> str:
    """Give an item (and everything inside it) to another person: it moves into their folder."""
    node, role = sharing.require(conn, user, node_id, "owner")
    if target_id == user["id"]:
        raise HTTPException(422, "It's yours already.")
    target = sharing.user_row(conn, target_id)
    if target is None:
        raise HTTPException(404, "That person isn't known to Household Docs.")
    if target["disabled"]:
        raise HTTPException(409, f"{target['name']} can't use Household Docs (an admin has turned it off for them).")
    fileio.ensure_writable()
    new_root = roots.ensure_person_root(conn, target_id)
    if new_root is None:
        raise roots.DocsUnavailable()
    ids = [node["id"]] + nodes.descendant_ids(conn, node)
    q = ",".join("?" * len(ids))
    size = conn.execute(f"SELECT COALESCE(SUM(size), 0) FROM nodes WHERE id IN ({q})", ids).fetchone()[0]
    check_quota(conn, new_root, size)
    with fileio.file_lock(node["id"]):
        _old_root, real = nodes.real_path(conn, node)
        base = roots.root_real(new_root)
        name = paths.unique_name(base, node["name"])
        rel = name
        nodes.free_rel(conn, new_root["id"], rel)
        dest = paths.resolve(base, rel)
        try:
            os.rename(real, dest)
        except OSError:                       # another file system (shouldn't happen inside one docs folder)
            shutil.move(real, dest)
        nodes.relocate(conn, node, root_id=new_root["id"], rel=rel, parent_id=None)
        conn.execute("DELETE FROM shares WHERE node_id = ? AND user_id = ?", (node["id"], target_id))
        conn.execute("INSERT INTO shares (node_id, user_id, role, viewers_tick, added_by, added_at) VALUES (?, ?, 'editor', 1, ?, ?) "
                     "ON CONFLICT(node_id, user_id) DO UPDATE SET role = 'editor'",
                     (node["id"], user["id"], user["id"], config.now_iso()))
    db.audit(conn, "transferred", user["id"], node["id"], new_root["id"])
    activity.record_id(conn, "moved", user["id"], node["id"], detail={"from": "", "to": f"{target['name']}'s folder",
                                                                          "fromId": "", "transfer": True})
    return new_root["id"]


def rescan_parent(node) -> None:
    index.scan_folder(node["root_id"], paths.parent_rel(node["rel"]))
