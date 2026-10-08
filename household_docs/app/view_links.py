"""View links (SPEC §6.6): "Anyone in the household with the link can view".

The owner or a manager of an item in someone's folder turns on a link for it. Whoever opens the link — signed in to
Home Assistant like everyone else, since the app is reached only through Home Assistant — gets Can view on that
item: a share to them marked `via_link`, so the item is in their Shared with me from then on and every other rule
(folders inside, Activity, search) is the usual one. People who can open it already just open it.

Turning the link off removes it and every Can view it gave (shares added by hand stay). A new link has a new
address; the old one stops working. Children's accounts (§17.20) get nothing from a link, as with Everyone shares.
Items in admin shared folders go by the folder's access and have no links.
"""
import secrets

from fastapi import HTTPException

from . import config, db, sharing

TOKEN_BYTES = 16                       # 22 URL-safe characters
GONE = "This link doesn't work any more — it was turned off, or the item was deleted. Ask whoever sent it."


def get(conn, node_id: str):
    return conn.execute("SELECT * FROM view_links WHERE node_id = ?", (node_id,)).fetchone()


def link_json(conn, node) -> dict | None:
    row = get(conn, node["id"])
    if row is None:
        return None
    by = sharing.user_row(conn, row["created_by"])
    n = conn.execute("SELECT COUNT(*) FROM shares WHERE node_id = ? AND via_link = 1", (node["id"],)).fetchone()[0]
    return {"token": row["token"], "createdAt": row["created_at"], "by": by["name"] if by else None, "opened": n}


def _may_manage(conn, actor_role: str | None, node) -> None:
    if not sharing.at_least(actor_role, "manager"):
        raise HTTPException(403, "Only its owner or a manager can make or turn off its link.")
    if sharing.owner_id(conn, node) is None:
        raise HTTPException(409, "Items in admin shared folders are shared through the folder's access, not by a link.")
    if node["trash_id"] is not None:
        raise HTTPException(409, "It's in the Trash.")


def make(conn, actor: dict, actor_role: str | None, node, *, new: bool = False) -> dict:
    """Turn the link on (or keep the one there). `new`: a fresh address; the old one stops working."""
    _may_manage(conn, actor_role, node)
    row = get(conn, node["id"])
    if row is not None and not new:
        return link_json(conn, node)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    conn.execute("INSERT INTO view_links (node_id, token, created_by, created_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(node_id) DO UPDATE SET token = excluded.token, created_by = excluded.created_by, "
                 "created_at = excluded.created_at", (node["id"], token, actor["id"], config.now_iso()))
    db.audit(conn, "view_link_new" if row is not None else "view_link_on", actor["id"], node["id"], node["root_id"])
    return link_json(conn, node)


def remove(conn, actor: dict, actor_role: str | None, node) -> int:
    """Turn the link off; returns how many Can view shares it had given (now removed)."""
    _may_manage(conn, actor_role, node)
    if get(conn, node["id"]) is None:
        raise HTTPException(404, "There's no link to turn off.")
    conn.execute("DELETE FROM view_links WHERE node_id = ?", (node["id"],))
    n = conn.execute("DELETE FROM shares WHERE node_id = ? AND via_link = 1", (node["id"],)).rowcount
    db.audit(conn, "view_link_off", actor["id"], node["id"], node["root_id"])
    return n


def open_link(conn, user: dict, token: str):
    """The item the link is for, given Can view to this person first if they couldn't open it. 404 otherwise."""
    if not token or len(token) > 64:
        raise HTTPException(404, GONE)
    row = conn.execute("SELECT * FROM view_links WHERE token = ?", (token,)).fetchone()
    node = nodes_get(conn, row["node_id"]) if row is not None else None
    if node is None or node["trash_id"] is not None or node["gone_at"] is not None or user.get("disabled"):
        raise HTTPException(404, GONE)
    if sharing.role_of(conn, user, node) is not None:
        return node
    if sharing.is_child_id(conn, user["id"]) or sharing.owner_id(conn, node) is None:
        raise HTTPException(404, GONE)
    maker = sharing.user_row(conn, row["created_by"])               # its maker must still be able to share it
    if maker is None or not sharing.at_least(sharing.role_of(conn, dict(maker), node), "manager"):
        raise HTTPException(404, GONE)
    conn.execute("INSERT INTO shares (node_id, user_id, role, viewers_tick, added_by, added_at, via_link) "
                 "VALUES (?, ?, 'viewer', 1, ?, ?, 1) ON CONFLICT(node_id, user_id) DO NOTHING",
                 (node["id"], user["id"], row["created_by"], config.now_iso()))
    db.audit(conn, "view_link_opened", user["id"], node["id"], node["root_id"])
    return node


def nodes_get(conn, node_id: str):
    return conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
