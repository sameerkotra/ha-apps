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
from test_quick_flow import ADMIN, QuickHousehold

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
        try:
            return pw.chromium.launch(executable_path=hits[-1])
        except Exception as e:                 # there, but it won't start here
            raise unittest.SkipTest("the browser for Playwright won't start: " + (str(e).splitlines() or [""])[0])


class InBrowser:
    """Mixin: the real app served by uvicorn in this process and a headless Chromium, for a test household class."""

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
        except BaseException:
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


@unittest.skipUnless(sync_playwright, "playwright not installed")
class PageInBrowser(InBrowser, Household):
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
        # the ask box comes first, then the answers, newest first
        self.assertEqual(p.evaluate("[...document.querySelectorAll('#askForm, #conversation')].map((e) => e.id)"),
                         ["askForm", "conversation"])
        self.assertEqual(p.evaluate("[...document.querySelectorAll('.turn .question')].map((e) => e.textContent)"),
                         ["Anything tomorrow?", "What's on today?"])
        p.reload()                                                      # and so after a reload
        p.wait_for_selector(".turn .question")
        self.assertEqual(p.evaluate("[...document.querySelectorAll('.turn .question')].map((e) => e.textContent)"),
                         ["Anything tomorrow?", "What's on today?"])
        # followed live: each question read once and then its stream, never polled
        self.assertEqual([a.split("/", 2)[2] if a.count("/") > 1 else "" for a in asked], ["", "events", "", "events"])


    def test_morning_briefing_dialog(self):
        from app import briefing
        p = self.open_page()
        phones = [{"service": "mobile_app_alice_phone", "label": "Alice's phone"}]
        sent = []
        with mock.patch.object(briefing, "phones", lambda uid: phones), \
                mock.patch.object(briefing.ha_notify, "send_to_services",
                                  side_effect=lambda s, t, m, d=None: sent.append(m) or {x: True for x in s}):
            p.click("#briefingBtn")
            p.wait_for_selector(".briefing")
            self.assertIn("Sent to: Alice's phone", p.inner_text(".briefing"))
            self.assertIn("today's tasks and schedule", p.inner_text(".briefing"))
            p.check("#brOn")
            p.fill("#brTime", "06:45")
            p.select_option("#brDays", "weekdays")
            if os.environ.get("SHOT"):
                p.screenshot(path=os.environ["SHOT"])
            p.click(".briefing .btn-primary")
            self.until(p, "!document.querySelector('.briefing')")
            b = self.c.get("/api/briefing", headers=ALICE).json()
            self.assertEqual((b["on"], b["time"], b["days"]), (True, "06:45", "weekdays"))
            p.click("#briefingBtn")
            p.wait_for_selector(".briefing")
            self.assertTrue(p.is_checked("#brOn"))
            self.assertEqual(p.evaluate("[...document.querySelectorAll('.briefing .actions button')].map((b) => b.textContent)"),
                             ["Save"])                                  # Save is the only footer action
            p.click(".briefing .send-now >> nth=0")                     # Send a morning one now, in its own section
            self.until(p, "!document.querySelector('.briefing')")
            p.wait_for_selector(".turn .question")
            self.assertEqual(p.inner_text(".turn .question"), "Morning briefing")
        self.assertEqual(sent, ["📋 2 tasks today: Bins, Call plumber."])


