"""Indian relationship names (§13.1): kin keys, elder/younger from
dates or birth order, speaker gender, custom terms and the fallbacks."""
from base import ADMIN, BOB, ApiTestCase, set_app_settings, sql
import _env  # noqa: F401

import unittest

from app import graph as graph_mod, kin, relations
from test_relations import SharedTree


def D(y, m=1, d=1):
    return {"y": y, "m": m, "d": d}


class KinTree(SharedTree):
    """
    FF+FM ─┬─ PeddaNanna(1955)+Peddamma ─ PNSon(1985), PNDaughter(1995)
           ├─ Dad(1960)+Mum
           ├─ Babai(no date, born after Dad by order)+Pinni ─ BabaiSon(no date)
           └─ Atta(1968)+Mamayya ─ AttaSon(1988), AttaDaughter(1996)
    MF+MM ─┬─ MumBigSis(1958)+MumBigSisH ─ MBSSon(1989)
           ├─ Mum(1962)
           ├─ MumSmallSis(1966)+MumSmallSisH
           └─ MumBro(1964)+MumBroW ─ MumBroSon(1992), MumBroDaughter(1985)
    Dad+Mum ─┬─ Akka(f,1987)+AkkaH ─ AkkaSon
             ├─ Me(m,1990)+Wife ─ Son+Kodalu ─ GrandSon ; Daughter+Alludu
             └─ Tammudu(1993)+TammuduW ─ TammuduSon
    WifeF+WifeM ─┬─ WifeBro(1985) ; Wife(1991) ; WifeSis(1994)
    """

    def build(self):
        a = self.add
        a("FF", "FF", "male", born=1930); a("FM", "FM", "female", born=1932)
        a("MF", "MF", "male", born=1931); a("MM", "MM", "female", born=1934)
        a("PeddaNanna", "PeddaNanna", "male", born=D(1955)); a("Peddamma", "Peddamma", "female")
        a("Dad", "Dad", "male", born=D(1960)); a("Mum", "Mum", "female", born=D(1962))
        a("Babai", "Babai", "male"); a("Pinni", "Pinni", "female")
        a("Atta", "Atta", "female", born=D(1968)); a("Mamayya", "Mamayya", "male")
        self.fam("FF", "FM", "PeddaNanna", "Dad", "Babai", "Atta")
        a("PNSon", "PNSon", "male", born=D(1985)); a("PNDaughter", "PNDaughter", "female", born=D(1995))
        self.fam("PeddaNanna", "Peddamma", "PNSon", "PNDaughter")
        a("BabaiSon", "BabaiSon", "male")
        self.fam("Babai", "Pinni", "BabaiSon")
        a("AttaSon", "AttaSon", "male", born=D(1988)); a("AttaDaughter", "AttaDaughter", "female", born=D(1996))
        self.fam("Mamayya", "Atta", "AttaSon", "AttaDaughter")
        a("MumBigSis", "MumBigSis", "female", born=D(1958)); a("MumBigSisH", "MumBigSisH", "male")
        a("MumSmallSis", "MumSmallSis", "female", born=D(1966)); a("MumSmallSisH", "MumSmallSisH", "male")
        a("MumBro", "MumBro", "male", born=D(1964)); a("MumBroW", "MumBroW", "female")
        self.fam("MF", "MM", "MumBigSis", "Mum", "MumBro", "MumSmallSis")
        a("MBSSon", "MBSSon", "male", born=D(1989))
        self.fam("MumBigSisH", "MumBigSis", "MBSSon")
        self.fam("MumSmallSisH", "MumSmallSis")
        a("MumBroSon", "MumBroSon", "male", born=D(1992)); a("MumBroDaughter", "MumBroDaughter", "female", born=D(1985))
        self.fam("MumBro", "MumBroW", "MumBroSon", "MumBroDaughter")
        a("Akka", "Akka", "female", born=D(1987)); a("Me", "Me", "male", born=D(1990, 5, 1))
        a("Tammudu", "Tammudu", "male", born=D(1993))
        self.fam("Dad", "Mum", "Akka", "Me", "Tammudu")
        a("AkkaH", "AkkaH", "male"); a("AkkaSon", "AkkaSon", "male")
        self.fam("AkkaH", "Akka", "AkkaSon")
        a("TammuduW", "TammuduW", "female"); a("TammuduSon", "TammuduSon", "male")
        self.fam("Tammudu", "TammuduW", "TammuduSon")
        a("Wife", "Wife", "female", born=D(1991))
        a("Son", "Son", "male"); a("Daughter", "Daughter", "female")
        self.fam("Me", "Wife", "Son", "Daughter")
        a("Kodalu", "Kodalu", "female"); a("GrandSon", "GrandSon", "male")
        self.fam("Son", "Kodalu", "GrandSon")
        a("Alludu", "Alludu", "male")
        self.fam("Alludu", "Daughter")
        a("WifeF", "WifeF", "male"); a("WifeM", "WifeM", "female")
        a("WifeBro", "WifeBro", "male", born=D(1985)); a("WifeSis", "WifeSis", "female", born=D(1994))
        self.fam("WifeF", "WifeM", "WifeBro", "Wife", "WifeSis")
        self.set_me(self.p["Me"])
        self.set_me(self.p["Akka"], user=BOB)

    def key(self, who, viewer="Me"):
        g = graph_mod.get()
        rel = relations.relationship(g, self.p[viewer], self.p[who])
        k = kin.build(g, self.p[viewer], rel["steps"])
        return k["key"] if k else None

    def term(self, who, lang="te", viewer="Me"):
        g = graph_mod.get()
        return kin.label_for(g, self.p[viewer], self.p[who], lang)


