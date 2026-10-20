"""The shared browser files in common/static: theme-boot.js (the page theme every app shares, old theme
names mapped to the new ones) and ui.js (the escaper and the fetch wrapper). Run in Node with a tiny
stand-in for the browser; skipped when Node isn't installed. Standard library only."""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "common", "static")
NODE = shutil.which("node")

STUB = r"""
globalThis.window = globalThis;
const attrs = {};
const store = Object.assign({}, %(store)s);
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };
globalThis.matchMedia = () => ({ matches: %(light)s, addEventListener() {} });
globalThis.document = {
  documentElement: { setAttribute: (k, v) => { attrs[k] = v; }, removeAttribute: (k) => { delete attrs[k]; },
                     getAttribute: (k) => (k in attrs ? attrs[k] : null) },
  addEventListener() {},
};
"""


def run_node(script):
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(NODE, "node not installed")
class ThemeBoot(unittest.TestCase):
    def boot(self, saved=None, light=False, extra=""):
        store = {} if saved is None else {"theme": saved}
        stub = STUB % {"store": json.dumps(store), "light": "true" if light else "false"}
        return run_node(stub + read("theme-boot.js") + extra +
                        "\nprocess.stdout.write(JSON.stringify({ attrs, store, choice: HouseholdTheme.choice() }));")

    def test_saved_choices_and_old_names(self):
        cases = {None: "midnight", "midnight": "midnight", "slate": "slate", "daylight": "daylight",
                 "heritage": "midnight", "ink": "midnight", "vault": "midnight", "paper": "midnight",
                 "parchment": "daylight", "sandstone": "daylight", "no-such-theme": "midnight"}
        for saved, want in cases.items():
            with self.subTest(saved):
                r = self.boot(saved)
                self.assertEqual(r["attrs"]["data-theme"], want)
                self.assertEqual(r["choice"], want)

    def test_auto_follows_the_device(self):
        self.assertEqual(self.boot("auto", light=True)["attrs"]["data-theme"], "daylight")
        r = self.boot("auto", light=False)
        self.assertEqual(r["attrs"]["data-theme"], "midnight")
        self.assertEqual(r["choice"], "auto")

    def test_set_saves_and_applies(self):
        r = self.boot("ink", extra="\nHouseholdTheme.set('slate');")
        self.assertEqual(r["attrs"]["data-theme"], "slate")
        self.assertEqual(r["store"]["theme"], "slate")

    def test_sidebar_collapsed(self):
        stub = STUB % {"store": json.dumps({"sidebarCollapsed": "1"}), "light": "false"}
        r = run_node(stub + read("theme-boot.js") +
                     "\nconst before = attrs['data-sidebar']; HouseholdTheme.setSidebarCollapsed(false);"
                     "\nprocess.stdout.write(JSON.stringify({ before, after: attrs['data-sidebar'] || null, store }));")
        self.assertEqual(r["before"], "collapsed")
        self.assertIsNone(r["after"])
        self.assertEqual(r["store"]["sidebarCollapsed"], "0")


