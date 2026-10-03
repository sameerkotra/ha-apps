"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const H = loadLogic("hop-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const PER_SECOND = 250, BASE = 1000, MAX_SCORE = 850_000;
function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) all.push(...(each(s) || [])); all.push(...H.step(s)); } return all; }
function settle(s) { while (!s.over && (s.air > 0 || s.phase !== "play")) H.step(s); }
function homeX(i) { return H.OX + H.HOMES[i] * H.CS + H.CS / 2; }
function calm(s) { for (const ln of s.lanes) ln.kind = "grass"; }     // test helper: no traffic, no water
const GRASS = { kind: "grass", pattern: ".............", speed: 0.5, dir: "left" };
const ROAD = { kind: "road", pattern: "c............", speed: 0.3, dir: "left" };
const RIVER = { kind: "river", pattern: "lllll..lllll..", speed: 0.3, dir: "right" };

// A frame-perfect hopper on a calm board: hops up the moment it may, steering to the nearest empty home.
function rusher(s) {
  if (s.phase !== "play" || s.air > 0) return [];
  if (s.row === s.n) {
    let best = -1, bd = 1e9;
    s.homes.forEach((f, i) => { if (!f && Math.abs(homeX(i) - s.x) < bd) { bd = Math.abs(homeX(i) - s.x); best = i; } });
    if (bd > 8) return H.press(s, homeX(best) < s.x ? "left" : "right", true);
  }
  return H.press(s, "up", true);
}

test("the board: the bank, the level's lanes and the homes; 3 lives; a timer", () => {
  const s = H.create({ seed: 1 });
  assert.equal(s.mode, "classic");
  assert.equal(s.lives, 3);
  assert.equal(s.n, H.LEVELS[0].lanes.length);
  assert.equal(s.row, 0);
  assert.equal(s.x, 120);
  assert.equal(s.time, 60 * 60);
  assert.equal(H.rowY(s, 0), 276);
  assert.ok(H.rowY(s, s.n + 1) >= 52);
  assert.ok(H.rowY(H.create({ mode: "levels", levels: [H.LEVELS[7]] }), 12) >= 52, "11 lanes still fit");
  assert.equal(H.create({ mode: "nope" }).mode, "classic");
  for (const lv of H.LEVELS) assert.equal(H.levelProblem(lv), "", lv.name);
});

test("hops: one square a press, 8 updates each, one press waits during a hop; edges and the bank stop it", () => {
  const s = H.create({ seed: 2 });
  calm(s);
  assert.deepEqual(types(H.press(s, "down", true)), [], "nothing below the bank");
  assert.deepEqual(types(H.press(s, "up", true)), ["hop"]);
  assert.equal(s.row, 1);
  assert.equal(s.air, H.HOP_T);
  H.press(s, "left", true);
  assert.equal(s.x, 120, "waits");
  run(s, H.HOP_T);
  assert.equal(s.x, 102, "the waiting press hops as soon as it lands");
  settle(s);
  for (let i = 0; i < 10; i++) { H.press(s, "left", true); settle(s); }
  assert.equal(s.x, H.OX + 9);
  H.press(s, "right", true); settle(s);
  assert.equal(s.x, H.OX + 27);
  assert.equal(s.stats.hops, 8);
  H.press(s, "up", false);
  assert.equal(s.stats.hops, 8, "key-up does nothing");
  H.press(s, "fire", true);
  assert.equal(s.row, 2, "fire hops forward");
});

test("forward hops score 10 for each new row; going back and forth doesn't", () => {
  const s = H.create({ seed: 3 });
  calm(s);
  H.press(s, "up", true); settle(s);
  assert.equal(s.score, 10);
  H.press(s, "down", true); settle(s);
  H.press(s, "up", true); settle(s);
  assert.equal(s.score, 10);
  H.press(s, "up", true); settle(s);
  assert.equal(s.score, 20);
});

test("traffic squashes, water swallows, a log carries (and off the edge is lost), time runs out", () => {
  // squashed
  const a = H.create({ mode: "levels", seed: 4, levels: [{ name: "T", time: 60, lanes: [ROAD, GRASS, GRASS, GRASS, GRASS, RIVER] }] });
  a.lanes[0].off = 0; a.lanes[0].v = 0;       // the car sits in column 0
  a.x = H.OX + 9;
  H.press(a, "up", true);
  const evs = run(a, 20);
  assert.ok(evs.some((e) => e.type === "splat" && e.cause === "car"));
  assert.equal(a.lives, 2);
  assert.equal(a.phase, "dead");
  settle(a);
  assert.equal(a.row, 0); assert.equal(a.x, 120);
  // water
  const b = H.create({ mode: "levels", seed: 4, levels: [{ name: "W", time: 60, lanes: [GRASS, GRASS, GRASS, GRASS, ROAD, RIVER] }] });
  b.lanes[5].off = 0; b.lanes[5].v = 0;        // gap at column 6
  b.row = 5; b.x = 120;
  H.press(b, "up", true);
  assert.ok(run(b, 20).some((e) => e.type === "splat" && e.cause === "water"));
  // carried by a log, then off the edge
  const c = H.create({ mode: "levels", seed: 4, levels: [{ name: "L", time: 60, lanes: [GRASS, GRASS, GRASS, GRASS, ROAD, RIVER] }] });
  c.lanes[5].off = 0; c.lanes[5].v = 1;
  c.row = 5; c.x = H.OX + 9 + 18 * 8;         // on the second log
  H.press(c, "up", true); settle(c);
  assert.equal(c.row, 6);
  const x0 = c.x;
  H.step(c);
  assert.equal(c.x, x0 + 1, "carried");
  const e2 = run(c, 400);
  assert.ok(e2.some((e) => e.type === "splat" && (e.cause === "swept" || e.cause === "water")));
  // time
  const d = H.create({ seed: 5 });
  calm(d);
  const e3 = run(d, d.time + 2);
  assert.ok(e3.some((e) => e.type === "splat" && e.cause === "time"));
  // the last life ends the game
  d.lives = 1; settle(d);
  const e4 = run(d, d.time + 2);
  assert.ok(d.over);
  assert.ok(types(e4).includes("gameover"));
  assert.equal(H.result(d).stats.cause, "time");
});

