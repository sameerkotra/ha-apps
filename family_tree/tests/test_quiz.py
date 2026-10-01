"""Photo quiz and kids mode (§13.13)."""
from base import ADMIN, ALICE, BOB, ApiTestCase, image_bytes, sql
import _env  # noqa: F401

import unittest
from datetime import timedelta
from unittest import mock

from app import config, kidmode

DEV = {"X-Device-Id": "device-aaaaaaaaaaaaaaaa"}
OTHER_DEV = {"X-Device-Id": "device-bbbbbbbbbbbbbbbb"}


class QuizCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.kid = self.person("Chinni", "K", gender="female", born=2018)
        self.dad = self.person("Ravi", "K", gender="male", born=1985)
        self.mum = self.person("Priya", "K", gender="female", born=1987)
        self.aunt = self.person("Lakshmi", "M", gender="female", born=1990)
        self.gp = self.person("Venkat", "K", gender="male", born=1955)
        self.gm = self.person("Sita", "K", gender="female", born=1958)
        self.mgp = self.person("Rao", "M", gender="male", born=1960)
        self.mgm = self.person("Kamala", "M", gender="female", born=1962)
        self.family(self.dad, self.mum, [self.kid])
        self.family(self.gp, self.gm, [self.dad])
        self.family(self.mgp, self.mgm, [self.mum, self.aunt])
        mid = self.ok(self.post("/api/media", files={"file": ("f.jpg", image_bytes(size=(800, 400)), "image/jpeg")}), 201)["media"]["id"]
        self.mid = mid
        for i, pid in enumerate((self.dad, self.mum, self.aunt, self.gp, self.gm, self.mgp, self.mgm)):
            self.ok(self.post(f"/api/media/{mid}/regions", {"personId": pid, "x": i * 0.12, "y": 0.1, "w": 0.1, "h": 0.3}), 201)

    def ask(self, mode="who", headers=None, **kw):
        body = {"playerId": self.kid, "mode": mode, "scope": "all"}
        body.update(kw)
        return self.post("/api/quiz/next", body, headers=headers or {})


