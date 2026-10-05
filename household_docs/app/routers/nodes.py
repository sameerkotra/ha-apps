"""Spaces, folders and items (SPEC §6, §7.4, §12): My docs, Shared with me, Everyone, Favourites, Recent, Trash;
a folder's contents; rename, move, copy, transfer, leave, hide, favourite, delete; downloads. Every answer is
checked against the caller's role (sharing.py); anything they can't see is a 404."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import Field

from .. import db, docops, files, notify, share_folders, sharing, tags
from ..auth import require_user
from ..store import fileio, index, moving, nodes, roots, trash
from .common import Strict

router = APIRouter(prefix="/api", tags=["nodes"])
SORTS = {"name": "n.kind != 'folder', n.name_folded", "modified": "n.kind != 'folder', n.mtime DESC",
         "size": "n.kind != 'folder', COALESCE(n.size, 0) DESC, n.name_folded"}
SPACES = ("mine", "shared", "everyone", "favourites", "recent", "trash")


def _names(conn, ids) -> dict:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    return {r["id"]: r["name"] for r in conn.execute(f"SELECT id, name FROM users WHERE id IN ({q})", ids)}


def _state(conn, user_id: str, ids) -> dict:
    ids = list(ids)
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT * FROM user_state WHERE user_id = ? AND node_id IN ({q})", (user_id, *chunk)):
            out[r["node_id"]] = r
    return out


def _shared_ids(conn, ids) -> set:
    ids = list(ids)
    out = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        out |= {r[0] for r in conn.execute(f"SELECT DISTINCT node_id FROM shares WHERE node_id IN ({q})", chunk)}
    return out


# More per-item fields from later steps: fn(conn, user, rows) -> {node id: {field: value}} (follows, HA sensors …)
ROW_EXTRAS: list = []


def rows_json(conn, user: dict, rows, role_for=None, extra=None) -> list[dict]:
    """List rows as the browser sees them: role, favourite, hidden, shared, owner and editor names, which kind of
    root it's in (an admin shared folder: its name) and how to name the folder it's in."""
    rinfo = nodes.roots_info(conn)
    st = _state(conn, user["id"], [r["id"] for r in rows])
    shared = _shared_ids(conn, [r["id"] for r in rows])
    owner_ids = {}
    for r in rows:
        if "owner_id" in r.keys():
            owner_ids[r["id"]] = r["owner_id"]
        else:
            owner_ids[r["id"]] = sharing.owner_id(conn, r)
    names = _names(conn, [r["updated_by"] for r in rows] + list(owner_ids.values()))
    tag_map = tags.for_ids(conn, [r["id"] for r in rows])
    more = [fn(conn, user, rows) for fn in ROW_EXTRAS] if rows else []
    kids_of = children_of(conn, user) if rows else {}
    out = []
    for r in rows:
        s = st.get(r["id"])
        role = role_for(r) if role_for else sharing.role_of(conn, user, r)
        e = {"favourite": bool(s and s["favourite"]), "hidden": bool(s and s["hidden"]),
             "openedAt": s["opened_at"] if s else None, "shared": r["id"] in shared,
             "ownerId": owner_ids[r["id"]], "ownerName": names.get(owner_ids[r["id"]]),
             "updatedByName": names.get(r["updated_by"]),
             "rootKind": rinfo.get(r["root_id"], ("person", ""))[0], "parentRef": nodes.parent_ref(r, rinfo),
             "tags": tag_map.get(r["id"], []), "pinned": bool(s and s["pinned_order"] is not None)}
        if e["rootKind"] == "shared":
            e["rootLabel"] = rinfo[r["root_id"]][1]
        elif e["parentRef"] is None and e["ownerId"] in kids_of:      # the top of a child's My docs (a parent)
            e["parentRef"] = "root:" + r["root_id"]
        for m in more:
            e.update(m.get(r["id"]) or {})
        if extra:
            e.update(extra(r))
        out.append(nodes.node_json(r, role=role, extra=e))
    return out


def _sort_key(sort: str):
    if sort == "modified":
        return lambda x: (x["kind"] != "folder", -(_ts(x["modified"])))
    if sort == "size":
        return lambda x: (x["kind"] != "folder", -(x["size"] or 0), x["name"].casefold())
    return lambda x: (x["kind"] != "folder", x["name"].casefold())


def _ts(iso) -> float:
    from ..config import parse_iso
    t = parse_iso(iso)
    return t.timestamp() if t else 0.0


def children_of(conn, user: dict) -> dict:
    """{child's user id: name} for the children whose My docs this person may view (§17.20)."""
    return {r["id"]: r["name"] for r in conn.execute(
        "SELECT u.id, u.name FROM kid_parents kp JOIN users u ON u.id = kp.child_id WHERE kp.parent_id = ?",
        (user["id"],))}


def visible_rows(conn, user: dict, root, rows) -> list:
    """A parent looking into a child's My docs sees only what was made there while they're a child (§17.20):
    the other rows of a listing are left out. Anyone else sees the folder's rows as they are."""
    if root["kind"] != "person" or root["user_id"] == user["id"] or not sharing.is_parent_of(conn, user["id"], root["user_id"]):
        return rows
    return [r for r in rows if sharing.role_of(conn, user, r) is not None]


def kid_root_crumbs(root, child_name: str) -> list[dict]:
    return [{"id": None, "name": "Kids' docs", "space": "kids"}, {"id": "root:" + root["id"], "name": f"{child_name}'s docs"}]


def crumbs(conn, user: dict, node) -> list[dict]:
    """The way to a folder, as far as the person can see it."""
    chain = nodes.ancestors(conn, node) + [node]
    root = roots.root_row(conn, node["root_id"])
    if root["kind"] == "person" and root["user_id"] != user["id"]:
        kids_of = children_of(conn, user)
        if root["user_id"] in kids_of:
            seen = [n for n in chain if sharing.role_of(conn, user, n) is not None]
            return kid_root_crumbs(root, kids_of[root["user_id"]]) + [{"id": n["id"], "name": n["name"]} for n in seen]
    if root["kind"] == "shared":
        return ([{"id": None, "name": "Shared folders", "space": "folders"}, {"id": "root:" + root["id"], "name": root["label"]}]
                + [{"id": n["id"], "name": n["name"]} for n in chain])
    if sharing.owner_id(conn, node) == user["id"]:
        return [{"id": None, "name": "My docs", "space": "mine"}] + [{"id": n["id"], "name": n["name"]} for n in chain]
    seen = [n for n in chain if sharing.role_of(conn, user, n) is not None]
    return [{"id": None, "name": "Shared with me", "space": "shared"}] + [{"id": n["id"], "name": n["name"]} for n in seen]


# The same for an admin shared folder's top ("root:<id>"): fn(conn, user, ref) -> {field: value}
ROOT_EXTRAS: list = []


def root_head(root, role: str) -> dict:
    """An admin shared folder's top, as the folder view shows it."""
    return {"id": "root:" + root["id"], "name": root["label"], "kind": "folder", "rootKind": "shared",
            "rootId": root["id"], "rootLabel": root["label"], "role": role, "isRoot": True, "parentRef": None,
            "favourite": False, "shared": False, "document": False, "outside": False}


def _mode(root, role) -> str | None:
    if root["kind"] != "shared":
        return None
    return "rw" if sharing.at_least(role, "editor") else "ro"


@router.get("/list")
def list_folder(node: str | None = Query(default=None, max_length=70), sort: str = Query(default="name"),
                user: dict = Depends(require_user)):
    """A folder's contents (no content), after a quick look at the folder on disk. No node: My docs;
    "root:<id>": the top of an admin shared folder."""
    with db.get_conn() as conn:
        root, folder, role = docops.place(conn, user, node, "viewer")
        root_id, rel = root["id"], folder["rel"] if folder else ""
        if root["kind"] == "shared":
            roots.root_real(root)                     # 404 while its storage is away
    if root["kind"] == "shared" or roots.is_ok():
        index.scan_folder(root_id, rel)
    with db.get_conn() as conn:
        if folder is not None:
            folder = nodes.get(conn, folder["id"])
            if folder is None:
                raise HTTPException(404, sharing.NOT_FOUND)
        rows = visible_rows(conn, user, root, nodes.children(conn, root_id, folder["id"] if folder else None))
        items = rows_json(conn, user, rows, role_for=lambda _r: role)
        items.sort(key=_sort_key(sort))
        head = None
        if folder is not None:
            documents_touch(conn, user["id"], folder["id"])
            head = rows_json(conn, user, [folder], role_for=lambda _r: role)[0]
            head["canShare"] = (sharing.at_least(role, "manager") and sharing.owner_id(conn, folder) is not None
                               and not user.get("is_child"))
            cr = crumbs(conn, user, folder)
        elif root["kind"] == "shared":
            head = root_head(root, role)
            for fn in ROOT_EXTRAS:
                head.update(fn(conn, user, head["id"]) or {})
            cr = [{"id": None, "name": "Shared folders", "space": "folders"}, {"id": head["id"], "name": root["label"]}]
        elif root["user_id"] != user["id"]:                          # a parent at the top of a child's My docs
            name = children_of(conn, user).get(root["user_id"], "")
            head = dict(root_head(root, role), name=f"{name}'s docs", rootKind="person", rootLabel=None, kidsRoot=True)
            cr = kid_root_crumbs(root, name)
        else:
            cr = [{"id": None, "name": "My docs", "space": "mine"}]
        return {"folder": head, "role": role, "canEdit": sharing.at_least(role, "editor") and not moving.is_read_only(conn),
                "mode": _mode(root, role),
                "ref": docops.place_ref(root, folder, user), "rootKind": root["kind"], "crumbs": cr, "items": items}


def documents_touch(conn, user_id, node_id):
    from ..documents import touch_opened
    touch_opened(conn, user_id, node_id)


@router.get("/folders")
def folders(node: str | None = Query(default=None, max_length=70), user: dict = Depends(require_user)):
    """Folders inside a folder (or My docs, or an admin shared folder's top): the Move and Copy pickers."""
    with db.get_conn() as conn:
        root, folder, role = docops.place(conn, user, node, "viewer")
        rows = visible_rows(conn, user, root, nodes.children(conn, root["id"], folder["id"] if folder else None))
        if folder is not None:
            cr = crumbs(conn, user, folder)
        elif root["kind"] == "shared":
            cr = [{"id": None, "name": "Shared folders", "space": "folders"}, {"id": "root:" + root["id"], "name": root["label"]}]
        elif root["user_id"] != user["id"]:
            cr = kid_root_crumbs(root, children_of(conn, user).get(root["user_id"], ""))
        else:
            cr = [{"id": None, "name": "My docs", "space": "mine"}]
        out = sorted([{"id": r["id"], "name": r["name"]} for r in rows if r["kind"] == "folder"],
                     key=lambda x: x["name"].casefold())
        return {"folders": out, "crumbs": cr, "canEdit": sharing.at_least(role, "editor"),
                "ref": docops.place_ref(root, folder, user), "rootId": root["id"], "rootKind": root["kind"]}


@router.get("/space/{space}")
def space(space: str, hidden: bool = Query(default=False), user: dict = Depends(require_user)):
    if space not in SPACES:
        raise HTTPException(404, "No such space.")
    with db.get_conn() as conn:
        if space == "mine":
            root = docops.my_root(conn, user)
            rows = nodes.children(conn, root["id"], None)
            return {"items": sorted(rows_json(conn, user, rows, role_for=lambda _r: "owner"), key=_sort_key("name"))}
        if space in ("shared", "everyone"):
            rows = (sharing.shared_with_me if space == "shared" else sharing.everyone_items)(conn, user, hidden)
            names = _names(conn, [r["shared_by"] for r in rows])
            items = rows_json(conn, user, rows, extra=lambda r: {"sharedAt": r["shared_at"],
                                                                  "sharedByName": names.get(r["shared_by"])})
            return {"items": items}
        if space == "trash":
            rows = trash.listing(conn, user["id"])
            out = [{"id": t["id"], "nodeId": t["node_id"], "name": t["name"], "kind": t["kind"],
                    "size": t["size"], "originalPath": t["original_rel"], "deletedAt": t["deleted_at"],
                    "deletedByName": t["deleted_by_name"]} for t in rows]
            folders_out = []
            for f in share_folders.for_user(conn, user):
                if f["mode"] != "rw":
                    continue
                items = [{"id": t["id"], "nodeId": t["node_id"], "name": t["name"], "kind": t["kind"], "size": t["size"],
                          "originalPath": t["original_rel"], "deletedAt": t["deleted_at"],
                          "deletedByName": t["deleted_by_name"]} for t in trash.listing_root(conn, f["rootId"])]
                if items:
                    folders_out.append({"rootId": f["rootId"], "label": f["label"], "items": items})
            return {"items": out, "folders": folders_out}
        acc_sql, acc_params = sharing.access_cte(user["id"])
        if space == "favourites":
            rows = conn.execute(acc_sql + f"SELECT n.*, acc.rank AS acc_rank FROM user_state us JOIN nodes n ON n.id = us.node_id "
                                f"JOIN acc ON acc.node_id = n.id WHERE us.user_id = ? AND us.favourite = 1 AND {nodes.LIVE} "
                                "ORDER BY n.name_folded", (*acc_params, user["id"])).fetchall()
        else:
            rows = conn.execute(acc_sql + f"SELECT n.*, acc.rank AS acc_rank FROM user_state us JOIN nodes n ON n.id = us.node_id "
                                f"JOIN acc ON acc.node_id = n.id WHERE us.user_id = ? AND us.opened_at IS NOT NULL "
                                f"AND {nodes.LIVE} ORDER BY us.opened_at DESC LIMIT 50", (*acc_params, user["id"])).fetchall()
        return {"items": rows_json(conn, user, rows, role_for=lambda r: sharing.ROLE_OF_RANK.get(r["acc_rank"]))}


@router.get("/nodes/{node_id}")
def node_info(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        out = rows_json(conn, user, [node], role_for=lambda _r: role)[0]
        out["crumbs"] = crumbs(conn, user, node)
        out["path"] = sharing.visible_path(conn, user, node)
        out["canShare"] = (sharing.at_least(role, "manager") and sharing.owner_id(conn, node) is not None
                           and not user.get("is_child"))
        return out


class NameIn(Strict):
    name: str = Field(min_length=1, max_length=250)


class DestIn(Strict):
    parentId: str | None = Field(default=None, max_length=70)


class UserIn(Strict):
    userId: str = Field(min_length=1, max_length=128)


class FlagIn(Strict):
    value: bool = True


@router.post("/nodes/{node_id}/rename")
def rename(node_id: str, body: NameIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        docops.rename(conn, user, node_id, body.name)
        return node_info_json(conn, user, node_id)


def node_info_json(conn, user, node_id):
    """The item as the caller now sees it — or {id, outOfReach: true} when the change took it out of their reach
    (a manager moving something out of the shared folder, an owner after a transfer keeps Can edit)."""
    node = nodes.get(conn, node_id)
    role = sharing.role_of(conn, user, node)
    if role is None:
        return {"id": node_id, "outOfReach": True}
    return rows_json(conn, user, [node], role_for=lambda _r: role)[0]


@router.post("/nodes/{node_id}/move")
def move(node_id: str, body: DestIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        docops.move(conn, user, node_id, body.parentId)
        return node_info_json(conn, user, node_id)


@router.post("/nodes/{node_id}/copy", status_code=201)
def copy(node_id: str, body: DestIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        nid = docops.copy(conn, user, node_id, body.parentId)
        return node_info_json(conn, user, nid)


@router.post("/nodes/{node_id}/transfer")
def transfer(node_id: str, body: UserIn, background: BackgroundTasks, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        name = sharing.require(conn, user, node_id, "owner")[0]["name"]
        docops.transfer(conn, user, node_id, body.userId)
        out = node_info_json(conn, user, node_id)
    background.add_task(notify.send_shared, body.userId, user["name"], name, transfer=True)
    return out


@router.post("/nodes/{node_id}/leave")
def leave(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        sharing.leave(conn, user, node)
    return {"ok": True}


@router.post("/nodes/{node_id}/hide")
def hide(node_id: str, body: FlagIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        sharing.set_hidden(conn, user, node, body.value)
    return {"ok": True, "hidden": body.value}


@router.post("/nodes/{node_id}/favourite")
def favourite(node_id: str, body: FlagIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        sharing.require(conn, user, node_id, "viewer")
        conn.execute("INSERT INTO user_state (user_id, node_id, favourite) VALUES (?, ?, ?) "
                     "ON CONFLICT(user_id, node_id) DO UPDATE SET favourite = excluded.favourite",
                     (user["id"], node_id, 1 if body.value else 0))
    return {"ok": True, "favourite": body.value}


@router.delete("/nodes/{node_id}")
def delete(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        tid = docops.delete(conn, user, node_id)
    return {"ok": True, "trashId": tid}


@router.post("/trash/{trash_id}/restore")
def restore(trash_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        t = conn.execute("SELECT * FROM trash WHERE id = ?", (trash_id[:64],)).fetchone()
        if t is not None and t["owner_id"] != user["id"]:
            root = roots.root_row(conn, t["root_id"])        # an admin shared folder's Trash: read and write
            if t["owner_id"] is not None or root is None or root["kind"] != "shared" \
                    or not sharing.at_least(sharing.root_role(conn, user, root), "editor"):
                t = None
        if t is None:
            raise HTTPException(404, "That isn't in your Trash.")
        fileio.ensure_writable()
        nid = trash.restore(conn, user["id"], t)
        return node_info_json(conn, user, nid)


@router.post("/trash/empty")
def empty_trash(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        roots.docs_real()
        fileio.ensure_writable()
        n = trash.empty(conn, user["id"])
    return {"ok": True, "removed": n}


@router.get("/nodes/{node_id}/file")
def download(node_id: str, user: dict = Depends(require_user)):
    """The file as a download — never shown inline (SPEC §3.3)."""
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        if node["kind"] == "folder":
            raise HTTPException(422, "Folders download as a .zip.")
        _root, real = nodes.real_path(conn, node)
    return files.download_response(real, node["name"])
