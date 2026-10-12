# Shared file: edit common/tests/test_backup_core.py and run tools/sync_common.py; don't edit this copy. sha256=ea63546b67edc327e30291f3ca9cd187ba0b021d4d6426666d8a61e0829c2c22
"""The shared backup/restore helpers (common/python/backup_core.py), tested in every app that uses them."""
import asyncio
import io
import os
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.testclient import TestClient

from app.common import backup_core

try:
    from app.common import db_core  # noqa: F401  (restore_file needs it; not every app has it)
except ImportError:
    db_core = None


class FakeUpload:
    """What backup_core.receive reads from an UploadFile: async read(n)."""
    def __init__(self, data: bytes):
        self.buf = io.BytesIO(data)

    async def read(self, n=-1):
        return self.buf.read(n)


class BackupCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def put(self, rel, data=b"x"):
        p = self.path(*rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    # ---- download ----
    def test_file_name(self):
        now = datetime(2026, 9, 21, 7, 5, 3)
        self.assertEqual(backup_core.file_name("x-backup", ".db", now=now), "x-backup-20260921-070503.db")
        self.assertEqual(backup_core.file_name("x", ".zip", now=now, fmt="%Y-%m-%d", suffix="-with-photos"),
                         "x-2026-09-21-with-photos.zip")
        self.assertRegex(backup_core.file_name("x", ".db"), r"^x-\d{8}-\d{6}\.db$")

    def test_send_file_deletes_after_sending(self):
        p = self.put("snap.db", b"SQLite format 3\x00...")
        app = FastAPI()
        app.get("/dl")(lambda: backup_core.send_file(p, "a-1.db"))
        r = TestClient(app).get("/dl")
        self.assertEqual(r.content, b"SQLite format 3\x00...")
        self.assertEqual(r.headers["content-type"], "application/vnd.sqlite3")
        self.assertIn('filename="a-1.db"', r.headers["content-disposition"])
        self.assertFalse(os.path.exists(p))

    def test_walk_and_write_zip(self):
        root = self.path("files")
        self.put("files/a.txt", b"A")
        self.put("files/sub/b.txt", b"B")
        self.put("files/skip/c.txt", b"C")
        self.put("files/.hidden", b"H")

        def prune(dirpath, dirnames):
            if "skip" in dirnames:
                dirnames.remove("skip")
            return False

        found = sorted(backup_core.walk(root, prefix="files/", prune=prune,
                                        keep=lambda full, rel: not os.path.basename(full).startswith(".")))
        self.assertEqual([n for _p, n in found], ["files/a.txt", "files/sub/b.txt"])
        db = self.put("x.db", b"DB")
        buf = io.BytesIO()
        backup_core.write_zip(buf, [(db, "x.db"), backup_core.Text("backup.json", "{}")]
                              + [(p, n, zipfile.ZIP_STORED) for p, n in found])
        with zipfile.ZipFile(buf) as z:
            self.assertEqual(z.namelist(), ["x.db", "backup.json", "files/a.txt", "files/sub/b.txt"])
            self.assertEqual(z.getinfo("files/a.txt").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.getinfo("x.db").compress_type, zipfile.ZIP_DEFLATED)
            self.assertEqual(z.read("backup.json"), b"{}")
        out = self.path("out.zip")
        backup_core.write_zip(out, iter([(db, "x.db")]))
        self.assertTrue(zipfile.is_zipfile(out))

    def test_walk_prune_can_skip_a_folders_files(self):
        self.put("r/top.txt")
        self.put("r/a/inner.txt")
        got = [n for _p, n in backup_core.walk(self.path("r"), prune=lambda d, _n: d.endswith(os.sep + "a"))]
        self.assertEqual(got, ["top.txt"])

    # ---- restore: receiving the upload ----
    def test_receive_upload_and_request(self):
        p = asyncio.run(backup_core.receive(FakeUpload(b"x" * 3_000_000), self.path("data"), suffix=".zip", prefix=".r-"))
        self.assertTrue(os.path.basename(p).startswith(".r-") and p.endswith(".zip"))
        self.assertEqual(os.path.getsize(p), 3_000_000)

        app = FastAPI()

        @app.post("/up")
        async def up(request: Request):
            path = await backup_core.receive(request, self.dir, max_bytes=10,
                                             too_big=lambda: HTTPException(413, "That backup is too big."))
            with open(path, "rb") as f:
                data = f.read()
            os.remove(path)
            return {"got": data.decode()}

        c = TestClient(app)
        self.assertEqual(c.post("/up", content=b"0123456789").json(), {"got": "0123456789"})
        before = set(os.listdir(self.dir))
        r = c.post("/up", content=b"01234567890")
        self.assertEqual((r.status_code, r.json()["detail"]), (413, "That backup is too big."))
        self.assertEqual(set(os.listdir(self.dir)), before)          # the partial file is gone

    def test_receive_too_big_upload_leaves_nothing(self):
        d = self.path("data")
        with self.assertRaises(ValueError):
            asyncio.run(backup_core.receive(FakeUpload(b"x" * (2 * 1024 * 1024 + 1)), d, max_bytes=2 * 1024 * 1024,
                                            too_big=lambda: ValueError("too big")))
        self.assertEqual(os.listdir(d), [])

    def test_receive_sync(self):
        p = backup_core.receive_sync(io.BytesIO(b"abc"), self.path("d"))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"abc")
        self.assertTrue(p.endswith(".db"))

    def test_upload_file_is_read_in_chunks(self):
        up = UploadFile(io.BytesIO(b"hello"), filename="b.db")
        p = asyncio.run(backup_core.receive(up, self.dir))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"hello")

    # ---- restore: reading the zip ----
    def zip_bytes(self, names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in names:
                z.writestr(n, b"data-" + n.encode())
        return buf.getvalue()

    def test_open_zip(self):
        with backup_core.open_zip(self.zip_bytes(["a.db"]), lambda: ValueError("no")) as z:
            self.assertEqual(z.namelist(), ["a.db"])
        bad = self.put("bad.zip", b"not a zip")
        with self.assertRaisesRegex(ValueError, "not mine"):
            backup_core.open_zip(bad, lambda: ValueError("not mine"))
        with self.assertRaisesRegex(ValueError, "not mine"):
            backup_core.open_zip(b"junk", lambda: ValueError("not mine"))

    def test_unsafe_names(self):
        for n in ("/etc/passwd", "../x", "a/../../x", "a\\b", "files/..", ".."):
            self.assertTrue(backup_core.unsafe_name(n), n)
        for n in ("a.db", "files/a/b.txt", "files/..x", "x../y"):
            self.assertFalse(backup_core.unsafe_name(n), n)

    def test_check_members(self):
        z = zipfile.ZipFile(io.BytesIO(self.zip_bytes(["db.db", "files/", "files/a.txt"])))
        seen = []
        infos = backup_core.check_members(z, unsafe=lambda n: ValueError("unsafe " + n),
                                          check=lambda i: seen.append(i.filename), skip_dirs=True)
        self.assertEqual([i.filename for i in infos], ["db.db", "files/a.txt"])
        self.assertEqual(seen, ["db.db", "files/a.txt"])
        self.assertEqual(len(backup_core.check_members(z, unsafe=ValueError)), 3)

        def only_files(info):
            if not info.filename.startswith("files/"):
                raise ValueError("unexpected " + info.filename)

        z = zipfile.ZipFile(io.BytesIO(self.zip_bytes(["../evil"])))
        with self.assertRaisesRegex(ValueError, r"^unsafe \.\./evil$"):           # name first ...
            backup_core.check_members(z, unsafe=lambda n: ValueError("unsafe " + n), check=only_files)
        with self.assertRaisesRegex(ValueError, r"^unexpected \.\./evil$"):       # ... or the app's check first
            backup_core.check_members(z, unsafe=lambda n: ValueError("unsafe " + n), check=only_files, check_first=True)
        z = zipfile.ZipFile(io.BytesIO(self.zip_bytes(["files/a\\b"])))
        self.assertEqual(len(backup_core.check_members(z, unsafe=ValueError, is_unsafe=lambda n: n.startswith("/"))), 1)
        with self.assertRaises(ValueError):
            backup_core.check_members(z, unsafe=ValueError)

    def test_copy_out(self):
        z = zipfile.ZipFile(io.BytesIO(self.zip_bytes(["files/a/b.txt"])))
        dest = self.path("out", "a", "b.txt")
        backup_core.copy_out(z, "files/a/b.txt", dest)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"data-files/a/b.txt")
        self.assertEqual(os.listdir(self.path("out", "a")), ["b.txt"])
        backup_core.copy_out(z, "files/a/b.txt", dest, part=".tmp")        # replaces an existing file
        self.assertEqual(os.listdir(self.path("out", "a")), ["b.txt"])

    # ---- restore: the swap ----
    @unittest.skipIf(db_core is None, "this app has no db_core")
    def test_restore_file_swaps_and_migrates_under_the_lock(self):
        live = self.path("live.db")
        with closing(sqlite3.connect(live)) as c:
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("CREATE TABLE t (v TEXT)")
            c.execute("INSERT INTO t VALUES ('old')")
            c.commit()
        new = self.path("new.db")
        with closing(sqlite3.connect(new)) as c:
            c.execute("CREATE TABLE t (v TEXT)")
            c.execute("INSERT INTO t VALUES ('restored')")
            c.commit()
        lock = threading.Lock()
        calls = []

        def migrate():
            calls.append(lock.locked())
            with closing(sqlite3.connect(live)) as c:
                c.execute("ALTER TABLE t ADD COLUMN extra TEXT")
                c.commit()

        backup_core.restore_file(new, live, lambda: closing(sqlite3.connect(live)), migrate=migrate, lock=lock)
        self.assertEqual(calls, [True])
        self.assertFalse(os.path.exists(new))
        self.assertFalse(os.path.exists(live + "-wal"))
        with closing(sqlite3.connect(live)) as c:
            self.assertEqual(c.execute("SELECT v, extra FROM t").fetchall(), [("restored", None)])
        again = self.path("again.db")
        with closing(sqlite3.connect(again)) as c:
            c.execute("CREATE TABLE t (v TEXT)")
            c.commit()
        backup_core.restore_file(again, live, lambda: closing(sqlite3.connect(live)), migrate=lambda: calls.append("m"))
        self.assertEqual(calls, [True, "m"])


