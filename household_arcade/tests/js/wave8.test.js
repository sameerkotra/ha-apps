"use strict";
// Wave 8 (Ludo, Snakes and Ladders, Carrom, Chess): the registry; Ludo's and Snakes and Ladders' rules on one phone
// (the computer filling seats, pass and play, the die from the seed, moves the roll leaves no choice about), from
// phones (the die only ever the server's: a roll is sent, never made here; the board from the server's moves and
// rolls); Chess's perft counts, the rules through taps (castling, en passant, the promotion chooser), the engine at
// every level (legal, the same move for the same position, mates found, Hard beating Easy) and its thinking off the
// main thread; Carrom's whole-number physics (the same on two "phones" bit for bit, with Math.sin & co. made to throw),
// its rules (fouls, the queen and its cover, the last coin, the board's end), the computer's levels; saving; scores
// within the honest-score limits; every mode in all six looks.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox, loadLogic } = require("./helpers");
const BK = loadLogic("boardkit.js");
const Ludo = loadLogic("ludo-logic.js"), Snakes = loadLogic("snakes-logic.js"), Chess = loadLogic("chess-logic.js"), Carrom = loadLogic("carrom-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const plain = (x) => JSON.parse(JSON.stringify(x));
const LIMIT = 1000;                     // games.py: max_score 1,000 (and base 1,000) for all four

function rng(seed) { const s = { rng: seed >>> 0 || 1 }; return () => BK.rand(s); }

test("the four wave 8 games register after wave 7: modes, touch controls, turns or live turns, options", () => {
  const { win } = makeSandbox();
  const ids = plain(win.ArcadeGames.list().map((g) => g.id));
  assert.deepEqual(ids.slice(ids.indexOf("ludo"), ids.indexOf("ludo") + 4), ["ludo", "snakes", "carrom", "chess"]);
  const G = (id) => win.ArcadeGames.get(id);
  for (const id of ["ludo", "snakes"]) {
    assert.deepEqual(plain(G(id).modes.map((m) => m.id)), ["cpu", "pass", "phones"]);
    assert.equal(G(id).turns, true);
    assert.deepEqual(plain(G(id).turnModes), ["phones"]);
  }
  assert.deepEqual(plain(G("ludo").options.map((o) => [o.id, o.default])), [["players", "4"], ["fill", "no"]]);
  assert.deepEqual(plain(G("snakes").options.map((o) => [o.id, o.default])), [["players", "2"], ["fill", "no"], ["board", "classic"], ["finish", "any"]]);
  assert.deepEqual(plain(G("chess").modes.map((m) => m.id)), ["easy", "medium", "hard", "two", "phones"]);
  assert.deepEqual(plain(G("chess").options.map((o) => [o.id, o.default])), [["side", "white"]]);
  assert.equal(G("chess").turns, true);
  const c = G("carrom");
  assert.deepEqual(plain(c.modes.map((m) => m.id)), ["easy", "medium", "hard", "two", "doubles"]);
  assert.equal(c.turns, false);
  assert.deepEqual(plain(c.buttons.map((b) => b.action)), ["left", "right", "up", "down", "fire"]);
  for (const id of ["ludo", "snakes", "carrom", "chess"]) {
    const g = G(id);
    assert.equal(g.controls, "touch");
    assert.equal(g.race, false);
    assert.equal(g.stateVersion, 1);
    assert.ok(g.help.length > 200, id);
    assert.ok(/gentle|cpu/.test(g.modes[0].label + g.modes[0].id), id);
  }
});

// =====================================================================
// Ludo and Snakes and Ladders
// =====================================================================
test("dice games on one phone: you and the computer, pass and play, the computer filling seats", () => {
  for (const [L, n] of [[Ludo, "4"], [Snakes, "3"]]) {
    const s = L.create({ mode: "cpu", seed: 3, options: { players: n } });
    assert.equal(s.n, +n);
    assert.deepEqual(s.people, [true].concat(Array(+n - 1).fill(false)));
    assert.equal(s.turn, 0, "you roll first");
    const p = L.create({ mode: "pass", seed: 3, options: { players: "2", fill: "yes" } });
    assert.equal(p.n, 4);
    assert.deepEqual(p.cpu, [false, false, true, true]);
    assert.equal(L.mySeat(p), 0);
    const q = L.create({ mode: "pass", seed: 3, options: { players: "3" } });
    assert.deepEqual(q.cpu, [false, false, false]);
  }
  assert.deepEqual(Ludo.create({ mode: "cpu", seed: 1, options: { players: "2" } }).cols, [0, 2]);
  assert.deepEqual(Ludo.create({ mode: "cpu", seed: 1, options: { players: "3" } }).cols, [0, 1, 2]);
});

test("dice games: rolling from the seed, forced moves by themselves, the computer's turns, the same game for a seed", () => {
  for (const L of [Ludo, Snakes]) {
    const play = (seed) => {
      const s = L.create({ mode: "cpu", seed, options: { players: "3" } });
      const trail = [];
      for (let i = 0; i < 40000 && !s.over; i++) {
        if (L.mySeat(s) === 0) {
          if (!s.rolled) L.press(s, "fire", true);
          else if (!L.R.forced(s, 0)) {
            const ms = L.R.legal(s, 0, s.rolled);
            const evs = L.play(s, 0, ms[i % ms.length]);
            assert.notEqual(evs[0].type, "nope");
          }
        }
        const evs = L.step(s);
        for (const e of evs) if (e.type === "roll") { trail.push(e.seat + ":" + e.value); assert.ok(e.value >= 1 && e.value <= 6); }
      }
      assert.ok(s.over, "finished");
      return { s, trail };
    };
    const a = play(5), b = play(5), c = play(6);
    assert.deepEqual(a.trail, b.trail);
    assert.notDeepEqual(a.trail, c.trail);
    assert.ok(a.trail.some((t) => t.startsWith("1:")) && a.trail.some((t) => t.startsWith("2:")), "the computers rolled");
    const res = L.result(a.s);
    assert.ok(res.score >= 0 && res.score <= LIMIT);
    if (a.s.winner === 0) assert.ok(res.score >= 100);
    else assert.equal(res.stats.notSaved, "Not a win — not saved");
  }
});

test("Ludo's rules: a 6 to come out, another roll after a 6, three 6s lose the turn, captures, safe squares, home exactly", () => {
  const s = Ludo.create({ mode: "pass", seed: 1, options: { players: "2" } });
  const R = Ludo.R, rollAs = (v) => { s._roll = { seat: s.turn + 1, value: v }; s.rolled = v; };
  rollAs(4);
  assert.deepEqual(plain(R.legal(s, 0, 4)), [{ pass: true }]);
  assert.deepEqual(plain(R.forced(s, 0)), { pass: true });
  R.apply(s, 0, { pass: true });
  assert.equal(s.turn, 1);
  assert.ok(/No move with a 4/.test(s.note));
  rollAs(6);
  assert.deepEqual(plain(R.forced(s, 1)), { token: 0 }, "four tokens alike in the yard: no choice");
  R.apply(s, 1, { token: 0 });
  assert.equal(s.turn, 1, "a 6 rolls again");
  rollAs(6); R.apply(s, 1, { token: 0 });
  rollAs(6);
  assert.deepEqual(plain(R.legal(s, 1, 6)), [{ pass: true }], "the third 6");
  R.apply(s, 1, { pass: true });
  assert.equal(s.turn, 0);
  assert.ok(/third 6/.test(s.note));
  // a capture (yellow on track square 20 is its progress 46) and a safe square
  s.tokens[0] = [18, -1, -1, -1]; s.tokens[1] = [46, -1, -1, -1]; s.turn = 0; s.sixes = 0;
  rollAs(2);
  const evs = R.apply(s, 0, { token: 0 });
  assert.deepEqual(s.tokens[1], [-1, -1, -1, -1]);
  assert.ok(evs.some((e) => e.type === "capture"));
  assert.deepEqual(plain(s.anim.caps), [[1, 0, 46]]);
  s.tokens[0] = [5, -1, -1, -1]; s.tokens[1] = [34, -1, -1, -1]; s.turn = 0;
  rollAs(3); R.apply(s, 0, { token: 0 });
  assert.equal(s.tokens[1][0], 34, "a star square is safe");
  s.tokens[0] = [53, 56, 56, 56]; s.turn = 0; s.sixes = 0;
  assert.deepEqual(plain(R.legal(s, 0, 4)), [{ pass: true }]);
  rollAs(3); R.apply(s, 0, { token: 0 });
  assert.equal(s.over, true);
  assert.equal(s.winner, 0);
  assert.throws(() => R.apply(s, 0, { token: 0 }));
  // bad moves
  const t = Ludo.create({ mode: "pass", seed: 1 });
  t._roll = { seat: 1, value: 6 };
  for (const bad of [{ token: 4 }, { token: "0" }, { pass: 1 }, { token: 0, pass: true }, null, []]) assert.throws(() => R.apply(BK.copy(t), 0, bad), JSON.stringify(bad));
  delete t._roll;
  assert.throws(() => R.apply(t, 0, { token: 0 }), /Roll the dice first/);
});

test("Snakes and Ladders: ladders up, snakes down, the finish (any or exact), the boards", () => {
  const s = Snakes.create({ mode: "pass", seed: 2, options: { players: "2", board: "classic", finish: "exact" } });
  const R = Snakes.R, rollAs = (v) => { s._roll = { seat: s.turn + 1, value: v }; s.rolled = v; };
  rollAs(3); R.apply(s, 0, { go: true });
  assert.equal(s.pos[0], 18, "the ladder at 3");
  s.pos[1] = 22; rollAs(4); R.apply(s, 1, { go: true });
  assert.equal(s.pos[1], 17, "the snake at 26");
  s.pos[0] = 98; rollAs(5); R.apply(s, 0, { go: true });
  assert.equal(s.pos[0], 98, "exact: a roll past 100 is lost");
  assert.ok(/exactly 2/.test(s.note));
  s.pos[1] = 95; rollAs(5); R.apply(s, 1, { go: true });
  assert.equal(s.over, true);
  const any = Snakes.create({ mode: "pass", seed: 2, options: { finish: "any" } });
  any.pos[0] = 97; any._roll = { seat: 1, value: 6 }; any.rolled = 6; R.apply(any, 0, { go: true });
  assert.equal(any.over, true);
  for (const name of ["classic", "gentle"]) {
    const B = Snakes.BOARDS[name];
    for (const [a, z] of Object.entries(B.ladders)) assert.ok(+a < z && !B.snakes[z] && !B.ladders[z]);
    for (const [a, z] of Object.entries(B.snakes)) assert.ok(+a > z && !B.snakes[z] && !B.ladders[z]);
  }
  // squares go back and forth: 1 bottom left, 10 bottom right, 11 above 10, 100 top left
  assert.deepEqual(plain([Snakes.cell(1), Snakes.cell(10), Snakes.cell(11), Snakes.cell(100)]), [[0, 9], [9, 9], [9, 8], [0, 0]]);
});

test("dice games from phones: the die is never rolled here — a roll is sent; the board comes from the server's moves", () => {
  // the server's picture after some moves with its rolls
  const seed = 9, players = [{ seat: 1, name: "Kabir Rao", id: "k" }, { seat: 2, name: "Meera Rao", id: "m" }, { seat: 3, name: "Asha Rao", id: "a", left: true, computer: true }];
  const ref = Ludo.create({ mode: "phones", seed, seat: 0, players: 3 });
  const moves = [];
  const r = rng(4);
  for (let i = 0; i < 40 && !ref.over; i++) {
    const seat = ref.turn, d = 1 + Math.floor(r() * 6);
    ref._roll = { seat: seat + 1, value: d };
    const ms = Ludo.R.legal(ref, seat, d), m = ms[Math.floor(r() * ms.length)];
    Ludo.R.apply(ref, seat, m);
    moves.push({ n: i + 1, seat: seat + 1, dice: d, move: m });
  }
  const me = ref.turn + 1;
  const pic = { seed, options: {}, seat: me, players, number: moves.length, moves, over: false, roll: null };
  const s = Ludo.fromTurns(pic);
  assert.deepEqual(plain(Ludo.R.digest(s)), plain(Ludo.R.digest(ref)));
  assert.equal(s.left[2], true);
  assert.equal(Ludo.mySeat(s), me - 1);
  // fire asks the server for a roll; nothing is rolled here
  const evs = Ludo.press(s, "fire", true);
  assert.deepEqual(plain(evs.map((e) => e.type)), ["rollsend"]);
  assert.equal(s.rolled, 0);
  assert.equal(s.sending, true);
  assert.deepEqual(Ludo.press(s, "fire", true), [], "not twice");
  // the server's answer: a roll for me
  const withRoll = Object.assign({}, pic, { roll: { seat: me, value: 6 } });
  const r2 = Ludo.sync(s, withRoll);
  assert.equal(r2.s.rolled, 6);
  assert.ok(r2.events.some((e) => e.type === "roll"));
  // a token tap sends the move (not played here)
  const s2 = r2.s, mine = Ludo.movable(s2, me - 1);
  if (mine.length > 1) {
    const sent = Ludo.tap(s2, { seat: me - 1, token: mine[0] });
    assert.equal(sent[0].type, "send");
    assert.deepEqual(plain(sent[0].move), { token: mine[0] });
    assert.deepEqual(plain(Ludo.R.digest(s2)), plain(Ludo.R.digest(s)), "the board waits for the server");
  }
  // the server ended it another way
  const over = Ludo.fromTurns(Object.assign({}, pic, { over: true, result: { winner: 2, reason: "resigned" }, endReason: "resigned" }));
  assert.equal(over.over, true);
  assert.equal(over.winner, 1);
  // Snakes and Ladders from a picture
  const sp = Snakes.fromTurns({ seed: 3, options: { board: "gentle", finish: "exact" }, seat: 2, players: players.slice(0, 2), number: 2,
    moves: [{ seat: 2, dice: 4, move: { go: true } }, { seat: 1, dice: 6, move: { go: true } }] });
  assert.deepEqual(plain(sp.pos), [6, 24]);
  assert.equal(sp.board, "gentle");
});

test("dice games: saving and continuing, scores within the honest limits, every mode in every look", () => {
  for (const [L, id] of [[Ludo, "ludo"], [Snakes, "snakes"]]) {
    const s = L.create({ mode: "cpu", seed: 4, options: { players: "3" } });
    for (let i = 0; i < 30; i++) L.force(s);
    const back = L.restore(plain(L.save(s)));
    assert.deepEqual(plain(L.R.digest(back)), plain(L.R.digest(s)));
    assert.throws(() => L.restore({ game: "x" }));
    assert.throws(() => L.restore(Object.assign(plain(L.save(s)), { mode: "phones" })));
    for (let n = 2; n <= 4; n++) {
      const w = L.create({ mode: "cpu", seed: 1, options: { players: String(n) } });
      w.over = true; w.winner = 0;
      assert.ok(L.score(w) >= 100 && L.score(w) <= 2 * 100 * Math.max(1, id === "ludo" ? n - 1 : 1) && L.score(w) <= LIMIT, `${id} ${n}`);
      const ph = L.create({ mode: "phones", seed: 1, players: n }); ph.over = true; ph.winner = 0;
      for (const k of ph.tokens || []) k.fill(-1);
      assert.ok(L.score(ph, 0) >= 500 && L.score(ph, 0) <= LIMIT);
      const pass = L.create({ mode: "pass", seed: 1 }); pass.over = true; pass.winner = 1;
      assert.equal(L.result(pass).score, 0);
      assert.equal(L.result(pass).stats.notSaved, "Everyone on one phone — not saved");
    }
  }
  drawEveryLook("ludo", ["cpu", "pass"]);
  drawEveryLook("snakes", ["cpu", "pass"]);
});

function drawEveryLook(id, modes, extra) {
  for (const mode of modes) {
    for (const look of LOOKS) {
      const sb = makeSandbox();
      const inst = sb.win.ArcadeGames.get(id).create(sb.canvas(390, 487), Object.assign({ mode, look, seed: 5, options: {} }, extra || {}));
      inst.start();
      for (let i = 0; i < 30; i++) { inst.input("fire", true); sb.frames(2); inst.input("fire", false); inst.pointer("down", 120, 150); inst.pointer("up", 120, 150); sb.frames(10); }
      assert.ok(!sb.counts.nonFinite, `${id} ${mode} ${look}: finite numbers only`);
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.fill || 0) > 10, `${id} ${mode} ${look}: something drawn`);
      inst.destroy();
    }
  }
}

