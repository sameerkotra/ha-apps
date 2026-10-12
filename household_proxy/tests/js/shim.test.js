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
  const history = { state: null, pushState: (s, t, u) => calls.push(["push", u]), replaceState: (s, t, u) => calls.push(["replace", u]) };
  const navListeners = [];
  const win = {
    location, history, URL, JSON, Object, Array, String, Request: Req,
    fetch: (u, init) => { calls.push(["fetch", u instanceof Req ? u.url : u]); return Promise.resolve(); },
    open: (u) => calls.push(["open", u]),
    XMLHttpRequest: XHR, WebSocket: WS, EventSource: ES, Worker,
    Element,
    HTMLScriptElement: el("HTMLScriptElement", ["src"]), HTMLImageElement: el("HTMLImageElement", ["src", "srcset"]),
    HTMLAnchorElement: el("HTMLAnchorElement", ["href"]), HTMLFormElement: el("HTMLFormElement", ["action"]),
    HTMLLinkElement: el("HTMLLinkElement", ["href"]),
    navigation: { addEventListener: (t, f) => navListeners.push(f) },
  };
  win.window = win;
  win.navigator = { sendBeacon: (u) => calls.push(["beacon", u]) };
  const cfg = { p: P, i: ING, o: SITE, spa: !!opts.spa, v: opts.spa ? "/stacks/1?x=1" : null };
  win.document = { currentScript: { getAttribute: () => JSON.stringify(cfg) } };
  vm.createContext(win);
  vm.runInContext(SRC, win);
  return { win, calls, navListeners };
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
