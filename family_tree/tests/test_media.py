"""Profile photos: upload pipeline, magic-byte checks, EXIF stripping, thumbnails,
serving (§4), and media storage on /share incl. the offline rules (§5.1)."""
from base import ADMIN, ALICE, ApiTestCase, addon_version, image_bytes, reset_state, set_app_settings, sql
import _env

import io
import os
import unittest

from PIL import Image

from app import config, db, media


def all_files(root):
    out = set()
    for dirpath, dirs, files in os.walk(root):
        for d in dirs:
            out.add(os.path.relpath(os.path.join(dirpath, d), root) + "/")
        for f in files:
            out.add(os.path.relpath(os.path.join(dirpath, f), root))
    return out


def exif_jpeg(size=(200, 100), orientation=6):
    img = Image.new("RGB", size, (10, 120, 200))
    # left half red so the rotation can be checked
    for x in range(size[0] // 2):
        for y in range(size[1]):
            img.putpixel((x, y), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "SpyCam"
    exif.get_ifd(0x8769)[36867] = "2001:02:03 04:05:06"
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (40.0, 26.0, 46.0)
    gps[3] = "W"
    gps[4] = (79.0, 58.0, 56.0)
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=95)
    return buf.getvalue()


class MediaCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.person("Ann", "Smith")

    def upload(self, data, name="photo.jpg", ctype="image/jpeg", pid=None, user=None):
        return self.post(f"/api/people/{pid or self.pid}/photo", files={"file": (name, data, ctype)}, user=user)

    def upload_ok(self, data=None, **kw):
        body = self.ok(self.upload(data or image_bytes(), **kw), 201)
        self.assertIn("batchId", body)
        return body["mediaId"]


class Upload(MediaCase):
    def test_upload_jpeg_profile_photo(self):
        body = self.ok(self.upload(image_bytes(size=(300, 200))), 201)
        mid = body["mediaId"]
        self.assertEqual(body["person"]["photo"], mid)
        row = sql("SELECT * FROM media WHERE id = ?", (mid,))[0]
        self.assertEqual((row["kind"], row["content_type"], row["width"], row["height"]), ("photo", "image/jpeg", 300, 200))
        self.assertEqual(row["title"], "Photo of Ann Smith")
        self.assertEqual(row["created_by"], ALICE["id"])
        self.assertEqual(len(row["sha256"]), 64)
        links = sql("SELECT * FROM media_links WHERE media_id = ?", (mid,))
        self.assertEqual([l["person_id"] for l in links], [self.pid])
        d = os.path.join(_env.MEDIA_PATH, "media", mid[:2], mid)
        self.assertEqual(sorted(os.listdir(d)), ["original", "thumb1024.jpg", "thumb256.jpg"])
        self.assertEqual(os.path.getsize(os.path.join(d, "original")), row["size"])
        # the list and tree cards carry the photo id
        self.assertEqual(self.ok(self.get("/api/people"))["items"][0]["photo"], mid)

    def test_png_named_jpg_is_fine(self):
        mid = self.upload_ok(image_bytes("PNG"), name="holiday.jpg")
        self.assertEqual(sql("SELECT content_type FROM media WHERE id = ?", (mid,))[0]["content_type"], "image/jpeg")

    def test_transparent_png_stays_png(self):
        mid = self.upload_ok(image_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0)), name="x.png", ctype="image/png")
        self.assertEqual(sql("SELECT content_type FROM media WHERE id = ?", (mid,))[0]["content_type"], "image/png")
        with open(media.file_path(mid), "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        with open(media.file_path(mid, "256"), "rb") as f:
            self.assertEqual(f.read(3), b"\xff\xd8\xff")

    def test_webp_and_gif(self):
        self.upload_ok(image_bytes("WEBP"), name="a.webp", ctype="image/webp")
        frames = [Image.new("P", (20, 20), i) for i in range(3)]
        buf = io.BytesIO()
        frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
        self.upload_ok(buf.getvalue(), name="a.gif", ctype="image/gif")

    def test_rejected_types(self):
        cases = {
            "svg": (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', 415),
            "html": (b"<!DOCTYPE html><html><script>alert(1)</script></html>", 415),
            "text": (b"just some text, honest it's a jpeg", 415),
            "pdf": (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\n", 415),
            "heic": (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64, 415),
            "empty": (b"", 415),
            "broken jpeg": (b"\xff\xd8\xff\xe0" + b"garbage" * 20, 415),
        }
        before = all_files(_env.MEDIA_PATH)
        for what, (data, status) in cases.items():
            with self.subTest(what=what):
                r = self.upload(data, name="photo.jpg")
                self.assertEqual(r.status_code, status, r.text)
                if what == "heic":
                    self.assertIn("HEIC", r.json()["detail"])
                if what == "pdf":
                    self.assertIn("Only photos", r.json()["detail"])
        self.assertEqual(all_files(_env.MEDIA_PATH), before)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)
        self.assertIsNone(self.detail(self.pid)["photo"])

    def test_sniff(self):
        self.assertEqual(media.sniff(image_bytes("JPEG")), "image/jpeg")
        self.assertEqual(media.sniff(image_bytes("PNG")), "image/png")
        self.assertEqual(media.sniff(image_bytes("WEBP")), "image/webp")
        self.assertEqual(media.sniff(b"GIF89a....."), "image/gif")
        self.assertEqual(media.sniff(b"%PDF-1.7"), "application/pdf")
        self.assertEqual(media.sniff(b"\x00\x00\x00\x18ftypheic...."), "image/heic")
        self.assertIsNone(media.sniff(b"<svg"))
        self.assertEqual(media.validate_upload(b"%PDF-1.7 ...", allow_documents=True), "application/pdf")

    def test_size_limit_413(self):
        set_app_settings(max_upload_mb=1)
        big = b"\xff\xd8\xff\xe0" + b"\x00" * (1024 * 1024)
        r = self.upload(big)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("1 MB", r.json()["detail"])
        # exactly at the limit is let through to the decoder (and then fails as a broken image)
        r = self.upload(b"\xff\xd8\xff\xe0" + b"\x00" * (1024 * 1024 - 4))
        self.assertEqual(r.status_code, 415, r.text)

    def test_decompression_bomb_413(self):
        data = image_bytes("PNG", size=(12000, 12000), mode="1", color=0)
        r = self.upload(data, name="bomb.png")
        self.assertEqual(r.status_code, 413, r.text)

    def test_exif_gps_stripped_orientation_applied_date_kept(self):
        data = exif_jpeg()
        self.assertIn(b"SpyCam", data)
        mid = self.upload_ok(data)
        row = sql("SELECT * FROM media WHERE id = ?", (mid,))[0]
        self.assertEqual(row["date_text"], "3 FEB 2001")
        self.assertEqual((row["width"], row["height"]), (100, 200))       # rotated 90°
        with open(media.file_path(mid), "rb") as f:
            stored = f.read()
        self.assertNotIn(b"SpyCam", stored)
        img = Image.open(io.BytesIO(stored))
        self.assertEqual(img.size, (100, 200))
        exif = img.getexif()
        self.assertNotIn(0x8825, exif)
        self.assertEqual(dict(exif.get_ifd(0x8825)), {})
        self.assertNotIn(0x0112, exif)                                     # no orientation left to re-apply
        # orientation 6 = rotate 90° clockwise: the red left half ends up on top
        top, bottom = img.convert("RGB").getpixel((50, 20)), img.convert("RGB").getpixel((50, 180))
        self.assertGreater(top[0], 200)
        self.assertLess(bottom[0], 60)
        for size in ("256", "1024"):
            with open(media.file_path(mid, size), "rb") as f:
                t = f.read()
            self.assertNotIn(b"SpyCam", t)
            self.assertNotIn(0x8825, Image.open(io.BytesIO(t)).getexif())

    def test_thumbnails_and_max_edge(self):
        mid = self.upload_ok(image_bytes(size=(5000, 2000)))
        sizes = {}
        for s in ("original", "1024", "256"):
            with Image.open(media.file_path(mid, s)) as im:
                sizes[s] = im.size
        self.assertEqual(sizes["original"], (4096, 1638))
        for s, edge in (("1024", 1024), ("256", 256)):
            w, h = sizes[s]
            self.assertEqual(w, edge)
            self.assertAlmostEqual(h, edge * 2000 / 5000, delta=1)
        row = sql("SELECT width, height FROM media WHERE id = ?", (mid,))[0]
        self.assertEqual((row["width"], row["height"]), (4096, 1638))

    def test_serving(self):
        mid = self.upload_ok(image_bytes(size=(600, 300)))
        for size, edge in (("256", 256), ("1024", 600), ("original", 600)):
            with self.subTest(size=size):
                r = self.get(f"/api/media/{mid}/file?size={size}")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["content-type"], "image/jpeg")
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
                self.assertEqual(r.headers["content-disposition"], "inline")
                self.assertEqual(max(Image.open(io.BytesIO(r.content)).size), edge)
        self.assertEqual(self.get(f"/api/media/{mid}/file").status_code, 200)            # default 1024
        self.assertEqual(self.get(f"/api/media/{mid}/file?size=2048").status_code, 422)
        self.assertEqual(self.get(f"/api/media/{mid}/file?size=../../etc/passwd").status_code, 422)
        self.assertEqual(self.get("/api/media/nope/file").status_code, 404)
        self.assertEqual(self.client.get(f"/api/media/{mid}/file").status_code, 401)        # no user header

    def test_served_png_has_png_type(self):
        mid = self.upload_ok(image_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0)), name="x.png")
        r = self.get(f"/api/media/{mid}/file?size=original")
        self.assertEqual(r.headers["content-type"], "image/png")
        r = self.get(f"/api/media/{mid}/file?size=256")
        self.assertEqual(r.headers["content-type"], "image/jpeg")

    def test_missing_file_404(self):
        mid = self.upload_ok()
        os.remove(media.file_path(mid, "256"))
        self.assertEqual(self.get(f"/api/media/{mid}/file?size=256").status_code, 404)

    def test_replace_keeps_old_photo_in_gallery(self):
        m1 = self.upload_ok()
        body = self.ok(self.upload(image_bytes(color=(0, 255, 0))), 201)
        m2 = body["mediaId"]
        self.assertEqual(body["person"]["photo"], m2)
        self.assertIsNone(sql("SELECT deleted_at FROM media WHERE id = ?", (m1,))[0]["deleted_at"])
        ids = [m["id"] for m in self.ok(self.get(f"/api/media?personId={self.pid}"))["items"]]
        self.assertEqual(sorted(ids), sorted([m1, m2]))
        # and the old one can be chosen again
        self.ok(self.put(f"/api/people/{self.pid}/photo", json={"mediaId": m1}))
        self.assertEqual(self.detail(self.pid)["photo"], m1)

    def test_delete_photo(self):
        mid = self.upload_ok()
        body = self.ok(self.delete(f"/api/people/{self.pid}/photo"))
        self.assertIn("batchId", body)
        self.assertIsNone(body["person"]["photo"])
        # only the profile choice goes; the photo stays in their photos
        self.assertIsNone(sql("SELECT deleted_at FROM media WHERE id = ?", (mid,))[0]["deleted_at"])
        self.assertEqual(self.delete(f"/api/people/{self.pid}/photo").status_code, 404)
        # undo puts it back
        self.ok(self.post(f"/api/history/{body['batchId']}/undo"))
        self.assertEqual(self.detail(self.pid)["photo"], mid)

    def test_undo_upload(self):
        m1 = self.upload_ok()
        bid = self.ok(self.upload(image_bytes(color=(0, 0, 255))), 201)["batchId"]
        self.ok(self.post(f"/api/history/{bid}/undo"))
        self.assertEqual(self.detail(self.pid)["photo"], m1)
        self.assertIsNone(sql("SELECT deleted_at FROM media WHERE id = ?", (m1,))[0]["deleted_at"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 1)

    def test_upload_for_unknown_or_deleted_person(self):
        before = all_files(_env.MEDIA_PATH)
        self.assertEqual(self.upload(image_bytes(), pid="nope").status_code, 404)
        self.ok(self.delete(f"/api/people/{self.pid}"))
        self.assertEqual(self.upload(image_bytes()).status_code, 404)
        self.assertEqual(all_files(_env.MEDIA_PATH), before)

    def test_purge_trashed_media_removes_files(self):
        m1 = self.upload_ok()
        self.upload_ok(image_bytes(color=(1, 2, 3)))
        self.assertTrue(os.path.isdir(os.path.dirname(media.file_path(m1))))
        self.ok(self.delete(f"/api/media/{m1}"))
        purged = self.ok(self.delete("/api/trash", user=ADMIN))["purged"]
        self.assertEqual(purged["media"], 1)
        self.assertFalse(os.path.exists(os.path.dirname(media.file_path(m1))))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 1)

    def test_purge_person_with_photo(self):
        # §5: "A daily purge removes them after trash_days, along with their media files."
        mid = self.upload_ok()
        self.ok(self.delete(f"/api/people/{self.pid}"))
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media_links")[0]["n"], 0)
        # the photo only this person used must not linger as an invisible, unlinked row + files
        self.assertEqual(sql("SELECT id FROM media WHERE deleted_at IS NULL"), [])
        self.ok(self.delete("/api/trash", user=ADMIN))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media")[0]["n"], 0)
        self.assertFalse(os.path.exists(os.path.dirname(media.file_path(mid))))

    def test_admin_media_check_and_orphans(self):
        mid = self.upload_ok()
        self.assertEqual(self.ok(self.post("/api/admin/media/check", user=ADMIN)), {"missing": [], "orphans": []})
        orphan = "ab" + "0" * 30
        os.makedirs(os.path.join(_env.MEDIA_PATH, "media", "ab", orphan))
        os.remove(media.file_path(mid))
        self.assertEqual(self.ok(self.post("/api/admin/media/check", user=ADMIN)), {"missing": [mid], "orphans": [orphan]})
        self.assertEqual(self.ok(self.post("/api/admin/media/orphans", {"ids": [orphan, mid, "../x"]}, user=ADMIN)),
                         {"moved": 1})
        self.assertTrue(os.path.isdir(os.path.join(_env.MEDIA_PATH, "orphans", orphan)))
        self.assertEqual(self.post("/api/admin/media/check").status_code, 403)


