"""Sharing (SPEC §6.3, §11): who an item is shared with, add or change a share, remove one. New shares with a
person send them a notification ("Alex shared 'Trip 2026' with you") unless they turned that off."""
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import Field

from .. import db, notify, sharing, view_links
from ..auth import require_user
from .common import Strict

router = APIRouter(prefix="/api/nodes", tags=["shares"])
links_router = APIRouter(prefix="/api/view-links", tags=["shares"])


@router.get("/{node_id}/shares")
def get_shares(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        out = sharing.shares_json(conn, node, user)
        can_share = sharing.at_least(role, "manager") and out["owner"]["id"] is not None
        out.update(role=role, canShare=can_share, canMakeManagers=role == "owner", kind=node["kind"], name=node["name"],
                   link=view_links.link_json(conn, node) if can_share else None)
        return out


class ShareIn(Strict):
    userId: str = Field(min_length=1, max_length=128)       # a person's id, or "*" for Everyone
    role: str = Field(pattern="^(viewer|editor|manager)$")
    viewersTick: bool | None = None


@router.post("/{node_id}/shares")
def add_share(node_id: str, body: ShareIn, background: BackgroundTasks, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        new = sharing.set_share(conn, user, role, node, body.userId, body.role, body.viewersTick)
        out = sharing.shares_json(conn, node, user)
    if new and body.userId != sharing.EVERYONE:
        background.add_task(notify.send_shared, body.userId, user["name"], node["name"])
    return out


@router.delete("/{node_id}/shares/{target}")
def remove_share(node_id: str, target: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        sharing.remove_share(conn, user, role, node, target)
        return sharing.shares_json(conn, node, user)


class LinkIn(Strict):
    new: bool = False                     # a fresh address: the old one stops working


@router.post("/{node_id}/link")
def make_link(node_id: str, body: LinkIn, user: dict = Depends(require_user)):
    """Anyone with the link can view (§6.6): turn it on, or give it a new address."""
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        return {"link": view_links.make(conn, user, role, node, new=body.new)}


@router.delete("/{node_id}/link")
def remove_link(node_id: str, user: dict = Depends(require_user)):
    """Turn the link off: it stops working and the Can view it gave goes too."""
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        n = view_links.remove(conn, user, role, node)
        out = sharing.shares_json(conn, node, user)
        out["removed"] = n
        return out


@links_router.get("/{token}")
def open_link(token: str, user: dict = Depends(require_user)):
    """Opening a view link: the item it is for (and Can view on it, for someone who couldn't open it before)."""
    with db.get_conn() as conn:
        node = view_links.open_link(conn, user, token)
        return {"id": node["id"], "kind": node["kind"], "name": node["name"]}
