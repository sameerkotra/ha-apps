"""The 13 Oct 2026 review: limits on what one person (or one upload) can do to the disk, the memory and other people,
and the rules around mentions, group roles and notification buttons."""
import _env  # noqa: F401

import asyncio
import io
import json
import os
import shutil
import struct
import subprocess
import time
import unittest
import zlib
from collections import namedtuple
from datetime import timedelta
from unittest import mock

from base import ADMIN, LEELA, NISHA, TARUN, ApiTestCase, sql

from app import calls, chats, config, db, exporter, files, main, settings
from app.common import image_guard
from app.live import TOO_MANY, Hub, MAX_PER_USER
from app.routers import extras

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def png_header(w, h):
    """A tiny PNG that says it is w × h pixels (only the header: decoding it would need gigabytes)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 16)) + chunk(b"IEND", b""))


def png(w, h):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), (20, 120, 200)).save(out, "PNG")
    return out.getvalue()


def jpeg(w, h):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (w, h), (20, 120, 200)).save(out, "JPEG")
    return out.getvalue()


Usage = namedtuple("Usage", "total used free")


class ImageTests(ApiTestCase):
    """C1: images are checked from their header before anything is decoded."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA)
        self.hh = self.household()

    def test_an_image_with_too_many_pixels_is_never_decoded(self):
        data = png_header(10_000, 10_000)                         # 100 million pixels in a few dozen bytes
        with mock.patch("PIL.ImageFile.ImageFile.load", side_effect=AssertionError("decoded")):
            up = self.ok(self.upload(self.hh, "huge.png", data, NISHA), 201)
        self.assertEqual(up["mime"], "application/octet-stream")          # kept as a file, never shown inline
        self.assertIsNone(up["width"])
        self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        self.assertEqual(self.get(f"/api/files/{up['id']}/thumb", NISHA).status_code, 404)
        self.assertEqual(files.sniff_image(os.path.join(config.SHARE_DIR, sql(
            "SELECT rel_path FROM attachments WHERE id = ?", (up["id"],))[0]["rel_path"])), (None, None, None, False))
        # within the limit it's an image as before
        ok = self.ok(self.upload(self.hh, "small.png", png(400, 300), NISHA), 201)
        self.assertEqual((ok["mime"], ok["width"], ok["height"]), ("image/png", 400, 300))

    def test_a_big_photo_is_decoded_scaled_down(self):
        self.ok(self.put("/api/admin/settings", {"photo_max_px": 500}))
        seen = []
        real = image_guard.open_for_decode

        def spy(*args, **kw):
            seen.append(kw.get("draft"))
            return real(*args, **kw)
        with mock.patch.object(image_guard, "open_for_decode", spy):
            up = self.ok(self.upload(self.hh, "wide.jpg", jpeg(3000, 600), NISHA), 201)
        self.assertEqual((up["width"], up["height"]), (500, 100))
        # the resize asked for a copy about 500 wide in the photo's proportions, the preview about 320
        self.assertIn(("RGB", (500, 100)), seen)
        self.assertIn(("RGB", (320, 64)), seen)

    def test_busy_decoding_refuses_the_upload_and_leaves_nothing(self):
        self.ok(self.put("/api/admin/settings", {"photo_max_px": 500}))

        def busy(timeout=30):
            raise image_guard.DecodeBusy("Too many images are being processed right now; try again in a moment.")
        with mock.patch.object(image_guard, "decode_slot", busy):
            r = self.upload(self.hh, "big.jpg", jpeg(1200, 800), NISHA)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(sql("SELECT id FROM attachments"), [])
        self.assertEqual([f for _, _, fs in os.walk(config.SHARE_DIR) for f in fs if f.endswith((".jpg", ".part"))], [])


