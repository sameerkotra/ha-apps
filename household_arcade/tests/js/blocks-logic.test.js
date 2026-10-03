"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const L = loadLogic("blocks-logic.js");

function types(evs) { return evs.map((e) => e.type); }
function run(s, n) { const all = []; for (let i = 0; i < n && !s.over; i++) all.push(...L.step(s)); return all; }
function setRow(s, row, filledExcept) {
  for (let c = 0; c < L.COLS; c++) s.grid[row * L.COLS + c] = filledExcept.includes(c) ? 0 : 8;
}
// A quick player: for each new piece, try every turn and column and drop where it leaves the
// fewest holes and the lowest stack (it plays as fast as the rules allow).
function bot(s) {
  if (!s.piece) return;
  let best = null;
  for (let r = 0; r < 4; r++) {
    for (let x = -2; x < L.COLS; x++) {
      const p = { t: s.piece.t, r, x, y: s.piece.y };
      if (!L.fits(s, L.cellsOf(p))) continue;
      while (L.fits(s, L.cellsOf(p, 0, 1))) p.y++;
      const g = s.grid.slice();
      for (const [cx, cy] of L.cellsOf(p)) g[cy * L.COLS + cx] = 1;
      let lines = 0, holes = 0, agg = 0, bump = 0, prevH = null;
      const full = [];
      for (let row = 0; row < L.ROWS; row++) if (g.slice(row * L.COLS, row * L.COLS + L.COLS).every(Boolean)) { lines++; full.push(row); }
      for (let c = 0; c < L.COLS; c++) {
        let h = 0, seen = false;
        for (let row = 0; row < L.ROWS; row++) {
          if (full.includes(row)) continue;
          if (g[row * L.COLS + c]) { if (!seen) h = L.ROWS - row; seen = true; } else if (seen) holes++;
        }
        agg += h; if (prevH !== null) bump += Math.abs(h - prevH); prevH = h;
      }
      const score = -0.51 * agg + 0.76 * lines - 0.36 * holes - 0.18 * bump;
      if (!best || score > best.score) best = { score, r, x };
    }
  }
  if (!best) return;
  for (let i = 0; i < best.r; i++) L.press(s, "up", true);
  L.slide(s, best.x - s.piece.x);
  L.press(s, "fire", true);
}

test("the seven shapes, each with four turns of four cells", () => {
  assert.deepEqual([...L.KINDS], ["I", "O", "T", "S", "Z", "J", "L"]);
  for (const k of L.KINDS) {
    assert.equal(L.CELLS[k].length, 4);
    for (const rot of L.CELLS[k]) assert.equal(rot.length, 4);
  }
});

test("pieces come from a bag: every 7 pieces have all seven shapes", () => {
  const s = L.create({ seed: 9 });
  const seen = [s.piece.t];
  while (seen.length < 21) {
    L.press(s, "fire", true);
    s.grid.fill(0);
    run(s, L.ENTRY + 1);
    if (s.over) break;
    seen.push(s.piece.t);
  }
  for (let i = 0; i + 7 <= 21; i += 7) assert.equal(new Set(seen.slice(i, i + 7)).size, 7, seen.join(""));
});

test("gravity, moving, walls and turning", () => {
  const s = L.create({ seed: 1 });
  const y0 = s.piece.y;
  run(s, L.GRAVITY[1]);
  assert.equal(s.piece.y, y0 + 1);
  const x0 = s.piece.x;
  L.press(s, "left", true); L.press(s, "left", false);
  assert.equal(s.piece.x, x0 - 1);
  for (let i = 0; i < 12; i++) { L.press(s, "left", true); L.press(s, "left", false); }
  const minX = Math.min(...L.cellsOf(s.piece).map((c) => c[0]));
  assert.equal(minX, 0, "stops at the wall");
  const r0 = s.piece.r;
  const evs = L.press(s, "up", true);
  if (s.piece.t !== "O") { assert.notEqual(s.piece.r, r0); assert.deepEqual(types(evs), ["turn"]); }
});

test("held left repeats after a delay", () => {
  const s = L.create({ seed: 4 });
  s.piece.x = 6;
  L.press(s, "left", true);
  assert.equal(s.piece.x, 5);
  run(s, L.DAS - 1);
  assert.equal(s.piece.x, 5, "no repeat before the delay");
  run(s, 1 + L.ARR * 2);
  assert.ok(s.piece.x <= 3);
  L.press(s, "left", false);
});

