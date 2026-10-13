// The proxy's in-page script (app/static/_hp/shim.js) in a small fake browser: each wrapped API maps URLs by the
// one rule; other hosts are left alone; single-page app mode changes the frame's address.
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "..", "app", "static", "_hp", "shim.js"), "utf8");
const ING = "/api/hassio_ingress/TOK";
const P = ING + "/s/nas";
const SITE = "http://192.168.1.10:5000";

function browser(opts = {}) {
  const calls = [];
  const loc = new URL(opts.href || `https://ha.example.com${P}/page`);
  const location = { href: loc.href, origin: loc.origin, host: loc.host, protocol: loc.protocol, pathname: loc.pathname,
    assign: (u) => calls.push(["assign", u]) };
  class Element { setAttribute(n, v) { calls.push(["setAttribute", n, v]); } setAttributeNS(ns, n, v) { calls.push(["setAttributeNS", n, v]); } }
  function el(name, props) {
    const C = class extends Element {};
    Object.defineProperty(C, "name", { value: name });
    for (const p of props) {
      Object.defineProperty(C.prototype, p, { configurable: true, get() { return this["_" + p]; }, set(v) { this["_" + p] = v; } });
    }
    return C;
  }
  class XHR { open(m, u) { calls.push(["xhr", m, u]); } }
  class WS { constructor(u, p) { calls.push(["ws", u, p]); } }
  WS.OPEN = 1;
  class ES { constructor(u) { calls.push(["es", u]); } }
  class Worker { constructor(u) { calls.push(["worker", u]); } }
  class Req { constructor(u, init) { this.url = u instanceof Req ? u.url : u; this.init = init; } }
  class Storage {
    constructor(init) { Object.defineProperty(this, "_m", { value: new Map(Object.entries(init || {})) }); }
    getItem(k) { return this._m.has(String(k)) ? this._m.get(String(k)) : null; }
    setItem(k, v) { this._m.set(String(k), String(v)); }
    removeItem(k) { this._m.delete(String(k)); }
    key(i) { const ks = [...this._m.keys()]; return i < ks.length ? ks[i] : null; }
    clear() { this._m.clear(); }
    get length() { return this._m.size; }
  }
  const jar = new Map(Object.entries(opts.cookies || {}));
  class Document {
    get cookie() { return [...jar].map(([k, v]) => `${k}=${v}`).join("; "); }
    set cookie(v) { calls.push(["cookie", v]); const nv = v.split(";")[0]; const i = nv.indexOf("="); jar.set(nv.slice(0, i), nv.slice(i + 1)); }
  }
  const history = { state: null, pushState: (s, t, u) => calls.push(["push", u]), replaceState: (s, t, u) => calls.push(["replace", u]) };
  const navListeners = [];
  const win = {
    location, history, URL, JSON, Object, Array, String, Request: Req,
    fetch: (u, init) => { calls.push(["fetch", u instanceof Req ? u.url : u]); return Promise.resolve(); },
    open: (u) => calls.push(["open", u]),
    XMLHttpRequest: XHR, WebSocket: WS, EventSource: ES, Worker,
    Element, Storage, Document,
    localStorage: new Storage(opts.storage), sessionStorage: new Storage(),
    HTMLScriptElement: el("HTMLScriptElement", ["src"]), HTMLImageElement: el("HTMLImageElement", ["src", "srcset"]),
    HTMLAnchorElement: el("HTMLAnchorElement", ["href"]), HTMLFormElement: el("HTMLFormElement", ["action"]),
    HTMLLinkElement: el("HTMLLinkElement", ["href"]),
    navigation: { addEventListener: (t, f) => navListeners.push(f) },
  };
  win.window = win;
  win.navigator = { sendBeacon: (u) => calls.push(["beacon", u]) };
  const cfg = { p: P, i: ING, o: SITE, spa: !!opts.spa, v: opts.spa ? "/stacks/1?x=1" : null };
  const cfg2 = opts.noSlug ? (delete cfg.s, cfg) : Object.assign(cfg, { s: "nas" });
  win.document = new Document();
  win.document.currentScript = { getAttribute: () => JSON.stringify(cfg2) };
  vm.createContext(win);
  vm.runInContext(SRC, win);
  return { win, calls, navListeners, jar };
}