// =====================================================================
// Chess
// =====================================================================
const POSITIONS = [
  [Chess.START, [20, 400, 8902, 197281, 4865609]],
  ["r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", [48, 2039, 97862]],
  ["8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", [14, 191, 2812, 43238]],
  ["r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", [6, 264, 9467]],
  ["rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", [44, 1486, 62379]],
];
test("chess: perft counts of the start and well-known positions", () => {
  for (const [fen, counts] of POSITIONS) counts.forEach((want, i) => assert.equal(Chess.perft(fen, i + 1), want, `${fen} depth ${i + 1}`));
  assert.equal(Chess.toFen(Chess.fromFen(POSITIONS[1][0])), POSITIONS[1][0]);
});

test("chess: moves through taps — castling, en passant, the promotion chooser, a refused move", () => {
  const L = Chess;
  const s = L.create({ mode: "two", seed: 1 });
  const sq = L.sqIndex;
  s.fen = "r3k2r/pppq1ppp/8/3pP3/8/8/PPPP1PPP/R3K2R w KQkq d6 0 1";
  s.keys = {};
  // en passant: e5 takes d6
  assert.deepEqual(plain(L.tap(s, sq("e5"))), [{ type: "pick" }]);
  let evs = L.tap(s, sq("d6"));
  assert.equal(evs[0].type, "move");
  assert.ok(s.fen.startsWith("r3k2r/pppq1ppp/3P4/8/8/8/PPPP1PPP/R3K2R b"));
  // Black castles queen side (the king's two-step)
  L.tap(s, sq("e8")); evs = L.tap(s, sq("c8"));
  assert.equal(evs[0].type, "move");
  assert.ok(s.fen.startsWith("2kr3r/"), s.fen);
  assert.ok(s.fen.includes(" w KQ "), "Black's rights are gone");
  // White castles king side
  L.tap(s, sq("e1")); L.tap(s, sq("g1"));
  assert.ok(s.fen.includes("R4RK1 b"), s.fen);
  // a refused move: the board says so
  L.tap(s, sq("a7")); evs = L.tap(s, sq("a4"));
  assert.equal(evs[0].type, "nope");
  // promotion: the chooser, then a knight
  const p = L.create({ mode: "two", seed: 1 });
  p.fen = "8/4P3/8/8/8/8/k7/4K3 w - - 0 1"; p.keys = {};
  L.tap(p, sq("e7")); evs = L.tap(p, sq("e8"));
  assert.deepEqual(plain(evs), [{ type: "pick" }]);
  assert.deepEqual(plain(p.promo), { from: sq("e7"), to: sq("e8") });
  L.press(p, "right", true); L.press(p, "right", true); L.press(p, "right", true);     // Q R B N: the fourth
  evs = L.press(p, "fire", true);
  assert.equal(evs[0].type, "move");
  assert.ok(p.fen.startsWith("4N3/"), p.fen);
  // the second phone of a match sees Black at the bottom: its taps are turned round
  const ph = L.create({ mode: "phones", seed: 2, seat: 1 });
  assert.equal(ph.white, 0);
  assert.equal(L.flipped(ph), true);
  assert.equal(L.flipped(L.create({ mode: "hard", seed: 2, options: { side: "black" } })), true);
  assert.equal(L.flipped(L.create({ mode: "hard", seed: 2 })), false);
  // against the computer as Black: the computer (White) moves first
  const cb = L.create({ mode: "easy", seed: 3, options: { side: "black" } });
  assert.equal(cb.white, 1);
  assert.equal(cb.turn, 1);
  assert.equal(L.mySeat(cb), -1);
  for (let i = 0; i < 3 && cb.turn === 1; i++) L.step(cb);
  assert.equal(cb.turn, 0, "the computer has moved");
});

