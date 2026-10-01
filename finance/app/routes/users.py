"""Storage → Users tab: who has opened the app, and shared access between regular users (SPEC.md section 20).

Shared access lets a regular user (the viewer) use another user's data (the owner) with full
rights — e.g. a household member sees the statements already uploaded instead of uploading them
twice. The viewer opens straight into the first owner's data. Admins don't need it: they use
the user switch. Admin only.
"""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_acting_user, is_admin_identity, require_admin
from ..db import get_db
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def users_tab(request: Request, current: User, acting: User, message: str = "", error: str = ""):
    """GET admin-storage?tab=users (wired from routes/admin_storage.py)."""
    with get_db() as conn:
        known = [dict(r) for r in conn.execute(
            "SELECT id, name, last_seen_at FROM known_users ORDER BY name COLLATE NOCASE")]
        grants = [dict(r) for r in conn.execute(
            "SELECT a.viewer_id, a.owner_id, a.granted_at, COALESCE(k.name, a.owner_id) AS owner_name "
            "FROM user_access a LEFT JOIN known_users k ON k.id = a.owner_id ORDER BY a.granted_at, a.owner_id")]
    names = {u["id"]: u["name"] for u in known}
    for u in known:
        u["is_admin"] = is_admin_identity(u["id"], u["name"])
        u["owners"] = [g for g in grants if g["viewer_id"] == u["id"]]
        taken = {g["owner_id"] for g in u["owners"]}
        u["can_add"] = [o for o in known if o["id"] != u["id"] and o["id"] not in taken]
    # Grants whose viewer isn't in known_users any more still show, so they can be removed.
    orphans = [g for g in grants if g["viewer_id"] not in names]
    return templates.TemplateResponse(request, "storage_users.html", {
        "user": acting, "acting_qs": _acting_qs(current, acting), "acting_as_banner": _acting_banner(current, acting),
        "active_page": "admin_storage", "tab": "users", "users": known, "orphans": orphans,
        "message": message, "error": error})


def _back(current: User, acting: User, **params) -> RedirectResponse:
    qs = _acting_qs(current, acting)
    query = {"tab": "users", **params}
    if qs:
        query["as_user"] = qs.split("=", 1)[1]
    return RedirectResponse("admin-storage?" + urlencode(query), status_code=303)


@router.post("/user-access-grant")
def user_access_grant(viewer: str = Form(""), owner: str = Form(""),
                      current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    with get_db() as conn:
        found = {r["id"]: r["name"] for r in conn.execute(
            "SELECT id, name FROM known_users WHERE id IN (?, ?)", (viewer, owner))}
        if viewer not in found or owner not in found or viewer == owner:
            return _back(current, acting, error="Pick two different users from the list.")
        if is_admin_identity(viewer, found[viewer]):
            return _back(current, acting, error=f"{found[viewer]} is an admin and uses the user switch instead.")
        conn.execute("INSERT OR IGNORE INTO user_access (viewer_id, owner_id, granted_by) VALUES (?, ?, ?)",
                     (viewer, owner, current.id))
    return _back(current, acting, message=f"{found[viewer]} can now use {found[owner]}'s data.")


@router.post("/user-access-remove")
def user_access_remove(viewer: str = Form(""), owner: str = Form(""),
                       current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM user_access WHERE viewer_id = ? AND owner_id = ?", (viewer, owner))
    return _back(current, acting, message="Access removed.")
