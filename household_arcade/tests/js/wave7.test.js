"use strict";
// Wave 7 (Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes, Sea Battle): the registry, each game's rules,
// the computer at every level (only legal moves, the same game for the same seed, the levels in order of strength),
// scores inside the honest-score limits, saving, one screen (Sea Battle's "pass the phone"), turn by turn (the board
// rebuilt from the server's picture, a move sent rather than played, Sea Battle from its view alone), a random-input
// fuzz run, the rules with engine-dependent maths made to throw, and every mode drawn in all six looks.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const BK = loadLogic("boardkit.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const GAMES = {
  fourrow: ["Four in a Row", "fourrow-logic.js"],
  tictactoe: ["Tic-tac-toe", "tictactoe-logic.js"],
  checkers: ["Checkers", "checkers-logic.js"],
  reversi: ["Reversi", "reversi-logic.js"],
  dots: ["Dots and Boxes", "dots-logic.js"],
  seabattle: ["Sea Battle", "seabattle-logic.js"],
};
const L = {};
for (const [id, [, file]] of Object.entries(GAMES)) L[id] = loadLogic(file);
const plain = (x) => JSON.parse(JSON.stringify(x));

/** Play a whole local game with the computer on both sides (levels a and b), every move checked legal first. */
function selfPlay(id, seed, a, b, opts = {}) {
  const G = L[id];
  const s = G.create({ mode: "two", seed, options: opts });
  let guard = 0;
  const trail = [];
  while (!s.over && guard++ < 800) {
    const seat = G.toMove(s)[0];
    const m = G.R.ai(s, seat, seat === 0 ? a : b);
    assert.ok(G.legal(s, seat, m), `${id}: the computer's move is legal (${JSON.stringify(m)})`);
    G.R.apply(s, seat, m);
    trail.push(JSON.stringify(m));
  }
  assert.ok(s.over, `${id}: finished`);
  return { s, trail };
}

test("the six wave 7 games register after wave 6: modes, touch controls, turn by turn, no race, saving, options", () => {
  const { win } = makeSandbox();
  const ids = plain(win.ArcadeGames.list().map((g) => g.id));
  assert.deepEqual(ids.slice(ids.indexOf("fourrow"), ids.indexOf("fourrow") + 6), Object.keys(GAMES));
  for (const [id, [name]] of Object.entries(GAMES)) {
    const g = win.ArcadeGames.get(id);
    assert.equal(g.name, name);
    assert.deepEqual(plain(g.modes.map((m) => m.id)), ["easy", "medium", "hard", "two", "phones"]);
    assert.ok(/gentle/.test(g.modes[0].label));
    assert.equal(g.defaultMode, "easy");
    assert.equal(g.controls, "touch");
    assert.equal(g.turns, true);
    assert.deepEqual(plain(g.turnModes), ["phones"]);
    assert.equal(g.race, false);
    assert.equal(g.stateVersion, 1);
    assert.ok(g.help.length > 150, id);
  }
  assert.deepEqual(plain(win.ArcadeGames.get("dots").options.map((o) => [o.id, o.default])), [["size", "4"]]);
  assert.deepEqual(plain(win.ArcadeGames.get("seabattle").options.map((o) => [o.id, o.default])), [["fleet", "classic"]]);
});

test("who moves first: you against the computer and on one screen; the seed from two phones (as the server)", () => {
  for (const id of Object.keys(GAMES)) {
    assert.equal(L[id].create({ mode: "hard", seed: 7 }).turn, 0);
    assert.equal(L[id].create({ mode: "two", seed: 7 }).turn, 0);
    assert.equal(L[id].create({ mode: "phones", seed: 7 }).first, 1);
    assert.equal(L[id].create({ mode: "phones", seed: 8 }).first, 0);
  }
});

test("the computer plays only legal moves at every level, and the same seed plays the same game", () => {
  for (const id of Object.keys(GAMES)) {
    const levels = id === "checkers" ? [[1, 1], [2, 1], [3, 2]] : [[1, 1], [2, 2], [3, 3], [1, 3]];
    for (const [a, b] of levels) {
      for (let seed = 1; seed <= (id === "checkers" && a === 3 ? 1 : 3); seed++) {
        const one = selfPlay(id, seed, a, b), two = selfPlay(id, seed, a, b);
        assert.deepEqual(one.trail, two.trail, `${id} ${a}/${b} seed ${seed}: deterministic`);
      }
    }
  }
});

