"use strict";
// Gem Swap (wave 5): lines, swapping back, cascades, special gems, stars, locks, shuffling, the clock and the
// Moves levels, saving, the honest-score limits, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const G = loadLogic("gems-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const EXTRA = ["gems-logic.js", "gems.js"];
const LIMIT = { perSecond: 5500, base: 3000, max: 50000000 };
const EMPTY = ["........", "........", "........", "........", "........", "........", "........", "........"];

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...G.step(s)); } return all; }
function settle(s, max = 600) { const all = []; for (let i = 0; i < max && !s.over && s.phase !== "idle"; i++) all.push(...G.step(s)); return all; }
function lvl(extra) { return Object.assign({ name: "Test", moves: 20, goal: 600, kinds: 4, locks: EMPTY.slice() }, extra || {}); }
/** A board from 8 strings of kind digits (no lines unless asked for). */
function board(s, rows) {
  let id = 1000;
  s.board = [];
  for (const row of rows) for (const ch of row) s.board.push({ k: Number(ch), sp: 0, lock: false, id: ++id, fy: 0 });
  s.phase = "idle";
}
const BASE = ["01230123", "12301230", "23012301", "30123012", "01230123", "12301230", "23012301", "30123012"];
// The move that clears the most right now (and breaks locks), made as soon as the board is still.
function best(s) {
  const b = s.board; let bm = null, bv = -1;
  for (let i = 0; i < 64; i++) for (const j of [i + 1, i + 8]) {
    if ((j === i + 1 && i % 8 === 7) || j >= 64 || !G.canSwap(b, i, j)) continue;
    let v;
    if (b[i].sp === G.STAR || b[j].sp === G.STAR) v = 100;
    else { const t = b[i]; b[i] = b[j]; b[j] = t; v = G.findRuns(b).reduce((a, r) => a + r.cells.length ** 2 + r.cells.filter((c) => b[c].lock).length * 40, 0); b[j] = b[i]; b[i] = t; }
    if (v > bv) { bv = v; bm = [i, j]; }
  }
  return bv > 0 ? bm : null;
}
function bot(s) { if (s.phase !== "idle") return; const m = best(s); if (m) { G.tap(s, m[0]); G.tap(s, m[1]); } }

test("a fresh board has no lines and at least one move; Timed has a 90 s clock", () => {
  for (let seed = 1; seed <= 20; seed++) {
    const s = G.create({ seed });
    assert.equal(G.findRuns(s.board).length, 0, `seed ${seed}`);
    assert.ok(G.findMove(s.board));
    assert.ok(s.board.every((g) => g.k >= 0 && g.k < 6 && g.sp === 0));
  }
  assert.equal(G.create({ seed: 1 }).time, G.TIMED);
  assert.equal(G.create({ seed: 1, mode: "zen" }).time, 0);
  assert.ok(G.create({ seed: 1, mode: "moves" }).board.every((g) => g.k < G.LEVELS[0].kinds));
});

test("a swap that makes a line clears it, gems fall and new ones drop in; one that doesn't swaps back", () => {
  const s = G.create({ seed: 2, mode: "zen" });
  board(s, BASE);
  s.board[G.idx(0, 2)].k = 0; s.board[G.idx(0, 1)].k = 0;     // 0 0 0? not yet: row 0 is 0 0 0 3 … after the swap below
  s.board[G.idx(0, 2)].k = 2; s.board[G.idx(1, 2)].k = 0;     // row 0: 0 0 2 …, below the 2 a 0
  const nope = G.create({ seed: 2, mode: "zen" }); board(nope, BASE);
  let evs = G.tap(nope, G.idx(5, 5)).concat(G.tap(nope, G.idx(5, 6)));
  assert.ok(types(evs).includes("swap"));
  evs = settle(nope);
  assert.ok(types(evs).includes("nope"));
  assert.equal(nope.board[G.idx(5, 5)].k, Number(BASE[5][5]), "swapped back");
  assert.equal(nope.score, 0);
  evs = G.tap(s, G.idx(0, 2)).concat(G.tap(s, G.idx(1, 2)));
  evs = evs.concat(settle(s));
  assert.ok(types(evs).includes("match"), JSON.stringify(types(evs)));
  assert.ok(s.score >= 3 * G.GEM_POINTS);
  assert.ok(s.board.every((g) => g && g.fy === 0), "every square full again");
  assert.equal(s.stats.swaps, 1);
  // only neighbours, and not while gems are moving
  const t = G.create({ seed: 3 });
  assert.equal(types(G.tap(t, 0).concat(G.tap(t, 9))).includes("swap"), false, "not diagonal");
  assert.equal(t.sel, 9, "the second tap picks the new gem instead");
});

