"""The 13 Oct 2026 review's speed fixes: the same answers as before, with fewer queries, no file work inside
write transactions, nothing slow on the event loop, and less sent to every page."""
import _env  # noqa: F401

import inspect
import os
import sqlite3
import threading
from unittest import mock

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import auth, calls, chats, config, db, disappearing, exporter, files, housekeeping, presence
from app.live import hub
from app.routers import admin as admin_router
from app.routers import stream


def writer_free() -> bool:
    """Can another connection start writing right now (nobody holds a write transaction)?"""
    c = sqlite3.connect(config.DB_PATH, timeout=0)
    try:
        c.execute("BEGIN IMMEDIATE")
        c.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


class Seeded(ApiTestCase):
    """A household with groups, direct chats, a personal room, files, polls, calls, cards, deletions, drafts,
    mentions, a child and someone who lost access."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.hh = self.household()
        self.g = self.ok(self.post("/api/conversations", {"name": "Trip", "memberIds": [TARUN["id"], LEELA["id"]]}, NISHA), 201)["id"]
        self.d1 = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.d2 = self.ok(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, NISHA))["id"]
        self.d3 = self.ok(self.post("/api/conversations/direct", {"userId": ADMIN["id"]}, NISHA))["id"]
        self.send(self.hh, "hello *all*", NISHA)
        self.send(self.hh, "@Nisha yes @everyone", TARUN, mentions=[NISHA["id"]])
        up = self.ok(self.upload(self.g, "plan.pdf", b"%PDF-1 plan", TARUN), 201)
        self.send(self.g, "", TARUN, attachmentIds=[up["id"]])
        vo = self.ok(self.upload(self.d1, "v.webm", b"webm", TARUN, voice="true", duration="3"), 201)
        self.send(self.d1, "", TARUN, attachmentIds=[vo["id"]])
        self.ok(self.post(f"/api/conversations/{self.g}/polls", {"question": "When?", "options": ["Sat", "Sun"]}, LEELA), 201)
        m = self.send(self.d2, "oops", LEELA)
        self.ok(self.delete(f"/api/messages/{m['id']}", LEELA))
        self.send(self.d2, "reply", NISHA, replyTo=m["id"])
        self.send(self.d3, "admin here", ADMIN)
        self.send(self.personal(NISHA), "my note", NISHA)
        self.ok(self.put(f"/api/conversations/{self.hh}/draft", {"body": "unsent"}, NISHA))
        self.ok(self.put(f"/api/conversations/{self.g}/me", {"mutedUntil": config.iso(config.utcnow() + __import__("datetime").timedelta(days=30)), "pinned": True}, NISHA))
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        calls.reset()
        c = self.ok(self.post("/api/calls", {"conversationId": self.d1, "kind": "video"}, TARUN), 201)
        self.ok(self.post(f"/api/calls/{c['id']}/end", user=TARUN))
        calls.reset()
        with db.get_conn() as conn:
            out = chats.Outbox()
            card = {"app": "household_docs", "badge": "Docs", "type": "note", "item_id": "n1", "title": "Packing list",
                    "panel": "/a1b2c3d4_household_docs", "target": "/doc/n1"}
            chats.post_card(conn, out, chats.conv_row(conn, self.g), {"id": TARUN["id"], "is_child": False}, card)
        out.flush()
        self.ok(self.patch(f"/api/admin/people/{LEELA['id']}", {"isChild": True}))
        self.ok(self.patch(f"/api/admin/people/{ADMIN['id']}", {"disabled": True}))


class BatchedAnswersTests(Seeded):
    """CP3: the chat list, the replay after a reconnect, starred messages and the admin overview — the same
    answer as the one-by-one versions, with a fixed number of queries."""

    def test_chat_list_same_as_one_by_one(self):
        for user in (NISHA, TARUN, LEELA):
            with db.get_conn() as conn:
                rows = conn.execute("SELECT c.*, m.role, m.joined_message_id, m.last_read_id, m.notify, m.muted_until, "
                                    "m.pinned, m.user_id, m.joined_at, m.conversation_id FROM members m JOIN conversations c "
                                    "ON c.id = m.conversation_id WHERE m.user_id = ?", (user["id"],)).fetchall()
                one_by_one = [chats.conversation_out(conn, r, r, user["id"]) for r in rows]
                batched = chats.conversations_out(conn, rows, user["id"])
            self.assertEqual(batched, one_by_one, user["name"])
            self.assertGreater(len(batched), 3)
        # with children allowed to message each other, and without
        sql("UPDATE users SET is_child = 1 WHERE id = ?", (NISHA["id"],))
        with db.get_conn() as conn:
            rows = conn.execute("SELECT c.*, m.role, m.joined_message_id, m.last_read_id, m.notify, m.muted_until, m.pinned, "
                                "m.user_id, m.joined_at, m.conversation_id FROM members m JOIN conversations c ON c.id = m.conversation_id "
                                "WHERE m.user_id = ?", (NISHA["id"],)).fetchall()
            self.assertEqual(chats.conversations_out(conn, rows, NISHA["id"]),
                             [chats.conversation_out(conn, r, r, NISHA["id"]) for r in rows])

    def test_chat_list_queries_dont_grow_with_chats(self):
        def count():
            n = [0]
            with db.get_conn() as conn:
                conn.set_trace_callback(lambda _s: n.__setitem__(0, n[0] + 1))
                chats.list_conversations(conn, NISHA["id"])
            return n[0]
        before = count()
        for i in range(10):
            self.ok(self.post("/api/conversations", {"name": f"G{i}", "memberIds": [TARUN["id"]]}, NISHA), 201)
        self.assertLessEqual(count(), before + 2)

    def test_replay_and_starred_same_as_one_by_one(self):
        for m in self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["messages"]:
            if m["kind"] != "system":
                self.ok(self.put(f"/api/messages/{m['id']}/star", user=NISHA))
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT m.*, mb.joined_message_id AS vf FROM messages m JOIN members mb ON mb.conversation_id = m.conversation_id "
                "AND mb.user_id = ? WHERE m.id > 0 AND m.id > mb.joined_message_id ORDER BY m.id", (NISHA["id"],)).fetchall()
            old = [stream.format_event("message", chats.messages_out(conn, [r], None, r["vf"])[0], r["id"]) for r in rows]
            old_starred = [chats.messages_out(conn, [r], NISHA["id"], r["vf"])[0] for r in rows]
            self.assertEqual(chats.messages_out_by_join(conn, rows, NISHA["id"]), old_starred)
        self.assertEqual(stream.replay(NISHA["id"], 0), old)
        self.assertEqual(len(self.ok(self.get("/api/me/starred", NISHA))["messages"]), 2)

    def test_admin_overview_same_as_one_by_one(self):
        def old_overview(conn):
            out = []
            for c in conn.execute("SELECT * FROM conversations ORDER BY kind, COALESCE(last_activity_at, created_at) DESC").fetchall():
                members = conn.execute("SELECT u.id, u.name, u.disabled FROM members m JOIN users u ON u.id = m.user_id "
                                       "WHERE m.conversation_id = ? ORDER BY u.name COLLATE NOCASE", (c["id"],)).fetchall()
                size = files.usage_bytes(conn, c["id"])
                count = conn.execute("SELECT COUNT(*) FROM attachments WHERE conversation_id = ? AND message_id IS NOT NULL",
                                     (c["id"],)).fetchone()[0]
                if c["kind"] == "personal":
                    owner = chats.user_row(conn, c["created_by"])
                    out.append({"id": c["id"], "kind": "personal", "name": f"{owner['name'] if owner else 'Someone'}'s room",
                                "bytes": size, "files": count})
                    continue
                enabled = [m for m in members if not m["disabled"]]
                name = c["name"] if c["kind"] == "group" else "Direct: " + " & ".join(m["name"] for m in members)
                out.append({"id": c["id"], "kind": c["kind"], "name": name, "household": bool(c["is_household"]),
                            "members": [m["name"] + (" (no access)" if m["disabled"] else "") for m in members],
                            "createdAt": c["created_at"], "lastActivityAt": c["last_activity_at"],
                            "messages": conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND kind != 'system' "
                                                     "AND deleted_at IS NULL", (c["id"],)).fetchone()[0],
                            "bytes": size, "files": count, "canDelete": c["kind"] == "group" and not enabled})
            return out
        with db.get_conn() as conn:
            self.assertEqual(admin_router._overview(conn), old_overview(conn))

    def test_chat_download_in_batches_is_the_same(self):
        for i in range(12):
            self.send(self.g, f"line {i}", NISHA)
        page = lambda: __import__("zipfile").ZipFile(exporter.build(self.g, {"id": NISHA["id"]}, None, None)[0]).read("Trip.html")  # noqa: E731
        exporter.export_limit.reset()
        whole = page()
        with mock.patch.object(exporter, "BATCH", 3):
            batched = page()
        exporter.export_limit.reset()
        strip = lambda b: b.split(b"</h1>", 1)[1].split(b"</div>", 1)[1]           # noqa: E731 (minus the time it was made)
        self.assertEqual(strip(batched), strip(whole))
        self.assertIn(b"line 11", whole)
        exporter.export_limit.reset()
        import zipfile
        z = zipfile.ZipFile(exporter.build(self.d1, {"id": NISHA["id"]}, None, None)[0])
        html_page = z.read([n for n in z.namelist() if n.endswith(".html")][0]).decode()
        self.assertIn("📹 Missed video call", html_page)          # CI10: a video call is called one (it said "📞 Call")
        exporter.export_limit.reset()
        for f in os.listdir(config.DATA_DIR):
            if f.startswith(".export-"):
                os.remove(os.path.join(config.DATA_DIR, f))


class NoFileWorkInTransactionsTests(ApiTestCase):
    """CP1: files are moved, renamed and looked at with no write transaction open."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def test_identity_is_a_plain_function_and_writes_only_when_needed(self):
        self.assertFalse(inspect.iscoroutinefunction(auth.get_current_user))
        self.assertFalse(inspect.iscoroutinefunction(auth.require_user))
        self.assertFalse(inspect.iscoroutinefunction(auth.require_admin))
        recent = config.now_iso()
        sql("UPDATE users SET last_seen = ? WHERE id = ?", (recent, NISHA["id"]))
        self.ok(self.get("/api/me", NISHA))
        self.assertEqual(sql("SELECT last_seen FROM users WHERE id = ?", (NISHA["id"],))[0]["last_seen"], recent)
        sql("UPDATE users SET last_seen = '2000-01-01T00:00:00.000Z' WHERE id = ?", (NISHA["id"],))
        self.ok(self.get("/api/me", NISHA))
        self.assertNotEqual(sql("SELECT last_seen FROM users WHERE id = ?", (NISHA["id"],))[0]["last_seen"], "2000-01-01T00:00:00.000Z")

    def test_a_new_name_renames_folders_after_saving_it(self):
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        up = self.ok(self.upload(d, "a.txt", b"a", NISHA), 201)
        self.send(d, "", NISHA, attachmentIds=[up["id"]])
        seen = []
        real = os.rename

        def rename(a, b):
            seen.append(sql("SELECT name FROM users WHERE id = ?", (NISHA["id"],))[0]["name"])
            return real(a, b)
        with mock.patch.object(files.os, "rename", rename):
            self.ok(self.get("/api/me", {**NISHA, "display": "Nisha K"}))
        self.assertIn("Nisha K", seen)                      # the new name was saved before the folder moved
        folder = sql("SELECT folder FROM conversations WHERE id = ?", (d,))[0]["folder"]
        self.assertIn("Nisha K", folder)
        self.assertEqual(self.get(f"/api/files/{up['id']}", TARUN).status_code, 200)

    def test_deleting_moves_files_after_the_transaction(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"a", NISHA), 201)
        m = self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        free = []
        real = files.move_to_deleted

        def saved():          # what another connection sees: the deletion is already committed
            c = sqlite3.connect(config.DB_PATH)
            try:
                return c.execute("SELECT deleted_at IS NOT NULL FROM messages WHERE id = ?", (m["id"],)).fetchone()[0] == 1
            finally:
                c.close()
        with mock.patch.object(files, "move_to_deleted", lambda rel: free.append(writer_free() and saved()) or real(rel)):
            self.ok(self.delete(f"/api/messages/{m['id']}", NISHA))
        self.assertEqual(free, [True])
        self.assertTrue(any("a.txt" in fs for _, _, fs in os.walk(os.path.join(config.SHARE_DIR, files.DELETED))))
        # a whole group
        g = self.ok(self.post("/api/conversations", {"name": "Gone", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        free.clear()
        real_folder = files.move_folder_to_deleted

        def gone():
            c = sqlite3.connect(config.DB_PATH)
            try:
                return c.execute("SELECT COUNT(*) FROM conversations WHERE id = ?", (g,)).fetchone()[0] == 0
            finally:
                c.close()
        with mock.patch.object(files, "move_folder_to_deleted", lambda f: free.append(writer_free() and gone()) or real_folder(f)):
            self.ok(self.delete(f"/api/conversations/{g}", NISHA, json={"confirmName": "Gone"}))
        self.assertEqual(free, [True])

    def test_missing_files_are_looked_at_outside_a_transaction(self):
        for name in ("a.txt", "b.txt", "c.txt"):
            up = self.ok(self.upload(self.hh, name, b"a", NISHA), 201)
            self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        first = sql("SELECT rel_path FROM attachments ORDER BY rowid")[0]["rel_path"]
        os.remove(os.path.join(config.SHARE_DIR, first))           # the first one looked at is gone
        free = []
        real = os.path.isfile
        with mock.patch.object(files.os.path, "isfile", lambda p: free.append(writer_free()) or real(p)):
            self.assertEqual(files.check_missing(), 1)
        self.assertEqual(free, [True, True, True])
        self.assertEqual([r["missing"] for r in sql("SELECT missing FROM attachments ORDER BY rowid")], [1, 0, 0])

    def test_sharing_a_folder_walks_it_outside_a_transaction(self):
        docs = os.path.join(config.SHARE_ROOT, "Docs")
        os.makedirs(docs)
        with open(os.path.join(docs, "a.pdf"), "wb") as f:
            f.write(b"%PDF")
        free = []
        real = os.stat
        from app import shared_folders

        def stat(p, *a, **kw):
            if str(p).endswith("a.pdf"):
                free.append(writer_free())
            return real(p, *a, **kw)
        with mock.patch.object(shared_folders.os, "stat", stat):
            self.ok(self.post("/api/admin/folders", {"path": "Docs", "chats": [{"id": self.hh, "mode": "ro"}]}), 201)
        self.assertEqual(free, [True])
        self.assertEqual(sql("SELECT rel FROM folder_files"), [{"rel": "a.pdf"}])


class PresenceTests(ApiTestCase):
    """CP4: someone coming online is one small event with their state, not "everyone reload everything"."""

    def test_online_event_carries_the_person(self):
        self.enable(ADMIN, NISHA, TARUN)
        got = []
        real = hub.publish

        def publish(uids, name, data, event_id=None):
            got.append((name, data))
            return real(uids, name, data, event_id)
        with mock.patch.object(hub, "publish", publish):
            presence.publish_online(TARUN["id"])
            sql("UPDATE users SET hide_online = 1 WHERE id = ?", (NISHA["id"],))
            presence.publish_online(NISHA["id"])
        [(name, first), (_, hidden)] = [g for g in got if g[0] == "presence"]
        self.assertEqual(list(first["online"]), [TARUN["id"]])
        self.assertEqual(set(first["online"][TARUN["id"]]), {"online", "lastSeen"})
        self.assertEqual(hidden, {"online": {NISHA["id"]: None}})          # hidden: nothing about them
        # the hub's callback hands the work to a thread (it may be called on the event loop)
        done = threading.Event()
        with mock.patch.object(presence, "publish_online", lambda uid: done.set()):
            presence.online_changed(TARUN["id"], True)
            self.assertTrue(done.wait(5))


class CallSpeedTests(ApiTestCase):
    """CP5: a relay that failed isn't asked again for a minute; a polling page in a call doesn't get the
    STUN/relay addresses each second."""

    def setUp(self):
        super().setUp()
        calls.reset()
        calls.reset_relay_cache()
        self.enable(ADMIN, NISHA, TARUN)
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True, "calls_relay": "cloudflare", "calls_cf_key_id": "k",
                                                 "calls_cf_api_token": "t", "calls_stun": "stun:stun.example.com:3478"}))
        self.d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]

    def tearDown(self):
        calls.reset()
        calls.reset_relay_cache()
        super().tearDown()

    def test_failure_cached_and_ice_only_when_asked(self):
        asked = []

        def fail(key_id, token, ttl=calls.CF_TTL):
            asked.append(1)
            raise RuntimeError("HTTP 500 from Cloudflare: down")
        with mock.patch.object(calls, "cf_fetch", fail):
            c = self.ok(self.post("/api/calls", {"conversationId": self.d}, NISHA), 201)
            self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
            for _ in range(5):
                cur = self.ok(self.get("/api/calls/current", NISHA))["call"]
            self.assertEqual(len(asked), 1)
            self.assertEqual(cur["iceServers"], [{"urls": "stun:stun.example.com:3478"}])
            self.assertNotIn("iceServers", self.ok(self.get("/api/calls/current?ice=0", NISHA))["call"])
            calls._cf["failed_until"] = 0.0               # a minute later it's asked again
            self.ok(self.get("/api/calls/current", NISHA))
            self.assertEqual(len(asked), 2)