test("the levels: Hard beats Medium and Medium beats Easy more often than not", () => {
  const win = (id, strong, weak, n, opts) => {
    let w = 0;
    for (let seed = 1; seed <= n; seed++) {
      const strongFirst = seed % 2 === 0;
      const { s } = selfPlay(id, seed, strongFirst ? strong : weak, strongFirst ? weak : strong, opts);
      if (s.winner === (strongFirst ? 0 : 1)) w++;
    }
    return w;
  };
  assert.ok(win("fourrow", 3, 2, 6) >= 4);
  assert.ok(win("fourrow", 2, 1, 10) >= 8);
  assert.ok(win("reversi", 3, 2, 6) >= 4);
  assert.ok(win("dots", 3, 2, 16, { size: "4" }) >= 9);
  assert.ok(win("dots", 2, 1, 10) >= 8);
  assert.ok(win("checkers", 2, 1, 6) >= 5);
  assert.ok(win("seabattle", 3, 1, 10) >= 7);
  // Tic-tac-toe's Hard never loses
  for (let seed = 1; seed <= 20; seed++) {
    const { s } = selfPlay("tictactoe", seed, 1, 3);
    assert.notEqual(s.winner, 0, `seed ${seed}`);
  }
});

test("scores: a win's base by level (100 / 250 / 500; 500 against a person) and a bonus, a draw a quarter, a loss 0", () => {
  for (const id of Object.keys(GAMES)) {
    const G = L[id];
    for (const [mode, base] of [["easy", 100], ["medium", 250], ["hard", 500]]) {
      const s = G.create({ mode, seed: 1 });
      s.over = true; s.winner = 0;
      const sc = G.score(s);
      assert.ok(sc >= base && sc <= 2 * base, `${id} ${mode}: ${sc}`);
      s.winner = -1; assert.equal(G.score(s), Math.floor(base / 4));
      s.winner = 1; assert.equal(G.score(s), 0);
      assert.equal(G.result(s).stats.notSaved, "Not a win — not saved");
    }
    const t = G.create({ mode: "two", seed: 1 }); t.over = true; t.winner = 0;
    assert.equal(G.score(t), 0);
    assert.match(G.result(t).stats.notSaved, /one screen/);
    // the most: never above the server's 1,000
    for (let seed = 1; seed <= 4; seed++) {
      const { s } = selfPlay(id, seed, 3, 1);
      s.mode = "phones"; s.me = s.winner === -1 ? 0 : s.winner;
      assert.ok(G.score(s) <= 1000);
    }
  }
});

test("Four in a Row: dropping, a full column, four in every direction, ↓ drops", () => {
  const G = L.fourrow, s = G.create({ mode: "two", seed: 1 });
  for (const c of [0, 1, 0, 1, 0, 1]) G.play(s, s.turn, { col: c });
  assert.deepEqual(s.board.slice(35, 42), [1, 2, 0, 0, 0, 0, 0]);
  s.cursor = 0;
  const evs = G.press(s, "down", true);
  assert.ok(evs.some((e) => e.type === "drop"));
  assert.equal(s.winner, 0);
  assert.deepEqual(s.line, [14, 21, 28, 35]);
  const t = G.create({ mode: "two", seed: 1 });
  for (let i = 0; i < 6; i++) G.play(t, t.turn, { col: 3 });
  assert.equal(G.legal(t, t.turn, { col: 3 }), false);
  assert.deepEqual(plain(G.tap(t, 3).map((e) => e.type)), ["nope"]);
});

test("Tic-tac-toe: a line, a draw, a taken square refused", () => {
  const G = L.tictactoe, s = G.create({ mode: "two", seed: 1 });
  for (const c of [0, 1, 2, 4, 3, 5, 7, 6, 8]) G.play(s, s.turn, { cell: c });
  assert.equal(s.winner, -1);
  const t = G.create({ mode: "two", seed: 1 });
  for (const c of [4, 0, 2, 6, 3]) G.play(t, t.turn, { cell: c });
  assert.deepEqual(G.tap(t, 4).map((e) => e.type), ["nope"]);
  G.play(t, t.turn, { cell: 1 });
  G.play(t, t.turn, { cell: 5 });
  assert.equal(t.winner, 0);
  assert.deepEqual(t.line, [3, 4, 5]);
});

