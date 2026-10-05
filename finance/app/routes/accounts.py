import re
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ..auth import ADMIN_USERS, User, get_current_user, get_acting_user
from ..common import whoami as whoami_core
from ..db import get_db
from ..version import APP_VERSION

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def _sanitize_last4(value: str) -> str | None:
    """Enforces what the schema comment already promises — "masked, never the full number" — at the one
    point this ever gets written, since the form's own `maxlength="4"`/`pattern="[0-9]{4}"` are only a
    client-side hint that a raw POST (curl, devtools, a future caller) can ignore entirely. Keeps only
    digits, then only the last 4 of those, so pasting a full account/card number here can never store
    more than its last 4 digits — never the full number, and never a non-digit string either."""
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[-4:] or None


_UPLOAD_METHODS = ("both", "pdf", "csv")


def _sanitize_url(value: str) -> str | None:
    """An account's web address: http(s) only (never javascript: or data:), https:// added when no
    scheme was typed, at most 500 characters. Blank clears it."""
    value = (value or "").strip()
    if not value:
        return None
    if "://" not in value:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:(?!\d)", value):   # javascript:, data:, mailto: …
            return None
        value = "https://" + value
    scheme, _, rest = value.partition("://")
    host = rest.split("/", 1)[0]
    if scheme.lower() not in ("http", "https") or not host or any(c.isspace() for c in value):
        return None
    return value[:500]


def _sanitize_upload_method(value: str) -> str:
    """Same "never trust the client" reasoning as _sanitize_last4: the form only ever offers these three
    choices, but a raw POST could send anything — and the column's own CHECK constraint (migration 14)
    would reject the whole write outright rather than degrade gracefully. Anything unrecognized falls
    back to "both", the same as never having set a preference at all, rather than erroring the request."""
    value = (value or "").strip().lower()
    return value if value in _UPLOAD_METHODS else "both"


def _acting_banner(current: User, acting: User) -> str | None:
    """None when working on your own data (the common case); whose data it
    is otherwise, for the banner in base.html."""
    return acting.name if acting.id != current.id else None


def _acting_qs(current: User, acting: User) -> str:
    """as_user=... to append to internal links so the chosen data survives
    navigation — empty when it's the data this person opens into anyway
    (their own, or for shared access the first owner's)."""
    return f"as_user={acting.id}" if acting.id != (current.home_id or current.id) else ""


def htmx_fragment_requested(request: Request) -> bool:
    """True when an htmx request wants just a fragment to swap into the page.

    Back/Forward on a page htmx pushed a URL for is ALSO sent with HX-Request:
    true (plus HX-History-Restore-Request: true), but there htmx replaces the
    whole page body with the response — answering with a fragment left a
    half-empty page that only a refresh repaired. That case needs the full page."""
    return (request.headers.get("hx-request") == "true"
            and request.headers.get("hx-history-restore-request") != "true")


@router.get("/whoami")
def whoami(request: Request, current: User = Depends(get_current_user)):
    """How this app sees the signed-in person: the exact id and name Home Assistant sent, and whether either
    is on the app's admin_users list. Meant for "I added my name but there is no debug button": it shows
    what to type. Only the person's own identity and a count are shown, never the admin list itself.
    The data is the shared whoami contract (common/whoami.py); whoami.html draws it."""
    w = whoami_core.build(
        request, user_id=current.id, username=request.headers.get("x-remote-user-name"),
        display_name=request.headers.get("x-remote-user-display-name"), is_admin=current.is_admin,
        admin_entries=len(ADMIN_USERS))
    return templates.TemplateResponse(request, "whoami.html", {
        "user": current, "acting_qs": "", "acting_as_banner": None, "active_page": "whoami", "w": w,
    })


