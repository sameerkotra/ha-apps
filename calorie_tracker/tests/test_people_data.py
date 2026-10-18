"""One person's data — export, import, delete (Admin → Users → ⋯ Data; spec/SPEC.md §6a)."""
import _env  # noqa: F401  (must be first)

import io
import json
import zipfile
from unittest import mock

from app import config, db, ha_sync
from app.common import person_data as pd
from app.routers import people_data
from test_app import ADMIN, ALICE, BOB, ApiBase, FakeHA, hdr

BASE = "/api/admin/people"


def counts_of(summary_or_preview) -> dict:
    return {c["key"]: c["count"] for c in summary_or_preview["counts"]}


def rezip(data: bytes, change) -> bytes:
    """A copy of the zip with change(name, bytes) → bytes for each member (the manifest left as it was)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info.filename, change(info.filename, zin.read(info.filename)))
    return out.getvalue()


def resign(data: bytes, change_manifest=None, change_table=None) -> bytes:
    """A copy with its checksums made to match again (a hand-made file, not a tampered one)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest = json.loads(z.read("manifest.json"))
        members = {n: z.read(n) for n in z.namelist() if n != "manifest.json"}
    if change_table:
        for n in list(members):
            if n.startswith("data/"):
                rows = json.loads(members[n])
                members[n] = json.dumps(change_table(n[5:-5], rows)).encode()
    import hashlib
    manifest["sha256"] = {n: hashlib.sha256(b).hexdigest() for n, b in members.items()}
    if change_manifest:
        change_manifest(manifest)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        for n, b in members.items():
            z.writestr(n, b)
    return out.getvalue()


class PersonDataBase(ApiBase):
    def setUp(self):
        super().setUp()
        people_data.UPLOADS._items.clear()
        # Alice: a saved food, entries (one from it, one without calories, two equal ones), symptoms, weights, goals
        self.food = self.c.post("/api/saved-foods", json={"name": "Dal", "calories": 300, "tags": ["lentils"]},
                                headers=ALICE).json()
        self.log(ALICE, "Dal", date="2026-09-20", saved_food_id=self.food["id"], meal_type="lunch", eaten_at="12:30")
        self.log(ALICE, "Tea", date="2026-09-20", eaten_at="08:00")
        self.log(ALICE, "Tea", date="2026-09-20", eaten_at="08:00")       # the same twice, on purpose
        self.c.post("/api/logs", json={"date": "2026-09-21", "food_name": "Apple", "eaten_at": "10:00"}, headers=ALICE)
        for t in ("09:00", "21:00"):
            self.assertEqual(self.c.post("/api/symptoms", json={"date": "2026-09-20", "time": t, "symptom": "Bloating",
                                                                "severity": 2}, headers=ALICE).status_code, 200)
        for d, kg in (("2026-09-01", 70.5), ("2026-09-15", 70.1)):
            self.c.post("/api/weight", json={"date": d, "weight_kg": kg}, headers=ALICE)
        self.c.put("/api/goals", json={"calorie_goal": 1800, "target_weight_kg": 68}, headers=ALICE)
        # Bob has his own
        self.log(BOB, "Bob's toast", date="2026-09-20")
        self.c.post("/api/symptoms", json={"date": "2026-09-20", "time": "09:00", "symptom": "Bloating"}, headers=BOB)

    # ---- helpers ----
    def summary(self, uid="u_alice", h=ADMIN):
        r = self.c.get(f"{BASE}/{uid}/data", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def export(self, uid="u_alice") -> bytes:
        r = self.c.post(f"{BASE}/{uid}/data/export", json={"options": {}}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertIn("attachment", r.headers["content-disposition"])
        return r.content

    def upload(self, data: bytes, expect=200):
        r = self.c.post(f"{BASE}/data/import", files={"file": ("x.zip", data, "application/zip")}, headers=ADMIN)
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()

    def do_import(self, token, uid="u_alice", expect=200, **body):
        r = self.c.post(f"{BASE}/{uid}/data/import/{token}", json={"mode": "add", **body}, headers=ADMIN)
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()

    def delete(self, uid="u_alice", name="Alice", forget=False, expect=200):
        r = self.c.post(f"{BASE}/{uid}/data/delete", json={"confirm": name, "forget": forget, "choices": {}},
                        headers=ADMIN)
        self.assertEqual(r.status_code, expect, r.text)
        return r.json()

    def rows(self, sql, *args):
        with db.get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, args)]


