"""Recurring/Subscriptions page (SPEC.md section 16) — one place
listing every detected recurring charge across all accounts, plus the
card-migration view (same page, filtered to one credit card account), the
per-transaction "is this recurring?" modal and the manual mark/dismiss
actions.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user
from ..db import get_db
from ..version import APP_VERSION
from ..recurring import frequency_totals, clear_override, list_unique_merchants, scan_recurring, series_for_transaction, upsert_override
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def _account_qs_params(account_id: str, current: User, acting: User, **extra) -> str:
    """Query string for redirects back to the recurring page."""
    params = [f"{k}={v}" for k, v in extra.items()]
    if account_id.strip():
        params.append(f"account_id={account_id.strip()}")
    qs = _acting_qs(current, acting)
    if qs:
        params.append(qs)
    return ("?" + "&".join(params)) if params else ""


@router.get("/recurring")
def recurring_page(
    request: Request,
    account_id: str = "",
    scanned: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL "
            "AND is_archived = 0 ORDER BY name COLLATE NOCASE",
            (acting.id,),
        ).fetchall()

        selected_account = None
        if account_id.strip().isdigit():
            selected_account = conn.execute(
                "SELECT id, name, type FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                (int(account_id), acting.id),
            ).fetchone()

        found = scan_recurring(conn, acting.id, account_id=selected_account["id"] if selected_account else None)

        # The card-migration view's secondary list only makes sense once
        # narrowed to one specific credit card.
        unique_merchants = None
        if selected_account is not None and selected_account["type"] == "credit_card":
            unique_merchants = list_unique_merchants(conn, acting.id, selected_account["id"])

    return templates.TemplateResponse(
        request,
        "recurring.html",
        {
            "user": acting,
            "accounts": accounts,
            "selected_account": selected_account,
            "recurring": found["recurring"],
            "totals": frequency_totals(found["recurring"]),
            "suggested": found["suggested"],
            "dismissed": found["dismissed"],
            "scan_summary": found if scanned else None,
            "unique_merchants": unique_merchants,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "recurring",
        },
    )


@router.post("/recurring-scan-all")
def rescan_recurring_all(
    account_id: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Detection runs at query time, so "scan" is a fresh pass over the whole
    history (or one account) whose summary the page then shows."""
    return RedirectResponse(url="recurring" + _account_qs_params(account_id, current, acting, scanned=1), status_code=303)


@router.get("/recurring-find-match")
def recurring_find_match(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Modal body for one transaction: the series it belongs to (every
    similar charge), any other amount series from the same merchant, and the
    buttons to mark or dismiss them."""
    with get_db() as conn:
        found = series_for_transaction(conn, acting.id, id)
    if found is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

    return templates.TemplateResponse(
        request,
        "_recurring_match_fragments.html",
        {"user": acting, "series": found["series"], "siblings": found["siblings"],
         "acting_qs": _acting_qs(current, acting)},
    )


@router.post("/recurring-override")
def recurring_override(
    merchant_pattern: str = Form(...),
    is_recurring: str = Form(...),
    account_id: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """is_recurring: "1" mark recurring, "0" dismiss, "clear" drop the manual
    choice and go back to automatic detection."""
    pattern = merchant_pattern.strip()
    if pattern and is_recurring in ("1", "0", "clear"):
        with get_db() as conn:
            if is_recurring == "clear":
                clear_override(conn, acting.id, pattern)
            else:
                upsert_override(conn, acting.id, pattern, is_recurring == "1")
            conn.commit()
    return RedirectResponse(url="recurring" + _account_qs_params(account_id, current, acting), status_code=303)