class Quiz(QuizCase):
    def test_who_and_find(self):
        q = self.ok(self.ask("who"))
        self.assertEqual(len(q["choices"]), 4)
        self.assertIn("region", q["photo"])
        tgt = sql("SELECT 1")  # noqa: F841
        # answer every choice until right: exactly one is right
        a = self.ok(self.post("/api/quiz/answer", {"token": q["token"], "choice": q["choices"][0]["id"]}))
        self.assertIn(a["personId"], [c["id"] for c in q["choices"]])
        self.assertEqual(a["correct"], a["personId"] == q["choices"][0]["id"])
        # a token is good once
        self.assertEqual(self.post("/api/quiz/answer", {"token": q["token"], "choice": "x"}).status_code, 404)
        f = self.ok(self.ask("find"))
        self.assertTrue(f["name"])
        self.assertEqual(len({c["id"] for c in f["choices"]}), 4)
        self.assertTrue(all(c["photo"]["media"] == self.mid for c in f["choices"]))
        # nothing in History
        self.assertEqual(sql("SELECT COUNT(*) n FROM batches WHERE label LIKE '%quiz%'")[0]["n"], 0)

    def test_call_mode_terms_from_the_players_view(self):
        self.ok(self.put("/api/me/kin-lang", {"kinLang": "te"}))
        seen = set()
        for _ in range(25):
            q = self.ok(self.ask("call", difficulty="easy"))
            a = self.ok(self.post("/api/quiz/answer", {"token": q["token"], "choice": q["choices"][0]["label"]}))
            seen.add((a["personId"], a["answer"]))
            self.assertIn(a["answer"], [c["label"] for c in q["choices"]])
            self.assertEqual(len(q["choices"]), len({c["label"] for c in q["choices"]}))
        terms = dict(seen)
        if self.dad in terms:
            self.assertEqual(terms[self.dad], "Nanna")
        if self.aunt in terms:
            self.assertEqual(terms[self.aunt], "Pinni")
        self.assertGreaterEqual(len(terms), 4)

    def test_spaced_repetition_and_stats(self):
        # Lakshmi is always missed: she comes up far more often than the others
        sql("INSERT INTO quiz_stats (player_person_id, person_id, mode, correct, wrong) VALUES (?, ?, 'who', 0, 20)", (self.kid, self.aunt))
        for pid in (self.dad, self.mum, self.gp, self.gm, self.mgp, self.mgm):
            sql("INSERT INTO quiz_stats (player_person_id, person_id, mode, correct, wrong) VALUES (?, ?, 'who', 20, 0)", (self.kid, pid))
        hits = 0
        for _ in range(30):
            q = self.ok(self.ask("who"))
            a = self.ok(self.post("/api/quiz/answer", {"token": q["token"], "choice": "nobody"}))
            hits += a["personId"] == self.aunt
        self.assertGreaterEqual(hits, 5)
        st = self.ok(self.get(f"/api/quiz/stats?playerId={self.kid}"))
        self.assertEqual(st["oftenMissed"][0]["id"], self.aunt)

    def test_explanation_and_needs_photos(self):
        q = self.ok(self.ask("who"))
        a = self.ok(self.post("/api/quiz/answer", {"token": q["token"], "choice": "x"}))
        self.assertTrue(a["explanation"].startswith(sql("SELECT given_names FROM people WHERE id = ?", (a["personId"],))[0]["given_names"]))
        self.assertIn("Chinni's", a["explanation"])
        self.assertEqual(self.post("/api/quiz/next", {"mode": "who"}).status_code, 422)     # no "This is me", no player
        loner = self.person("Loner")
        self.assertEqual(self.post("/api/quiz/next", {"playerId": loner, "mode": "who", "scope": "close"}).status_code, 409)


