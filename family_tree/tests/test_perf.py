"""Speed-ups that must not change answers (each compared with the way it used to be worked out on a seeded
tree), and smaller fixes from the October review: the user dependency off the event loop, memoised
ancestors and searches, the gallery paged in SQL, the export preview, duplicates, the chart's photos
loaded on demand, and script names honouring their switch."""
from base import ADMIN, ALICE, ApiTestCase, all_features_on, image_bytes, set_app_settings, sql
import _env

import asyncio
import json
import os
import random
import shutil
import subprocess
import unittest
from collections import deque
from datetime import date
from unittest import mock

from app import auth, config, db, duplicates, graph as graph_mod, milestones, relations


def seeded_tree(case, n=60, seed=11):
    """A random tree: couples, children, a few second marriages; returns the person ids."""
    rnd = random.Random(seed)
    names = ["Ravi", "Sita", "Lakshmi", "Venkat", "Anu", "Kiran", "Ramesh", "Padma", "Arjun", "Meena"]
    people = [case.person(rnd.choice(names), rnd.choice(["Rao", "Sharma", "Varma"]),
                          gender=rnd.choice(["male", "female"]), born=1900 + i) for i in range(n)]
    for i in range(0, n - 6, 3):
        kids = [people[j] for j in range(i + 3, min(i + 3 + rnd.randint(1, 3), n))]
        case.family(people[i], people[i + 1], children=kids)
    return people


def old_any_path(g, a, b, limit=40):
    """relations._any_path as it was before the search was kept on the graph."""
    prev = {a: None}
    q = deque([(a, 0)])
    while q:
        cur, d = q.popleft()
        if cur == b:
            break
        if d >= limit:
            continue
        nbrs = [(p, relations.ancestor_word(1, g.people[p].gender)) for p, _f, _r in g.parents(cur)]
        nbrs += [(c, relations.descendant_word(1, g.people[c].gender)) for c, _f, _r in g.children(cur)]
        nbrs += [(p, relations._spouse_word(g, p, f)) for p, f in g.partners(cur) if p]
        for n, rel in nbrs:
            if n not in prev:
                prev[n] = (cur, rel)
                q.append((n, d + 1))
    if b not in prev:
        return None
    out, cur = [], b
    while cur != a:
        p, rel = prev[cur]
        out.append({"id": cur, "rel": rel})
        cur = p
    out.append({"id": a, "rel": None})
    return list(reversed(out))


