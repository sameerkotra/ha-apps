"""The shared admin pages in common/static: settings.js (Admin → App settings drawn from the server's meta) and
people.js (Admin → People). Run in headless Chromium through Playwright against a stand-in API; skipped when
Playwright (or a browser for it) isn't installed."""
import glob
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "common", "static")

try:
    from playwright.sync_api import sync_playwright
except ImportError:          # pragma: no cover
    sync_playwright = None


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


PAYLOAD = {
    "values": {"days": 30, "on": True, "mode": "x", "url": "", "key": "", "hidden": [], "extra": 2, "dep": 1},
    "defaults": {"days": 30, "on": True, "mode": "x", "url": "", "key": "", "hidden": [], "extra": 2, "dep": 1},
    "meta": {
        "days": {"label": "Days", "help": "How long.", "group": "a", "kind": "int", "restartRequired": False, "min": 1, "max": 90, "unit": "days"},
        "on": {"label": "Switched on", "help": "", "group": "a", "kind": "bool", "restartRequired": False},
        "mode": {"label": "Mode", "help": "", "group": "b", "kind": "choice", "restartRequired": True,
                 "choices": [{"value": "x", "label": "Ex"}, {"value": "y", "label": "Why"}]},
        "url": {"label": "Address", "help": "", "group": "b", "kind": "url", "restartRequired": False, "showIf": "on"},
        "key": {"label": "Key", "help": "", "group": "b", "kind": "secret", "restartRequired": False},
        "hidden": {"label": "Hidden", "help": "", "group": "b", "kind": "list", "restartRequired": False, "hidden": True},
        "extra": {"label": "Extra", "help": "", "group": "b", "kind": "int", "restartRequired": False, "min": 0, "max": 5},
        "dep": {"label": "Dependent", "help": "", "group": "b", "kind": "int", "restartRequired": False, "enabledIf": "on"},
    },
    "groups": [{"id": "a", "label": "First", "help": "The first group."}, {"id": "b", "label": "Second", "help": ""}],
    "secretsSet": {"key": True},
}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>%(css)s</style></head>
<body><div id="box"></div><div id="people"></div>
<script>%(ui)s</script><script>%(settings)s</script><script>%(people)s</script></body></html>"""


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
class SharedPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            cls.browser = chromium(cls.pw)
        except unittest.SkipTest:
            cls.pw.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.set_content(PAGE % {"css": read("settings.css"), "ui": read("ui.js"), "settings": read("settings.js"),
                                      "people": read("people.js")})
        self.page.evaluate("""(payload) => {
          window.sent = [];
          window.state = JSON.parse(JSON.stringify(payload));
          window.load = async () => JSON.parse(JSON.stringify(window.state));
          window.save = async (body) => {
            window.sent.push(body);
            if (body.days === 77) { const e = new Error("Days: the server says no"); throw e; }
            Object.assign(window.state.values, body);
            return JSON.parse(JSON.stringify(window.state));
          };
        }""", PAYLOAD)

    def render(self, extra=""):
        self.page.evaluate("async () => { window.pg = await SettingsPage.render(document.getElementById('box'), "
                           "{ load, save %s }); }" % extra)

    def test_draws_groups_fields_and_help_from_meta(self):
        self.render()
        text = self.page.inner_text("#box")
        for s in ("First", "The first group.", "Second", "Days", "How long. 1–90 days. Default: 30 days.",
                  "Switched on", "Default: on.", "Mode", "restart needed", "Default: Ex.", "Address", "Key"):
            self.assertIn(s, text)
        self.assertNotIn("Hidden", text)
        self.assertEqual(self.page.eval_on_selector("#set-days", "e => [e.type, e.min, e.max, e.value]"), ["number", "1", "90", "30"])
        self.assertEqual(self.page.eval_on_selector("#set-key", "e => [e.type, e.value, e.placeholder]"),
                         ["password", "", "Saved — type a new one to replace it"])
        self.assertTrue(self.page.is_disabled("#settingsSave"))
        self.assertEqual(self.errors, [])

    def test_save_sends_only_what_changed_and_redraws(self):
        self.render()
        self.page.fill("#set-days", "45")
        self.page.select_option("#set-mode", label="Why")
        self.assertIn("2 unsaved changes", self.page.inner_text(".sp-bar"))
        self.page.click("#settingsSave")
        self.page.wait_for_function("window.sent.length === 1")
        self.assertEqual(self.page.evaluate("window.sent[0]"), {"days": 45, "mode": "y"})
        self.page.wait_for_selector(".sp-status.ok")
        self.assertIn("Restart the app for Mode to take effect.", self.page.inner_text(".sp-status"))
        self.assertEqual(self.page.input_value("#set-days"), "45")
        self.assertTrue(self.page.is_disabled("#settingsSave"))

    def test_bad_number_is_caught_on_the_page(self):
        self.render()
        for bad in ("0", "91", "4.5", ""):
            self.page.fill("#set-days", bad)
            self.assertEqual(self.page.inner_text("[data-key=days] .sp-error"), "Days must be a whole number from 1 to 90.")
        self.page.click("#settingsSave")
        self.assertEqual(self.page.evaluate("window.sent.length"), 0)
        self.assertIn("must be a whole number", self.page.inner_text(".sp-status"))
        self.page.fill("#set-days", "12")
        self.assertEqual(self.page.inner_text("[data-key=days] .sp-error"), "")

    def test_server_error_is_shown_at_the_field(self):
        self.render()
        self.page.fill("#set-days", "77")
        self.page.click("#settingsSave")
        self.page.wait_for_selector(".sp-status.err")
        self.assertEqual(self.page.inner_text("[data-key=days] .sp-error"), "Days: the server says no")
        self.assertEqual(self.page.input_value("#set-days"), "77")           # kept, not saved

    def test_show_if_enabled_if_discard_and_secret(self):
        self.render()
        self.assertTrue(self.page.is_visible("[data-key=url]"))
        self.page.click("[data-key=on] .sp-switch")
        self.assertFalse(self.page.is_visible("[data-key=url]"))
        self.assertTrue(self.page.is_disabled("#set-dep"))
        self.assertIn("Only used while “Switched on” is on.", self.page.inner_text("[data-key=dep]"))
        self.page.click("#settingsDiscard")
        self.assertTrue(self.page.is_visible("[data-key=url]"))
        self.page.click("[data-key=key] button")                              # Remove the saved key
        self.page.click("#settingsSave")
        self.page.wait_for_function("window.sent.length === 1")
        self.assertEqual(self.page.evaluate("window.sent[0]"), {"key": ""})

    def test_hooks(self):
        self.render(""", fields: { extra: { control: (page) => { const b = document.createElement('button'); b.id = 'bump';
                         b.onclick = () => page.set('extra', page.value('extra') + 1); return b; } } },
                     groups: { a: { top: () => UI.h('p', { id: 'top' }, 'On top') } },
                     beforeSave: async (body, page) => (body.extra === 4 ? null : Object.assign(body, { confirm: true })),
                     afterSave: async () => 'Done!'""")
        self.assertEqual(self.page.inner_text("#top"), "On top")
        self.page.click("#bump")
        self.page.click("#settingsSave")
        self.page.wait_for_function("window.sent.length === 1")
        self.assertEqual(self.page.evaluate("window.sent[0]"), {"extra": 3, "confirm": True})
        self.page.wait_for_selector(".sp-status.ok")
        self.assertEqual(self.page.inner_text(".sp-status"), "Done!")
        self.page.click("#bump")                                              # 4: beforeSave cancels
        self.page.click("#settingsSave")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("window.sent.length"), 1)

    def test_people_page(self):
        r = self.page.evaluate("""async () => {
          const calls = [];
          const api = async (path, opts) => { calls.push([path, opts && opts.method, opts && opts.body]);
            if (path.endsWith('/test')) return { results: { 'notify.mobile_app_p': true, 'notify.x': false } };
            if (opts && opts.method === 'POST') return { notify: ['notify.x'] };
            return { notify: [] }; };
          const toasts = [];
          const people = [
            { id: 'u1', name: 'Asha', notify: [], ha: { known: true, person: 'person.asha', personName: 'Asha',
              phones: [{ label: 'Pixel', service: 'notify.mobile_app_p', tracker: 'device_tracker.p' }] } },
            { id: 'u2', name: 'Ravi', notify: [], ha: { known: false, phones: [] } },
          ];
          PeoplePage.render(document.getElementById('people'), {
            people, intro: 'Everyone.', checkAgain: async () => calls.push(['again']),
            person: (p) => ({ badges: [p.id === 'u1' ? ['you'] : null], sub: 'login ' + p.id,
              controls: PeoplePage.accessSwitch(true, (on) => calls.push(['access', p.id, on]), { label: 'Access' }) }),
            notify: { api, services: { available: true, services: ['notify.a', 'notify.persistent_notification'], entities: [] },
              toast: (m, err) => toasts.push([m, !!err]), path: (p) => `api/u/${p.id}/notify`, testPath: (p) => `api/u/${p.id}/notify/test` },
          });
          const box = document.getElementById('people');
          const first = box.querySelector('#person-u1');
          const out = { text: box.innerText, options: [...first.querySelectorAll('select option')].map((o) => o.value) };
          const sel = first.querySelector('select'); sel.value = '__manual'; sel.dispatchEvent(new Event('change'));
          const input = first.querySelector('.pp-manual'); input.value = 'mobile_app_x';
          [...first.querySelectorAll('button')].find((b) => b.textContent === 'Add').click();
          await new Promise((r) => setTimeout(r, 50));
          out.afterAdd = box.querySelector('#person-u1').innerText;
          [...box.querySelector('#person-u1').querySelectorAll('button')].find((b) => b.textContent === 'Send a test').click();
          await new Promise((r) => setTimeout(r, 50));
          out.result = box.querySelector('#person-u1 .pp-result').textContent;
          box.querySelector('#person-u2 input[role=switch]').click();
          out.secondTestDisabled = [...box.querySelector('#person-u2').querySelectorAll('button')].find((b) => b.textContent === 'Send a test').disabled;
          out.calls = calls; out.toasts = toasts;
          return out;
        }""")
        self.assertIn("Everyone.", r["text"])
        self.assertIn("Check Home Assistant again", r["text"])
        self.assertIn("📱 Pixel", r["text"])
        self.assertIn("Home Assistant's people couldn't be read yet.", r["text"])
        self.assertEqual(r["options"], ["", "notify.a", "__manual"])                  # persistent_notification left out
        self.assertIn(["api/u/u1/notify", "POST", {"service": "notify.mobile_app_x"}], r["calls"])
        self.assertIn("notify.x", r["afterAdd"])
        self.assertEqual(r["result"], "notify.mobile_app_p: sent ✓ · notify.x: failed")
        self.assertIn(["Sent, but Home Assistant refused notify.x", True], r["toasts"])
        self.assertIn(["access", "u2", False], r["calls"])
        self.assertTrue(r["secondTestDisabled"])                                        # nothing reaches Ravi
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
