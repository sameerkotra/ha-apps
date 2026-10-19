"""Saving a QFX / OFX file as statements (SPEC.md section 4a): one `statements` row per bank or card block, in the
request (a worker thread, like the CSV import), with no job queue and no AI reading.

    import_file(conn, user_id, filename, raw, file_hash, picked, choices, confirm_last4) -> result for the page

Which account each block goes to (section 4a, "Which account"): the account picked on the form for a one-block
file, else the person's account with the same last 4 and a matching type. When that can't be settled — the
picked account's last 4 differs from the file's, nothing matches, several accounts match, the file has no number —
nothing is saved and the result says `needs_account`, with a question per block; the page asks and sends the file
again with the person's `choices` ({block index: account id, or {"new": name}}).

Everything for one file is written in one `BEGIN IMMEDIATE` transaction: accounts created for it, an empty last 4
filled in, the statements, their rows, the rows skipped as duplicates (`skipped_duplicates`, for **Insert anyway**)
and transfer linking. Categories come after the commit, as for a CSV (the AI may take minutes). Only the last 4
digits of an account number are ever stored, shown or logged.
"""
import json
import logging
import sqlite3

from . import ai_usage, ofx, settings
from .amounts import to_cents
from .categorize import apply_categories, plan_categories
from .matching import relink

logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = "USD"
MAX_DETAIL_ROWS = 5000          # rows kept in a statement's debug record
MAX_ACCOUNT_NAME = 100


def _accounts(conn, user_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, type, account_number_last4 AS last4 FROM accounts "
        "WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0 ORDER BY name", (user_id,))]


def _ending(last4: str | None) -> str:
    return f"ending {last4}" if last4 else "with no account number"


def _new_name(f: ofx.OfxFile, b: ofx.Block, taken: set) -> str:
    """'Chase 1234' (the file's <ORG>, else the account type, and the last 4), made unique among their accounts."""
    return _unique(" ".join(p for p in ((f.org or b.type_label), b.last4 or "") if p)[:MAX_ACCOUNT_NAME - 4], taken)


def _unique(base: str, taken: set) -> str:
    name, n = base, 2
    while name.lower() in taken:
        name, n = f"{base} ({n})", n + 1
    return name


def _type_fits(block: ofx.Block, account: dict, exact: bool) -> bool:
    if block.account_type == "credit_card" or account["type"] == "credit_card":
        return block.account_type == account["type"]
    return block.account_type == account["type"] or not exact


def _by_number(block: ofx.Block, accounts: list[dict]) -> list[dict]:
    """The accounts with the block's last 4 and its type (a bank block also takes the other bank type when no
    account has the exact one: OFX's CHECKING/SAVINGS isn't always what the person called it)."""
    if not block.last4:
        return []
    same = [a for a in accounts if (a["last4"] or "") == block.last4]
    return [a for a in same if _type_fits(block, a, True)] or [a for a in same if _type_fits(block, a, False)]


