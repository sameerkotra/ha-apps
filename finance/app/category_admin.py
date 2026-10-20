"""The Categories page's work (SPEC.md section 9, "The Categories page"): what each category and rule is used for,
rename / merge / move / hide / colour / delete, the rules table and its Test, rule suggestions from the person's own
corrections, and "Sort my income". routes/categories.py is the pages and forms; categorize.py the cascade.

Every function takes the acting person's id and touches only their rows. A category name is stored as text in
transactions.category, category_rules.category, skipped_duplicates.category and
category_suggestion_dismissals.category (recurring_overrides stores a merchant, never a category): rename, merge and
delete update all of them in the caller's one transaction.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import categorize as cz

NAME_COLUMNS = (("transactions", "category"), ("category_rules", "category"), ("skipped_duplicates", "category"))
SUGGEST_AFTER = 3                  # manual changes of one merchant to one category before a rule is offered
TEST_LIMIT = 10                    # a rule Test lists this many matches


class Problem(ValueError):
    """Something the person asked for that can't be done; the message is for them."""


# ------------------------------------------------------------------ the category lists (A, B)

def _month_bounds(today: date) -> tuple[str, str]:
    return today.replace(day=1).isoformat(), f"{today.isoformat()[:7]}-31"


def category_rows(conn, user_id: str, today: date | None = None) -> list[dict]:
    """all_categories with, for each: this month's total (spending: what the Overview counts as spend in it;
    income: what it counts as income), transactions in the last 12 months (any direction) and the rules pointing
    to it (the person's and the built-ins)."""
    today = today or date.today()
    start, end = _month_bounds(today)
    counted = ("t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND a.deleted_at IS NULL "
               "AND t.txn_type != 'transfer' AND t.is_excluded = 0 AND t.date >= ? AND t.date <= ?")
    spend = {r[0]: r[1] for r in conn.execute(
        f"SELECT COALESCE(t.category, 'Uncategorized'), SUM({cz.SPEND_AMOUNT_SQL}) FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {counted} AND {cz.SPEND_SQL} "
        "GROUP BY COALESCE(t.category, 'Uncategorized')", (user_id, start, end))}
    income = {r[0]: r[1] for r in conn.execute(
        f"SELECT COALESCE(t.category, 'Uncategorized'), SUM(t.amount) FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {counted} AND {cz.INCOME_SQL} "
        "GROUP BY COALESCE(t.category, 'Uncategorized')", (user_id, start, end))}
    since = (today - timedelta(days=365)).isoformat()
    used = {r[0]: r[1] for r in conn.execute(
        "SELECT COALESCE(t.category, 'Uncategorized'), COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND a.deleted_at IS NULL AND t.date >= ? AND t.date <= ? "
        "GROUP BY COALESCE(t.category, 'Uncategorized')", (user_id, since, today.isoformat()))}
    rules = Counter(r[0] for r in conn.execute("SELECT category FROM category_rules WHERE user_id = ?", (user_id,)))
    rules.update(c for _p, _m, c in cz._BUILTIN_RULES)
    out = []
    for c in cz.all_categories(conn, user_id):
        figures = income if c["kind"] == "income" else spend
        out.append(dict(c, month_total=round(figures.get(c["name"]) or 0.0, 2), used_12m=used.get(c["name"], 0),
                        rules=rules.get(c["name"], 0)))
    return out


SORTS = ("name", "total", "use")


def sort_rows(rows: list[dict], how: str) -> list[dict]:
    if how == "total":
        return sorted(rows, key=lambda r: (-r["month_total"], r["name"].lower()))
    if how == "use":
        return sorted(rows, key=lambda r: (-r["used_12m"], r["name"].lower()))
    return sorted(rows, key=lambda r: r["name"].lower())


def uncategorized_counts(conn, user_id: str) -> dict[str, int]:
    """{"spending": n, "income": n}: rows still Uncategorized that the Overview counts as spending (the
    Transactions filter category=Uncategorized&flow=expense shows the same rows) or as income (what Sort my
    income looks at)."""
    base = ("t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND a.deleted_at IS NULL "
            "AND t.txn_type != 'transfer' AND t.is_excluded = 0 AND t.category = 'Uncategorized'")
    n = lambda cond: conn.execute(f"SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
                                  f"WHERE {base} AND {cond}", (user_id,)).fetchone()[0]
    return {"spending": n(cz.SPEND_SQL), "income": n(cz.INCOME_SQL)}


