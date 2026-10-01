"""Export (§13.6): the filtered projection (scopes, branch cuts,
placeholders, the living rule, detail filters, photo privacy), the export
routes (preview, jobs, downloads, presets, last-used options) and the
generated website (escaping, file:// friendly, nothing private inside)."""
from base import ADMIN, ALICE, BOB, ApiTestCase, image_bytes, sql

import io
import os
import re
import time
import unittest
import zipfile

from app import db
from app.export_view import ExportOptions, build, included_ids
from app.routers import export as export_router


class ExportCase(ApiTestCase):
    """Three generations on dad's side, mum's parents, my wife's family and my
    sister's husband's family:

        gpa+gma ─ dad, uncle(+aunt ─ cousin)
        mgpa+mgma ─ mum
        dad+mum ─ me, sis
        ilp1+ilp2 ─ wife, wbro
        me+wife ─ kid
        hp1+hp2 ─ hub;  sis+hub ─ niece
    """

    def setUp(self):
        super().setUp()
        P = self.person
        self.gpa = P("Gopal", "Sharma", "male", born=1920, died=1990)
        self.gma = P("Gita", "Sharma", "female", born=1925, died=1999)
        self.dad = P("Venkat", "Sharma", "male", born=1950)
        self.uncle = P("Ravi", "Sharma", "male", born=1948)
        self.aunt = P("Meena", "Sharma", "female", born=1952)
        self.cousin = P("Kiran", "Sharma", "male", born=1978)
        self.mgpa = P("Subba", "Rao", "male", born=1922, died=1995)
        self.mgma = P("Kamala", "Rao", "female", born=1926, died=2001)
        self.mum = P("Lakshmi", "Sharma", "female", born=1955, birth_surname="Rao")
        self.me = P("Arjun", "Sharma", "male", born=1980)
        self.sis = P("Priya", "Sharma", "female", born=1983)
        self.ilp1 = P("Hari", "Reddy", "male", born=1950)
        self.ilp2 = P("Sarala", "Reddy", "female", born=1954)
        self.wife = P("Anjali", "Sharma", "female", born=1984)
        self.wbro = P("Vikram", "Reddy", "male", born=1987)
        self.kid = P("Aarav", "Sharma", "male", born=2012)
        self.hp1 = P("Mohan", "Varma", "male", born=1945)
        self.hp2 = P("Uma", "Varma", "female", born=1950)
        self.hub = P("Rahul", "Varma", "male", born=1979)
        self.niece = P("Maya", "Varma", "female", born=2015)
        self.family(self.gpa, self.gma, children=[self.uncle, self.dad])
        self.family(self.uncle, self.aunt, children=[self.cousin])
        self.family(self.mgpa, self.mgma, children=[self.mum])
        self.family(self.dad, self.mum, children=[self.me, self.sis])
        self.family(self.ilp1, self.ilp2, children=[self.wife, self.wbro])
        self.family(self.me, self.wife, children=[self.kid])
        self.family(self.hp1, self.hp2, children=[self.hub])
        self.family(self.sis, self.hub, children=[self.niece])
        self.set_me(self.me)
        self.everyone = {self.gpa, self.gma, self.dad, self.uncle, self.aunt, self.cousin, self.mgpa, self.mgma,
                         self.mum, self.me, self.sis, self.ilp1, self.ilp2, self.wife, self.wbro, self.kid,
                         self.hp1, self.hp2, self.hub, self.niece}

    # ---- helpers ----
    def view(self, **opts):
        o = ExportOptions.model_validate(opts)
        with db.get_conn() as conn:
            return build(conn, o, self.me)

    def ids(self, **opts):
        return set(included_ids(self.view(**opts))["included"])

    def preview(self, options=None, user=None, status=200):
        return self.ok(self.post("/api/export/preview", {"format": "site", "options": options or {}}, user=user), status)

    def run_export(self, options=None, user=None):
        r = self.ok(self.post("/api/export", {"format": "site", "options": options or {}}, user=user), 202)
        jid = r["jobId"]
        for _ in range(200):
            job = self.ok(self.get(f"/api/export/{jid}", user=user))
            if job["status"] != "running":
                return job
            time.sleep
        self.fail("export didn't finish")

    def site(self, options=None, user=None):
        job = self.run_export(options, user)
        self.assertEqual(job["status"], "done", job)
        resp = self.get(f"/api/export/{job['id']}/file", user=user)
        self.assertEqual(resp.status_code, 200)
        return zipfile.ZipFile(io.BytesIO(resp.content))

    # Random ids (uuid4 hex, and the 8-character suffix of page names) are masked:
    # an id like "1a85dbae332a4cb1945…" once made `assertNotIn("1945", text)` fail
    # at random. The tests look for names, dates and places, never for ids.
    _IDS = re.compile(r"[0-9a-f]{32}|(?<=-)[0-9a-f]{8}(?=\.html)")

    def all_text(self, z):
        return self._IDS.sub("<id>", "\n".join(z.read(n).decode("utf-8") for n in z.namelist()
                                                if n.endswith((".html", ".js", ".css"))))

    def html_text(self, z):
        return self._IDS.sub("<id>", "\n".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".html")))