class SecretSettings(unittest.TestCase):
    """blank_settings / saved_settings / keep_settings: a backup never carries access keys, and a restore
    never wipes the ones this install has."""
    BLANKS = {"api_key": '""', "password": '""'}

    def db(self, cols="key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT", rows=()):
        conn = sqlite3.connect(":memory:")
        conn.execute(f"CREATE TABLE app_settings ({cols})")
        for row in rows:
            conn.execute(f"INSERT INTO app_settings VALUES ({','.join('?' * len(row))})", row)
        conn.commit()
        self.addCleanup(conn.close)
        return conn

    def values(self, conn):
        return dict(conn.execute("SELECT key, value FROM app_settings").fetchall())

    def test_blank(self):
        conn = self.db(rows=[("api_key", '"sk-1"', "t", "a"), ("model", '"m"', "t", "a")])
        backup_core.blank_settings(conn, self.BLANKS)
        self.assertEqual(self.values(conn), {"api_key": '""', "model": '"m"'})

    def test_blank_and_keep_ignore_a_file_without_the_table(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        backup_core.blank_settings(conn, self.BLANKS)
        self.assertEqual(backup_core.saved_settings(conn, self.BLANKS), {})
        self.assertEqual(backup_core.keep_settings(conn, {"api_key": '"x"'}, self.BLANKS), [])

    def test_saved_skips_blank_ones(self):
        conn = self.db(rows=[("api_key", '"sk-1"', "t", "a"), ("password", '""', "t", "a")])
        self.assertEqual(backup_core.saved_settings(conn, self.BLANKS), {"api_key": '"sk-1"'})

    def test_keep_fills_blank_and_missing_rows_only(self):
        conn = self.db(rows=[("api_key", '""', "t", "a"), ("other", '"o"', "t", "a")])
        kept = backup_core.keep_settings(conn, {"api_key": '"sk-1"', "password": '"pw"'}, self.BLANKS, now="NOW")
        self.assertEqual(kept, ["api_key", "password"])
        rows = {r[0]: r[1:] for r in conn.execute("SELECT * FROM app_settings")}
        self.assertEqual(rows["api_key"], ('"sk-1"', "NOW", "kept on restore"))
        self.assertEqual(rows["password"], ('"pw"', "NOW", "kept on restore"))
        self.assertEqual(rows["other"], ('"o"', "t", "a"))

    def test_keep_leaves_a_secret_the_backup_carries(self):
        conn = self.db(rows=[("api_key", '"from-backup"', "t", "a")])
        self.assertEqual(backup_core.keep_settings(conn, {"api_key": '"ours"'}, self.BLANKS), [])
        self.assertEqual(self.values(conn), {"api_key": '"from-backup"'})

    def test_raw_text_storage_and_a_table_without_dates(self):
        conn = self.db(cols="key TEXT PRIMARY KEY, value TEXT", rows=[("ai_api_key", "")])
        blanks = {"ai_api_key": ""}
        self.assertEqual(backup_core.keep_settings(conn, {"ai_api_key": "sk"}, blanks), ["ai_api_key"])
        self.assertEqual(self.values(conn), {"ai_api_key": "sk"})
        backup_core.blank_settings(conn, blanks)
        self.assertEqual(self.values(conn), {"ai_api_key": ""})


if __name__ == "__main__":
    unittest.main()
