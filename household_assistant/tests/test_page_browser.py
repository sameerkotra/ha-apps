"""The page in headless Chromium (Playwright), against the real app served by uvicorn in this process and the fake
household of test_assistant: an answer arrives live (Server-Sent Events, no polling) and is drawn; 🔊 reads it aloud with the browser's speech synthesis,
and a question asked with 🎤 is read aloud when it is answered. Skipped when Playwright (or a browser) isn't there."""
import _env  # noqa: F401  (must be first)

import glob
import os
import socket
import threading
import time
import unittest
from unittest import mock

import uvicorn

from app import ai_client
from app.main import app
from fakes import Model
from test_assistant import ALICE, Household

try:
    from playwright.sync_api import sync_playwright
except ImportError:          # pragma: no cover
    sync_playwright = None

# Stand-ins for the browser's speech: what was said is kept in window.__said; 🎤 "hears" window.__heard.
SPEECH = """
window.__said = [];
const fake = (name, value) => Object.defineProperty(window, name, { value, configurable: true, writable: true });
fake("speechSynthesis", { speaking: false, cancel() {}, speak(u) { window.__said.push(u.text); } });
fake("SpeechSynthesisUtterance", function (text) { this.text = text; });
fake("SpeechRecognition", function () {
  this.start = () => setTimeout(() => this.onresult({ results: [[{ transcript: window.__heard }]] }), 10);
});
"""


def chromium(pw):
    try:
        return pw.chromium.launch()
    except Exception:
        hits = sorted(glob.glob(os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"),
                                             "chromium-*/chrome-linux*/chrome")))
        if not hits:
            raise unittest.SkipTest("no browser for Playwright")
        return pw.chromium.launch(executable_path=hits[-1])


@unittest.skipUnless(sync_playwright, "playwright not installed")
class PageInBrowser(Household):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}/"
        # the app's start-up already ran (Household); this server only serves requests
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning",
                                                   proxy_headers=False))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        end = time.monotonic() + 10
        while not cls.server.started and time.monotonic() < end:
            time.sleep(0.02)
        cls.pw = sync_playwright().start()
        try:
            cls.browser = chromium(cls.pw)
        except unittest.SkipTest:
            cls.pw.stop()
            cls.server.should_exit = True
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.should_exit = True
        cls.thread.join(10)
        super().tearDownClass()

    def open_page(self):
        ctx = self.browser.new_context(extra_http_headers=ALICE)          # with the page's own CSP
        self.addCleanup(ctx.close)
        ctx.add_init_script(SPEECH)
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        p.goto(self.base)
        p.wait_for_selector("#askInput")
        return p

    def until(self, p, js, timeout=10.0):
        """Wait for `js` to be true (wait_for_function would need eval, which the page's policy refuses)."""
        end = time.monotonic() + timeout
        while not p.evaluate(js):
            if time.monotonic() > end:
                self.fail(f"never true: {js}")
            time.sleep(0.05)

    def test_read_aloud(self):
        model = Model('{"answer": "x"}', '{"answer": "Two tasks: **Bins** and the plumber."}')
        with mock.patch.object(ai_client, "generate", model):
            p = self.open_page()
            asked = []
            p.on("request", lambda req: asked.append(req.url.split("/api/", 1)[1]) if "/api/ask/" in req.url else None)
            p.fill("#askInput", "What's on today?")
            p.click("#askBtn")
            p.wait_for_selector(".answer .md")
            self.assertEqual(p.evaluate("window.__said"), [])           # typed: not read aloud by itself
            p.click(".answer .read-aloud")
            self.assertEqual(p.evaluate("window.__said"), ["x"])
            p.evaluate("window.__heard = 'Anything tomorrow?'")
            p.click("#micBtn")
            self.until(p, "document.querySelector('#askInput').value === 'Anything tomorrow?'")
            p.click("#askBtn")
            self.until(p, "window.__said.length === 2")
        self.assertEqual(p.evaluate("window.__said[1]"), "Two tasks: Bins and the plumber.")
        # followed live: each question read once and then its stream, never polled
        self.assertEqual([a.split("/", 2)[2] if a.count("/") > 1 else "" for a in asked], ["", "events", "", "events"])


if __name__ == "__main__":
    unittest.main()
