import io
import os

from base import ADMIN, NISHA, TARUN, ApiTestCase, sql

from app import config, db, files, housekeeping


def jpeg(w, h, gps=False):
    from PIL import Image
    im = Image.new("RGB", (w, h), (200, 30, 30))
    out = io.BytesIO()
    kw = {}
    if gps:
        exif = Image.Exif()
        exif[0x0112] = 6                       # rotate 90°
        exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0)}
        kw["exif"] = exif
    im.save(out, "JPEG", **kw)
    return out.getvalue()


class FileTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_upload_send_serve(self):
        up = self.ok(self.upload(self.hh, "Lease.pdf", b"%PDF-1.4 lease", NISHA), 201)
        self.assertEqual(up["mime"], "application/pdf")
        # pending: only the uploader can fetch it
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).status_code, 404)
        m = self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        self.assertEqual(m["kind"], "file")
        r = self.get(f"/api/files/{up['id']}", TARUN)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"%PDF-1.4 lease")
        self.assertTrue(r.headers["content-disposition"].startswith("inline"))
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        d = self.get(f"/api/files/{up['id']}?download=1", TARUN)
        self.assertTrue(d.headers["content-disposition"].startswith("attachment"))
        # on disk under the chat's folder and month
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))[0]["rel_path"]
        month = config.local_now().strftime("%Y-%m")
        self.assertEqual(rel, f"Household ({self.hh[:8]})/{month}/Lease.pdf")
        # can't be sent twice
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "", "attachmentIds": [up["id"]]},
                                   NISHA).status_code, 422)

    def test_serving_headers(self):
        up = self.ok(self.upload(self.hh, "page.html", b"<script>alert(1)</script>", NISHA), 422)
        up = self.ok(self.upload(self.hh, "notes.txt", b"hello", NISHA), 201)
        self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        r = self.get(f"/api/files/{up['id']}", TARUN)
        self.assertIn("sandbox", r.headers["content-security-policy"])
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        # something named .png that isn't an image is never shown inline
        fake = self.ok(self.upload(self.hh, "x.png", b"<html><script>1</script>", NISHA), 201)
        self.assertEqual(fake["mime"], "application/octet-stream")
        self.send(self.hh, "", NISHA, attachmentIds=[fake["id"]])
        r = self.get(f"/api/files/{fake['id']}", TARUN)
        self.assertTrue(r.headers["content-disposition"].startswith("attachment"))
        # an unknown type downloads
        z = self.ok(self.upload(self.hh, "data.bin", b"\x00\x01", NISHA), 201)
        self.send(self.hh, "", NISHA, attachmentIds=[z["id"]])
        self.assertTrue(self.get(f"/api/files/{z['id']}", TARUN).headers["content-disposition"].startswith("attachment"))

    def test_limits_blocked_quota(self):
        self.ok(self.put("/api/admin/settings", {"max_upload_mb": 1}))
        self.assertEqual(self.upload(self.hh, "big.bin", b"x" * (1024 * 1024 + 1), NISHA).status_code, 413)
        self.assertEqual(self.upload(self.hh, "run.EXE", b"MZ", NISHA).status_code, 422)
        self.assertEqual(self.upload(self.hh, "empty.txt", b"", NISHA).status_code, 422)
        with db.get_conn() as conn:
            conn.execute("INSERT INTO attachments (id, conversation_id, rel_path, original_name, mime, size, sha256, created_at) "
                         "VALUES ('big', ?, 'x', 'x', 'x', ?, '', ?)", (self.hh, 1024 ** 3, config.now_iso()))
        self.ok(self.put("/api/admin/settings", {"share_folder_quota_gb": 1}))
        self.assertEqual(self.upload(self.hh, "a.txt", b"abc", NISHA).status_code, 507)
        # nothing half-written is left behind
        leftovers = [f for _, _, fs in os.walk(config.SHARE_DIR) for f in fs if f.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_names_windows_safe_and_clashes(self):
        self.assertEqual(files.clean('a<b>:c"d|e?.txt'), "a-b--c-d-e-.txt")
        self.assertEqual(files.clean("CON.txt"), "_CON.txt")
        self.assertEqual(files.clean("...hidden. "), "hidden")
        self.assertEqual(files.clean("../../etc/passwd"), "-..-etc-passwd")
        a = self.ok(self.upload(self.hh, "Bill.pdf", b"%PDF-1", NISHA), 201)
        b = self.ok(self.upload(self.hh, "Bill.pdf", b"%PDF-2", NISHA), 201)
        paths = [r["rel_path"].rsplit("/", 1)[1] for r in sql("SELECT rel_path FROM attachments ORDER BY created_at")]
        self.assertEqual(paths, ["Bill.pdf", "Bill (2).pdf"])
        self.assertEqual(b["name"], "Bill.pdf")

    def test_rename_moves_folder(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"abc", ADMIN), 201)
        self.send(self.hh, "", ADMIN, attachmentIds=[up["id"]])
        self.ok(self.patch(f"/api/conversations/{self.hh}", {"name": "Home: sweet/home"}, ADMIN))
        folder = sql("SELECT folder FROM conversations WHERE id = ?", (self.hh,))[0]["folder"]
        self.assertEqual(folder, f"Home- sweet-home ({self.hh[:8]})")
        self.assertTrue(os.path.isdir(os.path.join(config.SHARE_DIR, folder)))
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).content, b"abc")

    def test_traversal_and_symlink_refused(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"abc", ADMIN), 201)
        self.send(self.hh, "", ADMIN, attachmentIds=[up["id"]])
        with db.get_conn() as conn:
            conn.execute("UPDATE attachments SET rel_path = '../../data/chat.db' WHERE id = ?", (up["id"],))
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).status_code, 404)
        outside = os.path.join(os.path.dirname(config.SHARE_DIR), "outside.txt")
        with open(outside, "w") as f:
            f.write("nope")
        link = os.path.join(config.SHARE_DIR, "link.txt")
        os.symlink(outside, link)
        with db.get_conn() as conn:
            conn.execute("UPDATE attachments SET rel_path = 'link.txt' WHERE id = ?", (up["id"],))
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).status_code, 404)

    def test_missing_file(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"abc", ADMIN), 201)
        m = self.send(self.hh, "", ADMIN, attachmentIds=[up["id"]])
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))[0]["rel_path"]
        os.remove(os.path.join(config.SHARE_DIR, rel))
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).status_code, 404)
        msgs = self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))["messages"]
        self.assertTrue([x for x in msgs if x["id"] == m["id"]][0]["attachments"][0]["missing"])

    def test_photo_made_smaller_without_location(self):
        up = self.ok(self.upload(self.hh, "IMG_1.jpg", jpeg(3000, 1500, gps=True), NISHA), 201)
        self.assertEqual(up["mime"], "image/jpeg")
        self.assertEqual(max(up["width"], up["height"]), 2000)
        self.assertEqual((up["width"], up["height"]), (1000, 2000))      # orientation applied
        self.assertTrue(up["originalSize"])
        from PIL import Image
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))[0]["rel_path"]
        with Image.open(os.path.join(config.SHARE_DIR, rel)) as im:
            self.assertNotIn(0x8825, im.getexif())
        # Send original keeps it byte for byte
        raw = jpeg(3000, 1500, gps=True)
        o = self.ok(self.upload(self.hh, "IMG_2.jpg", raw, NISHA, original="true"), 201)
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (o["id"],))[0]["rel_path"]
        with open(os.path.join(config.SHARE_DIR, rel), "rb") as f:
            self.assertEqual(f.read(), raw)
        self.assertIsNone(o["originalSize"])
        # a thumbnail exists and is served to members
        self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        t = self.get(f"/api/files/{up['id']}/thumb", TARUN)
        self.assertEqual(t.status_code, 200)
        self.assertEqual(t.headers["content-type"], "image/jpeg")

    def test_voice(self):
        self.assertEqual(self.upload(self.hh, "Voice.webm", b"\x1aE\xdf\xa3voice", NISHA, voice="true").status_code, 422)  # no length
        self.ok(self.put("/api/admin/settings", {"voice_max_seconds": 60}))
        self.assertEqual(self.upload(self.hh, "Voice.webm", b"\x1aE", NISHA, voice="true", duration="90").status_code, 422)
        v = self.ok(self.upload(self.hh, "Voice.webm", b"\x1aE\xdf\xa3voice", NISHA, voice="true", duration="12.34"), 201)
        self.assertEqual(v["mime"], "audio/webm")
        self.assertEqual(v["duration"], 12.3)
        self.send(self.hh, "", NISHA, attachmentIds=[v["id"]])
        r = self.get(f"/api/files/{v['id']}", TARUN)
        self.assertTrue(r.headers["content-disposition"].startswith("inline"))
        msg = self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))["messages"][-1]
        self.assertFalse(msg["attachments"][0]["heard"])
        self.ok(self.post(f"/api/files/{v['id']}/heard", user=TARUN))
        msg = self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))["messages"][-1]
        self.assertTrue(msg["attachments"][0]["heard"])

    def test_files_lists_and_cancel(self):
        a = self.ok(self.upload(self.hh, "a.pdf", b"%PDF-", NISHA), 201)
        b = self.ok(self.upload(self.hh, "b.jpg", jpeg(10, 10), NISHA), 201)
        self.send(self.hh, "", NISHA, attachmentIds=[a["id"], b["id"]])
        self.assertEqual(len(self.ok(self.get(f"/api/conversations/{self.hh}/files", TARUN))["files"]), 2)
        self.assertEqual([f["name"] for f in self.ok(self.get(f"/api/conversations/{self.hh}/files?type=images", TARUN))["files"]], ["b.jpg"])
        self.assertEqual(len(self.ok(self.get("/api/me/files", NISHA))["files"]), 2)
        self.assertEqual(self.ok(self.get("/api/me/files", TARUN))["files"], [])
        c = self.ok(self.upload(self.hh, "c.txt", b"c", NISHA), 201)
        self.assertEqual(self.delete(f"/api/uploads/{c['id']}", TARUN).status_code, 404)
        self.ok(self.delete(f"/api/uploads/{c['id']}", NISHA))
        # unsent uploads go after a day
        d = self.ok(self.upload(self.hh, "d.txt", b"d", NISHA), 201)
        with db.get_conn() as conn:
            conn.execute("UPDATE attachments SET created_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (d["id"],))
        self.assertEqual(housekeeping.remove_unsent_uploads(), 1)

    def test_delete_message_moves_file_aside(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"abc", NISHA), 201)
        m = self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        self.ok(self.delete(f"/api/messages/{m['id']}", NISHA))
        self.assertEqual(self.get(f"/api/files/{up['id']}", NISHA).status_code, 404)
        deleted = [f for _, _, fs in os.walk(os.path.join(config.SHARE_DIR, "_deleted")) for f in fs]
        self.assertEqual(deleted, ["a.txt"])

