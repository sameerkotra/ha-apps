"""Forwarding messages into Household Chat (spec/SPEC.md §6): the owner's switch and the household's, filters,
the loop guard, duplicates, Android 15's hidden codes, long texts and summaries — and that the text is never stored
or logged. Plus `phones.forwarding_into` for Household Chat."""
import json
import logging
import os
from unittest import mock

from base import ADMIN, ASHA, BOB, PhonesCase

from app import app_messages, config, db, forwarding
from app.common import app_bus as bus

MSGS = "com.google.android.apps.messaging"
CODE_TEXT = "Your one-time code is 482913. Do not share it."


def note(text=CODE_TEXT, title="AX-BANK", package=MSGS, **extra):
    attrs = {"package": package, "android.title": title, "android.text": text, **extra}
    return attrs


class Forwarding(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()

    def notify(self, attrs, state="x"):
        self.report(self.pid, key="last_notification", value=state, attrs=attrs)

    def on(self, **kw):
        body = {"on": True, **kw}
        r = self.put(f"/api/phones/{self.pid}/forwarding", body, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_off_until_the_owner_switches_it_on(self):
        self.notify(note())
        self.assertEqual(self.posted, [])
        self.assertEqual(self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ADMIN).status_code, 403)
        self.on()
        self.notify(note())
        self.assertEqual(len(self.posted), 1)
        d = self.posted[0]
        self.assertEqual((d["requested_by"], d["my_room"], d["disappear_after"]), ("u-asha", True, "1h"))
        self.assertEqual(d["label"], "📱 Asha's Pixel 8 · Messages · 08:00")
        self.assertEqual(d["text"], "AX-BANK\n" + CODE_TEXT)
        # the household's switch
        self.setting(forwarding_allowed=False)
        self.advance(3)
        self.notify(note(text="Code 111222 for login"))
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.put(f"/api/phones/{self.pid}/forwarding", {"on": True}, ASHA).status_code, 403)

    def test_codes_only_filter(self):
        self.on()
        for text, want in (("Your verification code: 4821", True), ("123456 is your OTP", True),
                           ("PIN 99887766 expires soon", True), ("See you at 1830 for dinner", False),
                           ("Order 123456 shipped", False), ("code 12 is too short", False),
                           ("Use passcode 7788 to enter", True)):
            self.posted.clear()
            forwarding.reset()
            self.notify(note(text=text))
            self.assertEqual(bool(self.posted), want, text)

    def test_words_and_everything(self):
        self.assertEqual(self.put(f"/api/phones/{self.pid}/forwarding", {"on": True, "filter": "words"}, ASHA).status_code, 422)
        self.on(filter="words", words=["parcel"])
        self.notify(note(text="Your Parcel arrives today"))
        self.notify(note(text="Dinner?"))
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.put(f"/api/phones/{self.pid}/forwarding", {"filter": "all"}, ASHA).status_code, 422)
        self.on(filter="all", allConfirmed=True)
        self.notify(note(text="Dinner at 8?"))
        self.assertEqual(len(self.posted), 2)

    def test_loop_guard_and_apps(self):
        self.on(filter="all", allConfirmed=True)
        self.notify(note(package="io.homeassistant.companion.android"))
        self.notify(note(package="io.homeassistant.companion.android.minimal"))
        self.notify(note(package="com.whatsapp", text="hi"))
        self.assertEqual(self.posted, [])
        self.on(apps=[MSGS, "com.whatsapp"])
        self.notify(note(package="com.whatsapp", title="Family: Bob", text="Bob: hi"))
        self.assertEqual(self.posted[0]["label"].split(" · ")[1], "WhatsApp")

    def test_duplicates_within_two_minutes(self):
        self.on()
        self.notify(note())
        self.notify(note(package="com.samsung.android.messaging"))         # the same text from another app
        self.assertEqual(len(self.posted), 1)
        self.advance(2.1)
        self.notify(note())
        self.assertEqual(len(self.posted), 2)

    def test_android_15_hidden_code(self):
        self.on()
        self.notify(note(text="Sensitive notification content hidden"))
        self.assertEqual(self.posted[0]["text"], "📱 A code arrived on Asha's phone (08:00, Messages, from AX-BANK) — "
                                                 "Android hid it.")
        item = [c for c in self.get(f"/api/phones/{self.pid}", ASHA).json()["checklist"] if c["id"] == "android15"][0]
        self.assertFalse(item["ok"])
        self.assertIn("RECEIVE_SENSITIVE_NOTIFICATIONS allow", item["adb"])
        self.advance(3)
        self.notify(note(text="", title="Bank"))                           # the empty-text pattern
        self.assertIn("A code arrived", self.posted[1]["text"])
        # fixed: a real message after it clears the warning
        self.advance(3)
        self.notify(note(text="New code 556677"))
        item = [c for c in self.get(f"/api/phones/{self.pid}", ASHA).json()["checklist"] if c["id"] == "android15"][0]
        self.assertIsNone(item["ok"])

    def test_big_text_long_text_and_summaries(self):
        self.on(filter="all", allConfirmed=True)
        self.notify(note(text="Code 1234…", **{"android.bigText": "Code 123456 — the full text of the message"}))
        self.assertEqual(self.posted[0]["text"], "AX-BANK\nCode 123456 — the full text of the message")
        self.advance(3)
        self.notify(note(text="A long message that was cut…"))
        self.assertTrue(self.posted[1]["text"].endswith("(shortened — see the phone)"))
        self.advance(3)
        self.notify(note(text="3 new messages", title="Messages"))
        self.assertEqual(self.posted[2]["text"], "📱 3 new messages on Asha's phone")
        self.advance(3)
        self.report(self.pid, key="last_notification", value="x" * 300, attrs={"package": MSGS})
        self.assertLessEqual(len(self.posted[3]["text"]), 4000)

    def test_several_in_one_second_each_handled(self):
        self.on()
        self.notify(note(text="Code 111111"))
        self.notify(note(text="Code 222222"))
        self.assertEqual([p["text"].split("\n")[1] for p in self.posted], ["Code 111111", "Code 222222"])

    def test_group_target_suggests_codes_only_and_members_check(self):
        self.on(filter="all", allConfirmed=True)
        r = self.on(target="chat", chatId="c-codes", chatName="Phone codes", chatKind="group")
        self.assertEqual(r["forwarding"]["filter"], "codes")
        self.notify(note())
        self.assertEqual(self.posted[0]["chat_id"], "c-codes")
        self.assertNotIn("my_room", self.posted[0])
        self.setting(forward_chats="own_room")
        r = self.put(f"/api/phones/{self.pid}/forwarding", {"target": "chat", "chatId": "c2", "chatName": "Other",
                                                             "chatKind": "group"}, ASHA)
        self.assertEqual(r.status_code, 403)
        self.setting(forward_chats="listed", forward_groups=["Phone codes"])
        self.assertEqual(self.put(f"/api/phones/{self.pid}/forwarding", {"target": "chat", "chatId": "c2",
                                                                          "chatName": "Other", "chatKind": "group"}, ASHA).status_code, 403)

    def test_quiet_hours_hold_or_drop(self):
        self.on(quietStart="07:00", quietEnd="08:30")
        self.notify(note())
        self.assertEqual(self.posted, [])
        self.assertEqual(forwarding.release_held(self.now), 0)
        self.assertEqual(forwarding.release_held(self.now + 31 * 60), 1)
        self.assertEqual(len(self.posted), 1)
        self.on(quietMode="drop")
        self.advance(3)
        self.notify(note(text="Code 778899"))
        self.assertEqual(forwarding.release_held(self.now + 31 * 60), 0)
        self.assertEqual(len(self.posted), 1)

    def test_text_is_never_stored_or_logged(self):
        secret = "Your code is 918273 SECRETWORD"
        self.on(filter="all", allConfirmed=True)
        records = []

        class Grab(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))
        h = Grab(level=logging.DEBUG)
        root = logging.getLogger()
        old = root.level
        root.addHandler(h)
        root.setLevel(logging.DEBUG)
        try:
            self.notify(note(text=secret, title="SENDERNAME"))
            self.assertEqual(len(self.posted), 1)
            self.get(f"/api/phones/{self.pid}", ASHA)
            self.get("/api/admin/phones")
        finally:
            root.removeHandler(h)
            root.setLevel(old)
        logs = "\n".join(records)
        self.assertNotIn("918273", logs)
        self.assertNotIn("SECRETWORD", logs)
        self.assertNotIn("SENDERNAME", logs)
        with db.get_conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dump = "\n".join(conn.iterdump())
            self.assertEqual(conn.execute("SELECT kind, chat_name FROM forwards").fetchall()[0][:], ("message", "📌 My room"))
        self.assertNotIn("918273", dump)
        for suffix in ("", "-wal", "-shm"):
            path = config.DB_PATH + suffix
            if os.path.exists(path):
                with open(path, "rb") as f:
                    raw = f.read()
                self.assertNotIn(b"918273", raw, suffix)
                self.assertNotIn(b"SECRETWORD", raw, suffix)
        # nor in memory's view of the sensor
        from app import phones
        self.assertNotIn("918273", json.dumps(phones.LIVE.states))

    def test_owner_page_and_log(self):
        self.on()
        self.notify(note())
        page = self.get(f"/api/phones/{self.pid}", ASHA).json()
        self.assertEqual(page["forwarding"]["last"]["chat"], "📌 My room")
        self.assertIn("That it forwards messages", " ".join(page["adminsSee"]))
        self.assertNotIn("482913", json.dumps(page))