test("Checkers: compulsory captures, multi-jumps finished by taps, crowning, picking and letting go", () => {
  const G = L.checkers, s = G.create({ mode: "two", seed: 1 });
  const b = Array(64).fill(0);
  b[56] = 1; b[49] = 2; b[35] = 2; b[7] = 2;
  s.board = b; s.turn = 0;
  assert.deepEqual(plain(G.moves(s, 0)), [{ path: [56, 42, 28] }]);
  assert.deepEqual(G.tap(s, 56).map((e) => e.type), ["pick"]);
  const evs = G.tap(s, 42);                   // one way on: the double jump is played
  assert.ok(evs.some((e) => e.type === "capture" && e.n === 2));
  assert.equal(s.board[28], 1);
  const c = G.create({ mode: "two", seed: 1 });
  const b2 = Array(64).fill(0);
  b2[17] = 1; b2[10] = 2; b2[60] = 2;
  c.board = b2; c.turn = 0;
  G.play(c, 0, { path: [17, 3] });
  assert.equal(c.board[3], 3);                // crowned
  const d = G.create({ mode: "two", seed: 1 });
  assert.deepEqual(G.tap(d, 0).map((e) => e.type), ["nope"]);
  G.tap(d, 42); assert.deepEqual(plain(d.sel), [42]);
  G.press(d, "alt", true); assert.equal(d.sel, null);
  // the second phone of a match sees the board turned round: its arrows turn round too
  const p = G.create({ mode: "phones", seed: 2, seat: 1 });     // seed 2: seat 0 first, so seat 1 is light at the top
  const at = p.cursor; G.press(p, "up", true);
  assert.equal(p.cursor, at + 8);
});

test("Reversi: flips, a pass when one side can't place, the end by counting", () => {
  const G = L.reversi, s = G.create({ mode: "two", seed: 1 });
  assert.deepEqual(plain(G.moves(s, 0).map((m) => m.cell)), [19, 26, 37, 44]);
  G.play(s, 0, { cell: 19 });
  assert.equal(G.count(s.board, 0), 4);
  const t = G.create({ mode: "two", seed: 1 });
  t.board = Array(64).fill(0); t.board[0] = 1; t.board[1] = 2; t.board[3] = 2; t.board[63] = 2; t.turn = 0;
  const evs = G.play(t, 0, { cell: 2 });
  assert.ok(evs.some((e) => e.type === "pass" && e.seat === 1));
  assert.equal(t.turn, 0);
  G.play(t, 0, { cell: 4 });
  assert.ok(t.over);
});

test("Dots and Boxes: sizes, an extra turn for a box, the cursor walks the lines", () => {
  const G = L.dots;
  for (const [size, n] of [["3", 24], ["4", 40], ["5", 60]]) assert.equal(G.create({ mode: "two", options: { size } }).edges.length, n);
  const s = G.create({ mode: "two", seed: 1, options: { size: "3" } });
  for (const e of [0, 3, 12]) G.play(s, s.turn, { edge: e });
  const who = s.turn;
  const evs = G.play(s, who, { edge: 13 });
  assert.ok(evs.some((e) => e.type === "box"));
  assert.equal(s.turn, who);
  const seen = new Set();
  for (const dir of ["up", "left", "down", "right", "down", "down", "right", "up", "left", "down", "down"]) { G.press(s, dir, true); seen.add(s.cursor); }
  for (const e of seen) assert.ok(e >= 0 && e < 24);
  assert.ok(seen.size > 4);
  for (let e = 0; e < 24; e++) { const sl = G.edgeToSlot(3, e); assert.equal(G.slotToEdge(3, sl[0], sl[1]), e); }
});