# ------------------------------------------------------------------ rename, merge, move, hide, colour, delete (C)

def _custom(conn, user_id: str, cid: int):
    row = conn.execute("SELECT id, name, kind, colour FROM custom_categories WHERE id = ? AND user_id = ?",
                       (cid, user_id)).fetchone()
    if row is None:
        raise Problem("That category isn't one of yours")
    return row


def _category(conn, user_id: str, name: str) -> dict:
    c = cz.find_category(conn, user_id, name)
    if c is None:
        raise Problem(f"There's no category called “{(name or '').strip()[:60]}”")
    return c


def _move_name(conn, user_id: str, old: str, new: str) -> dict[str, int]:
    """Every stored use of the name `old` becomes `new` (the person's rows only). Returns {table: rows}."""
    counts = {}
    for table, col in NAME_COLUMNS:
        counts[table] = conn.execute(f"UPDATE {table} SET {col} = ? WHERE user_id = ? AND {col} = ?",
                                     (new, user_id, old)).rowcount
    conn.execute("UPDATE OR IGNORE category_suggestion_dismissals SET category = ? WHERE user_id = ? AND category = ?",
                 (new, user_id, old))
    conn.execute("DELETE FROM category_suggestion_dismissals WHERE user_id = ? AND category = ?", (user_id, old))
    return counts


def rename(conn, user_id: str, cid: int, new_name: str) -> str:
    """The person's own category gets a new name; every row, rule, skipped row and dismissed suggestion using it
    follows. A built-in can't be renamed (merge it instead)."""
    row = _custom(conn, user_id, cid)
    ok, name = cz.check_category_name(conn, user_id, new_name, except_id=cid)
    if not ok:
        raise Problem(name)
    if name != row["name"]:
        conn.execute("UPDATE custom_categories SET name = ? WHERE id = ?", (name, cid))
        _move_name(conn, user_id, row["name"], name)
    return name


def merge(conn, user_id: str, source: str, target: str) -> dict:
    """All of `source`'s rows and rules move to `target` (a category of the same kind), then `source` goes: the
    person's own is deleted, a built-in is hidden from the menus (it can't be deleted)."""
    s, t = _category(conn, user_id, source), _category(conn, user_id, target)
    if s["kind"] is None or t["kind"] is None:
        raise Problem("Payments and Uncategorized can't be merged")
    if s["name"] == t["name"]:
        raise Problem("Choose another category to merge into")
    if s["kind"] != t["kind"]:
        raise Problem(f"“{s['name']}” can only be merged into another {s['kind']} category")
    counts = _move_name(conn, user_id, s["name"], t["name"])
    if s["builtin"]:
        set_hidden(conn, user_id, s["name"], True)
    else:
        conn.execute("DELETE FROM custom_categories WHERE id = ? AND user_id = ?", (s["id"], user_id))
    return {"from": s["name"], "to": t["name"], **counts}


def set_kind(conn, user_id: str, cid: int, kind: str) -> dict:
    """Moves the person's own category to the other list. Its rows and rules keep the name: only the list it
    shows under (and its colour range) changes."""
    row = _custom(conn, user_id, cid)
    if kind not in cz.KINDS:
        raise Problem("Choose Spending or Income")
    colour = row["colour"] if row["colour"] in cz.palette_for(kind) else None
    conn.execute("UPDATE custom_categories SET kind = ?, colour = ? WHERE id = ?", (kind, colour, cid))
    return {"name": row["name"], "kind": kind}


def _pref(conn, user_id: str, name: str, **fields) -> None:
    conn.execute("INSERT OR IGNORE INTO category_prefs (user_id, name) VALUES (?, ?)", (user_id, name))
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE category_prefs SET {sets}, updated_at = datetime('now') WHERE user_id = ? AND name = ?",
                 (*fields.values(), user_id, name))


def set_hidden(conn, user_id: str, name: str, hidden: bool) -> str:
    """Hide a built-in from every menu (old rows keep it, and a rule pointing to it still works), or show it."""
    builtin = cz.builtin_name(name)
    if builtin is None or cz.BUILTIN_KIND.get(builtin) is None:
        raise Problem("Only a built-in spending or income category can be hidden")
    _pref(conn, user_id, builtin, hidden=1 if hidden else 0)
    return builtin


