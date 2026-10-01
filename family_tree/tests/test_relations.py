"""Relationship names (§6) and loop rejection (§5), built through the API."""
from base import BOB, ApiTestCase, reset_state

import unittest

from fastapi.testclient import TestClient

from app.main import app


class SharedTree(ApiTestCase):
    """Builds its tree once per class (tests only read it)."""

    @classmethod
    def setUpClass(cls):
        cls._built = False

    def setUp(self):
        self.client = TestClient(app, client=("127.0.0.1", 12345))
        cls = type(self)
        if not cls._built:
            reset_state(all_features=cls.ALL_FEATURES)
            cls.p = {}
            self.build()
            cls._built = True

    def add(self, key, given, gender, surname=None, **kw):
        self.p[key] = self.person(given, surname, gender=gender, **kw)
        return self.p[key]

    def fam(self, a, b, *children, relation="birth", kind="married"):
        return self.family(self.p[a] if a else None, self.p[b] if b else None,
                           [self.p[c] for c in children], relation=relation, kind=kind)


class BloodAndInLaw(SharedTree):
    """
    GGG1 ─ GG1+GG2 ─ GGF+GGM ─┬─ GF+GM ─┬─ Dad+Mom ─┬─ Me(f)+Hubby ─ Son, Daughter+SonInLaw
                              │         │           ├─ Brother+SisInLaw ─ Nephew ─ GrandNiece
                              │         │           └─ Sister
                              │         │  Dad+Stepmom ─ HalfBro
                              │         └─ Aunt+UncleBM ─ Cousin1+CousinHub ─ Cousin1Kid
                              └─ GAunt ─ C1 ─ C2 ─ C3 ─ C4
    Hubby's side: SGF+SGM ─┬─ FIL+MIL ─┬─ Hubby
                           │           └─ BIL+Wendy
                           └─ SAunt ─ SCousin
    Stranger: nobody.
    """

    def build(self):
        a = self.add
        a("GGG1", "Gregor", "male")
        a("GG1", "Gustav", "male"); a("GG2", "Greta", "female")
        a("GGF", "George", "male"); a("GGM", "Gloria", "female")
        a("GF", "Frank", "male"); a("GM", "Frances", "female")
        a("GAunt", "Gertrude", "female")
        a("Dad", "Dan", "male"); a("Mom", "Mary", "female"); a("Stepmom", "Stella", "female")
        a("Aunt", "Anne", "female"); a("UncleBM", "Ulrich", "male")
        a("Me", "Meg", "female"); a("Hubby", "Hugo", "male")
        a("Brother", "Bert", "male"); a("SisInLaw", "Sally", "female"); a("Sister", "Susan", "female")
        a("HalfBro", "Harry", "male")
        a("Nephew", "Ned", "male"); a("GrandNiece", "Gina", "female")
        a("Cousin1", "Cora", "female"); a("CousinHub", "Carl", "male"); a("Cousin1Kid", "Kyle", "male")
        a("Son", "Sam", "male"); a("Daughter", "Dora", "female"); a("SonInLaw", "Seth", "male")
        a("C1", "Cid", "male"); a("C2", "Cal", "male"); a("C3", "Cam", "female"); a("C4", "Cy", "male")
        a("SGF", "Stan", "male"); a("SGM", "Sue", "female")
        a("FIL", "Fred", "male"); a("MIL", "Mabel", "female")
        a("BIL", "Boris", "male"); a("Wendy", "Wendy", "female")
        a("SAunt", "Sara", "female"); a("SCousin", "Sophie", "female")
        a("Stranger", "Xavier", "male"); a("WendyDad", "Walt", "male")

        f = self.fam
        f("GGG1", None, "GG1")
        f("GG1", "GG2", "GGF")
        f("GGF", "GGM", "GF", "GAunt")
        f("GF", "GM", "Dad", "Aunt")
        f("Dad", "Mom", "Me", "Brother", "Sister")
        f("Dad", "Stepmom", "HalfBro")
        f("Aunt", "UncleBM", "Cousin1")
        f("Cousin1", "CousinHub", "Cousin1Kid")
        f("Me", "Hubby", "Son", "Daughter")
        f("Daughter", "SonInLaw")
        f("Brother", "SisInLaw", "Nephew")
        f("Nephew", None, "GrandNiece")
        f("GAunt", None, "C1")
        f("C1", None, "C2")
        f("C2", None, "C3")
        f("C3", None, "C4")
        f("SGF", "SGM", "FIL", "SAunt")
        f("FIL", "MIL", "Hubby", "BIL")
        f("BIL", "Wendy")
        f("WendyDad", None, "Wendy")
        f("SAunt", None, "SCousin")

    def L(self, a, b):
        return self.label(self.p[a], self.p[b])

    def assertLabels(self, frm, table):
        for key, want in table.items():
            with self.subTest(frm=frm, to=key):
                self.assertEqual(self.L(frm, key), want)

    def test_direct_line(self):
        self.assertLabels("Me", {
            "Me": "you", "Dad": "father", "Mom": "mother", "GF": "grandfather", "GM": "grandmother",
            "GGF": "great-grandfather", "GGM": "great-grandmother", "GG1": "great-great-grandfather",
            "GG2": "great-great-grandmother", "GGG1": "3× great-grandfather",
            "Son": "son", "Daughter": "daughter",
        })
        self.assertEqual(self.L("GF", "Me"), "granddaughter")
        self.assertEqual(self.L("GGF", "Nephew"), "great-great-grandson")

    def test_siblings_and_half(self):
        self.assertLabels("Me", {"Brother": "brother", "Sister": "sister", "HalfBro": "half-brother"})
        self.assertEqual(self.L("HalfBro", "Me"), "half-sister")

    def test_aunts_nieces_cousins(self):
        self.assertLabels("Me", {
            "Aunt": "aunt", "GAunt": "great-aunt",
            "Nephew": "nephew", "GrandNiece": "grandniece",
            "Cousin1": "first cousin", "Cousin1Kid": "first cousin once removed",
            "C1": "first cousin once removed", "C2": "second cousin", "C3": "second cousin once removed",
            "C4": "second cousin twice removed",
        })
        self.assertEqual(self.L("Cousin1Kid", "Me"), "first cousin once removed")
        self.assertEqual(self.L("C4", "Me"), "second cousin twice removed")
        self.assertEqual(self.L("Nephew", "Me"), "aunt")
        self.assertEqual(self.L("GrandNiece", "Dad"), "great-grandfather")
        self.assertEqual(self.L("GrandNiece", "Me"), "great-aunt")
        self.assertEqual(self.L("Dad", "GrandNiece"), "great-granddaughter")

    def test_in_laws(self):
        self.assertLabels("Me", {
            "Hubby": "husband", "SisInLaw": "sister-in-law", "BIL": "brother-in-law",
            "FIL": "father-in-law", "MIL": "mother-in-law", "SonInLaw": "son-in-law",
            "Stepmom": "stepmother", "UncleBM": "uncle by marriage",
            "SCousin": "husband's first cousin", "CousinHub": "first cousin's husband",
        })
        self.assertEqual(self.L("Hubby", "Me"), "wife")
        self.assertEqual(self.L("Hubby", "Cousin1"), "wife's first cousin")
        self.assertEqual(self.L("Hubby", "Brother"), "brother-in-law")      # wife's brother
        self.assertEqual(self.L("Brother", "Hubby"), "brother-in-law")      # sister's husband
        self.assertEqual(self.L("BIL", "Me"), "sister-in-law")              # brother's wife
        self.assertEqual(self.L("SisInLaw", "Me"), "sister-in-law")         # husband's sister
        self.assertEqual(self.L("FIL", "Me"), "daughter-in-law")
        self.assertEqual(self.L("Stepmom", "Me"), "stepdaughter")
        self.assertEqual(self.L("UncleBM", "Me"), "wife's niece")

    def test_related_by_marriage_and_not_related(self):
        r = self.rel(self.p["Me"], self.p["Wendy"])                 # across two marriages, still named
        self.assertEqual((r["label"], r["kind"]), ("husband's brother's wife", "inlaw"))
        self.assertEqual([s["t"] for s in r["steps"]], ["spouse", "sibling", "spouse"])
        self.assertEqual(self.L("Brother", "Hubby"), "brother-in-law")
        self.assertEqual(self.L("Nephew", "Hubby"), "uncle by marriage")
        self.assertEqual(self.L("Hubby", "SisInLaw"), "wife's brother's wife")
        self.assertEqual(self.L("SisInLaw", "Hubby"), "husband's sister's husband")
        r = self.rel(self.p["Me"], self.p["WendyDad"])              # further out: related by marriage
        self.assertEqual(r["label"], "related by marriage")
        self.assertEqual(r["kind"], "marriage")
        ids = [s["id"] for s in r["path"]]
        self.assertEqual(ids[0], self.p["Me"])
        self.assertEqual(ids[-1], self.p["WendyDad"])
        r = self.rel(self.p["Me"], self.p["Stranger"])
        self.assertEqual({k: r[k] for k in ("label", "kind", "path")}, {"label": "not related", "kind": "none", "path": []})
        self.assertIsNone(r["term"])

    def test_kinship_path_blood(self):
        r = self.rel(self.p["Me"], self.p["Cousin1"])
        self.assertEqual(r["kind"], "blood")
        path = r["path"]
        self.assertEqual([s["id"] for s in path][:2], [self.p["Me"], self.p["Dad"]])
        self.assertIn(path[2]["id"], (self.p["GF"], self.p["GM"]))
        self.assertEqual([s["id"] for s in path][3:], [self.p["Aunt"], self.p["Cousin1"]])
        self.assertEqual([s["rel"] for s in path][0:2], [None, "father"])
        self.assertEqual([s["rel"] for s in path][3:], ["daughter", "daughter"])
        self.assertEqual(path[0]["name"], "Meg")
        self.assertEqual(path[-1]["name"], "Cora")

    def test_kinship_path_in_law(self):
        r = self.rel(self.p["Me"], self.p["FIL"])
        self.assertEqual(r["kind"], "inlaw")
        self.assertEqual([(s["id"], s["rel"]) for s in r["path"]],
                         [(self.p["Me"], None), (self.p["Hubby"], "husband"), (self.p["FIL"], "father")])
        r = self.rel(self.p["Me"], self.p["SonInLaw"])
        self.assertEqual([s["id"] for s in r["path"]], [self.p["Me"], self.p["Daughter"], self.p["SonInLaw"]])
        self.assertEqual(r["path"][-1]["rel"], "husband")

    def test_self_relationship(self):
        r = self.rel(self.p["Me"], self.p["Me"])
        self.assertEqual(r["label"], "you")
        self.assertEqual(r["kind"], "self")

    def test_relationship_unknown_person_404(self):
        self.assertEqual(self.get(f"/api/relationship?from={self.p['Me']}&to=nope").status_code, 404)
        self.assertEqual(self.get(f"/api/relationship?from=nope&to={self.p['Me']}").status_code, 404)

    def test_labels_relative_to_me_in_api(self):
        self.set_me(self.p["Me"], user=BOB)
        d = self.detail(self.p["Cousin1"], user=BOB)
        self.assertEqual(d["relationshipToMe"]["label"], "first cousin")
        parents = {x["id"]: x["relationship"] for x in d["parents"]}
        self.assertEqual(parents[self.p["Aunt"]], "aunt")
        self.assertEqual(parents[self.p["UncleBM"]], "uncle by marriage")
        me = self.detail(self.p["Me"], user=BOB)
        sib = {x["id"]: (x["relationship"], x["full"]) for x in me["siblings"]}
        self.assertEqual(sib[self.p["HalfBro"]], ("half-brother", False))
        self.assertEqual(sib[self.p["Brother"]], ("brother", True))
        tree = self.ok(self.get("/api/tree?up=1&down=1", user=BOB))
        self.assertEqual(tree["focus"], self.p["Me"])
        self.assertEqual(tree["ancestors"]["p"]["relationship"], "you")
        self.assertEqual(tree["ancestors"]["parents"]["a"]["p"]["relationship"], "father")
        self.set_me(None, user=BOB)