class Offline(MediaCase):
    def marker(self):
        return os.path.join(_env.MEDIA_PATH, media.MARKER)

    def test_first_setup_writes_marker_and_store_id(self):
        with open(self.marker()) as f:
            mark = f.read().strip()
        with db.get_conn() as c:
            self.assertEqual(db.get_setting(c, "media_store_id"), mark)
        self.assertTrue(media.is_online())
        s = self.ok(self.get("/api/admin/storage", user=ADMIN))
        self.assertTrue(s["online"])
        self.assertFalse(s["foreignMarker"])
        self.assertEqual(s["path"], _env.MEDIA_PATH)
        self.assertEqual(s["appVersion"], addon_version())

    def test_marker_missing_goes_offline(self):
        mid = self.upload_ok()
        os.remove(self.marker())
        st = media.check()
        self.assertFalse(st["online"])
        self.assertIn("marker", st["reason"])
        before = all_files(_env.MEDIA_PATH)
        r = self.upload(image_bytes())
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(self.get(f"/api/media/{mid}/file").status_code, 503)
        self.assertEqual(self.get("/api/admin-storage-download-db?media=1", user=ADMIN).status_code, 503)
        self.assertEqual(self.post("/api/admin/media/check", user=ADMIN).status_code, 503)
        # purging trashed media waits while offline
        self.ok(self.delete(f"/api/people/{self.pid}/photo"))
        self.assertEqual(self.ok(self.delete("/api/trash", user=ADMIN))["purged"]["media"], 0)
        self.assertEqual(all_files(_env.MEDIA_PATH), before)
        self.assertFalse(os.path.exists(self.marker()))                  # never re-created
        # the tree itself keeps working
        self.assertEqual(self.detail(self.pid)["name"], "Ann Smith")
        s = self.ok(self.get("/api/admin/storage", user=ADMIN))
        self.assertFalse(s["online"])
        self.assertFalse(s["foreignMarker"])

    def test_folder_gone_stays_offline_and_is_not_recreated(self):
        import shutil
        shutil.rmtree(_env.MEDIA_PATH)
        self.assertFalse(media.check()["online"])
        self.assertEqual(self.upload(image_bytes()).status_code, 503)
        self.assertFalse(os.path.exists(_env.MEDIA_PATH))

    def test_marker_back_goes_online_again(self):
        with open(self.marker()) as f:
            mark = f.read()
        os.remove(self.marker())
        self.assertFalse(media.check()["online"])
        with open(self.marker(), "w") as f:
            f.write(mark)
        # an upload re-checks by itself
        self.upload_ok()

    def test_foreign_marker_and_adopt(self):
        with open(self.marker(), "w") as f:
            f.write("someone-elses-store\n")
        st = media.check()
        self.assertFalse(st["online"])
        self.assertEqual(st["foreign_id"], "someone-elses-store")
        s = self.ok(self.get("/api/admin/storage", user=ADMIN))
        self.assertFalse(s["online"])
        self.assertTrue(s["foreignMarker"])
        self.assertEqual(self.upload(image_bytes()).status_code, 503)
        self.assertEqual(self.post("/api/admin/media/adopt", user=ALICE).status_code, 403)
        st = self.ok(self.post("/api/admin/media/adopt", user=ADMIN))
        self.assertTrue(st["online"])
        with db.get_conn() as c:
            self.assertEqual(db.get_setting(c, "media_store_id"), "someone-elses-store")
        self.upload_ok()

    def test_adopt_without_marker_409(self):
        os.remove(self.marker())
        media.check()
        self.assertEqual(self.post("/api/admin/media/adopt", user=ADMIN).status_code, 409)

    def test_path_outside_share_rejected(self):
        config.ALLOW_ANY_MEDIA_PATH = False
        st = media.check()
        self.assertFalse(st["online"])
        self.assertIn("/share", st["reason"])
        self.assertEqual(self.upload(image_bytes()).status_code, 503)
        self.assertTrue(media.path_allowed("/share"))
        self.assertTrue(media.path_allowed("/share/nas/family_tree"))
        self.assertFalse(media.path_allowed("/sharepoint/x"))
        self.assertFalse(media.path_allowed("/data/media"))

    def test_first_start_with_existing_foreign_marker(self):
        reset_state(setup_media=False)
        os.makedirs(_env.MEDIA_PATH)
        with open(self.marker(), "w") as f:
            f.write("old-tree\n")
        st = media.check()
        self.assertFalse(st["online"])
        self.assertEqual(st["foreign_id"], "old-tree")
        with db.get_conn() as c:
            self.assertIsNone(db.get_setting(c, "media_store_id"))

    def test_first_start_missing_parent_folder(self):
        reset_state(setup_media=False)
        target = os.path.join(_env.TMP, "elsewhere", "nas", "family_tree")
        set_app_settings(media_path=target)
        st = media.check()
        self.assertFalse(st["online"])
        self.assertIn("doesn't exist", st["reason"])
        self.assertFalse(os.path.exists(os.path.dirname(target)))
        with db.get_conn() as c:
            self.assertIsNone(db.get_setting(c, "media_store_id"))
        # once the mount appears, setup succeeds
        os.makedirs(os.path.dirname(target))
        self.assertTrue(media.check()["online"])
        self.assertTrue(os.path.isfile(os.path.join(target, media.MARKER)))