class ScopeTests(ExportCase):
    def test_whole(self):
        self.assertEqual(self.ids(), self.everyone)

    def test_ancestors_and_descendants(self):
        up = self.ids(scope={"type": "ancestors", "personId": self.me, "generations": 2, "partners": False})
        self.assertEqual(up, {self.me, self.dad, self.mum, self.gpa, self.gma, self.mgpa, self.mgma})
        one = self.ids(scope={"type": "ancestors", "personId": self.me, "generations": 1, "partners": False})
        self.assertEqual(one, {self.me, self.dad, self.mum})
        down = self.ids(scope={"type": "descendants", "personId": self.gpa, "generations": 3, "partners": False})
        self.assertEqual(down, {self.gpa, self.uncle, self.dad, self.cousin, self.me, self.sis, self.kid, self.niece})
        with_partners = self.ids(scope={"type": "descendants", "personId": self.dad, "generations": 1, "partners": True})
        self.assertEqual(with_partners, {self.dad, self.mum, self.me, self.sis, self.wife, self.hub})

    def test_both_doesnt_take_siblings(self):
        both = self.ids(scope={"type": "both", "personId": self.dad, "generations": 1, "partners": False})
        self.assertEqual(both, {self.dad, self.gpa, self.gma, self.me, self.sis})
        self.assertNotIn(self.uncle, both)

    def test_selected(self):
        self.assertEqual(self.ids(scope={"type": "selected", "ids": [self.me, self.kid]}), {self.me, self.kid})
        v = self.view(scope={"type": "selected", "ids": [self.me, "nobody-here"]})
        self.assertTrue(any("isn't in the tree" in w for w in v.warnings))

    def test_missing_start_warns(self):
        v = self.view(scope={"type": "branch", "start": []})
        self.assertFalse(v.people)
        self.assertTrue(any("start" in w for w in v.warnings))


class BranchTests(ExportCase):
    def branch(self, **sc):
        sc.setdefault("type", "branch")
        return self.ids(scope=sc)

    def test_blood_relatives_married_in_partners_not_expanded(self):
        got = self.branch(start=[self.dad])
        # dad's parents, brother and nephew, his children and grandchildren
        for pid in (self.dad, self.gpa, self.gma, self.uncle, self.cousin, self.me, self.sis, self.kid, self.niece):
            self.assertIn(pid, got)
        # partners who married in are there…
        for pid in (self.mum, self.aunt, self.wife, self.hub):
            self.assertIn(pid, got)
        # …but not their families — including mum's side, which isn't dad's family
        for pid in (self.mgpa, self.mgma, self.ilp1, self.ilp2, self.wbro, self.hp1, self.hp2):
            self.assertNotIn(pid, got)

    def test_follow_partners_reaches_everyone(self):
        self.assertEqual(self.branch(start=[self.dad], partnerRule="follow"), self.everyone)

    def test_stop_at_wife_with_follow(self):
        """The case from the request: start from my father, stop at my wife."""
        got = self.branch(start=[self.dad], partnerRule="follow", stops=[self.wife])
        self.assertIn(self.wife, got)
        self.assertIn(self.kid, got)
        for pid in (self.ilp1, self.ilp2, self.wbro):
            self.assertNotIn(pid, got)
        # the other lines are untouched
        for pid in (self.mgpa, self.hp1, self.hub):
            self.assertIn(pid, got)

    def test_multiple_cuts(self):
        got = self.branch(start=[self.dad], partnerRule="follow", stops=[self.wife, self.mum, self.hub])
        self.assertEqual(got, self.everyone - {self.ilp1, self.ilp2, self.wbro, self.mgpa, self.mgma, self.hp1, self.hp2})

    def test_exclude_partners(self):
        got = self.branch(start=[self.dad], partnerRule="exclude")
        for pid in (self.mum, self.aunt, self.wife, self.hub):
            self.assertNotIn(pid, got)
        self.assertIn(self.kid, got)

    def test_leave_out_drops_whoever_is_only_reached_through_them(self):
        got = self.branch(start=[self.dad], leaveOut=[{"id": self.sis}])
        self.assertNotIn(self.sis, got)
        self.assertNotIn(self.niece, got)
        self.assertNotIn(self.hub, got)
        self.assertIn(self.me, got)

    def test_leave_out_keep_chain_is_a_placeholder(self):
        v = self.view(scope={"type": "branch", "start": [self.dad], "leaveOut": [{"id": self.sis, "keepChain": True}]})
        ids = included_ids(v)
        self.assertIn(self.sis, ids["placeholderIds"])
        self.assertIn(self.niece, ids["included"])
        self.assertEqual(v.people[self.sis]["name"], "Private")
        self.assertEqual(ids["leaveOut"], [self.sis])

    def test_walk_directions(self):
        down = self.branch(start=[self.dad], walk="descendants", partnerRule="exclude")
        self.assertEqual(down, {self.dad, self.me, self.sis, self.kid, self.niece})
        up = self.branch(start=[self.me], walk="ancestors", partnerRule="exclude")
        self.assertEqual(up, {self.me, self.dad, self.mum, self.gpa, self.gma, self.mgpa, self.mgma})
        both = self.branch(start=[self.dad], walk="both", partnerRule="exclude")
        self.assertEqual(both, {self.dad, self.gpa, self.gma, self.me, self.sis, self.kid, self.niece})

    def test_several_starts_and_a_stop_that_is_a_start(self):
        got = self.branch(start=[self.dad, self.wife], stops=[self.wife])
        # a start is always expanded, even when it's also listed as a stop
        self.assertIn(self.ilp1, got)
        self.assertIn(self.wbro, got)

    def test_unknown_cut_ids_warn(self):
        v = self.view(scope={"type": "branch", "start": [self.dad], "stops": ["gone"]})
        self.assertTrue(any("stop point" in w for w in v.warnings))