class Summary(PersonDataBase):
    def test_counts_and_days(self):
        s = self.summary()
        self.assertEqual(s["person"], {"id": "u_alice", "name": "Alice"})
        self.assertEqual(counts_of(s), {"food_logs": 4, "symptom_logs": 2, "weight_logs": 2, "saved_foods": 1,
                                        "days": 4})
        days = next(c for c in s["counts"] if c["key"] == "days")
        self.assertEqual(days["detail"], "2026-09-01 to 2026-09-21")
        self.assertIn("health data", s["exportNote"])
        self.assertTrue(s["cannotReach"])
        self.assertEqual(self.c.get(f"{BASE}/nobody/data", headers=ADMIN).status_code, 404)


class Export(PersonDataBase):
    def test_the_file(self):
        data = self.export()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            m = json.loads(z.read("manifest.json"))
            self.assertEqual((m["app"], m["appVersion"], m["schema"]), ("calorie_tracker", config.APP_VERSION,
                                                                         people_data.SCHEMA))
            self.assertEqual(m["person"], {"id": "u_alice", "name": "Alice"})
            self.assertEqual(m["exportedBy"], "adminy")
            self.assertEqual(m["counts"]["food_logs"], 4)
            foods = json.loads(z.read("data/food_logs.json"))
            names = set(z.namelist())
            text = b"".join(z.read(n) for n in names)
        self.assertNotIn("data/ai_usage.json", names)
        self.assertNotIn("data/app_settings.json", names)
        self.assertNotIn(b"Bob", text)                         # nobody else's rows
        self.assertNotIn(b"user_id", text)
        apple = next(f for f in foods if f["food_name"] == "Apple")
        self.assertEqual(apple["no_calories"], 1)
        self.assertEqual(next(f for f in foods if f["food_name"] == "Dal")["saved_food_id"], self.food["id"])

    def test_the_ai_key_is_never_in_it(self):
        self.c.put("/api/admin/settings", json={"ai_provider": "anthropic", "ai_api_key": "sk-secret-123"},
                   headers=ADMIN)
        self.assertNotIn(b"sk-secret-123", zipfile.ZipFile(io.BytesIO(self.export())).read("manifest.json"))
        with zipfile.ZipFile(io.BytesIO(self.export())) as z:
            self.assertFalse(any(b"sk-secret" in z.read(n) for n in z.namelist()))

    def test_file_name(self):
        r = self.c.post(f"{BASE}/u_alice/data/export", headers=ADMIN)
        self.assertRegex(r.headers["content-disposition"], r'filename="calorie-tracker-alice-\d{8}-\d{4}\.zip"')

    def test_jobs_are_never_used(self):
        self.assertEqual(self.c.get(f"{BASE}/data/jobs/nope", headers=ADMIN).status_code, 404)
        self.assertEqual(self.c.get(f"{BASE}/data/jobs/nope/file", headers=ADMIN).status_code, 404)


