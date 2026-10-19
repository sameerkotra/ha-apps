"""One person's data: export it as a file, import such a file, delete it for good (PERSON_DATA_SPEC.md §2, §5;
SPEC.md section 20a). The file format, its checks and the id remapping are the shared common/person_data.py; this
module says which of Finance's tables and files are a person's, in what order they load, what a duplicate is, and
how each row is checked. The routes are routes/people_data.py.

Every per-person table has a plain `user_id` (no foreign key to known_users). Rows other people added while using
this person's data (shared access) are stored under this person's id, so they are this person's data too.

    summary(conn, uid)                         counts for the dialogs (also the delete's dry run)
    collect(conn, uid, options)                → (tables, files) for person_data.write_package
    check_package(pkg)                         the rows and files of a read file (raises Refused)
    import_package(conn, pkg, uid, mode, dir)  loads it in the caller's transaction → (counts, skipped, moves)
    delete_rows(conn, uid, forget)             the hard delete, in the caller's transaction → counts

`schema` in the file is the newest migration number (migrations/NNNN_*.sql). A file with an older schema is
brought up to date by UPGRADES (one step per migration that changed a table here); a newer one is refused.
"""
import json
import math
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field

from . import statement_due, storage, upload_page
from .amounts import to_cents
from .common import person_data
from .db import MIGRATIONS_DIR

APP = "finance"                 # the manifest's "app"
APP_LABEL = "Finance"           # the file name: finance-alice-20261018-1235.zip


def _latest_migration() -> int:
    numbers = [int(p.name.split("_", 1)[0]) for p in MIGRATIONS_DIR.glob("*.sql") if p.name[:4].isdigit()]
    return max(numbers or [0])


# The data version of an export: the newest migration. A migration that changes a table below needs an entry in
# UPGRADES (from the schema before it), so files made before it still load (test_people_data pins the number).
SCHEMA = _latest_migration()
UPGRADES: dict = {}             # {old schema: fn(tables dict) -> None}, applied in order for older files


def _to_28(tables: dict) -> None:
    """Migration 0028: statements gained `source` (every earlier statement was a PDF); transactions.fitid, the
    period columns and import_details start empty."""
    for row in tables.get("statements", []):
        row.setdefault("source", "pdf")


UPGRADES[27] = _to_28


def _to_29(tables: dict) -> None:
    """Migration 0029: accounts gained the expected statement (Off: NULL) and their own Remind me (on); the new
    reminder_prefs and statement_dismissals tables start empty."""
    for row in tables.get("accounts", []):
        row.setdefault("reminders_on", 1)


UPGRADES[28] = _to_29


def _to_30(tables: dict) -> None:
    """Migration 0030: accounts gained what the Upload page remembers (csv_layout, skip_confirmation); an older
    file has neither, which is "nothing saved" (NULL) — nothing to fill in."""


UPGRADES[29] = _to_30


class Refused(ValueError):
    """A file (or a row in it) that can't be imported; the message is for the admin."""


@dataclass
class Table:
    name: str
    label: str
    refs: dict = field(default_factory=dict)      # column → the table its id points at (remapped on import)
    parent: tuple = ()                            # (column, table) when the table has no user_id of its own
    text: tuple = ()                              # columns left out without "Include the text read from statements"
    pdf: bool = False                             # rows carry pdf_path / pdf_deleted_at
    dedupe_in_file: bool = False                  # a duplicate also counts against rows loaded earlier from the file


