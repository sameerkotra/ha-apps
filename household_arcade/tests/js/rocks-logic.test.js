"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const R = loadLogic("rocks-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...R.step(s)); } return all; }
function wave(extra) { return Object.assign({ name: "Test", big: 2, medium: 0, speed: 0.5, saucer: 0 }, extra || {}); }
function rock(s, size, x, y, vx = 0, vy = 0) { s.rocks.push({ x, y, vx, vy, size, shape: [1, 0.9, 1, 0.8, 1, 0.9, 1, 0.85], rot: 0, spin: 0 }); }

// A bot as quick as the rules allow: turns toward the nearest rock (or the saucer) where it will be, and fires
// whenever it's nearly lined up.
function bot(s) {
  const p = s.ship;
  if (p.dead) return;
  let best = null, bd = 1e9;
  for (const r of s.rocks) { const d = R.dist(p.x, p.y, r.x, r.y); if (d < bd) { bd = d; best = r; } }
  if (s.saucer) { best = s.saucer; bd = R.dist(p.x, p.y, best.x, best.y); }
  if (!best) { R.press(s, "left", false); R.press(s, "right", false); R.press(s, "fire", false); return; }
  const t = bd / 4.2, d = R.delta(p.x, p.y, best.x + (best.vx || 0) * t, best.y + (best.vy || 0) * t);
  let diff = Math.atan2(d[1], d[0]) - p.a;
  while (diff > Math.PI) diff -= 2 * Math.PI;
  while (diff < -Math.PI) diff += 2 * Math.PI;
  R.press(s, "left", diff < -0.04); R.press(s, "right", diff > 0.04);
  if (Math.abs(diff) < 0.25) R.press(s, "fire", true); else R.press(s, "fire", false);
}

test("the start: a ship in the middle, the wave's rocks away from it, lives by mode", () => {
  const s = R.create({ seed: 1 });
  assert.equal(s.mode, "classic");
  assert.equal(s.lives, 3);
  assert.equal(R.create({ mode: "calm" }).lives, 5);
  assert.deepEqual([s.ship.x, s.ship.y], [R.CX, R.CY]);
  assert.equal(s.rocks.length, R.waveSpec(s, 1).big + R.waveSpec(s, 1).medium);
  for (const r of s.rocks) assert.ok(R.dist(r.x, r.y, s.ship.x, s.ship.y) >= 90);
  // rocks are irregular: their outlines differ
  assert.ok(new Set(s.rocks.map((r) => r.shape.join())).size > 1);
  assert.ok(s.rocks.every((r) => r.shape.length >= 8 && r.shape.every((f) => f >= 0.72 && f <= 1)));
  // Calm: slower rocks
  const calm = R.create({ mode: "calm" });
  assert.ok(R.waveSpec(calm, 5).speed < R.waveSpec(s, 5).speed);
  assert.equal(R.waveSpec(s, 2).saucer, 0, "no saucer before wave 3");
  assert.ok(R.waveSpec(s, 3).saucer > 0);
});

test("turning, thrust with slow drag, and the screen wraps around", () => {
  const s = R.create({ seed: 2 });
  s.rocks = [];
  rock(s, 3, 20, 60);
  const a = s.ship.a;
  R.press(s, "left", true); R.step(s); R.press(s, "left", false);
  assert.ok(s.ship.a < a);
  R.press(s, "right", true); run(s, 2); R.press(s, "right", false);
  assert.ok(s.ship.a > a);
  s.ship.a = 0;
  R.press(s, "up", true); run(s, 20); R.press(s, "up", false);
  assert.ok(s.ship.vx > 1);
  const v = s.ship.vx;
  run(s, 60);
  assert.ok(s.ship.vx < v && s.ship.vx > v * 0.4, "drifts on, slowing gently");
  R.press(s, "up", true); run(s, 200); R.press(s, "up", false);
  assert.ok(Math.hypot(s.ship.vx, s.ship.vy) <= 3.2 + 1e-9, "a top speed");
  s.ship.x = R.W - 0.5; s.ship.vx = 2; s.ship.vy = 0; R.step(s);
  assert.ok(s.ship.x < 5, "off the right edge, in on the left");
  s.ship.y = R.BOTTOM - 0.5; s.ship.vy = 2; s.ship.vx = 0; R.step(s);
  assert.ok(s.ship.y < R.TOP + 5);
});

