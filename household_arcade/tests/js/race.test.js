"use strict";
// Playing together, step 1 (a race, spec §13.3): every game that can be raced runs the same on two phones from
// the same seed (so the race is fair), the kit's status() and the registry's race flag, and the live link in the
// browser (a WebSocket, falling back to a long poll when the socket can't open or drops).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { makeSandbox, GAMES_DIR } = require("./helpers");

const RACE = {
  snake: "walls-normal", brick: "classic", blocks: "classic", racer: "three", flap: "normal", mines: "easy",
  merge: "classic", colours: "classic", cards: "small", mole: "classic", numbers: "add", invaders: "classic",
  rocks: "classic", hop: "classic",
  // wave 5 (more on them, with their level-list modes, in wave5.test.js)
  bubbles: "classic", gems: "timed", stack: "classic", runner: "classic", lander: "classic", defense: "classic",
  // wave 6 (more on them in wave6.test.js)
  slide: "four", lights: "classic", nonogram: "ten", tiles: "classic", codebreak: "classic", typerain: "classic",
};
const NOT_RACE = ["snakeduel"];     // two players on one screen; Paddle Duel and Tank Battle are left out by the server (games.py / together.RACE_DEFAULT)

// A small deterministic stream of key presses and taps, the same for both phones.
function script(seed) {
  let x = seed >>> 0;
  return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; };
}
const ACTIONS = ["up", "down", "left", "right", "fire", "alt"];

function run(gameId, mode, seed, frames) {
  const sb = makeSandbox();
  const ends = [];
  const inst = sb.win.ArcadeGames.get(gameId).create(sb.canvas(390, 487), { mode, seed, look: "modern", onEnd: (r) => ends.push(r) });
  inst.start();
  const rnd = script(99);
  for (let i = 0; i < frames; i++) {
    const r = rnd();
    if (i % 5 === 0) {
      const a = ACTIONS[Math.floor(rnd() * ACTIONS.length)];
      inst.input(a, true);
      if (rnd() < 0.7) inst.input(a, false);
    }
    if (i % 11 === 0) {
      const px = rnd() * 390, py = rnd() * 487;
      inst.pointer("down", px, py); inst.pointer("move", px + 5, py + 5); inst.pointer("up", px + 5, py + 5);
    }
    if (r < 0.01) inst.pointer("move", rnd() * 390, 400);
    sb.frames(1);
  }
  const out = { logic: JSON.stringify(inst.logic), score: inst.score, level: inst.level, status: inst.status(), ends };
  inst.destroy();
  return out;
}

for (const [gameId, mode] of Object.entries(RACE)) {
  test(`${gameId}: two phones with the same seed play the same game`, () => {
    const a = run(gameId, mode, 4242, 900), b = run(gameId, mode, 4242, 900);
    assert.equal(a.logic, b.logic, "the whole state is identical");
    assert.equal(a.score, b.score);
    assert.equal(a.level, b.level);
    assert.deepEqual(JSON.parse(JSON.stringify(a.ends)), JSON.parse(JSON.stringify(b.ends)));
  });
}

test("a different seed gives a different game (so the seed is what the race shares)", () => {
  let differ = 0;
  for (const [gameId, mode] of Object.entries(RACE)) {
    if (run(gameId, mode, 4242, 120).logic !== run(gameId, mode, 777, 120).logic) differ++;
  }
  assert.ok(differ >= Object.keys(RACE).length - 2, `${differ} of ${Object.keys(RACE).length} games differ`);
});

test("the registry: a game can be raced unless it is a two-player game or says race: false", () => {
  const { win } = makeSandbox();
  for (const id of Object.keys(RACE)) assert.equal(win.ArcadeGames.get(id).race, true, id);
  for (const id of NOT_RACE) assert.equal(win.ArcadeGames.get(id).race, false, id);
  win.ArcadeGames.register({ id: "solo", name: "Solo", modes: [{ id: "a", label: "A" }], create() {} });
  assert.equal(win.ArcadeGames.get("solo").race, true);
  win.ArcadeGames.register({ id: "alone", name: "Alone", race: false, modes: [{ id: "a", label: "A" }], create() {} });
  assert.equal(win.ArcadeGames.get("alone").race, false);
});

test("status(): score, level, over and paused, for every game that can be raced", () => {
  for (const [gameId, mode] of Object.entries(RACE)) {
    const sb = makeSandbox();
    const inst = sb.win.ArcadeGames.get(gameId).create(sb.canvas(), { mode, seed: 3 });
    const st = () => JSON.parse(JSON.stringify(inst.status()));
    assert.deepEqual(Object.keys(st()), ["score", "level", "over", "paused"], gameId);
    assert.equal(st().score, 0, gameId);
    assert.ok(st().level >= 1, gameId);
    assert.equal(st().over, false);
    inst.start(); sb.frames(10);
    assert.equal(st().over, false);
    inst.pause();
    assert.deepEqual(st(), { score: inst.score, level: inst.level, over: false, paused: true }, gameId);
    inst.destroy();
  }
});

