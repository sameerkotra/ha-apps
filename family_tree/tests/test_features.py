"""Feature switches (Admin → App settings → Features): defaults for a new
install, the existing-data rule for older databases and restored backups,
off = hidden (UI data, API 404, background jobs, exports), and switching back
on shows everything again. The people and places here are invented data."""
from base import ADMIN, ALICE, ApiTestCase, all_features_on, image_bytes, set_app_settings, sql
import _env

import io
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import date
from unittest import mock

from app import config, db, export_view, features, geocode, inbox, reminders, settings, upcoming

GENERAL = ["reminders", "milestones", "sides", "photo_tagging", "photo_fixes", "inbox", "custom_fields", "sources",
           "contacts", "duplicates", "quiz", "printing", "export"]
OFF_FOR_NEW = ["map", "kin_names", "script_names", "tithi", "ceremonies"]
DEV = {"X-Device-Id": "device-feature-test-0001"}


def off(**names):
    set_app_settings(**{f"feature_{k}": v for k, v in names.items()})


class Defaults(ApiTestCase):
    ALL_FEATURES = False

    def test_new_install_defaults(self):
        me = self.ok(self.get("/api/me"))
        self.assertEqual(me["features"], {**{k: True for k in GENERAL}, **{k: False for k in OFF_FOR_NEW}})
        self.assertFalse(me["mapEnabled"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM app_settings")[0]["n"], 0)     # nothing stored: the defaults
        s = self.ok(self.get("/api/admin/settings", user=ADMIN))
        kinds = {f["name"]: f["kind"] for f in s["features"]}
        self.assertEqual(kinds["map"], "internet")
        self.assertEqual({k for k, v in kinds.items() if v == "regional"}, {"kin_names", "script_names", "tithi", "ceremonies"})
        self.assertEqual(sorted(kinds), sorted(GENERAL + OFF_FOR_NEW))
        for f in s["features"]:
            self.assertEqual(s["values"][f["key"]], f["default"])
            self.assertTrue(f["help"] and f["label"])

    def test_switches_validate_and_apply_live(self):
        for bad in ({"feature_map": "yes"}, {"feature_map": 1}, {"feature_nope": True}, {"enable_map": True},
                    {"inbox_enabled": False}):
            self.assertEqual(self.put("/api/admin/settings", bad, user=ADMIN).status_code, 422, bad)
        self.assertEqual(self.put("/api/admin/settings", {"feature_tithi": True}, user=ALICE).status_code, 403)
        self.assertEqual(self.get("/api/tithi/dates?year=2026").status_code, 404)
        self.ok(self.put("/api/admin/settings", {"feature_tithi": True}, user=ADMIN))
        self.ok(self.get("/api/tithi/dates?year=2026"))                               # no restart needed
        self.assertTrue(self.ok(self.get("/api/me"))["features"]["tithi"])

    def test_household_language_needs_the_switch(self):
        r = self.put("/api/admin/settings", {"relationship_language": "te"}, user=ADMIN)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Indian relationship names", r.json()["detail"])
        self.ok(self.put("/api/admin/settings", {"feature_kin_names": True, "relationship_language": "te"}, user=ADMIN))
        # switching the module off later keeps the choice for when it comes back
        self.ok(self.put("/api/admin/settings", {"feature_kin_names": False}, user=ADMIN))
        self.assertEqual(settings.get("relationship_language"), "te")
        self.assertEqual(self.ok(self.get("/api/me"))["kinLangEffective"], "en")

    def test_friendly_404_names_the_way_back(self):
        r = self.get("/api/map/status")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"detail": "Places map is turned off. An admin can turn it on in Admin → App settings.",
                                    "featureOff": "map"})
        r = self.get("/api/map/status", user=ADMIN)
        self.assertIn("Turn it on in Admin → App settings → Features", r.json()["detail"])

    def test_csp_has_no_tile_server_while_the_map_is_off(self):
        self.assertNotIn("openstreetmap", self.get("/api/health").headers["content-security-policy"])


