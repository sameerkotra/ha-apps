"""tree.relation (app/tools.py) and finding a person from a loosely spelt name (app/find_people.py): both ways, in
the asker's language, with the chain; misspellings, nicknames, initials and "my mother"; two people of one name
give the choices; nobody near gives the nearest names. All people here are invented."""
from base import ALICE, BOB, ApiTestCase, all_features_on, set_app_settings, sql
import _env  # noqa: F401

from datetime import datetime, timedelta, timezone

from app import config, db, find_people, graph as graph_mod, tools
from app.common import app_bus


def call(uid, tool="tree.relation", **args):
    now = datetime.now(timezone.utc)
    env = {"id": app_bus.new_ulid(now), "v": 1, "from": "household_assistant", "to": "family_tree",
           "kind": "assist.tool.call", "kv": 1, "reply_to": None, "ref": None, "sent": now.isoformat(),
           "expires": (now + timedelta(seconds=20)).isoformat(),
           "data": {"tool": tool, "args": args, "requested_by": uid, "question": "q1"}}
    with db.get_conn() as conn:
        try:
            return tools.tools.on_call(app_bus.Message(env), conn)
        except app_bus.Nack as n:
            return n


class WordTests(ApiTestCase):
    def test_spellings_fold_together(self):
        s = find_people.sound
        self.assertEqual(s("lakshmi"), s("laxmi"))
        self.assertEqual(s("sreenivas"), s("srinivas"))
        self.assertEqual(s("vijay"), s("vijai"))
        self.assertEqual(s("lalitha"), s("lalita"))
        self.assertEqual(s("bhaskar"), s("baskar"))
        self.assertEqual(find_people.words("José  O'Neil-Rao"), ["jose", "o", "neil", "rao"])
        self.assertEqual(find_people.word_score("p", "pallem"), 0.9)
        self.assertEqual(find_people.word_score("venkat", "venkateswara"), 0.85)
        self.assertEqual(find_people.word_score("kiren", "kiran"), 0.88)
        self.assertLess(find_people.word_score("ravi", "sita"), 0.6)


class RelationTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.old_panel = config.INGRESS_PANEL
        config.INGRESS_PANEL = "/local_family_tree"
        # Ramesh + Lakshmi → Sita, Ravi Kumar; Ravi Kumar + Anu → Kiran; Sita + Venkateswara (nickname Venky)
        self.ramesh = self.person("Ramesh", "Pallem", gender="male", born=1940)
        self.lakshmi = self.person("Lakshmi", "Pallem", gender="female", born=1945)
        self.sita = self.person("Sita", "Pallem", gender="female", born={"d": 1, "m": 1, "y": 1968})
        self.ravi = self.person("Ravi Kumar", "Pallem", gender="male", born={"d": 1, "m": 1, "y": 1970})
        self.anu = self.person("Anu", "Rao", gender="female")
        self.kiran = self.person("Kiran", "Pallem", gender="male", born=1998)
        self.venky = self.person("Venkateswara", "Varma", gender="male", nickname="Venky")
        self.family(self.ramesh, self.lakshmi, children=[self.sita, self.ravi])
        self.family(self.ravi, self.anu, children=[self.kiran])
        self.family(self.venky, self.sita)
        self.stranger = self.person("Zed", "Far")
        self.get("/api/me", user=BOB)

    def tearDown(self):
        config.INGRESS_PANEL = self.old_panel
        super().tearDown()

    def assertNack(self, res, reason, detail):
        self.assertIsInstance(res, app_bus.Nack, res)
        self.assertEqual((res.reason, res.detail), (reason, detail))

    def find(self, text, me=None):
        with db.get_conn() as conn:
            g = graph_mod.get(conn)
            return find_people.find(conn, g, text, me, "en")

    def test_both_ways_with_the_chain_and_link(self):
        res = call(ALICE["id"], person="Kiran Pallem", to="Sita Pallem")
        self.assertIn("Kiran Pallem is Sita Pallem's nephew", res["text"])
        self.assertIn("Sita Pallem is Kiran Pallem's aunt", res["text"])
        self.assertRegex(res["text"], r"The link: Sita Pallem → (father|mother): .* → son: Ravi Kumar Pallem → son: Kiran Pallem\.")
        self.assertEqual(res["items"][0]["relationship"], "nephew")
        self.assertEqual(res["links"], [{"label": "How are they related? in Family Tree", "panel": "/local_family_tree",
                                         "target": f"/relate/{self.sita}/{self.kiran}"}])

    def test_names_that_are_not_exact(self):
        self.assertEqual(self.find("Laxmi")["id"], self.lakshmi)
        self.assertEqual(self.find("lakshmi pallem")["id"], self.lakshmi)
        self.assertEqual(self.find("Seeta")["id"], self.sita)
        self.assertEqual(self.find("Ravi")["id"], self.ravi)          # one of his given names
        self.assertEqual(self.find("Ravi P")["id"], self.ravi)        # an initial
        self.assertEqual(self.find("Venky")["id"], self.venky)        # a nickname
        self.assertEqual(self.find("venkat")["id"], self.venky)       # the start of a name
        self.assertEqual(self.find("Kiren Pallemm")["id"], self.kiran) # typos
        res = call(ALICE["id"], person="Kiren", to="seeta")
        self.assertIn("“Kiren” taken as Kiran Pallem", res["text"])
        self.assertIn("Kiran Pallem is Sita Pallem's nephew", res["text"])

    def test_the_asker_and_relation_words(self):
        self.set_me(self.sita, user=ALICE)
        res = call(ALICE["id"], person="Kiran")
        self.assertIn("Kiran Pallem is your nephew", res["text"])
        self.assertIn("You are Kiran Pallem's aunt", res["text"])
        self.assertEqual(self.find("my husband", me=self.sita)["id"], self.venky)
        self.assertEqual(self.find("Ravi's son", me=self.sita)["id"], self.kiran)
        res = call(ALICE["id"], person="my husband", to="Ravi")
        self.assertIn("Venkateswara Varma is Ravi Kumar Pallem's brother-in-law", res["text"])
        res = call(BOB["id"], person="Kiran")                     # Bob hasn't said who he is
        self.assertIn("I don't know who you are", res["text"])

    def test_two_people_of_one_name(self):
        other = self.person("Ravi", "Varma", gender="male", born=1990)
        self.family(self.venky, None, children=[other])
        res = call(ALICE["id"], person="Ravi", to="Sita")
        self.assertIn("More than one person could be “Ravi”", res["text"])
        self.assertIn("Ravi Kumar Pallem (born 1970, son of Ramesh and Lakshmi)", res["text"])
        self.assertIn("Ravi Varma (born 1990, son of Venkateswara)", res["text"])
        self.assertEqual(len(res["items"]), 2)
        self.assertIn("Ravi Kumar Pallem is Sita Pallem's brother", call(ALICE["id"], person="Ravi Pallem", to="Sita")["text"])

    def test_nobody_and_not_related(self):
        res = call(ALICE["id"], person="Bartholomew", to="Sita")
        self.assertTrue(res["text"].startswith("Nobody in the family tree is called “Bartholomew”."))
        res = call(ALICE["id"], person="Zed", to="Sita")
        self.assertIn("Zed Far and Sita Pallem aren't related in the family tree", res["text"])
        res = call(ALICE["id"], person="Sita", to="Seeta")
        self.assertIn("same person twice", res["text"])
        self.assertNack(call(ALICE["id"]), "invalid", "person")

    def test_in_telugu_with_the_meaning(self):
        all_features_on()
        set_app_settings(relationship_language="te")
        self.addCleanup(set_app_settings, relationship_language="en")
        res = call(ALICE["id"], person="Kiran", to="Sita")
        self.assertIn("Kiran Pallem is Sita Pallem's ", res["text"])
        self.assertIn("Sita Pallem's Menalludu (younger brother's son). Sita Pallem is Kiran Pallem's Atta (father's elder sister).", res["text"])
        self.assertNotIn("nephew", res["items"][0]["relationship"])
        self.assertEqual(res["items"][0]["english"], "nephew")

    def test_a_persons_facts(self):
        from datetime import date
        from unittest import mock
        fid = sql("SELECT id FROM families WHERE partner1_id = ? OR partner2_id = ?", (self.venky, self.venky))[0]["id"]
        self.ok(self.patch(f"/api/families/{fid}", {"marriage": {"date": {"d": 12, "m": 5, "y": 1994}, "place": "Guntur"}}))
        with mock.patch.object(config, "today", return_value=date(2026, 10, 7)):
            res = call(ALICE["id"], tool="tree.person", person="seeta")
        self.assertEqual(res["text"], "“seeta” taken as Sita Pallem. Sita Pallem: born 1 January 1968, 58 years old; "
                                      "child of Ramesh Pallem and Lakshmi Pallem; partner Venkateswara Varma, married 12 May "
                                      "1994 in Guntur (32 years); brother Ravi Kumar Pallem.")
        self.assertEqual(res["links"][0]["target"], f"/person/{self.sita}")
        self.assertEqual(res["items"][0]["age"], 58)
        self.set_me(self.sita, user=ALICE)
        res = call(ALICE["id"], tool="tree.person", person="Ravi")
        self.assertIn("1 child: Kiran Pallem (b. 1998)", res["text"])
        self.assertIn("your brother", res["text"])
        self.assertIn("that's you", call(ALICE["id"], tool="tree.person", person="me")["text"])
        self.ok(self.patch(f"/api/people/{self.ramesh}", {"deceased": True}))
        res = call(ALICE["id"], tool="tree.person", person="Ramesh")
        self.assertIn("died", res["text"])
        self.assertNotIn("years old", res["text"])
        self.assertTrue(call(ALICE["id"], tool="tree.person", person="Bartholomew")["text"].startswith("Nobody"))

    def test_in_the_catalogue(self):
        names = [t["name"] for t in tools.tools.spec()]
        self.assertEqual(names, ["tree.birthdays", "tree.relation", "tree.person"])

    def test_deep_link(self):
        r = self.client.get(f"/relate/{self.sita}/{self.kiran}", follow_redirects=False)
        self.assertEqual(r.status_code, 307)
        self.assertEqual(r.headers["location"], f"../../../#/relate/{self.sita}/{self.kiran}")
