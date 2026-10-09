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
          "tarun": ("u-tarun", "tarun", "Tarun"), "leela": ("u-leela", "leela", "Leela")}
STATUS = "((document.querySelector('#callStatus') || {}).textContent || '')"
INBOUND = """async () => { let r = 0; for (const p of call.peers.values()) { if (!p.pc) continue; (await p.pc.getStats()).forEach((x) => {
  if (x.type === 'inbound-rtp' && x.kind === 'audio') r = Math.min(r || x.packetsReceived, x.packetsReceived); }); } return r; }"""
VIDEO_IN = """async () => { let r = 0; for (const p of call.peers.values()) { if (!p.pc) continue; (await p.pc.getStats()).forEach((x) => {
  if (x.type === 'inbound-rtp' && x.kind === 'video') r += x.framesDecoded || 0; }); } return r; }"""


def headers(who):
    uid, name, display = PEOPLE[who]
    return {"X-Remote-User-Id": uid, "X-Remote-User-Name": name, "X-Remote-User-Display-Name": display}


def chromium(pw):
    # sound may only start after a tap, as on phones and in the Home Assistant app
    args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=user-gesture-required"]
    try:
        return pw.chromium.launch(args=args)
    except Exception:
        hits = sorted(glob.glob(os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"),
                                             "chromium-*/chrome-linux*/chrome")))
        if not hits:
            raise unittest.SkipTest("no browser for Playwright")
        try:
            return pw.chromium.launch(executable_path=hits[-1], args=args)
        except Exception as e:                 # there, but it won't start here
            raise unittest.SkipTest("the browser for Playwright won't start: " + (str(e).splitlines() or [""])[0])


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
        except BaseException:
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
        # and each side actually plays it (desktop Chrome lets a page that uses the microphone play sound; phones are stricter)
        for p in (n, t):
            self.assertEqual(p.evaluate("[...call.peers.values()].map((x) => [x.audio.paused, x.audio.muted, x.audio.srcObject.getAudioTracks().length])"),
                             [[False, False, 1]])
            p.wait_for_selector(".call-tile .call-meter:not([hidden])", timeout=5000)
        # the camera can be turned on in a voice call: the other side gets the picture without a new negotiation
        n.click("#callScreen button.camera")
        n.wait_for_selector("#callScreen button.camera.on")
        t.wait_for_selector(".call-tile.has-video video", timeout=10000)
        for _ in range(50):
            if t.evaluate(VIDEO_IN) > 3:
                break
            time.sleep(0.2)
        self.assertGreater(t.evaluate(VIDEO_IN), 3)
        n.click("#callScreen button.camera")
        t.wait_for_function("!document.querySelector('.call-tile.has-video')", timeout=5000)
        # the other side is told about mute
        self.assertEqual(t.eval_on_selector_all("#callScreen button.speaker", "els => els.length"), 0)
        self.assertEqual(t.evaluate("document.querySelectorAll('#callScreen button.mute svg path').length"), 2)
        t.click("#callScreen button.mute")
        self.assertFalse(t.evaluate("call.stream.getAudioTracks()[0].enabled"))
        # muted: the microphone is drawn with a line across it
        self.assertEqual(t.evaluate("document.querySelectorAll('#callScreen button.mute.on svg path').length"), 3)
        n.wait_for_function("(document.querySelector('#callHint') || {}).textContent === 'Tarun has muted their microphone.'", timeout=8000)
        t.click("#callScreen button.mute")
        self.assertTrue(t.evaluate("call.stream.getAudioTracks()[0].enabled"))
        # ⚙: the microphone list (on Android the phone's sound routes) and, where labelled, the outputs
        t.click("#callScreen button.devices")
        t.wait_for_selector("#callPanel:not([hidden]) select")
        before = t.evaluate("call.stream.getAudioTracks()[0].id")
        t.select_option("#callPanel select[aria-label='Microphone']", index=t.evaluate("document.querySelector('#callPanel select').options.length") - 1)
        t.wait_for_function(f"call.stream.getAudioTracks()[0].id !== {json.dumps(before)}", timeout=5000)
        self.assertEqual(t.evaluate("[...call.peers.values()][0].pc.getSenders().find((x) => x.track && x.track.kind === 'audio').track.id"), t.evaluate("call.stream.getAudioTracks()[0].id"))
        if t.evaluate("CAN_PICK_OUTPUT"):
            t.select_option("#callPanel select[aria-label='Sound output']", index=0)
            t.wait_for_function("callAudio.sinkId === call.outputId", timeout=5000)
        # the Camera list shows every camera; choosing one turns the camera on (or switches it) and the others get it
        self.assertFalse(t.evaluate("call.cameraOn"))
        t.select_option("#callPanel select[aria-label='Camera']", index=1)
        t.wait_for_function("call.cameraOn && !!call.videoTrack", timeout=5000)
        n.wait_for_selector(".call-tile.has-video video", timeout=10000)
        t.click("#callScreen button.camera")
        t.wait_for_function("!call.cameraOn", timeout=5000)
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
        time.sleep(2.5)
        # the Calls list: both calls, Call back starts a call
        n.click("#side button[title='More']")
        n.click(".menu button:has-text('📞 Calls')")
        n.wait_for_selector(".call-row")
        self.assertEqual(n.evaluate("[...document.querySelectorAll('.call-row .hint')].map((e) => e.textContent.split(' · ')[0])"),
                         ["↗ Declined", "↗ Outgoing"])
        t.click("#side button[title='More']")
        t.click(".menu button:has-text('📞 Calls')")
        t.wait_for_selector(".call-row")
        self.assertEqual(t.evaluate("document.querySelectorAll('.call-row.missed').length"), 0)   # declined, not missed
        n.click(".call-row button:has-text('Call back')")
        t.wait_for_selector("#callScreen button.answer", timeout=15000)
        n.click("#callScreen button.hangup")
        t.wait_for_selector("#callScreen", state="detached", timeout=10000)
        t.click("#side button[title='More']")
        t.click(".menu button:has-text('📞 Calls')")
        t.wait_for_selector(".call-row.missed")
        # a group video call with three people: everyone sees and hears everyone, one leaving doesn't end it
        g = self.api("POST", "api/conversations", "nisha", {"name": "Trip", "memberIds": ["u-tarun", "u-leela"]})["id"]
        le = self.page("leela")
        for p in (n, t, le):
            for attempt in range(3):                        # a reload can be cut short by the page's own navigation
                try:
                    p.goto(self.base)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(0.5)
            p.wait_for_selector("#convList .conv")
        n.click("#convList .conv:has-text('Trip')")
        n.click("button[title='Video call']")
        for p in (t, le):
            p.wait_for_selector("#callScreen button.answer", timeout=15000)
        self.assertIn("Incoming video call from Nisha", t.inner_text("#callStatus"))
        t.click("#callScreen button.answer")
        le.click("#callScreen button.answer")
        for p in (n, t, le):
            p.wait_for_function(f"/^\\d+:\\d\\d$/.test({STATUS})", timeout=20000)
            p.wait_for_function("[...call.peers.values()].filter((x) => x.connected).length === 2", timeout=20000)
        for _ in range(60):
            if all(p.evaluate(INBOUND) > 10 and p.evaluate(VIDEO_IN) > 3 for p in (n, t, le)):
                break
            time.sleep(0.25)
        for p in (n, t, le):
            self.assertGreater(p.evaluate(INBOUND), 10)
            self.assertGreater(p.evaluate(VIDEO_IN), 3)
            self.assertEqual(p.evaluate("document.querySelectorAll('.call-tile.has-video:not(.mine)').length"), 2)
        t.click("#callScreen button.hangup")
        t.wait_for_selector("#callScreen", state="detached", timeout=10000)
        for p in (n, le):
            p.wait_for_function("call && call.peers.size === 1", timeout=10000)
        n.click("#callScreen button.hangup")
        le.wait_for_selector("#callScreen", state="detached", timeout=10000)
        n.wait_for_selector("#callScreen", state="detached", timeout=10000)
        gmsgs = self.api("GET", f"api/conversations/{g}/messages", "nisha")["messages"]
        [gc] = [m["call"] for m in gmsgs if m["kind"] == "call"]
        self.assertEqual((gc["group"], gc["kind"], gc["outcome"]), (True, "video", "answered"))
        self.assertEqual({m["state"] for m in gc["members"]}, {"left"})
        # New call…: pick a person and call, without opening their chat first
        n.click("#side button[title='More']")
        n.click(".menu button:has-text('New call')")
        n.wait_for_selector(".people-pick")
        n.click(".people-pick label:has-text('Tarun') input")
        n.click(".modal button:has-text('📞 Call'), button.primary:has-text('📞 Call')")
        t.wait_for_selector("#callScreen button.answer", timeout=15000)
        n.wait_for_function(f"{STATUS} === 'Ringing…'")
        n.click("#callScreen button.hangup")
        t.wait_for_selector("#callScreen", state="detached", timeout=10000)
        # deep links: the route after the page's address opens a chat or the ringing screen
        self.assertEqual(n.evaluate(f"routeOf('/a1b2c3d4_household_chat/chat/{d}', '/a1b2c3d4_household_chat')"), {"kind": "chat", "id": d})
        self.assertEqual(n.evaluate("routeOf('/a1b2c3d4_household_chat/call/abc', '/a1b2c3d4_household_chat')"), {"kind": "call", "id": "abc"})
        self.assertIsNone(n.evaluate("routeOf('/a1b2c3d4_household_chat/config/x', '/a1b2c3d4_household_chat')"))
        self.assertIsNone(n.evaluate("routeOf('/other/chat/abc', '/a1b2c3d4_household_chat')"))
        t.click("#side button[title='More']")
        t.click(".menu button:has-text('☆ Starred')")
        t.wait_for_selector(".page")
        self.assertTrue(t.evaluate(f"followRoute(routeOf('/x/chat/{d}', '/x'))"))
        t.wait_for_selector(".chat-head")
        self.assertEqual(t.evaluate("state.current"), d)
        # Test calling (Admin → App settings): the microphone and a local address at least
        a = self.page("admin")
        a.wait_for_selector("#convList .conv")
        a.click("#side button[title='More']")
        a.click(".menu button:has-text('Admin')")
        a.click(".tab:has-text('App settings')")
        a.wait_for_selector("button:has-text('Test calling')")
        a.click("button:has-text('Test calling')")
        a.wait_for_function("document.querySelector('.test-call-out') && document.querySelector('.test-call-out').textContent.includes('All good')", timeout=20000)
        self.assertIn("Microphone works", a.inner_text(".test-call-out"))
        msgs = self.api("GET", f"api/conversations/{d}/messages", "nisha")["messages"]
        self.assertEqual([m["call"]["outcome"] for m in msgs if m["kind"] == "call"], ["answered", "declined", "missed", "missed"])