class ExistingData(ApiTestCase):
    """An older database, or a restored backup, with no stored switch: a module that
    already holds data is switched on (INSERT OR IGNORE … WHERE EXISTS)."""
    ALL_FEATURES = False

    def migrate(self):
        db.init_db()
        settings.invalidate()
        return features.states()

    def test_data_turns_the_switch_on(self):
        a = self.person("Asha", "Sharma")
        now = config.now_iso()
        self.assertEqual(self.migrate(), {**{k: True for k in GENERAL}, **{k: False for k in OFF_FOR_NEW}})
        sql("UPDATE people SET given_local = 'ఆశ' WHERE id = ?", (a,))
        sql("INSERT INTO kin_terms (lang, kin_key, term, source, updated_at) VALUES ('te', 'F.B', 'Pedda', 'custom', ?)", (now,))
        self.ok(self.patch(f"/api/people/{a}", {"death": {"date": {"y": 2020, "m": 3, "d": 4}}}))
        death = sql("SELECT id FROM events WHERE person_id = ? AND type = 'death'", (a,))[0]["id"]
        sql("INSERT INTO event_tithi (event_id, masa, paksha, tithi, source) VALUES (?, 1, 'shukla', 5, 'entered')", (death,))
        sql("INSERT INTO events (id, person_id, type, created_at, updated_at) VALUES ('e-cer', ?, 'upanayanam', ?, ?)",
            (a, now, now))
        sql("INSERT INTO place_geo (place_key, place, lat, lon, source, checked_at) VALUES ('x', 'X', 1, 2, 'manual', ?)", (now,))
        self.assertEqual(self.migrate(), {k: True for k in GENERAL + OFF_FOR_NEW})
        rows = {r["key"]: r for r in sql("SELECT * FROM app_settings")}
        self.assertEqual(json.loads(rows["feature_tithi"]["value"]), True)
        self.assertIsNone(rows["feature_tithi"]["updated_by"])

    def test_household_language_or_a_personal_choice_counts(self):
        sql("INSERT INTO app_settings (key, value, updated_at) VALUES ('relationship_language', '\"hi\"', ?)", (config.now_iso(),))
        self.assertTrue(self.migrate()["kin_names"])

    def test_a_stored_value_always_wins(self):
        set_app_settings(feature_tithi=True)
        a = self.person("Asha")
        self.ok(self.patch(f"/api/people/{a}", {"death": {"date": {"y": 2020, "m": 3, "d": 4}}}))
        death = sql("SELECT id FROM events WHERE person_id = ? AND type = 'death'", (a,))[0]["id"]
        self.ok(self.put(f"/api/events/{death}/tithi", {"masa": 1, "paksha": "shukla", "tithi": 5}))
        set_app_settings(feature_tithi=False)                    # the admin turned it off on purpose
        self.assertFalse(self.migrate()["tithi"])
        self.assertFalse(self.ok(self.get("/api/me"))["features"]["tithi"])

    def test_old_map_and_inbox_settings_carry_over(self):
        now = config.now_iso()
        sql("INSERT INTO app_settings (key, value, updated_at) VALUES ('enable_map', 'true', ?), ('inbox_enabled', 'false', ?)",
            (now, now))
        st = self.migrate()
        self.assertTrue(st["map"])
        self.assertFalse(st["inbox"])
        keys = {r["key"] for r in sql("SELECT key FROM app_settings")}
        self.assertFalse(keys & {"enable_map", "inbox_enabled"})

    def test_restored_backup_keeps_its_modules(self):
        # a backup from an install that used tithis and Telugu names, taken before switches existed
        set_app_settings(feature_tithi=True, feature_kin_names=True)
        a = self.person("Asha", "Sharma")
        self.ok(self.patch(f"/api/people/{a}", {"death": {"date": {"y": 2020, "m": 3, "d": 4}}}))
        death = sql("SELECT id FROM events WHERE person_id = ? AND type = 'death'", (a,))[0]["id"]
        self.ok(self.put(f"/api/events/{death}/tithi", {"masa": 1, "paksha": "shukla", "tithi": 5}))
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        r = self.get("/api/admin-storage-download-db", user=ADMIN)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(z.read("family.db"))
        c = sqlite3.connect(f.name)
        c.execute("DELETE FROM app_settings WHERE key LIKE 'feature_%'")
        c.commit()
        c.close()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as nz:
            with open(f.name, "rb") as fh:
                nz.writestr("family.db", fh.read())
            nz.writestr("backup.json", z.read("backup.json"))
        os.remove(f.name)
        # restore it onto a new install (both switches off there)
        set_app_settings(feature_tithi=False, feature_kin_names=False)
        self.ok(self.post("/api/admin-storage-import-db", files={"file": ("b.zip", out.getvalue(), "application/zip")}, user=ADMIN))
        st = self.ok(self.get("/api/me"))["features"]
        self.assertTrue(st["tithi"] and st["kin_names"])
        self.assertFalse(st["map"] or st["ceremonies"] or st["script_names"])
        self.assertEqual(self.ok(self.get("/api/me"))["kinLangEffective"], "te")