@unittest.skipUnless(NODE, "node not installed")
class UiHelpers(unittest.TestCase):
    def ui(self, body):
        stub = STUB % {"store": "{}", "light": "false"}
        return run_node(stub + read("ui.js") + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e); process.exit(1); });")

    def test_escape_html(self):
        r = self.ui("process.stdout.write(JSON.stringify([UI.escapeHtml(`\" onmouseover='x' <b>&`), UI.escapeHtml(null)]));")
        self.assertEqual(r, ["&quot; onmouseover=&#39;x&#39; &lt;b&gt;&amp;", ""])

    def test_h_never_writes_inline_handlers(self):
        # A string under an on… key would become an inline handler (onclick="…"); only functions are listeners.
        r = self.ui(r"""
          const mk = (tag) => ({ tag, attrs: {}, listeners: [], kids: [], dataset: {}, style: {},
            setAttribute(k, v) { this.attrs[k] = String(v); }, addEventListener(t) { this.listeners.push(t); },
            appendChild(c) { this.kids.push(c); } });
          globalThis.Node = function () {};
          document.createElement = mk;
          document.createTextNode = (t) => ({ text: t });
          const el = UI.h("a", { onclick: "alert(1)", ONMOUSEOVER: "alert(2)", onFocus: () => 1, title: "ok" });
          process.stdout.write(JSON.stringify({ attrs: el.attrs, listeners: el.listeners }));
        """)
        self.assertEqual(r, {"attrs": {"title": "ok"}, "listeners": ["focus"]})

    def test_money(self):
        r = self.ui("process.stdout.write(JSON.stringify([UI.money(1234.5), UI.money(-3), UI.money(1234567.891, { symbol: '' }), "
                    "UI.money(-0.001), UI.money(5, { plus: true, symbol: '€' }), UI.money(null), UI.money('x', { blank: '—' }), "
                    "UI.money(999.999), UI.money(12, { decimals: 0 })]));")
        self.assertEqual(r, ["$1,234.50", "-$3.00", "1,234,567.89", "$0.00", "+€5.00", "", "—", "$1,000.00", "$12"])

    def test_states(self):
        r = self.ui(r"""
          const mk = (tag) => ({ tag, attrs: {}, listeners: {}, kids: [], dataset: {}, style: {}, className: "", firstChild: null,
            setAttribute(k, v) { this.attrs[k] = String(v); }, addEventListener(t, fn) { this.listeners[t] = fn; },
            appendChild(c) { this.kids.push(c); this.firstChild = this.kids[0]; return c; },
            removeChild(c) { this.kids.splice(this.kids.indexOf(c), 1); this.firstChild = this.kids[0] || null; } });
          globalThis.Node = function () {};
          document.createElement = (t) => Object.setPrototypeOf(mk(t), Node.prototype);
          document.createTextNode = (t) => Object.setPrototypeOf({ text: t }, Node.prototype);
          const text = (el) => el.text !== undefined ? el.text : el.kids.map(text).join("|");
          const box = mk("div"); box.appendChild(document.createTextNode("old"));
          const l = UI.loading(box);
          let tries = 0;
          const e = UI.errorBox(new Error("Nope"), () => tries++);
          e.kids[1].listeners.click();
          const em = UI.empty("Nothing yet", "Add one", { label: "Add", onclick: () => tries++ });
          process.stdout.write(JSON.stringify({ l: [l.className, l.attrs.role, text(l), box.kids.length], e: [e.className, e.attrs.role, text(e)],
            tries, em: [em.className, text(em), UI.empty("T").kids.length, UI.errorBox("plain").kids.length] }));
        """)
        self.assertEqual(r, {"l": ["ui-loading", "status", "|Loading…", 1], "e": ["ui-error", "alert", "Nope|Retry"],
                             "tries": 1, "em": ["ui-empty", "Nothing yet|Add one|Add", 1, 1]})

    def test_error_message(self):
        r = self.ui("process.stdout.write(JSON.stringify([UI.errorMessage('Nope', 400), "
                    "UI.errorMessage([{ msg: 'a' }, { msg: 'b' }], 422), UI.errorMessage(null, 500)]));")
        self.assertEqual(r, ["Nope", "a; b", "Something went wrong (HTTP 500)."])

    def test_api(self):
        r = self.ui(r"""
          const calls = [];
          const replies = [
            { ok: true, status: 200, json: async () => ({ hello: 1 }) },
            { ok: true, status: 204, json: async () => { throw new Error("no body"); } },
            { ok: false, status: 403, json: async () => ({ detail: "Admins only" }) },
          ];
          globalThis.fetch = async (url, init) => { calls.push([url, init]); if (!replies.length) throw new TypeError("offline"); return replies.shift(); };
          const api = UI.makeApi({ headers: () => ({ "X-Test": "1" }) });
          const out = {};
          out.json = await api("/api/x", { method: "POST", body: { a: 1 } });
          out.empty = await api("api/y");
          try { await api("api/z"); } catch (e) { out.err = [e.message, e.status]; }
          try { await api("api/w"); } catch (e) { out.net = [e.message, e.status === undefined]; }
          out.calls = calls.map(([u, i]) => [u, i.method, i.headers, i.body || null]);
          process.stdout.write(JSON.stringify(out));
        """)
        self.assertEqual(r["json"], {"hello": 1})
        self.assertIsNone(r["empty"])
        self.assertEqual(r["err"], ["Admins only", 403])
        self.assertEqual(r["net"], ["Can't reach the app. Check your connection and try again.", True])
        self.assertEqual(r["calls"][0], ["api/x", "POST", {"X-Test": "1", "Content-Type": "application/json"}, '{"a":1}'])
        self.assertEqual(r["calls"][1], ["api/y", "GET", {"X-Test": "1"}, None])



