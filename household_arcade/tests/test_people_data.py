"""One person's data (PERSON_DATA_SPEC.md §6): export, import and delete from Admin → Users → ⋯ Data — the same
counts back after export → delete → import, importing into someone else, Add skipping what is already there,
Replace asking for the name, keep vs forget, matches left working for the others (a race and a turn-by-turn match
going on, a finished match showing "Former player", the creator moving on), records passing on and the sensors
removed, newer and tampered files refused, row checks naming the table and row, and admin only everywhere."""
import _env  # noqa: F401  (must be first)

import io
import json
import zipfile

from app import db, games, people_data, progress
from app.common import person_data as pd
from app.routers import people_data as router
from base import ASHA, DEV, KABIR, MEERA, ApiBase

BASE = "/api/admin/people"
GAME, MODE = "racer", "three"          # a race (as in test_together)
STATE = {"snake": [{"x": 5, "y": 5}], "mode": "walls-normal", "score": 120}


def _continue_mode():
    for g in games.GAME_IDS:
        for m in games.continue_modes(g):
            return g, m


class PeopleDataBase(ApiBase):
    def setUp(self):
        super().setUp()
        router.UPLOADS._items.clear()

    def fill(self, h=KABIR):
        """Two scores, a saved game, level progress, notify service — and a finished race with Meera."""
        uid = h["X-Remote-User-Id"]
        self.assertEqual(self.play(100, h=h).status_code, 200)
        self.assertEqual(self.play(150, h=h).status_code, 200)
        sid = self.start(h=h).json()["id"]
        self.clock.advance(seconds=70)
        r = self.post(f"/api/sessions/{sid}/save", {"state": STATE, "stateVersion": 1, "score": 120, "level": 2,
                                                    "seconds": 60}, h)
        self.assertEqual(r.status_code, 200, r.text)
        g, m = _continue_mode()
        with db.get_conn() as conn:
            progress.record(conn, uid, g, m, 2)
        r = self.post(f"/api/admin/users/{uid}/notify", {"service": "notify.mobile_app_test"}, ASHA)
        self.assertEqual(r.status_code, 201, r.text)
        return uid

    def counts(self, uid):
        r = self.get(f"{BASE}/{uid}/data", ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        return {c["key"]: c["count"] for c in r.json()["counts"]}

    def export(self, uid):
        r = self.post(f"{BASE}/{uid}/data/export", {"options": {}}, ASHA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn(".zip", r.headers["content-disposition"])
        return r.content

    def upload(self, content, h=ASHA):
        return self.c.post(f"{BASE}/data/import", files={"file": ("x.zip", content, "application/zip")}, headers=h)

    def import_(self, content, uid, mode="add", confirm=None, options=None):
        r = self.upload(content)
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["token"]
        return self.post(f"{BASE}/{uid}/data/import/{token}", {"mode": mode, "confirm": confirm,
                                                               "options": options or {}}, ASHA)

    def remove(self, uid, confirm, forget=False):
        return self.post(f"{BASE}/{uid}/data/delete", {"confirm": confirm, "forget": forget}, ASHA)

    def rewrite(self, content, change):
        """The zip with a member changed by change(name, bytes) → bytes (checksums left as they were)."""
        src = zipfile.ZipFile(io.BytesIO(content))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for info in src.infolist():
                out.writestr(info.filename, change(info.filename, src.read(info.filename)))
        return buf.getvalue()

    def resign(self, content, change_rows):
        """A properly re-checksummed file whose data was changed (to test the row checks)."""
        with pd.read_package(io.BytesIO(content), app="household_arcade", schema=db.LATEST) as pkg:
            tables = {k: list(v) for k, v in pkg.tables.items()}
            records = dict(pkg.records)
            m = pkg.manifest
        change_rows(tables)
        return pd.bytes_io_package(app="household_arcade", app_version=m["appVersion"], schema=m["schema"],
                                   person=m["person"], exported_by="x", tables=tables, records=records)


class TestRoundTrip(PeopleDataBase):
    def test_export_delete_import_gives_the_same_counts(self):
        uid = self.fill()
        self.put("/api/prefs", {"look": "neon", "handedness": "left"}, KABIR)
        before = self.counts(uid)
        self.assertEqual((before["scores"], before["saved_games"], before["level_progress"], before["user_notify"]),
                         (2, 1, 1, 1))
        content = self.export(uid)
        names = zipfile.ZipFile(io.BytesIO(content)).namelist()
        self.assertIn("data/matches.json", names)
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(content)).read("manifest.json"))
        self.assertEqual((manifest["app"], manifest["schema"], manifest["person"]["id"]),
                         ("household_arcade", db.LATEST, uid))
        r = self.remove(uid, "kabir rao")                        # any case
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(all(v == 0 for v in self.counts(uid).values()))
        self.assertEqual(self.get("/api/prefs", KABIR).json()["look"], None)      # kept, empty
        r = self.import_(content, uid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Imported", r.json()["message"])
        after = self.counts(uid)
        self.assertEqual(after, before)
        prefs = self.get("/api/prefs", KABIR).json()
        self.assertEqual((prefs["look"], prefs["handedness"]), ("neon", "left"))
        # the leaderboard has them again
        rows = self.get("/api/leaderboard?game=snake&mode=walls-normal").json()["rows"]
        self.assertEqual([x["score"] for x in rows], [150])

    def test_import_into_another_person_with_new_ids(self):
        uid = self.fill()
        content = self.export(uid)
        self.get("/api/me", DEV)
        r = self.import_(content, "u_dev")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.counts("u_dev")["scores"], 2)
        self.assertEqual(self.counts(uid)["scores"], 2)           # theirs untouched
        with db.get_conn() as conn:
            k = {r[0] for r in conn.execute("SELECT id FROM play_sessions WHERE user_id = 'u_kabir'")}
            d = {r[0] for r in conn.execute("SELECT id FROM play_sessions WHERE user_id = 'u_dev'")}
            self.assertTrue(d and not (k & d))
            # each imported score points at the imported session, not the original one
            for row in conn.execute("SELECT session_id FROM scores WHERE user_id = 'u_dev'"):
                self.assertIn(row[0], d)

    def test_preview(self):
        uid = self.fill()
        r = self.upload(self.export(uid))
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()["preview"]
        self.assertEqual(p["person"]["id"], uid)
        self.assertEqual(p["target"], uid)
        self.assertTrue(any("leaderboard" in w for w in p["warnings"]))
        self.assertEqual(p["options"][0]["key"], "limits")
        self.assertFalse(p["options"][0]["default"])

    def test_add_skips_duplicates_and_replace_needs_the_name(self):
        uid = self.fill()
        content = self.export(uid)
        r = self.import_(content, uid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreater(r.json()["skipped"], 0)
        self.assertEqual(r.json()["added"]["scores"], 0)
        self.assertEqual(self.counts(uid)["scores"], 2)
        self.play(300)                                            # a new score after the export
        self.assertEqual(self.import_(content, uid, "replace", "wrong").status_code, 409)
        self.assertEqual(self.counts(uid)["scores"], 3)
        r = self.import_(content, uid, "replace", "Kabir Rao")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.counts(uid)["scores"], 2)

    def test_level_progress_the_further_one_wins(self):
        uid = self.fill()
        g, m = _continue_mode()
        content = self.export(uid)
        with db.get_conn() as conn:
            progress.record(conn, uid, g, m, 5)
        self.import_(content, uid)
        with db.get_conn() as conn:
            self.assertEqual(progress.cleared(conn, uid, g, m), 5)

    def test_child_limits_only_when_ticked(self):
        uid = self.make_child(KABIR, minutesSchool=20)
        self.play(100)
        content = self.export(uid)
        self.get("/api/me", DEV)
        self.import_(content, "u_dev")
        self.assertEqual(self.counts("u_dev")["child_limits"], 0)
        self.import_(content, "u_dev", options={"limits": True})
        self.assertEqual(self.counts("u_dev")["child_limits"], 1)
        u = self.get("/api/admin/users/u_dev", ASHA).json()
        self.assertTrue(u["isChild"])
        self.assertEqual(u["limits"]["minutesSchool"], 20)


