"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const M = loadLogic("mines-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const LIMIT = { per: 600, base: 1000, max: 2000000 };

function types(evs) { return evs.map((e) => e.type); }
function run(s, n) { const all = []; for (let i = 0; i < n && !s.over; i++) all.push(...M.step(s)); return all; }
function centre(s, i) {
  const m = M.geom(s);
  return [m.ox + (i % s.cols) * m.cs + m.cs / 2, m.oy + Math.floor(i / s.cols) * m.cs + m.cs / 2];
}
function idx(s, x, y) { return y * s.cols + x; }
// Lay a board by hand: mines at the given squares.
function lay(s, mines) {
  s.mine.fill(0);
  for (const i of mines) s.mine[i] = 1;
  s.mines = mines.length;
  s.safeLeft = s.cell.length - mines.length;
  for (let i = 0; i < s.cell.length; i++) s.adj[i] = M.neighbours(s, i).reduce((n, j) => n + s.mine[j], 0);
  s.placed = true;
}

test("the three boards and their mines; more mines each level, capped", () => {
  for (const [mode, cols, rows, mines] of [["easy", 9, 9, 10], ["medium", 10, 12, 18], ["hard", 12, 15, 32]]) {
    const s = M.create({ mode, seed: 1 });
    assert.equal(s.cols, cols); assert.equal(s.rows, rows); assert.equal(s.mines, mines);
    const m = M.MODES[mode];
    assert.ok(m.cs >= 16);
    assert.ok(m.ox >= 0 && m.ox + cols * m.cs <= 240, mode);
    assert.ok(m.oy >= 40 && m.oy + rows * m.cs <= 284, mode);
    assert.ok(M.minesFor(mode, 2) > mines);
    assert.equal(M.minesFor(mode, 99), m.cap);
    assert.ok(m.cap <= cols * rows - 9);
  }
  assert.equal(M.create({ mode: "nope" }).mode, "easy");
});

test("the first square opened is never a mine nor next to one, and floods open", () => {
  for (let seed = 1; seed <= 40; seed++) {
    const s = M.create({ mode: "hard", seed });
    const first = idx(s, seed % s.cols, (seed * 7) % s.rows);
    const evs = M.open(s, first);
    assert.equal(s.mine[first], 0);
    assert.equal(s.adj[first], 0);
    assert.equal(s.mine.reduce((a, b) => a + b, 0), s.mines);
    assert.ok(!s.over);
    const open = types(evs).includes("open") ? evs.find((e) => e.type === "open").count : 0;
    assert.ok(open >= 4, "at least the square and its neighbours");
    for (const j of M.neighbours(s, first)) assert.equal(s.cell[j], M.OPEN);
    assert.equal(s.score, open);
    // every open square with no mines around has only open neighbours
    for (let i = 0; i < s.cell.length; i++) {
      if (s.cell[i] === M.OPEN && s.adj[i] === 0) for (const j of M.neighbours(s, i)) assert.equal(s.cell[j], M.OPEN);
    }
  }
});

test("numbers count the mines around; flags toggle and count down the mines left", () => {
  const s = M.create({ mode: "easy", seed: 3 });
  lay(s, [idx(s, 0, 0), idx(s, 2, 0)]);
  assert.equal(s.adj[idx(s, 1, 0)], 2);
  assert.equal(s.adj[idx(s, 1, 1)], 2);
  assert.equal(s.adj[idx(s, 3, 1)], 1);
  assert.deepEqual(types(M.flag(s, 0)), ["flag"]);
  assert.equal(s.mines - s.flags, 1);
  assert.deepEqual(M.open(s, 0), [], "a flagged square doesn't open");
  assert.deepEqual(types(M.flag(s, 0)), ["unflag"]);
  assert.equal(s.flags, 0);
});

