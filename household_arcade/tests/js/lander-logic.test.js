"use strict";
// Lander (wave 5): the rules (thrust, turning, fuel, landing and crashing), the level list, saving, the honest-score
// limits, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const L = loadLogic("lander-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const EXTRA = ["lander-logic.js", "lander.js"];
const LIMIT = { perSecond: 350, base: 1000, max: 5000000 };
const FLAT = [60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60];

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...L.step(s)); } return all; }
function level(extra) { return Object.assign({ name: "Test", heights: FLAT.slice(), pads: "....11......", start: 100, drift: 0, gravity: 0.02, wind: 0, fuel: 600 }, extra || {}); }
// A careful pilot: across above the highest ground, then straight down onto the best pad near it.
function pilot(s) {
  if (s.phase !== "fly") { L.press(s, "left", false); L.press(s, "right", false); L.press(s, "up", false); return; }
  const l = s.land, pads = L.pads(l);
  let best = null, bd = 1e9;
  for (const p of pads) { const c = (p.from + p.to) / 2, d = Math.abs(c - s.x) - p.mult * 8; if (d < bd) { bd = d; best = p; } }
  const tx = (best.from + best.to) / 2, dx = tx - s.x, feet = s.y + L.FOOT, h = best.y - feet;
  let hi = 1e9;
  for (let x = Math.min(s.x, tx) - 12; x <= Math.max(s.x, tx) + 12; x += 2) hi = Math.min(hi, L.groundAt(l, Math.max(0, Math.min(240, x))));
  const over = Math.abs(dx) <= Math.max(1.5, (best.to - best.from) / 2 - 6.3), cruise = hi - 28;
  const wantVx = over ? 0 : Math.sign(dx) * Math.max(0.18, Math.min(0.9, Math.abs(dx) / 40));
  const windA = -l.wind / L.THRUST * 57.3;
  let wantA = Math.max(-40, Math.min(40, (wantVx - s.vx) * 90 + windA));
  if (over && h < 45) wantA = Math.max(-6, Math.min(6, -s.vx * 40 + windA));
  const wantVy = over ? Math.min(1.5, 0.35 + h / 70) : feet > cruise ? -0.4 : Math.min(1.0, (cruise - feet) / 40);
  L.press(s, "left", s.a > wantA + 1); L.press(s, "right", s.a < wantA - 1);
  L.press(s, "up", s.vy > wantVy || (Math.abs(s.vx - wantVx) > 0.12 && s.vy > wantVy - 0.6));
}

test("the start: the level's place, drift and fuel; three landers", () => {
  const s = L.create({ mode: "levels", seed: 1 });
  const l = L.LEVELS[0];
  assert.deepEqual([s.x, s.y, s.vx, s.vy, s.a, s.fuel, s.lives], [l.start, L.START_Y, l.drift, 0, 0, l.fuel, 3]);
  const c = L.create({ seed: 1 });
  assert.equal(L.levelProblem(c.land), null, "a made-up level is a good level");
  assert.equal(c.land.heights.length, 13);
});

test("gravity pulls, the engine pushes the way the lander points and burns fuel, turning stops at 90°", () => {
  const s = L.create({ mode: "levels", seed: 2, levels: [level()] });
  run(s, 20);
  assert.ok(s.vy > 0.3 && s.vy < 0.5, `${s.vy}`);
  L.press(s, "up", true);
  const f0 = s.fuel, v0 = s.vy;
  run(s, 20);
  assert.equal(s.fuel, f0 - 20);
  assert.ok(s.vy < v0, "slows the fall");
  L.press(s, "up", false); L.press(s, "right", true);
  run(s, 50);
  assert.equal(s.a, 90, "at most 90°");
  L.press(s, "right", false); L.press(s, "up", true);
  const vx0 = s.vx;
  run(s, 10);
  assert.ok(s.vx > vx0 + 0.5, "pointing right pushes right");
  s.fuel = 1; const evs = run(s, 3);
  assert.ok(types(evs).includes("empty"));
  assert.equal(s.fuel, 0);
  assert.equal(s.thrust, false, "no fuel, no engine");
});