class NeutralWording(SharedTree):
    """Everyone's gender is unknown."""

    def build(self):
        a = self.add
        for k in ("GGP", "GP", "GAunt", "Par", "PSib", "Me", "Sib", "SibKid", "SibGKid", "Kid", "GKid", "Spouse",
                  "SpSib", "Cousin", "SpParent", "Step"):
            a(k, k, "unknown")
        f = self.fam
        f("GGP", None, "GP", "GAunt")
        f("GP", None, "Par", "PSib")
        f("Par", None, "Me", "Sib")
        f("Par", "Step")
        f("PSib", None, "Cousin")
        f("Sib", None, "SibKid")
        f("SibKid", None, "SibGKid")
        f("Me", "Spouse", "Kid")
        f("Kid", None, "GKid")
        f("SpParent", None, "Spouse", "SpSib")

    def test_neutral_labels(self):
        want = {
            "Par": "parent", "GP": "grandparent", "GGP": "great-grandparent", "Sib": "sibling",
            "PSib": "parent's sibling", "GAunt": "grandparent's sibling", "SibKid": "sibling's child",
            "SibGKid": "sibling's grandchild", "Kid": "child", "GKid": "grandchild", "Cousin": "first cousin",
            "Spouse": "spouse", "SpSib": "sibling-in-law", "SpParent": "parent-in-law", "Step": "step-parent",
        }
        for k, v in want.items():
            with self.subTest(k=k):
                self.assertEqual(self.label(self.p["Me"], self.p[k]), v)
        self.assertEqual(self.label(self.p["Par"], self.p["Spouse"]), "child-in-law")
        self.assertEqual(self.label(self.p["Spouse"], self.p["Kid"]), "child")

    def test_partners_kind_and_ex(self):
        fid = self.detail(self.p["Me"])["families"][0]["id"]
        self.ok(self.patch(f"/api/families/{fid}", {"kind": "partners"}))
        self.assertEqual(self.label(self.p["Me"], self.p["Spouse"]), "partner")
        self.ok(self.patch(f"/api/families/{fid}", {"kind": "married", "ended": "divorced"}))
        self.assertEqual(self.label(self.p["Me"], self.p["Spouse"]), "ex-spouse")
        self.ok(self.patch(f"/api/families/{fid}", {"ended": None}))
        self.assertEqual(self.label(self.p["Me"], self.p["Spouse"]), "spouse")