test("opening a number whose flags match opens the rest; a wrong flag hits a mine", () => {
  const s = M.create({ mode: "easy", seed: 4 });
  lay(s, [idx(s, 0, 0), idx(s, 8, 8)]);
  M.open(s, idx(s, 1, 1));
  assert.equal(s.cell[idx(s, 1, 1)], M.OPEN);
  assert.equal(s.cell[idx(s, 0, 1)], M.COVERED);
  assert.deepEqual(M.open(s, idx(s, 1, 1)), [], "no flags yet: nothing");
  M.flag(s, 0);
  const evs = M.open(s, idx(s, 1, 1));
  assert.ok(types(evs).includes("open"));
  assert.equal(s.cell[idx(s, 0, 1)], M.OPEN);
  assert.ok(!s.over);

  const t = M.create({ mode: "easy", seed: 5 });
  lay(t, [idx(t, 0, 0), idx(t, 8, 8)]);
  M.open(t, idx(t, 1, 1));
  M.flag(t, idx(t, 1, 0));       // the wrong square
  const e2 = M.open(t, idx(t, 1, 1));
  assert.deepEqual(types(e2), ["boom", "gameover"]);
  assert.equal(t.hit, 0);
  assert.equal(M.result(t).stats.cause, "mine");
});

test("opening a mine ends the game; nothing more happens after", () => {
  const s = M.create({ mode: "medium", seed: 6 });
  M.open(s, 0);
  const mine = s.mine.indexOf(1);
  const evs = M.open(s, mine);
  assert.deepEqual(types(evs), ["boom", "gameover"]);
  assert.ok(s.over);
  assert.equal(s.hit, mine);
  const score = s.score;
  assert.deepEqual(M.open(s, 5), []);
  assert.deepEqual(M.press(s, "fire", true), []);
  assert.deepEqual(run(s, 10), []);
  assert.equal(s.score, score);
});

test("clearing every safe square: bonus, next level after a pause, more mines", () => {
  const s = M.create({ mode: "easy", seed: 7 });
  M.open(s, 40);
  run(s, 60 * 3);                         // three seconds on this board
  let evs = [];
  for (let i = 0; i < s.cell.length && !s.pause; i++) if (!s.mine[i] && s.cell[i] === M.COVERED) evs = M.open(s, i);
  const clear = evs.find((e) => e.type === "clear");
  assert.ok(clear);
  assert.equal(clear.bonus, 10 * 10 + 5 * 10 - 3);
  assert.equal(s.score, 81 - 10 + clear.bonus);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.level, 2);
  assert.deepEqual(M.open(s, 0), [], "nothing during the pause");
  run(s, M.CLEAR_PAUSE);
  assert.equal(s.pause, 0);
  assert.equal(s.mines, 12);
  assert.equal(s.placed, false);
  assert.ok(s.cell.every((c) => c === M.COVERED));
  assert.equal(M.result(s).stats.boards, 1);
});

test("touch: a tap opens, a long press flags, a drag does nothing; flag mode makes taps flag", () => {
  const s = M.create({ mode: "easy", seed: 8 });
  let [x, y] = centre(s, 40);
  M.touchDown(s, x, y); run(s, 3);
  assert.ok(types(M.touchUp(s, x + 2, y)).includes("open"));
  const covered = s.cell.findIndex((c) => c === M.COVERED);
  [x, y] = centre(s, covered);
  M.touchDown(s, x, y);
  let evs = run(s, M.LONG_PRESS - 1);
  assert.equal(s.cell[covered], M.COVERED);
  evs = run(s, 1);
  assert.deepEqual(types(evs), ["flag"]);
  assert.deepEqual(M.touchUp(s, x, y), [], "lifting after a long press doesn't open");
  assert.equal(s.cell[covered], M.FLAG);
  // long press again takes the flag off
  M.touchDown(s, x, y); run(s, M.LONG_PRESS); M.touchUp(s, x, y);
  assert.equal(s.cell[covered], M.COVERED);
  // a finger that moves is neither a tap nor a long press
  M.touchDown(s, x, y); M.touchMove(s, x + 10, y); run(s, 40);
  assert.deepEqual(M.touchUp(s, x + 10, y), []);
  assert.equal(s.cell[covered], M.COVERED);
  // flag mode (the Flag button: alt without the keyboard cursor)
  assert.deepEqual(types(M.press(s, "alt", true)), ["mode"]);
  assert.ok(s.flagMode);
  M.touchDown(s, x, y);
  assert.deepEqual(types(M.touchUp(s, x, y)), ["flag"]);
  M.touchDown(s, x, y);
  assert.deepEqual(types(M.touchUp(s, x, y)), ["unflag"]);
  M.press(s, "alt", true);
  assert.ok(!s.flagMode);
  assert.equal(M.cellAt(s, 1, 1), -1);
});

