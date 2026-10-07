"use strict";
// Tower Stack (wave 5): the rules, the tower list, saving, the honest-score limits, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const S = loadLogic("stack-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const EXTRA = ["stack-logic.js", "stack.js"];
const LIMIT = { perSecond: 450, base: 500, max: 3000000 };

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...S.step(s)); } return all; }
function tower(extra) { return Object.assign({ name: "Test", floors: 8, width: 100, speed: 1.5, ramp: 0, grow: true }, extra || {}); }
/** Wait until the block is right over the floor below, then drop: a perfect drop every time. */
function perfect(s) {
  if (s.phase !== "play" || !s.block || s.wait > 0) return;
  const top = s.floors[s.floors.length - 1];
  if (Math.abs(s.block.x - top.x) <= Math.abs(s.block.v)) S.press(s, "fire", true);
}
/** As fast as the rules allow: drop the moment a block may be dropped, and line it up first (a cheat). */
function fastest(s) {
  if (s.phase !== "play" || !s.block) return;
  const top = s.floors[s.floors.length - 1];
  s.block.x = top.x - s.block.v;              // it will be exactly over the floor after this update's move
  S.press(s, "fire", true);
}

test("the start: a base floor, a block the same width sliding in from one side", () => {
  const s = S.create({ seed: 1 });
  assert.equal(s.mode, "classic");
  assert.equal(s.floors.length, 1);
  assert.equal(s.floors[0].w, S.MODES.classic.width);
  assert.equal(s.block.w, s.floors[0].w);
  assert.ok(s.block.x === S.LEFT || s.block.x + s.block.w === S.RIGHT);
  assert.equal(s.lives, 1);
  assert.equal(S.create({ mode: "fast" }).floors[0].w, S.MODES.fast.width);
  assert.equal(S.create({ mode: "towers" }).lives, 3);
});

test("the block slides between the walls and bounces back", () => {
  const s = S.create({ seed: 2 });
  let minX = 999, maxX = -1;
  run(s, 600, (st) => { minX = Math.min(minX, st.block.x); maxX = Math.max(maxX, st.block.x + st.block.w); });
  assert.equal(minX, S.LEFT);
  assert.equal(maxX, S.RIGHT);
});

test("a drop cuts off the overhang, which falls away; the next block is as wide as the overlap", () => {
  const s = S.create({ seed: 3 });
  run(s, S.SPAWN);
  const top = s.floors[0];
  s.block.x = top.x + 30; s.block.v = 1;
  S.press(s, "fire", true);
  const evs = S.step(s);
  assert.ok(types(evs).includes("cut"));
  assert.ok(types(evs).includes("place"));
  assert.equal(s.floors.length, 2);
  assert.equal(Math.round(s.floors[1].w), Math.round(top.w - 31));
  assert.equal(s.block.w, s.floors[1].w);
  assert.equal(s.chunks.length, 1, "the overhang falls");
  assert.equal(s.score, S.FLOOR_POINTS);
  assert.equal(s.height, 1);
  run(s, 200);
  assert.equal(s.chunks.length, 0, "and is gone");
});

test("perfect drops line up exactly, score more and (with grow) widen the block back", () => {
  const s = S.create({ seed: 4 });
  run(s, S.SPAWN);
  s.floors[0].w = 80; s.block.w = 80;           // a narrowed tower
  const before = s.floors[0].w;
  for (let n = 1; n <= 4; n++) {
    let evs = [];
    while (s.height < n && !s.over) { perfect(s); evs = evs.concat(S.step(s)); }
    assert.ok(types(evs).includes("perfect"), "perfect " + n);
  }
  assert.equal(s.streak, 4);
  assert.equal(s.stats.perfects, 4);
  assert.ok(s.floors[s.floors.length - 1].w > before, "grown back");
  assert.ok(s.floors[s.floors.length - 1].w <= S.MODES.classic.width);
  // 10 + 10, 10 + 20, 10 + 30, 10 + 40
  assert.equal(s.score, 4 * S.FLOOR_POINTS + S.PERFECT_POINTS * (1 + 2 + 3 + 4));
  // the bonus stops growing at the cap
  for (let n = 5; n <= 8; n++) while (s.height < n && !s.over) { perfect(s); S.step(s); }
  const after = s.score;
  while (s.height < 9 && !s.over) { perfect(s); S.step(s); }
  assert.equal(s.score - after, S.FLOOR_POINTS + S.PERFECT_POINTS * S.PERFECT_CAP);
});

