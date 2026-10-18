"""Food without calories, food tags, symptoms and Patterns (SPEC §5.9): each person's own; calories optional and
not counted; a saved food's tags come with it; Patterns lists what came before a symptom more often than on other
days; the CSV export guards formulas."""
import _env  # noqa: F401  (must be first)

import unittest
from datetime import date, timedelta

from app import config, patterns, settings
from test_app import ADMIN, ALICE, BOB, ApiBase


def day(n: int) -> str:
    """n days before today (Home Assistant's day)."""
    return (config.today() - timedelta(days=n)).isoformat()


class CaloriesOptional(ApiBase):
    def test_a_food_without_calories_is_kept_and_not_counted(self):
        a = self.log(ALICE, "Toast", date=day(0), calories=250)
        b = self.log(ALICE, "Dal", date=day(0), calories=None, protein=12)
        c = self.c.post("/api/logs", json={"date": day(0), "food_name": "Chai"}, headers=ALICE)   # left out
        self.assertEqual(c.status_code, 200, c.text)
        self.assertEqual((a["calories"], b["calories"], c.json()["calories"]), (250, None, None))
        logs = self.c.get(f"/api/logs?date={day(0)}", headers=ALICE).json()
        self.assertEqual([x["calories"] for x in logs], [250, None, None])
        s = self.c.get(f"/api/summary?date={day(0)}", headers=ALICE).json()
        self.assertEqual((s["totals"]["calories"], s["totals"]["protein"], s["no_calories"]), (250, 12, 2))
        hist = self.c.get("/api/history?granularity=daily", headers=ALICE).json()["points"]
        self.assertEqual(hist[-1]["calories"], 250)

    def test_saved_food_without_calories(self):
        r = self.c.post("/api/saved-foods", json={"name": "Mum's sambar", "tags": ["Lentils", "spicy", "spicy"]},
                        headers=ALICE)
        self.assertEqual(r.status_code, 200, r.text)
        f = r.json()
        self.assertEqual((f["calories"], f["tags"]), (None, ["lentils", "spicy"]))
        f2 = self.c.put(f"/api/saved-foods/{f['id']}", json={"name": "Mum's sambar", "calories": 180, "tags": []},
                        headers=ALICE).json()
        self.assertEqual((f2["calories"], f2["tags"]), (180, []))
        self.assertEqual(self.c.post("/api/saved-foods", json={"name": "x", "tags": ["a" * 31]}, headers=ALICE)
                         .status_code, 422)
        self.assertEqual(self.c.post("/api/saved-foods", json={"name": "x", "tags": [str(i) for i in range(13)]},
                                     headers=ALICE).status_code, 422)


class TagsAndTimes(ApiBase):
    def test_a_saved_food_brings_its_tags_and_a_time_is_set(self):
        f = self.c.post("/api/saved-foods", json={"name": "Paneer tikka", "calories": 300, "tags": ["dairy", "spicy"]},
                        headers=ALICE).json()
        e = self.log(ALICE, "Paneer tikka", date=day(2), saved_food_id=f["id"], meal_type="dinner")
        self.assertEqual((e["tags"], e["eaten_at"]), (["dairy", "spicy"], "19:00"))     # another day: the meal's time
        e = self.log(ALICE, "Paneer tikka", date=day(2), saved_food_id=f["id"], tags=["dairy"], eaten_at="13:05")
        self.assertEqual((e["tags"], e["eaten_at"]), (["dairy"], "13:05"))               # given ones win
        e = self.log(ALICE, "Apple", date=day(0))
        self.assertRegex(e["eaten_at"], r"^\d\d:\d\d$")                                  # today: now
        self.assertEqual(self.c.post("/api/logs", json={"date": day(0), "food_name": "x", "eaten_at": "25:00"},
                                     headers=ALICE).status_code, 422)
        ch = self.c.get("/api/symptoms/choices", headers=ALICE).json()
        self.assertEqual(ch["tags"][:3], ["dairy", "gluten", "wheat"])     # App settings' list first …
        self.assertNotIn("Bloating", ch["tags"])


