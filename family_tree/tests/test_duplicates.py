"""Duplicate finder & merge (§13.4)."""
from base import ALICE, BOB, ApiTestCase, sql
import _env  # noqa: F401

import unittest

from app import duplicates


class Normalise(unittest.TestCase):
    def test_indian_spellings(self):
        n = duplicates.norm
        self.assertEqual(n("Lakshmi"), n("Laxmi"))
        self.assertEqual(n("Venkata"), n("Venkat"))
        self.assertEqual(n("Srinivas"), n("Shrinivasa"))
        self.assertEqual(n("Parvathi"), n("Parvati"))
        self.assertEqual(n("Vasudev"), n("Wasudev"))
        self.assertEqual(n("Annapurna"), n("Anapurna"))
        self.assertGreater(duplicates.jaro_winkler("martha", "marhta"), 0.96)
        self.assertEqual(duplicates.jaro_winkler("", "x"), 0.0)


class Finder(ApiTestCase):
    def test_finds_with_reasons_and_dismissals(self):
        dad = self.person("Subba", "Rao", gender="male")
        mum = self.person("Kamala", "Rao", gender="female")
        a = self.person("Lakshmi", "Sharma", gender="female", born={"y": 1950})
        self.family(dad, mum, [a])
        b = self.person("Laxmi", "Sharma", gender="female", born={"y": 1951})
        d2 = self.person("Subba", "Rao", gender="male")
        m2 = self.person("Kamala", "Rao", gender="female")
        self.family(d2, m2, [b])
        self.person("Lakshmi", "Sharma", gender="female", born={"y": 1990})     # too far apart
        self.person("Lakshman", "Sharma", gender="male", born={"y": 1950})     # other gender
        r = self.ok(self.get("/api/duplicates"))
        pairs = {tuple(sorted((x["a"], x["b"]))): x for x in r["items"]}
        key = tuple(sorted((a, b)))
        self.assertIn(key, pairs)
        self.assertIn("same parents", pairs[key]["reasons"])
        self.assertIn("birth year 1950 vs 1951", pairs[key]["reasons"])
        self.assertIn(tuple(sorted((dad, d2))), pairs)
        self.ok(self.post("/api/duplicates/dismiss", {"a": b, "b": a}))
        pairs = {tuple(sorted((x["a"], x["b"]))) for x in self.ok(self.get("/api/duplicates"))["items"]}
        self.assertNotIn(key, pairs)

    def test_relatives_are_never_duplicates(self):
        a = self.person("Ravi", "Sharma", gender="male", born={"y": 1960})
        b = self.person("Ravi", "Sharma", gender="male", born={"y": 1961})
        self.family(a, None, [b])                        # father and son with the same name
        self.assertEqual(self.ok(self.get("/api/duplicates"))["items"], [])


class Merge(ApiTestCase):
    def test_merge_moves_everything_and_undoes(self):
        keep = self.person("Lakshmi", "Sharma", gender="female", born={"y": 1950})
        other = self.person("Laxmi", "Sharma", gender="female", born={"d": 12, "m": 3, "y": 1950},
                            nickname="Lachi", biography="Loved music")
        dad = self.person("Subba", gender="male")
        husband = self.person("Ravi", gender="male")
        kid = self.person("Kid")
        self.family(dad, None, [other])
        self.family(husband, other, [kid])
        self.ok(self.post("/api/events", {"personId": other, "type": "residence", "place": "Guntur"}), 201)
        self.ok(self.post(f"/api/people/{other}/stories", {"title": "T", "body": "B"}), 201)
        self.set_me(other, user=BOB)
        r = self.ok(self.post(f"/api/people/{keep}/merge", {"otherId": other,
                                                           "choose": {"given_names": "both", "birth": "other", "biography": "other"}}))
        p = r["person"]
        self.assertEqual(p["givenNames"], "Lakshmi")
        self.assertIn({"type": "aka", "name": "Laxmi"}, p["otherNames"])
        self.assertEqual((p["nickname"], p["biography"], p["birth"]["dateDisplay"]), ("Lachi", "Loved music", "12 March 1950"))
        self.assertIn("Birth (other record)", [e["title"] for e in p["events"]])
        self.assertEqual([x["name"] for x in p["parents"]], ["Subba"])
        self.assertEqual([f["partner"]["name"] for f in p["families"]], ["Ravi"])
        self.assertEqual(p["storyCount"], 1)
        self.assertEqual(p["claimedBy"]["id"], BOB["id"])
        self.assertEqual(self.get(f"/api/people/{other}").status_code, 404)
        self.assertEqual(sql("SELECT merged_into FROM people WHERE id = ?", (other,))[0]["merged_into"], keep)
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (r["batchId"],))[0]["label"],
                         "Merged Laxmi Sharma into Lakshmi Sharma")
        # one undo puts it all back
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        o = self.detail(other)
        self.assertEqual(([x["name"] for x in o["parents"]], [f["partner"]["name"] for f in o["families"]]), (["Subba"], ["Ravi"]))
        k = self.detail(keep)
        self.assertEqual((k["parents"], k["families"], k["nickname"]), ([], [], None))

    def test_refusals(self):
        a = self.person("Ravi", gender="male")
        b = self.person("Ravi", gender="male")
        self.family(a, None, [b])
        self.assertEqual(self.post(f"/api/people/{a}/merge", {"otherId": b}).status_code, 422)
        c, d = self.person("X"), self.person("Y")
        self.family(c, d)
        self.assertEqual(self.post(f"/api/people/{c}/merge", {"otherId": d}).status_code, 422)
        e, f = self.person("E"), self.person("F")
        self.set_me(e, user=ALICE)
        self.set_me(f, user=BOB)
        self.assertEqual(self.post(f"/api/people/{e}/merge", {"otherId": f}).status_code, 409)
        self.assertEqual(self.post(f"/api/people/{e}/merge", {"otherId": e}).status_code, 422)
        self.assertEqual(self.post(f"/api/people/{c}/merge", {"otherId": e, "choose": {"shoe": "keep"}}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
