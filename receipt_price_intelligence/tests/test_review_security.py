"""Security fixes from the 13 Oct 2026 review (R1-R18 and the hours evidence rule). Needs the full requirements."""
import email
import email.policy
import json
import os
import ssl
import sys
import time
import unittest
from email.message import EmailMessage
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_support import HAVE_DEPS, PAT, SAM, DbCase


def settings(**over):
    base = dict(imap_host="mail.example", imap_user="u", imap_password="p", imap_port=993, imap_ssl=True,
                imap_folder="INBOX", import_home="", imap_allow_plaintext=False, imap_allowed_senders="",
                max_waiting_receipts=200, import_folder="", web_search_monthly_limit=0, model_monthly_limit=0)
    base.update(over)
    return SimpleNamespace(**base)


class FakeImap:
    def __init__(self, messages, capabilities=("IMAP4REV1",)):
        self.messages = messages
        self.capabilities = capabilities
        self.stored, self.calls, self.selected = [], [], None

    def starttls(self, ssl_context=None):
        self.calls.append(("starttls", ssl_context))

    def login(self, *a):
        self.calls.append(("login",))
        return "OK", []

    def select(self, name):
        self.selected = name
        return "OK", []

    def search(self, *a):
        return "OK", [b" ".join(self.messages)]

    def fetch(self, number, what):
        if what == "(RFC822.SIZE)":
            return "OK", [number + b" (RFC822.SIZE %d)" % len(self.messages[number])]
        return "OK", [(number + b" (BODY[] {n})", self.messages[number]), b")"]

    def store(self, *a):
        self.stored.append(a)
        return "OK", []

    def logout(self):
        pass


