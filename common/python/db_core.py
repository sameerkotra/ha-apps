"""SQLite helpers the household apps share (shared: common/python/db_core.py, copied into each app's
app/common/ by tools/sync_common.py).

Raw sqlite3, no ORM. Each app keeps its own `db.py` — its schema, its migrations, its backup checks —
built on these, with its own settings as parameters:

    from .common import db_core

    def _connect():
        return db_core.connect(config.DB_PATH, timeout=10, pragmas=("foreign_keys = ON",))

    def get_conn():
        return db_core.transaction(_connect)        # commit at the end, roll back on an error, close

    MIGRATIONS = [("users", "last_seen", "TEXT"), ...]  # columns older databases lack
    db_core.add_missing_columns(conn, MIGRATIONS)       # at start-up and after a restore

    db_core.snapshot_to_tempfile(_connect)              # a consistent copy for a backup download
    db_core.validate_file(path, REQUIRED_TABLES, app_name="Household Todo")
    db_core.swap_in(tmp_path, config.DB_PATH, get_conn) # restore: replace the live file

Connections are short-lived (one per request or job); WAL lets reads and writes overlap. SQLite
doesn't keep `foreign_keys` in the file, so it's set on every connection. Needs nothing from the app.
"""
import os
import sqlite3
import tempfile
from contextlib import contextmanager


class ClosingConnection(sqlite3.Connection):
    """A connection whose `with conn:` block commits (or rolls back on an exception) AND closes it.
    Plain sqlite3 only commits or rolls back, and leaves the connection open."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(path, *, timeout: float, pragmas=("foreign_keys = ON",), row_factory=sqlite3.Row,
            check_same_thread: bool = True, factory=None, functions=()) -> sqlite3.Connection:
    """A connection to `path`: `timeout` seconds to wait for a lock, rows as sqlite3.Row (by default),
    then each of `pragmas` ("foreign_keys = ON", "busy_timeout = 30000", …) run in order, then each
    SQL function in `functions` — (name, nargs, func, deterministic) — registered."""
    kw = {"timeout": timeout}
    if not check_same_thread:
        kw["check_same_thread"] = False
    if factory is not None:
        kw["factory"] = factory
    conn = sqlite3.connect(path, **kw)
    if row_factory is not None:
        conn.row_factory = row_factory
    for pragma in pragmas:
        conn.execute(f"PRAGMA {pragma}")
    for name, nargs, func, deterministic in functions:
        conn.create_function(name, nargs, func, deterministic=deterministic)
    return conn


@contextmanager
def transaction(connect_fn):
    """`with transaction(_connect) as conn:` — commits when the block ends, rolls back (and re-raises)
    on an exception, and always closes the connection."""
    conn = connect_fn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def closing(connect_fn):
    """`with closing(_connect) as conn:` — only closes the connection at the end (the code inside
    commits itself; anything it didn't commit is rolled back by the close)."""
    conn = connect_fn()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------
# Migrations: columns that older databases lack
# ---------------------------------------------------------------------------------------------
def columns(conn, table: str) -> set:
    """The column names of `table` (empty when there is no such table)."""
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def add_missing_columns(conn, migrations) -> list:
    """`CREATE TABLE IF NOT EXISTS` never adds a column to a table an older version made, so each app
    lists the columns added since: `migrations` is a list of (table, column, definition), or a dict
    {table: [(column, definition), …]}. Adds the ones that are missing, in order; returns them as
    "table.column". Safe to run at every start-up and right after a restore."""
    if isinstance(migrations, dict):
        migrations = [(t, c, d) for t, cols in migrations.items() for c, d in cols]
    added, have = [], {}
    for table, col, definition in migrations:
        if table not in have:
            have[table] = columns(conn, table)
        if col not in have[table]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            have[table].add(col)
            added.append(f"{table}.{col}")
    return added


# ---------------------------------------------------------------------------------------------
# Backup and restore
# ---------------------------------------------------------------------------------------------
def snapshot(src: sqlite3.Connection, dst_path, after=None) -> None:
    """Copy the database `src` is connected to into the file `dst_path` through SQLite's online
    backup API — never a plain file copy: in WAL mode recent commits may still be in the -wal file.
    `after(dst)` may change the copy before it's closed (it commits its own changes)."""
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
        if after is not None:
            after(dst)
    finally:
        dst.close()


def snapshot_to_tempfile(connect_fn, *, suffix: str = ".db", prefix: str | None = None,
                         dir: str | None = None, after=None) -> str:
    """`snapshot` of the live database (opened with `connect_fn`) into a new temp file; returns its
    path. The caller deletes the file."""
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
    os.close(fd)
    src = connect_fn()
    try:
        snapshot(src, tmp_path, after)
    finally:
        src.close()
    return tmp_path


INTEGRITY_MSG = "That file failed a database integrity check ({integrity}) — refusing to import it."
MISSING_MSG = "That doesn't look like a {app} database (missing tables: {missing})."
INVALID_MSG = "That file isn't a valid SQLite database."


def validate_file(path, required_tables, *, app_name: str = "", integrity_msg: str = INTEGRITY_MSG,
                  missing_msg: str = MISSING_MSG, invalid_msg: str = INVALID_MSG,
                  setup=None, extra=None) -> set:
    """Check that `path` is safe to restore: a SQLite file that passes `PRAGMA integrity_check` and has
    every one of `required_tables`. Raises ValueError with a message for the person otherwise
    (`integrity_msg` gets {integrity}; `missing_msg` gets {app} and {missing}). `setup(conn)` runs
    first (e.g. to register a SQL function an index uses); `extra(conn, tables)` adds the app's own
    checks. Returns the file's table names."""
    try:
        conn = sqlite3.connect(path)
        try:
            if setup is not None:
                setup(conn)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(integrity_msg.format(integrity=integrity))
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = set(required_tables) - tables
            if missing:
                raise ValueError(missing_msg.format(app=app_name, missing=", ".join(sorted(missing))))
            if extra is not None:
                extra(conn, tables)
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise ValueError(invalid_msg)
    return tables


def swap_in(tmp_path, db_path, get_conn) -> None:
    """Replace the live database file with an already-validated one: checkpoint and empty the current
    WAL (through the app's `get_conn` context manager; a database that can't be opened is skipped),
    move the new file into place, and remove the old -wal/-shm files so nothing replays stale WAL
    frames against it. `tmp_path` must be on the same filesystem as `db_path`. The caller holds its
    own lock against concurrent writers, and runs its start-up migrations afterwards."""
    try:
        with get_conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    os.replace(tmp_path, db_path)
    for ext in ("-wal", "-shm"):
        sidecar = str(db_path) + ext
        if os.path.exists(sidecar):
            os.remove(sidecar)
