import io
import json
import os
import sqlite3
import zipfile
from datetime import timedelta

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import config, db, disappearing, housekeeping, scheduled, shared_folders


def jpeg(w=800, h=600, gps=False):
    from PIL import Image
    im = Image.new("RGB", (w, h), (10, 120, 60))
    out = io.BytesIO()
    kw = {}
    if gps:
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0)}
        kw["exif"] = exif
    im.save(out, "JPEG", **kw)
    return out.getvalue()


class ChildTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        for kid in (TARUN, LEELA):
            self.ok(self.patch(f"/api/admin/people/{kid['id']}", {"isChild": True}))
        self.hh = self.household()

    def test_restrictions(self):
        self.assertTrue(self.ok(self.get("/api/me", TARUN))["isChild"])
        self.assertEqual(self.post("/api/conversations", {"name": "Kids"}, TARUN).status_code, 403)
        # children can't message each other directly unless allowed
        self.assertEqual(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, TARUN).status_code, 403)
        self.ok(self.put("/api/admin/settings", {"children_can_message_each_other": True}))
        self.ok(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, TARUN))
        # but they can message adults
        d = self.ok(self.post("/api/conversations/direct", {"userId": NISHA["id"]}, TARUN))["id"]
        self.send(d, "hi mum", TARUN)
        m = self.send(self.hh, "hello", NISHA)
        self.assertEqual(self.put(f"/api/messages/{m['id']}/pin", user=TARUN).status_code, 403)
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "x", "expiresIn": 3600}, TARUN).status_code, 403)
        self.assertEqual(self.patch(f"/api/conversations/{d}", {"disappearSeconds": 3600}, TARUN).status_code, 403)
        # a child is never a group admin
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.assertEqual(self.patch(f"/api/conversations/{g}/members/{TARUN['id']}", {"role": "admin"}, NISHA).status_code, 409)
        self.assertEqual(self.delete(f"/api/messages/{m['id']}", TARUN).status_code, 403)

    def test_becoming_a_child_hands_over(self):
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [ADMIN["id"]]}, NISHA), 201)["id"]
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"isChild": True}))
        roles = {r["user_id"]: r["role"] for r in sql("SELECT user_id, role FROM members WHERE conversation_id = ?", (g,))}
        self.assertEqual(roles[NISHA["id"]], "member")
        self.assertEqual(roles[ADMIN["id"]], "owner")

    def test_disappearing_chat_applies_to_children(self):
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.ok(self.patch(f"/api/conversations/{d}", {"disappearSeconds": 3600}, NISHA))
        m = self.send(d, "gone soon", TARUN)
        self.assertTrue(m["expiresAt"])


class AnnouncementTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_who_may_post_and_acks(self):
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "x", "announcement": True}, NISHA).status_code, 403)
        a = self.send(self.hh, "Power cut tomorrow 10–2", ADMIN, announcement=True)
        self.assertEqual(set(a["announcement"]["pending"]), {NISHA["id"], TARUN["id"]})
        conv = self.ok(self.get(f"/api/conversations/{self.hh}", NISHA))
        self.assertEqual([x["id"] for x in conv["announcements"]], [a["id"]])
        self.ok(self.post(f"/api/messages/{a['id']}/ack", user=NISHA))
        r = self.ok(self.post(f"/api/messages/{a['id']}/ack", user=TARUN))
        self.assertTrue(r["announcement"]["closed"])
        self.assertEqual(self.ok(self.get(f"/api/conversations/{self.hh}", NISHA))["announcements"], [])
        # group admins only when the setting allows it
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.assertEqual(self.post(f"/api/conversations/{g}/messages", {"body": "x", "announcement": True}, NISHA).status_code, 403)
        self.ok(self.put("/api/admin/settings", {"who_can_announce": "admins_and_group_admins"}))
        self.send(g, "Group news", NISHA, announcement=True)
        self.assertEqual(self.post(f"/api/conversations/{self.personal(ADMIN)}/messages", {"body": "x", "announcement": True}).status_code, 403)

    def test_bypasses_mute_not_off_and_reminder_once(self):
        self.notify_to(NISHA)
        self.notify_to(TARUN, "mobile_app_tarun")
        self.ok(self.put(f"/api/conversations/{self.hh}/me", {"mutedUntil": config.iso(config.utcnow() + timedelta(days=1))}, NISHA))
        self.ok(self.put("/api/me/settings", {"notifyLevel": "off"}, TARUN))
        a = self.send(self.hh, "Fire drill", ADMIN, announcement=True)
        who = [s["services"][0] for s in self.sent.items]
        self.assertEqual(who, ["mobile_app_phone"])
        self.assertIn("📢", self.sent.items[0]["message"])
        self.ok(self.post(f"/api/messages/{a['id']}/announcement-reminder"))
        self.assertEqual(self.post(f"/api/messages/{a['id']}/announcement-reminder").status_code, 409)
        self.assertEqual(self.post(f"/api/messages/{a['id']}/announcement-reminder", user=NISHA).status_code, 403)
        self.ok(self.post(f"/api/messages/{a['id']}/announcement-close"))
        self.assertTrue(self.ok(self.post(f"/api/messages/{a['id']}/ack", user=NISHA))["announcement"]["closed"])


class DisappearingTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def expire_all(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE messages SET expires_at = ? WHERE expires_at IS NOT NULL",
                         (config.iso(config.utcnow() - timedelta(seconds=1)),))

    def test_chat_setting_and_per_message(self):
        before = self.send(self.hh, "before", ADMIN)
        self.assertEqual(self.patch(f"/api/conversations/{self.hh}", {"disappearSeconds": 999}).status_code, 422)
        self.assertEqual(self.patch(f"/api/conversations/{self.hh}", {"disappearSeconds": 3600}, NISHA).status_code, 403)
        self.ok(self.patch(f"/api/conversations/{self.hh}", {"disappearSeconds": 86400}))
        texts = [m["body"] for m in self.ok(self.get(f"/api/conversations/{self.hh}/messages"))["messages"] if m["kind"] == "system"]
        self.assertIn("Meera turned on disappearing messages: 1 day", texts)
        m = self.send(self.hh, "new", NISHA)
        self.assertTrue(m["expiresAt"])
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/messages", {"body": "x", "expiresIn": 0}, NISHA).status_code, 422)
        m2 = self.send(self.hh, "short", NISHA, expiresIn=3600)
        self.assertLess(m2["expiresAt"], m["expiresAt"])
        self.assertIsNone(sql("SELECT expires_at FROM messages WHERE id = ?", (before["id"],))[0]["expires_at"])
        # per message in an ordinary chat
        room = self.personal(NISHA)
        n = self.send(room, "note", NISHA, expiresIn=604800)
        self.assertTrue(n["expiresAt"])

    def test_expiry_deletes_completely(self):
        up = self.ok(self.upload(self.hh, "Secret.pdf", b"%PDF-1 secret", NISHA), 201)
        m = self.send(self.hh, "vanishing *text*", NISHA, attachmentIds=[up["id"]], expiresIn=3600)
        rel = sql("SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))[0]["rel_path"]
        self.assertIn("/_disappearing/", rel)
        self.assertRegex(rel.rsplit("/", 1)[1], r"^\d{8}T\d{4}Z__Secret\.pdf$")
        reply = self.send(self.hh, "my reply", TARUN, replyTo=m["id"])
        self.ok(self.put(f"/api/messages/{m['id']}/reactions/👍", user=TARUN))
        self.ok(self.put(f"/api/messages/{m['id']}/star", user=TARUN))
        self.ok(self.put(f"/api/messages/{m['id']}/pin", user=TARUN))
        self.ok(self.post(f"/api/messages/{m['id']}/remind", {"preset": "tomorrow"}, TARUN))
        self.assertEqual(self.post("/api/messages/forward", {"messageIds": [m["id"]], "conversationIds": [self.personal(TARUN)]},
                                   TARUN).status_code, 409)
        self.expire_all()
        self.assertGreaterEqual(disappearing.run_expiry(), 1)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM messages WHERE id = ?", (m["id"],))[0]["n"], 0)
        for t in ("reactions", "stars", "reminders", "attachments"):
            self.assertEqual(sql(f"SELECT COUNT(*) AS n FROM {t} WHERE message_id = ?", (m["id"],))[0]["n"], 0, t)
        self.assertFalse(os.path.exists(os.path.join(config.SHARE_DIR, rel)))
        deleted_dir = os.path.join(config.SHARE_DIR, "_deleted")
        self.assertFalse(os.path.isdir(deleted_dir) and os.listdir(deleted_dir))
        msgs = self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))["messages"]
        r = [x for x in msgs if x["id"] == reply["id"]][0]
        self.assertEqual(r["body"], "my reply")
        self.assertEqual(r["replyTo"], {"gone": True})
        self.assertEqual(self.ok(self.get("/api/search?q=vanishing", TARUN))["messages"], [])
        # nothing left in the database file either
        disappearing.checkpoint()
        with open(config.DB_PATH, "rb") as f:
            self.assertNotIn(b"vanishing", f.read())

    def test_notification_never_shows_text(self):
        self.notify_to(TARUN, "mobile_app_tarun")
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.send(d, "the secret", NISHA, expiresIn=3600)
        self.assertEqual(self.sent.items[-1]["message"], "Nisha sent a disappearing message")

    def test_backup_leaves_them_out_and_restore_expires(self):
        up = self.ok(self.upload(self.hh, "keep.txt", b"keep", NISHA), 201)
        self.send(self.hh, "kept", NISHA, attachmentIds=[up["id"]])
        dup = self.ok(self.upload(self.hh, "gone.txt", b"gone", NISHA), 201)
        self.send(self.hh, "temporary", NISHA, attachmentIds=[dup["id"]], expiresIn=3600)
        r = self.get("/api/admin-storage-download-db?includeFiles=true")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertFalse(any("_disappearing" in n for n in z.namelist()))
        path = os.path.join(config.DATA_DIR, "copy.db")
        with open(path, "wb") as f:
            f.write(z.read("chat.db"))
        c = sqlite3.connect(path)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE body = 'temporary'").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM messages WHERE body = 'kept'").fetchone()[0], 1)
        c.close()
        with open(path, "rb") as f:
            self.assertNotIn(b"temporary", f.read())
        os.remove(path)

    def test_restore_of_old_database_expires_before_serving(self):
        # a full Home Assistant backup brings back a row and a file that are past due
        m = self.send(self.hh, "old secret", NISHA, expiresIn=3600)
        raw = io.BytesIO()
        src = sqlite3.connect(config.DB_PATH)
        tmp = os.path.join(config.DATA_DIR, "ha.db")
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        src.close()
        dst.execute("UPDATE messages SET expires_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (m["id"],))
        dst.commit()
        dst.close()
        with zipfile.ZipFile(raw, "w") as z:
            z.write(tmp, "chat.db")
        os.remove(tmp)
        folder = sql("SELECT folder FROM conversations WHERE id = ?", (self.hh,))[0]["folder"]
        stray = os.path.join(config.SHARE_DIR, folder, "_disappearing", "20000101T0000Z__leftover.txt")
        os.makedirs(os.path.dirname(stray), exist_ok=True)
        with open(stray, "w") as f:
            f.write("x")
        self.ok(self.req("POST", "/api/admin-storage-import-db", ADMIN, content=raw.getvalue()))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM messages WHERE id = ?", (m["id"],))[0]["n"], 0)
        self.assertFalse(os.path.exists(stray))


class ScheduledTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def due_now(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE scheduled SET send_at = ?", (config.iso(config.utcnow() - timedelta(seconds=1)),))

    def test_schedule_edit_send(self):
        at = config.iso(config.utcnow() + timedelta(hours=2))
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/scheduled", {"sendAt": config.now_iso(), "body": "x"}, NISHA).status_code, 422)
        self.assertEqual(self.post(f"/api/conversations/{self.personal(NISHA)}/scheduled", {"sendAt": at, "body": "x"}, NISHA).status_code, 409)
        up = self.ok(self.upload(self.hh, "plan.txt", b"plan", NISHA), 201)
        s = self.ok(self.post(f"/api/conversations/{self.hh}/scheduled", {"sendAt": at, "body": "Happy birthday!",
                                                                         "attachmentIds": [up["id"]]}, NISHA), 201)
        self.assertEqual(len(self.ok(self.get("/api/me/scheduled", NISHA))["scheduled"]), 1)
        self.assertEqual(self.ok(self.get("/api/me/scheduled", TARUN))["scheduled"], [])
        self.assertEqual(self.patch(f"/api/scheduled/{s['id']}", {"body": "x"}, TARUN).status_code, 404)
        self.ok(self.patch(f"/api/scheduled/{s['id']}", {"body": "Happy birthday, Tarun!"}, NISHA))
        # its file isn't removed as an unsent upload
        with db.get_conn() as conn:
            conn.execute("UPDATE attachments SET created_at = '2000-01-01T00:00:00.000Z'")
        self.assertEqual(housekeeping.remove_unsent_uploads(), 0)
        self.due_now()
        self.assertEqual(scheduled.run_due(), 1)
        last = self.ok(self.get(f"/api/conversations/{self.hh}/messages", TARUN))["messages"][-1]
        self.assertEqual((last["body"], last["userId"]), ("Happy birthday, Tarun!", NISHA["id"]))
        self.assertEqual(last["attachments"][0]["name"], "plan.txt")
        self.assertEqual(self.ok(self.get("/api/me/scheduled", NISHA))["scheduled"], [])

    def test_cancelled_when_no_longer_a_member_and_survives_restart(self):
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        at = config.iso(config.utcnow() + timedelta(hours=2))
        self.ok(self.post(f"/api/conversations/{g}/scheduled", {"sendAt": at, "body": "later"}, TARUN), 201)
        self.ok(self.post(f"/api/conversations/{g}/scheduled", {"sendAt": at, "poll": {"question": "Q?", "options": ["a", "b"]}}, NISHA), 201)
        self.ok(self.delete(f"/api/conversations/{g}/members/{TARUN['id']}", NISHA))
        self.notify_to(TARUN, "mobile_app_tarun")
        self.due_now()
        self.assertEqual(scheduled.run_due(), 1)                   # Nisha's poll went; Tarun's was cancelled
        self.assertIn("wasn't sent", self.sent.items[-1]["message"])
        msgs = self.ok(self.get(f"/api/conversations/{g}/messages", NISHA))["messages"]
        self.assertEqual(msgs[-1]["kind"], "poll")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM scheduled")[0]["n"], 0)

    def test_send_now_and_limit(self):
        at = config.iso(config.utcnow() + timedelta(days=1))
        s = self.ok(self.post(f"/api/conversations/{self.hh}/scheduled", {"sendAt": at, "body": "now please"}, NISHA), 201)
        self.ok(self.post(f"/api/scheduled/{s['id']}/send-now", user=NISHA))
        self.assertEqual(self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["messages"][-1]["body"], "now please")
        for i in range(20):
            self.ok(self.post(f"/api/conversations/{self.hh}/scheduled", {"sendAt": at, "body": str(i)}, NISHA), 201)
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/scheduled", {"sendAt": at, "body": "21"}, NISHA).status_code, 409)


class DraftTests(ApiTestCase):
    def test_drafts(self):
        self.enable(ADMIN, NISHA)
        hh = self.household()
        self.ok(self.put(f"/api/conversations/{hh}/draft", {"body": "half a thought"}, NISHA))
        c = [x for x in self.ok(self.get("/api/conversations", NISHA))["conversations"] if x["id"] == hh][0]
        self.assertEqual(c["draft"]["body"], "half a thought")
        self.assertIsNone([x for x in self.ok(self.get("/api/conversations", ADMIN))["conversations"] if x["id"] == hh][0]["draft"])
        self.ok(self.put(f"/api/conversations/{hh}/draft", {"body": "second device wins"}, NISHA))
        self.send(hh, "sent", NISHA)
        c = [x for x in self.ok(self.get("/api/conversations", NISHA))["conversations"] if x["id"] == hh][0]
        self.assertIsNone(c["draft"])
        self.notify_to(ADMIN)
        self.ok(self.put(f"/api/conversations/{hh}/draft", {"body": "never notified"}, NISHA))
        self.assertEqual(self.sent.items, [])


class ExportTests(ApiTestCase):
    def test_export(self):
        self.enable(ADMIN, NISHA, TARUN)
        hh = self.household()
        up = self.ok(self.upload(hh, "photo.jpg", jpeg(), NISHA), 201)
        self.send(hh, "*Hello* <script>alert(1)</script> https://example.com", NISHA, attachmentIds=[up["id"]])
        self.send(hh, "vanishing", NISHA, expiresIn=3600)
        self.ok(self.post(f"/api/conversations/{hh}/polls", {"question": "Tea?", "options": ["Yes", "No"]}, TARUN), 201)
        r = self.get(f"/api/conversations/{hh}/export", TARUN)
        self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        page = z.read("Household.html").decode()
        self.assertIn("<strong>Hello</strong>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("vanishing", page)
        self.assertIn("1 disappearing message(s) are not included", page)
        self.assertIn("Tea?", page)
        self.assertIn("files/photo.jpg", z.namelist())
        # members only
        g = self.ok(self.post("/api/conversations", {"name": "Private"}, NISHA), 201)["id"]
        self.assertEqual(self.get(f"/api/conversations/{g}/export", ADMIN).status_code, 404)
        self.assertEqual(self.get(f"/api/conversations/{hh}/export?from=yesterday", TARUN).status_code, 422)
        self.assertEqual(sql("SELECT action FROM audit_log WHERE action = 'exported'")[0]["action"], "exported")


class CleanupTests(ApiTestCase):
    def test_storage_cleanup_names_hidden(self):
        self.enable(ADMIN, NISHA)
        g = self.ok(self.post("/api/conversations", {"name": "Priv"}, NISHA), 201)["id"]
        up = self.ok(self.upload(g, "Private scan.pdf", b"%PDF-1" + b"x" * 5000, NISHA), 201)
        self.send(g, "", NISHA, attachmentIds=[up["id"]])
        hh = self.household()
        mine = self.ok(self.upload(hh, "mine.txt", b"abc", ADMIN), 201)
        self.send(hh, "", ADMIN, attachmentIds=[mine["id"]])
        lst = self.ok(self.get("/api/admin/storage/files"))["files"]
        priv = [x for x in lst if x["id"] == up["id"]][0]
        self.assertIsNone(priv["name"])
        self.assertEqual(priv["type"], "PDF")
        self.assertNotIn("Private scan", json.dumps(lst))
        self.assertEqual([x for x in lst if x["id"] == mine["id"]][0]["name"], "mine.txt")
        self.assertEqual(self.get("/api/admin/storage/files", NISHA).status_code, 403)
        usage = self.ok(self.get("/api/admin/storage/usage"))
        self.assertGreater(usage["byType"]["documents"], 5000)
        self.ok(self.post("/api/admin/storage/delete", {"attachmentIds": [up["id"]]}))
        self.assertEqual(self.get(f"/api/files/{up['id']}", NISHA).status_code, 404)
        msg = self.ok(self.get(f"/api/conversations/{g}/messages", NISHA))["messages"][-1]
        self.assertTrue(msg["attachments"][0]["adminDeleted"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'file_deleted_by_admin'")[0]["n"], 1)
        self.ok(self.post("/api/admin/storage/empty-deleted"))
        self.assertFalse(os.listdir(os.path.join(config.SHARE_DIR, "_deleted")))
        rep = self.ok(self.get("/api/admin/storage"))["retention"]
        self.assertIn("30", rep["olderThan"])


class PhotoTests(ApiTestCase):
    """People's photos are their assigned person.* entity's picture in Home Assistant; nothing is uploaded."""

    def setUp(self):
        super().setUp()
        from app import avatars, config as cfg, ha_client
        self.enable(ADMIN, NISHA, TARUN)
        self.pictures = {"person.nisha": "/api/image/serve/abc/512x512", "person.tarun": None}
        self.served = {"/api/image/serve/abc/512x512": jpeg(640, 480, gps=True)}
        self.downloads = []
        self._orig = (ha_client.fetch_states_blocking, avatars.download, ha_client.has_token)
        ha_client.fetch_states_blocking = lambda max_age=0: [
            {"entity_id": e, "state": "home", "attributes": {"entity_picture": p} if p else {}} for e, p in self.pictures.items()]
        ha_client.has_token = lambda: True

        def dl(pic):
            self.downloads.append(pic)
            return self.served.get(pic)
        avatars.download = dl
        self.avatars, self.cfg = avatars, cfg
        self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.nisha"}))
        self.ok(self.put(f"/api/admin/people/{TARUN['id']}/presence", {"entity": "person.tarun"}))

    def tearDown(self):
        from app import ha_client
        ha_client.fetch_states_blocking, self.avatars.download, ha_client.has_token = self._orig
        super().tearDown()

    def person(self, user, viewer=TARUN):
        return [p for p in self.ok(self.get("/api/people", viewer))["people"] if p["id"] == user["id"]][0]

    def test_photo_follows_the_assigned_entity(self):
        v = self.person(NISHA)["avatar"]
        self.assertTrue(v)
        self.assertIsNone(self.person(TARUN)["avatar"])            # no picture in HA → initials
        img = self.get(f"/api/avatars/{NISHA['id']}", TARUN)
        self.assertEqual(img.status_code, 200)
        from PIL import Image
        with Image.open(io.BytesIO(img.content)) as im:
            self.assertEqual(im.size, (256, 256))
            self.assertFalse(im.getexif())                        # no EXIF, no location
        # unchanged → not downloaded again
        n = len(self.downloads)
        self.assertFalse(self.avatars.sync_blocking())
        self.assertEqual(len(self.downloads), n)
        # changed in HA → follows, with a new version so pages reload it
        self.pictures["person.nisha"] = "/api/image/serve/def/512x512"
        self.served["/api/image/serve/def/512x512"] = jpeg(300, 300)
        self.assertTrue(self.avatars.sync_blocking())
        self.assertGreater(self.person(NISHA)["avatar"], v)
        # removed in HA → removed here
        self.pictures["person.nisha"] = None
        self.assertTrue(self.avatars.sync_blocking())
        self.assertIsNone(self.person(NISHA)["avatar"])
        self.assertEqual(self.get(f"/api/avatars/{NISHA['id']}", TARUN).status_code, 404)
        self.assertEqual(os.listdir(self.cfg.AVATAR_DIR), [])

    def test_unassigning_or_deleting_the_entity_removes_it(self):
        self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": None}))
        self.assertIsNone(self.person(NISHA)["avatar"])
        self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.nisha"}))
        self.assertTrue(self.person(NISHA)["avatar"])
        del self.pictures["person.nisha"]                          # the person was deleted in HA
        self.avatars.sync_blocking()
        self.assertIsNone(self.person(NISHA)["avatar"])

    def test_ha_unreachable_or_bad_picture_keeps_what_it_has(self):
        from app import ha_client
        good = self.person(NISHA)["avatar"]
        ha_client.fetch_states_blocking = lambda max_age=0: None
        self.assertFalse(self.avatars.sync_blocking())
        self.assertEqual(self.person(NISHA)["avatar"], good)
        ha_client.fetch_states_blocking = lambda max_age=0: [{"entity_id": "person.nisha", "attributes": {"entity_picture": "/api/x"}}]
        self.served["/api/x"] = b"not an image"
        self.avatars.sync_blocking()
        self.assertEqual(self.person(NISHA)["avatar"], good)

    def test_nothing_can_be_uploaded(self):
        g = self.ok(self.post("/api/conversations", {"name": "G", "icon": "🏡", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        for method, path in (("PUT", "/api/me/avatar"), ("DELETE", "/api/me/avatar"), ("PUT", f"/api/conversations/{g}/photo"),
                             ("GET", f"/api/conversations/{g}/photo")):
            self.assertIn(self.req(method, path, NISHA, content=jpeg()).status_code, (404, 405), path)
        c = self.ok(self.get(f"/api/conversations/{g}", TARUN))
        self.assertNotIn("photo", c)
        self.assertEqual(c["icon"], "🏡")

    def test_photos_uploaded_in_older_databases_are_removed(self):
        from app import main
        os.makedirs(os.path.join(self.cfg.DATA_DIR, "chat_photos"), exist_ok=True)
        os.makedirs(self.cfg.AVATAR_DIR, exist_ok=True)
        with open(os.path.join(self.cfg.AVATAR_DIR, "u-tarun.jpg"), "wb") as f:
            f.write(jpeg())
        sql("UPDATE users SET avatar_file = 'u-tarun.jpg', avatar_src = NULL WHERE id = ?", (TARUN["id"],))
        main.remove_uploaded_photos()
        self.assertFalse(os.path.exists(os.path.join(self.cfg.DATA_DIR, "chat_photos")))
        self.assertIsNone(self.person(TARUN)["avatar"])
        self.assertTrue(self.person(NISHA)["avatar"])              # the HA picture stays

    def test_download_only_from_home_assistant(self):
        self.assertIsNone(self._orig[1]("https://example.com/me.jpg"))

class SharedFolderTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.docs = os.path.join(config.SHARE_ROOT, "Documents", "House")
        os.makedirs(os.path.join(self.docs, "Bills"))
        with open(os.path.join(self.docs, "Lease.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 lease")
        with open(os.path.join(self.docs, ".hidden"), "w") as f:
            f.write("x")
        outside = os.path.join(config.SHARE_ROOT, "secret.txt")
        with open(outside, "w") as f:
            f.write("secret")
        os.symlink(outside, os.path.join(self.docs, "link.txt"))
        self.g = self.ok(self.post("/api/conversations", {"name": "Home", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]

    def share(self, path, chats, user=ADMIN, **extra):
        return self.post("/api/admin/folders", {"path": path, "chats": [{"id": c, "mode": m} for c, m in chats], **extra}, user)

    def link_of(self, r, cid):
        return [c for c in r["folders"][0]["chats"] if c["id"] == cid][0]["linkId"]

    def test_mapping_rules(self):
        self.assertEqual(self.share("household_chat", [(self.g, "ro")]).status_code, 422)
        self.assertEqual(self.share("../etc", [(self.g, "ro")]).status_code, 422)
        self.assertEqual(self.share("Nope", [(self.g, "ro")]).status_code, 422)
        self.assertEqual(self.share("Documents/House", [(self.g, "ro")], NISHA).status_code, 403)
        self.assertEqual(self.share("Documents/House", []).status_code, 422)          # at least one chat
        dirs = self.ok(self.get("/api/admin/share-dirs"))["folders"]
        self.assertIn("Documents", dirs)
        self.assertNotIn("household_chat", dirs)
        r = self.ok(self.share("Documents/House", [(self.g, "ro")], label="House papers"), 201)
        self.assertEqual(r["folders"][0]["label"], "House papers")
        self.assertEqual(self.share("Documents/House", [(self.g, "ro")]).status_code, 409)   # once; edit its chats instead

    def test_browse_serve_upload_and_announce(self):
        r = self.ok(self.share("Documents/House", [(self.g, "ro")]), 201)
        folder_id = r["folders"][0]["id"]
        fid = self.link_of(r, self.g)
        lst = self.ok(self.get(f"/api/folders/{fid}/list", TARUN))
        names = [e["name"] for e in lst["entries"]]
        self.assertEqual(names, ["Bills", "Lease.pdf"])           # no hidden files, no symlink out
        self.assertEqual(self.get(f"/api/folders/{fid}/list", ADMIN).status_code, 404)
        r = self.get(f"/api/folders/{fid}/file?path=Lease.pdf", TARUN)
        self.assertEqual(r.content, b"%PDF-1.4 lease")
        for bad in ("../secret.txt", "link.txt", ".hidden", "/etc/passwd"):
            self.assertEqual(self.get(f"/api/folders/{fid}/file?path={bad}", TARUN).status_code, 404, bad)
        self.assertEqual(self.req("POST", f"/api/folders/{fid}/upload?name=a.txt", TARUN, content=b"hi").status_code, 403)
        self.ok(self.req("PATCH", f"/api/admin/folders/{folder_id}", ADMIN, json={"chats": [{"id": self.g, "mode": "rw"}]}))
        self.ok(self.req("POST", f"/api/folders/{fid}/upload?name=Water.pdf&path=Bills", TARUN, content=b"%PDF-1 water"), 201)
        self.assertEqual(self.req("POST", f"/api/folders/{fid}/upload?name=x.exe", TARUN, content=b"MZ").status_code, 422)
        self.assertEqual(self.req("POST", f"/api/folders/{fid}/upload?name=x.txt&path=../..", TARUN, content=b"x").status_code, 404)
        self.assertTrue(os.path.isfile(os.path.join(self.docs, "Bills", "Water.pdf")))
        with open(os.path.join(self.docs, "Insurance.pdf"), "wb") as f:
            f.write(b"%PDF-1 ins")
        self.assertEqual(shared_folders.scan_all(), 2)
        texts = [m["body"] for m in self.ok(self.get(f"/api/conversations/{self.g}/messages", TARUN))["messages"] if m["kind"] == "system"]
        self.assertTrue(any(t.startswith("2 new files in “House”") for t in texts), texts)
        self.ok(self.delete(f"/api/admin/folders/{folder_id}"))
        self.assertEqual(self.get(f"/api/folders/{fid}/list", TARUN).status_code, 404)
        self.assertTrue(os.path.isfile(os.path.join(self.docs, "Lease.pdf")))    # stopping sharing deletes nothing


    def test_search_and_several_uploads(self):
        r = self.ok(self.share("Documents/House", [(self.g, "rw")]), 201)
        fid = self.link_of(r, self.g)
        os.makedirs(os.path.join(self.docs, "Bills", "2026"))
        for rel in ("Bills/Water bill.pdf", "Bills/2026/Électricité bill.pdf", "Insurance.pdf", ".secret bill.txt"):
            with open(os.path.join(self.docs, rel), "wb") as fh:
                fh.write(b"%PDF-1 x")
        res = self.ok(self.get(f"/api/folders/{fid}/search?q=BILL", TARUN))
        self.assertEqual(sorted((x["folder"], x["name"]) for x in res["results"]),
                         [("", "Bills"), ("Bills", "Water bill.pdf"), ("Bills/2026", "Électricité bill.pdf")])   # no hidden files
        self.assertEqual([x["name"] for x in self.ok(self.get(f"/api/folders/{fid}/search?q=electricite 2026", TARUN))["results"]], [])
        self.assertEqual([x["name"] for x in self.ok(self.get(f"/api/folders/{fid}/search?q=electricite", TARUN))["results"]],
                         ["Électricité bill.pdf"])                        # accents and case ignored
        self.assertEqual([(x["name"], x["dir"]) for x in self.ok(self.get(f"/api/folders/{fid}/search?q=bills", TARUN))["results"]],
                         [("Bills", True)])
        self.assertNotIn("secret", self.get(f"/api/folders/{fid}/search?q=link", TARUN).text)   # the symlink out is skipped
        self.assertEqual(self.get(f"/api/folders/{fid}/search?q=bill", ADMIN).status_code, 404)  # members only
        # several uploads in a row, same name twice → numbered
        for n in ("a.pdf", "b.pdf", "a.pdf"):
            self.ok(self.req("POST", f"/api/folders/{fid}/upload?name={n}&path=Bills", TARUN, content=b"%PDF-1 y"), 201)
        names = [e["name"] for e in self.ok(self.get(f"/api/folders/{fid}/list?path=Bills", TARUN))["entries"]]
        self.assertTrue({"a.pdf", "a (2).pdf", "b.pdf"} <= set(names), names)

    def test_any_chats_several_at_once_each_with_its_own_access(self):
        room = self.personal(NISHA)
        direct = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        mine = self.personal(ADMIN)
        choices = {c["id"]: c for c in self.ok(self.get("/api/admin/folders"))["chats"]}
        self.assertEqual(choices[mine]["name"], "📌 My room")
        self.assertEqual({choices[room]["kind"], choices[direct]["kind"], choices[self.g]["kind"]}, {"personal", "direct", "group"})
        r = self.ok(self.share("Documents/House", [(self.g, "ro"), (room, "rw"), (direct, "ro"), (mine, "rw")]), 201)
        f = r["folders"][0]
        self.assertEqual(len(f["chats"]), 4)
        in_room, in_group, in_direct = self.link_of(r, room), self.link_of(r, self.g), self.link_of(r, direct)
        # each chat shows it, with its own access
        self.assertEqual(self.ok(self.get(f"/api/conversations/{room}", NISHA))["folders"], [{"id": in_room, "label": "House", "mode": "rw"}])
        self.assertEqual(self.ok(self.get(f"/api/conversations/{self.g}", TARUN))["folders"][0]["mode"], "ro")
        self.ok(self.req("POST", f"/api/folders/{in_room}/upload?name=Mine.pdf", NISHA, content=b"%PDF-1 m"), 201)
        self.assertEqual(self.req("POST", f"/api/folders/{in_group}/upload?name=x.pdf", NISHA, content=b"%PDF-1 x").status_code, 403)
        # a link only works for members of its own chat: Tarun can't use Nisha's room link
        self.assertEqual(self.get(f"/api/folders/{in_room}/list", TARUN).status_code, 404)
        self.assertEqual(self.get(f"/api/folders/{in_room}/list", ADMIN).status_code, 404)
        self.ok(self.get(f"/api/folders/{in_direct}/list", TARUN))
        # one scan, announced in each chat that can show it (not personal rooms)
        with open(os.path.join(self.docs, "New.pdf"), "wb") as fh:
            fh.write(b"%PDF-1 n")
        self.assertEqual(shared_folders.scan_all(), 2)          # Mine.pdf and New.pdf
        for cid, user in ((self.g, TARUN), (direct, TARUN)):
            texts = [m["body"] for m in self.ok(self.get(f"/api/conversations/{cid}/messages", user))["messages"] if m["kind"] == "system"]
            self.assertTrue(any("new files in “House”" in t for t in texts), texts)
        self.assertFalse([m for m in self.ok(self.get(f"/api/conversations/{room}/messages", NISHA))["messages"] if m["kind"] == "system"])
        # change the list: drop the direct chat, make the group read-write, rename
        self.ok(self.req("PATCH", f"/api/admin/folders/{f['id']}", ADMIN, json={
            "label": "House papers", "chats": [{"id": self.g, "mode": "rw"}, {"id": room, "mode": "rw"}, {"id": mine, "mode": "rw"}]}))
        self.assertEqual(self.get(f"/api/folders/{in_direct}/list", TARUN).status_code, 404)
        self.assertEqual(self.ok(self.get(f"/api/conversations/{self.g}", TARUN))["folders"], [{"id": in_group, "label": "House papers", "mode": "rw"}])
        texts = [m["body"] for m in self.ok(self.get(f"/api/conversations/{direct}/messages", TARUN))["messages"] if m["kind"] == "system"]
        self.assertIn("The shared folder “House” was removed", texts)
        self.assertEqual(self.req("PATCH", f"/api/admin/folders/{f['id']}", ADMIN, json={"chats": []}).status_code, 422)
        # stopping sharing removes it from every chat
        self.ok(self.delete(f"/api/admin/folders/{f['id']}"))
        self.assertEqual(self.get(f"/api/folders/{in_room}/list", NISHA).status_code, 404)
        self.assertEqual(self.ok(self.get(f"/api/conversations/{self.g}", TARUN))["folders"], [])

    def test_ten_per_chat(self):
        for i in range(10):
            os.makedirs(os.path.join(config.SHARE_ROOT, "Many", str(i)))
            self.ok(self.share(f"Many/{i}", [(self.g, "ro")]), 201)
        os.makedirs(os.path.join(config.SHARE_ROOT, "Many", "x"))
        self.assertEqual(self.share("Many/x", [(self.g, "ro")]).status_code, 409)
        self.assertFalse(sql("SELECT 1 FROM folders WHERE path = 'Many/x'"))          # nothing half-made

    def test_old_database_is_migrated(self):
        from app import db
        room = self.personal(NISHA)
        with db.get_conn() as conn:
            conn.executescript("""
                CREATE TABLE shared_folders (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, path TEXT NOT NULL,
                  label TEXT NOT NULL, mode TEXT NOT NULL, announce INTEGER NOT NULL DEFAULT 1, created_by TEXT,
                  created_at TEXT NOT NULL, last_scan_at TEXT);
                CREATE TABLE shared_folder_files (folder_id TEXT NOT NULL, rel TEXT NOT NULL, size INTEGER NOT NULL,
                  mtime REAL NOT NULL, PRIMARY KEY (folder_id, rel));""")
            conn.execute("INSERT INTO shared_folders VALUES ('old1', ?, 'Documents/House', 'House', 'rw', 1, NULL, '2026-01-01', '2026-01-02')", (self.g,))
            conn.execute("INSERT INTO shared_folders VALUES ('old2', ?, 'Documents/House', 'Home docs', 'ro', 0, NULL, '2026-01-03', NULL)", (room,))
            conn.execute("INSERT INTO shared_folder_files VALUES ('old1', 'Lease.pdf', 14, 1.0)")
        db.init_db()
        self.assertEqual(sql("SELECT name FROM sqlite_master WHERE name LIKE 'shared_folder%'"), [])
        f = sql("SELECT * FROM folders")
        self.assertEqual((len(f), f[0]["id"], f[0]["label"]), (1, "old1", "House"))
        self.assertEqual({(r["id"], r["conversation_id"], r["mode"]) for r in sql("SELECT * FROM folder_links")},
                         {("old1", self.g, "rw"), ("old2", room, "ro")})
        self.assertEqual(len(sql("SELECT * FROM folder_files")), 1)
        self.ok(self.get("/api/folders/old1/list", TARUN))          # old ids (in old announcements) still open it
        self.ok(self.get("/api/folders/old2/list", NISHA))


class ReviewRegressions(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_announcement_pending_only_those_who_can_see_it(self):
        self.ok(self.patch(f"/api/conversations/{self.hh}", {"newMembersSeeHistory": False}))
        a = self.send(self.hh, "Old news", ADMIN, announcement=True)
        self.enable(LEELA)                     # joins later, can't see it
        r = self.ok(self.post(f"/api/messages/{a['id']}/ack", user=NISHA))
        self.assertNotIn(LEELA["id"], r["announcement"]["pending"])

    def test_storage_list_hides_sender_and_date(self):
        room = self.personal(NISHA)
        up = self.ok(self.upload(room, "x.pdf", b"%PDF-1 x", NISHA), 201)
        self.send(room, "", NISHA, attachmentIds=[up["id"]])
        row = [f for f in self.ok(self.get("/api/admin/storage/files"))["files"] if f["id"] == up["id"]][0]
        self.assertEqual((row["name"], row["sender"], row["sentAt"], row["chat"]), (None, None, None, None))

    def test_swapped_folder_symlink_refused(self):
        import shutil
        docs = os.path.join(config.SHARE_ROOT, "Docs")
        os.makedirs(docs)
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        r = self.ok(self.post("/api/admin/folders", {"path": "Docs", "chats": [{"id": g, "mode": "ro"}]}), 201)
        fid = r["folders"][0]["chats"][0]["linkId"]
        shutil.rmtree(docs)
        os.makedirs(config.SHARE_DIR, exist_ok=True)
        os.symlink(config.SHARE_DIR, docs)
        self.assertEqual(self.get(f"/api/folders/{fid}/list", TARUN).status_code, 404)

    def test_disappearing_files_never_to_deleted(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"a", NISHA), 201)
        m = self.send(self.hh, "", NISHA, attachmentIds=[up["id"]], expiresIn=3600)
        self.ok(self.delete(f"/api/messages/{m['id']}", NISHA))
        deleted = os.path.join(config.SHARE_DIR, "_deleted")
        self.assertFalse(os.path.isdir(deleted) and any(fs for _, _, fs in os.walk(deleted)))
        g = self.ok(self.post("/api/conversations", {"name": "G"}, NISHA), 201)["id"]
        up = self.ok(self.upload(g, "b.txt", b"b", NISHA), 201)
        self.send(g, "", NISHA, attachmentIds=[up["id"]], expiresIn=3600)
        self.ok(self.delete(f"/api/conversations/{g}", NISHA, json={"confirmName": "G"}))
        self.assertFalse(any("_disappearing" in d for d, _, _ in os.walk(deleted)))

    def test_sweep_includes_underscore_chat_names(self):
        g = self.ok(self.post("/api/conversations", {"name": "_Kids"}, NISHA), 201)["id"]
        folder = sql("SELECT folder FROM conversations WHERE id = ?", (g,))[0]["folder"]
        stray = os.path.join(config.SHARE_DIR, folder, "_disappearing", "20000101T0000Z__x.txt")
        os.makedirs(os.path.dirname(stray))
        with open(stray, "w") as f:
            f.write("x")
        disappearing.run_expiry()
        self.assertFalse(os.path.exists(stray))

    def test_scheduled_disappearing_not_in_backup(self):
        at = config.iso(config.utcnow() + timedelta(hours=2))
        up = self.ok(self.upload(self.hh, "later.txt", b"later", NISHA), 201)
        self.ok(self.post(f"/api/conversations/{self.hh}/scheduled", {"sendAt": at, "body": "future secret", "expiresIn": 3600,
                                                                      "attachmentIds": [up["id"]]}, NISHA), 201)
        z = zipfile.ZipFile(io.BytesIO(self.get("/api/admin-storage-download-db?includeFiles=true").content))
        self.assertFalse(any(n.endswith("later.txt") for n in z.namelist()))
        path = os.path.join(config.DATA_DIR, "c.db")
        with open(path, "wb") as f:
            f.write(z.read("chat.db"))
        with open(path, "rb") as f:
            self.assertNotIn(b"future secret", f.read())
        os.remove(path)

    def test_scheduled_poll_closing_before_sending(self):
        at = config.iso(config.utcnow() + timedelta(days=1))
        closes = config.iso(config.utcnow() + timedelta(hours=2))
        self.assertEqual(self.post(f"/api/conversations/{self.hh}/scheduled",
                                   {"sendAt": at, "poll": {"question": "Q", "options": ["a", "b"], "closesAt": closes}}, NISHA).status_code, 422)