class SameAnswers(ApiTestCase):
    def test_paths_and_ancestors_unchanged(self):
        people = seeded_tree(self)
        with db.get_conn() as conn:
            g = graph_mod.load_fresh(conn)
        for a in people[::7]:
            fresh = graph_mod.Graph(g.people, g.families)
            for b in people:
                self.assertEqual(relations._any_path(g, a, b), old_any_path(fresh, a, b), (a, b))
                self.assertEqual(g.ancestors(b), fresh._ancestors(b, 12))
        # one search per person asked from, kept on the graph
        self.assertLessEqual(len(g._memo["bfs"]), graph_mod.Graph.MEMO_LIMITS["bfs"])

    def test_relationship_labels_unchanged_and_cached(self):
        people = seeded_tree(self, n=45)
        me = people[10]
        self.set_me(me)
        with db.get_conn() as conn:
            g = graph_mod.get(conn)
        fresh = graph_mod.Graph(g.people, g.families)
        for b in people:
            self.assertEqual(relations.relationship(g, me, b), relations.relationship(fresh, me, b))
        from app import kin
        lab = kin.labeler(g, me, "en")
        first = [lab(b) for b in people]
        self.assertEqual([kin.labeler(g, me, "en")(b) for b in people], first)
        self.assertEqual(len(g._memo["labels"][(me, "en")]), len(people))

    def test_gallery_pages_match_the_old_whole_list_order(self):
        ann = self.person("Ann")
        dates_ = ["1990", None, "ABT 1950", "3 MAR 1950", "nonsense date", "1950", None, "BEF 2000", "1950"]
        ids = []
        for i in range(27):
            d = dates_[i % len(dates_)]
            m = self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")},
                                  data={"personId": ann}), 201)["media"]["id"]
            if d:
                sql("UPDATE media SET date_text = ?, date_sort = NULL WHERE id = ?", (d, m))
            ids.append(m)
        # the stored keys as the migration fills them
        from app import media
        for r in sql("SELECT id, date_text FROM media"):
            sql("UPDATE media SET date_sort = ? WHERE id = ?", (media.date_sort(r["date_text"]), r["id"]))
        sql("UPDATE media SET created_at = '2026-01-01T00:00:00Z'")          # ties broken by insertion order
        for sort in ("added", "date"):
            whole = self.ok(self.get(f"/api/media?sort={sort}&page_size=200"))["items"]
            # the old way: everything in SQL's added order, then a stable Python sort on the parsed date
            rows = sql("SELECT id, date_text FROM media ORDER BY created_at DESC, rowid DESC")
            if sort == "date":
                from app.routers.media import _date_view
                rows = sorted(rows, key=lambda r: (_date_view(r["date_text"])[2] is None, _date_view(r["date_text"])[2] or ""))
            self.assertEqual([m["id"] for m in whole], [r["id"] for r in rows], sort)
            paged = []
            for page in range(1, 5):
                r = self.ok(self.get(f"/api/media?sort={sort}&page={page}&page_size=8"))
                self.assertEqual(r["total"], 27)
                paged += [m["id"] for m in r["items"]]
            self.assertEqual(paged, [m["id"] for m in whole])

    def test_gallery_pages_through_hundreds(self):
        from app import media
        conn = __import__("sqlite3").connect(config.DB_PATH)
        rows = []
        for i in range(450):
            dt = None if i % 7 == 0 else f"{1900 + (i * 37) % 120}"
            rows.append((f"{i:032x}", f"p{i}", dt, media.date_sort(dt), f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"))
        conn.executemany("INSERT INTO media (id, kind, title, date_text, date_sort, content_type, size, sha256, created_at) "
                         "VALUES (?, 'photo', ?, ?, ?, 'image/jpeg', 1, 'x', ?)", rows)
        conn.commit()
        conn.close()
        for sort, key in (("added", lambda r: r[4]), ("date", None)):
            got = []
            for page in (1, 2, 3):
                r = self.ok(self.get(f"/api/media?sort={sort}&page={page}&page_size=200"))
                self.assertEqual(r["total"], 450)
                got += [m["id"] for m in r["items"]]
            if sort == "added":
                want = [r[0] for r in sorted(rows, key=key, reverse=True)]
            else:                                   # undated last; same date: newest added first
                by_added = sorted(rows, key=lambda r: r[4], reverse=True)
                want = [r[0] for r in sorted(by_added, key=lambda r: (r[3] is None, r[3] or ""))]
            self.assertEqual(got, want, sort)

    def test_gallery_reads_only_the_page(self):
        from app.routers import media as media_router
        ann = self.person("Ann")
        for _ in range(5):
            self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}, data={"personId": ann}), 201)
        seen = []
        real = media_router.media_out
        with mock.patch.object(media_router, "media_out", side_effect=lambda c, rows: seen.append(len(rows)) or real(c, rows)):
            self.ok(self.get("/api/media?page=2&page_size=2"))
        self.assertEqual(seen, [2])

    def test_duplicate_scores_unchanged(self):
        people = seeded_tree(self, n=40, seed=3)
        with db.get_conn() as conn:
            g = graph_mod.load_fresh(conn)

        def old_score(a, b):
            # the old order: the tree walk first — any pair it ruled out must still be ruled out
            if duplicates._related_directly(g, a, b):
                return None
            return duplicates.score(g, a, b)

        for a in people:
            for b in people:
                if a < b:
                    self.assertEqual(duplicates.score(g, a, b), old_score(a, b))

    def test_duplicates_kept_across_writes_that_dont_matter(self):
        a = self.person("Ravi", "Rao", gender="male", born=1950)
        self.person("Ravi", "Rao", gender="male", born=1950)
        first = self.ok(self.get("/api/duplicates"))
        calls = []
        real = duplicates.score
        with mock.patch.object(duplicates, "score", side_effect=lambda *x: calls.append(1) or real(*x)):
            self.ok(self.post(f"/api/people/{a}/stories", {"title": "T", "body": "A story"}), 201)
            self.assertEqual(self.ok(self.get("/api/duplicates")), first)
            self.assertEqual(calls, [])                                     # a story doesn't start the search again
            self.ok(self.patch(f"/api/people/{a}", {"surname": "Raoo"}))
            self.ok(self.get("/api/duplicates"))
            self.assertTrue(calls)                                          # a name does

    def test_finer_buckets_find_pairs_a_crowded_surname_initial_hid(self):
        with mock.patch.object(duplicates, "MAX_BUCKET", 5):
            for i in range(8):
                self.person(f"Zed{i}x", "Sharma")
            self.person("Lakshmi", "Sharma", gender="female", born=1950)
            self.person("Laxmi", "Sharma", gender="female", born=1950)
            pairs = self.ok(self.get("/api/duplicates"))["items"]
        names = [{x["name"] for x in p["people"]} for p in pairs]
        self.assertIn({"Lakshmi Sharma", "Laxmi Sharma"}, names)


