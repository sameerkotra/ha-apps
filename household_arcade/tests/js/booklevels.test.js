"use strict";
// The lists of named boards an AI model can add to (SPEC §11.9): Arrow Release's Picture boards, the Puzzle book of
// Car Park, Colour Sort, Bolt Sort, Dot Connect and Untangle, Picture Logic's Picture book, Word Search's Themes and
// Tile Match's Layouts. Each game's built-in list is usable, a list from the server replaces it (bad entries left
// out, an empty or broken list falls back), the game starts at the level asked for (SPEC §14) and plays that entry,
// every entry can be solved, saving and continuing keeps the list, and every look draws.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox, loadLogic } = require("./helpers");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const RUNS = { arrows: "book", parking: "book", watersort: "book", bolts: "book", connect: "book", untangle: "book" };
const SINGLE = { nonogram: "pictures", wordsearch: "themes", tiles: "layouts" };
const L = {};
for (const id of [...Object.keys(RUNS), ...Object.keys(SINGLE)]) L[id] = loadLogic(`${id}-logic.js`);

function solveBoard(id, s) {
  const G = L[id];
  if (id === "arrows") {
    for (let guard = 0; guard < 500 && s.phase === "play" && !s.over; guard++) {
      const a = s.board.arrows.findIndex((x, i) => G.isFree(s.board, i));
      if (a < 0) return false;
      G.tapCell(s, s.board.arrows[a].cells[0]);
    }
  } else if (id === "parking") {
    for (let guard = 0; guard < 80 && s.phase === "play" && !s.over; guard++) { const m = G.nextMove(s.vs, s.pos); if (!m) return false; G.slide(s, m.v, m.to); }
  } else if (id === "watersort") {
    const p = G.solve(s.tubes); if (!p) return false;
    for (const [a, b] of p) { G.tapTube(s, a); G.tapTube(s, b); }
  } else if (id === "bolts") {
    const p = G.solve(s.bolts); if (!p) return false;
    for (const [a, b] of p) { G.tapBolt(s, a); G.tapBolt(s, b); }
  } else if (id === "connect") {
    for (const piece of s.answer) { G.begin(s, piece[0]); for (let i = 1; i < piece.length; i++) G.extend(s, piece[i]); G.stop(s); }
  } else if (id === "untangle") {
    for (let i = 0; i < s.pts.length; i++) G.movePoint(s, i, s.home[i][0], s.home[i][1]);
    G.release(s);
  }
  return s.phase === "clear" || s.won;
}
function finish(s, G) { for (let i = 0; i < 200 && s.phase === "clear"; i++) G.step(s); }

test("every built-in entry passes the game's own check, and a broken list falls back to the built-in one", () => {
  for (const id of Object.keys(L)) {
    const G = L[id];
    assert.ok(G.LEVELS.length >= 5, id);
    for (const l of G.LEVELS) assert.equal(G.levelProblem(l), null, `${id}: ${l.name}`);
    assert.equal(G.usableLevels(undefined), G.LEVELS);
    assert.equal(G.usableLevels([]), G.LEVELS);
    assert.equal(G.usableLevels([{ name: "Broken" }, null, 7]), G.LEVELS);
    const mixed = G.usableLevels([{ name: "Broken" }, G.LEVELS[2]]);
    assert.deepEqual(mixed, [G.LEVELS[2]], `${id}: bad entries are left out`);
  }
});

test("run games: the Puzzle book starts where asked, plays every entry in order and is won after the last", () => {
  for (const [id, mode] of Object.entries(RUNS)) {
    const G = L[id], n = G.LEVELS.length;
    for (let start = 1; start <= n; start++) {
      const s = G.create({ mode, seed: 3, startLevel: start });
      assert.equal(s.level, start, `${id} starts at ${start}`);
      assert.equal(G.total(s), n);
      for (let lv = start; lv <= n; lv++) {
        assert.equal(s.level, lv);
        assert.ok(solveBoard(id, s), `${id} · board ${lv} can be solved`);
        finish(s, G);
      }
      assert.ok(s.won && s.over, `${id}: won after the last board`);
      assert.equal(G.result(s).level, n);
      assert.equal(G.result(s).stats.boards, n - start + 1);
    }
    assert.equal(G.create({ mode, startLevel: 99 }).level, n, `${id}: a start past the end plays the last`);
  }
});

