"""Families: quick family entry (§13.18), add parent / partner / child, child
order and reordering, partner slots."""
from base import ApiTestCase, sql

import unittest


class QuickFamily(ApiTestCase):
    def test_new_and_existing_partners_children_order_and_marriage(self):
        ben = self.person("Ben", "Brown", gender="male")
        body = {
            "partners": [
                {"person": {"given_names": "Ann", "surname": "Brown", "birth_surname": "Adams", "gender": "female",
                            "birth": {"date": {"y": 1970}}}},
                {"existingId": ben},
            ],
            "marriage": {"date": {"d": 1, "m": 6, "y": 1995}, "place": "Leeds"},
            "children": [
                {"person": {"given_names": "Cara", "birth": {"date": {"y": 2005}}}},
                {"person": {"given_names": "Alex", "birth": {"date": {"y": 2001}}}, "relation": "adopted"},
                {"person": {"given_names": "Bo"}},
            ],
        }
        r = self.ok(self.post("/api/families/quick", body), 201)
        fam = r["family"]
        self.assertIn("batchId", r)
        # a known male partner goes first
        self.assertEqual([p["name"] for p in fam["partners"]], ["Ben Brown", "Ann Brown"])
        self.assertEqual(fam["marriage"], {"date": {"qual": "exact", "d": 1, "m": 6, "y": 1995},
                                           "dateDisplay": "1 June 1995", "place": "Leeds", "time": None})
        self.assertEqual([(c["name"], c["position"], c["relation"]) for c in fam["children"]],
                         [("Cara", 0, "birth"), ("Alex", 1, "adopted"), ("Bo", 2, "birth")])
        h = self.ok(self.get("/api/history"))["items"][0]
        self.assertEqual(h["label"], "Added family Ben Brown & Ann Brown")
        self.assertEqual(h["id"], r["batchId"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM events WHERE type = 'marriage'")[0]["n"], 1)
        # one undo removes everything the form made; the existing partner stays
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual([p["name"] for p in self.ok(self.get("/api/people"))["items"]], ["Ben Brown"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM families")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM events")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM family_children")[0]["n"], 0)
        self.assertEqual(self.detail(ben)["families"], [])

    def test_single_parent(self):
        r = self.ok(self.post("/api/families/quick", {"partners": [{"person": {"given_names": "Solo"}}],
                                                      "children": [{"person": {"given_names": "K"}}]}), 201)
        self.assertEqual(len(r["family"]["partners"]), 1)
        self.assertIsNone(r["family"]["marriage"])
        self.assertEqual(r["family"]["kind"], "married")

    def test_rejections(self):
        a = self.person("Ann")
        cases = [
            {"partners": [{"existingId": a}, {"existingId": a}]},
            {"partners": [{"existingId": a}], "children": [{"existingId": a}]},
            {"partners": [{"person": {"given_names": "P"}}], "children": [{"person": {"given_names": "K"}}, {"existingId": "x"}]},
            {"partners": []},
            {"partners": [{"person": {"given_names": "a"}}] * 3},
            {"partners": [{"person": {"given_names": "P"}}], "kind": "situationship"},
            {"partners": [{"person": {"given_names": "P"}}], "children": [{"person": {"given_names": "K"}, "relation": "cousin"}]},
            {"partners": [{}]},
            {"partners": [{"person": {"gender": "male"}}]},                       # no name
            {"partners": [{"person": {"given_names": "P"}}], "marriage": {"date": {"m": 5}}},
        ]
        for body in cases:
            with self.subTest(body=body):
                r = self.post("/api/families/quick", body)
                self.assertIn(r.status_code, (404, 422), r.text)
        k = self.person("Kid")
        r = self.post("/api/families/quick", {"partners": [{"existingId": a}], "children": [{"existingId": k}, {"existingId": k}]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM families")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 2)


class AddParent(ApiTestCase):
    def test_fills_open_slot_then_409(self):
        kid = self.person("Kid")
        r1 = self.ok(self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "Dad", "gender": "male"}}), 201)
        dad = r1["newPersonId"]
        self.assertEqual(len(r1["person"]["parentFamilies"]), 1)
        self.assertTrue(r1["person"]["parentFamilies"][0]["openSlot"])
        r2 = self.ok(self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "Mum", "gender": "female"}}), 201)
        pf = r2["person"]["parentFamilies"]
        self.assertEqual(len(pf), 1)
        self.assertEqual([p["name"] for p in pf[0]["partners"]], ["Dad", "Mum"])
        self.assertFalse(pf[0]["openSlot"])
        # Dad and Mum are now partners of each other
        self.assertEqual(self.detail(dad)["families"][0]["partner"]["name"], "Mum")
        r3 = self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "Third"}})
        self.assertEqual(r3.status_code, 409)
        self.assertIn("both birth parents", r3.json()["detail"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people WHERE given_names = 'Third'")[0]["n"], 0)
        # explicitly naming the full family is a 409 too
        r4 = self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "Third"}, "familyId": pf[0]["id"]})
        self.assertEqual(r4.status_code, 409)
        # an adoptive parent is a separate family
        r5 = self.ok(self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "Ada", "gender": "female"},
                                                                  "relation": "adopted"}), 201)
        rels = sorted(f["relation"] for f in r5["person"]["parentFamilies"])
        self.assertEqual(rels, ["adopted", "birth"])
        self.assertEqual(self.label(kid, r5["newPersonId"]), "adoptive mother")

    def test_existing_parent_and_errors(self):
        kid = self.person("Kid")
        dad = self.person("Dad", gender="male")
        body = self.ok(self.post(f"/api/people/{kid}/add-parent", {"existingId": dad}), 201)
        fid = body["person"]["parentFamilies"][0]["id"]
        self.assertEqual(self.post(f"/api/people/{kid}/add-parent", {"existingId": dad}).status_code, 409)
        self.assertEqual(self.post(f"/api/people/{kid}/add-parent", {"existingId": "nope"}).status_code, 404)
        self.assertEqual(self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "X"}, "familyId": "nope"}).status_code, 404)
        self.assertEqual(self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "X"}, "relation": "uncle"}).status_code, 422)
        self.assertEqual(self.post(f"/api/people/{kid}/add-parent", {}).status_code, 422)
        self.assertEqual(self.post("/api/people/nope/add-parent", {"person": {"given_names": "X"}}).status_code, 404)
        mum = self.ok(self.post(f"/api/people/{kid}/add-parent", {"person": {"given_names": "Mum"}, "familyId": fid}), 201)
        self.assertEqual(len(mum["person"]["parents"]), 2)

    def test_open_slot_joins_siblings(self):
        dad = self.person("Dad", gender="male")
        k1, k2 = self.person("K1"), self.person("K2")
        fid = self.family(dad, None, [k1, k2])
        self.ok(self.post(f"/api/people/{k1}/add-parent", {"person": {"given_names": "Mum", "gender": "female"}}), 201)
        fam = self.ok(self.get(f"/api/families/{fid}"))
        self.assertEqual([p["name"] for p in fam["partners"]], ["Dad", "Mum"])
        self.assertEqual(len(self.detail(k2)["parents"]), 2)