class PrivacyTests(ExportCase):
    def test_keep_out_flag_removes_or_placeholders(self):
        self.ok(self.patch(f"/api/people/{self.wbro}", {"never_export": True}))
        self.assertTrue(self.detail(self.wbro)["neverExport"])
        v = self.view()
        self.assertNotIn(self.wbro, v.people)
        self.assertTrue(any("keep out" in w for w in v.warnings))
        # the only link between me and my kids' generation → a placeholder
        self.ok(self.patch(f"/api/people/{self.me}", {"never_export": True}))
        v = self.view()
        ids = included_ids(v)
        self.assertIn(self.me, ids["placeholderIds"])
        self.assertIn(self.kid, ids["included"])
        self.assertEqual(v.people[self.me]["name"], "Private")
        # and it's recorded in history, and undoable
        hist = self.ok(self.get(f"/api/history?personId={self.me}"))
        self.assertTrue(hist["items"])

    def test_living_rules(self):
        v = self.view(living="limited")
        me = v.people[self.me]
        self.assertTrue(me["limited"])
        self.assertIsNone(me["birth"])
        self.assertEqual(me["years"], "")
        self.assertIsNone(me["birthSurname"])
        gpa = v.people[self.gpa]
        self.assertFalse(gpa["limited"])
        self.assertEqual(gpa["years"], "1920–1990")
        full = self.view(living="full").people[self.me]
        self.assertEqual(full["birth"]["year"], 1980)
        ex = self.view(living="exclude")
        self.assertNotIn(self.kid, ex.people)
        # living dad is the only link between the grandparents and … nobody deceased below; he goes
        self.assertTrue(all(not p["living"] for p in ex.people.values() if not p["placeholder"]))

    def test_living_exclude_placeholder_links_deceased(self):
        # a deceased great-grandchild line: gpa → dad (living) → (deceased) child
        baby = self.person("Baby", "Sharma", "male", born=1985, died=1986)
        fam = sql("SELECT family_id FROM family_children WHERE child_id = ?", (self.me,))[0]["family_id"]
        self.ok(self.post(f"/api/families/{fam}/children", {"existingId": baby, "relation": "birth"}), 201)
        ex = self.view(living="exclude")
        self.assertIn(baby, ex.people)
        self.assertTrue(ex.people[self.dad]["placeholder"])      # the link to the grandparents

    def test_dates_places_events_filters(self):
        self.ok(self.post("/api/events", {"personId": self.gpa, "type": "occupation", "title": "Teacher",
                                          "date": {"y": 1950, "m": 6, "d": 1}, "place": "Guntur",
                                          "description": "Taught maths"}), 201)
        self.ok(self.patch(f"/api/people/{self.gpa}", {"birth": {"date": {"y": 1920, "m": 3, "d": 5}, "place": "Tenali"}}))
        v = self.view()
        g = v.people[self.gpa]
        self.assertIn("1920", g["birth"]["date"])
        self.assertEqual(g["birth"]["place"], "Tenali")
        occ = [e for e in g["events"] if e["type"] == "occupation"][0]
        self.assertEqual(occ["place"], "Guntur")
        self.assertIsNone(occ["description"])                        # notes off by default
        noted = [e for e in self.view(notes=True).people[self.gpa]["events"] if e["type"] == "occupation"][0]
        self.assertEqual(noted["description"], "Taught maths")
        y = self.view(dates="year").people[self.gpa]
        self.assertEqual(y["birth"]["date"], "1920")
        n = self.view(dates="none", places=False).people[self.gpa]
        self.assertIsNone(n["birth"]["date"])
        self.assertIsNone(n["birth"]["place"])
        self.assertEqual(n["years"], "")
        no_work = self.view(events={"education_occupation": False}).people[self.gpa]
        self.assertFalse([e for e in no_work["events"] if e["type"] == "occupation"])

    def test_names_and_stories(self):
        self.ok(self.post(f"/api/people/{self.gma}/stories", {"title": "Her garden", "body": "Jasmine everywhere."}), 201)
        self.ok(self.patch(f"/api/people/{self.gma}", {"birth_surname": "Sastry", "nickname": "Ammamma", "biography": "Kind."}))
        v = self.view()
        self.assertEqual(v.people[self.gma]["birthSurname"], "Sastry")
        self.assertEqual(v.people[self.gma]["stories"][0]["title"], "Her garden")
        v = self.view(maidenNames=False, otherNames=False, stories=False)
        self.assertIsNone(v.people[self.gma]["birthSurname"])
        self.assertIsNone(v.people[self.gma]["nickname"])
        self.assertEqual(v.people[self.gma]["stories"], [])
        self.assertIsNone(v.people[self.gma]["biography"])

    def test_relationship_labels(self):
        v = self.view()
        self.assertEqual(v.relative_to, self.me)
        self.assertEqual(v.people[self.gpa]["relationship"], "grandfather")
        v = self.view(relativeTo=self.kid)
        self.assertEqual(v.people[self.me]["relationship"], "father")
        self.assertIsNone(self.view(relationships=False).relative_to)

    def upload(self, pid, color=(10, 20, 30), **form):
        return self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(color=color), "image/jpeg")},
                                 data={"personId": pid, **form}), 201)["media"]["id"]

    def test_photo_privacy(self):
        solo = self.upload(self.gpa)
        group = self.upload(self.gma)
        self.ok(self.post(f"/api/media/{group}/links", {"personId": self.me}), 201)   # a living person is in it
        self.ok(self.put(f"/api/people/{self.gpa}/photo", {"mediaId": solo}))
        v = self.view()
        self.assertIn(solo, v.media)
        self.assertNotIn(group, v.media)                       # living, limited → the whole photo stays out
        self.assertEqual(v.people[self.gpa]["photo"], solo)
        self.assertIn(group, self.view(living="full").media)
        self.assertEqual(self.view(photos="none").media, {})
        prof = self.view(photos="profile", living="full").media
        self.assertEqual(set(prof), {solo})
        # the keep-out flag on anyone tagged keeps the photo out
        self.ok(self.patch(f"/api/people/{self.me}", {"never_export": True}))
        self.assertNotIn(group, self.view(living="full").media)


