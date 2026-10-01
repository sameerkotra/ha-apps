"""GET /api/tree/all and tree.generations(): everyone at once, one row per
generation, groups of relatives ordered with "me"'s first (§11 Everyone view)."""
from base import BOB, ApiTestCase
import _env  # noqa: F401

import sqlite3
import unittest

from app import config
from app.routers import tree as tree_router


class WholeTreeCase(ApiTestCase):
    def all(self, user=None):
        return self.ok(self.get("/api/tree/all", user=user))

    def by_id(self, data):
        return {p["id"]: p for p in data["people"]}


class Generations(WholeTreeCase):
    def setUp(self):
        super().setUp()
        #   gpa + gma
        #       |
        #   dad + mum        in-law parents: ilp1 + ilp2
        #    |                        |
        #   me + spouse  <-------------
        #    |
        #   kid
        self.gpa = self.person("Gpa", gender="male")
        self.gma = self.person("Gma", gender="female")
        self.dad = self.person("Dad", gender="male")
        self.mum = self.person("Mum", gender="female")
        self.me = self.person("Me", gender="female")
        self.spouse = self.person("Spouse", gender="male")
        self.ilp1 = self.person("Ilp1", gender="male")
        self.ilp2 = self.person("Ilp2", gender="female")
        self.kid = self.person("Kid")
        self.f_g = self.family(self.gpa, self.gma, children=[self.dad])
        self.f_p = self.family(self.dad, self.mum, children=[self.me])
        self.f_il = self.family(self.ilp1, self.ilp2, children=[self.spouse])
        self.f_me = self.family(self.me, self.spouse, children=[self.kid])

    def test_rows(self):
        data = self.all()
        P = self.by_id(data)
        self.assertEqual(len(P), 9)
        self.assertEqual(data["total"], 9)
        self.assertFalse(data["truncated"])
        gen = {k: v["gen"] for k, v in P.items()}
        self.assertEqual(gen[self.gpa], 0)
        self.assertEqual(gen[self.gpa], gen[self.gma])
        self.assertEqual(gen[self.dad], gen[self.mum])
        self.assertEqual(gen[self.me], gen[self.spouse])
        self.assertEqual(gen[self.ilp1], gen[self.ilp2])
        # parents one row above their children, for every family
        for f in data["families"]:
            for c in f["children"]:
                for par in (f["p1"], f["p2"]):
                    if par:
                        with self.subTest(parent=P[par]["name"], child=P[c["id"]]["name"]):
                            self.assertEqual(gen[c["id"]], gen[par] + 1)
        self.assertEqual(gen[self.kid], 3)
        self.assertEqual({p["comp"] for p in data["people"]}, {0})

    def test_families_payload(self):
        data = self.all()
        fams = {f["id"]: f for f in data["families"]}
        self.assertEqual(set(fams), {self.f_g, self.f_p, self.f_il, self.f_me})
        f = fams[self.f_me]
        self.assertEqual({f["p1"], f["p2"]}, {self.me, self.spouse})
        self.assertEqual(f["children"], [{"id": self.kid, "relation": "birth"}])
        self.assertEqual((f["kind"], f["ended"]), ("married", None))

    def test_card_fields_and_relationships(self):
        data = self.all()
        self.assertIsNone(data["meId"])
        p = self.by_id(data)[self.me]
        for k in ("id", "name", "given", "surname", "gender", "living", "years", "photo", "gen", "comp"):
            self.assertIn(k, p)
        self.assertNotIn("relationship", p)
        self.set_me(self.me)
        data = self.all()
        self.assertEqual(data["meId"], self.me)
        P = self.by_id(data)
        self.assertEqual(P[self.dad]["relationship"], "father")
        self.assertEqual(P[self.kid]["relationship"], "child")
        # other users don't see my relationships
        self.assertIsNone(self.all(user=BOB)["meId"])

    def test_generations_function_directly(self):
        from app import db, graph
        with db.get_conn() as conn:
            g = graph.get(conn)
        gen, comp = tree_router.generations(g)
        self.assertEqual(set(gen), set(g.people))
        self.assertEqual(min(gen.values()), 0)
        self.assertEqual(len(set(comp.values())), 1)

    def test_deleted_people_and_families_excluded(self):
        self.ok(self.delete(f"/api/people/{self.kid}"))
        self.ok(self.delete(f"/api/families/{self.f_il}"))
        data = self.all()
        P = self.by_id(data)
        self.assertNotIn(self.kid, P)
        self.assertEqual(data["total"], 8)
        fams = {f["id"]: f for f in data["families"]}
        self.assertNotIn(self.f_il, fams)
        self.assertEqual(fams[self.f_me]["children"], [])
        # with the in-laws' family gone, each of them is on their own
        self.assertEqual(len({p["comp"] for p in data["people"]}), 3)
        self.assertEqual(P[self.ilp1]["gen"], 0)
        for f in data["families"]:
            for x in (f["p1"], f["p2"]):
                if x:
                    self.assertIn(x, P)
            for c in f["children"]:
                self.assertIn(c["id"], P)


