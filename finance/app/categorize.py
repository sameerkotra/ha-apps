"""Categorization (SPEC.md section 9): auto-categorized on
upload via a cascading match, always manually editable afterward.

Cascade, in order:
1. The user's own rules (`category_rules` table) — built from past
   manual corrections (see save_rule).
2. Built-in default rules for common merchant patterns — a starting set,
   expected to grow from real corrections the same way the parsing
   pipeline itself does (SPEC.md section 4's own philosophy:
   start generic, add overrides only where real data shows a gap).
3. LLM fallback, for anything neither rule set matched — one batched
   call per statement covering every still-unmatched transaction at
   once, not one call per transaction: the vision extraction is already
   the slow part of processing a statement, and this must not multiply
   that by transaction count.
4. Explicit "Uncategorized" — never a silent NULL/guess.

Rule conflict resolution is by specificity, not insertion order: exact >
prefix > substring, so a broad rule saved later can't silently override
a precise one saved earlier. Ties within the same specificity go to the
longer (more specific) pattern, then the most recently saved rule.
"""
import json
import logging
from dataclasses import dataclass, field

from . import ai_client, settings

logger = logging.getLogger(__name__)

UNCATEGORIZED = "Uncategorized"
PAYMENTS = "Payments"
OTHER_INCOME = "Other income"
REFUNDS = "Refunds"

# Every category has a kind (SPEC.md section 9): spending or income. The built-ins' kinds are fixed here (not in the
# database); a person's own category has its kind in custom_categories.kind. Payments (a card payment) and
# Uncategorized (not decided yet) are neither. The kind only says which list a category is offered from and how
# income is broken down: whether a row counts as income or spending is still its direction and account.
SPENDING_CATEGORIES = [
    "Groceries", "Dining", "Transportation", "Shopping", "Subscriptions",
    "Utilities", "Gas", "Health", "Entertainment", "Travel", "Housing", "Other",
]
INCOME_CATEGORIES = [
    "Salary", "Interest", "Dividends", REFUNDS, "Reimbursements", "Gifts received", OTHER_INCOME,
]
NEITHER_CATEGORIES = [PAYMENTS, UNCATEGORIZED]
KINDS = ("spending", "income")

# The built-in names, offered in category menus app-wide — not the full set of categories that can ever exist (a
# saved rule or a manual edit can use any string). A transaction already carrying a category outside the menus still
# displays correctly (see _category_options.html), it just isn't one of the picks.
DISPLAY_CATEGORIES = SPENDING_CATEGORIES + INCOME_CATEGORIES + NEITHER_CATEGORIES
BUILTIN_KIND = {**{c: "spending" for c in SPENDING_CATEGORIES}, **{c: "income" for c in INCOME_CATEGORIES}}
_BUILTIN_LOWER = {c.lower(): c for c in DISPLAY_CATEGORIES}

# F. Colours: a small fixed palette, every colour at least 3:1 against every theme's --card and --panel (WCAG's
# contrast for graphics; tests/test_categories_page.py checks it against static/common/themes.css). Spending and
# income have separate ranges. Stored by key (custom_categories.colour, category_prefs.colour).
SPENDING_PALETTE = {
    "blue": "#3a85e0", "indigo": "#6a7ae6", "purple": "#9168dc", "plum": "#b05cc0", "magenta": "#c94f8a",
    "pink": "#d6577a", "red": "#d24848", "orange": "#d4602a", "amber": "#ab7a0a", "brown": "#a8743a",
    "olive": "#7a8c25", "slate": "#7a8699",
}
INCOME_PALETTE = {
    "emerald": "#17976a", "jade": "#2c9650", "lime": "#5c9420", "teal": "#1b9384", "sea": "#1690a8",
    "cyan": "#2d86c4", "pine": "#3f8f6f",
}
NEUTRAL_COLOUR = "#848c9c"            # Payments, Uncategorized (and any name that isn't a category)
PALETTE = {**SPENDING_PALETTE, **INCOME_PALETTE}
DEFAULT_COLOURS = {
    "Groceries": "olive", "Dining": "orange", "Transportation": "blue", "Shopping": "magenta",
    "Subscriptions": "purple", "Utilities": "amber", "Gas": "brown", "Health": "red", "Entertainment": "pink",
    "Travel": "indigo", "Housing": "plum", "Other": "slate",
    "Salary": "emerald", "Interest": "cyan", "Dividends": "teal", REFUNDS: "sea", "Reimbursements": "jade",
    "Gifts received": "lime", OTHER_INCOME: "pine",
}