def _resolve(f: ofx.OfxFile, accounts: list[dict], picked: dict | None, choices: dict, confirm_last4: bool):
    """({block index: account id or {"new": name}}, [questions]) — questions is empty when every block is settled."""
    by_id = {a["id"]: a for a in accounts}
    taken = {a["name"].strip().lower() for a in accounts}
    settled, questions, used = {}, [], set()
    single = len(f.blocks) == 1
    for b in f.blocks:
        choice = choices.get(str(b.index))
        if isinstance(choice, dict) and str(choice.get("new") or "").strip():
            name = _unique(" ".join(str(choice["new"]).split())[:MAX_ACCOUNT_NAME - 4], taken)
            settled[b.index] = {"new": name}
            taken.add(name.lower())
            continue
        if choice is not None and str(choice).isdigit() and int(choice) in by_id:
            settled[b.index] = int(choice)
            used.add(int(choice))
            continue
        new_name = _new_name(f, b, taken)
        base = {"index": b.index, "last4": b.last4, "type": b.account_type, "type_label": b.type_label,
                "period": ofx.period_label(b.start, b.end), "rows": len(b.usable), "new_name": new_name}
        if picked is not None and (single or (not b.last4 and picked["type"] == b.account_type)):
            if b.last4 and picked["last4"] and b.last4 != picked["last4"] and not confirm_last4:
                questions.append({**base, "reason": "last4_mismatch", "suggested": picked["id"],
                                  "question": f"This file is for an account ending {b.last4}, but {picked['name']} "
                                              f"ends {picked['last4']}. Use {picked['name']} anyway, or pick another "
                                              "account?"})
                continue
            settled[b.index] = picked["id"]
            used.add(picked["id"])
            continue
        matches = [a for a in _by_number(b, accounts) if a["id"] not in used]
        if len(matches) == 1:
            settled[b.index] = matches[0]["id"]
            used.add(matches[0]["id"])
            continue
        if picked is not None and not matches and picked["id"] not in used and _type_fits(b, picked, True) \
                and (not picked["last4"] or picked["last4"] == b.last4):
            settled[b.index] = picked["id"]          # several blocks: the picked account takes one it fits
            used.add(picked["id"])
            continue
        if len(matches) > 1:
            questions.append({**base, "reason": "several", "suggested": matches[0]["id"],
                              "question": f"More than one of your accounts ends {b.last4}. Which one is this "
                                          f"{b.type_label.lower()} statement for?"})
        elif not b.last4:
            questions.append({**base, "reason": "no_number", "suggested": "new",
                              "question": f"This {b.type_label.lower()} statement doesn't say which account it is "
                                          "for. Pick one of your accounts, or create it."})
        else:
            questions.append({**base, "reason": "no_match", "suggested": "new",
                              "question": f"None of your accounts ends {b.last4} ({b.type_label.lower()}). "
                                          f"Create it as “{new_name}”, or pick one of your accounts?"})
    return settled, questions


def parse_choices(raw: str) -> dict:
    """The page's answer to `needs_account`: {"0": 12, "1": {"new": "Chase 1234"}} (anything else is ignored)."""
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _already_uploaded(conn, user_id: str, file_hash: str):
    return conn.execute(
        "SELECT id, status, uploaded_at FROM statements WHERE user_id = ? AND file_hash = ? AND deleted_at IS NULL "
        "ORDER BY id LIMIT 1", (user_id, file_hash)).fetchone()


def _on_date(stamp: str | None) -> str:
    label = ofx.period_label((stamp or "")[:10], None)
    return label or (stamp or "")[:10]


def import_file(conn: sqlite3.Connection, user_id: str, filename: str | None, raw: bytes, file_hash: str,
                picked_id: int | None, choices: dict | None = None, confirm_last4: bool = False) -> dict:
    """One QFX/OFX file (see the module docstring). Returns the page's result for it."""
    choices = choices or {}
    try:
        f = ofx.parse(raw)
    except ofx.Refused as e:
        return {"filename": filename, "kind": "qfx", "error": str(e)}

    dup = _already_uploaded(conn, user_id, file_hash)
    if dup is not None:
        return {"filename": filename, "kind": "qfx", "duplicate_of": dup["id"],
                "message": f"Already uploaded on {_on_date(dup['uploaded_at'])}."}

    accounts = _accounts(conn, user_id)
    picked = next((a for a in accounts if a["id"] == picked_id), None) if picked_id is not None else None
    settled, questions = _resolve(f, accounts, picked, choices, confirm_last4)
    if questions:
        return {"filename": filename, "kind": "qfx", "needs_account": True, "blocks": questions,
                "accounts": [{"id": a["id"], "name": a["name"], "type": a["type"], "last4": a["last4"]}
                             for a in accounts]}

    try:
        conn.execute("BEGIN IMMEDIATE")
        saved = _save(conn, user_id, filename, f, file_hash, settled, accounts)
        relink(conn, user_id)              # an imported row can be one side of a transfer
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Saving QFX file %r failed", filename)
        return {"filename": filename, "kind": "qfx", "error": "Couldn't save that file — nothing was imported."}

    _categorize(conn, user_id, saved)
    return {
        "filename": filename, "kind": "qfx",
        "statements": [{k: s[k] for k in ("statement_id", "account_name", "created_account", "imported",
                                           "duplicates", "left_out", "status", "period", "error")}
                       for s in saved["statements"]],
        "notes": f.skipped + saved["warnings"],
    }


