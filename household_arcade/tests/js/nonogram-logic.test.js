"use strict";
// Picture Logic (nonograms, wave 6): the line solver, unique puzzles from the seed, filling and crossing, dragging,
// mistakes, hints, undo, the score, saving, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const N = loadLogic("nonogram-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const { UNKNOWN: U, FILL: F, CROSS: X } = N;

function types(evs) { return evs.map((e) => e.type); }
/** Count the answers of a small puzzle by trying every grid (5 × 5: 2^25 is too many — so a backtracking count). */
function countAnswers(rows, cols, n, limit) {
  let count = 0;
  const grid = [];
  function rowsOk(r) {
    const line = grid.slice(r * n, r * n + n);
    const got = N.runs(line, 1);
    return JSON.stringify(got) === JSON.stringify(rows[r]);
  }
  function colsOk() {
    for (let c = 0; c < n; c++) {
      const col = [];
      for (let r = 0; r < n; r++) col.push(grid[r * n + c]);
      if (JSON.stringify(N.runs(col, 1)) !== JSON.stringify(cols[c])) return false;
    }
    return true;
  }
  // every row pattern that matches its clue
  const patterns = rows.map((clue) => {
    const out = [];
    for (let m = 0; m < 1 << n; m++) {
      const line = [];
      for (let i = 0; i < n; i++) line.push(m >> i & 1);
      if (JSON.stringify(N.runs(line, 1)) === JSON.stringify(clue)) out.push(line);
    }
    return out;
  });
  (function rec(r) {
    if (count >= limit) return;
    if (r === n) { if (colsOk()) count++; return; }
    for (const p of patterns[r]) { grid.length = r * n; grid.push(...p); rec(r + 1); }
  })(0);
  return count;
}
function solveVia(s) {
  // fill every square of the answer the way a player would (taps)
  for (let i = 0; i < s.cells.length && !s.over; i++) if (s.sol[i] === "1" && s.cells[i] !== F) N.tap(s, i, false);
}

test("runs(): the clue of a line", () => {
  assert.deepEqual(N.runs([1, 1, 0, 1, 0, 0, 1, 1, 1], 1), [2, 1, 3]);
  assert.deepEqual(N.runs([0, 0, 0], 1), []);
  assert.deepEqual(N.runs([1, 1, 1], 1), [3]);
});

test("solveLine(): what the numbers force, and contradictions", () => {
  // 3 in 5: the middle square is surely filled
  assert.deepEqual(N.solveLine([3], [U, U, U, U, U]), [U, U, F, U, U]);
  // 5 in 5: all filled; nothing: all crossed
  assert.deepEqual(N.solveLine([5], [U, U, U, U, U]), [F, F, F, F, F]);
  assert.deepEqual(N.solveLine([], [U, U, U]), [X, X, X]);
  // 2 1 in 4: exactly one way
  assert.deepEqual(N.solveLine([2, 1], [U, U, U, U]), [F, F, X, F]);
  // a known cross splits the line
  assert.deepEqual(N.solveLine([2], [U, X, U, U]), [X, X, F, F]);
  // a filled square next to the edge with clue 2: the square after it is filled
  assert.deepEqual(N.solveLine([2], [F, U, U, U]), [F, F, X, X]);
  assert.equal(N.solveLine([4], [U, X, U, U]), null);
  assert.equal(N.solveLine([1], [F, U, F]), null);
});

test("generated puzzles have exactly one answer and the line solver finds it (checked by counting on 5 × 5)", () => {
  for (let seed = 1; seed <= 25; seed++) {
    const g = N.generate("five", seed);
    const sol = g.sol;
    const cells = sol.map(() => U);
    g.given.forEach((i) => { cells[i] = sol[i] ? F : X; });
    const r = N.solveGrid(g.rows, g.cols, cells, 5);
    assert.equal(r.unknown, 0);
    assert.deepEqual(cells.map((v) => (v === F ? 1 : 0)), sol);
    if (!g.given.length) assert.equal(countAnswers(g.rows, g.cols, 5, 2), 1, `seed ${seed}`);
  }
  for (const mode of ["eight", "ten", "fifteen"]) {
    for (let seed = 1; seed <= 15; seed++) {
      const g = N.generate(mode, seed), n = N.MODES[mode].n;
      assert.equal(g.n, n);
      const cells = g.sol.map(() => U);
      g.given.forEach((i) => { cells[i] = g.sol[i] ? F : X; });
      assert.equal(N.solveGrid(g.rows, g.cols, cells, n).unknown, 0, `${mode} ${seed} solved by lines`);
      assert.deepEqual(cells.map((v) => (v === F ? 1 : 0)), g.sol);
      const filled = g.sol.reduce((a, b) => a + b, 0);
      assert.ok(filled >= n * n * 0.3 && filled <= n * n * 0.75);
      if (mode === "fifteen") assert.ok(g.rows.concat(g.cols).every((c) => c.every((v) => v <= 9)), "15 × 15: one-digit numbers only");
    }
  }
});

