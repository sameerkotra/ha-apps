"""Sharing (SPEC §6.3, §11): who an item is shared with, add or change a share, remove one. New shares with a
person send them a notification ("Alex shared 'Trip 2026' with you") unless they turned that off."""
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import Field

from .. import db, notify, sharing
from ..auth import require_user
from .common import Strict

router = APIRouter(prefix="/api/nodes", tags=["shares"])


@router.get("/{node_id}/shares")
def get_shares(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        out = sharing.shares_json(conn, node, user)
        out.update(role=role, canShare=sharing.at_least(role, "manager") and out["owner"]["id"] is not None,
                   canMakeManagers=role == "owner", kind=node["kind"], name=node["name"])
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
