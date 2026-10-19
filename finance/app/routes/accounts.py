import re
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ..auth import ADMIN_USERS, User, get_current_user, get_acting_user
from ..common import whoami as whoami_core
from .. import settings
from ..db import get_db
from .. import card_payments, notices, ofx, statement_due, upload_page
from ..version import APP_VERSION

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["period_label"] = ofx.period_label


def _sanitize_last4(value: str) -> str | None:
    """Enforces what the schema comment already promises — "masked, never the full number" — at the one
    point this ever gets written, since the form's own `maxlength="4"`/`pattern="[0-9]{4}"` are only a
    client-side hint that a raw POST (curl, devtools, a future caller) can ignore entirely. Keeps only
    digits, then only the last 4 of those, so pasting a full account/card number here can never store
    more than its last 4 digits — never the full number, and never a non-digit string either."""
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[-4:] or None


_UPLOAD_METHODS = ("both", "pdf", "csv", "qfx")   # "qfx": QFX / OFX only (migration 0028)
ACCOUNT_TYPES = ("checking", "savings", "credit_card")
MAX_ACCOUNT_NAME = 100


def _account_problem(name: str, bank_name: str) -> str | None:
    """Why an account's name / bank can't be saved, or None."""
    if not name.strip():
        return "Give the account a name."
    if len(name.strip()) > MAX_ACCOUNT_NAME or len(bank_name.strip()) > MAX_ACCOUNT_NAME:
        return f"The name and bank can be at most {MAX_ACCOUNT_NAME} characters."
    return None


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
    """Same "never trust the client" reasoning as _sanitize_last4: the form only ever offers these four
    choices, but a raw POST could send anything — and the column's own CHECK constraint (migrations 14, 28)
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
        "assistant": _assistant(current.id),
    })


def _assistant(user_id: str) -> dict | None:
    """The person's own "Let the Household Assistant answer for me" — None while the admin's switch is off."""
    if not settings.get_bool("assistant_answers"):
        return None
    with get_db() as conn:
        r = conn.execute("SELECT assistant_ok FROM known_users WHERE id = ?", (user_id,)).fetchone()
    return {"ok": r is None or bool(r["assistant_ok"])}


@router.post("/whoami/assistant")
def set_assistant(assistant_ok: str = Form(""), current: User = Depends(get_current_user)):
    """Always the signed-in person's own switch (never the "view as" person's); tools.py reads it."""
    with get_db() as conn:
        conn.execute("UPDATE known_users SET assistant_ok = ? WHERE id = ?",
                     (1 if assistant_ok == "1" else 0, current.id))
        conn.commit()
    return RedirectResponse(url="../whoami", status_code=303)


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
        due = statement_due.statuses(conn, rows, current, acting)
        payment_due = card_payments.column(conn, rows, current, acting)

    return templates.TemplateResponse(
        request,
        "accounts_list.html",
        {
            "user": acting,
            "accounts": rows,
            "due": due,
            "payment_due": payment_due,
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
    expected_form: str = Form(""),
    expected_every_months: str = Form(""),
    expected_day: str = Form(""),
    expected_from: str = Form(""),
    reminders_on: str = Form(""),
    payment_form: str = Form(""),
    payment_due_day: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    problem = _account_problem(name, bank_name) or (None if type in ACCOUNT_TYPES else "Choose the account's type.")
    # The Expected statement group (SPEC.md section 27); a caller without it (the Upload page's new-account
    # fields) makes an account with it Off.
    expected, expected_problem = statement_due.parse_account_form(
        expected_every_months, expected_day, expected_from, reminders_on if expected_form else "1")
    problem = problem or expected_problem
    # Payment due (SPEC.md section 30): credit cards only; anything sent for another type is ignored.
    pay_day, pay_problem = card_payments.parse_form(payment_due_day if payment_form and type == "credit_card" else "")
    problem = problem or pay_problem
    if problem:
        return notices.form_error(request, templates, acting, _acting_qs(current, acting), problem, "accounts")
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO accounts (user_id, name, type, bank_name, account_number_last4, preferred_upload_method,
                                  website_url, expected_every_months, expected_day, expected_from, reminders_on,
                                  expected_set_at, payment_due_day, payment_due_set_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                acting.id, name.strip(), type, bank_name.strip() or None, _sanitize_last4(account_number_last4),
                _sanitize_upload_method(preferred_upload_method), _sanitize_url(website_url),
                expected["expected_every_months"], expected["expected_day"], expected["expected_from"],
                expected["reminders_on"], statement_due.stamp(), pay_day,
                statement_due.stamp() if pay_day is not None else None,
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
    expected_form: str = Form(""),
    expected_every_months: str = Form(""),
    expected_day: str = Form(""),
    expected_from: str = Form(""),
    reminders_on: str = Form(""),
    upload_prefs_form: str = Form(""),
    skip_confirmation: str = Form(""),
    clear_csv_layout: str = Form(""),
    payment_form: str = Form(""),
    payment_due_day: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Edit name, bank, last 4, preferred upload method, web address, the expected statement and what the Upload
    page remembers for the account (skip the PDF confirmation step; clear the saved CSV layout) — never the type,
    which decides the sign convention everywhere. Names are always read live
    through a JOIN, so nothing needs re-importing; only a multi-account CSV's
    Account column (matched by today's name) is affected by a rename."""
    problem = _account_problem(name, bank_name)
    expected = None
    if expected_form:          # the form's Expected statement group (a caller without it leaves it as it is)
        expected, expected_problem = statement_due.parse_account_form(
            expected_every_months, expected_day, expected_from, reminders_on)
        problem = problem or expected_problem
    pay_day = None
    if payment_form:           # the form's Payment due group (credit cards only; without it, left as it is)
        pay_day, pay_problem = card_payments.parse_form(payment_due_day)
        problem = problem or pay_problem
    if problem:
        return notices.form_error(request, templates, acting, _acting_qs(current, acting), problem, f"account?id={id}")
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        if expected is not None:
            # A new or changed expected date never makes a cycle before today overdue (expected_set_at).
            conn.execute(
                "UPDATE accounts SET expected_every_months = ?, expected_day = ?, expected_from = ?, reminders_on = ?"
                + ", expected_set_at = ?" + " WHERE id = ?",
                (expected["expected_every_months"], expected["expected_day"], expected["expected_from"],
                 expected["reminders_on"],
                 statement_due.stamp() if statement_due.changed(row, expected) else row["expected_set_at"], id))

        if payment_form and row["type"] == "credit_card":
            # A new or changed due day never makes an earlier due date count (payment_due_set_at).
            changed = pay_day != row["payment_due_day"]
            conn.execute("UPDATE accounts SET payment_due_day = ?, payment_due_set_at = ? WHERE id = ?",
                         (pay_day, statement_due.stamp() if changed else row["payment_due_set_at"], id))

        if upload_prefs_form:      # the form's Upload group (a caller without it leaves both as they are)
            conn.execute("UPDATE accounts SET skip_confirmation = ? WHERE id = ?",
                         (1 if skip_confirmation.strip() in ("1", "on") else 0, id))
            if clear_csv_layout.strip() in ("1", "on"):
                conn.execute("UPDATE accounts SET csv_layout = NULL WHERE id = ?", (id,))
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


