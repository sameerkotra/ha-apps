"""The shared App settings registry (common/python/settings_core.py), tested in every app that uses it."""
import contextlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import unittest
from typing import Literal

from app.common import settings_core as sc


LOG = logging.getLogger("settings_core_test")       # an app's tests may quieten the "settings" logger


def _upper(v):
    if v != v.strip():
        raise ValueError("no spaces around it")
    return v.upper()


class Registry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "t.db")
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT, updated_by TEXT)")
        conn.commit()
        conn.close()
        self.gen = 0
        self.writes = []
        # an app's tests may switch logging off (logging.disable): these tests check what is logged
        was = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, was)

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def make(self, **kw):
        settings = kw.pop("settings", None) or [
            sc.Setting("days", 30, "Days to keep", group="a", min=1, max=90, strict=True, unit="days", help="How long."),
            sc.Setting("on", True, "Switched on", group="a", strict=True),
            sc.Setting("mode", "x", "Mode", group="b", choices=[("x", "Ex"), ("y", "Why")]),
            sc.Setting("code", "AB", "Code", group="b", strict=True, max_length=5, validators=[_upper]),
            sc.Setting("key", "", "Access key", group="b", secret=True),
            sc.Setting("ids", [], "People", group="b", hidden=True),
            sc.Setting("price", 0.0, "Price", group="b", min=0, max=10, show_if="on"),
        ]
        kw.setdefault("groups", [sc.Group("a", "First", "The first."), sc.Group("b", "Second")])
        kw.setdefault("now", lambda: "NOW")
        kw.setdefault("logger", LOG)
        return sc.Registry(settings, connect=self.connect, **kw)

    def rows(self):
        conn = sqlite3.connect(self.path)
        try:
            return {r[0]: (json.loads(r[1]), r[3]) for r in conn.execute("SELECT * FROM app_settings")}
        finally:
            conn.close()

    def test_defaults_and_payload_shape(self):
        r = self.make()
        self.assertEqual(r.all(), {"days": 30, "on": True, "mode": "x", "code": "AB", "key": "", "ids": [], "price": 0.0})
        p = r.payload(extra=1)
        self.assertEqual(set(p), {"values", "defaults", "meta", "groups", "secretsSet", "extra"})
        self.assertEqual(p["groups"], [{"id": "a", "label": "First", "help": "The first."}, {"id": "b", "label": "Second", "help": ""}])
        self.assertEqual(p["meta"]["days"], {"label": "Days to keep", "help": "How long.", "group": "a", "kind": "int",
                                             "restartRequired": False, "min": 1, "max": 90, "unit": "days"})
        self.assertEqual(p["meta"]["mode"]["choices"], [{"value": "x", "label": "Ex"}, {"value": "y", "label": "Why"}])
        self.assertEqual(p["meta"]["key"]["kind"], "secret")
        self.assertTrue(p["meta"]["ids"]["hidden"])
        self.assertEqual(p["meta"]["price"]["showIf"], "on")
        self.assertEqual(p["meta"]["code"]["maxLength"], 5)
        self.assertEqual(list(p["meta"]), list(r.keys))              # the page's order

    def test_update_writes_only_changed_keys(self):
        r = self.make()
        values, changed = r.update({"days": 30, "on": False, "code": "cd"}, "alex")
        self.assertEqual(changed, ["on", "code"])
        self.assertEqual((values["on"], values["code"]), (False, "CD"))
        self.assertEqual(self.rows(), {"on": (False, "alex"), "code": ("CD", "alex")})
        self.assertEqual(r.get("code"), "CD")

    def test_bad_values_store_nothing(self):
        r = self.make()
        for body in ({"days": 0}, {"days": 91}, {"days": "5"}, {"days": 5.0}, {"on": 1}, {"mode": "z"},
                     {"code": " x "}, {"code": "toolong"}, {"nope": 1}, {"days": 5, "nope": 1}, [1], "x",
                     {"price": float("nan")}, {"price": -1}):
            with self.subTest(body=body):
                with self.assertRaises(sc.SettingsError):
                    r.update(body, "alex")
        self.assertEqual(self.rows(), {})

    def test_messages(self):
        r = self.make()
        with self.assertRaises(sc.SettingsError) as cm:
            r.update({"days": 0}, None)
        self.assertEqual(str(cm.exception), "Days to keep: Input should be greater than or equal to 1")
        with self.assertRaises(sc.SettingsError) as cm:
            r.update({"code": " x "}, None)
        self.assertEqual(str(cm.exception), "Code: no spaces around it")
        with self.assertRaises(sc.SettingsError) as cm:
            r.update({"b": 1, "a": 2}, None)
        self.assertEqual(str(cm.exception), "Unknown settings: a, b")
        r2 = self.make(format_error=sc.by_key, unknown_message=lambda k: "nope " + ",".join(k), not_object_message="obj")
        with self.assertRaises(sc.SettingsError) as cm:
            r2.update({"days": 0}, None)
        self.assertEqual(str(cm.exception), "days: Input should be greater than or equal to 1")
        with self.assertRaises(sc.SettingsError) as cm:
            r2.update({"zz": 0}, None)
        self.assertEqual(str(cm.exception), "nope zz")
        with self.assertRaises(sc.SettingsError) as cm:
            r2.update([], None)
        self.assertEqual(str(cm.exception), "obj")

    def test_validate_one_ignores_the_other_settings(self):
        r = self.make()
        self.assertEqual(r.validate_one("code", "ab"), "AB")
        with self.assertRaises(sc.SettingsError):
            r.validate_one("days", 0)
        with self.assertRaises(sc.SettingsError):
            r.validate_one("nope", 1)

    def test_stored_rows_that_no_longer_validate_are_ignored(self):
        r = self.make()
        conn = sqlite3.connect(self.path)
        conn.executemany("INSERT INTO app_settings VALUES (?, ?, 'x', NULL)",
                         [("days", "500"), ("on", "not json"), ("mode", '"y"'), ("other_app_row", '"kept"')])
        conn.commit()
        conn.close()
        with self.assertLogs(LOG, level="WARNING"):
            values = r.all()
        self.assertEqual((values["days"], values["on"], values["mode"]), (30, True, "y"))
        self.assertNotIn("other_app_row", values)

    def test_load_checks_by_type(self):
        conn = sqlite3.connect(self.path)
        conn.executemany("INSERT INTO app_settings VALUES (?, ?, 'x', NULL)", [("days", "500"), ("on", "1")])
        conn.commit()
        conn.close()
        self.assertEqual(self.make(load_check="isinstance").all()["days"], 500)      # kept as stored
        self.assertTrue(self.make(load_check="isinstance").all()["on"])               # 1 isn't a bool: default
        self.assertEqual(self.make(load_check="type").all()["days"], 500)

    def test_secrets(self):
        r = self.make()
        r.update({"key": "s3cret"}, None)
        p = r.payload()
        self.assertEqual(p["values"]["key"], "")
        self.assertEqual(p["defaults"]["key"], "")
        self.assertEqual(p["secretsSet"], {"key": True})
        self.assertEqual(r.get("key"), "s3cret")
        omit = self.make(secret_values="omit").payload()
        self.assertNotIn("key", omit["values"])
        self.assertNotIn("s3cret", json.dumps(p))

    def test_flags_prepare_and_check(self):
        seen = []

        def prepare(partial, current, flags):
            seen.append(flags)
            if flags.get("clear_key"):
                partial["key"] = ""
            return partial

        def check(merged, partial, current):
            if merged["mode"] == "y" and merged["days"] > 50:
                raise sc.SettingsError("Why needs 50 days or less.")
        r = self.make(flags={"clear_key": "clear_key: true or false"}, prepare=prepare, check=check)
        r.update({"key": "abc"}, None)
        r.update({"clear_key": True}, None)
        self.assertEqual(r.get("key"), "")
        self.assertEqual(seen[-1], {"clear_key": True})
        with self.assertRaises(sc.SettingsError) as cm:
            r.update({"clear_key": "yes"}, None)
        self.assertEqual(str(cm.exception), "clear_key: true or false")
        r.update({"days": 60}, None)
        with self.assertRaises(sc.SettingsError):
            r.update({"mode": "y"}, None)
        self.assertEqual(r.get("mode"), "x")

    def test_hooks_and_custom_storage(self):
        written, changes = [], []
        r = self.make(on_write=lambda conn, changed, before, after, who: written.append((changed, who)),
                      on_change=[lambda changed, after: changes.append(changed),
                                 lambda changed, after: 1 / 0])            # a failing hook is only logged
        with self.assertLogs(LOG, level="ERROR"):
            r.update({"days": 7}, "sam")
        self.assertEqual(written, [(["days"], "sam")])
        self.assertEqual(changes, [["days"]])
        r.update({"days": 7}, "sam")                       # nothing changed: no hooks
        self.assertEqual(len(written), 1)
        enc = self.make(encode=lambda v: "1" if v is True else "0" if v is False else str(v),
                        decode=lambda text, key: (text == "1") if key == "on" else int(text) if key == "days" else json.loads(text),
                        write_all=True, load_check="type")
        enc.update({"on": False, "days": 9}, None)
        conn = sqlite3.connect(self.path)
        stored = dict(conn.execute("SELECT key, value FROM app_settings").fetchall())
        conn.close()
        self.assertEqual((stored["on"], stored["days"]), ("0", "9"))
        self.assertEqual(set(stored), set(enc.keys))                # write_all
        self.assertEqual(enc.all()["on"], False)

    def test_cache_is_dropped_on_write_generation_and_ttl(self):
        r = self.make(generation=lambda: self.gen)
        self.assertEqual(r.get("days"), 30)
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO app_settings VALUES ('days', '40', 'x', NULL)")
        conn.commit()
        conn.close()
        self.assertEqual(r.get("days"), 30)                 # cached
        self.gen += 1                                        # the database was replaced
        self.assertEqual(r.get("days"), 40)
        nocache = self.make(cache_ttl=0)
        self.assertEqual(nocache.get("days"), 40)
        r.invalidate()
        self.assertEqual(r.get("days"), 40)

    def test_a_read_racing_a_write_is_not_cached(self):
        r = self.make()
        real, fired = r.load, []

        def load_then_write(conn):
            values = real(conn)
            if not fired:
                fired.append(1)
                r.update({"days": 60}, None)
            return values
        r.load = load_then_write
        self.assertEqual(r.all()["days"], 30)
        r.load = real
        self.assertEqual(r.get("days"), 60)

    def test_fallback_on_error(self):
        @contextlib.contextmanager
        def broken():
            raise sqlite3.OperationalError("no table")
            yield
        r = sc.Registry([sc.Setting("a", 1, "A")], connect=broken, fallback_on_error=True, logger=LOG)
        with self.assertLogs(LOG, level="ERROR"):
            self.assertEqual(r.all(), {"a": 1})
        r2 = sc.Registry([sc.Setting("a", 1, "A")], connect=broken)
        with self.assertRaises(sqlite3.OperationalError):
            r2.all()

    def test_types_and_write_lock(self):
        lock = threading.Lock()
        r = self.make(settings=[sc.Setting("n", 1, "N", type=int, choices=[(1, "one"), (2, "two")]),
                                sc.Setting("lit", "a", "L", type=Literal["a", "b"]),
                                sc.Setting("days", [1], "Days", type=list[int])], write_lock=lock)
        self.assertEqual(r.meta()["n"]["kind"], "choice")
        self.assertEqual(r.update({"n": 5, "days": [3, 1]}, None)[0]["n"], 5)      # type=int: choices are only for the page
        with self.assertRaises(sc.SettingsError):
            r.update({"lit": "c"}, None)
        self.assertFalse(lock.locked())

    def test_setting_listed_twice_or_unknown_group(self):
        with self.assertRaises(ValueError):
            sc.Registry([sc.Setting("a", 1), sc.Setting("a", 2)], connect=self.connect)
        with self.assertRaises(ValueError):
            sc.Registry([sc.Setting("a", 1, group="zz")], groups=[sc.Group("b", "B")], connect=self.connect)