test("chess: the engine plays legal moves at every level, the same move for the same position, mates in one, within budget", () => {
  const fens = [Chess.START, POSITIONS[1][0], "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"];
  for (const level of [1, 2, 3]) {
    for (const fen of fens) {
      const t0 = Date.now();
      const m = Chess.think(fen, level, 7, []);
      assert.ok(Date.now() - t0 < 6000, "a move in time");
      const legal = Chess.legalMoves(Chess.fromFen(fen)).map((x) => BK.canonical(Chess.asMove(x)));
      assert.ok(legal.includes(BK.canonical(m)), `level ${level}: ${JSON.stringify(m)}`);
      assert.deepEqual(plain(Chess.think(fen, level, 7, [])), plain(m), "deterministic");
    }
  }
  for (const level of [2, 3]) {
    assert.deepEqual(plain(Chess.think("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", level, 1, [])), { from: "a1", to: "a8" }, "back-rank mate");
    assert.deepEqual(plain(Chess.think("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 3", level, 1, [])) !== null, true);
  }
  // the levels: Hard beats Easy; Medium beats Easy
  const game = (a, b, seed) => {
    const s = Chess.create({ mode: "two", seed });
    for (let g = 0; g < 260 && !s.over; g++) { const seat = s.turn; Chess.R.apply(s, seat, Chess.R.ai(s, seat, seat === 0 ? a : b)); }
    return s;
  };
  for (const [strong, weak, seed] of [[3, 1, 1], [2, 1, 2]]) {
    const s = game(weak, strong, seed);
    assert.ok(s.winner === 1 || (s.over === false && Chess.materialOf(s.fen, false) > Chess.materialOf(s.fen, true)), `level ${strong} beat level ${weak}`);
  }
});

