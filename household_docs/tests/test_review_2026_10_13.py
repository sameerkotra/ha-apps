"""Regression tests for the 13 October 2026 review of Household Docs (each finding's proof, turned around):
wildcards that can't hang the app (D1), PDFs of hostile notes (D2), the index scan without one long write
(DP1), Kids' space and sharing (D3–D5), and the smaller items that followed. The browser code's fixes are in
tests/js/review_2026_10_13.test.js."""
import _env  # noqa: F401

import re
import time
import unittest
from unittest import mock

from base import ASHA, KABIR, MEERA, ApiBase, hdr  # noqa: F401
from test_tidy import KID, TidyBase

from app import db, docpdf, filing, pdfwriter
from app.formats import doc_layout
from app.search import pattern


# =====================================================================
# D1: wildcards run in linear time
# =====================================================================
class Wildcards(unittest.TestCase):
    CASES = ["*.pdf", "Power-bill*.pdf", "bill-2026-??.pdf", "[abc]*", "[!a]*", "[^a]*", "[0-9]*x", "a[", "*", "?",
             "*a*b*", "[]]x", "x[a-c]y", "INVOICE*"]
    NAMES = ["power-BILL-oct.pdf", "bill-2026-01.pdf", "a.pdf", "b", "abc", "x", "]x", "xby", "xdy", "a[",
             "invoice 2026.PDF", "", "aaab", "Ünïcode.pdf"]

    def test_the_same_answers_as_the_regular_expression(self):
        for p in self.CASES:
            for n in self.NAMES:
                try:
                    old = bool(re.match(pattern.wildcard_to_regex(p), n, re.I))
                except re.error:                # "[]]x" was never a valid expression there
                    continue
                self.assertEqual(pattern.wildcard_matches(p, n), old, (p, n))

    def test_many_stars_answer_at_once(self):
        t = time.monotonic()
        self.assertFalse(pattern.wildcard_matches("*a" * 100 + "*b", "a" * 255))
        self.assertFalse(pattern.wildcard_matches("*a*a*a*a*a*a*b", "a" * 10_000))
        self.assertFalse(pattern.wildcard_matches("*", "a" * 20_000))        # longer than any name: no match
        self.assertLess(time.monotonic() - t, 2)


class FilingWildcards(TidyBase):
    def test_a_rule_full_of_stars_files_quickly(self):
        inbox = self.create("folder", "Inbox")
        r = self.post(f"/api/rules/{inbox['id']}/filing", {"pattern": "*a*a*a*a*a*a*a*a*a*a*a*b", "rename": "x"})
        self.assertEqual(r.status_code, 201, r.text)
        t = time.monotonic()
        up = self.upload(inbox["id"], "a" * 120 + ".pdf")
        out = self.ok(self.post(f"/api/rules/{inbox['id']}/filing/dry-run", {"pattern": "*a*a*a*a*a*a*a*a*a*a*b", "rename": "y"}))
        self.assertLess(time.monotonic() - t, 5)
        self.assertEqual(up["name"], "a" * 120 + ".pdf")          # no match, nothing renamed
        self.assertEqual(out["count"], 0)

    def test_the_queue_row_is_gone_before_the_rules_run(self):
        """An arrival that broke (or hung) the rules must not be tried again at every start."""
        inbox = self.create("folder", "Inbox")
        seen = []

        def boom(conn, nid):
            with db.get_conn() as c2:
                seen.append(c2.execute("SELECT COUNT(*) FROM filing_queue WHERE node_id = ?", (nid,)).fetchone()[0])
            raise RuntimeError("stop")
        n = self.create("note", "x", inbox["id"])
        with db.get_conn() as conn:
            filing.queue(conn, n["id"], "found")
        with mock.patch.object(filing, "apply_rules", boom), self.assertLogs("filing", "ERROR"):
            filing.run_queue()
        self.assertEqual(seen, [0])                  # already committed away when the rules ran
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM filing_queue").fetchone()[0], 0)


class Watchdog(unittest.TestCase):
    def test_config_has_the_supervisor_watchdog(self):
        import os
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")) as f:
            text = f.read()
        self.assertIn("watchdog: http://[HOST]:[PORT:8105]/api/health", text)


# =====================================================================
# D2: PDFs of hostile notes
# =====================================================================
OLD_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
OLD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