class RoundTrip(PersonDataBase):
    def test_export_delete_import_gives_the_same_back(self):
        before = counts_of(self.summary())
        goals_before = self.c.get("/api/goals", headers=ALICE).json()
        data = self.export()
        self.delete()
        self.assertEqual(counts_of(self.summary())["food_logs"], 0)
        up = self.upload(data)
        self.assertEqual(up["preview"]["target"], "u_alice")
        self.assertEqual(up["preview"]["person"]["name"], "Alice")
        self.assertIn("1 food entry has no calories.", up["preview"]["warnings"])
        res = self.do_import(up["token"])
        self.assertEqual(res["skipped"], 0)
        self.assertIn("Imported 4 food entries", res["message"])
        self.assertEqual(counts_of(self.summary()), before)
        self.assertEqual(self.c.get("/api/goals", headers=ALICE).json(), goals_before)
        # the saved food is linked to the new saved food, with a new id
        logs = self.c.get("/api/logs?date=2026-09-20", headers=ALICE).json()
        saved = self.c.get("/api/saved-foods", headers=ALICE).json()
        dal = next(f for f in logs if f["food_name"] == "Dal")
        self.assertEqual(dal["saved_food_id"], saved[0]["id"])
        self.assertNotEqual(saved[0]["id"], self.food["id"])
        self.assertEqual(saved[0]["tags"], ["lentils"])
        apple = self.c.get("/api/logs?date=2026-09-21", headers=ALICE).json()[0]
        self.assertIsNone(apple["calories"])
        # the upload is gone after the import
        self.do_import(up["token"], expect=404)

    def test_import_into_another_person(self):
        data = self.export()
        bob_before = counts_of(self.summary("u_bob"))
        up = self.upload(data)
        self.do_import(up["token"], uid="u_bob")
        after = counts_of(self.summary("u_bob"))
        self.assertEqual(after["food_logs"], bob_before["food_logs"] + 4)
        self.assertEqual(after["saved_foods"], 1)
        # Bob's Bloating at 09:00 on the 20th was already there: skipped
        self.assertEqual(after["symptom_logs"], 2)
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)            # Alice's untouched
        # Bob's goals were never set: the file's are taken
        self.assertEqual(self.c.get("/api/goals", headers=BOB).json()["calorie_goal"], 1800)
        # every saved_food_id points at Bob's own saved food
        ids = {r["saved_food_id"] for r in self.rows("SELECT saved_food_id FROM food_logs WHERE user_id = 'u_bob'")}
        own = {r["id"] for r in self.rows("SELECT id FROM saved_foods WHERE user_id = 'u_bob'")}
        self.assertTrue(ids - {None} <= own and ids - {None})


class Modes(PersonDataBase):
    def test_add_skips_duplicates(self):
        up = self.upload(self.export())
        res = self.do_import(up["token"])
        # everything was already there: 4 food, 2 symptoms, 2 weights, 1 saved food
        self.assertEqual(res["skipped"], 9)
        self.assertEqual(res["message"], "Nothing new to import; 9 already there were skipped.")
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)

    def test_add_keeps_their_goals(self):
        data = self.export()
        self.c.put("/api/goals", json={"calorie_goal": 2500}, headers=ALICE)
        self.do_import(self.upload(data)["token"])
        self.assertEqual(self.c.get("/api/goals", headers=ALICE).json()["calorie_goal"], 2500)

    def test_replace_needs_the_name_and_replaces(self):
        data = self.export()
        self.log(ALICE, "Extra", date="2026-09-22")
        self.c.put("/api/goals", json={"calorie_goal": 2500}, headers=ALICE)
        token = self.upload(data)["token"]
        self.do_import(token, mode="replace", confirm="Bob", expect=409)
        self.do_import(token, mode="replace", expect=409)
        res = self.do_import(token, mode="replace", confirm="  alice ")
        self.assertEqual(res["skipped"], 0)
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)
        self.assertEqual(self.c.get("/api/logs?date=2026-09-22", headers=ALICE).json(), [])
        self.assertEqual(self.c.get("/api/goals", headers=ALICE).json()["calorie_goal"], 1800)

    def test_the_token_is_only_for_the_admin_who_uploaded(self):
        token = self.upload(self.export())["token"]
        other_admin = hdr("u_admin2", "Other", "other")
        config.ADMIN_USERS.add("other")
        try:
            self.assertEqual(self.c.get("/api/me", headers=other_admin).status_code, 200)
            r = self.c.post(f"{BASE}/u_alice/data/import/{token}", json={"mode": "add"}, headers=other_admin)
            self.assertEqual(r.status_code, 404)
        finally:
            config.ADMIN_USERS.discard("other")