test("a list from the server replaces the built-in one, and a saved game continues with it", () => {
  for (const [id, mode] of Object.entries({ ...RUNS, ...SINGLE })) {
    const G = L[id], list = [G.LEVELS[4], G.LEVELS[0]];
    const s = G.create({ mode, seed: 5, levels: list, startLevel: 2 });
    assert.equal(s.level, 2);
    const saved = JSON.parse(JSON.stringify(G.save(s)));
    const back = G.restore(saved, list);
    assert.equal(back.level, 2, id);
    assert.deepEqual(G.result(back).level, 2);
    if (RUNS[id]) {
      assert.equal(G.total(back), 2);
      assert.throws(() => G.restore(Object.assign({}, saved, { level: 3 }), list), /can't be continued/, `${id}: past the list`);
    }
  }
});

test("one-a-game modes: the entry asked for is played and its number is the level", () => {
  const N = L.nonogram;
  N.LEVELS.forEach((l, i) => {
    const s = N.create({ mode: "pictures", startLevel: i + 1 });
    assert.equal(s.n, l.picture.length);
    assert.equal(s.sol, l.picture.join("").replace(/#/g, "1").replace(/\./g, "0"), l.name);
    for (let c = 0; c < s.n * s.n; c++) if (s.sol[c] === "1" && s.cells[c] !== N.FILL) N.tap(s, c, false);
    assert.ok(s.won, `${l.name} solved`);
    assert.equal(N.result(s).level, i + 1);
    assert.equal(N.result(s).stats.name, l.name);
  });
  const W = L.wordsearch;
  W.LEVELS.forEach((l, i) => {
    const s = W.create({ mode: "themes", seed: 11 + i, startLevel: i + 1 });
    assert.equal(s.group, l.group);
    assert.equal(s.themeName, l.name);
    assert.equal(s.n, W.MODES[l.group].n);
    for (const w of s.words) assert.ok(l.words.includes(w.w), `${l.name}: ${w.w}`);
    assert.equal(W.result(s).level, i + 1);
  });
  const T = L.tiles;
  T.LEVELS.forEach((l, i) => {
    const s = T.create({ mode: "layouts", seed: 7, startLevel: i + 1 });
    assert.equal(s.alive.length, T.places(l).length, l.name);
    for (let guard = 0; guard < 200 && !s.over; guard++) {
      const f = T.pairsFree(s);
      if (!f.length) { T.shuffle(s); continue; }
      T.pick(s, f[0][0]); T.pick(s, f[0][1]);
    }
    assert.ok(s.won, `${l.name} cleared`);
    assert.equal(T.result(s).level, i + 1);
  });
});

test("the board makers and checks agree with the server's rules", () => {
  // Car Park: the fewest moves comes from a full search from the start
  const P = L.parking;
  assert.deepEqual(P.LEVELS.map((l) => { const b = P.fromGrid(l.grid); return P.fewestFrom(b.vs, b.pos); }), [3, 5, 6, 11, 13, 19]);
  assert.equal(P.fromGrid(["......", "......", "AAB...", "..C...", "......", "......"]), null, "B is one square");
  assert.equal(P.fromGrid(["......", "......", "....AA", "......", "......", "......"]), null, "already out");
  // Arrow Release: arrows only on the picture
  const A = L.arrows, s = A.create({ mode: "book", startLevel: 1 });
  for (const a of s.board.arrows) for (const c of a.cells) assert.ok(s.board.mask[c], "on the picture");
  // Dot Connect: a letter that touches itself isn't one line
  assert.equal(L.connect.fromGrid(["AABBB", "AACCB", "DDDCB", "EEECB", "FFFCC"]), null);
  // Untangle: a crossing drawing is refused
  assert.equal(L.untangle.levelProblem({ name: "X", points: [[0, 0], [8, 8], [0, 8], [8, 0], [4, 0], [4, 8]],
    edges: [[0, 1], [2, 3], [0, 4], [4, 3], [2, 5], [5, 1], [0, 2], [3, 1]] }), "crossing");
  // Colour Sort: every colour four times
  assert.equal(L.watersort.levelProblem({ name: "X", tubes: ["AAB", "BBBA", "A", "", ""] }), null);
  assert.equal(L.watersort.levelProblem({ name: "X", tubes: ["AAB", "BBB", "A", "", ""] }), "colour count");
  // Tile Match: a single tower can't be cleared two at a time
  assert.equal(L.tiles.levelProblem({ name: "X", layers: [{ dx: 0, dy: 0, rows: ["#"] }, { dx: 0, dy: 0, rows: ["#"] }] }), "tiles");
});

test("every look draws the list modes (and the start screen's still picture) without NaN", () => {
  for (const [id, mode] of Object.entries({ ...RUNS, ...SINGLE })) {
    for (const look of LOOKS) {
      const sb = makeSandbox({ extra: ["wordsearch-words.js", "wordsearch-logic.js", "wordsearch.js"] });
      const def = sb.win.ArcadeGames.get(id);
      assert.ok(def.modes.some((m) => m.id === mode), `${id} lists ${mode}`);
      const inst = def.create(sb.canvas(390, 487), { mode, seed: 5, look, startLevel: 3, levels: L[id].LEVELS, options: {} });
      inst.start(); sb.frames(10);
      assert.equal(inst.level, 3, `${id} · ${look}`);
      inst.input("right", true); inst.input("fire", true);
      inst.pointer("down", 120, 150); inst.pointer("move", 140, 160); inst.pointer("up", 140, 160);
      sb.frames(10);
      assert.ok(!sb.counts.nonFinite, `${id} · ${look}: no NaN drawn`);
      inst.destroy();
    }
  }
});