test("a press waits for the new block; each floor is faster up to the top speed", () => {
  const s = S.create({ seed: 5, mode: "fast" });
  S.press(s, "fire", true);
  const evs = run(s, S.SPAWN - 1);
  assert.ok(!types(evs).includes("place"), "not before the block has come");
  assert.ok(types(S.step(s)).includes("place") || s.height === 1 || s.over);
  assert.ok(S.speedAt(s, 10) > S.speedAt(s, 0));
  assert.equal(S.speedAt(s, 10000), S.MAX_V);
});

test("missing the tower ends Classic; in Towers it costs a life and the floor is tried again", () => {
  const s = S.create({ seed: 6 });
  run(s, S.SPAWN);
  s.floors[0].x = 8; s.floors[0].w = 40; s.block.x = 190; s.block.w = 40; s.block.v = 0.5;
  S.press(s, "fire", true);
  let evs = S.step(s);
  assert.ok(types(evs).includes("miss"));
  evs = run(s, S.MISS_T + 2);
  assert.ok(s.over);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(S.result(s).stats.cause, "miss");
  const t = S.create({ seed: 6, mode: "towers" });
  run(t, S.SPAWN);
  t.floors[0].x = 8; t.floors[0].w = 40; t.block.x = 190; t.block.w = 40; t.block.v = 0.5;
  S.press(t, "fire", true); S.step(t);
  assert.equal(t.lives, 2);
  run(t, S.MISS_T + 2);
  assert.ok(!t.over);
  assert.ok(t.block && t.phase === "play");
  assert.equal(t.height, 0, "the same floor again");
});

test("Towers: built-in or the session's list; bad towers are skipped; building one moves on, the last wins", () => {
  assert.equal(S.usableLevels(undefined), S.LEVELS);
  const bad = [tower({ floors: 7 }), tower({ floors: 61 }), tower({ width: 39 }), tower({ width: 161 }), tower({ speed: 0.9 }),
    tower({ speed: 3.6 }), tower({ ramp: 0.07 }), tower({ grow: 1 }), tower({ floors: 60, speed: 3, ramp: 0.06 }),
    tower({ floors: 8.5 }), tower({ name: "" }), null, 4];
  assert.equal(S.usableLevels(bad), S.LEVELS);
  assert.deepEqual(S.usableLevels(bad.concat([tower({ name: "Fine" })])).map((t) => t.name), ["Fine"]);
  for (const t of S.LEVELS) assert.equal(S.usableLevels([t]).length, 1, t.name);
  const two = [tower({ name: "A" }), tower({ name: "B", width: 60 })];
  const s = S.create({ mode: "towers", seed: 3, levels: two });
  assert.equal(s.towers.length, 2);
  assert.equal(S.create({ mode: "classic", levels: two }).towers, null, "only Towers plays the list");
  const all = [];
  while (!s.over && s.level === 1) { perfect(s); all.push(...S.step(s)); }
  assert.ok(types(all).includes("tower"));
  assert.ok(types(all).includes("level"));
  assert.equal(s.floors[0].w, 60, "the next tower's width");
  assert.equal(s.height, 0);
  const data = JSON.parse(JSON.stringify(S.save(s)));
  assert.ok(!("towers" in data));
  assert.equal(S.restore(data, two).towers.length, 2);
  while (!s.over) { perfect(s); all.push(...S.step(s)); }
  assert.ok(s.won);
  assert.ok(types(all).includes("win"));
  assert.equal(S.result(s).stats.towers, 2);
  assert.equal(S.result(s).stats.won, true);
  assert.equal(s.score, 16 * S.FLOOR_POINTS + 2 * S.TOWER_POINTS + S.PERFECT_POINTS * (1 + 2 + 3 + 4 + 5 * 4 + 1 + 2 + 3 + 4 + 5 * 4));
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed) => { const s = S.create({ seed }); run(s, 60 * 60, (st) => { if (st.updates % 37 === 0) S.press(st, "fire", true); }); return s; };
  assert.equal(JSON.stringify(play(9)), JSON.stringify(play(9)));
  assert.notEqual(JSON.stringify(play(9).floors), JSON.stringify(play(10).floors));
  const s = S.create({ seed: 12, mode: "towers" });
  run(s, 300, perfect);
  const copy = S.restore(JSON.parse(JSON.stringify(S.save(s))));
  for (let i = 0; i < 1200 && !s.over; i++) { perfect(s); perfect(copy); S.step(s); S.step(copy); }
  assert.equal(JSON.stringify(S.save(copy)), JSON.stringify(S.save(s)));
  assert.throws(() => S.restore({ floors: 3 }), /can't be continued/);
  assert.throws(() => S.restore(null));
});

