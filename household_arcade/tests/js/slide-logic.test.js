"use strict";
// Slide Puzzle (wave 6): the rules, solvable shuffles, moves and the score, saving, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const S = loadLogic("slide-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const LIMIT = { perSecond: 10000, base: 10000, max: 10000 };

function types(evs) { return evs.map((e) => e.type); }
/** Solve a tray by brute force (breadth-first on small trays), returning the cells to tap. */
function solve3(tiles) {
  const key = (t) => t.join(",");
  const goal = key(S.solvedTiles(3));
  const prev = new Map([[key(tiles), null]]);
  let frontier = [tiles];
  while (frontier.length) {
    const next = [];
    for (const t of frontier) {
      if (key(t) === goal) {
        const path = [];
        let k = key(t);
        while (prev.get(k)) { const [p, cell] = prev.get(k); path.unshift(cell); k = p; }
        return path;
      }
      const gap = t.indexOf(0), r = Math.floor(gap / 3), c = gap % 3;
      for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const rr = r + dr, cc = c + dc;
        if (rr < 0 || cc < 0 || rr > 2 || cc > 2) continue;
        const cell = rr * 3 + cc, u = t.slice();
        u[gap] = u[cell]; u[cell] = 0;
        if (!prev.has(key(u))) { prev.set(key(u), [key(t), cell]); next.push(u); }
      }
    }
    frontier = next;
  }
  return null;
}

test("every mode starts with a shuffled, solvable tray, the gap at the bottom right", () => {
  for (const mode of Object.keys(S.MODES)) {
    for (let seed = 1; seed <= 40; seed++) {
      const s = S.create({ mode, seed });
      const n = S.MODES[mode].n;
      assert.equal(s.n, n);
      assert.equal(s.tiles.length, n * n);
      assert.deepEqual([...s.tiles].sort((a, b) => a - b), [...Array(n * n).keys()]);
      assert.equal(s.tiles[n * n - 1], 0, "the gap at the bottom right");
      assert.ok(S.solvable(s.tiles, n), `${mode} ${seed} solvable`);
      assert.ok(!S.isSolved(s.tiles));
      assert.ok(S.distance(s.tiles, n) >= S.MODES[mode].minDist, `${mode} ${seed} not close to solved`);
      assert.ok(s.scene >= 0 && s.scene < S.SCENES);
    }
  }
  assert.equal(S.create({}).mode, "four");
  assert.equal(S.create({ mode: "picture" }).picture, true);
});

test("solvable(): the parity rule, checked against a search on 3 × 3", () => {
  assert.equal(S.solvable(S.solvedTiles(3), 3), true);
  assert.equal(S.solvable([2, 1, 3, 4, 5, 6, 7, 8, 0], 3), false);
  assert.equal(S.solvable(S.solvedTiles(4), 4), true);
  assert.equal(S.solvable([2, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 0], 4), false);
  for (let seed = 1; seed <= 6; seed++) assert.ok(solve3(S.create({ mode: "three", seed }).tiles), "a search finds the way");
});

test("a tap slides the tile — or the whole line — into the gap; each tile moved is a move", () => {
  const s = S.create({ mode: "four", seed: 3 });
  s.tiles = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 0, 13, 14, 15];     // the gap at the bottom left
  assert.deepEqual(types(S.move(s, 5)), ["nope"], "not in the gap's row or column");
  assert.equal(s.moves, 0);
  const evs = S.move(s, 15);                 // the whole row slides left
  assert.deepEqual(types(evs), ["slide", "win"]);
  assert.equal(s.moves, 3, "three tiles moved");
  assert.equal(s.won, true);
  assert.equal(S.score(s), S.TOP - 3);
  assert.deepEqual(types(S.move(s, 14)), [], "nothing after the end");
});

test("the arrows move the tile on that side of the gap that way", () => {
  const s = S.create({ mode: "three", seed: 1 });
  s.tiles = [1, 2, 3, 4, 0, 5, 7, 8, 6];
  S.press(s, "left", true);                  // 5 moves left
  assert.deepEqual(s.tiles, [1, 2, 3, 4, 5, 0, 7, 8, 6]);
  S.press(s, "up", true);                    // 6 moves up: solved
  assert.ok(s.won);
  assert.equal(S.result(s).stats.moves, 2);
  assert.deepEqual(S.result(s).stats.summary[0], "Solved in 2 moves");
  const t = S.create({ mode: "three", seed: 1 });
  t.tiles = [1, 2, 3, 4, 5, 6, 7, 8, 0];
  assert.deepEqual(types(S.arrow(t, "down")), ["slide"]);
  assert.deepEqual(types(S.arrow(t, "up")), ["slide", "win"], "and back: solved again");
  t.tiles = [1, 2, 3, 4, 5, 6, 7, 8, 0];
  assert.deepEqual(types(S.arrow(t, "up")), ["nope"], "nothing below the gap");
});

test("solving every small tray from the seed: the score is TOP − moves; unsolved scores 0", () => {
  for (let seed = 1; seed <= 8; seed++) {
    const s = S.create({ mode: "picture", seed });
    assert.equal(S.result(s).score, 0);
    const path = solve3(s.tiles);
    for (const cell of path) S.move(s, cell);
    assert.ok(s.won && s.over);
    assert.equal(s.moves, path.length);
    assert.equal(S.result(s).score, S.TOP - path.length);
    assert.equal(S.result(s).stats.won, true);
  }
});