test("root-relative, site-origin and Home Assistant-origin URLs go through P", () => {
  const { win } = browser();
  const fix = win.__householdProxy.fix;
  assert.equal(fix("/api/x?y=1"), P + "/api/x?y=1");
  assert.equal(fix(SITE + "/api/x"), "https://ha.example.com" + P + "/api/x");
  assert.equal(fix("https://ha.example.com/api/states"), "https://ha.example.com" + P + "/api/states");
  assert.equal(fix("//192.168.1.10:5000/z"), "https://ha.example.com" + P + "/z");
});

test("other URLs are left alone", () => {
  const { win } = browser();
  const fix = win.__householdProxy.fix;
  for (const u of ["x", "./x", "../x", "?q=1", "#h", "https://example.com/x", "data:text/plain,hi", "blob:abc",
    P + "/already", ING + "/other", "https://ha.example.com" + P + "/x", "", "//evil.example.com/x", "/\\evil.com"]) {
    assert.equal(fix(u), u, u);
  }
  assert.equal(fix(null), null);
  assert.equal(fix(undefined), undefined);
});

test("websocket addresses go to Home Assistant's host", () => {
  const { win, calls } = browser();
  new win.WebSocket("ws://192.168.1.10:5000/ws?a=1", ["p1"]);
  new win.WebSocket("/ws");
  new win.WebSocket("wss://ha.example.com/live");
  new win.WebSocket("wss://other.example.com/x");
  assert.deepEqual(calls.filter((c) => c[0] === "ws").map((c) => c[1]), [
    "wss://ha.example.com" + P + "/ws?a=1", P + "/ws", "wss://ha.example.com" + P + "/live", "wss://other.example.com/x"]);
  assert.equal(calls[0][2][0], "p1");
  assert.equal(win.WebSocket.OPEN, 1);
});

test("fetch, XHR, EventSource, workers, beacon, window.open", async () => {
  const { win, calls } = browser();
  await win.fetch("/a");
  await win.fetch(new win.Request("https://ha.example.com/b"));
  new win.XMLHttpRequest().open("GET", "/c");
  new win.EventSource("/d");
  new win.Worker("/e.js");
  win.navigator.sendBeacon("/f");
  win.open("/g");
  assert.deepEqual(calls.map((c) => c[c.length - 1]), [P + "/a", "https://ha.example.com" + P + "/b", P + "/c", P + "/d",
    P + "/e.js", P + "/f", P + "/g"]);
});

test("element setters and setAttribute", () => {
  const { win, calls } = browser();
  const img = new win.HTMLImageElement();
  img.src = "/i.png";
  img.srcset = "/a.png 1x, /b.png 2x";
  assert.equal(img._src, P + "/i.png");
  assert.equal(img._srcset, `${P}/a.png 1x, ${P}/b.png 2x`);
  const a = new win.HTMLAnchorElement();
  a.href = "https://example.com/";
  assert.equal(a._href, "https://example.com/");
  new win.Element().setAttribute("href", "/x");
  new win.Element().setAttribute("class", "/not-a-url");
  new win.Element().setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", "/sprite.svg#i");
  assert.deepEqual(calls, [["setAttribute", "href", P + "/x"], ["setAttribute", "class", "/not-a-url"],
    ["setAttributeNS", "xlink:href", P + "/sprite.svg#i"]]);
});

test("history stays under P normally", () => {
  const { win, calls } = browser();
  win.history.pushState(null, "", "/stacks");
  win.history.replaceState(null, "", "rel");
  assert.deepEqual(calls, [["push", P + "/stacks"], ["replace", "rel"]]);
});

test("single-page app mode shows the site's own path and leaves the app's navigation alone", () => {
  const { win, calls } = browser({ spa: true });
  assert.deepEqual(calls[0], ["replace", "/stacks/1?x=1"]);
  win.history.pushState(null, "", "/stacks/2");
  assert.deepEqual(calls[1], ["push", "/stacks/2"]);
});

