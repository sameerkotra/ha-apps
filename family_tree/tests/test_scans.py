"""Photo fixes for scans (§13.19) and the inbox folder (§13.10)."""
from base import ApiTestCase, image_bytes, set_app_settings, sql
import _env  # noqa: F401

import io
import os
import time
import unittest

from PIL import Image

from app import inbox, media


def photo(size=(400, 200), color=(200, 30, 30)):
    return image_bytes(size=size, color=color)


class Fixes(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.ann = self.person("Ann")
        r = self.ok(self.post("/api/media", files={"file": ("p.jpg", photo(), "image/jpeg")}), 201)
        self.mid = r["media"]["id"]

    def img(self, size="1024"):
        resp = self.get(f"/api/media/{self.mid}/file?size={size}")
        self.assertEqual(resp.status_code, 200)
        return Image.open(io.BytesIO(resp.content))

    def test_rotate_crop_revert_and_undo(self):
        self.assertEqual(self.img().size, (400, 200))
        r = self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": {"rotate": 90}}))
        self.assertEqual(r["media"]["edit"], {"rotate": 90, "angle": 0.0, "crop": None, "autocontrast": False})
        self.assertEqual(self.img().size, (200, 400))
        self.assertEqual(self.img("original").size, (400, 200))           # the original never changes
        self.assertEqual(self.img("display").size, (200, 400))
        r2 = self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": {"rotate": 90, "crop": {"x": 0, "y": 0.5, "w": 1, "h": 0.5},
                                                                        "autocontrast": True}}))
        self.assertEqual(self.img().size, (200, 200))
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (r2["batchId"],))[0]["label"], "Fixed a photo")
        # undo shows the older recipe's picture again
        self.ok(self.post(f"/api/history/{r2['batchId']}/undo"))
        self.assertEqual(self.img().size, (200, 400))
        self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": None}))
        self.assertEqual(self.img().size, (400, 200))
        self.assertIsNone(self.ok(self.get(f"/api/media/{self.mid}"))["edit"])

    def test_straighten_and_validation(self):
        self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": {"angle": 8}}))
        w, h = self.img().size
        self.assertLess(w, 400)                                          # corners cut off
        for bad in ({"rotate": 45}, {"angle": 30}, {"crop": {"x": 0.5, "y": 0, "w": 0.01, "h": 1}}, {"size": 3},
                    {"angle": "a lot"}):
            self.assertEqual(self.put(f"/api/media/{self.mid}/edit", {"edit": bad}).status_code, 422, bad)
        # a recipe that changes nothing is the same as none
        r = self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": {"rotate": 0, "angle": 0}}))
        self.assertIsNone(r["media"]["edit"])

    def test_tags_follow_the_face(self):
        # box on the original's left half, then rotate the photo 90° clockwise
        r = self.ok(self.post(f"/api/media/{self.mid}/regions", {"personId": self.ann, "x": 0.1, "y": 0.25, "w": 0.2, "h": 0.5}), 201)
        rid = r["regionId"]
        m = self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": {"rotate": 90}}))["media"]
        g = m["regions"][0]
        self.assertAlmostEqual(g["x"], 0.25, places=4)
        self.assertAlmostEqual(g["y"], 0.1, places=4)
        self.assertAlmostEqual(g["w"], 0.5, places=4)
        self.assertAlmostEqual(g["h"], 0.2, places=4)
        # stored against the original, unchanged
        self.assertEqual(sql("SELECT x, y FROM media_regions WHERE id = ?", (rid,))[0], {"x": 0.1, "y": 0.25})
        # a box drawn on the rotated picture is stored on the original
        self.ok(self.post(f"/api/media/{self.mid}/regions", {"personId": self.ann, "x": 0.25, "y": 0.6, "w": 0.5, "h": 0.2}), 201)
        row = sql("SELECT x, y, w, h FROM media_regions ORDER BY created_at DESC, rowid DESC LIMIT 1")[0]
        self.assertAlmostEqual(row["x"], 0.6, places=4)
        self.assertAlmostEqual(row["y"], 0.25, places=4)
        # a crop that cuts the face away hides the box
        m = self.ok(self.put(f"/api/media/{self.mid}/edit", {"edit": {"crop": {"x": 0.5, "y": 0, "w": 0.5, "h": 1}}}))["media"]
        vis = {g["id"]: g["visible"] for g in m["regions"]}
        self.assertFalse(vis[rid])

    def test_documents_cant_be_fixed(self):
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        d = self.ok(self.post("/api/media", files={"file": ("d.pdf", pdf, "application/pdf")}), 201)["media"]["id"]
        self.assertEqual(self.put(f"/api/media/{d}/edit", {"edit": {"rotate": 90}}).status_code, 422)