class ReviewFixTests(ExportCase):
    """Leaks found in review: each detail must stay out of the view (and so of every format)."""

    def upload(self, pid=None, **form):
        data = {"personId": pid} if pid else {}
        data.update(form)
        return self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}, data=data or None), 201)["media"]["id"]

    def test_relative_to_person_not_in_export_means_no_labels(self):
        self.ok(self.patch(f"/api/people/{self.me}", {"never_export": True}))
        v = self.view()
        self.assertIsNone(v.relative_to_name)
        self.assertTrue(all(p["relationship"] is None for p in v.people.values()))
        self.assertTrue(any("Relationship labels" in w for w in v.warnings))
        v = self.view(living="exclude")                  # me is alive
        self.assertIsNone(v.relative_to_name)
        text = self.all_text(self.site({"living": "full"}))
        self.assertNotIn("Arjun", text)

    def test_no_self_relationship(self):
        v = self.view(living="full")
        self.assertIsNone(v.people[self.me]["relationship"])

    def test_couple_events_hidden_when_a_partner_isnt_shown(self):
        fam = sql("SELECT id FROM families WHERE partner1_id = ?", (self.gpa,))[0]["id"]
        self.ok(self.post("/api/events", {"familyId": fam, "type": "marriage", "date": {"y": 1945}, "place": "Secretwedding"}), 201)
        self.assertTrue(self.view().families)
        shown = [f for f in self.view().families if f["id"] == fam][0]
        self.assertTrue(shown["events"])                  # both deceased and shown: fine
        self.ok(self.patch(f"/api/people/{self.gma}", {"never_export": True}))
        fam_v = [f for f in self.view().families if f["id"] == fam][0]
        self.assertEqual(fam_v["events"], [])
        self.assertNotIn("Secretwedding", self.all_text(self.site()))

    def test_photos_of_removed_or_out_of_scope_people_stay_out(self):
        group = self.upload(self.gpa)
        self.ok(self.post(f"/api/media/{group}/links", {"personId": self.dad}), 201)
        self.assertIn(group, self.view(living="full").media)
        self.assertNotIn(group, self.view(living="exclude").media)          # dad removed, not "Private"
        other = self.upload(self.gpa)
        self.ok(self.post(f"/api/media/{other}/links", {"personId": self.wbro}), 201)
        scoped = self.view(living="full", scope={"type": "ancestors", "personId": self.me, "generations": 2, "partners": False})
        self.assertNotIn(other, scoped.media)                                 # wbro isn't in this export

    def test_family_and_event_photos_need_every_partner_and_a_page(self):
        fam = sql("SELECT id FROM families WHERE partner1_id = ?", (self.gpa,))[0]["id"]
        wed = self.upload(familyId=fam, title="Gopal and Gita wedding")
        v = self.view()
        self.assertIn(wed, v.media)
        self.assertIn(wed, v.people[self.gpa]["media"])
        self.ok(self.patch(f"/api/people/{self.gma}", {"never_export": True}))
        z = self.site()
        self.assertFalse([n for n in z.namelist() if wed in n])
        self.assertNotIn("Gopal and Gita wedding", self.all_text(z))
        # a photo on an event type that's switched off appears nowhere, so it isn't copied
        eid = self.ok(self.post("/api/events", {"personId": self.gpa, "type": "military", "title": "Army"}), 201)["event"]["id"]
        army = self.upload(eventId=eid)
        self.assertIn(army, self.view().media)
        self.assertNotIn(army, self.view(events={"military": False}).media)

    def test_surnames_without_the_people_page(self):
        z = self.site({"site": {"pages": {"people": False}}})
        self.assertNotIn("people.html", z.read("surnames.html").decode())