def set_colour(conn, user_id: str, name: str, colour: str) -> str:
    """A category's colour, from its kind's range of the palette ("" = back to its default)."""
    c = _category(conn, user_id, name)
    if c["kind"] is None:
        raise Problem("Payments and Uncategorized keep their grey")
    key = (colour or "").strip() or None
    if key is not None and key not in cz.palette_for(c["kind"]):
        raise Problem("Choose one of the colours shown")
    if c["builtin"]:
        _pref(conn, user_id, c["name"], colour=key)
    else:
        conn.execute("UPDATE custom_categories SET colour = ? WHERE id = ? AND user_id = ?", (key, c["id"], user_id))
    return c["name"]


def delete(conn, user_id: str, cid: int, move_to: str = "") -> dict:
    """Deletes the person's own category; its rows (and skipped rows) go to `move_to` (Uncategorized by default)
    and so do its rules — except into Uncategorized, where its rules are deleted (a rule that only un-sorts rows
    isn't one)."""
    row = _custom(conn, user_id, cid)
    target = (move_to or "").strip() or cz.UNCATEGORIZED
    if target != cz.UNCATEGORIZED:
        target = _category(conn, user_id, target)["name"]
    if target == row["name"]:
        raise Problem("Choose where its transactions should go")
    if target == cz.UNCATEGORIZED:
        conn.execute("DELETE FROM category_rules WHERE user_id = ? AND category = ?", (user_id, row["name"]))
    counts = _move_name(conn, user_id, row["name"], target)
    conn.execute("DELETE FROM custom_categories WHERE id = ? AND user_id = ?", (cid, user_id))
    return {"name": row["name"], "to": target, **counts}


# ------------------------------------------------------------------ the rules table (D)

def _descriptions(conn, user_id: str) -> Counter:
    """{upper-cased description: live transactions with it}."""
    return Counter({d: n for d, n in conn.execute(
        "SELECT upper(COALESCE(description, '')), COUNT(*) FROM transactions WHERE user_id = ? AND deleted_at IS NULL "
        "GROUP BY upper(COALESCE(description, ''))", (user_id,))})


def _match_count(descriptions: Counter, pattern: str, match_type: str) -> int:
    p = (pattern or "").upper()
    return sum(n for d, n in descriptions.items() if cz._matches_upper(p, match_type, d))


def _specificity(r: dict) -> tuple:
    return (r["category"].lower(), -cz._SPECIFICITY.get(r["match_type"], 0), -len(r["pattern"]))


def rules_table(conn, user_id: str) -> list[dict]:
    """Every rule, the person's first, then the built-ins, each by category in the order the cascade tries them:
    {id, pattern, match_type, category, kind, matches (live transactions it matches), last_matched, builtin,
    overridden (a built-in that one of the person's rules with the same text and type replaces)}."""
    kinds = {c["name"]: c["kind"] for c in cz.all_categories(conn, user_id)}
    descriptions = _descriptions(conn, user_id)
    mine = [dict(r) for r in conn.execute(
        "SELECT id, pattern, match_type, category, last_matched_at FROM category_rules WHERE user_id = ?", (user_id,))]
    builtin_last = {r[0]: r[1] for r in conn.execute(
        "SELECT category_matched_pattern, MAX(created_at) FROM transactions WHERE user_id = ? AND deleted_at IS NULL "
        "AND category_source = 'builtin_rule' GROUP BY category_matched_pattern", (user_id,))}
    own_keys = {(r["pattern"].upper(), r["match_type"]) for r in mine}
    out = []
    for r in sorted(mine, key=_specificity):
        out.append({"id": r["id"], "pattern": r["pattern"], "match_type": r["match_type"], "category": r["category"],
                    "kind": kinds.get(r["category"]), "matches": _match_count(descriptions, r["pattern"], r["match_type"]),
                    "last_matched": (r["last_matched_at"] or "")[:10] or None, "builtin": False, "overridden": False})
    for r in sorted(cz.builtin_rules(), key=_specificity):
        out.append({"id": None, **r, "matches": _match_count(descriptions, r["pattern"], r["match_type"]),
                    "last_matched": (builtin_last.get(r["pattern"]) or "")[:10] or None, "builtin": True,
                    "overridden": (r["pattern"].upper(), r["match_type"]) in own_keys})
    return out