@router.post("/account-upload-prefs")
def account_upload_prefs(
    request: Request,
    id: int = Form(...),
    csv_layout: str | None = Form(None),
    clear_csv_layout: str = Form(""),
    skip_confirmation: str | None = Form(None),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """What the Upload page remembers for an account (SPEC.md section 10): `csv_layout` — the layout of a CSV
    that imported ("Remember this layout": JSON {header_row, data_row, date_column, debit_credit};
    `clear_csv_layout=1` forgets it) — and/or `skip_confirmation` ("1" or "0"). A field left out (or blank) is left
    as it is. Answers
    {"csv_layout", "skip_confirmation"} as saved; a browser form goes back to the account's page."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND user_id = ? AND deleted_at IS NULL", (id, acting.id),
        ).fetchone()
        if row is None:
            return JSONResponse({"error": "Account not found"}, status_code=404)
        if clear_csv_layout.strip() in ("1", "on", "true"):
            conn.execute("UPDATE accounts SET csv_layout = NULL WHERE id = ?", (id,))
        elif csv_layout is not None and csv_layout.strip():
            layout = upload_page.clean_layout(csv_layout)
            if layout is None:
                return notices.form_error(request, templates, acting, _acting_qs(current, acting),
                                          "That isn't a CSV layout the app can keep.", f"account?id={id}")
            conn.execute("UPDATE accounts SET csv_layout = ? WHERE id = ?", (upload_page.layout_json(layout), id))
        if skip_confirmation is not None and skip_confirmation.strip():
            v = skip_confirmation.strip().lower()
            if v not in ("1", "0", "on", "off", "true", "false"):
                return notices.form_error(request, templates, acting, _acting_qs(current, acting),
                                          "Skip confirmation is 1 or 0.", f"account?id={id}")
            conn.execute("UPDATE accounts SET skip_confirmation = ? WHERE id = ?",
                         (1 if v in ("1", "on", "true") else 0, id))
        conn.commit()
        saved = conn.execute("SELECT csv_layout, skip_confirmation FROM accounts WHERE id = ?", (id,)).fetchone()
    if notices.wants_page(request):
        qs = _acting_qs(current, acting)
        return RedirectResponse(url=f"account?id={id}{'&' + qs if qs else ''}", status_code=303)
    return JSONResponse({"csv_layout": upload_page.read_layout(saved["csv_layout"]),
                         "skip_confirmation": saved["skip_confirmation"]})


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
        if archived != "1":
            # Back from the archive: reminders start again from the next expected date, never for missed ones.
            conn.execute("UPDATE accounts SET expected_set_at = ?, payment_due_set_at = CASE WHEN payment_due_day "
                         "IS NULL THEN payment_due_set_at ELSE ? END WHERE id = ? AND is_archived = 0",
                         (statement_due.stamp(), statement_due.stamp(), id))
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

        due = statement_due.cycle_status(conn, account) if statement_due.can_see(current, acting) else None
        last_upload = statement_due.last_upload(conn, id)
        # The card's payment due date (section 30)
        payment = card_payments.status(conn, account) if card_payments.visible(current, acting) else None

    return templates.TemplateResponse(
        request,
        "account_detail.html",
        {
            "user": acting,
            "account": account,
            "due": due, "due_label": statement_due.column_label(due), "last_upload": last_upload,
            "due_enabled": statement_due.enabled(), "date_label": statement_due.label,
            "payment": payment, "payment_label": card_payments.column_label(payment),
            "payment_enabled": card_payments.enabled(),
            "csv_layout": upload_page.read_layout(account["csv_layout"]),
            "txn_count": txn_count_row["n"],
            "statements": statements,
            "flow_by_statement": flow_by_statement,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "accounts",
        },
    )
