"""The photo gallery (§5, §10 Media): uploads of photos and PDFs with and
without links, listing and filters, details, tags, trash, choosing an existing
photo as the profile photo, and history/undo for every write."""
from base import ADMIN, ALICE, BOB, ApiTestCase, image_bytes, set_app_settings, sql
import _env

import os
import unittest

from app import media
from app.routers import media as media_router

PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def all_files(root):
    out = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.add(os.path.relpath(os.path.join(dirpath, f), root))
    return out


class GalleryCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.ann = self.person("Ann", "Smith", gender="female")
        self.bob = self.person("Bob", "Smith", gender="male")

    def up(self, data=None, name="photo.jpg", ctype="image/jpeg", user=None, **form):
        data = image_bytes() if data is None else data
        return self.post("/api/media", files={"file": (name, data, ctype)}, data=form or None, user=user)

    def up_ok(self, data=None, **kw):
        body = self.ok(self.up(data, **kw), 201)
        self.assertIn("batchId", body)
        return body["media"]

    def listing(self, **params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return self.ok(self.get(f"/api/media?{qs}"))

    def undo(self, bid, user=None):
        return self.ok(self.post(f"/api/history/{bid}/undo", user=user))

    def media_row(self, mid):
        rows = sql("SELECT * FROM media WHERE id = ?", (mid,))
        return rows[0] if rows else None

    def links_of(self, mid):
        return sql("SELECT * FROM media_links WHERE media_id = ?", (mid,))


class Uploads(GalleryCase):
    def test_photo_without_link(self):
        m = self.up_ok(name="Beach_day.jpg")
        self.assertEqual((m["kind"], m["contentType"], m["title"], m["links"]), ("photo", "image/jpeg", "Beach day", []))
        self.assertEqual((m["width"], m["height"]), (64, 48))
        self.assertFalse(m["deleted"])
        self.assertEqual(m["profileOf"], [])
        self.assertNotIn("_sort", m)
        d = os.path.dirname(media.file_path(m["id"]))
        self.assertEqual(sorted(os.listdir(d)), ["original", "thumb1024.jpg", "thumb256.jpg"])
        row = self.media_row(m["id"])
        self.assertEqual((row["created_by"], row["kind"]), (ALICE["id"], "photo"))
        b = sql("SELECT * FROM batches ORDER BY rowid DESC LIMIT 1")[0]
        self.assertEqual(b["label"], "Added a photo")

    def test_title_from_form_wins_and_is_capped(self):
        m = self.up_ok(name="ignored.jpg", title="  Grandma's   70th  ")
        self.assertEqual(m["title"], "Grandma's 70th")
        m = self.up_ok(name="x.jpg", title="t" * 500)
        self.assertEqual(len(m["title"]), 200)
        m = self.up_ok(name="__.jpg")
        self.assertIsNone(m["title"])

    def test_photo_linked_to_person(self):
        m = self.up_ok(personId=self.ann)
        self.assertEqual([(l["personId"], l["label"]) for l in m["links"]], [(self.ann, "Ann Smith")])
        # a gallery upload never changes the profile photo
        self.assertIsNone(self.detail(self.ann)["photo"])
        b = sql("SELECT * FROM batches ORDER BY rowid DESC LIMIT 1")[0]
        self.assertEqual(b["label"], "Added a photo of Ann Smith")
        people = [r["person_id"] for r in sql("SELECT person_id FROM batch_people WHERE batch_id = ?", (b["id"],))]
        self.assertEqual(people, [self.ann])

    def test_pdf_document(self):
        m = self.up_ok(PDF, name="Birth certificate.pdf", ctype="application/pdf", personId=self.bob)
        self.assertEqual((m["kind"], m["contentType"], m["width"], m["height"]), ("document", "application/pdf", None, None))
        self.assertEqual(m["title"], "Birth certificate")
        d = os.path.dirname(media.file_path(m["id"]))
        self.assertEqual(os.listdir(d), ["original"])
        with open(media.file_path(m["id"]), "rb") as f:
            self.assertEqual(f.read(), PDF)                       # kept exactly as uploaded
        self.assertEqual(self.media_row(m["id"])["sha256"], media.sha256(PDF))
        b = sql("SELECT * FROM batches ORDER BY rowid DESC LIMIT 1")[0]
        self.assertEqual(b["label"], "Added a document of Bob Smith")

    def test_pdf_served_as_attachment_with_nosniff(self):
        m = self.up_ok(PDF, name="x.pdf", ctype="application/pdf")
        for size in ("original", "256", "1024"):          # documents have no thumbnails: always the file
            with self.subTest(size=size):
                r = self.get(f"/api/media/{m['id']}/file?size={size}")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["content-type"], "application/pdf")
                self.assertEqual(r.headers["content-disposition"], "attachment")
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
                self.assertEqual(r.content, PDF)

    def test_photo_served_inline(self):
        m = self.up_ok()
        r = self.get(f"/api/media/{m['id']}/file?size=original")
        self.assertEqual((r.headers["content-type"], r.headers["content-disposition"]), ("image/jpeg", "inline"))
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_link_to_family_and_event(self):
        fid = self.family(self.ann, self.bob)
        m = self.up_ok(familyId=fid)
        self.assertEqual(len(m["links"]), 1)
        self.assertEqual(m["links"][0]["familyId"], fid)
        self.assertIn(m["links"][0]["label"], ("Family of Ann Smith & Bob Smith", "Family of Bob Smith & Ann Smith"))
        eid = self.ok(self.post("/api/events", {"personId": self.ann, "type": "education", "title": "Graduation at MIT"}), 201)["event"]["id"]
        m = self.up_ok(eventId=eid)
        self.assertEqual(len(m["links"]), 1)
        self.assertEqual(m["links"][0]["eventId"], eid)
        self.assertEqual(m["links"][0]["personId"], self.ann)
        # the event title keeps its own capitals
        self.assertEqual(m["links"][0]["label"], "Graduation at MIT — Ann Smith")
        # the person's listing includes photos of their events
        ids = [x["id"] for x in self.listing(personId=self.ann)["items"]]
        self.assertIn(m["id"], ids)

    def test_bad_link_targets_leave_nothing_behind(self):
        fid = self.family(self.ann, self.bob)
        gone = self.person("Gone")
        self.ok(self.delete(f"/api/people/{gone}"))
        before = all_files(_env.MEDIA_PATH)
        cases = [
            ({"personId": "nope"}, 404), ({"personId": gone}, 404), ({"familyId": "nope"}, 404),
            ({"eventId": "nope"}, 404), ({"personId": self.ann, "familyId": fid}, 422),
        ]
        for form, status in cases:
            with self.subTest(form=form):
                self.assertEqual(self.up(**form).status_code, status)
                self.assertEqual(self.up(PDF, name="d.pdf", ctype="application/pdf", **form).status_code, status)
        self.assertEqual(all_files(_env.MEDIA_PATH), before)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)

    def test_link_limit_per_person(self):
        media_router.MAX_LINKS_PER_PERSON, old = 2, media_router.MAX_LINKS_PER_PERSON
        self.addCleanup(setattr, media_router, "MAX_LINKS_PER_PERSON", old)
        self.up_ok(personId=self.ann)
        self.up_ok(personId=self.ann)
        before = all_files(_env.MEDIA_PATH)
        r = self.up(personId=self.ann)
        self.assertEqual(r.status_code, 422)
        self.assertIn("at most 2", r.json()["detail"])
        self.assertEqual(all_files(_env.MEDIA_PATH), before)

    def test_rejected_types(self):
        cases = {
            "svg": b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "svg bare": b"<svg onload=alert(1)>",
            "html": b"<!DOCTYPE html><html><script>alert(1)</script></html>",
            "text": b"hello, I am a PDF, honest",
            "pdf later in file": b"   %PDF-1.4 but not at the start",
            "empty": b"",
            "heic": b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64,
            "broken jpeg": b"\xff\xd8\xff\xe0" + b"garbage" * 20,
        }
        before = all_files(_env.MEDIA_PATH)
        for what, data in cases.items():
            for name, ctype in (("x.pdf", "application/pdf"), ("x.jpg", "image/jpeg"), ("x.svg", "image/svg+xml")):
                with self.subTest(what=what, name=name):
                    r = self.up(data, name=name, ctype=ctype, personId=self.ann)
                    self.assertEqual(r.status_code, 415, r.text)
        self.assertEqual(all_files(_env.MEDIA_PATH), before)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)

    def test_offline_upload_503(self):
        m = self.up_ok()
        os.remove(os.path.join(_env.MEDIA_PATH, media.MARKER))
        media.check()
        before = all_files(_env.MEDIA_PATH)
        for data, name, ctype in ((image_bytes(), "a.jpg", "image/jpeg"), (PDF, "a.pdf", "application/pdf")):
            with self.subTest(name=name):
                r = self.up(data, name=name, ctype=ctype, personId=self.ann)
                self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(all_files(_env.MEDIA_PATH), before)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 1)
        # the records keep working; files can't be served; the purge waits
        self.assertEqual(self.listing()["total"], 1)
        self.assertEqual(self.get(f"/api/media/{m['id']}/file").status_code, 503)
        self.ok(self.patch(f"/api/media/{m['id']}", {"title": "Offline edit"}))
        self.ok(self.post(f"/api/media/{m['id']}/links", {"personId": self.bob}), 201)
        self.ok(self.delete(f"/api/media/{m['id']}"))
        self.assertEqual(self.ok(self.delete("/api/trash", user=ADMIN))["purged"]["media"], 0)
        self.assertEqual(all_files(_env.MEDIA_PATH), before)

    def test_size_limit(self):
        set_app_settings(max_upload_mb=1)
        r = self.up(PDF + b"\x00" * (1024 * 1024), name="big.pdf", ctype="application/pdf")
        self.assertEqual(r.status_code, 413, r.text)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)

    def test_undo_upload_and_redo(self):
        m = self.up_ok(personId=self.ann)
        bid = sql("SELECT id FROM batches ORDER BY rowid DESC LIMIT 1")[0]["id"]
        u = self.undo(bid)
        self.assertIsNone(self.media_row(m["id"]))
        self.assertEqual(self.links_of(m["id"]), [])
        self.assertEqual(self.listing()["total"], 0)
        self.undo(u["batchId"])                                   # redo
        self.assertEqual(self.listing()["total"], 1)
        self.assertEqual(len(self.links_of(m["id"])), 1)
        # the files were never removed, so it still shows
        self.assertEqual(self.get(f"/api/media/{m['id']}/file?size=256").status_code, 200)


