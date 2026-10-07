"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const S = loadLogic("sudoku-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const types = (evs) => evs.map((e) => e.type);
const digits = (str) => Array.from(str, Number);
function validGrid(sol) {
  const g = digits(sol);
  for (const u of S.UNITS) assert.deepEqual(u.map((i) => g[i]).sort(), [1, 2, 3, 4, 5, 6, 7, 8, 9], "a unit holds 1–9 once");
}
// the first empty cell (or the n-th) and the right answer for it
const empties = (s) => s.vals.map((v, i) => (v ? -1 : i)).filter((i) => i >= 0);
const answer = (s, i) => s.solution.charCodeAt(i) - 48;
function wrongDigit(s, i) { return answer(s, i) === 9 ? 1 : answer(s, i) + 1; }
function solveAll(s) { for (let i = 0; i < 81; i++) if (!s.fixed[i] && s.vals[i] !== answer(s, i)) { s.sel = i; S.digit(s, answer(s, i)); } }

test("every level makes puzzles with exactly one solution, graded to the level, with the right number of givens", () => {
  for (const level of S.LEVELS) {
    const seen = new Set();
    for (let seed = 1; seed <= 6; seed++) {
      const g = S.generate(level, seed * 104729);
      validGrid(g.solution);
      const puz = S.parse(g.puzzle), sol = S.parse(g.solution);
      assert.equal(S.countSolutions(puz, 3), 1, `${level} ${seed}: exactly one solution`);
      for (let i = 0; i < 81; i++) assert.ok(puz[i] === 0 || puz[i] === sol[i], "the givens are part of the answer");
      assert.equal(g.givens, puz.filter(Boolean).length);
      const [lo, hi] = S.GIVENS[level];
      assert.ok(g.givens >= lo - 2 && g.givens <= hi + 2, `${level}: ${g.givens} givens`);
      assert.equal(g.grade, level, `${level} ${seed} graded ${g.grade}`);
      seen.add(g.puzzle);
    }
    assert.equal(seen.size, 6, "different seeds make different puzzles");
  }
});

test("levels differ in the techniques they need: Easy only singles, Medium hidden singles, Hard and Expert more", () => {
  const T = S.T;
  const need = (level, seed) => S.gradeSolve(S.parse(S.generate(level, seed).puzzle));
  for (let seed = 1; seed <= 4; seed++) {
    const e = need("easy", seed), m = need("medium", seed), h = need("hard", seed), x = need("expert", seed);
    assert.ok(e.solved && e.hardest === T.NS, "easy: a single is always enough");
    assert.ok(m.solved && m.hardest === T.HS, "medium: needs a hidden single");
    assert.ok(h.solved && h.hardest >= T.LC && h.hardest < T.XW, "hard: locked candidates, pairs or triples");
    assert.ok(!x.solved || x.hardest >= T.XW, "expert: X-Wing, XY-Wing or harder");
  }
});

test("the same seed always makes the same puzzle (so a race and the daily challenge match)", () => {
  for (const level of S.LEVELS) assert.deepEqual(S.generate(level, 4242), S.generate(level, 4242));
  assert.equal(S.create({ mode: "hard", seed: 9 }).puzzle, S.create({ mode: "hard", seed: 9 }).puzzle);
  assert.notEqual(S.create({ mode: "hard", seed: 9 }).puzzle, S.create({ mode: "hard", seed: 10 }).puzzle);
  assert.equal(S.create({ mode: "nonsense", seed: 1 }).mode, "medium");
});

test("candidates, a naked single and a hidden single are found and explained", () => {
  // a grid with one empty cell: only one number fits
  const s = S.create({ mode: "easy", seed: 3 });
  const g = S.parse(s.solution);
  const cell = 40, want = g[cell];
  g[cell] = 0;
  assert.equal(S.candidates(g)[cell], 1 << (want - 1));
  const hint = S.findHint(g, s.solution, 0);
  assert.equal(hint.kind, "naked"); assert.equal(hint.cell, cell); assert.equal(hint.digit, want);
  assert.match(hint.text, new RegExp(`^Only ${want} fits here: `));
  // the explanation lists what blocks the other numbers, in ranges ("1–6 and 9")
  assert.equal(S.digitList([1, 2, 3, 4, 5, 6, 9]), "1–6 and 9");
  assert.equal(S.digitList([2, 4]), "2 and 4");
  assert.equal(S.digitList([7]), "7");
  assert.equal(S.digitList([1, 2, 3]), "1–3");
  // a hidden single: empty a whole row but one number can only go in one cell because of the columns
  const h = S.parse(s.solution);
  for (const c of S.UNITS[4]) h[c] = 0;                      // row 5 is empty
  const hid = S.findHint(h, s.solution, 0);
  assert.ok(["naked", "hidden"].includes(hid.kind));
  assert.equal(hid.digit, S.parse(s.solution)[hid.cell]);
});

