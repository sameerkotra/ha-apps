"""What the app answers the Household Assistant (spec/SPEC.md §10): the catalogue, status, Check now, and who may ask
about which phone."""
import json

from base import BOB, PhonesCase

from app import db, tools
from app.common import app_bus as bus


class Assistant(PhonesCase):
    def setUp(self):
        super().setUp()
        self.asha = self.add()
        self.bob = self.add("Bob Phone", user_id="u-bob")
        self.report(self.asha, value="62")
        self.report(self.bob, name="Bob Phone", value="30")
        self.tick()

    def call(self, tool, args, who="u-asha", frm="household_assistant"):
        msg = bus.Message({"id": "01", "from": frm, "to": "household_phones", "kind": "assist.tool.call",
                           "data": {"tool": tool, "args": args, "requested_by": who}})
        with db.get_conn() as conn:
            return tools.tools.on_call(msg, conn)

    def test_catalogue_fits_and_only_the_assistant_asks(self):
        tools.tools.check()
        names = [t["name"] for t in tools.tools.spec()]
        self.assertEqual(names, ["phones.status", "phones.check", "phones.find"])
        with self.assertRaises(bus.Nack):
            self.call("phones.status", {}, frm="household_chat")

    def test_my_phone_and_others(self):
        r = self.call("phones.status", {"person": "me"})
        self.assertIn("Asha's Pixel 8: Online", r["text"])
        self.assertIn("battery 62%", r["text"])
        self.assertEqual(self.call("phones.status", {"person": "Bob"})["text"],
                         "No phone you can see matches that in Household Phones.")
        r = self.call("phones.status", {"person": "Bob"}, who="u-ann")              # admins see every phone
        self.assertIn("Bob's Pixel 8", r["text"])
        self.assertIn("battery 30%", r["text"])

    def test_which_are_offline(self):
        self.advance(40)
        self.report(self.asha)
        self.tick()
        r = self.call("phones.status", {"which": "problems"}, who="u-ann")
        self.assertTrue(r["text"].startswith("Bob's Pixel 8: Offline"))
        self.assertEqual(len(r["items"]), 1)

    def test_paused_answers_paused_by(self):
        self.post(f"/api/phones/{self.bob}/pause", {"kind": "watching", "hours": 2}, BOB)
        r = self.call("phones.status", {"person": "bob"}, who="u-ann")
        self.assertIn("paused by Bob", r["text"])
        self.assertNotIn("30%", json.dumps(r))

    def test_check_runs_check_now_after_the_answer(self):
        r = self.call("phones.check", {"person": "me"})
        self.assertTrue(r["text"].startswith("Asked Asha's Pixel 8 to report now."))
        self.assertEqual(r["links"][0]["target"], f"/check/{self.asha}")
        self.assertEqual(self.call("phones.check", {"person": "bob"})["text"],
                         "No phone you can check matches that in Household Phones.")
