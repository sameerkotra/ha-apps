"""Admin → Sites, People, App settings and Storage (spec/SPEC.md §5.2, §9): validation, admins only, backups
without credentials."""
import _env  # noqa: F401  (must be first)

import os
import tempfile
import unittest

from app import db, sites
from helpers import ADMIN, BOB, ProxyCase


class SitesForm(ProxyCase):
    def post(self, **kw):
        body = {"name": "Komodo", "target": "http://192.168.1.10:9120", "trusted_ok": True}
        body.update(kw)
        return self.c.post("/api/admin/sites", json=body, headers=ADMIN)

    def test_saved_with_a_slug_from_the_name(self):
        r = self.post(name="My NAS!")
        self.assertEqual(r.json()["slug"], "my-nas")
        self.assertEqual(r.json()["target"], "http://192.168.1.10:9120")

    def test_trusted_confirmation_required(self):
        r = self.post(trusted_ok=False)
        self.assertEqual(r.status_code, 422)
        self.assertIn("Confirm", r.text)

    def test_targets(self):
        self.assertEqual(self.post(target="https://nas.lan:5001/").json()["target"], "https://nas.lan:5001")
        for bad in ("ftp://x", "http://x/admin", "http://8.8.8.8", "http://localhost:8099", "http://0.0.0.0"):
            with self.subTest(bad):
                from unittest import mock
                from app import netguard
                with mock.patch.object(netguard, "ALLOW_LOOPBACK", False):
                    self.assertEqual(self.post(name=bad[:20], slug="", target=bad).status_code, 422)
        self.assertEqual(self.post(name="Pub", target="http://8.8.8.8", allow_public=True).status_code, 200)

    def test_slugs(self):
        self.assertEqual(self.post(slug="komodo").status_code, 200)
        self.assertEqual(self.post(slug="komodo").status_code, 422)          # taken
        for bad in ("Komodo", "-x", "x-", "a/b", "a b", "ä"):
            self.assertEqual(self.post(slug=bad).status_code, 422, bad)

    def test_start_path(self):
        self.assertEqual(self.post(start_path="/dashboard?x=1").json()["start_path"], "/dashboard?x=1")
        for bad in ("dashboard", "//evil.com", "/a\\b"):
            self.assertEqual(self.post(slug="", name="N" + bad[:5], start_path=bad).status_code, 422, bad)

    def test_chosen_needs_people(self):
        self.assertEqual(self.post(access="chosen").status_code, 422)
        self.assertEqual(self.post(access="chosen", people=["u-bob"]).json()["people"], ["u-bob"])
        self.assertEqual(self.post(slug="x", access="nobody").status_code, 422)

    def test_edit_reorder_delete(self):
        a = self.post(name="A").json()
        b = self.post(name="B").json()
        body = {"name": "A2", "slug": "a", "target": "http://192.168.1.11", "trusted_ok": True}
        self.assertEqual(self.c.put(f"/api/admin/sites/{a['id']}", json=body, headers=ADMIN).json()["name"], "A2")
        self.c.post("/api/admin/sites/reorder", json={"ids": [b["id"], a["id"]]}, headers=ADMIN)
        self.assertEqual([s["slug"] for s in self.c.get("/api/sites", headers=ADMIN).json()["sites"]], ["b", "a"])
        self.assertEqual(self.c.delete(f"/api/admin/sites/{a['id']}", headers=ADMIN).status_code, 200)
        self.assertEqual(self.c.delete(f"/api/admin/sites/{a['id']}", headers=ADMIN).status_code, 404)
        self.assertIsNone(sites.get("a"))

    def test_changing_the_address_forgets_a_trusted_certificate(self):
        s = self.post().json()
        sites.set_certificate(s["id"], "PEM", "AB:CD")
        body = {"name": "Komodo", "slug": "komodo", "target": "http://192.168.1.10:9120", "trusted_ok": True}
        self.assertEqual(self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN).json()["tls_fingerprint"],
                         "AB:CD")
        body["target"] = "http://192.168.1.12:9120"
        self.assertIsNone(self.c.put(f"/api/admin/sites/{s['id']}", json=body, headers=ADMIN).json()["tls_fingerprint"])

    def test_admins_only(self):
        for method, path in (("get", "/api/admin/sites"), ("post", "/api/admin/sites"), ("put", "/api/admin/sites/1"),
                             ("delete", "/api/admin/sites/1"), ("get", "/api/admin/settings"),
                             ("put", "/api/admin/settings"), ("get", "/api/admin/people"),
                             ("get", "/api/admin-storage-download-db"), ("post", "/api/admin/sites/reorder")):
            kw = {"json": {}} if method in ("post", "put") else {}
            r = getattr(self.c, method)(path, headers=BOB, **kw)
            self.assertEqual(r.status_code, 403, path)


class People(ProxyCase):
    def test_turn_off_and_not_yourself(self):
        r = self.c.put("/api/admin/people/u-bob", json={"enabled": False}, headers=ADMIN)
        self.assertFalse([p for p in r.json()["people"] if p["id"] == "u-bob"][0]["enabled"])
        self.assertEqual(self.c.put("/api/admin/people/u-admin", json={"enabled": False}, headers=ADMIN).status_code, 422)
        self.assertEqual(self.c.put("/api/admin/people/nobody", json={"enabled": True}, headers=ADMIN).status_code, 404)
        me = self.c.get("/api/me", headers=BOB).json()
        self.assertFalse(me["user"]["enabled"])


class Storage(ProxyCase):
    def test_backup_leaves_out_header_values_and_restore_keeps_them(self):
        self.add_site(headers=[{"name": "Authorization", "value": "Basic SECRET"}])
        r = self.c.get("/api/admin-storage-download-db", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"Basic SECRET", r.content)
        self.assertIn(b"Authorization", r.content)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            f.write(r.content)
            path = f.name
        try:
            with open(path, "rb") as f:
                r = self.c.post("/api/admin-storage-import-db", files={"file": ("b.db", f)}, headers=ADMIN)
            self.assertEqual(r.status_code, 200, r.text)
        finally:
            os.remove(path)
        e = self.c.get("/s/fake/echo", headers=ADMIN).json()
        self.assertEqual(e["headers"]["authorization"], "Basic SECRET")

    def test_restore_refuses_other_files(self):
        r = self.c.post("/api/admin-storage-import-db", files={"file": ("x.db", b"not a database")}, headers=ADMIN)
        self.assertEqual(r.status_code, 400)
        with db.get_conn() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM users").fetchone())


class Settings(ProxyCase):
    def test_round_trip_and_ranges(self):
        r = self.c.put("/api/admin/settings", json={"request_timeout_s": 30}, headers=ADMIN)
        self.assertEqual(r.json()["values"]["request_timeout_s"], 30)
        self.assertEqual(self.c.put("/api/admin/settings", json={"request_timeout_s": 1}, headers=ADMIN).status_code, 422)
        self.assertEqual(self.c.put("/api/admin/settings", json={"nope": 1}, headers=ADMIN).status_code, 422)


class Pages(ProxyCase):
    def test_deep_link_redirect(self):
        r = self.c.get("/site/komodo", headers=ADMIN, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, "../../#/site/komodo"))

    def test_whoami(self):
        w = self.c.get("/api/whoami", headers=ADMIN).json()
        self.assertTrue(w["isAdmin"])
        self.assertNotIn("adminy", str(self.c.get("/api/whoami", headers=BOB).json()))


if __name__ == "__main__":
    unittest.main()
