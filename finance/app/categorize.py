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

from . import ai_client, settings

logger = logging.getLogger(__name__)

UNCATEGORIZED = "Uncategorized"

# Shown in category dropdowns app-wide — not the full set of categories
# that can ever exist (a saved rule or a manual edit can use any string),
# just a sane starting menu. A transaction already carrying a category
# outside this list still displays correctly (see _category_field.html),
# it just isn't itself one of the quick-pick options.
DISPLAY_CATEGORIES = [
    "Groceries", "Dining", "Transportation", "Shopping", "Subscriptions",
    "Utilities", "Gas", "Health", "Entertainment", "Travel", "Housing",
    "Income", "Payments", "Other", UNCATEGORIZED,
]

_SPECIFICITY = {"exact": 3, "prefix": 2, "substring": 1}

# Starter set, deliberately generic (SPEC.md section 4's own
# philosophy applied here too) — grows from real corrections via
# save_rule, not guessed at exhaustively in advance.
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
]

_LLM_PROMPT_TEMPLATE = """Categorize each of these bank/credit-card transaction
descriptions into a short spending category (examples: Groceries, Dining,
Transportation, Shopping, Subscriptions, Utilities, Gas, Health,
Entertainment, Travel, Housing, Income, Other). Respond with ONLY a JSON
object, no other text, in this exact shape:
{{"categories": {{"<description>": "<category>", ...}}}}
Descriptions:
{descriptions}"""


def _matches(pattern: str, match_type: str, description: str) -> bool:
    p, d = pattern.upper(), description.upper()
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
    for pattern, match_type, category in rules:
        if not _matches(pattern, match_type, description):
            continue
        key = (_SPECIFICITY[match_type], len(pattern))
        if best_key is None or key > best_key:
            best_key, best_category, best_pattern = key, category, pattern
    if best_category is None:
        return None
    return best_category, best_pattern


def match_builtin_rules(description: str) -> tuple[str, str] | None:
    return _best_match(_BUILTIN_RULES, description)


def save_rule(conn, user_id: str, pattern: str, match_type: str, category: str) -> None:
    """Upserts on (user_id, pattern, match_type) — correcting the same
    merchant's category again updates the existing rule rather than
    piling up near-duplicate rows that would just tie against each other
    on the next match."""
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


def list_all_categories(conn, user_id: str) -> list[str]:
    """The full set of category names to offer in any dropdown —
    DISPLAY_CATEGORIES' starter set merged with this user's own custom
    categories (Categories page's "Add a category" form, migration
    0002's `custom_categories` table), deduplicated case-insensitively
    and sorted alphabetically, with "Uncategorized" always pinned last
    since it's the explicit not-categorized state, not a class of
    spending. Every route that renders a category `<select>` should call
    this instead of using DISPLAY_CATEGORIES directly, so a user-added
    category actually shows up everywhere, not just on the page they
    added it from."""
    custom = [r["name"] for r in conn.execute(
        "SELECT name FROM custom_categories WHERE user_id = ? ORDER BY name COLLATE NOCASE",
        (user_id,),
    ).fetchall()]

    seen: set[str] = set()
    merged: list[str] = []
    for c in DISPLAY_CATEGORIES + custom:
        if c == UNCATEGORIZED:
            continue
        key = c.lower()
        if key not in seen:
            seen.add(key)
            merged.append(c)

    merged.sort(key=str.lower)
    merged.append(UNCATEGORIZED)
    return merged


def create_custom_category(conn, user_id: str, name: str) -> tuple[bool, str]:
    """Adds a user-defined category (Categories page). Case-insensitive
    de-duplication against both DISPLAY_CATEGORIES and the user's
    existing custom categories — "Groceries" and "groceries" are the
    same category to a person, even though the table's own UNIQUE
    constraint is case-sensitive and wouldn't catch that on its own.
    Returns (True, name) on success, or (False, <reason>) with nothing
    written if it's blank, too long, or already exists in some case."""
    name = name.strip()
    if not name:
        return False, "Category name is required"
    if len(name) > 60:
        return False, "Category name is too long (max 60 characters)"

    existing_lower = {c.lower() for c in DISPLAY_CATEGORIES}
    existing_lower |= {
        r["name"].lower() for r in conn.execute(
            "SELECT name FROM custom_categories WHERE user_id = ?", (user_id,)
        ).fetchall()
    }
    if name.lower() in existing_lower:
        return False, f'"{name}" already exists'

    conn.execute("INSERT INTO custom_categories (user_id, name) VALUES (?, ?)", (user_id, name))
    return True, name