test("homes: only an empty one near its middle; 50 + seconds left; all five is the next level (+250)", () => {
  const s = H.create({ seed: 6 });
  calm(s);
  s.row = s.n; s.x = H.OX + 9;                 // under the hedge
  assert.deepEqual(types(H.press(s, "up", true)), ["bump"]);
  assert.equal(s.row, s.n);
  s.x = homeX(0) + 5;
  H.press(s, "up", true);
  const before = s.score, secs = Math.floor((s.timer - H.HOP_T) / 60);
  const evs = run(s, H.HOP_T);
  assert.ok(types(evs).includes("home"));
  assert.equal(s.score - before, 10 + 50 + secs);
  assert.equal(s.homes[0], true);
  settle(s);
  assert.equal(s.row, 0, "a new hopper at the bank");
  calm(s);
  s.row = s.n; s.x = homeX(0);
  assert.deepEqual(types(H.press(s, "up", true)), ["bump"], "a filled home is closed");
  let all = [];
  for (let i = 1; i < 5; i++) {
    s.row = s.n; s.x = homeX(i); H.press(s, "up", true);
    all = all.concat(run(s, H.HOP_T));
    if (i < 4) { settle(s); calm(s); }
  }
  assert.ok(types(all).includes("level"));
  assert.equal(s.level, 2);
  assert.equal(s.stats.levels, 1);
  settle(s);
  assert.equal(s.homes.filter(Boolean).length, 0);
  assert.equal(s.n, H.LEVELS[1].lanes.length);
});

test("classic levels repeat quicker; Easy is slower with more time", () => {
  const s = H.create({ seed: 7 });
  const d1 = H.levelDef(s, 1), d9 = H.levelDef(s, 9);
  assert.equal(d9.def, H.LEVELS[0]);
  assert.ok(d9.speed > d1.speed);
  assert.ok(H.levelDef(s, 100).speed <= 1.5);
  const e = H.create({ mode: "easy", seed: 7 });
  assert.ok(H.levelDef(e, 1).speed < d1.speed);
  assert.ok(e.time > s.time);
  assert.ok(Math.abs(e.lanes[0].v) < Math.abs(s.lanes[0].v));
});

test("Levels: built-in or the session's list; a bad list falls back; the last level wins", () => {
  assert.equal(H.usableLevels(undefined), H.LEVELS);
  const bad = [
    { name: "Short", time: 60, lanes: [ROAD, ROAD] },
    { name: "Blocked", time: 60, lanes: [ROAD, { kind: "road", pattern: "cc.cc.cc.cc.cc", speed: 1, dir: "left" }, GRASS, GRASS, GRASS, GRASS] },
    { name: "Gappy", time: 60, lanes: [ROAD, { kind: "river", pattern: "ll.....ll....", speed: 1, dir: "left" }, GRASS, GRASS, GRASS, GRASS] },
    { name: "In step", time: 60, lanes: [ROAD, RIVER, Object.assign({}, RIVER, { pattern: "ll..ll..ll..ll" }), GRASS, GRASS, GRASS] },
    { name: "Fast", time: 60, lanes: [ROAD, Object.assign({}, RIVER, { speed: 1.5 }), GRASS, GRASS, GRASS, GRASS] },
    { name: "Slowpoke", time: 10, lanes: [ROAD, RIVER, GRASS, GRASS, GRASS, GRASS] },
    { name: "Calm", time: 60, lanes: [ROAD, GRASS, GRASS, GRASS, GRASS, GRASS] },
  ];
  for (const b of bad) assert.notEqual(H.levelProblem(b), "", b.name);
  assert.equal(H.usableLevels(bad), H.LEVELS);
  const two = [
    { name: "A", time: 60, lanes: [ROAD, GRASS, GRASS, GRASS, GRASS, RIVER] },
    { name: "B", time: 30, lanes: [ROAD, ROAD, GRASS, RIVER, GRASS, GRASS, GRASS] },
  ];
  assert.equal(H.usableLevels([two[0], bad[0]]).length, 1);
  const s = H.create({ mode: "levels", seed: 3, levels: two });
  assert.equal(s.list.length, 2);
  assert.equal(H.create({ mode: "levels" }).list, H.LEVELS);
  const evs = [];
  for (let i = 0; i < 60 * 120 && !s.over; i++) { calm(s); evs.push(...rusher(s), ...H.step(s)); }
  assert.ok(types(evs).includes("level"));
  assert.ok(types(evs).includes("win"));
  assert.ok(s.won && s.over);
  assert.equal(s.level, 2);
  assert.equal(s.n, 7);
  const r = H.result(s);
  assert.equal(r.stats.won, true); assert.equal(r.stats.cause, "won"); assert.equal(r.stats.levels, 2); assert.equal(r.stats.homes, 10);
  // saved mid-level, continued with the same list
  const t = H.create({ mode: "levels", seed: 3, levels: two });
  for (let i = 0; i < 300; i++) { calm(t); rusher(t); H.step(t); }
  assert.ok(!("list" in H.save(t)));
  const back = H.restore(JSON.parse(JSON.stringify(H.save(t))), two);
  assert.equal(back.list.length, 2);
  for (let i = 0; i < 60 * 120 && !back.over; i++) { calm(back); rusher(back); H.step(back); }
  assert.ok(back.won);
  // classic wins after level 100
  const c = H.create({ seed: 4 });
  c.level = H.MAX_LEVELS;
  for (let i = 0; i < 60 * 120 && !c.over; i++) { calm(c); rusher(c); H.step(c); }
  assert.ok(c.won);
  assert.equal(c.level, 100);
});

