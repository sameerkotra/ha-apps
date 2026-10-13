"""Performance and robustness fixes from the 13 Oct 2026 review (RP1-RP12, RI1-RI5). Where a query or a loop was
rewritten, the new result is compared with the old one on a seeded dataset. Needs the full requirements."""
import asyncio
import json
import os
import random
import sys
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_support import HAVE_DEPS, PAT, SAM, DbCase


def seed_purchases(db, n_receipts=12):
    """A chain, items with aliases and corrections, and receipts at the chain over several months."""
    from app.db.models import (CommonItem, CommonItemAlias, LineCorrection, PriceObservation, Receipt, ReceiptItem,
                               StoreChain, StoreLocation)
    from app.services import normalize
    key = normalize.normalize_store_name("Freshmart")[1]
    db.add(StoreChain(id="c1", name="Freshmart", normalized_name=key))
    db.flush()
    db.add(StoreLocation(id="l1", store_chain_id="c1", store_number="1"))
    db.flush()
    names = ["2% Milk", "Bananas", "Eggs", "Bread", "Crème fraîche"]
    for i, name in enumerate(names):
        db.add(CommonItem(id=f"i{i}", user_id="u_pat", home_id="h1", name=name, name_confirmed=1 if i % 2 == 0 else 0))
    db.flush()
    printed = ["MILK 2PCT", "BANANAS", "EGGS LG 12", "BREAD WHT", "CRÈME FRAÎCHE"]
    for i, text in enumerate(printed):
        db.add(CommonItemAlias(user_id="u_pat", common_item_id=f"i{i}", alias=text, normalized_alias=normalize.item_key(text),
                               source="USER_ADDED"))
    db.add(LineCorrection(store_chain_id="c1", original_key=normalize.item_key("MLK 2P"), corrected_text="MILK 2PCT"))
    db.flush()
    today = date.today()
    rnd = random.Random(4)
    for r in range(n_receipts):
        when = (today - timedelta(days=15 * r)).isoformat()
        db.add(Receipt(id=f"r{r}", user_id="u_pat", home_id="h1", store_location_id="l1", purchase_date=when,
                       grand_total=None if r % 3 == 0 else 20.0 + r, subtotal=None, currency_code="USD"))
        db.flush()
        for i, text in enumerate(printed):
            price = round(1 + i + rnd.random(), 2)
            weight = round(1 + rnd.random() * 2, 2) if i == 1 else None
            db.add(ReceiptItem(id=f"ri{r}_{i}", receipt_id=f"r{r}", common_item_id=f"i{i}", receipt_description=text,
                               quantity=2 if i == 2 and r % 2 else 1, weight_value=weight, weight_unit="lb" if weight else None,
                               unit_price=round(price / weight, 2) if weight else price,
                               unit_price_unit="LB" if weight else "EACH", line_total=price))
            db.flush()
            db.add(PriceObservation(user_id="u_pat", home_id="h1", common_item_id=f"i{i}", receipt_item_id=f"ri{r}_{i}",
                                    store_chain_id="c1", store_location_id="l1", purchase_date=when, quantity=1,
                                    unit_price=price, unit_price_unit="each", line_total=price, currency_code="USD"))
    db.commit()


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class SavingAReadingTests(DbCase):
    def test_rp1_saving_with_one_query_each_gives_the_same_lines(self):
        """The batch lookups (corrections, named items, last purchases) give exactly what the per-line ones gave."""
        from app.db.models import ReceiptDraftItem
        from app.services import corrections, fill_in, receipt_records
        from app.services.extraction import ExtractionService
        with self.session() as db:
            seed_purchases(db)
        reading = {"store_name": "Freshmart", "items": [
            {"receipt_description": d, "line_total": t, "quantity": q, "weight_value": None}
            for d, t, q in (("MLK 2P", 3.49, 1), ("BANANAS", 1.86, 1), ("EGGS LG 12", 6.98, 1), ("bread wht", 2.5, None),
                            ("CRÈME FRAÎCHE", 4.0, 1), ("UNKNOWN THING", 1.0, 1), ("MILK 2PCT", 3.49, 1))]}
        draft_id = self.draft(status="PROCESSING")

        def old_way(db, draft):
            chain_id = corrections.chain_for_store_name(db, reading["store_name"])
            out = []
            for line in reading["items"]:
                description = corrections.lookup(db, chain_id, line["receipt_description"]) or line["receipt_description"]
                known = receipt_records.find_confirmed_common_item(db, draft.home_id, description)
                item = ReceiptDraftItem(receipt_description=description, quantity=line.get("quantity"),
                                        weight_value=None, line_total=line["line_total"], raw_text="{}")
                fill_in.fill(db, draft.home_id, chain_id, item, known.id if known else None)
                out.append((description, known.id if known else None, item.quantity, item.weight_value, item.unit_price,
                            json.loads(item.raw_text).get("filled_from_last_time")))
            return out

        with self.session() as db:
            from app.db.models import ReceiptDraft
            draft = db.get(ReceiptDraft, draft_id)
            expected = old_way(db, draft)
            ExtractionService.__new__(ExtractionService).save_extraction_to_draft(db, draft, reading)
            from app.services import drafts as drafts_service
            got = [(i.receipt_description, i.suggested_common_item_id, i.quantity, i.weight_value, i.unit_price,
                    json.loads(i.raw_text).get("filled_from_last_time")) for i in drafts_service.get_draft_items(db, draft_id)]
        self.assertEqual(got, expected)
        self.assertEqual(got[0][0], "MILK 2PCT")          # the correction applied
        self.assertIsNotNone(got[1][3])                  # a weight filled in from last time


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ReadOnlyPagesTests(DbCase):
    def test_rp2_deals_are_left_out_without_deleting_and_purged_daily(self):
        from app.db.models import CommonItem, StoreChain, WebDeal
        from app.services import webinfo
        now = datetime.utcnow()
        today = date.today()
        with self.session() as db:
            db.add(StoreChain(id="c9", name="Valuco", normalized_name="valuco"))
            db.add(CommonItem(id="i9", user_id="u_pat", home_id="h1", name="Tea"))
            db.flush()
            rows = [("DEAL", (today - timedelta(days=1)).isoformat(), now), ("DEAL", (today + timedelta(days=2)).isoformat(), now),
                    ("DEAL", None, now - timedelta(days=30)), ("DEAL", None, now - timedelta(days=2)),
                    ("PRICE", None, now - timedelta(days=40)), ("PRICE", None, now - timedelta(days=1))]
            for n, (kind, valid_to, fetched) in enumerate(rows):
                db.add(WebDeal(id=f"d{n}", home_id="h1", store_chain_id="c9", common_item_id="i9", product="Tea", price=2.0 + n,
                               kind=kind, valid_to=valid_to, fetched_at=fetched, source_url="https://x.example"))
            db.commit()
            before = webinfo.deal_rows(db, "h1")
            history = webinfo.price_history(db, "h1")
            self.assertEqual(db.query(WebDeal).count(), 6)        # reading deletes nothing
            self.assertEqual(webinfo.purge_old_deals(db), 3)
            self.assertEqual(webinfo.deal_rows(db, "h1"), before)
            self.assertEqual(webinfo.price_history(db, "h1"), history)

    def test_rp5_a_months_receipts_add_up_as_before(self):
        from sqlalchemy import func
        from app.db.models import Receipt, ReceiptItem
        from app.services import analysis_data
        with self.session() as db:
            seed_purchases(db)
            start = (date.today() - timedelta(days=60)).isoformat()
            end = (date.today() - timedelta(days=10)).isoformat()
            rows = analysis_data.load_receipts(db, "h1", start, end)
            every = dict(db.query(ReceiptItem.receipt_id, func.sum(ReceiptItem.line_total)).join(Receipt).group_by(
                ReceiptItem.receipt_id).all())
            for r in rows:
                receipt = db.get(Receipt, r["id"])
                expected = receipt.grand_total if receipt.grand_total is not None else every[r["id"]]
                self.assertAlmostEqual(r["total"], expected)
            self.assertTrue(rows)

    def test_rp8_inflation_reads_only_whats_needed_with_the_same_answer(self):
        from app.services import insights
        from app.services.analysis_data import load_observations
        with self.session() as db:
            seed_purchases(db, n_receipts=30)
            expected = {"months": 6, **insights.basket_index(load_observations(db, "h1"), date.today(), 6)}
        r = self.client().get("/api/v1/homes/h1/analysis/inflation?months=6", headers=SAM)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), json.loads(json.dumps(expected)))

    def test_csv_export_route_guards_formulas(self):
        from app.db.models import Receipt, StoreChain, StoreLocation
        with self.session() as db:
            db.add(StoreChain(id="cx", name="=HYPERLINK(\"http://x\")", normalized_name="evil"))
            db.flush()
            db.add(StoreLocation(id="lx", store_chain_id="cx"))
            db.flush()
            db.add(Receipt(id="rx", user_id="u_pat", home_id="h1", store_location_id="lx", purchase_date="2026-01-02",
                           grand_total=1.0, currency_code="USD"))
            db.commit()
        r = self.client().get("/api/v1/homes/h1/export/receipts.csv", headers=SAM)
        self.assertEqual(r.status_code, 200)
        self.assertIn("'=HYPERLINK", r.text)
        self.assertNotIn(',=HYPERLINK', r.text)

    def test_rp8_unused_prices_route_is_gone(self):
        self.assertEqual(self.client().get("/api/v1/homes/h1/prices", headers=SAM).status_code, 404)


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class PlannerTests(unittest.TestCase):
    def test_rp3_the_driving_bound_finds_the_same_trips(self):
        from app.services import planner
        rnd = random.Random(11)
        for case in range(25):
            n, n_items = rnd.randint(2, 9), rnd.randint(2, 10)
            pts = [(rnd.uniform(0, 20), rnd.uniform(0, 20)) for _ in range(n + 1)]
            dist = [[((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 * rnd.uniform(1.0, 1.3) for b in pts] for a in pts]
            dur = [[d * 1.5 for d in row] for row in dist]
            stores = [{"id": f"s{i}", "name": f"S{i}"} for i in range(n)]
            items = [{"item_id": f"i{i}", "name": f"I{i}", "qty": 1, "amount": 1} for i in range(n_items)]
            offers = {f"i{i}": {f"s{s}": {"price": round(rnd.uniform(1, 9), 2), "unit": "each", "date": "2026-01-01",
                                     "estimated": False, "stale": False} for s in range(n) if rnd.random() < 0.7}
                      for i in range(n_items)}
            args = (items, offers, stores, dist, dur, rnd.uniform(0.1, 0.8))
            kw = {"per_min": rnd.uniform(0, 0.3), "stop_minutes": 5, "round_trip": case % 2 == 0}
            try:
                planner.USE_DRIVING_BOUND = False
                plain = planner.plan_trip(*args, **kw)
                planner.USE_DRIVING_BOUND = True
                bounded = planner.plan_trip(*args, **kw)
            finally:
                planner.USE_DRIVING_BOUND = True
            self.assertEqual(json.dumps(bounded, sort_keys=True, default=str), json.dumps(plain, sort_keys=True, default=str))


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class DatabaseTests(DbCase):
    def test_rp4_indexes(self):
        from app.db import get_engine
        from sqlalchemy import text
        with get_engine().connect() as conn:
            names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))}
        for wanted in ("idx_draft_items_draft", "idx_draft_taxes_draft", "idx_tax_records_receipt", "idx_tax_records_item",
                       "idx_store_tax_obs_receipt", "idx_line_corrections_chain_key", "idx_item_aliases_normalized",
                       "idx_prices_location", "idx_extraction_runs_draft", "idx_extraction_runs_receipt",
                       "idx_locations_address", "idx_drafts_home_hash"):
            self.assertIn(wanted, names)
        for gone in ("idx_drafts_home", "idx_receipts_home", "idx_price_home_item", "idx_price_home_date",
                     "idx_web_hours_location"):
            self.assertNotIn(gone, names)

    def test_ri4_unused_models_are_gone(self):
        import app.db as db_module
        for name in ("Job", "AuditLog", "RecommendationEvidence"):
            self.assertFalse(hasattr(db_module, name), name)


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class ReceiptsListTests(DbCase):
    def test_rp6_one_line_per_receipt_paged_without_gaps(self):
        from app.db.models import ReceiptDraft, ReceiptDraftItem
        ids = []
        for n in range(7):
            ids.append(self.draft(status="APPROVED", data={"store_name": f"Shop {n}", "grand_total": n, "tags": ["work"],
                                                           "purchase_date": "2026-01-02", "currency": "EUR"}))
        review = self.draft(status="PROCESSING", data={})
        with self.session() as db:
            for draft_id in ids:            # all in the same second: paging must still give each once
                db.get(ReceiptDraft, draft_id).created_at = datetime(2026, 1, 2, 3, 4, 5)
            db.add(ReceiptDraftItem(receipt_draft_id=ids[0], receipt_description="A", line_total=1.5))
            db.add(ReceiptDraftItem(receipt_draft_id=ids[0], receipt_description="B", line_total=2.0))
            db.commit()
        c = self.client()
        seen, after = [], None
        while True:
            url = "/api/v1/receipt-drafts/summaries?home_id=h1&group=saved&limit=3"
            if after:
                url += f"&before={after['created_at']}&before_id={after['id']}"
            page = c.get(url, headers=SAM).json()
            seen += page["drafts"]
            if not page["more"]:
                break
            after = page["drafts"][-1]
        self.assertEqual(sorted(d["id"] for d in seen), sorted(ids))
        first = next(d for d in seen if d["id"] == ids[0])
        self.assertEqual((first["item_count"], first["items_total"], first["tags"], first["currency"]), (2, 3.5, ["work"], "EUR"))
        self.assertNotIn("raw_llm_output", first)
        reviewing = c.get("/api/v1/receipt-drafts/summaries?home_id=h1&group=review", headers=SAM).json()["drafts"]
        self.assertEqual([d["id"] for d in reviewing], [review])
        states = c.get(f"/api/v1/extraction/status?ids={review},{ids[0]},nope", headers=SAM).json()
        self.assertEqual({s["draft_id"]: s["status"] for s in states}, {review: "PROCESSING", ids[0]: "APPROVED"})
        # the full list is limited too, with the same three queries for any number of drafts
        self.assertEqual(len(c.get("/api/v1/receipt-drafts?home_id=h1&limit=2", headers=SAM).json()), 2)

    def test_rp6_discarded_scans_are_deleted_after_their_time(self):
        from app.db.models import ReceiptDraft
        from app.services import drafts as drafts_service
        old = self.draft(status="REJECTED")
        recent = self.draft(status="REJECTED")
        with self.session() as db:
            db.get(ReceiptDraft, old).expires_at = datetime.utcnow() - timedelta(hours=1)
            db.commit()
            self.assertEqual(drafts_service.purge_rejected_drafts(db), 1)
            self.assertIsNone(db.get(ReceiptDraft, old))
            self.assertIsNotNone(db.get(ReceiptDraft, recent))

    def test_rp7_routes_on_the_thread_pool_still_know_who_is_asking(self):
        r = self.client().post("/api/v1/receipt-drafts", headers=SAM, json={"home_id": "h1"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["user_id"], "u_sam")

    def test_rp10_versioned_files_are_kept_pages_are_not(self):
        from app.config import APP_VERSION
        c = self.client()
        r = c.get(f"/static/app.js?v={APP_VERSION}", headers={**PAT, "Accept-Encoding": "gzip"})
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(r.headers.get("content-encoding"), "gzip")
        self.assertEqual(c.get("/static/app.js", headers=PAT).headers["cache-control"], "no-store")
        self.assertEqual(c.get("/list.html", headers=PAT).headers["cache-control"], "no-store")
        self.assertIn(f"static/app.js?v={APP_VERSION}", c.get("/list.html", headers=PAT).text)

    def test_ri1_receipts_left_reading_are_read_again(self):
        from app.db.models import ReceiptDraft
        from app.services import images as images_service
        from app.services import scheduler
        with_photo = self.draft(status="PROCESSING")
        without = self.draft(status="PROCESSING")
        with self.session() as db:
            images_service.store_image_bytes(db, with_photo, self.png(), "image/png", "r.png")
            db.get(ReceiptDraft, without).created_at = datetime.utcnow() - timedelta(hours=1)
            db.commit()
        again, failed = scheduler.interrupted_drafts()
        self.assertEqual((again, failed), ([with_photo], 1))
        with self.session() as db:
            self.assertEqual(db.get(ReceiptDraft, without).status, "EXTRACTION_FAILED")

    def test_ri1_a_receipt_never_read_is_never_expired(self):
        from app.db.models import ReceiptDraft
        from app.services import drafts as drafts_service
        reading = self.draft(status="PROCESSING")
        waiting = self.draft(status="NEEDS_REVIEW")
        with self.session() as db:
            for d in (reading, waiting):
                db.get(ReceiptDraft, d).expires_at = datetime.utcnow() - timedelta(hours=1)
            db.commit()
            self.assertEqual([d.id for d in drafts_service.get_expired_drafts(db)], [waiting])


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class RestoreTests(DbCase):
    def test_ri2_a_restore_waits_for_running_work_and_holds_new_requests(self):
        from app.services import backup, maintenance
        gate = maintenance.GATE
        entered = threading.Event()
        release = threading.Event()

        def job():
            with gate.work():
                entered.set()
                release.wait(5)
        t = threading.Thread(target=job)
        t.start()
        entered.wait(5)
        path = os.path.join(self.dir, "upload.db")
        backup.make_snapshot(path)
        with self.assertRaisesRegex(backup.BackupError, "busy"), mock.patch.object(backup, "RESTORE_WAIT_SECONDS", 0.2):
            backup.import_backup(path, "u_pat", "Pat")
        self.assertTrue(os.path.exists(path))            # nothing was changed, the upload is still there
        release.set()
        t.join(5)
        # while a restore holds the gate, other API requests are turned away
        with gate.pause(timeout=1):
            r = self.client().get("/api/v1/homes", headers=SAM)
            self.assertEqual(r.status_code, 503)
        self.assertEqual(self.client().get("/api/v1/homes", headers=SAM).status_code, 200)
        result = backup.import_backup(path, "u_pat", "Pat")
        self.assertIn("imported", result)


@unittest.skipUnless(HAVE_DEPS, "needs the app's requirements")
class SmallFixesTests(DbCase):
    def test_ri3_model_format_and_timeout(self):
        from app.services import extraction
        for url, fmt, expected in (("http://192.0.2.1:11434", "openai_compatible", "ollama"),
                                   ("http://192.0.2.1:11434/v1", "openai_compatible", "openai_compatible"),
                                   ("http://192.0.2.1:8000/v1", "openai_compatible", "openai_compatible"),
                                   ("http://192.0.2.1:8000", "ollama", "ollama")):
            self.app_settings.update({"model_url": url, "model_api_format": fmt, "model_name": "m",
                                      "model_timeout_seconds": 60}, "Pat")
            service = extraction.get_extraction_service()
            self.assertEqual(service._effective_format(), expected, url)
            self.assertEqual(service.timeout_seconds, 60)

    def test_ri3_numbers_and_json_from_the_model(self):
        from app.services import receipt_llm, textllm
        self.assertIsNone(receipt_llm.to_number(10 ** 400))
        self.assertIsNone(receipt_llm.to_number("9" * 400))
        self.assertIsNone(receipt_llm.to_number(1e300))
        self.assertEqual(receipt_llm.to_number("3,16"), 3.16)
        deep = "[" * 100_000 + "]" * 100_000
        with self.assertRaises(receipt_llm.ReceiptParseError):
            receipt_llm.parse_model_json(deep)
        with self.assertRaises(ValueError):
            textllm.parse_json_object("{" * 50_000)
        item = receipt_llm.normalize_extraction({"items": [{"receipt_description": "MILK", "quantity": -3, "weight_value": -1,
                                                            "line_total": 2.0}]}, "MDY")["items"][0]
        self.assertIsNone(item["quantity"])
        self.assertIsNone(item["weight_value"])

    def test_ri3_odd_answers_from_the_routing_service(self):
        from app.services import geo
        for answer in ({"code": "Ok", "distances": [[0]], "durations": "x"}, {"code": "Ok", "distances": [[0, None]]},
                       {"code": "Ok", "routes": [{"distance": 1}]}, ["not", "a", "dict"]):
            with mock.patch.object(geo, "_get_json", return_value=answer):
                with self.assertRaises(geo.GeoError):
                    geo.route_matrix([(0, 0), (1, 1)])
                with self.assertRaises(geo.GeoError):
                    geo.route_geometry([(0, 0), (1, 1)])

    def test_ri3_daily_hour_across_a_clock_change(self):
        from zoneinfo import ZoneInfo
        from app.services import scheduler
        zone = ZoneInfo("Europe/Berlin")
        before_change = datetime(2026, 3, 28, 12, 0, tzinfo=zone)         # the clocks go forward that night
        nxt = scheduler.next_run(3, before_change)
        self.assertEqual((nxt.hour, nxt.day), (3, 29))
        self.assertEqual(nxt.utcoffset(), timedelta(hours=2))
        self.assertEqual(scheduler.next_run(13, before_change).day, 28)

    def test_ri3_job_reads_are_kept_a_short_while_only(self):
        from app.services import reqcache
        calls = []
        with reqcache.scope(ttl=0.05):
            reqcache.remember(("k",), lambda: calls.append(1) or [1])
            reqcache.remember(("k",), lambda: calls.append(1) or [1])
            time.sleep(0.06)
            reqcache.remember(("k",), lambda: calls.append(1) or [1])
        self.assertEqual(len(calls), 2)

    def test_ri3_one_lookup_of_a_kind_per_home(self):
        from app.services import webinfo
        started = []

        def slow(home_id):
            if webinfo.running_job("deals", home_id):
                return webinfo.running_job("deals", home_id)
            time.sleep(0.05)
            job = webinfo._new_job("deals", home_id, 1)
            started.append(job["id"])
            return job
        with mock.patch.object(webinfo, "_start_deals_job", slow):
            starter = webinfo._one_at_a_time(webinfo._start_deals_job)
            threads = [threading.Thread(target=starter, args=("h1",)) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(started), 1)
        with webinfo._jobs_lock:
            webinfo._jobs.clear()

    def test_ri3_left_over_exports_are_swept(self):
        from app.services import backup
        folder = os.path.dirname(os.environ["DATABASE_PATH"])
        old = os.path.join(folder, "export-abc.db")
        new = os.path.join(folder, "export-new.db")
        for p in (old, new):
            open(p, "w").close()
        t = time.time() - 7 * 3600
        os.utime(old, (t, t))
        self.assertEqual(backup.sweep_temp_files(), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_rp5_settings_and_notification_settings_change_at_once(self):
        from app.config import get_settings
        from app.services import notifier
        first = get_settings()
        self.assertIs(get_settings(), first)
        self.app_settings.update({"unit_system": "metric"}, "Pat")
        self.assertEqual(get_settings().unit_system, "metric")
        self.assertFalse(notifier.settings()["saved"])
        notifier.save({"devices": [], "bell": True, "kinds": {"alerts": True}, "price_drop_percent": 20, "nearby_meters": 250})
        self.assertEqual(notifier.settings()["price_drop_percent"], 20)

    def test_rp5_many_list_changes_make_one_sync_and_one_after(self):
        from app.services import background
        ran, gate = [], threading.Event()

        def work():
            ran.append(1)
            gate.wait(2)
        self.assertTrue(background.coalesce("k", work))
        time.sleep(0.05)
        for _ in range(5):
            self.assertFalse(background.coalesce("k", work))
        gate.set()
        for _ in range(100):
            if not background.pending("k"):
                break
            time.sleep(0.02)
        self.assertEqual(len(ran), 2)

    def test_bus_signing_key_is_a_secret_app_setting(self):
        from app import app_messages
        self.assertIn("bus_signing_key", self.app_settings.SECRETS)
        self.app_settings.update({"bus_signing_key": "k" * 32}, "Pat")
        self.assertEqual(app_messages._signing_key(), "k" * 32)
        self.assertEqual(self.app_settings.public(self.app_settings.values())["bus_signing_key"], "")

    def test_rp12_page_thumbnails_are_small(self):
        from PIL import Image
        from io import BytesIO
        from app.services import images as images_service
        draft_id = self.draft()
        with self.session() as db:
            image_id = images_service.store_image_bytes(db, draft_id, self.png((2000, 1500)), "image/png", "r.png").id
        r = self.client().get(f"/api/v1/receipt-drafts/{draft_id}/images/{image_id}?size=thumb", headers=SAM)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertLessEqual(max(Image.open(BytesIO(r.content)).size), images_service.THUMB_SIDE)


if __name__ == "__main__":
    unittest.main()
