"""Setup → Settings (`my-settings`): each person's own settings, starting with My reminders, and Dismiss on an
overdue statement's notice (SPEC.md section 27; the dates and reminders are app/statement_due.py).

Whose settings: the signed-in person's own; an admin acting as someone sees and changes that person's. Someone
using another person's data through shared access still edits their own (never the owner's).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import statement_due
from ..auth import User, get_acting_user, get_current_user
from ..db import get_db
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def _whose(current: User, acting: User) -> User:
    return acting if (current.is_admin or acting.id == current.id) else current


def _page(request: Request, current: User, acting: User, *, form=None, error: str = "", saved: bool = False):
    whose = _whose(current, acting)
    with get_db() as conn:
        prefs = statement_due.get_prefs(conn, whose.id)
        phones = statement_due.phones_for(conn, whose.id)
    shown = form or prefs or {"mode": "off", "offsets": [], "repeat_days": 0, "at_time": statement_due.DEFAULT_AT}
    return templates.TemplateResponse(request, "my_settings.html", {
        "user": acting, "acting_as_banner": _acting_banner(current, acting), "acting_qs": _acting_qs(current, acting),
        "active_page": "my_settings", "whose": whose, "own": whose.id == current.id,
        "prefs": prefs, "shown": shown, "error": error, "saved": saved, "has_phone": bool(phones),
        "enabled": statement_due.enabled(), "offset_choices": statement_due.OFFSET_CHOICES,
        "repeat_choices": statement_due.REPEAT_CHOICES, "max_offsets": statement_due.MAX_OFFSETS,
    }, status_code=400 if error else 200)


@router.get("/my-settings")
def my_settings(request: Request, saved: str = "", current: User = Depends(get_current_user),
                acting: User = Depends(get_acting_user)):
    return _page(request, current, acting, saved=saved == "1")


@router.post("/my-settings-reminders")
async def save_reminders(request: Request, current: User = Depends(get_current_user),
                         acting: User = Depends(get_acting_user)):
    form = await request.form()
    raw = {"mode": str(form.get("mode") or ""), "offsets": [str(v) for v in form.getlist("offsets")],
           "repeat_days": str(form.get("repeat_days") or "0"), "at_time": str(form.get("at_time") or "")}
    prefs, error = statement_due.parse_prefs_form(raw["mode"], raw["offsets"], raw["repeat_days"], raw["at_time"])
    if error:
        shown = {"mode": raw["mode"] if raw["mode"] in ("off", "remind") else "off",
                 "offsets": [int(o) for o in raw["offsets"] if o.isdigit()],
                 "repeat_days": int(raw["repeat_days"]) if raw["repeat_days"].isdigit() else 0,
                 "at_time": raw["at_time"] or statement_due.DEFAULT_AT}
        return _page(request, current, acting, form=shown, error=error)
    with get_db() as conn:
        statement_due.save_prefs(conn, _whose(current, acting).id, prefs)
    qs = _acting_qs(current, acting)
    return RedirectResponse(url="my-settings?saved=1" + (f"&{qs}" if qs else ""), status_code=303)


@router.post("/statement-dismiss")
def statement_dismiss(account_id: int = Form(...), cycle_date: str = Form(""), next: str = Form("dashboard"),
                      current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    """Dismiss the latest cycle of one of the owner's accounts: no more reminders, no notice, no badge."""
    if statement_due.can_see(current, acting):
        with get_db() as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id = ? AND user_id = ?",
                                   (account_id, acting.id)).fetchone()
            if account is not None:
                statement_due.dismiss(conn, account, cycle_date.strip(), current.id)
    target = "uploads" if next == "uploads" else "dashboard"
    qs = _acting_qs(current, acting)
    return RedirectResponse(url=target + (f"?{qs}" if qs else ""), status_code=303)
