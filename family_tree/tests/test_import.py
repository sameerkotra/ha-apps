"""Importing part of another Family Tree (§13.6.3): the data file in the website export, the two-step
import (preview, then apply), incremental re-imports that only add, matching on first import, deletions the
admin removes or keeps, photos, undo, and the refusals."""
from base import ADMIN, ALICE, ApiTestCase, image_bytes, reset_state, sql

import io
import json
import os
import shutil
import tempfile
import time
import zipfile

import _env
from app import config, db, graph, media, settings


class Install:
    """A saved copy of one install (its database and photo folder), to switch between two trees."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(dir=_env.TMP)

    def save(self):
        with db.get_conn() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.rmtree(self.dir)
        os.makedirs(self.dir)
        shutil.copy(config.DB_PATH, os.path.join(self.dir, "family.db"))
        if os.path.isdir(_env.MEDIA_PATH):
            shutil.copytree(_env.MEDIA_PATH, os.path.join(self.dir, "media"))

    def load(self):
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(config.DB_PATH + ext):
                os.remove(config.DB_PATH + ext)
        shutil.copy(os.path.join(self.dir, "family.db"), config.DB_PATH)
        shutil.rmtree(_env.MEDIA_PATH, ignore_errors=True)
        if os.path.isdir(os.path.join(self.dir, "media")):
            shutil.copytree(os.path.join(self.dir, "media"), _env.MEDIA_PATH)
        db.init_db()
        settings.invalidate()
        with graph._lock:
            graph._cache["version"] = None
            graph._cache["graph"] = None
        media.check()


class ImportCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = Install(), Install()
        P = self.person
        self.gpa = P("Gopal", "Sharma", "male", born=1920, died=1990)
        self.gma = P("Gita", "Sharma", "female", born=1925, died=1999)
        self.dad = P("Venkat", "Sharma", "male", born=1950, died=2020, biography="Taught maths.")
        self.uncle = P("Ravi", "Sharma", "male", born=1948, died=2010)
        self.me = P("Arjun", "Sharma", "male", born=1980)            # living: names only by default
        self.f1 = self.family(self.gpa, self.gma, children=[self.uncle, self.dad])
        self.f2 = self.family(self.dad, None, children=[self.me])
        self.a.save()
        reset_state(all_features=True)          # now "install B": an empty tree
        self.b.save()

    # ---- helpers ----
    def export_zip(self, options=None) -> bytes:
        self.a.load()
        r = self.ok(self.post("/api/export", {"format": "site", "options": options or {}}, user=ADMIN), 202)
        for _ in range(400):
            job = self.ok(self.get(f"/api/export/{r['jobId']}", user=ADMIN))
            if job["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "done", job)
        data = self.get(f"/api/export/{r['jobId']}/file", user=ADMIN).content
        self.a.save()
        return data

    def upload(self, data: bytes, name="site.zip", user=ADMIN, status=201):
        self.b.load()
        r = self.post("/api/admin/import", user=user, files={"file": (name, data, "application/zip")})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def apply(self, token, unmatch=(), remove=()):
        out = self.ok(self.post("/api/admin/import/apply", {"token": token, "unmatch": list(unmatch), "remove": list(remove)},
                                user=ADMIN))
        self.b.save()
        return out

    def names_b(self):
        return sorted(r["given_names"] for r in sql("SELECT given_names FROM people WHERE deleted_at IS NULL"))


class TestExportData(ImportCase):
    def test_website_zip_carries_the_same_filtered_data(self):
        z = zipfile.ZipFile(io.BytesIO(self.export_zip()))
        data = json.loads(z.read("family-tree.json"))
        self.assertEqual((data["format"], data["version"]), ("family-tree-export", 1))
        people = {p["given_names"]: p for p in data["people"]}
        self.assertEqual(set(people), {"Gopal", "Gita", "Venkat", "Ravi", "Arjun"})
        self.assertTrue(people["Arjun"]["limited"])                  # living: name only
        self.assertEqual(people["Arjun"]["events"], [])
        self.assertEqual(people["Venkat"]["biography"], "Taught maths.")
        self.assertEqual([e["date"]["date_y"] for e in people["Gopal"]["events"] if e["type"] == "birth"], [1920])
        self.assertEqual(len(data["families"]), 2)
        # keep-out people and details switched off stay out of the data too
        self.a.load()
        self.patch(f"/api/people/{self.uncle}", {"never_export": True})
        self.a.save()
        data = json.loads(zipfile.ZipFile(io.BytesIO(self.export_zip({"dates": "year", "stories": False}))).read("family-tree.json"))
        self.assertNotIn("Ravi", {p["given_names"] for p in data["people"]})
        self.assertIsNone(next(p for p in data["people"] if p["given_names"] == "Venkat")["biography"])
        # and it can be left out altogether
        z = zipfile.ZipFile(io.BytesIO(self.export_zip({"importData": False})))
        self.assertNotIn("family-tree.json", z.namelist())


class TestImport(ImportCase):
    def test_first_import_adds_everything_and_undo_takes_it_back(self):
        zdata = self.export_zip()
        prev = self.upload(zdata)
        self.assertTrue(prev["firstImport"])
        self.assertEqual((prev["counts"]["newPeople"], prev["counts"]["newFamilies"], prev["counts"]["deletions"]), (5, 2, 0))
        self.assertEqual(self.names_b(), [])                         # the preview changed nothing
        stats = self.apply(prev["token"])
        self.assertEqual((stats["people"], stats["families"], stats["children"]), (5, 2, 3))
        self.assertEqual(self.names_b(), ["Arjun", "Gita", "Gopal", "Ravi", "Venkat"])
        ids = {r["given_names"]: r["id"] for r in sql("SELECT id, given_names FROM people")}
        self.assertEqual(self.label(ids["Arjun"], ids["Gopal"]), "grandfather")
        self.assertEqual(sql("SELECT date_y FROM events WHERE person_id = ? AND type = 'birth'", (ids["Gopal"],))[0]["date_y"], 1920)
        # one history entry; undo removes it all
        self.b.load()
        u = self.ok(self.post(f"/api/history/{stats['batchId']}/undo", user=ADMIN))
        self.assertTrue(u["batchId"])
        self.assertEqual(self.names_b(), [])
        self.b.save()
        # importing the same file again after the undo adds them afresh
        prev = self.upload(zdata)
        self.assertEqual(prev["counts"]["newPeople"], 5)

    def test_reimport_only_adds_and_never_overwrites(self):
        prev = self.upload(self.export_zip())
        self.apply(prev["token"])
        # here: someone edits the import
        ids = {r["given_names"]: r["id"] for r in sql("SELECT id, given_names FROM people")}
        self.b.load()
        self.patch(f"/api/people/{ids['Venkat']}", {"surname": "Sarma"}, user=ADMIN)
        self.b.save()
        # there: a new child, a new event, a fixed biography and a different surname
        self.a.load()
        kid = self.person("Leela", "Sharma", "female", born=1952, died=2000)
        self.ok(self.post(f"/api/families/{self.f1}/children", {"existingId": kid, "relation": "birth"}), 201)
        self.ok(self.post("/api/events", {"personId": self.gpa, "type": "occupation", "title": "Farmer", "place": "Guntur"}), 201)
        self.patch(f"/api/people/{self.dad}", {"surname": "Sharma-Rao", "biography": "Taught physics."})
        self.patch(f"/api/people/{self.gma}", {"nickname": "Ammamma"})
        self.a.save()
        prev = self.upload(self.export_zip())
        self.assertFalse(prev["firstImport"])
        self.assertEqual((prev["counts"]["newPeople"], prev["counts"]["newFamilies"], prev["counts"]["deletions"]), (1, 0, 0))
        upd = {u["name"]: u["adds"] for u in prev["updated"]}
        self.assertIn("occupation", upd["Gopal Sharma"])
        self.assertIn("nickname", upd["Gita Sharma"])
        stats = self.apply(prev["token"])
        self.assertEqual((stats["people"], stats["children"]), (1, 1))
        self.assertEqual(stats["events"], 3)              # Leela's birth and death, Gopal's occupation
        venkat = sql("SELECT surname, biography FROM people WHERE id = ?", (ids["Venkat"],))[0]
        self.assertEqual((venkat["surname"], venkat["biography"]), ("Sarma", "Taught maths."))   # ours kept
        self.assertEqual(sql("SELECT nickname FROM people WHERE id = ?", (ids["Gita"],))[0]["nickname"], "Ammamma")
        self.assertEqual(len(sql("SELECT 1 FROM events WHERE person_id = ? AND type = 'occupation'", (ids["Gopal"],))), 1)
        # a third time: nothing left to do
        prev = self.upload(self.export_zip())
        c = prev["counts"]
        self.assertEqual((c["newPeople"], c["updated"], c["newFamilies"], c["familiesGainingChildren"], c["deletions"]), (0, 0, 0, 0, 0))

    def test_first_import_matches_the_same_person(self):
        self.b.load()
        here = self.person("Gopal", "Sharma", "male", born=1920, user=ADMIN)
        self.person("Ravi", "Sharma", "male", born=1948, user=ADMIN)
        self.person("Ravi", "Sharma", "male", born=1948, user=ADMIN)          # two: not matched
        self.b.save()
        prev = self.upload(self.export_zip())
        self.assertEqual([m["name"] for m in prev["matched"]], ["Gopal Sharma"])
        self.assertIn("marked as died", prev["matched"][0]["adds"])
        stats = self.apply(prev["token"])
        self.assertEqual(stats["people"], 4)
        self.assertEqual(sql("SELECT deceased FROM people WHERE id = ?", (here,))[0]["deceased"], 1)
        self.assertEqual(len(sql("SELECT 1 FROM people WHERE given_names = 'Gopal'")), 1)
        # "not the same person" adds a new one instead
        reset_state(all_features=True)
        self.person("Gopal", "Sharma", "male", born=1920, user=ADMIN)
        self.b.save()
        prev = self.upload(self.export_zip())
        gopal = prev["matched"][0]["remoteId"]
        self.apply(prev["token"], unmatch=[gopal])
        self.assertEqual(len(sql("SELECT 1 FROM people WHERE given_names = 'Gopal'")), 2)

    def test_deletions_are_removed_or_kept_as_the_admin_chooses(self):
        self.apply(self.upload(self.export_zip())["token"])
        self.a.load()
        self.delete(f"/api/people/{self.uncle}")
        self.a.save()
        prev = self.upload(self.export_zip())
        dels = {d["label"]: d["key"] for d in prev["deletions"]}
        self.assertIn("Ravi Sharma", dels)
        self.assertTrue(any(k.startswith("child:") for k in dels.values()))
        # keep everything: nothing removed, and not asked again
        self.apply(prev["token"])
        self.assertIn("Ravi", self.names_b())
        self.assertEqual(self.upload(self.export_zip())["deletions"], [])
        # a later deletion, removed this time
        self.a.load()
        self.delete(f"/api/people/{self.gma}")
        self.a.save()
        prev = self.upload(self.export_zip())
        key = next(d["key"] for d in prev["deletions"] if d["label"] == "Gita Sharma")
        stats = self.apply(prev["token"], remove=[key])
        self.assertEqual(stats["removed"], 1)
        self.assertNotIn("Gita", self.names_b())
        self.assertEqual(len(sql("SELECT 1 FROM people WHERE given_names = 'Gita' AND deleted_at IS NOT NULL")), 1)   # in the trash

    def test_photos_come_along(self):
        self.a.load()
        pid = self.gpa
        r = self.post(f"/api/people/{pid}/photo", files={"file": ("g.jpg", image_bytes(size=(320, 240)), "image/jpeg")})
        self.assertEqual(r.status_code, 201, r.text)
        self.a.save()
        prev = self.upload(self.export_zip())
        self.assertEqual(prev["counts"]["newMedia"], 1)
        stats = self.apply(prev["token"])
        self.assertEqual(stats["media"], 1)
        gopal = sql("SELECT id, photo_media_id FROM people WHERE given_names = 'Gopal'")[0]
        self.assertTrue(gopal["photo_media_id"])
        self.assertTrue(os.path.isfile(media.file_path(gopal["photo_media_id"], "original")))
        self.assertEqual(len(sql("SELECT 1 FROM media_links WHERE person_id = ?", (gopal["id"],))), 1)
        # again: not duplicated
        prev = self.upload(self.export_zip())
        self.assertEqual(prev["counts"]["newMedia"], 0)

    def test_refusals(self):
        zdata = self.export_zip()
        self.upload(zdata, user=ALICE, status=403)
        self.upload(b"not a zip at all", name="x.zip", status=422)
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("index.html", "<html></html>")
        r = self.upload(bad.getvalue(), status=422)
        self.assertIn("family-tree.json", r["detail"])
        # importing a tree's own export into itself
        self.a.load()
        r = self.post("/api/admin/import", user=ADMIN, files={"file": ("s.zip", zdata, "application/zip")})
        self.assertEqual(r.status_code, 422)
        self.assertIn("same Family Tree", r.json()["detail"])
        self.assertEqual(self.post("/api/admin/import/apply", {"token": "0" * 32}, user=ADMIN).status_code, 404)
        # the data file on its own works too (without photos)
        data = zipfile.ZipFile(io.BytesIO(zdata)).read("family-tree.json")
        prev = self.upload(data, name="family-tree.json")
        self.assertEqual(prev["counts"]["newPeople"], 5)

    def test_sources_list_and_forget(self):
        self.apply(self.upload(self.export_zip())["token"])
        self.b.load()
        s = self.ok(self.get("/api/admin/import/sources", user=ADMIN))["sources"]
        self.assertEqual((len(s), s[0]["people"], s[0]["imports"]), (1, 5, 1))
        self.ok(self.delete(f"/api/admin/import/sources/{s[0]['id']}", user=ADMIN))
        self.b.save()
        prev = self.upload(self.export_zip())
        self.assertTrue(prev["firstImport"])