test("honest score: even a perfect drop at every moment stays inside the limits, also on the hardest towers", () => {
  const hardest = [];
  for (let i = 0; i < 6; i++) hardest.push(tower({ name: "Hard " + i, floors: 8, width: 40, speed: 3.5, ramp: 0.06, grow: true }));
  let top = 0;
  for (const [mode, levels] of [["classic"], ["fast"], ["easy"], ["towers"], ["towers", hardest]]) {
    const s = S.create({ seed: 1, mode, levels });
    while (!s.over && s.updates < 60 * 60 * 10) {
      fastest(s); S.step(s);
      const sec = s.updates / 60;
      assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
      assert.ok(s.score <= LIMIT.max && s.level <= 100);
      if (sec > 5) top = Math.max(top, s.score / sec);
    }
  }
  console.log(`# stack: fastest possible rate ${top.toFixed(1)} points a second`);
});

test("fuzz: random keys and taps never throw and keep the tower sound", () => {
  for (const mode of ["classic", "fast", "easy", "towers"]) {
    for (let seed = 1; seed <= 15; seed++) {
      const s = S.create({ seed, mode });
      let x = seed * 7919, last = 0;
      for (let i = 0; i < 4000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 9 === 0) S.press(s, ["fire", "up", "down", "left", "alt"][x % 5], x % 2 === 0);
        S.step(s);
        assert.ok(Number.isFinite(s.score) && s.score >= last, "the score never goes down");
        last = s.score;
        for (const f of s.floors) assert.ok(f.w > 0 && f.x >= S.LEFT - 0.01 && f.x + f.w <= S.RIGHT + 0.01, JSON.stringify(f));
        if (s.block) assert.ok(s.block.x >= S.LEFT - 0.01 && s.block.x + s.block.w <= S.RIGHT + 0.01);
        assert.ok(s.level >= 1 && s.level <= 100 && s.lives >= 0);
      }
    }
  }
});

test("stack renders and plays in every look, with keys and taps, saving and switching looks", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "easy", "towers"]) {
      const sb = makeSandbox({ extra: EXTRA });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("stack");
      assert.equal(def.name, "Tower Stack");
      assert.equal(def.controls, "buttons");
      assert.equal(def.stateVersion, 1);
      assert.equal(def.race, true);
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      for (let i = 0; i < 700; i++) {
        if (i % 23 === 0) inst.input("fire", true);
        if (i % 31 === 0) inst.pointer("down", 100, 200);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      if (inst.state === "over") assert.equal(ends.length, 1);
      else assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});

test("Easy (for little ones): a wide, slow block and three tries — a miss tries that floor again", () => {
  const s = S.create({ seed: 4, mode: "easy" });
  assert.equal(s.lives, 3);
  assert.equal(s.floors[0].w, 150);
  assert.ok(Math.abs(s.block.v) <= 1.01);
  let misses = 0;
  while (!s.over && s.updates < 60 * 120) {
    // a thin block held at the far left, clear of the tower: every drop misses
    if (s.phase === "play" && s.block) { Object.assign(s.block, { x: S.LEFT, w: 10, v: 0 }); S.press(s, "fire", true); }
    const evs = S.step(s);
    misses += evs.filter((e) => e.type === "miss").length;
  }
  assert.equal(s.lives, 0);
  assert.ok(s.over && misses === 3);
  assert.equal(S.result(s).stats.misses, 3);
});