class Listing(GalleryCase):
    def setUp(self):
        super().setUp()
        self.fid = self.family(self.ann, self.bob)
        self.p1 = self.up_ok(name="Wedding.jpg", familyId=self.fid)
        self.p2 = self.up_ok(name="Ann at school.jpg", personId=self.ann)
        self.d1 = self.up_ok(PDF, name="Deed.pdf", ctype="application/pdf", personId=self.bob)
        self.p3 = self.up_ok(name="Unknown people.jpg")

    def ids(self, **params):
        return [m["id"] for m in self.listing(**params)["items"]]

    def test_newest_first_by_default(self):
        self.assertEqual(self.ids(), [self.p3["id"], self.d1["id"], self.p2["id"], self.p1["id"]])
        self.assertEqual(self.listing()["total"], 4)

    def test_filters(self):
        self.assertEqual(self.ids(kind="document"), [self.d1["id"]])
        self.assertEqual(self.ids(kind="photo"), [self.p3["id"], self.p2["id"], self.p1["id"]])
        self.assertEqual(self.ids(personId=self.ann), [self.p2["id"]])
        self.assertEqual(self.ids(personId=self.bob), [self.d1["id"]])
        self.assertEqual(self.ids(familyId=self.fid), [self.p1["id"]])
        self.assertEqual(self.ids(unlinked="true"), [self.p3["id"]])
        self.assertEqual(self.ids(q="school"), [self.p2["id"]])
        self.assertEqual(self.ids(q="SCHOOL"), [self.p2["id"]])
        self.assertEqual(self.ids(q="deed", kind="photo"), [])
        self.ok(self.patch(f"/api/media/{self.p3['id']}", {"description": "Found in the attic"}))
        self.assertEqual(self.ids(q="attic"), [self.p3["id"]])
        self.assertEqual(self.ids(personId="nobody"), [])

    def test_trashed_media_hidden(self):
        self.ok(self.delete(f"/api/media/{self.p2['id']}"))
        self.assertNotIn(self.p2["id"], self.ids())
        self.assertEqual(self.ids(personId=self.ann), [])
        # but can still be fetched on its own (for the trash) and says so
        self.assertTrue(self.ok(self.get(f"/api/media/{self.p2['id']}"))["deleted"])

    def test_paging(self):
        a = self.listing(page=1, page_size=3)
        b = self.listing(page=2, page_size=3)
        self.assertEqual((a["total"], a["page"], a["pageSize"], len(a["items"])), (4, 1, 3, 3))
        self.assertEqual(len(b["items"]), 1)
        self.assertEqual([m["id"] for m in a["items"] + b["items"]], self.ids())
        self.assertEqual(self.listing(page=3, page_size=3)["items"], [])
        self.assertEqual(self.get("/api/media?page=0").status_code, 422)
        self.assertEqual(self.get("/api/media?page_size=500").status_code, 422)
        for it in a["items"]:
            self.assertNotIn("_sort", it)

    def test_sort_by_date(self):
        dates = {self.p1["id"]: {"d": 5, "m": 6, "y": 1990}, self.p2["id"]: {"y": 1975},
                 self.d1["id"]: {"d": 1, "m": 1, "y": 2001}}
        for mid, d in dates.items():
            self.ok(self.patch(f"/api/media/{mid}", {"date": d}))
        # oldest first; undated last
        self.assertEqual(self.ids(sort="date"), [self.p2["id"], self.p1["id"], self.d1["id"], self.p3["id"]])
        # paging applies after sorting
        self.assertEqual(self.ids(sort="date", page=2, page_size=2), [self.d1["id"], self.p3["id"]])

    def test_family_and_event_link_labels_skip_trashed_people(self):
        self.ok(self.delete(f"/api/people/{self.bob}"))
        m = self.ok(self.get(f"/api/media/{self.p1['id']}"))
        self.assertEqual([l["label"] for l in m["links"]], ["Family of Ann Smith"])
        m = self.ok(self.get(f"/api/media/{self.d1['id']}"))
        self.assertEqual(m["links"], [])

    def test_get_unknown_404(self):
        self.assertEqual(self.get("/api/media/nope").status_code, 404)
        self.assertEqual(self.patch("/api/media/nope", {"title": "x"}).status_code, 404)
        self.assertEqual(self.delete("/api/media/nope").status_code, 404)
        self.assertEqual(self.post("/api/media/nope/links", {"personId": self.ann}).status_code, 404)
        self.assertEqual(self.delete("/api/media/nope/links/x").status_code, 404)


