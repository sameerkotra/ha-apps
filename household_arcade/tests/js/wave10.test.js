"use strict";
// Waves 9 and 10 (spec/GAMES.md "Wave 9 — Arrow Release", "Wave 10"): Arrow Release, Car Park, Colour Sort, Bolt Sort,
// Dot Connect and Untangle — boards always solvable, the same seed the same board (with no engine-dependent maths), the
// moves' rules, the score, numbered Levels boards that are the same everywhere and start where asked (SPEC §14),
// saving and continuing, the registry and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { makeSandbox, loadLogic } = require("./helpers");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const plain = (x) => JSON.parse(JSON.stringify(x));
const GAMES = {
  arrows: ["Arrow Release", ["little", "classic", "big", "twisty", "levels", "book"]],
  parking: ["Car Park", ["little", "classic", "hard", "levels", "book"]],
  watersort: ["Colour Sort", ["little", "classic", "big", "levels", "book"]],
  bolts: ["Bolt Sort", ["little", "classic", "hidden", "levels", "book"]],
  connect: ["Dot Connect", ["little", "classic", "big", "levels", "book"]],
  untangle: ["Untangle", ["little", "classic", "big", "levels", "book"]],
};
const L = {
  arrows: loadLogic("arrows-logic.js"), parking: loadLogic("parking-logic.js"), watersort: loadLogic("watersort-logic.js"),
  bolts: loadLogic("bolts-logic.js"), connect: loadLogic("connect-logic.js"), untangle: loadLogic("untangle-logic.js"),
};
const boardOf = {
  arrows: (s) => s.board, parking: (s) => [s.vs, s.pos], watersort: (s) => s.tubes, bolts: (s) => s.bolts,
  connect: (s) => [s.dots, s.answer], untangle: (s) => [s.pts, s.edges],
};

/** Solve the current board with the game's own rules; true when it is cleared. */
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

test("every board of every mode can be solved (many seeds, and Levels boards across the 200)", () => {
  for (const [id, [, modes]] of Object.entries(GAMES)) {
    for (const mode of modes) {
      const seeds = id === "parking" && mode !== "little" ? 3 : 12;
      for (let seed = 1; seed <= seeds; seed++) {
        const s = L[id].create({ mode, seed: seed * 7919, startLevel: mode === "levels" ? 1 + (seed * 37) % 200 : 1 });
        assert.ok(solveBoard(id, s), `${id} · ${mode} · seed ${seed}`);
        assert.ok(L[id].score(s) >= L[id].MIN_SCORE, `${id} scores when solved`);
      }
    }
  }
});

test("the same seed gives the same board; another seed another; Levels board n is the same for everyone", () => {
  for (const id of Object.keys(GAMES)) {
    const a = L[id].create({ mode: "classic", seed: 4242 }), b = L[id].create({ mode: "classic", seed: 4242 });
    const c = L[id].create({ mode: "classic", seed: 777 });
    assert.deepEqual(plain(boardOf[id](a)), plain(boardOf[id](b)), id);
    assert.notDeepEqual(plain(boardOf[id](a)), plain(boardOf[id](c)), id);
    const l1 = L[id].create({ mode: "levels", seed: 1, startLevel: 12 }), l2 = L[id].create({ mode: "levels", seed: 999, startLevel: 12 });
    assert.equal(l1.level, 12);
    assert.deepEqual(plain(boardOf[id](l1)), plain(boardOf[id](l2)), `${id}: board 12`);
    // a run from level 11 reaches board 12 as the same board
    const run = L[id].create({ mode: "levels", seed: 5, startLevel: 11 });
    assert.ok(solveBoard(id, run), id);
    for (let i = 0; i < 200 && run.level === 11; i++) L[id].step(run);
    assert.equal(run.level, 12, id);
    assert.deepEqual(plain(boardOf[id](run)), plain(boardOf[id](l1)), `${id}: board 12 after 11`);
    assert.equal(L[id].create({ mode: "levels", startLevel: 900 }).level, 200);
  }
});

