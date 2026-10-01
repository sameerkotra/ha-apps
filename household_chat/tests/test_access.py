from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import chats, db
from app.live import hub


class AccessTests(ApiTestCase):
    def test_everyone_starts_disabled(self):
        me = self.ok(self.get("/api/me", NISHA))
        self.assertTrue(me["disabled"])
        self.assertIsNone(me["personalRoomId"])
        self.assertEqual(self.get("/api/conversations", NISHA).status_code, 403)
        self.assertEqual(self.get("/api/people", NISHA).status_code, 403)
        w = self.ok(self.get("/api/whoami", NISHA))
        self.assertTrue(w["disabled"])
        self.assertEqual(self.get("/api/admin/people", NISHA).status_code, 403)

    def test_enable_gives_room_and_household(self):
        self.enable(ADMIN, NISHA)
        convs = self.ok(self.get("/api/conversations", NISHA))["conversations"]
        self.assertEqual(convs[0]["kind"], "personal")
        self.assertEqual(convs[0]["name"], "My room")
        hh = [c for c in convs if c["isHousehold"]]
        self.assertEqual(len(hh), 1)
        # the first enabled person owns the Household group
        self.assertEqual(sql("SELECT role FROM members WHERE conversation_id = ? AND user_id = ?",
                             (hh[0]["id"], ADMIN["id"]))[0]["role"], "owner")
        # exactly one room each
        self.enable(NISHA)
        self.ok(self.get("/api/me", NISHA))
        self.assertEqual(len(sql("SELECT id FROM conversations WHERE kind = 'personal' AND created_by = ?", (NISHA["id"],))), 1)

    def test_personal_room_is_private_even_from_admins(self):
        self.enable(ADMIN, NISHA)
        room = self.personal(NISHA)
        m = self.send(room, "passport number is in the drawer", NISHA)
        up = self.ok(self.upload(room, "notes.txt", b"secret", NISHA), 201)
        self.send(room, "", NISHA, attachmentIds=[up["id"]])
        for path in (f"/api/conversations/{room}", f"/api/conversations/{room}/messages", f"/api/conversations/{room}/files",
                     f"/api/conversations/{room}/pins", f"/api/files/{up['id']}", f"/api/files/{up['id']}/thumb"):
            self.assertEqual(self.get(path, ADMIN).status_code, 404, path)
        self.assertEqual(self.put(f"/api/messages/{m['id']}/pin", user=ADMIN).status_code, 404)
        self.assertEqual(self.put(f"/api/messages/{m['id']}/star", user=ADMIN).status_code, 404)
        self.assertEqual(self.post("/api/messages/forward", {"messageIds": [m["id"]], "conversationIds": [self.household()]},
                                   ADMIN).status_code, 404)
        hits = self.ok(self.get("/api/search?q=passport", ADMIN))
        self.assertEqual(hits["messages"], [])
        self.assertEqual(len(self.ok(self.get("/api/search?q=passport", NISHA))["messages"]), 1)
        # admin overview: name and size only
        ov = self.ok(self.get("/api/admin/conversations"))["conversations"]
        room_row = [c for c in ov if c["id"] == room][0]
        self.assertEqual(room_row["name"], "Nisha's room")
        self.assertEqual(set(room_row), {"id", "kind", "name", "bytes", "files"})
        self.assertNotIn("passport", str(ov))
        self.assertNotIn("notes.txt", str(self.ok(self.get("/api/admin/storage"))))

    def test_personal_room_rules(self):
        self.enable(ADMIN, NISHA)
        room = self.personal(NISHA)
        m = self.send(room, "note", NISHA)
        self.assertEqual(self.post(f"/api/conversations/{room}/members", {"userIds": [ADMIN["id"]]}, NISHA).status_code, 409)
        self.assertEqual(self.post(f"/api/conversations/{room}/leave", user=NISHA).status_code, 409)
        self.assertEqual(self.patch(f"/api/conversations/{room}", {"name": "x"}, NISHA).status_code, 409)
        self.assertEqual(self.delete(f"/api/conversations/{room}", NISHA, json={"confirmName": None}).status_code, 409)
        self.assertEqual(self.post(f"/api/conversations/{room}/polls", {"question": "q", "options": ["a", "b"]}, NISHA).status_code, 409)
        self.assertEqual(self.put(f"/api/messages/{m['id']}/reactions/👍", user=NISHA).status_code, 409)
        # pins work, with no system message
        self.ok(self.put(f"/api/messages/{m['id']}/pin", user=NISHA))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND kind = 'system'", (room,))[0]["n"], 0)
        # opening a direct chat with yourself is your room
        self.assertEqual(self.ok(self.post("/api/conversations/direct", {"userId": NISHA["id"]}, NISHA))["id"], room)
        # never notifies
        self.notify_to(NISHA)
        self.send(room, "another", NISHA)
        self.assertEqual(self.sent.items, [])
        # kept through disable / re-enable
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": True}))
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": False}))
        self.assertEqual(self.personal(NISHA), room)
        self.assertEqual(len(self.ok(self.get(f"/api/conversations/{room}/messages", NISHA))["messages"]), 2)

    def test_personal_room_folder(self):
        self.enable(ADMIN, NISHA)
        room = self.personal(NISHA)
        folder = sql("SELECT folder FROM conversations WHERE id = ?", (room,))[0]["folder"]
        self.assertEqual(folder, f"Nisha - personal ({room[:8]})")

    def test_membership_404_and_admin_is_not_special(self):
        self.enable(ADMIN, NISHA, TARUN)
        g = self.ok(self.post("/api/conversations", {"name": "Just us", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.send(g, "hello", NISHA)
        self.assertEqual(self.get(f"/api/conversations/{g}/messages", ADMIN).status_code, 404)
        self.assertEqual(self.post(f"/api/conversations/{g}/messages", {"body": "hi"}, ADMIN).status_code, 404)
        # an admin can't add themselves
        self.assertEqual(self.post(f"/api/conversations/{g}/members", {"userIds": [ADMIN["id"]]}, ADMIN).status_code, 404)
        # admin overview shows names, not text
        ov = self.ok(self.get("/api/admin/conversations"))["conversations"]
        row = [c for c in ov if c["id"] == g][0]
        self.assertEqual(row["name"], "Just us")
        self.assertEqual(row["messages"], 1)
        self.assertNotIn("hello", str(ov))
        self.assertFalse(row["canDelete"])
        self.assertEqual(self.delete(f"/api/admin/conversations/{g}", json={"confirmName": "Just us"}).status_code, 409)
        # non-admins can't see the overview
        self.assertEqual(self.get("/api/admin/conversations", NISHA).status_code, 403)

    def test_admin_can_delete_orphan_group(self):
        self.enable(ADMIN, NISHA, TARUN)
        g = self.ok(self.post("/api/conversations", {"name": "Old", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": True}))
        self.ok(self.patch(f"/api/admin/people/{TARUN['id']}", {"disabled": True}))
        row = [c for c in self.ok(self.get("/api/admin/conversations"))["conversations"] if c["id"] == g][0]
        self.assertTrue(row["canDelete"])
        self.assertEqual(self.delete(f"/api/admin/conversations/{g}", json={"confirmName": "old"}).status_code, 422)
        self.ok(self.delete(f"/api/admin/conversations/{g}", json={"confirmName": "Old"}))
        self.assertEqual(sql("SELECT action FROM audit_log WHERE conversation_id = ?", (g,))[-1]["action"], "admin_deleted_chat")

    def test_disable_cuts_access_and_hands_over(self):
        self.enable(ADMIN, NISHA, TARUN)
        g = self.ok(self.post("/api/conversations", {"name": "Trip", "memberIds": [TARUN["id"], ADMIN["id"]]}, NISHA), 201)["id"]
        self.ok(self.patch(f"/api/conversations/{g}/members/{TARUN['id']}", {"role": "admin"}, NISHA))
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.send(d, "hi Tarun", NISHA)
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": True}))
        self.assertEqual(self.get("/api/conversations", NISHA).status_code, 403)
        # Tarun (the oldest group admin) is the owner now
        self.assertEqual(sql("SELECT role FROM members WHERE conversation_id = ? AND user_id = ?", (g, TARUN["id"]))[0]["role"], "owner")
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM members WHERE conversation_id = ? AND user_id = ?", (g, NISHA["id"]))[0]["n"], 0)
        # the direct chat is read-only for Tarun, and shows Nisha as no longer here
        conv = [c for c in self.ok(self.get("/api/conversations", TARUN))["conversations"] if c["id"] == d][0]
        self.assertTrue(conv["readOnly"])
        self.assertEqual(conv["name"], "Nisha (no longer here)")
        self.assertEqual(self.post(f"/api/conversations/{d}/messages", {"body": "?"}, TARUN).status_code, 409)
        # re-enabled: back in direct chats and the Household, not other groups
        self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": False}))
        ids = {c["id"] for c in self.ok(self.get("/api/conversations", NISHA))["conversations"]}
        self.assertIn(d, ids)
        self.assertIn(self.household(), ids)
        self.assertNotIn(g, ids)

    def test_direct_chat_unique_and_hidden_until_first_message(self):
        self.enable(ADMIN, NISHA)
        a = self.ok(self.post("/api/conversations/direct", {"userId": NISHA["id"]}, ADMIN))["id"]
        b = self.ok(self.post("/api/conversations/direct", {"userId": ADMIN["id"]}, NISHA))["id"]
        self.assertEqual(a, b)
        # (Nisha opened it herself above, so make a fresh pair to check hiding)
        self.enable(TARUN)
        c = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, ADMIN))["id"]
        self.assertNotIn(c, {x["id"] for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]})
        self.send(c, "hello", ADMIN)
        self.assertIn(c, {x["id"] for x in self.ok(self.get("/api/conversations", TARUN))["conversations"]})
        # nobody can be added to a direct chat
        self.assertEqual(self.post(f"/api/conversations/{c}/members", {"userIds": [NISHA["id"]]}, ADMIN).status_code, 409)

    def test_group_roles(self):
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        g = self.ok(self.post("/api/conversations", {"name": "Family", "icon": "👪", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        # a member can't add or rename
        self.assertEqual(self.post(f"/api/conversations/{g}/members", {"userIds": [LEELA["id"]]}, TARUN).status_code, 403)
        self.assertEqual(self.patch(f"/api/conversations/{g}", {"name": "X"}, TARUN).status_code, 403)
        self.ok(self.post(f"/api/conversations/{g}/members", {"userIds": [LEELA["id"]]}, NISHA))
        self.ok(self.patch(f"/api/conversations/{g}", {"name": "Family ❤"}, NISHA))
        texts = [m["body"] for m in self.ok(self.get(f"/api/conversations/{g}/messages", NISHA))["messages"] if m["kind"] == "system"]
        self.assertIn("Nisha added Leela", texts)
        self.assertIn("Nisha renamed the group to “Family ❤”", texts)
        # owner leaves: hand-over to the oldest member
        self.ok(self.post(f"/api/conversations/{g}/leave", user=NISHA))
        self.assertEqual(sql("SELECT user_id FROM members WHERE conversation_id = ? AND role = 'owner'", (g,))[0]["user_id"], TARUN["id"])
        self.assertEqual(self.get(f"/api/conversations/{g}", NISHA).status_code, 404)
        # delete needs the exact name
        self.assertEqual(self.delete(f"/api/conversations/{g}", TARUN, json={"confirmName": "family"}).status_code, 422)
        self.ok(self.delete(f"/api/conversations/{g}", TARUN, json={"confirmName": "Family ❤"}))
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM conversations WHERE id = ?", (g,))[0]["n"], 0)

    def test_new_members_history_setting(self):
        self.enable(ADMIN, NISHA, TARUN)
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": []}, NISHA), 201)["id"]
        self.send(g, "before", NISHA)
        self.ok(self.patch(f"/api/conversations/{g}", {"newMembersSeeHistory": False}, NISHA))
        self.ok(self.post(f"/api/conversations/{g}/members", {"userIds": [TARUN["id"]]}, NISHA))
        self.send(g, "after", NISHA)
        bodies = [m["body"] for m in self.ok(self.get(f"/api/conversations/{g}/messages", TARUN))["messages"] if m["kind"] == "text"]
        self.assertEqual(bodies, ["after"])
        self.assertEqual(self.ok(self.get("/api/search?q=before", TARUN))["messages"], [])

    def test_who_can_create_groups(self):
        self.enable(ADMIN, NISHA)
        self.ok(self.put("/api/admin/settings", {"who_can_create_groups": "admins"}))
        self.assertEqual(self.post("/api/conversations", {"name": "G"}, NISHA).status_code, 403)
        self.ok(self.post("/api/conversations", {"name": "G"}, ADMIN), 201)
        self.assertEqual(self.put("/api/admin/settings", {"nope": 1}).status_code, 422)
        self.assertEqual(self.put("/api/admin/settings", {"max_upload_mb": 0}).status_code, 422)
        self.assertEqual(self.put("/api/admin/settings", {"blocked_extensions": "exe, <b>"}).status_code, 422)

    def test_ingress_only(self):
        from fastapi.testclient import TestClient
        from app.main import app
        c = TestClient(app, client=("10.0.0.5", 1))
        self.assertEqual(c.get("/api/me", headers={"X-Remote-User-Id": "x"}).status_code, 403)

    def test_disable_closes_live_connections(self):
        self.enable(ADMIN, NISHA)
        closed = []
        orig = hub.close_user
        hub.close_user = lambda uid: closed.append(uid)
        try:
            self.ok(self.patch(f"/api/admin/people/{NISHA['id']}", {"disabled": True}))
        finally:
            hub.close_user = orig
        self.assertEqual(closed, [NISHA["id"]])


class ReviewRegressions(AccessTests.__bases__[0]):
    def test_starred_hides_quotes_from_before_joining(self):
        self.enable(ADMIN, NISHA, TARUN)
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [NISHA["id"]]}, ADMIN), 201)["id"]
        self.ok(self.patch(f"/api/conversations/{g}", {"newMembersSeeHistory": False}, ADMIN))
        secret = self.send(g, "SECRET before tarun", ADMIN)
        self.ok(self.post(f"/api/conversations/{g}/members", {"userIds": [TARUN["id"]]}, ADMIN))
        reply = self.send(g, "re", NISHA, replyTo=secret["id"])
        self.ok(self.put(f"/api/messages/{reply['id']}/star", user=TARUN))
        starred = self.ok(self.get("/api/me/starred", TARUN))
        self.assertNotIn("SECRET", str(starred))
        self.assertTrue(starred["messages"][0]["replyTo"]["hidden"])

    def test_blocked_extension_with_trailing_dot(self):
        self.enable(ADMIN)
        hh = self.household()
        self.assertEqual(self.upload(hh, "evil.exe.", b"MZ").status_code, 422)
        self.assertEqual(self.upload(hh, "x.html ", b"<b>").status_code, 422)

    def test_failed_patch_doesnt_move_the_folder(self):
        import os
        from app import config
        self.enable(ADMIN)
        g = self.ok(self.post("/api/conversations", {"name": "G"}, ADMIN), 201)["id"]
        up = self.ok(self.upload(g, "a.txt", b"abc"), 201)
        self.send(g, "", ADMIN, attachmentIds=[up["id"]])
        self.assertEqual(self.patch(f"/api/conversations/{g}", {"name": "New name", "icon": "abc"}).status_code, 422)
        self.assertEqual(self.get(f"/api/files/{up['id']}").status_code, 200)
        self.assertTrue(os.path.isdir(os.path.join(config.SHARE_DIR, f"G ({g[:8]})")))

    def test_personal_room_description(self):
        self.enable(ADMIN)
        room = self.personal(ADMIN)
        self.ok(self.patch(f"/api/conversations/{room}", {"description": "My notes"}))
        self.assertEqual(self.ok(self.get(f"/api/conversations/{room}"))["description"], "My notes")
