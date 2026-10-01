"""Read-only SQL for the Query tab and Reports, scoped to one user (SPEC.md section 19).

Every run gets a private in-memory database holding a copy of only that user's
live rows from the registered tables (the real database is attached read-only
just long enough to copy, then detached). The user's SQL runs there under an
authorizer that allows only reads and ordinary functions, so it can't reach the
real file or anyone else's data whatever it says. A table with no REGISTRY
entry is never copied.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from . import db

TIME_LIMIT_SECONDS = 10
MAX_SQL_BYTES = 20_000
MAX_ROWS = 100_000
MAX_VALUE_BYTES = 10_000_000          # SQLITE_LIMIT_LENGTH on query connections (Python 3.11+)
MAX_RESULT_BYTES = 64 * 1024 * 1024   # a single result, checked while fetching
CACHE_TTL_SECONDS = 600
CACHE_MAX_BYTES = 64 * 1024 * 1024
HIDDEN_COLUMNS = {"user_id", "deleted_at"}


@dataclass(frozen=True)
class TableSpec:
    description: str
    scope: str        # WHERE clause over alias x; {u} is the quoted user id


_OWN = "x.user_id = {u}"
_LIVE = _OWN + " AND x.deleted_at IS NULL"
_LIVE_ACCOUNT = " AND EXISTS (SELECT 1 FROM main.accounts a WHERE a.id = x.account_id AND a.deleted_at IS NULL)"

REGISTRY: dict[str, TableSpec] = {
    "accounts": TableSpec("Bank and credit-card accounts: name, type, bank, last 4, archived, preferred upload method, website_url.", _LIVE),
    "transactions": TableSpec(
        "Every bank/card transaction. amount is signed: negative is money out, positive is money in. "
        "transfer_pair_id is set for a transfer between your own accounts; is_excluded = 1 is left out of dashboards.",
        _LIVE + _LIVE_ACCOUNT
        + " AND (x.statement_id IS NULL OR EXISTS (SELECT 1 FROM main.statements s WHERE s.id = x.statement_id AND s.deleted_at IS NULL))"
        + " AND (x.csv_import_id IS NULL OR EXISTS (SELECT 1 FROM main.csv_imports c WHERE c.id = x.csv_import_id AND c.deleted_at IS NULL))"),
    "statements": TableSpec("Uploaded PDF statements: account, statement_period (month), status, previous and new balance, when confirmed.",
                            _LIVE + _LIVE_ACCOUNT),
    "csv_imports": TableSpec("Each CSV import: file, rows imported, duplicates, errors, rows skipped by status. "
                             "Transactions link to it by csv_import_id.", _LIVE),
    "category_rules": TableSpec("Your categorization rules: pattern, match type, category.", _OWN),
    "custom_categories": TableSpec("Categories you added.", _OWN),
    "recurring_overrides": TableSpec("Merchants you marked as recurring or not recurring.", _OWN),
    "utility_bills": TableSpec("Xcel Energy and Aurora Water bills: type, provider, billing period, due date, cost, usage_amount, usage_unit.", _LIVE),
    "electric_meter_details": TableSpec(
        "Per electric bill: grid import, solar export and net kWh (on/off peak), rates and charges. Links by utility_bill_id.",
        "EXISTS (SELECT 1 FROM main.utility_bills b WHERE b.id = x.utility_bill_id AND b.user_id = {u} AND b.deleted_at IS NULL)"),
    "toll_statements": TableSpec("Uploaded E-470 statements: period, totals, status.", _LIVE),
    "toll_transactions": TableSpec(
        "Each toll pass: date, occurred_at (local time), agency, road, plaza, lane, direction, amount (positive), device_ref, trip_id.",
        _LIVE + " AND EXISTS (SELECT 1 FROM main.toll_statements s WHERE s.id = x.statement_id AND s.deleted_at IS NULL)"),
    "toll_trips": TableSpec("Passes grouped into trips: vehicle, started_at, ended_at, pass_count, amount, tag (pattern_id).", _OWN),
    "toll_cars": TableSpec("Your cars.", _OWN),
    "toll_devices": TableSpec("Transponders and plates, and the car each belongs to (car_id).", _OWN),
    "toll_patterns": TableSpec("Trip tags.", _OWN),
    "toll_pattern_groups": TableSpec("Groups of trip tags.", _OWN),
    "toll_trip_overrides": TableSpec("Trips you tagged by hand.", _OWN),
}

# Deliberately never queryable. Every table in the database must be in REGISTRY or here (tested).
HIDDEN_TABLES = {"known_users", "app_settings", "ai_usage_log", "schema_version", "sqlite_sequence", "saved_reports", "user_access",
                 "skipped_duplicates"}

# Convenience views, built on the scoped views above.
EXTRA_VIEWS: dict[str, tuple[str, str]] = {
    "transactions_with_account": (
        "Live transactions, with account_name, account_bank and account_last4. The easiest place to start.",
        "SELECT t.*, a.name AS account_name, a.bank_name AS account_bank, a.account_number_last4 AS account_last4 "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id"),
}

_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class QueryError(Exception):
    """A query the app refused or SQLite rejected; the message is safe to show the admin."""


class QueryCancelled(QueryError):
    pass


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def visible_names() -> list[str]:
    return list(EXTRA_VIEWS) + list(REGISTRY)


_SCHEMA_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema"}


def _open(user_id: str, *, guard: bool = True) -> sqlite3.Connection:
    """The private copy for `user_id`. guard=False only for the app's own SQL (table_overview)."""
    conn = sqlite3.connect(":memory:", timeout=10.0, check_same_thread=False, uri=True, isolation_level=None)
    conn.execute("ATTACH DATABASE ? AS src", (f"file:{db.DB_PATH}?mode=ro",))
    conn.execute("PRAGMA src.busy_timeout = 5000")
    present = {r[0] for r in conn.execute("SELECT name FROM src.sqlite_master WHERE type = 'table'")}
    u = _quote(user_id)
    conn.execute("BEGIN")                      # one snapshot of the source for every table
    for name, spec in REGISTRY.items():
        if name not in present:
            continue
        cols = [(r[1], r[2] or "") for r in conn.execute(f'PRAGMA src.table_info("{name}")') if r[1] not in HIDDEN_COLUMNS]
        conn.execute(f'CREATE TABLE main."{name}" (' + ", ".join(f'"{c}" {t}' for c, t in cols) + ")")
        col_sql = ", ".join(f'x."{c}"' for c, _t in cols)
        scope = spec.scope.format(u=u).replace("main.", "src.")
        conn.execute(f'INSERT INTO main."{name}" SELECT {col_sql} FROM src."{name}" AS x WHERE {scope}')
    conn.execute("COMMIT")
    conn.execute("DETACH DATABASE src")
    for name, (_desc, sql) in EXTRA_VIEWS.items():
        conn.execute(f'CREATE VIEW main."{name}" AS {sql}')
    conn.execute("PRAGMA query_only = ON")
    if hasattr(conn, "setlimit"):
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)

    def authorizer(action, arg1, arg2, dbname, inner):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_DENY if (arg1 or "").lower() in _SCHEMA_TABLES else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _BLOCKED_FUNCTIONS else sqlite3.SQLITE_OK
        if action == getattr(sqlite3, "SQLITE_RECURSIVE", 33):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY          # ATTACH, PRAGMA, writes, schema changes, transactions

    if guard:
        conn.set_authorizer(authorizer)
    return conn