class Delete(PersonDataBase):
    def test_wrong_name_deletes_nothing(self):
        self.delete(name="Bob", expect=409)
        self.delete(name="", expect=409)
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)

    def test_keep_them(self):
        with db.get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO ai_usage (user_id, day, requests) VALUES ('u_alice', '2026-09-20', 3)")
            conn.execute("INSERT OR REPLACE INTO ai_usage (user_id, day, requests) VALUES ('u_bob', '2026-09-20', 2)")
        res = self.delete()
        self.assertFalse(res["forgotten"])
        self.assertEqual(res["deleted"]["food_logs"], 4)
        self.assertIn("Alice stays in the list", res["message"])
        users = {u["id"]: u for u in self.c.get("/api/users", headers=ADMIN).json()}
        self.assertIn("u_alice", users)
        self.assertEqual(counts_of(self.summary()), {"food_logs": 0, "symptom_logs": 0, "weight_logs": 0,
                                                    "saved_foods": 0, "days": 0})
        self.assertEqual(self.c.get("/api/goals", headers=ALICE).json()["calorie_goal"], 2000)
        self.assertEqual(self.rows("SELECT user_id FROM ai_usage"), [{"user_id": "u_bob"}])
        self.log(ALICE, "New start")                      # she carries on as if new

    def test_forget_them(self):
        res = self.delete(forget=True)
        self.assertTrue(res["forgotten"])
        self.assertNotIn("u_alice", {u["id"] for u in self.c.get("/api/users", headers=ADMIN).json()})
        self.assertEqual(self.c.get(f"{BASE}/u_alice/data", headers=ADMIN).status_code, 404)
        for t in ("goals", "food_logs", "symptom_logs", "weight_logs", "saved_foods"):
            self.assertEqual(self.rows(f"SELECT 1 FROM {t} WHERE user_id = 'u_alice'"), [], t)
        # she opens the app again with her Home Assistant login: a new, empty person (and her writes work at once)
        self.log(ALICE, "Back again")
        self.assertIn("u_alice", {u["id"] for u in self.c.get("/api/users", headers=ADMIN).json()})

    def test_others_untouched(self):
        def bob():
            return (self.rows("SELECT * FROM food_logs WHERE user_id = 'u_bob'"),
                    self.rows("SELECT * FROM symptom_logs WHERE user_id = 'u_bob'"),
                    self.rows("SELECT * FROM goals WHERE user_id = 'u_bob'"),
                    self.rows("SELECT id, name, enabled FROM users WHERE id != 'u_alice'"))
        before = bob()
        settings_before = self.rows("SELECT * FROM app_settings")
        self.delete(forget=True)
        self.assertEqual(bob(), before)
        self.assertEqual(self.rows("SELECT * FROM app_settings"), settings_before)
        # the household's lists still work for everyone else
        self.assertEqual(self.c.get("/api/symptoms/choices", headers=BOB).status_code, 200)

    def test_one_at_a_time(self):
        pd._lock.acquire()                    # another import or delete is running
        try:
            self.delete(expect=423)
            token = self.upload(self.export())["token"]
            self.do_import(token, expect=423)
        finally:
            pd._lock.release()
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)


