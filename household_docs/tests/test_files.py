"""Path rules and file writes (SPEC §7.1, §9.3, §14): names Windows can't hold, `..`, absolute paths, links and
folders swapped for links; write-to-temp-then-rename with fsync and file mode 0664; the per-file lock; downloads
never inline."""
import _env  # noqa: F401

import os
import stat
import threading
import unittest
from unittest import mock

from app import db
from app.store import fileio, paths, roots
from base import DOCS, KABIR, MEERA, SHARE, ApiBase, person_dir, read, write


class Names(unittest.TestCase):
    def test_names(self):
        for bad in ("", " ", ".hidden", "a/b", "a\\b", "a:b", "what?", "star*", 'q"', "<x>", "pipe|", "dot.", "space ",
                    "CON", "nul.txt", "COM1.md", "x" * 201, "tab\there", "..", "."):
            with self.subTest(bad):
                self.assertIsNotNone(paths.name_problem(bad))
        for good in ("Budget 2026.xlsx", "Trip – Italy", "Ünïcødé", "a.b.c", "CONSOLE.txt", "(1)"):
            with self.subTest(good):
                self.assertIsNone(paths.name_problem(good))

    def test_clean_name(self):
        self.assertEqual(paths.clean_name("Priya"), "Priya")
        self.assertEqual(paths.clean_name("A/B:C"), "A_B_C")
        self.assertEqual(paths.clean_name("..dots.. "), "dots")
        self.assertEqual(paths.clean_name("con"), "con_")
        self.assertEqual(paths.clean_name("   "), "Untitled")

    def test_resolve(self):
        base = os.path.realpath(SHARE)
        for bad in ("..", "../x", "a/../../x", ".hidden", "a/.b", "/etc/passwd"):
            with self.subTest(bad):
                if bad.startswith("/"):
                    self.assertEqual(paths.resolve(base, bad), os.path.join(base, "etc", "passwd"))   # always inside
                else:
                    with self.assertRaises(paths.PathError):
                        paths.resolve(base, bad)

    def test_unique_name(self):
        d = os.path.join(SHARE, "u")
        os.makedirs(d, exist_ok=True)
        for n in ("A.txt", "a (2).txt"):
            write(os.path.join(d, n), "x")
        self.assertEqual(paths.unique_name(d, "a.txt"), "a (3).txt")
        self.assertEqual(paths.unique_name(d, "A.txt", suffix_word="restored"), "A (restored).txt")
        self.assertEqual(paths.unique_name(d, "B"), "B")


class PathAttacks(ApiBase):
    def test_bad_names_in_requests(self):
        for bad in ("../escape", "a/b", ".hidden", "con", "x:y"):
            with self.subTest(bad):
                self.assertEqual(self.post("/api/docs", {"kind": "note", "name": bad}).status_code, 422)
                self.assertEqual(self.post("/api/docs", {"kind": "folder", "name": bad}).status_code, 422)
        self.assertEqual(self.post("/api/docs", {"kind": "file", "name": "x"}).status_code, 422)
        self.assertEqual(self.post("/api/docs", {"kind": "nope", "name": "x"}).status_code, 422)
        self.assertEqual(os.listdir(person_dir("Kabir Rao")), [])

    def test_folder_swapped_for_a_link_is_refused(self):
        f = self.create("folder", "Trip")
        n = self.note("Plan", f["id"], text="mine")
        outside = os.path.join(SHARE, "outside")
        write(os.path.join(outside, "Plan.txt"), "someone else's file")
        trip = os.path.join(person_dir("Kabir Rao"), "Trip")
        os.rename(trip, trip + "-moved")
        os.symlink(outside, trip)
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 422)
        self.assertEqual(self.get(f"/api/nodes/{n['id']}/file").status_code, 422)
        r = self.patch(f"/api/docs/{n['id']}", {"etag": "x", "text": "overwrite"})
        self.assertIn(r.status_code, (404, 422))
        self.assertEqual(read(os.path.join(outside, "Plan.txt")), "someone else's file")

    def test_person_root_swapped_for_a_link_is_refused(self):
        n = self.note("Plan", text="x")
        home = person_dir("Kabir Rao")
        os.rename(home, home + "-real")
        os.symlink("/tmp", home)
        self.assertEqual(self.get(f"/api/docs/{n['id']}").status_code, 422)

    def test_ids_are_never_paths(self):
        for bad in ("..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "x" * 100):
            self.assertEqual(self.get(f"/api/docs/{bad}").status_code, 404)


class Writes(ApiBase):
    def test_write_then_rename_and_mode(self):
        n = self.note("Plan")
        seen = []
        real_replace = os.replace

        def spy(src, dst, **kw):
            # the rename is relative to the target folder's descriptor (security review: a folder swapped for a
            # link meanwhile can't send the file elsewhere)
            folder = os.readlink(f"/proc/self/fd/{kw['dst_dir_fd']}")
            seen.append((src, os.path.join(folder, dst), os.path.exists(src), read(src)))
            return real_replace(src, dst, **kw)

        with mock.patch("app.store.fileio.os.replace", side_effect=spy):
            self.save(n["id"], "complete text")
        [(src, dst, existed, data)] = seen
        self.assertTrue(existed)
        self.assertEqual(os.path.dirname(src), os.path.join(os.path.realpath(DOCS), ".tmp"))
        self.assertEqual(dst, self.real(n["id"]))
        self.assertEqual(data, "complete text")
        self.assertEqual(stat.S_IMODE(os.stat(dst).st_mode), 0o664)
        self.assertEqual(os.listdir(os.path.join(DOCS, ".tmp")), [])

    def test_a_failed_write_leaves_the_file_and_no_temp(self):
        n = self.note("Plan", text="safe")
        with mock.patch("app.store.fileio.os.fsync", side_effect=OSError(5, "I/O error")):
            r = self.patch(f"/api/docs/{n['id']}", {"etag": self.open(n["id"])["etag"], "text": "lost"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(read(self.real(n["id"])), "safe")
        self.assertEqual(os.listdir(os.path.join(DOCS, ".tmp")), [])

    def test_lock_serialises_two_checklist_writers(self):
        c = self.create("checklist", "List")
        self.ok(self.share(c["id"], MEERA, "editor"))
        errors = []

        def add(h, prefix):
            for i in range(8):
                r = self.ops(c["id"], [{"op": "add", "text": f"{prefix}{i}"}], h)
                if r.status_code != 200:
                    errors.append(r.text)

        ts = [threading.Thread(target=add, args=(KABIR, "k")), threading.Thread(target=add, args=(MEERA, "m"))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        items = self.open(c["id"])["items"]
        self.assertEqual(len(items), 16)                                  # nothing lost

    def test_exclusive_write(self):
        n = self.note("Plan")
        with db.get_conn() as conn:
            root = roots.root_row(conn, self.row(n["id"])["root_id"])
        with self.assertRaises(FileExistsError):
            fileio.write_atomic(root, self.real(n["id"]), b"x", exclusive=True)

    def test_download_headers(self):
        n = self.note("page", text="<html><script>alert(1)</script></html>")
        r = self.get(f"/api/nodes/{n['id']}/file")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-disposition"].startswith("attachment;"))
        self.assertEqual(r.headers["content-type"], "application/octet-stream")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertTrue(r.headers["content-security-policy"].startswith("sandbox"))
        f = self.create("folder", "F")
        self.assertEqual(self.get(f"/api/nodes/{f['id']}/file").status_code, 422)


if __name__ == "__main__":
    unittest.main()