test("a soft, upright touchdown on a pad lands: multiplier × 100 + half the fuel left; then the next level", () => {
  const s = L.create({ mode: "levels", seed: 3, levels: [level(), level({ name: "Two" })] });
  s.x = 100; s.y = L.FLOOR - 60 - L.FOOT - 2; s.vy = 0.5; s.fuel = 300;
  const evs = run(s, 10);
  assert.ok(types(evs).includes("land"), JSON.stringify(types(evs)));
  assert.equal(s.score, 1 * L.PAD_POINTS + 150);
  assert.equal(s.stats.landings, 1);
  let next = [];
  for (let i = 0; i <= L.LAND_T && !types(next).includes("level"); i++) next = L.step(s);
  assert.ok(types(next).includes("level"));
  assert.equal(s.level, 2);
  assert.equal(s.y, L.START_Y);
});

test("too fast, tilted or off the pad crashes and costs a lander; the level starts again", () => {
  const crash = (set) => {
    const s = L.create({ mode: "levels", seed: 4, levels: [level()] });
    s.x = 100; s.y = L.FLOOR - 60 - L.FOOT - 2; s.vy = 0.5;
    Object.assign(s, set);
    const evs = run(s, 10);
    return types(evs).includes("crash") ? [s.stats.cause, s] : [null, s];
  };
  assert.equal(crash({ vy: 1.5 })[0], "fast");
  assert.equal(crash({ vx: 0.9 })[0], "fast");
  assert.equal(crash({ a: 20 })[0], "tilt");
  assert.equal(crash({ x: 30 })[0], "ground");
  const [, s] = crash({ vy: 2 });
  assert.equal(s.lives, 2);
  const evs = run(s, L.CRASH_T + 1);
  assert.ok(types(evs).includes("retry"));
  assert.equal(s.level, 1);
  assert.equal(s.fuel, level().fuel, "fresh fuel");
  s.lives = 1; s.y = L.FLOOR - 70; s.vy = 3;
  const end = run(s, L.CRASH_T + 20);
  assert.ok(s.over);
  assert.ok(types(end).includes("gameover"));
});

test("the walls and the top of the screen stop the lander", () => {
  const s = L.create({ mode: "levels", seed: 5, levels: [level()] });
  s.vx = -5; run(s, 25);
  assert.equal(s.x, 8);
  s.vx = 5; run(s, 60);
  assert.equal(s.x, 232);
  s.vy = -5; run(s, 30);
  assert.ok(s.y >= L.TOP + 8);
});

test("pads: flat runs of the same multiplier; levels need a pad, flat pads and enough fuel", () => {
  assert.deepEqual(L.pads(level({ pads: "22..3......5" })).map((p) => [p.from, p.to, p.mult]), [[0, 40, 2], [80, 100, 3], [220, 240, 5]]);
  assert.equal(L.levelProblem(level({ pads: "............" })), "no pad");
  assert.equal(L.levelProblem(level({ heights: [0, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], pads: "1..........." })), "a pad must be flat");
  assert.equal(L.levelProblem(level({ gravity: 0.04, fuel: 200 })), "not enough fuel");
  assert.ok(L.fuelNeeded(0.04) > L.fuelNeeded(0.015));
  assert.equal(L.padUnder(level(), 100).mult, 1);
  assert.equal(L.padUnder(level(), 82), null, "a foot off the pad");
});