test("status(): over when the game ends", () => {
  const sb = makeSandbox();
  const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), { mode: "walls-normal", seed: 3 });
  inst.start();
  sb.frames(700);
  assert.equal(inst.state, "over");
  assert.equal(inst.status().over, true);
});

// ---------- the browser side: together.js in a sandbox ----------

function together(over = {}) {
  const calls = { api: [], ws: [], timeouts: [] };
  const sockets = [];
  class FakeWebSocket {
    constructor(url) {
      if (over.wsThrows) throw new Error("no sockets here");
      this.url = url; this.readyState = 0; this.sent = []; sockets.push(this);
    }
    send(t) { this.sent.push(JSON.parse(t)); }
    close() { this.readyState = 3; this.closed = true; }
    open() { this.readyState = 1; }
    message(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
    drop() { this.readyState = 3; if (this.onclose) this.onclose(); }
  }
  const h = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false) });
  let clock = 1000;
  let answers = over.answers || (() => ({ v: 1, status: "playing", players: [] }));
  const ctx = vm.createContext({
    window: {}, console, JSON, Math, Date, Promise, URL, Object, Array, String, Number, setTimeout, clearTimeout, setInterval, clearInterval,
    performance: { now: () => clock },
    location: { href: over.href || "https://ha.example/api/hassio_ingress/abc/", protocol: "https:" },
    document: { hidden: false },
    WebSocket: over.noWebSocket ? undefined : FakeWebSocket,
    h, spinner: () => h("div"), mount: () => {}, clear: () => {}, openModal: () => ({ close() {} }), modalStack: [], fmtNum: (n) => String(n),
    fmtDuration: (n) => `${n} s`, toast() {}, fail() {}, showTab() {}, $: () => null, state: { tab: "home" }, renderHome() {},
    // a long poll takes its time, as it does on the server (an instant answer would spin the loop)
    api: (p, o) => { calls.api.push([p, o]); return new Promise((r) => setTimeout(r, /wait=\d+/.test(p) ? 150 : 0)).then(() => answers(p, o)); },
  });
  ctx.window.window = ctx.window;
  vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, "..", "together.js"), "utf8"), ctx, { filename: "together.js" });
  const T = ctx.window.Together, realLink = T.link;
  T.link = (id, hnd) => { const l = realLink(id, hnd); links.push(l); return l; };
  return { T, calls, sockets, tick(ms) { clock += ms; }, setAnswers(f) { answers = f; } };
}
const links = [];
test.afterEach(() => { while (links.length) links.pop().close(); });
const plain = (v) => JSON.parse(JSON.stringify(v));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test("the link uses a WebSocket through ingress (relative URL, wss on https) and sends my state", async () => {
  const t = together();
  const got = [], kinds = [];
  const link = t.T.link("M1", { match: (m) => got.push(m), transport: (k) => kinds.push(k) });
  assert.equal(t.sockets.length, 1);
  assert.equal(t.sockets[0].url, "wss://ha.example/api/hassio_ingress/abc/api/matches/M1/live");
  t.sockets[0].open();
  t.sockets[0].message({ t: "match", v: 4, status: "playing", players: [] });
  assert.deepEqual(kinds, ["websocket"]);
  assert.equal(got[0].v, 4);
  assert.equal(link.transport, "websocket");
  link.send({ score: 40, level: 2, over: false, paused: false });
  await wait(500);
  assert.deepEqual(plain(t.sockets[0].sent[0]), { t: "state", score: 40, level: 2, over: false, paused: false });
  const n = t.sockets[0].sent.length;
  await wait(500);
  assert.equal(t.sockets[0].sent.length, n, "an unchanged state isn't resent every time");
  link.send({ score: 50, level: 2, over: false, paused: false });
  await wait(500);
  assert.equal(t.sockets[0].sent[t.sockets[0].sent.length - 1].score, 50);
  link.close();
  assert.equal(t.sockets[0].closed, true);
});

test("http is plain ws", () => {
  const t = together({ href: "http://homeassistant.local:8123/api/hassio_ingress/x/" });
  t.T.link("M2", { match() {} });
  assert.ok(t.sockets[0].url.startsWith("ws://homeassistant.local:8123/"));
});

test("without WebSocket the link long-polls and posts the state", async () => {
  let n = 0;
  const t = together({ noWebSocket: true, answers: (p) => { n++; return { v: n, status: "playing", players: [] }; } });
  const got = [], kinds = [];
  const link = t.T.link("M3", { match: (m) => got.push(m.v), transport: (k) => kinds.push(k) });
  await wait(60);
  assert.deepEqual(kinds, ["poll"]);
  const polls = t.calls.api.filter(([p]) => /^api\/matches\/M3\?since=\d+&wait=20$/.test(p));
  assert.ok(polls.length >= 1, "a long poll is waiting");
  link.send({ score: 7, level: 1, over: false, paused: false });
  await wait(500);
  const posts = t.calls.api.filter(([p, o]) => p === "api/matches/M3/state" && o.method === "POST");
  assert.ok(posts.length >= 1);
  assert.deepEqual(plain(posts[0][1].body), { score: 7, level: 1, over: false, paused: false });
  link.close();
});