class Keys(KinTree):
    def test_kin_keys(self):
        expected = {
            "Dad": "F", "Mum": "M", "FF": "F.F", "FM": "F.M", "MF": "M.F", "MM": "M.M",
            "PeddaNanna": "F.B+", "Babai": "F.B-", "Atta": "F.Z-", "Peddamma": "F.B+.W", "Pinni": "F.B-.W",
            "Mamayya": "F.Z-.H", "MumBigSis": "M.Z+", "MumSmallSis": "M.Z-", "MumBro": "M.B-",
            "MumBigSisH": "M.Z+.H", "MumSmallSisH": "M.Z-.H", "MumBroW": "M.B-.W",
            "Akka": "Z+", "Tammudu": "B-", "AkkaH": "Z+.H", "TammuduW": "B-.W",
            "PNSon": "F.B+.S+", "PNDaughter": "F.B+.D-", "AttaSon": "F.Z-.S+", "AttaDaughter": "F.Z-.D-",
            "MBSSon": "M.Z+.S+", "MumBroSon": "M.B-.S-", "MumBroDaughter": "M.B-.D+",
            "Wife": "W", "WifeF": "W.F", "WifeM": "W.M", "WifeBro": "W.B+", "WifeSis": "W.Z-",
            "Son": "S", "Daughter": "D", "Kodalu": "S.W", "Alludu": "D.H", "GrandSon": "S.S",
            "AkkaSon": "Z+.S", "TammuduSon": "B-.S",
        }
        self.maxDiff = None
        got = {k: self.key(k) for k in expected}
        self.assertEqual(got, expected)
        self.assertIsNone(self.key("Me"))

    def test_age_unknown_keeps_no_mark(self):
        self.assertEqual(self.key("BabaiSon"), "F.B-.S")        # cousin with no birth date
        g = graph_mod.get()
        rel = relations.relationship(g, self.p["Me"], self.p["BabaiSon"])
        self.assertTrue(kin.build(g, self.p["Me"], rel["steps"])["ageUnknown"])

    def test_birth_order_when_dates_are_missing(self):
        # Babai has no birth date: he comes after Dad in the family, so he is younger
        g = graph_mod.get()
        self.assertEqual(kin.compare_age(g, self.p["Dad"], self.p["Babai"]), -1)
        self.assertEqual(kin.compare_age(g, self.p["Dad"], self.p["PeddaNanna"]), 1)
        # dates beat order: MumBro is listed after Mum but born earlier
        self.assertEqual(kin.compare_age(g, self.p["Mum"], self.p["MumBro"]), -1)   # 1964 > 1962: younger
        self.assertEqual(kin.compare_age(g, self.p["Me"], self.p["Wife"]), -1)      # 1991: years are enough
        self.assertIsNone(kin.compare_age(g, self.p["Me"], self.p["Son"]))          # no date, no shared family

    def test_female_speaker(self):
        self.assertEqual(self.key("Me", viewer="Akka"), "B-")
        self.assertEqual(self.key("TammuduSon", viewer="Akka"), "B-.S")
        self.assertEqual(self.term("TammuduSon", viewer="Akka"), "Menalludu")    # B.S@f
        self.assertEqual(self.term("TammuduSon"), "Koduku")                      # B.S@m for Me
        self.assertEqual(self.term("AkkaSon"), "Menalludu")                      # Z.S@m

    def test_telugu_terms(self):
        cases = {"Dad": "Nanna", "FM": "Nanamma", "MM": "Ammamma", "PeddaNanna": "Peddananna", "Babai": "Babai",
                 "Pinni": "Pinni", "Peddamma": "Peddamma", "Atta": "Atta", "Mamayya": "Mamayya",
                 "MumBigSis": "Peddamma", "MumSmallSis": "Pinni", "MumSmallSisH": "Babai", "MumBro": "Mamayya",
                 "MumBroW": "Atta", "Akka": "Akka", "Tammudu": "Tammudu", "AkkaH": "Bava", "TammuduW": "Maradalu",
                 "PNSon": "Anna", "PNDaughter": "Chelli", "MBSSon": "Anna", "AttaSon": "Bava",
                 "AttaDaughter": "Maradalu", "MumBroDaughter": "Vadina", "MumBroSon": "Bavamaridi",
                 "WifeF": "Mamagaru", "WifeM": "Attagaru", "WifeBro": "Bava", "WifeSis": "Maradalu",
                 "Son": "Koduku", "Kodalu": "Kodalu", "Alludu": "Alludu", "GrandSon": "Manavadu"}
        self.assertEqual({k: self.term(k) for k in cases}, cases)

    def test_hindi_terms(self):
        cases = {"PeddaNanna": "Tauji", "Babai": "Chacha", "Atta": "Bua", "Mamayya": "Phupha", "MumBro": "Mama",
                 "MumBigSis": "Mausi", "Akka": "Didi", "AkkaH": "Jijaji", "FF": "Dada", "MF": "Nana",
                 "WifeBro": "Saala", "WifeSis": "Saali", "Kodalu": "Bahu", "TammuduSon": "Bhatija",
                 "AttaSon": "Phuphera bhai", "BabaiSon": "Chachera bhai"}
        self.assertEqual({k: self.term(k, "hi") for k in cases}, cases)

    def test_fallbacks(self):
        self.assertEqual(self.term("BabaiSon"), "Anna / Tammudu")          # age unknown: neutral term
        self.assertEqual(self.term("PeddaNanna", "en"), "uncle")            # English
        self.assertEqual(self.term("GrandSon", "hi"), "Pota")
        g = graph_mod.get()
        # a key with no term of its own is built from parts, else the English label
        te, hi = kin.terms()["te"], kin.terms()["hi"]
        self.assertEqual(kin.lookup(te, "B+.W.B+", "male"), None)
        self.assertEqual(kin.compose(te, "B+.W.B+", "male", "te"), "Vadina Anna")
        self.assertEqual(kin.compose(te, "F.B-.S.W.Z-", "male", "te"), "Vadina Chelli")     # "Vadina / Maradalu" → Vadina
        self.assertEqual(kin.compose(hi, "B+.W.B+", "male", "hi"), "Bhabhi ka Bhaiya")
        self.assertEqual(kin.compose(hi, "B+.W.F", "male", "hi"), "Bhabhi ka Pitaji")          # "(Papa)" dropped
        self.assertEqual(kin.term_for(te, "S.S.S", "male", "te")[1]["term"], "Munimanavadu")
        self.assertIsNone(kin.compose(te, "E.X.E.X", None, "te"))
        self.assertEqual(kin.term_for(te, "E.X.E.X", None, "te"), (None, {}))
        self.assertEqual(kin.candidates("F.B+.S+", "male"),
                         ["F.B+.S+@m", "F.B+.S+", "F.B.S+@m", "F.B.S+", "F.B.S@m", "F.B.S"])
        self.assertTrue(g)

    def test_meanings(self):
        g = graph_mod.get()
        rel = relations.relationship(g, self.p["Me"], self.p["Babai"])
        self.assertEqual(kin.build(g, self.p["Me"], rel["steps"])["meaning"], "father's younger brother")
        rel = relations.relationship(g, self.p["Me"], self.p["PNSon"])
        self.assertEqual(kin.build(g, self.p["Me"], rel["steps"])["meaning"], "father's elder brother's son, older than you")
        self.assertEqual(kin.meaning_of_key("M.Z-"), "mother's younger sister")
        self.assertEqual(kin.meaning_of_key("Z.S@m"), "sister's son (said by a man)")
        self.assertEqual(kin.meaning_of_key("F.B.S+"), "father's brother's son, older than you")