# In the order they load (a table after the ones it points at). toll_trips is a cache: rebuilt after an import.
TABLES = [
    Table("accounts", "accounts"),
    Table("csv_imports", "CSV imports", text=("categorization_llm_prompt", "categorization_llm_raw_response")),
    Table("statements", "bank statements", refs={"account_id": "accounts"}, pdf=True,
          text=("llm_raw_response", "categorization_llm_prompt", "categorization_llm_raw_response",
                "deterministic_amounts", "import_details")),
    Table("transactions", "transactions", refs={"account_id": "accounts", "statement_id": "statements",
                                                "csv_import_id": "csv_imports", "transfer_pair_id": "transactions"}),
    Table("skipped_duplicates", "skipped possible duplicates",
          refs={"account_id": "accounts", "statement_id": "statements", "csv_import_id": "csv_imports",
                "matched_transaction_id": "transactions", "inserted_transaction_id": "transactions"}),
    Table("category_rules", "category rules", dedupe_in_file=True),
    Table("custom_categories", "custom categories", dedupe_in_file=True),
    Table("recurring_overrides", "recurring choices", dedupe_in_file=True),
    Table("utility_bills", "utility bills", pdf=True, text=("llm_raw_response", "deterministic_value", "llm_value")),
    Table("electric_meter_details", "electric meter readings", parent=("utility_bill_id", "utility_bills")),
    Table("toll_cars", "toll cars", dedupe_in_file=True),
    Table("toll_devices", "toll tags and plates", refs={"car_id": "toll_cars"}, dedupe_in_file=True),
    Table("toll_statements", "toll statements", pdf=True,
          text=("llm_raw_response", "deterministic_value", "llm_value", "unparsed_json")),
    Table("toll_transactions", "toll passes", refs={"statement_id": "toll_statements", "device_ref": "toll_devices"},
          text=("raw_line",)),
    Table("toll_pattern_groups", "toll tag groups", dedupe_in_file=True),
    Table("toll_patterns", "toll tags", refs={"group_id": "toll_pattern_groups"}, dedupe_in_file=True),
    Table("toll_trip_overrides", "toll trip choices", refs={"pattern_id": "toll_patterns"}, dedupe_in_file=True),
    # Expected statements (SPEC.md section 27): the person's own My reminders and the cycles they dismissed. The
    # send log (statement_reminders) isn't theirs to carry: it stays out, so does user_notify (admin setup).
    Table("reminder_prefs", "reminder settings", dedupe_in_file=True),
    Table("statement_dismissals", "dismissed overdue statements", parent=("account_id", "accounts")),
]
BY_NAME = {t.name: t for t in TABLES}
NEVER = {"id", "user_id", "trip_id"}     # trip_id points into the toll_trips cache, rebuilt after an import

# Export options (the dialog's ticks): key, label, default, help.
OPTIONS = [
    ("pdfs", "Include statement PDFs", True, "The PDFs still kept (a PDF is deleted once its statement is confirmed)."),
    ("deleted", "Include Recently deleted items", False, "Items deleted in the last 30 days, which can still be restored."),
    ("text", "Include the text read from statements", True,
     "What the AI and the app read from each PDF, needed to re-check a statement later."),
]


