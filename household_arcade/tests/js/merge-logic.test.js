"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const G = loadLogic("merge-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const LIMIT = { per: 800, base: 300, max: 17000000 };

function types(evs) { return evs.map((e) => e.type); }
function run(s, n) { const all = []; for (let i = 0; i < n && !s.over; i++) all.push(...G.step(s)); return all; }
function board(s, rows) { s.grid = [].concat(...rows); s.cool = 0; s.queued = null; s.dead = false; }
function idle(s) { run(s, G.MOVE_GAP); }

test("the three sizes start with two tiles of 2 or 4", () => {
  for (const [mode, n] of [["classic", 4], ["big", 5], ["small", 3]]) {
    for (let seed = 1; seed < 20; seed++) {
      const s = G.create({ mode, seed });
      assert.equal(s.n, n);
      const tiles = s.grid.filter(Boolean);
      assert.equal(tiles.length, 2);
      for (const v of tiles) assert.ok(v === 2 || v === 4);
      assert.equal(s.score, 0);
      assert.equal(s.level, G.levelOf(Math.max(...tiles)));
    }
  }
  assert.equal(G.create({ mode: "zzz" }).mode, "classic");
});

test("sliding and merging: each tile merges at most once a move; merged values score", () => {
  const s = G.create({ seed: 1 });
  board(s, [[2, 2, 2, 2], [4, 0, 4, 8], [2, 2, 4, 0], [0, 0, 0, 2]]);
  const p = G.plan(s, "left");
  assert.deepEqual(p.grid.slice(0, 4), [4, 4, 0, 0]);
  assert.deepEqual(p.grid.slice(4, 8), [8, 8, 0, 0]);
  assert.deepEqual(p.grid.slice(8, 12), [4, 4, 0, 0]);
  assert.deepEqual(p.grid.slice(12, 16), [2, 0, 0, 0]);
  assert.equal(p.points, 4 + 4 + 8 + 4);
  const evs = G.press(s, "left", true);
  assert.deepEqual(types(evs), ["move", "merge", "level"]);
  assert.equal(s.score, 20);
  assert.equal(s.grid.filter(Boolean).length, 8, "seven tiles and a new one");
  assert.equal(s.level, 3);
  // right: [2, 2, 4, 0] → [0, 0, 4, 4] (the new 4 doesn't merge again)
  board(s, [[2, 2, 4, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]);
  assert.deepEqual(G.plan(s, "right").grid.slice(0, 4), [0, 0, 4, 4]);
  board(s, [[2, 0, 0, 0], [2, 0, 0, 0], [4, 0, 0, 0], [4, 0, 0, 0]]);
  assert.deepEqual([0, 4, 8, 12].map((i) => G.plan(s, "down").grid[i]), [0, 0, 4, 8]);
  assert.deepEqual([0, 4, 8, 12].map((i) => G.plan(s, "up").grid[i]), [4, 8, 0, 0]);
});

test("a move that changes nothing does nothing (no new tile)", () => {
  const s = G.create({ seed: 2 });
  board(s, [[2, 4, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]);
  const before = s.grid.slice(), rng = s.rng;
  assert.deepEqual(G.press(s, "left", true), []);
  assert.deepEqual(G.press(s, "up", true), []);
  assert.deepEqual(s.grid, before);
  assert.equal(s.rng, rng);
  assert.equal(s.cool, 0);
});

test("new tiles: 2 about nine times in ten, else 4, on an empty square", () => {
  const s = G.create({ seed: 3 });
  let twos = 0, fours = 0;
  for (let i = 0; i < 2000; i++) {
    s.grid.fill(0); s.grid[5] = 2;
    const at = G.spawn(s);
    assert.notEqual(at, 5);
    if (s.grid[at] === 2) twos++; else if (s.grid[at] === 4) fours++;
  }
  assert.equal(twos + fours, 2000);
  assert.ok(fours > 120 && fours < 290, `${fours} fours`);
});

test("moves are at least MOVE_GAP updates apart; one move pressed meanwhile is kept and made after", () => {
  const s = G.create({ seed: 4 });
  board(s, [[2, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 4], [0, 0, 0, 0]]);
  assert.ok(types(G.press(s, "right", true)).includes("move"));
  assert.equal(s.cool, G.MOVE_GAP);
  assert.deepEqual(G.press(s, "down", true), []);
  assert.deepEqual(G.press(s, "left", true), [], "the last press is the one kept");
  assert.equal(s.queued, "left");
  const moves = s.stats.moves;
  let evs = run(s, G.MOVE_GAP - 1);
  assert.equal(s.stats.moves, moves);
  evs = run(s, 1);
  assert.ok(types(evs).includes("move"));
  assert.equal(s.stats.moves, moves + 1);
  assert.equal(s.queued, null);
  assert.deepEqual(G.press(s, "left", false), [], "key-up does nothing");
});

test("level is the power of two of the largest tile; a win event once at the big tile, play goes on", () => {
  assert.deepEqual([2, 4, 8, 1024, 2048, 4096].map(G.levelOf), [1, 2, 3, 10, 11, 12]);
  const s = G.create({ seed: 5 });
  board(s, [[1024, 1024, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [2, 0, 0, 0]]);
  s.best = 1024; s.level = 10;
  const evs = G.press(s, "left", true);
  assert.deepEqual(types(evs), ["move", "merge", "level", "win"]);
  assert.equal(s.level, 11);
  assert.ok(s.won);
  idle(s);
  assert.ok(!s.over);
  board(s, [[2048, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [2, 2, 0, 0]]);
  assert.ok(!types(G.press(s, "left", true)).includes("win"), "only once");
  assert.equal(G.result(s).stats.won, true);
  assert.equal(G.result(s).stats.bestTile, 2048);
});

test("no moves left ends the game once the last slide is done", () => {
  let ended = 0;
  for (let seed = 1; seed <= 20; seed++) {
    const s = G.create({ mode: "small", seed });
    board(s, [[2, 4, 2], [4, 2, 4], [0, 8, 16]]);
    const evs = G.press(s, "left", true);           // [8, 16, new]: stuck unless the new tile is a 4
    assert.ok(types(evs).includes("move"));
    assert.equal(s.dead, s.grid[8] === 2);
    if (!s.dead) continue;
    ended++;
    assert.ok(!s.over, "not before the slide has finished");
    assert.deepEqual(types(run(s, G.MOVE_GAP)), ["gameover"]);
    assert.ok(s.over);
    assert.deepEqual(G.press(s, "left", true), []);
    assert.deepEqual(run(s, 5), []);
  }
  assert.ok(ended > 10);
});

test("a board with no moves", () => {
  const s = G.create({ mode: "small", seed: 7 });
  board(s, [[2, 4, 2], [4, 2, 4], [2, 4, 2]]);
  assert.equal(G.anyMove(s), false);
  board(s, [[2, 4, 2], [4, 2, 4], [2, 4, 4]]);
  assert.equal(G.anyMove(s), true);
});

// Plays as fast as the rules allow: presses a move every update (queued during slides), preferring
// down, left, right, then up.
function bot(s) {
  for (const d of ["down", "left", "right", "up"]) if (G.canMove(s, d)) { G.press(s, d, true); return; }
}

test("deterministic for a seed; save and restore carry on identically", () => {
  const play = (seed) => { const s = G.create({ seed, mode: "big" }); for (let i = 0; i < 2000 && !s.over; i++) { bot(s); G.step(s); } return s; };
  assert.deepEqual(play(9), play(9));
  const s = G.create({ seed: 10 });
  for (let i = 0; i < 303; i++) { bot(s); G.step(s); }
  G.press(s, "up", true);                       // mid-slide, with a move queued
  const copy = G.restore(JSON.parse(JSON.stringify(G.save(s))));
  for (let i = 0; i < 3000 && !s.over; i++) { bot(s); bot(copy); G.step(s); G.step(copy); }
  assert.deepEqual(copy, s);
  assert.throws(() => G.restore({ grid: [] }), /can't be continued/);
  assert.throws(() => G.restore(Object.assign(G.save(s), { grid: [3] })));
});

test("a frame-perfect bot stays inside the honest-score limits", () => {
  let fastest = 0, maxLevel = 0;
  for (const mode of ["classic", "big", "small"]) {
    for (const seed of [1, 2, 3]) {
      const s = G.create({ mode, seed });
      let u = 0;
      while (!s.over && u++ < 60 * 60 * 20) {
        bot(s);
        G.step(s);
        const secs = s.updates / 60;
        assert.ok(s.score <= secs * LIMIT.per + LIMIT.base, `${mode}: score ${s.score} after ${secs}s`);
        assert.ok(s.score <= LIMIT.max);
        assert.ok(s.level <= 100);
        if (secs >= 10) fastest = Math.max(fastest, s.score / secs);
      }
      maxLevel = Math.max(maxLevel, s.level);
      // at most one move every MOVE_GAP updates
      assert.ok(s.stats.moves <= Math.floor(s.updates / G.MOVE_GAP) + 1);
    }
  }
  // the proof behind the limit: tiles add at most 4 a move, and a tile worth v carries at most
  // v × (log2 v − 1) points, so score ≤ S × (log2 S − 1) with S ≤ 8 + 4 × (1 + 10 × seconds)
  for (const secs of [0, 1, 10, 60, 600, 3600, 6 * 3600]) {
    const S = 12 + 40 * secs;
    assert.ok(S * (Math.log2(S) - 1) <= secs * LIMIT.per + LIMIT.base, `bound at ${secs}s`);
  }
  const S6 = 12 + 40 * 6 * 3600;
  assert.ok(S6 * (Math.log2(S6) - 1) <= LIMIT.max, "max_score covers six hours of play");
  console.log(`merge: fastest ${fastest.toFixed(1)} a second, highest level ${maxLevel}`);
});

for (const look of LOOKS) {
  test(`merge renders and plays in the ${look} look`, () => {
    for (const mode of ["classic", "big", "small", "goals"]) {
      const sb = makeSandbox({ extra: ["merge-logic.js", "merge.js"] });
      const def = sb.win.ArcadeGames.get("merge");
      assert.equal(def.controls, "dpad");
      assert.equal(def.stateVersion, 1);
      const canvas = sb.canvas(390, 487);
      const inst = def.create(canvas, { mode, look, seed: 5 });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      for (let i = 0; i < 400 && inst.state === "running"; i++) {
        const d = ["down", "left", "down", "right", "up"][i % 5];
        if (i % 3 === 0) { inst.input(d, true); inst.input(d, false); }
        if (i % 50 === 0) { inst.pointer("down", 100, 100); inst.pointer("up", 200, 100); }
        sb.frames(1);
      }
      assert.ok(inst.logic.stats.moves > 10);
      // big values draw too
      inst.logic.grid[0] = 131072; inst.logic.grid[1] = 2048; inst.logic.grid[2] = 128;
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); inst.input("left", true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      // fill the board with no moves and let it end
      const s = inst.logic;
      for (let c = 0; c < s.grid.length; c++) s.grid[c] = 2 << ((c + Math.floor(c / s.n)) % 2) << (c % 3) * 2;
      s.dead = !sb.win.MergeLogic.anyMove(s); s.cool = s.dead ? 1 : 0;
      sb.frames(3);
      if (s.dead) assert.equal(inst.state, "over");
      for (const other of LOOKS) inst.setLook(other);
      assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  });
}

// ---- Goals (the level list) ----
const OPEN = (n) => Array.from({ length: n }, () => ".".repeat(n));

// A stronger bot for Goals: looks `depth` moves ahead (averaging over where the new tile lands), keeping
// rows and columns in order, neighbours alike and squares free. Depth 2 makes 4096 on an open 5 × 5 board.
function evalGrid(s, grid) {
  const n = s.n, L = (v) => (v ? Math.log2(v) : 0);
  let empty = 0, mono = 0, smooth = 0;
  for (let i = 0; i < grid.length; i++) if (!grid[i] && !G.isStone(s, i)) empty++;
  for (let k = 0; k < n; k++) {
    let a1 = 0, a2 = 0, b1 = 0, b2 = 0;
    for (let j = 0; j + 1 < n; j++) {
      const r0 = grid[k * n + j], r1 = grid[k * n + j + 1], c0 = grid[j * n + k], c1 = grid[(j + 1) * n + k];
      const dr = L(r0) - L(r1), dc = L(c0) - L(c1);
      if (dr > 0) a1 += dr; else a2 -= dr;
      if (dc > 0) b1 += dc; else b2 -= dc;
      if (r0 && r1) smooth -= Math.abs(dr);
      if (c0 && c1) smooth -= Math.abs(dc);
    }
    mono -= Math.min(a1, a2) + Math.min(b1, b2);
  }
  return empty * 27 + mono * 10 + smooth + L(Math.max(...grid)) * 10;
}
function lookAhead(s, grid, depth) {
  const t = { n: s.n, grid, stones: s.stones };
  let best = -Infinity, bd = null;
  for (const d of G.DIRS) {
    const p = G.plan(t, d);
    if (!p.changed) continue;
    let v;
    if (depth <= 1) v = evalGrid(s, p.grid);
    else {
      let sum = 0, cnt = 0;
      for (let i = 0; i < p.grid.length; i++) {
        if (p.grid[i] || G.isStone(s, i)) continue;
        for (const [nv, w] of [[2, 0.9], [4, 0.1]]) { const g2 = p.grid.slice(); g2[i] = nv; sum += w * lookAhead(s, g2, depth - 1)[0]; }
        cnt++;
      }
      v = cnt ? sum / cnt : evalGrid(s, p.grid);
    }
    if (v > best) { best = v; bd = d; }
  }
  return [best === -Infinity ? -1e12 : best, bd];
}
function smartBot(s, depth = 1) { if (s.cool || s.pause) return; const d = lookAhead(s, s.grid, depth)[1]; if (d) G.press(s, d, true); }

test("Goals: built-in or the session's list; bad goals are skipped", () => {
  assert.equal(G.usableLevels(undefined), G.LEVELS);
  assert.ok(G.LEVELS.length >= 5 && G.LEVELS.length <= 10);
  for (const lv of G.LEVELS) assert.ok(G.stonesOf(lv), lv.name);
  const bad = [
    { name: "Too big", size: 3, goal: 256, stones: OPEN(3) },                         // 9 free → 128 at most
    { name: "Crowded", size: 3, goal: 32, stones: ["#..", ".#.", "..#"] },            // 6 free
    { name: "Shut in", size: 4, goal: 64, stones: [".#..", "#...", "....", "...."] }, // a corner shut in
    { name: "Wall", size: 4, goal: 64, stones: ["####", "....", "....", "...."] },   // 4 in a row
    { name: "Many", size: 5, goal: 64, stones: ["#....", ".#...", "..#..", "...#.", "....."] },
    { name: "Odd goal", size: 4, goal: 100, stones: OPEN(4) },
    { name: "Wrong size", size: 4, goal: 64, stones: OPEN(3) },
    { name: "Six", size: 6, goal: 64, stones: OPEN(6) },
    { name: "Letters", size: 3, goal: 32, stones: ["..x", "...", "..."] },
    { name: "Centre", size: 3, goal: 32, stones: ["...", ".#.", "..."] },               // power 1
    { name: "Middle", size: 4, goal: 512, stones: ["....", "..#.", "....", "...."] },    // power 8: 256
    { name: "Fine", size: 3, goal: 128, stones: OPEN(3) },
    { name: "Fine too", size: 4, goal: 256, stones: ["....", "..#.", "....", "...."] },
  ];
  assert.deepEqual(G.usableLevels(bad).map((l) => l.name), ["Fine", "Fine too"]);
  assert.equal(G.usableLevels(bad.slice(0, 11)), G.LEVELS);
  assert.equal(G.goalPower(3, [0, 0, 0, 0, 0, 0, 0, 0, 0]), 7);
  assert.equal(G.goalPower(4, new Array(16).fill(0)), 12);
  assert.equal(G.goalPower(5, new Array(25).fill(0)), 19);
  assert.equal(G.goalPower(4, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0]), 13 - 4 - 3 - 2);
  assert.equal(G.usableLevels([null, 7]), G.LEVELS);
});

test("Goals: stones never move, take no tiles and stop slides", () => {
  const s = G.create({ mode: "goals", seed: 2, levels: [{ name: "Rock", size: 4, goal: 64, stones: ["....", ".#..", "....", "...."] }] });
  assert.equal(s.n, 4); assert.equal(s.goal, 64); assert.equal(s.goalName, "Rock");
  assert.ok(G.isStone(s, 5) && !G.isStone(s, 4));
  assert.equal(s.grid[5], 0);
  assert.equal(s.grid.filter(Boolean).length, 2);
  board(s, [[0, 2, 0, 0], [0, 0, 0, 2], [0, 0, 0, 0], [0, 4, 0, 0]]);
  G.press(s, "left", true);
  assert.equal(s.grid[0], 2, "row 0 slides to the edge");
  assert.equal(s.grid[6], 2, "row 1: the tile stops at the stone");
  assert.equal(s.grid[5], 0, "the stone square stays empty");
  idle(s);
  board(s, [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 4, 0, 0]]);
  G.press(s, "up", true);
  assert.equal(s.grid[9], 4, "column 1: stops below the stone");
  idle(s);
  // equal tiles on both sides of a stone don't merge through it
  board(s, [[0, 0, 0, 0], [8, 0, 8, 0], [0, 0, 0, 0], [0, 0, 0, 0]]);
  assert.ok(!G.canMove(s, "left") || G.plan(s, "left").merged.length === 0);
  // new tiles never land on a stone
  for (let i = 0; i < 300; i++) { s.grid.fill(0); G.spawn(s); assert.equal(s.grid[5], 0); }
});

test("Goals: making the goal brings the next board; the last one wins", () => {
  const two = [
    { name: "First", size: 4, goal: 32, stones: OPEN(4) },
    { name: "Second", size: 3, goal: 64, stones: ["#..", "...", "..."] },
  ];
  const s = G.create({ mode: "goals", seed: 4, levels: two });
  assert.equal(s.goals.length, 2);
  assert.equal(s.level, 1);
  board(s, [[16, 16, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]);
  let evs = G.press(s, "left", true);
  assert.deepEqual(types(evs), ["move", "merge", "goal"]);
  assert.ok(s.pause > 0);
  assert.deepEqual(G.press(s, "down", true), [], "no moves until the next board");
  evs = run(s, G.MOVE_GAP + G.GOAL_PAUSE - 1);
  assert.deepEqual(evs, []);
  // saved during the pause, continued with the same list
  const back = G.restore(JSON.parse(JSON.stringify(G.save(s))), two);
  assert.ok(!("goals" in G.save(s)));
  for (const t of [s, back]) {
    evs = run(t, 1);
    assert.deepEqual(types(evs), ["level"]);
    assert.equal(t.level, 2); assert.equal(t.n, 3); assert.equal(t.goal, 64); assert.equal(t.goalName, "Second");
    assert.equal(t.grid.length, 9); assert.equal(t.grid[0], 0); assert.ok(G.isStone(t, 0));
    assert.equal(t.grid.filter(Boolean).length, 2);
    board(t, [[0, 0, 0], [0, 0, 0], [32, 32, 0]]);
    evs = G.press(t, "left", true);
    assert.ok(types(evs).includes("goal"));
    evs = run(t, G.MOVE_GAP + G.GOAL_PAUSE);
    assert.deepEqual(types(evs), ["win"]);
    assert.ok(t.won && t.over);
    const r = G.result(t);
    assert.equal(r.stats.won, true); assert.equal(r.stats.cause, "won"); assert.equal(r.stats.goals, 2); assert.equal(r.level, 2);
  }
  assert.deepEqual(G.save(back), G.save(s));
  // getting stuck ends it, not won
  const u = G.create({ mode: "goals", seed: 5, levels: two });
  board(u, [[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 0]]);
  u.grid[15] = 0;
  G.press(u, "right", true) ;
  if (!u.dead) { G.press(u, "down", true); }
  run(u, 400);
  for (let i = 0; i < 2000 && !u.over; i++) { bot(u); G.step(u); }
  assert.ok(u.over && !u.won);
  assert.equal(G.result(u).stats.cause, "stuck");
  // a bad list falls back to the built-ins; old saves without the goal fields still restore
  const v = G.create({ mode: "goals", seed: 6, levels: [{ name: "Bad", size: 9, goal: 3, stones: [] }] });
  assert.equal(v.goals, G.LEVELS);
  assert.equal(v.goalName, G.LEVELS[0].name);
  assert.equal(G.restore(JSON.parse(JSON.stringify(G.save(v)))).goals, G.LEVELS);
  const old = G.save(G.create({ mode: "classic", seed: 7 }));
  for (const k of ["goals", "stones", "goal", "goalName", "pause"]) delete old[k];
  delete old.stats.goals; delete old.stats.cause;
  const o = G.restore(old);
  assert.equal(o.stones, null); assert.equal(o.pause, 0);
  for (let i = 0; i < 200 && !o.over; i++) { bot(o); G.step(o); }
  assert.ok(o.stats.moves > 10);
  assert.throws(() => G.restore(Object.assign(G.save(s), { stones: [1] })), /can't be continued/);
});

test("Goals: deterministic; a restored game plays on identically", () => {
  const play = (seed) => { const s = G.create({ seed, mode: "goals" }); for (let i = 0; i < 3000 && !s.over; i++) { smartBot(s); G.step(s); } return s; };
  assert.deepEqual(play(9), play(9));
  const s = G.create({ mode: "goals", seed: 10 });
  for (let i = 0; i < 600; i++) { smartBot(s); G.step(s); }
  assert.ok(!s.over);
  const copy = G.restore(JSON.parse(JSON.stringify(G.save(s))));
  for (let i = 0; i < 6000 && !s.over; i++) { smartBot(s); smartBot(copy); G.step(s); G.step(copy); }
  assert.deepEqual(G.save(copy), G.save(s));
  assert.ok(s.level >= 2, "the bot gets past the first goals");
});

test("Goals: a frame-perfect bot stays inside the limits on the hardest goals allowed", () => {
  let fastest = 0, most = 0, bestTile = 0;
  const lists = [
    // the biggest board and goal: the most points a board can carry
    Array.from({ length: 30 }, (_, i) => ({ name: "Huge " + i, size: 5, goal: 4096, stones: OPEN(5) })),
    // many quick goals: the most boards a minute
    Array.from({ length: 200 }, (_, i) => ({ name: "Quick " + i, size: 4, goal: 32, stones: OPEN(4) })),
    // stones everywhere they may be
    Array.from({ length: 40 }, (_, i) => ({ name: "Rocky " + i, size: 5, goal: 2048, stones: ["#....", "..#..", "....#", ".....", "....."] })),
    // the hardest small board
    Array.from({ length: 40 }, (_, i) => ({ name: "Snug " + i, size: 3, goal: 128, stones: OPEN(3) })),
  ];
  const runs = lists.map((list) => [list, 1]).concat([[lists[0].slice(0, 2), 2], [lists[3], 2]]);
  for (const [list, depth] of runs) {
    assert.equal(G.usableLevels(list).length, list.length);
    for (const seed of depth === 1 ? [1, 2] : [3]) {
      const s = G.create({ mode: "goals", seed, levels: list });
      let u = 0;
      while (!s.over && u++ < 60 * 60 * 60) {
        smartBot(s, depth);
        G.step(s);
        const secs = s.updates / 60;
        assert.ok(s.score <= secs * LIMIT.per + LIMIT.base, `score ${s.score} after ${secs}s`);
        assert.ok(s.score <= LIMIT.max);
        assert.ok(s.level <= list.length);
        if (secs >= 10) fastest = Math.max(fastest, s.score / secs);
      }
      most = Math.max(most, s.stats.goals);
      bestTile = Math.max(bestTile, s.best);
      assert.ok(s.stats.moves <= Math.floor(s.updates / G.MOVE_GAP) + 1);
    }
  }
  // the proof: on one board tiles add ≤ 4 a move and stay under 25 × 4096 before the goal is made,
  // so a board scores ≤ S × (log2 S − 1) ≤ (8 + 4m) × 15.7 for m moves (≥ 6 updates each, and ≥ 6 moves
  // to reach 32), and a new board waits GOAL_PAUSE updates
  for (const m of [6, 10, 100, 1000, 25600]) {
    const S = Math.min(25 * 4096, 8 + 4 * m);
    const secs = (m * G.MOVE_GAP + G.GOAL_PAUSE) / 60;
    assert.ok(S * (Math.log2(S) - 1) <= secs * LIMIT.per, `a board of ${m} moves`);
  }
  console.log(`merge goals: fastest ${fastest.toFixed(1)} a second, most goals ${most}, best tile ${bestTile}`);
});
