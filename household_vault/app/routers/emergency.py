"""Emergency access routes (SPEC §12.4)."""
from fastapi import APIRouter, Depends
from pydantic import Field

from .. import db, emergency
from .common import Ctx, Strict, unlocked, vault_access

router = APIRouter(prefix="/api", tags=["emergency"])


@router.get("/emergency")
def overview(ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        return emergency.overview(conn, ctx.session)


class ContactIn(Strict):
    userId: str = Field(min_length=1, max_length=100)
    waitDays: int = Field(default=7, ge=1, le=30)


@router.post("/emergency/contacts")
def add_contact(body: ContactIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        emergency.add_contact(conn, ctx.session, body.userId, body.waitDays)
        return emergency.overview(conn, ctx.session)


@router.delete("/emergency/contacts/{uid}")
def remove_contact(uid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        emergency.remove_contact(conn, ctx.user["id"], uid, ctx.user["id"])
        return emergency.overview(conn, ctx.session)


@router.post("/emergency/contacts/{uid}/deny")
def deny(uid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        emergency.deny(conn, ctx.user["id"], uid)
        return emergency.overview(conn, ctx.session)


@router.post("/emergency/contacts/{uid}/approve")
def approve(uid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        emergency.approve(conn, ctx.user["id"], uid)
        return emergency.overview(conn, ctx.session)


@router.post("/emergency/{owner_id}/request")
def request_access(owner_id: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        emergency.request(conn, ctx.user["id"], owner_id)
        return emergency.overview(conn, ctx.session)


class FlagIn(Strict):
    include: bool


@router.put("/vaults/{vid}/items/{iid}/emergency")
def flag_item(vid: str, iid: str, body: FlagIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vault_access(conn, ctx, vid, "own")
        emergency.set_flag(conn, ctx.session, vid, iid, body.include)
        return {"include": body.include}
