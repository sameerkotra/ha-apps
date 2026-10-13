"""The 13 Oct 2026 review's smaller fixes: fonts served by the app (SP7), the image and packaging pins (SP6),
amounts in whole cents (SPI1, SPI2), balance sensors pushed after the answer and only when changed, and stale ones
removed (SPP1, SPI3), cached versioned assets, the app-messages signing key and backup snapshots swept.
Every person here is invented."""
import _env  # noqa: F401  (must be first)

import os
import tempfile
import time
import unittest
from pathlib import Path

from app import app_messages, main
from test_admin_settings import FakeHA
from test_app import ADMIN, ALICE, Base

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"


class Fonts(Base):
    def test_nothing_is_loaded_from_elsewhere(self):
        for part in ("fonts.googleapis.com", "fonts.gstatic.com", "https:"):
            self.assertNotIn(part, main.CSP)
        self.assertIn("font-src 'self';", main.CSP)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("googleapis", html)
        self.assertIn('href="fonts/fonts.css?v=', html)

    def test_font_files_and_licences_are_there(self):
        css = (STATIC / "fonts" / "fonts.css").read_text(encoding="utf-8")
        files = set(__import__("re").findall(r"url\('([^']+\.woff2)'\)", css))
        self.assertEqual(len(files), 14)
        for name in files:
            with (STATIC / "fonts" / name).open("rb") as f:
                self.assertEqual(f.read(4), b"wOF2", name)
        for lic in ("OFL-Inter.txt", "OFL-SourceSerif4.txt"):
            self.assertIn("SIL Open Font License", (STATIC / "fonts" / lic).read_text(encoding="utf-8"))
        r = self.c.get("/fonts/inter-latin-400-normal.woff2")
        self.assertEqual(r.status_code, 200)


class Caching(Base):
    def test_versioned_assets_are_cached_for_good_and_the_page_never(self):
        r = self.c.get(f"/app.js?v={main.config.APP_VERSION}")
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(self.c.get("/").headers["cache-control"], "no-cache, no-store, must-revalidate")
        self.assertEqual(self.c.get("/api/health", headers=ALICE).headers["cache-control"], "no-store")
        self.assertNotIn("immutable", self.c.get("/app.js").headers.get("cache-control", ""))


class Packaging(unittest.TestCase):
    def test_image_and_requirements_pinned(self):
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-alpine3.22", docker)
        self.assertIn("apk add --no-cache tzdata", docker)
        reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8").split()
        self.assertIn("uvicorn==0.53.0", reqs)
        self.assertFalse([r for r in reqs if "[" in r or "==" not in r])

    def test_app_version_matches(self):
        cfg = (ROOT / "config.yaml").read_text(encoding="utf-8")
        self.assertIn(f'version: "{main.config.APP_VERSION}"', cfg)


class WholeCents(Base):
    def test_more_than_two_decimals_refused(self):
        for raw in ("0.004", "10.005", "12.345"):
            r = self.c.post(f"/api/groups/{self.gid}/expenses", headers={**ALICE, "Content-Type": "application/json"},
                            content=f'{{"description": "x", "amount": {raw}, "paidBy": "a"}}')
            self.assertEqual(r.status_code, 422, raw)
        self.expense(amount=0.07)                                  # two decimals: fine (0.07 * 100 isn't exactly 7)
        self.expense(amount=0.01)
        self.expense(splitType="custom", amount=10, splits=[{"userId": "a", "amount": 5.005},
                                                            {"userId": "b", "amount": 4.995}], status=422)
        r = self.c.post(f"/api/groups/{self.gid}/payments", headers=ALICE,
                        json={"fromUserId": "b", "toUserId": "a", "amount": 1.234})
        self.assertEqual(r.status_code, 422)

    def test_percent_split_uses_the_stored_amount(self):
        # 2.675 is stored as 2.67 or 2.68 depending on float rounding; the shares must add up to what's stored
        for amount in (2.68, 1000000, 0.03):
            shares = main.percent_shares(amount, [("a", 33.33), ("b", 33.33), ("c", 33.34)])
            self.assertEqual(sum(round(s["amount"] * 100) for s in shares), round(round(amount, 2) * 100))


