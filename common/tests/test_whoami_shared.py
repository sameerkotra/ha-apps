"""The shared whoami contract (common/python/whoami.py), tested in every app that uses it."""
import os
import re
import unittest

from app.common import whoami

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeRequest:
    def __init__(self, headers):
        self.headers = {k.lower(): v for k, v in headers.items()}


def build(**kw):
    args = dict(user_id="u1", username="jane.doe", display_name="Jane", is_admin=False, admin_entries=2)
    args.update(kw)
    return whoami.build(FakeRequest({"X-Ingress-Path": "/x"}), **args)


class WhoamiContract(unittest.TestCase):
    def test_fields(self):
        w = build()
        self.assertEqual(w, {"haUserId": "u1", "haUsername": "jane.doe", "haDisplayName": "Jane", "nameSent": True,
                             "isAdmin": False, "displayNameOnly": False, "adminEntries": 2, "noAdmin": False,
                             "viaIngress": True, "extras": []})

    def test_no_admin_follows_the_count(self):
        self.assertTrue(build(admin_entries=0)["noAdmin"])
        self.assertFalse(build(admin_entries=1)["noAdmin"])

    def test_name_not_sent(self):
        w = build(username=None)
        self.assertFalse(w["nameSent"])
        self.assertIsNone(w["haUsername"])
        self.assertFalse(build(username="")["nameSent"])

    def test_via_ingress(self):
        self.assertFalse(whoami.build(FakeRequest({}), user_id="u", username=None, display_name=None,
                                      is_admin=False, admin_entries=1)["viaIngress"])

    def test_display_name_only_never_for_admins(self):
        self.assertTrue(build(display_name_only=True)["displayNameOnly"])
        self.assertFalse(build(display_name_only=True, is_admin=True)["displayNameOnly"])

    def test_notify_linked_only_when_the_app_has_notifications(self):
        self.assertNotIn("notifyLinked", build())
        self.assertIs(build(notify_linked=1)["notifyLinked"], True)
        self.assertIs(build(notify_linked=[])["notifyLinked"], False)

    def test_extras_rows(self):
        w = build(extras=[whoami.row("Chats you're in", 3), None, whoami.notify_row(False),
                          whoami.row("Account status", "Turned off", tone="danger"),
                          whoami.row("This is me", None, action={"label": "set it", "target": "settings"})])
        self.assertEqual(w["extras"][0], {"label": "Chats you're in", "value": 3})
        self.assertEqual(w["extras"][1]["value"], False)
        self.assertIn("Track device", w["extras"][1]["hint"])
        self.assertNotIn("hint", whoami.notify_row(True))
        self.assertEqual(w["extras"][2]["tone"], "danger")
        self.assertEqual(w["extras"][3]["action"], {"label": "set it", "target": "settings"})
        self.assertEqual(len(w["extras"]), 4)                       # None rows are dropped

    def test_rows_hold_plain_values_only(self):
        with self.assertRaises(ValueError):
            whoami.row("People", ["alice", "bob"])                  # never lists
        with self.assertRaises(ValueError):
            whoami.row("", 1)
        with self.assertRaises(ValueError):
            whoami.row("x", 1, tone="loud")
        with self.assertRaises(ValueError):
            whoami.row("x", 1, action={"label": "go"})
        with self.assertRaises(ValueError):
            build(extras=[{"text": "x"}])

    def test_app_fields_cannot_shadow_shared_ones(self):
        self.assertEqual(build(chatCount=4)["chatCount"], 4)
        with self.assertRaises(ValueError):
            build(isAdmin=True, noAdmin=False)

    def test_page_script_wording(self):
        # the shared page script (when this app has it) says the same things in every app
        path = os.path.join(APP_DIR, "app", "static", "common", "whoami.js")
        if not os.path.exists(path):
            self.skipTest("this app draws the page itself")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        for text in ("User name (sent by Home Assistant)", "User id (sent by Home Assistant)",
                     "Display name (not used for matching)", "Administrator in this app",
                     "Names in the app's admin_users", "No admin yet", " on the app's Configuration tab, save, and restart the app. ",
                     "How the app sees you", "textContent"):
            self.assertIn(text, js)
        self.assertNotRegex(js, r"\.innerHTML\s*=|insertAdjacentHTML|outerHTML\s*=")    # DOM nodes only


if __name__ == "__main__":
    unittest.main()