class Backups(unittest.TestCase):
    """Secret settings and backups: the registry knows which settings are secret; backup_core does the SQL."""
    setUp, connect, make, rows = Registry.setUp, Registry.connect, Registry.make, Registry.rows

    def test_secret_keys_and_blanks(self):
        r = self.make()
        self.assertEqual(r.secret_keys(), ("key",))
        self.assertEqual(r.secret_blanks(), {"key": '""'})

    def test_scrub_secrets_in_a_copy(self):
        r = self.make()
        r.update({"key": "sk-123", "days": 7}, "admin")
        copy = os.path.join(self.tmp.name, "copy.db")
        src = sqlite3.connect(self.path)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
            r.scrub_secrets(dst)
        finally:
            dst.close()
            src.close()
        conn = sqlite3.connect(copy)
        try:
            rows = dict(conn.execute("SELECT key, value FROM app_settings").fetchall())
        finally:
            conn.close()
        self.assertEqual(rows["key"], '""')
        self.assertEqual(rows["days"], "7")
        with open(copy, "rb") as f:
            self.assertNotIn(b"sk-123", f.read())
        self.assertEqual(r.all()["key"], "sk-123")          # the live database keeps it

    def test_keep_secrets_over_a_restored_file_without_them(self):
        r = self.make()
        r.update({"key": "sk-123"}, "admin")
        saved = r.saved_secrets()
        self.assertEqual(saved, {"key": '"sk-123"'})
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE app_settings SET value = '\"\"' WHERE key = 'key'")   # the restored file
        conn.commit()
        conn.close()
        r.invalidate()
        self.assertEqual(r.all()["key"], "")
        self.assertEqual(r.keep_secrets(saved), ["key"])
        self.assertEqual(r.all()["key"], "sk-123")
        self.assertEqual(self.rows()["key"], ("sk-123", "kept on restore"))

    def test_keep_secrets_inserts_a_missing_row(self):
        r = self.make()
        saved = {"key": '"sk-9"'}
        self.assertEqual(r.keep_secrets(saved), ["key"])
        self.assertEqual(r.all()["key"], "sk-9")
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("SELECT updated_at FROM app_settings WHERE key='key'").fetchone()[0], "NOW")
        finally:
            conn.close()

    def test_a_secret_the_restored_file_carries_wins(self):
        r = self.make()
        r.update({"key": "from-backup"}, "admin")
        self.assertEqual(r.keep_secrets({"key": '"ours"'}), [])
        self.assertEqual(r.all()["key"], "from-backup")

    def test_nothing_saved_when_unset(self):
        r = self.make()
        self.assertEqual(r.saved_secrets(), {})
        self.assertEqual(r.keep_secrets({}), [])
        self.assertEqual(r.all()["key"], "")

    def test_no_secrets(self):
        r = self.make(settings=[sc.Setting("days", 30, "Days")])
        self.assertEqual(r.secret_keys(), ())
        self.assertEqual(r.secret_blanks(), {})
        conn = sqlite3.connect(self.path)
        try:
            r.scrub_secrets(conn)
            self.assertEqual(r.saved_secrets(conn), {})
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
