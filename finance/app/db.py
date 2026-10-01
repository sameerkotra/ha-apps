"""Raw sqlite3 access — no SQLAlchemy (SPEC.md section 12).

Every connection carries the pragmas required once background processing
(section 4) makes concurrent DB access routine: WAL so htmx status-polling
reads never block on a background writer, busy_timeout so a brief lock
contention retries instead of raising immediately, foreign_keys because
SQLite doesn't enforce them by default, synchronous=NORMAL as the standard
pairing with WAL.
"""
import re
import sqlite3
import os
from pathlib import Path

from .datenorm import normalize_date

DB_PATH = os.environ.get("DB_PATH", "/data/finance.db")
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


class _Connection(sqlite3.Connection):
    """`with get_db() as conn:` commits (or rolls back on an exception) AND
    closes the connection at the end of the block. Plain sqlite3 only does the
    commit/rollback and leaves the connection open until garbage collection."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def get_db() -> sqlite3.Connection:
    """One connection per request/task. Row factory gives dict-like access
    in templates (row["name"] and row.name both work). Use it as a context
    manager, or call close() yourself."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0, factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def run_migrations() -> None:
    """Applies any migrations/NNNN_*.sql not yet recorded in schema_version,
    in numeric order, each as its own executescript (so a migration that
    fails partway doesn't leave schema_version out of sync with what
    actually landed — sqlite3's executescript auto-commits any prior
    statement in the script, so migrations should each be self-contained
    rather than relying on cross-file atomicity).

    Safe to call every startup: a fully-migrated DB does nothing.
    """
    conn = get_db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in migration_files:
        # Filenames are "NNNN_description.sql" — the leading number is the
        # version. A file that doesn't parse this way is a naming mistake,
        # not silently skipped.
        version = int(path.stem.split("_", 1)[0])
        if version in applied:
            continue
        conn.executescript(path.read_text())

        # Enforcement lives here, not in any migration file: PRAGMA
        # foreign_key_check returns rows describing violations (like a
        # SELECT) rather than raising on its own — a migration that
        # includes the pragma statement but never has its result checked
        # would silently do nothing. This applies after every migration,
        # not just ones that happen to rebuild a table (0003 does; a
        # future one might not need to) — cheap when clean, and it's the
        # one place that actually stops a broken migration from being
        # recorded as applied.
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"{path.name} left foreign key violations: {violations} — "
                "not recording it as applied."
            )

        # Each migration file is responsible for its own
        # `INSERT INTO schema_version (version) VALUES (N)` — this just
        # confirms it actually happened rather than trusting the file.
        row = conn.execute("SELECT 1 FROM schema_version WHERE version = ?", (version,)).fetchone()
        if row is None:
            raise RuntimeError(
                f"{path.name} ran but never recorded version {version} in schema_version — "
                "every migration file must end with its own INSERT INTO schema_version."
            )

    conn.close()


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_legacy_transaction_dates() -> int:
    """Rewrite any stored transaction date that isn't ISO but can be parsed
    confidently (app/datenorm.py) — every date filter and sort compares the
    stored string. Runs at every startup; an already-clean database does no
    writes. Unparseable dates are left alone (recurring detection skips
    them). Returns the number of rows fixed.
    """
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, date FROM transactions").fetchall()
        fixed = 0
        for row in rows:
            raw = row["date"] or ""
            if _ISO_DATE_RE.match(raw):
                continue
            normalized = normalize_date(raw)
            if normalized is not None and normalized != raw:
                conn.execute("UPDATE transactions SET date = ? WHERE id = ?", (normalized, row["id"]))
                fixed += 1
        if fixed:
            conn.commit()
        return fixed
    finally:
        conn.close()
