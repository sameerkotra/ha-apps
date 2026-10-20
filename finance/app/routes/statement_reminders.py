"""Setup → Settings (`my-settings`): each person's own settings — My reminders (SPEC.md section 27), Card payments
and Subscription changes (section 31, app/alerts.py) — and the notices' buttons: Dismiss on an overdue statement,
Mark as paid on a card payment (section 30) and Dismiss on a subscription change (section 29).

Whose settings: the signed-in person's own; an admin acting as someone sees and changes that person's. Someone
using another person's data through shared access still edits their own (never the owner's).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import alerts, card_payments, statement_due, subscriptions
from ..auth import User, get_acting_user, get_current_user
from ..db import get_db
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
from ..template_filters import install as _install_filters
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_install_filters(templates.env)
templates.env.globals["app_version"] = APP_VERSION


def _whose(current: User, acting: User) -> User:
    return acting if (current.is_admin or acting.id == current.id) else current


_OFF = {"mode": "off", "offsets": [], "at_time": alerts.DEFAULT_AT}


def _page(request: Request, current: User, acting: User, *, form=None, error: str = "", saved: str = "",
          card_form=None, card_error: str = "", subs_form=None, subs_error: str = ""):
    whose = _whose(current, acting)
    with get_db() as conn:
        prefs = statement_due.get_prefs(conn, whose.id)
        card_prefs = alerts.get_prefs(conn, whose.id, "card_payments")
        subs_prefs = alerts.get_prefs(conn, whose.id, "subscriptions")
        phones = statement_due.phones_for(conn, whose.id)
    shown = form or prefs or {"mode": "off", "offsets": [], "repeat_days": 0, "at_time": statement_due.DEFAULT_AT}
    return templates.TemplateResponse(request, "my_settings.html", {
        "user": acting, "acting_as_banner": _acting_banner(current, acting), "acting_qs": _acting_qs(current, acting),
        "active_page": "my_settings", "whose": whose, "own": whose.id == current.id,
        "prefs": prefs, "shown": shown, "error": error, "saved": saved == "1", "has_phone": bool(phones),
        "enabled": statement_due.enabled(), "offset_choices": statement_due.OFFSET_CHOICES,
        "repeat_choices": statement_due.REPEAT_CHOICES, "max_offsets": statement_due.MAX_OFFSETS,
        "card_prefs": card_prefs, "card_shown": card_form or card_prefs or _OFF, "card_error": card_error,
        "card_saved": saved == "cards", "card_enabled": card_payments.enabled(), "card_choices": alerts.CARD_OFFSETS,
        "subs_prefs": subs_prefs, "subs_shown": subs_form or subs_prefs or _OFF, "subs_error": subs_error,
        "subs_saved": saved == "subs", "subs_enabled": subscriptions.enabled(),
    }, status_code=400 if (error or card_error or subs_error) else 200)


@router.get("/my-settings")
def my_settings(request: Request, saved: str = "", current: User = Depends(get_current_user),
                acting: User = Depends(get_acting_user)):
    return _page(request, current, acting, saved=saved)


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


@router.post("/my-settings-notify")
async def save_notify(request: Request, current: User = Depends(get_current_user),
                      acting: User = Depends(get_acting_user)):
    """The Card payments (`kind=card_payments`) or Subscription changes (`kind=subscriptions`) card."""
    form = await request.form()
    kind = str(form.get("kind") or "")
    if kind not in alerts.KINDS:
        return _page(request, current, acting, card_error="Choose which notifications to save.")
    raw_offsets = [str(v) for v in form.getlist("offsets")]
    p, error = alerts.parse_form(kind, str(form.get("mode") or ""), raw_offsets, str(form.get("at_time") or ""))
    if error:
        shown = {"mode": "on" if form.get("mode") == "on" else "off",
                 "offsets": [int(o) for o in raw_offsets if o.lstrip("-").isdigit()],
                 "at_time": str(form.get("at_time") or "") or alerts.DEFAULT_AT}
        if kind == "card_payments":
            return _page(request, current, acting, card_form=shown, card_error=error)
        return _page(request, current, acting, subs_form=shown, subs_error=error)
    with get_db() as conn:
        alerts.save_prefs(conn, _whose(current, acting).id, kind, p)
    qs = _acting_qs(current, acting)
    tag = "cards" if kind == "card_payments" else "subs"
    return RedirectResponse(url=f"my-settings?saved={tag}" + (f"&{qs}" if qs else "") + f"#notify-{tag}",
                            status_code=303)


def _back(next_page: str, current: User, acting: User, allowed=("dashboard", "uploads")) -> RedirectResponse:
    target = next_page if next_page in allowed else allowed[0]
    qs = _acting_qs(current, acting)
    return RedirectResponse(url=target + (f"?{qs}" if qs else ""), status_code=303)


@router.post("/card-payment-paid")
def card_payment_paid(account_id: int = Form(...), due_date: str = Form(""), next: str = Form("dashboard"),
                      current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    """Mark as paid: the card's noticed due date gets no more notices or reminders (owner only)."""
    if statement_due.can_see(current, acting):
        with get_db() as conn:
            account = conn.execute("SELECT * FROM accounts WHERE id = ? AND user_id = ? AND type = 'credit_card'",
                                   (account_id, acting.id)).fetchone()
            if account is not None:
                card_payments.mark_paid(conn, account, due_date.strip(), current.id)
    if next == "account":
        qs = _acting_qs(current, acting)
        return RedirectResponse(url=f"account?id={account_id}" + (f"&{qs}" if qs else ""), status_code=303)
    return _back(next, current, acting)


@router.post("/subscription-dismiss")
def subscription_dismiss(account_id: int = Form(...), series: str = Form(""), change_key: str = Form(""),
                         next: str = Form("dashboard"), current: User = Depends(get_current_user),
                         acting: User = Depends(get_acting_user)):
    """Dismiss one subscription change (owner only); a later, different change of the series shows again."""
    if statement_due.can_see(current, acting):
        with get_db() as conn:
            subscriptions.dismiss(conn, acting.id, account_id, series.strip(), change_key.strip(), current.id)
    return _back(next, current, acting, allowed=("dashboard", "recurring"))


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