test("shots: at most four in the air, one every 10 updates, short-lived", () => {
  const s = R.create({ seed: 3 });
  s.rocks = []; rock(s, 3, 20, 60);
  R.press(s, "fire", true);
  let evs = R.step(s);
  assert.deepEqual(types(evs), ["fire"]);
  evs = run(s, 9);
  assert.ok(!types(evs).includes("fire"), "holding waits");
  evs = run(s, 100);
  const fires = types(evs).filter((t) => t === "fire").length;
  assert.ok(fires >= 4 && fires <= 7, `fires ${fires}`);
  assert.ok(s.shots.length <= R.MAX_SHOTS);
  R.press(s, "fire", false);
  run(s, 60);
  assert.equal(s.shots.length, 0, "shots fade");
  // taps: no faster than one every 10 updates, and never more than four at once
  const t = R.create({ seed: 3 });
  t.rocks = []; rock(t, 3, 20, 60);
  let n = 0;
  for (let i = 0; i < 60; i++) { R.press(t, "fire", true); R.press(t, "fire", false); n += types(R.step(t)).filter((x) => x === "fire").length; assert.ok(t.shots.length <= 4); }
  assert.ok(n <= 6);
});

test("a big rock breaks into two medium, a medium into two small, a small one is gone; 20 / 50 / 100", () => {
  const s = R.create({ seed: 4 });
  s.rocks = [];
  rock(s, 3, R.CX, R.CY - 60);
  rock(s, 1, 10, 50);                      // keeps the wave going
  s.ship.a = -Math.PI / 2;
  R.press(s, "fire", true); R.step(s); R.press(s, "fire", false);
  const evs = run(s, 20);
  assert.ok(types(evs).includes("break"));
  assert.equal(s.score, 20);
  assert.equal(s.rocks.filter((r) => r.size === 2).length, 2);
  const m = s.rocks.findIndex((r) => r.size === 2);
  R.breakRock(s, m, [], true);
  assert.equal(s.score, 70);
  assert.equal(s.rocks.filter((r) => r.size === 1).length, 3);
  const k = s.rocks.findIndex((r) => r.size === 1);
  R.breakRock(s, k, [], true);
  assert.equal(s.score, 170);
  assert.equal(s.stats.rocks, 3);
});

test("clearing the wave: a pause, then the next wave with more rocks", () => {
  const s = R.create({ seed: 5 });
  s.rocks = []; rock(s, 1, 30, 60);
  R.breakRock(s, 0, [], true);
  let evs = R.step(s);
  assert.ok(types(evs).includes("clear"));
  evs = run(s, R.WAVE_PAUSE);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.wave, 2);
  assert.equal(s.level, 2);
  assert.equal(s.rocks.length, R.waveSpec(s, 2).big + R.waveSpec(s, 2).medium);
  assert.ok(R.waveSpec(s, 2).big > R.waveSpec(s, 1).big);
  // the level stays at 100 at most in the endless modes
  s.wave = 150; s.rocks = []; run(s, R.WAVE_PAUSE + 2);
  assert.equal(s.level, 100);
});

test("a rock costs a life; a safe time after the new ship; no lives left ends the game", () => {
  const s = R.create({ seed: 6 });
  s.rocks = []; rock(s, 3, R.CX + 10, R.CY); rock(s, 1, 10, 50);
  let evs = R.step(s);
  assert.ok(types(evs).includes("lifeLost"));
  assert.equal(s.lives, 2);
  assert.equal(s.score, 0, "bumping into a rock scores nothing");
  evs = run(s, R.RESPAWN);
  assert.ok(!s.ship.dead && s.ship.safe > 0);
  s.rocks = []; rock(s, 3, R.CX, R.CY); rock(s, 1, 10, 50);
  evs = run(s, 30);
  assert.ok(!types(evs).includes("lifeLost"), "safe");
  s.ship.safe = 0; s.lives = 1;
  s.rocks = []; rock(s, 3, s.ship.x, s.ship.y); rock(s, 1, 10, 50);
  evs = run(s, 2);
  assert.ok(s.over);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(R.result(s).stats.cause, "lives");
});