def filter_rules(rules: list[dict], *, q: str = "", who: str = "", kind: str = "", category: str = "") -> list[dict]:
    """The search box (pattern or category, any case) and the chips: Mine / Built in, Spending / Income, one
    category."""
    q = (q or "").strip().lower()
    out = []
    for r in rules:
        if q and q not in r["pattern"].lower() and q not in r["category"].lower():
            continue
        if who == "mine" and r["builtin"] or who == "builtin" and not r["builtin"]:
            continue
        if kind in cz.KINDS and r["kind"] != kind:
            continue
        if category and r["category"] != category:
            continue
        out.append(r)
    return out


def check_rule(pattern: str, match_type: str, category: str) -> tuple[str, str, str]:
    """The cleaned (pattern, match_type, category), or Problem — the same limits as the Add a rule form."""
    pattern, category = (pattern or "").strip(), " ".join((category or "").split())
    if not pattern:
        raise Problem("Match text is required")
    if not category:
        raise Problem("Category is required")
    if len(pattern) > 200:
        raise Problem("Match text is too long (max 200 characters)")
    if len(category) > cz.MAX_CATEGORY_CHARS:
        raise Problem(f"Category is too long (max {cz.MAX_CATEGORY_CHARS} characters)")
    return pattern, match_type if match_type in cz.MATCH_TYPES else "substring", category


def _rules_with(conn, user_id: str, pattern: str, match_type: str, category: str,
                rule_id: int | None = None) -> list[tuple[str, str, str]]:
    """The person's rules (newest first, as the cascade orders them) as they'd be once this rule is saved: an
    edited rule in its own place, a new one first, and one with the same text and type replaced (save_rule's
    upsert)."""
    rows = conn.execute("SELECT id, pattern, match_type, category FROM category_rules WHERE user_id = ? "
                        "ORDER BY created_at DESC, id DESC", (user_id,)).fetchall()
    out, placed = [], False
    for r in rows:
        if r["id"] == rule_id or (rule_id is None and r["pattern"] == pattern and r["match_type"] == match_type):
            if not placed:
                out.append((pattern, match_type, category))
                placed = True
            continue
        if rule_id is not None and r["pattern"] == pattern and r["match_type"] == match_type:
            continue
        out.append((r["pattern"], r["match_type"], r["category"]))
    if not placed:
        out.insert(0, (pattern, match_type, category))
    return out


def try_rule(conn, user_id: str, pattern: str, match_type: str, category: str, rule_id: int | None = None) -> dict:
    """A rule tried before it's saved — nothing is written: the first TEST_LIMIT transactions it matches (newest
    first; each with its category now and the one the rules would give it), how many it matches in all, and how
    many "Also re-categorize existing transactions" would change when it's saved (the same rows
    recategorize_for_rule then changes)."""
    pattern, match_type, category = check_rule(pattern, match_type, category)
    if rule_id is not None and conn.execute("SELECT 1 FROM category_rules WHERE id = ? AND user_id = ?",
                                            (rule_id, user_id)).fetchone() is None:
        rule_id = None
    rules = _rules_with(conn, user_id, pattern, match_type, category, rule_id)
    changes = {c["id"]: c["to"] for c in cz.rule_changes(conn, user_id, pattern, match_type, rules=rules)}
    p = pattern.upper()
    conn.create_function("finance_rule_test", 1, lambda d: cz._matches_upper(p, match_type, (d or "").upper()),
                         deterministic=True)
    where = ("t.user_id = ? AND t.deleted_at IS NULL AND a.deleted_at IS NULL AND finance_rule_test(t.description)")
    total = conn.execute(f"SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE {where}",
                         (user_id,)).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.id, t.date, t.description, t.amount, t.category, a.name AS account FROM transactions t "
        f"JOIN accounts a ON a.id = t.account_id WHERE {where} ORDER BY t.date DESC, t.id DESC LIMIT ?",
        (user_id, TEST_LIMIT)).fetchall()
    matches = [{"id": r["id"], "date": r["date"], "description": r["description"], "amount": round(r["amount"], 2),
                "account": r["account"], "category": r["category"], "to": changes.get(r["id"], r["category"])}
               for r in rows]
    return {"pattern": pattern, "match_type": match_type, "category": category, "matches": matches, "count": total,
            "will_change": len(changes)}