test("four in a line leaves a line gem, an L a blast gem, five a star; each goes off when cleared", () => {
  const s = G.create({ seed: 4, mode: "zen" });
  board(s, BASE);
  // row 3: 3 0 1 2 … → make 1 1 1 1 at columns 0-3 by placing 1s and swapping one in
  for (const c of [0, 1, 3]) s.board[G.idx(3, c)].k = 1;
  s.board[G.idx(3, 2)].k = 2; s.board[G.idx(2, 2)].k = 1;
  G.tap(s, G.idx(2, 2)); G.tap(s, G.idx(3, 2));
  const evs = run(s, G.SWAP_T + 1);
  assert.ok(types(evs).includes("special"));
  assert.equal(s.make.length, 1);
  assert.equal(s.make[0].sp, G.LINE_H, "a horizontal four clears its row later");
  assert.equal(s.make[0].at, G.idx(3, 2), "where the swapped gem went");
  settle(s);
  const line = s.board.findIndex((g) => g.sp === G.LINE_H);
  assert.ok(line >= 0);
  // a line gem going off takes its whole row
  const before = s.stats.gems;
  s.phase = "idle";
  const row = Math.floor(line / 8);
  // put two of its kind next to it so it clears
  const k = s.board[line].k, c = line % 8, at = c <= 5 ? [line + 1, line + 2] : [line - 1, line - 2];
  for (const i of at) s.board[i] = { k, sp: 0, lock: false, id: 9000 + i, fy: 0 };
  for (let cc = 0; cc < 8; cc++) if (!at.includes(G.idx(row, cc)) && G.idx(row, cc) !== line && s.board[G.idx(row, cc)].k === k) s.board[G.idx(row, cc)].k = (k + 1) % 4;
  s.chain = 0;
  s.phase = "fall";                                   // the next update finds the line
  run(s, 1);
  assert.ok(s.dying.length >= 8, `a whole row: ${s.dying.length}`);
  settle(s);
  assert.ok(s.stats.gems - before >= 8);
  // an L shape: a blast gem; five: a star
  const t = G.create({ seed: 5, mode: "zen" }); board(t, BASE);
  for (const [r, cc] of [[0, 0], [0, 1], [0, 2], [1, 0], [2, 0]]) t.board[G.idx(r, cc)].k = 2;
  t.phase = "fall"; run(t, 1);
  assert.deepEqual(t.make.map((m) => m.sp), [G.BLAST]);
  const u = G.create({ seed: 5, mode: "zen" }); board(u, BASE);
  for (let cc = 0; cc < 5; cc++) u.board[G.idx(7, cc)].k = 3;
  u.phase = "fall"; run(u, 1);
  assert.deepEqual(u.make.map((m) => m.sp), [G.STAR]);
  settle(u);
  const star = u.board.findIndex((g) => g.sp === G.STAR);
  assert.ok(star >= 0);
  // swap the star with a neighbour: every gem of that kind goes
  const nb = star % 8 < 7 ? star + 1 : star - 1, kind = u.board[nb].k;
  const count = u.board.filter((g) => g.k === kind).length;
  u.phase = "idle";
  G.tap(u, star); G.tap(u, nb);
  run(u, G.SWAP_T + 1);
  assert.ok(u.dying.length >= count, `${u.dying.length} >= ${count}`);
});

test("cascades count up and score more; the chain is shown and capped", () => {
  let found = null;
  for (let seed = 1; seed <= 60 && !found; seed++) {
    const s = G.create({ seed, mode: "zen" });
    for (let i = 0; i < 60 * 60 && !found; i++) { bot(s); const evs = G.step(s); if (types(evs).includes("cascade")) found = { s, evs }; }
  }
  assert.ok(found, "a cascade happens in normal play");
  assert.ok(found.s.chain >= 2);
  assert.ok(found.s.stats.bestChain >= 2);
});

