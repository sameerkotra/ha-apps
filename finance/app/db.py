"""Raw sqlite3 access — no SQLAlchemy (SPEC.md section 12).

Every connection carries the pragmas required once background processing
(section 4) makes concurrent DB access routine: WAL so htmx status-polling
reads never block on a background writer, busy_timeout so a brief lock
contention retries instead of raising immediately, foreign_keys because
SQLite doesn't enforce them by default, synchronous=NORMAL as the standard
pairing with WAL.
"""
import logging
import re
import sqlite3
import os
import threading
from pathlib import Path

from .common import db_core
from .datenorm import normalize_date

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/finance.db")
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# Background writers (the PDF jobs, the purge sweep) run inside GATE.work(); a backup restore swaps the
# database file inside GATE.pause(), so no job is half-way through writing to the file being replaced.
GATE = db_core.MaintenanceGate()


# `with get_db() as conn:` commits (or rolls back on an exception) AND
# closes the connection at the end of the block. Plain sqlite3 only does the
# commit/rollback and leaves the connection open until garbage collection.
_Connection = db_core.ClosingConnection


class ChangeWatcher:
    """Tells whether the database changed since last asked, cheaply: one long-lived read connection
    whose `PRAGMA data_version` moves whenever any other connection commits (no table is read). A
    restore swaps the database file, so the file's inode is part of the answer too. Used for the
    in-memory copies of App settings and of the Recurring scan. Thread-safe."""

    def __init__(self):
        self.lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._ident = None

    def generation(self):
        """(file identity, data_version), or None when the database can't be read (no file yet)."""
        with self.lock:
            return self._generation_locked()

    def _generation_locked(self):
        try:
            st = os.stat(DB_PATH)
            ident = (DB_PATH, st.st_dev, st.st_ino)
            if self._conn is None or ident != self._ident:
                self._close_locked()
                self._conn = sqlite3.connect(DB_PATH, timeout=10.0, check_same_thread=False)
                self._ident = ident
            return ident, self._conn.execute("PRAGMA data_version").fetchone()[0]
        except (OSError, sqlite3.Error):
            self._close_locked()
            return None

    def read(self, sql: str, params=()):
        """Run a small read on the watcher's own connection (call with `lock` held)."""
        return self._conn.execute(sql, params).fetchall()

    def _close_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        self._conn, self._ident = None, None

    def forget(self) -> None:
        with self.lock:
            self._close_locked()


WATCHER = ChangeWatcher()


def get_db() -> sqlite3.Connection:
    """One connection per request/task. Row factory gives dict-like access
    in templates (row["name"] and row.name both work). Use it as a context
    manager, or call close() yourself."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return db_core.connect(DB_PATH, timeout=10.0, factory=_Connection,
                           pragmas=("journal_mode = WAL", "busy_timeout = 5000", "foreign_keys = ON",
                                    "synchronous = NORMAL"))


_FK_PRAGMA = re.compile(r"^\s*PRAGMA\s+foreign_keys\s*=\s*(ON|OFF)\s*;?\s*$", re.IGNORECASE | re.MULTILINE)


def run_migrations() -> None:
    """Applies any migrations/NNNN_*.sql not yet recorded in schema_version,
    in numeric order, each in ONE transaction (BEGIN IMMEDIATE ... COMMIT):
    a migration that fails part-way — a power cut in the middle of a table
    rebuild, a broken statement — leaves the database exactly as it was
    before it, never half-changed.

    PRAGMA foreign_keys can't change inside a transaction, so a migration's
    own "PRAGMA foreign_keys = OFF/ON" lines (a table rebuild) are applied
    around its transaction instead. Before the first pending migration of a
    run, a copy of the database is kept as <db>.pre-NNNN (only the newest
    copy is kept), so even a migration that runs fine but turns out wrong
    can be undone by hand.

    Safe to call every startup: a fully-migrated DB does nothing.
    """
    conn = get_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.commit()

        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}

        # Filenames are "NNNN_description.sql" — the leading number is the
        # version. A file that doesn't parse this way is a naming mistake,
        # not silently skipped.
        pending = [(int(path.stem.split("_", 1)[0]), path) for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
        pending = [(version, path) for version, path in pending if version not in applied]
        if pending and applied:
            _keep_copy_before(conn, pending[0][0])

        for version, path in pending:
            script = path.read_text()
            fk_off = any(m.group(1).upper() == "OFF" for m in _FK_PRAGMA.finditer(script))
            script = _FK_PRAGMA.sub("", script)
            if fk_off:
                conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.executescript("BEGIN IMMEDIATE;\n" + script)
                _check_migration(conn, path.name, version)
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                if fk_off:
                    conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def _check_migration(conn: sqlite3.Connection, name: str, version: int) -> None:
    """Inside the migration's transaction, before it commits.

    Enforcement lives here, not in any migration file: PRAGMA
    foreign_key_check returns rows describing violations (like a
    SELECT) rather than raising on its own — a migration that
    includes the pragma statement but never has its result checked
    would silently do nothing. This applies after every migration,
    not just ones that happen to rebuild a table — cheap when clean,
    and it's the one place that actually stops a broken migration from
    being recorded as applied (the whole migration is rolled back)."""
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            f"{name} left foreign key violations: {[tuple(v) for v in violations]} — "
            "not recording it as applied."
        )
    # Each migration file is responsible for its own
    # `INSERT INTO schema_version (version) VALUES (N)` — this just
    # confirms it actually happened rather than trusting the file.
    if conn.execute("SELECT 1 FROM schema_version WHERE version = ?", (version,)).fetchone() is None:
        raise RuntimeError(
            f"{name} ran but never recorded version {version} in schema_version — "
            "every migration file must end with its own INSERT INTO schema_version."
        )


def _keep_copy_before(conn: sqlite3.Connection, version: int) -> None:
    """A consistent copy of the database (SQLite's backup API, so nothing still in the WAL is missed)
    as <db>.pre-NNNN, replacing the copy an earlier upgrade left. Best effort: no room for the copy
    never stops the app from starting."""
    target = f"{DB_PATH}.pre-{version:04d}"
    tmp = target + ".part"
    try:
        dest = sqlite3.connect(tmp)
        try:
            conn.backup(dest)
        finally:
            dest.close()
        os.replace(tmp, target)
        for old in Path(DB_PATH).parent.glob(Path(DB_PATH).name + ".pre-*"):
            if str(old) != target:
                old.unlink(missing_ok=True)
    except (OSError, sqlite3.Error):
        logger.warning("Couldn't keep a copy of the database before upgrading it", exc_info=True)
        try:
            os.remove(tmp)
        except OSError:
            pass


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
        # Only rows that aren't already ISO (an indexed scan; a clean database reads nothing else).
        rows = conn.execute(
            "SELECT id, date FROM transactions WHERE date IS NULL "
            "OR date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").fetchall()
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