NAMES_ONLY = {
    "living": "full", "maidenNames": False, "otherNames": False, "dates": "none", "places": False,
    "events": {k: False for k in ("birth_death", "marriage", "education_occupation", "residence", "migration",
                                  "military", "religious", "other")},
    "photos": "none", "documents": False, "stories": False, "notes": False, "relationships": False,
    "gender": False, "deaths": False, "familyDetails": False,
}


class DetailSwitchTests(ExportCase):
    """Every detail except the name can be switched off (§13.6.1)."""

    def setUp(self):
        super().setUp()
        self.ok(self.patch(f"/api/people/{self.gpa}", {"birth": {"date": {"y": 1920, "m": 3, "d": 5}, "place": "Tenali"},
                                                       "death": {"date": {"y": 1990}, "place": "Guntur"},
                                                       "nickname": "Tatayya", "biography": "Farmer and teacher."}))
        self.ok(self.post(f"/api/people/{self.gpa}/stories", {"title": "The well", "body": "He dug it himself."}), 201)
        self.ok(self.post("/api/events", {"personId": self.gpa, "type": "occupation", "title": "Headmaster", "place": "Bapatla"}), 201)
        fam = sql("SELECT id FROM families WHERE partner1_id = ?", (self.gpa,))[0]["id"]
        self.ok(self.post("/api/events", {"familyId": fam, "type": "marriage", "date": {"y": 1945}, "place": "Ongole"}), 201)
        step = self.person("Stepkid", "Sharma", "male", born=1990)
        dadfam = sql("SELECT id FROM families WHERE partner1_id = ?", (self.dad,))[0]["id"]
        self.ok(self.post(f"/api/families/{dadfam}/children", {"existingId": step, "relation": "step"}), 201)
        self.step = step
        self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")},
                          data={"personId": self.gpa, "title": "Harvest photo"}), 201)

    def test_names_only_view(self):
        v = self.view(**NAMES_ONLY)
        self.assertEqual(v.media, {})
        self.assertIsNone(v.relative_to_name)
        for p in v.people.values():
            self.assertEqual(p["gender"], "unknown")
            self.assertFalse(p["died"])
            self.assertEqual(p["years"], "")
            self.assertIsNone(p["birth"])
            self.assertIsNone(p["death"])
            self.assertEqual(p["events"], [])
            self.assertEqual(p["stories"], [])
            self.assertIsNone(p["biography"])
            self.assertIsNone(p["nickname"])
            self.assertIsNone(p["relationship"])
        for f in v.families:
            self.assertEqual(f["kind"], "unknown")
            self.assertIsNone(f["ended"])
            self.assertEqual(f["events"], [])
            self.assertTrue(all(c["relation"] == "birth" for c in f["children"]))
        self.assertEqual(v.people[self.gpa]["name"], "Gopal Sharma")

    def test_names_only_site(self):
        z = self.site(NAMES_ONLY)
        text = self.all_text(z)
        for gone in ("Tenali", "Guntur", "Bapatla", "Ongole", "1920", "1945", "1990", "Tatayya", "Farmer", "The well",
                     "Headmaster", "Harvest", "grandfather", "Married", '"gender": "male"', '"gender": "female"',
                     "av male", "av female", '"step"', "Step)", '"living": false', "places.html"):
            self.assertNotIn(gone, text, gone)
        self.assertFalse([n for n in z.namelist() if n.startswith("media/")])
        self.assertNotIn("🕯", self.html_text(z))
        self.assertIn("Gopal Sharma", text)
        self.assertIn("Spouse / partner", text)            # who is whose partner is the tree itself

    def test_neutral_relationship_labels(self):
        v = self.view(living="full", gender=False)
        self.assertEqual(v.people[self.dad]["relationship"], "parent")
        self.assertEqual(v.people[self.gpa]["relationship"], "grandparent")
        v = self.view(living="full", familyDetails=False)
        self.assertEqual(v.people[self.step]["relationship"], "brother")
        full = self.view(living="full")
        self.assertEqual(full.people[self.step]["relationship"], "half-brother")
        self.assertEqual(full.people[self.dad]["relationship"], "father")

    def test_deaths_off(self):
        v = self.view(deaths=False)
        g = v.people[self.gpa]
        self.assertFalse(g["died"])
        self.assertIsNone(g["death"])
        self.assertEqual(g["years"], "b. 1920")
        self.assertIsNotNone(g["birth"])
        z = self.site({"deaths": False})
        self.assertNotIn("🕯", self.html_text(z))
        self.assertNotIn('"living": false', z.read("assets/data.js").decode())

    def test_individual_switches_are_independent(self):
        v = self.view(gender=False)
        self.assertEqual(v.people[self.gpa]["birth"]["place"], "Tenali")
        self.assertTrue(v.media)
        self.assertIn("places.html", self.site().namelist())      # there are places to list
        v = self.view(photos="none")
        self.assertEqual(v.media, {})
        self.assertEqual(v.people[self.gpa]["gender"], "male")