test("the saucer: from wave 3 sometimes, crosses, shoots at the ship; hitting it scores 300", () => {
  const s = R.create({ seed: 7 });
  s.saucerChance = 100;
  s.rocks = []; rock(s, 1, 10, 50);
  const evs = run(s, R.SAUCER_CHECK + 1, (st) => { st.ship.safe = 999; });
  assert.ok(types(evs).includes("saucer"));
  assert.ok(s.saucer);
  run(s, 200, (st) => { st.ship.safe = 999; });
  assert.ok(s.sshots.length > 0 || s.saucer === null, "it shoots");
  // a saucer's shot costs a life
  const t = R.create({ seed: 7 });
  t.rocks = []; rock(t, 1, 10, 50); t.ship.safe = 0;
  t.sshots.push({ x: t.ship.x - 10, y: t.ship.y, vx: 2.4, vy: 0, life: 50 });
  assert.ok(types(run(t, 10)).includes("lifeLost"));
  // shooting it
  const u = R.create({ seed: 7 });
  u.rocks = []; rock(u, 1, 10, 50);
  u.saucer = { x: R.CX, y: R.CY - 50, vx: 0, phase: 0, cool: 999 };
  u.ship.a = -Math.PI / 2;
  R.press(u, "fire", true); R.step(u); R.press(u, "fire", false);
  const e2 = run(u, 20);
  assert.ok(types(e2).includes("saucerHit"));
  assert.equal(u.score, R.SAUCER_POINTS);
  // never more often than one check every 10 s
  const v = R.create({ seed: 8 });
  v.saucerChance = 100;
  let count = 0;
  for (let i = 0; i < 60 * 60 && !v.over; i++) { v.ship.safe = 999; if (v.rocks.length < 2) rock(v, 1, 10, 50); count += types(R.step(v)).filter((x) => x === "saucer").length; }
  assert.ok(count <= 6, `saucers ${count}`);
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed, mode) => { const s = R.create({ seed, mode }); run(s, 60 * 90, bot); return s; };
  const a = play(11, "classic"), b = play(11, "classic");
  assert.equal(JSON.stringify(R.result(a)) + a.updates, JSON.stringify(R.result(b)) + b.updates);
  assert.ok(a.stats.rocks > 20, `rocks ${a.stats.rocks}`);
  const s = R.create({ seed: 12, mode: "calm" });
  run(s, 60 * 20, bot);
  const copy = R.restore(JSON.parse(JSON.stringify(R.save(s))));
  for (let i = 0; i < 60 * 30 && !s.over; i++) { bot(s); bot(copy); R.step(s); R.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(JSON.stringify(R.save(copy)), JSON.stringify(R.save(s)));
  assert.throws(() => R.restore({ rocks: 3 }), /can't be continued/);
  assert.throws(() => R.restore(null));
});

test("Waves: built-in or the session's list; bad waves are skipped; the last wave wins; save keeps the list", () => {
  assert.equal(R.usableLevels(undefined), R.LEVELS);
  const bad = [wave({ big: 1 }), wave({ big: 11 }), wave({ medium: 7 }), wave({ speed: 1.7 }), wave({ speed: 0.3 }),
    wave({ saucer: 61 }), wave({ big: 2.5 }), wave({ big: 10, medium: 5 }), wave({ speed: 1.3, big: 8, medium: 3 }),
    wave({ name: 3 }), null, "x"];
  assert.equal(R.usableLevels(bad), R.LEVELS);
  assert.deepEqual(R.usableLevels(bad.concat([wave({ name: "Fine" })])).map((w) => w.name), ["Fine"]);
  for (const w of R.LEVELS) assert.equal(R.usableLevels([w]).length, 1, w.name);
  const two = [wave({ name: "A" }), wave({ name: "B", big: 3 })];
  const s = R.create({ mode: "waves", seed: 3, levels: two });
  assert.equal(s.waves.length, 2);
  assert.equal(s.rocks.length, 2);
  assert.equal(R.create({ mode: "classic", levels: two }).waves, null, "only Waves plays the list");
  const all = [];
  while (!s.over && s.wave === 1) { for (let i = s.rocks.length - 1; i >= 0; i--) R.breakRock(s, i, all, true); all.push(...R.step(s)); }
  assert.ok(types(all).includes("level"));
  assert.equal(s.wave, 2);
  assert.equal(s.name, "B");
  assert.equal(s.rocks.length, 3);
  // saved mid-wave, continued with the same list
  const data = JSON.parse(JSON.stringify(R.save(s)));
  assert.ok(!("waves" in data));
  const back = R.restore(data, two);
  assert.equal(back.waves.length, 2);
  while (!s.over) { for (let i = s.rocks.length - 1; i >= 0; i--) R.breakRock(s, i, all, true); all.push(...R.step(s)); }
  assert.ok(s.won);
  assert.ok(types(all).includes("win"));
  assert.equal(R.result(s).stats.cause, "won");
  assert.equal(R.result(s).stats.won, true);
  assert.equal(R.result(s).stats.waves, 2);
  assert.equal(s.level, 2);
});

test("honest score: the quick bot stays inside the limits in every mode, also with the hardest waves", () => {
  const PER_SECOND = 630, BASE = 400, MAX = 10000000;
  const hardest = [];
  for (let i = 0; i < 6; i++) hardest.push(wave({ name: "Hard " + "ABCDEF"[i], big: 9, medium: 6, speed: 1.2, saucer: 60 }));
  hardest.push(wave({ name: "Fast", big: 9, medium: 0, speed: 1.6, saucer: 60 }));
  let fastest = 0;
  for (const [mode, levels] of [["classic"], ["calm"], ["waves"], ["waves", hardest]]) {
    for (const seed of [1, 2, 3]) {
      const s = R.create({ seed, mode, levels });
      while (!s.over && s.updates < 60 * 60 * 10) {
        bot(s);
        if (s.ship.safe < 5) s.ship.safe = 5;     // the bot never loses, so it plays on as long as it can
        R.step(s);
        const sec = s.updates / 60;
        assert.ok(s.score <= sec * PER_SECOND + BASE, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
        assert.ok(s.score <= MAX && s.level <= 100);
        if (sec > 10) fastest = Math.max(fastest, s.score / sec);
      }
    }
  }
  console.log(`# rocks: quick bot's fastest rate ${fastest.toFixed(1)} points a second`);
});

test("rocks renders and plays in every look, with keys and taps, switching looks mid-game", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "waves"]) {
      const sb = makeSandbox({ extra: ["rocks-logic.js", "rocks.js"] });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("rocks");
      assert.equal(def.name, "Rocks");
      assert.equal(def.controls, "buttons");
      assert.equal(def.stateVersion, 1);
      assert.equal(def.buttons.length, 4);
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      const s = inst.logic;
      for (let i = 0; i < 500; i++) {
        if (i % 40 === 0) inst.input(["left", "up", "right"][(i / 40) % 3], true);
        if (i % 40 === 30) inst.input(["left", "up", "right"][((i - 30) / 40) % 3], false);
        if (i % 7 === 0) inst.input("fire", i % 14 === 0);
        if (i % 50 === 25) { inst.pointer("down", 200, 300); inst.pointer("up", 200, 300); }
        if (i === 100) { s.saucer = { x: 5, y: 60, vx: 1.1, phase: 0, cool: 10 }; s.rocks.push({ x: 2, y: 40, vx: 0, vy: 0, size: 3, shape: [1, 0.8, 0.9, 1, 0.8, 0.9, 1, 0.9], rot: 0, spin: 0.01 }); }
        if (i === 300) { s.pause = 20; }
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      if (inst.state === "over") assert.equal(ends.length, 1);
      else { const saved = inst.save(); assert.ok(saved && saved.state); }
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
  // a saved Waves game continues with the session's list
  const sb = makeSandbox({ extra: ["rocks-logic.js", "rocks.js"] });
  const def = sb.win.ArcadeGames.get("rocks");
  const list = [wave({ name: "Only" })];
  const first = def.create(sb.canvas(390, 487), { mode: "waves", seed: 3, levels: list });
  first.start(); sb.frames(100);
  const saved = first.save();
  first.destroy();
  const ends = [];
  const inst = def.create(sb.canvas(390, 487), { mode: "waves", seed: 3, levels: list, restore: saved, onEnd: (r) => ends.push(r) });
  inst.start();
  assert.equal(inst.logic.waves.length, 1);
  inst.logic.rocks = [];
  sb.frames(5);
  assert.equal(ends.length, 1);
  assert.equal(ends[0].stats.won, true);
});
