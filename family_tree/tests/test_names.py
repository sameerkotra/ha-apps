"""Names in Telugu / Hindi script, name order (§13.7) and ceremony event types (§13.14)."""
from base import ADMIN, ApiTestCase, set_app_settings, sql
import _env  # noqa: F401

import hashlib
import os
import re
import unittest

from app import names


class ScriptNames(ApiTestCase):
    def test_store_detect_and_search(self):
        pid = self.person("Lakshmi", "Sharma", given_local="లక్ష్మి", surname_local="శర్మ")
        d = self.detail(pid)
        self.assertEqual((d["givenLocal"], d["surnameLocal"], d["nameLocal"], d["localScript"]),
                         ("లక్ష్మి", "శర్మ", "లక్ష్మి శర్మ", "te"))
        hi = self.person("Ravi", given_local="रवि")
        self.assertEqual(self.detail(hi)["localScript"], "hi")
        # either spelling finds them
        self.assertEqual([p["id"] for p in self.ok(self.get("/api/people?q=లక్ష్మి"))["items"]], [pid])
        self.assertEqual([p["id"] for p in self.ok(self.get("/api/people?q=lakshmi"))["items"]], [pid])
        card = self.ok(self.get(f"/api/tree?focus={pid}"))["descendants"]["p"]
        self.assertEqual((card["nameLocal"], card["givenLocal"]), ("లక్ష్మి శర్మ", "లక్ష్మి"))
        self.ok(self.patch(f"/api/people/{pid}", {"given_local": None, "surname_local": None}))
        self.assertIsNone(self.detail(pid)["nameLocal"])

    def test_name_order(self):
        pid = self.person("Arjun", "Sharma")
        self.assertEqual(self.detail(pid)["name"], "Arjun Sharma")
        set_app_settings(name_order="surname_first")
        self.assertEqual(self.detail(pid)["name"], "Sharma Arjun")
        self.assertEqual(self.ok(self.get("/api/people?q=sharma"))["items"][0]["name"], "Sharma Arjun")
        r = self.ok(self.patch(f"/api/people/{pid}", {"name_order": "given_first"}))
        self.assertEqual((r["person"]["name"], r["person"]["nameOrder"], r["person"]["nameOrderEffective"]),
                         ("Arjun Sharma", "given_first", "given_first"))
        self.assertEqual(self.patch(f"/api/people/{pid}", {"name_order": "backwards"}).status_code, 422)
        self.assertEqual(self.put("/api/admin/settings", {"name_order": "x"}, user=ADMIN).status_code, 422)
        self.assertEqual(names.full("A", "B", person_order="surname_first"), "B A")
        self.assertEqual(names.script_of("abc"), None)

    def test_display_preference(self):
        self.assertEqual(self.ok(self.get("/api/me"))["nameDisplay"], "en")
        self.ok(self.put("/api/me/name-display", {"nameDisplay": "both"}))
        self.assertEqual(self.ok(self.get("/api/me"))["nameDisplay"], "both")
        self.assertEqual(self.put("/api/me/name-display", {"nameDisplay": "klingon"}).status_code, 422)

    def test_vendored_sanscript(self):
        root = os.path.join(_env.ROOT, "app", "static", "vendor", "sanscript")
        want = re.search(r"sanscript\.js ([0-9a-f]{64})", open(os.path.join(root, "README.md")).read()).group(1)
        self.assertEqual(hashlib.sha256(open(os.path.join(root, "sanscript.js"), "rb").read()).hexdigest(), want)
        self.assertEqual(self.get("/vendor/sanscript/sanscript.js").status_code, 200)


class Ceremonies(ApiTestCase):
    def test_person_and_family_ceremonies(self):
        a = self.person("Ravi", gender="male")
        b = self.person("Priya", gender="female")
        fid = self.family(a, b)
        r = self.ok(self.post("/api/events", {"personId": a, "type": "upanayanam", "date": {"y": 1972}, "place": "Guntur"}), 201)
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (r["batchId"],))[0]["label"], "Added Upanayanam")
        self.ok(self.post("/api/events", {"personId": a, "type": "ceremony", "title": "Pelli choopulu"}), 201)
        self.ok(self.post("/api/events", {"familyId": fid, "type": "gruhapravesham", "date": {"y": 2001}}), 201)
        self.assertEqual(self.post("/api/events", {"familyId": fid, "type": "upanayanam"}).status_code, 422)
        self.assertEqual(self.post("/api/events", {"personId": a, "type": "gruhapravesham"}).status_code, 422)
        types = [e["type"] for e in self.detail(a)["events"]]
        self.assertIn("upanayanam", types)

    def test_labels(self):
        from app import ceremonies
        self.assertEqual(ceremonies.label("upanayanam", "te"), "ఉపనయనం")
        self.assertEqual(ceremonies.label("upanayanam", "hi"), "उपनयन")
        self.assertTrue(ceremonies.label("upanayanam").startswith("Upanayanam"))
        from app.export_view import EVENT_GROUPS
        self.assertIn("upanayanam", EVENT_GROUPS["religious"])


if __name__ == "__main__":
    unittest.main()