class Symptoms(ApiBase):
    def add(self, h=ALICE, **kw):
        body = {"date": day(0), "time": "14:00", "symptom": "Bloating", "severity": 3, **kw}
        return self.c.post("/api/symptoms", json=body, headers=h)

    def test_log_list_delete_and_only_ones_own(self):
        r = self.add(symptom="  Head   ache ", severity=4, note="after lunch")
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json()
        self.assertEqual((s["symptom"], s["severity"], s["note"]), ("Head ache", 4, "after lunch"))
        self.assertEqual(len(self.c.get(f"/api/symptoms?date={day(0)}", headers=ALICE).json()), 1)
        self.assertEqual(self.c.get(f"/api/symptoms?date={day(0)}", headers=BOB).json(), [])
        self.assertEqual(self.c.delete(f"/api/symptoms/{s['id']}", headers=BOB).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/symptoms/{s['id']}", headers=ALICE).status_code, 200)
        self.assertEqual(self.c.delete(f"/api/symptoms/{s['id']}", headers=ALICE).status_code, 404)

    def test_checks(self):
        for bad in ({"severity": 0}, {"severity": 6}, {"time": "2pm"}, {"symptom": "   "}, {"symptom": "x" * 61},
                    {"date": (config.today() + timedelta(days=1)).isoformat()}, {"note": "x" * 501}):
            with self.subTest(bad=bad):
                self.assertEqual(self.add(**bad).status_code, 422)

    def test_choices_put_ones_own_first(self):
        for _ in range(3):
            self.add(symptom="Hiccups")
        self.add(symptom="bloating")
        ch = self.c.get("/api/symptoms/choices", headers=ALICE).json()["symptoms"]
        self.assertEqual(ch[:2], ["Hiccups", "bloating"])
        self.assertEqual(len([x for x in ch if x.lower() == "bloating"]), 1)
        self.assertEqual(self.c.get("/api/symptoms/choices", headers=BOB).json()["symptoms"][0], "Bloating")

    def test_settings_lists(self):
        r = self.c.put("/api/admin/settings", json={"symptom_list": " Cramps,, cramps , Reflux "}, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["values"]["symptom_list"], "Cramps, Reflux")
        self.assertEqual(self.c.put("/api/admin/settings", json={"food_tag_list": "x" * 61}, headers=ADMIN)
                         .status_code, 422)
        self.assertEqual(self.c.put("/api/admin/settings", json={"pattern_hours": 0}, headers=ADMIN).status_code, 422)
        settings.update({"symptom_list": settings.DEFAULT_SYMPTOMS}, None)


class Patterns(ApiBase):
    def test_what_came_before_more_often(self):
        # 8 days: milk on days 1-4 at 09:00, bloating at 11:00 those days; toast every day; days 5-8 no milk, no bloating
        for n in range(1, 9):
            self.log(ALICE, "Toast", date=day(n), eaten_at="08:30", tags=["gluten"])
            if n <= 4:
                self.log(ALICE, "Milk", date=day(n), eaten_at="09:00", tags=["dairy"], calories=None)
                self.c.post("/api/symptoms", json={"date": day(n), "time": "11:00", "symptom": "Bloating",
                                                   "severity": n % 3 + 2}, headers=ALICE)
        self.c.post("/api/symptoms", json={"date": day(1), "time": "20:00", "symptom": "Headache"}, headers=ALICE)
        p = self.c.get("/api/patterns?days=30", headers=ALICE).json()
        self.assertEqual(p["loggedDays"], 8)
        b = p["symptoms"][0]
        self.assertEqual((b["name"], b["count"], b["usualTime"], b["otherDays"]), ("Bloating", 4, "11:00", 4))
        self.assertEqual([(t["tag"], t["before"], t["of"], t["other"]) for t in b["tags"]], [("dairy", 4, 4, 0)])
        self.assertEqual([f["food"] for f in b["foods"]], ["Milk"])          # toast came before both: not listed
        self.assertEqual(len(b["series"]), 30)
        self.assertEqual(b["series"][-2], 1 % 3 + 2)                          # yesterday's worst
        self.assertEqual([x["name"] for x in p["tooFew"]], ["Headache"])
        self.assertEqual(self.c.get("/api/patterns?days=30", headers=BOB).json()["symptoms"], [])
        self.assertEqual(self.c.get("/api/patterns?days=31", headers=ALICE).status_code, 422)

    def test_crosses_midnight_and_window_length(self):
        foods = [{"date": "2026-10-01", "eaten_at": "23:00", "meal_type": "snack", "food_name": "Chips",
                  "tags": "fried"}] * 1 + [
                 {"date": "2026-10-03", "eaten_at": "23:00", "meal_type": "snack", "food_name": "Chips", "tags": "fried"},
                 {"date": "2026-10-05", "eaten_at": "12:00", "meal_type": "lunch", "food_name": "Rice", "tags": None}]
        syms = [{"date": "2026-10-02", "time": "01:00", "symptom": "Heartburn", "severity": 3},
                {"date": "2026-10-04", "time": "01:30", "symptom": "Heartburn", "severity": 4}]
        out = patterns.analyse(foods, syms, start=date(2026, 10, 1), end=date(2026, 10, 7), hours=6, min_count=2)
        s = out["symptoms"][0]
        self.assertEqual([(t["tag"], t["before"]) for t in s["tags"]], [("fried", 2)])
        out = patterns.analyse(foods, syms, start=date(2026, 10, 1), end=date(2026, 10, 7), hours=1, min_count=2)
        self.assertEqual(out["symptoms"][0]["tags"], [])                       # 23:00 is more than an hour before


class Journal(ApiBase):
    def test_csv_in_time_order_with_formula_guard(self):
        self.log(ALICE, "=HYPERLINK(1)", date=day(1), eaten_at="08:00", calories=None, tags=["dairy"])
        self.c.post("/api/symptoms", json={"date": day(1), "time": "09:00", "symptom": "Nausea", "severity": 2,
                                           "note": "+mild"}, headers=ALICE)
        r = self.c.get("/api/journal.csv?days=30", headers=ALICE)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["content-disposition"])
        lines = r.text.lstrip("\ufeff").splitlines()
        self.assertEqual(lines[0], "Date,Time,Type,What,Details,Severity (1-5),Tags,Calories,Note")
        self.assertTrue(lines[1].startswith(f"{day(1)},08:00,Food,'=HYPERLINK(1),\"snack, 1 serving\",,dairy,,"))
        self.assertEqual(lines[2], f"{day(1)},09:00,Symptom,Nausea,,2,,,'+mild")
        self.assertEqual(len(self.c.get("/api/journal.csv?days=30", headers=BOB).text.splitlines()), 1)