class Details(GalleryCase):
    def setUp(self):
        super().setUp()
        self.m = self.up_ok(personId=self.ann, name="Picnic.jpg")

    def test_patch_title_description_date(self):
        body = self.ok(self.patch(f"/api/media/{self.m['id']}", {
            "title": "  Picnic   by the lake ", "description": "  Line one\nline two  ", "date": {"d": 12, "m": 3, "y": 1950}}))
        m = body["media"]
        self.assertEqual((m["title"], m["description"]), ("Picnic by the lake", "Line one\nline two"))
        self.assertEqual((m["dateText"], m["dateDisplay"]), ("12 MAR 1950", "12 March 1950"))
        self.assertEqual(m["date"]["y"], 1950)
        self.assertNotIn("_sort", m)
        # only what's sent changes; null clears
        m = self.ok(self.patch(f"/api/media/{self.m['id']}", {"title": None}))["media"]
        self.assertIsNone(m["title"])
        self.assertEqual(m["description"], "Line one\nline two")
        m = self.ok(self.patch(f"/api/media/{self.m['id']}", {"date": None}))["media"]
        self.assertIsNone(m["dateText"])
        m = self.ok(self.patch(f"/api/media/{self.m['id']}", {"date": {"d": 1, "m": 2}}))["media"]
        self.assertEqual(m["dateDisplay"], "1 February")

    def test_patch_undo(self):
        bid = self.ok(self.patch(f"/api/media/{self.m['id']}", {"title": "New", "date": {"y": 1990}}))["batchId"]
        self.undo(bid)
        m = self.ok(self.get(f"/api/media/{self.m['id']}"))
        self.assertEqual((m["title"], m["dateText"]), ("Picnic", None))

    def test_patch_validation(self):
        mid = self.m["id"]
        for body in ({"date": {"d": 31, "m": 2, "y": 1950}}, {"date": {"d": 5}}, {"date": {"m": 13, "y": 1950}},
                     {"date": {"qual": "sometime", "y": 1950}}, {"title": "x" * 201}, {"description": "x" * 5001},
                     {"kind": "document"}, {"sha256": "0"}):
            with self.subTest(body=body):
                self.assertEqual(self.patch(f"/api/media/{mid}", body).status_code, 422)
        self.assertEqual(self.media_row(mid)["title"], "Picnic")

    def test_patch_trashed_404(self):
        self.ok(self.delete(f"/api/media/{self.m['id']}"))
        self.assertEqual(self.patch(f"/api/media/{self.m['id']}", {"title": "x"}).status_code, 404)

    def test_exif_date_becomes_the_date(self):
        from PIL import Image
        import io
        img = Image.new("RGB", (40, 30), (1, 2, 3))
        exif = Image.Exif()
        exif.get_ifd(0x8769)[36867] = "1999:12:31 10:00:00"
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif)
        m = self.up_ok(buf.getvalue())
        self.assertEqual(m["dateDisplay"], "31 December 1999")