# How the dashboards, Transactions' Money out and the Household Assistant count spending and income (section 8's
# sign convention), over transactions t joined to accounts a. Money back on a card categorized Refunds is spending
# going down (a negative spend); any other money in on a card is a card payment, neither.
SPEND_SQL = ("((a.type = 'credit_card' AND (t.amount > 0 OR t.category = 'Refunds')) "
             "OR (a.type != 'credit_card' AND t.amount < 0))")
SPEND_AMOUNT_SQL = "CASE WHEN a.type = 'credit_card' THEN t.amount ELSE -t.amount END"
INCOME_SQL = "(t.amount > 0 AND a.type IN ('checking', 'savings'))"


def palette_for(kind: str) -> dict:
    return INCOME_PALETTE if kind == "income" else SPENDING_PALETTE


def default_colour(name: str, kind: str) -> str:
    """A built-in's own colour; a person's category without one gets a steady pick from its kind's range."""
    if name in DEFAULT_COLOURS:
        return DEFAULT_COLOURS[name]
    keys = list(palette_for(kind))
    return keys[sum(name.lower().encode("utf-8")) % len(keys)]


def builtin_name(name: str) -> str | None:
    """The built-in category this name is (any case), or None."""
    return _BUILTIN_LOWER.get((name or "").strip().lower())

_SPECIFICITY = {"exact": 3, "prefix": 2, "substring": 1}
MATCH_TYPES = ("exact", "prefix", "substring")
MATCH_LABELS = {"exact": "exact", "prefix": "starts with", "substring": "contains"}

# Starter set, deliberately generic (SPEC.md section 4's own
# philosophy applied here too) — grows from real corrections via
# save_rule, not guessed at exhaustively in advance. A built-in rule is only
# tried on rows of its category's kind (_BUILTIN_FOR): the spending ones on
# money out and card purchases, the income ones on money in — so "TARGET" on a
# deposit isn't Shopping, and "REFUND" on a card's money back is Refunds.
_BUILTIN_RULES = [
    ("UBER EATS", "substring", "Dining"),
    ("UBER", "substring", "Transportation"),
    ("LYFT", "substring", "Transportation"),
    ("NETFLIX", "substring", "Subscriptions"),
    ("SPOTIFY", "substring", "Subscriptions"),
    ("HULU", "substring", "Subscriptions"),
    ("AMAZON PRIME", "substring", "Subscriptions"),
    ("AMAZON", "substring", "Shopping"),
    ("WALMART", "substring", "Groceries"),
    ("TARGET", "substring", "Shopping"),
    ("WHOLE FOODS", "substring", "Groceries"),
    ("TRADER JOE", "substring", "Groceries"),
    ("KROGER", "substring", "Groceries"),
    ("SAFEWAY", "substring", "Groceries"),
    ("STARBUCKS", "substring", "Dining"),
    ("MCDONALD", "substring", "Dining"),
    ("CHIPOTLE", "substring", "Dining"),
    ("DOORDASH", "substring", "Dining"),
    ("GRUBHUB", "substring", "Dining"),
    ("SHELL", "substring", "Gas"),
    ("CHEVRON", "substring", "Gas"),
    ("EXXON", "substring", "Gas"),
    ("CVS", "substring", "Health"),
    ("WALGREENS", "substring", "Health"),
    ("AT&T", "substring", "Utilities"),
    ("COMCAST", "substring", "Utilities"),
    ("XCEL ENERGY", "substring", "Utilities"),
    ("AURORA WATER", "substring", "Utilities"),
    ("VERIZON", "substring", "Utilities"),
    # Income (money in). Money in from ZELLE FROM / VENMO FROM is left for the AI on purpose: it could be a gift, a
    # reimbursement or rent (see _AI_ONLY).
    ("PAYROLL", "substring", "Salary"),
    ("DIRECT DEP", "substring", "Salary"),
    ("DIR DEP", "substring", "Salary"),
    ("SALARY", "substring", "Salary"),
    ("INTEREST PAID", "substring", "Interest"),
    ("INTEREST EARNED", "substring", "Interest"),
    ("INT PAID", "substring", "Interest"),
    ("DIVIDEND", "substring", "Dividends"),
    ("REFUND", "substring", REFUNDS),
]
_AI_ONLY = ("ZELLE FROM", "VENMO FROM")      # money in with these goes past the built-in rules, to the AI