test("soft drop scores a point a row; drop scores two a row and locks at once", () => {
  const s = L.create({ seed: 2 });
  L.press(s, "down", true);
  assert.equal(s.score, 1);
  L.press(s, "down", false);
  const d = L.dropDistance(s);
  const evs = L.press(s, "fire", true);
  assert.equal(s.score, 1 + d * 2);
  assert.ok(types(evs).includes("drop"));
  assert.equal(s.piece, null);
});

test("a piece on the floor locks after the lock delay", () => {
  const s = L.create({ seed: 3 });
  s.piece.y += L.dropDistance(s);
  const evs = run(s, L.LOCK_DELAY);
  assert.ok(types(evs).includes("lock"));
  assert.equal(s.piece, null);
  run(s, L.ENTRY);
  assert.ok(s.piece, "the next piece comes");
});

test("full rows clear and score 100 / 300 / 500 / 800 × level", () => {
  for (const [n, pts] of [[1, 100], [2, 300], [3, 500], [4, 800]]) {
    const s = L.create({ seed: 1 });
    s.piece = { t: "I", r: 1, x: -2, y: 0 };      // upright I in column 0
    for (let k = 0; k < n; k++) setRow(s, L.ROWS - 1 - k, [0]);
    const before = s.score, d = L.dropDistance(s);
    const evs = L.press(s, "fire", true);
    const row = evs.find((e) => e.type === "row");
    assert.equal(row.lines, n);
    assert.equal(s.score - before - d * 2, pts);
    assert.equal(s.lines, n);
    run(s, 25);
    assert.equal(s.grid.filter((v) => v === 8).length, 0, "cleared rows are gone");
    assert.equal(s.grid.filter(Boolean).length, 4 - n, "what's left of the piece falls");
  }
});

test("a level every 10 rows, faster gravity; Fast start begins at level 6", () => {
  const s = L.create({ seed: 1 });
  s.lines = 9; s.piece = { t: "I", r: 1, x: -2, y: 0 }; setRow(s, L.ROWS - 1, [0]);
  const evs = L.press(s, "fire", true);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.level, 2);
  assert.ok(L.GRAVITY[2] < L.GRAVITY[1]);
  assert.equal(L.create({ mode: "fast", seed: 1 }).level, 6);
});

test("Rising floor pushes up a row with one gap", () => {
  const s = L.create({ mode: "rising", seed: 5 });
  const evs = run(s, L.riseEvery(s));
  assert.ok(types(evs).includes("rise"));
  const bottom = s.grid.slice((L.ROWS - 1) * L.COLS);
  assert.equal(bottom.filter((v) => !v).length, 1);
  assert.equal(L.create({ mode: "classic", seed: 5 }).stats.rowsRisen, 0);
});

test("the game ends when a new piece has no room", () => {
  const s = L.create({ seed: 6 });
  for (let r = 0; r < L.ROWS; r++) setRow(s, r, [9]);
  for (let c = 0; c < L.COLS; c++) s.grid[c] = 0;
  s.piece = null; s.wait = 1;
  const evs = run(s, 2);
  assert.ok(s.over);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(L.result(s).stats.cause, "top");
});

test("deterministic for a seed; a quick player stays inside the honest-score limits", () => {
  const limit = { per: 10000, base: 2000, max: 5000000 };
  const play = (seed, mode) => {
    const s = L.create({ seed, mode });
    let u = 0;
    while (!s.over && u++ < 60 * 400) {
      bot(s);
      L.step(s);
      assert.ok(s.score <= (s.updates / 60) * limit.per + limit.base, `score ${s.score} after ${s.updates / 60}s`);
    }
    assert.ok(s.score <= limit.max);
    return { key: JSON.stringify(L.result(s)) + s.updates, s };
  };
  const a = play(11, "fast");
  assert.equal(a.key, play(11, "fast").key);
  assert.ok(a.s.lines >= 20, `the bot clears rows (${a.s.lines})`);
  play(12, "rising");
});

test("save and restore carry on the same game", () => {
  const s = L.create({ seed: 21 });
  for (let i = 0; i < 300; i++) { bot(s); L.step(s); }
  const copy = L.restore(JSON.parse(JSON.stringify(L.save(s))));
  for (let i = 0; i < 600 && !s.over; i++) { bot(s); bot(copy); L.step(s); L.step(copy); }
  assert.equal(copy.score, s.score);
  assert.deepEqual(copy.grid, s.grid);
  assert.throws(() => L.restore({ grid: [] }));
});

