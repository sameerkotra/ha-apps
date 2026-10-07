"use strict";
// Lights Out (wave 6): the rules, solvable boards and the exact fewest presses, Climb, the hint option, saving, a
// fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const L = loadLogic("lights-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

function types(evs) { return evs.map((e) => e.type); }
/** The fewest presses by trying every set of presses (small boards only). */
function bruteFewest(lights, n) {
  const N = n * n;
  let best = Infinity;
  for (let m = 0; m < 1 << N; m++) {
    const t = lights.slice();
    let cnt = 0;
    for (let i = 0; i < N; i++) if (m >> i & 1) { L.toggle(t, n, i); cnt++; }
    if (cnt < best && t.every((v) => v === 0)) best = cnt;
  }
  return best === Infinity ? null : best;
}
function solveAll(s) {
  let guard = 0;
  while (!s.over && guard++ < 5000) {
    if (s.phase === "play") { const sol = L.solve(s.lights, s.n); L.pressCell(s, sol[0]); }
    L.step(s);
  }
}

test("a press switches the light and its four neighbours", () => {
  const t = [0, 0, 0, 0, 0, 0, 0, 0, 0];
  L.toggle(t, 3, 4);
  assert.deepEqual(t, [0, 1, 0, 1, 1, 1, 0, 1, 0]);
  L.toggle(t, 3, 0);
  assert.deepEqual(t, [1, 0, 0, 0, 1, 1, 0, 1, 0]);
  assert.deepEqual(L.neighbours(5, 24).sort((a, b) => a - b), [19, 23, 24]);
});

test("solve(): the fewest presses, checked by trying every set on 3 × 3 and 4 × 4; unsolvable boards say so", () => {
  for (const n of [3, 4]) {
    for (let seed = 1; seed <= 25; seed++) {
      const st = { rng: seed }, lights = [];
      for (let i = 0; i < n * n; i++) lights.push(L.rand(st) < 0.5 ? 1 : 0);
      const sol = L.solve(lights, n), brute = bruteFewest(lights, n);
      if (brute === null) { assert.equal(sol, null); continue; }
      assert.equal(sol.length, brute, `${n} × ${n} seed ${seed}`);
      const t = lights.slice();
      for (const c of sol) L.toggle(t, n, c);
      assert.ok(t.every((v) => v === 0));
    }
  }
  // 5 × 5 has boards with no answer: a single light in a corner
  const one = Array(25).fill(0); one[0] = 1;
  assert.equal(L.solve(one, 5), null);
});

test("every mode's boards can be solved and need at least the set number of presses", () => {
  for (const mode of Object.keys(L.MODES)) {
    for (let seed = 1; seed <= 20; seed++) {
      const s = L.create({ mode, seed });
      assert.equal(s.n, L.MODES[mode].sizes[0]);
      assert.ok(L.lit(s) > 0);
      const sol = L.solve(s.lights, s.n);
      assert.ok(sol, "solvable");
      assert.equal(sol.length, s.fewest);
      assert.ok(s.fewest >= L.MAKE[s.n][2], `${mode} ${seed}: ${s.fewest}`);
    }
  }
  assert.equal(L.create({}).mode, "classic");
});

test("switching every light off wins; the score is TOP − presses; Climb goes through five sizes", () => {
  const s = L.create({ mode: "little", seed: 5 });
  assert.equal(L.result(s).score, 0);
  const sol = L.solve(s.lights, 3);
  let evs = [];
  for (const c of sol) evs = evs.concat(L.pressCell(s, c));
  assert.ok(types(evs).includes("clear") && types(evs).includes("win"));
  assert.equal(L.result(s).score, L.TOP - sol.length);
  assert.equal(L.result(s).stats.fewest, sol.length);
  assert.match(L.result(s).stats.summary[0], /fewest possible/);
  const c = L.create({ mode: "climb", seed: 2 }), sizes = [];
  let guard = 0;
  while (!c.over && guard++ < 5000) {
    if (c.phase === "play") { if (!sizes.includes(c.n)) sizes.push(c.n); L.pressCell(c, L.solve(c.lights, c.n)[0]); }
    const e = L.step(c);
    assert.ok(!types(e).includes("level") || c.level <= 5);
  }
  assert.deepEqual(sizes, [3, 4, 5, 6, 7]);
  assert.ok(c.won);
  assert.equal(L.result(c).level, 5);
  assert.equal(L.result(c).stats.boards, 5);
});

test("a press during the pause between Climb boards does nothing", () => {
  const c = L.create({ mode: "climb", seed: 3 });
  for (const cell of L.solve(c.lights, c.n)) L.pressCell(c, cell);
  assert.equal(c.phase, "clear");
  const moves = c.moves;
  assert.deepEqual(L.pressCell(c, 0), []);
  assert.equal(c.moves, moves);
  for (let i = 0; i < L.CLEAR_T; i++) L.step(c);
  assert.equal(c.phase, "play");
  assert.equal(c.n, 4);
});

