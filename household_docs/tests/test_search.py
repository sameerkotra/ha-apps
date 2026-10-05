"""Basic search (SPEC §10): words in names and content, over everything you can open — the access check is
inside the query, so nothing you can't open ever shows (a scan of every answer); LIKE when FTS5 is missing."""
import _env  # noqa: F401

import json
import os
import unittest

from app import db
from base import ASHA, DEV, KABIR, MEERA, ApiBase, person_dir, write


class Search(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def setUp(self):
        super().setUp()
        self.mine = self.note("Garden plan", text="Plant tomatoes in May")
        self.secret = self.note("Diary", h=MEERA, text="tomatoes are my secret")
        self.shared = self.note("Recipes", h=DEV, text="tomato soup, tomatoes and basil")
        self.ok(self.share(self.shared["id"], KABIR, "viewer", h=DEV))

    def names(self, q, h=KABIR):
        return [r["name"] for r in self.ok(self.get(f"/api/search?q={q}", h))["results"]]

    def test_names_and_content(self):
        self.assertEqual(self.names("garden"), ["Garden plan.txt"])
        self.assertEqual(self.names("gard pla"), ["Garden plan.txt"])          # every word, any order, prefix
        self.assertEqual(sorted(self.names("tomatoes")), ["Garden plan.txt", "Recipes.txt"])
        r = self.ok(self.get("/api/search?q=basil"))["results"][0]
        self.assertEqual(r["role"], "viewer")
        self.assertIn("⁅basil⁆", r["snippet"])
        self.assertEqual(r["location"], "Dev Rao")
        self.assertEqual(self.names(""), [])
        self.assertEqual(self.names('"'), [])

    def test_access_inside_every_answer(self):
        for q in ("tomatoes", "secret", "diary", "my", "t"):
            body = json.dumps(self.ok(self.get(f"/api/search?q={q}"))["results"]).lower()
            self.assertNotIn(self.secret["id"], body, q)
            self.assertNotIn("diary", body, q)
            self.assertNotIn("secret", body, q)
        self.assertEqual(self.names("tomatoes", ASHA), [])                    # admins see nothing as admins

    def test_trash_gone_and_outside(self):
        self.ok(self.delete(f"/api/nodes/{self.mine['id']}"))
        self.assertEqual(self.names("garden"), [])
        write(os.path.join(person_dir("Kabir Rao"), "notes.json"), '{"word": "zucchini"}')
        self.scan()
        self.assertEqual(self.names("zucchini"), ["notes.json"])
        self.settings({"content_index_mb": 0})
        write(os.path.join(person_dir("Kabir Rao"), "more.txt"), "aubergine")
        self.scan()
        self.assertEqual(self.names("aubergine"), [])

    def test_like_fallback(self):
        db._fts_state["available"] = False
        with db.get_conn() as conn:
            conn.execute("DROP TABLE fts")
            conn.execute(db.FTS_FALLBACK_SQL)
            conn.execute("INSERT INTO fts (node_id, name, body) VALUES (?, ?, ?)",
                         (self.mine["id"], "Garden plan.txt", "Plant tomatoes in May"))
        try:
            self.assertEqual(self.names("tomatoes"), ["Garden plan.txt"])
            self.assertEqual(self.names("100%"), [])
        finally:
            for f in __import__("glob").glob(db.config.DB_PATH + "*"):
                os.remove(f)
            db.init_db()


if __name__ == "__main__":
    unittest.main()
