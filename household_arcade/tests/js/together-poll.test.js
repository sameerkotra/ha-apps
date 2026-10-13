"use strict";
// One request for "my matches" shared by everything on the page that looks (the Games page's cards, the invite
// sheet, a result card's rematch), and polls that stop while the page is hidden and look at once when it is back.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { GAMES_DIR } = require("./helpers");

function load() {
  const calls = [], listeners = {}, timers = [];
  const doc = { hidden: false, addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); } };
  const h = () => ({});
  const ctx = vm.createContext({
    window: {}, console, JSON, Math, Date, Promise, URL, Object, Array, String, Number, Set, setTimeout, clearTimeout,
    setInterval(fn, ms) { timers.push({ fn, ms, on: true }); return timers.length; },
    clearInterval(id) { if (timers[id - 1]) timers[id - 1].on = false; },
    performance: { now: () => 0 }, location: { href: "https://ha.example/x/", protocol: "https:" }, document: doc,
    h, spinner: h, mount() {}, clear() {}, openModal: () => ({ close() {} }), fmtNum: String, toast() {}, fail() {}, showTab() {},
    $: () => null, state: { tab: "home" }, renderHome() {},
    api: (p) => { calls.push(p); return Promise.resolve({ waiting: [], sent: [], playing: [], recent: [] }); },
  });
  ctx.window.window = ctx.window;
  vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, "..", "together.js"), "utf8"), ctx, { filename: "together.js" });
  return { T: ctx.window.Together, calls, doc, fire: (t) => (listeners[t] || []).forEach((fn) => fn()), timers };
}

test("two lookers at once share one request", async () => {
  const { T, calls } = load();
  const [a, b] = await Promise.all([T.matches(), T.matches()]);
  assert.equal(a, b);
  assert.deepEqual(calls, ["api/matches"]);
});

test("a poll stops while the page is hidden and looks at once when it comes back", () => {
  const { T, doc, fire, timers } = load();
  let runs = 0;
  const stop = T.poll(() => { runs++; }, 5000);
  assert.equal(timers.filter((t) => t.on).length, 1);
  doc.hidden = true; fire("visibilitychange");
  assert.equal(timers.filter((t) => t.on).length, 0, "no timer while hidden");
  doc.hidden = false; fire("visibilitychange");
  assert.equal(runs, 1, "looked at once");
  assert.equal(timers.filter((t) => t.on).length, 1);
  stop();
  assert.equal(timers.filter((t) => t.on).length, 0);
  doc.hidden = true; fire("visibilitychange"); doc.hidden = false; fire("visibilitychange");
  assert.equal(runs, 1, "a stopped poll stays stopped");
});