class ParentRelationKinds(ApiTestCase):
    def test_adoptive_step_foster(self):
        kid = self.person("Kid", gender="male")
        kid2 = self.person("Kid2", gender="female")
        bm = self.person("BirthMum", gender="female")
        am = self.person("AdoptMum", gender="female")
        sf = self.person("StepDad", gender="male")
        fm = self.person("FosterMum", gender="female")
        self.family(bm, None, [kid, kid2])
        self.family(am, None, [(kid, "adopted"), (kid2, "adopted")])
        self.family(sf, None, [(kid, "step"), (kid2, "step")])
        self.family(fm, None, [(kid, "foster")])
        self.assertEqual(self.label(kid, bm), "mother")
        self.assertEqual(self.label(kid, am), "adoptive mother")
        self.assertEqual(self.label(kid, sf), "stepfather")
        self.assertEqual(self.label(kid, fm), "foster mother")
        self.assertEqual(self.label(am, kid), "adopted son")
        self.assertEqual(self.label(sf, kid2), "stepdaughter")
        self.assertEqual(self.label(fm, kid), "foster son")
        self.assertEqual(self.label(kid, kid2), "sister")

    def test_step_parent_via_parents_partner(self):
        kid = self.person("Kid", gender="female")
        mum = self.person("Mum", gender="female")
        new = self.person("Newhusband", gender="male")
        self.family(mum, None, [kid])
        self.family(mum, new)
        self.assertEqual(self.label(kid, new), "stepfather")
        self.assertEqual(self.label(new, kid), "stepdaughter")

    def test_label_on_detail_parents(self):
        kid = self.person("Kid", gender="male")
        am = self.person("AdoptMum", gender="female")
        self.family(am, None, [(kid, "adopted")])
        self.set_me(kid)
        d = self.detail(kid)
        self.assertEqual(d["parents"][0]["relation"], "adopted")
        self.assertEqual(d["parents"][0]["relationship"], "adoptive mother")


