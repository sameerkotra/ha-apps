"""Request body limits (main.py's BodyLimit, on the shared web_security.BodyLimit): bodies sent without a
Content-Length are counted as they arrive, and the backup restore — the one route without a size limit —
is refused to anyone but an admin before its body is read. Also the cache rules for versioned files."""
from base import ADMIN, ALICE, ApiTestCase, image_bytes, set_app_settings, sql

import json
import unittest
from unittest import mock

from app.routers import admin as admin_router


def chunks(data: bytes, size: int = 64 * 1024):
    """A body with no Content-Length (the client sends it chunked)."""
    for i in range(0, len(data), size):
        yield data[i:i + size]


class BodyLimits(ApiTestCase):
    def test_chunked_json_over_the_limit_is_refused(self):
        set_app_settings(max_upload_mb=1)
        body = json.dumps({"given_names": "x", "biography": "y" * (3 * 1024 * 1024)}).encode()
        r = self.post("/api/people", content=chunks(body), headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("1 MB", r.json()["detail"])
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM people")[0]["n"], 0)

    def test_chunked_body_under_the_limit_works(self):
        body = json.dumps({"given_names": "Ann"}).encode()
        r = self.post("/api/people", content=chunks(body, 4), headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 201, r.text)

    def test_chunked_photo_upload_over_the_limit(self):
        set_app_settings(max_upload_mb=1)
        pid = self.person("Ann")
        boundary = "xyzzy"
        data = image_bytes() + b"\x00" * (3 * 1024 * 1024)
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"p.jpg\"\r\n"
                f"Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        r = self.post(f"/api/people/{pid}/photo", content=chunks(body),
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("App settings", r.json()["detail"])

    def test_restore_refused_to_members_before_the_body(self):
        called = []
        with mock.patch.object(admin_router, "_restore", side_effect=lambda *a: called.append(a)):
            for kw in ({"content": chunks(b"x" * (5 * 1024 * 1024))},
                       {"files": {"file": ("b.zip", b"x" * (5 * 1024 * 1024), "application/zip")}}):
                r = self.post("/api/admin-storage-import-db", user=ALICE, **kw)
                self.assertEqual(r.status_code, 403, r.text)
                self.assertEqual(r.json()["detail"], "Only admins can restore a backup.")
            # no user at all: refused the same way
            r = self.client.post("/api/admin-storage-import-db", content=b"x" * 1024)
            self.assertEqual(r.status_code, 403)
        self.assertEqual(called, [])

    def test_restore_has_no_size_limit_for_admins(self):
        set_app_settings(max_upload_mb=1)
        r = self.post("/api/admin-storage-import-db", user=ADMIN,
                      files={"file": ("b.zip", b"PK" + b"x" * (3 * 1024 * 1024), "application/zip")})
        self.assertEqual(r.status_code, 400, r.text)            # read in full, then refused as not a backup
        self.assertIn("backup", r.json()["detail"])


class CacheRules(ApiTestCase):
    def test_versioned_assets_are_immutable_and_the_rest_is_not(self):
        r = self.client.get("/app.js?v=2.7.1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertNotIn("immutable", self.client.get("/app.js").headers.get("cache-control", ""))
        self.assertIn("no-store", self.client.get("/").headers["cache-control"])
        self.assertEqual(self.get("/api/me").headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/missing.js?v=1").status_code, 404)
        self.assertNotIn("immutable", self.client.get("/missing.js?v=1").headers.get("cache-control", ""))

    def test_photos_keep_their_own_cache_rule(self):
        pid = self.person("Ann")
        mid = self.ok(self.post(f"/api/people/{pid}/photo", files={"file": ("p.jpg", image_bytes(), "image/jpeg")}), 201)["mediaId"]
        r = self.get(f"/api/media/{mid}/file?size=256")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "private, max-age=86400")


if __name__ == "__main__":
    unittest.main()
