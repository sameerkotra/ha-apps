"""The page's API (spec/SPEC.md §9): the launcher's sites, "How the app sees you", and for admins the sites' form,
App settings, People and Storage."""
import os

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .. import auth, config, db, probe, settings, sidebar, sites
from ..auth import get_current_user, require_admin
from ..common import auth_core, backup_core
from ..common import whoami as whoami_core

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------------------------- everyone

@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": {"id": user["id"], "name": user["name"], "isAdmin": user["is_admin"],
                     "enabled": user["enabled"]},
            "noAdmin": auth.no_admins(), "version": config.APP_VERSION, "page": sidebar.PAGE}


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """"How the app sees you": what Home Assistant sent and whether it matched admin_users (never the list)."""
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_USERS),
        display_name_only=(not user["is_admin"]) and auth_core.display_name_listed(user["name"], config.ADMIN_USERS))


@router.get("/sites")
def my_sites(user: dict = Depends(get_current_user)):
    """The sites this person may open (the launcher)."""
    return {"sites": [sites.public(s) for s in sites.for_user(user)]}


# --------------------------------------------------------------------------------------------- admin: sites

def _site_error(e: Exception):
    if isinstance(e, LookupError):
        raise HTTPException(404, str(e) or "No such site.")
    raise HTTPException(422, str(e))


@router.get("/admin/sites")
def admin_sites(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        people = [{"id": r["id"], "name": r["name"]} for r in
                  conn.execute("SELECT id, name FROM users ORDER BY name COLLATE NOCASE")]
    return {"sites": sites.admin_list(), "people": people}


@router.post("/admin/sites")
def admin_add_site(body: sites.SiteIn, admin: dict = Depends(require_admin)):
    try:
        return sites.save(body, admin)
    except (sites.SiteError, LookupError) as e:
        _site_error(e)


@router.put("/admin/sites/{site_id}")
def admin_edit_site(site_id: int, body: sites.SiteIn, admin: dict = Depends(require_admin)):
    try:
        return sites.save(body, admin, site_id)
    except (sites.SiteError, LookupError) as e:
        _site_error(e)


@router.delete("/admin/sites/{site_id}")
def admin_delete_site(site_id: int, admin: dict = Depends(require_admin)):
    if not sites.delete(site_id):
        raise HTTPException(404, "No such site.")
    return {"ok": True}


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(max_length=sites.MAX_SITES)


@router.post("/admin/sites/reorder")
def admin_reorder(body: Order, admin: dict = Depends(require_admin)):
    sites.reorder(body.ids)
    return {"sites": sites.admin_list()}


@router.get("/sites/{slug}/favicon")
def site_favicon(slug: str, user: dict = Depends(get_current_user)):
    """A site's own icon (stored by an admin), for the people who may open the site. Raster images only; served so
    the browser can never treat it as a page."""
    site = sites.get(slug)
    icon = sites.favicon(slug) if site is not None and sites.can_open(user, site) else None
    if icon is None:
        raise HTTPException(404, "No icon.")
    data, ctype = icon
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=86400",
                                                     "Content-Security-Policy": "sandbox; default-src 'none'",
                                                     "X-Content-Type-Options": "nosniff"})


# --------------------------------------------------------------------------------------------- admin: the Test

class TestIn(sites.SiteIn):
    trusted_ok: bool = True                     # the Test doesn't need the confirmation; saving does


@router.post("/admin/sites/test")
async def admin_test_form(body: TestIn, id: int | None = Query(None), admin: dict = Depends(require_admin)):
    """The Test on the form as it is (not saved)."""
    try:
        site = sites.preview(body, id)
    except sites.SiteError as e:
        raise HTTPException(422, str(e))
    return await probe.run(site)


def _saved(site_id: int) -> sites.Site:
    site = sites.by_id(site_id)
    if site is None:
        raise HTTPException(404, "No such site.")
    return site


@router.post("/admin/sites/{site_id}/test")
async def admin_test_site(site_id: int, admin: dict = Depends(require_admin)):
    return await probe.run(_saved(site_id))


class TrustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(min_length=10, max_length=200)


