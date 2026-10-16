"""The open chat in a real browser: a live event (a reaction, a new message) changes only its own message's row —
the rest of the list stays the same elements (a long chat isn't drawn again for each event, and a voice message that
is playing keeps playing). Skipped when Playwright (or a browser for it) isn't installed."""
import _env  # noqa: F401

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request

import test_calls_browser as cb

APP = cb.APP


@unittest.skipUnless(cb.sync_playwright, "playwright not installed")
class ChatInBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="hchat_page_")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.base = f"http://localhost:{port}/"
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
        cls.pw = cb.sync_playwright().start()
        try:
            cls.browser = cb.chromium(cls.pw)
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
        req = urllib.request.Request(self.base + path, method=method, headers={**cb.headers(who), "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")

    def test_events_change_only_their_own_row(self):
        for who in ("admin", "nisha", "tarun"):
            self.api("GET", "api/me", who)
        for who in ("nisha", "tarun"):
            self.api("PATCH", f"api/admin/people/{cb.PEOPLE[who][0]}", "admin", {"disabled": False})
        hh = [c for c in self.api("GET", "api/conversations", "tarun")["conversations"] if c["isHousehold"]][0]["id"]
        first = self.api("POST", f"api/conversations/{hh}/messages", "tarun", {"body": "first"})["id"]
        second = self.api("POST", f"api/conversations/{hh}/messages", "tarun", {"body": "second"})["id"]
        ctx = self.browser.new_context(extra_http_headers=cb.headers("nisha"), viewport={"width": 1000, "height": 700}, bypass_csp=True)
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(self.base)
        p.click("#convList .conv:has-text('Household')")
        p.wait_for_selector(f"#m{second}")
        p.wait_for_function("!!state.sse && state.sse.readyState === 1", timeout=10000)
        # mark the rows as they are now: a row drawn again loses its mark
        p.evaluate(f"document.getElementById('m{first}').dataset.mark = 'kept'; document.getElementById('m{second}').dataset.mark = 'kept'")
        self.api("PUT", f"api/messages/{second}/reactions/{urllib.parse.quote('👍')}", "tarun")
        p.wait_for_selector(f"#m{second} .reaction", timeout=10000)
        self.assertEqual(p.evaluate(f"document.getElementById('m{first}').dataset.mark"), "kept")
        self.assertIsNone(p.evaluate(f"document.getElementById('m{second}').dataset.mark || null"))   # its own row is new
        # a new message is added at the end; the rows before it stay
        third = self.api("POST", f"api/conversations/{hh}/messages", "tarun", {"body": "third"})["id"]
        p.wait_for_selector(f"#m{third}", timeout=10000)
        self.assertEqual(p.evaluate(f"document.getElementById('m{first}').dataset.mark"), "kept")
        self.assertEqual(p.evaluate("[...document.querySelectorAll('#msgList .msg')].map((x) => x.id)"),
                         [f"m{first}", f"m{second}", f"m{third}"])
        self.assertTrue(p.evaluate(f"document.getElementById('m{third}').classList.contains('grouped')"))
        # someone coming online is patched in place too (the rows stay)
        self.api("POST", "api/presence", "tarun", {"tab": "tab-tarun", "visible": True})
        p.wait_for_function("state.online['u-tarun'] && state.online['u-tarun'].online", timeout=10000)
        self.assertEqual(p.evaluate(f"document.getElementById('m{first}').dataset.mark"), "kept")
        self.assertEqual(errors, [])

    def test_delete_a_one_to_one_chat(self):
        for who in ("admin", "nisha", "tarun"):
            self.api("GET", "api/me", who)
        for who in ("nisha", "tarun"):
            self.api("PATCH", f"api/admin/people/{cb.PEOPLE[who][0]}", "admin", {"disabled": False})
        c = self.api("POST", "api/conversations/direct", "tarun", {"userId": cb.PEOPLE["nisha"][0]})["id"]
        self.api("POST", f"api/conversations/{c}/messages", "tarun", {"body": "see you at 6"})
        ctx = self.browser.new_context(extra_http_headers=cb.headers("nisha"), viewport={"width": 1000, "height": 700}, bypass_csp=True)
        self.addCleanup(ctx.close)
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(self.base)
        row = f"#convList .conv[aria-label^='{cb.PEOPLE['tarun'][2]}']"
        p.click(row)
        p.wait_for_selector("#msgList .msg:has-text('see you at 6')")
        p.click("button[aria-label='Chat menu']")
        p.click(".menu button:has-text('Delete chat')")
        p.click(".modal button.danger:has-text('Delete chat'), dialog button.danger:has-text('Delete chat')")
        p.wait_for_selector(row, state="detached", timeout=10000)
        self.assertEqual(errors, [])
        # Tarun still has it
        self.assertIn(c, {x["id"] for x in self.api("GET", "api/conversations", "tarun")["conversations"]})


if __name__ == "__main__":
    unittest.main()