test("filling the picture wins; the score is TOP − the counted seconds; unsolved scores 0", () => {
  const s = N.create({ mode: "five", seed: 3 });
  assert.equal(N.result(s).score, 0);
  for (let i = 0; i < 600; i++) N.step(s);
  solveVia(s);
  assert.ok(s.won && s.over);
  assert.equal(N.result(s).score, N.TOP - 10);
  assert.equal(N.result(s).stats.won, true);
  assert.deepEqual(N.result(s).stats.summary, ["Time 0:10", "Counted time 0:10"]);
  assert.deepEqual(N.tap(s, 0), [], "nothing after the end");
});

test("mistakes shown at once: a wrong square is put right (+10 s) and fixed", () => {
  const s = N.create({ mode: "five", seed: 4 });
  const empty = s.sol.indexOf("0"), full = s.sol.indexOf("1");
  let evs = N.tap(s, empty, false);
  assert.deepEqual(types(evs), ["wrong"]);
  assert.equal(s.cells[empty], X);
  assert.ok(s.fixed[empty]);
  assert.equal(s.mistakes, 1);
  assert.equal(s.fixed[full], false);
  evs = N.tap(s, full, true);
  assert.deepEqual(types(evs), ["wrong"], "a cross on a filled square is a mistake too");
  assert.equal(s.cells[full], F);
  assert.equal(N.effective(s), 20);
});

test("mistakes shown at the end: nothing is said until the grid has enough squares, then the wrong ones are marked", () => {
  const s = N.create({ mode: "five", seed: 4, mistakes: "end" });
  const empty = s.sol.indexOf("0");
  N.tap(s, empty, false);
  assert.equal(s.cells[empty], F, "kept, no word");
  assert.equal(s.mistakes, 0);
  let evs = [];
  for (let i = 0; i < 25; i++) if (s.sol[i] === "1" && i !== s.sol.indexOf("1")) evs = evs.concat(N.tap(s, i, false));
  assert.ok(types(evs).includes("check"));
  assert.ok(s.checking);
  assert.deepEqual(N.wrongCells(s), [empty]);
  N.tap(s, empty, false);                          // take it out again
  evs = N.tap(s, s.sol.indexOf("1"), false);
  assert.ok(types(evs).includes("win"));
  assert.equal(N.effective(s), 0, "no penalty in this mode");
});

test("dragging fills along the row or column it started in, only squares like the first one", () => {
  const s = N.create({ mode: "ten", seed: 5, mistakes: "end" });
  N.setCell(s, 3, X, []);                          // a cross in the way
  N.tap(s, 0, false);
  N.drag(s, 5);
  assert.deepEqual(s.cells.slice(0, 6), [F, F, F, X, F, F], "the cross is kept");
  N.drag(s, 15);                                   // off the row: follows the row's column 5
  assert.equal(s.cells[10 + 5], U);
  N.endDrag(s);
  N.undo(s);
  assert.deepEqual(s.cells.slice(0, 6), [U, U, U, X, U, U], "one stroke, one undo");
  // starting on a filled square empties
  N.tap(s, 20, false); N.endDrag(s);
  N.tap(s, 20, false); N.endDrag(s);
  assert.equal(s.cells[20], U);
});

test("hints: a wrong square first, then a square the numbers decide (+30 s); 3 a puzzle", () => {
  const s = N.create({ mode: "ten", seed: 6, mistakes: "end" });
  const empty = s.sol.indexOf("0");
  N.tap(s, empty, false);
  let evs = N.hint(s);
  assert.deepEqual(types(evs), ["hint"]);
  assert.equal(s.hint, empty);
  assert.equal(s.cells[empty], X);
  evs = N.hint(s);
  const h = s.hint;
  assert.ok(h >= 0 && s.fixed[h]);
  assert.equal(s.cells[h] === F, s.sol[h] === "1", "the hint is right");
  N.hint(s);
  assert.deepEqual(types(N.hint(s)), ["nohint"]);
  assert.equal(N.hintsLeft(s), 0);
  assert.equal(N.effective(s), 3 * N.HINT_PENALTY);
});