class Sensors(Base):
    def setUp(self):
        super().setUp()
        main.SENSORS.forget()

    def test_pushed_after_the_answer_and_only_what_changed(self):
        with FakeHA(self) as ha:
            e = self.expense(amount=30)
            self.assertEqual(len(ha.balance_sensors()), 3)
            posts = ha.count("POST")
            r = self.c.put(f"/api/expenses/{e['id']}", headers=ALICE,
                           json={"description": "Renamed only", "amount": 30, "paidBy": "a"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(ha.count("POST"), posts)              # balances unchanged: nothing re-sent
            self.expense(amount=3, paid_by="b", participants=["a", "b"])
            self.assertEqual(ha.count("POST"), posts + 2)          # only Ann's and Ben's changed
            self.assertEqual(main.push_balances_to_ha(), 3)        # the background sync re-posts everything

    def test_a_push_failure_never_reaches_the_person_adding(self):
        orig = main.push_balances_to_ha
        main.push_balances_to_ha = lambda force=True: 1 / 0
        try:
            with self.assertLogs("splitpot", "ERROR"):
                self.expense(amount=5)
        finally:
            main.push_balances_to_ha = orig

    def test_sensors_nobody_has_any_more_are_removed(self):
        with FakeHA(self) as ha:
            self.expense(amount=30)
            self.assertIn("sensor.splitpot_balance_ann", ha.balance_sensors())
            with main.get_conn() as conn:                         # Ann renamed in Home Assistant
                conn.execute("UPDATE users SET name = 'Anna' WHERE id = 'a'")
                conn.commit()
            main.push_balances_to_ha()
            self.assertIn("sensor.splitpot_balance_anna", ha.balance_sensors())
            self.assertNotIn("sensor.splitpot_balance_ann", ha.balance_sensors())
            self.assertEqual(self.c.delete(f"/api/groups/{self.gid}", headers=ADMIN).status_code, 204)
            self.assertEqual(ha.balance_sensors(), {})             # the last group is gone: so are its sensors


class SigningKeyAndSnapshots(Base):
    def test_signing_key_is_an_app_setting_read_live(self):
        self.assertEqual(app_messages._signing_key(), "")
        r = self.c.put("/api/admin/settings", headers=ADMIN, json={"bus_signing_key": "s3cret-key"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("s3cret-key", r.text)                    # a secret: never sent back
        self.assertEqual(app_messages._signing_key(), "s3cret-key")
        ev = self.c.get("/api/events", headers=ALICE).json()[0]["message"]
        self.assertIn("app-messages signing key set", ev)
        self.assertNotIn("s3cret", ev)

    def test_backup_snapshots_are_named_and_swept(self):
        r = self.c.get("/api/admin/backup-db", headers=ADMIN)
        self.assertEqual(r.status_code, 200)
        folder = tempfile.gettempdir()
        stale = os.path.join(folder, main.SNAPSHOT_PREFIX + "left-behind.db")
        with open(stale, "wb") as f:
            f.write(b"x")
        os.utime(stale, (time.time() - 7 * 3600, time.time() - 7 * 3600))
        main.housekeeping()
        self.assertFalse(os.path.exists(stale))

    def test_restore_still_works_off_the_event_loop(self):
        self.expense(amount=30)
        snap = self.c.get("/api/admin/backup-db", headers=ADMIN).content
        self.expense(amount=12)
        r = self.c.post("/api/admin/import-db", headers=ADMIN, files={"file": ("b.db", snap)})
        self.assertEqual(r.status_code, 200, r.text)
        g = self.c.get(f"/api/groups/{self.gid}", headers=ALICE).json()
        self.assertEqual([e["amount"] for e in g["expenses"]], [30])


if __name__ == "__main__":
    unittest.main()