class ExportPreview(ApiTestCase):
    def test_preview_counts_match_the_full_build(self):
        people = seeded_tree(self, n=30, seed=5)
        self.set_me(people[12])
        for p in people[:6]:
            self.ok(self.post(f"/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}, data={"personId": p}), 201)
        young = self.person("Young", "Rao", born=1995)
        self.family(people[12], None, children=[young])
        self.ok(self.post(f"/api/people/{young}/contacts", {"kind": "phone", "value": "+15550001111"}), 201)
        from app.export_view import ExportOptions, build, included_ids
        for opts in ({}, {"living": "full", "contacts": True, "sources": True}, {"photos": "profile"}, {"living": "exclude"}):
            o = ExportOptions.model_validate(opts)
            with db.get_conn() as conn:
                full, quick = build(conn, o, people[12]), build(conn, o, people[12], stats_only=True)
            self.assertEqual(full.stats(), quick.stats(), opts)
            self.assertEqual(included_ids(full), included_ids(quick))
            self.assertEqual(full.warnings, quick.warnings)
            self.assertEqual(full.relative_to_name, quick.relative_to_name)

    def test_preview_never_renders_photo_fixes_and_exports_keep_the_caches(self):
        pid = self.person("Ann")
        mid = self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(size=(200, 100)), "image/jpeg")},
                                data={"personId": pid}), 201)["media"]["id"]
        sql("UPDATE media SET edit = ? WHERE id = ?", (json.dumps({"rotate": 90, "angle": 0, "crop": None, "autocontrast": False}), mid))
        from app import media
        with mock.patch.object(media, "ensure_edited", side_effect=AssertionError("rendered")):
            r = self.ok(self.post("/api/export/preview", {"format": "site", "options": {"living": "full"}}))
        self.assertEqual(r["photos"], 1)
        version = sql("SELECT value FROM settings WHERE key = 'tree_version'")[0]["value"]
        r = self.post("/api/export/gedcom", {"format": "gedcom", "options": {"living": "full"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(sql("SELECT value FROM settings WHERE key = 'tree_version'")[0]["value"], version)
        self.assertTrue(self.ok(self.get("/api/history"))["items"][0]["label"].startswith("Exported"))


class UserDependency(ApiTestCase):
    def test_runs_in_the_thread_pool_and_writes_rarely(self):
        self.assertFalse(asyncio.iscoroutinefunction(auth.get_current_user))
        self.ok(self.get("/api/me"))
        before = sql("SELECT last_seen FROM users WHERE id = ?", (ALICE["id"],))[0]["last_seen"]
        writes = []
        real = db.get_conn

        class Spy:
            def __enter__(self_):
                self_.cm = real()
                conn = self_.cm.__enter__()
                conn.set_trace_callback(lambda s: writes.append(s) if s.lstrip().upper().startswith("UPDATE USERS") else None)
                return conn

            def __exit__(self_, *a):
                return self_.cm.__exit__(*a)

        with mock.patch.object(auth.db, "get_conn", lambda: Spy()):
            for _ in range(3):
                self.ok(self.get("/api/me"))
        self.assertEqual(writes, [])
        self.assertEqual(sql("SELECT last_seen FROM users WHERE id = ?", (ALICE["id"],))[0]["last_seen"], before)
        later = config.utcnow() + __import__("datetime").timedelta(seconds=auth.LAST_SEEN_EVERY + 5)
        with mock.patch.object(config, "utcnow", return_value=later):
            self.ok(self.get("/api/me"))
        self.assertNotEqual(sql("SELECT last_seen FROM users WHERE id = ?", (ALICE["id"],))[0]["last_seen"], before)


class ScriptNamesSwitch(ApiTestCase):
    def test_milestones_leave_out_script_names_when_off(self):
        pid = self.person("Lakshmi", "Sharma", born={"d": 20, "m": 10, "y": 1966}, given_local="లక్ష్మి")
        with db.get_conn() as conn:
            g = graph_mod.get(conn)
            on = milestones.upcoming(conn, g, date(2026, 10, 13), 30)
        self.assertEqual(on[0]["people"][0]["nameLocal"], "లక్ష్మి")
        set_app_settings(feature_script_names=False)
        with db.get_conn() as conn:
            g = graph_mod.get(conn)
            off = milestones.upcoming(conn, g, date(2026, 10, 13), 30)
        self.assertIsNone(off[0]["people"][0]["nameLocal"])
        self.assertEqual(pid, off[0]["people"][0]["id"])


@unittest.skipUnless(shutil.which("node"), "Node.js isn't installed")
class ChartPhotosOnDemand(unittest.TestCase):
    def run_js(self, body):
        src = os.path.join(_env.ROOT, "app", "static", "tree.js")
        script = ("const vm = require('vm'); const fs = require('fs');"
                  "const ctx = { window: {}, document: {} }; vm.createContext(ctx);"
                  f"vm.runInContext(fs.readFileSync({json.dumps(src)}, 'utf8'), ctx);"
                  "const F = ctx.window.FamilyChart;" + body)
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_visible_window(self):
        r = self.run_js(
            "const w = F.visibleWindow({k: 1, x: 0, y: 0}, 800, 600, 0.5);"
            "const far = F.visibleWindow({k: 0.1, x: 0, y: 0}, 800, 600, 0.5);"
            "const card = (x, y) => F.cardInWindow({x, y}, w);"
            "console.log(JSON.stringify({w, far, inside: card(100, 100), near: card(1100, 100), "
            "out: card(3000, 100), left: card(-500, 0), above: card(0, -1000)}));")
        self.assertEqual(r["w"], {"x0": -400, "y0": -300, "x1": 1200, "y1": 900})
        self.assertIsNone(r["far"])                     # zoomed far out: no photos fetched
        self.assertEqual((r["inside"], r["near"], r["out"], r["left"], r["above"]), (True, True, False, True, False))

    def test_cards_start_without_a_photo_address(self):
        src = open(os.path.join(_env.ROOT, "app", "static", "tree.js"), encoding="utf-8").read()
        self.assertIn('"data-href": href', src)
        self.assertNotIn('s("image", { href,', src)


if __name__ == "__main__":
    unittest.main()
