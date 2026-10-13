#!/usr/bin/env python3
"""Run by the Supervisor inside the app's container just before Home Assistant backs the app up
(config.yaml: backup_pre).

The database runs in SQLite's WAL mode: recent changes sit in finance.db-wal until SQLite copies them
into finance.db. A backup taken file by file could catch the two at different moments. This copies
everything into finance.db first and empties the -wal file (a "checkpoint"), so the backup's
finance.db is complete on its own. It waits up to 30 seconds for a writer to finish, and never fails
the backup: at worst the backup is as good as it was before this hook existed.
"""
import os
import sqlite3
import sys

DB_PATH = os.environ.get("DB_PATH", "/data/finance.db")


def checkpoint(path: str = DB_PATH) -> bool:
    if not os.path.exists(path):
        return False
    try:
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            busy, _log, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            return busy == 0
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"Finance Dashboard: couldn't prepare the database for the backup: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    ok = checkpoint()
    print("Finance Dashboard: database ready for the backup" if ok else
          "Finance Dashboard: the database was busy; it is backed up as it is")
    sys.exit(0)