class Links(GalleryCase):
    def setUp(self):
        super().setUp()
        self.m = self.up_ok(personId=self.ann)
        self.mid = self.m["id"]

    def test_add_duplicate_remove(self):
        body = self.ok(self.post(f"/api/media/{self.mid}/links", {"personId": self.bob}), 201)
        self.assertIn("batchId", body)
        self.assertEqual(sorted(l["label"] for l in body["media"]["links"]), ["Ann Smith", "Bob Smith"])
        self.assertEqual(sql("SELECT label FROM batches ORDER BY rowid DESC LIMIT 1")[0]["label"], "Tagged Bob Smith in a photo")
        r = self.post(f"/api/media/{self.mid}/links", {"personId": self.bob})
        self.assertEqual(r.status_code, 409)
        link = next(l for l in body["media"]["links"] if l["personId"] == self.bob)
        body = self.ok(self.delete(f"/api/media/{self.mid}/links/{link['id']}"))
        self.assertEqual([l["personId"] for l in body["media"]["links"]], [self.ann])
        self.assertEqual(self.delete(f"/api/media/{self.mid}/links/{link['id']}").status_code, 404)

    def test_link_validation(self):
        fid = self.family(self.ann, self.bob)
        for body, status in (({}, 422), ({"personId": self.bob, "familyId": fid}, 422), ({"personId": "nope"}, 404),
                             ({"familyId": "nope"}, 404), ({"eventId": "nope"}, 404), ({"other": "x"}, 422)):
            with self.subTest(body=body):
                self.assertEqual(self.post(f"/api/media/{self.mid}/links", body).status_code, status)
        self.ok(self.post(f"/api/media/{self.mid}/links", {"familyId": fid}), 201)
        self.assertEqual(self.post(f"/api/media/{self.mid}/links", {"familyId": fid}).status_code, 409)

    def test_link_on_trashed_media_404(self):
        self.ok(self.delete(f"/api/media/{self.mid}"))
        self.assertEqual(self.post(f"/api/media/{self.mid}/links", {"personId": self.bob}).status_code, 404)

    def test_link_id_must_belong_to_the_media(self):
        other = self.up_ok(personId=self.bob)
        lid = other["links"][0]["id"]
        self.assertEqual(self.delete(f"/api/media/{self.mid}/links/{lid}").status_code, 404)
        self.assertEqual(len(self.links_of(other["id"])), 1)

    def test_removing_profile_link_clears_profile_and_undo_restores(self):
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid}))
        self.assertEqual(self.detail(self.ann)["photo"], self.mid)
        self.assertEqual(self.ok(self.get(f"/api/media/{self.mid}"))["profileOf"], [self.ann])
        lid = self.m["links"][0]["id"]
        body = self.ok(self.delete(f"/api/media/{self.mid}/links/{lid}"))
        self.assertEqual(body["media"]["profileOf"], [])
        self.assertIsNone(self.detail(self.ann)["photo"])
        self.assertEqual(sql("SELECT label FROM batches ORDER BY rowid DESC LIMIT 1")[0]["label"], "Untagged Ann Smith from a photo")
        self.undo(body["batchId"])
        self.assertEqual(self.detail(self.ann)["photo"], self.mid)
        self.assertEqual([l["person_id"] for l in self.links_of(self.mid)], [self.ann])
        self.assertEqual(self.links_of(self.mid)[0]["id"], lid)

    def test_removing_link_of_someone_else_keeps_profile(self):
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid}))
        body = self.ok(self.post(f"/api/media/{self.mid}/links", {"personId": self.bob}), 201)
        lid = next(l["id"] for l in body["media"]["links"] if l["personId"] == self.bob)
        self.ok(self.delete(f"/api/media/{self.mid}/links/{lid}"))
        self.assertEqual(self.detail(self.ann)["photo"], self.mid)

    def test_undo_add_link(self):
        bid = self.ok(self.post(f"/api/media/{self.mid}/links", {"personId": self.bob}), 201)["batchId"]
        self.undo(bid)
        self.assertEqual([l["person_id"] for l in self.links_of(self.mid)], [self.ann])

    def test_undo_add_link_refused_once_it_became_their_profile_photo(self):
        body = self.ok(self.post(f"/api/media/{self.mid}/links", {"personId": self.bob}), 201)
        self.ok(self.put(f"/api/people/{self.bob}/photo", {"mediaId": self.mid}))
        r = self.post(f"/api/history/{body['batchId']}/undo")
        self.assertEqual(r.status_code, 409, r.text)
        # still consistent: Bob's profile photo is one of Bob's photos
        self.assertEqual(self.detail(self.bob)["photo"], self.mid)
        self.assertIn(self.bob, [l["person_id"] for l in self.links_of(self.mid)])

    def test_tagging_touches_person_history(self):
        self.ok(self.post(f"/api/media/{self.mid}/links", {"personId": self.bob}), 201)
        items = self.ok(self.get(f"/api/history?personId={self.bob}"))["items"]
        self.assertEqual(items[0]["label"], "Tagged Bob Smith in a photo")