class ApiTerms(KinTree):
    def test_detail_wider_family_in_telugu(self):
        """The person page names wider and two-marriage relatives in Telugu too."""
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        bf = self.person("AkkaHF", gender="male")
        self.family(bf, None, [self.p["AkkaH"]])
        want = {self.p["FF"]: "Thatha", self.p["WifeSis"]: "Maradalu",
                self.p["AkkaH"]: "Bava", bf: "Bava Nanna"}        # sister's husband's father: composed
        for pid, term in want.items():
            with self.subTest(pid=pid):
                r = self.detail(pid)["relationshipToMe"]
                self.assertEqual(r["label"], term)
        kid = self.person("Kid")                                    # no gender: both words
        self.family(self.p["Me"], self.p["Wife"], [kid])
        self.assertEqual(self.detail(kid)["relationshipToMe"]["label"], "Koduku / Kuthuru")
        sw_f = self.person("KodaluF", gender="male")                # son's wife's father
        self.family(sw_f, None, [self.p["Kodalu"]])
        self.assertEqual(self.detail(sw_f)["relationshipToMe"]["label"], "Viyyankudu")
        wbw = self.person("WifeBroW", gender="female")
        self.family(self.p["WifeBro"], wbw, [])
        r = self.detail(wbw)["relationshipToMe"]                  # two marriages away, still named
        self.assertEqual((r["english"], r["kinKey"], r["label"]), ("wife's brother's wife", "W.B+.W", "Vadina"))
        wbwf = self.person("WifeBroWDad", gender="male")
        self.family(wbwf, None, [wbw])
        r = self.detail(wbwf)["relationshipToMe"]                 # three marriages away: a word built from parts
        self.assertEqual((r["english"], r["kinKey"]), ("wife's brother's wife's father", "W.B+.W.F"))
        self.assertTrue(r["composed"])
        self.assertTrue(r["label"].startswith("Vadina "), r["label"])

    def test_detail_in_telugu(self):
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        d = self.detail(self.p["Babai"])
        r = d["relationshipToMe"]
        self.assertEqual((r["label"], r["term"], r["english"], r["meaning"], r["kinKey"]),
                         ("Babai", "Babai", "uncle", "father's younger brother", "F.B-"))
        self.assertEqual(r["note"], "also: Chinnanna")
        # cards on the page use the term too
        d = self.detail(self.p["Dad"])
        by = {s["id"]: s.get("relationship") for s in d["siblings"]}
        self.assertEqual(by[self.p["PeddaNanna"]], "Peddananna")
        # someone else still sees English
        d = self.detail(self.p["Babai"], user=ADMIN)
        self.assertIsNone(d["relationshipToMe"])            # admin has no "me"
        me = self.ok(self.get("/api/me"))
        self.assertEqual((me["kinLang"], me["kinLangEffective"], me["kinLangApp"]), ("te", "te", "en"))
        self.ok(self.put("/api/me/kin-lang", {"kinLang": None}))
        self.assertEqual(self.detail(self.p["Babai"])["relationshipToMe"]["label"], "uncle")
        self.assertEqual(self.put("/api/me/kin-lang", {"kinLang": "fr"}).status_code, 422)

    def test_app_setting_is_the_default(self):
        self.addCleanup(set_app_settings, relationship_language="en")
        r = self.put("/api/admin/settings", {"relationship_language": "hi"}, user=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.detail(self.p["Atta"])["relationshipToMe"]["label"], "Bua")
        self.assertEqual(self.detail(self.p["Me"], user=BOB)["relationshipToMe"]["label"], "Chhota bhai")
        self.assertEqual(self.put("/api/admin/settings", {"relationship_language": "xx"}, user=ADMIN).status_code, 422)

    def test_tree_upcoming_and_relationship_endpoint(self):
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        self.addCleanup(lambda: self.put("/api/me/kin-lang", {"kinLang": None}))
        t = self.ok(self.get(f"/api/tree?focus={self.p['Me']}"))
        self.assertEqual(t["ancestors"]["parents"]["a"]["p"]["relationship"], "Nanna")
        r = self.ok(self.get(f"/api/relationship?from={self.p['Me']}&to={self.p['Atta']}"))
        self.assertEqual((r["term"], r["english"]), ("Atta", "aunt"))
        whole = {p["id"]: p.get("relationship") for p in self.ok(self.get("/api/tree/all"))["people"]}
        self.assertEqual(whole[self.p["MumBro"]], "Mamayya")