class HalfSiblingsWithStepLink(ApiTestCase):
    """Kid (Mum + BioDad) is also linked as a *step* child into StepDad + Mum,
    whose birth child is Y. Kid and Y share only Mum by birth: half-siblings.
    Ids are made deterministic so the result can't depend on uuid order."""

    def setUp(self):
        super().setUp()
        from app import db
        counter = iter(range(1, 10 ** 6))
        orig = db.new_id
        db.new_id = lambda: f"{next(counter):032x}"
        self.addCleanup(setattr, db, "new_id", orig)

    def build(self, stepdad_first):
        if stepdad_first:
            step = self.person("Step", gender="male")
            mum = self.person("Mum", gender="female")
        else:
            mum = self.person("Mum", gender="female")
            step = self.person("Step", gender="male")
        bio = self.person("Bio", gender="male")
        kid = self.person("Kid", gender="male")
        y = self.person("Y", gender="female")
        self.family(bio, mum, [kid])
        self.family(step, mum, [(kid, "step"), y])
        return kid, y, step

    def test_mum_created_first(self):
        kid, y, step = self.build(stepdad_first=False)
        self.assertEqual(self.label(kid, y), "half-sister")
        self.assertEqual(self.label(y, kid), "half-brother")
        self.assertEqual(self.label(kid, step), "stepfather")

    def test_stepdad_created_first(self):
        kid, y, step = self.build(stepdad_first=True)
        self.assertEqual(self.label(kid, step), "stepfather")
        self.assertEqual(self.label(kid, y), "half-sister")
        self.assertEqual(self.label(y, kid), "half-brother")


