"""Relationship search (§13.17)."""
from base import ADMIN
import _env  # noqa: F401

import unittest
from urllib.parse import quote

from test_kin import KinTree


class Related(KinTree):
    def find(self, user=None, **params):
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return self.ok(self.get(f"/api/people/related?{qs}", user=user))

    def names(self, **params):
        return sorted(p["name"] for p in self.find(**params)["items"])

    def parse(self, q, status=200):
        return self.ok(self.get(f"/api/people/related/parse?q={quote(q)}"), status)

    def test_categories(self):
        self.assertEqual(self.names(rel="first_cousins"),
                         sorted(["PNSon", "PNDaughter", "BabaiSon", "AttaSon", "AttaDaughter", "MBSSon", "MumBroSon",
                                 "MumBroDaughter"]))
        self.assertEqual(self.names(rel="uncles_aunts", gender="male"), ["Babai", "MumBro", "PeddaNanna"])
        self.assertEqual(self.names(rel="descendants"), ["Daughter", "GrandSon", "Son"])
        self.assertEqual(self.names(rel="partners"), ["Wife"])
        self.assertIn("WifeF", self.names(rel="in_laws"))
        self.assertIn("Mamayya", self.names(rel="in_laws"))
        self.assertNotIn("Wife", self.names(rel="in_laws"))
        self.assertEqual(self.names(rel="grandparents"), ["FF", "FM", "MF", "MM"])
        self.assertEqual(self.names(rel="siblings"), ["Akka", "Tammudu"])
        self.assertEqual(self.names(rel="nieces_nephews"), ["AkkaSon", "TammuduSon"])
        self.assertEqual(self.names(rel="descendants", anchor=self.p["FF"]),
                         sorted(["PeddaNanna", "Dad", "Babai", "Atta", "PNSon", "PNDaughter", "BabaiSon", "AttaSon",
                                 "AttaDaughter", "Akka", "Me", "Tammudu", "AkkaSon", "TammuduSon", "Son", "Daughter",
                                 "GrandSon"]))

    def test_filters(self):
        self.assertEqual(self.names(rel="first_cousins", side="paternal"),
                         sorted(["PNSon", "PNDaughter", "BabaiSon", "AttaSon", "AttaDaughter"]))
        self.assertEqual(self.names(rel="first_cousins", gender="female", side="maternal"), ["MumBroDaughter"])
        self.assertEqual(self.names(rel="blood", generation=-1),
                         sorted(["Dad", "Mum", "PeddaNanna", "Babai", "Atta", "MumBigSis", "MumSmallSis", "MumBro"]))
        self.assertEqual(self.names(rel="grandparents", living="deceased"), [])
        r = self.find(rel="first_cousins", anchor=self.p["Me"])
        labels = {p["name"]: (p["relationship"], p["generation"]) for p in r["items"]}
        self.assertEqual(labels["AttaSon"], ("first cousin", 0))
        self.assertEqual(r["anchor"]["name"], "Me")
        self.assertEqual(self.get("/api/people/related?rel=cousins", user=ADMIN).status_code, 422)
        self.assertEqual(self.get("/api/people/related?rel=wizards").status_code, 422)
        self.assertEqual(self.get("/api/people/related?anchor=nobody").status_code, 404)

    def test_kin_words(self):
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        self.addCleanup(lambda: self.put("/api/me/kin-lang", {"kinLang": None}))
        self.assertEqual(self.names(term="Babai"), ["Babai", "MumSmallSisH"])
        items = self.find(rel="first_cousins", side="maternal")["items"]
        self.assertEqual({p["name"]: p["relationship"] for p in items},
                         {"MBSSon": "Anna", "MumBroSon": "Bavamaridi", "MumBroDaughter": "Vadina"})

    def test_parse(self):
        me = self.p["Me"]
        cases = {
            "first cousins of me": ("first_cousins", None, None, None, me),
            "all my Babais": ("relatives", None, None, "babai", me),
            "descendants of FF": ("descendants", None, None, None, self.p["FF"]),
            "everyone on Amma's side": ("relatives", None, "maternal", None, me),
            "my father's side": ("relatives", None, "paternal", None, me),
            "in-laws of Dad": ("in_laws", None, None, None, self.p["Dad"]),
            "Dad's cousins": ("cousins", None, None, None, self.p["Dad"]),
            "my uncles": ("uncles_aunts", "male", None, None, me),
            "sisters": ("siblings", "female", None, None, me),
            "cousins on mother's side": ("cousins", None, "maternal", None, me),
            "tauji": ("relatives", None, None, "tauji", me),
        }
        for q, (rel, gender, side, term, anchor) in cases.items():
            r = self.parse(q)
            self.assertEqual((r["rel"], r["gender"], r["side"], r["term"], r["anchor"]), (rel, gender, side, term, anchor), q)
        self.assertEqual(self.parse("my uncles")["understood"], "uncles and aunts (male) of you")
        for bad in ("cousins of Nobody Here", "wibble", "everyone on Bob's side"):
            self.assertEqual(self.get(f"/api/people/related/parse?q={quote(bad)}").status_code, 422, bad)


if __name__ == "__main__":
    unittest.main()
