"""Step 9 (SPEC §17.5, §17.7, §17.11): PDF text search (sample, password-protected, scanned and hostile PDFs in the
time-limited worker; snippets with the page), the camera scan (a PDF built by the browser's own writer in Node,
read back with pypdf, and saved through /scan), and imports (an invented Google Takeout zip; .zip attacks).
Every person, note and file here is invented."""
import _env  # noqa: F401

import io
import json
import os
import shutil
import subprocess
import unittest
import zipfile

from pypdf import PdfReader, PdfWriter

from app import imports, pdftext
from app.pdfwriter import PdfDoc
from base import ASHA, KABIR, MEERA, ApiBase, person_dir, read, write

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)


def text_pdf(pages):
    doc = PdfDoc("Sample")
    for i, words in enumerate(pages):
        if i:
            doc._new_page()
        doc.paragraph(words)
    return doc.output()


def encrypted(data: bytes, user_pw="secret") -> bytes:
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    w.encrypt(user_pw, "owner-pw", algorithm="RC4-128")
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def jpeg(w=40, h=30, colour=(240, 240, 240)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), colour)
    ImageDraw.Draw(im).rectangle([5, 5, w // 2, h // 2], fill=(20, 20, 20))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    return b.getvalue()


def node_pdf(jpegs) -> bytes:
    """The browser's own PDF writer (app/static/scanpdf.js), run in Node."""
    script = ("const S = require(process.argv[1]); const imgs = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              "process.stdout.write(Buffer.from(S.buildPdf(imgs.map((b) => ({ jpeg: Buffer.from(b, 'base64') })))));")
    import base64
    r = subprocess.run(["node", "-e", script, os.path.join(APP, "app", "static", "scanpdf.js")],
                       input=json.dumps([base64.b64encode(j).decode() for j in jpegs]).encode(), capture_output=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


class PdfText(ApiBase):
    def setUp(self):
        super().setUp()
        self.scan()

    def put_pdf(self, name, data):
        write(os.path.join(person_dir("Kabir Rao"), name), data)

    def test_text_pages_and_search(self):
        self.put_pdf("Manual.pdf", text_pdf(["Warranty card for the dishwasher", "Descale the boiler every spring"]))
        self.scan()
        self.assertEqual(pdftext.run_pending(), 1)
        item = self.ok(self.get("/api/space/mine"))["items"][0]
        self.assertEqual(item["pdfText"], "ok")
        self.assertEqual(item["pdfPages"], 2)
        r = self.ok(self.get("/api/search?q=boiler"))["results"]
        self.assertEqual([x["name"] for x in r], ["Manual.pdf"])
        self.assertEqual(r[0]["page"], 2)
        self.assertTrue(r[0]["snippet"].startswith("page 2: "))
        self.assertEqual(self.ok(self.get("/api/search?q=boiler", MEERA))["results"], [])     # not hers
        info = self.ok(self.get(f"/api/nodes/{item['id']}/text"))
        self.assertEqual(info["pdf"]["status"], "ok")
        self.assertEqual(pdftext.run_pending(), 0)                 # read once per content
        self.put_pdf("Manual.pdf", text_pdf(["Now about the fridge"]))  # changed outside: read again
        os.utime(os.path.join(person_dir("Kabir Rao"), "Manual.pdf"), (1_800_000_000, 1_800_000_000))
        self.scan()
        self.assertEqual(self.ok(self.get("/api/search?q=boiler"))["results"], [])
        self.assertEqual(pdftext.run_pending(), 1)
        self.assertEqual(len(self.ok(self.get("/api/search?q=fridge"))["results"]), 1)

    def test_text_comes_back_after_the_index_rewrites_it(self):
        self.put_pdf("A.pdf", text_pdf(["kettle descaling"]))
        self.scan()
        pdftext.run_pending()
        from app import db
        with db.get_conn() as conn:                                   # e.g. a touch outside the app re-indexed it
            conn.execute("UPDATE nodes SET content_indexed = 0")
            conn.execute("UPDATE fts SET body = NULL")
        self.assertEqual(self.ok(self.get("/api/search?q=kettle"))["results"], [])
        self.assertEqual(pdftext.restore_bodies(), 1)
        self.assertEqual(len(self.ok(self.get("/api/search?q=kettle"))["results"]), 1)

    def test_protected_scanned_too_big_and_off(self):
        plain = text_pdf(["visible words"])
        self.put_pdf("Locked.pdf", encrypted(plain))
        self.put_pdf("Open lock.pdf", encrypted(plain, user_pw=""))          # opens with an empty password: read
        w = PdfWriter()
        w.add_blank_page(200, 200)
        b = io.BytesIO()
        w.write(b)
        self.put_pdf("Scan.pdf", b.getvalue())
        self.put_pdf("Huge.pdf", b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024))
        self.settings({"pdf_index_mb": 1})
        self.scan()
        pdftext.run_pending()
        items = {i["name"]: i for i in self.ok(self.get("/api/space/mine"))["items"]}
        self.assertEqual(items["Locked.pdf"]["pdfText"], "encrypted")
        self.assertEqual(items["Open lock.pdf"]["pdfText"], "ok")
        self.assertEqual(items["Scan.pdf"]["pdfText"], "scanned")
        self.assertEqual(items["Huge.pdf"]["pdfText"], "too_big")
        self.assertEqual(self.ok(self.get(f"/api/nodes/{items['Locked.pdf']['id']}/text"))["pdf"]["label"],
                         "Password-protected — its text isn't searchable")
        self.assertEqual(self.ok(self.get(f"/api/nodes/{items['Scan.pdf']['id']}/text"))["pdf"]["label"],
                         "Scanned — no text (AI can read it)")
        self.settings({"pdf_index_mb": 0})
        self.put_pdf("Later.pdf", plain)
        self.scan()
        self.assertEqual(pdftext.run_pending(), 0)

    def test_hostile_pdfs_never_hurt_the_app(self):
        # garbage, a loop of references, deep nesting, a stream that inflates to 300 MB
        import zlib
        bomb_stream = zlib.compress(b"\0" * (300 * 1024 * 1024), 9)
        bomb = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >> endobj\n"
                b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Contents 4 0 R >> endobj\n"
                b"4 0 obj << /Length " + str(len(bomb_stream)).encode() + b" /Filter /FlateDecode >> stream\n"
                + bomb_stream + b"\nendstream endobj\ntrailer << /Root 1 0 R >>\n%%EOF")
        deep = b"%PDF-1.4\n1 0 obj " + b"[" * 200_000 + b"]" * 200_000 + b" endobj\ntrailer << /Root 1 0 R >>\n%%EOF"
        loop = (b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Kids [2 0 R] "
                b"/Count 1 >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF")
        for name, data in (("garbage.pdf", b"%PDF-1.7\n\x00\xff garbage"), ("bomb.pdf", bomb), ("deep.pdf", deep),
                           ("loop.pdf", loop), ("notpdf.pdf", b"PK\x03\x04 not a pdf")):
            self.put_pdf(name, data)
        self.scan()
        pdftext.run_pending(budget=120)
        items = {i["name"]: i for i in self.ok(self.get("/api/space/mine"))["items"]}
        for name in ("garbage.pdf", "bomb.pdf", "deep.pdf", "loop.pdf", "notpdf.pdf"):
            self.assertIn(items[name].get("pdfText"), ("failed", "scanned"), name)
        self.assertEqual(self.get("/api/health").status_code, 200)

    def test_worker_is_stopped_after_its_time(self):
        data = text_pdf(["a slow but harmless document"] * 3)
        old = pdftext.TIMEOUT
        pdftext.TIMEOUT = 0.001
        try:
            with self.assertRaises(pdftext.WorkerError) as e:
                pdftext.run_worker(data)
            self.assertIn("longer than", str(e.exception))
        finally:
            pdftext.TIMEOUT = old
        self.assertEqual(pdftext.run_worker(data)["status"], "ok")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Scan(ApiBase):
    def test_browser_pdf_is_valid_and_saved(self):
        pdf = node_pdf([jpeg(40, 30), jpeg(30, 50)])
        r = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(r.pages), 2)
        self.assertAlmostEqual(float(r.pages[0].mediabox.width), 595.28, places=1)
        self.assertAlmostEqual(float(r.pages[1].mediabox.height), 595.28 * 50 / 30, places=1)
        self.assertEqual(len(r.pages[0].images), 1)
        folder = self.create("folder", "Bills")
        out = self.ok(self.c.post(f"/api/nodes/{folder['id']}/scan?name=Scan 2026-10-03 20-41&type=pdf", content=pdf,
                                  headers=KABIR), 201)
        self.assertEqual(out["name"], "Scan 2026-10-03 20-41.pdf")
        self.assertEqual(out["pdfText"], "scanned")                   # straight away: no text in a photo
        self.assertEqual(read(self.real(out["id"]), binary=True), pdf)
        # the worker finds the JPEG pages (what AI would read)
        imgs = pdftext.run_worker(pdf, mode="images")["images"]
        self.assertEqual([i["page"] for i in imgs], [1, 2])
        again = self.ok(self.c.post(f"/api/nodes/{folder['id']}/scan?name=Scan 2026-10-03 20-41.pdf&type=pdf",
                                    content=pdf, headers=KABIR), 201)
        self.assertEqual(again["name"], "Scan 2026-10-03 20-41 (2).pdf")

    def test_jpeg_pages_and_refusals(self):
        out = self.ok(self.c.post("/api/nodes/mine/scan?name=Receipt&type=jpg", content=jpeg(), headers=KABIR), 201)
        self.assertEqual(out["name"], "Receipt.jpg")
        self.assertTrue(out["preview"])
        self.assertEqual(self.c.post("/api/nodes/mine/scan?name=x&type=pdf", content=b"<svg/>", headers=KABIR).status_code, 415)
        self.assertEqual(self.c.post("/api/nodes/mine/scan?name=x&type=jpg", content=b"%PDF-1.4", headers=KABIR).status_code, 415)
        self.assertEqual(self.c.post("/api/nodes/mine/scan?name=x&type=svg", content=b"x", headers=KABIR).status_code, 422)
        theirs = self.create("folder", "Theirs", h=MEERA)
        self.ok(self.share(theirs["id"], KABIR, "viewer", h=MEERA))
        self.assertEqual(self.c.post(f"/api/nodes/{theirs['id']}/scan?name=x&type=jpg", content=jpeg(), headers=KABIR).status_code, 403)


def zip_bytes(entries, *, attrs=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if attrs and name in attrs:
                info.external_attr = attrs[name]
            z.writestr(info, data)
    return b.getvalue()


def keep_note(**kw):
    n = {"color": "DEFAULT", "isTrashed": False, "isPinned": False, "isArchived": False, "textContent": "",
         "title": "", "userEditedTimestampUsec": 1_700_000_000_000_000, "createdTimestampUsec": 1_690_000_000_000_000}
    n.update(kw)
    return json.dumps(n).encode()


class Imports(ApiBase):
    def upload(self, kind, data, name="x.zip", h=KABIR, code=201):
        r = self.c.post(f"/api/import/{kind}/upload?name={name}", content=data, headers=h)
        self.assertEqual(r.status_code, code, r.text)
        return r.json()

    def run_job(self, kind, token, body=None, h=KABIR):
        j = self.ok(self.post(f"/api/import/{kind}/{token}", body or {}, h), 202)
        done = imports.wait(j["id"])
        self.assertEqual(done["state"], "done", done.get("error"))
        return self.ok(self.get(f"/api/import/jobs/{j['id']}", h))["result"]

    def test_google_keep_takeout(self):
        z = zip_bytes([
            ("Takeout/Keep/Shopping.json", keep_note(title="Party", listContent=[{"text": "Cake", "isChecked": True},
                                                                                {"text": "Candles", "isChecked": False}],
                                                     labels=[{"name": "family"}, {"name": "bad,label"}], color="PINK",
                                                     createdTimestampUsec=1_690_000_000_000_001)),
            ("Takeout/Keep/Idea.json", keep_note(title="", textContent="Paint the shed green\nsecond line",
                                                 isPinned=True, color="GREEN", createdTimestampUsec=1_690_000_000_000_002,
                                                 attachments=[{"filePath": "shed.jpg", "mimetype": "image/jpeg"}])),
            ("Takeout/Keep/shed.jpg", jpeg()),
            ("Takeout/Keep/Old.json", keep_note(title="Old one", textContent="archived text", isArchived=True,
                                                createdTimestampUsec=1_690_000_000_000_003)),
            ("Takeout/Keep/Gone.json", keep_note(title="Gone", textContent="trashed", isTrashed=True,
                                                 createdTimestampUsec=1_690_000_000_000_004)),
            ("Takeout/Keep/Shopping.html", b"<html>not used</html>"),
            ("Takeout/archive_browser.html", b"<html></html>"),
        ])
        up = self.upload("keep", z, "takeout.zip")
        p = up["preview"]
        self.assertEqual((p["notes"], p["checklists"], p["archived"], p["trashed"], p["pinned"], p["attachments"]),
                         (2, 1, 1, 1, 1, 1))
        self.assertEqual(p["labels"], ["bad,label", "family"])
        res = self.run_job("keep", up["token"])
        self.assertEqual((res["added"], res["attachments"]), (3, 1))
        top = self.ok(self.get(f"/api/list?node={res['folderId']}"))
        names = {i["name"]: i for i in top["items"]}
        self.assertEqual(set(names), {"Party.md", "Paint the shed green.txt", "shed.jpg", "Keep archive"})
        party = names["Party.md"]
        self.assertEqual(party["kind"], "checklist")
        self.assertEqual(party["tags"], ["family"])
        self.assertEqual(party["color"], "purple")
        self.assertEqual(read(self.real(party["id"])), "- [x] Cake\n- [ ] Candles\n")
        idea = names["Paint the shed green.txt"]
        self.assertTrue(idea["favourite"])
        self.assertEqual(idea["color"], "green")
        self.assertEqual(os.stat(self.real(idea["id"])).st_mtime, 1_700_000_000)
        self.assertEqual(idea["modified"], "2023-11-14T22:13:20+00:00")
        arch = self.ok(self.get(f"/api/list?node={names['Keep archive']['id']}"))["items"]
        self.assertEqual([i["name"] for i in arch], ["Old one.txt"])
        again = self.upload("keep", z, "takeout.zip")
        self.assertEqual(again["preview"]["already"], 3)
        self.assertEqual(again["preview"]["toImport"], 0)
        self.assertEqual(self.run_job("keep", again["token"])["added"], 0)
        # Meera's import is hers: nothing is skipped for her
        self.assertEqual(self.upload("keep", z, h=MEERA)["preview"]["toImport"], 3)
        self.assertEqual(self.post(f"/api/import/keep/{again['token']}", {}).status_code, 404)        # used up
        self.assertEqual(self.upload("keep", zip_bytes([("a.txt", b"x")]), code=422)["detail"][:22], "No Google Keep notes w")
        self.settings({"keep_import": False})
        self.upload("keep", z, code=403)

    def test_zip_import_and_attacks(self):
        link = (0o120777 << 16)
        z = zip_bytes([
            ("Photos/2026/a.txt", b"inside"),
            ("Photos/b?:c.txt", b"cleaned"),
            ("../evil.txt", b"escape"),
            ("/abs.txt", b"absolute"),
            ("C:/win.txt", b"drive"),
            ("Photos/.hidden", b"h"),
            ("__MACOSX/Photos/._a.txt", b"mac"),
            ("Photos/link", b"/etc/passwd"),
            ("Photos/empty/", b""),
        ], attrs={"Photos/link": link})
        folder = self.create("folder", "Target")
        up = self.upload("zip", z, "Holiday.zip")
        p = up["preview"]
        self.assertEqual(p["files"], 2)
        self.assertEqual(p["skippedCount"], 6)
        self.assertEqual(p["folderName"], "Holiday")
        res = self.run_job("zip", up["token"], {"parentId": folder["id"]})
        self.assertEqual(res["added"], 2)
        base = os.path.join(person_dir("Kabir Rao"), "Target", "Holiday")
        self.assertEqual(read(os.path.join(base, "Photos", "2026", "a.txt")), "inside")
        self.assertEqual(read(os.path.join(base, "Photos", "b__c.txt")), "cleaned")
        self.assertTrue(os.path.isdir(os.path.join(base, "Photos", "empty")))
        for root, dirs, files in os.walk(os.environ["SHARE_DIR"]):
            self.assertNotIn("evil.txt", files)
            self.assertNotIn("abs.txt", files)
            self.assertNotIn("link", files)
        self.assertFalse(os.path.exists(os.path.join(os.environ["SHARE_DIR"], "..", "evil.txt")))
        # into someone else's folder only with Can edit
        theirs = self.create("folder", "Theirs", h=MEERA)
        up = self.upload("zip", zip_bytes([("x.txt", b"x")]))
        self.assertEqual(self.post(f"/api/import/zip/{up['token']}", {"parentId": theirs["id"]}).status_code, 404)

    def test_bombs_and_limits(self):
        big = b"\0" * (20 * 1024 * 1024)
        up = self.upload("zip", zip_bytes([("bomb.bin", big), ("ok.txt", b"fine")]))
        self.assertEqual(up["preview"]["files"], 1)
        self.assertIn("zip bomb", up["preview"]["skipped"][0]["why"])
        self.upload("zip", b"not a zip at all", code=422)
        many = zip_bytes([(f"f{i}.txt", b"") for i in range(imports.MAX_MEMBERS + 1)])
        self.assertIn("more than", self.upload("zip", many, code=422)["detail"])
        # an encrypted member is skipped
        raw = bytearray(zip_bytes([("secret.txt", b"x"), ("plain.txt", b"y")]))
        zf = zipfile.ZipFile(io.BytesIO(bytes(raw)))
        for i in zf.infolist():
            if i.filename == "secret.txt":
                raw[i.header_offset + 6] |= 1                 # the local header's flag
        cd = bytes(raw).rfind(b"PK\x01\x02secret"[:4], 0)
        idx = bytes(raw).find(b"PK\x01\x02")
        raw[idx + 8] |= 1                                      # the central directory's flag (first entry)
        up = self.upload("zip", bytes(raw))
        self.assertEqual(up["preview"]["files"], 1)
        self.assertEqual(up["preview"]["skipped"][0]["why"], "password-protected")
        self.settings({"upload_mb": 1})
        self.upload("zip", zip_bytes([("x.bin", os.urandom(2 * 1024 * 1024))]), code=413)
        self.assertEqual(cd, cd)
