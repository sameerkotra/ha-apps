"""Custom fields (§13.8), sources & citations (§13.9) and contact details with
vCard (§13.11)."""
from base import ADMIN, BOB, ApiTestCase, set_app_settings, sql
import _env  # noqa: F401

import unittest

from app.routers.contacts import normalize_phone


class CustomFields(ApiTestCase):
    def test_admin_defines_everyone_fills(self):
        self.assertEqual(self.post("/api/custom-fields", {"label": "Gotram"}).status_code, 403)
        f = self.ok(self.post("/api/custom-fields/templates/gotram", user=ADMIN), 201)
        nak = self.ok(self.post("/api/custom-fields/templates/nakshatram", user=ADMIN), 201)
        village = self.ok(self.post("/api/custom-fields", {"label": " Native  village ", "kind": "place", "onCard": True}, user=ADMIN), 201)
        self.assertEqual(village["label"], "Native village")
        self.assertEqual(len(nak["choices"]), 27)
        self.assertEqual(self.post("/api/custom-fields", {"label": "gotram"}, user=ADMIN).status_code, 409)
        self.assertEqual(self.post("/api/custom-fields", {"label": "X", "kind": "choice"}, user=ADMIN).status_code, 422)
        a = self.person("Ann", "Smith")
        b = self.person("Bob", "Smith")
        r = self.ok(self.put(f"/api/people/{a}/custom", {f["id"]: "Kashyapa", nak["id"]: "Rohini", village["id"]: "Kakinada"}, user=BOB))
        by = {c["label"]: c["value"] for c in r["custom"]}
        self.assertEqual(by, {"Gotram": "Kashyapa", "Nakshatram": "Rohini", "Native village": "Kakinada"})
        self.assertEqual(sql("SELECT label, user_id FROM batches WHERE id = ?", (r["batchId"],))[0],
                         {"label": "Edited details of Ann Smith", "user_id": BOB["id"]})
        self.assertEqual(self.put(f"/api/people/{a}/custom", {nak["id"]: "Pluto"}).status_code, 422)
        self.ok(self.put(f"/api/people/{b}/custom", {village["id"]: "Guntur"}))
        # detail, card, filter, search
        self.assertEqual({c["label"]: c["value"] for c in self.detail(a)["custom"]}["Gotram"], "Kashyapa")
        card = self.ok(self.get(f"/api/tree?focus={a}"))["descendants"]["p"]
        self.assertEqual(card["cardFields"], ["Native village: Kakinada"])
        got = [p["id"] for p in self.ok(self.get(f"/api/people?field={village['id']}&value=kakinada"))["items"]]
        self.assertEqual(got, [a])
        self.assertEqual([p["id"] for p in self.ok(self.get("/api/people?q=kashyapa"))["items"]], [a])
        vals = self.ok(self.get(f"/api/custom-fields/{village['id']}/values"))
        self.assertEqual(vals, [{"value": "Guntur", "count": 1}, {"value": "Kakinada", "count": 1}])
        # clearing a value, undo
        r = self.ok(self.put(f"/api/people/{a}/custom", {f["id"]: None}))
        self.assertIsNone({c["label"]: c["value"] for c in r["custom"]}["Gotram"])
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual({c["label"]: c["value"] for c in self.detail(a)["custom"]}["Gotram"], "Kashyapa")
        # archive hides, keeps values; restore brings them back
        self.ok(self.patch(f"/api/custom-fields/{f['id']}", {"archived": True}, user=ADMIN))
        self.assertNotIn("Gotram", [c["label"] for c in self.detail(a)["custom"]])
        self.assertEqual(self.put(f"/api/people/{a}/custom", {f["id"]: "x"}).status_code, 422)
        self.ok(self.patch(f"/api/custom-fields/{f['id']}", {"archived": False}, user=ADMIN))
        self.assertEqual({c["label"]: c["value"] for c in self.detail(a)["custom"]}["Gotram"], "Kashyapa")

    def test_date_and_family_fields(self):
        d = self.ok(self.post("/api/custom-fields", {"label": "Muhurtham", "kind": "date", "appliesTo": "family"}, user=ADMIN), 201)
        a, b = self.person("A"), self.person("B")
        fid = self.family(a, b)
        r = self.ok(self.put(f"/api/families/{fid}/custom", {d["id"]: "12 mar 1985"}))
        self.assertEqual(r["custom"][0]["value"], "12 MAR 1985")
        self.assertEqual(r["custom"][0]["display"], "12 March 1985")
        self.assertEqual(self.put(f"/api/families/{fid}/custom", {d["id"]: "someday"}).status_code, 422)
        self.assertEqual(self.put(f"/api/people/{a}/custom", {d["id"]: "12 mar 1985"}).status_code, 422)   # family-only
        self.assertEqual(self.ok(self.get(f"/api/families/{fid}"))["custom"][0]["value"], "12 MAR 1985")