class ProfileChoice(GalleryCase):
    def test_choose_existing_photo_links_it(self):
        m = self.up_ok(personId=self.ann)
        body = self.ok(self.put(f"/api/people/{self.bob}/photo", {"mediaId": m["id"]}))
        self.assertEqual(body["person"]["photo"], m["id"])
        self.assertIn("batchId", body)
        self.assertEqual(sorted(l["person_id"] for l in self.links_of(m["id"])), sorted([self.ann, self.bob]))
        self.assertEqual(self.detail(self.bob)["photoCount"], 1)
        # choosing it again doesn't add a second link
        self.ok(self.put(f"/api/people/{self.bob}/photo", {"mediaId": m["id"]}))
        self.assertEqual(len(self.links_of(m["id"])), 2)
        self.assertEqual(sorted(self.ok(self.get(f"/api/media/{m['id']}"))["profileOf"]), [self.bob])

    def test_undo_choice_removes_link_and_profile(self):
        m = self.up_ok()
        body = self.ok(self.put(f"/api/people/{self.bob}/photo", {"mediaId": m["id"]}))
        u = self.undo(body["batchId"])
        self.assertIsNone(self.detail(self.bob)["photo"])
        self.assertEqual(self.links_of(m["id"]), [])
        self.undo(u["batchId"])                                   # redo
        self.assertEqual(self.detail(self.bob)["photo"], m["id"])
        self.assertEqual([l["person_id"] for l in self.links_of(m["id"])], [self.bob])

    def test_documents_refused(self):
        d = self.up_ok(PDF, name="x.pdf", ctype="application/pdf", personId=self.ann)
        r = self.put(f"/api/people/{self.ann}/photo", {"mediaId": d["id"]})
        self.assertEqual(r.status_code, 422)
        self.assertIsNone(self.detail(self.ann)["photo"])

    def test_unknown_trashed_and_bad_body(self):
        m = self.up_ok()
        self.assertEqual(self.put(f"/api/people/{self.ann}/photo", {"mediaId": "nope"}).status_code, 404)
        self.assertEqual(self.put("/api/people/nope/photo", {"mediaId": m["id"]}).status_code, 404)
        self.assertEqual(self.put(f"/api/people/{self.ann}/photo", {}).status_code, 422)
        self.assertEqual(self.put(f"/api/people/{self.ann}/photo", {"mediaId": m["id"], "x": 1}).status_code, 422)
        self.ok(self.delete(f"/api/media/{m['id']}"))
        self.assertEqual(self.put(f"/api/people/{self.ann}/photo", {"mediaId": m["id"]}).status_code, 404)
        self.assertEqual(self.links_of(m["id"]), [])

    def test_link_limit_applies_when_choosing(self):
        media_router.MAX_LINKS_PER_PERSON, old = 1, media_router.MAX_LINKS_PER_PERSON
        self.addCleanup(setattr, media_router, "MAX_LINKS_PER_PERSON", old)
        self.up_ok(personId=self.ann)
        other = self.up_ok()
        self.assertEqual(self.put(f"/api/people/{self.ann}/photo", {"mediaId": other["id"]}).status_code, 422)

    def test_replace_and_clear_keep_the_photo(self):
        m1 = self.ok(self.post(f"/api/people/{self.ann}/photo", files={"file": ("a.jpg", image_bytes(), "image/jpeg")}), 201)["mediaId"]
        m2 = self.ok(self.post(f"/api/people/{self.ann}/photo", files={"file": ("b.jpg", image_bytes(color=(0, 9, 0)), "image/jpeg")}), 201)["mediaId"]
        self.assertEqual(self.detail(self.ann)["photo"], m2)
        self.ok(self.delete(f"/api/people/{self.ann}/photo"))
        self.assertIsNone(self.detail(self.ann)["photo"])
        ids = sorted(x["id"] for x in self.listing(personId=self.ann)["items"])
        self.assertEqual(ids, sorted([m1, m2]))
        self.assertEqual(self.detail(self.ann)["photoCount"], 2)


