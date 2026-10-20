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

    def test_bound_selects_are_released(self):
        r = self.boot("slate", extra=r"""
          const sel = () => ({ firstChild: null, kids: [], value: "", isConnected: false, listeners: 0,
            removeChild(c) { this.kids.splice(this.kids.indexOf(c), 1); this.firstChild = this.kids[0] || null; },
            appendChild(c) { this.kids.push(c); this.firstChild = this.kids[0]; }, addEventListener() { this.listeners++; } });
          document.createElement = () => ({});
          // one menu per dialog opening: shown, then the dialog closes
          for (let k = 0; k < 50; k++) { const s = HouseholdTheme.bindSelect(sel()); s.isConnected = true; HouseholdTheme.set("slate"); s.isConnected = false; }
          const afterDialogs = HouseholdTheme._boundCount();
          const page = sel(); HouseholdTheme.bindSelect(page);           // bound before it is on the page: kept
          HouseholdTheme.bindSelect(page);                                  // again: no second listener
          const pending = HouseholdTheme._boundCount();
          page.isConnected = true; HouseholdTheme.set("daylight");
          const shown = page.value, options = page.kids.length;
          HouseholdTheme.unbindSelect(page); HouseholdTheme.set("midnight");
          globalThis.result = { afterDialogs, pending, shown, options, listeners: page.listeners, unbound: page.value, left: HouseholdTheme._boundCount() };
        """ + "\nprocess.stdout.write(JSON.stringify(result)); process.exit(0);")
        self.assertEqual(r, {"afterDialogs": 0, "pending": 1, "shown": "daylight", "options": 4, "listeners": 1,
                             "unbound": "daylight", "left": 0})

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

    def test_button_classes_and_toast_action(self):
        r = self.ui(r"""
          const mk = (tag) => ({ tag, attrs: {}, listeners: {}, kids: [], dataset: {}, style: {}, className: "", firstChild: null, removed: false,
            classList: { add() {} }, setAttribute(k, v) { this.attrs[k] = String(v); }, addEventListener(t, fn) { this.listeners[t] = fn; },
            appendChild(c) { this.kids.push(c); this.firstChild = this.kids[0]; return c; }, remove() { this.removed = true; } });
          globalThis.Node = function () {};
          document.createElement = (t) => Object.setPrototypeOf(mk(t), Node.prototype);
          document.createTextNode = (t) => Object.setPrototypeOf({ text: t }, Node.prototype);
          const root = mk("div");
          globalThis.setTimeout = () => 0;
          const before = [UI.buttonClass("primary"), UI.buttonClass("ghost"), UI.buttonClass("danger"),
            UI.errorBox("x", () => 1).kids[1].className, UI.empty("t", null, { label: "Go" }).kids[1].kids[0].className];
          UI.buttonClasses = { primary: "btn primary", ghost: "btn ghost" };
          const after = [UI.buttonClass("primary"), UI.buttonClass("danger"), UI.errorBox("x", () => 1).kids[1].className,
            UI.empty("t", null, { label: "Go" }).kids[1].kids[0].className,
            UI.empty("t", null, { label: "Go", class: "mine" }).kids[1].kids[0].className,
            UI.errorBox("x", () => 1, { retryClass: "own", retryLabel: "Again" }).kids[1].className,
            UI.errorBox("x", () => 1, { retryLabel: "Again" }).kids[1].kids[0].text];
          let undone = 0;
          const t = UI.toast("Deleted", { root, action: { label: "Undo", class: "link-btn", id: "u1", onclick: () => undone++ } });
          const btn = t.kids[1];
          btn.listeners.click({ stopPropagation() {} });
          process.stdout.write(JSON.stringify({ before, after, action: [btn.tag, btn.className, btn.attrs.id, btn.kids[0].text], undone, closed: t.removed }));
        """)
        self.assertEqual(r["before"], ["btn-primary", "btn-ghost", "btn-danger", "btn-ghost", "btn-primary"])
        self.assertEqual(r["after"], ["btn primary", "btn-danger", "btn ghost", "btn primary", "mine", "own", "Again"])
        self.assertEqual(r["action"], ["button", "ui-toast-action link-btn", "u1", "Undo"])
        self.assertEqual(r["undone"], 1)
        self.assertTrue(r["closed"])

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
  const docListeners = {};
  const els = {};
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
    document: { body: {}, addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
                getElementById: (id) => els[id] || null },
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
    pos: () => idx, els,
    /** A left click on <a href=…> (attrs: other attributes); → whether the default was prevented. */
    click(href, attrs) {
      const a = { getAttribute: (k) => (k === "href" ? href : (attrs || {})[k] ?? null), hasAttribute: (k) => k in (attrs || {}), target: "" };
      let prevented = false;
      const ev = { button: 0, target: { closest: () => a }, get defaultPrevented() { return prevented; }, preventDefault() { prevented = true; } };
      (docListeners.click || []).forEach((fn) => fn(ev));
      return prevented;
    },
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

    def test_peek_clear_trail_and_keep_trail(self):
        r = self.run_js(r"""
          const b = browser("#/home"); const A = app(b, "home", { isTab: (h) => /^#\/[a-z]+$/.test(h) });
          const N = b.win.BackNav;
          const empty = N.peek();
          N.go("#/dash"); N.go("#/dash/day/1"); b.run();
          const p1 = N.peek();
          N.go("#/goals", { keepTrail: true }); b.run();           // a link from one tab into another
          const kept = N._trail.get(), p2 = N.peek();
          b.history.back(); b.run(); const back = b.location.hash;
          N.go("#/goals"); b.run(); const reset = N._trail.get();  // a plain tab change still starts a new trail
          N.go("#/goals/x"); b.run(); N.clearTrail(); b.run();
          process.stdout.write(JSON.stringify({ empty, p1, kept, p2, back, reset, cleared: N._trail.get(), p3: N.peek(),
            saved: JSON.parse(b.store[N._trail.key]) }));""")
        self.assertEqual(r, {"empty": None, "p1": "#/dash", "kept": ["#/dash", "#/dash/day/1"], "p2": "#/dash/day/1",
                             "back": "#/dash/day/1", "reset": [], "cleared": [], "p3": None, "saved": []})

    def test_link_with_keep_trail_and_in_page_anchors(self):
        r = self.run_js(r"""
          const b = browser("#/dash"); const A = app(b, "dash", { isTab: (h) => /^#\/[a-z]+$/.test(h) });
          const N = b.win.BackNav;
          const k1 = b.click("#/goals", { "data-keep-trail": "" }); b.run();
          const kept = N._trail.get(), at = b.location.hash;
          let scrolled = null;
          b.els["flagged"] = { tagName: "DETAILS", open: false, scrollIntoView(o) { scrolled = o.block; } };
          const k2 = b.click("#flagged"); b.run();
          const anchor = [b.location.hash, b.els.flagged.open, scrolled, N._trail.get().length];
          const k3 = b.click("#nothere"); b.run();                  // no such element: an address, as before
          process.stdout.write(JSON.stringify({ k1, kept, at, k2, anchor, k3, last: b.location.hash }));""")
        self.assertEqual(r, {"k1": True, "kept": ["#/dash"], "at": "#/goals", "k2": True,
                             "anchor": ["#/goals", True, "start", 1], "k3": True, "last": "#nothere"})

    def test_replace_empty_clears_the_hash(self):
        r = self.run_js(r"""
          const b = browser("#/chat/5"); const A = app(b, "chat");
          const N = b.win.BackNav;
          N.replace(""); b.run();
          const one = [b.location.href, A.seen.slice(), N._trail.get()];
          N.go("#/x", { replace: true, silent: true }); b.run();
          process.stdout.write(JSON.stringify({ one, two: [b.location.hash, A.seen.length] }));""")
        self.assertEqual(r, {"one": ["http://h/app/", [""], []], "two": ["#/x", 1]})

    def test_parent_of_gets_the_page_url(self):
        r = self.run_js(r"""
          const b = browser("", {}, "review.html?id=7"); const seen = [];
          const A = app(b, "review", { parentOf: (h, url) => { seen.push([h, url]); return "/app/index.html"; } });
          b.history.back(); b.run();
          process.stdout.write(JSON.stringify({ seen: seen.slice(0, 1), replaced: b.replaced }));""")
        self.assertEqual(r, {"seen": [["", "/app/review.html?id=7"]], "replaced": ["/app/index.html"]})

    def test_go_page_takes_the_guard_away_first(self):
        r = self.run_js(r"""
          const store = {};
          const b = browser("#/home", store); const A = app(b, "home");
          const N = b.win.BackNav;
          N.go("#/a"); b.run();
          const guarded = [b.pos(), !!b.history.state];
          A.layers.push("dialog");                                   // even with a dialog open
          N.goPage("/app/deals.html"); N.goPage("/app/other.html");  // a second call while leaving is ignored
          const before = b.replaced.length;
          b.run();
          const first = { guarded, before, replaced: b.replaced, pos: b.pos(), closed: A.closed,
                          trail: JSON.parse(store["backnav.trail:/app/"]) };
          // the new page: its ← button (BackNav.back) with the guard in place leaves no entry behind either
          const c = browser("", store, "deals.html"); const B = app(c, "deals");
          const g = c.pos();
          const went = c.win.BackNav.back(); c.run();
          // and goPage({replace: true}) doesn't remember the page
          const d = browser("", {}, "capture.html"); app(d, "capture");
          d.win.BackNav.goPage("/app/review.html", { replace: true }); d.run();
          process.stdout.write(JSON.stringify({ first, second: { g, went, replaced: c.replaced, pos: c.pos() },
            third: [d.replaced, d.win.BackNav._trail.get()] }));""")
        self.assertEqual(r["first"], {"guarded": [1, True], "before": 0, "replaced": ["/app/deals.html"], "pos": 0, "closed": 0,
                                      "trail": ["#/home", "/app/#/a"]})
        self.assertEqual(r["second"], {"g": 1, "went": True, "replaced": ["/app/#/a"], "pos": 0})
        self.assertEqual(r["third"], [["/app/review.html"], []])

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