def _save(conn, user_id: str, filename: str | None, f: ofx.OfxFile, file_hash: str, settled: dict,
          accounts: list[dict]) -> dict:
    """Everything for the file, in the caller's transaction."""
    by_id = {a["id"]: a for a in accounts}
    out = {"statements": [], "to_categorize": [], "card_payments": [], "warnings": []}
    for b in f.blocks:
        target = settled[b.index]
        created = isinstance(target, dict)
        if created:
            account_id = conn.execute(
                "INSERT INTO accounts (user_id, name, type, bank_name, account_number_last4, preferred_upload_method) "
                "VALUES (?, ?, ?, ?, ?, 'qfx')",
                (user_id, target["new"], b.account_type, f.org or None, b.last4)).lastrowid
            account = {"id": account_id, "name": target["new"], "type": b.account_type, "last4": b.last4}
            by_id[account_id] = account
        else:
            account = by_id[target]
            if b.last4 and not account["last4"]:
                conn.execute("UPDATE accounts SET account_number_last4 = ? WHERE id = ? "
                             "AND (account_number_last4 IS NULL OR account_number_last4 = '')", (b.last4, account["id"]))
                account["last4"] = b.last4
        out["statements"].append(_save_block(conn, user_id, filename, f, b, file_hash, account, created, out))
    # Each statement's debug record also lists the file's other blocks and where they went.
    blocks = [{"index": b.index, "type": b.account_type, "last4": b.last4, "currency": b.currency or None,
               "start": b.start, "end": b.end, "rows": len(b.transactions),
               "account": s["account_name"], "statement_id": s["statement_id"]}
              for b, s in zip(f.blocks, out["statements"])]
    for s in out["statements"]:
        details = s["details"]
        details["file"] = {"version": f.version, "encoding": f.encoding, "org": f.org or None, "blocks": blocks,
                           "skipped": f.skipped}
        conn.execute("UPDATE statements SET import_details = ? WHERE id = ?", (json.dumps(details), s["statement_id"]))
    return out