class OffIsHidden(ApiTestCase):
    """Every switch on (the test base), then each module is switched off and back on."""

    def assertOff(self, resp, name):
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(resp.json()["featureOff"], name)

    def photo(self, pid=None):
        data = {"personId": pid} if pid else None
        return self.ok(self.post("/api/media", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}, data=data), 201)["media"]["id"]

    def test_kin_names(self):
        dad, uncle, me = self.person("Dad", gender="male", born=1960), self.person("Uncle", gender="male", born=1965), self.person("Me")
        gpa = self.person("Grandpa", gender="male")
        self.family(gpa, None, [dad, uncle])
        self.family(dad, None, [me])
        self.set_me(me)
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        self.assertEqual(self.detail(uncle)["relationshipToMe"]["label"], "Babai")
        off(kin_names=False)
        self.assertEqual(self.detail(uncle)["relationshipToMe"]["label"], "uncle")
        self.assertOff(self.get("/api/kin-terms?lang=te"), "kin_names")
        self.assertOff(self.put("/api/me/kin-lang", {"kinLang": "hi"}), "kin_names")
        me_ = self.ok(self.get("/api/me"))
        self.assertEqual((me_["kinLang"], me_["kinLangEffective"], me_["kinLangApp"]), ("te", "en", "en"))
        off(kin_names=True)
        self.assertEqual(self.detail(uncle)["relationshipToMe"]["label"], "Babai")       # the choice was kept

    def test_script_names(self):
        a = self.person("Lakshmi", "Sharma", given_local="లక్ష్మి", surname_local="శర్మ")
        off(script_names=False)
        d = self.detail(a)
        self.assertEqual((d["nameLocal"], d["givenLocal"], d["surnameLocal"]), (None, None, None))
        card = self.ok(self.get("/api/people"))["items"][0]
        self.assertIsNone(card["nameLocal"])
        self.assertEqual(self.ok(self.get("/api/people?q=లక్ష్మి"))["total"], 0)
        self.assertOff(self.post("/api/people", {"given_names": "B", "given_local": "బి"}), "script_names")
        self.assertOff(self.patch(f"/api/people/{a}", {"given_local": "x"}), "script_names")
        self.assertOff(self.put("/api/me/name-display", {"nameDisplay": "both"}), "script_names")
        self.ok(self.patch(f"/api/people/{a}", {"nickname": "Lucky"}))                  # the form without script fields
        off(script_names=True)
        self.assertEqual(self.detail(a)["nameLocal"], "లక్ష్మి శర్మ")                    # never deleted

    def test_tithi_and_milestones(self):
        a = self.person("Seetha", "Sharma", born={"d": 5, "m": 3, "y": 1930}, died={"d": 2, "m": 10, "y": 2024})
        death = sql("SELECT id FROM events WHERE person_id = ? AND type = 'death'", (a,))[0]["id"]
        self.ok(self.put(f"/api/events/{death}/tithi", {"masa": 7, "paksha": "krishna", "tithi": 10}))
        self.assertTrue(self.detail(a)["tithi"]["death"])
        off(tithi=False)
        self.assertEqual(self.detail(a)["tithi"], {})
        for r in (self.get("/api/tithi/dates?year=2026"), self.get(f"/api/events/{death}/tithi/options"),
                  self.put(f"/api/events/{death}/tithi", {"masa": 1, "paksha": "shukla", "tithi": 1})):
            self.assertOff(r, "tithi")
        up = self.ok(self.get("/api/upcoming?days=366&scope=all&remembrance=true&tithi=true"))["items"]
        self.assertFalse(any(i["kind"] == "tithi" for i in up))
        off(tithi=True)
        self.assertEqual(self.detail(a)["tithi"]["death"]["tithi"], 10)
        up = self.ok(self.get("/api/upcoming?days=366&scope=all&remembrance=true&tithi=true"))["items"]
        self.assertTrue(any(i["kind"] == "tithi" for i in up))
        off(milestones=False)
        self.assertOff(self.get("/api/milestones"), "milestones")
        off(milestones=True)
        self.ok(self.get("/api/milestones"))

    def test_milestones_and_full_moons_follow_their_switches(self):
        with mock.patch.object(config, "today", return_value=date(2026, 9, 1)):
            self.person("Ravi", "K", born={"d": 15, "m": 10, "y": 1966})             # 60 on 15 Oct
            items = self.ok(self.get("/api/upcoming?days=60&scope=all"))["items"]
            self.assertEqual([i.get("milestone") for i in items], ["60th birthday"])
            off(milestones=False)
            items = self.ok(self.get("/api/upcoming?days=60&scope=all"))["items"]
            self.assertEqual([i.get("milestone") for i in items], [None])
            off(milestones=True)
        from app import db as dbm, graph, milestones
        with dbm.get_conn() as conn:
            g = graph.get(conn)
            with mock.patch.object(milestones, "rows", return_value=[{"kind": "full_moons", "value": 1000, "label_en": "M",
                                                                        "leads": [0]}]):
                with mock.patch("app.panchang.full_moon_count_date", return_value=date(2026, 9, 2)) as fm:
                    off(tithi=False)
                    milestones.upcoming(conn, g, date(2046, 1, 1), 366 * 30)
                    fm.assert_not_called()                     # the lunar calendar isn't touched while off

    def test_ceremonies(self):
        a = self.person("Arjun", "Sharma")
        e = self.ok(self.post("/api/events", {"personId": a, "type": "upanayanam", "date": {"y": 1995}}), 201)
        eid = (e.get("event") or e)["id"] if isinstance(e, dict) else None
        off(ceremonies=False)
        self.assertEqual([x["type"] for x in self.detail(a)["events"]], [])
        self.assertFalse(any(i.get("type") == "upanayanam" for i in self.ok(self.get(f"/api/people/{a}/timeline"))["items"]))
        self.assertOff(self.post("/api/events", {"personId": a, "type": "seemantham"}), "ceremonies")
        if eid:
            self.assertOff(self.patch(f"/api/events/{eid}", {"title": "x"}), "ceremonies")
        self.ok(self.post("/api/events", {"personId": a, "type": "education", "title": "School"}), 201)
        off(ceremonies=True)
        self.assertEqual(sorted(x["type"] for x in self.detail(a)["events"]), ["education", "upanayanam"])

    def test_reminders_and_the_daily_run(self):
        a = self.person("Asha")
        off(reminders=False)
        self.assertOff(self.get("/api/reminders/prefs"), "reminders")
        self.assertOff(self.patch(f"/api/people/{a}", {"remind": True}), "reminders")
        self.assertOff(self.get("/api/people?remind=on"), "reminders")
        with mock.patch.object(reminders, "due_digests") as due:
            self.assertEqual(reminders.run_digest_pass_blocking(sender=lambda *a: True), 0)
            due.assert_not_called()                                  # the background job skips it
        off(reminders=True)
        with mock.patch.object(reminders, "due_digests", return_value=[]) as due:
            reminders.run_digest_pass_blocking(sender=lambda *a: True)
            due.assert_called_once()
        self.ok(self.patch(f"/api/people/{a}", {"remind": True}))

    def test_map_background_lookups(self):
        a = self.person("Asha")
        self.ok(self.post("/api/events", {"personId": a, "type": "residence", "place": "Springfield"}), 201)
        off(map=False)
        with mock.patch.object(geocode, "geocode_blocking", return_value=(1.0, 2.0)) as g:
            self.assertFalse(geocode.step_blocking())
            g.assert_not_called()
        self.assertOff(self.get("/api/map/points"), "map")
        self.assertFalse(self.ok(self.get("/api/me"))["mapEnabled"])
        off(map=True)
        with mock.patch.object(geocode, "geocode_blocking", return_value=(1.0, 2.0)), \
                mock.patch.object(geocode, "MIN_INTERVAL", 0):
            self.assertTrue(geocode.step_blocking())
        self.assertEqual(len(self.ok(self.get("/api/map/points"))["points"]), 1)

    def test_inbox_scan_skips_and_queue_hides(self):
        path = os.path.join(inbox.inbox_dir(), "a.jpg")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(image_bytes())
        t = time.time() - 120
        os.utime(path, (t, t))
        off(inbox=False)
        self.assertEqual(inbox.scan_blocking()["imported"], 0)
        self.assertTrue(os.path.exists(path))
        for r in (self.get("/api/inbox/status"), self.post("/api/inbox/scan"), self.get("/api/media?unsorted=true")):
            self.assertOff(r, "inbox")
        off(inbox=True)
        self.assertEqual(inbox.scan_blocking()["imported"], 1)
        self.assertEqual(self.ok(self.get("/api/inbox/status"))["unsorted"], 1)

    def test_custom_fields_sources_contacts(self):
        a = self.person("Asha", "Sharma", born=1990)
        f = self.ok(self.post("/api/custom-fields", {"label": "Village", "kind": "text", "appliesTo": "person"}, user=ADMIN), 201)
        fid = (f.get("field") or f)["id"]
        self.ok(self.put(f"/api/people/{a}/custom", {fid: "Hilltop"}))
        s = self.ok(self.post("/api/sources", {"title": "Birth certificate", "kind": "certificate"}), 201)
        sid = (s.get("source") or s)["id"]
        self.ok(self.post("/api/citations", {"sourceId": sid, "personId": a}), 201)
        self.ok(self.post(f"/api/people/{a}/contacts", {"kind": "email", "value": "asha@example.org"}), 201)
        d = self.detail(a)
        self.assertTrue(d["custom"] and d["citations"] and d["contacts"])
        off(custom_fields=False, sources=False, contacts=False)
        d = self.detail(a)
        self.assertEqual((d["custom"], d["citations"], d["contacts"]), ([], [], []))
        self.assertOff(self.get("/api/custom-fields"), "custom_fields")
        self.assertOff(self.get(f"/api/people?field={fid}&value=Hilltop"), "custom_fields")
        self.assertEqual(self.ok(self.get("/api/people?q=hilltop"))["total"], 0)
        self.assertOff(self.get("/api/sources"), "sources")
        self.assertOff(self.get("/api/contacts"), "contacts")
        self.assertOff(self.get(f"/api/contacts.vcf?ids={a}"), "contacts")
        # exports leave them out, even when the saved choices still ask for them
        with db.get_conn() as conn:
            v = export_view.build(conn, export_view.ExportOptions(living="full", contacts=True, sources=True,
                                                                  customFields={fid: True}), None)
        self.assertEqual((v.people[a]["custom"], v.people[a]["contacts"], v.people[a]["sources"]), ([], [], []))
        off(custom_fields=True, sources=True, contacts=True)
        with db.get_conn() as conn:
            v = export_view.build(conn, export_view.ExportOptions(living="full", contacts=True, sources=True,
                                                                  customFields={fid: True}), None)
        self.assertEqual(v.people[a]["custom"], [{"label": "Village", "value": "Hilltop"}])
        self.assertTrue(v.people[a]["contacts"] and v.people[a]["sources"])
        d = self.detail(a)
        self.assertTrue(d["custom"] and d["citations"] and d["contacts"])

    def test_ceremony_events_stay_out_of_exports(self):
        a = self.person("Arjun", "Sharma", born=1950, died=2000)
        self.ok(self.post("/api/events", {"personId": a, "type": "upanayanam", "date": {"y": 1958}}), 201)
        off(ceremonies=False)
        with db.get_conn() as conn:
            v = export_view.build(conn, export_view.ExportOptions(), None)
        self.assertNotIn("upanayanam", [e["type"] for e in v.people[a]["events"]])
        off(ceremonies=True)
        with db.get_conn() as conn:
            v = export_view.build(conn, export_view.ExportOptions(), None)
        self.assertIn("upanayanam", [e["type"] for e in v.people[a]["events"]])

    def test_duplicates_quiz_printing_export(self):
        a = self.person("Asha")
        off(duplicates=False, quiz=False, printing=False, export=False)
        self.assertOff(self.get("/api/duplicates"), "duplicates")
        self.assertOff(self.post("/api/quiz/next", {"playerId": a, "mode": "who", "scope": "all"}), "quiz")
        self.assertOff(self.post("/api/export/print", {"format": "site", "options": {}}), "printing")
        self.assertOff(self.post("/api/export/preview", {"format": "site", "options": {}}), "export")
        self.assertOff(self.get("/api/export/presets"), "export")
        off(duplicates=True, quiz=True, printing=True, export=True)
        self.ok(self.get("/api/duplicates"))
        self.ok(self.post("/api/export/print", {"format": "site", "options": {}}))
        self.ok(self.post("/api/export/preview", {"format": "site", "options": {}}))

    def test_quiz_off_releases_a_kids_mode_device(self):
        kid = self.person("Kid")
        mid = self.photo(kid)
        self.ok(self.put(f"/api/people/{kid}/photo", {"mediaId": mid}))
        self.ok(self.post("/api/kidmode/start", {"playerId": kid, "modes": ["who"], "scope": "all"}, headers=DEV))
        self.assertEqual(self.get("/api/people", headers=DEV).status_code, 423)
        off(quiz=False)
        self.ok(self.get("/api/people", headers=DEV))
        off(quiz=True)
        self.assertEqual(self.get("/api/people", headers=DEV).status_code, 423)       # the session was kept

    def test_photo_tagging_and_fixes(self):
        a = self.person("Asha")
        mid = self.photo(a)
        self.ok(self.post(f"/api/media/{mid}/regions", {"personId": a, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}), 201)
        self.ok(self.put(f"/api/media/{mid}/edit", {"edit": {"rotate": 90}}))
        off(photo_tagging=False, photo_fixes=False)
        item = self.ok(self.get(f"/api/media/{mid}"))
        self.assertEqual(item["regions"], [])
        self.assertTrue(item["edit"])                                 # a fixed photo keeps its fixed look
        self.assertOff(self.post(f"/api/media/{mid}/regions", {"personId": a, "x": 0, "y": 0, "w": 0.2, "h": 0.2}), "photo_tagging")
        self.assertOff(self.put(f"/api/media/{mid}/edit", {"edit": None}), "photo_fixes")
        self.assertEqual(self.get(f"/api/media/{mid}/file?size=256").status_code, 200)
        off(photo_tagging=True, photo_fixes=True)
        self.assertEqual(len(self.ok(self.get(f"/api/media/{mid}"))["regions"]), 1)

    def test_sides(self):
        dad, me = self.person("Dad", gender="male"), self.person("Me")
        self.family(dad, None, [me])
        self.set_me(me)
        self.assertIn("side", self.ok(self.get(f"/api/tree?focus={me}&sides=true"))["descendants"]["p"])
        off(sides=False)
        self.assertOff(self.get("/api/people?side=paternal"), "sides")
        self.assertNotIn("side", self.ok(self.get(f"/api/tree?focus={me}&sides=true"))["descendants"]["p"])
        self.assertIsNone(self.ok(self.get("/api/tree/all?sides=true"))["sideAnchor"])
        off(sides=True)
        self.ok(self.get("/api/people?side=paternal"))