test("a wrong number is pointed out first, with the reason when it clashes", () => {
  const s = S.create({ mode: "easy", seed: 5 });
  const [a, b] = empties(s);
  S.tap(s, a); S.digit(s, wrongDigit(s, a));
  s.mistakesNow = false;                                     // no penalty bookkeeping needed here
  const hint = S.findHint(s.vals, s.solution, 0);
  assert.equal(hint.kind, "wrong"); assert.equal(hint.cell, a); assert.equal(hint.digit, 0);
  assert.match(hint.text, /Erase it/);
  void b;
});

test("Hint: the first press explains and counts (+30 s), the second fills the cell in; the limit is respected", () => {
  const s = S.create({ mode: "easy", seed: 6, hints: 2 });
  assert.equal(S.hintsLeft(s), 2);
  let evs = S.hint(s);
  assert.deepEqual(types(evs), ["hint"]);
  assert.ok(s.hint && s.sel === s.hint.cell && s.message.length > 10);
  assert.equal(s.hintsUsed, 1); assert.equal(S.hintsLeft(s), 1);
  assert.equal(S.effective(s), 30);
  const cell = s.hint.cell, d = s.hint.digit;
  assert.ok(!s.vals[cell]);
  evs = S.hint(s);
  assert.ok(types(evs).includes("hintFill"));
  assert.equal(s.vals[cell], d); assert.equal(s.hint, null);
  assert.equal(s.hintsUsed, 1, "filling it in is free");
  S.hint(s); S.hint(s);
  assert.equal(S.hintsLeft(s), 0);
  assert.deepEqual(types(S.hint(s)), ["nohint"]);
  assert.match(s.message, /No hints left/);
  assert.equal(S.effective(s), 60);
  // another action drops an explained hint without filling it
  const t = S.create({ mode: "easy", seed: 6, hints: 3 });
  S.hint(t); const c2 = t.hint.cell;
  S.tap(t, (c2 + 1) % 81);
  assert.equal(t.hint, null); assert.ok(!t.vals[c2]); assert.equal(t.hintsUsed, 1);
  // unlimited (Practice)
  const u = S.create({ mode: "easy", seed: 6, hints: null });
  for (let i = 0; i < 10; i++) { S.hint(u); S.hint(u); }
  assert.equal(S.hintsLeft(u), -1); assert.equal(u.hintsUsed, 10);
  // the default is 3
  assert.equal(S.create({ mode: "easy", seed: 1 }).hintsAllowed, 3);
});

test("Hint on a wrong number says to erase it, and the second press erases it", () => {
  const s = S.create({ mode: "easy", seed: 7, hints: null });
  const [a] = empties(s);
  S.tap(s, a); S.digit(s, wrongDigit(s, a));
  S.hint(s);
  assert.equal(s.hint.kind, "wrong"); assert.equal(s.hint.cell, a);
  S.hint(s);
  assert.equal(s.vals[a], 0);
});

test("hints work in every state, down to the last cell, on every level (never a dead end)", () => {
  for (const level of S.LEVELS) {
    const s = S.create({ mode: level, seed: 11, hints: null });
    let guard = 0;
    while (!s.over && guard++ < 100) { S.hint(s); S.hint(s); }
    assert.ok(s.won, `${level}: hints alone solve it`);
  }
});

test("Notes: numbers become small pencil marks, tapping again removes one, Notes never count as answers", () => {
  const s = S.create({ mode: "easy", seed: 8 });
  const [a] = empties(s);
  S.tap(s, a);
  assert.deepEqual(types(S.toggleNotes(s)), ["mode"]); assert.ok(s.notesOn);
  S.digit(s, 3); S.digit(s, 5); S.digit(s, 7);
  assert.equal(s.notes[a], (1 << 2) | (1 << 4) | (1 << 6));
  assert.equal(s.vals[a], 0, "a note is not an answer");
  S.digit(s, 5);
  assert.equal(s.notes[a], (1 << 2) | (1 << 6));
  assert.ok(s.stats.notesMade >= 3);
  S.toggleNotes(s);
  assert.ok(!s.notesOn);
});