test("chess: thinking off the main thread — the computer's move arrives later and is the same move", async () => {
  const s = Chess.create({ mode: "medium", seed: 4 });
  Chess.R.apply(s, 0, { from: "e2", to: "e4" });
  s.wait = 0;
  const want = Chess.R.ai(BK.copy(s), 1, 2);
  let job = null;
  Chess.setThinker((j, done) => { job = j; setTimeout(() => done(Chess.think(j.fen, j.level, j.seed, j.past)), 5); });
  try {
    assert.equal(Chess.R.ai(s, 1, 2), null, "nothing yet");
    assert.equal(Chess.R.ai(s, 1, 2), null, "asked once");
    assert.equal(job.fen, s.fen);
    await new Promise((res) => setTimeout(res, 40));
    assert.deepEqual(plain(Chess.R.ai(s, 1, 2)), plain(want));
    // a restored game asks again (no half-thought move kept)
    const back = Chess.restore(Chess.save(s));
    assert.equal(back.aiAsked, null);
  } finally { Chess.setThinker(null); }
});

test("chess: saving, scores, from a match picture, every mode in every look", () => {
  const s = Chess.create({ mode: "hard", seed: 2 });
  Chess.R.apply(s, 0, { from: "e2", to: "e4" });
  const back = Chess.restore(plain(Chess.save(s)));
  assert.equal(back.fen, s.fen);
  for (const [mode, base] of [["easy", 100], ["medium", 250], ["hard", 500]]) {
    const w = Chess.create({ mode, seed: 1 }); w.over = true; w.winner = 0;
    assert.ok(Chess.score(w) >= base && Chess.score(w) <= Math.min(2 * base, LIMIT));
    w.winner = -1; assert.equal(Chess.score(w), base / 4 | 0);
  }
  const pic = { seed: 5, options: {}, seat: 2, number: 3, moves: [{ seat: 2, move: { from: "e2", to: "e4" } }, { seat: 1, move: { from: "c7", to: "c5" } },
    { seat: 2, move: { from: "g1", to: "f3" } }], players: [{ seat: 1, name: "Kabir" }, { seat: 2, name: "Meera" }] };
  const m = Chess.fromTurns(pic);
  assert.equal(m.white, 1);
  assert.ok(m.fen.startsWith("rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b"));
  assert.equal(Chess.mySeat(m), -1, "Black's move, on the other phone");
  drawEveryLook("chess", ["easy", "two"]);
  drawEveryLook("chess", ["phones"], { turns: { seat: 2, picture: pic, send: () => Promise.resolve() } });
});