# Which way a row's money goes, as the dashboards count it (section 8's sign convention), for the cascade:
#   "in"       money in on checking/savings        income built-in rules; the AI gets the income list
#   "out"      money out on checking/savings       spending built-in rules; the AI gets the spending list
#   "card"     a card purchase (positive)          spending built-in rules; the AI gets spending + Refunds
#   "card_in"  money back on a card (negative)     a rule giving Refunds, else Payments (never the AI)
#   None       not known (an old caller)           every built-in rule; the AI gets the spending list
FLOWS = ("in", "out", "card", "card_in")


def flow_of(account_type: str | None, amount: float | None) -> str | None:
    if account_type is None or amount is None:
        return None
    if account_type == "credit_card":
        return "card_in" if amount < 0 else "card"
    return "in" if amount > 0 else "out"


def income_first(account_type: str | None, amount: float | None) -> bool:
    """Menus list Income first for money in on a checking or savings account, Spending first for everything else
    (money out, and every card row — a card's money back is spending going down)."""
    return flow_of(account_type, amount) == "in"


def income_names_upper(conn, user_id: str) -> set[str]:
    """The person's income categories (built-in and their own, hidden ones too), upper-cased."""
    return {n.upper() for n in categories_by_kind(conn, user_id, include_hidden=True)["income"]}


def rule_fits_flow(category: str, flow: str | None, income_upper: set[str]) -> bool:
    """A rule giving an income category applies only to money in on a checking or savings account (flow "in").
    Other rules apply to every row. A row whose direction isn't known (None) keeps every rule. Money back on a card
    has its own handling (a rule giving Refunds), not this check."""
    if flow in ("in", None) or income_upper is None:
        return True
    return (category or "").upper() not in income_upper


def _builtin_for(flow: str | None) -> list[tuple[str, str, str]]:
    if flow is None:
        return _BUILTIN_RULES
    if flow == "in":
        return [r for r in _BUILTIN_RULES if BUILTIN_KIND.get(r[2]) == "income"]
    if flow == "card_in":
        return [r for r in _BUILTIN_RULES if r[2] == REFUNDS]
    return [r for r in _BUILTIN_RULES if BUILTIN_KIND.get(r[2]) == "spending"]


def builtin_rules() -> list[dict]:
    """The built-in rules for the Rules table: pattern, match type, category and kind."""
    return [{"pattern": p, "match_type": mt, "category": c, "kind": BUILTIN_KIND.get(c)} for p, mt, c in _BUILTIN_RULES]


# The descriptions are numbered and the answer is keyed by number: a description the model echoes
# back slightly changed (spacing, case, a quote) would otherwise match nothing, and two rows with the
# same description are still each answered. The model is offered only the list for the rows' direction.
_LLM_PROMPT_TEMPLATE = """Categorize each of these bank/credit-card transaction
descriptions ({what}) into one of these categories: {categories}.
Respond with ONLY a JSON
object, no other text, in this exact shape, keyed by each description's number:
{{"categories": {{"1": "<category>", "2": "<category>", ...}}}}
Descriptions:
{descriptions}"""
_LLM_WHAT = {"spending": "money spent", "income": "money coming in"}

MAX_CATEGORY_CHARS = 60          # the same limit as a category typed on the Categories page
MAX_PROMPT_DESCRIPTION_CHARS = 200


def _matches(pattern: str, match_type: str, description: str) -> bool:
    return _matches_upper(pattern.upper(), match_type, description.upper())


def _matches_upper(p: str, match_type: str, d: str) -> bool:
    """Both already upper-cased. An empty pattern matches nothing (it would otherwise match every
    description as a substring or prefix)."""
    if not p:
        return False
    if match_type == "exact":
        return d == p
    if match_type == "prefix":
        return d.startswith(p)
    return p in d  # substring


def _best_match(rules: list[tuple[str, str, str]], description: str) -> tuple[str, str] | None:
    """rules: (pattern, match_type, category), most recent first. Returns
    (category, pattern) for the best match by (specificity, pattern length) —
    ties go to the newest rule — or None. The pattern is returned for the
    categorization debug view."""
    best_key = None
    best_category = None
    best_pattern = None
    d = (description or "").upper()
    for pattern, match_type, category in rules:
        if not _matches_upper(pattern.upper(), match_type, d):
            continue
        key = (_SPECIFICITY[match_type], len(pattern))
        if best_key is None or key > best_key:
            best_key, best_category, best_pattern = key, category, pattern
    if best_category is None:
        return None
    return best_category, best_pattern