test("placing a number removes it from the notes of its row, column and box (and only those)", () => {
  const s = S.create({ mode: "easy", seed: 9, hints: null });
  S.fillNotes(s);
  const [a] = empties(s), d = answer(s, a);
  const peers = S.PEERS[a], others = [];
  for (let i = 0; i < 81; i++) if (i !== a && !peers.includes(i)) others.push(i);
  const before = s.notes.slice();
  S.tap(s, a); S.digit(s, d);
  assert.equal(s.notes[a], 0);
  for (const p of peers) assert.equal(s.notes[p] & (1 << (d - 1)), 0, "gone from a peer");
  for (const i of others) assert.equal(s.notes[i], before[i], "other cells keep their notes");
  S.undo(s);
  assert.deepEqual(s.notes, before, "undo brings the notes back too");
  assert.equal(s.vals[a], 0);
});

test("Fill notes writes every possible number into every empty cell, and one Undo takes it back", () => {
  const s = S.create({ mode: "medium", seed: 10 });
  const before = s.notes.slice();
  assert.deepEqual(types(S.fillNotes(s)), ["fill"]);
  const cand = S.candidates(Int8Array.from(s.vals));
  for (let i = 0; i < 81; i++) {
    if (s.vals[i]) assert.equal(s.notes[i], 0); else { assert.equal(s.notes[i], cand[i]); assert.ok(s.notes[i] & (1 << (answer(s, i) - 1)), "the answer is always among the notes"); }
  }
  S.undo(s);
  assert.deepEqual(s.notes, before);
});

test("number lines: every cell with the number, the lines through their rows and columns, their boxes shaded, the rest open", () => {
  const s = S.create({ mode: "easy", seed: 12, hints: null });
  assert.equal(S.lines(s, 0), null);
  const d = 5, ln = S.lines(s, d);
  const holders = s.vals.map((v, i) => (v === d ? i : -1)).filter((i) => i >= 0);
  assert.deepEqual(ln.holders, holders);
  assert.deepEqual(ln.rows, [...new Set(holders.map((i) => S.ROW[i]))].sort((x, y) => x - y));
  assert.deepEqual(ln.cols, [...new Set(holders.map((i) => S.COL[i]))].sort((x, y) => x - y));
  assert.deepEqual(ln.boxes, [...new Set(holders.map((i) => S.BOX[i]))].sort((x, y) => x - y));
  // the open cells are exactly where the number could still go (given only the placed numbers)
  const cand = S.candidates(Int8Array.from(s.vals));
  for (let i = 0; i < 81; i++) {
    if (ln.open.includes(i)) { assert.ok(!s.vals[i]); assert.ok(cand[i] & (1 << (d - 1)), "an open cell can take it"); }
    else if (!s.vals[i]) assert.ok(!(cand[i] & (1 << (d - 1))), "a covered empty cell can't take it");
  }
  // focus: tapping a cell with a number, tapping the pad, placing a number
  const s2 = S.create({ mode: "easy", seed: 12, hints: null });
  const given = s2.vals.findIndex((v) => v === 7);
  S.tap(s2, given); assert.equal(s2.focus, 7);
  S.tap(s2, given); assert.equal(s2.focus, 0, "tapping the cell again lets go");
  S.digit(s2, 4); assert.equal(s2.focus, 4, "the pad with no editable cell picks the number");
  S.digit(s2, 4); assert.equal(s2.focus, 0);
  const [e] = empties(s2); S.tap(s2, e); S.digit(s2, answer(s2, e));
  assert.equal(s2.focus, answer(s2, e), "placing a number focuses it");
});

test("Mistakes shown at once cost 10 s each, and the clock shows the penalties", () => {
  const s = S.create({ mode: "easy", seed: 13, hints: null });
  assert.ok(s.mistakesNow);
  const [a, b] = empties(s);
  S.tap(s, a);
  const evs = S.digit(s, wrongDigit(s, a));
  assert.deepEqual(types(evs), ["wrong"]); assert.equal(evs[0].penalty, 10);
  assert.equal(s.mistakes, 1); assert.equal(S.effective(s), 10);
  assert.deepEqual(S.wrongCells(s), [a]);
  S.digit(s, answer(s, a));                       // fixing it doesn't give the time back
  assert.equal(S.effective(s), 10); assert.deepEqual(S.wrongCells(s), []);
  S.tap(s, b); S.digit(s, wrongDigit(s, b)); S.digit(s, wrongDigit(s, b) === 9 ? 1 : wrongDigit(s, b) + 1);
  assert.equal(s.mistakes, 3);
  for (let i = 0; i < 60 * 5; i++) S.step(s);
  assert.equal(S.effective(s), 5 + 30);
  assert.equal(S.seconds(s), 5);
});