test("Levels: the boards' scores add up, the run carries on, and board 200 is the end", () => {
  for (const id of Object.keys(GAMES)) {
    const s = L[id].create({ mode: "levels", seed: 3, startLevel: 199 });
    assert.ok(solveBoard(id, s));
    const first = L[id].score(s);
    for (let i = 0; i < 200 && s.level === 199; i++) L[id].step(s);
    assert.equal(s.level, 200);
    assert.ok(solveBoard(id, s), id);
    assert.ok(s.won && s.over, id);
    assert.ok(L[id].score(s) > first, id);
    assert.equal(L[id].result(s).level, 200);
    assert.equal(L[id].result(s).stats.boards, 2);
  }
});

test("unsolved scores nothing; a solved board scores 10,000 less its costs", () => {
  for (const id of Object.keys(GAMES)) {
    const s = L[id].create({ mode: "classic", seed: 11 });
    assert.equal(L[id].score(s), 0, id);
    assert.equal(L[id].result(s).stats.notSaved.length > 0, true);
    for (let i = 0; i < 60 * 30; i++) L[id].step(s);                 // 30 seconds on the clock
    assert.ok(solveBoard(id, s), id);
    assert.ok(L[id].score(s) < L[id].TOP && L[id].score(s) >= L[id].MIN_SCORE, id);
    assert.equal(L[id].result(s).stats.won, true);
  }
});

test("Arrow Release: a free arrow leaves, a blocked one bumps and costs a heart; Little has no hearts", () => {
  const A = L.arrows, s = A.create({ mode: "classic", seed: 21 });
  const free = s.board.arrows.findIndex((x, i) => A.isFree(s.board, i));
  const stuck = s.board.arrows.findIndex((x, i) => !A.isFree(s.board, i));
  assert.ok(free >= 0 && stuck >= 0);
  const before = A.left(s);
  assert.deepEqual(A.tapCell(s, s.board.arrows[free].cells[0]).map((e) => e.type), ["release"]);
  assert.equal(A.left(s), before - 1);
  assert.deepEqual(A.tapCell(s, s.board.arrows[stuck].cells[0]).map((e) => e.type), ["bump", "heart"]);
  assert.equal(s.hearts, 2);
  A.tapCell(s, s.board.arrows[stuck].cells[0]); A.tapCell(s, s.board.arrows[stuck].cells[0]);
  assert.ok(s.over && !s.won && s.cause === "hearts");
  const little = A.create({ mode: "little", seed: 21 }), lb = little.board.arrows.findIndex((x, i) => !A.isFree(little.board, i));
  if (lb >= 0) { A.tapCell(little, little.board.arrows[lb].cells[0]); assert.equal(little.over, false); assert.equal(little.mistakes, 1); }
  // twisty: a bent arrow leaves when its head's way is clear, whatever its body is
  const t = A.create({ mode: "twisty", seed: 5 });
  assert.ok(t.board.arrows.some((a) => a.cells.length > 2 && new Set(a.cells.map((c) => c % t.board.n)).size > 1 && new Set(a.cells.map((c) => Math.floor(c / t.board.n))).size > 1));
});

test("Car Park: vehicles slide only along their length, the stated fewest is exact, the red car leaves", () => {
  const P = L.parking, s = P.create({ mode: "little", seed: 8 });
  const space = P.explore(s.vs, s.pos), d = P.distances(space);
  assert.equal(d[0], s.fewest);
  const r = P.range(s.vs, s.pos, 1);
  assert.deepEqual(P.slide(s, 1, r[1] + 1).map((e) => e.type), ["nope"]);        // past what's free (or off the grid)
  assert.equal(s.moves, 0);
  let moves = 0;
  for (; moves < 40 && !s.over; moves++) { const m = P.nextMove(s.vs, s.pos); P.slide(s, m.v, m.to); }
  assert.ok(s.won);
  assert.equal(s.moves, s.fewest);
  assert.equal(P.score(s), P.TOP);
  const u = P.create({ mode: "classic", seed: 2 }), m = P.nextMove(u.vs, u.pos);
  P.slide(u, m.v, m.to); P.undo(u);
  assert.deepEqual(u.pos, P.create({ mode: "classic", seed: 2 }).pos);
  assert.equal(u.moves, 1);                                            // an undone move still counts
});

