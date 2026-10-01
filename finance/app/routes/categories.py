"""Categories page (SPEC.md section 9): every category rule, grouped by
category, with add (optionally re-categorizing existing transactions) and
delete; custom categories. Built-in rules (categorize.py's _BUILTIN_RULES) are
listed read-only so a person can see why a row landed where it did.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import User, get_current_user, get_acting_user
from ..categorize import _BUILTIN_RULES, create_custom_category, list_all_categories, recategorize_transactions, save_rule
from ..db import get_db
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


@router.get("/categories")
def list_categories(
    request: Request,
    recategorized: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        rules = conn.execute(
            "SELECT * FROM category_rules WHERE user_id = ? "
            "ORDER BY category COLLATE NOCASE, "
            "CASE match_type WHEN 'exact' THEN 3 WHEN 'prefix' THEN 2 ELSE 1 END DESC, "
            "length(pattern) DESC, created_at DESC",
            (acting.id,),
        ).fetchall()
        custom_categories = conn.execute(
            "SELECT id, name FROM custom_categories WHERE user_id = ? ORDER BY name COLLATE NOCASE",
            (acting.id,),
        ).fetchall()
        all_categories = list_all_categories(conn, acting.id)

    # Grouped by category for display, in the same specificity order the
    # matching cascade itself would try them in (categorize.py's
    # _best_match) — so the order on screen isn't just cosmetic, it
    # reflects which rule would actually win a conflict.
    grouped: dict[str, list] = {}
    for r in rules:
        grouped.setdefault(r["category"], []).append(r)

    builtin_grouped: dict[str, list[tuple[str, str]]] = {}
    for pattern, match_type, category in _BUILTIN_RULES:
        builtin_grouped.setdefault(category, []).append((pattern, match_type))

    return templates.TemplateResponse(
        request, "categories.html",
        {
            "user": acting,
            "grouped_rules": grouped,
            "builtin_grouped": builtin_grouped,
            "custom_categories": custom_categories,
            "categories": all_categories,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "categories",
            "just_recategorized": int(recategorized) if recategorized.isdigit() else None,
        },
    )


@router.post("/custom-categories")
def create_custom_category_route(
    request: Request,
    name: str = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Adds a new category name to the picker everywhere DISPLAY_CATEGORIES
    is offered (see categorize.py's list_all_categories) — the actual
    feature this page exists for. Validation/de-duplication logic lives
    in categorize.py so it's the same rule whether a category is added
    from here or (in principle) from anywhere else in the future."""
    with get_db() as conn:
        ok, result = create_custom_category(conn, acting.id, name)
        if ok:
            conn.commit()
        else:
            return JSONResponse({"error": result}, status_code=400)

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"categories{'?' + qs if qs else ''}", status_code=303)


@router.post("/custom-category-delete")
def delete_custom_category(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Removes the category from the picker only — any transaction or
    rule that already uses this category name keeps it as a plain string
    (transactions.category / category_rules.category aren't foreign keys
    into custom_categories, so there's nothing to cascade). Scoped to the
    acting user's own row via the WHERE clause."""
    with get_db() as conn:
        conn.execute("DELETE FROM custom_categories WHERE id = ? AND user_id = ?", (id, acting.id))
        conn.commit()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"categories{'?' + qs if qs else ''}", status_code=303)


@router.post("/category-rules")
def create_category_rule(
    request: Request,
    pattern: str = Form(...),
    match_type: str = Form("substring"),
    category: str = Form(...),
    recategorize: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Same upsert-on-(user_id, pattern, match_type) semantics as the
    save-as-rule path in transactions.py — resaving the same match
    updates its category rather than piling up a near-duplicate row.

    recategorize="1" (an opt-in checkbox on the Add-a-rule form) also
    applies the user's full current rule set to every one of the user's
    existing transactions — including overwriting a category a row
    already had — for any whose description a rule now matches; see
    categorize.py's recategorize_transactions for the exact scope. Off
    by default: adding a rule alone never touches existing data unless
    the user explicitly asks for that."""
    pattern = pattern.strip()
    category = category.strip()
    if not pattern:
        return JSONResponse({"error": "Match text is required"}, status_code=400)
    if not category:
        return JSONResponse({"error": "Category is required"}, status_code=400)
    safe_match_type = match_type if match_type in ("exact", "prefix", "substring") else "substring"

    recat_count = 0
    with get_db() as conn:
        save_rule(conn, acting.id, pattern, safe_match_type, category)
        if recategorize == "1":
            recat_count = recategorize_transactions(conn, acting.id)
        conn.commit()

    qs_parts = [p for p in (
        _acting_qs(current, acting),
        f"recategorized={recat_count}" if recategorize == "1" else "",
    ) if p]
    qs = "&".join(qs_parts)
    return RedirectResponse(url=f"categories{'?' + qs if qs else ''}", status_code=303)


@router.post("/category-rule-delete")
def delete_category_rule(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Scoped to the acting user's own rule via the WHERE clause — a
    tampered id can't delete another user's rule."""
    with get_db() as conn:
        conn.execute("DELETE FROM category_rules WHERE id = ? AND user_id = ?", (id, acting.id))
        conn.commit()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url=f"categories{'?' + qs if qs else ''}", status_code=303)