@unittest.skipUnless(NODE, "node not installed")
class ConnectedAppsJs(unittest.TestCase):
    """common/static/connected-apps.js: the Connected apps card and links to another app's page (§5, §6.5)."""
    STUB = r"""
globalThis.window = globalThis;
const H = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false) });
"""

    def run_js(self, body):
        return run_node(self.STUB + read("connected-apps.js") + "\n(async () => {\n" + body +
                        "\n})().catch((e) => { console.error(e); process.exit(1); });")

    def test_labels_rows_and_status(self):
        r = self.run_js(r"""
          const data = { on: true, connected: true, apps: [
            { slug: "household_docs", name: "Household Docs", version: "1.0.0", can: ["chat.card", "x.y"], last_seen: "2026-10-04T10:00:00Z", active: false },
            { slug: "household_arcade", name: "Household Arcade", version: "1.5.0", can: [], last_seen: "2026-10-04T09:00:00Z", active: true } ] };
          const rows = ConnectedApps.rows(data);
          const text = (n) => typeof n === "string" ? n : (n.kids || []).map(text).join("");
          process.stdout.write(JSON.stringify({
            slugs: rows.map((r) => r.slug), does: rows[1].does,
            status: [ConnectedApps.status(null), ConnectedApps.status({ on: true, connected: false, apps: [] }),
                     ConnectedApps.status({ on: true, connected: true, apps: [] }), ConnectedApps.status(data)],
            card: text(ConnectedApps.card(data, { h: H, when: (d) => d.toISOString() })),
          }));
        """)
        self.assertEqual(r["slugs"], ["household_arcade", "household_docs"])        # active first
        self.assertEqual(r["does"], ["Post shared documents in chats", "x.y"])
        self.assertEqual(r["status"][3], None)
        self.assertIn("only when the app runs inside Home Assistant", r["status"][0])
        self.assertIn("Not connected", r["status"][1])
        self.assertIn("No other household app", r["status"][2])
        self.assertIn("Household Docs 1.0.0", r["card"])
        self.assertIn("not seen lately", r["card"])
        self.assertIn("Can: Post shared documents in chats", r["card"])

    def test_safe_path(self):
        r = self.run_js(r"""
          const p = ConnectedApps.safePath;
          process.stdout.write(JSON.stringify([p("/a1b2c3d4_household_docs", "/doc/n1"), p("/local_household_docs", null),
            p("/x", "/"), p("household_docs", "/doc"), p("/Bad", "/doc"), p("/x", "/doc/../config"), p("/x", "doc"),
            p("/x", "/a b"), p("//evil", "/x"), p("/x", "/" + "a".repeat(200))]));
        """)
        self.assertEqual(r, ["/a1b2c3d4_household_docs/doc/n1", "/local_household_docs", "/x", None, None, None, None,
                             None, None, None])

    def test_open_app_page(self):
        r = self.run_js(r"""
          function frame(opts) {
            const log = [];
            const top = { location: { pathname: "/a1_household_chat", search: "" }, history: { pushState: (s, t, p) => { log.push(["push", p]); top.location.pathname = p; } },
              dispatchEvent: (e) => log.push(["event", e.type, e.detail]), CustomEvent: function (type, init) { this.type = type; this.detail = init.detail; } };
            const win = { top, location: { origin: "http://ha.local" }, open: (p, t) => log.push(["open", p, t]),
              parent: { postMessage: (m, o) => { log.push(["post", m.type, m.path, o]); if (opts.haNavigates) top.location.pathname = m.path; } } };
            if (opts.crossOrigin) Object.defineProperty(top, "location", { get() { throw new Error("blocked"); } });
            return { win, log };
          }
          const out = {};
          let f = frame({ haNavigates: true });
          out.ha = [await ConnectedApps.openAppPage("/a1_household_docs/doc/n1", { win: f.win, wait: 5 }), f.log];
          f = frame({});
          out.old = [await ConnectedApps.openAppPage("/a1_household_docs", { win: f.win, wait: 5 }), f.log];
          f = frame({ crossOrigin: true });
          out.load = [await ConnectedApps.openAppPage("/a1_household_docs", { win: f.win, wait: 5 }), f.log];
          const alone = { log: [] }; alone.win = { open: (p, t) => alone.log.push(["open", p, t]) }; alone.win.top = alone.win;
          out.alone = [await ConnectedApps.openAppPage("/x", { win: alone.win, wait: 5 }), alone.log];
          process.stdout.write(JSON.stringify(out));
        """)
        self.assertEqual(r["ha"], ["ha", [["post", "home-assistant/navigate", "/a1_household_docs/doc/n1", "http://ha.local"]]])
        self.assertEqual(r["old"][0], "history")
        self.assertEqual(r["old"][1][1:], [["push", "/a1_household_docs"], ["event", "location-changed", {"replace": False}]])
        self.assertEqual(r["load"], ["load", [["post", "home-assistant/navigate", "/a1_household_docs", "http://ha.local"],
                                              ["open", "/a1_household_docs", "_top"]]])
        self.assertEqual(r["alone"], ["load", [["open", "/x", "_top"]]])