class PdfLayout(unittest.TestCase):
    def test_cutting_a_long_word_is_quick_and_the_same(self):
        for font in ("R", "B", "M"):
            for word in (b"W" * 50, b"iiiiiiiiiijjjjjjjj" * 9, bytes(range(32, 250))):
                for avail in (30.0, 100.5, 400.0):
                    n = len(word)
                    while n > 1 and pdfwriter.width(word[:n], font, 10.5) > avail:
                        n -= 1
                    self.assertEqual(pdfwriter.fit(word, font, 10.5, avail), n)
        doc = pdfwriter.PdfDoc("x")
        t = time.monotonic()
        doc.paragraph([("x" * 20_000, "R")])
        doc.paragraph([("W" * 20_000, "M")])
        self.assertLess(time.monotonic() - t, 3)

    def test_headings_and_table_rules_as_before(self):
        for line in ("# Title", "## Title ##", "###   Spaced   #  ", "# C#", "# #", "#  ", "  # x", "####### no",
                     "#no", "# a # b ##", "# \t tab\t#\t"):
            m_old, m_new = OLD_HEAD.match(line), doc_layout._HEAD.match(line)
            self.assertEqual(bool(m_old), bool(m_new), line)
            if m_old:
                self.assertEqual(doc_layout.heading_text(m_new.group(2)), m_old.group(2), line)
        for line in ("---", "|---|", "| --- | :-: |", ":--|--:", "||---", "|", "", "a|b", "--- x", " | - | ", "|-|-|-|",
                     "-|", "|:", "::"):
            self.assertEqual(doc_layout.is_table_sep(line), bool(OLD_TABLE_SEP.match(line)), line)

    def test_hostile_markdown_is_laid_out_quickly(self):
        lines = ["# " + " #" * 20_000 + "x", "|" + " - |" * 20_000 + "x", "*a " * 30_000, "[a" * 30_000,
                 "**a " * 30_000, "_a " * 30_000, "![a" * 30_000, "[[a " * 30_000]
        t = time.monotonic()
        for ln in lines:
            doc_layout.md_blocks(ln + "\n" + ln)
            doc_layout.inline_runs(ln)
        self.assertLess(time.monotonic() - t, 10)

    def test_formatting_still_works(self):
        self.assertEqual(doc_layout.inline_runs("a **b** *c* `d` [e](https://x.y) [[F]]"),
                         [("a ", "R"), ("b", "B"), (" ", "R"), ("c", "I"), (" ", "R"), ("d", "M"), (" ", "R"),
                          ("e", "R"), (" (https://x.y)", "R"), (" ", "R"), ("F", "B")])
        self.assertEqual(doc_layout.md_blocks("## Head ##\n\n| a | b |\n|---|:-:|\n| 1 | 2 |"),
                         [{"t": "heading", "level": 2, "text": "Head"},
                          {"t": "table", "rows": [["a", "b"], ["1", "2"]]}])


class PdfWorker(ApiBase):
    def test_the_pdf_is_made_in_the_worker(self):
        data, bad = docpdf.render("Title", "sub", "markdown", "# Hi\n\n- [ ] a\n\nüñ ☃")
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(bad, 1)                       # the snowman
        data, _bad = docpdf.render("List", "sub", "checklist", "- [ ] milk\n- [x] eggs")
        self.assertTrue(data.startswith(b"%PDF"))

    def test_a_slow_pdf_is_stopped(self):
        n = self.note("Slow", text="hello")
        with mock.patch.object(docpdf, "TIMEOUT", 0.001):
            r = self.get(f"/api/docs/{n['id']}/pdf")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertIn("Print", r.json()["detail"])
        self.assertTrue(self.get(f"/api/docs/{n['id']}/pdf").content.startswith(b"%PDF"))

    def test_a_broken_worker_says_so(self):
        n = self.note("Broken", text="hello")
        with mock.patch.object(docpdf, "WORKER", "/nonexistent/worker.py"), self.assertLogs("docpdf", "WARNING"):
            r = self.get(f"/api/docs/{n['id']}/pdf")
        self.assertEqual(r.status_code, 422, r.text)


