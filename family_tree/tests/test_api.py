"""People & events CRUD, the tree endpoint, the people list, and request validation."""
from base import ALICE, ApiTestCase, headers, sql

import unittest


from app import config
from app.main import app
from common_tests.ingress import ingress_client


class People(ApiTestCase):
    def test_create_and_detail(self):
        body = {"given_names": "  Ann   Marie ", "surname": "Smith", "birth_surname": "Adams", "nickname": "Annie",
                "other_names": [{"type": "married", "name": "Ann Jones"}], "gender": "female",
                "biography": "  Lived in York.\nLiked cats.  ",
                "birth": {"date": {"d": 12, "m": 3, "y": 1950}, "place": "York"}}
        r = self.ok(self.post("/api/people", body), 201)
        p = r["person"]
        self.assertEqual((p["givenNames"], p["surname"], p["birthSurname"], p["nickname"]),
                         ("Ann Marie", "Smith", "Adams", "Annie"))
        self.assertEqual(p["name"], "Ann Marie Smith")
        self.assertEqual(p["otherNames"], [{"type": "married", "name": "Ann Jones"}])
        self.assertEqual(p["biography"], "Lived in York.\nLiked cats.")
        self.assertEqual(p["birth"], {"date": {"qual": "exact", "d": 12, "m": 3, "y": 1950},
                                      "dateDisplay": "12 March 1950", "place": "York", "time": None})
        self.assertTrue(p["living"])
        self.assertEqual(p["age"], config.today().year - 1950 - (1 if (config.today().month, config.today().day) < (3, 12) else 0))
        self.assertEqual(p["years"], "b. 1950")
        self.assertEqual(self.ok(self.get("/api/history"))["items"][0]["label"], "Added Ann Marie Smith")
        self.assertEqual(self.get("/api/people/nope").status_code, 404)

    def test_nickname_only_is_enough_and_no_name_rejected(self):
        p = self.ok(self.post("/api/people", {"nickname": "Nana"}), 201)["person"]
        self.assertEqual(p["name"], "Nana")
        self.assertEqual(self.post("/api/people", {"gender": "male"}).status_code, 422)
        self.assertEqual(self.post("/api/people", {"given_names": "   "}).status_code, 422)
        r = self.patch(f"/api/people/{p['id']}", {"nickname": None})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.detail(p["id"])["nickname"], "Nana")

    def test_birth_and_death_via_patch(self):
        pid = self.person("Ann", born={"d": 1, "m": 2, "y": 1950})
        d = self.ok(self.patch(f"/api/people/{pid}", {"death": {"date": {"y": 2020}, "place": "Leeds"}}))["person"]
        self.assertFalse(d["living"])
        self.assertEqual(d["death"]["dateDisplay"], "2020")
        self.assertEqual(d["years"], "1950–2020")
        self.assertEqual(d["age"], 70)
        # edit the birth in place (same event row)
        before = sql("SELECT id FROM events WHERE person_id = ? AND type = 'birth'", (pid,))[0]["id"]
        d = self.ok(self.patch(f"/api/people/{pid}", {"birth": {"date": {"qual": "about", "y": 1949}}}))["person"]
        self.assertEqual(d["birth"]["dateDisplay"], "about 1949")
        self.assertIsNone(d["birth"]["place"])
        self.assertEqual(sql("SELECT id FROM events WHERE person_id = ? AND type = 'birth'", (pid,))[0]["id"], before)
        # remove the death with null → living again
        d = self.ok(self.patch(f"/api/people/{pid}", {"death": None}))["person"]
        self.assertIsNone(d["death"])
        self.assertTrue(d["living"])
        # remove the birth with null
        d = self.ok(self.patch(f"/api/people/{pid}", {"birth": None}))["person"]
        self.assertIsNone(d["birth"])
        self.assertIsNone(d["age"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM events")[0]["n"], 0)
        # a place alone is kept as an undated event
        d = self.ok(self.patch(f"/api/people/{pid}", {"birth": {"place": "Hull"}}))["person"]
        self.assertEqual(d["birth"], {"date": None, "dateDisplay": None, "place": "Hull", "time": None})
        # omitting birth leaves it alone
        d = self.ok(self.patch(f"/api/people/{pid}", {"surname": "X"}))["person"]
        self.assertEqual(d["birth"]["place"], "Hull")

    def test_yearless_birthday(self):
        pid = self.person("Ann", born={"d": 29, "m": 2})
        d = self.detail(pid)
        self.assertEqual(d["birth"]["dateDisplay"], "29 February")
        self.assertIsNone(d["age"])
        self.assertTrue(d["living"])
        self.assertEqual(d["years"], "")
        row = sql("SELECT * FROM events WHERE person_id = ?", (pid,))[0]
        self.assertEqual((row["date_text"], row["date_y"], row["date_m"], row["date_d"], row["sort_key"]),
                         ("29 FEB", None, 2, 29, None))

    def test_living_rules_through_api(self):
        old = self.person("Old", born=config.today().year - 120)
        flagged = self.person("Flag", deceased=True)
        dead = self.person("Dead", died={"d": 1, "m": 1})                  # year-less death still counts
        young = self.person("Young", born=2000)
        self.assertFalse(self.detail(old)["living"])
        self.assertIn("Born more than 110 years ago with no death recorded — shown as deceased.", self.detail(old)["warnings"])
        self.assertFalse(self.detail(flagged)["living"])
        self.assertFalse(self.detail(dead)["living"])
        self.assertTrue(self.detail(young)["living"])

    def test_warnings(self):
        kid = self.person("Kid", born=1950)
        par = self.person("Par", born=1945, died=1940)
        self.family(par, None, [kid])
        w = self.detail(par)["warnings"]
        self.assertIn("Death is recorded before birth.", w)
        self.assertIn("Par would have been under 12 at this birth.", self.detail(kid)["warnings"])

    def test_events_sorted_with_yearless_last(self):
        pid = self.person("Ann")
        for title, date in (("March", {"d": 12, "m": 3}), ("Y1990", {"y": 1990}), ("January", {"d": 5, "m": 1}),
                            ("Y1980", {"y": 1980})):
            self.ok(self.post("/api/events", {"personId": pid, "type": "occupation", "title": title, "date": date}), 201)
        self.assertEqual([e["title"] for e in self.detail(pid)["events"]], ["Y1980", "Y1990", "January", "March"])

    def test_timeline(self):
        me = self.person("Me", born=1950, died=2020)
        dad = self.person("Dad", gender="male", born=1920, died=1990)
        self.family(dad, None, [me])
        kid = self.person("Kid", born=1980)
        self.family(me, self.person("Wife", gender="female", born=1952, died=2030), [kid])
        self.person("Stranger", born=1960)
        self.ok(self.post("/api/events", {"personId": me, "type": "occupation", "title": "Baker", "date": {"y": 1970}}), 201)
        t = self.ok(self.get(f"/api/people/{me}/timeline"))
        got = [(i["type"], i.get("name") or i.get("title"), i.get("relation")) for i in t["items"]]
        self.assertEqual(got, [("birth", None, None), ("occupation", "Baker", None), ("relative-birth", "Kid", "child"),
                               ("relative-death", "Dad", "father"), ("death", None, None)])
        self.assertEqual(self.get("/api/people/nope/timeline").status_code, 404)

    def test_bad_person_fields(self):
        cases = [
            {"given_names": "A", "gender": "robot"},
            {"given_names": "A", "other_names": [{"type": "alias", "name": "B"}]},
            {"given_names": "A", "other_names": [{"type": "aka", "name": ""}]},
            {"given_names": "A" * 101},
            {"given_names": "A", "biography": "x" * 20001},
            {"given_names": "A", "other_names": [{"type": "aka", "name": "n"}] * 11},
        ]
        for body in cases:
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.post("/api/people", body).status_code, 422)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)