test("Mistakes shown at the end: no penalty, nothing marked until the grid is full; then the wrong ones are marked", () => {
  const s = S.create({ mode: "easy", seed: 14, hints: null, mistakes: "end" });
  assert.ok(!s.mistakesNow);
  const list = empties(s), a = list[0];
  S.tap(s, a);
  const evs = S.digit(s, wrongDigit(s, a));
  assert.deepEqual(types(evs), ["place"]);
  assert.equal(S.effective(s), 0);
  for (const i of list.slice(1)) { S.tap(s, i); const e = S.digit(s, answer(s, i)); if (i !== list[list.length - 1]) assert.ok(!types(e).includes("check")); }
  assert.ok(s.checking && !s.over, "full but wrong: not solved");
  assert.deepEqual(S.wrongCells(s), [a]);
  assert.match(s.message, /some numbers are wrong/);
  S.tap(s, a); S.digit(s, answer(s, a));
  assert.ok(s.won);
  assert.equal(s.score, S.TOP - S.effective(s));
  assert.equal(S.result(s).stats.mistakes, 1);
});

test("Undo and Erase", () => {
  const s = S.create({ mode: "easy", seed: 15, hints: null });
  assert.deepEqual(S.undo(s), []);
  const [a, b] = empties(s);
  S.tap(s, a); S.digit(s, answer(s, a));
  S.tap(s, b); S.toggleNotes(s); S.digit(s, 2); S.toggleNotes(s);
  assert.ok(s.notes[b]);
  S.erase(s);
  assert.equal(s.notes[b], 0);
  S.undo(s); assert.ok(s.notes[b] & 2, "erase undone");
  S.undo(s); assert.equal(s.notes[b], 0, "note undone");
  S.undo(s); assert.equal(s.vals[a], 0, "number undone");
  assert.equal(s.sel, a);
  // a given can't be erased or overwritten
  const g = s.fixed.findIndex(Boolean), gv = s.vals[g];
  S.tap(s, g); assert.deepEqual(S.erase(s), []); S.digit(s, gv === 1 ? 2 : 1);
  assert.equal(s.vals[g], gv);
  // erasing a number
  S.tap(s, a); S.digit(s, answer(s, a)); S.erase(s);
  assert.equal(s.vals[a], 0);
  // notes can't be written over a number
  S.digit(s, answer(s, a)); S.toggleNotes(s); S.digit(s, 3);
  assert.equal(s.notes[a], 0); assert.match(s.message, /Erase the number first/);
});

test("solving the puzzle wins: the score is 10,000 minus the counted seconds; unsolved scores 0", () => {
  const s = S.create({ mode: "easy", seed: 16, hints: null });
  assert.equal(S.result(s).score, 0); assert.equal(S.result(s).stats.won, false);
  for (let i = 0; i < 60 * 90; i++) S.step(s);       // 90 s on the clock
  const first = empties(s)[0];
  S.hint(s);                                          // +30 s
  S.hint(s);
  for (const i of empties(s)) { if (i === first) continue; S.tap(s, i); S.digit(s, answer(s, i)); }
  assert.ok(s.over && s.won);
  assert.equal(S.seconds(s), 90);
  assert.equal(S.effective(s), 120);
  assert.equal(s.score, 10000 - 120);
  const r = S.result(s);
  assert.equal(r.score, 9880); assert.equal(r.level, 1);
  assert.equal(r.stats.won, true); assert.equal(r.stats.hints, 1); assert.equal(r.stats.mode, "easy");
  assert.ok(r.stats.summary.some((l) => /1 hint \(\+30 s\)/.test(l)));
  assert.ok(r.stats.summary[0].startsWith("Time 1:30"));
  assert.deepEqual(S.step(s), [], "nothing runs after the win");
  assert.deepEqual(S.digit(s, 1), []);
  // faster is always better: more counted seconds, a lower score; never below the floor
  const slow = S.create({ mode: "easy", seed: 16 });
  slow.updates = 60 * 20000; solveAll(slow);
  assert.equal(slow.score, S.MIN_SCORE);
});

test("keyboard: arrows move the cursor, 1–9 place, N notes, H hint, U undo, Backspace erases", () => {
  const s = S.create({ mode: "easy", seed: 17, hints: null });
  assert.equal(s.sel, -1);
  S.press(s, "right", true); assert.equal(s.sel, 40);
  S.press(s, "up", true); assert.equal(s.sel, 31);
  S.press(s, "left", true); assert.equal(s.sel, 30);
  S.press(s, "down", true); assert.equal(s.sel, 39);
  for (let i = 0; i < 9; i++) S.press(s, "right", true);
  assert.equal(s.sel, 39, "the cursor wraps around the row");
  const [a] = empties(s);
  S.tap(s, a);
  S.press(s, "key:" + answer(s, a), true);
  assert.equal(s.vals[a], answer(s, a));
  S.press(s, "key:BACKSPACE", true); assert.equal(s.vals[a], 0);
  S.press(s, "key:N", true); assert.ok(s.notesOn);
  S.press(s, "key:4", true); assert.equal(s.notes[a], 8);
  S.press(s, "key:N", true);
  S.press(s, "key:U", true); assert.equal(s.notes[a], 0);
  S.press(s, "key:H", true); assert.ok(s.hint);
  S.press(s, "n3", true); assert.equal(s.hint, null, "a pad number drops the hint");
  assert.deepEqual(S.press(s, "n3", false), [], "key releases do nothing");
  assert.deepEqual(S.press(s, "nonsense", true), []);
});