class DiskTests(ApiTestCase):
    """C2: the folder's limit counts what really arrived; unsent files are capped per person; the disk keeps
    free space; chat downloads run one at a time."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN)
        self.hh = self.household()

    def big_row(self, size, user=None, pending=False):
        mid = None if pending else self.send(self.hh, "x", NISHA)["id"]
        user = user["id"] if isinstance(user, dict) else user
        with db.get_conn() as conn:
            conn.execute("INSERT INTO attachments (id, message_id, conversation_id, rel_path, original_name, mime, size, "
                         "sha256, uploaded_by, created_at) VALUES (?, ?, ?, 'x', 'x', 'x', ?, '', ?, ?)",
                         (db.new_id(), mid, self.hh, size, user, config.now_iso()))

    def test_default_limit_and_existing_big_installs(self):
        self.assertEqual(settings.DEFAULTS["share_folder_quota_gb"], 50)
        self.assertEqual(settings.get("share_folder_quota_gb"), 50)
        # an install already holding more than that, which never chose a limit, keeps "no limit"
        self.big_row(48 * 1024 ** 3)
        main.keep_no_limit_if_over()
        self.assertEqual(settings.get("share_folder_quota_gb"), 0)
        # a limit the admin chose is never changed
        self.ok(self.put("/api/admin/settings", {"share_folder_quota_gb": 60}))
        main.keep_no_limit_if_over()
        self.assertEqual(settings.get("share_folder_quota_gb"), 60)

    def test_small_install_gets_the_default_limit(self):
        main.keep_no_limit_if_over()
        self.assertIsNone(sql("SELECT value FROM app_settings WHERE key = 'share_folder_quota_gb'") or None)
        self.assertEqual(settings.get("share_folder_quota_gb"), 50)

    def test_usage_total_follows_every_change(self):
        up = self.ok(self.upload(self.hh, "a.txt", b"12345", NISHA), 201)
        self.big_row(1000)
        m = self.send(self.hh, "", NISHA, attachmentIds=[up["id"]])
        total = lambda: sql("SELECT bytes FROM file_usage")[0]["bytes"]          # noqa: E731
        summed = lambda: sql("SELECT COALESCE(SUM(size), 0) AS n FROM attachments")[0]["n"]   # noqa: E731
        self.assertEqual(total(), summed())
        self.ok(self.delete(f"/api/messages/{m['id']}", NISHA))
        sql("UPDATE attachments SET size = size + 7")
        self.assertEqual(total(), summed())
        sql("DELETE FROM conversations WHERE id = ?", (self.hh,))             # cascades
        self.assertEqual((total(), summed()), (0, 0))
        with db.get_conn() as conn:
            self.assertEqual(files.usage_bytes(conn), 0)

    def test_chunked_upload_is_counted_as_it_arrives(self):
        self.ok(self.put("/api/admin/settings", {"max_upload_mb": 1}))

        def body(n):
            for _ in range(n):
                yield b"x" * 65536
        ok = self.req("POST", f"/api/conversations/{self.hh}/uploads?name=a.bin", NISHA, content=body(4))
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(ok.json()["size"], 4 * 65536)
        r = self.req("POST", f"/api/conversations/{self.hh}/uploads?name=b.bin", NISHA, content=body(20))
        self.assertEqual(r.status_code, 413)
        self.assertEqual(len(sql("SELECT id FROM attachments")), 1)
        self.assertEqual([f for _, _, fs in os.walk(config.SHARE_DIR) for f in fs if f.endswith(".part")], [])

    def test_the_limit_is_checked_again_with_the_real_size(self):
        self.ok(self.put("/api/admin/settings", {"share_folder_quota_gb": 1}))
        real = files.Incoming.finish

        def finish(inc, **kw):
            fields = real(inc, **kw)
            self.big_row(1024 ** 3 - 2)                 # meanwhile, the folder filled up
            return fields
        with mock.patch.object(files.Incoming, "finish", finish):
            r = self.upload(self.hh, "a.txt", b"abc", NISHA)
        self.assertEqual(r.status_code, 507)
        self.assertEqual(sql("SELECT id FROM attachments WHERE original_name = 'a.txt'"), [])
        self.assertFalse(any(f == "a.txt" for _, _, fs in os.walk(config.SHARE_DIR) for f in fs))

    def test_unsent_files_are_capped_per_person(self):
        self.ok(self.put("/api/admin/settings", {"max_upload_mb": 1}))
        with db.get_conn() as conn:
            limit = files.pending_limit(conn)
        self.assertEqual(limit, 2 * chats.MAX_FILES * 1024 * 1024)
        self.big_row(limit - 2, NISHA, pending=True)
        self.assertEqual(self.upload(self.hh, "a.txt", b"abc", NISHA).status_code, 507)
        self.ok(self.upload(self.hh, "a.txt", b"abc", TARUN), 201)              # someone else's are theirs
        self.ok(self.upload(self.hh, "b.txt", b"a", NISHA), 201)                # what still fits

    def test_free_space_is_kept(self):
        self.ok(self.put("/api/admin/settings", {"min_free_mb": 100}))
        low = Usage(10 ** 12, 10 ** 12, 50 * 1024 * 1024)
        with mock.patch.object(files.shutil, "disk_usage", return_value=low):
            self.assertEqual(self.upload(self.hh, "a.txt", b"abc", NISHA).status_code, 507)
            self.assertEqual(self.get(f"/api/conversations/{self.hh}/export", NISHA).status_code, 507)
            self.assertEqual(self.get("/api/admin-storage-download-db").status_code, 507)
        self.assertEqual([f for f in os.listdir(config.DATA_DIR) if f.startswith((".export-", ".backup-"))], [])
        self.ok(self.put("/api/admin/settings", {"min_free_mb": 0}))           # 0: not checked
        with mock.patch.object(files.shutil, "disk_usage", return_value=low):
            self.ok(self.upload(self.hh, "a.txt", b"abc", NISHA), 201)

    def test_downloads_one_at_a_time_and_ten_an_hour(self):
        extras_ = exporter
        extras_._running.add(NISHA["id"])
        try:
            self.assertEqual(self.get(f"/api/conversations/{self.hh}/export", NISHA).status_code, 429)
            extras_._running.add(TARUN["id"])
            self.assertEqual(self.get(f"/api/conversations/{self.hh}/export", ADMIN).status_code, 503)
        finally:
            extras_._running.clear()
        exporter.export_limit.reset()
        try:
            for _ in range(10):
                self.assertEqual(self.get(f"/api/conversations/{self.hh}/export", NISHA).status_code, 200)
            self.assertEqual(self.get(f"/api/conversations/{self.hh}/export", NISHA).status_code, 429)
            self.assertEqual(self.get(f"/api/conversations/{self.hh}/export", TARUN).status_code, 200)
        finally:
            exporter.export_limit.reset()
        self.assertEqual(exporter._running, set())

    def test_json_bodies_are_counted_without_a_length(self):
        def chunks():
            yield b'{"body": "'
            for _ in range(40):
                yield b"x" * 32768
            yield b'"}'
        r = self.req("POST", f"/api/conversations/{self.hh}/messages", NISHA, content=chunks(),
                     headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_shared_folder_upload_and_new_folders(self):
        docs = os.path.join(config.SHARE_ROOT, "Docs")
        os.makedirs(docs)
        g = self.ok(self.post("/api/conversations", {"name": "Home", "memberIds": [TARUN["id"]]}, NISHA), 201)["id"]
        r = self.ok(self.post("/api/admin/folders", {"path": "Docs", "chats": [{"id": g, "mode": "rw"}]}), 201)
        link = r["folders"][0]["chats"][0]["linkId"]
        self.ok(self.put("/api/admin/settings", {"min_free_mb": 100}))
        low = Usage(10 ** 12, 10 ** 12, 50 * 1024 * 1024)
        with mock.patch.object(files.shutil, "disk_usage", return_value=low):
            self.assertEqual(self.req("POST", f"/api/folders/{link}/upload?name=a.txt", TARUN, content=b"x").status_code, 507)
        self.assertEqual(os.listdir(docs), [])
        # the same name twice: the second gets its own
        self.assertEqual(self.ok(self.req("POST", f"/api/folders/{link}/upload?name=a.txt", TARUN, content=b"1"), 201)["name"], "a.txt")
        self.assertEqual(self.ok(self.req("POST", f"/api/folders/{link}/upload?name=a.txt", TARUN, content=b"2"), 201)["name"], "a (2).txt")
        extras.mkdir_limit.reset()
        try:
            for i in range(30):
                self.ok(self.post(f"/api/folders/{link}/mkdir", {"name": f"f{i}"}, TARUN), 201)
            self.assertEqual(self.post(f"/api/folders/{link}/mkdir", {"name": "one more"}, TARUN).status_code, 429)
        finally:
            extras.mkdir_limit.reset()


class SameNameTests(ApiTestCase):
    """CI1: two files of the same name arriving together each get their own name; neither replaces the other."""

    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA)
        self.hh = self.household()

    def test_reserve_name_is_atomic(self):
        d = os.path.join(config.SHARE_ROOT, "names")
        os.makedirs(d)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            got = list(ex.map(lambda _: files.reserve_name(d, "Lease.pdf"), range(40)))
        self.assertEqual(len(set(got)), 40)
        self.assertEqual(sorted(os.listdir(d)), sorted(got))
        self.assertIn("Lease (40).pdf", got)

    def test_concurrent_uploads_of_one_name(self):
        real = files.unique_name

        def slow_unique(full_dir, name):          # the old race: both see the name free, then both take it
            n = real(full_dir, name)
            time.sleep(0.05)
            return n
        import concurrent.futures
        with mock.patch.object(files, "unique_name", slow_unique):
            with concurrent.futures.ThreadPoolExecutor(4) as ex:
                rs = list(ex.map(lambda i: self.upload(self.hh, "same.txt", f"copy {i}".encode(), NISHA), range(4)))
        self.assertEqual([r.status_code for r in rs], [201] * 4)
        rows = sql("SELECT rel_path, size FROM attachments")
        self.assertEqual(len({r["rel_path"] for r in rows}), 4)
        contents = set()
        for r in rows:
            with open(os.path.join(config.SHARE_DIR, r["rel_path"]), "rb") as f:
                contents.add(f.read())
        self.assertEqual(contents, {f"copy {i}".encode() for i in range(4)})


class RoleAndMentionTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.g = self.ok(self.post("/api/conversations", {"name": "Trip", "memberIds": [TARUN["id"], LEELA["id"], ADMIN["id"]]},
                                   NISHA), 201)["id"]

    def test_only_the_owner_changes_another_admins_role(self):
        # C3
        self.ok(self.patch(f"/api/conversations/{self.g}/members/{TARUN['id']}", {"role": "admin"}, NISHA))
        self.ok(self.patch(f"/api/conversations/{self.g}/members/{LEELA['id']}", {"role": "admin"}, TARUN))   # admins promote
        self.assertEqual(self.patch(f"/api/conversations/{self.g}/members/{LEELA['id']}", {"role": "member"}, TARUN).status_code, 403)
        self.ok(self.patch(f"/api/conversations/{self.g}/members/{TARUN['id']}", {"role": "member"}, TARUN))  # stepping down
        self.ok(self.patch(f"/api/conversations/{self.g}/members/{LEELA['id']}", {"role": "member"}, NISHA))  # the owner may

    def test_transfer_to_a_child_is_refused(self):
        # CI4
        self.ok(self.patch(f"/api/admin/people/{LEELA['id']}", {"isChild": True}))
        self.assertEqual(self.post(f"/api/conversations/{self.g}/transfer", {"userId": LEELA["id"]}, NISHA).status_code, 409)
        self.ok(self.post(f"/api/conversations/{self.g}/transfer", {"userId": TARUN["id"]}, NISHA))

    def test_mentions_must_be_in_the_text(self):
        # C4: a mention the text doesn't show is dropped
        m = self.send(self.g, "see you there", NISHA, mentions=[TARUN["id"], LEELA["id"]])
        self.assertEqual(m["mentions"], [])
        m = self.send(self.g, "@tarun see you", NISHA, mentions=[TARUN["id"], LEELA["id"]])
        self.assertEqual(m["mentions"], [TARUN["id"]])
        edited = self.ok(self.patch(f"/api/messages/{m['id']}", {"body": "@Leela now", "mentions": [TARUN["id"], LEELA["id"]]}, NISHA))
        self.assertEqual(edited["mentions"], [LEELA["id"]])

    def test_everyone_is_for_adults(self):
        self.assertTrue(self.send(self.g, "@everyone dinner", NISHA)["mentionAll"])
        self.ok(self.patch(f"/api/admin/people/{LEELA['id']}", {"isChild": True}))
        self.assertFalse(self.send(self.g, "@everyone look!", LEELA)["mentionAll"])


class LiveLimitTests(ApiTestCase):
    """C6: at most MAX_PER_USER live connections per person; typing goes out at most every few seconds."""

    def test_eleventh_connection_closes_the_oldest(self):
        async def run():
            h = Hub()
            conns = [h.register("u1") for _ in range(MAX_PER_USER)]
            other = h.register("u2")
            newest = h.register("u1")
            await asyncio.sleep(0)
            self.assertEqual(conns[0].queue.get_nowait(), TOO_MANY)
            self.assertTrue(all(c.queue.empty() for c in conns[1:] + [other, newest]))
            self.assertEqual(h.connection_count(), MAX_PER_USER + 1)
        asyncio.run(run())

    def test_typing_is_throttled(self):
        self.enable(ADMIN, NISHA, TARUN)
        hh = self.household()
        got = []
        real = main.hub.publish

        def publish(uids, name, data, event_id=None):
            got.append(name)
            return real(uids, name, data, event_id)
        with mock.patch.object(main.hub, "publish", publish):
            for _ in range(5):
                self.ok(self.post(f"/api/conversations/{hh}/typing", user=NISHA))
        self.assertEqual(got.count("typing"), 1)


class NotificationRuleTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enable(ADMIN, NISHA, TARUN, LEELA)
        self.hh = self.household()
        self.notify_to(TARUN, "mobile_app_tarun")
        self.notify_to(LEELA, "mobile_app_leela")

    def test_announcement_reminder_respects_notifications_off(self):
        # C7
        self.ok(self.put("/api/me/settings", {"notifyLevel": "off"}, LEELA))
        m = self.send(self.hh, "Bins tonight", ADMIN, announcement=True)
        self.sent.items.clear()
        self.ok(self.post(f"/api/messages/{m['id']}/announcement-reminder", user=ADMIN))
        services = [s for item in self.sent.items for s in item["services"]]
        self.assertIn("mobile_app_tarun", services)
        self.assertNotIn("mobile_app_leela", services)


class RelayUsageCapTests(ApiTestCase):
    def test_reported_usage_is_capped_to_what_the_call_could_carry(self):
        # C9
        calls.reset()
        self.enable(ADMIN, NISHA, TARUN)
        self.ok(self.put("/api/admin/settings", {"calls_enabled": True}))
        d = self.ok(self.post("/api/conversations/direct", {"userId": TARUN["id"]}, NISHA))["id"]
        try:
            c = self.ok(self.post("/api/calls", {"conversationId": d}, NISHA), 201)
            self.ok(self.post(f"/api/calls/{c['id']}/answer", user=TARUN))
            sql("UPDATE calls SET started_at = ?", (config.iso(config.utcnow() - timedelta(seconds=40)),))
            self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 1 << 39}, NISHA))
            got = sql("SELECT relay_bytes FROM call_usage")[0]["relay_bytes"]
            self.assertLessEqual(got, 101 * calls.RELAY_RATE)            # 40 s + a minute, one other person
            self.assertGreater(got, 99 * calls.RELAY_RATE)
            self.ok(self.post(f"/api/calls/{c['id']}/usage", {"relayBytes": 5_000_000}, TARUN))
            self.assertEqual(sql("SELECT relay_bytes FROM call_usage WHERE user_id = ?", (TARUN["id"],))[0]["relay_bytes"], 5_000_000)
        finally:
            calls.reset()


class FormattingSpeedTests(unittest.TestCase):
    """C8: the page and the chat download format text in linear time (a message made of thousands of unmatched
    marks used to take seconds)."""

    CASES = {"stars": "*a " * 13000, "mixed": "_a ~b *c " * 4500, "fences": "```a\n" * 8000, "ats": "@" * 40000,
             "brackets": "http://a" + ")" * 40000, "links": "h " * 20000}

    def test_export_page(self):
        for name, text in self.CASES.items():
            t = time.monotonic()
            out = exporter.render_text(text)
            self.assertLess(time.monotonic() - t, 2.0, name)
            self.assertTrue(out)
        # the same results as before for ordinary text
        self.assertEqual(exporter.render_text("*bold* _it_ ~s~ `c` see www.example.com."),
                         '<div class="p"><strong>bold</strong> <em>it</em> <s>s</s> <code>c</code> see '
                         '<a href="https://www.example.com" rel="noopener noreferrer">www.example.com</a>.</div>')
        self.assertEqual(exporter.render_text("```\ncode\n```\n*a *b"), '<pre>code</pre><div class="p">*a *b</div>')

    @unittest.skipUnless(shutil.which("node"), "Node.js isn't installed")
    def test_browser_formatting(self):
        script = r"""
