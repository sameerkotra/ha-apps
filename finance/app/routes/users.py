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
from ..common import ha_notify
from ..db import get_db
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
from ..template_filters import install as _install_filters
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_install_filters(templates.env)
templates.env.globals["app_version"] = APP_VERSION


def users_tab(request: Request, current: User, acting: User, message: str = "", error: str = ""):
    """GET admin-storage?tab=users (wired from routes/admin_storage.py)."""
    with get_db() as conn:
        known = [dict(r) for r in conn.execute(
            "SELECT id, name, last_seen_at FROM known_users ORDER BY name COLLATE NOCASE")]
        grants = [dict(r) for r in conn.execute(
            "SELECT a.viewer_id, a.owner_id, a.granted_at, COALESCE(k.name, a.owner_id) AS owner_name "
            "FROM user_access a LEFT JOIN known_users k ON k.id = a.owner_id ORDER BY a.granted_at, a.owner_id")]
        notify = [dict(r) for r in conn.execute("SELECT user_id, service FROM user_notify ORDER BY service")]
    names = {u["id"]: u["name"] for u in known}
    for u in known:
        u["notify"] = [n["service"] for n in notify if n["user_id"] == u["id"]]
        u["is_admin"] = is_admin_identity(u["id"], u["name"])
        u["owners"] = [g for g in grants if g["viewer_id"] == u["id"]]
        taken = {g["owner_id"] for g in u["owners"]}
        u["can_add"] = [o for o in known if o["id"] != u["id"] and o["id"] not in taken]
    # Grants whose viewer isn't in known_users any more still show, so they can be removed.
    orphans = [g for g in grants if g["viewer_id"] not in names]
    # The address carries codes and user ids, never the sentences (a link someone made can't put its
    # own text on this page); the names come from known_users.
    params = request.query_params
    viewer_name = names.get(params.get("viewer", ""), "")
    owner_name = names.get(params.get("owner", ""), "")
    message = {"granted": f"{viewer_name} can now use {owner_name}'s data." if viewer_name and owner_name else "",
               "removed": "Access removed.", "notify_added": "Notify service added.",
               "notify_removed": "Notify service removed."}.get(message, "")
    error = {"pick_two": "Pick two different users from the list.",
             "notify_bad": "That isn't a notify service name: it must look like notify.mobile_app_phone.",
             "viewer_admin": f"{viewer_name} is an admin and uses the user switch instead." if viewer_name else ""
             }.get(error, "")
    return templates.TemplateResponse(request, "storage_users.html", {
        "user": acting, "acting_qs": _acting_qs(current, acting), "acting_as_banner": _acting_banner(current, acting),
        "active_page": "admin_storage", "tab": "users", "users": known, "orphans": orphans,
        "people": [{"id": u["id"], "name": u["name"]} for u in known],      # the Data dialogs' import targets
        "notify_choices": _notify_choices(),
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
            return _back(current, acting, error="pick_two")
        if is_admin_identity(viewer, found[viewer]):
            return _back(current, acting, error="viewer_admin", viewer=viewer)
        conn.execute("INSERT OR IGNORE INTO user_access (viewer_id, owner_id, granted_by) VALUES (?, ?, ?)",
                     (viewer, owner, current.id))
    return _back(current, acting, message="granted", viewer=viewer, owner=owner)


@router.post("/user-access-remove")
def user_access_remove(viewer: str = Form(""), owner: str = Form(""),
                       current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM user_access WHERE viewer_id = ? AND owner_id = ?", (viewer, owner))
    return _back(current, acting, message="removed")


def _notify_choices() -> list[str]:
    """Home Assistant's notify services for the picker (cached a minute by ha_notify), or [] if it can't be asked."""
    try:
        found = ha_notify.list_notify_services_blocking()
    except ha_notify.NotifyListError:
        return []
    return found["services"] + found["entities"]


@router.post("/user-notify-add")
def user_notify_add(user_id: str = Form(""), service: str = Form(""),
                    current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    """An extra notify service for one person (statement reminders, SPEC.md section 27), on top of the phones Home
    Assistant links to them."""
    try:
        service = ha_notify.normalize_service(service)
    except ValueError:
        return _back(current, acting, error="notify_bad")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM known_users WHERE id = ?", (user_id,)).fetchone() is None:
            return _back(current, acting, error="pick_two")
        conn.execute("INSERT OR IGNORE INTO user_notify (user_id, service, created_by) VALUES (?, ?, ?)",
                     (user_id, service, current.id))
    return _back(current, acting, message="notify_added")


@router.post("/user-notify-remove")
def user_notify_remove(user_id: str = Form(""), service: str = Form(""),
                       current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    with get_db() as conn:
        conn.execute("DELETE FROM user_notify WHERE user_id = ? AND service = ?", (user_id, service))
    return _back(current, acting, message="notify_removed")