class TrashAndDelete(GalleryCase):
    def test_delete_to_trash_clears_profile_and_restore(self):
        m = self.up_ok(personId=self.ann)
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": m["id"]}))
        body = self.ok(self.delete(f"/api/media/{m['id']}"))
        self.assertIn("batchId", body)
        self.assertIsNotNone(self.media_row(m["id"])["deleted_at"])
        self.assertIsNone(self.detail(self.ann)["photo"])
        self.assertEqual(self.detail(self.ann)["photoCount"], 0)
        self.assertEqual(self.delete(f"/api/media/{m['id']}").status_code, 404)
        trash = self.ok(self.get("/api/trash", user=ADMIN))["items"]
        self.assertEqual([(t["entity"], t["id"]) for t in trash], [("media", m["id"])])
        self.ok(self.post(f"/api/trash/media/{m['id']}/restore", user=ADMIN))
        self.assertIsNone(self.media_row(m["id"])["deleted_at"])
        self.assertEqual(self.listing()["total"], 1)
        self.assertEqual(self.post(f"/api/trash/media/{m['id']}/restore", user=ADMIN).status_code, 409)
        # the links survived the trip
        self.assertEqual([l["person_id"] for l in self.links_of(m["id"])], [self.ann])

    def test_undo_delete_restores_profile(self):
        m = self.up_ok(personId=self.ann)
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": m["id"]}))
        body = self.ok(self.delete(f"/api/media/{m['id']}"))
        self.undo(body["batchId"])
        self.assertIsNone(self.media_row(m["id"])["deleted_at"])
        self.assertEqual(self.detail(self.ann)["photo"], m["id"])

    def test_document_trash_label(self):
        d = self.up_ok(PDF, name="Will.pdf", ctype="application/pdf")
        self.ok(self.delete(f"/api/media/{d['id']}"))
        self.assertEqual(sql("SELECT label FROM batches ORDER BY rowid DESC LIMIT 1")[0]["label"], "Deleted a document")
        item = self.ok(self.get("/api/trash", user=ADMIN))["items"][0]
        self.assertEqual((item["name"], item["kind"]), ("Will", "document"))
        self.ok(self.post(f"/api/trash/media/{d['id']}/restore", user=ADMIN))
        self.ok(self.patch(f"/api/media/{d['id']}", {"title": None}))
        self.ok(self.delete(f"/api/media/{d['id']}"))
        self.assertEqual(self.ok(self.get("/api/trash", user=ADMIN))["items"][0]["name"], "Document")

    def test_purge_removes_files(self):
        m = self.up_ok(PDF, name="x.pdf", ctype="application/pdf")
        self.ok(self.delete(f"/api/media/{m['id']}"))
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assertIsNone(self.media_row(m["id"]))
        self.assertFalse(os.path.exists(os.path.dirname(media.file_path(m["id"]))))

    def test_purged_person_photo_shared_with_someone_else_survives(self):
        m = self.up_ok(personId=self.ann)
        self.ok(self.post(f"/api/media/{m['id']}/links", {"personId": self.bob}), 201)
        self.ok(self.delete(f"/api/people/{self.ann}"))
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assertIsNone(self.media_row(m["id"])["deleted_at"])
        self.assertEqual([l["person_id"] for l in self.links_of(m["id"])], [self.bob])