test("locks: a locked gem can't be moved; a line through it breaks the lock", () => {
  const s = G.create({ seed: 6, mode: "moves", levels: [lvl({ locks: ["L.......", "........", "........", "........", "........", "........", "........", "........"] })] });
  assert.equal(s.locksLeft, 1);
  assert.ok(s.board[0].lock);
  assert.equal(G.canSwap(s.board, 0, 1), false);
  assert.equal(types(G.tap(s, 0)).includes("pick"), false);
  board(s, BASE); s.locksLeft = 1;
  s.board[0].lock = true; s.board[1].k = 0; s.board[2].k = 0;
  s.phase = "fall"; run(s, 1);
  assert.equal(s.locksLeft, 0);
  assert.equal(s.stats.locks, 1);
});

test("no move left: the board is shuffled (locks stay where they are)", () => {
  const s = G.create({ seed: 7, mode: "zen" });
  const stuck = ["01230123", "45014501", "23452345", "01230123", "45014501", "23452345", "01230123", "45014501"];
  board(s, stuck);
  s.board[G.idx(4, 4)].lock = true;
  assert.equal(G.findMove(s.board), null);
  s.phase = "fall";
  const evs = run(s, 2);
  assert.ok(types(evs).includes("shuffle"));
  assert.ok(G.findMove(s.board));
  assert.equal(G.findRuns(s.board).length, 0);
  assert.ok(s.board[G.idx(4, 4)].lock, "the lock is kept");
});

test("Timed ends when the clock runs out (after the board settles); Zen never ends", () => {
  const s = G.create({ seed: 8 });
  s.time = 3;
  const evs = run(s, 10);
  assert.ok(s.over);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(G.result(s).stats.cause, "time");
  const z = G.create({ seed: 8, mode: "zen" });
  run(z, 60 * 200, bot);
  assert.ok(!z.over);
});

test("Moves: the level list; reach the goal and break the locks within the moves; moves left score; the last wins", () => {
  assert.equal(G.usableLevels(undefined), G.LEVELS);
  const bad = [lvl({ moves: 9 }), lvl({ moves: 41 }), lvl({ goal: 299 }), lvl({ goal: 1700 }), lvl({ kinds: 3 }), lvl({ kinds: 7 }),
    lvl({ locks: EMPTY.slice(0, 7) }), lvl({ locks: EMPTY.map((r, i) => (i ? r : "......L.X")) }),
    lvl({ moves: 10, locks: ["LLLLLL..", ...EMPTY.slice(1)] }), lvl({ moves: 40, goal: 3000, locks: ["LLLLLLLL", "LLLLLLLL", "L.......", ...EMPTY.slice(3)] }),
    lvl({ name: "" }), null, 2];
  assert.equal(G.usableLevels(bad), G.LEVELS);
  assert.deepEqual(G.usableLevels(bad.concat([lvl({ name: "Fine" })])).map((l) => l.name), ["Fine"]);
  for (const l of G.LEVELS) { assert.equal(G.levelProblem(l), null, l.name); assert.equal(G.usableLevels([l]).length, 1, l.name); }
  assert.equal(G.create({ mode: "timed", levels: [lvl()] }).list, null);
  const two = [lvl({ name: "A", goal: 300 }), lvl({ name: "B", goal: 300, kinds: 5 })];
  const s = G.create({ mode: "moves", seed: 3, levels: two });
  const all = run(s, 60 * 600, bot);
  assert.ok(s.won, JSON.stringify(G.result(s)));
  assert.ok(types(all).includes("clear"));
  assert.ok(types(all).includes("level"));
  assert.ok(types(all).includes("win"));
  assert.equal(G.result(s).stats.levels, 2);
  // running out of moves ends it
  const f = G.create({ mode: "moves", seed: 3, levels: [lvl({ goal: 1600 })] });
  f.moves = 1;
  run(f, 60 * 30, bot);
  assert.ok(f.over && !f.won);
  assert.equal(G.result(f).stats.cause, "moves");
  const data = JSON.parse(JSON.stringify(G.save(G.create({ mode: "moves", seed: 1, levels: two }))));
  assert.ok(!("list" in data));
  assert.equal(G.restore(data, two).list.length, 2);
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed) => { const s = G.create({ seed, mode: "zen" }); run(s, 60 * 60, bot); return s; };
  assert.equal(JSON.stringify(play(9)), JSON.stringify(play(9)));
  assert.notEqual(JSON.stringify(play(9).board), JSON.stringify(play(10).board));
  const s = G.create({ seed: 12 });
  run(s, 1000, bot);
  const copy = G.restore(JSON.parse(JSON.stringify(G.save(s))));
  for (let i = 0; i < 2000 && !s.over; i++) { bot(s); bot(copy); G.step(s); G.step(copy); }
  assert.equal(JSON.stringify(G.save(copy)), JSON.stringify(G.save(s)));
  assert.throws(() => G.restore({ board: [] }), /can't be continued/);
  assert.throws(() => G.restore(null));
});

