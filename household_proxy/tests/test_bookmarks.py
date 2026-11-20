"""Bookmarks (spec/SPEC.md §5.6): the household's links, seen by everyone the app lets in; only http(s) addresses;
changed by whoever added one or an admin; ordered by admins; never fetched through the proxy."""
import _env  # noqa: F401  (must be first)

import unittest

from app import auth, db
from helpers import ADMIN, BOB, CAROL, ProxyCase


class Bookmarks(ProxyCase):
    def add(self, who=BOB, **kw):
        body = {"name": "School", "url": "https://school.example.org/term-dates"}
        body.update(kw)
        return self.c.post("/api/bookmarks", json=body, headers=who)

    def test_everyone_sees_them(self):
        r = self.add()
        self.assertEqual(r.status_code, 200, r.text)
        for who in (ADMIN, BOB, CAROL):
            got = self.get("/api/bookmarks", who).json()["bookmarks"]
            self.assertEqual([(b["name"], b["url"], b["addedBy"]) for b in got],
                             [("School", "https://school.example.org/term-dates", "Bob")])
        self.assertEqual([b["canEdit"] for b in self.get("/api/bookmarks", CAROL).json()["bookmarks"]], [False])
        self.assertEqual([b["canEdit"] for b in self.get("/api/bookmarks", ADMIN).json()["bookmarks"]], [True])

    def test_addresses(self):
        self.assertEqual(self.add(url="example.com/bins").json()["bookmarks"][0]["url"], "https://example.com/bins")
        self.assertEqual(self.add(url="localhost:8123/x").json()["bookmarks"][1]["url"], "https://localhost:8123/x")
        self.assertEqual(self.add(url="http://192.168.1.1", name="").json()["bookmarks"][2]["name"], "192.168.1.1")
        for bad in ("javascript:alert(1)", "ftp://files.example", "file:///etc/passwd", "https://", "",
                    "https://bob:secret@example.com", "https://exa mple.com", "data:text/html,hi", "mailto:a@b.c", "https://example.com:abc/", "x" * 2100):
            with self.subTest(bad=bad[:30]):
                self.assertEqual(self.add(url=bad).status_code, 422)
        self.assertEqual(self.add(icon="not an emoji").status_code, 422)

    def test_who_may_change_them(self):
        bid = self.add().json()["bookmarks"][0]["id"]
        body = {"name": "School term", "url": "https://school.example.org"}
        self.assertEqual(self.c.put(f"/api/bookmarks/{bid}", json=body, headers=CAROL).status_code, 403)
        self.assertEqual(self.c.delete(f"/api/bookmarks/{bid}", headers=CAROL).status_code, 403)
        self.assertEqual(self.c.put(f"/api/bookmarks/{bid}", json=body, headers=BOB).json()["bookmarks"][0]["name"],
                         "School term")                                  # the person who added it
        self.assertEqual(self.c.delete(f"/api/bookmarks/{bid}", headers=ADMIN).json()["bookmarks"], [])   # an admin
        self.assertEqual(self.c.delete(f"/api/bookmarks/{bid}", headers=ADMIN).status_code, 404)

    def test_admins_set_the_order(self):
        a = self.add(name="A").json()["bookmarks"][0]["id"]
        b = self.add(name="B").json()["bookmarks"][1]["id"]
        self.assertEqual(self.c.post("/api/bookmarks/reorder", json={"ids": [b, a]}, headers=BOB).status_code, 403)
        r = self.c.post("/api/bookmarks/reorder", json={"ids": [b, a]}, headers=ADMIN)
        self.assertEqual([x["name"] for x in r.json()["bookmarks"]], ["B", "A"])
        self.assertEqual(self.c.post("/api/bookmarks/reorder", json={"ids": [a]}, headers=ADMIN).status_code, 409)
        self.assertEqual(self.c.post("/api/bookmarks/reorder", json={"ids": "x"}, headers=ADMIN).status_code, 422)

    def test_groups(self):
        a = self.add(name="Bins", group="  Home   stuff ").json()["bookmarks"][0]
        self.assertEqual(a["group"], "Home stuff")                                    # spaces tidied
        got = self.add(CAROL, name="School", group="home STUFF").json()["bookmarks"]
        self.assertEqual([b["group"] for b in got], ["Home stuff", "Home stuff"])     # joins it, never split by case
        c = self.add(name="Loose").json()["bookmarks"][2]
        self.assertIsNone(c["group"])
        self.assertEqual(self.add(group="x" * 41).status_code, 422)
        body = {"name": "Bins", "url": "https://bins.example.org"}                      # no "group": it stays put
        self.assertEqual(self.c.put(f"/api/bookmarks/{a['id']}", json=body, headers=BOB).json()["bookmarks"][0]["group"],
                         "Home stuff")
        r = self.c.put(f"/api/bookmarks/{a['id']}", json={**body, "group": ""}, headers=BOB)
        self.assertIsNone(r.json()["bookmarks"][0]["group"])                         # empty: out of the group
        self.assertEqual(self.get("/api/bookmarks", ADMIN).json()["bookmarks"][1]["group"], "Home stuff")

    def test_admins_rename_groups(self):
        self.add(name="A", group="Work")
        self.add(name="B", group="work")
        self.add(name="C", group="School")
        rename = lambda who, **b: self.c.post("/api/bookmarks/rename-group", json=b, headers=who)
        self.assertEqual(rename(BOB, **{"from": "Work", "to": "Job"}).status_code, 403)
        self.assertEqual([b["group"] for b in rename(ADMIN, **{"from": "WORK", "to": "Job"}).json()["bookmarks"]],
                         ["Job", "Job", "School"])
        self.assertEqual([b["group"] for b in rename(ADMIN, **{"from": "job", "to": "school"}).json()["bookmarks"]],
                         ["School", "School", "School"])                              # merged into the existing one
        self.assertEqual([b["group"] for b in rename(ADMIN, **{"from": "School", "to": ""}).json()["bookmarks"]],
                         [None, None, None])                                          # ungrouped
        self.assertEqual(rename(ADMIN, **{"from": "Nope", "to": "X"}).status_code, 404)
        self.assertEqual(rename(ADMIN, **{"from": "", "to": "X"}).status_code, 422)

    def test_older_database_gets_the_group_column(self):
        with db.get_conn() as conn:
            conn.executescript("ALTER TABLE bookmarks DROP COLUMN group_name;")
        db.init_db()
        self.assertEqual(self.add(group="Kids").json()["bookmarks"][0]["group"], "Kids")

    def test_turned_off_people_see_none(self):
        self.add()
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET enabled = 0 WHERE id = 'u-carol'")
        auth.forget_cached()
        self.assertEqual(self.get("/api/bookmarks", CAROL).status_code, 403)
        self.assertEqual(self.add(CAROL).status_code, 403)


if __name__ == "__main__":
    unittest.main()