def match_builtin_rules(description: str, flow: str | None = None) -> tuple[str, str] | None:
    if flow == "in" and any(w in (description or "").upper() for w in _AI_ONLY):
        return None
    return _best_match(_builtin_for(flow), description)


def save_rule(conn, user_id: str, pattern: str, match_type: str, category: str) -> None:
    """Upserts on (user_id, pattern, match_type) — correcting the same
    merchant's category again updates the existing rule rather than
    piling up near-duplicate rows that would just tie against each other
    on the next match. A blank pattern is refused (it would match every
    transaction)."""
    if not (pattern or "").strip():
        raise ValueError("A rule needs a pattern to match")
    existing = conn.execute(
        "SELECT id FROM category_rules WHERE user_id = ? AND pattern = ? AND match_type = ?",
        (user_id, pattern, match_type),
    ).fetchone()
    if existing:
        conn.execute("UPDATE category_rules SET category = ? WHERE id = ?", (category, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO category_rules (user_id, pattern, match_type, category) VALUES (?, ?, ?, ?)",
            (user_id, pattern, match_type, category),
        )


def _custom_rows(conn, user_id: str) -> list:
    return conn.execute(
        "SELECT id, name, kind, colour FROM custom_categories WHERE user_id = ? ORDER BY name COLLATE NOCASE",
        (user_id,),
    ).fetchall()


def category_prefs(conn, user_id: str) -> dict[str, dict]:
    """{built-in name: {"hidden": bool, "colour": key or None}} — the person's own choices for built-ins."""
    return {r["name"]: {"hidden": bool(r["hidden"]), "colour": r["colour"]} for r in conn.execute(
        "SELECT name, hidden, colour FROM category_prefs WHERE user_id = ?", (user_id,)).fetchall()}


def hidden_categories(conn, user_id: str) -> set[str]:
    return {n for n, p in category_prefs(conn, user_id).items() if p["hidden"]}


def all_categories(conn, user_id: str) -> list[dict]:
    """Every category this person has: the built-ins (Payments and Uncategorized too) and their own, each
    {name, kind ('spending' / 'income' / None), builtin, hidden, colour (key), hex, id (their own's row id)}.
    A custom category whose name is a built-in's (any case) is the built-in."""
    prefs = category_prefs(conn, user_id)
    out = []
    for name in DISPLAY_CATEGORIES:
        kind = BUILTIN_KIND.get(name)
        p = prefs.get(name, {})
        colour = p.get("colour") if p.get("colour") in PALETTE else (DEFAULT_COLOURS.get(name) if kind else None)
        out.append({"name": name, "kind": kind, "builtin": True, "hidden": bool(p.get("hidden")) and kind is not None,
                    "colour": colour, "hex": PALETTE.get(colour, NEUTRAL_COLOUR), "id": None})
    for r in _custom_rows(conn, user_id):
        if builtin_name(r["name"]):
            continue
        kind = r["kind"] if r["kind"] in KINDS else "spending"
        colour = r["colour"] if r["colour"] in PALETTE else default_colour(r["name"], kind)
        out.append({"name": r["name"], "kind": kind, "builtin": False, "hidden": False, "colour": colour,
                    "hex": PALETTE[colour], "id": r["id"]})
    return out


def find_category(conn, user_id: str, name: str) -> dict | None:
    """The person's category with this name (any case), or None."""
    want = (name or "").strip().lower()
    return next((c for c in all_categories(conn, user_id) if c["name"].lower() == want), None)


def colours(conn, user_id: str) -> dict[str, str]:
    """{category name: hex} for the charts (Home, Compare); a name that isn't a category gets NEUTRAL_COLOUR."""
    return {c["name"]: c["hex"] for c in all_categories(conn, user_id)}


def categories_by_kind(conn, user_id: str, *, include_hidden: bool = False) -> dict[str, list[str]]:
    """{"spending": [...], "income": [...]} for the grouped menus, each sorted by name (built-in and the person's
    own together), hidden built-ins left out unless include_hidden. Payments and Uncategorized are in neither:
    every menu adds them after the two groups (_category_options.html)."""
    out: dict[str, list[str]] = {"spending": [], "income": []}
    for c in all_categories(conn, user_id):
        if c["kind"] in KINDS and (include_hidden or not c["hidden"]):
            out[c["kind"]].append(c["name"])
    for names in out.values():
        names.sort(key=str.lower)
    return out


def menu_groups(conn, user_id: str) -> dict:
    """What a template needs for grouped menus: categories_by_kind plus the trailing names (Payments, then
    Uncategorized) and the hidden ones (still shown when a row already has one)."""
    groups = categories_by_kind(conn, user_id)
    return {"spending": groups["spending"], "income": groups["income"], "neither": list(NEITHER_CATEGORIES),
            "hidden": sorted(hidden_categories(conn, user_id))}


def list_all_categories(conn, user_id: str) -> list[str]:
    """The full set of category names (built-in and the person's own, both kinds), deduplicated case-insensitively
    and sorted alphabetically, with "Uncategorized" always pinned last since it's the explicit not-categorized
    state. For anything that just needs names (the Query tab's category choices, Write SQL); the grouped menus use
    categories_by_kind / menu_groups. Hidden built-ins are left out, as from every menu."""
    hidden = hidden_categories(conn, user_id)
    names = [c["name"] for c in all_categories(conn, user_id) if c["name"] != UNCATEGORIZED and c["name"] not in hidden]
    names.sort(key=str.lower)
    names.append(UNCATEGORIZED)
    return names


def check_category_name(conn, user_id: str, name: str, *, except_id: int | None = None) -> tuple[bool, str]:
    """(True, cleaned name) or (False, reason): not blank, at most 60 characters, and not already a category of
    either kind in any case (except_id: the person's own category being renamed may change its own case)."""
    name = " ".join((name or "").split())
    if not name:
        return False, "Category name is required"
    if len(name) > MAX_CATEGORY_CHARS:
        return False, f"Category name is too long (max {MAX_CATEGORY_CHARS} characters)"
    for c in all_categories(conn, user_id):
        if c["name"].lower() == name.lower() and (except_id is None or c["id"] != except_id):
            return False, f'"{name}" already exists'
    return True, name


def create_custom_category(conn, user_id: str, name: str, kind: str = "spending",
                           colour: str | None = None) -> tuple[bool, str]:
    """Adds a user-defined category (Categories page) of a kind, Spending by default. Case-insensitive
    de-duplication against every category of both kinds — "Groceries" and "groceries" are the same category to a
    person, and "Salary" can't be added again as a spending category, even though the table's own UNIQUE
    constraint is case-sensitive and wouldn't catch that on its own. Returns (True, name) on success, or
    (False, <reason>) with nothing written."""
    if kind not in KINDS:
        return False, "Choose Spending or Income"
    ok, name = check_category_name(conn, user_id, name)
    if not ok:
        return False, name
    if colour is not None and colour not in palette_for(kind):
        colour = None
    conn.execute("INSERT INTO custom_categories (user_id, name, kind, colour) VALUES (?, ?, ?, ?)",
                 (user_id, name, kind, colour))
    return True, name


def ai_categories(conn, user_id: str, flow: str | None) -> list[str]:
    """The names the AI is offered for rows going this way: the income list for money in on checking/savings, the
    spending list for everything else, plus Refunds for card purchases (a card's money back)."""
    groups = categories_by_kind(conn, user_id)
    if flow == "in":
        return groups["income"]
    names = list(groups["spending"])
    if flow == "card" and REFUNDS not in names:
        names.append(REFUNDS)
    return names


def categorize_via_llm(
    ollama_url: str, ollama_model: str, descriptions: list[str], categories: list[str] | None = None,
    kind: str = "spending",
) -> tuple[dict[str, str], str, str | None, str | None]:
    """One batched LLM call for every still-unmatched description. Returns
    (categories, prompt, raw_response, error): categories is {} on any
    failure (rows then stay Uncategorized — this never fails an import);
    prompt is "" when no call was attempted; raw_response is None when the
    call never completed. The caller stores the last three for the debug
    view; this function does no DB access. `categories`: the list offered (by
    default the built-in spending list); `kind` words the prompt."""
    if not ollama_url or not ollama_model or not descriptions:
        return {}, "", None, None

    offered = categories if categories else SPENDING_CATEGORIES
    prompt = _LLM_PROMPT_TEMPLATE.format(what=_LLM_WHAT.get(kind, _LLM_WHAT["spending"]),
                                         categories=", ".join(offered), descriptions="\n".join(
        f"{i}. {' '.join(str(d).split())[:MAX_PROMPT_DESCRIPTION_CHARS]}" for i, d in enumerate(descriptions, start=1)))
    # A cheaper "text model" can be set for this on App settings; otherwise the vision model.
    model = settings.ai_text_model() or ollama_model
    try:
        reply = ai_client.generate(prompt, url=ollama_url, model=model, want_json=True, timeout=120,
                                   purpose="Categorization")
    except ai_client.AIError as e:
        logger.warning("Categorization LLM fallback failed, leaving affected rows Uncategorized: %s", e)
        return {}, prompt, e.raw or None, str(e)
    text, raw_body = reply.text, reply.raw
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}, prompt, raw_body, "Response didn't contain a JSON object"
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as e:
        return {}, prompt, raw_body, repr(e)
    categories = data.get("categories", {}) if isinstance(data, dict) else {}
    if not isinstance(categories, dict):
        return {}, prompt, raw_body, "Response had no categories object"
    return _by_description(categories, descriptions), prompt, raw_body, None