class TestDelete(PeopleDataBase):
    def test_wrong_name_and_others_untouched(self):
        uid = self.fill()
        self.fill(MEERA)
        meera = self.counts("u_meera")
        self.assertEqual(self.remove(uid, "Kabir").status_code, 409)
        self.assertEqual(self.counts(uid)["scores"], 2)
        self.assertEqual(self.remove(uid, "Kabir Rao").status_code, 200)
        self.assertEqual(self.counts("u_meera"), meera)

    def test_keep_vs_forget(self):
        uid = self.fill()
        self.remove(uid, "Kabir Rao")
        users = {u["id"] for u in self.get("/api/admin/users", ASHA).json()["users"]}
        self.assertIn(uid, users)
        self.play(50)
        r = self.remove(uid, "Kabir Rao", forget=True)
        self.assertEqual(r.status_code, 200, r.text)
        users = {u["id"] for u in self.get("/api/admin/users", ASHA).json()["users"]}
        self.assertNotIn(uid, users)
        self.assertEqual(self.get(f"{BASE}/{uid}/data", ASHA).status_code, 404)
        with db.get_conn() as conn:
            for t in people_data.OWN_TABLES:
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (uid,)).fetchone()[0], 0)

    def test_finished_race_stays_for_the_other_as_former_player(self):
        r = self.post("/api/matches", {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "kind": "race"}, KABIR)
        mid = r.json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        for who, score in ((KABIR, 5), (MEERA, 9)):
            sid = self.post("/api/sessions", {"game": GAME, "matchId": mid}, who).json()["id"]
            self.clock.advance(seconds=60)
            self.post("/api/scores", {"sessionId": sid, "score": score, "level": 1, "seconds": 60}, who)
        self.assertEqual(self.counts("u_kabir")["matches"], 1)
        r = self.remove("u_kabir", "Kabir Rao", forget=True)      # Kabir created it: the match must not go
        self.assertEqual(r.status_code, 200, r.text)
        m = self.get(f"/api/matches/{mid}", MEERA).json()
        self.assertEqual((m["status"], m["winner"], m["createdBy"]), ("done", "u_meera", "u_meera"))
        names = {p["id"]: p["name"] for p in m["players"]}
        self.assertEqual(names["u_kabir"], "Former player")
        r = self.get("/api/against", MEERA)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Former player", r.text)                    # head to head still counts the match

    def test_race_going_on_is_won_by_the_one_left(self):
        r = self.post("/api/matches", {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "kind": "race"}, KABIR)
        mid = r.json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.post("/api/sessions", {"game": GAME, "matchId": mid}, KABIR)
        sid = self.post("/api/sessions", {"game": GAME, "matchId": mid}, MEERA).json()["id"]
        self.assertEqual(self.remove("u_kabir", "Kabir Rao").status_code, 200)
        self.clock.advance(seconds=60)
        self.post("/api/scores", {"sessionId": sid, "score": 3, "level": 1, "seconds": 60}, MEERA)
        m = self.get(f"/api/matches/{mid}", MEERA).json()
        self.assertEqual((m["status"], m["winner"]), ("done", "u_meera"))

    def test_turns_match_going_on_seat_goes_to_the_computer(self):
        # a two-player turn-by-turn game: Kabir's seat goes to the computer and Meera, the one left, wins
        r = self.post("/api/matches", {"game": "tictactoe", "mode": "phones", "opponents": ["u_meera"]}, KABIR)
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["id"]
        self.post(f"/api/matches/{mid}/accept", None, MEERA)
        self.assertEqual(self.remove("u_kabir", "Kabir Rao", forget=True).status_code, 200)
        m = self.get(f"/api/matches/{mid}", MEERA).json()
        # two players: the one left wins, as when a player resigns
        self.assertEqual((m["status"], m["winner"], m["createdBy"]), ("done", "u_meera", "u_meera"))
        kab = next(p for p in m["players"] if p["id"] == "u_kabir")
        self.assertTrue(kab["computer"])

    def test_match_with_nobody_else_is_deleted_and_invite_cancelled(self):
        r = self.post("/api/matches", {"game": GAME, "mode": MODE, "opponents": ["u_meera"], "kind": "race"}, KABIR)
        mid = r.json()["id"]
        self.remove("u_meera", "Meera Rao", forget=True)          # the invited one goes: an invite to nobody
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT status FROM matches WHERE id = ?", (mid,)).fetchone()[0], "cancelled")
        self.remove("u_kabir", "Kabir Rao")                       # then nobody else is in it: it goes
        with db.get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM matches WHERE id = ?", (mid,)).fetchone())

    def test_record_passes_on_and_sensors_removed(self):
        self.settings({"ha_sensors": True})
        self.play(500, h=KABIR)
        self.play(100, h=MEERA)
        from app import ha_sensors
        ha_sensors.push_blocking(force=True)
        self.assertEqual(self.ha.states["sensor.household_arcade_snake_record"]["state"], "500")
        self.assertIn("sensor.household_arcade_kabir_played_today", self.ha.states)
        self.assertEqual(self.remove("u_kabir", "Kabir Rao", forget=True).status_code, 200)
        self.assertEqual(self.ha.states["sensor.household_arcade_snake_record"]["state"], "100")
        self.assertNotIn("sensor.household_arcade_kabir_played_today", self.ha.states)
        self.assertNotIn("binary_sensor.household_arcade_kabir_playing", self.ha.states)