def categorize_via_llm(
    ollama_url: str, ollama_model: str, descriptions: list[str],
) -> tuple[dict[str, str], str, str | None, str | None]:
    """One batched LLM call for every still-unmatched description. Returns
    (categories, prompt, raw_response, error): categories is {} on any
    failure (rows then stay Uncategorized — this never fails an import);
    prompt is "" when no call was attempted; raw_response is None when the
    call never completed. The caller stores the last three for the debug
    view; this function does no DB access."""
    if not ollama_url or not ollama_model or not descriptions:
        return {}, "", None, None

    prompt = _LLM_PROMPT_TEMPLATE.format(descriptions="\n".join(f"- {d}" for d in descriptions))
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
    parsed = {k: str(v) for k, v in categories.items() if isinstance(v, (str, int, float))}
    return parsed, prompt, raw_body, None


def recategorize_transactions(conn, user_id: str) -> int:
    """Re-runs the user's current rule set against EVERY one of the
    user's transactions (not just Uncategorized ones), updating any
    whose description a rule now matches — including overwriting a
    category that was already set, whether that came from a human's
    manual edit, an earlier rule, or the LLM fallback. Called right
    after a rule is saved (routes/categories.py's create_category_rule,
    routes/transactions.py's set_transaction_category) so existing
    transactions benefit from the correction retroactively, not just
    ones imported after the rule existed.

    Deliberately unscoped by current category, per explicit user
    request — an earlier version of this only touched rows still at
    "Uncategorized"; this one applies to all of them. A row is still
    left alone if nothing in the user's current rule set matches its
    description at all — there's nothing to "apply" there — but a row
    that DOES match is updated regardless of what it's currently
    categorized as.

    Uses the FULL current rule set (via _best_match), not just the rule
    that was just saved — so if more than one of the user's rules now
    matches a given description, the same specificity resolution the
    import-time cascade uses (exact > prefix > substring, longer
    pattern then most-recent as tiebreakers) picks the winner, rather
    than blindly applying whichever rule triggered this call. Returns
    the number of transactions updated, for the caller to report back
    to the user."""
    user_rules = conn.execute(
        "SELECT pattern, match_type, category FROM category_rules WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    user_rule_tuples = [(r["pattern"], r["match_type"], r["category"]) for r in user_rules]

    rows = conn.execute(
        "SELECT id, description, category FROM transactions "
        "WHERE user_id = ? AND deleted_at IS NULL",
        (user_id,),
    ).fetchall()

    count = 0
    for row in rows:
        match = _best_match(user_rule_tuples, row["description"])
        if match and match[0] != row["category"]:
            category, pattern = match
            conn.execute(
                "UPDATE transactions SET category = ?, category_source = 'user_rule', "
                "category_matched_pattern = ? WHERE id = ?",
                (category, pattern, row["id"]),
            )
            count += 1
    return count


def categorize_transactions(
    conn, user_id: str, ollama_url: str, ollama_model: str, rows: list[tuple[int, str]],
    *, statement_id: int | None = None, csv_import_id: int | None = None,
) -> None:
    """Categorize rows (transaction_id, description) just inserted for one
    statement or one CSV import's blank-category rows: always writes a
    category (explicit "Uncategorized" as the last resort) plus
    category_source / category_matched_pattern. Rules are loaded once per
    batch. Pass statement_id or csv_import_id so an LLM call's prompt and
    response are stored on that row."""
    user_rules = conn.execute(
        "SELECT pattern, match_type, category FROM category_rules WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    user_rule_tuples = [(r["pattern"], r["match_type"], r["category"]) for r in user_rules]

    unmatched: list[tuple[int, str]] = []
    for txn_id, description in rows:
        user_match = _best_match(user_rule_tuples, description)
        if user_match:
            category, pattern = user_match
            conn.execute(
                "UPDATE transactions SET category = ?, category_source = 'user_rule', "
                "category_matched_pattern = ? WHERE id = ?",
                (category, pattern, txn_id),
            )
            continue
        builtin_match = match_builtin_rules(description)
        if builtin_match:
            category, pattern = builtin_match
            conn.execute(
                "UPDATE transactions SET category = ?, category_source = 'builtin_rule', "
                "category_matched_pattern = ? WHERE id = ?",
                (category, pattern, txn_id),
            )
            continue
        unmatched.append((txn_id, description))

    if unmatched:
        llm_categories, prompt, raw_response, error = categorize_via_llm(
            ollama_url, ollama_model, [d for _, d in unmatched],
        )

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

        for txn_id, description in unmatched:
            category = llm_categories.get(description)
            if category:
                conn.execute(
                    "UPDATE transactions SET category = ?, category_source = 'llm' WHERE id = ?",
                    (category, txn_id),
                )
            else:
                conn.execute(
                    "UPDATE transactions SET category = ?, category_source = 'uncategorized_fallback' "
                    "WHERE id = ?",
                    (UNCATEGORIZED, txn_id),
                )