class RouteTests(ExportCase):
    def test_preview(self):
        r = self.preview({"scope": {"type": "branch", "start": [self.dad]}})
        self.assertEqual(r["people"], len(r["included"]))
        self.assertIn(self.dad, r["included"])
        self.assertIn("bytesEstimate", r)
        self.assertEqual(r["relativeToName"], "Arjun Sharma")
        self.assertTrue(r["mediaOnline"])

    def test_bad_options_rejected(self):
        self.ok(self.post("/api/export/preview", {"format": "site", "options": {"living": "everyone"}}), 422)
        self.ok(self.post("/api/export/preview", {"format": "site", "options": {"nope": 1}}), 422)
        self.ok(self.post("/api/export/preview", {"format": "gedcom", "options": {}}), 422)

    def test_job_download_and_history(self):
        job = self.run_export()
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["filename"].endswith(".zip"))
        self.assertEqual(job["stats"]["people"], 20)
        z = zipfile.ZipFile(io.BytesIO(self.get(f"/api/export/{job['id']}/file").content))
        self.assertIn("index.html", z.namelist())
        hist = self.ok(self.get("/api/history"))["items"]
        row = [h for h in hist if h["label"].startswith("Exported the website")][0]
        self.assertEqual(row["changes"], 0)
        # someone else can't see or download it; an admin can
        self.ok(self.get(f"/api/export/{job['id']}", user=BOB), 404)
        self.ok(self.get(f"/api/export/{job['id']}/file", user=BOB), 404)
        self.assertEqual(self.get(f"/api/export/{job['id']}/file", user=ADMIN).status_code, 200)

    def test_the_file_is_on_the_share_and_expires(self):
        job = self.run_export()
        path = export_router._jobs[job["id"]]["path"]
        self.assertTrue(path.startswith(os.path.join(export_router.media.root(), ".exports")))
        export_router._jobs[job["id"]]["finished"] -= export_router.JOB_TTL + 5
        export_router.cleanup()
        self.assertFalse(os.path.exists(path))
        self.ok(self.get(f"/api/export/{job['id']}"), 404)

    def test_one_running_job_per_user(self):
        jid = "x" * 32
        with export_router._lock:
            export_router._jobs[jid] = {"id": jid, "user": ALICE["id"], "status": "running", "created": time.time()}
        try:
            self.ok(self.post("/api/export", {"format": "site", "options": {}}), 409)
            other = self.ok(self.post("/api/export", {"format": "site", "options": {}}, user=BOB), 202)["jobId"]
            for _ in range(200):                     # let it finish before the next test resets the DB
                if self.ok(self.get(f"/api/export/{other}", user=BOB))["status"] != "running":
                    break
                time.sleep
        finally:
            export_router._jobs.pop(jid, None)

    def test_nobody_included_fails_cleanly(self):
        job = self.run_export({"scope": {"type": "selected", "ids": []}})
        self.assertEqual(job["status"], "failed")
        self.assertIn("Nobody", job["error"])

    def test_last_used_options_per_user(self):
        self.assertIsNone(self.ok(self.get("/api/export/last?format=site"))["options"])
        self.run_export({"living": "exclude", "site": {"title": "Sharmas"}})
        last = self.ok(self.get("/api/export/last?format=site"))["options"]
        self.assertEqual(last["living"], "exclude")
        self.assertEqual(last["site"]["title"], "Sharmas")
        self.assertIsNone(self.ok(self.get("/api/export/last?format=site", user=BOB))["options"])

    def test_presets(self):
        a = self.ok(self.post("/api/export/presets", {"name": "Dad's  side", "format": "site",
                                                      "options": {"scope": {"type": "branch", "start": [self.dad]}}}), 201)
        self.assertEqual(a["name"], "Dad's side")
        b = self.ok(self.post("/api/export/presets", {"name": "dad's side", "format": "site", "options": {"living": "full"}}, user=BOB), 201)
        self.assertEqual(a["id"], b["id"])                    # same name (any case) replaces
        lst = self.ok(self.get("/api/export/presets", user=BOB))
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["options"]["living"], "full")
        self.ok(self.post("/api/export/presets", {"name": "", "format": "site", "options": {}}), 422)
        self.ok(self.delete(f"/api/export/presets/{a['id']}"))
        self.ok(self.delete(f"/api/export/presets/{a['id']}"), 404)