@router.post("/admin/sites/{site_id}/certificate")
async def admin_trust_certificate(site_id: int, body: TrustIn, admin: dict = Depends(require_admin)):
    """Trust the certificate the site presents now — only if it is the one the admin saw (its fingerprint)."""
    site = _saved(site_id)
    try:
        info = await probe.certificate(site)
    except Exception as e:                      # noqa: BLE001 — an address problem, said in words
        raise HTTPException(422, str(e))
    if info is None:
        raise HTTPException(422, "The site didn't present a certificate.")
    if info["fingerprint"].replace(":", "").upper() != body.fingerprint.replace(":", "").upper():
        raise HTTPException(409, "The site now presents a different certificate. Run the Test again.")
    sites.set_certificate(site_id, info["pem"], info["fingerprint"])
    return {"fingerprint": info["fingerprint"]}


@router.delete("/admin/sites/{site_id}/certificate")
def admin_forget_certificate(site_id: int, admin: dict = Depends(require_admin)):
    try:
        sites.set_certificate(site_id, None, None)
    except LookupError:
        raise HTTPException(404, "No such site.")
    return {"ok": True}


@router.post("/admin/sites/{site_id}/favicon")
async def admin_fetch_favicon(site_id: int, admin: dict = Depends(require_admin)):
    """Fetch the site's own icon and keep it (a small PNG, ICO, GIF, JPEG or WebP; never SVG)."""
    icon = await probe.fetch_icon(_saved(site_id))
    if icon is None:
        raise HTTPException(422, "The site has no icon this app can use (a small PNG, ICO, GIF, JPEG or WebP).")
    sites.set_favicon(site_id, *icon)
    return {"ok": True, "type": icon[1], "bytes": len(icon[0])}


@router.get("/admin/sites/{site_id}/log")
def admin_site_log(site_id: int, limit: int = Query(200, ge=1, le=1000), admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT l.at, l.method, l.path, l.status, l.bytes, u.name FROM access_log l "
                            "LEFT JOIN users u ON u.id = l.user_id WHERE l.site_id = ? ORDER BY l.at DESC LIMIT ?",
                            (site_id, limit)).fetchall()
    return {"log": [dict(r) for r in rows]}


# --------------------------------------------------------------------------------------------- admin: settings

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return settings.payload()


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    return settings.payload()


# --------------------------------------------------------------------------------------------- admin: people

@router.get("/admin/people")
def admin_people(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name, username, enabled FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return {"people": [{"id": r["id"], "name": r["name"], "username": r["username"], "enabled": bool(r["enabled"])}
                       for r in rows]}


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.put("/admin/people/{user_id}")
def admin_person(user_id: str, body: PersonIn, admin: dict = Depends(require_admin)):
    if user_id == admin["id"] and not body.enabled:
        raise HTTPException(422, "You can't turn yourself off.")
    with db.get_conn() as conn:
        if not conn.execute("UPDATE users SET enabled = ? WHERE id = ?", (int(body.enabled), user_id)).rowcount:
            raise HTTPException(404, "No such person.")
    auth.forget_cached()
    return admin_people(admin)


# --------------------------------------------------------------------------------------------- admin: storage

def _scrub(conn) -> None:
    settings.REGISTRY.scrub_secrets(conn)
    db.blank_header_values(conn)


@router.get("/admin-storage-download-db")
def download_db(admin: dict = Depends(require_admin)):
    """A full copy of the database; the sites' extra header values (credentials) are blanked in it."""
    tmp_path = db.backup_to_tempfile(after=_scrub)
    return backup_core.send_file(tmp_path, backup_core.file_name("household-proxy-backup", ".db", now=config.now()))


@router.post("/admin-storage-import-db")
async def import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """Replaces the whole database with a downloaded backup (validated first). The extra header values this
    install has are kept for the sites that still exist (a backup carries none)."""
    with db.get_conn() as conn:
        kept = [(r["site_id"], r["name"], r["value"]) for r in
                conn.execute("SELECT site_id, name, value FROM site_headers WHERE value != ''")]
    tmp_path = await backup_core.receive(file, config.DATA_DIR)
    try:
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.import_from_tempfile(tmp_path)
        with db.get_conn() as conn:
            conn.executemany("UPDATE site_headers SET value = ? WHERE site_id = ? AND name = ? AND value = ''",
                             [(v, s, n) for s, n, v in kept])
        sites.invalidate()
        auth.forget_cached()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"status": "ok"}