const fs = require("fs"), vm = require("vm");
const ctx = { URL, document: { createTextNode: (t) => ({ t: "#", v: String(t) }) } };
ctx.h = (tag, attrs, ...kids) => { const el = { t: tag, a: attrs || null, k: [] }; el.appendChild = (n) => { el.k.push(n); return n; };
  const add = (x) => { if (x == null || x === false) return; if (Array.isArray(x)) x.forEach(add); else if (typeof x === "string") el.k.push({ t: "#", v: x }); else el.k.push(x); };
  kids.forEach(add); return el; };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8") + ";this.renderText = renderText;", ctx);
const ser = (n) => n.t === "#" ? n.v : `<${n.t}${n.a && n.a.href ? " " + n.a.href : ""}${n.a && n.a.class ? "." + n.a.class : ""}>${n.k.map(ser).join("")}</${n.t}>`;
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const [name, text] of Object.entries(cases)) { const t = Date.now(); ctx.renderText(text, { names: ["Nisha"], myName: "Nisha" }); out[name] = Date.now() - t; }
out.sample = ser(ctx.renderText("*bold* _it_ @Nisha see (www.example.com/a_(b)).", { names: ["Nisha"], myName: "Nisha" }));
console.log(JSON.stringify(out));
"""
        import json
        r = subprocess.run(["node", "-e", script, os.path.join(APP, "app", "static", "format.js")],
                           input=json.dumps(self.CASES), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        for name in self.CASES:
            self.assertLess(got[name], 2000, name)
        self.assertEqual(got["sample"], '<div.fmt><div.p><strong>bold</strong> <em>it</em> <span.mention me>@Nisha</span> '
                                        'see (<a https://www.example.com/a_(b)>www.example.com/a_(b)</a>).</div></div>')


class SigningKeyTests(ApiTestCase):
    """The shared app-messages signing key: an App setting, secret, never in a backup, read by the bus."""

    def test_signing_key(self):
        import zipfile
        from app import app_messages
        self.enable(ADMIN)
        self.assertEqual(app_messages._signing_key(), "")                 # none: unsigned, as before
        self.ok(self.put("/api/admin/settings", {"bus_signing_key": "household-k3y"}))
        page = self.ok(self.get("/api/admin/settings"))
        self.assertNotIn("household-k3y", json.dumps(page))                  # write-only
        self.assertEqual(page["meta"]["bus_signing_key"]["group"], "apps")
        self.assertEqual(app_messages._signing_key(), "household-k3y")
        backup = self.get("/api/admin-storage-download-db").content
        with zipfile.ZipFile(io.BytesIO(backup)) as z:
            self.assertNotIn(b"household-k3y", z.read("chat.db"))
        self.ok(self.put("/api/admin/settings", {"clear_bus_signing_key": True}))
        self.assertEqual(app_messages._signing_key(), "")


class PackagingTests(unittest.TestCase):
    def test_base_image_is_one_alpine_release(self):
        # C10
        with open(os.path.join(APP, "Dockerfile"), encoding="utf-8") as f:
            froms = [ln.split()[1] for ln in f if ln.startswith("FROM ")]
        self.assertRegex(froms[-1], r"^python:3\.12-alpine3\.\d+(@sha256:[0-9a-f]{64})?$")


if __name__ == "__main__":
    unittest.main()
