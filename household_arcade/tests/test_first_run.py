"""A fresh install: with admin_users empty nobody is an admin, the API says so
(`noAdmins`) and every page shows the "No admin yet" banner. Nobody is ever
auto-promoted. Invented people only."""
import _env  # noqa: F401  (must be first)

import os
import unittest

from app import config
from base import ApiBase, hdr

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static")


def read_static(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class TestNoAdminYet(ApiBase):
    def setUp(self):
        super().setUp()
        self._admins = set(config.ADMIN_NAMES)

    def tearDown(self):
        config.ADMIN_NAMES.clear()
        config.ADMIN_NAMES.update(self._admins)
        super().tearDown()

    def test_flag_set_while_admin_users_is_empty(self):
        config.ADMIN_NAMES.clear()
        first = hdr("u_first", "Nisha Rao", "nisha")
        me = self.get("/api/me", first).json()
        self.assertTrue(me["noAdmins"])
        self.assertFalse(me["isAdmin"])                                  # the first visitor isn't promoted
        self.assertEqual(me["username"], "nisha")                        # the name the banner shows
        w = self.get("/api/whoami", first).json()
        self.assertEqual(w["adminEntries"], 0)
        self.assertEqual(self.get("/api/admin/settings", first).status_code, 403)
        self.assertFalse(self.get("/api/me", first).json()["isAdmin"])   # nor on a later visit
        self.assertEqual(self.start(h=first).status_code, 201)           # everyone can still play

    def test_flag_clear_once_someone_is_listed(self):
        self.assertTrue(config.ADMIN_NAMES)
        self.assertFalse(self.get("/api/me").json()["noAdmins"])

    def test_banner_is_on_every_page(self):
        index = read_static("index.html")
        self.assertIn('id="noAdminBanner"', index)
        self.assertLess(index.index('id="noAdminBanner"'), index.index("<main>"))   # outside the pages
        js = read_static("app.js")
        self.assertIn("state.me.noAdmins", js)
        self.assertIn("No admin yet — add your Home Assistant user name (", js)
        self.assertIn("admin_users", js)
        self.assertIn("How the app sees you", js)
        self.assertIn("syncNoAdminBanner();", js)


if __name__ == "__main__":
    unittest.main()