class Events(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.person("Ann")
        self.fid = self.family(self.pid, self.person("Ben"))

    def test_create_edit_delete(self):
        r = self.ok(self.post("/api/events", {"personId": self.pid, "type": "occupation", "title": " Nurse ",
                                              "date": {"qual": "between", "y": 1970, "y2": 1980},
                                              "place": "York", "description": "Night shifts"}), 201)
        ev = r["event"]
        self.assertIn("batchId", r)
        self.assertEqual((ev["title"], ev["dateText"], ev["dateDisplay"], ev["place"], ev["sortKey"]),
                         ("Nurse", "BET 1970 AND 1980", "between 1970 and 1980", "York", "1970-00-00"))
        ev = self.ok(self.patch(f"/api/events/{ev['id']}", {"date": {"d": 12, "m": 3}, "type": "residence"}))["event"]
        self.assertEqual((ev["type"], ev["dateDisplay"], ev["sortKey"], ev["title"]),
                         ("residence", "12 March", None, "Nurse"))
        ev = self.ok(self.patch(f"/api/events/{ev['id']}", {"date": None}))["event"]
        self.assertIsNone(ev["date"])
        self.assertEqual(ev["place"], "York")
        self.ok(self.delete(f"/api/events/{ev['id']}"))
        self.assertEqual(self.detail(self.pid)["events"], [])
        self.assertEqual(self.delete(f"/api/events/{ev['id']}").status_code, 404)
        self.assertEqual(self.patch(f"/api/events/{ev['id']}", {"place": "x"}).status_code, 404)

    def test_one_birth_death_marriage(self):
        self.ok(self.post("/api/events", {"personId": self.pid, "type": "birth", "date": {"y": 1950}}), 201)
        r = self.post("/api/events", {"personId": self.pid, "type": "birth", "date": {"y": 1951}})
        self.assertEqual(r.status_code, 409)
        self.ok(self.post("/api/events", {"personId": self.pid, "type": "death"}), 201)
        self.assertEqual(self.post("/api/events", {"personId": self.pid, "type": "death"}).status_code, 409)
        self.assertFalse(self.detail(self.pid)["living"])                 # an undated death event
        self.ok(self.post("/api/events", {"familyId": self.fid, "type": "marriage", "date": {"y": 1970}}), 201)
        self.assertEqual(self.post("/api/events", {"familyId": self.fid, "type": "marriage"}).status_code, 409)
        # birth set through the event route shows on the person
        self.assertEqual(self.detail(self.pid)["birth"]["dateDisplay"], "1950")

    def test_custom_requires_title(self):
        self.assertEqual(self.post("/api/events", {"personId": self.pid, "type": "custom"}).status_code, 422)
        self.assertEqual(self.post("/api/events", {"personId": self.pid, "type": "custom", "title": "  "}).status_code, 422)
        ev = self.ok(self.post("/api/events", {"personId": self.pid, "type": "custom", "title": "Knighted"}), 201)["event"]
        self.assertEqual(self.patch(f"/api/events/{ev['id']}", {"title": None}).status_code, 422)
        other = self.ok(self.post("/api/events", {"personId": self.pid, "type": "education", "title": None}), 201)["event"]
        self.assertEqual(self.patch(f"/api/events/{other['id']}", {"type": "custom"}).status_code, 422)
        self.ok(self.patch(f"/api/events/{other['id']}", {"type": "custom", "title": "School"}))

    def test_person_vs_family_types(self):
        bad = [
            {"personId": self.pid, "type": "marriage"},
            {"personId": self.pid, "type": "engagement"},
            {"familyId": self.fid, "type": "occupation"},
            {"familyId": self.fid, "type": "birth"},
            {"personId": self.pid, "type": "party"},
            {"personId": self.pid, "familyId": self.fid, "type": "residence"},
            {"type": "residence"},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.post("/api/events", body).status_code, 422)
        for t in ("residence", "custom"):
            self.ok(self.post("/api/events", {"familyId": self.fid, "type": t, "title": "x"}), 201)
            self.ok(self.post("/api/events", {"personId": self.pid, "type": t, "title": "x"}), 201)
        self.ok(self.post("/api/events", {"familyId": self.fid, "type": "divorce"}), 201)
        self.assertEqual(self.post("/api/events", {"personId": "nope", "type": "residence"}).status_code, 404)
        self.assertEqual(self.post("/api/events", {"familyId": "nope", "type": "residence"}).status_code, 404)

    def test_birth_type_cannot_change(self):
        ev = self.ok(self.post("/api/events", {"personId": self.pid, "type": "birth", "date": {"y": 1950}}), 201)["event"]
        self.assertEqual(self.patch(f"/api/events/{ev['id']}", {"type": "residence"}).status_code, 422)
        res = self.ok(self.post("/api/events", {"personId": self.pid, "type": "residence"}), 201)["event"]
        self.assertEqual(self.patch(f"/api/events/{res['id']}", {"type": "death"}).status_code, 422)
        self.assertEqual(self.patch(f"/api/events/{res['id']}", {"type": "marriage"}).status_code, 422)

    def test_event_limit(self):
        for i in range(50):
            self.ok(self.post("/api/events", {"personId": self.pid, "type": "residence", "place": f"P{i}"}), 201)
        self.assertEqual(self.post("/api/events", {"personId": self.pid, "type": "residence"}).status_code, 422)

    def test_family_event_touches_partners_history(self):
        self.ok(self.post("/api/events", {"familyId": self.fid, "type": "engagement", "date": {"y": 1969}}), 201)
        h = self.ok(self.get(f"/api/history?personId={self.pid}"))["items"][0]
        self.assertEqual(h["label"], "Added engagement")


class Tree(ApiTestCase):
    def test_empty_tree(self):
        self.assertEqual(self.ok(self.get("/api/tree")), {"focus": None, "empty": True, "meId": None})

    def test_unknown_focus_404(self):
        self.person("Ann")
        self.assertEqual(self.get("/api/tree?focus=nope").status_code, 404)
        self.assertEqual(self.get("/api/tree?up=11").status_code, 422)
        self.assertEqual(self.get("/api/tree?down=-1").status_code, 422)

    def build_line(self):
        names = ["GGP", "GP", "P", "Me", "Kid", "GKid"]
        ids = {n: self.person(n) for n in names}
        for up, down in zip(names, names[1:]):
            self.family(ids[up], None, [ids[down]])
        return ids

    def test_default_focus_is_me_and_depth_limits(self):
        ids = self.build_line()
        self.set_me(ids["Me"])
        t = self.ok(self.get("/api/tree"))
        self.assertEqual((t["focus"], t["meId"], t["people"]), (ids["Me"], ids["Me"], 6))
        anc = t["ancestors"]
        self.assertEqual(anc["p"]["id"], ids["Me"])
        chain = []
        node = anc
        while node and node["parents"]:
            node = node["parents"]["a"]
            chain.append(node["p"]["name"])
            self.assertIsNone(node["parents"]["b"] if node["parents"] else None)
        self.assertEqual(chain, ["P", "GP", "GGP"])
        # up=1: parent shown, grandparents cut off (truncated)
        t = self.ok(self.get("/api/tree?up=1&down=1"))
        par = t["ancestors"]["parents"]["a"]
        self.assertEqual(par["p"]["name"], "P")
        self.assertIsNone(par["parents"])
        self.assertTrue(par["truncated"])
        kid = t["descendants"]["families"][0]["children"][0]
        self.assertEqual(kid["p"]["name"], "Kid")
        self.assertEqual(kid["families"][0]["children"], [])
        self.assertEqual(kid["families"][0]["childCount"], 1)
        self.assertTrue(kid["truncated"])
        # up=0 / down=0
        t = self.ok(self.get("/api/tree?up=0&down=0"))
        self.assertIsNone(t["ancestors"]["parents"])
        self.assertTrue(t["ancestors"]["truncated"])
        self.assertEqual(t["descendants"]["families"][0]["children"], [])
        self.assertTrue(t["descendants"]["truncated"])
        # explicit focus beats "me"; relationship labels are relative to me
        t = self.ok(self.get(f"/api/tree?focus={ids['GP']}&up=0&down=1"))
        self.assertEqual(t["focus"], ids["GP"])
        self.assertEqual(t["ancestors"]["p"]["relationship"], "grandparent")
        self.assertEqual(t["descendants"]["families"][0]["children"][0]["p"]["relationship"], "parent")

    def test_default_focus_without_me(self):
        ids = self.build_line()
        t = self.ok(self.get("/api/tree"))
        self.assertIsNone(t["meId"])
        self.assertIn(t["focus"], ids.values())
        self.assertNotIn("relationship", t["ancestors"]["p"])

    def test_siblings_older_and_younger(self):
        mum = self.person("Mum", gender="female")
        dad = self.person("Dad", gender="male")
        kids = {n: self.person(n, born=y) for n, y in (("Eldest", 1980), ("Older", 1982), ("Me", 1985),
                                                          ("Younger", 1988), ("Youngest", 1990))}
        self.family(dad, mum, [kids[n] for n in ("Younger", "Me", "Youngest", "Eldest", "Older")])
        half = self.person("Half", born=1995)
        self.family(dad, self.person("Other"), [half])
        t = self.ok(self.get(f"/api/tree?focus={kids['Me']}"))
        self.assertEqual([(s["name"], s["older"], s["full"]) for s in t["siblings"]],
                         [("Eldest", True, True), ("Older", True, True), ("Younger", False, True),
                          ("Youngest", False, True), ("Half", False, False)])
        t = self.ok(self.get(f"/api/tree?focus={kids['Me']}&siblings=false"))
        self.assertEqual(t["siblings"], [])

    def test_several_parent_families_reported(self):
        kid = self.person("Kid")
        self.family(self.person("Birth"), None, [kid])
        self.family(self.person("Adopt"), None, [(kid, "adopted")])
        t = self.ok(self.get(f"/api/tree?focus={kid}"))
        self.assertEqual(t["ancestors"]["moreParents"], 1)
        self.assertEqual(t["ancestors"]["parents"]["relation"], "birth")
        self.assertEqual(t["ancestors"]["parents"]["a"]["p"]["name"], "Birth")


class PeopleList(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.ann = self.person("Ann", "Smith", born=1950, nickname="Nan",
                               other_names=[{"type": "religious", "name": "Sister Agnes"}])
        self.bob = self.person("Bob", "Jones", born=1900)                 # > 110 years → deceased
        self.cat = self.person("Catherine", "Brown", born={"d": 1, "m": 5}, birth_surname="SMITH")
        self.dan = self.person("Dan", born=1980, died=2010)
        self.eve = self.person("Eve", "smith", born=1990)

    def items(self, query=""):
        return self.ok(self.get("/api/people" + query))

    def names(self, query=""):
        return [i["name"] for i in self.items(query)["items"]]

    def test_search(self):
        self.assertEqual(self.names("?q=ann"), ["Ann Smith"])
        self.assertEqual(self.names("?q=nan"), ["Ann Smith"])                 # nickname
        self.assertEqual(self.names("?q=agnes"), ["Ann Smith"])               # other names
        self.assertEqual(self.names("?q=sister%20ann"), ["Ann Smith"])        # all words must match
        self.assertEqual(self.names("?q=ann%20jones"), [])
        self.assertEqual(sorted(self.names("?q=smith")), ["Ann Smith", "Catherine Brown", "Eve smith"])
        self.assertEqual(self.names("?q=CATH"), ["Catherine Brown"])
        self.assertEqual(self.names("?q=zzz"), [])

    def test_filters(self):
        self.assertEqual(sorted(self.names("?living=living")), ["Ann Smith", "Catherine Brown", "Eve smith"])
        self.assertEqual(sorted(self.names("?living=deceased")), ["Bob Jones", "Dan"])
        self.assertEqual(sorted(self.names("?surname=Smith")), ["Ann Smith", "Catherine Brown", "Eve smith"])
        self.assertEqual(self.names("?surname=jones"), ["Bob Jones"])
        self.assertEqual(self.names("?has_photo=true"), [])
        self.assertEqual(len(self.names("?has_photo=false")), 5)
        item = next(i for i in self.items()["items"] if i["id"] == self.cat)
        self.assertEqual((item["birthDisplay"], item["birthSurname"], item["years"]), ("1 May", "SMITH", ""))
        item = next(i for i in self.items()["items"] if i["id"] == self.dan)
        self.assertEqual((item["living"], item["years"], item["deathDisplay"]), (False, "1980–2010", "2010"))

    def test_sorts(self):
        # by surname, then given names; no surname last
        self.assertEqual(self.names(), ["Catherine Brown", "Bob Jones", "Ann Smith", "Eve smith", "Dan"])
        # by birth, unknown/year-less last
        self.assertEqual(self.names("?sort=birth"), ["Bob Jones", "Ann Smith", "Dan", "Eve smith", "Catherine Brown"])
        # recently changed first
        stamps = {self.ann: "2020-01-01T00:00:00Z", self.bob: "2021-01-01T00:00:00Z", self.cat: "2019-01-01T00:00:00Z",
                  self.dan: "2022-01-01T00:00:00Z", self.eve: "2018-01-01T00:00:00Z"}
        for pid, ts in stamps.items():
            sql("UPDATE people SET updated_at = ? WHERE id = ?", (ts, pid))
        self.assertEqual(self.names("?sort=changed"), ["Dan", "Bob Jones", "Ann Smith", "Catherine Brown", "Eve smith"])

    def test_pagination(self):
        p1 = self.items("?page_size=2")
        p3 = self.items("?page_size=2&page=3")
        self.assertEqual((p1["total"], len(p1["items"]), p1["page"], p1["pageSize"]), (5, 2, 1, 2))
        self.assertEqual([i["name"] for i in p3["items"]], ["Dan"])
        self.assertEqual(self.items("?page=9")["items"], [])
        self.assertEqual(self.get("/api/people?page_size=501").status_code, 422)
        self.assertEqual(self.get("/api/people?page=0").status_code, 422)

    def test_surnames(self):
        s = self.ok(self.get("/api/surnames"))
        self.assertEqual([(x["surname"].lower(), x["count"]) for x in s], [("brown", 1), ("jones", 1), ("smith", 2)])

    def test_deleted_hidden(self):
        self.ok(self.delete(f"/api/people/{self.ann}"))
        self.assertNotIn("Ann Smith", self.names())
        self.assertEqual(self.names("?q=agnes"), [])


class Validation(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.raw = ingress_client(app, raise_server_exceptions=False)
        self.pid = self.person("Ann")
        self.fid = self.family(self.pid, None)

    def send(self, method, path, text):
        h = dict(headers(ALICE))
        h["Content-Type"] = "application/json"
        return self.raw.request(method, path, content=text.encode(), headers=h)

    def test_nan_infinity_and_huge_numbers_in_dates(self):
        values = ["NaN", "Infinity", "-Infinity", "1e400", "1e308", "100000000000000000000000000000",
                  "-100000000000000000000000000000", "12.5"]
        targets = [
            ("POST", "/api/people", '{{"given_names": "X", "birth": {{"date": {{"y": {v}}}}}}}'),
            ("POST", "/api/people", '{{"given_names": "X", "birth": {{"date": {{"y": 2000, "m": 1, "d": {v}}}}}}}'),
            ("POST", "/api/people", '{{"given_names": "X", "birth": {{"date": {{"y": 2000, "m": {v}}}}}}}'),
            ("POST", "/api/people", '{{"given_names": "X", "birth": {{"date": {{"qual": "between", "y": 1900, "y2": {v}}}}}}}'),
            ("PATCH", f"/api/people/{self.pid}", '{{"death": {{"date": {{"y": {v}}}}}}}'),
            ("POST", "/api/events", '{{"personId": "' + self.pid + '", "type": "residence", "date": {{"y": {v}}}}}'),
            ("POST", "/api/families/quick", '{{"partners": [{{"existingId": "' + self.pid + '"}}], "marriage": {{"date": {{"y": {v}}}}}}}'),
            ("PATCH", f"/api/families/{self.fid}", '{{"marriage": {{"date": {{"y": 1950, "m": 2, "d": {v}}}}}}}'),
        ]
        for method, path, tmpl in targets:
            for v in values:
                with self.subTest(path=path, body=tmpl.format(v=v)):
                    r = self.send(method, path, tmpl.format(v=v))
                    self.assertEqual(r.status_code, 422, r.text)
                    self.assertIsInstance(r.json()["detail"], str)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM events")[0]["n"], 0)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 1)

    def test_nan_in_other_numbers(self):
        kid = self.person("Kid")
        self.ok(self.post(f"/api/families/{self.fid}/children", {"existingId": kid}), 201)
        for v in ("NaN", "Infinity", "1e400", "100000000000000000000000"):
            r = self.send("PATCH", f"/api/families/{self.fid}/children/{kid}", '{"position": %s}' % v)
            self.assertEqual(r.status_code, 422, r.text)

    def test_huge_page_numbers(self):
        for path in ("/api/people", "/api/history"):
            with self.subTest(path=path):
                r = self.raw.get(path + "?page=100000000000000000000", headers=headers(ALICE))
                self.assertIn(r.status_code, (200, 422), r.text)

    def test_unknown_fields_rejected(self):
        cases = [
            ("POST", "/api/people", {"given_names": "X", "is_admin": True}),
            ("POST", "/api/people", {"given_names": "X", "birth": {"date": {"y": 1950, "hour": 3}}}),
            ("POST", "/api/people", {"given_names": "X", "birth": {"when": "1950"}}),
            ("PATCH", f"/api/people/{self.pid}", {"deleted_at": None}),
            ("POST", "/api/events", {"personId": self.pid, "type": "residence", "sort_key": "0000"}),
            ("PATCH", f"/api/families/{self.fid}", {"partner1_id": "x"}),
            ("POST", "/api/families", {"partner1Id": self.pid, "children": []}),
            ("POST", "/api/families/quick", {"partners": [{"existingId": self.pid, "role": "mum"}]}),
            ("POST", f"/api/people/{self.pid}/add-child", {"person": {"given_names": "K"}, "position": 0}),
            ("PUT", "/api/me/person", {"personId": self.pid, "userId": "u-bob"}),
            ("PUT", f"/api/families/{self.fid}/partners/2", {"personId": None, "force": True}),
        ]
        for method, path, body in cases:
            with self.subTest(path=path, body=body):
                r = self.req(method, path, json=body)
                self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 1)

    def test_bad_json_and_bad_date_messages(self):
        r = self.send("POST", "/api/people", "{not json")
        self.assertEqual(r.status_code, 422)
        r = self.post("/api/people", {"given_names": "X", "birth": {"date": {"d": 12}}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["detail"], "A day needs a month too.")
        r = self.post("/api/people", {"given_names": "X", "birth": {"date": {"m": 3}}})
        self.assertEqual(r.json()["detail"], "A month on its own isn't a date — add a day or a year.")
        r = self.post("/api/people", {"given_names": "X", "birth": {"date": {"d": 29, "m": 2, "y": 1900}}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.post("/api/people", {"given_names": "X", "birth": {"date": {"d": 29, "m": 2, "y": 2000}}}).status_code, 201)
        r = self.post("/api/people", {"given_names": "X", "birth": {"date": {"qual": "between", "y": 1910, "y2": 1900}}})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