def options_from(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {k: bool(raw[k]) if k in raw else default for k, _, default, _ in OPTIONS}


# ------------------------------------------------------------------ rules for a row (the app's own limits)

MAX_TEXT = 1_000_000          # any other text column: a sanity bound (the query tab's own value limit)
LIMITS = {                    # the same bounds the forms and the API use
    ("accounts", "name"): 100, ("accounts", "bank_name"): 100, ("accounts", "website_url"): 500,
    ("accounts", "csv_layout"): 2000,
    ("transactions", "description"): 500, ("transactions", "category"): 60, ("transactions", "note"): 1000,
    ("skipped_duplicates", "description"): 500, ("skipped_duplicates", "category"): 60,
    ("category_rules", "pattern"): 200, ("category_rules", "category"): 60, ("custom_categories", "name"): 60,
    ("recurring_overrides", "merchant_pattern"): 500, ("toll_cars", "name"): 60, ("toll_pattern_groups", "name"): 60,
    ("toll_patterns", "signature"): 2000, ("toll_patterns", "name"): 200,
}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
DATES = {("transactions", "date"): _DATE, ("skipped_duplicates", "date"): _DATE, ("toll_transactions", "date"): _DATE,
         ("toll_transactions", "occurred_at"): _DATETIME}


def columns(conn, table: str) -> dict:
    """{column: (declared type, not null, has a default)} from the database itself, so a new column is exported
    and imported without changing this file."""
    return {r["name"]: ((r["type"] or "").upper(), bool(r["notnull"]), r["dflt_value"] is not None)
            for r in conn.execute(f"PRAGMA table_info({table})")}


def _check_value(table: str, col: str, kind: str, value, where: str):
    if value is None:
        return None
    if "INT" in kind:
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2 ** 62:
            raise Refused(f"{where}: {col} must be a whole number.")
    elif "REAL" in kind:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise Refused(f"{where}: {col} must be a number.")
        value = float(value)
    else:
        if not isinstance(value, str):
            raise Refused(f"{where}: {col} must be text.")
        limit = LIMITS.get((table, col), MAX_TEXT)
        if len(value) > limit:
            raise Refused(f"{where}: {col} is too long (max {limit} characters).")
        pattern = DATES.get((table, col))
        if pattern and not pattern.fullmatch(value):
            raise Refused(f"{where}: {col} isn't a date the app stores ({value[:30]}).")
    return value


def clean_row(table: str, row: dict, cols: dict, where: str) -> dict:
    """The row with only this table's known columns (unknown ones dropped), each value checked like the app's own
    forms check it. Ids are left for the caller to remap."""
    t = BY_NAME[table]
    remapped = set(t.refs) | ({t.parent[0]} if t.parent else set())     # filled in by the import
    out = {}
    for col, (kind, notnull, has_default) in cols.items():
        if col in NEVER:
            continue
        value = _check_value(table, col, kind, row.get(col), where)
        if value is None and notnull and col not in remapped:
            if has_default:
                continue                      # the database's own default
            raise Refused(f"{where}: {col} is missing.")
        if col in row:
            out[col] = value
    if table == "accounts":
        from .routes.accounts import _sanitize_last4, _sanitize_url, ACCOUNT_TYPES
        if not (out.get("name") or "").strip():
            raise Refused(f"{where}: the account has no name.")
        if out.get("type") not in ACCOUNT_TYPES:
            raise Refused(f"{where}: the account type isn't one the app knows.")
        last4 = out.get("account_number_last4")
        if last4 is not None and _sanitize_last4(last4) != last4:
            raise Refused(f"{where}: account_number_last4 must be the last 4 digits only.")
        url = out.get("website_url")
        if url is not None and _sanitize_url(url) != url:
            raise Refused(f"{where}: the account's web address isn't one the app accepts.")
        if out.get("expected_from") is not None and statement_due.parse_month(out["expected_from"]) is None:
            raise Refused(f"{where}: expected_from must be a month (YYYY-MM).")
        if out.get("csv_layout") is not None:
            layout = upload_page.clean_layout(out["csv_layout"])
            if layout is None:
                raise Refused(f"{where}: csv_layout isn't a CSV layout the app keeps.")
            out["csv_layout"] = upload_page.layout_json(layout)
        if out.get("skip_confirmation") not in (None, 0, 1):
            raise Refused(f"{where}: skip_confirmation must be 0 or 1.")
    if table == "reminder_prefs":
        try:
            offsets = json.loads(out.get("offsets") or "[]")
        except ValueError:
            offsets = None
        mode = out.get("mode") or "off"
        if (not isinstance(offsets, list) or len(offsets) > statement_due.MAX_OFFSETS
                or any(o not in dict(statement_due.OFFSET_CHOICES) for o in offsets) or (mode == "remind" and not offsets)):
            raise Refused(f"{where}: the reminder days aren't ones the app offers.")
        if out.get("at_time") is not None and statement_due.parse_prefs_form(
                "remind", ["0"], "0", out["at_time"])[1] is not None:
            raise Refused(f"{where}: at_time must be HH:MM.")
    if table == "statement_dismissals" and not _DATE.fullmatch(str(out.get("cycle_date") or "")):
        raise Refused(f"{where}: cycle_date isn't a date the app stores.")
    if table == "transactions" and out.get("amount") is not None:
        out["amount"] = round(out["amount"], 2)          # amounts are stored in whole cents (migration 0027)
        if out["amount"] == 0:
            raise Refused(f"{where}: a transaction of 0.00 isn't accepted.")
    if table in ("category_rules", "custom_categories", "toll_cars", "toll_pattern_groups"):
        key = "pattern" if table == "category_rules" else "name"
        if not (out.get(key) or "").strip():
            raise Refused(f"{where}: {key} is blank.")
    return out


# ------------------------------------------------------------------ what they have

def _rows(conn, table: Table, uid: str) -> list:
    if table.parent:
        col, parent = table.parent
        return [dict(r) for r in conn.execute(
            f"SELECT c.* FROM {table.name} c JOIN {parent} p ON p.id = c.{col} WHERE p.user_id = ? ORDER BY c.id", (uid,))]
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table.name} WHERE user_id = ? ORDER BY id", (uid,))]


