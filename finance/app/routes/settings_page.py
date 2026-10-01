"""Admin → App settings: app-wide settings, admin only (SPEC.md section 21).

Values live in the app_settings table (app/settings.py) and are read on every use, so saving here
changes the app at once — no app restart.
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import ai_client as _ai_client

from ..auth import User, get_acting_user, require_admin
from ..version import APP_VERSION
from .. import ai_client, settings
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_privacy"] = _ai_client.privacy

TEST_TIMEOUT = 10


def _page(request, current: User, acting: User, *, values: dict, errors=None, saved=False):
    return templates.TemplateResponse(request, "settings.html", {
        "user": acting, "acting_as_banner": _acting_banner(current, acting), "acting_qs": _acting_qs(current, acting),
        "active_page": "settings", "groups": settings.GROUPS, "defs": settings.SETTINGS, "values": values,
        "meta": settings.meta(), "errors": errors or [], "saved": saved, "key_hint": settings.api_key_hint(),
        "ai_ready": settings.ai_configured(), "default_urls": settings.DEFAULT_URLS,
    })


@router.get("/settings")
async def settings_page(request: Request, saved: str = "", current: User = Depends(require_admin),
                        acting: User = Depends(get_acting_user)):
    values = await asyncio.to_thread(settings.all_values)
    return _page(request, current, acting, values=values, saved=saved == "1")


@router.post("/settings")
async def settings_save(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    form = await request.form()
    keys = list(settings.SETTINGS) + [f"clear_{k}" for k, s in settings.SETTINGS.items() if s.kind == "secret"]
    values = {k: str(form.get(k) or "") for k in keys if k in form}
    errors = await asyncio.to_thread(settings.save, values, current.id)
    if errors:
        shown = dict(await asyncio.to_thread(settings.all_values),
                     **{k: v for k, v in values.items() if k in settings.SETTINGS and settings.SETTINGS[k].kind != "secret"})
        for key, s in settings.SETTINGS.items():   # an unticked box isn't sent at all
            if s.kind == "bool":
                shown[key] = "1" if values.get(key) in ("1", "on", "true") else "0"
        return _page(request, current, acting, values=shown, errors=errors)
    qs = _acting_qs(current, acting)
    return RedirectResponse(url="settings?saved=1" + (f"&{qs}" if qs else ""), status_code=303)


def _test(cfg: ai_client.Config) -> tuple[bool, str]:
    """Ask the provider which models it offers: quick, loads nothing, costs nothing, never disturbs a PDF."""
    if not cfg.url:
        return False, "Enter the address first."
    try:
        names = ai_client.list_models(cfg, timeout=TEST_TIMEOUT)
    except ai_client.AIError as err:
        return False, str(err)
    shown = ", ".join(names[:40]) + (f" … and {len(names) - 40} more" if len(names) > 40 else "")
    if not cfg.model:
        return True, f"{cfg.label} answered. Models: {shown or 'none listed'}."
    if cfg.model in names or f"{cfg.model}:latest" in names:
        return True, f"{cfg.label} answered and offers {cfg.model}."
    if not names:
        return True, f"{cfg.label} answered (it doesn't list its models, so {cfg.model} couldn't be checked)."
    return False, f"{cfg.label} answered but doesn't list {cfg.model}. Models: {shown}."


@router.post("/settings-test")
async def settings_test(request: Request, current: User = Depends(require_admin)):
    """Tests what is typed on the page (before saving); a blank key field means the saved key."""
    form = await request.form()
    provider, err = settings.clean("ai_provider", str(form.get("ai_provider") or settings.ai_provider()))
    url, err2 = settings.clean("ai_url", str(form.get("ai_url") or ""))
    if err or err2:
        ok, message = False, err or err2
    else:
        key = str(form.get("ai_api_key") or "").strip()
        if not key and form.get("clear_ai_api_key") != "1":
            key = await asyncio.to_thread(settings.get, "ai_api_key")
        cfg = ai_client.Config(provider=provider, url=(url or settings.DEFAULT_URLS[provider]).rstrip("/"),
                               model=str(form.get("ai_model") or "").strip(), api_key=key)
        ok, message = await asyncio.to_thread(_test, cfg)
    return templates.TemplateResponse(request, "_settings_test.html", {"ok": ok, "message": message})