// =====================================================================
// Carrom
// =====================================================================
function randomShot(r) { return [Math.floor(r() * 1001), 100 + Math.floor(r() * 1601), 1 + Math.floor(r() * 100)]; }
function settle(L, s) { let g = 0; while (s.moving && g++ < 5000) L.step(s); return g; }
function withThrowingMaths(fn) {
  const saved = {};
  for (const k of ["sin", "cos", "tan", "atan2", "exp", "log", "pow", "hypot", "random"]) { saved[k] = Math[k]; Math[k] = () => { throw new Error("Math." + k + " used"); }; }
  const now = Date.now; Date.now = () => { throw new Error("Date.now used"); };
  try { return fn(); } finally { Object.assign(Math, saved); Date.now = now; }
}

test("carrom: whole-number physics — two phones playing the same shots match bit for bit (no engine-dependent maths)", () => {
  const sums = (seed) => withThrowingMaths(() => {
    const L = loadFresh("carrom-logic.js");
    const s = L.create({ mode: "phones", seed }), r = rng(seed), out = [];
    for (let k = 0; k < 120 && !s.over; k++) {
      L.press(s, "shot", randomShot(r), s.turn);
      settle(L, s);
      out.push(L.checksum(s));
      for (const p of s.pieces) {
        if (!p.on) continue;
        for (const v of [p.x, p.y, p.vx, p.vy]) assert.ok(Number.isInteger(v), "whole numbers");
        assert.ok(p.x >= 0 && p.y >= 0 && p.x <= L.BOARD && p.y <= L.BOARD, "on the board");
      }
      const coins = s.pieces.slice(1).filter((p) => p.on).length + s.pocketed[0].length + s.pocketed[1].length + (s.queen === "on" ? 0 : 1);
      assert.equal(coins, 19, "no coin lost or made");
    }
    return out;
  });
  for (const seed of [1, 2, 3]) {
    const a = sums(seed), b = sums(seed);
    assert.deepEqual(a, b);
    assert.ok(a.length > 50);
  }
  assert.notDeepEqual(sums(4), sums(5));
  // the sine table: exact whole numbers, the same as the series always gives
  assert.equal(Carrom.SIN[900], 16384);
  assert.equal(Carrom.SIN[0], 0);
  assert.equal(Carrom.SIN[300], 8192);
});