test("save and restore carry on the same game; bad data is refused", () => {
  const s = S.create({ mode: "hard", seed: 18, hints: 3, mistakes: "end" });
  const list = empties(s);
  S.tap(s, list[0]); S.digit(s, answer(s, list[0]));
  S.toggleNotes(s); S.tap(s, list[1]); S.digit(s, 4); S.toggleNotes(s);
  S.hint(s);
  for (let i = 0; i < 500; i++) S.step(s);
  const data = JSON.parse(JSON.stringify(S.save(s)));
  const copy = S.restore(data);
  assert.equal(copy.hint, null, "an open hint isn't kept");
  assert.equal(copy.hintsUsed, 1); assert.equal(copy.updates, s.updates); assert.equal(copy.mistakesNow, false);
  assert.deepEqual(copy.vals, s.vals); assert.deepEqual(copy.notes, s.notes);
  for (const i of empties(copy)) { S.tap(copy, i); S.digit(copy, answer(copy, i)); }
  assert.ok(copy.won);
  assert.throws(() => S.restore({ puzzle: "x" }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, solution: "1".repeat(81) }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, vals: data.vals.slice(1) }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, notes: data.notes.map(() => 999) }), /can't be continued/);
  const tampered = JSON.parse(JSON.stringify(data)); const g = tampered.fixed.indexOf(true); tampered.vals[g] = tampered.vals[g] === 1 ? 2 : 1;
  assert.throws(() => S.restore(tampered), /can't be continued/);
});

test("status() for a race: the score, over and done, and the counted seconds", () => {
  const s = S.create({ mode: "easy", seed: 19, hints: null });
  assert.deepEqual({ ...S.status(s), filled: 0 }, { score: 0, level: 1, over: false, done: false, seconds: 0, filled: 0 });
  solveAll(s);
  const st = S.status(s);
  assert.equal(st.done, true); assert.equal(st.over, true); assert.equal(st.score, 10000);
});

test("a fast solver stays inside the server's honest-score limits", () => {
  // score ≤ 10,000, and a result of S seconds always has S ≥ the seconds it really took
  for (const level of S.LEVELS) {
    const s = S.create({ mode: level, seed: 20, hints: null });
    let steps = 0;
    for (const i of empties(s)) { for (let k = 0; k < 10; k++) { S.step(s); steps++; } S.tap(s, i); S.digit(s, answer(s, i)); }
    assert.ok(s.won);
    assert.ok(s.score <= 10000 && s.score >= S.MIN_SCORE);
    assert.equal(s.score, S.TOP - Math.floor(steps / 60));
  }
});