# =====================================================================
# DP1: the index scan reads files without holding the database, and commits in batches
# =====================================================================
class ScanWithoutLongWrites(ApiBase):
    def files(self, n, folder="Outside"):
        import os
        from base import person_dir, write
        for i in range(n):
            write(os.path.join(person_dir("Kabir Rao"), folder, f"f{i:03}.txt"), f"text number {i}")

    def free_to_write(self) -> bool:
        import sqlite3
        from app import config
        c = sqlite3.connect(config.DB_PATH, timeout=0)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("ROLLBACK")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            c.close()

    def test_files_are_read_while_others_can_write(self):
        from app.store import index
        self.files(7)
        seen = []
        real = index.read_file_info

        def spy(*a, **kw):
            seen.append(self.free_to_write())
            return real(*a, **kw)
        with mock.patch.object(index, "read_file_info", spy), mock.patch.object(index, "BATCH", 3):
            self.scan()
        self.assertGreaterEqual(len(seen), 7)
        self.assertTrue(all(seen), seen)

    def test_batches_give_the_same_index(self):
        from app.store import index

        def snapshot():
            with db.get_conn() as conn:
                return sorted((r["rel"], r["kind"], r["sha256"], r["size"], r["gone_at"])
                              for r in conn.execute("SELECT * FROM nodes"))
        self.files(9)
        self.scan()
        one = snapshot()
        self.setUp()
        self.files(9)
        with mock.patch.object(index, "BATCH", 2):
            self.scan()
        self.assertEqual(snapshot(), one)
        self.assertEqual(len([x for x in one if x[0].endswith(".txt")]), 9)

    def test_a_locked_database_is_a_503(self):
        import sqlite3
        from app.routers import me as me_router   # noqa: F401
        from app import auth
        with mock.patch.object(auth, "identify", side_effect=sqlite3.OperationalError("database is locked")), \
                self.assertLogs("main", "WARNING"):
            r = self.get("/api/me")
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.headers.get("retry-after"), "2")


# =====================================================================
# D3: a child's Keep import isn't back-dated out of the parents' view
# =====================================================================
class KeepForAChild(TidyBase):
    def test_kept_dates_dont_hide_a_childs_notes_and_long_titles_import(self):
        from test_bring import keep_note, zip_bytes
        from app import imports
        z = zip_bytes([("Takeout/Keep/A.json", keep_note(title="Old secret", textContent="hello",
                                                         createdTimestampUsec=1_500_000_000_000_000)),
                       ("Takeout/Keep/B.json", keep_note(title="T" * 400, textContent="long title"))])
        r = self.c.post("/api/import/keep/upload?name=t.zip", content=z, headers=KID)
        self.assertEqual(r.status_code, 201, r.text)
        j = self.ok(self.post(f"/api/import/keep/{r.json()['token']}", {}, KID), 202)
        done = imports.wait(j["id"])
        self.assertEqual(done["state"], "done", done.get("error"))
        self.assertEqual(done["result"]["added"], 2)
        with db.get_conn() as conn:
            rows = conn.execute("SELECT n.name, n.ctime, u.child_since FROM nodes n JOIN roots r ON r.id = n.root_id "
                                "JOIN users u ON u.id = r.user_id WHERE u.id = 'u_kid' AND n.kind != 'folder'").fetchall()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertGreaterEqual(r["ctime"], r["child_since"])         # parents still see it (KID_VISIBLE_SQL)
        self.assertTrue(any(len(r["name"]) <= 130 and r["name"].startswith("TTT") for r in rows))

    def test_an_adults_import_keeps_keeps_date(self):
        from test_bring import keep_note, zip_bytes
        from app import imports
        z = zip_bytes([("Takeout/Keep/A.json", keep_note(title="Old", textContent="x", createdTimestampUsec=1_500_000_000_000_000))])
        r = self.c.post("/api/import/keep/upload?name=t.zip", content=z, headers=KABIR)
        j = self.ok(self.post(f"/api/import/keep/{r.json()['token']}", {}), 202)
        self.assertEqual(imports.wait(j["id"])["state"], "done")
        with db.get_conn() as conn:
            self.assertTrue(conn.execute("SELECT ctime FROM nodes WHERE name = 'Old.txt'").fetchone()["ctime"].startswith("2017"))


class ZipDepth(TidyBase):
    def test_a_zip_too_deep_for_the_folder_is_refused_before_anything_is_made(self):
        from test_bring import zip_bytes
        deep = "/".join(f"d{i}" for i in range(6)) + "/f.txt"
        z = zip_bytes([(deep, b"x")])
        parent = None
        for i in range(4):
            parent = self.create("folder", f"p{i}", parent and parent["id"])
        r = self.c.post("/api/import/zip/upload?name=z.zip", content=z, headers=KABIR)
        tok = r.json()["token"]
        self.assertEqual(r.json()["preview"]["depth"], 6)
        r2 = self.post(f"/api/import/zip/{tok}", {"parentId": parent["id"]})
        self.assertEqual(r2.status_code, 422, r2.text)
        self.assertIn("deep", r2.json()["detail"])
        self.assertEqual([i["name"] for i in self.children(parent["id"]).values()], [])
        # the same upload still goes somewhere nearer the top
        self.assertEqual(self.post(f"/api/import/zip/{tok}", {}).status_code, 202)