function loadFresh(file) {
  const p = require.resolve("./helpers").replace(/tests[\\/]js[\\/]helpers\.js$/, "app/static/games/" + file);
  delete require.cache[p];
  return require(p);
}

test("carrom rules: another shot after pocketing yours, fouls, the queen and its cover, the last coin, the end", () => {
  const L = Carrom;
  const s = L.create({ mode: "two", seed: 1 });
  assert.equal(L.turn(s), 0);
  assert.equal(L.colourOfTeam(s, 0), "w");
  const sink = (i) => { const p = s.pieces[i]; p.x = L.POCKET_AT; p.y = L.POCKET_AT; };
  // a foul: the striker into a pocket brings back what fell and one of yours
  s.pocketed[0] = [1]; s.pieces[1].on = false;
  L.shoot(s, 0, [500, 900, 10]);
  s.pieces[0].x = L.POCKET_AT + 10; s.pieces[0].y = L.POCKET_AT + 10;
  settle(L, s);
  assert.equal(s.foul, true);
  assert.deepEqual(s.pocketed[0], []);
  assert.equal(s.pieces[1].on, true, "the penalty coin is back");
  assert.equal(L.turn(s), 1, "the turn passes");
  // pocketing your own coin: shoot again
  sink(10);                                                     // a black coin for seat 1
  L.shoot(s, 1, [500, 900, 5]);
  settle(L, s);
  assert.deepEqual(s.pocketed[1], [10]);
  assert.equal(L.turn(s), 1);
  // the queen, then no cover: it goes back
  sink(19);
  L.shoot(s, 1, [500, 900, 5]); settle(L, s);
  assert.equal(s.queen, "pending");
  assert.equal(L.turn(s), 1);
  L.shoot(s, 1, [500, 900, 3]); settle(L, s);
  assert.equal(s.queen, "on");
  assert.equal(s.pieces[19].on, true);
  assert.equal(L.turn(s), 0);
  // the queen and a cover in one shot
  sink(19); sink(2);
  L.shoot(s, 0, [500, 900, 3]); settle(L, s);
  assert.equal(s.queen, "team0");
  assert.equal(s.queenBy, 0);
  // the last coin can't go while the queen is open: it comes back
  const t = L.create({ mode: "two", seed: 2 });
  for (let i = 1; i <= 8; i++) { t.pieces[i].on = false; t.pocketed[0].push(i); }
  t.pieces[9].x = L.POCKET_AT; t.pieces[9].y = L.POCKET_AT;
  L.shoot(t, 0, [500, 900, 3]); settle(L, t);
  assert.equal(t.pieces[9].on, true);
  assert.equal(L.turn(t), 1);
  // the board's end: all nine and the queen covered; points = the other's coins left + 3 for the queen
  t.queen = "team0"; t.queenBy = 0; t.turn = 0;
  t.pieces[9].x = L.POCKET_AT; t.pieces[9].y = L.POCKET_AT;
  L.shoot(t, 0, [500, 900, 3]); settle(L, t);
  assert.equal(t.over, true);
  assert.equal(t.winnerTeam, 0);
  assert.equal(t.points, 9 + 3);
  assert.deepEqual(plain(L.report(t)), { scores: [0, 0], levels: [1, 1], winner: 1 }, "two on one screen: no score");
  const ph = L.create({ mode: "phones", seed: 2 });
  Object.assign(ph, { over: true, winnerTeam: 1, winner: 1, points: 12 });
  assert.deepEqual(plain(L.report(ph)), { scores: [0, 500 + 240], levels: [1, 1], winner: 2 });
  assert.equal(L.result(ph, 1).score, 740);
  assert.equal(L.result(ph, 0).stats.notSaved, "Not a win — not saved");
  // a board that goes on for 300 shots ends on the points so far (or a draw)
  const long = L.create({ mode: "phones", seed: 5 });
  long.shots = L.MAX_SHOTS - 1; long.pocketed[0] = [1, 2]; long.pocketed[1] = [10];
  long.pieces[1].on = false; long.pieces[2].on = false; long.pieces[10].on = false;
  L.shoot(long, long.turn, [500, 900, 2]); settle(L, long);
  assert.equal(long.over, true);
  assert.equal(long.winnerTeam, 0);
  assert.equal(long.points, 1);
  const even = L.create({ mode: "phones", seed: 5 });
  even.shots = L.MAX_SHOTS - 1;
  L.shoot(even, even.turn, [500, 900, 2]); settle(L, even);
  assert.deepEqual(plain(L.report(even)), { scores: [0, 0], levels: [1, 1], winner: 0 }, "a draw");
  assert.equal(L.result(even, 0).stats.draw, true);
  // a place on a coin is moved along the line; shots must be whole numbers in range
  const u = L.create({ mode: "two", seed: 3 });
  u.pieces[1].x = L.BOARD / 2; u.pieces[1].y = L.BOARD - L.BASE_OFF;
  const at = L.strikerPreview(u, 0, 500);
  assert.notEqual(at[0], L.BOARD / 2);
  for (const bad of [[500, 900], [500, 50, 50], [500, 900, 0], [1001, 900, 5], [500.5, 900, 5], "x"]) assert.deepEqual(L.shoot(u, 0, bad), []);
  assert.deepEqual(L.shoot(u, 1, [500, 900, 50]), [], "not their turn");
  assert.deepEqual(plain(L.timeoutShot()), [500, 900, 12]);
});

