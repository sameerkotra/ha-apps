"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const M = loadLogic("mole-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const PER_SECOND = 40, BASE = 60, MAX_SCORE = 1_000_000;
function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) all.push(...each(s)); all.push(...M.step(s)); } return all; }
function upHoles(s, kind) { return s.holes.map((h, i) => (h.k && !h.hit && (!kind || h.k === kind) ? i : -1)).filter((i) => i >= 0); }
function waitFor(s, kind, max = 6000, each) { for (let i = 0; i < max && !s.over; i++) { const u = upHoles(s, kind); if (u.length) return u[0]; if (each) each(s); M.step(s); } return -1; }
// A perfect bot: bonks every mole (and golden one) the update it appears, never a hedgehog.
function perfect(s) { const evs = []; for (const i of upHoles(s)) if (s.holes[i].k !== "hog") evs.push(...M.bonk(s, i)); return evs; }

test("gardens: 3 × 3 and 4 × 4 holes, big cells, tapping maps to the right hole", () => {
  for (const [mode, n, cs] of [["classic", 9, 72], ["big", 16, 54], ["rush", 9, 72]]) {
    const s = M.create({ mode, seed: 1 });
    assert.equal(s.holes.length, n);
    assert.equal(M.cellSize(s), cs);
    for (let i = 0; i < n; i++) {
      const b = M.cellBox(s, i);
      assert.equal(M.holeAt(s, b.x + b.w / 2, b.y + b.h / 2), i);
      assert.ok(b.x >= 0 && b.x + b.w <= 240 && b.y >= 40 && b.y + b.h <= 276, "inside the play area");
      assert.equal(b.x % 6, 0); assert.equal(b.y % 6, 0);
    }
    assert.equal(M.holeAt(s, 5, 5), -1);
    assert.equal(M.holeAt(s, 120, 290), -1);
  }
  assert.equal(M.create({ mode: "nope" }).mode, "classic");
});

test("moles pop up, rise, and a tap while up bonks for 10; empty holes and second taps do nothing", () => {
  const s = M.create({ seed: 2 });
  const evs = run(s, 200, () => []);
  assert.ok(types(evs).includes("pop"));
  const s2 = M.create({ seed: 2 });
  const i = waitFor(s2, "mole");
  assert.ok(i >= 0);
  assert.equal(M.height(s2.holes[i]) < 1, true, "still rising");
  const empty = s2.holes.findIndex((h) => !h.k);
  assert.deepEqual(types(M.bonk(s2, empty)), ["whiff"]);
  const e = M.bonk(s2, i);
  assert.deepEqual(types(e), ["bonk"]);
  assert.equal(s2.score, 10);
  assert.deepEqual(types(M.bonk(s2, i)), ["whiff"]);
  assert.equal(s2.score, 10);
  // the dazed mole goes away and the hole rests a moment
  run(s2, M.HIT_SHOW + 1);
  assert.equal(s2.holes[i].k, null);
  assert.ok(s2.holes[i].rest > 0);
});

test("a golden mole scores 50 and stays up for less time", () => {
  const s = M.create({ seed: 3 });
  const i = waitFor(s, "mole");
  s.holes[i].k = "gold"; s.holes[i].up = M.goldUp(1);
  assert.ok(M.goldUp(1) < M.upTime(1));
  assert.deepEqual(types(M.bonk(s, i)), ["gold"]);
  assert.equal(s.score, 50);
  assert.equal(s.stats.golden, 1);
  // golden moles do appear on their own, at least GOLD_GAP updates apart
  const t = M.create({ seed: 4 });
  let last = -Infinity, golds = 0;
  for (let u = 0; u < 60 * 300 && !t.over; u++) {
    for (const ev of perfect(t)) if (ev.type === "gold") { assert.ok(t.updates - last >= M.GOLD_GAP - M.RISE); last = t.updates; golds++; }
    M.step(t);
  }
  assert.ok(golds >= 3, `golds ${golds}`);
});