# =====================================================================
# D4, D5: Kids' space and Home Assistant
# =====================================================================
class KidsAndSheets(TidyBase):
    def setUp(self):
        super().setUp()
        self.sh = self.sheet("Pocket money", {"A1": {"v": "secret-number"}, "B1": {"v": 42}})
        self.ok(self.share(self.sh["id"], KID, "editor"))

    def test_search_shows_a_child_no_cells(self):
        out = self.ok(self.get("/api/search?q=secret-number", KID))
        hit = [r for r in out["results"] if r["id"] == self.sh["id"]]
        self.assertTrue(hit)
        self.assertIsNone(hit[0]["snippet"])
        self.assertNotIn("cell", hit[0])
        adult = self.ok(self.get("/api/search?q=secret-number"))
        self.assertIn("secret-number", next(r for r in adult["results"] if r["id"] == self.sh["id"])["snippet"])

    def test_a_child_cant_pin_a_cell_or_publish_a_sheet(self):
        r = self.post(f"/api/nodes/{self.sh['id']}/pin", {"value": True, "cell": {"tab": "Sheet1", "ref": "B1"}}, KID)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.put(f"/api/nodes/{self.sh['id']}/ha-sensor", {"cell": "B1"}, KID).status_code, 403)
        # a cell pinned before they were a child isn't shown
        from app import pins
        with db.get_conn() as conn:
            prev = pins._cell_preview(conn, {"id": "u_kid", "is_child": True}, {"id": self.sh["id"], "size": 1},
                                      {"tab": "Sheet1", "ref": "B1"})
        self.assertNotIn("v", prev)

    def test_an_editor_cant_put_someone_elses_things_in_home_assistant(self):
        c = self.create("checklist", "Kabir's list")
        self.ok(self.share(c["id"], MEERA, "editor"))
        r = self.put(f"/api/nodes/{c['id']}/ha-sensor", {}, MEERA)
        self.assertEqual(r.status_code, 403, r.text)
        info = self.ok(self.get(f"/api/nodes/{c['id']}/ha-sensor", MEERA))
        self.assertFalse(info["canChange"])
        self.ok(self.put(f"/api/nodes/{c['id']}/ha-sensor", {}))                # the owner may
        info = self.ok(self.get(f"/api/nodes/{c['id']}/ha-sensor", MEERA))
        self.assertTrue(info["canTurnOff"])
        self.ok(self.delete(f"/api/nodes/{c['id']}/ha-sensor", MEERA))           # and an editor may turn it off


# =====================================================================
# D6, D9: copies and uploads
# =====================================================================
class CopiesAndUploads(ApiBase):
    def test_a_copy_is_streamed_and_the_same(self):
        from app.store import fileio
        data = bytes(range(256)) * 20000
        up = self.c.post("/api/nodes/mine/upload?name=big.bin", content=data, headers=KABIR)
        self.assertEqual(up.status_code, 201, up.text)
        reads = []
        real = fileio.open_read

        def spy(path, base=None):
            f = real(path, base)
            orig = f.read
            f.read = lambda n=-1: (reads.append(n), orig(n))[1]
            return f
        with mock.patch.object(fileio, "open_read", spy):
            cp = self.ok(self.post(f"/api/nodes/{up.json()['id']}/copy", {}), 201)
        self.assertTrue(reads and all(n is not None and 0 < n <= 1024 * 1024 for n in reads), reads)
        from app.store import fileio as fio
        self.assertEqual(self.row(cp["id"])["sha256"], fio.sha256_bytes(data))
        with open(self.real(cp["id"]), "rb") as f:
            self.assertEqual(f.read(), data)

    def test_copies_have_a_size_limit(self):
        from app import docops
        n = self.note("x", text="hello")
        with mock.patch.object(docops, "MAX_COPY_BYTES", 2):
            self.assertEqual(self.post(f"/api/nodes/{n['id']}/copy", {}).status_code, 413)

    def test_an_upload_never_lands_through_a_swapped_folder(self):
        import os
        from app.store import fileio, paths
        d = self.create("folder", "Drop")
        folder_real = self.real(d["id"])
        elsewhere = os.path.join(os.path.dirname(folder_real), "elsewhere")
        os.makedirs(elsewhere)
        real_place = fileio.place

        def swap(tmp, dest, **kw):
            os.rename(folder_real, folder_real + ".old")
            os.symlink(elsewhere, folder_real)              # the folder is now a link, after the path checks
            return real_place(tmp, dest, **kw)
        with mock.patch.object(fileio, "place", swap):
            r = self.c.post(f"/api/nodes/{d['id']}/upload?name=a.txt", content=b"x", headers=KABIR)
        self.assertGreaterEqual(r.status_code, 400)
        self.assertEqual(os.listdir(elsewhere), [])
        self.assertTrue(issubclass(paths.PathError, Exception))