class AddPartner(ApiTestCase):
    def test_new_partner_family_and_marriage(self):
        ann = self.person("Ann", gender="female")
        r = self.ok(self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Ben", "gender": "male"},
                                                                  "marriage": {"date": {"y": 1990}, "place": "Rome"}}), 201)
        fam = self.ok(self.get(f"/api/families/{r['familyId']}"))
        self.assertEqual([p["name"] for p in fam["partners"]], ["Ben", "Ann"])
        self.assertEqual(fam["marriage"]["dateDisplay"], "1990")
        self.assertEqual(self.post(f"/api/people/{ann}/add-partner", {"existingId": ann}).status_code, 422)

    def test_fill_single_parent_family(self):
        ann = self.person("Ann", gender="female")
        k = self.person("K")
        fid = self.ok(self.post(f"/api/people/{ann}/add-child", {"existingId": k}), 201)["familyId"]
        self.assertEqual(self.ok(self.get(f"/api/families/{fid}"))["kind"], "unknown")
        r = self.ok(self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Ben"}, "familyId": fid,
                                                                  "kind": "partners"}), 201)
        self.assertEqual(r["familyId"], fid)
        fam = self.ok(self.get(f"/api/families/{fid}"))
        self.assertEqual(fam["kind"], "partners")
        self.assertEqual(len(fam["partners"]), 2)
        self.assertEqual(len(self.detail(k)["parents"]), 2)
        r = self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Cy"}, "familyId": fid})
        self.assertEqual(r.status_code, 409)
        other = self.family(self.person("Z"))
        self.assertEqual(self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Cy"},
                                                                       "familyId": other}).status_code, 404)

    def test_slot_of_a_trashed_partner_counts_as_open(self):
        # the trashed partner is hidden everywhere, so the family shows one partner
        # and an open slot; filling it must not answer "already has two"
        ann = self.person("Ann", gender="female")
        ben = self.person("Ben", gender="male")
        k = self.person("Kid")
        fid = self.family(ann, ben, [k])
        self.ok(self.delete(f"/api/people/{ben}"))
        pf = self.detail(k)["parentFamilies"]
        self.assertTrue(pf[0]["openSlot"])
        r = self.ok(self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Cy"}, "familyId": fid}), 201)
        self.assertEqual(r["familyId"], fid)
        self.assertEqual(sorted(p["name"] for p in self.detail(k)["parents"]), ["Ann", "Cy"])
        undo = self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertTrue(undo["ok"])
        # the same through add-parent, with and without the familyId
        mum = self.ok(self.post(f"/api/people/{k}/add-parent", {"person": {"given_names": "Dee"}, "familyId": fid}), 201)
        self.assertEqual(sorted(p["name"] for p in mum["person"]["parents"]), ["Ann", "Dee"])
        self.ok(self.post(f"/api/history/{mum['batchId']}/undo"))
        auto = self.ok(self.post(f"/api/people/{k}/add-parent", {"person": {"given_names": "Eve"}}), 201)
        self.assertEqual([f["id"] for f in auto["person"]["parentFamilies"]], [fid])


class AddChild(ApiTestCase):
    def test_needs_family_when_several(self):
        ann = self.person("Ann")
        f1 = self.ok(self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Ben"}}), 201)["familyId"]
        self.ok(self.post(f"/api/people/{ann}/add-partner", {"person": {"given_names": "Cal"}}), 201)
        r = self.post(f"/api/people/{ann}/add-child", {"person": {"given_names": "Kid"}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people WHERE given_names = 'Kid'")[0]["n"], 0)
        r = self.ok(self.post(f"/api/people/{ann}/add-child", {"person": {"given_names": "Kid"}, "familyId": f1}), 201)
        self.assertEqual(r["familyId"], f1)
        self.assertEqual(self.post(f"/api/people/{ann}/add-child", {"person": {"given_names": "K"}, "familyId": "nope"}).status_code, 404)

    def test_single_family_used_and_none_creates_one(self):
        ann = self.person("Ann")
        r1 = self.ok(self.post(f"/api/people/{ann}/add-child", {"person": {"given_names": "K1"}}), 201)
        r2 = self.ok(self.post(f"/api/people/{ann}/add-child", {"person": {"given_names": "K2"}}), 201)
        self.assertEqual(r1["familyId"], r2["familyId"])
        self.assertEqual(self.post(f"/api/people/{ann}/add-child", {"existingId": r1["newPersonId"]}).status_code, 409)

    def test_positions_follow_birth_dates(self):
        ann = self.person("Ann")
        fid = self.ok(self.post(f"/api/people/{ann}/add-child", {"person": {"given_names": "B1990", "birth": {"date": {"y": 1990}}}}), 201)["familyId"]

        def add(name, born=None):
            p = {"given_names": name}
            if born:
                p["birth"] = {"date": born}
            self.ok(self.post(f"/api/families/{fid}/children", {"person": p}), 201)

        add("C2000", {"y": 2000})
        add("M1995", {"m": 5, "y": 1995})
        add("NoDate")
        add("A1980", {"y": 1980})
        add("D1995b", {"d": 20, "m": 5, "y": 1995})
        add("Yearless", {"d": 1, "m": 1})
        fam = self.ok(self.get(f"/api/families/{fid}"))
        self.assertEqual([(c["name"], c["position"]) for c in fam["children"]],
                         [("A1980", 0), ("B1990", 1), ("M1995", 2), ("D1995b", 3), ("C2000", 4), ("NoDate", 5),
                          ("Yearless", 6)])

    def test_reorder_relation_and_remove(self):
        ann = self.person("Ann")
        kids = [self.person(n) for n in ("K0", "K1", "K2", "K3")]
        fid = self.family(ann, None, kids)

        def order():
            return [(c["name"], c["position"]) for c in self.ok(self.get(f"/api/families/{fid}"))["children"]]

        self.assertEqual(order(), [("K0", 0), ("K1", 1), ("K2", 2), ("K3", 3)])
        self.ok(self.patch(f"/api/families/{fid}/children/{kids[3]}", {"position": 0}))
        self.assertEqual(order(), [("K3", 0), ("K0", 1), ("K1", 2), ("K2", 3)])
        self.ok(self.patch(f"/api/families/{fid}/children/{kids[3]}", {"position": 99}))
        self.assertEqual(order(), [("K0", 0), ("K1", 1), ("K2", 2), ("K3", 3)])
        body = self.ok(self.patch(f"/api/families/{fid}/children/{kids[1]}", {"position": 2, "relation": "step"}))
        self.assertEqual(order(), [("K0", 0), ("K2", 1), ("K1", 2), ("K3", 3)])
        self.assertEqual([c["relation"] for c in body["family"]["children"]], ["birth", "birth", "step", "birth"])
        self.assertEqual(self.patch(f"/api/families/{fid}/children/{kids[1]}", {"position": -1}).status_code, 422)
        self.assertEqual(self.patch(f"/api/families/{fid}/children/{kids[1]}", {"relation": "x"}).status_code, 422)
        self.assertEqual(self.patch(f"/api/families/{fid}/children/{ann}", {"position": 0}).status_code, 404)
        # undoing the reorder restores the previous order
        bid = self.ok(self.patch(f"/api/families/{fid}/children/{kids[0]}", {"position": 3}))["batchId"]
        self.ok(self.post(f"/api/history/{bid}/undo"))
        self.assertEqual(order(), [("K0", 0), ("K2", 1), ("K1", 2), ("K3", 3)])
        self.ok(self.delete(f"/api/families/{fid}/children/{kids[2]}"))
        self.assertEqual([n for n, _ in order()], ["K0", "K1", "K3"])
        self.assertEqual(self.detail(kids[2])["parents"], [])
        self.assertEqual(self.delete(f"/api/families/{fid}/children/{kids[2]}").status_code, 404)


class Partners(ApiTestCase):
    def test_set_and_clear_partner_slots(self):
        a = self.person("Ann", gender="female")
        b = self.person("Ben", gender="male")
        c = self.person("Cal", gender="male")
        fid = self.family(a, None)
        body = self.ok(self.put(f"/api/families/{fid}/partners/2", {"personId": b}))
        self.assertIn("batchId", body)
        self.assertEqual([p["id"] for p in body["family"]["partners"]], [a, b])
        self.ok(self.put(f"/api/families/{fid}/partners/2", {"personId": c}))       # replace
        self.assertEqual(self.detail(b)["families"], [])
        self.assertEqual(self.detail(c)["families"][0]["partner"]["id"], a)
        self.ok(self.put(f"/api/families/{fid}/partners/1", {"personId": None}))    # clear one
        self.assertEqual([p["id"] for p in self.ok(self.get(f"/api/families/{fid}"))["partners"]], [c])
        r = self.put(f"/api/families/{fid}/partners/2", {"personId": None})       # can't clear the last one
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.put(f"/api/families/{fid}/partners/1", {"personId": c}).status_code, 422)
        self.assertEqual(self.put(f"/api/families/{fid}/partners/3", {"personId": a}).status_code, 404)
        self.assertEqual(self.put(f"/api/families/{fid}/partners/1", {"personId": "nope"}).status_code, 404)
        self.assertEqual(self.put("/api/families/nope/partners/1", {"personId": a}).status_code, 404)

    def test_family_crud(self):
        a = self.person("Ann")
        self.assertEqual(self.post("/api/families", {}).status_code, 422)
        self.assertEqual(self.post("/api/families", {"partner1Id": a, "partner2Id": a}).status_code, 422)
        self.assertEqual(self.post("/api/families", {"partner1Id": "nope"}).status_code, 404)
        self.assertEqual(self.post("/api/families", {"partner1Id": a, "kind": "bff"}).status_code, 422)
        fid = self.ok(self.post("/api/families", {"partner1Id": a, "marriage": {"date": {"y": 2000}}}), 201)["family"]["id"]
        f = self.ok(self.patch(f"/api/families/{fid}", {"ended": "widowed"}))["family"]
        self.assertEqual((f["ended"], f["marriage"]["dateDisplay"]), ("widowed", "2000"))
        self.assertEqual(self.patch(f"/api/families/{fid}", {"ended": "exploded"}).status_code, 422)
        f = self.ok(self.patch(f"/api/families/{fid}", {"marriage": None}))["family"]
        self.assertIsNone(f["marriage"])
        self.assertEqual(self.get("/api/families/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