class HousekeepingSpeedTests(ApiTestCase):
    """CP7: disappearing messages expire every tick; the sweep of every chat's folder and the full clean of the
    index and the write-ahead log every 5 minutes."""

    def test_sweep_and_full_clean_every_fifth_minute(self):
        self.enable(ADMIN, NISHA, TARUN)
        hh = self.household()
        sweeps, cleans = [], []
        real_clean = disappearing.clean
        with mock.patch.object(files, "sweep_disappearing", lambda stamp: sweeps.append(stamp) or 0), \
                mock.patch.object(disappearing, "clean", lambda full: cleans.append(full) or real_clean(full)):
            m = self.send(hh, "soon gone", NISHA, expiresIn=3600)
            sql("UPDATE messages SET expires_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (m["id"],))
            housekeeping.tick(3)
            self.assertEqual(sql("SELECT id FROM messages WHERE id = ?", (m["id"],)), [])     # gone at once
            self.assertEqual((sweeps, cleans), ([], [False]))                                  # a small clean only
            housekeeping.tick(4)
            self.assertEqual((sweeps, cleans), ([], [False]))                                  # nothing deleted: nothing
            housekeeping.tick(17)                                                              # 17 % 15 == 2
            self.assertEqual((len(sweeps), cleans), (1, [False, True]))
            housekeeping.tick(32)
            self.assertEqual((len(sweeps), cleans), (2, [False, True]))                        # clean: nothing to do


class SmallerThingsTests(ApiTestCase):
    """CP8: synchronous=NORMAL in WAL; versioned assets cached for good; the page itself never."""

    def test_sqlite_settings(self):
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)          # NORMAL
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA secure_delete").fetchone()[0], 1)

    def test_static_caching(self):
        r = self.client.get(f"/chat.js?v={config.APP_VERSION}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertNotIn("immutable", self.client.get("/chat.js").headers.get("cache-control", ""))
        self.assertIn("no-store", self.client.get("/").headers["cache-control"])
        self.assertNotIn("immutable", self.client.get(f"/nope.js?v={config.APP_VERSION}").headers.get("cache-control", ""))
        self.assertEqual(self.get("/api/me", NISHA).headers["cache-control"], "no-store")