test("a full navigation to Home Assistant's own address goes through P", () => {
  const { navListeners, calls } = browser();
  let prevented = false;
  navListeners[0]({ hashChange: false, destination: { url: "https://ha.example.com/login?n=1", sameDocument: false },
    preventDefault: () => { prevented = true; } });
  assert.ok(prevented);
  assert.deepEqual(calls[0], ["assign", "https://ha.example.com" + P + "/login?n=1"]);
  prevented = false;
  navListeners[0]({ hashChange: false, destination: { url: "https://ha.example.com" + P + "/x", sameDocument: false },
    preventDefault: () => { prevented = true; } });
  navListeners[0]({ hashChange: false, destination: { url: "https://example.com/", sameDocument: false },
    preventDefault: () => { prevented = true; } });
  assert.ok(!prevented);
});

test("runs once", () => {
  const { win } = browser();
  const fetchBefore = win.fetch;
  vm.runInContext(SRC, win);
  assert.equal(win.fetch, fetchBefore);
});

test("a page's own subclass of WebSocket keeps its class", () => {
  const { win, calls } = browser();
  const r = vm.runInContext("class MyWS extends WebSocket { hi() { return 'hi'; } }; " +
    "const m = new MyWS('/live'); [m instanceof MyWS, m.hi(), new WebSocket('/x') instanceof WebSocket]", win);
  assert.deepEqual(Array.from(r), [true, "hi", true]);
  assert.deepEqual(calls.filter((c) => c[0] === "ws").map((c) => c[1]), [P + "/live", P + "/x"]);
});

test("each site sees only its own browser storage", () => {
  const { win } = browser({ storage: { hassTokens: "secret", "hp:other:k": "theirs" } });
  const ls = win.localStorage;
  assert.equal(ls.getItem("hassTokens"), null);              // Home Assistant's sign-in isn't this site's
  assert.equal(ls.length, 0);
  ls.setItem("theme", "dark");
  ls.setItem("k", "mine");
  assert.equal(ls.getItem("theme"), "dark");
  assert.equal(ls._m.get("hp:nas:theme"), "dark");
  assert.equal(ls.length, 2);
  assert.deepEqual([ls.key(0), ls.key(1), ls.key(2)], ["theme", "k", null]);
  ls.removeItem("theme");
  assert.equal(ls.getItem("theme"), null);
  ls.clear();                                                // clears this site's keys only
  assert.deepEqual([...ls._m.keys()], ["hassTokens", "hp:other:k"]);
  win.sessionStorage.setItem("s", "1");
  assert.equal(win.sessionStorage._m.get("hp:nas:s"), "1");
});

test("each site sees and sets only its own cookies, on its own path", () => {
  const { win, calls, jar } = browser({ cookies: { ingress_session: "HA", hp_nas_sid: "1", hp_other_x: "2" } });
  assert.equal(win.document.cookie, "sid=1");
  win.document.cookie = "lang=en; path=/; domain=nas.lan; Secure; SameSite=None";
  win.document.cookie = "tab=2";
  win.document.cookie = "deep=1; Path=/app";
  win.document.cookie = "__Host-t=1; Path=/; Secure";
  assert.deepEqual(calls.filter((c) => c[0] === "cookie").map((c) => c[1]), [
    `hp_nas_lang=en; Secure; SameSite=None; path=${P}/`, `hp_nas_tab=2; path=${P}/`, `hp_nas_deep=1; path=${P}/app`,
    `hp_nas___Host-t=1; Secure; path=${P}/`]);
  assert.equal(jar.get("ingress_session"), "HA");
  assert.equal(win.document.cookie, "sid=1; lang=en; tab=2; deep=1; __Host-t=1");
});

test("on plain http, Secure is left out (the browser would refuse the cookie)", () => {
  const { win, calls } = browser({ href: `http://ha.local:8123${P}/` });
  win.document.cookie = "a=1; Secure; SameSite=None";
  assert.deepEqual(calls.filter((c) => c[0] === "cookie").map((c) => c[1]), [`hp_nas_a=1; SameSite=Lax; path=${P}/`]);
});

test("without the short name in its settings, it is read from P", () => {
  const { win } = browser({ noSlug: true });
  win.localStorage.setItem("a", "1");
  assert.equal(win.localStorage._m.get("hp:nas:a"), "1");
});
