"""Father's side / mother's side (§13.16)."""
from base import ADMIN, ApiTestCase
import _env  # noqa: F401

import unittest

from app import graph as graph_mod, sides
from test_kin import KinTree


class Sides(KinTree):
    def side(self, who, anchor="Me"):
        return sides.compute(graph_mod.get(), self.p[anchor]).get(self.p[who])

    def test_sides(self):
        expected = {
            "FF": "paternal", "FM": "paternal", "Dad": "paternal", "PeddaNanna": "paternal", "Babai": "paternal",
            "Atta": "paternal", "PNSon": "paternal", "AttaDaughter": "paternal", "BabaiSon": "paternal",
            "Peddamma": "paternal", "Mamayya": "paternal", "Pinni": "paternal",
            "MF": "maternal", "MM": "maternal", "Mum": "maternal", "MumBro": "maternal", "MumBigSis": "maternal",
            "MBSSon": "maternal", "MumBroDaughter": "maternal", "MumBroW": "maternal", "MumSmallSisH": "maternal",
            "Me": None, "Akka": None, "Tammudu": None, "AkkaSon": None, "AkkaH": None, "TammuduW": None,
            "Wife": None, "Son": None, "GrandSon": None, "Kodalu": None, "WifeF": None, "WifeBro": None,
        }
        self.assertEqual({k: self.side(k) for k in expected}, expected)

    def test_other_anchor(self):
        # from Tammudu's son: everyone in this tree is on his father's side
        self.assertEqual(self.side("Dad", anchor="TammuduSon"), "paternal")
        self.assertEqual(self.side("Mum", anchor="TammuduSon"), "paternal")
        self.assertEqual(self.side("Me", anchor="TammuduSon"), "paternal")
        self.assertEqual(self.side("Tammudu", anchor="TammuduSon"), "paternal")
        self.assertEqual(self.side("TammuduW", anchor="TammuduSon"), "maternal")

    def test_api(self):
        t = self.ok(self.get(f"/api/tree?focus={self.p['Me']}&sides=true"))
        self.assertEqual(t["sideAnchor"], self.p["Me"])
        self.assertEqual(t["ancestors"]["parents"]["a"]["p"]["side"], "paternal")
        self.assertEqual(t["ancestors"]["parents"]["b"]["p"]["side"], "maternal")
        self.assertIsNone(t["descendants"]["p"]["side"])
        self.assertNotIn("side", self.ok(self.get(f"/api/tree?focus={self.p['Me']}"))["descendants"]["p"])
        whole = {p["id"]: p["side"] for p in self.ok(self.get("/api/tree/all?sides=true"))["people"]}
        self.assertEqual(whole[self.p["MumBro"]], "maternal")
        # admin has no "me": the focus person is the anchor on the chart, and there's no side on the whole tree
        t = self.ok(self.get(f"/api/tree?focus={self.p['Tammudu']}&sides=true", user=ADMIN))
        self.assertEqual(t["sideAnchor"], self.p["Tammudu"])
        self.assertIsNone(self.ok(self.get("/api/tree/all?sides=true", user=ADMIN))["sideAnchor"])
        pat = {p["id"] for p in self.ok(self.get("/api/people?side=paternal&page_size=500"))["items"]}
        mat = {p["id"] for p in self.ok(self.get("/api/people?side=maternal&page_size=500"))["items"]}
        self.assertIn(self.p["Atta"], pat)
        self.assertNotIn(self.p["Atta"], mat)
        self.assertIn(self.p["MumBro"], mat)
        self.assertNotIn(self.p["Akka"], pat | mat)
        self.assertEqual(self.get("/api/people?side=paternal", user=ADMIN).status_code, 422)
        self.assertEqual(self.get("/api/people?side=left").status_code, 422)


class CousinMarriage(ApiTestCase):
    def test_both_sides_and_half_siblings(self):
        ff, fm = self.person("FF", gender="male"), self.person("FM", gender="female")
        mf, mm = self.person("MF", gender="male"), self.person("MM", gender="female")
        dad, atta = self.person("Dad", gender="male"), self.person("Atta", gender="female")
        mum, mama = self.person("Mum", gender="female"), self.person("Mama", gender="male")
        self.family(ff, fm, [dad, atta])
        self.family(mf, mm, [mum, mama])
        cousin = self.person("Cousin", gender="female")
        self.family(mama, atta, [cousin])                  # father's sister married mother's brother
        me = self.person("Me", gender="male")
        self.family(dad, mum, [me])
        other_wife = self.person("Other", gender="female")
        half = self.person("Half", gender="male")
        self.family(dad, other_wife, [half])
        wife_of_cousin_husband = self.person("CousinsHusband", gender="male")
        self.family(wife_of_cousin_husband, cousin)
        g = graph_mod.get()
        s = sides.compute(g, me)
        self.assertEqual(s[cousin], "both")
        self.assertEqual(s[wife_of_cousin_husband], "both")
        self.assertEqual(s[half], "paternal")
        self.assertEqual(s[other_wife], "paternal")
        self.assertEqual((s[atta], s[mama]), ("paternal", "maternal"))


if __name__ == "__main__":
    unittest.main()