test("hints: off by default (and in races); on, Hint rings a light from the fewest presses and costs 5", () => {
  const off = L.create({ mode: "classic", seed: 4 });
  assert.deepEqual(types(L.press(off, "alt", true)), ["nohint"]);
  assert.equal(off.hints, 0);
  const s = L.create({ mode: "classic", seed: 4, hints: "on" });
  const evs = L.press(s, "alt", true);
  assert.deepEqual(types(evs), ["hint"]);
  assert.ok(L.solve(s.lights, 5).includes(s.hint));
  assert.deepEqual(L.press(s, "alt", true), [], "one hint at a time");
  L.pressCell(s, s.hint);
  assert.equal(s.hint, -1);
  solveAll(s);
  assert.ok(s.won);
  assert.equal(L.result(s).score, L.TOP - s.moves - L.HINT_COST);
});

test("the keyboard cursor wraps; Space presses it", () => {
  const s = L.create({ mode: "classic", seed: 6 });
  s.cursor = 0;
  L.press(s, "left", true); assert.equal(s.cursor, 4);
  L.press(s, "up", true); assert.equal(s.cursor, 24);
  const before = s.lights.slice();
  L.press(s, "fire", true);
  assert.notDeepEqual(s.lights, before);
  assert.equal(s.moves, 1);
});

test("deterministic for a seed; save and restore plays on the same; bad saves are refused", () => {
  assert.equal(JSON.stringify(L.create({ mode: "big", seed: 9 })), JSON.stringify(L.create({ mode: "big", seed: 9 })));
  assert.notEqual(JSON.stringify(L.create({ mode: "big", seed: 9 }).lights), JSON.stringify(L.create({ mode: "big", seed: 10 }).lights));
  const s = L.create({ mode: "climb", seed: 12 });
  for (const cell of L.solve(s.lights, s.n)) L.pressCell(s, cell);
  for (let i = 0; i < 100; i++) L.step(s);
  const copy = L.restore(JSON.parse(JSON.stringify(L.save(s))));
  solveAll(s); solveAll(copy);
  assert.equal(JSON.stringify(L.save(copy)), JSON.stringify(L.save(s)));
  const bad = L.save(L.create({ mode: "classic", seed: 1 }));
  bad.lights = Array(25).fill(0); bad.lights[0] = 1;
  assert.throws(() => L.restore(bad), /can't be continued/, "a board with no answer");
  assert.throws(() => L.restore({ mode: "classic" }), /can't be continued/);
  assert.throws(() => L.restore(null));
});

test("honest score: never above TOP, whatever is pressed", () => {
  const s = L.create({ mode: "little", seed: 1 });
  for (const c of L.solve(s.lights, 3)) L.pressCell(s, c);
  assert.ok(L.result(s).score <= 10000);
  assert.ok(L.result(s).score >= L.MIN_SCORE);
});

test("fuzz: random presses and keys never throw and keep the board sound", () => {
  for (const mode of Object.keys(L.MODES)) {
    for (let seed = 1; seed <= 8; seed++) {
      const s = L.create({ mode, seed, hints: seed % 2 ? "on" : "off" });
      let x = seed * 7919;
      for (let i = 0; i < 3000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 3 === 0) L.press(s, ["up", "down", "left", "right", "fire", "alt"][x % 6], x % 4 !== 0);
        if (x % 5 === 0) L.pressCell(s, x % (s.n * s.n + 3) - 1);
        L.step(s);
        assert.equal(s.lights.length, s.n * s.n);
        assert.ok(s.lights.every((v) => v === 0 || v === 1));
        assert.ok(L.solve(s.lights, s.n) !== null, "still solvable");
        assert.ok(s.level >= 1 && s.level <= s.sizes.length);
      }
    }
  }
});

test("lights renders and plays in every look, with taps, keys and hints", () => {
  for (const look of LOOKS) {
    for (const mode of ["little", "big", "climb"]) {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("lights");
      assert.equal(def.name, "Lights Out");
      assert.equal(def.stateVersion, 1);
      assert.equal(def.race, true);
      assert.equal(def.options[0].offInRaces, true);
      const inst = def.create(canvas, { mode, look, seed: 5, options: { hints: "on" } });
      inst.start();
      for (let i = 0; i < 240; i++) {
        if (i % 11 === 0) inst.input(["up", "left", "fire", "right", "down", "alt"][(i / 11) % 6], true);
        if (i % 17 === 0) inst.pointer("down", 60 + (i % 260), 120 + (i % 200));
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(inst.logic.moves > 0);
      if (inst.state !== "over") assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