test("hold: puts the piece aside, then swaps it back; once per piece", () => {
  const s = L.create({ seed: 13 });
  const first = s.piece.t, second = s.next[0];
  assert.deepEqual(types(L.press(s, "alt", true)), ["hold"]);
  assert.equal(s.hold, first);
  assert.equal(s.piece.t, second);
  assert.deepEqual(L.press(s, "alt", true), [], "only once until the piece lands");
  L.press(s, "fire", true);
  run(s, L.ENTRY + 1);
  const third = s.piece.t;
  L.press(s, "alt", true);
  assert.equal(s.piece.t, first, "the held piece comes back");
  assert.equal(s.hold, third);
  assert.equal(s.piece.y, 0);
  assert.equal(L.result(s).stats.holds, 2);
  // a save from before Hold still continues
  const old = L.save(L.create({ seed: 2 }));
  delete old.hold; delete old.canHold; delete old.stats.holds;
  const back = L.restore(old);
  assert.equal(back.hold, null);
  assert.equal(back.canHold, true);
});

// ---------------------------------------------------------------------------
// Challenges: the level list
// ---------------------------------------------------------------------------
const { makeSandbox } = require("./helpers");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const TWO = [
  { name: "A", rows: ["#########.", "#########."], goal: 5, speed: 2 },
  { name: "B", rows: ["###.......", ".......###", "#.#.#.#.#."], goal: 5, speed: 4 },
];
// clear rows at once: put an upright I piece in the gap column of full-but-one rows
function clearRows(s, n) {
  s.piece = { t: "I", r: 1, x: -2, y: 0 };
  for (let k = 0; k < n; k++) setRow(s, L.ROWS - 1 - k, [0]);
  return L.press(s, "fire", true);
}

test("Challenges: built-in or the session's list; bad challenges are skipped", () => {
  assert.equal(L.usableLevels(undefined), L.LEVELS);
  assert.equal(L.usableLevels([]), L.LEVELS);
  for (const bad of [
    { name: "Full row", rows: ["##########"], goal: 5, speed: 1 },
    { name: "Thin row", rows: ["##........"], goal: 5, speed: 1 },
    { name: "Short", rows: ["###"], goal: 5, speed: 1 },
    { name: "Fast", rows: [], goal: 5, speed: 16 },
    { name: "Long", rows: [], goal: 41, speed: 1 },
    { name: "Tall", rows: new Array(13).fill("###......."), goal: 5, speed: 1 },
    { rows: [], goal: 5, speed: 1 },
  ]) assert.equal(L.usableLevels([bad]), L.LEVELS, bad.name);
  assert.equal(L.usableLevels([TWO[0], { name: "Bad", rows: ["x"], goal: 5, speed: 1 }]).length, 1);
  assert.equal(L.LEVELS.length, 10);
  for (const c of L.LEVELS) assert.equal(L.usableLevels([c])[0], c, c.name);
});

test("Challenges: the well starts with the rows, the first piece waits; speed sets the fall", () => {
  const s = L.create({ mode: "challenge", seed: 3, levels: TWO });
  assert.equal(s.challenges.length, 2);
  assert.equal(s.level, 1);
  assert.equal(s.goalLeft, 5);
  assert.equal(s.piece, null);
  assert.deepEqual(s.grid.slice((L.ROWS - 2) * L.COLS), [8, 8, 8, 8, 8, 8, 8, 8, 8, 0, 8, 8, 8, 8, 8, 8, 8, 8, 8, 0]);
  assert.equal(s.grid.filter(Boolean).length, 18);
  run(s, L.INTRO - 1);
  assert.equal(s.piece, null, "the name shows for a while");
  run(s, 1);
  assert.ok(s.piece);
  assert.equal(L.speedLevel(s), 2);
  const y0 = s.piece.y;
  run(s, L.GRAVITY[2]);
  assert.equal(s.piece.y, y0 + 1, "falls at the challenge's speed");
});