class ChatContract(PhonesCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add()
        self.put(f"/api/phones/{self.pid}/forwarding", {"on": True, "target": "chat", "chatId": "c-codes",
                                                        "chatName": "Phone codes", "chatKind": "group"}, ASHA)

    def ask(self, data, frm="household_chat"):
        msg = bus.Message({"id": "01", "from": frm, "to": "household_phones", "kind": "phones.forwarding_into",
                           "data": data})
        with db.get_conn() as conn:
            return app_messages.forwarding_into(msg, conn)

    def test_forwarding_into_permissions(self):
        self.assertEqual(self.ask({"requested_by": "u-asha", "chat_id": "c-codes"})["phones"][0]["id"], self.pid)
        r = self.ask({"requested_by": "u-ann", "chat_id": "c-codes"})
        self.assertEqual(r, {"phones": [{"id": self.pid, "name": "Asha's Pixel 8", "owner_name": "Asha"}],
                             "page": "#/check/"})
        self.assertEqual(self.ask({"requested_by": "u-bob", "chat_id": "c-codes"})["phones"], [])
        self.set_row(self.pid, fwd_members=json.dumps(["u-bob"]))
        self.assertEqual(len(self.ask({"requested_by": "u-bob", "chat_id": "c-codes"})["phones"]), 1)
        self.assertEqual(self.ask({"requested_by": "u-bob", "chat_id": "c-other"})["phones"], [])
        self.setting(check_by_chat_members=False)
        self.assertEqual(self.ask({"requested_by": "u-bob", "chat_id": "c-codes"})["phones"], [])
        self.setting(forwarding_allowed=False)
        self.assertEqual(self.ask({"requested_by": "u-asha", "chat_id": "c-codes"})["phones"], [])

    def test_my_room(self):
        self.put(f"/api/phones/{self.pid}/forwarding", {"target": "my_room"}, ASHA)
        self.assertEqual(len(self.ask({"requested_by": "u-asha", "chat_id": "c-room", "my_room": True})["phones"]), 1)
        self.assertEqual(self.ask({"requested_by": "u-bob", "chat_id": "c-room", "my_room": True})["phones"], [])

    def test_refusals(self):
        with self.assertRaises(bus.Nack) as e:
            self.ask({"requested_by": "u-asha", "chat_id": "c-codes"}, frm="household_docs")
        self.assertEqual(e.exception.reason, "not_allowed")
        with self.assertRaises(bus.Nack) as e:
            self.ask({"requested_by": "u-asha", "chat_id": "bad id!"})
        self.assertEqual(e.exception.reason, "invalid")

    def test_members_answer_and_chats_list(self):
        msg = bus.Message({"id": "02", "from": "household_chat", "to": "household_phones", "kind": "ack",
                           "ref": f"members:{self.pid}", "data": {"result": {"chats": [
                               {"id": "c-codes", "name": "Phone codes", "kind": "group", "members": 3,
                                "member_ids": ["u-bob", "u-ann"]}]}}}, answers="chat.chats.list")
        with db.get_conn() as conn:
            app_messages.on_reply(msg, conn)
        self.assertEqual(json.loads(self.row(self.pid)["fwd_members"]), ["u-bob", "u-ann"])
        page = self.get(f"/api/phones/{self.pid}", ASHA).json()
        self.assertEqual(page["forwarding"]["members"], 3)                  # "3 people will see…"
        # the owner's chats for the picker
        with db.get_conn() as conn:
            conn.execute("INSERT INTO bus_requests (id, user_id, phone_id, kind, created) VALUES ('r1', 'u-asha', ?, "
                         "'chats', ?)", (self.pid, self.now))
        msg = bus.Message({"id": "03", "from": "household_chat", "to": "household_phones", "kind": "ack", "ref": "chats:r1",
                           "data": {"result": {"chats": [{"id": "c-room", "kind": "personal", "name": "My room", "members": 1},
                                                         {"id": "c-codes", "kind": "group", "name": "Phone codes",
                                                          "members": 3}], "more": False}}}, answers="chat.chats.list")
        with db.get_conn() as conn:
            app_messages.on_reply(msg, conn)
        r = self.get("/api/requests/r1", ASHA).json()
        self.assertEqual([c["id"] for c in r["result"]["chats"]], ["c-room", "c-codes"])
        self.assertEqual(self.get("/api/requests/r1", BOB).status_code, 404)

    def test_forward_goes_without_the_outbox_and_chat_refusal_is_shown(self):
        app_messages.POST["fn"] = None
        posted = []
        b = bus.default
        with mock.patch.object(app_messages, "chat_ready", lambda kind="chat.message.post": True), \
                mock.patch.object(b, "slug", "household_phones"), \
                mock.patch.object(b, "_post", lambda env: posted.append(env) or True):
            self.put(f"/api/phones/{self.pid}/forwarding", {"target": "my_room"}, ASHA)
            self.report(self.pid, key="last_notification", value="x", attrs=note())
            self.assertEqual(len(posted), 1)
            env = posted[0]
            self.assertEqual((env["kind"], env["to"], env["data"]["my_room"]), ("chat.message.post", "household_chat", True))
            with db.get_conn() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM bus_outbox").fetchone()[0], 0)
            # not answered: posted again (the same id) after 30 s
            self.assertEqual(app_messages.resend_forwards(self.now + 31), 1)
            self.assertEqual(posted[1]["id"], env["id"])
            app_messages.on_answer_event({"data": {"from": "household_chat", "to": "household_phones", "kind": "nack",
                                                   "reply_to": env["id"], "data": {"reason": "not_allowed", "detail": "app"}}})
            self.assertEqual(app_messages.pending_count(), 0)
        err = self.row(self.pid)["fwd_error"]
        self.assertIn("Phones may post forwarded messages", err)
        items = {c["id"]: c for c in self.get(f"/api/phones/{self.pid}", ASHA).json()["checklist"]}
        self.assertFalse(items["chat"]["ok"])