class Upgrade(ApiBase):
    def test_an_older_database_keeps_its_food(self):
        import glob
        import os
        import sqlite3
        from app import db
        for f in glob.glob(config.DB_PATH + "*"):
            os.remove(f)
        old = sqlite3.connect(config.DB_PATH)     # the tables as they were, without the new columns
        old.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT);
            CREATE TABLE goals (user_id TEXT PRIMARY KEY, calorie_goal REAL NOT NULL DEFAULT 2000,
                protein_goal REAL NOT NULL DEFAULT 150, carb_goal REAL NOT NULL DEFAULT 200,
                fat_goal REAL NOT NULL DEFAULT 65, starting_weight_kg REAL, target_weight_kg REAL);
            CREATE TABLE weight_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, date TEXT NOT NULL,
                weight_kg REAL NOT NULL, note TEXT);
            CREATE TABLE saved_foods (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL,
                serving_size REAL NOT NULL DEFAULT 1, serving_unit TEXT NOT NULL DEFAULT 'serving',
                calories REAL NOT NULL, protein REAL NOT NULL DEFAULT 0, carbs REAL NOT NULL DEFAULT 0,
                fat REAL NOT NULL DEFAULT 0);
            CREATE TABLE food_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, date TEXT NOT NULL,
                meal_type TEXT NOT NULL DEFAULT 'snack', food_name TEXT NOT NULL, servings REAL NOT NULL DEFAULT 1,
                calories REAL NOT NULL, protein REAL NOT NULL DEFAULT 0, carbs REAL NOT NULL DEFAULT 0,
                fat REAL NOT NULL DEFAULT 0, saved_food_id INTEGER, created_at TEXT);
            INSERT INTO users (id, name) VALUES ('u_alice', 'Alice');
            """)
        old.execute("INSERT INTO food_logs (user_id, date, meal_type, food_name, calories) VALUES "
                    "('u_alice', ?, 'lunch', 'Rice', 300)", (day(1),))
        old.commit()
        old.close()
        db.init_db()
        self.c.get("/api/me", headers=ALICE)
        logs = self.c.get(f"/api/logs?date={day(1)}", headers=ALICE).json()
        self.assertEqual([(x["food_name"], x["calories"], x["eaten_at"], x["tags"]) for x in logs],
                         [("Rice", 300, None, [])])
        self.assertEqual(self.c.post("/api/symptoms", json={"date": day(1), "time": "14:00", "symptom": "Gas"},
                                     headers=ALICE).status_code, 200)
        p = self.c.get("/api/patterns?days=30", headers=ALICE).json()    # an entry with no time counts at lunch
        self.assertEqual((p["loggedDays"], p["tooFew"][0]["name"]), (1, "Gas"))


if __name__ == "__main__":
    unittest.main()