@router.get("/accounts")
def list_accounts(
    request: Request,
    show_archived: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """show_archived=1 (SPEC.md section 11) reveals archived
    accounts on this list too, each tagged with an "(archived)" badge —
    archiving hides an account from the default view but keeps it fully
    reachable with one click, never a confirmation dance, since nothing
    is actually at risk (unlike delete)."""
    include_archived = show_archived == "1"
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT a.*
            FROM accounts a
            WHERE a.user_id = ?
              AND a.deleted_at IS NULL
              {"" if include_archived else "AND a.is_archived = 0"}
            ORDER BY a.is_archived ASC, a.created_at DESC
            """,
            (acting.id,),
        ).fetchall()

    return templates.TemplateResponse(
        request,
        "accounts_list.html",
        {
            "user": acting,
            "accounts": rows,
            "show_archived": include_archived,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "accounts",
        },
    )


@router.post("/accounts")
def create_account(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    bank_name: str = Form(""),
    account_number_last4: str = Form(""),
    preferred_upload_method: str = Form("both"),
    website_url: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO accounts (user_id, name, type, bank_name, account_number_last4, preferred_upload_method,
                                  website_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                acting.id, name.strip(), type, bank_name.strip() or None, _sanitize_last4(account_number_last4),
                _sanitize_upload_method(preferred_upload_method), _sanitize_url(website_url),
            ),
        )
        conn.commit()
        new_id = cur.lastrowid

    # The inline "create account while uploading" flow (uploads_list.html)
    # calls this with Accept: application/json and needs the new id
    # directly — a 303 redirect gives it nothing usable without either
    # following the redirect (navigating away from the upload page, which
    # defeats the entire point) or re-parsing the account list HTML to
    # guess which row is new (fragile, and depends on browser fetch
    # redirect-mode behavior that isn't something this sandbox can verify
    # against a real browser). A direct JSON response sidesteps all of
    # that — fully testable server-side, no fetch-redirect-semantics
    # dependency at all.
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"id": new_id, "name": name.strip()})

    # POST-redirect-GET: never leave a form resubmit sitting on refresh.
    # Bare "accounts" — no leading slash, same rule as every href in the
    # templates (SPEC.md section 1).
    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"accounts?{qs}" if qs else "accounts", status_code=303)


@router.post("/account-update")
def update_account(
    request: Request,
    id: int = Form(...),
    name: str = Form(...),
    bank_name: str = Form(""),
    account_number_last4: str = Form(""),
    preferred_upload_method: str = Form("both"),
    website_url: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Edit name, bank, last 4, preferred upload method and web address — never the type,
    which decides the sign convention everywhere. Names are always read live
    through a JOIN, so nothing needs re-importing; only a multi-account CSV's
    Account column (matched by today's name) is affected by a rename."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        conn.execute(
            "UPDATE accounts SET name = ?, bank_name = ?, account_number_last4 = ?, preferred_upload_method = ?, "
            "website_url = ? WHERE id = ?",
            (
                name.strip(), bank_name.strip() or None, _sanitize_last4(account_number_last4),
                _sanitize_upload_method(preferred_upload_method), _sanitize_url(website_url), id,
            ),
        )
        conn.commit()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"account?id={id}{'&' + qs if qs else ''}", status_code=303)


@router.post("/account-archive")
def set_account_archived(
    request: Request,
    id: int = Form(...),
    archived: str = Form(...),
    next: str = Form("accounts"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Archive toggle (SPEC.md section 11) — non-destructive,
    reversible with one click, no confirmation needed since nothing is
    actually at risk: an archived account is hidden from the default
    accounts list and (via is_archived filtering already in place
    everywhere it matters — dashboard, transaction/transfer account
    pickers, statement upload) the current-period dashboard and every
    "pick an account" dropdown, but its own detail page, statements,
    and transactions all stay fully intact and viewable. `archived`
    arrives as an explicit desired state ("1"/"0"), not a blind toggle
    — same discipline as /transaction-exclude — so a double-submit
    can't flip it twice."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        conn.execute(
            "UPDATE accounts SET is_archived = ? WHERE id = ?",
            (1 if archived == "1" else 0, id),
        )
        conn.commit()

    # next is just "accounts" or "account" (never a full URL with its own
    # query string) — for "account" the id query param is rebuilt here
    # from the same id just archived/unarchived, avoiding the
    # double-"?" bug a caller-supplied "account?id=5" would risk once
    # acting_qs also needs appending.
    target = f"account?id={id}" if next == "account" else "accounts"
    qs = _acting_qs(current, acting)
    sep = "&" if "?" in target else "?"
    return RedirectResponse(url=f"{target}{sep + qs if qs else ''}", status_code=303)


@router.get("/account")
def account_detail(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        # deleted_at IS NULL only — is_archived is deliberately NOT filtered
        # here. Archiving hides an account from the default list but keeps
        # it fully viewable (section 11); only soft-delete actually hides it.
        account = conn.execute(
            "SELECT * FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()

        if account is None:
            # Scoped lookup returning nothing is indistinguishable from
            # "doesn't exist" vs "exists but isn't yours" by design —
            # never leak which one it was.
            return templates.TemplateResponse(
                request, "not_found.html", {"user": acting}, status_code=404
            )

        txn_count_row = conn.execute(
            "SELECT COUNT(*) AS n FROM transactions WHERE account_id = ? AND deleted_at IS NULL",
            (id,),
        ).fetchone()

        statements = conn.execute(
            "SELECT * FROM statements WHERE account_id = ? AND deleted_at IS NULL "
            "ORDER BY uploaded_at DESC",
            (id,),
        ).fetchall()

        # Money in/out per statement — a raw summary of what actually
        # landed on each statement's own transactions (every non-deleted
        # row, regardless of review_status; this is "what this statement
        # contains", not a dashboard total, so a still-flagged amount
        # counts here even though it's excluded from the dashboard).
        #
        # Sign convention (section 5): checking/savings — positive = money in
        # (deposit), negative = money out. Credit card — positive = a purchase
        # (charged to the card), negative = a payment/credit toward it. Money
        # in/out are shown from the account's point of view, so for a card
        # "Money in" is the payments/credits (the negative total) and
        # "Money out" is the purchases (the positive total). Same as
        # upload.py's statement_flows().
        flow_rows = conn.execute(
            """
            SELECT statement_id,
                   COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END), 0) AS positive_total,
                   COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0) AS negative_total
            FROM transactions
            WHERE account_id = ? AND deleted_at IS NULL
            GROUP BY statement_id
            """,
            (id,),
        ).fetchall()
        if account["type"] == "credit_card":
            flow_by_statement = {
                r["statement_id"]: {"money_in": r["negative_total"], "money_out": r["positive_total"]}
                for r in flow_rows
            }
        else:
            flow_by_statement = {
                r["statement_id"]: {"money_in": r["positive_total"], "money_out": r["negative_total"]}
                for r in flow_rows
            }

    return templates.TemplateResponse(
        request,
        "account_detail.html",
        {
            "user": acting,
            "account": account,
            "txn_count": txn_count_row["n"],
            "statements": statements,
            "flow_by_statement": flow_by_statement,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "accounts",
        },
    )