test("Sea Battle: placing (move, turn, shuffle, ready), shots, sinking, the computer only uses what it was told", () => {
  const G = L.seabattle, s = G.create({ mode: "hard", seed: 3, options: { fleet: "small" } });
  assert.equal(s.size, 8);
  assert.ok(s.placed[1] && !s.placed[0]);
  assert.equal(G.placer(s), 0);
  const draft = plain(s.draft);
  G.tap(s, { btn: "shuffle" });
  assert.notDeepEqual(plain(s.draft), draft);
  const cells = G.shipCells(8, s.draft[0][0], s.draft[0][1], s.draft[0][2], s.draft[0][3]);
  G.tap(s, { cell: cells[0] });
  assert.equal(s.dsel, 0);
  G.tap(s, { btn: "ready" });
  assert.ok(s.placed[0] && s.phase === "play");
  // a shot needs an unshot square; a hit is reported; the computer replies
  G.play(s, 0, { shot: 0 });
  assert.equal(G.legal(s, 0, { shot: 0 }), false);
  for (let i = 0; i < 400 && s.turn === 1 && !s.over; i++) s.updates++, G.step(s);
  assert.equal(s.turn, 0);
  // the computer never looks at the hidden fleet: its shots come from the results only (same with another fleet)
  const a = G.create({ mode: "two", seed: 9, options: { fleet: "small" } }), b = G.create({ mode: "two", seed: 9, options: { fleet: "small" } });
  G.R.apply(a, 0, { place: [[0, 0, 4, 0], [2, 0, 3, 0], [4, 0, 3, 0], [6, 0, 2, 0]] });
  G.R.apply(b, 0, { place: [[0, 0, 4, 0], [2, 0, 3, 0], [4, 0, 3, 0], [6, 0, 2, 0]] });
  G.R.apply(a, 1, { place: [[7, 4, 4, 0], [5, 5, 3, 0], [3, 5, 3, 0], [1, 6, 2, 0]] });
  G.R.apply(b, 1, { place: [[0, 7, 4, 1], [5, 0, 3, 0], [3, 3, 3, 0], [7, 0, 2, 0]] });
  G.R.apply(a, 0, { shot: 63 }); G.R.apply(b, 0, { shot: 63 });            // a miss on both
  assert.deepEqual(G.R.ai(a, 1, 3), G.R.ai(b, 1, 3));
});

test("one screen: Sea Battle shows the shot, then a pass-the-phone screen; a tap hands over", () => {
  const G = L.seabattle, s = G.create({ mode: "two", seed: 4, options: { fleet: "small" } });
  G.tap(s, { btn: "ready" });
  assert.equal(G.mySeat(s), -1);                          // the result shows a moment …
  for (let i = 0; i < G.PASS_DELAY; i++) G.step(s);
  assert.equal(s.pass, 1);                                // … then "Pass to Player 2"
  assert.equal(G.placer(s), -1);
  G.tap(s, { cell: 0 });
  assert.equal(s.pass, -1);
  assert.equal(G.placer(s), 1);
  G.tap(s, { btn: "ready" });
  for (let i = 0; i < G.PASS_DELAY; i++) G.step(s);
  assert.equal(s.pass, 0);
  G.press(s, "fire", true);
  assert.equal(G.mySeat(s), 0);
  assert.equal(L.fourrow.create({ mode: "two" }).pass, -1);      // games without hidden boards never pass
});

test("turn by turn: the board from the server's moves, a move sent not played, the other's move synced in", () => {
  for (const id of ["fourrow", "tictactoe", "checkers", "reversi", "dots"]) {
    const G = L[id];
    const { s: done } = selfPlay(id, 6, 2, 1);
    void done;
    const local = G.create({ mode: "phones", seed: 6, seat: 0 });
    const moves = [];
    for (let i = 0; i < 4 && !local.over; i++) {
      const seat = G.toMove(local)[0], m = G.R.ai(local, seat, 1);
      G.R.apply(local, seat, m);
      moves.push({ n: i + 1, seat: seat + 1, move: m });
    }
    const pic = { seat: 1, seed: 6, options: {}, moves, number: moves.length, status: "playing" };
    const s = G.fromTurns(pic);
    assert.deepEqual(plain(G.digest(s)), plain(G.digest(local)), id);
    assert.equal(s.number, moves.length);
    const seat = G.toMove(s)[0];
    s.me = seat;
    const m = G.R.ai(s, seat, 1), before = JSON.stringify(G.digest(s));
    const evs = G.play(s, seat, m);
    assert.deepEqual(plain(evs), [{ type: "send", move: plain(m) }]);
    assert.equal(JSON.stringify(G.digest(s)), before, `${id}: nothing played here`);
    assert.equal(G.mySeat(s), -1, "no second move while one is on its way");
    const r = G.sync(s, Object.assign({}, pic, { seat: seat + 1, moves: moves.concat([{ n: 7, seat: seat + 1, move: m }]), number: 7 }));
    assert.equal(r.s.number, 7);
    assert.ok(r.events.some((e) => e.type === "move"));
    assert.equal(r.s.sending, false);
    // ended another way: the server's word
    const g = G.fromTurns(Object.assign({}, pic, { over: true, status: "done", endReason: "resigned", result: { winner: 1, reason: "resigned" } }));
    assert.ok(g.over); assert.equal(g.winner, 0); assert.equal(g.endReason, "resigned");
  }
});