test("Challenges: rows score × speed; the goal clears the challenge, the next starts fresh, the last one wins", () => {
  const s = L.create({ mode: "challenge", seed: 4, levels: TWO });
  run(s, L.INTRO);
  L.press(s, "alt", true);                     // hold a piece: it carries over
  const held = s.hold;
  s.grid.fill(0);
  const d0 = s.score;
  let evs = clearRows(s, 4);
  const row = evs.find((e) => e.type === "row");
  assert.equal(row.points, 800 * 2, "× the challenge's speed");
  assert.equal(s.goalLeft, 1);
  assert.ok(s.score - d0 >= 1600);
  run(s, L.CLEAR_TIME + L.ENTRY);
  evs = clearRows(s, 2);
  assert.equal(s.goalLeft, 0);
  assert.equal(s.stats.challenges, 1);
  evs = run(s, L.CLEAR_TIME);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.level, 2);
  assert.equal(s.goalLeft, 5);
  assert.equal(s.hold, held, "the held piece carries over");
  assert.equal(s.piece, null);
  assert.equal(s.grid.filter(Boolean).length, 3 + 3 + 5, "a fresh well with challenge 2's rows");
  run(s, L.INTRO);
  assert.equal(L.speedLevel(s), 4);
  s.grid.fill(0);
  evs = clearRows(s, 4);
  assert.equal(evs.find((e) => e.type === "row").points, 800 * 4);
  run(s, L.CLEAR_TIME + L.ENTRY);
  evs = clearRows(s, 1);
  assert.ok(types(evs).includes("win"));
  assert.ok(s.over && s.won);
  const r = L.result(s);
  assert.equal(r.level, 2);
  assert.equal(r.stats.won, true);
  assert.equal(r.stats.cause, "won");
  assert.equal(r.stats.challenges, 2);
  assert.equal(r.stats.mode, "challenge");
});

test("Challenges: save and restore keep going with the list (kept out of the save)", () => {
  const s = L.create({ mode: "challenge", seed: 5, levels: TWO });
  for (let i = 0; i < 500; i++) { bot(s); L.step(s); }
  const data = JSON.parse(JSON.stringify(L.save(s)));
  assert.ok(!("challenges" in data));
  const copy = L.restore(data, TWO);
  assert.equal(copy.challenges.length, 2);
  for (let i = 0; i < 2000 && !s.over; i++) { bot(s); bot(copy); L.step(s); L.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.level, s.level);
  assert.deepEqual(copy.grid, s.grid);
  // without the list, the built-in one
  assert.equal(L.restore(JSON.parse(JSON.stringify(L.save(s)))).challenges, L.LEVELS);
  // a save from before Challenges still continues
  const old = L.save(L.create({ seed: 2 }));
  delete old.goalLeft; delete old.won; delete old.stats.challenges;
  const back = L.restore(old);
  assert.equal(back.challenges, null);
  assert.equal(L.result(back).stats.challenges, 0);
});

test("Challenges: the hardest challenges the fields allow stay inside the honest-score limits", () => {
  const limit = { per: 10000, base: 2000, max: 5000000 };
  // speed 15, 12 starting rows each one square short in the same column: an upright I clears four at once
  const hardest = [];
  for (let i = 0; i < 40; i++) hardest.push({ name: "Hard", rows: new Array(12).fill(i % 2 ? ".#########" : "#########."), goal: 5 + (i % 3) * 4, speed: 15 });
  const play = (seed, levels, lucky) => {
    const s = L.create({ mode: "challenge", seed, levels });
    let u = 0, best = 0;
    while (!s.over && u++ < 60 * 600) {
      if (lucky && s.piece && s.piece.y === 0 && s.piece.t !== "I") s.piece.t = "I";   // every piece the best one
      bot(s);
      L.step(s);
      assert.ok(s.score <= (s.updates / 60) * limit.per + limit.base, `score ${s.score} after ${s.updates / 60}s`);
      best = Math.max(best, s.score / Math.max(1, s.updates / 60));
    }
    assert.ok(s.score <= limit.max);
    assert.ok(s.level <= levels.length);
    return { s, best };
  };
  const a = play(7, hardest, true);
  assert.ok(a.s.level >= 10, `the lucky bot gets through challenges (${a.s.level})`);
  play(8, hardest, false);
  const b = play(9, L.LEVELS, false);
  assert.ok(b.s.stats.challenges >= 2, `the bot clears built-in challenges (${b.s.stats.challenges})`);
});

test("Challenges: draws in every look with a list, a name panel and a win", () => {
  for (const look of LOOKS) {
    const sb = makeSandbox();
    const canvas = sb.canvas(390, 487);
    const ends = [];
    const inst = sb.win.ArcadeGames.get("blocks").create(canvas, { mode: "challenge", look, seed: 5, levels: TWO, onEnd: (r) => ends.push(r) });
    inst.start();
    for (let i = 0; i < 400; i++) {
      const k = ["left", "up", "right", "down", "fire", "alt"][i % 6];
      if (i % 12 === 0) inst.input(k, true);
      if (i % 12 === 6) inst.input(k, false);
      if (i % 40 === 0) { inst.pointer("down", 60, 200); inst.pointer("move", 90, 230); inst.pointer("up", 90, 230); }
      sb.frames(1);
    }
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
  }
});
