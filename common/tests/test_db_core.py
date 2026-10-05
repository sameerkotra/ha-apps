"""The shared SQLite helpers (common/python/db_core.py), tested in every app that uses them."""
import os
import sqlite3
import tempfile
import threading
import unittest

from app.common import db_core


class DbCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "t.db")

    def _connect(self):
        return db_core.connect(self.path, timeout=5)

    def test_connect_applies_pragmas_in_order(self):
        conn = db_core.connect(self.path, timeout=5, pragmas=("journal_mode = WAL", "foreign_keys = ON",
                                                              "busy_timeout = 1234", "secure_delete = ON"))
        try:
            self.assertIs(conn.row_factory, sqlite3.Row)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 1234)
            self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            conn.close()

    def test_functions_and_threads(self):
        conn = db_core.connect(self.path, timeout=5, check_same_thread=False,
                               functions=[("twice", 1, lambda x: x * 2, True)])
        try:
            self.assertEqual(conn.execute("SELECT twice(21)").fetchone()[0], 42)
            out = []
            t = threading.Thread(target=lambda: out.append(conn.execute("SELECT 1").fetchone()[0]))
            t.start()
            t.join()
            self.assertEqual(out, [1])
        finally:
            conn.close()

    def test_transaction_commits_or_rolls_back(self):
        with db_core.transaction(self._connect) as c:
            c.execute("CREATE TABLE t (x INTEGER)")
            c.execute("INSERT INTO t VALUES (1)")
        with self.assertRaises(RuntimeError):
            with db_core.transaction(self._connect) as c:
                c.execute("INSERT INTO t VALUES (2)")
                raise RuntimeError("boom")
        with db_core.closing(self._connect) as c:
            self.assertEqual([r[0] for r in c.execute("SELECT x FROM t")], [1])
            c.execute("INSERT INTO t VALUES (3)")                # never committed
        with db_core.closing(self._connect) as c:
            self.assertEqual([r[0] for r in c.execute("SELECT x FROM t")], [1])

    def test_closing_connection_class(self):
        with db_core.connect(self.path, timeout=5, factory=db_core.ClosingConnection) as c:
            c.execute("CREATE TABLE t (x INTEGER)")
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")                                 # closed at the end of the block

    def test_add_missing_columns(self):
        with db_core.transaction(self._connect) as c:
            c.execute("CREATE TABLE a (id INTEGER)")
            c.execute("CREATE TABLE b (id INTEGER, y TEXT)")
            added = db_core.add_missing_columns(c, [("a", "x", "TEXT"), ("b", "y", "TEXT"),
                                                    ("a", "z", "INTEGER NOT NULL DEFAULT 0")])
            self.assertEqual(added, ["a.x", "a.z"])
            self.assertEqual(db_core.add_missing_columns(c, {"a": [("x", "TEXT")], "b": [("w", "REAL")]}), ["b.w"])
            self.assertEqual(db_core.columns(c, "a"), {"id", "x", "z"})

    def test_snapshot_validate_and_swap(self):
        with db_core.transaction(self._connect) as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("CREATE TABLE users (id TEXT)")
            c.execute("CREATE TABLE app_settings (key TEXT, value TEXT)")
            c.execute("INSERT INTO users VALUES ('u1')")
            c.execute("INSERT INTO app_settings VALUES ('secret', '\"k\"')")

        def blank(dst):
            dst.execute("UPDATE app_settings SET value = '\"\"'")
            dst.commit()

        snap = db_core.snapshot_to_tempfile(self._connect, dir=self.tmp.name, after=blank)
        self.assertEqual(db_core.validate_file(snap, {"users"}), {"users", "app_settings"})
        with sqlite3.connect(snap) as s:
            self.assertEqual(s.execute("SELECT value FROM app_settings").fetchone()[0], '""')
        with self.assertRaises(ValueError) as e:
            db_core.validate_file(snap, {"users", "people"}, app_name="Test App")
        self.assertEqual(str(e.exception), "That doesn't look like a Test App database (missing tables: people).")
        junk = os.path.join(self.tmp.name, "junk.db")
        with open(junk, "wb") as f:
            f.write(b"not a database" * 100)
        with self.assertRaises(ValueError) as e:
            db_core.validate_file(junk, {"users"})
        self.assertEqual(str(e.exception), "That file isn't a valid SQLite database.")

        def too_new(conn, tables):
            raise ValueError("newer")
        with self.assertRaises(ValueError) as e:
            db_core.validate_file(snap, {"users"}, extra=too_new)
        self.assertEqual(str(e.exception), "newer")

        with db_core.transaction(self._connect) as c:
            c.execute("INSERT INTO users VALUES ('u2')")
        db_core.swap_in(snap, self.path, lambda: db_core.transaction(self._connect))
        self.assertFalse(os.path.exists(snap))
        self.assertFalse(os.path.exists(self.path + "-wal"))
        with db_core.closing(self._connect) as c:
            self.assertEqual([r[0] for r in c.execute("SELECT id FROM users")], ["u1"])


if __name__ == "__main__":
    unittest.main()