class FirstRun(ApiTestCase):
    """No admin yet: everyone is told how to become one; nobody is promoted."""

    def test_no_admin_flag(self):
        config.ADMIN_NAMES = set()                       # restored by the base class
        w = self.ok(self.get("/api/whoami"))
        self.assertTrue(w["noAdmin"])
        self.assertEqual((w["adminOption"], w["haUsername"], w["isAdmin"]), ("admin_users", "alice", False))
        self.assertTrue(self.ok(self.get("/api/me"))["noAdmin"])
        self.assertEqual(self.get("/api/admin/settings").status_code, 403)          # the first visitor isn't promoted
        config.ADMIN_NAMES = {"admin"}
        self.assertFalse(self.ok(self.get("/api/whoami"))["noAdmin"])

    def test_banner_in_the_page(self):
        with open(os.path.join(_env.ROOT, "app", "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(_env.ROOT, "app", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('id="noAdminBanner"', html)
        self.assertIn("No admin yet — add your Home Assistant user name (", js)
        self.assertIn('go("whoami")', js[js.index("function showNoAdminBanner"):js.index("async function init()")])
        self.assertIn("showNoAdminBanner(state.me)", js)


class FrontEnd(ApiTestCase):
    """The page hides what's off: the Features section and data-feature nav items."""

    def test_page_knows_the_switches(self):
        with open(os.path.join(_env.ROOT, "app", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(_env.ROOT, "app", "static", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('data-feature="map"', html)
        self.assertIn('data-feature="export"', html)
        self.assertIn('id: "featuresCard"', js)
        for name in GENERAL + OFF_FOR_NEW:
            self.assertIn(f'feat("{name}")', js, name)


if __name__ == "__main__":
    import unittest
    unittest.main()