class SiteTests(ExportCase):
    def test_pages_and_links(self):
        z = self.site({"living": "full"})
        names = set(z.namelist())
        for n in ("index.html", "tree.html", "people.html", "surnames.html", "assets/site.css",
                  "assets/site.js", "assets/tree.js", "assets/data.js"):
            self.assertIn(n, names)
        person_pages = [n for n in names if n.startswith("people/")]
        self.assertEqual(len(person_pages), 20)
        # every relative link and src inside the zip points at a file that exists
        for n in names:
            if not n.endswith(".html"):
                continue
            base = os.path.dirname(n)
            for ref in re.findall(r'(?:href|src)="([^"#?]+)', z.read(n).decode()):
                if ref.startswith(("http:", "https:", "data:", "mailto:")):
                    self.fail(f"external reference {ref} in {n}")
                target = os.path.normpath(os.path.join(base, ref)).replace(os.sep, "/")
                self.assertIn(target, names, f"{n} → {ref}")

    def test_pages_can_be_switched_off(self):
        z = self.site({"site": {"pages": {"tree": False, "surnames": False, "places": False, "people": True}}})
        names = set(z.namelist())
        self.assertNotIn("tree.html", names)
        self.assertNotIn("surnames.html", names)
        self.assertNotIn("places.html", names)
        self.assertNotIn('href="tree.html"', z.read("index.html").decode())

    def test_escaping(self):
        evil = '<script>alert(1)</script>"&'
        pid = self.person(evil, "O'Brien", born=1900, died=1960)
        self.family(self.gpa, None, children=[pid])
        self.ok(self.post(f"/api/people/{pid}/stories", {"title": "<b>t</b>", "body": "<img src=x onerror=alert(1)>"}), 201)
        z = self.site({"site": {"title": "</title><script>x()</script>", "intro": "<i>hi</i>"}})
        text = self.html_text(z)            # data.js holds names as JSON strings, drawn with textContent
        self.assertNotIn("<script>alert(1)", text)
        self.assertNotIn("<img src=x", text)
        self.assertNotIn("<script>x()", text)
        self.assertNotIn("<i>hi</i>", text)
        data = z.read("assets/data.js").decode()
        self.assertNotIn("</script>", data)
        # the pages only run local scripts, under a strict policy
        for n in z.namelist():
            if n.endswith(".html"):
                page = z.read(n).decode()
                self.assertIn("default-src 'none'", page)
                self.assertNotRegex(page, r"<script>(?!\s*</script>)")    # no inline scripts

    def test_limited_people_have_no_private_details_in_the_site(self):
        self.ok(self.patch(f"/api/people/{self.me}", {"birth": {"date": {"y": 1980, "m": 7, "d": 14}, "place": "Secretplace"},
                                                      "biography": "Private bio text"}))
        self.ok(self.post("/api/events", {"personId": self.me, "type": "residence", "place": "Hiddenville"}), 201)
        mid = self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")},
                                data={"personId": self.me}), 201)["media"]["id"]
        z = self.site({"living": "limited"})
        text = self.all_text(z)
        for secret in ("Secretplace", "Private bio text", "Hiddenville", "14 Jul", "1980"):
            self.assertNotIn(secret, text)
        self.assertFalse([n for n in z.namelist() if mid in n])
        self.assertIn("Arjun Sharma", text)                     # the name is still there

    def test_keep_out_person_never_in_the_site(self):
        self.ok(self.patch(f"/api/people/{self.wbro}", {"never_export": True}))
        text = self.all_text(self.site({"living": "full"}))
        self.assertNotIn("Vikram", text)

    def test_photos_copied_at_the_chosen_size(self):
        mid = self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(size=(2400, 1600)), "image/jpeg")},
                                data={"personId": self.gpa}), 201)["media"]["id"]
        z = self.site()
        files = [n for n in z.namelist() if n.startswith("media/") and mid in n]
        self.assertEqual(len(files), 1)
        from PIL import Image
        self.assertLessEqual(max(Image.open(io.BytesIO(z.read(files[0]))).size), 1024)
        z = self.site({"photoSize": "original"})
        files = [n for n in z.namelist() if n.startswith("media/") and mid in n]
        self.assertEqual(max(Image.open(io.BytesIO(z.read(files[0]))).size), 2400)

    def test_branch_site_contains_only_the_branch(self):
        z = self.site({"living": "full", "scope": {"type": "branch", "start": [self.dad], "partnerRule": "follow", "stops": [self.wife]}})
        text = self.all_text(z)
        self.assertIn("Anjali", text)
        self.assertNotIn("Vikram", text)
        self.assertNotIn("Sarala", text)


