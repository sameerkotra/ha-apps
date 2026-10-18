"""One person's data (app/routers/people_data.py; PERSON_DATA_SPEC.md §2, §8): export, import and delete, on the
same fake household as test_assistant.py.

- export → delete → import gives back the same counts; import into another person; Add skips what is already
  there, Replace needs the name; questions only with the option; the admin's switches never imported;
- delete: keep vs forget, a question still running is stopped, the answer-reuse cache emptied, the others' data and
  the household's shared things (learned phrases, usage totals) untouched;
- refused: a newer schema, a changed file, a row that doesn't fit (its table and row), another import running;
- every endpoint is admin only.
"""
import _env  # noqa: F401  (must be first)

import io
import json
import time
import zipfile
from datetime import timedelta
from unittest import mock

from app import ai_client, briefing, config, db, engine
from app.common import person_data as pd
from app.routers import people_data
from fakes import Model, wait_done
from test_assistant import ADMIN, ALICE, BOB, Household, plan_call


def counts(conn, uid):
    q = conn.execute("SELECT COUNT(*) FROM questions WHERE user_id = ?", (uid,)).fetchone()[0]
    c = conn.execute("SELECT COUNT(*) FROM calls JOIN questions q ON q.id = question_id WHERE q.user_id = ?",
                     (uid,)).fetchone()[0]
    b = conn.execute("SELECT COUNT(*) FROM briefings WHERE user_id = ?", (uid,)).fetchone()[0]
    return q, c, b