class KidsMode(QuizCase):
    def start(self, **kw):
        body = {"playerId": self.kid, "modes": ["who", "find"], "scope": "all"}
        body.update(kw)
        return self.ok(self.post("/api/kidmode/start", body, headers=DEV))

    def test_lock_and_pin_unlock(self):
        self.ok(self.put("/api/me/kid-pin", {"pin": "4321"}))
        self.assertTrue(self.ok(self.get("/api/me"))["kidPinSet"])
        self.assertNotIn("4321", sql("SELECT kid_pin_hash FROM users WHERE id = ?", (ALICE["id"],))[0]["kid_pin_hash"])
        st = self.start()
        self.assertTrue(st["locked"] and st["pinSet"])
        # everything but the quiz is locked — on the server
        for method, path in (("GET", "/api/people"), ("GET", f"/api/people/{self.kid}"), ("POST", "/api/people"),
                             ("GET", "/api/me"), ("DELETE", f"/api/people/{self.dad}"), ("GET", "/api/tree")):
            r = self.req(method, path, headers=DEV, json={} if method == "POST" else None)
            self.assertEqual(r.status_code, 423, path)
        # the quiz, its photos and the lock state still work; play-as is fixed
        q = self.ok(self.ask("who", headers=DEV, playerId=self.dad))
        self.assertEqual(q["playerId"], self.kid)
        self.assertEqual(self.ask("call", headers=DEV).status_code, 422)
        self.assertEqual(self.get(f"/api/media/{self.mid}/file?size=256", headers=DEV).status_code, 200)
        self.assertTrue(self.ok(self.get("/api/kidmode", headers=DEV))["locked"])          # survives a reload
        # other devices of the same user aren't locked
        self.ok(self.get("/api/people", headers=OTHER_DEV))
        self.ok(self.get("/api/people"))
        # unlock
        self.assertEqual(self.ok(self.post("/api/kidmode/challenge", headers=DEV))["kind"], "pin")
        r = self.post("/api/kidmode/unlock", {"answer": "0000"}, headers=DEV)
        self.assertEqual(r.status_code, 403)
        self.assertIn("4 tries left", r.json()["detail"])
        self.assertFalse(self.ok(self.post("/api/kidmode/unlock", {"answer": "4321"}, headers=DEV))["locked"])
        self.ok(self.get("/api/people", headers=DEV))

    def test_question_unlock_and_lockout(self):
        self.start()
        ch = self.ok(self.post("/api/kidmode/challenge", headers=DEV))
        self.assertEqual(ch["kind"], "question")
        answer = sql("SELECT challenge FROM kid_sessions")[0]["challenge"]
        a, op, b = ch["question"].removeprefix("What is ").rstrip("?").split()
        self.assertEqual(str(int(a) + int(b) if op == "+" else int(a) * int(b)), answer)
        for _ in range(5):
            self.post("/api/kidmode/challenge", headers=DEV)
            r = self.post("/api/kidmode/unlock", {"answer": "-1"}, headers=DEV)
            self.assertEqual(r.status_code, 403)
        self.assertIn("5 minutes", r.json()["detail"])
        ch = self.ok(self.post("/api/kidmode/challenge", headers=DEV))
        self.assertTrue(ch["blockedUntil"])
        self.assertEqual(self.post("/api/kidmode/unlock", {"answer": answer}, headers=DEV).status_code, 429)
        # five minutes later
        later = config.utcnow() + timedelta(minutes=6)
        with mock.patch.object(config, "utcnow", return_value=later):
            ch = self.ok(self.post("/api/kidmode/challenge", headers=DEV))
            answer = sql("SELECT challenge FROM kid_sessions")[0]["challenge"]
            self.assertFalse(self.ok(self.post("/api/kidmode/unlock", {"answer": answer}, headers=DEV))["locked"])

    def test_time_limit_and_admin_unlock(self):
        self.assertEqual(self.post("/api/kidmode/start", {"playerId": self.kid, "modes": ["who"], "minutes": 7}, headers=DEV).status_code, 422)
        self.assertEqual(self.post("/api/kidmode/start", {"playerId": self.kid, "modes": ["who"]}).status_code, 422)   # no device id
        st = self.start(minutes=10)
        self.assertTrue(st["endsAt"])
        self.ok(self.ask("who", headers=DEV))
        later = config.utcnow() + timedelta(minutes=11)
        with mock.patch.object(config, "utcnow", return_value=later):
            r = self.ask("who", headers=DEV)
            self.assertEqual(r.status_code, 423)
            self.assertIn("Time's up", r.json()["detail"])
            self.assertTrue(self.ok(self.get("/api/kidmode", headers=DEV))["timeUp"])
        # an admin ends it from their own device
        self.assertEqual(self.delete(f"/api/kidmode/{DEV['X-Device-Id']}", user=BOB).status_code, 403)
        items = self.ok(self.get("/api/admin/kidmode", user=ADMIN))["items"]
        self.assertEqual(items[0]["playerName"], "Chinni K")
        self.ok(self.delete(f"/api/kidmode/{DEV['X-Device-Id']}", user=ADMIN))
        self.ok(self.get("/api/people", headers=DEV))

    def test_pin_rules(self):
        for bad in ("123", "1234567", "12a4"):
            self.assertEqual(self.put("/api/me/kid-pin", {"pin": bad}).status_code, 422)
        self.ok(self.put("/api/me/kid-pin", {"pin": "123456"}))
        self.ok(self.put("/api/me/kid-pin", {"pin": None}))
        self.assertFalse(self.ok(self.get("/api/me"))["kidPinSet"])
        self.assertTrue(kidmode.check_pin("9876", kidmode.hash_pin("9876")))
        self.assertFalse(kidmode.check_pin("9877", kidmode.hash_pin("9876")))


if __name__ == "__main__":
    unittest.main()
