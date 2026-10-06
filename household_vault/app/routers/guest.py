"""Guest Wi-Fi: the dashboard sensor and the no-unlock page (guest_wifi.py)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db, guest_wifi, service
from ..auth import require_user
from .common import Ctx, Strict, unlocked, vault_access

router = APIRouter(prefix="/api", tags=["guest-wifi"])


@router.get("/guest-wifi")
def show(user: dict = Depends(require_user)):
    """For anyone with access — no unlock needed (it's on the dashboard anyway)."""
    with db.get_conn() as conn:
        u = service.user_row(conn, user["id"])
        if u["status"] != "active":
            raise HTTPException(403, "Only people who are set up can see this.")
        row = guest_wifi.current(conn)
    if row is None:
        return {"published": False}
    out = {"published": True, "ssid": row["ssid"], "security": row["security"],
           "picture": guest_wifi.picture(row), "vaultId": row["vault_id"], "itemId": row["item_id"],
           "showPassword": bool(row["show_password"]), "entity": guest_wifi.ENTITY}
    # the password as text only when the publisher ticked "show the password as text too" (SPEC §12.9) — the
    # QR code has to contain it to work, but text is one copy more (a screenshot, a copied field)
    if row["show_password"] and row["security"] != "nopass":
        out["password"] = row["password"]
    return out


class PublishIn(Strict):
    security: str = Field(default="WPA", pattern="^(WPA|WEP|nopass)$")
    showPassword: bool = False


@router.put("/vaults/{vid}/items/{iid}/guest-wifi")
def publish(vid: str, iid: str, body: PublishIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vault_access(conn, ctx, vid, "edit")
        guest_wifi.publish(conn, ctx.user, vid, iid, body.security, body.showPassword)
    return {"ok": True}


@router.delete("/guest-wifi")
def unpublish(ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        row = guest_wifi.current(conn)
        if row is None:
            return {"ok": True}
        vault_access(conn, ctx, row["vault_id"], "edit", must_be_open=False)
        guest_wifi.unpublish(conn, ctx.user["id"])
    return {"ok": True}
