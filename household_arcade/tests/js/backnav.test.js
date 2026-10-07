"use strict";
// The back gesture (common/backnav.js, this app's copy) in a small fake browser history: Back pops the guard and goes
// home or closes the top layer; a link or a notification that changes the address (setting location.hash adds an entry
// and fires popstate, then hashchange — as Chrome does) is a navigation the app must see, not the back gesture.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const FILE = path.join(__dirname, "..", "..", "app", "static", "common", "backnav.js");

function browser(startHash) {
  const base = "http://h/app/";
  const listeners = {};
  const entries = [{ href: base + startHash, state: null }];
  let idx = 0;
  const timers = [];
  const location = {
    get href() { return entries[idx].href; },
    get hash() { const h = entries[idx].href; const i = h.indexOf("#"); return i < 0 ? "" : h.slice(i); },
  };
  const fire = (type, ev) => { for (const fn of (listeners[type] || []).slice()) fn(Object.assign({ type }, ev || {})); };
  const history = {
    get length() { return entries.length; },
    get state() { return entries[idx].state; },
    pushState(state, _t, url) { entries.splice(idx + 1); entries.push({ href: abs(url), state }); idx++; },
    replaceState(state, _t, url) { entries[idx] = { href: abs(url), state }; },
    back() { if (idx > 0) { const old = location.href; idx--; timers.push(() => { fire("popstate"); if (old.split("#")[1] !== location.href.split("#")[1]) fire("hashchange", { newURL: location.href, oldURL: old }); }); } },
  };
  function abs(url) { return url.startsWith("#") ? base + url : url; }
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
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(fs.readFileSync(FILE, "utf8"), win);
  return {
    win, location, history, entries,
    /** Let the queued timers and animation frames run. */
    run() { for (let k = 0; k < 20 && timers.length; k++) timers.splice(0).forEach((fn) => fn()); },
    /** What setting location.hash does: a new entry, then popstate and hashchange. */
    setHash(h) { const old = location.href; entries.splice(idx + 1); entries.push({ href: base + h, state: null }); idx++; fire("popstate"); fire("hashchange", { newURL: location.href, oldURL: old }); },
    on(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  };
}

function app(b, startTab) {
  const A = { tab: startTab, home: 0, closed: 0, layers: [], seen: [] };
  b.win.BackNav.init({
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

test("a notification's link changes the address on the Games page: the app sees it (not reverted, not Back)", () => {
  const b = browser("#/home");
  const A = app(b, "home");
  assert.equal(b.entries.length, 1, "no guard at home");
  b.setHash("#/home/turn/abc");
  b.run();
  assert.equal(b.location.hash, "#/home/turn/abc");
  assert.deepEqual(A.seen, ["#/home/turn/abc"]);
  assert.equal(A.home, 0);
});

test("the same while a game page is open (the guard is in place)", () => {
  const b = browser("#/play/ludo");
  const A = app(b, "play");
  assert.equal(b.entries.length, 2, "the guard");
  b.setHash("#/home/turn/xyz");
  b.run();
  assert.equal(b.location.hash, "#/home/turn/xyz");
  assert.equal(A.home, 0);
  assert.equal(A.closed, 0);
});

test("Back still pops the guard: home first (the address stays the page's own), then a dialog is closed first", () => {
  const b = browser("#/play/ludo");
  const A = app(b, "play");
  b.history.replaceState(b.history.state, "", "#/scores");          // the app moved on to another page (no new entry)
  A.tab = "scores";
  b.win.BackNav.sync();
  b.run();
  b.history.back();
  b.run();
  assert.equal(A.home, 1, "Back went home");
  assert.equal(b.location.hash, "#/home");
  // with a dialog open, Back closes it and stays on the page
  const c = browser("#/home");
  const B = app(c, "home");
  B.layers.push("dialog");
  c.win.BackNav.sync();
  c.run();
  assert.equal(c.entries.length, 2);
  c.history.back();
  c.run();
  assert.equal(B.closed, 1);
  assert.equal(B.home, 0);
});