test("keys: arrows move the cursor, Space fills, X / alt crosses, M switches the tap, H / U", () => {
  const s = N.create({ mode: "five", seed: 7, mistakes: "end" });
  s.sel = 0;
  N.press(s, "left", true); assert.equal(s.sel, 4);
  N.press(s, "up", true); assert.equal(s.sel, 24);
  N.press(s, "fire", true); assert.equal(s.cells[24], F);
  N.press(s, "alt", true); assert.equal(s.cells[24], X);
  N.press(s, "key:X", true); assert.equal(s.cells[24], U);
  N.press(s, "key:M", true); assert.equal(s.crossMode, true);
  N.tap(s, 0); assert.equal(s.cells[0], X);
  N.press(s, "key:U", true); assert.equal(s.cells[0], U);
  N.press(s, "key:H", true); assert.equal(s.hintsUsed, 1);
});

test("lineDone(): a line whose filled squares match its numbers", () => {
  const s = N.create({ mode: "five", seed: 8 });
  for (let c = 0; c < 5; c++) if (s.sol[c] === "1") N.setCell(s, c, F, []);
  assert.equal(N.lineDone(s, "r", 0), true);
});

test("deterministic for a seed; save and restore plays on the same; bad saves are refused", () => {
  assert.equal(JSON.stringify(N.create({ mode: "fifteen", seed: 9 })), JSON.stringify(N.create({ mode: "fifteen", seed: 9 })));
  assert.notEqual(N.create({ mode: "fifteen", seed: 9 }).sol, N.create({ mode: "fifteen", seed: 10 }).sol);
  const s = N.create({ mode: "ten", seed: 12 });
  for (let i = 0; i < 30; i++) { N.tap(s, i * 3 % 100); N.endDrag(s); N.step(s); }
  const copy = N.restore(JSON.parse(JSON.stringify(N.save(s))));
  assert.deepEqual(copy.rows, s.rows);
  solveVia(s); solveVia(copy);
  assert.equal(JSON.stringify(N.save(copy)), JSON.stringify(N.save(s)));
  assert.throws(() => N.restore({ mode: "ten", n: 10, sol: "01" }), /can't be continued/);
  assert.throws(() => N.restore(null));
});

test("honest score: never above TOP however quickly it is solved", () => {
  const s = N.create({ mode: "five", seed: 1 });
  solveVia(s);
  assert.ok(N.result(s).score <= 10000 && N.result(s).score >= N.MIN_SCORE);
});

test("fuzz: random taps, drags and keys never throw and keep the grid sound", () => {
  for (const mode of Object.keys(N.MODES)) {
    for (let seed = 1; seed <= 6; seed++) {
      const s = N.create({ mode, seed, mistakes: seed % 2 ? "now" : "end" });
      let x = seed * 7919;
      for (let i = 0; i < 2500 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        const nn = s.n * s.n;
        if (x % 4 === 0) N.press(s, ["up", "down", "left", "right", "fire", "alt", "notes", "hint", "undo", "key:X", "key:M"][x % 11], true);
        if (x % 5 === 0) N.tap(s, x % (nn + 2) - 1);
        if (x % 7 === 0) N.drag(s, x % nn);
        if (x % 9 === 0) N.endDrag(s);
        N.step(s);
        assert.ok(s.cells.every((v) => v === 0 || v === 1 || v === 2));
        s.cells.forEach((v, k) => { if (s.fixed[k]) assert.equal(v === F, s.sol[k] === "1", "fixed squares are right"); });
        assert.ok(s.hintsUsed <= N.HINTS);
      }
    }
  }
});

test("nonogram renders and plays in every look and size, with taps, drags and keys", () => {
  for (const look of LOOKS) {
    for (const mode of ["five", "ten", "fifteen"]) {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("nonogram");
      assert.equal(def.name, "Picture Logic");
      assert.equal(def.typed, true);
      assert.equal(def.stateVersion, 1);
      const inst = def.create(canvas, { mode, look, seed: 5 });
      inst.start();
      for (let i = 0; i < 200; i++) {
        if (i % 9 === 0) inst.input(["up", "left", "fire", "right", "down", "alt", "notes", "hint", "undo"][(i / 9) % 9], true);
        if (i % 13 === 0) { inst.pointer("down", 150 + (i % 150), 200 + (i % 150)); inst.pointer("move", 200 + (i % 150), 200 + (i % 150)); inst.pointer("up", 200, 200); }
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      if (inst.state !== "over") assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
