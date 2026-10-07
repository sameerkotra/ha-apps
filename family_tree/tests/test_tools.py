"""What Family Tree answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §7.2).

tree.birthdays: what Upcoming lists for the asking person (close family once they've said "This is me", the
whole tree on request), with relationships; the admin's and the person's switches; the catalogue fits; the link
opens the app's sidebar page. "Today" is patched through config.today. All people here are invented."""
from base import ADMIN, ALICE, BOB, ApiTestCase, sql
import _env  # noqa: F401

from datetime import date, datetime, timedelta, timezone
from unittest import mock

from app import app_messages, config, db, settings, tools
from app.common import app_bus


def call(uid, **args):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "family_tree",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": "tree.birthdays", "args": args, "requested_by": uid, "question": "q1"}}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


class BirthdayTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(config, "today", return_value=date(2026, 9, 25))
        p.start()
        self.addCleanup(p.stop)
        self.ann = self.person("Ann", "Smith", gender="female", born={"d": 1, "m": 10, "y": 1960})
        self.raj = self.person("Raj", "Smith", gender="male", born={"d": 26, "m": 9, "y": 1958})
        self.kid = self.person("Kit", "Smith", gender="female", born={"d": 30, "m": 11, "y": 1990})
        self.far = self.person("Zed", "Far", born={"d": 2, "m": 10, "y": 1970})
        self.family(self.ann, self.raj, children=[self.kid])
        self.get("/api/me", user=BOB)
        self.old_panel = config.INGRESS_PANEL
        config.INGRESS_PANEL = "/local_family_tree"

    def tearDown(self):
        config.INGRESS_PANEL = self.old_panel
        super().tearDown()

    def test_coming_up_with_ages(self):
        res = call(ALICE["id"])
        self.assertIn("3 coming up in the next 30 days (the whole tree)", res["text"])
        self.assertIn("tomorrow: Raj Smith turns 68", res["text"])
        self.assertEqual([(i["date"], i["what"], i["years"]) for i in res["items"]],
                         [("2026-09-26", "birthday", 68), ("2026-10-01", "birthday", 66), ("2026-10-02", "birthday", 56)])
        self.assertEqual(res["links"], [{"label": "Upcoming in Family Tree", "panel": "/local_family_tree",
                                         "target": "/upcoming"}])
        self.assertEqual(len(call(ALICE["id"], days=90)["items"]), 4)
        self.assertNack(call(ALICE["id"], days=91), "invalid", "days")

    def test_close_family_and_relationships(self):
        self.set_me(self.kid, user=ALICE)
        res = call(ALICE["id"])
        self.assertIn("(close family)", res["text"])
        self.assertEqual([i["relationship"] for i in res["items"]], ["father", "mother"])
        res = call(ALICE["id"], everyone=True)
        self.assertEqual(len(res["items"]), 3)

    def test_nothing_coming(self):
        res = call(ALICE["id"], days=1)
        self.assertEqual(len(res["items"]), 1)
        with mock.patch.object(config, "today", return_value=date(2026, 12, 15)):
            res = call(ALICE["id"], days=5)
        self.assertEqual(res["text"], "No birthdays or anniversaries in the next 5 days (the whole tree).")

    def test_switches(self):
        self.ok(self.put("/api/admin/settings", {"assistant_answers": False}, user=ADMIN))
        self.assertNack(call(ALICE["id"]), "not_allowed", "off")
        self.ok(self.put("/api/admin/settings", {"assistant_answers": True}, user=ADMIN))
        me = self.ok(self.get("/api/me", user=BOB))
        self.assertTrue(me["assistant"] and me["assistantOk"])
        self.assertEqual(self.ok(self.put("/api/me/assistant", {"assistantOk": False}, user=BOB)), {"assistantOk": False})
        self.assertNack(call(BOB["id"]), "not_allowed", "person_off")
        self.assertIn("text", call(ALICE["id"]))
        self.assertEqual(self.put("/api/me/assistant", {"assistantOk": "no"}, user=BOB).status_code, 422)
        sql("UPDATE users SET disabled = 1 WHERE id = ?", (ALICE["id"],))
        self.assertNack(call(ALICE["id"]), "not_allowed", "no_access")

    def test_catalogue(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()], ["tree.birthdays"])
        self.assertIn("assist.tool.call", app_bus.default._handlers)

    def test_panel_from_the_supervisor(self):
        self.assertEqual(app_messages.learn_panel({"slug": "a1b2c3d4_family_tree", "ingress_panel": True}),
                         "/a1b2c3d4_family_tree")
        self.assertIsNone(app_messages.learn_panel({"slug": "a1b2c3d4_family_tree", "ingress_panel": False}))
        self.assertIsNone(app_messages.learn_panel({"slug": "evil/../x"}))

    def test_sub_paths_redirect_to_the_page(self):
        for path, where in (("/upcoming", "../#/upcoming"), ("/person/p_1", "../../#/person/p_1")):
            r = self.client.get(path, headers=self.client.headers, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (307, where), path)
        self.assertEqual(self.ok(self.get("/api/me"))["page"], "/local_family_tree")

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))