class TestRefused(PeopleDataBase):
    def test_newer_schema_refused(self):
        uid = self.fill()
        content = self.resign(self.export(uid), lambda t: None)
        def bump(name, data):
            if name == "manifest.json":
                m = json.loads(data)
                m["schema"] = db.LATEST + 1
                return json.dumps(m).encode()
            return data
        r = self.upload(self.rewrite(content, bump))
        self.assertEqual(r.status_code, 400)
        self.assertIn("newer", r.json()["detail"])

    def test_tampered_file_refused(self):
        uid = self.fill()
        def change(name, data):
            return data.replace(b"150", b"999") if name == "data/scores.json" else data
        r = self.upload(self.rewrite(self.export(uid), change))
        self.assertEqual(r.status_code, 400)
        self.assertIn("changed", r.json()["detail"])

    def test_bad_row_names_table_and_row(self):
        uid = self.fill()
        def bad(t):
            t["scores"][1]["score"] = 10 ** 9
        r = self.upload(self.resign(self.export(uid), bad))
        self.assertEqual(r.status_code, 422)
        self.assertIn("scores, row 2", r.json()["detail"])

    def test_not_a_zip_and_another_app(self):
        self.assertEqual(self.upload(b"hello").status_code, 400)
        other = pd.bytes_io_package(app="calorie_tracker", app_version="1", schema=1, person={"id": "x"},
                                    exported_by="x", tables={})
        self.assertEqual(self.upload(other).status_code, 400)

    def test_admin_only_everywhere(self):
        uid = self.fill()
        content = self.export(uid)
        token = self.upload(content).json()["token"]
        for h in (KABIR, MEERA):
            self.assertEqual(self.get(f"{BASE}/{uid}/data", h).status_code, 403)
            self.assertEqual(self.post(f"{BASE}/{uid}/data/export", {}, h).status_code, 403)
            self.assertEqual(self.get(f"{BASE}/data/jobs/x", h).status_code, 403)
            self.assertEqual(self.get(f"{BASE}/data/jobs/x/file", h).status_code, 403)
            self.assertEqual(self.upload(content, h).status_code, 403)
            self.assertEqual(self.post(f"{BASE}/{uid}/data/import/{token}", {"mode": "add"}, h).status_code, 403)
            self.assertEqual(self.post(f"{BASE}/{uid}/data/delete", {"confirm": "Kabir Rao"}, h).status_code, 403)
        self.assertEqual(self.counts(uid)["scores"], 2)
        self.assertEqual(self.get(f"{BASE}/data/jobs/x", ASHA).status_code, 404)

    def test_busy(self):
        uid = self.fill()
        with pd.one_at_a_time():
            self.assertEqual(self.remove(uid, "Kabir Rao").status_code, 423)