test("turn by turn, Sea Battle: the board from the view alone — the other fleet is never there", () => {
  const G = L.seabattle;
  const view = { size: 8, fleet: [4, 3, 3, 2], fleetId: "small", phase: "play", turn: 1, first: 1, placed: [true, true], over: false,
    winner: null, ships: [[0, 1, 2, 3], [16, 17, 18], [32, 33, 34], [48, 49]], myShots: [[10, true], [20, false]], sunk: [],
    atMe: [[0, true], [63, false]], lost: [], last: 63 };
  const s = G.fromTurns({ seat: 1, seed: 5, options: { fleet: "small" }, view, moves: [{ seat: 2, move: { shot: 63, hit: false, sunk: 0, cells: [] } }], number: 6 });
  assert.equal(s.ships[1], null);
  assert.deepEqual(plain(s.ships[0]), view.ships);
  assert.equal(s.res[0][10], 1);
  assert.equal(s.res[1][0], 1);
  assert.equal(G.mySeat(s), -1);                  // their move
  assert.deepEqual(plain(s.lastShot), { seat: 1, cell: 63, hit: false, sunk: 0 });
  // a shot of mine can't be worked out here (their fleet isn't known): it goes to the server
  s.turn = 0;
  assert.deepEqual(plain(G.play(s, 0, { shot: 11 })), [{ type: "send", move: { shot: 11 } }]);
  assert.throws(() => G.R.apply(G.fromTurns({ seat: 1, seed: 5, options: { fleet: "small" }, view: Object.assign({}, view, { turn: 0 }), moves: [] }), 0, { shot: 11 }));
  // placing from a phone: Ready sends the arrangement on screen
  const p = G.fromTurns({ seat: 2, seed: 5, options: { fleet: "small" }, view: Object.assign({}, view, { phase: "place", placed: [true, false], ships: null, myShots: [], atMe: [] }), moves: [{ seat: 1, move: { placed: true } }] });
  assert.equal(G.placer(p), 1);
  const evs = G.tap(p, { btn: "ready" });
  assert.equal(evs[0].type, "send");
  assert.deepEqual(plain(evs[0].move.place), plain(p.draft));
});

test("saving and continuing (not for a two-phone match); bad saves refused", () => {
  for (const id of Object.keys(GAMES)) {
    const G = L[id], s = G.create({ mode: "medium", seed: 2 });
    for (let i = 0; i < 4 && !s.over; i++) { const seat = G.toMove(s)[0]; G.R.apply(s, seat, G.R.ai(s, seat, 1)); }
    const back = G.restore(JSON.parse(JSON.stringify(G.save(s))));
    assert.deepEqual(plain(G.digest(back)), plain(G.digest(s)));
    assert.throws(() => G.restore({ game: id, mode: "phones" }));
    assert.throws(() => G.restore({ nonsense: 1 }));
    assert.throws(() => G.restore(Object.assign(plain(s), { game: "snake" })));
  }
});

test("a fuzz run of random keys and taps never throws and keeps the rules' invariants", () => {
  const acts = ["up", "down", "left", "right", "fire", "alt"];
  for (const id of Object.keys(GAMES)) {
    const G = L[id];
    for (const mode of ["easy", "hard", "two"]) {
      const st = { rng: 77 }, s = G.create({ mode, seed: 11 });
      for (let i = 0; i < 3000 && !s.over; i++) {
        const r = BK.rand(st);
        if (r < 0.5) G.press(s, acts[Math.floor(BK.rand(st) * 6)], true);
        else if (r < 0.8) {
          const n = id === "dots" ? s.edges.length : id === "seabattle" ? s.size * s.size : id === "fourrow" ? 7 : id === "tictactoe" ? 9 : 64;
          const t = Math.floor(BK.rand(st) * n);
          G.tap(s, id === "seabattle" ? (BK.rand(st) < 0.1 ? { btn: ["shuffle", "turn", "ready"][t % 3] } : { cell: t }) : t);
        }
        G.step(s);
        if (s.over) assert.deepEqual(plain(G.toMove(s)), []);
        else assert.ok(G.toMove(s).length >= 1);
      }
    }
  }
});