test("Classic: a mole that gets away costs a life; three and the game is over", () => {
  const s = M.create({ seed: 5 });
  const evs = run(s, 60 * 60);
  assert.ok(s.over);
  assert.equal(s.lives, 0);
  assert.equal(types(evs).filter((t) => t === "miss").length, 3);
  assert.equal(types(evs).filter((t) => t === "life").length, 3);
  assert.equal(M.result(s).stats.cause, "missed");
  assert.equal(M.result(s).stats.livesLeft, 0);
  assert.deepEqual(types(M.bonk(s, 0)), []);
  assert.deepEqual(M.step(s), []);
});

test("60 seconds: misses cost nothing, the clock ends the game after 3600 updates", () => {
  const s = M.create({ mode: "rush", seed: 6 });
  assert.equal(s.lives, 0);
  const evs = run(s, 5000);
  assert.ok(s.over);
  assert.equal(s.updates, 3600);
  assert.ok(types(evs).includes("miss"));
  assert.ok(!types(evs).includes("life"));
  assert.equal(M.result(s).stats.cause, "time");
});

test("hedgehogs: never before level 3; tapping one costs a life (60 seconds: 5 s)", () => {
  const s = M.create({ seed: 7 });
  for (let u = 0; u < 60 * 120 && !s.over && s.level < 3; u++) {
    for (const ev of perfect(s)) assert.notEqual(ev.type, "hog");
    for (const ev of M.step(s)) if (ev.type === "pop") assert.notEqual(ev.kind, "hog");
  }
  assert.equal(s.level, 3);
  const i = waitFor(s, "hog", 6000, perfect);
  assert.ok(i >= 0, "a hedgehog comes at level 3");
  assert.equal(M.hogChance(2), 0);
  assert.ok(M.hogChance(3) > 0 && M.hogChance(99) <= 0.3);
  const lives = s.lives, score = s.score;
  assert.deepEqual(types(M.bonk(s, i)), ["hog", "life"]);
  assert.equal(s.lives, lives - 1);
  assert.equal(s.score, score);
  // a hedgehog left alone costs nothing
  const j = waitFor(s, "hog", 6000, perfect);
  assert.ok(j >= 0);
  const l2 = s.lives;
  run(s, s.holes[j].up + 2, perfect);
  assert.equal(s.lives, l2);

  const r = M.create({ mode: "rush", seed: 8 });
  r.level = 5;
  const k = waitFor(r, "hog", 6000, perfect);
  const c = r.clock;
  assert.deepEqual(types(M.bonk(r, k)), ["hog"]);
  assert.equal(r.clock, c - M.HOG_COST);
  r.clock = 100; r.holes[k].hit = 0;
  M.bonk(r, k);
  assert.ok(r.over);
});

test("a level every 10 bonks; moles come quicker and stay up shorter, within limits", () => {
  const s = M.create({ seed: 9 });
  const evs = run(s, 60 * 60, perfect);
  const lv = evs.filter((e) => e.type === "level");
  assert.ok(lv.length >= 2);
  assert.equal(s.level, 1 + Math.floor(s.bonks / 10));
  assert.ok(M.upTime(5) < M.upTime(1) && M.gap(5) < M.gap(1));
  assert.ok(M.maxUp({ mode: "classic", level: 9 }) > M.maxUp({ mode: "classic", level: 1 }));
  for (let l = 1; l <= 100; l++) {
    assert.ok(M.upTime(l) >= 36 && M.goldUp(l) >= 30, "never shorter than half a second");
    assert.ok(M.gap(l) >= M.MIN_GAP);
    assert.ok(M.maxUp({ mode: "classic", level: l }) <= 3 && M.maxUp({ mode: "big", level: l }) <= 4);
  }
});

test("keyboard: arrows move the ring (stopping at the edges), Space bonks under it", () => {
  const s = M.create({ seed: 10 });
  assert.equal(s.cur, 4);
  M.press(s, "left", true); M.press(s, "left", true); M.press(s, "up", true);
  assert.equal(s.cur, 0);
  M.press(s, "right", true); M.press(s, "down", true);
  assert.equal(s.cur, 4);
  M.press(s, "right", false);
  assert.equal(s.cur, 4);
  const i = waitFor(s, "mole");
  s.cur = i;
  assert.deepEqual(types(M.press(s, "fire", true)), ["bonk"]);
  const b = M.create({ mode: "big", seed: 1 });
  for (let n = 0; n < 6; n++) { M.press(b, "right", true); M.press(b, "down", true); }
  assert.equal(b.cur, 15);
});