test("a socket that throws, never opens or drops falls back to polling", async () => {
  // throws at once
  let t = together({ wsThrows: true });
  const kinds = [];
  t.T.link("M4", { match() {}, transport: (k) => kinds.push(k) });
  await wait(30);
  assert.deepEqual(kinds, ["poll"]);
  // an error before it ever spoke
  t = together();
  const k2 = [];
  t.T.link("M5", { match() {}, transport: (k) => k2.push(k) });
  t.sockets[0].onerror();
  await wait(30);
  assert.deepEqual(k2, ["poll"]);
  // spoke, then dropped
  t = together();
  const k3 = [];
  t.T.link("M6", { match() {}, transport: (k) => k3.push(k) });
  t.sockets[0].open();
  t.sockets[0].message({ t: "match", v: 1, players: [] });
  t.sockets[0].drop();
  await wait(30);
  assert.deepEqual(k3, ["websocket", "poll"]);
});

test("a socket that doesn't open in three seconds is given up on", async () => {
  const t = together();
  const kinds = [];
  const link = t.T.link("M7", { match() {}, transport: (k) => kinds.push(k) });
  await wait(3200);
  assert.deepEqual(kinds, ["poll"]);
  assert.equal(t.sockets[0].closed, true);
  link.close();
});

test("a closed link is silent", async () => {
  const t = together({ noWebSocket: true });
  const got = [];
  const link = t.T.link("M8", { match: (m) => got.push(m) });
  link.close();
  await wait(50);
  const before = t.calls.api.length;
  link.send({ score: 1, level: 1 });
  await wait(500);
  assert.equal(t.calls.api.length, before);
});

test("the race bar shows the other player's score, level and whether they are still playing", () => {
  const { T } = together();
  const player = (o) => Object.assign({ id: "u2", name: "Meera Rao", you: false, score: 120, level: 3, over: false, final: false, paused: false, connected: true, started: true }, o);
  const text = (m) => { const kids = T.barContent(m, "websocket"); return plain(kids.map((k) => k.kids.join("")).filter(Boolean)); };
  const base = (p) => ({ status: "playing", players: [{ id: "u1", name: "Kabir Rao", you: true }, p] });
  assert.deepEqual(text(base(player({}))), ["Meera", "120", "L3", "playing"]);
  assert.deepEqual(text(base(player({ paused: true })))[3], "paused");
  assert.deepEqual(text(base(player({ connected: false })))[3], "connection lost?");
  assert.deepEqual(text(base(player({ over: true })))[3], "finishing…");
  assert.deepEqual(text(base(player({ over: true, final: true })))[3], "finished");
  assert.deepEqual(text(base(player({ over: true, final: true, seconds: 372 })))[3], "finished in 6:12");
  assert.deepEqual(text(base(player({ started: false, connected: false, score: 0, level: 1 })))[3], "getting ready");
  assert.deepEqual(text({ status: "invited", players: [{ id: "u1", you: true }, player({})] })[3], "has been invited");
  assert.deepEqual(plain(T.barContent(null)), []);
  const dot = T.barContent(base(player({})), "poll")[0];
  assert.match(dot.attrs.class, /\bon\b/);
  assert.equal(dot.attrs.title, "Live link: poll");
  assert.match(T.barContent(base(player({ connected: false })), "poll")[0].attrs.class, /\boff\b/);
});

test("the result headline says who won, in either direction, and when someone left", () => {
  const { T } = together();
  const m = (you, other, endReason) => ({ endReason, players: [{ id: "u1", you: true, name: "Kabir Rao", result: you }, { id: "u2", you: false, name: "Meera Rao", result: other }] });
  assert.equal(T.headline(m("won", "lost")), "🏆 You won!");
  assert.equal(T.headline(m("lost", "won")), "Meera won");
  assert.equal(T.headline(m("draw", "draw")), "A draw");
  assert.match(T.headline(m("won", "lost", "left")), /Meera left the game\. You win!/);
  assert.equal(T.headline(m(null, null)), "");
});

test("the count-in and the invite's time left run from when the picture arrived", () => {
  const t = together();
  const m = t.T.stamp({ startsInMs: 4000, expiresIn: 300 });
  assert.equal(Math.round(t.T.startsIn(m)), 4000);
  t.tick(1500);
  assert.equal(Math.round(t.T.startsIn(m)), 2500);
  assert.ok(Math.abs(t.T.inviteLeft(m) - 298.5) < 0.01);
  t.tick(10000);
  assert.ok(t.T.startsIn(m) < 0);
  assert.equal(t.T.startsIn({ startsInMs: null }), null);
  assert.equal(t.T.inviteLeft({ expiresIn: null }), null);
  assert.equal(t.T.clock(75), "1:15");
  assert.equal(t.T.clock(5), "0:05");
});