def edit_rule(conn, user_id: str, rule_id: int, pattern: str, match_type: str, category: str) -> dict:
    """Changes one of the person's rules in place (its text, match type or category); nothing else changes."""
    pattern, match_type, category = check_rule(pattern, match_type, category)
    if conn.execute("SELECT 1 FROM category_rules WHERE id = ? AND user_id = ?", (rule_id, user_id)).fetchone() is None:
        raise Problem("That rule isn't one of yours")
    clash = conn.execute("SELECT 1 FROM category_rules WHERE user_id = ? AND pattern = ? AND match_type = ? AND id != ?",
                         (user_id, pattern, match_type, rule_id)).fetchone()
    if clash:
        raise Problem("You already have a rule with that text and match type")
    conn.execute("UPDATE category_rules SET pattern = ?, match_type = ?, category = ? WHERE id = ? AND user_id = ?",
                 (pattern, match_type, category, rule_id, user_id))
    return {"pattern": pattern, "match_type": match_type, "category": category}


def copy_builtin(conn, user_id: str, pattern: str, match_type: str) -> int:
    """"Use my own instead": the built-in rule becomes one of the person's (the same text, type and category) to
    change; theirs is tried first, so it wins. Returns the rule's id (an existing one with that text and type)."""
    found = next(((p, mt, c) for p, mt, c in cz._BUILTIN_RULES if p == pattern and mt == match_type), None)
    if found is None:
        raise Problem("That isn't a built-in rule")
    existing = conn.execute("SELECT id FROM category_rules WHERE user_id = ? AND pattern = ? AND match_type = ?",
                            (user_id, found[0], found[1])).fetchone()
    if existing:
        return existing["id"]
    return conn.execute("INSERT INTO category_rules (user_id, pattern, match_type, category) VALUES (?, ?, ?, ?)",
                        (user_id, *found)).lastrowid


# ------------------------------------------------------------------ suggestions from the person's corrections (E)

_TOKEN = re.compile(r"[A-Z0-9&']+")


def merchant_of(description: str) -> str:
    """A merchant's name from a bank description: its first two words that aren't reference numbers
    ("AMAZON MKTPL*2K3J4 SEATTLE" → "AMAZON MKTPL")."""
    words = [w for w in _TOKEN.findall((description or "").upper())
             if any(ch.isalpha() for ch in w) and sum(ch.isdigit() for ch in w) * 2 < len(w)]
    return " ".join(words[:2])


def _rule_text(descriptions: list[str]) -> str:
    """What a "starts with" rule for these descriptions matches: their common beginning, whole words only."""
    ups = [(d or "").upper().strip() for d in descriptions]
    prefix = ups[0]
    for d in ups[1:]:
        n = 0
        while n < min(len(prefix), len(d)) and prefix[n] == d[n]:
            n += 1
        prefix = prefix[:n]
    if any(len(d) > len(prefix) and d[len(prefix)].isalnum() for d in ups) and prefix and prefix[-1].isalnum():
        prefix = prefix[:max(prefix.rfind(" "), 0)]          # it ended inside a word: back to the last whole one
    return prefix.rstrip(" *#-/.,:;")


def suggestions(conn, user_id: str) -> list[dict]:
    """Rules worth offering: a merchant the person set by hand (category_source = 'manual') to the same category
    SUGGEST_AFTER times or more, that their rules don't already give, and that they haven't dismissed. Worked out
    each time from the rows, never stored. Each {merchant, category, kind, times, pattern, match_type}."""
    groups: dict[tuple, list[str]] = defaultdict(list)
    for d, c in conn.execute(
            "SELECT description, category FROM transactions WHERE user_id = ? AND deleted_at IS NULL "
            "AND category_source = 'manual' AND category IS NOT NULL AND category != 'Uncategorized'", (user_id,)):
        m = merchant_of(d)
        if m:
            groups[(m, c)].append(d)
    if not groups:
        return []
    dismissed = {(r[0], r[1]) for r in conn.execute(
        "SELECT merchant, category FROM category_suggestion_dismissals WHERE user_id = ?", (user_id,))}
    rules_upper = [(p.upper(), mt, c, p) for p, mt, c in cz._user_rules(conn, user_id)]
    kinds = {c["name"]: c["kind"] for c in cz.all_categories(conn, user_id)}
    out = []
    for (merchant, category), descriptions in groups.items():
        if len(descriptions) < SUGGEST_AFTER or (merchant, category) in dismissed:
            continue
        text = _rule_text(descriptions)
        if len(text) < 3:
            continue
        given = [cz._best_match_upper(rules_upper, (d or "").upper()) for d in descriptions]
        if all(g is not None and g[0] == category for g in given):
            continue                                   # a rule of theirs already does it
        out.append({"merchant": merchant, "category": category, "kind": kinds.get(category) or "spending",
                    "times": len(descriptions), "pattern": text, "match_type": "prefix"})
    out.sort(key=lambda s: (-s["times"], s["merchant"], s["category"]))
    return out