// ---------------------------------------------------------------------------
// the game file
// ---------------------------------------------------------------------------
test("sudoku registers with its modes, number pad, tools, options and typed keys", () => {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  const def = sb.win.ArcadeGames.get("sudoku");
  assert.equal(def.name, "Sudoku"); assert.equal(def.controls, "buttons"); assert.equal(def.stateVersion, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(def.modes.map((m) => m.id))), ["easy", "medium", "hard", "expert"]);
  assert.equal(def.defaultMode, "easy");
  assert.equal(def.typed, true);
  assert.deepEqual(JSON.parse(JSON.stringify(def.buttons.map((b) => b.action))),
    ["n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "notes", "undo", "erase", "hint", "fill"]);
  assert.ok(def.buttons.every((b) => b.place && b.place[0] + b.place[2] - 1 <= 5), "the pad is at most five columns wide");
  assert.deepEqual(JSON.parse(JSON.stringify(def.options.map((o) => [o.id, o.default]))), [["mistakes", "now"], ["lines", "on"]]);
  assert.ok(def.help.length > 40);
});

for (const look of LOOKS) {
  test(`sudoku renders and plays in the ${look} look`, () => {
    const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
    const def = sb.win.ArcadeGames.get("sudoku");
    const canvas = sb.canvas(390, 487);
    const ends = [], events = [];
    const inst = def.create(canvas, { mode: "medium", look, seed: 5, config: { hints: 2 }, options: { mistakes: "now", lines: "on" },
      onEnd: (r) => ends.push(r), onEvent: (t) => events.push(t) });
    inst.start();
    const L = inst.logic, k = 390 / 240;
    const px = (cell) => [(16 + (cell % 9) * 23 + 11) * k, (38 + Math.floor(cell / 9) * 23 + 11) * k];
    const [first, second, third] = empties(L);
    inst.pointer("down", ...px(first)); inst.pointer("up", ...px(first));
    assert.equal(L.sel, first);
    inst.input("notes", true); inst.input("n2", true); inst.input("n4", true); inst.input("notes", true);
    inst.input("fill", true);
    sb.frames(3);
    inst.input("n" + answer(L, first), true);
    sb.frames(3);
    inst.pointer("down", ...px(second));
    inst.input("n" + wrongDigit(L, second), true);        // a wrong one (shows red, adds 10 s)
    sb.frames(5);
    inst.input("hint", true); sb.frames(5);
    inst.input("hint", true); sb.frames(5);
    inst.input("undo", true); inst.input("erase", true);
    inst.input("key:" + answer(L, third), true);
    inst.input("down", true); inst.input("right", true);
    inst.input("n5", true);                                // a number on a spot that may be a given
    for (let i = 0; i < 120; i++) sb.frames(1);
    assert.ok(events.includes("hint"));
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    // number lines off, a hint showing, a wrong number and a full wrong grid in the end-mistakes mode
    inst.input("hint", true); sb.frames(3);
    L.vals[third] = wrongDigit(L, third); L.notes[third] = 0; sb.frames(3);
    L.checking = true; L.message = "A message that is long enough to wrap around a few lines when it is drawn under the board."; sb.frames(3);
    L.focus = 3; sb.frames(3);
    solveAll(L); sb.frames(3);
    assert.equal(ends.length, 1);
    assert.ok(ends[0].stats.won); assert.equal(ends[0].level, 1);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
    assert.equal(sb.pending(), 0);
  });
}

test("sudoku: options turn the number lines off, the end-mistakes mode takes no penalty; saved games continue", () => {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  const def = sb.win.ArcadeGames.get("sudoku");
  const off = def.create(sb.canvas(), { mode: "easy", seed: 2, look: "modern", options: { lines: "off", mistakes: "end" } });
  assert.equal(off.logic.mistakesNow, false);
  off.start(); off.input("n3", true); sb.frames(2);       // focus 3 with the lines off draws without lines
  off.pause();
  const snap = off.save();
  assert.equal(off.canSave, true);
  assert.equal(snap.score, 0); assert.equal(snap.level, 1);
  const back = def.create(sb.canvas(), { mode: "easy", seed: 99, look: "modern", restore: { state: JSON.parse(JSON.stringify(snap.state)), seconds: snap.seconds } });
  assert.equal(back.logic.puzzle, off.logic.puzzle);
  back.start(); sb.frames(2);
  back.destroy(); off.destroy();
  // a race or the daily challenge hands the game its seed: the same puzzle on both phones
  const a = def.create(sb.canvas(), { mode: "hard", seed: 777 }), b = def.create(sb.canvas(), { mode: "hard", seed: 777 });
  assert.equal(a.logic.puzzle, b.logic.puzzle);
  a.destroy(); b.destroy();
});

test("sudoku: the clock counts only the time the game ran; a pause stops it", () => {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  const inst = sb.win.ArcadeGames.get("sudoku").create(sb.canvas(), { mode: "easy", seed: 3 });
  inst.start(); sb.frames(120);
  const secs = S.seconds(inst.logic);
  assert.ok(secs >= 1 && secs <= 3, `seconds ${secs}`);
  inst.pause(); sb.frames(600);
  assert.equal(S.seconds(inst.logic), secs);
  inst.resume(); sb.frames(60);
  assert.ok(S.seconds(inst.logic) >= secs);
  inst.destroy();
});

test("number lines cover the rows, columns and boxes of every cell holding the number, each cell once", () => {
  const s = S.create({ mode: "medium", seed: 31, hints: null });
  const d = 7, ln = S.lines(s, d);
  const holders = s.vals.map((v, i) => (v === d ? i : -1)).filter((i) => i >= 0);
  assert.ok(holders.length > 0);
  for (let i = 0; i < 81; i++) {
    const want = holders.some((h) => S.ROW[h] === S.ROW[i] || S.COL[h] === S.COL[i] || S.BOX[h] === S.BOX[i]);
    assert.equal(ln.covered[i], want, `cell ${i}`);
  }
  assert.equal(ln.covered.length, 81);
});

// Draw one frame of Sudoku with a recording "g" (grabbing the game's draw from createSession).
function recordDraw(look, setup) {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  let impl = null;
  const real = sb.win.ArcadeKit.createSession;
  sb.win.ArcadeKit.createSession = (c, o, i) => { impl = i; return real(c, o, i); };
  const inst = sb.win.ArcadeGames.get("sudoku").create(sb.canvas(), { mode: "easy", seed: 12, look, options: { lines: "on" } });
  inst.start(); sb.frames(1);
  const s = impl.logic();
  setup(s);
  const calls = { line: [], fill: [], rect: [] };
  const ctx = { fillRect: (...a) => calls.fill.push(a), set fillStyle(v) {}, get fillStyle() { return ""; } };
  const g = { kind: look, lowres: look === "lcd" || look === "pixel", k: look === "lcd" ? 2 / 3 : look === "pixel" ? 0.5 : 1, ctx,
    col: () => "rgba(0,0,0,0.2)", hud() {}, text() {}, poly() {},
    line: (...a) => calls.line.push(a), rect: (...a) => calls.rect.push(a) };
  impl.draw(g, {});
  inst.destroy();
  return { s, calls };
}

test("number lines shade cells instead of drawing a line through the middle", () => {
  const GX = 16, GY = 38, CS = 23, GW = CS * 9;
  for (const look of LOOKS) {
    const { s, calls } = recordDraw(look, (s) => { s.focus = 5; s.sel = -1; s.hint = null; });
    const ln = S.lines(s, 5);
    // every line inside the grid lies on a grid line: no line through a cell's middle
    for (const [x1, y1, x2, y2] of calls.line) {
      if (x1 < GX || x2 > GX + GW || y1 < GY || y2 > GY + GW) continue;
      const onGrid = (v, o) => Math.abs(((v - o) / CS) - Math.round((v - o) / CS)) < 1e-6;
      assert.ok((y1 === y2 && onGrid(y1, GY)) || (x1 === x2 && onGrid(x1, GX)), `${look}: line ${[x1, y1, x2, y2]} is a grid line`);
    }
    const shaded = ln.covered.filter((c, i) => c && s.vals[i] !== 5).length;
    if (look === "lcd") assert.ok(calls.rect.length > shaded, "lcd dithers the covered cells");
    else if (look === "pixel") assert.equal(calls.rect.filter((r) => r[4] === 7).length, shaded, "pixel: one fill per covered cell");
    else assert.equal(calls.fill.length, shaded, `${look}: one fill per covered cell`);
  }
  // with nothing in focus nothing is shaded
  const none = recordDraw("modern", (s) => { s.focus = 0; s.sel = -1; s.hint = null; });
  assert.equal(none.calls.fill.length, 0);
});

// ---------- number lines: three kinds (rows, columns and boxes / rows and columns only / none) ----------

test("number lines kinds: 'on' is rows, columns and boxes, 'rows' leaves the boxes out, 'off' shades nothing", () => {
  const s = S.create({ mode: "medium", seed: 31, hints: null });
  const d = 7;
  const holders = s.vals.map((v, i) => (v === d ? i : -1)).filter((i) => i >= 0);
  assert.deepEqual(S.LINE_MODES, ["on", "rows", "off"]);
  const on = S.lines(s, d, "on"), rows = S.lines(s, d, "rows");
  assert.deepEqual(on.covered, S.lines(s, d).covered, "no kind given = rows, columns and boxes");
  assert.equal(on.mode, "on"); assert.equal(rows.mode, "rows");
  assert.deepEqual(rows.boxes, []);
  assert.deepEqual(rows.rows, on.rows); assert.deepEqual(rows.cols, on.cols); assert.deepEqual(rows.holders, holders);
  let boxOnly = 0;
  for (let i = 0; i < 81; i++) {
    const rc = holders.some((h) => S.ROW[h] === S.ROW[i] || S.COL[h] === S.COL[i]);
    assert.equal(rows.covered[i], rc, `rows-only cell ${i}`);
    if (on.covered[i] && !rc) boxOnly++;
    if (!rows.covered[i] && !s.vals[i]) assert.ok(rows.open.includes(i));
  }
  assert.ok(boxOnly > 0, "the sample has cells that only a box covers, so the kinds differ");
  assert.equal(S.lines(s, d, "off"), null);
  assert.equal(S.lines(s, 0, "rows"), null);
  // an old or odd saved choice reads as the default
  for (const odd of [undefined, null, "", "On", "boxes"]) assert.equal(S.lineMode(odd), "on");
  assert.equal(S.lineMode("rows"), "rows"); assert.equal(S.lineMode("off"), "off");
  // linked: which cells the selected cell's own tint reaches
  const a = 40;                                  // centre cell
  assert.ok(S.linked(a, 4, "on") && S.linked(a, 4, "rows"), "same column");
  assert.ok(S.linked(a, 36, "rows"), "same row");
  assert.ok(S.linked(a, 30, "on") && !S.linked(a, 30, "rows"), "same box only");
  assert.ok(!S.linked(a, 4, "off") && !S.linked(a, 0, "on"));
});

test("sudoku: the start-screen option offers the three kinds; old On / Off choices keep working", () => {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  const def = sb.win.ArcadeGames.get("sudoku");
  const op = def.options.find((o) => o.id === "lines");
  assert.equal(op.default, "on");
  assert.equal(JSON.stringify(op.choices.map((c) => c.id)), JSON.stringify(["on", "rows", "off"]));
  assert.equal(JSON.stringify(op.choices.map((c) => c.label)), JSON.stringify(["Rows, columns and boxes", "Rows and columns only", "None"]));
  assert.ok(!op.offInRaces, "a display choice: races and daily challenges keep each person's own");
  // the option never reaches the rules' state: the same seed gives the same saved state whatever the kind
  const states = ["on", "rows", "off"].map((k) => {
    const inst = def.create(sb.canvas(), { mode: "easy", seed: 5, look: "modern", options: { lines: k } });
    inst.start(); inst.input("n4", true); sb.frames(2);
    inst.pause(); const st = JSON.stringify(inst.save().state); inst.destroy(); return st;
  });
  assert.equal(states[0], states[1]); assert.equal(states[1], states[2]);
});

// Count the number-line fills of one frame (the per-look shade) for a kind of lines.
function shadeCount(look, kind, setup) {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  let impl = null;
  const real = sb.win.ArcadeKit.createSession;
  sb.win.ArcadeKit.createSession = (c, o, i) => { impl = i; return real(c, o, i); };
  const opts = { mode: "easy", seed: 12, look };
  if (kind !== undefined) opts.options = { lines: kind };
  const inst = sb.win.ArcadeGames.get("sudoku").create(sb.canvas(), opts);
  inst.start(); sb.frames(1);
  const s = impl.logic();
  setup(s);
  const calls = { fill: [], rect: [] };
  const ctx = { fillRect: (...a) => calls.fill.push(a), set fillStyle(v) {}, get fillStyle() { return ""; } };
  const g = { kind: look, lowres: look === "lcd" || look === "pixel", k: look === "lcd" ? 2 / 3 : look === "pixel" ? 0.5 : 1, ctx,
    col: () => "rgba(0,0,0,0.2)", hud() {}, text() {}, poly() {}, line() {}, rect: (...a) => calls.rect.push(a) };
  impl.draw(g, {});
  inst.destroy();
  return { s, calls };
}

test("number lines kinds are drawn in every look: one shade per covered cell, none with None", () => {
  for (const look of LOOKS) {
    for (const kind of ["on", "rows", "off", undefined, "Off-ish"]) {
      const { s, calls } = shadeCount(look, kind, (s) => { s.focus = 5; s.sel = -1; s.hint = null; });
      const ln = S.lines(s, 5, kind);
      const want = ln ? ln.covered.filter((c, i) => c && s.vals[i] !== 5 && !(look === "lcd" && s.vals[i])).length : 0;
      // the focus cells' own highlight is drawn with rect (colour 3) in every kind
      const focusRects = calls.rect.filter((r) => r[4] === 3).length;
      assert.equal(focusRects, s.vals.filter((v) => v === 5).length, `${look}/${kind}: the number's cells stay highlighted`);
      if (look === "lcd") {
        const dots = calls.rect.filter((r) => r[2] < 2 && r[4] === 0).length;
        if (want) assert.ok(dots > want, `${look}/${kind}: dithered`); else assert.equal(dots, 0, `${look}/${kind}: no dither`);
      } else if (look === "pixel") assert.equal(calls.rect.filter((r) => r[4] === 7).length, want, `${look}/${kind}`);
      else assert.equal(calls.fill.length, want, `${look}/${kind}`);
    }
  }
});

test("the selected cell's own tint follows the kind: row, column and box / row and column / none", () => {
  const sel = 40;
  for (const [kind, n] of [["on", 20], ["rows", 16], ["off", 0]]) {
    const { calls } = shadeCount("modern", kind, (s) => { s.focus = 0; s.sel = sel; s.hint = null; });
    // the tint is a solid colour-8 rect the size of a cell
    const tints = calls.rect.filter((r) => r[4] === 8 && r[2] === 23 && r[5] && r[5].solid).length;
    assert.equal(tints, n, kind);
  }
});