test("a quick human (reacts in 0.3 s) keeps up at high levels", () => {
  const s = M.create({ seed: 11 });
  s.level = 20; s.bonks = 190;
  let u = 0;
  while (!s.over && u++ < 60 * 120) {
    for (const i of upHoles(s)) if (s.holes[i].k !== "hog" && s.holes[i].t >= 18) M.bonk(s, i);
    M.step(s);
  }
  assert.ok(!s.over, "still playing after 2 minutes");
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed, mode) => { const s = M.create({ seed, mode }); run(s, 60 * 90, (t) => (t.updates % 3 ? [] : perfect(t))); return JSON.stringify(M.result(s)) + s.updates + s.rng; };
  assert.equal(play(21, "classic"), play(21, "classic"));
  assert.notEqual(play(21, "big"), play(22, "big"));
  for (const mode of ["classic", "big", "rush"]) {
    const s = M.create({ seed: 23, mode });
    run(s, 900, (t) => (t.updates % 4 ? [] : perfect(t)));
    const copy = M.restore(JSON.parse(JSON.stringify(M.save(s))));
    for (let i = 0; i < 1200; i++) {
      const bot = (t) => (t.updates % 4 ? [] : perfect(t));
      assert.deepEqual(types(bot(copy)), types(bot(s)));
      assert.deepEqual(M.step(copy), M.step(s));
    }
    assert.equal(copy.score, s.score);
    assert.equal(JSON.stringify(copy), JSON.stringify(s));
  }
  assert.throws(() => M.restore({ holes: 3 }), /can't be continued/);
  assert.throws(() => M.restore(null), /can't be continued/);
  assert.throws(() => M.restore({ mode: "big", holes: [] }), /can't be continued/);
});

test("honest score: a perfect bot stays within seconds × 40 + 60", () => {
  let best = 0;
  for (const mode of ["classic", "big", "rush"]) {
    for (const seed of [31, 32, 33]) {
      const s = M.create({ mode, seed });
      while (!s.over && s.updates < 60 * 600) {
        perfect(s);
        M.step(s);
        assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `${mode}: score ${s.score} after ${s.updates} updates`);
        assert.ok(s.score <= MAX_SCORE && s.level <= 100);
      }
      best = Math.max(best, s.score / (s.updates / 60));
      if (mode === "rush") assert.ok(s.over);
    }
  }
  assert.ok(best > 15, `fastest ${best.toFixed(1)} a second`);
  console.log(`# mole: perfect bot's fastest rate ${best.toFixed(1)} points a second`);
});

test("mole renders and plays in every look, with taps and keys, switching looks mid-game", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "big", "rush", "gardens"]) {
      const sb = makeSandbox({ extra: ["mole-logic.js", "mole.js"] });
      const canvas = sb.canvas(390, 487);
      const ends = [];
      const def = sb.win.ArcadeGames.get("mole");
      assert.equal(def.controls, "touch");
      assert.equal(def.name, "Tap the Mole");
      assert.equal(def.stateVersion, 1);
      const levels = mode === "gardens" ? [EASY3, Object.assign({}, HARDEST, { bonks: 8 })].concat(M.LEVELS) : undefined;
      const inst = def.create(canvas, { mode, look, seed: 5, levels, onEnd: (r) => ends.push(r) });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      const s = inst.logic;
      if (mode !== "gardens") s.level = 4;   // hedgehogs too
      for (let i = 0; i < 400; i++) {
        if (i % 7 === 0) {
          const up = upHoles(s);
          if (up.length) { const b = M.cellBox(s, up[0]); inst.pointer("down", (b.x + b.w / 2) * 1.625, (b.y + b.h / 2) * 1.625); inst.pointer("up", 0, 0); }
        }
        if (i % 11 === 0) inst.input(["left", "up", "right", "down"][(i / 11) % 4], true);
        if (i % 13 === 0) inst.input("fire", true);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(inst.score > 0, "taps bonked moles");
      const saved = inst.save();
      assert.ok(saved && saved.state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});

// ---- Gardens (the level list) ----
// The hardest garden the field ranges allow (app/level_kinds/mole.py): 25 holes, critters every 24 updates,
// moles up the shortest time, the most golden ones and no hedgehogs (so every critter can score).
const HARDEST = { name: "Hardest", holes: ["ooooo", "ooooo", "ooooo", "ooooo", "ooooo"], bonks: 40, up: 36, every: 24, hedgehogs: 0, golden: 0.2 };
const EASY3 = { name: "Easy", holes: ["o.o", ".o.", "o.o"], bonks: 8, up: 120, every: 30, hedgehogs: 0, golden: 0 };

test("Gardens: built-in or the session's list; bad ones skipped; nothing usable means the built-ins", () => {
  assert.equal(M.usableLevels(undefined), M.LEVELS);
  assert.equal(M.usableLevels([]), M.LEVELS);
  assert.ok(M.LEVELS.length >= 5 && M.LEVELS.length <= 10);
  const bad = [
    null, Object.assign({}, EASY3, { holes: ["oo", "oo", "oo"] }), Object.assign({}, EASY3, { holes: ["ooo", "ooo"] }),
    Object.assign({}, EASY3, { holes: ["ooo", "o.o", "oo"] }), Object.assign({}, EASY3, { holes: ["o..", "...", "..o"] }),
    Object.assign({}, EASY3, { holes: ["oxo", "ooo", "ooo"] }), Object.assign({}, EASY3, { up: 30 }), Object.assign({}, EASY3, { every: 20 }),
    Object.assign({}, EASY3, { bonks: 41 }), Object.assign({}, EASY3, { hedgehogs: 0.5 }), Object.assign({}, EASY3, { golden: 0.3 }),
    Object.assign({}, EASY3, { up: 50.5 }), Object.assign({}, EASY3, { name: 1 }),
  ];
  assert.equal(M.usableLevels(bad), M.LEVELS);
  assert.deepEqual(M.usableLevels(bad.concat([EASY3])), [EASY3]);
});

test("Gardens: every shape fits the play area on multiples of 6 with big cells; grass is never a hole", () => {
  const shapes = [];
  for (const rows of [3, 4, 5]) for (const cols of [3, 4, 5]) shapes.push(Array.from({ length: rows }, () => "o".repeat(cols)));
  for (const holes of shapes.concat(M.LEVELS.map((l) => l.holes))) {
    const s = M.create({ mode: "gardens", seed: 1, levels: [Object.assign({}, EASY3, { holes })] });
    assert.equal(s.rows, holes.length); assert.equal(s.cols, holes[0].length);
    assert.equal(s.holes.length, s.rows * s.cols);
    assert.ok(M.cellSize(s) >= 42, "big enough to tap");
    for (let i = 0; i < s.holes.length; i++) {
      const b = M.cellBox(s, i);
      assert.equal(M.holeAt(s, b.x + b.w / 2, b.y + b.h / 2), i);
      assert.ok(b.x >= 0 && b.x + b.w <= 240 && b.y >= 40 && b.y + b.h <= 276, `${holes}: inside the play area`);
      assert.equal(b.x % 6, 0); assert.equal(b.y % 6, 0);
      assert.equal(M.isHole(s, i), holes[Math.floor(i / s.cols)][i % s.cols] === "o");
    }
    assert.ok(M.isHole(s, s.cur), "the ring starts on a hole");
  }
  const s = M.create({ mode: "gardens", seed: 2, levels: [EASY3] });
  assert.equal(M.holeCount(s), 5);
  for (let u = 0; u < 60 * 60 && !s.over; u++) for (const ev of M.step(s)) if (ev.type === "pop") assert.ok(M.isHole(s, ev.hole));
  assert.deepEqual(M.bonk(s, 1), [], "tapping grass does nothing");
});

test("Gardens: the garden's numbers set the pace; enough bonks → the next garden; the last one wins", () => {
  const two = [EASY3, { name: "Second", holes: ["oooo", "o..o", "oooo"], bonks: 9, up: 60, every: 40, hedgehogs: 0.3, golden: 0.2 }];
  const s = M.create({ mode: "gardens", seed: 3, levels: two });
  assert.equal(s.lives, 3);
  const evs = [];
  while (!s.over && s.updates < 60 * 300) {
    for (const i of upHoles(s)) if (s.holes[i].k !== "hog") evs.push(...M.bonk(s, i));
    for (const ev of M.step(s)) {
      evs.push(ev);
      if (ev.type === "pop") assert.equal(s.holes[ev.hole].up, ev.kind === "gold" ? Math.round(two[s.level - 1].up * 0.6) : two[s.level - 1].up);
    }
  }
  const lv = evs.filter((e) => e.type === "level");
  assert.equal(lv.length, 1);
  assert.equal(lv[0].name, "Second");
  assert.ok(s.won && s.over);
  assert.deepEqual(types(evs).slice(-1), ["win"]);
  assert.equal(s.stats.bonks, 17);
  assert.ok(types(evs).includes("pop"));
  assert.ok(evs.some((e) => e.type === "pop" && e.kind === "hog"), "the second garden has hedgehogs");
  assert.ok(!evs.slice(0, evs.indexOf(lv[0])).some((e) => e.type === "pop" && e.kind !== "mole"), "the first has none, nor golden");
  const r = M.result(s);
  assert.equal(r.level, 2);
  assert.equal(r.stats.won, true);
  assert.equal(r.stats.cause, "won");
  assert.equal(r.stats.gardens, 2);
  assert.equal(r.stats.mode, "gardens");
  assert.equal(s.rows, 3); assert.equal(s.cols, 4);
  // nobody playing: moles get away, three lives for the whole game
  const t = M.create({ mode: "gardens", seed: 4, levels: two });
  const tev = run(t, 60 * 60);
  assert.ok(t.over && !t.won);
  assert.equal(types(tev).filter((x) => x === "life").length, 3);
  assert.equal(M.result(t).stats.cause, "missed");
});

test("Gardens: save and restore keeps going with the list; the list isn't saved; old saves continue", () => {
  const list = M.LEVELS.slice(0, 6);
  const bot = (t) => (t.updates % 3 ? [] : perfect(t));
  const s = M.create({ mode: "gardens", seed: 5, levels: list });
  run(s, 60 * 25, bot);
  assert.ok(s.level >= 2 && !s.over, `level ${s.level}`);
  const data = JSON.parse(JSON.stringify(M.save(s)));
  assert.ok(!("gardens" in data));
  const copy = M.restore(data, list);
  assert.equal(copy.gardens.length, 6);
  for (let i = 0; i < 3000 && !s.over; i++) {
    assert.deepEqual(types(bot(copy)), types(bot(s)));
    assert.deepEqual(M.step(copy), M.step(s));
  }
  assert.equal(copy.score, s.score);
  assert.equal(JSON.stringify(M.save(copy)), JSON.stringify(M.save(s)));
  assert.equal(M.restore(data).gardens, M.LEVELS);
  assert.throws(() => M.restore(Object.assign({}, data, { cols: 9 }), list), /can't be continued/);
  // an old Classic save (before Gardens) still continues
  const old = M.save(M.create({ seed: 6 }));
  delete old.won; delete old.gbonks; delete old.gt; delete old.gardens; delete old.stats.gardens;
  const back = M.restore(old);
  assert.equal(back.gardens, null);
  run(back, 600, perfect);
  assert.ok(back.score > 0);
  assert.equal(M.result(back).stats.gardens, 0);
});

test("honest score in Gardens: a perfect bot on the hardest gardens stays within seconds × 40 + 60", () => {
  let best = 0;
  const longest = Array.from({ length: 100 }, (_, i) => Object.assign({}, HARDEST, { name: "Hard " + i, bonks: 8 }));
  const small = Object.assign({}, HARDEST, { holes: ["o.o", ".o.", "o.o"] });
  for (const levels of [[HARDEST], longest, [small], M.LEVELS]) {
    for (const seed of [41, 42]) {
      const s = M.create({ mode: "gardens", seed, levels });
      while (!s.over && s.updates < 60 * 900) {
        perfect(s);
        M.step(s);
        assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates} updates`);
        assert.ok(s.score <= MAX_SCORE && s.level <= 100 && s.level <= levels.length);
      }
      assert.ok(s.won, "the perfect bot clears the list");
      best = Math.max(best, s.score / (s.updates / 60));
    }
  }
  console.log(`# mole gardens: perfect bot's fastest rate ${best.toFixed(1)} points a second`);
});