_COMMENTS = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.S)


def strip_comments(sql: str) -> str:
    """SQL with comments removed, keeping string literals intact."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`[":
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if j + 1 < n and sql[j + 1] == close and close != "]":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check_sql(sql: str) -> str:
    """The SQL to run, or QueryError with the reason it was refused."""
    if len(sql.encode("utf-8")) > MAX_SQL_BYTES:
        raise QueryError(f"The SQL is longer than {MAX_SQL_BYTES // 1000} KB.")
    body = strip_comments(sql).strip().rstrip(";").strip()
    if not body:
        raise QueryError("Write a SELECT query first.")
    if not re.match(r"(?is)^(select|with)\b", body):
        raise QueryError("Only SELECT queries are allowed.")
    if ";" in strip_literals(body):
        raise QueryError("Run one statement at a time.")
    return sql


def strip_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", sql)


_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def parameters(sql: str) -> list[str]:
    """Named parameters (:name) used in the SQL, in first-use order, outside strings and comments."""
    seen: list[str] = []
    for name in _PARAM.findall(strip_literals(strip_comments(sql))):
        if name not in seen:
            seen.append(name)
    return seen


def _friendly(err: sqlite3.Error) -> str:
    msg = str(err)
    m = re.search(r"access to (\w+)\.\w+ is prohibited", msg)
    if m:
        return f"{m.group(1)} can't be queried here."
    if msg == "not authorized":
        return "Not allowed here: only SELECT queries can run (no PRAGMA, ATTACH or changes)."
    if "readonly" in msg or "cannot modify" in msg:
        return "Only SELECT queries are allowed."
    return msg


def _cell(value):
    if isinstance(value, bytes):
        return f"<blob, {len(value)} bytes>"
    return value


def _size(rows: list[tuple]) -> int:
    total = 0
    for row in rows[:2000]:
        total += 64 + sum(len(v) if isinstance(v, str) else 16 for v in row)
    if len(rows) > 2000:
        total = total * len(rows) // 2000
    return total


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    truncated: bool
    elapsed: float
    owner: tuple[str, str] = ("", "")     # (runner id, acting user id)
    key: str = ""
    id: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    created: float = field(default_factory=time.monotonic)
    size: int = 0


class Canceller:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def execute(user_id: str, sql: str, params: dict | None = None, *, time_limit: bool = True,
            canceller: Canceller | None = None) -> Result:
    """Runs one read-only SELECT as `user_id` sees the data. Blocking: call it in a thread."""
    check_sql(sql)
    started = time.monotonic()
    deadline = started + TIME_LIMIT_SECONDS if time_limit else None
    conn = _open(user_id)
    state = {"why": None}

    def progress():
        if canceller is not None and canceller.cancelled:
            state["why"] = "cancelled"
            return 1
        if deadline is not None and time.monotonic() > deadline:
            state["why"] = "timeout"
            return 1
        return 0

    conn.set_progress_handler(progress, 2000)
    try:
        cur = conn.execute(sql, params or {})
        columns = [d[0] for d in (cur.description or [])]
        rows: list[tuple] = []
        used = 0
        while len(rows) <= MAX_ROWS:
            batch = cur.fetchmany(1000)
            if not batch:
                break
            for r in batch:
                row = tuple(_cell(v) for v in r)
                used += 64 + sum(len(v) if isinstance(v, str) else 16 for v in row)
                rows.append(row)
            if used > MAX_RESULT_BYTES:
                raise QueryError(f"The result is larger than {MAX_RESULT_BYTES // (1024 * 1024)} MB. "
                                 "Select fewer rows or columns (long text columns add up quickly).")
        truncated = len(rows) > MAX_ROWS
        del rows[MAX_ROWS:]
    except sqlite3.ProgrammingError as err:
        if "one statement" in str(err):
            raise QueryError("Run one statement at a time.") from None
        if "binding" in str(err) or "supplied" in str(err):
            raise QueryError(f"A variable is missing a value: {err}") from None
        raise QueryError(str(err)) from None
    except sqlite3.Error as err:
        if state["why"] == "cancelled":
            raise QueryCancelled("The query was cancelled.") from None
        if state["why"] == "timeout":
            raise QueryError(f"The query took longer than {TIME_LIMIT_SECONDS} seconds and was stopped. "
                             "Turn on “No time limit” to let it run longer.") from None
        raise QueryError(_friendly(err)) from None
    finally:
        conn.close()
    result = Result(columns, rows, truncated, time.monotonic() - started)
    result.size = _size(rows)
    return result


def table_overview(user_id: str) -> list[dict]:
    """For the Reference box: every visible table and view, its description, row count and columns."""
    conn = _open(user_id, guard=False)
    try:
        out = []
        descriptions = {**{k: v[0] for k, v in EXTRA_VIEWS.items()}, **{k: v.description for k, v in REGISTRY.items()}}
        existing = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master")}
        for name in visible_names():
            if name not in existing:
                continue
            cols = [(r[1], r[2] or "") for r in conn.execute(f'PRAGMA main.table_info("{name}")')]
            count = conn.execute(f'SELECT COUNT(*) FROM main."{name}"').fetchone()[0]
            out.append({"name": name, "description": descriptions[name], "columns": cols, "count": count,
                        "is_view": name in EXTRA_VIEWS})
        return out
    finally:
        conn.close()


# ---- result cache -------------------------------------------------------------------------------

_cache: dict[str, Result] = {}
_cache_lock = threading.Lock()


def remember(result: Result, runner_id: str, acting_id: str, key: str) -> Result:
    result.owner, result.key = (runner_id, acting_id), key
    with _cache_lock:
        _expire_locked()
        _cache[result.id] = result
        while sum(r.size for r in _cache.values()) > CACHE_MAX_BYTES and len(_cache) > 1:
            oldest = min((r for r in _cache.values() if r.id != result.id), key=lambda r: r.created)
            del _cache[oldest.id]
    return result


def recall(result_id: str, runner_id: str, acting_id: str, key: str | None = None) -> Result | None:
    """A kept result, only for the same person, acting user and (if given) query key."""
    if not result_id:
        return None
    with _cache_lock:
        _expire_locked()
        r = _cache.get(result_id)
    if r is None or r.owner != (runner_id, acting_id) or (key is not None and r.key != key):
        return None
    return r


def _expire_locked():
    now = time.monotonic()
    for rid in [k for k, r in _cache.items() if now - r.created > CACHE_TTL_SECONDS]:
        del _cache[rid]


def clear_cache():
    with _cache_lock:
        _cache.clear()