class SensorRemoval(PersonDataBase):
    def setUp(self):
        super().setUp()
        self.ha = FakeHA()
        for p in (mock.patch("app.common.ha_client.urllib.request.urlopen", self.ha),
                  mock.patch.object(config, "SUPERVISOR_TOKEN", "test-token")):
            p.start()
            self.addCleanup(p.stop)
        ha_sync._published.clear()

    def test_delete_removes_their_sensor(self):
        self.delete()
        self.assertEqual(self.ha.of("DELETE"), [("sensor.calorie_tracker_alice_daily_calories", None)])

    def test_the_slug_is_the_one_before_the_delete(self):
        # a second "Alice" (later) has a sensor with an ending; deleting her removes that one, not the first Alice's
        self.assertEqual(self.c.get("/api/me", headers=hdr("u_alice2", "Alice", "alice2")).status_code, 200)
        with db.get_conn() as conn:
            from app.auth import sensor_slugs
            slug = sensor_slugs(conn)["u_alice2"]
        self.ha.calls.clear()
        self.delete(uid="u_alice2")
        self.assertEqual(self.ha.of("DELETE"), [(ha_sync.entity_id_for(slug), None)])
        self.assertNotEqual(slug, "alice")


class Refused(PersonDataBase):
    def test_a_newer_schema(self):
        data = resign(self.export(), change_manifest=lambda m: m.update(schema=people_data.SCHEMA + 1))
        r = self.upload(data, expect=400)
        self.assertIn("newer version", r["detail"])

    def test_a_tampered_file(self):
        def change(name, b):
            return b.replace(b"Tea", b"Tex") if name == "data/food_logs.json" else b
        r = self.upload(rezip(self.export(), change), expect=400)
        self.assertIn("changed", r["detail"])

    def test_another_apps_file_and_junk(self):
        self.upload(resign(self.export(), change_manifest=lambda m: m.update(app="household_arcade")), expect=400)
        self.upload(b"not a zip", expect=400)

    def test_a_row_the_app_would_refuse(self):
        def bad(table, rows):
            if table == "food_logs":
                rows[1]["calories"] = 10 ** 9
            return rows
        r = self.upload(resign(self.export(), change_table=bad), expect=422)
        self.assertIn("food_logs row 2", r["detail"])
        self.assertIn("calories", r["detail"])

    def test_unknown_columns_are_dropped_and_ids_ignored(self):
        def odd(table, rows):
            for r in rows:
                r["user_id"] = "u_bob"          # the file never decides whose a row is
                r["evil"] = "x"
            return rows
        token = self.upload(resign(self.export(), change_table=odd))["token"]
        self.delete()
        self.do_import(token)
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)
        self.assertEqual(len(self.rows("SELECT 1 FROM food_logs WHERE user_id = 'u_bob'")), 1)

    def test_nothing_half_imported(self):
        data = self.export()
        self.delete()
        token = self.upload(data)["token"]
        original = people_data.load

        def failing(conn, uid, rows, mode):
            original(conn, uid, rows, mode)      # everything inserted, then the transaction fails
            raise RuntimeError("disk full")
        with mock.patch.object(people_data, "load", failing), self.assertRaises(RuntimeError):
            self.c.post(f"{BASE}/u_alice/data/import/{token}", json={"mode": "add"}, headers=ADMIN)
        self.assertEqual(counts_of(self.summary())["food_logs"], 0)


class AdminOnly(PersonDataBase):
    def test_every_route_refuses_non_admins(self):
        token = self.upload(self.export())["token"]
        for h in (ALICE, BOB):
            calls = [
                self.c.get(f"{BASE}/u_alice/data", headers=h),
                self.c.post(f"{BASE}/u_alice/data/export", json={}, headers=h),
                self.c.get(f"{BASE}/data/jobs/x", headers=h),
                self.c.get(f"{BASE}/data/jobs/x/file", headers=h),
                self.c.post(f"{BASE}/data/import", files={"file": ("x.zip", b"x")}, headers=h),
                self.c.post(f"{BASE}/u_alice/data/import/{token}", json={"mode": "add"}, headers=h),
                self.c.post(f"{BASE}/u_alice/data/delete", json={"confirm": "Alice"}, headers=h),
                # acting as an admin isn't a thing: as_user changes nothing
                self.c.get(f"{BASE}/u_alice/data?as_user=u_admin", headers=h),
            ]
            self.assertEqual([r.status_code for r in calls], [403] * len(calls))
        self.assertEqual(counts_of(self.summary())["food_logs"], 4)
