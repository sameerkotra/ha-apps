"""Setup → Categories (SPEC.md section 9): three tabs — Spending, Income, Rules (`categories?tab=…`).

Spending and Income list that kind's categories, built-in and the person's own, each with this month's total, its
transactions in the last 12 months, the rules pointing to it and a ⋯ menu (colour, rename, merge into…, move to the
other list, hide from menus, delete). Rules is one table of every rule, the person's and the built-ins, with search,
chips, edit in place, Test before saving and "Use my own instead". Suggestions from the person's own corrections
and the Uncategorized count sit at the top of a list; the Income tab has Sort my income.

Everything works without the page's script (static/pages/categories.js): the tabs are links and every action is a
plain form that redirects back to the same tab. The script switches tabs without a reload, edits in place, tests a
rule as it's typed and opens the forms as sheets on a phone. The acting person's data throughout (an admin acting
as someone edits theirs; shared access as everywhere else). The work itself is in category_admin.py; today's
routes (custom-categories, custom-category-delete, category-rules, category-rule-delete) keep working.
"""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import category_admin as ca
from .. import categorize as cz
from .. import notices, settings
from ..auth import User, get_current_user, get_acting_user
from ..categorize import recategorize_for_rule, save_rule
from ..db import get_db
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
MAX_PATTERN_LENGTH = 200     # the form's own maxlength
MAX_CATEGORY_LENGTH = 60
TABS = ("spending", "income", "rules")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def _tab(value: str) -> str:
    return value if value in TABS else "spending"


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "") and "text/html" not in request.headers.get("accept", "")


def _back(current: User, acting: User, tab: str = "", **extra) -> RedirectResponse:
    """Back to the Categories page on the same tab (plus a notice code or a count), keeping "viewing as"."""
    params = {"tab": tab} if tab in TABS else {}
    params.update({k: v for k, v in extra.items() if v not in (None, "")})
    qs = "&".join(p for p in (urlencode(params), _acting_qs(current, acting)) if p)
    return RedirectResponse(url=f"categories{'?' + qs if qs else ''}", status_code=303)


def _problem(request: Request, current: User, acting: User, message: str, tab: str = ""):
    back = f"categories?tab={tab}" if tab in TABS else "categories"
    return notices.form_error(request, templates, acting, _acting_qs(current, acting), message, back)


def _page(request: Request, current: User, acting: User, *, tab: str = "spending", sort: str = "name", q: str = "",
          who: str = "", kind: str = "", rule_category: str = "", edit: str = "", notice: str = "",
          recategorized: str = "", sorted_count: str = "", test: dict | None = None, test_error: str = "",
          sort_plan: list | None = None, status_code: int = 200):
    tab = _tab(tab)
    sort = sort if sort in ca.SORTS else "name"
    with get_db() as conn:
        rows = ca.category_rows(conn, acting.id)
        rules = ca.rules_table(conn, acting.id)
        groups = cz.menu_groups(conn, acting.id)
        uncategorized = ca.uncategorized_counts(conn, acting.id)
        suggestions = ca.suggestions(conn, acting.id)
    lists = {}
    for k in cz.KINDS:
        mine = [r for r in rows if r["kind"] == k]
        lists[k] = {"shown": ca.sort_rows([r for r in mine if not r["hidden"]], sort),
                    "hidden": ca.sort_rows([r for r in mine if r["hidden"]], "name")}
    who = who if who in ("mine", "builtin") else ""
    kind = kind if kind in cz.KINDS else ""
    shown_rules = ca.filter_rules(rules, q=q, who=who, kind=kind, category=rule_category)
    rule_categories = sorted({r["category"] for r in rules}, key=str.lower)
    acting_qs = _acting_qs(current, acting)

    def rule_url(who_=None, kind_=None, cat_=None) -> str:
        """The Rules tab with one chip changed, the others (and the search) kept."""
        params = {"tab": "rules", "q": q.strip(), "who": who if who_ is None else who_,
                  "kind": kind if kind_ is None else kind_, "rule_category": rule_category if cat_ is None else cat_}
        qs = urlencode({k: v for k, v in params.items() if v})
        return "categories?" + qs + ("&" + acting_qs if acting_qs else "")

    return templates.TemplateResponse(request, "categories.html", {
        "aq": ("&" + acting_qs) if acting_qs else "", "q1": ("?" + acting_qs) if acting_qs else "",
        "rule_url": rule_url,
        "user": acting, "tab": tab, "sort": sort, "lists": lists, "groups": groups,
        "kinds": {r["name"]: r["kind"] for r in rows}, "hexes": {r["name"]: r["hex"] for r in rows},
        "neutral": cz.NEUTRAL_COLOUR,
        "palettes": {"spending": cz.SPENDING_PALETTE, "income": cz.INCOME_PALETTE},
        "uncategorized": uncategorized,
        "suggestions": {k: [s for s in suggestions if s["kind"] == k] for k in cz.KINDS}, "all_suggestions": suggestions,
        "rules": shown_rules, "rules_total": len(rules), "rule_categories": rule_categories,
        "q": q.strip(), "who": who, "kind_filter": kind, "rule_category": rule_category,
        "edit_id": int(edit) if edit.isdigit() else None,
        "match_labels": cz.MATCH_LABELS, "match_types": cz.MATCH_TYPES,
        "notice": notices.text(notice),
        "just_recategorized": int(recategorized) if recategorized.isdigit() else None,
        "just_sorted": int(sorted_count) if sorted_count.isdigit() else None,
        "test": test, "test_error": test_error, "sort_plan": sort_plan,
        "as_user": acting.id if acting_qs else "",
        "acting_as_banner": _acting_banner(current, acting), "acting_qs": acting_qs,
        "active_page": "categories",
    }, status_code=status_code)