def _clean_category(value) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())[:MAX_CATEGORY_CHARS]
    return text or None


def _by_description(categories: dict, descriptions: list[str]) -> dict[str, str]:
    """The answer, keyed by number ("1", "2", …), as {description: category}. A model that keyed it by
    the description itself instead (the old prompt's shape) still counts."""
    out: dict[str, str] = {}
    known = set(descriptions)
    for key, value in categories.items():
        category = _clean_category(value)
        if category is None:
            continue
        k = str(key).strip().rstrip(".")
        if k.isdigit() and 1 <= int(k) <= len(descriptions):
            out.setdefault(descriptions[int(k) - 1], category)
        elif key in known:
            out.setdefault(key, category)
    return out


def recategorize_transactions(conn, user_id: str) -> int:
    """Re-runs the user's current rule set against EVERY one of the
    user's transactions (not just Uncategorized ones), updating any
    whose description a rule now matches — including overwriting a
    category that was already set, whether that came from a human's
    manual edit, an earlier rule, or the LLM fallback. A row is left
    alone if nothing in the user's current rule set matches its
    description at all. Uses the FULL current rule set (via _best_match),
    so the same specificity resolution the import-time cascade uses
    (exact > prefix > substring, longer pattern then most-recent as
    tiebreakers) picks the winner. Returns the number updated."""
    user_rule_tuples = _user_rules(conn, user_id)
    rules_upper = [(p.upper(), mt, c, p) for p, mt, c in user_rule_tuples]

    rows = conn.execute(
        "SELECT t.id, t.description, t.category, t.amount, a.type AS account_type FROM transactions t "
        "LEFT JOIN accounts a ON a.id = t.account_id WHERE t.user_id = ? AND t.deleted_at IS NULL",
        (user_id,),
    ).fetchall()
    income_upper = income_names_upper(conn, user_id)

    updates = []
    for row in rows:
        match = _best_match_upper(rules_upper, (row["description"] or "").upper(),
                                  flow_of(row["account_type"], row["amount"]), income_upper)
        if match and match[0] != row["category"]:
            updates.append((match[0], match[1], row["id"], match[2]))
    _write_rule_updates(conn, user_id, updates)
    return len(updates)


