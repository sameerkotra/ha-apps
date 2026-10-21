"""The page in a real browser (Chromium through Playwright; skipped without it): My phone, one of my phones (with
do-not-monitor hours), the Check-now card, All phones (with Available devices and the Add-to-monitoring dialog) and a
phone's page at phone width (390 px) and computer width (1280 px), with no script errors or sideways scrolling."""
import _env  # noqa: F401  (must be first)

import socket
import threading
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                     # pragma: no cover
    sync_playwright = None

from base import ADMIN, PhonesCase


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class Browser(PhonesCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn
        from app.main import app
        cls.port = _free_port()
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning",
                                                   lifespan="off"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:                           # pragma: no cover — no browser installed
            cls.server.should_exit = True
            raise unittest.SkipTest(f"Chromium can't start: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.should_exit = True

    def page(self, width):
        ctx = self.browser.new_context(viewport={"width": width, "height": 860},
                                       extra_http_headers={k: v for k, v in ADMIN.items()})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        self.addCleanup(ctx.close)
        return page, errors

    def test_pages_at_both_widths(self):
        pid = self.add("Ann Pixel", user_id="u-ann")
        self.add("Spare Tab", user_id="u-ann", model="SM-X710", manufacturer="Samsung", monitored=False)
        self.report(pid, name="Ann Pixel", value="57")
        self.tick()
        self.put(f"/api/phones/{pid}/dnm", {"windows": [{"days": [0, 1, 2, 3, 4], "start": "22:00", "end": "07:00",
                                                          "pauseForwarding": False}]})
        base = f"http://127.0.0.1:{self.port}/"
        for width in (390, 1280):
            page, errors = self.page(width)
            page.goto(base + "#/")
            page.wait_for_selector(".my-card")
            self.assertIn("Ann's Pixel 8", page.inner_text("#myBody"))
            self.assertIn("Your phone isn't monitored — ask an admin.", page.inner_text("#myBody"))
            page.goto(base + f"#/mine/{pid}")
            page.wait_for_selector("#dnm-card .dnm-window")
            self.assertIn("At most 12 hours a day", page.inner_text("#dnm-card"))
            bar = page.locator("#topnav")
            self.assertTrue(bar.is_visible())
            page.goto(base + "#/phones")
            page.wait_for_selector(".phone-row")
            self.assertIn("Online", page.inner_text("#phonesBody"))
            self.assertIn("Not monitored weekdays 22:00–07:00", page.inner_text("#phonesBody"))
            page.click("#available button:has-text('Add to monitoring')")
            page.wait_for_selector(".modal select")
            self.assertIn("Tablet (guess)", page.inner_text("#available"))
            page.keyboard.press("Escape")
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width, width)
            page.goto(base + f"#/phone/{pid}")
            page.wait_for_selector("#phoneBody h3:has-text('Every sensor')")
            page.goto(base + f"#/check/{pid}")
            page.wait_for_selector(".check-card")
            page.goto(base + "#/admin")
            page.wait_for_selector("#tab-settings .card")
            self.assertEqual(errors, [], width)