test("Colour Sort and Bolt Sort: pours and moves follow the rules", () => {
  const W = L.watersort;
  const tubes = [[0, 0, 1], [1], [], [0, 0, 0, 0]];
  assert.equal(W.pourable(tubes, 0, 1), 1);            // 1 onto 1
  assert.equal(W.pourable(tubes, 1, 0), 1);
  assert.equal(W.pourable(tubes, 0, 3), 0);            // full
  assert.equal(W.pourable(tubes, 3, 2), 4);            // a whole tube into an empty one (allowed, if useless)
  assert.equal(W.pourable([[0, 1, 1, 1], [2, 2, 2]], 0, 1), 0);   // another colour on top
  assert.equal(W.pourable([[2, 1, 1], [0, 0]], 0, 1), 0);
  assert.equal(W.pourable([[2, 1, 1], [0, 1]], 0, 1), 2);
  const w = W.create({ mode: "little", seed: 3 });
  W.press(w, "fire", true);
  assert.equal(w.sel, 0);
  W.restart(w);
  assert.deepEqual(w.tubes, w.dealt);
  const B = L.bolts;
  assert.equal(B.canMove([[0, 1], [1, 1, 1, 1]], 0, 1), false);    // no room
  assert.equal(B.canMove([[0, 1], [2]], 0, 1), false);             // another colour
  assert.equal(B.canMove([[0, 1], [1]], 0, 1), true);
  const h = B.create({ mode: "hidden", seed: 6 });
  assert.ok(h.hidden.some((col) => col.some(Boolean)), "nuts under the top start hidden");
  assert.ok(h.hidden.every((col) => !col.length || col[col.length - 1] === false), "the top nut is shown");
  const path = B.solve(h.bolts);
  B.tapBolt(h, path[0][0]); B.tapBolt(h, path[0][1]);
  assert.ok(h.hidden[path[0][0]].every((x, i, a) => i !== a.length - 1 || x === false), "a nut that comes to the top shows");
});

test("Dot Connect: lines can't cross dots of another colour, drawing over a line cuts it, every square must be filled", () => {
  const C = L.connect, s = C.create({ mode: "classic", seed: 12 });
  const a = s.answer[0], b = s.answer[1];
  C.begin(s, a[0]);
  for (let i = 1; i < a.length; i++) C.extend(s, a[i]);
  assert.ok(C.joined(s, 0));
  // colour 1's line through one of colour 0's squares cuts colour 0 there
  const n = s.n;
  const sq = a[1];
  const nb = C.neighbours(n, sq).find((x) => s.dots[x] < 0 && a.indexOf(x) < 0);
  if (nb !== undefined) {
    s.paths[1] = [b[0]];
    s.pen = 1;
    s.paths[1].push(nb);                                   // put colour 1's end next to the square (test set-up)
    const evs = C.extend(s, sq);
    assert.ok(evs.some((e) => e.type === "cut" && e.colour === 0));
    assert.ok(!C.joined(s, 0));
  }
  const little = C.create({ mode: "little", seed: 1 });
  assert.equal(little.fill, false);
  const blocked = C.create({ mode: "classic", seed: 4 });
  const other = blocked.answer[1][0], start = blocked.answer[0][0];
  C.begin(blocked, start);
  if (C.adjacent(blocked.n, start, other)) assert.deepEqual(C.extend(blocked, other).map((e) => e.type), ["blocked"]);
});

test("Untangle: crossings are counted with whole numbers; lines sharing a point don't cross", () => {
  const U = L.untangle;
  assert.equal(U.segmentsCross([0, 0], [10, 10], [0, 10], [10, 0]), true);
  assert.equal(U.segmentsCross([0, 0], [10, 0], [0, 5], [10, 5]), false);
  const pts = [[0, 0], [10, 0], [0, 10], [10, 10]];
  assert.equal(U.edgesCross(pts, [0, 1], [0, 2]), false);                    // a shared point
  assert.equal(U.edgesCross(pts, [0, 3], [1, 2]), true);
  assert.equal(U.crossings(pts, [[0, 1], [1, 3], [3, 2], [2, 0]]).count, 0);
  const s = U.create({ mode: "classic", seed: 9 });
  assert.ok(s.crossCount > 0);
  assert.equal(U.crossings(s.home, s.edges).count, 0);
  U.movePoint(s, 0, 50, 50); U.movePoint(s, 0, 60, 60); U.movePoint(s, 1, 70, 70);
  assert.equal(s.moves, 2);                                                   // the same point again doesn't count
});