test("keyboard: arrows show then move the cursor, Space opens, alt flags at the cursor", () => {
  const s = M.create({ mode: "easy", seed: 9 });
  assert.ok(!s.showCur);
  M.press(s, "left", true);
  assert.ok(s.showCur);
  assert.deepEqual(s.cur, { x: 4, y: 4 });
  M.press(s, "left", true); M.press(s, "up", true);
  assert.deepEqual(s.cur, { x: 3, y: 3 });
  for (let i = 0; i < 20; i++) M.press(s, "up", true);
  assert.equal(s.cur.y, 0, "stays on the board");
  assert.ok(types(M.press(s, "fire", true)).includes("open"));
  const covered = s.cell.findIndex((c) => c === M.COVERED);
  s.cur = { x: covered % s.cols, y: Math.floor(covered / s.cols) };
  assert.deepEqual(types(M.press(s, "alt", true)), ["flag"]);
  assert.ok(!s.flagMode);
  assert.deepEqual(M.press(s, "alt", false), [], "key-up does nothing");
});

test("deterministic for a seed and the same moves; save and restore carry on the same game", () => {
  const play = (s, n) => { for (let i = 0; i < n && !s.over; i++) { if (i % 7 === 0) M.press(s, ["up", "right", "down", "fire", "alt"][(i / 7) % 5], true); M.step(s); } };
  const a = M.create({ mode: "medium", seed: 31 }), b = M.create({ mode: "medium", seed: 31 });
  play(a, 300); play(b, 300);
  assert.deepEqual(a, b);
  const s = M.create({ mode: "hard", seed: 32 });
  M.open(s, 50);
  M.touchDown(s, 100, 100);
  const copy = M.restore(JSON.parse(JSON.stringify(M.save(s))));
  s.touch = null;
  for (let i = 0; i < s.cell.length && !s.over; i++) {
    if (s.mine[i]) continue;
    M.open(s, i); M.open(copy, i); M.step(s); M.step(copy);
  }
  assert.equal(copy.score, s.score);
  assert.deepEqual(copy.cell, s.cell);
  assert.deepEqual(M.result(copy), M.result(s));
  assert.throws(() => M.restore({ cell: [] }), /can't be continued/);
  assert.throws(() => M.restore(null));
});

// The fastest possible player: it knows where the mines are and opens every safe square in the
// same update, board after board, then opens a mine.
function cheat(s, untilBoards) {
  if (s.pause || s.over) return;
  if (!s.placed) M.open(s, Math.floor(s.cell.length / 2));
  if (s.stats.boards >= untilBoards) { M.open(s, s.mine.indexOf(1)); return; }
  for (let i = 0; i < s.cell.length && !s.pause; i++) if (!s.mine[i] && s.cell[i] === M.COVERED) M.open(s, i);
}

test("a perfect, instant player stays inside the honest-score limits", () => {
  let fastest = 0;
  for (const mode of ["easy", "medium", "hard"]) {
    const s = M.create({ mode, seed: 41 });
    let u = 0;
    while (!s.over && u++ < 60 * 600) {
      cheat(s, 60);
      M.step(s);
      const secs = s.updates / 60;
      assert.ok(s.score <= secs * LIMIT.per + LIMIT.base, `${mode}: score ${s.score} after ${secs}s`);
      assert.ok(s.score <= LIMIT.max);
      assert.ok(s.level <= 100);
      if (secs > 5) fastest = Math.max(fastest, (s.score - LIMIT.base) / secs);
    }
    assert.ok(s.over);
    assert.ok(s.stats.boards >= 60);
  }
  // a board's worth at most every CLEAR_PAUSE updates (the worst case: Hard at the mine cap)
  const worst = 12 * 15 - 45 + 10 * 45 + 5 * 45;
  assert.ok(worst / ((M.CLEAR_PAUSE + 1) / 60) <= LIMIT.per);
  assert.ok(worst <= LIMIT.base);
  console.log(`mines: fastest rate above base ${fastest.toFixed(1)} a second`);
});

test("the level stops at 99", () => {
  const s = M.create({ mode: "easy", seed: 2 });
  s.level = 99;
  M.open(s, 40);
  for (let i = 0; i < s.cell.length && !s.pause; i++) if (!s.mine[i] && s.cell[i] === M.COVERED) M.open(s, i);
  assert.equal(s.level, 99);
});

for (const look of LOOKS) {
  test(`mines renders and plays in the ${look} look`, () => {
    for (const mode of ["easy", "medium", "hard", "boards"]) {
      const sb = makeSandbox({ extra: ["mines-logic.js", "mines.js"] });
      const def = sb.win.ArcadeGames.get("mines");
      assert.equal(def.controls, "touch");
      assert.equal(def.buttons[0].action, "alt");
      assert.equal(def.stateVersion, 1);
      const canvas = sb.canvas(390, 487);
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      const r = inst.renderer;
      const tapAt = (lx, ly, hold) => {
        // logical → CSS px through the renderer's mapping
        const a = r.toLogical(0, 0), b = r.toLogical(100, 100);
        const sx = 100 / (b.x - a.x), sy = 100 / (b.y - a.y);
        const cx = (lx - a.x) * sx, cy = (ly - a.y) * sy;
        inst.pointer("down", cx, cy);
        sb.frames(hold);
        inst.pointer("move", cx + 1, cy);
        inst.pointer("up", cx + 1, cy);
      };
      for (let i = 0; i < 300 && inst.state === "running"; i++) {
        const s = inst.logic;
        if (i % 25 === 0) {
          const safe = s.placed ? s.cell.findIndex((c, j) => c === M.COVERED && !s.mine[j]) : 40;
          if (safe >= 0) tapAt(...centre(s, safe), 2);
        }
        if (i === 60) {
          const mine = s.mine.indexOf(1);
          if (mine >= 0) tapAt(...centre(s, mine), M.LONG_PRESS + 4);
        }
        if (i % 30 === 5) { inst.input(["left", "up", "right", "down"][(i / 30) % 4 | 0], true); inst.input("left", false); }
        if (i === 100) { inst.input("alt", true); inst.input("alt", false); }
        if (i === 150) { inst.pointer("down", 10, 10); inst.pointer("up", 10, 10); inst.input("alt", true); }
        sb.frames(1);
      }
      assert.ok(inst.logic.stats.squares > 0, "taps opened squares");
      assert.ok(inst.logic.stats.flags > 0, "a long press flagged");
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      // end it on a mine and draw the end
      const s = inst.logic;
      sb.frames(M.CLEAR_PAUSE);
      if (inst.state === "running") {
        if (!s.placed) tapAt(...centre(s, 40), 1);
        if (s.flagMode) inst.input("alt", true);   // the cursor is hidden after a touch: alt turns flag mode off
        assert.ok(!s.flagMode);
        const mine = s.mine.findIndex((v, j) => v && s.cell[j] === M.COVERED);
        tapAt(...centre(s, mine), 1); sb.frames(2);
      }
      assert.equal(inst.state, "over");
      assert.equal(ends.length, 1);
      assert.equal(ends[0].stats.mode, mode);
      for (const other of LOOKS) inst.setLook(other);
      assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  });
}

// ---- Shaped boards (the level list) ----
const FULL = (cols, rows) => Array.from({ length: rows }, () => "#".repeat(cols));
const RING = ["########", "########", "##....##", "##....##", "##....##", "##....##", "########", "########"];

test("Shaped boards: built-in or the session's list; bad boards are skipped", () => {
  assert.equal(M.usableLevels(undefined), M.LEVELS);
  assert.ok(M.LEVELS.length >= 5 && M.LEVELS.length <= 10);
  for (const b of M.LEVELS) {
    const info = M.shapeInfo(b.shape);
    assert.ok(info, b.name);
    const [lo, hi] = M.mineRange(info.squares);
    assert.ok(b.mines >= lo && b.mines <= hi, b.name);
    const L = M.layoutFor(info.cols, info.rows);
    assert.ok(L.cs >= 16 && L.ox >= 0 && L.ox + info.cols * L.cs <= 240 && L.oy >= 40 && L.oy + info.rows * L.cs <= 284, b.name);
  }
  const bad = [
    { name: "Few", mines: 3, shape: ["######", "######", "######", "######", "......", "#....."] },  // too few, not joined
    { name: "Islands", mines: 6, shape: ["####.####", "####.####", "####.####", "####.####", "####.####", "####.####"] },
    { name: "Corner only", mines: 6, shape: ["#####.", "####.#", "######", "######", "######", "######"] },  // ok (joined)
    { name: "Crowded", mines: 20, shape: FULL(8, 8) },
    { name: "Sparse", mines: 4, shape: FULL(8, 8) },
    { name: "Ragged", mines: 6, shape: ["########", "#######", "########", "########", "########", "########"] },
    { name: "Empty edge", mines: 6, shape: ["........", "########", "########", "########", "########", "########"] },
    { name: "Tiny", mines: 3, shape: FULL(5, 8) },
    { name: "Odd", mines: 6, shape: ["####x###", "########", "########", "########", "########", "########"] },
    { name: "Half", mines: 6.5, shape: FULL(8, 8) },
  ];
  assert.equal(M.usableLevels(bad).length, 1);
  assert.equal(M.usableLevels(bad)[0].name, "Corner only");
  assert.equal(M.usableLevels(bad.filter((b) => b.name !== "Corner only")), M.LEVELS);
  assert.equal(M.usableLevels([null, 3, "x"]), M.LEVELS);
  // the extremes the checks allow
  assert.deepEqual(M.mineRange(30), [3, 6]);
  assert.deepEqual(M.mineRange(180), [15, 39]);
  assert.equal(M.layoutFor(12, 15).cs, 16);
  assert.equal(M.layoutFor(6, 6).cs, 30);
});

function clearAll(s) {
  if (!s.placed) M.open(s, s.hole.indexOf(0));
  let evs = [];
  for (let i = 0; i < s.cell.length && !s.pause && !s.over; i++) if (!s.hole[i] && !s.mine[i] && s.cell[i] === M.COVERED) evs = evs.concat(M.open(s, i));
  return evs;
}

test("Shaped boards: holes are never squares; a cleared board brings the next; the last one wins", () => {
  const two = [{ name: "Ring", mines: 6, shape: RING }, { name: "Field", mines: 10, shape: FULL(10, 9) }];
  const s = M.create({ mode: "boards", seed: 3, levels: two });
  assert.equal(s.boards.length, 2);
  assert.equal(s.cols, 8); assert.equal(s.rows, 8);
  assert.equal(s.mines, 6);
  assert.equal(s.boardName, "Ring");
  assert.equal(s.safeLeft, 48 - 6);
  assert.ok(!M.isHole(s, s.cur.y * s.cols + s.cur.x), "the cursor starts on a square");
  const hole = 3 * 8 + 3;
  assert.ok(M.isHole(s, hole));
  assert.deepEqual(M.open(s, hole), []);
  assert.deepEqual(M.flag(s, hole), []);
  const [hx, hy] = centre(s, hole);
  assert.equal(M.cellAt(s, hx, hy), -1);
  M.open(s, 0);
  assert.equal(s.mine.reduce((a, b) => a + b, 0), 6);
  for (let i = 0; i < 64; i++) if (s.hole[i]) assert.equal(s.mine[i], 0);
  // a square next to the hole doesn't count the hole as a neighbour
  assert.equal(M.neighbours(s, 2 * 8 + 2).length, 5);
  let evs = clearAll(s);
  assert.deepEqual(types(evs).slice(-2), ["clear", "level"]);
  assert.equal(s.level, 2);
  assert.ok(!s.over);
  run(s, M.CLEAR_PAUSE);
  assert.equal(s.cols, 10); assert.equal(s.rows, 9); assert.equal(s.mines, 10);
  assert.equal(s.boardName, "Field");
  assert.ok(s.cell.every((c) => c === M.COVERED));
  // saved on the last board, continued with the same list
  const back = M.restore(JSON.parse(JSON.stringify(M.save(s))), two);
  assert.ok(!("boards" in M.save(s)));
  assert.equal(back.boards.length, 2);
  for (const t of [s, back]) {
    evs = clearAll(t);
    assert.deepEqual(types(evs).slice(-2), ["clear", "win"]);
    assert.ok(t.won && t.over);
    assert.equal(t.level, 2);
    const r = M.result(t);
    assert.equal(r.stats.won, true); assert.equal(r.stats.cause, "won"); assert.equal(r.stats.boards, 2);
    assert.deepEqual(run(t, 10), []);
  }
  assert.equal(back.score, s.score);
  // a bad list falls back to the built-ins; restoring without a list too
  const t = M.create({ mode: "boards", seed: 4, levels: [{ name: "Bad", mines: 1, shape: ["#"] }] });
  assert.equal(t.boards, M.LEVELS);
  assert.equal(t.boardName, M.LEVELS[0].name);
  assert.equal(M.restore(JSON.parse(JSON.stringify(M.save(t)))).boards, M.LEVELS);
  assert.throws(() => M.restore(Object.assign(M.save(t), { hole: [] })), /can't be continued/);
});

test("Shaped boards: deterministic, and a restored game plays on the same", () => {
  const a = M.create({ mode: "boards", seed: 9 }), b = M.create({ mode: "boards", seed: 9 });
  M.open(a, 20); M.open(b, 20);
  assert.deepEqual(a, b);
  const c = M.restore(JSON.parse(JSON.stringify(M.save(a))));
  for (let i = 0; i < a.cell.length && !a.over; i++) {
    if (a.mine[i] || a.hole[i]) continue;
    M.open(a, i); M.open(c, i); M.step(a); M.step(c);
  }
  run(a, M.CLEAR_PAUSE + 2); run(c, M.CLEAR_PAUSE + 2);
  assert.deepEqual({ ...M.save(c), version: 0 }, { ...M.save(a), version: 0 }, "the same apart from the drawing counter");
  assert.equal(a.level, 2);
});

test("Shaped boards: a perfect, instant player stays inside the limits on the hardest boards allowed", () => {
  // the biggest board (12 × 15 = 180 squares) with the most mines allowed (22 % = 39), 60 times over
  const hardest = Array.from({ length: 60 }, (_, i) => ({ name: "Huge " + i, mines: 39, shape: FULL(12, 15) }));
  assert.equal(M.usableLevels(hardest).length, 60);
  let fastest = 0;
  const s = M.create({ mode: "boards", seed: 41, levels: hardest });
  while (!s.over) {
    if (!s.pause) clearAll(s);
    M.step(s);
    const secs = s.updates / 60;
    assert.ok(s.score <= secs * LIMIT.per + LIMIT.base, `score ${s.score} after ${secs}s`);
    assert.ok(s.score <= LIMIT.max);
    assert.ok(s.level <= hardest.length);
    if (secs > 5) fastest = Math.max(fastest, (s.score - LIMIT.base) / secs);
  }
  assert.ok(s.won);
  assert.equal(s.stats.boards, 60);
  const worst = 180 + 15 * 39;
  assert.ok(worst <= LIMIT.base && worst / ((M.CLEAR_PAUSE + 1) / 60) <= LIMIT.per);
  console.log(`mines boards: fastest rate above base ${fastest.toFixed(1)} a second`);
});
