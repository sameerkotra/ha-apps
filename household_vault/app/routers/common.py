"""Shared bits for the routers: the unlocked-session dependency and vault access checks."""
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import service, sessions
from ..auth import require_user


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ctx:
    def __init__(self, user: dict, session: sessions.Session):
        self.user = user
        self.session = session


def _ctx(x_vault_session: str | None, user: dict, allow_temporary: bool) -> Ctx:
    s = sessions.get(x_vault_session, user)
    if s.must_change and not allow_temporary:
        raise HTTPException(409, "Choose your own master password first.")
    return Ctx(user, s)


async def unlocked(x_vault_session: str | None = Header(default=None), user: dict = Depends(require_user)) -> Ctx:
    return _ctx(x_vault_session, user, False)


async def unlocked_any(x_vault_session: str | None = Header(default=None), user: dict = Depends(require_user)) -> Ctx:
    """Also a first-unlock session (one-time password) — only for choosing the new password."""
    return _ctx(x_vault_session, user, True)


LEVELS = {"view": service.ROLES, "edit": service.EDIT_ROLES,
          "manage": service.MANAGE_ROLES, "own": ("owner",)}


def vault_access(conn, ctx: Ctx, vault_id: str, need: str = "view", must_be_open: bool = True):
    """(open vault or None, vault row as dict, role). 404 when not a member (existence isn't leaked);
    423 when the vault is locked for this session; 403 when the role isn't enough."""
    v = service.vault_row(conn, vault_id)
    role = service.role_of(conn, vault_id, ctx.user["id"]) if v else None
    if v is None or role is None:
        raise HTTPException(404, "That vault doesn't exist.")
    if v["kind"] == "emergency" and need != "view":
        # a mirror of marked Personal items, kept by the server (emergency.py) — nobody edits or shares it by hand
        raise HTTPException(403, "These are emergency items: they're kept in step with the owner's Personal vault.")
    if role not in LEVELS[need]:
        raise HTTPException(403, {"view": "You can't see this vault.", "edit": "You can only view this vault.",
                                  "manage": "Only the vault's owner or a manager can do this.",
                                  "own": "Only the vault's owner can do this."}[need])
    ov = sessions.get_open(vault_id)
    if must_be_open and (ov is None or vault_id not in ctx.session.vaults):
        raise HTTPException(423, "That vault is locked — open it with its password first.")
    return ov, dict(v), role
