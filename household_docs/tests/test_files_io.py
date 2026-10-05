"""Uploads, previews and .zip downloads (SPEC §3.3, §9.2, §9.3): uploads into folders you can edit only, the
`upload_mb` limit (said up front or found while reading), cleaned names, keep both / replace (the previous copy kept
7 days), quota; previews only for real raster images, with the right headers; zips streamed with safe names, hidden
entries and links left out, a size cap, and nothing you can't open."""
import _env  # noqa: F401

import io
import os
import unittest
import zipfile
from unittest import mock

from app import files
from app.store import versions
from base import ASHA, DEV, KABIR, MEERA, SHARE, ApiBase, person_dir, read, write

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 30
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><script>alert(1)</script></html>"


class Uploads(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA, DEV)

    def up(self, ref, name, data, h=KABIR, clash=None, headers=None):
        url = f"/api/nodes/{ref}/upload?name={name}" + (f"&onClash={clash}" if clash else "")
        return self.c.post(url, content=data, headers={**h, **(headers or {})})

    def test_into_my_docs_and_folders(self):
        it = self.ok(self.up("mine", "photo.png", PNG), 201)
        self.assertEqual((it["name"], it["kind"], it["size"], it["outside"]), ("photo.png", "file", len(PNG), False))
        self.assertEqual(read(os.path.join(person_dir("Kabir Rao"), "photo.png"), True), PNG)
        self.assertEqual(oct(os.stat(os.path.join(person_dir("Kabir Rao"), "photo.png")).st_mode & 0o777), "0o664")
        f = self.create("folder", "Trip")
        note = self.ok(self.up(f["id"], "plan.txt", b"pack the tent"), 201)
        self.assertEqual(note["kind"], "note")                                 # a .txt is a note
        self.assertEqual([r["name"] for r in self.ok(self.get("/api/search?q=tent"))["results"]], ["plan.txt"])
        cl = self.ok(self.up(f["id"], "list.md", b"- [ ] a\n- [x] b\n"), 201)
        self.assertEqual(cl["kind"], "checklist")
        self.assertEqual(os.listdir(os.path.join(SHARE, "household_docs", ".tmp")), [])   # nothing left behind

    def test_who_may_upload_where(self):
        f = self.create("folder", "Shared", h=MEERA)
        self.assertEqual(self.up(f["id"], "x.txt", b"x").status_code, 404)            # can't see it
        self.ok(self.share(f["id"], KABIR, "viewer", h=MEERA))
        self.assertEqual(self.up(f["id"], "x.txt", b"x").status_code, 403)            # can only view
        self.ok(self.share(f["id"], KABIR, "editor", h=MEERA))
        it = self.ok(self.up(f["id"], "x.txt", b"x"), 201)
        self.assertTrue(os.path.isfile(os.path.join(person_dir("Meera Rao"), "Shared", "x.txt")))   # Meera owns it
        self.assertEqual(it["ownerId"], "u_meera")
        self.assertEqual(self.up("root:nope", "x.txt", b"x").status_code, 404)
        doc = self.note("Not a folder")
        self.assertEqual(self.up(doc["id"], "x.txt", b"x").status_code, 422)
        self.assertEqual(self.up("x" * 80, "x.txt", b"x").status_code, 404)

    def test_names_are_cleaned(self):
        cases = {"../../escape.txt": "escape.txt", "..\\..\\win.txt": "win.txt", "D:\\Stuff\\x\\a.txt": "a.txt",
                 "a:b?.txt": "a_b_.txt", "con": "con_", ".hidden": "hidden", "spaces . ": "spaces",
                 "Ünïcødé.txt": "Ünïcødé.txt", "/etc/passwd": "passwd", "a\x00b.txt": "a_b.txt"}
        from urllib.parse import quote
        for raw, want in cases.items():
            with self.subTest(raw):
                it = self.ok(self.c.post(f"/api/nodes/mine/upload?name={quote(raw)}", content=b"x", headers=KABIR), 201)
                self.assertEqual(it["name"], want)
                self.assertTrue(os.path.isfile(os.path.join(person_dir("Kabir Rao"), want)))
        for raw in ("...", "/", "\\"):
            self.assertEqual(self.c.post(f"/api/nodes/mine/upload?name={quote(raw)}", content=b"x", headers=KABIR).status_code, 422)
        self.assertFalse(os.path.exists(os.path.join(SHARE, "household_docs", "people", "escape.txt")))

    def test_size_limit(self):
        self.settings({"upload_mb": 1})
        big = b"x" * (1024 * 1024 + 1)
        r = self.up("mine", "big.bin", big)
        self.assertEqual(r.status_code, 413)
        # without a Content-Length the limit is found while reading
        def gen():
            for _ in range(3):
                yield b"y" * (512 * 1024)
        r = self.c.post("/api/nodes/mine/upload?name=stream.bin", content=gen(), headers=KABIR)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertFalse(os.path.exists(os.path.join(person_dir("Kabir Rao"), "stream.bin")))
        self.assertEqual(os.listdir(os.path.join(SHARE, "household_docs", ".tmp")), [])
        self.ok(self.up("mine", "ok.bin", b"x" * (1024 * 1024)), 201)
        for bad in ({"upload_mb": 0}, {"upload_mb": 2049}):
            self.assertEqual(self.put("/api/admin/settings", bad, ASHA).status_code, 422)

    def test_quota(self):
        self.settings({"quota_gb": 1})
        with mock.patch("app.docops.check_quota", side_effect=__import__("fastapi").HTTPException(507, "full")):
            self.assertEqual(self.up("mine", "a.bin", b"x").status_code, 507)

    def test_keep_both_and_replace(self):
        a = self.ok(self.up("mine", "scan.pdf", b"%PDF one"), 201)
        b = self.ok(self.up("mine", "scan.pdf", b"%PDF two"), 201)
        self.assertEqual(b["name"], "scan (2).pdf")
        self.ok(self.post(f"/api/nodes/{a['id']}/favourite", {"value": True}))
        self.ok(self.share(a["id"], MEERA, "viewer"))
        c = self.ok(self.up("mine", "SCAN.pdf", b"%PDF three", clash="replace"), 201)
        self.assertEqual(c["id"], a["id"])                                  # same item: shares and favourite stay
        self.assertTrue(c["favourite"])
        self.assertEqual(read(os.path.join(person_dir("Kabir Rao"), "scan.pdf"), True), b"%PDF three")
        v = self.ok(self.get(f"/api/docs/{a['id']}/versions"))["versions"]
        self.assertEqual(len(v), 1)
        r = self.get(f"/api/docs/{a['id']}/versions/{v[0]['n']}/file")
        self.assertEqual(r.content, b"%PDF one")
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.get(f"/api/docs/{a['id']}/versions/{v[0]['n']}/file", DEV).status_code, 404)
        # restore puts it back (and keeps the replaced one)
        self.ok(self.post(f"/api/docs/{a['id']}/versions/{v[0]['n']}/restore", {}))
        self.assertEqual(read(os.path.join(person_dir("Kabir Rao"), "scan.pdf"), True), b"%PDF one")
        # kept 7 days, then gone (documents keep theirs under the usual rules)
        n = self.note("Doc", text="v1")
        self.ok(self.up("mine", "Doc.txt", b"v2 from computer", clash="replace"), 201)
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)
        self.clock.advance(days=8)
        self.assertGreaterEqual(versions.prune_replaced(), 2)
        self.assertEqual(self.ok(self.get(f"/api/docs/{a['id']}/versions"))["versions"], [])
        self.assertEqual(len(self.ok(self.get(f"/api/docs/{n['id']}/versions"))["versions"]), 1)
        # replacing a folder's name never replaces the folder
        self.create("folder", "Box")
        it = self.ok(self.up("mine", "Box", b"file", clash="replace"), 201)
        self.assertEqual(it["name"], "Box (2)")