class Groups(WholeTreeCase):
    def test_empty(self):
        self.assertEqual(self.all(), {"empty": True, "meId": None, "people": [], "families": []})

    def test_unconnected_groups_me_first(self):
        # a big group, a small group containing me, and a loner
        big = [self.person(f"Big{i}") for i in range(4)]
        self.family(big[0], big[1], children=big[2:])
        a, b = self.person("SmallA"), self.person("SmallB")
        self.family(a, None, children=[b])
        loner = self.person("Loner")
        data = self.all()
        comps = {p["id"]: p["comp"] for p in data["people"]}
        self.assertEqual({comps[x] for x in big}, {0})               # biggest first when "me" isn't set
        self.assertEqual(comps[a], comps[b])
        self.assertEqual(sorted({comps[a], comps[loner]}), [1, 2])
        self.assertEqual(comps[a], 1)                                # then by size
        # each group's rows start at 0
        self.assertEqual(data["people"][0]["gen"], 0)
        self.assertEqual({p["gen"] for p in data["people"] if p["id"] in (a, b)}, {0, 1})
        self.assertEqual(next(p["gen"] for p in data["people"] if p["id"] == loner), 0)
        # people are listed group by group
        self.assertEqual([p["comp"] for p in data["people"]], sorted(p["comp"] for p in data["people"]))
        self.set_me(b)
        comps = {p["id"]: p["comp"] for p in self.all()["people"]}
        self.assertEqual((comps[a], comps[b]), (0, 0))
        self.assertEqual({comps[x] for x in big}, {1})
        self.assertEqual(comps[loner], 2)

    def test_partner_without_parents_on_the_row_of_their_partner(self):
        # the in-law is reached through the marriage, never floats to row 0
        gpa, dad, wife = self.person("Gpa"), self.person("Dad"), self.person("Wife")
        self.family(gpa, None, children=[dad])
        self.family(dad, wife)
        P = self.by_id(self.all())
        self.assertEqual((P[gpa]["gen"], P[dad]["gen"], P[wife]["gen"]), (0, 1, 1))

    def test_second_marriage_and_half_siblings(self):
        dad, m1, m2 = self.person("Dad"), self.person("M1"), self.person("M2")
        c1, c2 = self.person("C1"), self.person("C2")
        self.family(dad, m1, children=[c1])
        self.family(dad, m2, children=[c2])
        P = self.by_id(self.all())
        self.assertEqual({P[x]["gen"] for x in (dad, m1, m2)}, {0})
        self.assertEqual({P[x]["gen"] for x in (c1, c2)}, {1})

    def test_truncation(self):
        tree_router.MAX_ALL, old = 3, tree_router.MAX_ALL
        self.addCleanup(setattr, tree_router, "MAX_ALL", old)
        ids = [self.person(f"P{i}") for i in range(5)]
        self.family(ids[0], ids[1], children=[ids[2], ids[3]])
        data = self.all()
        self.assertTrue(data["truncated"])
        self.assertEqual((len(data["people"]), data["total"]), (3, 5))
        kept = {p["id"] for p in data["people"]}
        # families only mention people who are in the payload
        for f in data["families"]:
            for x in (f["p1"], f["p2"]):
                self.assertTrue(x is None or x in kept)
            for c in f["children"]:
                self.assertIn(c["id"], kept)

    def test_relationship_labels_skipped_for_big_trees(self):
        me = self.person("Me")
        self.set_me(me)
        self.assertIn("relationship", self.all()["people"][0])
        rows = [(f"{i:032x}", f"P{i}", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z") for i in range(1, 801)]
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.executemany("INSERT INTO people (id, given_names, created_at, updated_at) VALUES (?, ?, ?, ?)", rows)
            conn.execute("UPDATE settings SET value = 'bulk' WHERE key = 'tree_version'")
            conn.commit()
        finally:
            conn.close()
        data = self.all()
        self.assertEqual(len(data["people"]), 801)
        self.assertFalse(any("relationship" in p for p in data["people"]))
        self.assertEqual(data["people"][0]["id"], me)                 # still first: my group

if __name__ == "__main__":
    unittest.main()
