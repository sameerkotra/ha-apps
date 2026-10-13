"use strict";
// The 13 October review's browser-side fixes: the board games' computer thinks within a budget (the same move as the
// full search whenever the budget is enough) and can think elsewhere (a worker) with exactly the same game; a match
// picture adds only the new moves; Chess asks again when its thinker fails; Sudoku's undo history stays bounded; the
// keyboard cursor never rests on Mines' holes or Mole's grass; Tower Stack's camera is the same at any refresh rate;
// the two-phone leftovers are gone.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { makeSandbox, loadLogic, GAMES_DIR } = require("./helpers");

const BK = require(path.join(GAMES_DIR, "boardkit.js"));
const plain = (v) => JSON.parse(JSON.stringify(v));

function rng(seed) { let s = seed >>> 0 || 1; return () => { s = (s + 0x6D2B79F5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

/** Positions part-way through games played at random (seat to move, state). */
function positions(L, mode, n, seed) {
  const r = rng(seed), out = [];
  for (let g = 0; out.length < n && g < n * 4; g++) {
    const s = L.create({ mode, seed: seed + g });
    const plies = Math.floor(r() * 30);
    for (let k = 0; k < plies && !s.over; k++) {
      const seat = L.toMove(s)[0], ms = L.moves(s, seat);
      if (!ms.length) break;
      L.force(s, ms[Math.floor(r() * ms.length)]);
    }
    if (!s.over && L.toMove(s).length) out.push(s);
  }
  return out;
}

test("Hard thinks within a budget: the full search's move whenever the budget is enough", () => {
  for (const [file, budgetName] of [["fourrow-logic.js", "fourrow"], ["reversi-logic.js", "reversi"], ["checkers-logic.js", "checkers"]]) {
    const L = loadLogic(file);
    let compared = 0;
    for (const s of positions(L, "hard", 25, 11)) {
      const seat = L.toMove(s)[0];
      const a = plain(s), b = plain(s);
      const mine = L.R.ai(a, seat, 3);
      const spent = BK.meter.n;
      const real = BK.deepen;
      BK.deepen = function (depths, budget, run) { return run(depths[depths.length - 1]); };     // the old way: full depth at once
      let full;
      try { full = L.R.ai(b, seat, 3); } finally { BK.deepen = real; }
      if (spent <= L.NODE_BUDGET) {
        assert.deepEqual(plain(mine), plain(full), budgetName);
        assert.equal(a.rng, b.rng);
        compared++;
      }
      assert.ok(L.legal(plain(s), seat, mine), budgetName + ": always a legal move");
    }
    assert.ok(compared >= 10, budgetName + ": compared " + compared);
  }
});

test("a search out of budget keeps the deepest answer it finished", () => {
  const order = [];
  const out = BK.deepen([1, 2, 3], 7, (d) => { order.push(d); for (let i = 0; i < d * 2; i++) BK.spend(); return "depth " + d; });
  assert.deepEqual(order, [1, 2, 3]);
  assert.equal(out, "depth 2");
  assert.equal(BK.meter.max, Infinity, "outside a search nothing is counted");
  assert.equal(BK.deepen([1], 0, () => { BK.spend(); return 1; }), null);
  assert.throws(() => BK.deepen([1], 9, () => { throw new Error("a real error"); }), /a real error/);
});

test("thinking elsewhere plays exactly the same game", () => {
  for (const file of ["fourrow-logic.js", "reversi-logic.js", "checkers-logic.js"]) {
    const L = loadLogic(file);
    const here = L.create({ mode: "hard", seed: 5 }), there = L.create({ mode: "hard", seed: 5 });
    const asked = [];
    L.setThinker((job, done) => { asked.push(job); const s = plain(job.s); done({ move: L.R.ai(s, job.seat, job.level), rng: s.rng }); });
    try {
      const r = rng(3);
      for (let turn = 0; turn < 12 && !here.over; turn++) {
        // the person moves (the same random legal move on both), then the computer, in its own time
        const pick = r();
        for (const s of [here, there]) {
          if (s.over || L.toMove(s)[0] !== 0) continue;
          const ms = L.moves(s, 0);
          L.play(s, 0, ms[Math.floor(pick * ms.length)]);
        }
        L.setThinker(null);
        for (let k = 0; k < 400 && !here.over && L.toMove(here)[0] === 1; k++) L.step(here);
        L.setThinker((job, done) => { asked.push(job); const s = plain(job.s); done({ move: L.R.ai(s, job.seat, job.level), rng: s.rng }); });
        for (let k = 0; k < 400 && !there.over && L.toMove(there)[0] === 1; k++) L.step(there);
        assert.deepEqual(plain(L.digest(there)), plain(L.digest(here)), file);
        assert.equal(there.rng, here.rng);
      }
      assert.ok(asked.length > 0);
    } finally { L.setThinker(null); }
  }
});

test("a late answer for a position that has gone is dropped, and a failed one is asked again", () => {
  const L = loadLogic("fourrow-logic.js");
  const s = L.create({ mode: "hard", seed: 2 });
  L.play(s, 0, L.moves(s, 0)[3]);
  const jobs = [];
  L.setThinker((job, done) => jobs.push(done));
  try {
    for (let k = 0; k < 100; k++) L.step(s);
    assert.equal(jobs.length, 1, "asked once while it waits");
    assert.equal(L.toMove(s)[0], 1);
    jobs[0](null);                                           // the worker failed
    L.step(s);
    assert.equal(jobs.length, 2, "asked again");
    const restored = L.restore(L.save(s));                   // a saved game: the question is asked again
    assert.equal(restored.aiAsked, null);
    jobs[1]({ move: L.moves(s, 1)[0], rng: s.rng });
    L.step(s);
    assert.equal(L.toMove(s)[0], 0, "the answer was played");
  } finally { L.setThinker(null); }
});

test("Chess asks its thinker again when an answer doesn't come", () => {
  const C = loadLogic("chess-logic.js");
  const calls = [];
  C.setThinker((job, done) => calls.push(done));
  try {
    const s = C.create({ mode: "hard", seed: 2, options: { side: "black" } });     // the computer plays White
    for (let k = 0; k < 60; k++) C.step(s);
    assert.equal(calls.length, 1);
    calls[0](null);
    for (let k = 0; k < 5; k++) C.step(s);
    assert.equal(calls.length, 2, "asked again, not stuck");
  } finally { C.setThinker(null); }
});

test("a match picture adds only the new moves: the same state as replaying them all", () => {
  for (const file of ["tictactoe-logic.js", "fourrow-logic.js", "checkers-logic.js", "reversi-logic.js", "dots-logic.js", "chess-logic.js"]) {
    const L = loadLogic(file);
    const ref = L.create({ mode: "phones", seed: 9, seat: 0 }), list = [];
    const r = rng(17);
    for (let k = 0; k < 24 && !ref.over; k++) {
      const seat = L.toMove(ref)[0], ms = L.moves(ref, seat);
      if (!ms.length) break;
      const m = ms[Math.floor(r() * ms.length)];
      L.force(ref, m);
      list.push({ n: list.length + 1, seat: seat + 1, move: m });
    }
    let s = L.fromTurns({ seed: 9, seat: 1, moves: [], number: 0 });
    const applied = [];
    const realApply = L.R.apply;
    let counting = false;
    L.R.apply = function () { if (counting) applied.push(1); return realApply.apply(this, arguments); };
    try {
      for (let k = 1; k <= list.length; k++) {
        const p = { seed: 9, seat: 1, moves: list.slice(0, k), number: k };
        counting = true;
        s = L.sync(s, p).s;
        counting = false;
        const full = L.fromTurns(p);
        assert.deepEqual(plain(L.digest(s)), plain(L.digest(full)), file + " after " + k);
        assert.equal(s.number, k);
      }
    } finally { L.R.apply = realApply; }
    assert.equal(applied.length, list.length, file + ": each move played once, not every move again each time");
  }
});

test("Sudoku's undo history stays at most HISTORY_MAX steps", () => {
  const S = loadLogic("sudoku-logic.js");
  const s = S.create({ mode: "easy", seed: 4 });
  const free = [];
  for (let i = 0; i < 81; i++) if (!s.fixed[i]) free.push(i);
  for (let k = 0; k < 1200; k++) {
    s.sel = free[k % free.length];
    S.press(s, "n" + (1 + (k % 9)), true);
    S.press(s, "erase", true);
  }
  assert.ok(s.history.length <= 400, `history ${s.history.length}`);
  assert.ok(s.history.length > 0);
});

test("the keyboard cursor never rests on Mines' holes or Mole's grass", () => {
  const M = loadLogic("mines-logic.js");
  for (let level = 1; level <= M.LEVELS.length; level++) {
    const s = M.create({ mode: "boards", seed: 3, startLevel: level });
    const r = rng(level);
    M.press(s, "up", true);                                   // shows the cursor
    for (let k = 0; k < 200; k++) {
      M.press(s, ["up", "down", "left", "right"][Math.floor(r() * 4)], true);
      assert.ok(!M.isHole(s, s.cur.y * s.cols + s.cur.x), `level ${level}: on a hole`);
    }
  }
  const W = loadLogic("mole-logic.js");
  for (let level = 1; level <= W.LEVELS.length; level++) {
    const s = W.create({ mode: "gardens", seed: 3, startLevel: level });
    const r = rng(level + 50);
    for (let k = 0; k < 200; k++) {
      W.press(s, ["up", "down", "left", "right"][Math.floor(r() * 4)], true);
      assert.ok(W.isHole(s, s.cur), `garden ${level}: on grass`);
    }
  }
});

test("Tower Stack's camera moves the same on a 60 Hz and a 120 Hz screen", () => {
  function run(hz) {
    const sb = makeSandbox();
    const def = sb.win.ArcadeGames.get("stack");
    const inst = def.create(sb.canvas(), { mode: def.defaultMode, seed: 6, onEnd() {}, onScore() {}, onEvent() {} });
    inst.start();
    const per = hz / 60, out = [];
    for (let k = 0; k < 900 && inst.state === "running"; k++) {
      if (k % 50 === 25) { inst.input("fire", true); inst.input("fire", false); }
      sb.frames(per, 1000 / hz);
      if (k % 30 === 0) out.push(Math.round(inst.view.camY * 1000));
    }
    inst.destroy();
    return out;
  }
  assert.deepEqual(run(120), run(60));
});

test("the two-phone leftovers are gone (decided on 2026-10-06)", () => {
  const D = loadLogic("duel-logic.js"), T = loadLogic("tanks-logic.js"), S = loadLogic("snakeduel-logic.js"), C = loadLogic("carrom-logic.js");
  for (const L of [D, T, S, C]) { assert.equal(L.checksum, undefined); assert.equal(L.report, undefined); }
  assert.deepEqual(Object.keys(T.MODES), ["classic", "easy"]);
  assert.deepEqual(plain(S.MODES), ["cpu", "two"]);
  assert.ok(!C.MODES.some((m) => m.id === "phones"));
  assert.equal(D.create({ mode: "phones", seed: 1 }).mode, "normal");
});