@unittest.skipUnless(sync_playwright, "playwright not installed")
class QuickPageInBrowser(InBrowser, QuickHousehold):
    """Quick answers on the page (HOUSEHOLD_ASSISTANT_SPEC §17): the understood line, Not what I asked, the Did you
    mean buttons, and Admin → Quick answers. No model is called (QuickHousehold fails the test if one is)."""

    def test_understood_choices_and_not_this(self):
        p = self.open_page()
        p.fill("#askInput", "whats on my list 2day")
        p.click("#askBtn")
        p.wait_for_selector(".turn .understood")
        self.assertIn("Your tasks · today · Household Todo", p.inner_text(".turn .understood"))
        self.assertEqual(p.inner_text(".turn .answer .md"), "2 tasks today: Bins, Call plumber.")
        p.fill("#askInput", "balances")
        p.click("#askBtn")
        p.wait_for_selector(".turn .choices .chip")
        self.assertEqual(p.evaluate("[...document.querySelectorAll('.turn:first-child .choices .chip')]"
                                    ".map((b) => b.textContent)"), ["Who owes whom", "Account balances"])
        if os.environ.get("SHOT"):
            p.screenshot(path=os.environ["SHOT"].replace(".png", "-choices.png"), full_page=True)
        p.click(".turn:first-child .choices .chip >> nth=1")
        self.until(p, "(document.querySelector('.turn:first-child .answer .md') || {}).textContent"
                      " === 'Current account 1,200.00; card owes 300.00.'")
        self.assertEqual(p.evaluate("document.querySelectorAll('.turn:first-child .choices .chip').length"), 0)
        self.allow_model('{"answer": "Bob owes you 12.00."}')
        p.click(".turn:first-child .understood .link-btn")             # Not what I asked: asks the model
        self.until(p, "document.querySelectorAll('.turn').length === 3")
        self.until(p, "(document.querySelector('.turn:first-child .answer .md') || {}).textContent"
                      " === 'Bob owes you 12.00.'")
        self.assertEqual(p.evaluate("document.querySelectorAll('.turn:first-child .understood').length"), 0)

    def test_quick_only_note_and_admin_tab(self):
        from app import settings
        settings.update({"ai_on": False}, None)
        ctx = self.browser.new_context(extra_http_headers=ADMIN)
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(self.base)
        p.wait_for_selector("#askInput")
        self.until(p, "!document.querySelector('#quickOnly').hidden")
        p.click("#adminBtn")
        self.until(p, "location.hash === '#/admin/apps'")
        self.assertEqual(p.get_attribute("#adminBtn", "aria-current"), "page")
        p.click("#adminTabs .tab[data-tab=quick]")
        self.until(p, "location.hash === '#/admin/quick'")
        self.assertEqual(p.get_attribute("#adminTabs .tab[data-tab=quick]", "aria-current"), "page")
        p.wait_for_selector("#tab-quick input[aria-label='A question to test']")
        p.fill("#tab-quick input[aria-label='A question to test']", "whats on the shoping list tmrw")
        p.click("#tab-quick .btn-secondary")
        p.wait_for_selector("#tab-quick .quick-test pre")
        self.assertIn("Answered quickly", p.inner_text("#tab-quick .quick-test"))
        self.assertIn("shopping*", p.inner_text("#tab-quick .quick-test pre"))
        p.fill("#tab-quick input[aria-label='The words']", "how are we fixed")
        p.select_option("#tab-quick select[aria-label='What they mean']", "balances")
        p.click("#tab-quick .btn-primary")
        p.wait_for_selector("#tab-quick .quick-table td")
        self.assertIn("“how are we fixed”", p.inner_text("#tab-quick .quick-table"))
        heads = p.evaluate("[...document.querySelectorAll('#tab-quick h3')].map((x) => x.textContent)")
        self.assertEqual(heads, ["Asked but not answered quickly", "Test a question", "Phrases", "Words", "Replay"])
        p.click("#tab-quick button:has-text('Replay the last 100')")
        p.wait_for_selector("#tab-quick p:has-text('checked:')")
        p.fill("#tab-quick input[aria-label='The word']", "rota")
        p.click("#tab-quick button:has-text('Add word')")
        p.wait_for_selector("#tab-quick td:has-text('“rota”')")
        if os.environ.get("SHOT"):
            p.screenshot(path=os.environ["SHOT"].replace(".png", "-admin.png"), full_page=True)
        # the apps' speeds and who asks are on Usage; moving the phrases and words is on Storage
        p.click("#adminTabs .tab[data-tab=usage]")
        p.wait_for_selector("#tab-usage .summary-strip")
        heads = p.evaluate("[...document.querySelectorAll('#tab-usage h3')].map((x) => x.textContent)")
        self.assertEqual(heads, ["Each day", "How fast the apps answer", "Who asks"])
        p.click("#adminTabs .tab[data-tab=storage]")
        p.wait_for_selector("#storageQuick h3:has-text('Move to another install')")
        self.assertEqual(p.inner_text("#tab-storage .danger-zone h3"), "Restore a backup")
        # a reload keeps the tab; ← Ask goes back to the ask page
        p.reload()
        p.wait_for_selector("#storageQuick h3")
        self.assertFalse(p.is_visible("#askView"))
        p.click("#adminBackBtn")
        self.until(p, "!document.querySelector('#askView').hidden")
        self.assertIsNone(p.get_attribute("#adminBtn", "aria-current"))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