def mail(sender="shop@example.com", attachments=()):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Receipt"
    msg.set_content("see attached")
    for name, data in attachments:
        msg.add_attachment(data, maintype="image", subtype="png", filename=name)
    return msg.as_bytes(policy=email.policy.default)


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class NotificationTests(DbCase):
    def test_r1_only_notify_services_are_ever_called(self):
        from app.services import ha
        with mock.patch.object(ha, "_request") as req:
            for domain, service in (("light", "turn_on/../x"), ("../states", "x"), ("notify", "a?b"), ("", "x")):
                with self.assertRaises(ha.HAError):
                    ha.call_service(domain, service)
            with self.assertRaises(ha.HAError):
                ha.notify("t", "m", "light.turn_on")
            with self.assertRaises(ha.HAError):
                ha.notify("t", "m", "notify.Phone")
            req.assert_not_called()
            ha.notify("t", "m", "notify.mobile_app_pixel")
            self.assertEqual(req.call_args.args[:2], ("POST", "/services/notify/mobile_app_pixel"))

    def test_r1_the_test_route_takes_only_an_offered_notify_service(self):
        from app.services import ha, notifier
        c = self.client()
        with mock.patch.object(ha, "available", return_value=True), \
                mock.patch.object(ha, "list_notify_services", return_value=[{"service": "notify.mobile_app_pixel"}]), \
                mock.patch.object(notifier, "send", return_value=1) as send:
            for bad in ("light.turn_on", "notify.x/../../states", "homeassistant.restart"):
                r = c.post("/api/v1/notifications/test", headers=SAM, json={"service": bad})
                self.assertEqual(r.status_code, 422, bad)
            r = c.post("/api/v1/notifications/test", headers=SAM, json={"service": "notify.someone_elses"})
            self.assertEqual(r.status_code, 400)
            send.assert_not_called()
            r = c.post("/api/v1/notifications/test", headers=SAM, json={"service": "notify.mobile_app_pixel"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(send.call_args.kwargs["devices"], ["notify.mobile_app_pixel"])

    def test_r2_household_notification_settings_are_saved_by_admins_only(self):
        c = self.client()
        body = {"devices": [], "bell": True, "kinds": {"alerts": True}, "price_drop_percent": 10, "nearby_meters": 250}
        self.assertEqual(c.put("/api/v1/notifications", headers=SAM, json=body).status_code, 403)
        self.assertEqual(c.put("/api/v1/notifications", headers=PAT, json=body).status_code, 200)
        self.assertFalse(c.get("/api/v1/notifications", headers=SAM).json()["can_edit"])
        self.assertTrue(c.get("/api/v1/notifications", headers=PAT).json()["can_edit"])

    def test_r2_where_someone_is_is_told_to_them_only(self):
        from app.services import ha, notifier, shoplist_nearby
        states = [{"entity_id": "person.pat", "attributes": {"user_id": "u_pat", "latitude": 1.0, "longitude": 2.0}},
                  {"entity_id": "device_tracker.phone", "attributes": {"latitude": 3.0, "longitude": 4.0}}]
        for tracker, owner, expected in (("person.pat", "u_pat", (1.0, 2.0)), ("person.pat", "u_sam", None),
                                         ("device_tracker.phone", "u_pat", None), ("device_tracker.phone", None, (3.0, 4.0))):
            with mock.patch.object(ha, "available", return_value=True), \
                    mock.patch.object(ha, "all_states", return_value=states), \
                    mock.patch.object(notifier, "settings", return_value={"devices": [], "tracker": tracker}):
                self.assertEqual(shoplist_nearby.position(owner=owner), expected, (tracker, owner))


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class DraftRouteTests(DbCase):
    def test_r3_the_models_output_cant_be_sent_back_and_fields_are_capped(self):
        draft_id = self.draft()
        c = self.client()
        r = c.patch(f"/api/v1/receipt-drafts/{draft_id}", headers=SAM,
                    json={"raw_llm_output": '{"store_name": "Forged"}', "raw_ocr_text": "x", "store_name": "Real Shop"})
        self.assertEqual(r.status_code, 200, r.text)
        data = json.loads(r.json()["raw_llm_output"])
        self.assertEqual(data["store_name"], "Real Shop")
        self.assertIsNone(r.json()["raw_ocr_text"])
        for field, value in (("store_name", "x" * 256), ("tags", ["t"] * 31), ("grand_total", 1e300)):
            self.assertEqual(c.patch(f"/api/v1/receipt-drafts/{draft_id}", headers=SAM, json={field: value}).status_code,
                             422, field)

    def test_r3_bodies_over_the_limit_are_refused(self):
        draft_id = self.draft()
        c = self.client()
        big = {"store_name": "x", "pad": "y" * (1 << 20)}
        r = c.patch(f"/api/v1/receipt-drafts/{draft_id}", headers=SAM, json=big)
        self.assertEqual(r.status_code, 413)
        # a photo may be bigger than a JSON body
        photo = self.png((50, 50)) + b"\0" * (2 << 20)
        r = c.post(f"/api/v1/receipt-drafts/{draft_id}/images", headers=SAM, files={"file": ("r.png", photo, "image/png")})
        self.assertNotEqual(r.status_code, 413, r.text)
        r = c.post(f"/api/v1/receipt-drafts/{draft_id}/images", headers=SAM,
                   files={"file": ("r.png", b"\0" * (26 << 20), "image/png")})
        self.assertEqual(r.status_code, 413)

    def test_r4_a_saved_receipt_cant_be_changed_without_reopening_it(self):
        draft_id = self.draft(status="APPROVED")
        c = self.client()
        self.assertEqual(c.patch(f"/api/v1/receipt-drafts/{draft_id}", headers=SAM, json={"store_name": "X"}).status_code, 409)
        r = c.put(f"/api/v1/receipt-drafts/{draft_id}/items", headers=SAM,
                  json={"items": [{"receipt_description": "MILK", "line_total": 1.0}]})
        self.assertEqual(r.status_code, 409)
        for status in ("NEEDS_REVIEW", "EXTRACTION_FAILED", "PROCESSING"):
            other = self.draft(status=status)
            self.assertEqual(c.patch(f"/api/v1/receipt-drafts/{other}", headers=SAM, json={"store_name": "X"}).status_code,
                             200, status)

    def test_r5_raw_item_and_tax_routes_are_gone(self):
        draft_id = self.draft()
        c = self.client()
        self.assertIn(c.post(f"/api/v1/receipt-drafts/{draft_id}/items", headers=SAM, json={"bogus": 1}).status_code, (404, 405))
        self.assertIn(c.post(f"/api/v1/receipt-drafts/{draft_id}/taxes", headers=SAM, json={"bogus": 1}).status_code, (404, 405))

    def test_r6_paths_and_servers_are_for_admins(self):
        from app.services import images as images_service
        draft_id = self.draft()
        with self.session() as db:
            images_service.store_image_bytes(db, draft_id, self.png(), "image/png", "r.png")
        c = self.client()
        r = c.get(f"/api/v1/receipt-drafts/{draft_id}", headers=SAM)
        self.assertNotIn("file_path", r.json()["images"][0])
        self.assertNotIn(self.dir, r.text)
        self.app_settings.update({"import_folder": "/share/receipts", "imap_host": "mail.secret.example", "imap_user": "u",
                                  "imap_password": "p", "receipt_archive_path": "/share/archive"}, "Pat")
        member = c.get("/api/v1/import/status", headers=SAM).json()
        self.assertIsNone(member["folder"])
        self.assertIsNone(member["mailbox"])
        self.assertTrue(member["mailbox_ready"])
        self.assertEqual(c.get("/api/v1/import/status", headers=PAT).json()["mailbox"], "mail.secret.example")
        self.assertIsNone(c.get("/api/v1/archive", headers=SAM).json()["path"])
        self.assertEqual(c.get("/api/v1/archive", headers=PAT).json()["path"], "/share/archive")
        cfg = c.get("/api/v1/planner/config", headers=SAM).json()
        self.assertIsNone(cfg["geocoder"])
        self.assertTrue(c.get("/api/v1/planner/config", headers=PAT).json()["geocoder"])

    def test_r7_new_scans_wait_once_a_home_has_enough_waiting(self):
        self.app_settings.update({"max_waiting_receipts": 10}, "Pat")
        for _ in range(10):
            self.draft(status="NEEDS_REVIEW")
        r = self.client().post("/api/v1/receipt-drafts", headers=SAM, json={"home_id": "h1"})
        self.assertEqual(r.status_code, 409)
        self.assertIn("waiting", r.json()["detail"])

    def test_r7_one_folder_check_at_a_time(self):
        from app.services import importer
        self.assertTrue(importer._folder_lock.acquire())
        try:
            with mock.patch.object(importer, "_scan_folder", return_value=["x"]) as scan:
                self.assertEqual(importer.scan_folder(), [])
            scan.assert_not_called()
        finally:
            importer._folder_lock.release()

    def test_r8_no_api_description_pages(self):
        c = self.client()
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(c.get(path, headers=PAT).status_code, 404, path)

    def test_ri5_create_takes_a_known_source_and_approve_no_store_ids(self):
        from app.api import drafts as drafts_api
        c = self.client()
        self.assertEqual(c.post("/api/v1/receipt-drafts", headers=SAM, json={"home_id": "h1", "source": "x" * 80}).status_code, 422)
        self.assertEqual(c.post("/api/v1/receipt-drafts", headers=SAM, json={"home_id": "h1", "source": "upload"}).status_code, 201)
        self.assertNotIn("store_chain_id", drafts_api.DraftApproveRequest.model_fields)
        self.assertNotIn("store_location_id", drafts_api.DraftApproveRequest.model_fields)

    def test_ri5_photos_of_a_saved_receipt_cant_be_deleted(self):
        from app.services import images as images_service
        draft_id = self.draft()
        with self.session() as db:
            image_id = images_service.store_image_bytes(db, draft_id, self.png(), "image/png", "r.png").id
        with self.session() as db:
            from app.db.models import ReceiptDraft
            db.get(ReceiptDraft, draft_id).status = "APPROVED"
            db.commit()
        self.assertEqual(self.client().delete(f"/api/v1/receipt-drafts/{draft_id}/images/{image_id}", headers=SAM).status_code, 409)

    def test_ri5_store_discounts_check_the_home(self):
        c = self.client()
        self.assertEqual(c.get("/api/v1/homes/nope/store-discounts", headers=SAM).status_code, 404)
        self.assertEqual(c.get("/api/v1/homes/h1/store-discounts", headers=SAM).status_code, 200)


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class HostilePageTests(unittest.TestCase):
    """R10: tag stripping runs in linear time on pages built to make regular expressions backtrack."""

    PAGES = ["<svg " * 40_000, "<" * 200_000, "<script>" * 25_000, "<a" * 100_000, "<!--" * 50_000,
             "<p itemprop='price' " * 10_000, "<meta " * 40_000, ("<div>" + "x" * 50) * 4000]

    def test_r10_hostile_pages_are_read_quickly(self):
        from app.services import priceextract, websearch
        for page in self.PAGES:
            started = time.monotonic()
            priceextract.extract(page)
            priceextract.page_text(page)
            priceextract.extract_hours(page)
            websearch.clean_page_text("<html><body>" + page)
            self.assertLess(time.monotonic() - started, 3.0, page[:20])

    def test_r10_ordinary_pages_read_as_before(self):
        """The one-pass reader gives what the regular expressions gave on ordinary pages."""
        import html as htmllib
        import re
        from app.services import priceextract, websearch
        old_script_style = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
        old_block = re.compile(r"</?(?:div|p|li|tr|td|th|h\d|section|article|br|ul|ol|table|header|footer|span)\b[^>]*>", re.I)
        old_tag = re.compile(r"<[^>]+>")
        old_ws_tag = re.compile(r"<(script|style|noscript)\b.*?</\1>|<[^>]+>", re.S | re.I)

        def old_page_text(raw):
            text = old_script_style.sub(" ", raw)
            text = old_block.sub("\n", text)
            text = htmllib.unescape(old_tag.sub(" ", text))
            text = re.sub(r"[ \t\r\f\v]+", " ", text)
            return re.sub(r"\n\s*\n+", "\n", text).strip()

        def old_clean(raw):
            text = htmllib.unescape(old_ws_tag.sub(" ", raw))
            text = re.sub(r"[ \t\r\f\v]+", " ", text)
            return re.sub(r"\n\s*\n+", "\n", text).strip()[:websearch.MAX_PAGE_CHARS]

        pages = [
            "<html><head><title>Milk</title><style>p{color:red}</style></head><body><div class='a'>Great Value 2% Milk 1 gal"
            "</div><div>$3.79</div><script>var x = '<div>';</script><ul><li>Bananas $0.58/lb</li><li>Eggs &amp; more $2.99"
            "</li></ul><svg><path d='M0'/></svg><p>Was $4.98 Now $4.12</p><br/>Bread<span>$2.50</span></body></html>",
            "<html><body><table><tr><td>Kroger 2% Milk</td><td>1 gal</td><td>$3.29</td></tr></table>"
            "<noscript>enable js</noscript><h2>Apples</h2><div>$1.49 / lb</div></body></html>",
        ]
        for raw in pages:
            self.assertEqual(priceextract.page_text(raw), old_page_text(raw))
            self.assertEqual(websearch.clean_page_text(raw), old_clean(raw))
        meta = ('<html><head><meta property="og:title" content="Oat Milk 64 oz"><meta property="product:price:amount" '
                'content="4.29"></head><body><span itemprop="price" content="9.99">x</span></body></html>')
        self.assertEqual([(x["product"], x["price"]) for x in priceextract.from_meta(meta)], [("Oat Milk 64 oz", 4.29)])
        micro = '<div><span content="2.49" itemprop="price"></span><h1 itemprop="name">Butter 1 lb</h1></div>'
        self.assertEqual([(x["product"], x["price"]) for x in priceextract.from_meta(micro)], [("Butter 1 lb", 2.49)])
        ld = ('<script type="application/ld+json">{"@type":"Product","name":"Tea","offers":{"price":"3.10"}}</script>')
        self.assertEqual([(x["product"], x["price"]) for x in priceextract.from_jsonld(ld)], [("Tea", 3.1)])


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ImageTests(DbCase):
    def bomb(self):
        """A PNG of a few kilobytes that claims 100 million pixels."""
        from io import BytesIO
        from PIL import Image
        out = BytesIO()
        Image.new("1", (10_000, 10_000)).save(out, "PNG")
        return out.getvalue()

    def test_r11_a_pixel_bomb_is_refused_before_decoding(self):
        from app.services import images as images_service
        from app.services.extraction import ExtractionService
        bomb = self.bomb()
        self.assertLess(len(bomb), 200_000)
        with self.assertRaisesRegex(ValueError, "too large"):
            images_service.check_image_bytes(bomb)
        draft_id = self.draft()
        r = self.client().post(f"/api/v1/receipt-drafts/{draft_id}/images", headers=SAM,
                               files={"file": ("r.png", bomb, "image/png")})
        self.assertEqual(r.status_code, 400)
        path = os.path.join(self.dir, "bomb.png")
        with open(path, "wb") as f:
            f.write(bomb)
        with self.assertRaises(ValueError):
            ExtractionService.prepare_image_base64(path)

    def test_r11_photos_are_still_prepared(self):
        from app.services.extraction import ExtractionService
        path = os.path.join(self.dir, "ok.png")
        with open(path, "wb") as f:
            f.write(self.png((3000, 1000)))
        self.assertTrue(ExtractionService.prepare_image_base64(path))

    def test_r13_only_real_photos_are_kept_and_their_type_is_their_own(self):
        from app.services import images as images_service
        draft_id = self.draft()
        with self.session() as db:
            with self.assertRaises(ValueError):
                images_service.store_image_bytes(db, draft_id, b"<html>not a photo</html>" * 50, "image/jpeg", "r.jpg")
            image = images_service.store_image_bytes(db, draft_id, self.png(), "image/jpeg", "r.jpg")
            self.assertEqual(image.mime_type, "image/png")
            self.assertTrue(image.file_path.endswith(".png"))
        folder = os.path.join(self.dir, "img", "drafts", draft_id)
        self.assertEqual(len([n for n in os.listdir(folder) if not n.endswith(".thumb.jpg")]), 1)

    def test_r13_the_import_folder_never_follows_links(self):
        from app.services import importer
        folder = os.path.join(self.dir, "inbox")
        os.makedirs(folder)
        secret = os.path.join(self.dir, "secret.png")
        with open(secret, "wb") as f:
            f.write(self.png())
        os.symlink(secret, os.path.join(folder, "link.png"))
        with open(os.path.join(folder, "real.png"), "wb") as f:
            f.write(self.png((41, 30)))
        old = time.time() - 60
        os.utime(os.path.join(folder, "real.png"), (old, old))
        with mock.patch.object(importer, "get_settings", return_value=settings(import_folder=folder)):
            created = importer.scan_folder()
        self.assertEqual(len(created), 1)
        self.assertTrue(os.path.islink(os.path.join(folder, "link.png")))       # left alone, never read or moved
        self.assertTrue(os.path.exists(secret))

    def test_r18_the_archive_never_writes_through_a_link(self):
        from app.services import archive
        root = os.path.join(self.dir, "share", "receipts")
        os.makedirs(root)
        elsewhere = os.path.join(self.dir, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(root, archive.PENDING))
        with mock.patch.object(archive, "root", return_value=root):
            self.assertIsNone(archive.save_original("d1", b"data", "r.jpg"))
            self.assertEqual(os.listdir(elsewhere), [])
            os.remove(os.path.join(root, archive.PENDING))
            self.assertTrue(archive.save_original("d1", b"data", "r.jpg"))


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class MailTests(DbCase):
    def run_poll(self, client, **over):
        from app.services import importer
        with mock.patch.object(importer, "get_settings", return_value=settings(**over)), \
                mock.patch.object(importer.imaplib, "IMAP4_SSL", return_value=client) as ssl_client, \
                mock.patch.object(importer.imaplib, "IMAP4", return_value=client):
            created = importer.poll_mailbox()
        return created, ssl_client

    def test_r12_ssl_checks_the_certificate(self):
        client = FakeImap({})
        _created, ssl_client = self.run_poll(client)
        context = ssl_client.call_args.kwargs["ssl_context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_r12_without_ssl_starttls_or_no_password_at_all(self):
        client = FakeImap({}, capabilities=("IMAP4REV1", "STARTTLS"))
        self.run_poll(client, imap_ssl=False)
        self.assertEqual([c[0] for c in client.calls], ["starttls", "login"])
        plain = FakeImap({})
        with self.assertLogs("app.importer", "WARNING"):
            self.run_poll(plain, imap_ssl=False)
        self.assertNotIn(("login",), plain.calls)
        self.run_poll(plain, imap_ssl=False, imap_allow_plaintext=True)
        self.assertIn(("login",), plain.calls)

    def test_ri3_a_folder_with_spaces_is_quoted(self):
        from app.services import importer
        client = FakeImap({})
        self.run_poll(client, imap_folder='My "Receipts"')
        self.assertEqual(client.selected, '"My \\"Receipts\\""')
        self.assertEqual(importer.quote_mailbox("INBOX"), '"INBOX"')

    def test_r14_only_allowed_senders(self):
        from app.services import importer
        messages = {b"1": mail("shop@example.com", [("a.png", self.png((50, 40)) + b"\0" * importer.MIN_EMAIL_IMAGE_BYTES)]),
                    b"2": mail("stranger@evil.example", [("b.png", self.png((51, 40)) + b"\0" * importer.MIN_EMAIL_IMAGE_BYTES)])}
        client = FakeImap(messages)
        created, _ = self.run_poll(client, imap_allowed_senders="@example.com, other@x.org")
        self.assertEqual(len(created), 1)
        self.assertEqual({a[0] for a in client.stored}, {b"1", b"2"})     # both read; only one imported
        self.assertFalse(importer.sender_allowed(email.message_from_bytes(messages[b"2"]), ["@example.com"]))
        self.assertTrue(importer.sender_allowed(email.message_from_bytes(messages[b"2"]), []))

    def test_r14_a_full_home_leaves_mail_unread_and_repeats_are_skipped(self):
        from app.services import importer
        photo = self.png((52, 40)) + b"\0" * importer.MIN_EMAIL_IMAGE_BYTES
        client = FakeImap({b"1": mail(attachments=[("a.png", photo)]), b"2": mail(attachments=[("a.png", photo)])})
        created, _ = self.run_poll(client)
        self.assertEqual(len(created), 1)                                  # the second is the same file
        self.assertEqual(len(client.stored), 2)
        for _ in range(10):
            self.draft()
        full = FakeImap({b"3": mail(attachments=[("c.png", self.png((53, 40)) + b"\0" * importer.MIN_EMAIL_IMAGE_BYTES)])})
        with self.assertLogs("app.importer", "WARNING"):
            created, _ = self.run_poll(full, max_waiting_receipts=10)
        self.assertEqual(created, [])
        self.assertEqual(full.stored, [])                                  # stays unread for later

    def test_settings_check_the_sender_list(self):
        from app.app_settings import SettingsError
        self.app_settings.update({"imap_allowed_senders": "a@b.co, @shop.example"}, "Pat")
        with self.assertRaises(SettingsError):
            self.app_settings.update({"imap_allowed_senders": "not an address"}, "Pat")


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class WebLimitTests(DbCase):
    def test_r15_monthly_search_and_model_limits(self):
        from app.services import textllm, webcache, websearch
        self.app_settings.update({"web_search_monthly_limit": 2, "web_search_provider": "searxng",
                                  "web_search_url": "http://192.0.2.9:8080", "model_monthly_limit": 1,
                                  "model_url": "http://192.0.2.1:11434", "model_name": "m"}, "Pat")
        with mock.patch.object(websearch, "_searxng_search", return_value=[]) as search, \
                mock.patch.object(websearch, "_pace"):
            websearch.search("milk", use_cache=False)
            websearch.search("eggs", use_cache=False)
            with self.assertRaises(websearch.SearchBlocked):
                websearch.search("bread", use_cache=False)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(webcache.usage()["search"], {"used": 2, "limit": 2})
        with mock.patch.object(textllm.urllib.request, "urlopen", side_effect=OSError("no server")):
            with self.assertRaises(textllm.TextLLMError):
                textllm.complete_json("s", "u")
            with self.assertRaisesRegex(textllm.TextLLMError, "This month"):
                textllm.complete_json("s", "u")

    def test_r15_a_price_check_that_found_nothing_isnt_repeated_daily(self):
        from app.services import webinfo
        self.app_settings.update({"web_prices_days": 3, "web_deals_days": 0, "web_search_provider": "searxng",
                                  "web_search_url": "http://192.0.2.9:8080"}, "Pat")
        webinfo._last_prices_run.clear()
        with mock.patch.object(webinfo, "start_prices_job", return_value={"id": "j"}) as prices, \
                mock.patch.object(webinfo, "start_hours_job", side_effect=webinfo.WebInfoError("none")), \
                mock.patch.object(webinfo, "_wait"):
            webinfo.run_scheduled()
            webinfo.run_scheduled()
        self.assertEqual(prices.call_count, 1)

    def test_r16_answers_are_capped_in_size_and_time(self):
        from app.services import httpread

        class Slow:
            def read(self, n):
                time.sleep(0.05)
                return b"x"

        class Big:
            def read(self, n):
                return b"x" * n
        with self.assertRaises(httpread.TooLarge):
            httpread.read_capped(Big(), 1000, 5)
        self.assertEqual(len(httpread.read_capped(Big(), 1000, 5, truncate=True)), 1000)
        with self.assertRaises(httpread.TooSlow):
            httpread.read_capped(Slow(), 10_000, 0.2)

    def test_r17_addresses_that_lead_back_into_ipv4_are_refused(self):
        from app.services import websearch
        for address in ("64:ff9b::c0a8:101", "2002:c0a8:101::1", "64:ff9b:1::a00:1"):
            with mock.patch.object(websearch.socket, "getaddrinfo", return_value=[(0, 0, 0, "", (address, 0, 0, 0))]):
                self.assertIsNone(websearch._public_address("example.com"), address)
        with mock.patch.object(websearch.socket, "getaddrinfo", return_value=[(0, 0, 0, "", ("2606:4700::1111", 0, 0, 0))]):
            self.assertEqual(websearch._public_address("example.com"), "2606:4700::1111")

    def test_r9_debug_log_hides_keys_in_addresses(self):
        from app.services import webdebug
        entry = webdebug.record("page", "fetch", url="https://user:pw@x.example/p?api_key=SECRET1&q=milk&token=SECRET2")
        self.assertNotIn("SECRET", entry["url"])
        self.assertNotIn("pw", entry["url"])
        self.assertIn("q=milk", entry["url"])

    def test_r18_logs_and_messages_leave_out_addresses_and_server_text(self):
        from app.services import extraction
        self.assertEqual(extraction.redact_url("http://user:pw@192.0.2.1:11434/v1?key=abc"), "http://192.0.2.1:11434/v1")
        self.app_settings.update({"model_url": "http://user:pw@192.0.2.1:11434", "model_name": "m"}, "Pat")
        service = extraction.ExtractionService()

        class Answer:
            status_code, text = 500, "internal path /srv/secret and key sk-123"

        async def post(*a, **k):
            return Answer()
        import asyncio
        with mock.patch.object(service.client, "post", post), self.assertLogs("app.extraction", "ERROR"):
            with self.assertRaises(extraction.ExtractionError) as caught:
                asyncio.run(service._post_json("/api/chat", {}))
        self.assertNotIn("secret", str(caught.exception))

    def test_hours_are_applied_by_themselves_only_on_evidence(self):
        from app.db.models import StoreChain, StoreLocation
        from app.services import webinfo
        self.app_settings.update({"web_auto_hours": True}, "Pat")
        with self.session() as db:
            db.add(StoreChain(id="c1", name="Corner", normalized_name="corner"))
            db.flush()
            for lid in ("l1", "l2", "l3"):
                db.add(StoreLocation(id=lid, store_chain_id="c1", name="Corner"))
            db.commit()
            hours = {"Mo-Su": "08:00-20:00"}
            base = {"hours": hours, "confidence": 0.99, "source_url": "https://x.example"}
            self.assertEqual(webinfo._save_hours(db, "l1", {**base, "evidence": "model"}, False), "SUGGESTED")
            self.assertEqual(webinfo._save_hours(db, "l2", {**base, "evidence": "agreed"}, False), "APPLIED")
            self.assertEqual(webinfo._save_hours(db, "l3", {**base, "evidence": "structured"}, False), "APPLIED")

    def test_hours_from_two_sites_that_agree(self):
        from app.services import webinfo, webparse
        answers = iter([{"found": True, "hours": "Mo-Su 08:00-20:00", "confidence": 0.7},
                        {"found": True, "hours": "Mo-Su 08:00-20:00", "confidence": 0.7}])
        hits = [{"title": "Corner hours", "url": "https://a.example/h", "snippet": ""},
                {"title": "Corner hours", "url": "https://b.example/h", "snippet": ""}]
        page = {"raw": "", "text": "Corner Mon-Sun 8:00 - 20:00"}
        with mock.patch.object(webinfo.textllm, "complete_json", side_effect=lambda *a, **k: next(answers)), \
                mock.patch.object(webinfo.websearch, "search", return_value=hits), \
                mock.patch.object(webinfo, "_open", return_value=page), \
                mock.patch.object(webinfo, "_full_text", return_value=page["text"]), \
                mock.patch.object(webparse, "clean_hours_answer",
                                  side_effect=lambda a, t, n: {"hours": a["hours"], "confidence": a["confidence"], "note": None}):
            found = webinfo.find_hours({"chain_name": "Corner", "address": "1 Main St", "website": None})
        self.assertEqual(found["evidence"], "agreed")


if __name__ == "__main__":
    unittest.main()