class Counts(GalleryCase):
    def test_person_detail_counts(self):
        d = self.detail(self.ann)
        self.assertEqual((d["photoCount"], d["documentCount"], d["storyCount"]), (0, 0, 0))
        self.up_ok(personId=self.ann)
        m = self.up_ok(personId=self.ann)
        self.up_ok(PDF, name="x.pdf", ctype="application/pdf", personId=self.ann)
        self.up_ok(personId=self.bob)
        self.ok(self.post(f"/api/people/{self.ann}/stories", {"title": "T", "body": "B"}), 201)
        d = self.detail(self.ann)
        self.assertEqual((d["photoCount"], d["documentCount"], d["storyCount"]), (2, 1, 1))
        self.ok(self.delete(f"/api/media/{m['id']}"))
        self.assertEqual(self.detail(self.ann)["photoCount"], 1)

    def test_counts_match_the_photos_tab(self):
        # photos of a person's events show on their Photos tab, so they count too
        eid = self.ok(self.post("/api/events", {"personId": self.ann, "type": "education", "title": "School"}), 201)["event"]["id"]
        self.up_ok(eventId=eid)
        self.up_ok(personId=self.ann)
        d = self.detail(self.ann)
        tab = self.listing(personId=self.ann)
        self.assertEqual(d["photoCount"] + d["documentCount"], tab["total"])