@router.get("/categories")
def list_categories(
    request: Request,
    tab: str = "spending",
    sort: str = "name",
    q: str = "",
    who: str = "",
    kind: str = "",
    rule_category: str = "",
    edit: str = "",
    notice: str = "",
    recategorized: str = "",
    sorted: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    return _page(request, current, acting, tab=tab, sort=sort, q=q, who=who, kind=kind, rule_category=rule_category,
                 edit=edit, notice=notice, recategorized=recategorized, sorted_count=sorted)


# ------------------------------------------------------------------ categories: add, delete (today's routes)

@router.post("/custom-categories")
def create_custom_category_route(
    request: Request,
    name: str = Form(...),
    kind: str = Form("spending"),
    tab: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Adds a category of a kind (Spending by default, as before) to every menu of that kind. The duplicate check
    covers both kinds, any case (categorize.create_custom_category)."""
    with get_db() as conn:
        ok, result = cz.create_custom_category(conn, acting.id, name, kind)
        if not ok:
            return _problem(request, current, acting, result, tab)
        conn.commit()
    if not tab:
        return _back(current, acting, kind if kind == "income" else "")
    return _back(current, acting, kind, notice="cat_added")


@router.post("/custom-category-delete")
def delete_custom_category(
    request: Request,
    id: int = Form(...),
    move_to: str = Form(""),
    tab: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Deletes one of the person's own categories; its transactions go where they chose (Uncategorized unless
    they chose another), with its rules (category_admin.delete). Scoped to the acting user's own row."""
    with get_db() as conn:
        try:
            ca.delete(conn, acting.id, id, move_to)
        except ca.Problem as e:
            if "isn't one of yours" in str(e):          # someone else's id: nothing happens, as before
                return _back(current, acting, tab)
            return _problem(request, current, acting, str(e), tab)
        conn.commit()
    return _back(current, acting, tab, notice="cat_deleted" if tab else "")


# ------------------------------------------------------------------ the ⋯ menu (C) and colours (F)

def _run(request, current, acting, tab, notice, fn):
    with get_db() as conn:
        try:
            fn(conn)
        except ca.Problem as e:
            conn.rollback()
            return _problem(request, current, acting, str(e), tab)
        conn.commit()
    return _back(current, acting, tab, notice=notice)


@router.post("/category-rename")
def category_rename(request: Request, id: int = Form(...), name: str = Form(...), tab: str = Form(""),
                    current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "cat_renamed", lambda conn: ca.rename(conn, acting.id, id, name))


@router.post("/category-merge")
def category_merge(request: Request, name: str = Form(...), into: str = Form(...), tab: str = Form(""),
                   current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "cat_merged", lambda conn: ca.merge(conn, acting.id, name, into))


@router.post("/category-kind")
def category_kind(request: Request, id: int = Form(...), kind: str = Form(...), tab: str = Form(""),
                  current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "cat_moved", lambda conn: ca.set_kind(conn, acting.id, id, kind))


@router.post("/category-hide")
def category_hide(request: Request, name: str = Form(...), tab: str = Form(""),
                  current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "cat_hidden", lambda conn: ca.set_hidden(conn, acting.id, name, True))


@router.post("/category-show")
def category_show(request: Request, name: str = Form(...), tab: str = Form(""),
                  current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "cat_shown", lambda conn: ca.set_hidden(conn, acting.id, name, False))


@router.post("/category-colour")
def category_colour(request: Request, name: str = Form(...), colour: str = Form(""), tab: str = Form(""),
                    current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "cat_colour", lambda conn: ca.set_colour(conn, acting.id, name, colour))


# ------------------------------------------------------------------ rules (D)

@router.post("/category-rules")
def create_category_rule(
    request: Request,
    pattern: str = Form(...),
    match_type: str = Form("substring"),
    category: str = Form(...),
    recategorize: str = Form(""),
    tab: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Same upsert-on-(user_id, pattern, match_type) semantics as before — resaving the same match updates its
    category rather than piling up a near-duplicate row. Works for income categories exactly as for spending.

    recategorize="1" (an opt-in checkbox) also applies it to the existing transactions it matches — including
    overwriting a category a row already had, with the user's full rule set deciding between overlapping rules;
    see categorize.py's recategorize_for_rule. Off by default: adding a rule alone never touches existing data."""
    pattern = pattern.strip()
    category = category.strip()
    problem = ("Match text is required" if not pattern
               else "Category is required" if not category
               else f"Match text is too long (max {MAX_PATTERN_LENGTH} characters)" if len(pattern) > MAX_PATTERN_LENGTH
               else f"Category is too long (max {MAX_CATEGORY_LENGTH} characters)" if len(category) > MAX_CATEGORY_LENGTH
               else None)
    if problem:
        return _problem(request, current, acting, problem, tab)
    safe_match_type = match_type if match_type in cz.MATCH_TYPES else "substring"

    recat_count = 0
    with get_db() as conn:
        save_rule(conn, acting.id, pattern, safe_match_type, category)
        if recategorize == "1":
            recat_count = recategorize_for_rule(conn, acting.id, pattern, safe_match_type)
        conn.commit()
    return _back(current, acting, tab, recategorized=str(recat_count) if recategorize == "1" else "",
                 notice="rule_saved" if tab else "")


@router.post("/category-rule-delete")
def delete_category_rule(
    request: Request,
    id: int = Form(...),
    tab: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Scoped to the acting user's own rule via the WHERE clause — a tampered id can't delete another user's rule."""
    with get_db() as conn:
        conn.execute("DELETE FROM category_rules WHERE id = ? AND user_id = ?", (id, acting.id))
        conn.commit()
    return _back(current, acting, tab)


@router.post("/category-rule-edit")
def edit_category_rule(
    request: Request,
    id: int = Form(...),
    pattern: str = Form(...),
    match_type: str = Form("substring"),
    category: str = Form(...),
    recategorize: str = Form(""),
    tab: str = Form("rules"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Edit in place: this one rule's text, match type or category; with "Also re-categorize existing
    transactions", the rows it now matches get what the rules give them (the number its Test showed)."""
    count = 0
    with get_db() as conn:
        try:
            saved = ca.edit_rule(conn, acting.id, id, pattern, match_type, category)
        except ca.Problem as e:
            return _problem(request, current, acting, str(e), tab)
        if recategorize == "1":
            count = recategorize_for_rule(conn, acting.id, saved["pattern"], saved["match_type"])
        conn.commit()
    return _back(current, acting, tab or "rules", notice="rule_saved",
                 recategorized=str(count) if recategorize == "1" else "")


@router.get("/category-rule-test")
def test_category_rule(
    request: Request,
    pattern: str = "",
    match_type: str = "substring",
    category: str = "",
    rule_id: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Test before saving — writes nothing: the first 10 transactions the rule would match, how many it matches,
    and how many "Also re-categorize existing transactions" would change. JSON for the page's script; without it,
    the Rules tab with the results under the form."""
    rid = int(rule_id) if rule_id.isdigit() else None
    with get_db() as conn:
        try:
            result = ca.try_rule(conn, acting.id, pattern, match_type, category or cz.UNCATEGORIZED, rid)
        except ca.Problem as e:
            if _wants_json(request):
                return JSONResponse({"error": str(e)}, status_code=400)
            return _page(request, current, acting, tab="rules", test_error=str(e), status_code=400)
    result["rule_id"] = rid
    if _wants_json(request):
        return JSONResponse(result)
    return _page(request, current, acting, tab="rules", test=result, edit=str(rid or ""))


@router.post("/category-rule-copy")
def copy_category_rule(request: Request, pattern: str = Form(...), match_type: str = Form(...),
                       tab: str = Form("rules"), current: User = Depends(get_current_user),
                       acting: User = Depends(get_acting_user)):
    """"Use my own instead": a built-in rule copied into the person's rules, opened for editing."""
    with get_db() as conn:
        try:
            rid = ca.copy_builtin(conn, acting.id, pattern, match_type)
        except ca.Problem as e:
            return _problem(request, current, acting, str(e), tab)
        conn.commit()
    return _back(current, acting, "rules", notice="rule_copied", edit=str(rid))


# ------------------------------------------------------------------ suggestions (E)

@router.post("/category-suggestion-make")
def make_suggestion(request: Request, merchant: str = Form(...), category: str = Form(...), tab: str = Form(""),
                    current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "suggestion_made",
                lambda conn: ca.make_suggested_rule(conn, acting.id, merchant, category))


@router.post("/category-suggestion-dismiss")
def dismiss_suggestion(request: Request, merchant: str = Form(...), category: str = Form(...), tab: str = Form(""),
                       current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return _run(request, current, acting, tab, "suggestion_dismissed",
                lambda conn: ca.dismiss_suggestion(conn, acting.id, merchant, category))


# ------------------------------------------------------------------ Sort my income (section 9)

@router.post("/category-sort-income")
def sort_income(request: Request, current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    """Works out — writing nothing — what the rules and then the AI would give the money-in rows that are still
    Other income or Uncategorized, and lists it on the Income tab to confirm. Reads only, so no write transaction
    is open while the AI answers."""
    with get_db() as conn:
        plan = ca.plan_income(conn, acting.id, settings.ai_url(), settings.ai_model())
    if _wants_json(request):
        return JSONResponse({"changes": plan})
    return _page(request, current, acting, tab="income", sort_plan=plan)


@router.post("/category-sort-income-apply")
async def sort_income_apply(request: Request, current: User = Depends(get_current_user),
                            acting: User = Depends(get_acting_user)):
    """Changes only the rows ticked on the list (`pick` = their ids), each to the category listed with it."""
    form = await request.form()
    chosen = []
    for raw in form.getlist("pick"):
        if not str(raw).isdigit():
            continue
        tid = int(raw)
        chosen.append({"id": tid, "to": form.get(f"to_{tid}", ""), "source": form.get(f"source_{tid}", ""),
                       "pattern": form.get(f"pattern_{tid}", ""), "match_type": form.get(f"type_{tid}", "")})
    def write() -> int:
        with get_db() as conn:
            count = ca.apply_income(conn, acting.id, chosen[:5000])
            conn.commit()
            return count
    n = await run_in_threadpool(write)
    return _back(current, acting, "income", notice="income_sorted", sorted=str(n))
