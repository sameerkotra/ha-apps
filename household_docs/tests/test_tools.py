"""What Docs answers the Household Assistant (app/tools.py; HOUSEHOLD_ASSISTANT_SPEC §4.2, §6.4, §7.2).

- docs.search: the Search page's search, with its access check — only what the person can open;
- docs.read: a note's text in 4000-character parts, a sheet's cells; nothing the person can't open (404-shaped);
- docs.checklist: ticked and open items;
- docs.note.create: only with the tap, into My docs → Inbox;
- Kids' space: no sheets, no new notes; the switches; the catalogue; Docs now answers the two kinds.

All people and documents here are invented."""
from base import ASHA, KABIR, MEERA, ApiBase, write

from datetime import datetime, timedelta, timezone

from app import app_messages, db, tools
from app.common import app_bus


def call(tool, h=KABIR, confirm=None, **args):
    now = datetime.now(timezone.utc)
    data = {"tool": tool, "args": args, "requested_by": h["X-Remote-User-Id"], "question": "q1"}
    if confirm is not None:
        data["confirm"] = confirm
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "household_docs",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(), "data": data}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            conn.rollback()
            return n


class ToolTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.boiler = self.note("Boiler service", text="The boiler was serviced in March.\nNext service due in March.")
        self.secret = self.note("Meera's boiler diary", h=MEERA, text="boiler private notes")
        self.trip = self.create("checklist", "Trip")
        r = self.ops(self.trip["id"], [{"op": "add", "text": "Passports"}, {"op": "add", "text": "Tickets"},
                                       {"op": "add", "text": "Charger"}])
        keys = {i["text"]: i["key"] for i in self.ok(r)["items"]}
        self.ok(self.ops(self.trip["id"], [{"op": "tick", "key": keys["Passports"]}]))
        self.old = app_messages._panel.copy()
        app_messages._panel.update(value="/a1b2c3d4_household_docs", known=True)

    def tearDown(self):
        app_messages._panel.update(self.old)
        super().tearDown()

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def test_search_only_what_i_can_open(self):
        res = call("docs.search", query="boiler")
        self.assertEqual([i["name"] for i in res["items"]], ["Boiler service.txt"])
        self.assertEqual(res["links"][0], {"label": "Boiler service.txt", "panel": "/a1b2c3d4_household_docs",
                                           "target": f"/doc/{self.boiler['id']}"})
        self.assertNotIn("private notes", str(res))
        self.ok(self.share(self.secret["id"], KABIR, h=MEERA))
        self.assertEqual(len(call("docs.search", query="boiler")["items"]), 2)
        self.assertEqual(call("docs.search", query="boiler", kind="checklist")["items"], [])
        self.assertIn("Nothing in Household Docs matches", call("docs.search", query="zebra")["text"])

    def test_read(self):
        res = call("docs.read", id=self.boiler["id"])
        self.assertIn("The boiler was serviced in March.", res["text"])
        self.assertFalse(res["more"])
        self.assertEqual(res["links"][0]["target"], f"/doc/{self.boiler['id']}")
        self.assertNack(call("docs.read", id=self.secret["id"]), "not_found", "document")
        self.assertNack(call("docs.read", id="nope"), "not_found", "document")
        self.save(self.boiler["id"], "x" * 5000)
        first = call("docs.read", id=self.boiler["id"])
        self.assertTrue(first["more"])
        self.assertEqual(first["items"][0]["next_offset"], 4000)
        rest = call("docs.read", id=self.boiler["id"], offset=4000)
        self.assertFalse(rest["more"])
        self.assertIn("x" * 1000, rest["text"])

    def test_read_a_sheet(self):
        sheet = self.ok(self.post("/api/docs", {"kind": "sheet", "name": "Budget", "format": "csv"}), 201)
        path = self.real(sheet["id"])
        write(path, b"Item,Cost\nRent,1200\nFood,35.5\n")
        self.scan()
        res = call("docs.read", id=sheet["id"])
        self.assertIn("A2: Rent", res["text"])
        self.assertIn("B2: 1200", res["text"])

    def test_checklist(self):
        res = call("docs.checklist", id=self.trip["id"])
        self.assertTrue(res["text"].endswith(": 2 of 3 open: Tickets; Charger."), res["text"])
        self.assertEqual([(i["item"], i["done"]) for i in res["items"]],
                         [("Passports", True), ("Tickets", False), ("Charger", False)])
        self.assertNack(call("docs.checklist", id=self.boiler["id"]), "invalid", "not_a_checklist")

    def test_new_note_needs_the_tap(self):
        self.assertNack(call("docs.note.create", name="Plumber", text="Call Ravi"), "not_allowed", "confirm")
        res = call("docs.note.create", confirm=True, name="Plumber", text="Call Ravi on Monday")
        nid = res["items"][0]["id"]
        self.assertIn("in My docs → Inbox", res["text"])
        doc = self.open(nid)
        self.assertEqual(doc["text"], "Call Ravi on Monday")
        self.assertEqual(res["links"][0]["target"], f"/doc/{nid}")

    def test_kids_space(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_child = 1, child_since = ? WHERE id = 'u_kabir'", ("2000-01-01",))
        self.assertIn("text", call("docs.search", query="boiler"))
        self.assertNack(call("docs.note.create", confirm=True, name="x", text="y"), "not_allowed", "child")

    def test_switches(self):
        self.settings({"assistant_answers": False})
        self.assertNack(call("docs.search", query="boiler"), "not_allowed", "off")
        self.settings({"assistant_answers": True})
        me = self.ok(self.get("/api/me"))
        self.assertTrue(me["app"]["assistant"] and me["prefs"]["assistantOk"])
        self.ok(self.put("/api/me/settings", {"assistantOk": False}))
        self.assertNack(call("docs.search", query="boiler"), "not_allowed", "person_off")
        self.assertIn("text", call("docs.search", h=MEERA, query="boiler"))
        self.assertNack(call("docs.search", h={"X-Remote-User-Id": "u_nobody"}, query="x"), "not_allowed", "no_access")

    def test_catalogue_and_kinds(self):
        tools.tools.check()
        self.assertEqual([t["name"] for t in tools.tools.spec()],
                         ["docs.search", "docs.read", "docs.checklist", "docs.note.create"])
        self.assertEqual(app_messages.CAN, ["assist.tool.call", "assist.tools.list"])