class Previews(ApiBase):
    def test_raster_only_by_magic_bytes(self):
        ok = {"a.png": (PNG, "image/png"), "b.jpg": (JPEG, "image/jpeg"), "c.gif": (GIF, "image/gif"),
              "d.webp": (WEBP, "image/webp"), "photo.bin": (PNG, "image/png")}
        for name, (data, mime) in ok.items():
            with self.subTest(name):
                it = self.ok(self.c.post(f"/api/nodes/mine/upload?name={name}", content=data, headers=KABIR), 201)
                r = self.get(f"/api/nodes/{it['id']}/preview")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["content-type"], mime)
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
                self.assertTrue(r.headers["content-security-policy"].startswith("sandbox"))
                self.assertEqual(r.content, data)
                self.assertEqual(self.get(f"/api/nodes/{it['id']}/preview", MEERA).status_code, 404)
        bad = {"x.svg": SVG, "y.png": SVG, "z.png": HTML, "w.html": HTML, "doc.pdf": b"%PDF-1.4", "v.webp": b"RIFF0000WAVE"}
        for name, data in bad.items():
            with self.subTest(name):
                it = self.ok(self.c.post(f"/api/nodes/mine/upload?name={name}", content=data, headers=KABIR), 201)
                self.assertEqual(self.get(f"/api/nodes/{it['id']}/preview").status_code, 415)
                r = self.get(f"/api/nodes/{it['id']}/file")
                self.assertEqual(r.headers["content-type"], "application/octet-stream")
                self.assertTrue(r.headers["content-disposition"].startswith("attachment"))
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
                self.assertIn("sandbox", r.headers["content-security-policy"])

    def test_swapped_after_upload(self):
        it = self.ok(self.c.post("/api/nodes/mine/upload?name=a.png", content=PNG, headers=KABIR), 201)
        write(os.path.join(person_dir("Kabir Rao"), "a.png"), SVG)                  # changed outside the app
        self.assertEqual(self.get(f"/api/nodes/{it['id']}/preview").status_code, 415)
        os.remove(os.path.join(person_dir("Kabir Rao"), "a.png"))
        os.symlink("/etc/passwd", os.path.join(person_dir("Kabir Rao"), "a.png"))
        self.assertIn(self.get(f"/api/nodes/{it['id']}/preview").status_code, (404, 422))
        self.assertIn(self.get(f"/api/nodes/{it['id']}/file").status_code, (404, 422))

    def test_too_big_to_preview(self):
        it = self.ok(self.c.post("/api/nodes/mine/upload?name=a.png", content=PNG, headers=KABIR), 201)
        with mock.patch.object(files, "PREVIEW_MAX", 10):
            self.assertEqual(self.get(f"/api/nodes/{it['id']}/preview").status_code, 413)