test("Levels: built-in or the session's list; bad levels are skipped; the last landing wins; the pilot lands them all", () => {
  assert.equal(L.usableLevels(undefined), L.LEVELS);
  const bad = [level({ heights: FLAT.slice(0, 12) }), level({ heights: FLAT.map((h, i) => (i === 3 ? 151 : h)) }), level({ pads: "....11....." }),
    level({ pads: "....61......" }), level({ start: 5 }), level({ drift: 1.5 }), level({ gravity: 0.05 }), level({ wind: 0.02 }),
    level({ fuel: 100 }), level({ fuel: 1001 }), level({ pads: "............" }), level({ name: "" }), null, 3];
  assert.equal(L.usableLevels(bad), L.LEVELS);
  assert.deepEqual(L.usableLevels(bad.concat([level({ name: "Fine" })])).map((l) => l.name), ["Fine"]);
  for (const l of L.LEVELS) { assert.equal(L.levelProblem(l), null, l.name); assert.equal(L.usableLevels([l]).length, 1, l.name); }
  assert.equal(L.create({ mode: "classic", levels: [level()] }).list, null);
  const s = L.create({ mode: "levels", seed: 1 });
  const evs = run(s, 60 * 400, pilot);
  assert.ok(s.won, "every built-in level can be landed");
  assert.equal(s.stats.crashes, 0);
  assert.ok(types(evs).includes("win"));
  assert.equal(L.result(s).stats.levels, L.LEVELS.length);
  const data = JSON.parse(JSON.stringify(L.save(L.create({ mode: "levels", seed: 1, levels: [level()] }))));
  assert.ok(!("list" in data));
  assert.equal(L.restore(data, [level()]).list.length, 1);
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed, mode) => { const s = L.create({ seed, mode }); run(s, 60 * 90, pilot); return s; };
  assert.equal(JSON.stringify(play(9, "classic")), JSON.stringify(play(9, "classic")));
  assert.notEqual(JSON.stringify(play(9, "classic").land), JSON.stringify(play(10, "classic").land));
  const s = L.create({ seed: 12, mode: "easy" });
  run(s, 500, pilot);
  const copy = L.restore(JSON.parse(JSON.stringify(L.save(s))));
  for (let i = 0; i < 2400 && !s.over; i++) { pilot(s); pilot(copy); L.step(s); L.step(copy); }
  assert.equal(JSON.stringify(L.save(copy)), JSON.stringify(L.save(s)));
  assert.throws(() => L.restore({ land: 1 }), /can't be continued/);
  assert.throws(() => L.restore(null));
});

test("honest score: the quickest possible landings stay inside the limits", () => {
  let top = 0;
  // as fast as the rules allow: free fall onto a ×5 pad as high as a pad can be, slowed only at the very end
  const quick = [];
  for (let i = 0; i < 6; i++) {
    const h = [150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150];
    quick.push(level({ name: "Quick " + i, heights: h, pads: "555555555555", start: 120, gravity: 0.04, fuel: 1000 }));
  }
  for (const [mode, levels] of [["classic"], ["easy"], ["levels"], ["levels", quick]]) {
    const s = L.create({ seed: 1, mode, levels });
    while (!s.over && s.updates < 60 * 60 * 10) {
      if (levels === quick && s.phase === "fly") { L.press(s, "up", s.vy > L.SAFE_VY - 0.05 && s.y > 110); }
      else pilot(s);
      L.step(s);
      const sec = s.updates / 60;
      assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
      assert.ok(s.score <= LIMIT.max && s.level <= 100);
      if (sec > 5) top = Math.max(top, s.score / sec);
    }
  }
  console.log(`# lander: fastest rate seen ${top.toFixed(1)} points a second`);
});

test("fuzz: random keys never throw and keep the lander sound", () => {
  for (const mode of ["classic", "easy", "levels"]) {
    for (let seed = 1; seed <= 12; seed++) {
      const s = L.create({ seed, mode });
      let x = seed * 15485863, last = 0;
      for (let i = 0; i < 5000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 5 === 0) L.press(s, ["up", "left", "right", "fire", "down"][x % 5], x % 3 !== 0);
        L.step(s);
        assert.ok(Number.isFinite(s.x) && Number.isFinite(s.y) && s.score >= last);
        last = s.score;
        assert.ok(s.x >= 8 && s.x <= 232 && s.y >= L.TOP + 8 && s.y <= L.FLOOR, `${s.x},${s.y}`);
        assert.ok(s.fuel >= 0 && Math.abs(s.a) <= 90 && s.level >= 1 && s.level <= 100);
        if (s.phase === "fly") assert.equal(L.levelProblem(s.land), null);
      }
    }
  }
});

test("lander renders and plays in every look", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "levels"]) {
      const sb = makeSandbox({ extra: EXTRA });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("lander");
      assert.equal(def.name, "Lander");
      assert.equal(def.controls, "buttons");
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      for (let i = 0; i < 700; i++) {
        if (i % 60 === 0) inst.input("left", true);
        if (i % 60 === 10) inst.input("left", false);
        if (i % 20 === 0) inst.pointer("down", 100, 200);
        if (i % 20 === 12) inst.pointer("up", 100, 200);
        if (i === 400) inst.logic.fuel = 0;
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}`);
      if (inst.state === "over") assert.equal(ends.length, 1); else assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
