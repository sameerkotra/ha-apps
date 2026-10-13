"""Limits and fixes from the 2026-10 review: a packed page can't unpack past the fetch limit, the mailbox skips
huge emails and reads at most ten attachments, an attachment's type is used when its name has none, a failing
mailbox doesn't strand the folder's drafts, and the image routes answer 404 / a plain message."""
import asyncio
import email.policy
import gzip
import importlib.util
import os
import sys
import tempfile
import unittest
import zlib
from email.message import EmailMessage
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAVE_DEPS = all(importlib.util.find_spec(m) for m in ("sqlalchemy", "fastapi", "PIL"))


class UnpackTests(unittest.TestCase):
    def setUp(self):
        from app.services import websearch
        self.ws = websearch

    def test_small_pages_unpack(self):
        page = b"<html>" + b"x" * 1000 + b"</html>"
        self.assertEqual(self.ws._unpack(gzip.compress(page), "gzip"), page)
        self.assertEqual(self.ws._unpack(zlib.compress(page), "deflate"), page)
        raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        self.assertEqual(self.ws._unpack(raw.compress(page) + raw.flush(), "deflate"), page)
        self.assertEqual(self.ws._unpack(page, ""), page)

    def test_a_packed_bomb_is_refused_without_unpacking_it_all(self):
        bomb = gzip.compress(b"\0" * (self.ws.MAX_FETCH_BYTES * 4))
        self.assertLess(len(bomb), 100_000)
        with self.assertRaisesRegex(self.ws.WebSearchError, "too large"):
            self.ws._unpack(bomb, "gzip")
        with self.assertRaisesRegex(self.ws.WebSearchError, "too large"):
            self.ws._unpack(zlib.compress(b"\0" * (self.ws.MAX_FETCH_BYTES + 1)), "deflate")
        # exactly the limit is still fine
        self.assertEqual(len(self.ws._unpack(gzip.compress(b"a" * self.ws.MAX_FETCH_BYTES), "gzip")),
                         self.ws.MAX_FETCH_BYTES)

    def test_garbage_is_refused(self):
        with self.assertRaisesRegex(self.ws.WebSearchError, "unpacked"):
            self.ws._unpack(b"not gzip at all", "gzip")


class DbCase(unittest.TestCase):
    """A fresh database with one person and one home."""

    def setUp(self):
        from app import app_settings
        from app.db import dispose_engine, get_db_session, init_models
        self.dir = tempfile.mkdtemp()
        self.old_env = {k: os.environ.get(k) for k in ("DATABASE_PATH", "IMAGE_PATH")}
        os.environ["DATABASE_PATH"] = os.path.join(self.dir, "t.db")
        os.environ["IMAGE_PATH"] = os.path.join(self.dir, "img")
        dispose_engine()
        app_settings.configure(os.environ["DATABASE_PATH"])
        init_models()
        self.app_settings = app_settings
        from app.db.models import Home, User
        with get_db_session()() as db:
            db.add(User(id="u_pat", display_name="Pat"))
            db.flush()
            db.add(Home(id="h1", name="Maple Street", created_by_user_id="u_pat"))
            db.commit()

    def tearDown(self):
        from app.db import dispose_engine
        dispose_engine()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self.old_env["DATABASE_PATH"]:
            self.app_settings.configure(self.old_env["DATABASE_PATH"])

    @staticmethod
    def png(size=(40, 30)):
        from PIL import Image
        out = BytesIO()
        Image.new("RGB", size, (200, 10, 10)).save(out, "PNG")
        return out.getvalue()


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ImportTests(DbCase):
    def test_attachment_type_is_used_when_the_name_has_none(self):
        from app.db import get_db_session
        from app.services import importer
        with get_db_session()() as db:
            # a photo is known by what it is, whatever it is called or claims to be (13 Oct 2026 review, R13)
            self.assertTrue(importer.import_bytes(db, "h1", "Receipt", self.png(), "EMAIL"))
            with self.assertRaises(importer.ImportFailed):
                importer.import_bytes(db, "h1", "Receipt", b"not a photo" * 100, "EMAIL", content_type="image/png")
            draft_id = importer.import_bytes(db, "h1", "Receipt", self.png((41, 30)), "EMAIL", content_type="image/png")
            self.assertTrue(draft_id)
            # a name with a known type keeps it
            self.assertTrue(importer.import_bytes(db, "h1", "scan.png", self.png((42, 30)), "EMAIL",
                                                  content_type="application/octet-stream"))

    def test_poll_passes_the_attachment_type(self):
        from app.services import importer
        msg = EmailMessage()
        msg["Subject"] = "Receipt"
        msg.set_content("see attached")
        msg.add_attachment(b"x" * (importer.MIN_EMAIL_IMAGE_BYTES + 10), maintype="image", subtype="jpeg",
                           filename="Receipt")
        client = FakeImap({b"1": msg.as_bytes(policy=email.policy.default)})
        with self.mailbox(client), mock.patch.object(importer, "import_bytes", return_value="d1") as imp:
            self.assertEqual(importer.poll_mailbox(), ["d1"])
        self.assertEqual(imp.call_args.kwargs["content_type"], "image/jpeg")

    def test_at_most_ten_attachments_per_email(self):
        from app.services import importer
        msg = EmailMessage()
        msg.set_content("lots")
        for i in range(14):
            msg.add_attachment(b"%PDF-1.4 " + str(i).encode() * 10, maintype="application", subtype="pdf",
                               filename=f"r{i}.pdf")
        got = importer.extract_attachments(msg)
        self.assertEqual(len(got), importer.MAX_ATTACHMENTS_PER_MAIL)
        self.assertEqual(importer.MAX_ATTACHMENTS_PER_MAIL, 10)

    def test_huge_emails_are_skipped_without_downloading(self):
        from app.services import importer
        client = FakeImap({b"1": b"Subject: x\r\n\r\nbody"}, sizes={b"1": importer.MAX_MAIL_BYTES + 1})
        with self.mailbox(client), mock.patch.object(importer, "import_bytes") as imp:
            self.assertEqual(importer.poll_mailbox(), [])
        imp.assert_not_called()
        self.assertNotIn((b"1", "(BODY.PEEK[])"), client.fetched)
        self.assertIn((b"1", "+FLAGS", "\\Seen"), client.stored)

    def mailbox(self, client):
        from app.services import importer
        settings = SimpleNamespace(imap_host="mail.example", imap_user="u", imap_password="p", imap_port=993,
                                   imap_ssl=True, imap_folder="INBOX", import_home="", imap_allow_plaintext=False,
                                   imap_allowed_senders=getattr(self, "allowed_senders", ""), max_waiting_receipts=200)
        stack = [mock.patch.object(importer, "get_settings", return_value=settings),
                 mock.patch.object(importer.imaplib, "IMAP4_SSL", return_value=client)]

        class Both:
            def __enter__(self_inner):
                for p in stack:
                    p.start()

            def __exit__(self_inner, *exc):
                for p in reversed(stack):
                    p.stop()
        return Both()


