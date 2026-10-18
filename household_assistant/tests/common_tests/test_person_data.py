# Shared file: edit common/tests/test_person_data.py and run tools/sync_common.py; don't edit this copy. sha256=984480135f8ddae52f4b3591221206ac992b7b6a395faa6d61a4bf1731c9257a
"""The shared person-data file and helpers (common/python/person_data.py, PERSON_DATA_SPEC.md §2, §9), tested in
every app that uses them: a round trip, and every way a file is refused (another app, newer, changed, unsafe, big)."""
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile

from app.common import person_data as pd

PERSON = {"id": "u1", "username": "alice", "name": "Alice"}


def package(**kw):
    args = dict(app="test_app", app_version="1.0.0", schema=3, person=PERSON, exported_by="admin",
                tables={"notes": [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]}, records={"elsewhere": {"n": 1}})
    args.update(kw)
    return pd.bytes_io_package(**args)


def rezip(data: bytes, change) -> bytes:
    """A copy of the zip with change(name, bytes) → bytes | None (None drops the member), and extra members."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            b = change(info.filename, zin.read(info.filename))
            if b is not None:
                zout.writestr(info.filename, b)
    return out.getvalue()


class PersonData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def read(self, data: bytes, **kw):
        kw.setdefault("app", "test_app")
        kw.setdefault("schema", 3)
        return pd.read_package(io.BytesIO(data), **kw)

    def test_round_trip_with_files(self):
        src = os.path.join(self.tmp, "a.pdf")
        with open(src, "wb") as f:
            f.write(b"%PDF-1.4 hello")
        data = package(files=[("docs/a.pdf", src)], options={"pdfs": True})
        with self.read(data, tables=["notes"]) as p:
            self.assertEqual(p.tables, {"notes": [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]})
            self.assertEqual(p.records, {"elsewhere": {"n": 1}})
            self.assertEqual(p.file_names, ["files/docs/a.pdf"])
            self.assertEqual(p.size("files/docs/a.pdf"), 14)
            self.assertEqual(p.manifest["counts"], {"notes": 2, "files": 1})
            self.assertEqual((p.person["name"], p.schema, p.manifest["options"]), ("Alice", 3, {"pdfs": True}))
            out = os.path.join(self.tmp, "out", "a.pdf")
            p.extract("files/docs/a.pdf", out)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), b"%PDF-1.4 hello")
            with self.assertRaises(KeyError):
                p.extract("files/../x", out)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("not encrypted", z.read("README.txt").decode())

    def test_refused(self):
        good = package()
        cases = {
            "not a zip": b"hello",
            "another app": package(app="other_app"),
            "newer schema": package(schema=4),
            "a changed row": rezip(good, lambda n, b: b.replace(b'"a"', b'"z"') if n == "data/notes.json" else b),
            "an extra member": rezip(good, lambda n, b: b) + b"",
            "no manifest": rezip(good, lambda n, b: None if n == "manifest.json" else b),
            "a dropped member": rezip(good, lambda n, b: None if n == "data/notes.json" else b),
            "rows not a list": package(tables={"notes": []}),
        }
        # an unlisted member added afterwards
        buf = io.BytesIO(good)
        with zipfile.ZipFile(buf, "a") as z:
            z.writestr("data/extra.json", "[]")
        cases["an unlisted member"] = buf.getvalue()
        # a table that isn't a list of rows (checksum fixed up, so only the shape is wrong)
        def not_rows(n, b):
            if n == "data/notes.json":
                return b'{"x": 1}'
            if n == "manifest.json":
                m = json.loads(b)
                import hashlib
                m["sha256"]["data/notes.json"] = hashlib.sha256(b'{"x": 1}').hexdigest()
                return json.dumps(m).encode()
            return b
        cases["rows not a list"] = rezip(good, not_rows)
        del cases["an extra member"]
        for what, data in cases.items():
            with self.subTest(what=what):
                with self.assertRaises(pd.PackageError):
                    self.read(data, tables=["notes"]).close()
        with self.assertRaises(pd.PackageError):                          # an older one is fine, a newer isn't
            self.read(good, schema=2).close()
        self.read(good, schema=9).close()

    def test_unsafe_names_and_limits(self):
        for bad in ("../../etc/passwd", "a/../../b", "a\\b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                package(files=[(bad, __file__)])
        link = os.path.join(self.tmp, "link.pdf")                         # a link is never followed into the file
        os.symlink(__file__, link)
        with self.assertRaises(OSError):
            package(files=[("x.pdf", link)])
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("manifest.json", "{}")
            z.writestr("../x", "x")
        with self.assertRaises(pd.PackageError):
            self.read(evil.getvalue()).close()
        with self.assertRaises(pd.PackageError):
            self.read(package(), limits=pd.Limits(max_unpacked=10)).close()
        with self.assertRaises(pd.PackageError):
            self.read(package(), limits=pd.Limits(max_members=2)).close()
        with self.assertRaises(pd.PackageError):
            self.read(package(), tables=["notes"], limits=pd.Limits(max_rows=1)).close()

    def test_helpers(self):
        self.assertTrue(pd.names_match("  alice ", "Alice"))
        self.assertTrue(pd.names_match("Mary  Ann", "mary ann"))
        self.assertFalse(pd.names_match("", ""))
        self.assertFalse(pd.names_match("Alic", "Alice"))
        self.assertEqual(pd.pick({"a": 1, "b": 2, "evil": 3}, ["a", "b", "c"]), {"a": 1, "b": 2})
        ids = pd.IdMap()
        ids.put("foods", 7, 101)
        self.assertEqual((ids.get("foods", "7"), ids.get("foods", 8), ids.get("foods", None), ids.count("foods")),
                         (101, None, None, 1))
        from datetime import datetime
        self.assertEqual(pd.file_name("Calorie Tracker", "Ánna B.", datetime(2026, 10, 18, 12, 35)),
                         "calorie-tracker-anna-b-20261018-1235.zip")
        self.assertEqual(pd.file_name("Docs", "😀"), pd.file_name("Docs", "😀"))
        self.assertTrue(pd.file_name("Docs", "😀").startswith("docs-person-"))
        self.assertEqual(pd.bytes_text(1536), "1.5 KB")
        self.assertEqual(pd.count_list([("a", "Things", 3)]), [{"key": "a", "label": "Things", "count": 3}])

    def test_one_at_a_time(self):
        lock = threading.Lock()
        with pd.one_at_a_time(lock):
            with self.assertRaises(pd.Busy):
                with pd.one_at_a_time(lock):
                    pass
        with pd.one_at_a_time(lock):        # released again
            pass

    def test_uploads_and_jobs(self):
        up = pd.Uploads(self.tmp)
        path = pd.temp_path(self.tmp, prefix=up.prefix)
        token = up.add(path, by="admin")
        self.assertIsNotNone(up.get(token, "admin"))
        self.assertIsNone(up.get(token, "someone-else"))
        up.drop(token)
        self.assertFalse(os.path.exists(path))
        jobs = pd.Jobs()

        def work(progress):
            progress(1, 2)
            p = pd.temp_path(self.tmp)
            progress(2, 2)
            return p
        jid = jobs.start(work, by="admin", filename="x.zip")
        for _ in range(100):
            if jobs.status(jid, "admin")["state"] != "running":
                break
            time.sleep(0.02)
        st = jobs.status(jid, "admin")
        self.assertEqual((st["state"], st["done"], st["total"]), ("done", 2, 2))
        self.assertIsNone(jobs.status(jid, "other"))
        failed = jobs.start(lambda progress: (_ for _ in ()).throw(RuntimeError("disk full")), by="admin",
                            filename="y.zip")
        for _ in range(100):
            if jobs.status(failed, "admin")["state"] != "running":
                break
            time.sleep(0.02)
        self.assertEqual(jobs.status(failed, "admin")["error"], "disk full")


if __name__ == "__main__":
    unittest.main()