def make_suggested_rule(conn, user_id: str, merchant: str, category: str) -> dict:
    found = next((s for s in suggestions(conn, user_id) if s["merchant"] == merchant and s["category"] == category),
                 None)
    if found is None:
        raise Problem("That suggestion isn't offered any more")
    cz.save_rule(conn, user_id, found["pattern"], found["match_type"], found["category"])
    return found


def dismiss_suggestion(conn, user_id: str, merchant: str, category: str) -> None:
    merchant, category = (merchant or "").strip()[:200], (category or "").strip()[:60]
    if not merchant or not category:
        raise Problem("Nothing to dismiss")
    conn.execute("INSERT OR IGNORE INTO category_suggestion_dismissals (user_id, merchant, category) VALUES (?, ?, ?)",
                 (user_id, merchant, category))


# ------------------------------------------------------------------ Sort my income (section 9, §5)

SORTABLE = ("Other income", "Income", cz.UNCATEGORIZED)
SOURCES = ("user_rule", "builtin_rule", "llm")


def income_rows(conn, user_id: str, ids: list[int] | None = None) -> list:
    """Money in on checking/savings (not transfers) that is still Other income, Income or Uncategorized."""
    extra, params = "", [user_id]
    if ids is not None:
        extra = f" AND t.id IN ({','.join('?' * len(ids))})"
        params += ids
    return conn.execute(
        "SELECT t.id, t.date, t.description, t.amount, t.category, a.name AS account FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id WHERE t.user_id = ? AND t.deleted_at IS NULL AND a.deleted_at IS NULL "
        f"AND {cz.INCOME_SQL} AND t.txn_type != 'transfer' "
        f"AND COALESCE(t.category, 'Uncategorized') IN ({','.join('?' * len(SORTABLE))}){extra} "
        "ORDER BY t.date DESC, t.id DESC", (*params[:1], *SORTABLE, *params[1:])).fetchall()


def plan_income(conn, user_id: str, ai_url: str, ai_model: str) -> list[dict]:
    """What Sort my income would change, written nowhere: the rules, then the AI (offered the income list) for
    what no rule matched. Each {id, date, description, amount, account, category (now), to, source, pattern,
    match_type}. The caller must not hold a write transaction (the AI can take minutes)."""
    rows = income_rows(conn, user_id)
    if not rows:
        return []
    plan = cz.plan_categories(conn, user_id, ai_url, ai_model, [(r["id"], r["description"], "in") for r in rows])
    types = {p: mt for p, mt, _c in cz._user_rules(conn, user_id)}
    out = []
    for r in rows:
        to, source, pattern = plan.assignments.get(r["id"], (None, None, None))
        if not to or to == cz.UNCATEGORIZED or to == (r["category"] or cz.UNCATEGORIZED):
            continue
        out.append({"id": r["id"], "date": r["date"], "description": r["description"], "amount": round(r["amount"], 2),
                    "account": r["account"], "category": r["category"] or cz.UNCATEGORIZED, "to": to,
                    "source": source, "pattern": pattern or "",
                    "match_type": types.get(pattern, "") if source == "user_rule" else ""})
    return out


def apply_income(conn, user_id: str, chosen: list[dict]) -> int:
    """Writes only the confirmed changes ({id, to, source, pattern, match_type}): each must still be one of the
    person's sortable income rows. Returns how many changed."""
    by_id = {c["id"]: c for c in chosen}
    if not by_id:
        return 0
    live = {r["id"] for r in income_rows(conn, user_id, list(by_id))}
    used, n = set(), 0
    for tid, c in by_id.items():
        to = " ".join((c.get("to") or "").split())
        if tid not in live or not to or len(to) > cz.MAX_CATEGORY_CHARS:
            continue
        source = c.get("source") if c.get("source") in SOURCES else "manual"
        pattern = (c.get("pattern") or "")[:200] or None
        conn.execute("UPDATE transactions SET category = ?, category_source = ?, category_matched_pattern = ? "
                     "WHERE id = ? AND user_id = ?", (to, source, pattern if source != "manual" else None, tid, user_id))
        if source == "user_rule" and pattern and c.get("match_type") in cz.MATCH_TYPES:
            used.add((pattern, c["match_type"]))
        n += 1
    cz.touch_rules(conn, user_id, used)
    return n
