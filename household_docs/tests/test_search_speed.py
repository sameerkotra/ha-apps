"""Speed (SPEC §10.7): name, filter and word searches over 50 000 indexed entries (target under 300 ms on a Raspberry
Pi 4) and FTS content searches (under 500 ms). The rows are synthetic — written straight into the index, no files —
spread over two people's folders and an admin shared folder, so the access check has real work to do. The timings
are printed; the assertions use a generous ceiling so a busy test machine doesn't fail the run (run this file alone
and read the numbers)."""
import _env  # noqa: F401

import os
import time
import unittest
from urllib.parse import urlencode

from app import config, db
from app.search import fts
from base import ASHA, KABIR, MEERA, SHARE, ApiBase

N = 50_000
WORDS = ("invoice", "receipt", "garden", "budget", "insurance", "recipe", "holiday", "school", "manual", "warranty",
         "letter", "report", "tax", "plan", "notes", "bill", "photo", "scan", "contract", "minutes")
EXTS = ("txt", "pdf", "md", "jpg", "xlsx", "docx", "csv", "png")
CEILING_MS = 3000


class Speed(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA)
    timings: dict = {}

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(SHARE, "Archive"))
        rid = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/Archive", "label": "Archive",
                                                              "access": {"u_kabir": "ro"}}, ASHA), 201)["id"]
        roots = [self.root_id(KABIR), self.root_id(MEERA), rid]
        now = config.utcnow()
        nodes_rows, fts_rows = [], []
        folder_ids = {}
        for r in roots:
            for f in range(100):
                fid = f"f{r[:6]}{f:04d}"
                folder_ids[(r, f)] = fid
                name = f"Folder {f} {WORDS[f % len(WORDS)]}"
                nodes_rows.append((fid, r, name, None, name, fts.fold(name), None, "folder", None,
                                   (now.replace(day=1)).isoformat(timespec="seconds"), None, None))
                fts_rows.append((fid, name, ""))
        for i in range(N):
            r = roots[i % 3]
            f = (i // 3) % 100
            w1, w2 = WORDS[i % len(WORDS)], WORDS[(i * 7) % len(WORDS)]
            ext = EXTS[i % len(EXTS)]
            name = f"{w1}-{i:05d} {w2}.{ext}"
            nid = f"n{i:07d}"
            day = 1 + i % 28
            mtime = now.replace(month=1 + i % 12, day=day).isoformat(timespec="seconds")
            sha = f"{i % 997:064x}" if i % 10 == 0 else None
            nodes_rows.append((nid, r, f"Folder {f} {WORDS[f % len(WORDS)]}/{name}", folder_ids[(r, f)], name,
                               fts.fold(name), ext, "note" if ext == "txt" else "file", 100 + i * 37 % 5_000_000, mtime,
                               None if i % 5 else "u_kabir", sha))
            body = f"{w1} {w2} lorem ipsum {i} dolor {WORDS[(i * 3) % len(WORDS)]}" if ext in ("txt", "md", "csv") else ""
            fts_rows.append((nid, name, body))
        with db.get_conn() as conn:
            conn.executemany("INSERT INTO nodes (id, root_id, rel, parent_id, name, name_folded, ext, kind, size, mtime, "
                             "updated_by, sha256, ctime) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'2026-01-01T00:00:00+00:00')", nodes_rows)
            conn.executemany("INSERT INTO fts (node_id, name, body) VALUES (?, ?, ?)", fts_rows)
            conn.execute("ANALYZE")

    def timed(self, label, **params):
        self.get("/api/search?" + urlencode(params))                  # warm
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            r = self.get("/api/search?" + urlencode(params))
            ms = (time.perf_counter() - t0) * 1000
            self.assertEqual(r.status_code, 200, r.text)
            best = ms if best is None else min(best, ms)
        self.timings[label] = (round(best), r.json()["total"])
        return r.json()

    def test_speed(self):
        with db.get_conn() as conn:
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], N)
        self.timed("name contains", q="warranty 012", match="name")
        self.timed("name starts", q="garden-1", nameMode="starts")
        self.timed("name wildcard", q="*-0012? *.pdf", nameMode="wildcard")
        self.timed("name pattern in the box", q="receipt-0*.pdf")
        self.timed("filters only", modified="30d", type="pdf")
        self.timed("filters + name", q="bill", size=">1mb", sort="size")
        self.timed("path filter", q="path:folder*/ tax")
        self.timed("words (name or text)", q="insurance")
        self.timed("FTS content", q="lorem dolor", match="content")
        self.timed("FTS phrase", q='"ipsum 4712"', match="content")
        self.timed("duplicates", q="is:dup")
        self.timed("everything + group", q="plan", group="location", sort="modified")
        self.timed("name regex (worker)", q="^warranty-0\\d{3}9", nameMode="regex")
        self.timed("text regex (worker)", q="ipsum 47\\d\\d dolor", contentMode="regex", match="content")
        print("\nSearch over", N, "rows (best of 3, through the API):")
        for k, (ms, total) in self.timings.items():
            print(f"  {k:24s} {ms:6d} ms  ({total} results)")
        for k, (ms, _total) in self.timings.items():
            self.assertLess(ms, CEILING_MS, k)
        # Meera never sees Kabir's or the Archive's rows, even here
        r = self.get("/api/search?" + urlencode({"q": "invoice", "match": "name"}), MEERA).json()
        mine = self.root_id(MEERA)
        self.assertTrue(all(x["rootId"] == mine for x in r["results"]))


if __name__ == "__main__":
    unittest.main()