@unittest.skipUnless(NODE, "node not installed")
class HttpWarning(unittest.TestCase):
    """common/static/http-warning.js: the plain-http:// banner."""
    STUB = r"""
globalThis.window = globalThis;
globalThis.location = %(loc)s;
globalThis.document = { createElement: (tag) => ({ tag, attrs: {}, kids: [], className: "",
  setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.kids.push(c); } }),
  createTextNode: (t) => ({ text: t }) };
"""

    def run_case(self, loc, call):
        return run_node(self.STUB % {"loc": json.dumps(loc)} + read("http-warning.js") +
                        "\nprocess.stdout.write(JSON.stringify(" + call + "));")

    def test_only_plain_http_off_this_computer(self):
        cases = [({"protocol": "http:", "hostname": "192.168.1.5"}, True),
                 ({"protocol": "http:", "hostname": "homeassistant.local"}, True),
                 ({"protocol": "https:", "hostname": "ha.example.org"}, False),
                 ({"protocol": "http:", "hostname": "localhost"}, False),
                 ({"protocol": "http:", "hostname": "127.0.0.1"}, False)]
        for loc, want in cases:
            with self.subTest(loc):
                self.assertEqual(self.run_case(loc, "HttpWarning.isPlainHttp()"), want)
                banner = self.run_case(loc, 'HttpWarning.banner("Careful")')
                if want:
                    self.assertEqual(banner, {"tag": "div", "attrs": {}, "kids": [{"text": "Careful"}], "className": "banner"})
                else:
                    self.assertIsNone(banner)

    def test_options(self):
        loc = {"protocol": "https:", "hostname": "x"}
        b = self.run_case(loc, 'HttpWarning.banner("T", { className: "warn", role: "alert", '
                               'loc: { protocol: "http:", hostname: "10.0.0.2" } })')
        self.assertEqual((b["className"], b["attrs"]), ("warn", {"role": "alert"}))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NODE, "node not installed")
