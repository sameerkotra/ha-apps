"""Password health and the breach check (SPEC §12.1)."""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db, health, service, sessions, settings
from .common import Ctx, Strict, unlocked
from .me import _vault_list

router = APIRouter(prefix="/api", tags=["health"])


def open_vaults(conn, ctx: Ctx) -> tuple:
    rows = _vault_list(conn, ctx.user["id"], ctx.session)
    out, locked = [], []
    for vr in rows:
        ov = sessions.get_open(vr["id"]) if vr["open"] else None
        if ov is None:
            locked.append(vr["name"])
            continue
        out.append(({"id": vr["id"], "name": vr["name"]}, ov))
    return out, locked


def breach_state(conn, ctx: Ctx) -> dict:
    allowed = bool(settings.all_values(conn)["allow_breach_check"])
    enabled = bool(service.user_row(conn, ctx.user["id"])["breach_check"])
    return {"allowed": allowed, "enabled": allowed and enabled}


def _require_breach(conn, ctx: Ctx) -> None:
    b = breach_state(conn, ctx)
    if not b["allowed"]:
        raise HTTPException(403, "An admin hasn't allowed the breach check (Admin → App settings).")
    if not b["enabled"]:
        raise HTTPException(403, "Turn on the breach check in Settings first.")


@router.get("/password-health")
def password_health(ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vaults, locked = open_vaults(conn, ctx)
        b = breach_state(conn, ctx)
    rep = health.report(vaults, ctx.session.token if b["enabled"] else None)
    rep["lockedVaults"] = locked
    rep["breach"] = dict(b, **health.status(ctx.session.token))
    rep["oldDays"] = health.OLD_DAYS
    return rep


@router.post("/password-health/breach-check")
def start_breach_check(ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        _require_breach(conn, ctx)
        vaults, _ = open_vaults(conn, ctx)
    hashes = set()
    for _, ov in vaults:
        with ov.lock:
            hashes |= {f["sha1"] for f in health.facts(ov)}
    health.start(ctx.session.token, hashes)
    return health.status(ctx.session.token)


class OneIn(Strict):
    password: str = Field(min_length=1, max_length=1000)


@router.post("/breach/check")
def check_one(body: OneIn, ctx: Ctx = Depends(unlocked)):
    """Check one password (the editor asks when you type a new one). Only its hash prefix leaves the app."""
    with db.get_conn() as conn:
        _require_breach(conn, ctx)
    h = hashlib.sha1(body.password.encode()).hexdigest().upper()
    return {"count": health.check_hashes({h})[h]}