if __name__ == "__main__":
    unittest.main()


class Extras080(ExportCase):
    """Custom fields, contacts and sources in exports; the print data."""

    def test_custom_contacts_sources(self):
        f = self.ok(self.post("/api/custom-fields/templates/gotram", user=ADMIN), 201)
        secret = self.ok(self.post("/api/custom-fields", {"label": "Blood group", "exportDefault": False}, user=ADMIN), 201)
        self.ok(self.put(f"/api/people/{self.gpa}/custom", {f["id"]: "Kashyapa", secret["id"]: "O+"}))
        self.ok(self.put(f"/api/people/{self.me}/custom", {f["id"]: "Kashyapa"}))
        self.ok(self.post(f"/api/people/{self.me}/contacts", {"kind": "phone", "value": "+91 90000 12345"}), 201)
        src = self.ok(self.post("/api/sources", {"title": "Family bible"}), 201)["source"]
        self.ok(self.post("/api/citations", {"sourceId": src["id"], "personId": self.gpa, "fact": "name", "page": "p. 3"}), 201)
        v = self.view()
        gp = v.people[self.gpa]
        self.assertEqual(gp["custom"], [{"label": "Gotram", "value": "Kashyapa"}])      # Blood group is off by default
        self.assertEqual(gp["sources"], [])
        self.assertEqual(v.people[self.me]["contacts"], [])
        self.assertEqual(v.people[self.me]["custom"], [])                                # living, limited
        v = self.view(customFields={secret["id"]: True, f["id"]: False}, sources=True, contacts=True, living="full")
        self.assertEqual(v.people[self.gpa]["custom"], [{"label": "Blood group", "value": "O+"}])
        self.assertEqual(v.people[self.gpa]["sources"], [{"title": "Family bible", "page": "p. 3", "fact": "name"}])
        self.assertEqual(v.people[self.me]["contacts"][0]["value"], "+919000012345")
        text = self.html_text(self.site({"sources": True, "contacts": True, "living": "full"}))
        self.assertIn("Family bible", text)
        self.assertIn("+919000012345", text)
        self.assertIn("Kashyapa", text)
        text = self.html_text(self.site({"living": "full"}))
        self.assertNotIn("+919000012345", text)
        self.assertNotIn("Family bible", text)

    def test_print_data(self):
        r = self.ok(self.post("/api/export/print", {"format": "site", "options": {
            "scope": {"type": "ancestors", "personId": self.me, "generations": 2}, "living": "limited"}}))
        self.assertIn(self.gpa, r["people"])
        self.assertNotIn(self.kid, r["people"])
        self.assertTrue(r["people"][self.me]["limited"])
        self.assertTrue(all("path" not in m for m in r["media"].values()))
        self.assertEqual(self.post("/api/export/print", {"format": "site", "options": {
            "scope": {"type": "selected", "ids": []}}}).status_code, 422)
