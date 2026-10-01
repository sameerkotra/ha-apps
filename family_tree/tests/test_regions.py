"""Photo tagging with boxes (§13.2)."""
from base import ADMIN, ApiTestCase, image_bytes, sql
import _env  # noqa: F401

import io
import unittest

from PIL import Image


class Regions(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.ann = self.person("Ann", "Smith")
        self.bob = self.person("Bob", "Smith")
        r = self.post("/api/media", files={"file": ("p.jpg", image_bytes(size=(400, 200)), "image/jpeg")})
        self.mid = self.ok(r, 201)["media"]["id"] if "media" in r.json() else r.json()["id"]

    def box(self, pid, x=0.1, y=0.2, w=0.2, h=0.4, status=201, user=None):
        return self.ok(self.post(f"/api/media/{self.mid}/regions", {"personId": pid, "x": x, "y": y, "w": w, "h": h}, user=user), status)

    def media(self):
        return self.ok(self.get(f"/api/media/{self.mid}"))

    def test_box_tags_the_person(self):
        r = self.box(self.ann)
        regions = r["media"]["regions"]
        self.assertEqual([(g["personId"], g["name"], g["x"], g["y"], g["w"], g["h"], g["profile"]) for g in regions],
                         [(self.ann, "Ann Smith", 0.1, 0.2, 0.2, 0.4, False)])
        self.assertEqual([l["personId"] for l in r["media"]["links"]], [self.ann])
        self.assertEqual(sql("SELECT label FROM batches WHERE id = ?", (r["batchId"],))[0]["label"], "Tagged Ann Smith in a photo")
        # the person's Photos tab lists it
        items = self.ok(self.get(f"/api/media?personId={self.ann}"))["items"]
        self.assertEqual([m["id"] for m in items], [self.mid])
        # a second box for someone already tagged doesn't add a second link
        self.box(self.ann, x=0.5)
        self.assertEqual(len(self.media()["links"]), 1)
        self.assertEqual(len(self.media()["regions"]), 2)
        # undo removes box and link together
        self.ok(self.post(f"/api/history/{sql('SELECT id FROM batches ORDER BY created_at DESC, rowid DESC LIMIT 1')[0]['id']}/undo"))
        self.ok(self.post(f"/api/history/{r['batchId']}/undo"))
        m = self.media()
        self.assertEqual((m["regions"], m["links"]), ([], []))

    def test_validation(self):
        for body in ({"personId": self.ann, "x": -0.1, "y": 0, "w": 0.2, "h": 0.2},
                     {"personId": self.ann, "x": 0.1, "y": 0.1, "w": 0.001, "h": 0.2},
                     {"personId": self.ann, "x": 0.1, "y": 0.1, "w": 1.5, "h": 0.2},
                     {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2},
                     {"personId": "nobody", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}):
            self.assertIn(self.post(f"/api/media/{self.mid}/regions", body).status_code, (404, 422), body)
        # a box running off the edge is clamped
        r = self.box(self.ann, x=0.9, y=0.9, w=0.5, h=0.5)
        g = r["media"]["regions"][0]
        self.assertAlmostEqual(g["x"] + g["w"], 1.0)
        self.assertAlmostEqual(g["y"] + g["h"], 1.0)
        self.assertEqual(self.post("/api/media/" + "0" * 32 + "/regions",
                                   {"personId": self.ann, "x": 0, "y": 0, "w": 0.2, "h": 0.2}).status_code, 404)

    def test_profile_photo_from_a_box(self):
        rid = self.box(self.ann)["regionId"]
        other = self.box(self.bob, x=0.6)["regionId"]
        self.assertEqual(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid, "regionId": other}).status_code, 422)
        res = self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid, "regionId": rid}))
        self.assertEqual((res["person"]["photo"], res["person"]["photoRegion"]), (self.mid, rid))
        self.assertTrue(next(g for g in self.media()["regions"] if g["id"] == rid)["profile"])
        # cards and lists carry the crop
        self.assertEqual(self.ok(self.get(f"/api/tree?focus={self.ann}"))["descendants"]["p"]["photoRegion"], rid)
        # the cropped file: square, 256 px, around the box
        resp = self.get(f"/api/media/{self.mid}/file?size=256&region={rid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "image/jpeg")
        im = Image.open(io.BytesIO(resp.content))
        self.assertEqual(im.size[0], im.size[1])
        self.assertLessEqual(im.size[0], 256)
        # a region of another photo, or a stale id, just gives the whole photo
        whole = self.get(f"/api/media/{self.mid}/file?size=256&region=nope")
        self.assertEqual(whole.status_code, 200)
        self.assertNotEqual(Image.open(io.BytesIO(whole.content)).size[0], Image.open(io.BytesIO(whole.content)).size[1])
        # removing the box keeps the photo but uncropped
        res = self.ok(self.delete(f"/api/media/{self.mid}/regions/{rid}"))
        d = self.detail(self.ann)
        self.assertEqual((d["photo"], d["photoRegion"]), (self.mid, None))
        self.ok(self.post(f"/api/history/{res['batchId']}/undo"))
        self.assertEqual(self.detail(self.ann)["photoRegion"], rid)
        # choosing the whole photo clears the crop
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid}))
        self.assertIsNone(self.detail(self.ann)["photoRegion"])

    def test_untag_removes_boxes_and_crop(self):
        rid = self.box(self.ann)["regionId"]
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid, "regionId": rid}))
        link = self.media()["links"][0]["id"]
        res = self.ok(self.delete(f"/api/media/{self.mid}/links/{link}"))
        self.assertEqual(res["media"]["regions"], [])
        d = self.detail(self.ann)
        self.assertEqual((d["photo"], d["photoRegion"]), (None, None))
        self.ok(self.post(f"/api/history/{res['batchId']}/undo"))
        d = self.detail(self.ann)
        self.assertEqual((d["photo"], d["photoRegion"]), (self.mid, rid))

    def test_move_and_retag(self):
        rid = self.box(self.ann)["regionId"]
        self.ok(self.put(f"/api/people/{self.ann}/photo", {"mediaId": self.mid, "regionId": rid}))
        r = self.ok(self.patch(f"/api/media/{self.mid}/regions/{rid}", {"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.2}))
        g = r["media"]["regions"][0]
        self.assertEqual((g["x"], g["personId"]), (0.3, self.ann))
        self.assertEqual(self.detail(self.ann)["photoRegion"], rid)
        r = self.ok(self.patch(f"/api/media/{self.mid}/regions/{rid}", {"personId": self.bob, "x": 0.3, "y": 0.3, "w": 0.2, "h": 0.2}))
        self.assertEqual(r["media"]["regions"][0]["personId"], self.bob)
        self.assertIn(self.bob, [l["personId"] for l in r["media"]["links"]])
        self.assertIsNone(self.detail(self.ann)["photoRegion"])           # Ann's crop was that box
        self.assertEqual(self.patch(f"/api/media/{self.mid}/regions/nope", {"x": 0, "y": 0, "w": 0.2, "h": 0.2}).status_code, 404)

    def test_trash_and_documents(self):
        self.box(self.ann)
        self.ok(self.delete(f"/api/people/{self.ann}"))
        self.assertEqual(self.media()["regions"], [])                   # a trashed person's box is hidden
        self.ok(self.post(f"/api/people/{self.ann}/restore", user=ADMIN))
        self.assertEqual(len(self.media()["regions"]), 1)
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        doc = self.ok(self.post("/api/media", files={"file": ("d.pdf", pdf, "application/pdf")}), 201)
        did = doc["media"]["id"] if "media" in doc else doc["id"]
        self.assertEqual(self.post(f"/api/media/{did}/regions", {"personId": self.ann, "x": 0, "y": 0, "w": 0.2, "h": 0.2}).status_code, 422)

    def test_purge_removes_boxes(self):
        from app import housekeeping
        self.box(self.ann)
        self.ok(self.delete(f"/api/media/{self.mid}"))
        housekeeping.purge(None)
        self.assertEqual(sql("SELECT COUNT(*) AS n FROM media_regions")[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()