class Sources(ApiTestCase):
    def test_sources_and_citations(self):
        a = self.person("Ann", "Smith", born={"y": 1950})
        gma = self.person("Grandma")
        s = self.ok(self.post("/api/sources", {"title": "Birth certificate", "kind": "certificate", "repository": "Guntur registry"}), 201)["source"]
        iv = self.ok(self.post("/api/sources", {"title": "Talk with Grandma", "kind": "interview", "toldBy": gma, "date": "2026"}), 201)["source"]
        self.assertEqual(self.post("/api/sources", {"title": "X", "kind": "rumour"}).status_code, 422)
        self.assertEqual(self.post("/api/sources", {"title": "X", "url": "javascript:alert(1)"}).status_code, 422)
        birth = next(e for e in self.detail(a)["events"] if e["type"] == "birth")
        c1 = self.ok(self.post("/api/citations", {"sourceId": s["id"], "eventId": birth["id"], "page": "Reg. 42", "quality": 3}), 201)
        self.ok(self.post("/api/citations", {"sourceId": iv["id"], "personId": a, "fact": "name"}), 201)
        self.assertEqual(self.post("/api/citations", {"sourceId": s["id"], "personId": a, "eventId": birth["id"]}).status_code, 422)
        self.assertEqual(self.post("/api/citations", {"sourceId": s["id"], "personId": a, "fact": "shoe size"}).status_code, 422)
        self.assertEqual(self.post("/api/citations", {"sourceId": s["id"], "personId": a, "quality": 5}).status_code, 422)
        cites = self.detail(a)["citations"]
        self.assertEqual(sorted((c["sourceTitle"], c["fact"], c["eventId"] is not None) for c in cites),
                         [("Birth certificate", None, True), ("Talk with Grandma", "name", False)])
        lst = {x["title"]: x["citations"] for x in self.ok(self.get("/api/sources"))["items"]}
        self.assertEqual(lst, {"Birth certificate": 1, "Talk with Grandma": 1})
        full = self.ok(self.get(f"/api/sources/{iv['id']}"))
        self.assertEqual((full["toldByName"], full["citationsList"][0]["personName"]), ("Grandma", "Ann Smith"))
        # in the person's history
        hist = [i["label"] for i in self.ok(self.get(f"/api/history?personId={a}"))["items"]]
        self.assertIn("Cited Birth certificate for Ann Smith", hist)
        # deleting a source hides its citations; undo brings them back
        r = self.ok(self.delete(f"/api/sources/{s['id']}"))
        self.assertEqual(len(self.detail(a)["citations"]), 1)
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        self.assertEqual(len(self.detail(a)["citations"]), 2)
        self.ok(self.patch(f"/api/citations/{c1['citation']['id']}", {"page": "Reg. 43"}))
        self.ok(self.delete(f"/api/citations/{c1['citation']['id']}"))
        self.assertEqual(len(self.detail(a)["citations"]), 1)
        # an event with a citation can't be un-created from under it
        self.assertEqual(self.ok(self.get(f"/api/citations?personId={a}"))["items"][0]["fact"], "name")


class Contacts(ApiTestCase):
    def test_phone_normalisation(self):
        self.assertEqual(normalize_phone("+91 90000 12345"), "+919000012345")
        self.assertEqual(normalize_phone("(202) 555-0100", "+1"), "+12025550100")
        self.assertEqual(normalize_phone("0091 90000 12345"), "+919000012345")
        self.assertEqual(normalize_phone("011 91 90000 12345"), "+919000012345")
        self.assertEqual(normalize_phone("090000 12345", "+91"), "+919000012345")
        with self.assertRaises(ValueError):
            normalize_phone("12")
        set_app_settings(default_phone_code="+91")
        self.assertEqual(self.ok(self.get("/api/phone-preview?value=90000%2012345"))["value"], "+919000012345")

    def test_contacts_address_book_and_vcard(self):
        me = self.person("Me", "Sharma")
        a = self.person("Lakshmi", "Sharma", gender="female", born={"d": 12, "m": 3}, given_local="లక్ష్మి")
        dead = self.person("Venkat", "Sharma", deceased=True)
        self.family(a, None, [me])
        self.set_me(me)
        r = self.ok(self.post(f"/api/people/{a}/contacts", {"kind": "phone", "label": "mobile", "value": "+91 90000 12345"}), 201)
        self.assertEqual(r["contacts"][0]["link"], "tel:+919000012345")
        self.ok(self.post(f"/api/people/{a}/contacts", {"kind": "whatsapp", "value": "+91 90000 12345"}), 201)
        self.ok(self.post(f"/api/people/{a}/contacts", {"kind": "email", "value": "Lakshmi@Example.COM"}), 201)
        r = self.ok(self.post(f"/api/people/{a}/contacts", {"kind": "address", "value": "12 Main Rd\nGuntur"}), 201)
        kinds = {c["kind"]: c for c in r["contacts"]}
        self.assertEqual(kinds["whatsapp"]["link"], "https://wa.me/919000012345")
        self.assertEqual(kinds["email"]["value"], "lakshmi@example.com")
        self.assertEqual(self.post(f"/api/people/{a}/contacts", {"kind": "email", "value": "nope"}).status_code, 422)
        self.assertEqual(self.post(f"/api/people/{dead}/contacts", {"kind": "phone", "value": "+15550000000"}).status_code, 409)
        self.assertEqual(len(self.detail(a)["contacts"]), 4)
        book = self.ok(self.get("/api/contacts"))
        self.assertEqual([(p["name"], p["relationship"]) for p in book["items"]], [("Lakshmi Sharma", "mother")])
        vcf = self.get(f"/api/contacts.vcf?ids={a}")
        self.assertEqual(vcf.status_code, 200)
        self.assertIn("text/vcard", vcf.headers["content-type"])
        t = vcf.text
        for line in ("BEGIN:VCARD", "FN:Lakshmi Sharma", "N:Sharma;Lakshmi;;;", "TEL;TYPE=mobile:+919000012345",
                     "EMAIL:lakshmi@example.com", "BDAY;X-APPLE-OMIT-YEAR=1604:1604-03-12", "NOTE:లక్ష్మి"):
            self.assertIn(line, t)
        # once marked deceased the contacts are hidden (kept)
        self.ok(self.patch(f"/api/people/{a}", {"deceased": True}))
        self.assertEqual(self.detail(a)["contacts"], [])
        self.assertEqual(self.ok(self.get("/api/contacts"))["items"], [])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM contacts")[0]["n"], 4)
        self.assertEqual(self.get(f"/api/contacts.vcf?ids={a}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