class Permissions(GalleryCase):
    def test_every_new_route_needs_an_enabled_user(self):
        m = self.up_ok(personId=self.ann)
        sid = self.ok(self.post(f"/api/people/{self.ann}/stories", {"title": "T", "body": "B"}), 201)["story"]["id"]
        lid = m["links"][0]["id"]
        self.ok(self.patch(f"/api/users/{ALICE['id']}", {"disabled": True}, user=ADMIN))
        routes = [
            ("GET", "/api/media", {}), ("GET", f"/api/media/{m['id']}", {}),
            ("GET", f"/api/media/{m['id']}/file", {}),
            ("POST", "/api/media", {"files": {"file": ("a.jpg", image_bytes(), "image/jpeg")}}),
            ("PATCH", f"/api/media/{m['id']}", {"json": {"title": "x"}}),
            ("DELETE", f"/api/media/{m['id']}", {}),
            ("POST", f"/api/media/{m['id']}/links", {"json": {"personId": self.bob}}),
            ("DELETE", f"/api/media/{m['id']}/links/{lid}", {}),
            ("PUT", f"/api/people/{self.ann}/photo", {"json": {"mediaId": m["id"]}}),
            ("GET", f"/api/people/{self.ann}/stories", {}),
            ("POST", f"/api/people/{self.ann}/stories", {"json": {"title": "T", "body": "B"}}),
            ("PATCH", f"/api/stories/{sid}", {"json": {"title": "x"}}),
            ("DELETE", f"/api/stories/{sid}", {}),
            ("GET", "/api/upcoming", {}), ("GET", "/api/tree/all", {}),
        ]
        for method, path, kw in routes:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.req(method, path, ALICE, **kw).status_code, 403)
                self.assertEqual(self.client.request(method, path, **kw).status_code, 401)
        # nothing changed
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 1)
        self.assertEqual(sql("SELECT title FROM stories")[0]["title"], "T")
        self.assertEqual(len(self.links_of(m["id"])), 1)
        # and other users are fine
        self.ok(self.get("/api/media", user=BOB))


if __name__ == "__main__":
    unittest.main()