def _save_block(conn, user_id, filename, f, b: ofx.Block, file_hash, account: dict, created: bool, out: dict) -> dict:
    if account["type"] == "credit_card":       # the account's convention decides (section 8)
        flip, rule = ofx.card_sign_rule(b)
    else:
        flip, rule = False, "Checking/savings: signs as written (negative = money out)."
    sign = -1 if flip else 1

    rows, notes, period_cents = [], [], 0
    inserted: list[tuple[int, str, float]] = []
    inserted_ids: set[int] = set()
    skipped: list[tuple] = []          # (date, amount, description, reason, matched id)
    seen_fitid: dict[str, int] = {}
    for t in b.transactions:
        detail = {"line": t.line, "type": t.trntype, "date": t.date, "date_from": t.date_from, "posted": t.posted,
                  "raw_amount": t.raw_amount, "fitid": t.fitid or None, "description": t.description}
        if t.problem:
            detail["outcome"] = f"left out: {t.problem}"
            notes.append(f"Row {t.line} ({t.description[:60]}): {t.problem} — left out.")
            rows.append(detail)
            continue
        amount = round(float(t.amount) * sign, 2)
        detail["amount"] = amount
        if t.fitid and t.fitid in seen_fitid:
            detail["outcome"] = f"same FITID as row {seen_fitid[t.fitid]} of this file — counted once"
            notes.append(f"Row {t.line} {t.date} {t.description!r} {amount:.2f}: same FITID as row "
                         f"{seen_fitid[t.fitid]} of this file — not inserted twice.")
            skipped.append((t.date, amount, t.description, "repeated", None))
            rows.append(detail)
            continue
        if t.fitid:
            seen_fitid[t.fitid] = t.line
        period_cents += to_cents(amount)

        match, why = None, None
        if t.fitid:
            hit = conn.execute(
                "SELECT id FROM transactions WHERE user_id = ? AND account_id = ? AND fitid = ? "
                "AND deleted_at IS NULL LIMIT 1", (user_id, account["id"], t.fitid)).fetchone()
            if hit is not None:
                match, why = hit[0], "fitid"
        if match is None:
            # Rows from PDFs and CSVs have no FITID: a row with a new FITID is only compared with those. A row with
            # none is compared with every row.
            hit = conn.execute(
                "SELECT id FROM transactions WHERE user_id = ? AND account_id = ? AND date = ? AND amount = ? "
                "AND description = ? AND deleted_at IS NULL" + (" AND fitid IS NULL" if t.fitid else "") + " LIMIT 1",
                (user_id, account["id"], t.date, amount, t.description)).fetchone()
            if hit is not None:
                match, why = hit[0], "same"
        if match is not None:
            repeated = match in inserted_ids
            if repeated:
                period_cents -= to_cents(amount)     # read twice in this file: counted once
            detail["outcome"] = ("duplicate: same FITID as transaction #%d" % match if why == "fitid" else
                                 "duplicate: same date, amount and description as an earlier row of this file"
                                 if repeated else
                                 "duplicate: same date, amount and description as transaction #%d" % match)
            notes.append(f"Row {t.line} {t.date} {t.description!r} {amount:.2f}: {detail['outcome']} — skipped.")
            skipped.append((t.date, amount, t.description, "repeated" if repeated else "on_file",
                            None if repeated else match))
            rows.append(detail)
            continue
        detail["outcome"] = "inserted"
        rows.append(detail)
        inserted.append((len(rows) - 1, t, amount))

    usable = [t for t in b.transactions if t.problem is None]
    dates = [t.date for t in usable]
    start = b.start or (min(dates) if dates else None)
    end = b.end or (max(dates) if dates else None)
    period = (end or "")[:7] or None

    new_balance = round(float(b.balance) * sign, 2) if b.balance is not None else None
    previous_balance = round(new_balance - period_cents / 100, 2) if new_balance is not None else None
    mismatch, continuity = None, None
    if previous_balance is not None and start:
        before = ofx.day_before(start)
        prev = conn.execute(
            "SELECT id, new_balance, period_end FROM statements WHERE user_id = ? AND account_id = ? "
            "AND deleted_at IS NULL AND period_end = ? AND new_balance IS NOT NULL ORDER BY id DESC LIMIT 1",
            (user_id, account["id"], before)).fetchone()
        if prev is not None:
            if abs(to_cents(prev["new_balance"]) - to_cents(previous_balance)) > 1:
                mismatch = (f"The previous statement (up to {prev['period_end']}) closed at {prev['new_balance']:.2f}, "
                            f"but this file's closing balance {new_balance:.2f} less its rows means it opened at "
                            f"{previous_balance:.2f} — a download in between may be missing.")
                continuity = mismatch
            else:
                continuity = (f"Opening balance {previous_balance:.2f} matches the previous statement's closing "
                              f"balance (up to {prev['period_end']}).")
        else:
            continuity = f"No earlier statement of this account ends on {before}: not checked."

    warnings = []
    if b.currency and b.currency != DEFAULT_CURRENCY:
        warnings.append(f"{account['name']}: the file's currency is {b.currency}; amounts are kept as written.")
    out["warnings"].extend(warnings)

    error = None
    if not usable:
        error = "No transaction in this file could be used (" + "; ".join(
            sorted({t.problem for t in b.transactions if t.problem})) + ")." if b.transactions else \
            "This statement in the file has no transactions."
    status = "error" if error else ("pending_review" if mismatch else "complete")
    details = {"block": b.index, "type": b.account_type, "last4": b.last4, "currency": b.currency or None,
               "start": start, "end": end, "sign_rule": rule, "continuity": continuity,
               "created_account": created, "warnings": warnings, "rows": rows[:MAX_DETAIL_ROWS],
               "rows_total": len(rows)}

    sid = conn.execute(
        "INSERT INTO statements (user_id, account_id, source, bank_name, statement_period, account_number_last4, "
        "original_filename, status, error_message, file_hash, pdf_path, needs_confirmation, previous_balance, "
        "new_balance, balance_mismatch, period_start, period_end, duplicate_transactions, processed_at) "
        "VALUES (?, ?, 'qfx', ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (user_id, account["id"], f.org or None, period, b.last4, filename, status, error, file_hash,
         previous_balance, new_balance, mismatch, start, end,
         json.dumps([rule] + notes + warnings) if (notes or warnings or account["type"] == "credit_card") else None)).lastrowid

    for _, t, amount in inserted:
        post_date = t.posted if t.date_from == "DTUSER" and t.posted and t.posted != t.date else None
        tid = conn.execute(
            "INSERT INTO transactions (user_id, account_id, statement_id, date, post_date, amount, description, fitid, "
            "review_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'clean')",
            (user_id, account["id"], sid, t.date, post_date, amount, t.description, t.fitid or None)).lastrowid
        inserted_ids.add(tid)
        if account["type"] == "credit_card" and amount < 0:
            out["card_payments"].append(tid)       # a card payment isn't a purchase: "Payments", as for a PDF
        else:
            out["to_categorize"].append((tid, t.description, sid))
    if skipped:
        conn.executemany(
            "INSERT INTO skipped_duplicates (user_id, account_id, statement_id, date, amount, description, reason, "
            "matched_transaction_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(user_id, account["id"], sid, d, a, desc, reason, m) for d, a, desc, reason, m in skipped])
    return {"statement_id": sid, "account_name": account["name"], "created_account": created,
            "imported": len(inserted), "duplicates": len(skipped), "left_out": len(b.left_out), "status": status,
            "period": ofx.period_label(start, end), "error": error, "details": details}


def _categorize(conn, user_id: str, saved: dict) -> None:
    """Rules first, then the AI for the rest in one batch, after the commit (SPEC.md section 9), as for a CSV."""
    if saved["card_payments"]:
        conn.executemany("UPDATE transactions SET category = 'Payments', category_source = 'card_payment' WHERE id = ?",
                         [(tid,) for tid in saved["card_payments"]])
        conn.commit()
    rows = saved["to_categorize"]
    if not rows:
        return
    plan = None
    with ai_usage.recording() as calls:
        try:
            plan = plan_categories(conn, user_id, settings.ai_url(), settings.ai_model(), [(t, d) for t, d, _ in rows])
        except Exception:      # the rows are saved already: they just stay Uncategorized
            logger.exception("Categorizing a QFX file's rows failed")
    conn.commit()
    if plan is not None:
        apply_categories(conn, plan)
        if plan.llm is not None:
            prompt, raw_response, error = plan.llm
            conn.executemany(
                "UPDATE statements SET categorization_llm_prompt = ?, categorization_llm_raw_response = ?, "
                "categorization_llm_error = ? WHERE id = ?",
                [(prompt, raw_response, error, sid) for sid in sorted({s for _, _, s in rows})])
    else:
        conn.executemany("UPDATE transactions SET category = 'Uncategorized', category_source = "
                         "'uncategorized_fallback' WHERE id = ? AND category IS NULL", [(t,) for t, _, _ in rows])
    if calls:
        first = min(s for _, _, s in rows)
        conn.execute("UPDATE statements SET ai_usage = ? WHERE id = ?", (ai_usage.to_json(calls), first))
    conn.commit()