class PersonData(Household):
    def setUp(self):
        super().setUp()
        for text in ("What's on my list today?", "And tomorrow?"):
            q = self.ask(text, model=Model(plan_call("todo.tasks", when="today"), '{"answer": "done"}',
                                           "You have 2 tasks."))
            self.assertEqual(q["state"], "done", q)
        self.ask("Hello", who=BOB)
        with db.get_conn() as conn:
            briefing.save(conn, "u_alice", True, "06:45", "weekdays", evening_on=True, evening_time="21:00",
                          speaker="media_player.kitchen", speaker_checked=True)
            self.alice = counts(conn, "u_alice")
            self.bob = counts(conn, "u_bob")
        self.assertEqual(self.alice, (2, 2, 1))

    # ------------------------------------------------------------------------------------------------ helpers
    def summary(self, uid="u_alice", who=ADMIN):
        return self.c.get(f"/api/admin/people/{uid}/data", headers=who)

    def export(self, uid="u_alice", questions=True):
        r = self.c.post(f"/api/admin/people/{uid}/data/export", json={"options": {"questions": questions}},
                        headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertIn("attachment; filename=\"household-assistant-", r.headers["content-disposition"])
        return r.content

    def upload(self, data: bytes, who=ADMIN):
        return self.c.post("/api/admin/people/data/import", files={"file": ("x.zip", data, "application/zip")},
                           headers=who)

    def load(self, data: bytes, uid="u_alice", mode="add", confirm=None):
        r = self.upload(data)
        self.assertEqual(r.status_code, 200, r.text)
        return self.c.post(f"/api/admin/people/{uid}/data/import/{r.json()['token']}",
                           json={"mode": mode, "confirm": confirm, "options": {}}, headers=ADMIN)

    def delete(self, uid="u_alice", confirm="Alice", forget=False):
        return self.c.post(f"/api/admin/people/{uid}/data/delete", json={"confirm": confirm, "forget": forget,
                                                                         "choices": {}}, headers=ADMIN)

    def now(self, uid):
        with db.get_conn() as conn:
            return counts(conn, uid)

    # ------------------------------------------------------------------------------------------------ tests
    def test_summary(self):
        s = self.summary().json()
        got = {c["key"]: c["count"] for c in s["counts"]}
        self.assertEqual(got, {"questions": 2, "calls": 2, "briefings": 2})
        self.assertEqual(s["person"], {"id": "u_alice", "name": "Alice"})
        self.assertEqual([o["key"] for o in s["exportOptions"]], ["questions"])
        self.assertFalse(s["exportOptions"][0]["default"])
        self.assertEqual(self.summary("nobody").status_code, 404)

    def test_export_delete_import_gives_back_the_same(self):
        data = self.export()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            m = json.loads(z.read("manifest.json"))
            names = set(z.namelist())
            self.assertNotIn("ai_api_key", json.dumps([z.read(n).decode() for n in names]))
        self.assertEqual((m["app"], m["schema"], m["appVersion"]), ("household_assistant", people_data.SCHEMA,
                                                                    config.APP_VERSION))
        self.assertEqual(m["counts"], {"users": 1, "briefings": 1, "questions": 2, "calls": 2, "files": 0})
        self.assertNotIn("data/rate_events.json", names)
        r = self.delete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.now("u_alice"), (0, 0, 0))
        r = self.load(data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Imported 2 questions and answers, their briefing settings", r.json()["message"])
        self.assertEqual(self.now("u_alice"), self.alice)
        b = self.c.get("/api/briefing", headers=ALICE).json()
        self.assertEqual((b["on"], b["time"], b["days"], b["eveningOn"], b["eveningTime"], b["speaker"]),
                         (True, "06:45", "weekdays", True, "21:00", "media_player.kitchen"))
        hist = self.c.get("/api/history", headers=ALICE).json()["questions"]
        self.assertEqual([q["text"] for q in hist], ["And tomorrow?", "What's on my list today?"])
        self.assertEqual(hist[0]["shared"][0]["text"], "2 tasks today: Bins, Call plumber.")
        self.assertEqual(self.now("u_bob"), self.bob)

    def test_import_into_another_person_with_new_ids(self):
        data = self.export()
        with db.get_conn() as conn:
            old = {r[0] for r in conn.execute("SELECT id FROM questions WHERE user_id = 'u_alice'")}
        with db.get_conn() as conn:                       # Alice is a child here: Bob doesn't become one
            conn.execute("UPDATE users SET is_child = 1, enabled = 0 WHERE id = 'u_alice'")
        data = self.export()
        r = self.upload(data)
        self.assertEqual(r.json()["preview"]["target"], "u_alice")
        r = self.load(data, "u_bob")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.now("u_bob"), (self.bob[0] + 2, self.bob[1] + 2, 1))
        self.assertEqual(self.now("u_alice"), self.alice)
        with db.get_conn() as conn:
            new = {r[0] for r in conn.execute("SELECT id FROM questions WHERE user_id = 'u_bob'")}
            bob = conn.execute("SELECT enabled, is_child FROM users WHERE id = 'u_bob'").fetchone()
        self.assertFalse(old & new)
        self.assertEqual(tuple(bob), (1, 0))
        self.assertEqual(len(self.c.get("/api/history", headers=BOB).json()["questions"]), self.bob[0] + 2)

    def test_add_skips_duplicates_and_replace_needs_the_name(self):
        data = self.export()
        with db.get_conn() as conn:
            briefing.save(conn, "u_alice", False, "08:00", "every", speaker_checked=True)
        r = self.load(data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["imported"]["skipped"], 3)            # 2 questions + Alice's own briefings kept
        self.assertEqual(self.now("u_alice"), self.alice)
        self.assertEqual(self.c.get("/api/briefing", headers=ALICE).json()["time"], "08:00")
        self.assertEqual(self.load(data, mode="replace").status_code, 409)
        self.assertEqual(self.load(data, mode="replace", confirm="Bob").status_code, 409)
        r = self.load(data, mode="replace", confirm="  alice ")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.now("u_alice"), self.alice)
        self.assertEqual(self.c.get("/api/briefing", headers=ALICE).json()["time"], "06:45")

    def test_questions_only_with_the_option(self):
        data = self.export(questions=False)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(sorted(n for n in z.namelist() if n.startswith("data/")),
                             ["data/briefings.json", "data/users.json"])
            self.assertNotIn("What's on my list", "".join(z.read(n).decode() for n in z.namelist()))
        with self.assertLogs("people_data", "INFO") as logs:
            self.export(questions=True)
        self.assertIn("questions and answers included", logs.output[0])
        self.assertNotIn("list today", logs.output[0])
        preview = self.upload(data).json()["preview"]
        self.assertTrue(any("without their questions" in w for w in preview["warnings"]))
        self.delete()
        self.assertEqual(self.load(data).status_code, 200)
        self.assertEqual(self.now("u_alice"), (0, 0, 1))

    def test_old_questions_and_proposed_actions(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE questions SET asked_at = ? WHERE user_id = 'u_alice' AND text = 'And tomorrow?'",
                         (engine._iso(config.utcnow() - timedelta(days=40)),))
            conn.execute("UPDATE calls SET state = 'proposed', say = 'Add milk' WHERE question_id IN "
                         "(SELECT id FROM questions WHERE user_id = 'u_alice')")
        data = self.export()
        preview = self.upload(data).json()["preview"]
        self.assertTrue(any("older than Keep questions for" in w for w in preview["warnings"]), preview)
        self.delete()
        r = self.load(data)
        self.assertEqual(r.json()["imported"]["old"], 1)
        self.assertEqual(self.now("u_alice")[0], 1)
        with db.get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM calls WHERE state = 'proposed'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rate_events WHERE user_id = 'u_alice'")
                             .fetchone()[0], 0)

    def test_delete_keep_and_forget(self):
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_child = 1 WHERE id = 'u_alice'")
            conn.execute("INSERT INTO quick_phrases (id, phrase, intent, source, created_at) VALUES "
                         "('p1', 'chores', 'tasks', 'admin', '2026-10-01')")
            usage = conn.execute("SELECT SUM(questions) FROM usage_days").fetchone()[0]
        s = self.summary().json()
        self.assertTrue(s["keeps"] and s["cannotReach"] and s["forgetNote"])
        self.assertEqual(self.delete(confirm="Alicia").status_code, 409)
        self.assertEqual(self.now("u_alice"), self.alice)
        r = self.delete()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"]["questions"], 2)
        with db.get_conn() as conn:
            row = conn.execute("SELECT enabled, is_child FROM users WHERE id = 'u_alice'").fetchone()
            self.assertEqual(tuple(row), (1, 1))                          # kept, with the same switches
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rate_events WHERE user_id = 'u_alice'")
                             .fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quick_phrases").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT SUM(questions) FROM usage_days").fetchone()[0], usage)
        self.assertEqual(self.now("u_bob"), self.bob)                     # others untouched
        self.assertEqual(len(self.c.get("/api/history", headers=BOB).json()["questions"]), self.bob[0])
        r = self.delete(forget=True)
        self.assertEqual(r.status_code, 200, r.text)
        with db.get_conn() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM users WHERE id = 'u_alice'").fetchone())
        self.c.get("/api/me", headers=ALICE)                               # back as someone new
        people = {p["id"]: p for p in self.c.get("/api/admin/people", headers=ADMIN).json()["people"]}
        self.assertTrue(people["u_alice"]["new"])
        self.assertFalse(people["u_alice"]["isChild"])
        self.assertEqual(self.summary("u_bob").status_code, 200)

    def test_delete_stops_a_running_question_and_forgets_the_cache(self):
        engine._recent[("u_alice", "todo.tasks", "{}")] = (time.time(), {"result": {}})
        engine._recent[("u_bob", "todo.tasks", "{}")] = (time.time(), {"result": {}})
        slow = lambda prompt, purpose: (time.sleep(0.5), '{"answer": "late"}')[1]
        with mock.patch.object(ai_client, "generate", Model(slow)):
            qid = self.c.post("/api/ask", json={"text": "Slow one"}, headers=ALICE).json()["id"]
            time.sleep(0.1)
            r = self.delete()
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["deleted"]["questions"], 3)
            with engine._lock:
                self.assertNotIn(qid, engine._stops)                       # its thread had ended
        self.assertEqual(self.now("u_alice"), (0, 0, 0))
        self.assertNotIn(("u_alice", "todo.tasks", "{}"), engine._recent)
        self.assertIn(("u_bob", "todo.tasks", "{}"), engine._recent)
        q = self.ask("Hi again")                                           # Alice can ask again at once
        self.assertEqual(q["state"], "done")

    def test_refused_files(self):
        data = self.export()
        newer = pd.bytes_io_package(app="household_assistant", app_version="9.0.0", schema=people_data.SCHEMA + 1,
                                    person={"id": "u_alice", "name": "Alice"}, exported_by="x",
                                    tables={"briefings": []})
        r = self.upload(newer)
        self.assertEqual(r.status_code, 400)
        self.assertIn("newer version", r.json()["detail"])
        other = pd.bytes_io_package(app="calorie_tracker", app_version="1.0.0", schema=1,
                                    person={"id": "u_alice"}, exported_by="x", tables={})
        self.assertIn("not this app", self.upload(other).json()["detail"])
        buf = io.BytesIO()                                                  # a changed row, the manifest as it was
        with zipfile.ZipFile(io.BytesIO(data)) as z, zipfile.ZipFile(buf, "w") as out:
            for n in z.namelist():
                b = z.read(n)
                if n == "data/briefings.json":
                    b = b.replace(b"06:45", b"05:45")
                out.writestr(n, b)
        r = self.upload(buf.getvalue())
        self.assertEqual(r.status_code, 400)
        self.assertIn("was changed", r.json()["detail"])
        self.assertEqual(self.upload(b"not a zip").status_code, 400)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            qs = json.loads(z.read("data/questions.json"))
        qs[1]["text"] = "x" * (engine.MAX_QUESTION + 1)                    # a row past the app's own limit
        bad = pd.bytes_io_package(app="household_assistant", app_version=config.APP_VERSION,
                                  schema=people_data.SCHEMA, person={"id": "u_alice"}, exported_by="x",
                                  tables={"questions": qs})
        r = self.upload(bad)
        self.assertEqual(r.status_code, 400)
        self.assertIn("questions, row 2", r.json()["detail"])
        self.assertEqual(self.now("u_alice"), self.alice)

    def test_one_at_a_time(self):
        data = self.export()
        token = self.upload(data).json()["token"]
        with people_data._busy:
            self.assertEqual(self.delete().status_code, 423)
            r = self.c.post(f"/api/admin/people/u_alice/data/import/{token}", json={"mode": "add"}, headers=ADMIN)
            self.assertEqual(r.status_code, 423)
        self.assertEqual(self.now("u_alice"), self.alice)
        r = self.c.post(f"/api/admin/people/u_alice/data/import/{token}", json={"mode": "add"}, headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        r = self.c.post(f"/api/admin/people/u_alice/data/import/{token}", json={"mode": "add"}, headers=ADMIN)
        self.assertEqual(r.status_code, 404)                                # the upload is gone after its import

    def test_admin_only(self):
        data = self.export()
        token = self.upload(data).json()["token"]
        calls = [("get", "/api/admin/people/u_alice/data", {}),
                 ("post", "/api/admin/people/u_alice/data/export", {"json": {"options": {}}}),
                 ("get", "/api/admin/people/data/jobs/x", {}),
                 ("get", "/api/admin/people/data/jobs/x/file", {}),
                 ("post", "/api/admin/people/data/import", {"files": {"file": ("x.zip", data, "application/zip")}}),
                 ("post", f"/api/admin/people/u_alice/data/import/{token}", {"json": {"mode": "add"}}),
                 ("post", "/api/admin/people/u_alice/data/delete", {"json": {"confirm": "Alice"}})]
        for who in (ALICE, BOB):
            for method, path, kw in calls:
                self.assertEqual(getattr(self.c, method)(path, headers=who, **kw).status_code, 403, path)
        self.assertEqual(self.now("u_alice"), self.alice)
        # another admin can't use this admin's upload
        with mock.patch.object(config, "ADMIN_USERS", config.ADMIN_USERS | {"bob"}):
            r = self.c.post(f"/api/admin/people/u_alice/data/import/{token}", json={"mode": "add"}, headers=BOB)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.c.get("/api/admin/people/data/jobs/x", headers=ADMIN).status_code, 404)


if __name__ == "__main__":
    import unittest
    unittest.main()