test("honest score: the quick player stays inside the limits; the most a step can pay is inside them too", () => {
  let top = 0;
  for (const [mode, levels] of [["timed"], ["zen"], ["moves"], ["moves", [lvl({ name: "Easy", moves: 40, goal: 300, kinds: 4 })]]]) {
    for (const seed of [1, 2]) {
      const s = G.create({ seed, mode, levels });
      while (!s.over && s.updates < 60 * 60 * 5) {
        bot(s); G.step(s);
        const sec = s.updates / 60;
        assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
        assert.ok(s.score <= LIMIT.max && s.level <= 100);
        if (sec > 5) top = Math.max(top, s.score / sec);
      }
    }
  }
  // the most a clearing step can pay: the whole board at the top cascade, each gem part of a star made,
  // over the clear and the fall that refills eight rows
  const step = 64 * (G.GEM_POINTS * G.MAX_CHAIN + G.STAR_BONUS / 5);
  const updates = G.CLEAR_T + 8 * G.CELL / G.FALL_V;
  assert.ok(step / updates * 60 <= LIMIT.perSecond, `${(step / updates * 60).toFixed(0)}`);
  console.log(`# gems: quick player's fastest rate ${top.toFixed(1)}; the rules' most ${(step / updates * 60).toFixed(0)} points a second`);
});

test("fuzz: random taps, drags and keys never throw and keep the board sound", () => {
  for (const mode of ["timed", "moves", "zen"]) {
    for (let seed = 1; seed <= 8; seed++) {
      const s = G.create({ seed, mode });
      let x = seed * 48271, last = 0;
      for (let i = 0; i < 4000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 5 === 0) G.tap(s, (x >> 4) % 70 - 3);
        else if (x % 7 === 0) G.drag(s, (x >> 4) % 64, ["up", "down", "left", "right"][x % 4]);
        else if (x % 11 === 0) G.press(s, ["up", "down", "left", "right", "fire", "alt"][x % 6], true);
        G.step(s);
        assert.ok(Number.isFinite(s.score) && s.score >= last);
        last = s.score;
        assert.equal(s.board.length, 64);
        if (s.phase === "idle") {
          assert.ok(s.board.every((g) => g && g.fy === 0), "a still board is full");
          assert.equal(G.findRuns(s.board).length, 0, "and has no lines left");
        }
        assert.ok(s.locksLeft >= 0 && s.level >= 1 && s.level <= 100 && s.cur >= 0 && s.cur < 64);
      }
    }
  }
});

test("gems renders and plays in every look, with taps, drags and keys", () => {
  for (const look of LOOKS) {
    for (const mode of ["timed", "moves"]) {
      const sb = makeSandbox({ extra: EXTRA });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("gems");
      assert.equal(def.name, "Gem Swap");
      assert.equal(def.controls, "touch");
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      for (let i = 0; i < 900; i++) {
        bot(inst.logic);
        if (i % 41 === 0) { inst.pointer("down", 120, 200); inst.pointer("move", 150, 200); inst.pointer("up", 150, 200); }
        if (i % 29 === 0) { inst.input("fire", true); inst.input("right", true); inst.input("alt", true); }
        if (i === 300) { const b = inst.logic.board; b[0].sp = 4; b[0].k = -1; b[9].sp = 1; b[18].sp = 2; b[27].sp = 3; b[36].lock = true; }
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