test("Peek is held with alt and never moves anything", () => {
  const s = S.create({ mode: "picture4", seed: 2 });
  const before = s.tiles.join();
  S.press(s, "alt", true);
  assert.equal(s.peek, true);
  S.press(s, "alt", false);
  assert.equal(s.peek, false);
  assert.equal(s.tiles.join(), before);
  assert.equal(s.moves, 0);
});

test("deterministic for a seed; save and restore plays on the same; bad saves are refused", () => {
  assert.equal(JSON.stringify(S.create({ mode: "five", seed: 9 })), JSON.stringify(S.create({ mode: "five", seed: 9 })));
  assert.notEqual(JSON.stringify(S.create({ mode: "five", seed: 9 }).tiles), JSON.stringify(S.create({ mode: "five", seed: 10 }).tiles));
  const s = S.create({ mode: "four", seed: 12 });
  for (let i = 0; i < 30; i++) { S.press(s, ["up", "left", "down", "right"][i % 4], true); S.step(s); }
  const copy = S.restore(JSON.parse(JSON.stringify(S.save(s))));
  for (let i = 0; i < 40; i++) { const a = ["left", "up", "right", "down"][i % 4]; S.press(s, a, true); S.press(copy, a, true); S.step(s); S.step(copy); }
  assert.equal(JSON.stringify(S.save(copy)), JSON.stringify(S.save(s)));
  assert.throws(() => S.restore({ mode: "four", n: 4, tiles: [1, 2] }), /can't be continued/);
  const bad = S.save(S.create({ mode: "three", seed: 1 }));
  bad.tiles = [2, 1, 3, 4, 5, 6, 7, 8, 0];
  assert.throws(() => S.restore(bad), /can't be continued/, "an unsolvable tray");
  assert.throws(() => S.restore(null));
});

test("honest score: a solved tray scores at most TOP however fast", () => {
  const s = S.create({ mode: "picture", seed: 4 });
  for (const cell of solve3(s.tiles)) S.move(s, cell);
  const r = S.result(s);
  assert.ok(r.score <= LIMIT.max && r.score <= 0 * LIMIT.perSecond + LIMIT.base);
  assert.ok(r.score >= S.MIN_SCORE);
});

test("fuzz: random arrows, taps and peeks never throw and keep the tray a permutation", () => {
  for (const mode of Object.keys(S.MODES)) {
    for (let seed = 1; seed <= 10; seed++) {
      const s = S.create({ mode, seed });
      let x = seed * 7919, moves = 0;
      for (let i = 0; i < 3000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 3 === 0) S.press(s, ["up", "down", "left", "right", "alt", "fire"][x % 6], x % 5 !== 0);
        if (x % 7 === 0) S.move(s, x % (s.n * s.n + 2) - 1);
        S.step(s);
        assert.ok(s.moves >= moves); moves = s.moves;
        assert.deepEqual([...s.tiles].sort((a, b) => a - b), [...Array(s.n * s.n).keys()]);
        assert.ok(S.solvable(s.tiles, s.n));
      }
    }
  }
});

test("slide renders and plays in every look (numbers and pictures), with arrows, taps and swipes", () => {
  for (const look of LOOKS) {
    for (const mode of ["three", "five", "picture", "picture4"]) {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("slide");
      assert.equal(def.name, "Slide Puzzle");
      assert.equal(def.controls, "touch");
      assert.equal(def.stateVersion, 1);
      assert.equal(def.race, true);
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r), options: { numbers: look === "paper" ? "off" : "on" } });
      inst.start();
      for (let i = 0; i < 300; i++) {
        if (i % 9 === 0) inst.input(["up", "left", "down", "right"][(i / 9) % 4], true);
        if (i % 20 === 0) { inst.pointer("down", 100 + (i % 200), 150); inst.pointer("up", 101 + (i % 200), 151); }
        if (i % 33 === 0) { inst.pointer("down", 200, 200); inst.pointer("up", 140, 200); }
        if (i === 100) inst.input("alt", true);
        if (i === 110) inst.input("alt", false);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(inst.logic.moves > 0, "the tiles moved");
      if (inst.state !== "over") assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});

test("a solved puzzle through the game file: onEnd with the moves in the summary and won", () => {
  const sb = makeSandbox();
  const ends = [];
  const inst = sb.win.ArcadeGames.get("slide").create(sb.canvas(390, 487), { mode: "picture", seed: 8, onEnd: (r) => ends.push(r) });
  inst.start();
  const s = inst.logic;
  for (const cell of solve3(s.tiles)) {
    const n = 3, BS = 212, ts = BS / n;
    // tap the middle of the cell: the canvas is 390 × 487 CSS px for 240 × 300 logical
    const k = Math.min(390 / 240, 487 / 300), x = (14 + (cell % n + 0.5) * ts) * k, y = (42 + (Math.floor(cell / n) + 0.5) * ts) * k;
    inst.pointer("down", x, y); inst.pointer("up", x, y);
    sb.frames(1);
  }
  sb.frames(2);
  assert.equal(inst.state, "over");
  assert.equal(ends.length, 1);
  assert.equal(ends[0].stats.won, true);
  assert.ok(ends[0].score > 9000);
  assert.match(ends[0].stats.summary[0], /^Solved in \d+ moves?$/);
});