class Hardening(MediaCase):
    def test_pixel_limit_checked_before_decoding(self):
        # Pillow itself only errors above twice MAX_IMAGE_PIXELS; we refuse anything above it
        orig = media.MAX_PIXELS
        media.MAX_PIXELS = 50 * 50
        try:
            r = self.upload(image_bytes("PNG", size=(60, 60)), name="big.png")
            self.assertEqual(r.status_code, 413, r.text)
            self.upload_ok(image_bytes("PNG", size=(50, 50)))
        finally:
            media.MAX_PIXELS = orig

    def test_oversized_body_refused_before_parsing(self):
        set_app_settings(max_upload_mb=1)
        big = b"\xff\xd8\xff\xe0" + b"\x00" * (3 * 1024 * 1024)
        r = self.upload(big)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("1 MB", r.json()["detail"])
        r = self.post("/api/people", {"given_names": "x" * 10, "biography": "y" * (3 * 1024 * 1024)})
        self.assertEqual(r.status_code, 413)

    def test_invalid_media_ids_never_become_paths(self):
        self.assertEqual(self.get("/api/media/..%2F..%2Fx/file").status_code, 404)
        self.assertEqual(self.get("/api/media/" + "A" * 32 + "/file").status_code, 404)
        with self.assertRaises(ValueError):
            media.file_path("../..")
        outside = os.path.join(_env.SHARE_DIR, "keep")
        os.makedirs(outside, exist_ok=True)
        media.remove_files("../..")                      # a no-op, never an rmtree outside media/
        self.assertTrue(os.path.isdir(outside))


if __name__ == "__main__":
    unittest.main()
