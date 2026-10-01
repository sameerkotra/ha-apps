"""The printable household sheet (SPEC §12.3): chosen fields of chosen
entries, for Household and your own Personal vault. Card numbers, CVVs, PINs
and 2FA secrets are never printable."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import alerts, db, items, service
from .common import Ctx, Strict, unlocked, vault_access
from .items import _rate_limit_secret

router = APIRouter(prefix="/api", tags=["sheet"])


def _sheet_vault(v: dict, ctx: Ctx) -> None:
    if v["kind"] == "household" or (v["kind"] == "personal" and v["owner_user_id"] == ctx.user["id"]):
        return
    raise HTTPException(409, "The household sheet is for the Household vault and your own Personal vault.")


class SheetIn(Strict):
    include: bool
    fields: list[str] = Field(default_factory=list, max_length=30)
    wifi: str | None = Field(default=None, pattern="^(WPA|WEP|nopass)$")


@router.put("/vaults/{vid}/items/{iid}/sheet")
def set_sheet(vid: str, iid: str, body: SheetIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        _sheet_vault(v, ctx)
        with ov.lock:
            e = items.find_entry(ov.db, iid)
            if ov.db.in_recycle_bin(e):
                raise HTTPException(409, "That item is in the Trash.")
            items.set_sheet(ov.db, e, body.include, body.fields, body.wifi)
            service.save(conn, ov, ctx.user["id"])
            return items.sheet_settings(ov.db, e)


@router.get("/vaults/{vid}/sheet")
def get_sheet(vid: str, ctx: Ctx = Depends(unlocked)):
    """Everything to print — secrets included, so it's rate-limited and logged (without contents)."""
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
        _sheet_vault(v, ctx)
        _rate_limit_secret(ctx)
        with ov.lock:
            out = []
            for e in items.all_live_entries(ov.db):
                s = items.sheet_entry(ov.db, e)
                if s:
                    out.append(s)
        out.sort(key=lambda x: x["title"].lower())
        db.audit(conn, "printed_sheet", ctx.user["id"], vid)
        if v["kind"] == "household":
            alerts.send(conn, alerts.others_in(conn, vid, ctx.user["id"]),
                        f"{ctx.user['name']} printed the household sheet (Wi-Fi and other passwords on paper).")
        name = "Household" if v["kind"] == "household" else "Personal"
    return {"vault": name, "items": out}
