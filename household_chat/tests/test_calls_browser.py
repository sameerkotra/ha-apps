"""Voice calls in two real browsers (SPEC §15.12): the app runs in uvicorn, two headless Chromium pages with fake
microphones call each other, sound flows both ways, and the notes appear. Skipped when Playwright (or a browser
for it) isn't installed."""
import _env  # noqa: F401

import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

try:
    from playwright.sync_api import sync_playwright
except ImportError:          # pragma: no cover
    sync_playwright = None

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE = {"admin": ("u-admin", "admin", "Meera"), "nisha": ("u-nisha", "nisha", "Nisha"),
          "tarun": ("u-tarun", "tarun", "Tarun")}
STATUS = "((document.querySelector('#callStatus') || {}).textContent || '')"
INBOUND = """async () => { let r = 0; (await call.pc.getStats()).forEach((x) => {
  if (x.type === 'inbound-rtp' && x.kind === 'audio') r = x.packetsReceived; }); return r; }"""


def headers(who):
    uid, name, display = PEOPLE[who]
    return {"X-Remote-User-Id": uid, "X-Remote-User-Name": name, "X-Remote-User-Display-Name": display}


def chromium(pw):
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
    try:
        return pw.chromium.launch(args=args)
    except Exception:
        hits = sorted(glob.glob(os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"),
                                             "chromium-*/chrome-linux*/chrome")))
        if not hits:
            raise unittest.SkipTest("no browser for Playwright")
        return pw.chromium.launch(executable_path=hits[-1], args=args)


@unittest.skipUnless(sync_playwright, "playwright not installed")
class CallsInBrowsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hchat_calls_")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.base = f"http://localhost:{port}/"          # localhost is a secure context: the microphone works
        env = dict(os.environ, DATA_DIR=os.path.join(cls.tmp, "data"), SHARE_DIR=os.path.join(cls.tmp, "share", "household_chat"),
                   DB_PATH=os.path.join(cls.tmp, "data", "chat.db"), DEV_ADMINS="admin",
                   OPTIONS_PATH=os.path.join(cls.tmp, "none.json"), SUPERVISOR_TOKEN="")
        os.makedirs(os.path.join(cls.tmp, "share"))
        cls.log = open(os.path.join(cls.tmp, "server.log"), "w")
        cls.server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                       "--port", str(port), "--workers", "1", "--no-proxy-headers"],
                                      cwd=APP, env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "api/health")
                break
            except OSError:
                time.sleep(0.1)
        cls.pw = sync_playwright().start()
        try:
            cls.browser = chromium(cls.pw)
        except unittest.SkipTest:
            cls.pw.stop()
            cls.server.terminate()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.terminate()
        cls.server.wait(10)
        cls.log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def api(self, method, path, who="admin", body=None):
        req = urllib.request.Request(self.base + path, method=method, headers={**headers(who), "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")

    def page(self, who):
        # bypass_csp: Playwright's own checks run as page script; the app's pages allow only their files
        ctx = self.browser.new_context(extra_http_headers=headers(who), viewport={"width": 1000, "height": 700}, bypass_csp=True)
        self.addCleanup(ctx.close)
        ctx.grant_permissions(["microphone"], origin=self.base.rstrip("/"))
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        p.goto(self.base)
        return p

    def test_a_call_from_ringing_to_notes(self):
        for who in PEOPLE:
            self.api("GET", "api/me", who)
        for who in PEOPLE:
            self.api("PATCH", f"api/admin/people/{PEOPLE[who][0]}", "admin", {"disabled": False})
        self.api("PUT", "api/admin/settings", "admin", {"calls_enabled": True})
        d = self.api("POST", "api/conversations/direct", "nisha", {"userId": "u-tarun"})["id"]
        self.api("POST", f"api/conversations/{d}/messages", "nisha", {"body": "hi"})
        n, t = self.page("nisha"), self.page("tarun")
        n.click("#convList .conv:has-text('Tarun')")
        n.click("button[title='Call']")
        t.wait_for_selector("#callScreen button.answer", timeout=15000)
        n.wait_for_function(f"{STATUS} === 'Ringing…'")
        t.click("#callScreen button.answer")
        for p in (n, t):
            p.wait_for_function(f"/^\\d+:\\d\\d$/.test({STATUS})", timeout=20000)
        for _ in range(50):                             # sound arrives both ways
            if n.evaluate(INBOUND) > 10 and t.evaluate(INBOUND) > 10:
                break
            time.sleep(0.2)
        self.assertGreater(n.evaluate(INBOUND), 10)
        self.assertGreater(t.evaluate(INBOUND), 10)
        t.click("#callScreen button.mute")
        self.assertFalse(t.evaluate("call.stream.getAudioTracks()[0].enabled"))
        n.click("#callScreen button.hangup")
        for p in (n, t):
            p.wait_for_selector("#callScreen", state="detached", timeout=10000)
        t.click("#convList .conv:has-text('Nisha')")
        self.assertIn("📞 Incoming call", t.inner_text(".call-note"))
        self.assertIn("📞 Outgoing call", n.inner_text(".call-note"))
        # declined
        n.click("button[title='Call']")
        t.wait_for_selector("#callScreen button.decline", timeout=15000)
        t.click("#callScreen button.decline")
        n.wait_for_function(f"{STATUS} === 'Declined'", timeout=10000)
        msgs = self.api("GET", f"api/conversations/{d}/messages", "nisha")["messages"]
        self.assertEqual([m["call"]["outcome"] for m in msgs if m["kind"] == "call"], ["answered", "declined"])