class BackNavJs(unittest.TestCase):
    """common/static/backnav.js in a small fake browser history: a link or a notification that changes the address
    (setting location.hash adds an entry and fires popstate, then hashchange, as Chrome does) is a navigation the app
    must see, not the back gesture; the real Back still pops the guard (home first, an open dialog closed first)."""
    HARNESS = r"""
const vm = require("node:vm");
const SRC = %s;
function browser(startHash, store, page) {
  const base = "http://h/app/";
  const listeners = {};
  const entries = [{ href: base + (page || "") + startHash, state: null }];
  const replaced = [];
  store = store || {};
  let idx = 0;
  const timers = [];
  const location = {
    get href() { return entries[idx].href; },
    get hash() { const h = entries[idx].href; const i = h.indexOf("#"); return i < 0 ? "" : h.slice(i); },
    get pathname() { return entries[idx].href.replace(/^http:\/\/h/, "").split(/[?#]/)[0]; },
    get search() { const m = /\?[^#]*/.exec(entries[idx].href); return m ? m[0] : ""; },
    replace(url) { replaced.push(url); entries[idx] = { href: abs(url), state: null }; },
  };
  const fire = (type, ev) => { for (const fn of (listeners[type] || []).slice()) fn(Object.assign({ type }, ev || {})); };
  const history = {
    get length() { return entries.length; },
    get state() { return entries[idx].state; },
    pushState(state, _t, url) { entries.splice(idx + 1); entries.push({ href: abs(url), state }); idx++; },
    replaceState(state, _t, url) { entries[idx] = { href: abs(url), state }; },
    back() { if (idx > 0) { const old = location.href; idx--; timers.push(() => { fire("popstate"); if (old.split("#")[1] !== location.href.split("#")[1]) fire("hashchange", { newURL: location.href, oldURL: old }); }); } },
  };
  function abs(url) { const cur = entries[idx].href.split("#")[0]; return url.startsWith("#") ? cur + url : url.startsWith("/") ? "http://h" + url : url; }
  const win = {
    location, history,
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    dispatchEvent(ev) { fire(ev.type, ev); },
    requestAnimationFrame(fn) { timers.push(fn); },
    setTimeout(fn) { timers.push(fn); },
    clearTimeout() {},
    MutationObserver: class { observe() {} },
    HashChangeEvent: class { constructor(type, o) { this.type = type; Object.assign(this, o || {}); } },
    document: { body: {}, addEventListener() {} },
    sessionStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(SRC, win);
  return {
    win, location, history, entries, replaced, store,
    /** Let the queued timers and animation frames run. */
    run() { for (let k = 0; k < 20 && timers.length; k++) timers.splice(0).forEach((fn) => fn()); },
    /** What setting location.hash does: a new entry, then popstate and hashchange. */
    setHash(h) { const old = location.href; entries.splice(idx + 1); entries.push({ href: base + h, state: null }); idx++; fire("popstate"); fire("hashchange", { newURL: location.href, oldURL: old }); },
    on(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  };
}

function app(b, startTab, extra) {
  const A = { tab: startTab, home: 0, closed: 0, layers: [], seen: [] };
  b.win.BackNav.init({ ...(extra || {}),
    atHome: () => A.tab === "home",
    goHome: () => { A.home++; A.tab = "home"; b.history.replaceState(b.history.state, "", "#/home"); },
    openLayers: () => A.layers.slice(),
    closeLayer: (l) => { A.closed++; A.layers.splice(A.layers.indexOf(l), 1); },
  });
  // the app's own hashchange listener (as app.js): it shows whatever the address now says
  b.on("hashchange", () => { A.seen.push(b.location.hash); A.tab = b.location.hash.split("/")[1] || "home"; });
  b.run();
  return A;
}
"""

    def run_js(self, body):
        return run_node(self.HARNESS % json.dumps(read("backnav.js")) + "\n" + body)

    def test_link_hash_change_at_home_is_not_back(self):
        r = self.run_js(r"""
          const b = browser("#/home"); const A = app(b, "home");
          const guard = b.entries.length;
          b.setHash("#/home/turn/abc"); b.run();
          process.stdout.write(JSON.stringify({ guard, hash: b.location.hash, seen: A.seen, home: A.home }));""")
        self.assertEqual(r, {"guard": 1, "hash": "#/home/turn/abc", "seen": ["#/home/turn/abc"], "home": 0})

    def test_link_hash_change_with_guard_is_not_back(self):
        r = self.run_js(r"""
          const b = browser("#/play/ludo"); const A = app(b, "play");
          const guard = b.entries.length;
          b.setHash("#/home/turn/xyz"); b.run();
          process.stdout.write(JSON.stringify({ guard, hash: b.location.hash, home: A.home, closed: A.closed }));""")
        self.assertEqual(r, {"guard": 2, "hash": "#/home/turn/xyz", "home": 0, "closed": 0})

    def test_real_back_still_goes_home_or_closes_a_dialog(self):
        r = self.run_js(r"""
          const b = browser("#/play/ludo"); const A = app(b, "play");
          b.history.replaceState(b.history.state, "", "#/scores"); A.tab = "scores";
          b.win.BackNav.sync(); b.run();
          b.history.back(); b.run();
          const c = browser("#/home"); const B = app(c, "home");
          B.layers.push("dialog"); c.win.BackNav.sync(); c.run();
          const guard = c.entries.length;
          c.history.back(); c.run();
          process.stdout.write(JSON.stringify({ home: A.home, hash: b.location.hash, guard, closed: B.closed, home2: B.home }));""")
        self.assertEqual(r, {"home": 1, "hash": "#/home", "guard": 2, "closed": 1, "home2": 0})

    # ---- the back trail ----
    def test_back_retraces_the_trail_then_leaves(self):
        r = self.run_js(r"""
          const b = browser("#/home"); const A = app(b, "home");
          const N = b.win.BackNav;
          N.go("#/people"); b.run(); N.go("#/people/p1"); b.run(); N.go("#/people/p1/edit"); b.run();
          const trail = N._trail.get(); const hashes = [];
          for (let k = 0; k < 3; k++) { b.history.back(); b.run(); hashes.push(b.location.hash); }
          process.stdout.write(JSON.stringify({ trail, hashes, left: N._trail.get(), home: A.home, saved: JSON.parse(b.store[N._trail.key]) }));""")
        self.assertEqual(r["trail"], ["#/home", "#/people", "#/people/p1"])
        self.assertEqual(r["hashes"], ["#/people/p1", "#/people", "#/home"])
        self.assertEqual(r["left"], [])
        self.assertEqual(r["saved"], [])
        self.assertEqual(r["home"], 0)

    def test_replace_is_not_remembered_and_duplicates_collapse(self):
        r = self.run_js(r"""
          const b = browser("#/x"); const A = app(b, "x");
          const N = b.win.BackNav;
          N.go("#/y"); N.replace("#/x"); N.go("#/z"); b.run();
          const one = N._trail.get();
          N.go("#/list"); N.go("#/list?sort=date", { replace: true }); b.run();
          const two = N._trail.get();
          b.history.back(); b.run(); const after = b.location.hash;
          for (let k = 0; k < 30; k++) N.go("#/p/" + k);
          process.stdout.write(JSON.stringify({ one, two, after, capped: N._trail.get().length }));""")
        self.assertEqual(r, {"one": ["#/x"], "two": ["#/x", "#/z"], "after": "#/z", "capped": 20})

    def test_tabs_start_a_new_trail_then_home(self):
        r = self.run_js(r"""
          const b = browser("#/home"); const A = app(b, "home", { isTab: (h) => /^#\/[a-z]+$/.test(h) });
          const N = b.win.BackNav;
          N.go("#/people"); N.go("#/people/p1"); N.go("#/money"); N.go("#/money/tx/5"); b.run();
          const trail = N._trail.get();
          b.history.back(); b.run(); const first = b.location.hash;
          b.history.back(); b.run();
          process.stdout.write(JSON.stringify({ trail, first, home: A.home, hash: b.location.hash }));""")
        self.assertEqual(r, {"trail": ["#/money"], "first": "#/money", "home": 1, "hash": "#/home"})

    def test_parent_of_a_deep_link(self):
        r = self.run_js(r"""
          const b = browser("#/people/p9"); const A = app(b, "people",
            { parentOf: (h) => (h.startsWith("#/people/") ? "#/people" : null) });
          b.history.back(); b.run(); const first = b.location.hash;
          b.history.back(); b.run();
          process.stdout.write(JSON.stringify({ first, home: A.home }));""")
        self.assertEqual(r, {"first": "#/people", "home": 1})

    def test_back_button(self):
        r = self.run_js(r"""
          const b = browser("#/home"); const A = app(b, "home");
          const N = b.win.BackNav;
          N.go("#/a"); N.go("#/b"); b.run();
          A.layers.push("dialog");
          const w1 = N.back(); const h1 = b.location.hash;
          const w2 = N.back(); const h2 = b.location.hash;
          const w3 = N.back();
          process.stdout.write(JSON.stringify({ w1, h1, w2, h2, w3, closed: A.closed }));""")
        self.assertEqual(r, {"w1": True, "h1": "#/a", "w2": True, "h2": "#/home", "w3": False, "closed": 0})

    def test_pages_of_a_multi_page_app(self):
        r = self.run_js(r"""
          const store = {};
          const b = browser("#/home", store); const A = app(b, "home");
          b.win.BackNav.goPage("/app/deals.html?w=1");
          // the new page loads with the same sessionStorage: its trail arms the guard even on its start page
          const c = browser("", store, "deals.html?w=1"); const B = app(c, "home");
          const guard = c.entries.length;
          c.history.back(); c.run();
          process.stdout.write(JSON.stringify({ first: b.replaced, trail: JSON.parse(store["backnav.trail:/app/"]), guard, back: c.replaced }));""")
        self.assertEqual(r, {"first": ["/app/deals.html?w=1"], "trail": [], "guard": 2, "back": ["/app/#/home"]})
