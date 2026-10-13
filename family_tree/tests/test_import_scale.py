"""Importing a big tree: the upload is read and planned off the event loop, links are read once, the
family tree graph is loaded once for all the child links (and still catches loops made inside the same
file), photos are processed before the write transaction, and the faster lookups give the same answers
as the old ones."""
from base import ADMIN, ApiTestCase, image_bytes, sql

import io
import json
import random
import unittest
import zipfile
from unittest import mock

from app import db, graph, media, tree_data
from app.routers import tree_import


def big_tree(n_families=400, kids=3, source="far-away-tree"):
    """Generations of couples, each with `kids` children; every child after the first generation is
    someone's partner in the next."""
    people, families = [], []
    nid = [0]

    def person(gender):
        nid[0] += 1
        p = {"id": f"p{nid[0]}", "given_names": f"Person{nid[0]}", "surname": "Big", "gender": gender,
             "events": [{"id": f"e{nid[0]}", "type": "birth", "date": {"date_text": str(1800 + nid[0] % 200)}}]}
        people.append(p)
        return p["id"]

    pool = [person("male"), person("female")]
    for i in range(n_families):
        a = pool.pop(0) if pool else person("male")
        b = person("female")
        children = [person("male" if k % 2 else "female") for k in range(kids)]
        pool.extend(children)
        families.append({"id": f"f{i}", "p1": a, "p2": b, "kind": "married",
                         "children": [{"id": c, "relation": "birth"} for c in children]})
    return {"format": tree_data.FORMAT, "version": 1, "source": {"id": source, "title": "Big tree"},
            "people": people, "families": families, "media": []}


class ImportScale(ApiTestCase):
    def upload_json(self, data):
        r = self.post("/api/admin/import", user=ADMIN,
                      files={"file": ("tree.json", json.dumps(data).encode(), "application/json")})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_thousands_of_people_with_one_graph_load(self):
        data = big_tree()
        prev = self.upload_json(data)
        self.assertEqual(prev["counts"]["newPeople"], len(data["people"]))
        loads = []
        real = graph.load_fresh
        with mock.patch.object(graph, "load_fresh", side_effect=lambda c: loads.append(1) or real(c)):
            stats = self.ok(self.post("/api/admin/import/apply", {"token": prev["token"]}, user=ADMIN))
        self.assertLessEqual(len(loads), 1)
        self.assertEqual(stats["people"], len(data["people"]))
        self.assertEqual(stats["families"], len(data["families"]))
        self.assertEqual(stats["children"], sum(len(f["children"]) for f in data["families"]))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM family_children")[0]["n"], stats["children"])
        with db.get_conn() as conn:
            g = graph.load_fresh(conn)
        self.assertFalse(any(g.in_loop(pid) for pid in g.people))

    def test_a_loop_inside_the_same_file_is_still_refused(self):
        # A is B's parent in f1; f2 then makes A B's child: refused, reported, and the rest goes in
        people = [{"id": x, "given_names": x, "events": []} for x in ("A", "B", "C")]
        data = {"format": tree_data.FORMAT, "version": 1, "source": {"id": "loopy", "title": "Loopy"},
                "people": people, "media": [],
                "families": [{"id": "f1", "p1": "A", "children": [{"id": "B"}]},
                             {"id": "f2", "p1": "B", "children": [{"id": "C"}, {"id": "A"}]}]}
        prev = self.upload_json(data)
        stats = self.ok(self.post("/api/admin/import/apply", {"token": prev["token"]}, user=ADMIN))
        self.assertEqual(stats["children"], 2)
        self.assertTrue(any("own ancestor" in s for s in stats["skipped"]), stats["skipped"])

    def test_broken_shapes_are_a_422_not_a_crash(self):
        def person(**kw):
            return dict({"id": "p1", "given_names": "Ann", "events": []}, **kw)

        base = {"format": tree_data.FORMAT, "version": 1, "source": {"id": "odd", "title": "Odd"}, "families": [], "media": []}
        broken = [
            dict(base, people={"p1": person()}),
            dict(base, people=[person(other_names={"name": "x"})]),
            dict(base, people=[person(events="birth")]),
            dict(base, people=[person(stories={"a": 1})]),
            dict(base, people=[person()], families=[{"id": "f1", "p1": "p1", "children": {"id": "p1"}}]),
            dict(base, people=[person()], media=[{"id": "m1", "kind": "photo", "file": "media/m1.jpg", "people": "p1"}]),
            dict(base, source=["odd"], people=[person()]),
            dict(base, people=[person(given_names={"first": "Ann"})]),
        ]
        for data in broken:
            r = self.post("/api/admin/import", user=ADMIN,
                          files={"file": ("tree.json", json.dumps(data).encode(), "application/json")})
            self.assertEqual(r.status_code, 422, (r.text, data))
            self.assertIn("tree data", r.json()["detail"])
        # a partner or child given as a list (unhashable) used to crash the lookup; now it's just not someone
        self.upload_json(dict(base, people=[person()], families=[{"id": "f1", "p1": ["p1"], "children": []}]))
        self.upload_json(dict(base, people=[person()], families=[{"id": "f1", "p1": "p1", "children": [{"id": ["x"]}]}]))

    def test_upload_is_read_and_planned_in_a_thread(self):
        calls = []
        real = tree_import.run_in_threadpool

        async def spy(fn, *a, **kw):
            calls.append(fn.__name__)
            return await real(fn, *a, **kw)

        with mock.patch.object(tree_import, "run_in_threadpool", spy):
            self.upload_json(big_tree(n_families=3))
        self.assertIn("_read_and_plan", calls)

    def test_family_index_matches_the_old_scan(self):
        rnd = random.Random(7)
        ids = [f"x{i}" for i in range(30)]
        rows = []
        for i in range(300):
            a = rnd.choice(ids + [None])
            b = rnd.choice(ids + [None])
            if a or b:
                rows.append({"id": f"f{i}", "partner1_id": a, "partner2_id": b})
                sql("INSERT INTO people (id, given_names, created_at, updated_at) VALUES (?, ?, 'x', 'x') "
                    "ON CONFLICT DO NOTHING", (a or b, a or b))
        for r in rows:
            for x in (r["partner1_id"], r["partner2_id"]):
                if x:
                    sql("INSERT INTO people (id, given_names, created_at, updated_at) VALUES (?, ?, 'x', 'x') "
                        "ON CONFLICT DO NOTHING", (x, x))
            sql("INSERT INTO families (id, partner1_id, partner2_id, created_at, updated_at) VALUES (?, ?, ?, 'x', 'x')",
                (r["id"], r["partner1_id"], r["partner2_id"]))
        with db.get_conn() as conn:
            local = [dict(r) for r in conn.execute("SELECT * FROM families WHERE deleted_at IS NULL")]
            index = tree_data._FamilyIndex(conn)
        for a in ids + [None]:
            for b in ids[:10] + [None]:
                self.assertEqual(index.match(a, b), tree_data._match_family(local, a, b), (a, b))

    def test_plan_for_apply_keeps_the_same_matching(self):
        data = tree_data.validate(big_tree(n_families=20))
        prev = self.upload_json(big_tree(n_families=20))
        self.ok(self.post("/api/admin/import/apply", {"token": prev["token"]}, user=ADMIN))
        raw = big_tree(n_families=20)
        raw["people"] = raw["people"][:-5]                  # five went missing in the newer file
        again = tree_data.validate(raw)
        with db.get_conn() as conn:
            full = tree_data.plan(conn, again, set())
            quick = tree_data.plan(conn, again, set(), details=False)
        self.assertEqual(full["_pmap"], quick["_pmap"])
        self.assertEqual([d["key"] for d in full["deletions"]], [d["key"] for d in quick["deletions"]])
        self.assertEqual(sum(1 for d in full["deletions"] if d["kind"] == "person"), 5)
        self.assertTrue(data["people"])