class Inbox(ApiTestCase):
    def drop(self, rel, data, age=120):
        path = os.path.join(inbox.inbox_dir(), rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    def test_scan_import_duplicates_rejects(self):
        venkat = self.person("Venkat", "Sharma")
        p1 = self.drop("Venkat/wedding_1985.jpg", photo(color=(10, 200, 10)))
        p2 = self.drop("scan.png", image_bytes(fmt="PNG", color=(1, 2, 3)))
        p3 = self.drop("notes.docx", b"PK\x03\x04 not a photo")
        fresh = self.drop("still-copying.jpg", photo(color=(5, 5, 5)), age=0)
        r = self.ok(self.post("/api/inbox/scan"))
        self.assertEqual((r["imported"], r["rejected"], r["waiting"], r["unsorted"]), (2, 1, 1, 2))
        self.assertFalse(os.path.exists(p1) or os.path.exists(p2) or os.path.exists(p3))
        self.assertTrue(os.path.exists(fresh))
        rej = os.path.join(inbox.inbox_dir(), "_rejected")
        self.assertTrue(os.path.exists(os.path.join(rej, "notes.docx")))
        self.assertIn("isn't allowed", open(os.path.join(rej, "notes.docx.txt")).read())
        items = self.ok(self.get("/api/media?unsorted=true"))["items"]
        by = {m["origName"]: m for m in items}
        self.assertEqual(by["Venkat/wedding_1985.jpg"]["title"], "Venkat — wedding 1985")
        self.assertEqual(by["Venkat/wedding_1985.jpg"]["suggest"], {"id": venkat, "name": "Venkat Sharma"})
        self.assertIsNone(by["scan.png"]["suggest"])
        self.assertEqual(sql("SELECT label, user_id FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1")[0],
                         {"label": "Inbox: 2 files", "user_id": None})
        # the same file again is skipped
        self.drop("again.jpg", photo(color=(10, 200, 10)))
        r = self.ok(self.post("/api/inbox/scan"))
        self.assertEqual((r["imported"], r["duplicates"]), (0, 1))
        self.assertIn("already", open(os.path.join(rej, "again.jpg.txt")).read())
        # bulk tag and Done
        ids = [m["id"] for m in items]
        self.ok(self.post("/api/media/bulk-link", {"ids": ids, "personId": venkat}))
        self.assertEqual(len(self.ok(self.get(f"/api/media?personId={venkat}"))["items"]), 2)
        self.ok(self.post("/api/media/sorted", {"ids": ids}))
        self.assertEqual(self.ok(self.get("/api/media?unsorted=true"))["items"], [])
        self.assertEqual(self.ok(self.get("/api/inbox/status"))["unsorted"], 0)

    def test_off_and_offline(self):
        self.drop("a.jpg", photo())
        set_app_settings(feature_inbox=False)
        self.assertEqual(self.post("/api/inbox/scan").status_code, 404)
        self.assertEqual(inbox.scan_blocking()["imported"], 0)
        set_app_settings(feature_inbox=True)
        with media._state_lock:
            media._state["online"] = False
        self.assertEqual(inbox.scan_blocking()["imported"], 0)


if __name__ == "__main__":
    unittest.main()