test("deterministic for a seed; save and restore play on identically", () => {
  // a wandering hopper that dies now and then
  const wander = (s) => { if (s.updates % 11 === 0) H.press(s, ["up", "left", "up", "right", "down"][Math.floor(s.updates / 11) % 5], true); };
  const go = (seed) => { const s = H.create({ seed }); run(s, 60 * 60, wander); return JSON.stringify(H.result(s)) + s.updates + JSON.stringify(s.lanes); };
  assert.equal(go(21), go(21));
  const s = H.create({ seed: 22 });
  run(s, 60 * 3, wander);
  assert.ok(!s.over);
  const copy = H.restore(JSON.parse(JSON.stringify(H.save(s))));
  for (let i = 0; i < 60 * 40 && !s.over; i++) { wander(s); wander(copy); H.step(s); H.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.x, s.x);
  assert.equal(copy.lives, s.lives);
  assert.deepEqual(copy.lanes, s.lanes);
  assert.throws(() => H.restore({ lanes: 3 }), /can't be continued/);
  assert.throws(() => H.restore(undefined), /can't be continued/);
});

test("honest-score limits hold for a frame-perfect hopper on calm boards, in every mode and the shortest, longest-timed levels", () => {
  const quick = [{ name: "Quick", time: 90, lanes: [ROAD, GRASS, GRASS, GRASS, GRASS, RIVER] }];
  const tall = [{ name: "Tall", time: 90, lanes: [ROAD, GRASS, GRASS, GRASS, GRASS, GRASS, GRASS, GRASS, GRASS, GRASS, RIVER] }];
  let fastest = 0;
  for (const [mode, levels] of [["classic"], ["easy"], ["levels", quick], ["levels", tall], ["levels"]]) {
    const s = H.create({ mode, seed: 9, levels });
    while (!s.over && s.updates < 60 * 60 * 30) {
      calm(s);
      rusher(s);
      H.step(s);
      assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `${mode}: ${s.score} after ${s.updates / 60}s`);
      assert.ok(s.score <= MAX_SCORE);
      assert.ok(s.level <= Math.max(100, s.list ? s.list.length : 0));
      if (s.updates > 600) fastest = Math.max(fastest, s.score / (s.updates / 60));
    }
    assert.ok(s.won, `${mode}: the game ends won (${s.level})`);
  }
  if (process.env.SHOW_RATE) console.log("fastest", fastest);
  assert.ok(fastest > 100, `fastest ${fastest.toFixed(1)} points a second`);
});

test("renders in every look while it plays (hops, deaths, homes), switching looks, reduce motion and size", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "levels"]) {
      const sb = makeSandbox({ extra: ["hop-logic.js", "hop.js"] });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("hop");
      assert.equal(def.controls, "dpad");
      assert.equal(def.name, "Road Hop");
      assert.equal(def.stateVersion, 1);
      const inst = def.create(canvas, { mode, look, seed: 5, levels: mode === "levels" ? H.LEVELS.slice(5) : undefined });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      const s = inst.logic;
      for (let i = 0; i < 600; i++) {
        if (i % 10 === 0) inst.input(["up", "up", "left", "up", "right", "down", "fire"][(i / 10) % 7], true);
        if (i === 300) { s.row = s.n; s.x = homeX(2); s.air = 0; s.phase = "play"; inst.input("up", true); }
        inst.pointer("down", 100, 100);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(s.stats.hops > 0, "hopped");
      if (!s.over) assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