def rule_changes(conn, user_id: str, pattern: str, match_type: str,
                 rules: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """What "Also re-categorize existing transactions" would do for one rule, without writing anything: the
    transactions the rule (pattern, match_type) matches whose category the rule set `rules` (pattern, match_type,
    category; newest first; by default the person's saved rules) would change, each {id, category (now), to,
    pattern, match_type}. recategorize_for_rule writes exactly these, so the Rules table's "will change N" (a Test,
    which passes the unsaved rule in `rules`) is what saving does."""
    p = (pattern or "").upper()
    if not p:
        return []
    conn.create_function("finance_rule_matches", 1,
                         lambda d: _matches_upper(p, match_type, (d or "").upper()), deterministic=True)
    rows = conn.execute(
        "SELECT t.id, t.description, t.category, t.amount, a.type AS account_type FROM transactions t "
        "LEFT JOIN accounts a ON a.id = t.account_id "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND finance_rule_matches(t.description)",
        (user_id,),
    ).fetchall()
    rules = _user_rules(conn, user_id) if rules is None else rules
    rules_upper = [(rp.upper(), mt, c, rp) for rp, mt, c in rules]
    income_upper = income_names_upper(conn, user_id)
    out = []
    for row in rows:
        match = _best_match_upper(rules_upper, (row["description"] or "").upper(),
                                  flow_of(row["account_type"], row["amount"]), income_upper)
        if match and match[0] != row["category"]:
            out.append({"id": row["id"], "category": row["category"], "to": match[0], "pattern": match[1],
                        "match_type": match[2]})
    return out


def recategorize_for_rule(conn, user_id: str, pattern: str, match_type: str) -> int:
    """"Also re-categorize existing transactions" after one rule was saved (Categories page): the
    transactions the saved rule matches get the category the user's whole rule set gives them (the
    same specificity order an import uses, so a more specific older rule still wins), overwriting
    whatever they had. Only those rows are looked at — the rule is tested once per transaction
    inside SQLite, instead of every rule on every transaction in Python — and only those can be
    changed; other transactions keep their category. Returns how many were updated (rule_changes
    says which, beforehand)."""
    changes = rule_changes(conn, user_id, pattern, match_type)
    _write_rule_updates(conn, user_id, [(c["to"], c["pattern"], c["id"], c["match_type"]) for c in changes])
    return len(changes)


def _write_rule_updates(conn, user_id: str, updates: list[tuple]) -> None:
    """updates: (category, pattern, transaction id, match_type). The rules that did it get last_matched_at."""
    conn.executemany(
        "UPDATE transactions SET category = ?, category_source = 'user_rule', category_matched_pattern = ? WHERE id = ?",
        [u[:3] for u in updates],
    )
    touch_rules(conn, user_id, {(u[1], u[3]) for u in updates})


def touch_rules(conn, user_id: str, used: set[tuple[str, str]]) -> None:
    """last_matched_at = now on the person's rules (pattern, match_type) that just categorized a row."""
    if used:
        conn.executemany(
            "UPDATE category_rules SET last_matched_at = datetime('now') WHERE user_id = ? AND pattern = ? "
            "AND match_type = ?", [(user_id, p, mt) for p, mt in used])


def _user_rules(conn, user_id: str) -> list[tuple[str, str, str]]:
    return [(r["pattern"], r["match_type"], r["category"]) for r in conn.execute(
        "SELECT pattern, match_type, category FROM category_rules WHERE user_id = ? ORDER BY created_at DESC, id DESC",
        (user_id,),
    ).fetchall()]


def _best_match_upper(rules_upper, d: str, flow: str | None = None,
                      income_upper: set[str] | None = None) -> tuple[str, str, str] | None:
    """_best_match with the patterns (p_upper, match_type, category, pattern) and the description
    upper-cased once by the caller. Returns (category, pattern, match_type). With income_upper, rules giving an
    income category are skipped unless the row is money in on checking/savings (rule_fits_flow)."""
    best_key = best = None
    for p, match_type, category, pattern in rules_upper:
        if income_upper is not None and not rule_fits_flow(category, flow, income_upper):
            continue
        if not _matches_upper(p, match_type, d):
            continue
        key = (_SPECIFICITY[match_type], len(pattern))
        if best_key is None or key > best_key:
            best_key, best = key, (category, pattern, match_type)
    return best


@dataclass
class CategoryPlan:
    """What categorization decided, before anything is written: {row key: (category, source, pattern)},
    plus the LLM call's prompt / raw answer / error for the debug view (None when no call was made), and the
    person's rules that matched (pattern, match_type) for last_matched_at."""
    assignments: dict = field(default_factory=dict)
    llm: tuple[str, str | None, str | None] | None = None
    user_id: str = ""
    rules_used: set = field(default_factory=set)


def _llm_list_kind(flow: str | None) -> str:
    return "income" if flow == "in" else "spending"


def plan_categories(conn, user_id: str, ollama_url: str, ollama_model: str, rows: list[tuple]) -> CategoryPlan:
    """The cascade for rows (key, description) or (key, description, flow) — flow as flow_of says — without
    writing anything: the user's rules, the built-in ones for the row's direction, then the LLM for the rest (one
    batched call per list: the income list for money in on checking/savings, the spending one — with Refunds for
    card purchases — for the others), and "Uncategorized" last. Money back on a card ("card_in") never goes to the
    AI: a rule giving Refunds, else "Payments" (a card payment, as always). `conn` is only read (the rules). The
    LLM call can take minutes, so the caller must not hold a write transaction while this runs."""
    rules_upper = [(p.upper(), mt, c, p) for p, mt, c in _user_rules(conn, user_id)]
    income_upper = income_names_upper(conn, user_id)
    plan = CategoryPlan(user_id=user_id)
    unmatched: dict[tuple, list[tuple]] = {}
    offered_for: dict = {}
    for row in rows:
        key, description = row[0], row[1]
        flow = row[2] if len(row) > 2 else None
        d = (description or "").upper()
        user_match = _best_match_upper(rules_upper, d, flow, None if flow == "card_in" else income_upper)
        if flow == "card_in":
            if user_match and user_match[0] == REFUNDS:
                plan.assignments[key] = (REFUNDS, "user_rule", user_match[1])
                plan.rules_used.add((user_match[1], user_match[2]))
                continue
            builtin_match = match_builtin_rules(description or "", flow)
            plan.assignments[key] = ((builtin_match[0], "builtin_rule", builtin_match[1]) if builtin_match
                                     else (PAYMENTS, "card_payment", None))
            continue
        if user_match:
            plan.assignments[key] = (user_match[0], "user_rule", user_match[1])
            plan.rules_used.add((user_match[1], user_match[2]))
            continue
        builtin_match = match_builtin_rules(description or "", flow)
        if builtin_match:
            plan.assignments[key] = (builtin_match[0], "builtin_rule", builtin_match[1])
            continue
        if flow not in offered_for:
            offered_for[flow] = tuple(ai_categories(conn, user_id, flow))
        offered = offered_for[flow]
        unmatched.setdefault((_llm_list_kind(flow), tuple(offered)), []).append((key, description))

    prompts, raws, errors = [], [], []
    for (kind, offered), group in unmatched.items():
        args = (ollama_url, ollama_model, [d for _, d in group])
        # The default list (the built-in spending one) is asked for without naming it.
        if kind == "spending" and sorted(offered) == sorted(SPENDING_CATEGORIES):
            llm_categories, prompt, raw_response, error = categorize_via_llm(*args)
        else:
            llm_categories, prompt, raw_response, error = categorize_via_llm(*args, categories=list(offered), kind=kind)
        if prompt:
            prompts.append(prompt)
            raws.append(raw_response or "")
        if error:
            errors.append(error)
        for key, description in group:
            category = llm_categories.get(description)
            plan.assignments[key] = (category, "llm", None) if category else (UNCATEGORIZED, "uncategorized_fallback", None)
    if unmatched:
        plan.llm = ("\n\n".join(prompts), "\n\n".join(raws) if any(raws) else None,
                    "; ".join(errors) if errors else None)
    return plan


def apply_categories(conn, plan: CategoryPlan, ids: dict | None = None, *,
                     statement_id: int | None = None, csv_import_id: int | None = None) -> None:
    """Write a plan: each row key's transaction id is ids[key] (or the key itself). The person's rules that
    matched get last_matched_at."""
    conn.executemany(
        "UPDATE transactions SET category = ?, category_source = ?, category_matched_pattern = ? WHERE id = ?",
        [(c, src, pattern, ids[key] if ids is not None else key) for key, (c, src, pattern) in plan.assignments.items()],
    )
    if plan.user_id:
        touch_rules(conn, plan.user_id, plan.rules_used)
    if plan.llm is not None:
        prompt, raw_response, error = plan.llm
        if statement_id is not None:
            conn.execute(
                "UPDATE statements SET categorization_llm_prompt = ?, "
                "categorization_llm_raw_response = ?, categorization_llm_error = ? WHERE id = ?",
                (prompt, raw_response, error, statement_id),
            )
        elif csv_import_id is not None:
            conn.execute(
                "UPDATE csv_imports SET categorization_llm_prompt = ?, "
                "categorization_llm_raw_response = ?, categorization_llm_error = ? WHERE id = ?",
                (prompt, raw_response, error, csv_import_id),
            )


def categorize_transactions(
    conn, user_id: str, ollama_url: str, ollama_model: str, rows: list[tuple],
    *, statement_id: int | None = None, csv_import_id: int | None = None,
) -> None:
    """Categorize rows (transaction_id, description[, flow]) already inserted: always writes a
    category (explicit "Uncategorized" as the last resort) plus category_source /
    category_matched_pattern. With an AI address this may call the LLM — use plan_categories
    first and apply_categories in a short transaction instead when that matters."""
    apply_categories(conn, plan_categories(conn, user_id, ollama_url, ollama_model, rows),
                     statement_id=statement_id, csv_import_id=csv_import_id)