class LoopRejection(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.gp = self.person("Grandpa", gender="male")
        self.par = self.person("Parent", gender="male")
        self.kid = self.person("Kid", gender="female")
        self.f_gp = self.family(self.gp, None, [self.par])
        self.f_par = self.family(self.par, None, [self.kid])

    def assert_unchanged(self, before_batches):
        n = len(self.ok(self.get("/api/history?page_size=200"))["items"])
        self.assertEqual(n, before_batches)

    def test_descendant_as_parent(self):
        before = len(self.ok(self.get("/api/history?page_size=200"))["items"])
        r = self.post(f"/api/people/{self.gp}/add-parent", {"existingId": self.kid})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.post(f"/api/people/{self.gp}/add-parent", {"existingId": self.gp})
        self.assertEqual(r.status_code, 422, r.text)
        self.assert_unchanged(before)

    def test_ancestor_as_child(self):
        before = len(self.ok(self.get("/api/history?page_size=200"))["items"])
        r = self.post(f"/api/people/{self.kid}/add-child", {"existingId": self.gp})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.post(f"/api/families/{self.f_par}/children", {"existingId": self.gp})
        self.assertEqual(r.status_code, 422, r.text)
        r = self.post(f"/api/families/{self.f_par}/children", {"existingId": self.par})
        self.assertEqual(r.status_code, 422, r.text)
        self.assert_unchanged(before)

    def test_set_partner_creating_loop(self):
        r = self.put(f"/api/families/{self.f_gp}/partners/2", {"personId": self.kid})
        self.assertEqual(r.status_code, 422, r.text)
        fam = self.ok(self.get(f"/api/families/{self.f_gp}"))
        self.assertEqual([p["id"] for p in fam["partners"]], [self.gp])

    def test_add_partner_into_family_creating_loop(self):
        r = self.post(f"/api/people/{self.gp}/add-partner", {"existingId": self.kid, "familyId": self.f_gp})
        self.assertEqual(r.status_code, 422, r.text)

    def test_quick_family_creating_loop(self):
        r = self.post("/api/families/quick", {"partners": [{"existingId": self.kid}],
                                              "children": [{"existingId": self.gp}]})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.detail(self.kid)["families"], [])

    def test_valid_links_still_allowed(self):
        other = self.person("Other", gender="female")
        self.ok(self.put(f"/api/families/{self.f_gp}/partners/2", {"personId": other}))
        self.ok(self.post(f"/api/people/{self.kid}/add-child", {"person": {"given_names": "Baby"}}), 201)
        self.assertEqual(self.label(self.kid, self.gp), "grandfather")
        self.assertEqual(self.detail(self.kid)["warnings"], [])


if __name__ == "__main__":
    unittest.main()