class FakeImap:
    def __init__(self, messages, sizes=None):
        self.messages = messages
        self.sizes = sizes or {k: len(v) for k, v in messages.items()}
        self.fetched, self.stored = [], []

    def login(self, *a):
        return "OK", []

    def select(self, *a):
        return "OK", []

    def search(self, *a):
        return "OK", [b" ".join(self.messages)]

    def fetch(self, number, what):
        self.fetched.append((number, what))
        if what == "(RFC822.SIZE)":
            return "OK", [number + b" (RFC822.SIZE %d)" % self.sizes[number]]
        return "OK", [(number + b" (BODY[] {n})", self.messages[number]), b")"]

    def store(self, *a):
        self.stored.append(a)
        return "OK", []

    def logout(self):
        pass


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class SchedulerTests(unittest.TestCase):
    def test_a_failing_mailbox_still_reads_the_folder_drafts(self):
        from app.services import importer, scheduler
        reading = mock.AsyncMock()
        with mock.patch.object(importer, "scan_folder", return_value=["d1"]), \
                mock.patch.object(importer, "poll_mailbox", side_effect=RuntimeError("boom")), \
                mock.patch.object(importer, "start_reading", reading), \
                self.assertLogs(level="ERROR"):
            asyncio.run(scheduler._import_once(5))
        reading.assert_awaited_once_with(["d1"])

    def test_a_failing_folder_still_reads_the_mail_drafts(self):
        from app.services import importer, scheduler
        reading = mock.AsyncMock()
        with mock.patch.object(importer, "scan_folder", side_effect=RuntimeError("boom")), \
                mock.patch.object(importer, "poll_mailbox", return_value=["m1"]), \
                mock.patch.object(importer, "start_reading", reading), \
                self.assertLogs(level="ERROR"):
            asyncio.run(scheduler._import_once(10))
        reading.assert_awaited_once_with(["m1"])


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ImageRouteTests(DbCase):
    def test_missing_file_is_404(self):
        from fastapi import HTTPException
        from app.api import images as images_api
        from app.db import get_db_session
        from app.db.models import ReceiptImage
        from app.services import drafts as drafts_service
        with get_db_session()() as db:
            draft = drafts_service.create_draft(db, "u_pat", home_id="h1")
            image = ReceiptImage(receipt_draft_id=draft.id, image_type="original",
                                 file_path=os.path.join(self.dir, "gone.jpg"), mime_type="image/jpeg")
            db.add(image)
            db.commit()
            with self.assertRaises(HTTPException) as caught:
                images_api.get_draft_image(draft.id, image.id, db)
            self.assertEqual(caught.exception.status_code, 404)

    def test_upload_answer_has_no_server_path_or_error_detail(self):
        from fastapi import HTTPException
        from app.api import images as images_api
        from app.db import get_db_session
        from app.services import drafts as drafts_service
        with get_db_session()() as db:
            draft = drafts_service.create_draft(db, "u_pat", home_id="h1")
            upload = SimpleNamespace(file=BytesIO(self.png()), content_type="image/png", filename="r.png")
            answer = images_api.upload_draft_image(draft.id, upload, db)
            self.assertNotIn("file_path", answer)
            self.assertNotIn("file_path", answer["images"][0])
            self.assertNotIn(self.dir, str(answer))
            with mock.patch.object(images_api.images_service, "store_image_bytes",
                                   side_effect=RuntimeError("disk full at /data/receipts/secret")), \
                    self.assertLogs(level="ERROR"), self.assertRaises(HTTPException) as caught:
                upload.file.seek(0)
                images_api.upload_draft_image(draft.id, upload, db)
            self.assertEqual(caught.exception.status_code, 500)
            self.assertEqual(caught.exception.detail, "Failed to store image")


if __name__ == "__main__":
    unittest.main()