class Search(KinTree):
    def names(self, q, user=None):
        return sorted(p["name"] for p in self.ok(self.get(f"/api/people?q={q}&page_size=500", user=user))["items"])

    def test_search_by_relationship_word(self):
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        self.addCleanup(lambda: self.put("/api/me/kin-lang", {"kinLang": None}))
        self.assertEqual(self.names("babai"), ["Babai", "BabaiSon", "MumSmallSisH"])   # MumSmallSisH is Babai to Me too
        self.assertEqual(self.names("Mamayya"), ["Mamayya", "MumBro"])
        self.assertIn("PNSon", self.names("anna"))
        self.assertEqual(self.names("zzz"), [])
        # English words work in English
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "en"}))
        self.assertIn("PNSon", self.names("cousins"))
        self.assertIn("Babai", self.names("uncle"))
        # a name search still finds names only
        self.assertEqual(self.names("Peddamma"), ["Peddamma"])
        # without "me" a relationship word is just text
        self.assertEqual(self.names("uncle", user=ADMIN), [])


class CustomTerms(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.me = self.person("Me", gender="male", born={"y": 1990})
        self.dad = self.person("Dad", gender="male", born={"y": 1960})
        self.babai = self.person("Babai", gender="male", born={"y": 1965})
        self.family(self.person("FF", gender="male"), None, [self.dad, self.babai])
        self.family(self.dad, None, [self.me])
        self.set_me(self.me)
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))

    def label(self, pid):
        return self.detail(pid)["relationshipToMe"]["label"]

    def test_override_reset_and_history(self):
        self.assertEqual(self.label(self.babai), "Babai")
        r = self.ok(self.put("/api/kin-terms/te/F.B-", {"term": "  Chinnanna ", "note": "our word"}, user=BOB))
        self.assertEqual(self.label(self.babai), "Chinnanna")
        b = sql("SELECT label, user_id FROM batches WHERE id = ?", (r["batchId"],))[0]
        self.assertEqual(b, {"label": "Telugu name for father's younger brother: Chinnanna", "user_id": BOB["id"]})
        items = {i["key"]: i for i in self.ok(self.get("/api/kin-terms?lang=te"))["items"]}
        self.assertEqual((items["F.B-"]["term"], items["F.B-"]["source"], items["F.B-"]["seedTerm"], items["F.B-"]["note"]),
                         ("Chinnanna", "custom", "Babai", "our word"))
        self.assertEqual(items["F.B-"]["meaning"], "father's younger brother")
        # undo from History brings the seed back
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual(self.label(self.babai), "Babai")
        self.ok(self.post(f"/api/history/{r['batchId']}/undo".replace(r["batchId"], sql(
            "SELECT undone_by FROM batches WHERE id = ?", (r["batchId"],))[0]["undone_by"])))
        self.assertEqual(self.label(self.babai), "Chinnanna")                 # redo
        self.ok(self.put("/api/kin-terms/te/F.B-", {"term": "Chinnayya"}))
        self.assertEqual(self.label(self.babai), "Chinnayya")
        self.ok(self.delete("/api/kin-terms/te/F.B-"))
        self.assertEqual(self.label(self.babai), "Babai")
        self.assertEqual(self.delete("/api/kin-terms/te/F.B-").status_code, 404)
        detail = self.ok(self.get(f"/api/history/{r['batchId']}"))
        self.assertEqual(detail["changes"][0]["entity"], "relationship name")

    def test_same_as_default_stores_nothing(self):
        self.assertEqual(self.ok(self.put("/api/kin-terms/te/F.B-", {"term": "Babai"})), {"ok": True, "batchId": None})
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM kin_terms")[0]["n"], 0)

    def test_new_key_and_validation(self):
        self.ok(self.put("/api/kin-terms/te/F.F.F.F", {"term": "Mutthatha Nanna"}))
        items = {i["key"]: i for i in self.ok(self.get("/api/kin-terms?lang=te"))["items"]}
        self.assertEqual((items["F.F.F.F"]["term"], items["F.F.F.F"]["seedTerm"]), ("Mutthatha Nanna", None))
        for key in ("Q", "F..B", "F.B++", "F.B@x", "f.b"):
            self.assertEqual(self.put(f"/api/kin-terms/te/{key}", {"term": "x"}).status_code, 422, key)
        self.assertEqual(self.put("/api/kin-terms/xx/F", {"term": "x"}).status_code, 404)
        self.assertEqual(self.put("/api/kin-terms/te/F", {"term": ""}).status_code, 422)
        self.assertEqual(self.put("/api/kin-terms/te/F", {"term": "x" * 61}).status_code, 422)
        self.assertEqual(self.get("/api/kin-terms?lang=en").status_code, 404)

    def test_disabled_user_cannot_edit(self):
        self.get("/api/me", user=BOB)
        self.ok(self.patch(f"/api/users/{BOB['id']}", {"disabled": True}, user=ADMIN))
        self.assertEqual(self.put("/api/kin-terms/te/F", {"term": "Appa"}, user=BOB).status_code, 403)


if __name__ == "__main__":
    unittest.main()