test("carrom: doubles go round the table, partners opposite; the computer's levels, honest scores, saving", () => {
  const d = Carrom.create({ mode: "doubles", seed: 1 });
  assert.equal(d.n, 4);
  assert.deepEqual([0, 1, 2, 3].map((x) => Carrom.teamOf(d, x)), [0, 1, 0, 1]);
  assert.deepEqual([0, 1, 2, 3].map((x) => Carrom.sideOf(d, x)), [0, 1, 2, 3]);
  Carrom.shoot(d, 0, [500, 900, 2]); settle(Carrom, d);
  assert.equal(Carrom.turn(d), 1);
  // the computer finishes boards, Hard faster than Easy, inside the limits
  const selfPlay = (mode, seed) => {
    const s = Carrom.create({ mode, seed });
    for (let g = 0; g < 600 && !s.over; g++) { s.cpu = s.turn; let k = 0; do { Carrom.step(s); } while (!s.moving && !s.over && k++ < 400); settle(Carrom, s); }
    return s;
  };
  const hard = selfPlay("hard", 1), medium = selfPlay("medium", 1);
  assert.equal(hard.over, true);
  assert.equal(medium.over, true);
  assert.ok(hard.shots < 120, `hard: ${hard.shots} shots`);
  const vs = Carrom.create({ mode: "hard", seed: 3 }); Object.assign(vs, { over: true, winnerTeam: 0, winner: 0, points: 12 });
  assert.equal(Carrom.score(vs), 500 + 240);
  assert.ok(Carrom.score(vs) <= LIMIT);
  vs.winnerTeam = 1;
  assert.equal(Carrom.score(vs), 0);
  const s = Carrom.create({ mode: "easy", seed: 4 });
  Carrom.shoot(s, 0, [400, 880, 70]); settle(Carrom, s);
  const back = Carrom.restore(plain(Carrom.save(s)));
  assert.equal(Carrom.checksum(back), Carrom.checksum(s));
  assert.throws(() => Carrom.restore(Object.assign(plain(Carrom.save(s)), { mode: "phones" })));
});

test("carrom: every mode in every look, touch aiming", () => {
  drawEveryLook("carrom", ["easy", "two", "doubles"]);
  // a drag back from the striker shoots, the way the finger pulls
  const sb = makeSandbox();
  const inst = sb.win.ArcadeGames.get("carrom").create(sb.canvas(240, 300), { mode: "two", look: "modern", seed: 2 });
  inst.start(); sb.frames(2);
  const s = inst.logic, st = Carrom.strikerPreview(s, 0, 500);
  const sx = 12 + st[0] / 256, sy = 40 + st[1] / 256;
  inst.pointer("down", sx, sy);
  inst.pointer("move", sx, sy + 40);
  inst.pointer("up", sx, sy + 40);
  sb.frames(2);
  assert.equal(s.shots, 1, "shot");
  assert.ok(s.last.value[2] > 40 && s.last.value[2] < 70, "power from the pull");
  assert.ok(Math.abs(s.last.value[1] - 900) <= 2, "straight ahead");
});