class PhotosBeforeTheTransaction(ApiTestCase):
    def site_zip(self):
        data = {"format": tree_data.FORMAT, "version": 1, "source": {"id": "photo-tree", "title": "Photos"},
                "people": [{"id": "p1", "given_names": "Ann", "photo": "m1", "events": []}], "families": [],
                "media": [{"id": "m1", "kind": "photo", "title": "Ann", "file": "media/m1.jpg", "people": ["p1"]},
                          {"id": "m2", "kind": "photo", "title": "Broken", "file": "media/m2.jpg", "people": ["p1"]}]}
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr(tree_data.DATA_NAME, json.dumps(data))
            z.writestr("media/m1.jpg", image_bytes(size=(120, 90)))
            z.writestr("media/m2.jpg", b"\xff\xd8\xff not really a picture")
        return out.getvalue()

    def test_photos_are_processed_outside_the_write(self):
        prev = self.ok(self.post("/api/admin/import", user=ADMIN,
                                 files={"file": ("site.zip", self.site_zip(), "application/zip")}), 201)
        in_write = []
        real_process, real_apply = media.process_image, tree_data.apply
        state = {"applying": False}

        def process(raw):
            in_write.append(state["applying"])
            return real_process(raw)

        def apply(*a, **kw):
            state["applying"] = True
            try:
                return real_apply(*a, **kw)
            finally:
                state["applying"] = False

        with mock.patch.object(media, "process_image", process), mock.patch.object(tree_data, "apply", apply):
            stats = self.ok(self.post("/api/admin/import/apply", {"token": prev["token"]}, user=ADMIN))
        self.assertEqual(in_write, [False, False])                # both decoded (or tried) before the write
        self.assertEqual(stats["media"], 1)
        self.assertTrue(any("couldn't be read" in s or "damaged" in s for s in stats["skipped"]), stats["skipped"])
        mid = sql("SELECT id FROM media")[0]["id"]
        self.assertEqual(sql("SELECT photo_media_id FROM people")[0]["photo_media_id"], mid)

    def test_a_failed_import_leaves_no_files(self):
        prev = self.ok(self.post("/api/admin/import", user=ADMIN,
                                 files={"file": ("site.zip", self.site_zip(), "application/zip")}), 201)
        stored = []
        real_store = media.store_image

        def store(mid, processed):
            stored.append(mid)
            return real_store(mid, processed)

        with mock.patch.object(media, "store_image", store), \
                mock.patch.object(tree_data, "_import_media", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.post("/api/admin/import/apply", {"token": prev["token"]}, user=ADMIN)
        self.assertEqual(len(stored), 1)
        import os
        self.assertFalse(os.path.exists(media.file_path(stored[0])))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