test("saving and continuing", () => {
  for (const [id, [, modes]] of Object.entries(GAMES)) {
    for (const mode of modes) {
      const s = L[id].create({ mode, seed: 31, startLevel: 7 });
      for (let i = 0; i < 90; i++) L[id].step(s);
      const back = L[id].restore(JSON.parse(JSON.stringify(L[id].save(s))));
      assert.deepEqual(plain(boardOf[id](back)), plain(boardOf[id](s)), `${id} · ${mode}`);
      assert.equal(back.level, s.level);
      assert.throws(() => L[id].restore({ mode, level: 1 }), /can't be continued/);
    }
  }
});

test("the rules never use engine-dependent maths", () => {
  const FORBIDDEN = ["sin", "cos", "tan", "atan", "atan2", "exp", "log", "pow", "hypot", "random", "cbrt"];
  const ACTS = ["up", "down", "left", "right", "fire", "alt", "hint", "undo", "erase"];
  const saved = {}, now = Date.now;
  for (const k of FORBIDDEN) { saved[k] = Math[k]; Math[k] = () => { throw new Error(`Math.${k} in the rules`); }; }
  Date.now = () => { throw new Error("Date.now in the rules"); };
  const dir = path.join(__dirname, "..", "..", "app", "static", "games");
  try {
    for (const [id, [, modes]] of Object.entries(GAMES)) {
      const file = path.join(dir, `${id}-logic.js`);
      delete require.cache[require.resolve(file)];
      const G = require(file);
      for (const mode of modes) {
        let r = id.length * 31 + mode.length;
        const rnd = () => { r = (r * 1103515245 + 12345) & 0x7fffffff; return r / 0x7fffffff; };
        const s = G.create({ mode, seed: 99, hints: "on", startLevel: 3 });
        for (let i = 0; i < 1500 && !s.over; i++) {
          if (rnd() < 0.08) G.press(s, ACTS[Math.floor(rnd() * ACTS.length)], true);
          G.step(s);
        }
        G.result(s);
      }
      delete require.cache[require.resolve(file)];
    }
  } finally { Object.assign(Math, saved); Date.now = now; }
});

test("registry: the six games, their modes, touch controls and hints off in races; every look draws", () => {
  for (const [id, [name, modes]] of Object.entries(GAMES)) {
    for (const look of LOOKS) {
      const sb = makeSandbox();
      const def = sb.win.ArcadeGames.get(id);
      assert.equal(def.name, name);
      assert.deepEqual(plain(def.modes.map((m) => m.id)), modes);
      assert.equal(def.controls, "touch");
      assert.equal(def.stateVersion, 1);
      assert.deepEqual(plain(def.options.map((o) => [o.id, o.default, o.offInRaces])), [["hints", "off", true]]);
      const inst = def.create(sb.canvas(390, 487), { mode: "levels", seed: 5, look, startLevel: 4, options: { hints: "on" } });
      inst.start(); sb.frames(20);
      assert.equal(inst.level, 4, `${id} starts at level 4`);
      inst.input("right", true); inst.input("fire", true); inst.input("alt", true);
      inst.pointer("down", 120, 150); inst.pointer("move", 140, 150); inst.pointer("up", 140, 150);
      sb.frames(20);
      assert.ok(!sb.counts.nonFinite, `${id} · ${look}: no NaN drawn`);
      inst.destroy();
    }
  }
});

test("Arrow Release: every arrow has a tail on the big boards, and the board can be zoomed and moved", () => {
  const A = L.arrows;
  for (const mode of ["classic", "big", "twisty", "levels"]) {
    for (let seed = 1; seed <= 4; seed++) {
      const s = A.create({ mode, seed, startLevel: 150 });
      for (const a of s.board.arrows) assert.ok(a.cells.length >= 2, `${mode}: a tail on every arrow`);
      assert.ok(A.solvable(s.board));
    }
  }
  assert.equal(A.create({ mode: "classic", seed: 3 }).board.n, 14);
  assert.equal(A.create({ mode: "big", seed: 3 }).board.n, 20);
  assert.equal(A.create({ mode: "levels", startLevel: 200 }).board.n, A.MAX_N);
  const full = A.create({ mode: "big", seed: 9 }).board, used = full.arrows.reduce((n, a) => n + a.cells.length, 0);
  assert.ok(used >= 0.8 * 400, `the 20 × 20 board is well filled (${used} of 400)`);

  const sb = makeSandbox();
  const inst = sb.win.ArcadeGames.get("arrows").create(sb.canvas(390, 487), { mode: "big", seed: 4, look: "modern" });
  inst.start(); sb.frames(2);
  const K = 390 / 240, at = (x, y) => [x * K, y * K];
  const s = inst.logic, BX = 10, BY = 42, BS = 220;
  const screen = (cell) => {               // a cell's centre on the screen with the current view
    const v = inst.view, n = s.board.n, w = BS / n;
    return [BX + ((cell % n) * w + w / 2 - v.camX) * v.z, BY + (Math.floor(cell / n) * w + w / 2 - v.camY) * v.z];
  };
  const tap = (cell) => { const [x, y] = at(...screen(cell)); inst.pointer("down", x, y, 1); inst.pointer("up", x, y, 1); sb.frames(1); };
  const freeOne = () => s.board.arrows.findIndex((a, i) => A.isFree(s.board, i));
  assert.deepEqual([inst.view.z, inst.view.camX, inst.view.camY], [1, 0, 0]);
  let before = A.left(s);
  tap(s.board.arrows[freeOne()].cells[0]);
  assert.equal(A.left(s), before - 1, "a tap releases the arrow under it");
  // pinch: two fingers moving apart zoom in about the middle; nothing is released
  before = A.left(s);
  inst.pointer("down", ...at(100, 150), 1); inst.pointer("down", ...at(140, 150), 2);
  inst.pointer("move", ...at(180, 150), 2);
  inst.pointer("up", ...at(180, 150), 2); inst.pointer("up", ...at(100, 150), 1);
  assert.ok(Math.abs(inst.view.z - 2) < 1e-9, `pinched to ${inst.view.z}`);
  assert.equal(A.left(s), before, "a pinch releases nothing");
  // one finger drags the view; nothing is released
  const cam = inst.view.camX;
  inst.pointer("down", ...at(150, 150), 3); inst.pointer("move", ...at(120, 150), 3); inst.pointer("up", ...at(120, 150), 3);
  assert.ok(inst.view.camX > cam, "dragged to the right part of the board");
  assert.equal(A.left(s), before);
  // zoomed in, a tap still releases the arrow under the finger
  let free = -1;
  for (let i = 0; i < s.board.arrows.length && free < 0; i++) {
    if (!A.isFree(s.board, i)) continue;
    const [x, y] = screen(s.board.arrows[i].cells[0]);
    if (x > BX + 2 && x < BX + BS - 2 && y > BY + 2 && y < BY + BS - 2) free = i;
  }
  if (free >= 0) { tap(s.board.arrows[free].cells[0]); assert.ok(s.board.arrows[free].gone, "the arrow under the finger left"); }
  // the buttons and the wheel zoom too, within limits
  for (let i = 0; i < 20; i++) inst.input("zoomin", true);
  assert.equal(inst.view.z, inst.view.maxZoom);
  for (let i = 0; i < 20; i++) inst.input("zoomout", true);
  assert.equal(inst.view.z, 1);
  inst.wheel(...at(120, 150), -100);
  assert.ok(inst.view.z > 1, "the wheel zooms in");
  inst.wheel(...at(5, 5), -100);                 // outside the board: nothing
  sb.frames(5);
  assert.ok(!sb.counts.nonFinite);
  inst.destroy();
});