# =====================================================================
# D7: no document text in a database-only backup; restored by reading the files again
# =====================================================================
class BackupText(ApiBase):
    def test_backup_has_no_text_and_restore_reads_the_files_again(self):
        import io
        import sqlite3
        import tempfile
        import zipfile
        n = self.note("Diary", text="unicorn-password-hint")
        with db.get_conn() as conn:
            conn.execute("INSERT INTO pdf_text (node_id, sha256, status, text, updated_at) VALUES (?, 'x', 'ok', "
                         "'pdf-words-here', '2026-01-01')", (n["id"],))
            conn.execute("INSERT INTO ocr_text (node_id, sha256, text, updated_at) VALUES (?, 'x', 'ocr-words-here', "
                         "'2026-01-01')", (n["id"],))
        r = self.get("/api/admin/backup", ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = z.read("docs.db")
        for word in (b"unicorn-password-hint", b"pdf-words-here", b"ocr-words-here"):
            self.assertNotIn(word, raw)
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            f.write(raw)
            f.flush()
            c = sqlite3.connect(f.name)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM fts").fetchone()[0] > 0, True)
            c.close()
        # restore: the search finds the note's words again (the file is read again)
        self.assertEqual(self.c.post("/api/admin/restore", content=r.content, headers=ASHA).status_code, 200)
        hits = self.ok(self.get("/api/search?q=unicorn"))["results"]
        self.assertEqual([h["id"] for h in hits], [n["id"]])
        with db.get_conn() as conn:
            self.assertIsNone(db.state_get(conn, "index_emptied"))


# =====================================================================
# D8, D10, D11, D12
# =====================================================================
class SmallerItems(ApiBase):
    def test_members_see_only_the_ais_host(self):
        from app import ai
        at = chr(64)                                 # (no address-like text in the repository)
        self.assertEqual(ai.host_only(f"https://user:pa55{at}api.example.com:8443/v1?key=abc"), "https://api.example.com:8443")
        self.assertEqual(ai.host_only("http://[::1]:11434/"), "http://[::1]:11434")
        self.assertIsNone(ai.host_only(""))

    def test_one_time_limit_for_all_the_patterns_of_a_search(self):
        from app.search import regex_worker
        seen = []
        real = regex_worker.run

        def spy(*a, **kw):
            seen.append(kw.get("seconds"))
            return real(*a, **kw)
        self.note("invoice-2026", text="x")
        with mock.patch.object(regex_worker, "run", spy):
            self.get("/api/search?q=name:/inv/ name:/2026/ name:/o/")
        self.assertGreaterEqual(len(seen), 2)
        self.assertTrue(all(s is not None and s <= regex_worker.REGEX_SECONDS for s in seen))
        self.assertLess(seen[-1], seen[0] + 0.001)

    def test_the_regex_worker_limits_itself(self):
        from app.search import regex_worker
        res = regex_worker.run("(a?){25}a{25}", "name", [("1", "a" * 25)], seconds=5)
        self.assertIsNone(res.error)
        self.assertEqual(regex_worker.WORKER_MEM, 256 * 1024 * 1024)

    def test_file_system_errors_dont_log_paths(self):
        from app import documents
        n = self.note("Private name", text="x")
        err = OSError(13, "Permission denied", "/share/household_docs/people/Kabir Rao/Private name.txt")
        with mock.patch.object(documents, "open_doc", side_effect=err), self.assertLogs("main", "WARNING") as logs:
            r = self.get(f"/api/docs/{n['id']}")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("Private name", "\n".join(logs.output))
        self.assertIn("Permission denied", "\n".join(logs.output))

    def test_a_nearly_full_disk_refuses_saves_not_deletes(self):
        from app.store import marker
        n = self.note("Keep", text="one")
        self.settings({"min_free_mb": 200})
        with mock.patch.object(marker, "free_space", return_value=150 * 1024 * 1024):
            r = self.patch(f"/api/docs/{n['id']}", {"etag": self.open(n["id"])["etag"], "text": "two"})
            self.assertEqual(r.status_code, 507, r.text)
            up = self.c.post("/api/nodes/mine/upload?name=a.txt", content=b"x", headers=KABIR)
            self.assertEqual(up.status_code, 507, up.text)
            self.assertEqual(self.delete(f"/api/nodes/{n['id']}").status_code, 200)
        self.settings({"min_free_mb": 0})
        with mock.patch.object(marker, "free_space", return_value=0):
            self.note("Fine", text="ok")

    def test_kept_versions_count_toward_the_quota(self):
        n = self.note("Big", text="small")
        self.settings({"quota_gb": 1})
        with db.get_conn() as conn:          # versions kept of it: 1.5 GB (rows only — check_quota adds up sizes)
            conn.execute("INSERT INTO versions (node_id, n, file, size, sha256, created_at) VALUES (?, 99, 'x', ?, 'x', "
                         "'2026-01-01')", (n["id"], 1536 * 1024 * 1024))
        r = self.c.post("/api/nodes/mine/upload?name=a.txt", content=b"x", headers=KABIR)
        self.assertEqual(r.status_code, 507, r.text)


# =====================================================================
# The flaky "removed 91 != 120": a part acked while another part's items were being taken out
# =====================================================================
class MovedItems(ApiBase):
    def test_a_part_acked_meanwhile_still_has_its_items_taken_out(self):
        import json
        from app import app_messages, documents
        c = self.create("checklist", "Move me")
        out = self.ok(self.ops(c["id"], [{"op": "add", "text": f"T{i}"} for i in range(4)]))
        keys = [i["key"] for i in out["items"]]
        parts = [{"n": 2, "keys": keys[:2], "state": "acked", "remove": True},
                 {"n": 2, "keys": keys[2:], "state": "sent"}]
        with db.get_conn() as conn:
            conn.execute("INSERT INTO todo_sends (id, node_id, user_id, target, move, parts, items_total, state, "
                         "created_at, updated_at) VALUES ('s1', ?, 'u_kabir', '{}', 1, ?, 4, 'sending', 'x', 'x')",
                         (c["id"], json.dumps(parts)))
        real = documents.checklist_ops
        acked = []

        def ops_then_ack(conn, user, nid, ops):
            res = real(conn, user, nid, ops)
            if not acked:                            # the second part's ack lands while the first is being removed
                acked.append(1)
                ps = json.loads(conn.execute("SELECT parts FROM todo_sends WHERE id = 's1'").fetchone()[0])
                ps[1].update(state="acked", remove=True)
                conn.execute("UPDATE todo_sends SET parts = ? WHERE id = 's1'", (json.dumps(ps),))
            return res
        with mock.patch.object(documents, "checklist_ops", ops_then_ack):
            removed = app_messages._remove_moved("s1")
        self.assertEqual(removed, 4)
        self.assertEqual(self.open(c["id"])["items"], [])
        with db.get_conn() as conn:
            row = conn.execute("SELECT parts, state, items_removed FROM todo_sends WHERE id = 's1'").fetchone()
        self.assertFalse(any(p.get("remove") for p in json.loads(row["parts"])))
        self.assertEqual((row["state"], row["items_removed"]), ("done", 4))


# =====================================================================
# Performance: the same answers, fewer queries
# =====================================================================
class SameAnswers(TidyBase):
    def old_duplicates(self, conn, user, root_id):
        from app import sharing, storage_report
        from app.store import nodes
        acc_sql, acc_params = sharing.access_cte(user["id"])
        shas = conn.execute(
            f"SELECT n.sha256, n.size FROM nodes n WHERE n.root_id = ? AND n.kind != 'folder' AND n.size > 0 "
            f"AND n.sha256 IS NOT NULL AND {nodes.LIVE} GROUP BY n.sha256, n.size ORDER BY n.size DESC, n.sha256",
            (root_id,)).fetchall()
        out = []
        for s in shas:
            rows = conn.execute(acc_sql + f"SELECT n.* FROM nodes n JOIN acc ON acc.node_id = n.id JOIN roots r ON "
                                f"r.id = n.root_id WHERE n.sha256 = ? AND n.size = ? AND n.kind != 'folder' AND {nodes.LIVE} "
                                f"AND (r.kind = 'person' OR r.missing = 0) ORDER BY n.mtime",
                                (*acc_params, s["sha256"], s["size"])).fetchall()
            if len(rows) >= 2:
                out.append((s["sha256"], s["size"], sorted(r["id"] for r in rows)))
        return out[:storage_report.DUP_GROUPS]

    def test_duplicates_as_before(self):
        from app import storage_report
        import random
        rnd = random.Random(4)
        folders = [None] + [self.create("folder", f"F{i}")["id"] for i in range(3)]
        for i in range(40):
            body = f"content {rnd.randrange(8)}"
            n = self.note(f"n{i}", parent=rnd.choice(folders), text=body)
            if i % 5 == 0:
                self.ok(self.share(n["id"], MEERA, "viewer"))
        for i in range(6):                           # Meera's own copies of some
            self.note(f"m{i}", h=MEERA, text=f"content {i}")
        with db.get_conn() as conn:
            for who, h in (("u_kabir", KABIR), ("u_meera", MEERA)):
                user = {"id": who, "is_child": False}
                root = self.root_id(h)
                new = [(g["sha256"], g["size"], sorted(c["id"] for c in g["copies"]))
                       for g in storage_report.duplicates(conn, user, root)]
                self.assertEqual(new, self.old_duplicates(conn, user, root))
                self.assertTrue(new or who == "u_meera")

    def test_inside_a_folder_as_before(self):
        from app.store import nodes
        top = self.create("folder", "Top")
        sub = self.create("folder", "Top sub", top["id"])
        self.create("folder", "Top0")
        self.create("folder", "Topz")
        inner = self.create("folder", "ünï", sub["id"])
        self.note("deep", parent=inner["id"])
        self.note("Top.txt-like")
        with db.get_conn() as conn:
            for f in (top, sub, inner):
                row = nodes.get(conn, f["id"])
                old = {r[0] for r in conn.execute("SELECT id FROM nodes WHERE root_id = ? AND substr(rel, 1, ?) = ?",
                                                  (row["root_id"], len(row["rel"]) + 1, row["rel"] + "/"))}
                self.assertEqual(set(nodes.descendant_ids(conn, row)), old)
                plan = " ".join(str(x[-1]) for x in conn.execute(
                    f"EXPLAIN QUERY PLAN SELECT n.id FROM nodes n WHERE {nodes.INSIDE_SQL}",
                    nodes.inside_params(row["root_id"], row["rel"])))
                self.assertIn("INDEX", plan.upper())

    def test_follows_skip_quiet_minutes_without_the_big_query(self):
        from app import follows, sharing
        f = self.create("folder", "Followed")
        self.ok(self.share(f["id"], MEERA, "editor"))
        with db.get_conn() as conn:
            conn.execute("INSERT INTO follows (user_id, target, cursor, created_at) VALUES ('u_meera', ?, ?, ?)",
                         (f["id"], "2026-10-05T12:00:00+00:00", "2026-10-05T12:00:00+00:00"))
        calls = []
        real = sharing.access_cte
        with mock.patch.object(sharing, "access_cte", side_effect=lambda uid: (calls.append(uid), real(uid))[1]):
            follows.dispatch()
        self.assertEqual(calls, [])                  # nothing new: no access query at all

    def test_the_search_page_snippets_and_lines(self):
        n = self.note("Diary", text="first line\nsecond has the needle word\nthird")
        hit = next(r for r in self.ok(self.get("/api/search?q=needle"))["results"] if r["id"] == n["id"])
        self.assertIn("⁅needle⁆", hit["snippet"])
        self.assertEqual(hit["line"], 2)


# =====================================================================
# DI1, DI2: ticks at the same moment; control characters in sheets
# =====================================================================
class SameMoment(ApiBase):
    def test_two_ticks_at_once_keep_both_names(self):
        import threading
        from app import auth, documents
        c = self.create("checklist", "Shopping")
        self.ok(self.share(c["id"], MEERA, "editor"))
        out = self.ok(self.ops(c["id"], [{"op": "add", "text": "Milk"}, {"op": "add", "text": "Eggs"}]))
        keys = {i["text"]: i["key"] for i in out["items"]}
        with db.get_conn() as conn:
            kabir = auth.user_dict(conn.execute("SELECT * FROM users WHERE id = 'u_kabir'").fetchone())
            meera = auth.user_dict(conn.execute("SELECT * FROM users WHERE id = 'u_meera'").fetchone())
        self.clock.advance(minutes=10)
        errors = []

        def tick(user, key, linger):
            try:
                with db.get_conn() as conn:
                    documents.checklist_ops(conn, user, c["id"], [{"op": "tick", "key": key}])
                    time.sleep(linger)                 # the request isn't over yet (its transaction still open)
            except Exception as e:                     # pragma: no cover
                errors.append(e)
        a = threading.Thread(target=tick, args=(kabir, keys["Milk"], 0.6))
        b = threading.Thread(target=tick, args=(meera, keys["Eggs"], 0))
        a.start()
        time.sleep(0.15)
        b.start()
        a.join(10)
        b.join(10)
        self.assertEqual(errors, [])
        items = {i["text"]: i for i in self.open(c["id"])["items"]}
        self.assertTrue(items["Milk"]["done"] and items["Eggs"]["done"])
        with db.get_conn() as conn:
            by = {r["item_key"]: r["done_by"] for r in conn.execute("SELECT * FROM checklist_ticks WHERE node_id = ?", (c["id"],))}
            self.assertEqual(by, {keys["Milk"]: "u_kabir", keys["Eggs"]: "u_meera"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM versions WHERE node_id = ? AND made_by IS NULL",
                                          (c["id"],)).fetchone()[0], 0)


class ControlCharacters(TidyBase):
    def test_control_characters_are_taken_out_and_the_xlsx_saves(self):
        sh = self.sheet("Odd", {"A1": {"v": "bell\x07 and\x0bvertical tab"}, "A2": {"v": "tab\tand\nline"}})
        cells = self.open(sh["id"])["sheet"]["tabs"][0]["cells"]
        self.assertEqual(cells["A1"]["v"], "bell and" + "vertical tab")
        self.assertEqual(cells["A2"]["v"], "tab\tand\nline")              # tab and line breaks stay

    def test_a_csv_with_control_characters_imports(self):
        r = self.c.post("/api/import?name=odd.csv&format=csv", content="a\x01b,c\n".encode(), headers=KABIR)
        self.assertEqual(r.status_code, 201, r.text)
        cells = self.open(r.json()["id"])["sheet"]["tabs"][0]["cells"]
        self.assertEqual(cells["A1"]["v"], "ab")

    def test_formulas_nested_too_deep_are_refused(self):
        from app.formats import sheet_model as M
        with self.assertRaises(M.SheetError):
            M.clean_cell({"v": "=" + "(" * 65 + "1" + ")" * 65})
        self.assertEqual(M.clean_cell({"v": "=" + "(" * 64 + "1" + ")" * 64})["v"][:3], "=((")
        self.assertEqual(M.clean_cell({"v": '="((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((("', "q": 1})["q"], 1)
        self.assertEqual(M.nesting('=IF(A1, "(((", (1))'), 2)


# =====================================================================
# The shared code switched on here: request sizes, cached assets, signed app messages, stale snapshots
# =====================================================================
class SharedWiring(ApiBase):
    def test_versioned_assets_are_cached_for_good_and_pages_are_not(self):
        from app import config
        r = self.get(f"/app.js?v={config.APP_VERSION}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertNotIn("immutable", self.get("/app.js").headers.get("cache-control", ""))
        self.assertIn("no-store", self.get("/").headers["cache-control"])
        self.assertEqual(self.get("/api/health").headers["cache-control"], "no-store")
        self.assertNotIn("immutable", self.get("/nothing-here.js?v=1").headers.get("cache-control", ""))

    def test_request_sizes(self):
        r = self.c.post("/api/nodes/x/rename", content=b"{" + b" " * 300_000 + b"}",
                        headers={**KABIR, "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        self.assertIn("256 KB", r.json()["detail"])
        n = self.note("N", text="x")
        up = self.c.post("/api/nodes/mine/upload?name=big.bin", content=b"x" * 400_000, headers=KABIR)   # streamed route
        self.assertEqual(up.status_code, 201, up.text)
        self.assertEqual(self.save(n["id"], "y" * 400_000)["size"], 400_000)                      # a note's save

    def test_the_identity_dependency_runs_off_the_event_loop(self):
        import inspect
        from app import auth
        self.assertFalse(inspect.iscoroutinefunction(auth.get_current_user))

    def test_app_messages_can_be_signed(self):
        from app import app_messages, settings
        self.assertIn("bus_signing_key", settings.REGISTRY.secret_blanks())          # blanked in backups too
        self.settings({"bus_signing_key": "s3cret-key"})
        seen = {}
        with mock.patch.object(app_messages.bus, "start", side_effect=lambda *a, **kw: seen.update(kw)), \
                mock.patch.object(app_messages.bus.default, "started", False), \
                mock.patch.object(app_messages, "learn_panel"):
            app_messages.start(thread=False)
        self.assertEqual(seen["signing_key"](), "s3cret-key")
        self.assertNotIn("s3cret-key", self.ok(self.get("/api/admin/settings", ASHA)).__repr__())

    def test_abandoned_backup_copies_are_swept(self):
        import os
        from app import config, housekeeping
        old = os.path.join(config.DATA_DIR, ".backup-old.db")
        new = os.path.join(config.DATA_DIR, ".backup-new.db")
        for p in (old, new):
            with open(p, "wb") as f:
                f.write(b"x")
        os.utime(old, (time.time() - 7 * 3600,) * 2)
        self.assertEqual(housekeeping.remove_stale_snapshots(), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        os.remove(new)

    def test_a_backup_is_streamed_and_its_copy_removed(self):
        import glob
        import io
        import os
        import zipfile
        from app import config
        self.note("A", text="hello")
        r = self.get("/api/admin/backup", ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("attachment", r.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            self.assertEqual(z.namelist(), ["docs.db"])
        self.assertEqual(glob.glob(os.path.join(config.DATA_DIR, ".backup-*")), [])


if __name__ == "__main__":
    unittest.main()