class Zips(ApiBase):
    PEOPLE = (ASHA, KABIR, MEERA)

    def tree(self):
        f = self.create("folder", "Trip")
        self.note("Plan", f["id"], text="beach")
        sub = self.create("folder", "Photos", f["id"])
        self.c.post(f"/api/nodes/{sub['id']}/upload?name=a.png", content=PNG, headers=KABIR)
        base = os.path.join(person_dir("Kabir Rao"), "Trip")
        write(os.path.join(base, ".hidden.txt"), "hidden")
        os.symlink("/etc/passwd", os.path.join(base, "passwd-link"))
        os.symlink(os.path.join(SHARE, "household_docs"), os.path.join(base, "docs-link"))
        write(os.path.join(base, "odd\\..\\..\\name.txt"), "backslashes")       # a name only Linux allows
        return f

    def unzip(self, r):
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertTrue(r.headers["content-disposition"].startswith("attachment"))
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertIsNone(z.testzip())
        return z

    def test_folder_zip(self):
        f = self.tree()
        z = self.unzip(self.get(f"/api/nodes/{f['id']}/zip"))
        names = sorted(z.namelist())
        self.assertEqual(names, ["Trip/Photos/a.png", "Trip/Plan.txt", "Trip/odd_.._.._name.txt"])
        for n in names:
            self.assertFalse(n.startswith("/") or ".." in n.split("/") or "\\" in n, n)
        self.assertEqual(z.read("Trip/Plan.txt"), b"beach")
        self.assertIn("Trip.zip", self.get(f"/api/nodes/{f['id']}/zip").headers["content-disposition"])
        self.assertEqual(self.get(f"/api/nodes/{f['id']}/zip", MEERA).status_code, 404)
        self.ok(self.share(f["id"], MEERA, "viewer"))
        self.unzip(self.get(f"/api/nodes/{f['id']}/zip", MEERA))
        z = self.unzip(self.get("/api/nodes/mine/zip"))
        self.assertIn("My docs/Trip/Plan.txt", z.namelist())

    def test_several_items(self):
        f = self.tree()
        a = self.note("Plan", text="top plan")
        z = self.unzip(self.get(f"/api/zip?ids={f['id']},{a['id']}"))
        self.assertIn("Plan.txt", z.namelist())
        self.assertIn("Trip/Plan.txt", z.namelist())
        other = self.note("Secret", h=MEERA, text="x")
        self.assertEqual(self.get(f"/api/zip?ids={f['id']},{other['id']}").status_code, 404)
        self.assertEqual(self.get("/api/zip?ids=,").status_code, 422)
        self.assertEqual(self.get("/api/zip?ids=" + ",".join(f"x{i}" for i in range(201))).status_code, 422)
        # two items with the same name get " (2)"
        g = self.create("folder", "Other")
        b = self.note("Plan", g["id"], text="other plan")
        z = self.unzip(self.get(f"/api/zip?ids={a['id']},{b['id']}"))
        self.assertEqual(sorted(z.namelist()), ["Plan (2).txt", "Plan.txt"])

    def test_caps(self):
        f = self.tree()
        with mock.patch.object(files, "ZIP_MAX_FILES", 2):
            self.assertEqual(self.get(f"/api/nodes/{f['id']}/zip").status_code, 413)
        with mock.patch.object(files, "ZIP_MAX_BYTES", 20):
            self.assertEqual(self.get(f"/api/nodes/{f['id']}/zip").status_code, 413)

    def test_shared_folder_zip(self):
        house = os.path.join(SHARE, "House")
        write(os.path.join(house, "a", "b.txt"), "bee")
        rid = self.ok(self.post("/api/admin/shared-folders", {"path": "/share/House", "label": "House papers",
                                                              "access": {"u_kabir": "ro"}}, ASHA), 201)["id"]
        z = self.unzip(self.get(f"/api/nodes/root:{rid}/zip"))
        self.assertEqual(z.namelist(), ["House papers/a/b.txt"])
        self.assertEqual(self.get(f"/api/nodes/root:{rid}/zip", MEERA).status_code, 404)

    def test_safe_part(self):
        for raw, want in {"..": "_", ".": "_", "a/b": "a_b", "a\\b": "a_b", "x\x00y": "x_y", "": "_", "ok": "ok"}.items():
            self.assertEqual(files.safe_part(raw), want)


if __name__ == "__main__":
    unittest.main()