def _pdf_files(conn, uid: str) -> dict:
    """{stored path: size} of every PDF their rows still point at (a dual-fuel bill's PDF once)."""
    out = {}
    for t in ("statements", "utility_bills", "toll_statements"):
        for (path,) in conn.execute(f"SELECT pdf_path FROM {t} WHERE user_id = ? AND pdf_path IS NOT NULL", (uid,)):
            if path not in out and storage.stored_path(path):
                try:
                    out[path] = os.path.getsize(path)
                except OSError:
                    pass
    return out


def summary(conn, uid: str, name: str) -> dict:
    """GET …/data: what they have (every row, Recently deleted included: a delete takes it all)."""
    def n(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    def gone(table):
        return n(f"SELECT COUNT(*) FROM {table} WHERE user_id = ? AND deleted_at IS NOT NULL", uid)

    def item(key, label, count, detail=""):
        out = {"key": key, "label": label, "count": int(count or 0)}
        if detail:
            out["detail"] = detail
        return out

    def deleted_detail(table):
        d = gone(table)
        return f"{d} in Recently deleted" if d else ""

    pdfs = _pdf_files(conn, uid)
    passes = n("SELECT COUNT(*) FROM toll_transactions WHERE user_id = ?", uid)
    rules = sum(n(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", uid)
                for t in ("category_rules", "custom_categories", "recurring_overrides"))
    toll_setup = sum(n(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", uid)
                     for t in ("toll_cars", "toll_devices", "toll_patterns", "toll_pattern_groups", "toll_trip_overrides"))
    sees = n("SELECT COUNT(*) FROM user_access WHERE viewer_id = ?", uid)
    seen_by = n("SELECT COUNT(*) FROM user_access WHERE owner_id = ?", uid)
    counts = [
        item("accounts", "accounts", n("SELECT COUNT(*) FROM accounts WHERE user_id = ?", uid), deleted_detail("accounts")),
        item("statements", "bank statements", n("SELECT COUNT(*) FROM statements WHERE user_id = ?", uid),
             deleted_detail("statements")),
        item("pdfs", "PDFs still kept", len(pdfs), person_data.bytes_text(sum(pdfs.values())) if pdfs else ""),
        item("transactions", "transactions", n("SELECT COUNT(*) FROM transactions WHERE user_id = ?", uid),
             deleted_detail("transactions")),
        item("csv_imports", "CSV imports", n("SELECT COUNT(*) FROM csv_imports WHERE user_id = ?", uid)),
        item("utility_bills", "utility bills", n("SELECT COUNT(*) FROM utility_bills WHERE user_id = ?", uid),
             deleted_detail("utility_bills")),
        item("toll_statements", "toll statements", n("SELECT COUNT(*) FROM toll_statements WHERE user_id = ?", uid),
             f"{passes} passes" if passes else ""),
        item("rules", "category rules, categories and recurring choices", rules),
        item("reminders", "reminder settings and dismissed overdue statements",
             n("SELECT COUNT(*) FROM reminder_prefs WHERE user_id = ?", uid)
             + n("SELECT COUNT(*) FROM statement_dismissals d JOIN accounts a ON a.id = d.account_id WHERE a.user_id = ?",
                 uid)),
        item("toll_setup", "toll cars, tags and groups", toll_setup),
        item("access", "shared-access grants", sees + seen_by,
             f"uses {sees} other people's data · {seen_by} use theirs" if sees + seen_by else ""),
    ]
    return {
        "person": {"id": uid, "name": name},
        "counts": counts,
        "exportOptions": [{"key": k, "label": label, "default": d, "help": h} for k, label, d, h in OPTIONS],
        "exportNote": "The file holds their bank statements and transactions and isn't encrypted — keep it somewhere "
                      "safe. Shared access, saved reports and App settings aren't in it.",
        "keeps": ["Saved reports and App settings (they belong to the whole household)",
                  "The AI usage total",
                  "Who changed an App setting or gave shared access (admin history)"],
        "cannotReach": ["whole-app backups (Admin → Storage → Download database) and Home Assistant backups made "
                        "before", "the copy kept before a database upgrade (finance.db.pre-NNNN)"],
        "deleteChoices": [],
        "forgetNote": "They leave the Users list and the user switch. If they still have a Home Assistant login they "
                      "come back as a new, empty person the next time they open the app — remove them in Home "
                      "Assistant first if they shouldn't.",
        "deleteNote": "This can't be undone, unlike Wipe all data, which keeps everything in Recently deleted for "
                      "30 days. Shared access to and from them is removed too.",
    }


# ------------------------------------------------------------------ export

def _arcname(path: str, uid: str) -> str:
    """The PDF's place under files/: its path inside their folder (utility-bills/…, toll-statements/…)."""
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(storage.user_dir(uid)))
    if rel.startswith("..") or os.path.isabs(rel):
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


def collect(conn, uid: str, options: dict, when: str = "") -> tuple[dict, list]:
    """(tables, files) for write_package. Without "deleted", soft-deleted rows are left out, and so is anything
    that hangs on a row left out (a statement of a deleted account); a reference that may be empty is emptied."""
    tables, files, kept = {}, {}, {}
    for t in TABLES:
        rows = _rows(conn, t, uid)
        cols = columns(conn, t.name)
        not_null = {c for c, (_, nn, _) in cols.items() if nn}
        out = []
        for r in rows:
            if not options["deleted"] and r.get("deleted_at") is not None:
                continue
            if t.parent and r[t.parent[0]] not in kept.get(t.parent[1], set()):
                continue
            skip = False
            for col, target in t.refs.items():
                if target == t.name or r.get(col) is None:
                    continue
                if r[col] not in kept.get(target, set()):
                    if col in not_null:
                        skip = True
                        break
                    r[col] = None
            if skip:
                continue
            if t.pdf and r.get("pdf_path"):
                if options["pdfs"] and storage.stored_path(r["pdf_path"]):
                    arc = _arcname(r["pdf_path"], uid)
                    files.setdefault(arc, r["pdf_path"])
                    r["pdf_path"] = "files/" + arc
                else:
                    r["pdf_path"] = None          # not in the file: it reads as an already deleted PDF
                    r["pdf_deleted_at"] = r.get("pdf_deleted_at") or when or None
            if not options["text"]:
                for col in t.text:
                    r.pop(col, None)
            for col in NEVER - {"id"}:
                r.pop(col, None)
            out.append(r)
        kept[t.name] = {r["id"] for r in out}
        if "transfer_pair_id" in t.refs:
            for r in out:
                if r.get("transfer_pair_id") not in kept[t.name]:
                    r["transfer_pair_id"] = None
        tables[t.name] = out
    return tables, sorted(files.items())


def counts_text(counts: dict) -> str:
    """{"transactions": 812, …} → "812 transactions, 3 accounts" (non-zero ones, in table order)."""
    parts = [f"{counts[t.name]} {t.label}" for t in TABLES if counts.get(t.name)]
    if counts.get("files"):
        parts.append(f"{counts['files']} PDFs")
    return ", ".join(parts) or "nothing"


# ------------------------------------------------------------------ import

def _is_pdf_member(pkg, name: str) -> None:
    info = pkg._zip.getinfo(name)
    if info.file_size > storage.MAX_PDF_BYTES:
        raise Refused(f"{name} is larger than {storage.MAX_PDF_BYTES // (1024 * 1024)} MB.")
    with pkg.open(name) as f:
        head = f.read(1024)
    if b"%PDF-" not in head:
        raise Refused(f"{name} isn't a PDF.")


def upgrade(pkg) -> None:
    """Brings an older file's rows up to this schema, one step per migration (UPGRADES)."""
    for version in sorted(v for v in UPGRADES if v >= pkg.schema):
        UPGRADES[version](pkg.tables)


def check_package(conn, pkg) -> dict:
    """Every row checked like the app checks its own input, every PDF sniffed (as an upload is); returns the
    preview's counts and warnings. Raises Refused naming the table and row."""
    upgrade(pkg)
    counts, processing = {}, 0
    for t in TABLES:
        cols = columns(conn, t.name)
        rows = pkg.tables.get(t.name, [])
        for i, row in enumerate(rows, 1):
            clean_row(t.name, row, cols, f"{t.label}, row {i}")
            if row.get("status") == "processing":
                processing += 1
            if t.pdf and row.get("pdf_path") and row["pdf_path"] not in pkg.file_names:
                raise Refused(f"{t.label}, row {i}: its PDF isn't in the file.")
        counts[t.name] = len(rows)
    for name in pkg.file_names:
        _is_pdf_member(pkg, name)
    warnings = []
    opts = pkg.manifest.get("options") or {}
    if pkg.schema < SCHEMA:
        warnings.append("Made by an older version of the app: it's brought up to date as it's loaded.")
    if opts.get("pdfs") is False:
        warnings.append("Statement PDFs were left out of this file.")
    if opts.get("text") is False:
        warnings.append("The text read from statements was left out: those statements can't be re-checked.")
    if opts.get("deleted"):
        warnings.append("It includes Recently deleted items: they come back there, and are erased 30 days after "
                        "they were first deleted.")
    if processing:
        warnings.append(f"{processing} uploads were still being read when it was exported: they come in as failed.")
    return {"counts": counts, "files": len(pkg.file_names), "warnings": warnings}


SOFT_DELETED = {"accounts", "csv_imports", "statements", "transactions", "utility_bills", "toll_statements",
                "toll_transactions"}


def _existing_keys(conn, table: str, uid: str) -> tuple[dict, dict]:
    """({key: id} of their live rows, {key: id} of all their rows) for the duplicate check. A live row from the file
    is a duplicate of a live row only (a deleted one never blocks, as with an upload); a Recently deleted row from
    the file is a duplicate of any row, so importing the same file twice doesn't bring it twice."""
    q = {
        "accounts": "SELECT id, lower(trim(name)), COALESCE(account_number_last4, '') FROM accounts WHERE user_id = ?",
        "csv_imports": "SELECT id, original_filename, imported_at, row_count FROM csv_imports WHERE user_id = ?",
        "statements": "SELECT id, file_hash FROM statements WHERE user_id = ? AND file_hash IS NOT NULL",
        "transactions": "SELECT id, account_id, date, amount, description FROM transactions WHERE user_id = ?",
        "category_rules": "SELECT id, pattern, match_type FROM category_rules WHERE user_id = ?",
        "custom_categories": "SELECT id, lower(name) FROM custom_categories WHERE user_id = ?",
        "recurring_overrides": "SELECT id, merchant_pattern FROM recurring_overrides WHERE user_id = ?",
        "utility_bills": "SELECT id, file_hash, utility_type FROM utility_bills WHERE user_id = ? AND file_hash IS NOT NULL",
        "toll_cars": "SELECT id, lower(name) FROM toll_cars WHERE user_id = ?",
        "toll_devices": "SELECT id, device_id, CASE WHEN device_id = '' THEN plate ELSE '' END, "
                        "CASE WHEN device_id = '' THEN plate_state ELSE '' END FROM toll_devices WHERE user_id = ?",
        "toll_statements": "SELECT id, file_hash FROM toll_statements WHERE user_id = ? AND file_hash IS NOT NULL",
        "toll_transactions": "SELECT id, dedupe_key FROM toll_transactions WHERE user_id = ?",
        "toll_pattern_groups": "SELECT id, lower(name) FROM toll_pattern_groups WHERE user_id = ?",
        "toll_patterns": "SELECT id, signature, window_start_min, window_end_min FROM toll_patterns WHERE user_id = ?",
        "toll_trip_overrides": "SELECT id, trip_key FROM toll_trip_overrides WHERE user_id = ?",
        "reminder_prefs": "SELECT id, 1 FROM reminder_prefs WHERE user_id = ?",
    }.get(table)
    live, every = {}, {}
    if not q:
        return live, every
    soft = table in SOFT_DELETED
    if soft:
        q = q.replace("SELECT id, ", "SELECT id, deleted_at IS NOT NULL, ", 1)
    for r in conn.execute(q, (uid,)):
        values = tuple(r)
        deleted, key = (values[1], values[2:]) if soft else (False, values[1:])
        if table == "transactions":
            key = (key[0], key[1], to_cents(key[2]), key[3])
        every.setdefault(key, r[0])
        if not deleted:
            live.setdefault(key, r[0])
    return live, every


def _key(table: str, r: dict):
    """The duplicate key of a row from the file (its references already remapped), or None (never a duplicate)."""
    if table == "accounts":
        return ((r.get("name") or "").strip().lower(), r.get("account_number_last4") or "")
    if table == "csv_imports":
        return (r.get("original_filename"), r.get("imported_at"), r.get("row_count"))
    if table in ("statements", "toll_statements"):
        return (r["file_hash"],) if r.get("file_hash") else None
    if table == "utility_bills":
        return (r["file_hash"], r.get("utility_type")) if r.get("file_hash") else None
    if table == "transactions":
        return (r.get("account_id"), r.get("date"), to_cents(r.get("amount") or 0), r.get("description"))
    if table == "category_rules":
        return (r.get("pattern"), r.get("match_type"))
    if table in ("custom_categories", "toll_cars", "toll_pattern_groups"):
        return ((r.get("name") or "").lower(),)
    if table == "recurring_overrides":
        return (r.get("merchant_pattern"),)
    if table == "toll_devices":
        d = r.get("device_id") or ""
        return (d, "" if d else r.get("plate") or "", "" if d else r.get("plate_state") or "")
    if table == "toll_transactions":
        return (r.get("dedupe_key"),)
    if table == "toll_patterns":
        return (r.get("signature"), r.get("window_start_min"), r.get("window_end_min"))
    if table == "toll_trip_overrides":
        return (r.get("trip_key"),)
    if table == "reminder_prefs":
        return (1,)                         # one per person: theirs is kept when they already have one
    return None


def _new_pdf_path(arc: str, uid: str) -> str:
    """Where an imported PDF goes in the new owner's folder: the same sub-folder, a new random name."""
    sub = os.path.dirname(arc[len("files/"):])
    sub = sub if sub in ("utility-bills", "toll-statements") else ""
    folder = storage.user_dir(uid, sub) if sub else storage.user_dir(uid)
    return os.path.join(folder, f"imported-{os.urandom(8).hex()}.pdf")


def import_package(conn, pkg, uid: str, staging: str) -> tuple[dict, int, list]:
    """Loads the checked file into `uid`'s data on `conn`, in the caller's transaction (nothing is committed here).
    Every row gets a new id; references are remapped (IdMap); a row the person already has is skipped and its id
    mapped to theirs, so what hangs on it still finds it. PDFs are copied into `staging` (a folder on the same disk)
    and returned as (staged, final) pairs for the caller to move in after the commit.
    Returns ({table: rows added}, rows skipped, moves)."""
    ids = person_data.IdMap()
    dup = {t.name: set() for t in TABLES}          # old ids that were duplicates
    added, skipped, moves, staged = {}, 0, [], {}
    pairs = []                                    # (new id, old partner id): transfer pairs, set once all are in
    for t in TABLES:
        cols = columns(conn, t.name)
        live, every = _existing_keys(conn, t.name, uid)
        added[t.name] = 0
        for i, raw in enumerate(pkg.tables.get(t.name, []), 1):
            where = f"{t.label}, row {i}"
            row = clean_row(t.name, raw, cols, where)
            old_id = raw.get("id")
            # A row that hangs on a skipped duplicate (a meter reading of a bill already there, a skipped line of
            # a statement already there) is that row's, already there too.
            if t.parent:
                pcol, ptable = t.parent
                if raw.get(pcol) in dup[ptable] or not ids.has(ptable, raw.get(pcol)):
                    skipped += 1
                    continue
                row[pcol] = ids.get(ptable, raw.get(pcol))
            if t.name == "skipped_duplicates" and (raw.get("statement_id") in dup["statements"]
                                                   or raw.get("csv_import_id") in dup["csv_imports"]):
                skipped += 1
                continue
            missing = False
            for col, target in t.refs.items():
                if target == t.name:
                    row.pop(col, None)
                    continue
                if raw.get(col) is None:
                    continue
                new = ids.get(target, raw[col])
                if new is None and cols[col][1]:     # a required reference to nothing: the file is inconsistent
                    missing = True
                    break
                row[col] = new
            if missing:
                raise Refused(f"{where}: it points at a row that isn't in the file.")
            key = _key(t.name, row)
            existing = every if row.get("deleted_at") is not None else live
            if key is not None and key in existing:
                ids.put(t.name, old_id, existing[key])
                dup[t.name].add(old_id)
                skipped += 1
                continue
            if row.get("status") == "processing":
                row["status"] = "error"
                row["error_message"] = "It was still being read when it was exported. Restart it."
                row["current_step"] = None
            if t.pdf and row.get("pdf_path"):
                arc = row["pdf_path"]
                if arc not in staged:
                    tmp = os.path.join(staging, f"{len(staged)}.pdf")
                    pkg.extract(arc, tmp)
                    final = _new_pdf_path(arc, uid)
                    staged[arc] = final
                    moves.append((tmp, final))
                row["pdf_path"] = staged[arc]
            if not t.parent:
                row["user_id"] = uid
            names = list(row)
            try:
                cur = conn.execute(f"INSERT INTO {t.name} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                                   [row[c] for c in names])
            except sqlite3.IntegrityError as e:
                raise Refused(f"{where}: the app doesn't accept it ({e}).") from None
            ids.put(t.name, old_id, cur.lastrowid)
            if t.dedupe_in_file and key is not None:
                live[key] = every[key] = cur.lastrowid
            if t.name == "transactions" and raw.get("transfer_pair_id") is not None:
                pairs.append((cur.lastrowid, raw["transfer_pair_id"]))
            added[t.name] += 1
    for new_id, old_partner in pairs:
        partner = ids.get("transactions", old_partner)
        if partner is not None and partner != new_id:
            conn.execute("UPDATE transactions SET transfer_pair_id = ? WHERE id = ? AND user_id = ?",
                         (partner, new_id, uid))
    added["files"] = len(moves)
    return added, skipped, moves


# ------------------------------------------------------------------ delete

def delete_rows(conn, uid: str, forget: bool) -> dict:
    """The hard delete, in the caller's transaction: every table above, toll_trips and both sides of shared access
    (and with `forget` their known_users row), in an order the foreign keys allow — toll passes before their
    devices (that key has no ON DELETE). Returns {table: rows deleted}. The caller removes their folder of files
    after the commit. app_settings.updated_by and user_access.granted_by naming them stay (admin history)."""
    def gone(sql, *args):
        return conn.execute(sql, args).rowcount

    counts = {}
    counts["statement_dismissals"] = gone(
        "DELETE FROM statement_dismissals WHERE account_id IN (SELECT id FROM accounts WHERE user_id = ?)", uid)
    counts["statement_reminders"] = gone(
        "DELETE FROM statement_reminders WHERE user_id = ? OR account_id IN (SELECT id FROM accounts WHERE user_id = ?)",
        uid, uid)
    counts["reminder_prefs"] = gone("DELETE FROM reminder_prefs WHERE user_id = ?", uid)
    counts["user_notify"] = gone("DELETE FROM user_notify WHERE user_id = ?", uid)
    counts["electric_meter_details"] = gone(
        "DELETE FROM electric_meter_details WHERE utility_bill_id IN (SELECT id FROM utility_bills WHERE user_id = ?)", uid)
    for table in ("skipped_duplicates", "transactions", "statements", "csv_imports", "accounts", "utility_bills",
                  "toll_trip_overrides", "toll_trips", "toll_transactions", "toll_statements", "toll_devices",
                  "toll_cars", "toll_patterns", "toll_pattern_groups", "category_rules", "custom_categories",
                  "recurring_overrides"):
        counts[table] = gone(f"DELETE FROM {table} WHERE user_id = ?", uid)
    counts["user_access"] = gone("DELETE FROM user_access WHERE viewer_id = ? OR owner_id = ?", uid, uid)
    if forget:
        counts["known_users"] = gone("DELETE FROM known_users WHERE id = ?", uid)
    return counts


def remove_folder(uid: str) -> None:
    """Their folder of files (statement PDFs, utility-bills/, toll-statements/, csv-imports/), after the commit."""
    folder = storage.user_dir(uid)
    if storage.is_stored(os.path.join(folder, "x")) and os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
