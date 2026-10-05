import asyncio
import json
import time
from datetime import timedelta

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import chats, config, db, live, notifier, presence
from app.live import hub


class NotifyTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()
        self.notify_to(TARUN, "mobile_app_tarun")

    def test_levels_and_overrides(self):
        # default level: direct and mentions only
        self.send(self.hh, "hello all", NISHA)
        self.assertEqual(self.sent.items, [])
        self.send(self.hh, "@Tarun look", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(len(self.sent.items), 1)
        self.assertEqual(self.sent.items[0]["title"], "Household")
        self.assertEqual(self.sent.items[0]["message"], "Nisha: @Tarun look")
        self.assertEqual(self.sent.items[0]["services"], ["mobile_app_tarun"])
        notifier.reset()
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.send(d, "*psst*", NISHA)
        self.assertEqual(self.sent.items[-1]["title"], "Nisha")
        self.assertEqual(self.sent.items[-1]["message"], "psst")
        # per-chat override: all
        notifier.reset()
        self.ok(self.put(f"/api/conversations/{self.hh}/me", {"notify": "all"}, TARUN))
        self.send(self.hh, "anything", NISHA)
        self.assertEqual(self.sent.items[-1]["message"], "Nisha: anything")
        # muted
        notifier.reset()
        n = len(self.sent.items)
        self.ok(self.put(f"/api/conversations/{self.hh}/me", {"mutedUntil": config.iso(config.utcnow() + timedelta(hours=8))}, TARUN))
        self.send(self.hh, "muted?", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(len(self.sent.items), n)
        # level off
        self.ok(self.put(f"/api/conversations/{self.hh}/me", {"mutedUntil": None, "notify": "default"}, TARUN))
        self.ok(self.put("/api/me/settings", {"notifyLevel": "off"}, TARUN))
        self.send(d, "off?", NISHA)
        self.assertEqual(len(self.sent.items), n)
        # replies to my message count
        self.ok(self.put("/api/me/settings", {"notifyLevel": "direct_mentions"}, TARUN))
        mine = self.send(self.hh, "mine", TARUN)
        notifier.reset()
        self.send(self.hh, "re", NISHA, replyTo=mine["id"])
        self.assertEqual(self.sent.items[-1]["message"], "Nisha: re")

    def test_notifications_open_the_apps_real_page(self):
        from app import ha_client
        orig = config.INGRESS_URL
        try:
            # from the Supervisor: the sidebar page /<full slug> (the old /hassio/ingress/… is a 404 now)
            self.assertEqual(ha_client.learn_page_blocking({"slug": "a1b2c3d4_household_chat", "ingress_panel": True}),
                             "/a1b2c3d4_household_chat")
            self.assertEqual(config.INGRESS_URL, "/a1b2c3d4_household_chat")
            d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
            self.send(d, "hi", NISHA)
            self.assertEqual(self.sent.items[-1]["data"]["clickAction"], "/a1b2c3d4_household_chat/chat/" + d)   # §15.14
            self.assertEqual(ha_client.learn_page_blocking({"slug": "local_household_chat", "ingress_panel": False}),
                             "/app/local_household_chat")
            # without the Supervisor: the container's host name
            self.assertEqual(ha_client.learn_page_blocking({}, host="a1b2c3d4-household-chat"), "/a1b2c3d4_household_chat")
            for other in ("", "my-laptop", "a1b2c3d4-household-docs", "x/../household-chat"):
                self.assertEqual(ha_client.learn_page_blocking({"slug": other.replace("-", "_")}, host=other), "/household_chat")
        finally:
            config.INGRESS_URL = orig

    def test_preview_levels_and_buttons(self):
        self.ok(self.put("/api/me/settings", {"notifyPreview": "sender"}, TARUN))
        self.send(self.hh, "secret", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(self.sent.items[-1]["message"], "New message from Nisha")
        data = self.sent.items[-1]["data"]
        self.assertEqual(data["clickAction"], config.chat_link(self.hh))
        self.assertEqual([a["title"] for a in data["actions"]], ["Reply", "Mark as read"])
        notifier.reset()
        self.ok(self.put("/api/me/settings", {"notifyPreview": "full"}, TARUN))
        self.ok(self.put("/api/admin/settings", {"notify_preview": "none", "notification_reply": False}))
        self.send(self.hh, "secret", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(self.sent.items[-1]["message"], "New message in Household Chat")
        self.assertNotIn("actions", self.sent.items[-1]["data"])

    def test_never_to_sender_disabled_or_removed(self):
        self.notify_to(NISHA, "mobile_app_nisha")
        self.ok(self.put("/api/me/settings", {"notifyLevel": "all"}, NISHA))
        self.send(self.hh, "mine", NISHA)
        self.assertEqual([s for s in self.sent.items if "mobile_app_nisha" in s["services"]], [])
        self.ok(self.put("/api/me/settings", {"notifyLevel": "all"}, TARUN))
        self.ok(self.post(f"/api/conversations/{self.hh}/leave", user=TARUN))
        self.send(self.hh, "gone", NISHA)
        self.assertEqual(self.sent.items, [])

    def test_batching_and_read_cancels(self):
        self.ok(self.put("/api/me/settings", {"notifyLevel": "all"}, TARUN))
        self.send(self.hh, "one", NISHA)
        self.send(self.hh, "two", NISHA)
        m3 = self.send(self.hh, "three", NISHA)
        self.assertEqual(len(self.sent.items), 1)
        key = (TARUN["id"], self.hh)
        notifier._held[key]["until"] = time.monotonic() - 1
        notifier.flush()
        self.assertEqual(self.sent.items[-1]["message"], "Nisha: 2 new messages")
        # reading cancels what's held
        self.send(self.hh, "four", NISHA)
        m5 = self.send(self.hh, "five", NISHA)
        self.ok(self.post(f"/api/conversations/{self.hh}/read", {"upTo": m5["id"]}, TARUN))
        self.assertNotIn(key, notifier._held)

    def test_quiet_hours_hold_then_summary(self):
        now = config.local_now()
        start = (now - timedelta(minutes=5)).strftime("%H:%M")
        end = (now + timedelta(minutes=5)).strftime("%H:%M")
        self.ok(self.put("/api/me/settings", {"quietStart": start, "quietEnd": end}, TARUN))
        self.assertEqual(self.put("/api/me/settings", {"quietStart": "22:00"}, TARUN).status_code, 422)
        self.send(self.hh, "night", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(self.sent.items, [])
        notifier.flush()
        self.assertEqual(self.sent.items, [])
        self.ok(self.put("/api/me/settings", {"quietStart": None, "quietEnd": None}, TARUN))
        notifier.flush()
        self.assertEqual(self.sent.items[-1]["message"], "Nisha: night")

    def test_already_looking(self):
        self.ok(self.post("/api/presence", {"tab": "tab-1", "conversationId": self.hh, "visible": True}, TARUN))
        self.send(self.hh, "@Tarun here?", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(self.sent.items, [])
        self.ok(self.post("/api/presence", {"tab": "tab-1", "conversationId": self.hh, "visible": False}, TARUN))
        self.send(self.hh, "@Tarun now?", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(len(self.sent.items), 1)

    def test_reply_from_notification(self):
        self.send(self.hh, "@Tarun milk?", NISHA, mentions=[TARUN["id"]])
        acts = self.sent.items[-1]["data"]["actions"]
        reply, read = acts[0]["action"], acts[1]["action"]
        self.assertEqual(notifier.handle_action(reply, "yes, on my way"), "replied")
        last = self.ok(self.get(f"/api/conversations/{self.hh}/messages", NISHA))["messages"][-1]
        self.assertEqual((last["body"], last["userId"], last["via"]), ("yes, on my way", TARUN["id"], "notification"))
        self.assertEqual(notifier.handle_action(read, None), "read")
        self.assertEqual(notifier.handle_action("HCHAT_REPLY_nope", "x"), "unknown token")
        self.assertEqual(notifier.handle_action("OTHER_THING", "x"), "ignored")
        self.assertEqual(notifier.handle_action(reply, "  "), "refused")
        # expired
        with db.get_conn() as conn:
            conn.execute("UPDATE notify_tokens SET created_at = '2000-01-01T00:00:00.000Z'")
        self.assertEqual(notifier.handle_action(reply, "late"), "expired")
        with db.get_conn() as conn:
            conn.execute("UPDATE notify_tokens SET created_at = ?", (config.now_iso(),))
        # removed or disabled → refused
        self.ok(self.post(f"/api/conversations/{self.hh}/leave", user=TARUN))
        self.assertIn(notifier.handle_action(reply, "after leaving"), ("refused", "unknown token"))


class LiveTests(ApiTestCase):
    def test_events_only_to_members_and_replay(self):
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        g = self.ok(self.post("/api/conversations", {"name": "G", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        got = {}

        async def run():
            conns = {u["id"]: hub.register(u["id"]) for u in (NISHA, TARUN, LEELA)}
            await asyncio.to_thread(self.send, g, "hi", NISHA)
            await asyncio.sleep(0.05)
            for uid, c in conns.items():
                items = []
                while not c.queue.empty():
                    items.append(c.queue.get_nowait())
                got[uid] = items
                hub.unregister(c)
        asyncio.run(run())
        self.assertTrue(any("event: message" in x and '"hi"' in x for x in got[TARUN["id"]]))
        self.assertTrue(any("event: message" in x for x in got[NISHA["id"]]))
        self.assertFalse(any('"hi"' in x for x in got[LEELA["id"]]))
        from app.routers.stream import replay
        first = sql("SELECT MIN(id) AS i FROM messages WHERE conversation_id = ?", (g,))[0]["i"]
        items = replay(TARUN["id"], first - 1)
        self.assertTrue(any('"hi"' in x for x in items))
        self.assertFalse(any('"hi"' in x for x in replay(LEELA["id"], 0)))

    def test_presence_home_away(self):
        self.enable(ADMIN, NISHA)
        from app import ha_client
        orig = ha_client.fetch_states_blocking
        ha_client.fetch_states_blocking = lambda max_age=30: [
            {"entity_id": "person.nisha", "state": "Office", "last_changed": "2026-09-27T10:00:00Z",
             "attributes": {"friendly_name": "Nisha", "user_id": NISHA["id"]}}]
        try:
            self.assertEqual(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.Bad"}).status_code, 422)
            self.assertEqual(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.tarun"}).status_code, 422)
            self.assertEqual(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.nisha"}, NISHA).status_code, 403)
            self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.nisha"}))
            ha = self.ok(self.get("/api/conversations", NISHA))["homeAway"]
            self.assertEqual(ha[NISHA["id"]]["label"], "📍 Office")
            self.ok(self.put("/api/admin/settings", {"show_presence": False}))
            self.assertEqual(self.ok(self.get("/api/conversations", NISHA))["homeAway"], {})
        finally:
            ha_client.fetch_states_blocking = orig

    def test_hide_online(self):
        self.enable(ADMIN, NISHA)
        self.ok(self.post("/api/presence", {"tab": "tab-x", "visible": True}, NISHA))
        online = self.ok(self.get("/api/conversations", ADMIN))["online"]
        self.assertTrue(online[NISHA["id"]]["online"])
        self.ok(self.put("/api/me/settings", {"hideOnline": True}, NISHA))
        self.assertNotIn(NISHA["id"], self.ok(self.get("/api/conversations", ADMIN))["online"])


class PhonesFromHomeAssistantTests(ApiTestCase):
    """Phones, home/away and photos come from the person in Home Assistant (Settings → People)."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()
        self.ha.token = True
        self.ha.notify_services = ["mobile_app_pixel_7", "mobile_app_tarun_s_phone_2", "family_speaker"]
        self.ha.people = [
            {"entity_id": "person.nisha", "name": "Nisha", "user_id": NISHA["id"], "state": "home", "picture": None,
             "phones": [{"name": "Pixel 7", "label": "Nisha's Pixel", "tracker": "device_tracker.pixel_7"},
                        {"name": "Old Tablet", "label": "Old Tablet", "tracker": "device_tracker.old_tablet"}]},
            {"entity_id": "person.tarun", "name": "Tarun", "user_id": TARUN["id"], "state": "not_home", "picture": None,
             "phones": [{"name": "Tarun's Phone", "label": "Tarun's Phone", "tracker": "device_tracker.taruns_phone"}]},
            {"entity_id": "person.leela", "name": "Leela", "user_id": LEELA["id"], "state": "home", "picture": None,
             "phones": [{"name": "Leela Phone", "label": "Leela Phone", "tracker": "device_tracker.leela_phone"}]},
        ]
        self.ha.notify_services.append("mobile_app_leela_phone")
        from app.common import ha_people
        self.ha_people = ha_people
        ha_people.refresh_blocking(True)

    def test_messages_go_to_the_phones_in_home_assistant(self):
        self.assertEqual(self.ha_people.phones_for(NISHA["id"]), ["mobile_app_pixel_7"])        # the tablet has no action
        self.assertEqual(self.ha_people.phones_for(TARUN["id"]), ["mobile_app_tarun_s_phone_2"])  # HA numbered it
        self.assertTrue(self.ok(self.get("/api/me", TARUN))["notifyLinked"])
        self.assertTrue(self.ok(self.get("/api/whoami", NISHA))["notifyLinked"])
        self.assertFalse(self.ok(self.get("/api/me", ADMIN))["notifyLinked"])                   # no person linked
        self.send(self.hh, "@Tarun look", NISHA, mentions=[TARUN["id"]])
        self.assertEqual(self.sent.items[-1]["services"], ["mobile_app_tarun_s_phone_2"])
        # an extra service added here goes too; the phone isn't sent twice
        self.notify_to(TARUN, "family_speaker")
        self.notify_to(TARUN, "mobile_app_tarun_s_phone_2")
        notifier.reset()
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        self.send(d, "hi", NISHA)
        self.assertEqual(self.sent.items[-1]["services"], ["mobile_app_tarun_s_phone_2", "family_speaker"])

    def test_disabled_person_gets_nothing(self):
        self.seen(LEELA)                                         # synced, not enabled: phone known, no access
        self.assertEqual(self.ha_people.phones_for(LEELA["id"]), ["mobile_app_leela_phone"])
        sql("UPDATE users SET notify_level = 'all' WHERE id = ?", (LEELA["id"],))
        self.send(self.hh, "everyone", NISHA, mentions=[LEELA["id"]])
        self.assertFalse(notifier.send_to_user(LEELA["id"], "Reminder", "x"))
        self.assertEqual([s for s in self.sent.items if "mobile_app_leela_phone" in s["services"]], [])
        # enabled, then disabled again: nothing more
        self.enable(LEELA)
        d = self.ok(self.post("/api/conversations/direct", {"userId": LEELA["id"]}, NISHA))["id"]
        self.send(d, "one", NISHA)
        self.assertEqual(self.sent.items[-1]["services"], ["mobile_app_leela_phone"])
        n = len(self.sent.items)
        self.ok(self.patch(f"/api/admin/people/{LEELA['id']}", {"disabled": True}))
        notifier.reset()
        self.send(self.hh, "@Leela two", NISHA, mentions=[LEELA["id"]])
        self.assertFalse(notifier.send_to_user(LEELA["id"], "Reminder", "x"))
        self.assertEqual(len(self.sent.items), n)

    def test_admin_sees_phones_refreshes_and_tests(self):
        ppl = {p["id"]: p for p in self.ok(self.get("/api/admin/people"))["people"]}
        ha = ppl[NISHA["id"]]["ha"]
        self.assertTrue(ha["known"])
        self.assertEqual((ha["person"], ha["personName"]), ("person.nisha", "Nisha"))
        self.assertEqual([(p["label"], p["service"]) for p in ha["phones"]],
                         [("Nisha's Pixel", "notify.mobile_app_pixel_7"), ("Old Tablet", None)])
        self.assertIsNone(ppl[ADMIN["id"]]["ha"]["person"])                    # no person linked to the login
        self.notify_to(NISHA, "family_speaker")
        r = self.ok(self.post(f"/api/admin/people/{NISHA['id']}/notify/test"))
        self.assertEqual({x["service"] for x in r["results"]}, {"notify.mobile_app_pixel_7", "notify.family_speaker"})
        self.assertEqual(sorted(self.ha.notified), ["family_speaker", "mobile_app_pixel_7"])
        # the phone is Home Assistant's: it can't be removed here
        self.assertEqual(self.delete(f"/api/admin/people/{NISHA['id']}/notify/notify.mobile_app_pixel_7").status_code, 404)
        # phone removed in HA → gone after "Check Home Assistant again"; HA down keeps the last answer
        self.ha.people[0]["phones"] = []
        self.ha.people[1]["phones"] = []
        self.ha.people, saved = None, self.ha.people
        self.ok(self.get("/api/admin/people?refresh=1"))
        self.assertEqual(self.ha_people.phones_for(NISHA["id"]), ["mobile_app_pixel_7"])
        self.ha.people = saved
        ppl = {p["id"]: p for p in self.ok(self.get("/api/admin/people?refresh=1"))["people"]}
        self.assertEqual(ppl[NISHA["id"]]["ha"]["phones"], [])
        self.assertEqual(self.post(f"/api/admin/people/{TARUN['id']}/notify/test").status_code, 409)


class PersonFromLoginTests(ApiTestCase):
    """Home/away and the photo use the person linked to the login; an admin's choice overrides it."""

    def setUp(self):
        super().setUp()
        from app import avatars
        self.enable(ADMIN, NISHA, TARUN)
        self.ha.token = True
        self.ha.states = [
            {"entity_id": "person.nisha", "state": "home", "last_changed": "2026-09-27T10:00:00Z",
             "attributes": {"friendly_name": "Nisha", "user_id": NISHA["id"], "entity_picture": "/api/pic/nisha"}},
            {"entity_id": "person.work_badge", "state": "Office", "last_changed": "2026-09-27T10:00:00Z",
             "attributes": {"friendly_name": "Work badge", "entity_picture": "/api/pic/badge"}}]
        from test_features import jpeg
        self.served = {"/api/pic/nisha": jpeg(64, 64), "/api/pic/badge": jpeg(80, 40)}
        self._dl = avatars.download
        avatars.download = lambda pic: self.served.get(pic)
        self.avatars = avatars
        from app import ha_client
        ha_client.sync_users_blocking()                 # links Nisha's login to person.nisha (users.ha_person)

    def tearDown(self):
        self.avatars.download = self._dl
        super().tearDown()

    def home_away(self):
        presence.refresh_blocking(force=True)
        return self.ok(self.get("/api/conversations", TARUN))["homeAway"]

    def admin_row(self, user):
        return [p for p in self.ok(self.get("/api/admin/people"))["people"] if p["id"] == user["id"]][0]

    def avatar_src(self):
        return sql("SELECT avatar_src FROM users WHERE id = ?", (NISHA["id"],))[0]["avatar_src"]

    def test_automatic_then_override_then_back(self):
        # nothing assigned: their login's person is used
        self.assertEqual(self.home_away()[NISHA["id"]]["label"], "🏠 Home")
        self.avatars.sync_blocking()
        self.assertEqual(self.avatar_src(), "/api/pic/nisha")
        row = self.admin_row(NISHA)
        self.assertEqual((row["presenceEntity"], row["presenceChosen"]), ("person.nisha", False))
        self.assertNotIn(TARUN["id"], self.home_away())                   # no person linked to Tarun's login
        # an admin's choice wins
        self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.work_badge"}))
        self.assertEqual(self.home_away()[NISHA["id"]]["label"], "📍 Office")
        self.assertEqual(self.avatar_src(), "/api/pic/badge")
        row = self.admin_row(NISHA)
        self.assertEqual((row["presenceEntity"], row["presenceChosen"]), ("person.work_badge", True))
        # back to automatic
        self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": None}))
        self.assertEqual(self.home_away()[NISHA["id"]]["label"], "🏠 Home")
        self.assertEqual(self.avatar_src(), "/api/pic/nisha")
        # choosing their own person is the same as automatic
        self.ok(self.put(f"/api/admin/people/{NISHA['id']}/presence", {"entity": "person.nisha"}))
        self.assertFalse(self.admin_row(NISHA)["presenceChosen"])
        self.assertIsNone(sql("SELECT presence_entity FROM users WHERE id = ?", (NISHA["id"],))[0]["presence_entity"])