class TestMigration12(PeopleDataBase):
    def test_seats_outlive_their_person(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db.migrate(conn, upto=11)
        conn.execute("INSERT INTO users (id, name, created_at) VALUES ('a', 'A', 'x'), ('b', 'B', 'x')")
        conn.execute("INSERT INTO matches (id, game, mode, seed, status, created_by, created_at) "
                     "VALUES ('m', 'racer', 'three', 1, 'done', 'a', 'x')")
        conn.execute("INSERT INTO match_players (match_id, user_id, seat, result) VALUES ('m', 'a', 1, 'won'), "
                     "('m', 'b', 2, 'lost')")
        self.assertEqual(db.migrate(conn), db.LATEST)
        self.assertEqual(db.migrate(conn), db.LATEST)              # once only
        self.assertFalse(any(r[2] == "users" for r in conn.execute("PRAGMA foreign_key_list(match_players)")))
        conn.execute("DELETE FROM users WHERE id = 'b'")
        rows = [tuple(r) for r in conn.execute("SELECT user_id, result FROM match_players ORDER BY seat")]
        self.assertEqual(rows, [("a", "won"), ("b", "lost")])
        idx = {r[1] for r in conn.execute("PRAGMA index_list(match_players)")}
        self.assertIn("idx_match_players_user", idx)