test("the rules use no engine-dependent maths (Math.sin, random, … and Date made to throw)", () => {
  const saved = {};
  for (const k of ["sin", "cos", "tan", "atan2", "exp", "log", "pow", "hypot", "random"]) { saved[k] = Math[k]; Math[k] = () => { throw new Error("Math." + k); }; }
  const now = Date.now; Date.now = () => { throw new Error("Date.now"); };
  try {
    for (const id of Object.keys(GAMES)) for (const [a, b] of [[1, 2], [3, 1]]) selfPlay(id, 3, a, id === "checkers" && a === 3 ? 1 : b);
  } finally {
    Object.assign(Math, saved); Date.now = now;
  }
});

test("every mode drawn in all six looks; a two-phone match from a picture, synced and ended", () => {
  for (const id of Object.keys(GAMES)) {
    for (const look of LOOKS) {
      for (const mode of ["easy", "two"]) {
        const sb = makeSandbox();
        const def = sb.win.ArcadeGames.get(id);
        let ended = null;
        const inst = def.create(sb.canvas(390, 487), { mode, look, seed: 5, onEnd: (r) => { ended = r; } });
        inst.start();
        for (let i = 0; i < 400; i++) {
          if (i % 9 === 0) inst.input(["left", "fire", "down", "right", "up", "alt"][(i / 9) % 6], true);
          if (i % 13 === 0) inst.pointer("down", 40 + (i * 37) % 320, 80 + (i * 53) % 380);
          sb.frames(1);
        }
        assert.equal(sb.counts.nonFinite || 0, 0, `${id} ${look} ${mode}`);
        assert.ok(sb.counts.fillRect || sb.counts.fill, `${id} ${look}`);
        assert.equal(inst.state === "over", !!ended);
        inst.destroy();
      }
      // a match: the board from a picture, a move sent through opts.turns.send, then the server's answer
      const sb = makeSandbox(), def = sb.win.ArcadeGames.get(id), G = L[id];
      const pic = id === "seabattle"
        ? { seat: 1, seed: 8, options: { fleet: "small" }, moves: [], number: 0, view: { size: 8, fleet: [4, 3, 3, 2], fleetId: "small", phase: "place",
          turn: 0, first: 0, placed: [false, false], over: false, winner: null, ships: null, myShots: [], sunk: [], atMe: [], lost: [], last: -1 } }
        : { seat: 1, seed: 8, options: {}, moves: [], number: 0 };
      const sent = [];
      let ended = null;
      const inst = def.create(sb.canvas(390, 487), { mode: "phones", look, names: ["Asha Rao", "Kabir Rao"], onEnd: (r) => { ended = r; },
        turns: { seat: 1, picture: pic, send: (m) => { sent.push(m); return Promise.resolve(); } } });
      inst.start();
      assert.equal(inst.canSave, false);
      // Checkers: pick the man under the cursor, step up-left; Sea Battle: walk down to the buttons, right to Ready
      const keys = id === "checkers" ? ["fire", "up", "left", "fire"] : id === "seabattle" ? ["down", "down", "down", "down", "down", "down", "down", "down", "right", "right", "fire"]
        : ["fire", "fire", "fire", "down"];
      for (let i = 0; i < 30 && !sent.length; i++) { inst.input(keys[i % keys.length], true); if (id !== "checkers" && id !== "seabattle") inst.pointer("down", 150, 150); sb.frames(2); }
      assert.ok(sent.length >= 1, `${id} ${look}: a move was sent`);
      sb.frames(5);
      if (id !== "seabattle") {
        const s = G.fromTurns(pic);
        const m = sent[0], seat = G.toMove(s)[0];
        G.R.apply(s, seat, m);
        inst.turnSync(Object.assign({}, pic, { moves: [{ n: 1, seat: seat + 1, move: m }], number: 1 }));
        assert.equal(inst.logic.number, 1);
        inst.turnSync(Object.assign({}, pic, { moves: [{ n: 1, seat: seat + 1, move: m }], number: 1, over: true, status: "done", endReason: "resigned", result: { winner: 1 } }));
        sb.frames(100);
        assert.ok(ended, `${id}: the match ended`);
        assert.equal(ended.score, L[id].score(inst.logic));
      }
      inst.destroy();
    }
  }
});
