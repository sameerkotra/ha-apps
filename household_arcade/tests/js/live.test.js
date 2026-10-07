"use strict";
// Playing together, step 2 (live duels, spec §13.4): the lockstep (games/lockstep.js), two phones playing each duel
// game through a fake relay that delays, reorders and duplicates messages and drops a connection (they must stay
// identical, checksum for checksum), a divergence caught by the checksums, the rules of the two-phone modes (Snake
// Duel, Paddle Duel, Tank Battle together / against each other) with honest scores for each player, and the
// browser's live link (together.liveLink).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { makeSandbox, loadLogic, GAMES_DIR } = require("./helpers");

const Lockstep = loadLogic("lockstep.js");
const DUELS = [["snakeduel", "phones"], ["duel", "phones"], ["tanks", "together"], ["tanks", "against"]];
const LOGIC = { snakeduel: loadLogic("snakeduel-logic.js"), duel: loadLogic("duel-logic.js"), tanks: loadLogic("tanks-logic.js") };
// games.py: the honest-score limits (most, a second, base)
const LIMITS = { snakeduel: [5000000, 800, 500], duel: [10000000, 3500, 2000], tanks: [1500000, 160, 500] };
const plain = (v) => JSON.parse(JSON.stringify(v));

function rng(seed) { let x = seed >>> 0 || 1; return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; }; }

// =====================================================================
// The lockstep itself
// =====================================================================

function pair(delay = 3) {
  const sent = { 1: [], 2: [] };
  const a = Lockstep.create({ seat: 1, delay, send: (m) => sent[1].push(m) });
  const b = Lockstep.create({ seat: 2, delay, send: (m) => sent[2].push(m) });
  return { a, b, sent };
}
const relayed = (m, seat) => Object.assign({ seat }, plain(m));

test("lockstep: the first delay-1 ticks need nobody; then a tick waits for the other phone's word", () => {
  const { a, b, sent } = pair(3);
  assert.deepEqual(plain(a.next(0)), []);              // tick 1
  a.advance(); assert.deepEqual(plain(a.next(0)), []);  // tick 2
  a.advance();
  assert.equal(a.next(0), null, "tick 3 needs seat 2's inputs");
  assert.ok(a.waiting(40) >= 40);
  b.advance(); b.flush(true);                          // seat 2 played tick 1: its inputs are final up to 1 + 3 - 1 = 3
  assert.equal(sent[2][0].upto, 3);
  a.receive(relayed(sent[2][0], 2));
  assert.deepEqual(plain(a.next(50)), []);
  assert.equal(a.waiting(60), 0);
});

test("lockstep: an input is played delay ticks later on both phones, seat 1's before seat 2's", () => {
  const { a, b, sent } = pair(3);
  a.local("left", 1); b.local("up", 1); b.local("fire", 1);     // made at tick 0: played at tick 3
  for (let i = 0; i < 2; i++) {
    for (const [me, them, seat] of [[a, b, 1], [b, a, 2]]) { me.next(0); me.advance(); me.flush(true); }
    sent[1].splice(0).forEach((m) => b.receive(relayed(m, 1)));
    sent[2].splice(0).forEach((m) => a.receive(relayed(m, 2)));
  }
  const want = [[1, "left", 1], [2, "up", 1], [2, "fire", 1]];
  assert.deepEqual(plain(a.next(0)), want);
  assert.deepEqual(plain(b.next(0)), want);
});

test("lockstep: a message only claims ticks whose inputs it carries; checksums go out every 60 ticks", () => {
  const { a, sent } = pair(4);
  a.local("right", 1);                                 // tick 4
  a.next(0); a.advance(); a.flush(false);              // after tick 1: final up to 4, so the input goes
  assert.deepEqual(plain(sent[1][0]), { t: "in", seq: 1, upto: 4, ev: [[4, "right", 1]] });
  a.flush(false);
  assert.equal(sent[1].length, 1, "nothing new: nothing sent");
  a.advance(); a.flush(false);
  assert.equal(sent[1].length, 1, "upto goes out every 2 ticks when there is nothing else");
  a.advance(); a.flush(false);
  assert.equal(sent[1][1].upto, 6);
  for (let t = a.tick; t < 60; t++) { a.advance(() => 0xC0FFEE); }
  a.flush(false);
  const last = sent[1][sent[1].length - 1];
  assert.deepEqual(plain(last.sum), [60, 0xC0FFEE]);
  assert.equal(last.upto, 63);
});

test("lockstep: Paddle Duel's aim changing within one tick is one input; at most 6 inputs a tick", () => {
  const { a, sent } = pair(3);
  a.local("aim", 100); a.local("aim", 110); a.local("aim", 120);
  for (let i = 0; i < 9; i++) a.local("fire", i % 2);
  a.advance(); a.flush(true);
  assert.deepEqual(plain(sent[1][0].ev[0]), [3, "aim", 120]);
  assert.equal(sent[1][0].ev.length, Lockstep.MAX_PER_TICK);
});

test("lockstep: duplicates are ignored, an early message waits for the one before it", () => {
  const { a, b, sent } = pair(3);
  for (let i = 0; i < 6; i++) { b.local("up", i % 2); b.next(0); b.advance(); b.flush(true); }
  const msgs = sent[2].map((m) => relayed(m, 2));
  assert.equal(msgs.length, 6);
  a.receive(msgs[0]); a.receive(msgs[0]); a.receive(msgs[2]); a.receive(msgs[3]);
  assert.equal(a.upto[2], msgs[0].upto, "3 and 4 wait for 2");
  a.receive(msgs[1]);
  assert.equal(a.upto[2], msgs[3].upto);
  a.receive(msgs[3]); a.receive(msgs[5]); a.receive(msgs[4]);
  assert.equal(a.upto[2], msgs[5].upto);
  assert.ok(a.stats.dup >= 2 && a.stats.early >= 3);
});

test("lockstep: after a reconnection the server's welcome makes the phone send again what it hasn't acknowledged", () => {
  const { a, sent } = pair(3);
  for (let i = 0; i < 5; i++) { a.local("left", 1); a.next(0); a.advance(); a.flush(true); }
  assert.equal(a.unacked, 5);
  a.receive({ t: "ack", seq: 2 });
  assert.equal(a.unacked, 3);
  const before = sent[1].length;
  a.receive({ t: "welcome", inSeq: 3 });
  assert.deepEqual(sent[1].slice(before).map((m) => m.seq), [4, 5], "the ones after the server's last");
  a.receive({ t: "error", why: "gap", inSeq: 4 });
  assert.deepEqual(sent[1].slice(before + 2).map((m) => m.seq), [5]);
});

test("lockstep: behind() says how far the other phone is ahead", () => {
  const { a, b, sent } = pair(4);
  for (let i = 0; i < 20; i++) { b.next(0); b.advance(); b.flush(true); }
  sent[2].forEach((m) => a.receive(relayed(m, 2)));
  assert.equal(a.behind(), 20);
  for (let i = 0; i < 18; i++) { a.next(0); a.advance(); }
  assert.equal(a.behind(), 2);
});

// =====================================================================
// The rules on their own: deterministic, per player, and never a sin/cos/pow/random/Date in live play
// =====================================================================

const FORBIDDEN = ["sin", "cos", "tan", "atan", "atan2", "asin", "acos", "exp", "log", "pow", "hypot", "cbrt", "random", "sinh", "cosh", "tanh", "expm1", "log1p", "log2", "log10"];
function withoutEngineMaths(fn) {
  const saved = {}, now = Date.now;
  for (const k of FORBIDDEN) { saved[k] = Math[k]; Math[k] = () => { throw new Error(`Math.${k} in live rules (engines may differ)`); }; }
  Date.now = () => { throw new Error("Date.now in live rules"); };
  try { return fn(); } finally { Object.assign(Math, saved); Date.now = now; }
}
const ACTIONS = {
  snakeduel: () => [["up", 1], ["down", 1], ["left", 1], ["right", 1]],
  duel: (r) => [["left", r() < 0.5 ? 1 : 0], ["right", r() < 0.5 ? 1 : 0], ["fire", 1], ["aim", Math.floor(r() * 241)]],
  tanks: (r) => [["up", r() < 0.6 ? 1 : 0], ["down", r() < 0.6 ? 1 : 0], ["left", r() < 0.6 ? 1 : 0], ["right", r() < 0.6 ? 1 : 0], ["fire", r() < 0.7 ? 1 : 0], ["steer", Math.floor(r() * 5)]],
};
function playRules(game, mode, seed, ticks, script) {
  const L = LOGIC[game], s = L.create({ mode, seed }), r = rng(seed * 31 + 7), sums = [];
  for (let t = 1; t <= ticks && !s.over; t++) {
    for (let p = 0; p < 2; p++) {
      if (r() < 0.07) { const opts = ACTIONS[game](r); const [a, v] = opts[Math.floor(r() * opts.length)]; L.press(s, a, v, p); }
    }
    if (script) script(L, s, t);
    L.step(s);
    if (t % 30 === 0) sums.push(L.checksum(s));
  }
  return { s, sums, sum: L.checksum(s), report: L.report(s) };
}

for (const [game, mode] of DUELS) {
  test(`${game} · ${mode}: the rules are deterministic and use no engine-dependent maths`, () => {
    withoutEngineMaths(() => {
      for (const seed of [1, 2, 3, 99, 4242]) {
        const x = playRules(game, mode, seed, 4000), y = playRules(game, mode, seed, 4000);
        assert.deepEqual(x.sums, y.sums);
        assert.equal(JSON.stringify(x.s), JSON.stringify(y.s));
        assert.deepEqual(plain(x.report), plain(y.report));
        assert.equal(x.report.scores.length, 2);
      }
    });
  });

  test(`${game} · ${mode}: the checksum changes with the game`, () => {
    const L = LOGIC[game], s = L.create({ mode, seed: 5 });
    const c0 = L.checksum(s);
    L.step(s);
    assert.notEqual(L.checksum(s), c0, "an update is a different game");
    const c1 = L.checksum(s);
    s.score += 10;
    assert.notEqual(L.checksum(s), c1, "a score is part of it");
    assert.ok(Number.isInteger(c1) && c1 >= 0 && c1 < 2 ** 32);
  });

  test(`${game} · ${mode}: each player's score stays inside the honest-score limits`, () => {
    const [most, rate, base] = LIMITS[game];
    let checked = 0;
    for (let seed = 1; seed <= 25; seed++) {
      const { s, report } = playRules(game, mode, seed, 20000);
      const L = LOGIC[game], seconds = Math.round(s.updates / 60);
      for (let p = 0; p < 2; p++) {
        const r = L.result(s, p);
        assert.equal(r.score, report.scores[p]);
        assert.ok(r.score <= most && r.score <= seconds * rate + base, `${game} seed ${seed} player ${p + 1}: ${r.score} in ${seconds} s`);
        checked++;
      }
    }
    assert.equal(checked, 50);
  });
}

// ---------- Snake Duel · Two phones ----------
test("snake duel · phones: each phone's player steers their own snake and scores for themselves", () => {
  const L = LOGIC.snakeduel, s = L.create({ mode: "phones", seed: 3 });
  assert.equal(s.snakes[1].cpu, false);
  assert.ok(s.level >= 1 && s.level <= L.LEVELS.length, "the arena comes from the seed");
  L.press(s, "up", true, 1);
  assert.deepEqual(plain(s.snakes[1].queue), ["up"]);
  assert.deepEqual(plain(s.snakes[0].queue), []);
  L.press(s, "down", true, 0);
  assert.deepEqual(plain(s.snakes[0].queue), ["down"]);
  L.press(s, "up2", true, 1);                          // only the arrows' names travel
  assert.deepEqual(plain(s.snakes[1].queue), ["up"]);
  // player 2 wins a match: their score, their result
  const t = L.create({ mode: "phones", seed: 3 });
  t.phase = "play"; t.wins = [0, 2];
  t.snakes[0].body[0] = { x: 0, y: 0 }; t.snakes[0].dir = "up"; t.snakes[0].pu = 599;
  for (let i = 0; i < 5 && !t.over; i++) L.step(t);
  assert.equal(t.over, true);
  assert.equal(t.matchWinner, 2);
  assert.ok(t.score2 >= L.ROUND_POINTS + L.MATCH_POINTS, "round and match for player 2");
  assert.equal(L.report(t).winner, 2);
  assert.equal(L.result(t, 1).stats.won, true);
  assert.equal(L.result(t, 0).stats.won, false);
  assert.equal(L.result(t, 1).score, t.score2);
});

// ---------- Paddle Duel · Two phones ----------
test("paddle duel · phones: two paddles, the ball served to the one who lost the point, first to 7", () => {
  const L = LOGIC.duel, s = L.create({ mode: "phones", seed: 8 });
  assert.equal(s.pads.length, 2);
  const to = s.serveTo;
  L.press(s, "fire", true, 1 - to);                   // only the one the ball goes to can serve early
  for (let i = 0; i < 61; i++) L.step(s);
  assert.ok(s.serve > 0);
  L.press(s, "fire", true, to);
  L.step(s);
  assert.equal(s.serve, 0);
  assert.ok(to === 0 ? s.ball.vy > 0 : s.ball.vy < 0, "toward the receiver");
  // aim: the paddle follows the finger
  L.press(s, "aim", 30, 0); L.press(s, "aim", 210, 1);
  for (let i = 0; i < 20; i++) { L.press(s, "aim", 30, 0); L.press(s, "aim", 210, 1); L.step(s); if (s.serve) break; }
  assert.ok(s.pads[0].x < 60 * L.FP && s.pads[1].x > 180 * L.FP);
  // both paddles in a corner: the points come quickly; someone gets to 7
  const t = L.create({ mode: "phones", seed: 2 });
  for (let i = 0; i < 60 * 300 && !t.over; i++) { L.press(t, "aim", 0, 0); L.press(t, "aim", 240, 1); L.step(t); }
  assert.equal(t.over, true);
  assert.equal(Math.max(...t.points), L.PHONES_TO_WIN);
  const w = t.winner - 1;
  assert.equal(L.report(t).winner, t.winner);
  assert.ok((w ? t.score2 : t.score) >= 7 * L.POINT_POINTS + L.MATCH_POINTS);
  assert.equal(L.result(t, w).stats.won, true);
  assert.equal(L.result(t, 1 - w).stats.won, false);
});

test("paddle duel · phones: the court is the same from either end (seat 2's game is seat 1's turned round)", () => {
  // From each serve on, a second game that is the first turned round (players swapped, x → 240 − x,
  // y → 320 − y), given the same inputs turned round, stays exactly that: neither end has an advantage.
  const L = LOGIC.duel, F = L.FP;
  const mirror = (a) => {
    const b = plain(a);
    b.ball.x = 240 * F - a.ball.x; b.ball.y = 320 * F - a.ball.y; b.ball.vx = -a.ball.vx; b.ball.vy = -a.ball.vy;
    b.pads = [1, 0].map((i) => ({ x: 240 * F - a.pads[i].x, v: -a.pads[i].v, left: a.pads[i].right, right: a.pads[i].left,
      target: a.pads[i].target === null ? null : 240 * F - a.pads[i].target }));
    b.points = [a.points[1], a.points[0]]; b.serveTo = 1 - a.serveTo; b.score = a.score2; b.score2 = a.score;
    return b;
  };
  const r = rng(5);
  const a = L.create({ mode: "phones", seed: 1 });
  let b = null, compared = 0, hits = 0;
  for (let t = 0; t < 20000 && !a.over; t++) {
    if (a.serve === 0 && !b) b = mirror(a);                // a serve: from here on, the turned-round game
    // both players follow the ball, a little off (so the angles vary and points are scored)
    const bx = Math.round(a.ball.x / F), x0 = bx + Math.floor(r() * 50) - 25, x1 = bx + Math.floor(r() * 50) - 25, go = r() < 0.5;
    if (go) { L.press(a, "aim", x0, 0); L.press(a, "aim", x1, 1); if (b) { L.press(b, "aim", 240 - x0, 1); L.press(b, "aim", 240 - x1, 0); } }
    const evs = L.step(a);
    if (b) {
      L.step(b);
      if (evs.some((e) => e.type === "point" || e.type === "win" || e.type === "gameover")) { b = null; continue; }
      hits += evs.filter((e) => e.type === "paddle").length;
      assert.equal(b.ball.x, 240 * F - a.ball.x, `x at ${t}`);
      assert.equal(b.ball.y, 320 * F - a.ball.y, `y at ${t}`);
      assert.equal(b.pads[0].x, 240 * F - a.pads[1].x);
      compared++;
    }
  }
  assert.ok(compared > 500 && hits > 5, `${compared} updates, ${hits} returns compared`);
});

// ---------- Tank Battle · two tanks ----------
test("tanks · together: two tanks, one flag, more enemies, kills to the one who hit, friendly shells stop", () => {
  const L = LOGIC.tanks, s = L.create({ mode: "together", seed: 4 });
  assert.equal(L.humans(s).length, 2);
  assert.deepEqual([s.p2.x, s.p2.y], [L.P2_START.x, L.P2_START.y]);
  assert.equal(s.total, Math.ceil(L.LEVELS[0].enemies * 1.5));
  assert.equal(L.cellAt(s, 16, 24), L.EMPTY, "player 2's start is clear");
  for (let i = 0; i < L.INTRO; i++) L.step(s);
  // player 2's shell destroys an enemy: their kill
  s.enemies.push({ id: 99, x: s.p2.x, y: s.p2.y - 60, dir: 2, acc: 0, arrive: 0, turn: 500, stuck: false });
  L.press(s, "up", true, 1); L.press(s, "fire", true, 1); L.press(s, "fire", false, 1); L.press(s, "up", false, 1);
  for (let i = 0; i < 40 && s.enemies.some((e) => e.id === 99); i++) L.step(s);
  assert.ok(!s.enemies.some((e) => e.id === 99));
  assert.equal(s.score2, L.KILL_POINTS);
  assert.equal(s.score, 0);
  // player 1 shoots player 2: nothing happens to player 2
  const t = L.create({ mode: "together", seed: 4 });
  for (let i = 0; i < L.INTRO; i++) L.step(t);
  t.p2.y = 180; t.player.x = t.p2.x - 60; t.player.y = 180; t.p2.shield = 0;      // row 11 is open in arena 1
  L.press(t, "right", true, 0); L.step(t); L.press(t, "right", false, 0);
  L.press(t, "fire", true, 0); L.press(t, "fire", false, 0);
  const shots = t.stats.shots;
  for (let i = 0; i < 30; i++) L.step(t);
  assert.equal(t.stats.shots, shots + 1);
  assert.equal(t.lives2, 3);
  assert.equal(t.p2.dead, 0);
  // the game ends when both are out of lives
  t.lives = 1; t.lives2 = 1;
  t.player.shield = 0; t.p2.shield = 0;
  t.shells.push({ x: t.player.x + 9, y: t.player.y - 1, dir: 2, v: 2.5, owner: 7 });
  L.step(t);
  assert.ok(t.player.dead > 1e8 && !t.over, "player 1 is out, player 2 plays on");
  t.shells.push({ x: t.p2.x + 9, y: t.p2.y - 1, dir: 2, v: 2.5, owner: 7 });
  L.step(t);
  assert.equal(t.over, true);
  assert.equal(t.stats.cause, "lives");
});

test("tanks · against each other: a flag each, hits, rounds and the match", () => {
  const L = LOGIC.tanks;
  for (const a of L.VERSUS_LEVELS) {
    for (let r = 0; r < 13; r++) for (let c = 0; c < 13; c++) assert.equal(a.map[r][c], a.map[12 - r][12 - c], `${a.name} is the same turned round`);
  }
  const s = L.create({ mode: "against", seed: 6 });
  assert.equal(s.total, 0);
  assert.deepEqual([s.p2.x, s.p2.y, s.p2.dir], [L.P2_TOP.x, L.P2_TOP.y, 2]);
  for (let i = 0; i < L.INTRO; i++) L.step(s);
  // a hit on the other tank: 100, a life (a clear lane between them)
  for (let cy = 0; cy < 12; cy++) for (const cx of [16, 17]) s.map[cy * L.N + cx] = L.EMPTY;
  s.p2.shield = 0; s.player.x = s.p2.x; s.player.y = s.p2.y + 60; s.player.dir = 0;
  L.press(s, "fire", true, 0); L.press(s, "fire", false, 0);
  for (let i = 0; i < 30 && s.lives2 === 3; i++) L.step(s);
  assert.equal(s.lives2, 2);
  assert.equal(s.score, L.V_HIT);
  // player 2's flag falls: player 1 wins the round
  s.shells.push({ x: L.FLAG2.x + 9, y: L.FLAG2.y + 20, dir: 0, v: 4.5, owner: 0 });
  for (let i = 0; i < 10 && !s.clear; i++) L.step(s);
  assert.equal(s.wins[0], 1);
  assert.equal(s.score, L.V_HIT + L.V_ROUND);
  for (let i = 0; i < L.CLEAR_PAUSE + 1; i++) L.step(s);
  assert.equal(s.level, 2, "round 2");
  assert.equal(s.lives2, 3, "full lives again");
  for (let i = 0; i < L.INTRO; i++) L.step(s);
  s.shells.push({ x: L.FLAG2.x + 9, y: L.FLAG2.y + 20, dir: 0, v: 4.5, owner: -1 });   // their own shell counts too
  for (let i = 0; i < 10 && !s.over; i++) L.step(s);
  assert.equal(s.over, true);
  assert.equal(s.matchWinner, 1);
  assert.equal(L.report(s).winner, 1);
  assert.equal(L.result(s, 0).stats.won, true);
  assert.equal(L.result(s, 1).stats.won, false);
  assert.equal(s.score, L.V_HIT + 2 * L.V_ROUND + L.V_MATCH);
  // a round that runs out of time is drawn; five rounds at most
  const t = L.create({ mode: "against", seed: 6 });
  for (let i = 0; i < 5 * (L.INTRO + L.V_ROUND_LIMIT + L.CLEAR_PAUSE) + 50 && !t.over; i++) L.step(t);
  assert.equal(t.over, true);
  assert.equal(t.matchWinner, 0, "all drawn: a drawn match");
  assert.equal(L.report(t).winner, 0);
});

test("tanks: single-player modes have no second tank", () => {
  const L = LOGIC.tanks;
  for (const mode of ["classic", "easy"]) {
    const s = L.create({ mode, seed: 1 });
    assert.equal(s.two, undefined);
    assert.equal(s.p2, undefined);
    assert.equal(L.humans(s).length, 1);
  }
});

// =====================================================================
// Two phones through a fake relay (the server's rules: in order by seq, gaps, acks, numbered messages)
// =====================================================================

function phone(game, mode, seat, seed, names) {
  const sb = makeSandbox();
  const ends = [], sums = new Map();
  const box = { sb, seat, since: 0, ends, sums, inst: null, ls: null, hold: [] };
  return box;
}

/** Plays a duel on two phones in virtual time. opts: seed, latency [lo, hi] ms, dup, drop: {seat, from, to},
    diverge: {seat, at, fn}, ms (how long), period: [p1, p2] frame times. */
function simulate(game, mode, opts) {
  const seed = opts.seed || 11, r = rng(seed * 13 + 1);
  const latency = opts.latency || [5, 60], delay = opts.delay || 4;
  const events = [];      // [time, order, fn]
  let now = 0, order = 0;
  const at = (t, fn) => { events.push([t, order++, fn]); };
  const relay = { seats: { 1: { seq: 0, upto: delay - 1, sums: {}, n: 0, out: [] }, 2: { seq: 0, upto: delay - 1, sums: {}, n: 0, out: [] } },
    up: { 1: true, 2: true }, mismatch: null, dups: 0, gaps: 0, reordered: 0, lost: 0, forwarded: 0 };
  const phones = {};
  const lag = () => latency[0] + r() * (latency[1] - latency[0]);

  function toPhone(seat, msg) {
    if (!relay.up[seat]) { relay.lost++; return; }
    const t = now + lag();
    at(t, () => deliver(seat, msg));
    if (r() < (opts.dup || 0)) { relay.dups++; at(t + lag(), () => deliver(seat, msg)); }
  }
  function deliver(seat, msg) {
    if (!relay.up[seat]) { relay.lost++; return; }
    const P = phones[seat];
    if (typeof msg.n === "number") {               // the link: numbered messages in order, once each
      if (msg.n <= P.since) return;
      if (msg.n > P.since + 1) { P.early[msg.n] = msg; return; }
      P.since = msg.n; P.ls.receive(msg);
      while (P.early[P.since + 1]) { const m = P.early[P.since + 1]; delete P.early[P.since + 1]; P.since = m.n; P.ls.receive(m); }
      return;
    }
    P.ls.receive(msg);
  }
  function numbered(seat, msg) {
    const S = relay.seats[seat];
    S.n++;
    const m = Object.assign({}, msg, { n: S.n });
    S.out.push(m);
    toPhone(seat, m);
  }
  function arrive(seat, msg) {
    if (!relay.up[seat]) { relay.lost++; return; }
    const S = relay.seats[seat], O = relay.seats[3 - seat];
    if (msg.t !== "in") return;
    if (msg.seq <= S.seq) { toPhone(seat, { t: "ack", seq: S.seq }); return; }
    if (msg.seq !== S.seq + 1) { relay.gaps++; toPhone(seat, { t: "error", why: "gap", inSeq: S.seq }); return; }
    assert.ok(msg.upto >= S.upto && msg.upto <= O.upto + delay, `seat ${seat} claims ${msg.upto} with the other at ${O.upto}`);
    for (const e of msg.ev) assert.ok(e[0] > S.upto && e[0] <= msg.upto, "an input inside the ticks the message speaks for");
    S.seq = msg.seq; S.upto = msg.upto;
    relay.forwarded++;
    numbered(3 - seat, { t: "in", seat, seq: msg.seq, upto: msg.upto, ev: msg.ev });
    toPhone(seat, { t: "ack", seq: msg.seq });
    if (msg.sum) {
      S.sums[msg.sum[0]] = msg.sum[1];
      const theirs = O.sums[msg.sum[0]];
      if (theirs !== undefined && theirs !== msg.sum[1] && !relay.mismatch) relay.mismatch = msg.sum[0];
    }
  }
  function fromPhone(seat, msg) {
    if (!relay.up[seat]) { relay.lost++; return; }
    const t = now + lag();
    at(t, () => arrive(seat, plain(msg)));
    if (r() < (opts.dup || 0)) { relay.dups++; at(t + lag() * 2, () => arrive(seat, plain(msg))); }
  }

  for (const seat of [1, 2]) {
    const sb = makeSandbox();
    const P = phones[seat] = { sb, seat, since: 0, early: {}, ends: [], ticks: [], sumAt: new Map(), stateAt: new Map() };
    P.ls = Lockstep.create({ seat, delay, send: (m) => fromPhone(seat, m) });
    const advance = P.ls.advance;
    P.ls.advance = (fn) => { const t = advance(fn); P.sumAt.set(t, fn()); if (t === opts.compareAt) P.stateAt.set(t, JSON.stringify(P.inst.logic)); if (opts.diverge && opts.diverge.seat === seat && t === opts.diverge.at) opts.diverge.fn(P.inst.logic); return t; };
    P.inst = sb.win.ArcadeGames.get(game).create(sb.canvas(390, 487), { mode, seed, look: seat === 1 ? "modern" : "lcd", names: ["Kabir Rao", "Meera Rao"],
      live: { seat, lockstep: P.ls }, onEnd: (res) => P.ends.push(res) });
    P.inst.start();
  }
  const period = opts.period || [16.67, 17.4];
  const frame = (seat) => {
    const P = phones[seat];
    const dt = period[seat - 1] * (0.85 + r() * 0.3) + (r() < 0.01 ? 150 : 0);      // jitter and the odd stall
    P.sb.frames(1, dt);
    // this player's thumb: keys, buttons and fingers
    const rr = r();
    const keys = opts.keys || ["up", "down", "left", "right", "fire", "up2", "left2"];
    if (rr < 0.06) { const k = keys[Math.floor(r() * keys.length)]; P.inst.input(k, true); if (r() < 0.6) P.inst.input(k, false); }
    else if (rr < (opts.fingers === undefined ? 0.09 : 0.06 + opts.fingers)) { const x = r() * 390, y = r() * 487; P.inst.pointer("down", x, y); P.inst.pointer("move", x + (r() - 0.5) * 80, y + (r() - 0.5) * 80); if (r() < 0.5) P.inst.pointer("up", x, y); }
    if (now < (opts.ms || 60000) && !(P.ends.length && phones[3 - seat].ends.length)) at(now + dt, () => frame(seat));
  };
  at(0, () => frame(1)); at(3, () => frame(2));
  if (opts.drop) {
    const d = opts.drop;
    at(d.from, () => { relay.up[d.seat] = false; });
    at(d.to, () => {
      relay.up[d.seat] = true;
      const P = phones[d.seat], S = relay.seats[d.seat];
      // hello {since}: the welcome (my last accepted message) and the numbered messages after `since`
      toPhone(d.seat, { t: "welcome", inSeq: relay.seats[d.seat].seq });
      S.out.filter((m) => m.n > P.since).forEach((m) => toPhone(d.seat, m));
    });
  }
  let steps = 0;
  while (events.length && steps < 3e6) {
    events.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const [t, , fn] = events.shift();
    now = t; fn(); steps++;
  }
  return { phones, relay };
}

// Tank players who mostly drive up and fire (away from their own flag), so the games last
const BOTS = { tanks: { keys: ["up", "up", "up", "fire", "fire", "left", "right", "down"], fingers: 0.005 } };

function sameGame(res, label) {
  const [a, b] = [res.phones[1], res.phones[2]];
  const n = Math.min(a.ls.tick, b.ls.tick);
  assert.ok(n >= 300 || (a.ends.length && b.ends.length), `${label}: both played at least 5 seconds (${a.ls.tick}, ${b.ls.tick})`);
  for (let t = 1; t <= n; t++) assert.equal(a.sumAt.get(t), b.sumAt.get(t), `${label}: checksums differ at tick ${t}`);
  assert.equal(res.relay.mismatch, null);
  if (a.ends.length && b.ends.length) {
    assert.equal(JSON.stringify(a.inst.logic), JSON.stringify(b.inst.logic), `${label}: the same game at the end`);
    assert.deepEqual(plain(a.ends[0].live), plain(b.ends[0].live), `${label}: both phones report the same end`);
    assert.equal(a.ends[0].score, a.ends[0].live.scores[0]);
    assert.equal(b.ends[0].score, b.ends[0].live.scores[1]);
    assert.equal(a.ends[0].seconds, b.ends[0].seconds);
  }
  return n;
}

for (const [game, mode] of DUELS) {
  test(`${game} · ${mode}: two phones stay identical through delays, reordering, duplicates and a dropped connection`, () => {
    // home Wi-Fi with bad moments: 5–70 ms each way (in any order), 8 % of messages twice, a 1.8 s drop
    const res = simulate(game, mode, Object.assign({ seed: 21, latency: [5, 70], delay: 6, dup: 0.08, drop: { seat: 2, from: 3000, to: 4800 }, ms: 40000 }, BOTS[game]));
    const n = sameGame(res, `${game}/${mode}`);
    assert.ok(res.relay.dups > 0 && res.relay.lost > 0, "messages were duplicated and lost");
    assert.ok(res.relay.forwarded > 100);
    assert.ok(res.phones[2].ls.stats.resent > 0 || res.relay.gaps > 0, "after the drop the phone sent again");
    assert.ok(n > 4800 / 16.7, "the game went on after the reconnection");
  });

  test(`${game} · ${mode}: different screens (30 Hz, 144 Hz) and a slow link still play the same game`, () => {
    const res = simulate(game, mode, Object.assign({ seed: 5, latency: [40, 140], period: [33.3, 6.94], delay: 12, ms: 20000 }, BOTS[game]));
    sameGame(res, `${game}/${mode} slow`);
  });

  test(`${game} · ${mode}: a phone whose game went its own way is caught by the checksums`, () => {
    const L = LOGIC[game];
    const res = simulate(game, mode, { seed: 3, delay: 6, latency: [5, 40], ms: 15000, diverge: { seat: 2, at: 200, fn: (s) => { s.rng = (s.rng + 12345) | 0; s.score += 1; } } });
    assert.ok(res.relay.mismatch !== null, "out of step");
    assert.ok(res.relay.mismatch <= 240, `found at the first checksum after the change (${res.relay.mismatch})`);
    assert.ok(L.checksum);
  });
}

test("a phone that hears nothing more from the other stops and waits (it never runs ahead)", () => {
  const res = simulate("snakeduel", "phones", { seed: 4, drop: { seat: 2, from: 2000, to: 1e9 }, ms: 6000 });
  const a = res.phones[1].ls, b = res.phones[2].ls;
  assert.ok(a.tick <= res.relay.seats[2].upto, "phone 1 stopped at the last tick it had phone 2's inputs for");
  assert.ok(a.tick < 6000 / 16.7 - 60, "and waited");
  assert.ok(res.phones[1].inst.liveStatus().waitingMs > 1000);
  assert.ok(b.tick <= res.relay.seats[1].upto + 1);
});

// =====================================================================
// The registry, the kit and the drawing of the two-phone modes
// =====================================================================

test("registry: the duel games say lockstep and which modes are played on two phones", () => {
  const { win } = makeSandbox();
  const want = { snakeduel: ["phones"], duel: ["phones"], tanks: ["together", "against"] };
  for (const [id, modes] of Object.entries(want)) {
    const def = win.ArcadeGames.get(id);
    assert.equal(def.lockstep, true, id);
    assert.deepEqual(plain(def.liveModes), modes, id);
  }
  assert.equal(win.ArcadeGames.get("snake").lockstep, false);
  assert.deepEqual(plain(win.ArcadeGames.get("snake").liveModes), []);
  win.ArcadeGames.register({ id: "x", name: "X", modes: [{ id: "a", label: "A" }], liveModes: ["a", "zz"], create() {} });
  assert.deepEqual(plain(win.ArcadeGames.get("x").liveModes), [], "liveModes need lockstep");
  win.ArcadeGames.register({ id: "y", name: "Y", modes: [{ id: "a", label: "A" }], lockstep: true, liveModes: ["a", "zz"], create() {} });
  assert.deepEqual(plain(win.ArcadeGames.get("y").liveModes), ["a"]);
});

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
for (const [game, mode] of DUELS) {
  test(`${game} · ${mode}: both seats draw in every look, my side at the bottom`, () => {
    for (const look of LOOKS) for (const seat of [1, 2]) {
      const sb = makeSandbox();
      const ls = Lockstep.create({ seat, delay: 3, send() {} });
      const inst = sb.win.ArcadeGames.get(game).create(sb.canvas(390, 487), { mode, look, seed: 2, names: ["Kabir Rao", "Meera Rao"], live: { seat, lockstep: ls } });
      inst.start();
      sb.frames(5);                      // ticks 1 and 2 need nobody
      assert.equal(ls.tick, 2);
      sb.frames(20);
      assert.equal(ls.tick, 2, "then it waits for the other phone");
      assert.equal(sb.counts.nonFinite || 0, 0);
      inst.destroy();
    }
  });
}

test("paddle duel · phones: seat 2's left is the court's right, and its finger is turned round", () => {
  const sent = [];
  const sb = makeSandbox();
  const ls = Lockstep.create({ seat: 2, delay: 3, send: (m) => sent.push(m) });
  const inst = sb.win.ArcadeGames.get("duel").create(sb.canvas(240, 300), { mode: "phones", seed: 1, live: { seat: 2, lockstep: ls } });
  inst.start();
  inst.input("left", true);
  inst.pointer("move", 40, 200);
  inst.input("up", true);
  for (let i = 0; i < 3; i++) { ls.receive({ t: "in", seat: 1, seq: i + 1, upto: 3 + 2 * (i + 1), ev: [] }); sb.frames(2); }
  const ev = sent.flatMap((m) => m.ev).map((e) => [e[1], e[2]]);
  assert.deepEqual(plain(ev.slice(0, 3)), [["right", 1], ["aim", 200], ["fire", 1]]);
  assert.equal(inst.logic.pads[1].right, true, "and it reaches the rules as player 2's");
});

test("tanks · against: seat 2 drives the arena turned round; a tap on my tank fires", () => {
  const sent = [];
  const sb = makeSandbox();
  const ls = Lockstep.create({ seat: 2, delay: 3, send: (m) => sent.push(m) });
  const inst = sb.win.ArcadeGames.get("tanks").create(sb.canvas(240, 300), { mode: "against", seed: 1, live: { seat: 2, lockstep: ls } });
  inst.start();
  inst.input("up", true); inst.input("up", false);
  const p2 = inst.logic.p2, T = LOGIC.tanks;
  // my tank is drawn at the bottom: (ARENA − x − 18, ARENA − y − 18); tap its middle
  const sx = T.OX + T.ARENA - p2.x - 9, sy = T.OY + T.ARENA - p2.y - 9;
  inst.pointer("down", sx, sy);
  inst.pointer("move", sx, sy - 80);                // a finger above my tank on my screen: the arena's down
  for (let i = 0; i < 3; i++) { ls.receive({ t: "in", seat: 1, seq: i + 1, upto: 3 + 2 * (i + 1), ev: [] }); sb.frames(2); }
  const ev = sent.flatMap((m) => m.ev).map((e) => [e[1], e[2]]);
  assert.deepEqual(plain(ev), [["down", 1], ["down", 0], ["fire", 1], ["fire", 0], ["steer", 3]]);
});

test("the kit: a live game's end carries the report both phones must agree on", () => {
  const sb = makeSandbox();
  const ends = [];
  const ls = Lockstep.create({ seat: 1, delay: 3, send() {} });
  const inst = sb.win.ArcadeGames.get("snakeduel").create(sb.canvas(), { mode: "phones", seed: 9, live: { seat: 1, lockstep: ls }, onEnd: (r) => ends.push(r) });
  inst.start();
  const s = inst.logic;
  s.phase = "play"; s.wins = [2, 0]; s.snakes[1].body[0] = { x: 0, y: 0 }; s.snakes[1].dir = "up"; s.snakes[1].pu = 599; s.snakes[0].pu = 0;
  let seq = 0;
  for (let i = 0; i < 30 && !ends.length; i++) { ls.receive({ t: "in", seat: 2, seq: ++seq, upto: 2 + 2 * seq, ev: [] }); sb.frames(1); }
  assert.equal(ends.length, 1);
  const live = ends[0].live;
  assert.deepEqual(plain(live.scores), [s.score, s.score2]);
  assert.equal(live.winner, 1);
  assert.equal(live.tick, ls.tick);
  assert.equal(live.sum, LOGIC.snakeduel.checksum(s));
  assert.equal(ends[0].score, s.score);
  assert.equal(inst.liveStatus().seat, 1);
});

// =====================================================================
// The browser's live link (together.liveLink)
// =====================================================================

function together(over = {}) {
  const calls = { api: [] };
  const sockets = [];
  class FakeWebSocket {
    constructor(url) {
      if (over.wsThrows) throw new Error("no sockets here");
      this.url = url; this.readyState = 0; this.sent = []; sockets.push(this);
    }
    send(t) { this.sent.push(JSON.parse(t)); }
    close() { this.readyState = 3; this.closed = true; }
    open() { this.readyState = 1; if (this.onopen) this.onopen(); }
    message(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
    drop() { this.readyState = 3; if (this.onclose) this.onclose(); }
  }
  const h = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false) });
  let clock = 1000;
  const answers = over.answers || ((p) => (/\/live$/.test(p) ? { msgs: [] } : { v: 1, status: "playing", kind: "live", players: [] }));
  const ctx = vm.createContext({
    window: {}, console, JSON, Math, Date, Promise, URL, Object, Array, String, Number, setTimeout, clearTimeout, setInterval, clearInterval,
    performance: { now: () => clock },
    location: { href: "https://ha.example/api/hassio_ingress/abc/", protocol: "https:" },
    document: { hidden: false },
    WebSocket: over.noWebSocket ? undefined : FakeWebSocket,
    h, spinner: () => h("div"), mount: () => {}, clear: () => {}, openModal: () => ({ close() {} }), fmtNum: (n) => String(n),
    fmtDuration: (n) => `${n} s`, toast() {}, fail() {}, showTab() {}, $: () => null, state: { tab: "home" }, renderHome() {},
    api: (p, o) => { calls.api.push([p, o]); return new Promise((r) => setTimeout(r, o && o.body && o.body.wait ? 100 : 0)).then(() => answers(p, o)); },
  });
  ctx.window.window = ctx.window;
  vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, "..", "together.js"), "utf8"), ctx, { filename: "together.js" });
  return { T: ctx.window.Together, calls, sockets, tick(ms) { clock += ms; } };
}
const links = [];
test.afterEach(() => { while (links.length) links.pop().close(); });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

test("live link: hello on open, numbered messages once each, pings measure the round trip", async () => {
  const t = together();
  const got = [], pics = [];
  const link = t.T.liveLink("M1", { message: (m) => got.push(m), match: (m) => pics.push(m) }); links.push(link);
  const ws = t.sockets[0];
  assert.equal(ws.url, "wss://ha.example/api/hassio_ingress/abc/api/matches/M1/live");
  ws.open();
  assert.deepEqual(plain(ws.sent[0]), { t: "hello", since: 0 });
  assert.equal(ws.sent[1].t, "ping");
  t.tick(42);
  ws.message({ t: "pong", id: ws.sent[1].id });
  assert.equal(link.rtt(), 42);
  ws.message({ t: "welcome", inSeq: 0, last: 0 });
  ws.message({ t: "in", seat: 2, seq: 1, upto: 5, ev: [], n: 1 });
  ws.message({ t: "in", seat: 2, seq: 1, upto: 5, ev: [], n: 1 });        // again: dropped
  ws.message({ t: "start", delay: 4, inMs: 3000, n: 2 });
  ws.message({ t: "match", v: 3, players: [] });
  assert.deepEqual(got.map((m) => m.t), ["welcome", "in", "start"]);
  assert.equal(link.since, 2);
  assert.equal(pics[pics.length - 1].v, 3);
  link.send({ t: "in", seq: 1, upto: 4, ev: [] });
  assert.deepEqual(plain(ws.sent[ws.sent.length - 1]), { ack: 2, t: "in", seq: 1, upto: 4, ev: [] }, "acknowledges what it has");
  link.send({ t: "pause" });
  assert.deepEqual(plain(ws.sent[ws.sent.length - 1]), { t: "pause" });
});

test("live link: a dropped socket opens again and says where it was", async () => {
  const t = together();
  const kinds = [];
  const link = t.T.liveLink("M2", { message() {}, transport: (k) => kinds.push(k) }); links.push(link);
  t.sockets[0].open();
  for (let n = 1; n <= 7; n++) t.sockets[0].message({ t: "in", seat: 1, seq: n, upto: 5 + n, ev: [], n });
  t.sockets[0].drop();
  assert.equal(kinds[kinds.length - 1], "reconnecting");
  await wait(600);
  assert.equal(t.sockets.length, 2);
  t.sockets[1].open();
  assert.deepEqual(plain(t.sockets[1].sent[0]), { t: "hello", since: 7 });
  assert.deepEqual(kinds, ["websocket", "reconnecting", "websocket"]);
});

test("live link: without a WebSocket it uses plain HTTP, with hello first and the replies handed on", async () => {
  let n = 0;
  const t = together({ noWebSocket: true, answers: (p, o) => {
    if (!/\/live$/.test(p)) return { v: 1, status: "playing", players: [] };
    if (o.body.msgs.length) return { msgs: [{ t: "welcome", inSeq: 0, last: 0 }, { t: "ack", seq: 1 }] };
    n++;
    return { msgs: n === 1 ? [{ t: "start", delay: 4, inMs: 3000, n: 1 }] : [], match: { v: 9, players: [] } };
  } });
  const got = [], kinds = [];
  const link = t.T.liveLink("M3", { message: (m) => got.push(m), match() {}, transport: (k) => kinds.push(k) }); links.push(link);
  link.send({ t: "ready", rtt: 30 });
  await wait(250);
  assert.deepEqual(kinds, ["poll"]);
  const posts = t.calls.api.filter(([p]) => p === "api/matches/M3/live");
  const sends = posts.filter(([, o]) => o.body.msgs.length);
  assert.deepEqual(plain(sends[0][1].body.msgs.map((m) => m.t)), ["hello", "ready"]);
  assert.ok(posts.some(([, o]) => o.body.wait === 10), "a request waits for what comes");
  assert.deepEqual(got.map((m) => m.t).sort(), ["ack", "start", "welcome"]);
});

test("live link: a socket that can't be opened again falls back to HTTP", async () => {
  const t = together();
  const kinds = [];
  const link = t.T.liveLink("M4", { message() {}, transport: (k) => kinds.push(k) }); links.push(link);
  t.sockets[0].drop();
  for (const ms of [500, 1000, 2000, 4000]) { await wait(ms + 60); t.sockets[t.sockets.length - 1].drop(); }
  assert.equal(t.sockets.length, 5, "four more tries");
  assert.equal(kinds[kinds.length - 1], "poll");
});

test("the result headline for live duels: out of step, gave up, time ran out", () => {
  const { T } = together();
  const m = (you, other, endReason) => ({ endReason, players: [{ id: "u1", you: true, name: "Kabir Rao", result: you }, { id: "u2", you: false, name: "Meera Rao", result: other }] });
  assert.equal(T.headline(m(null, null, "out_of_step")), "Out of step — no result");
  assert.match(T.headline(m("won", "lost", "resigned")), /Meera gave up\. You win!/);
  assert.match(T.headline(m("lost", "won", "resigned")), /you gave up/);
  assert.equal(T.headline(m("draw", "draw", "time_limit")), "Play time ran out — a draw");
  assert.equal(T.headline(m(null, null, "timeout")), "Ended — no result");
  assert.equal(T.headline(m("won", "lost", "finished")), "🏆 You won!");
});

test("the bar in a live duel: the other's score from my own game, and waiting / paused / lost", () => {
  const { T } = together();
  const m = { kind: "live", status: "playing", players: [{ id: "u1", you: true, name: "Kabir" }, { id: "u2", you: false, name: "Meera Rao", connected: true }] };
  const text = (info) => plain(T.barContent(m, "websocket", info).map((k) => k.kids.join("")).filter(Boolean));
  assert.deepEqual(text({ state: "playing", scores: [30, 120] }), ["Meera", "120", "playing"]);
  assert.deepEqual(text({ state: "waiting", scores: [30, 120] })[2], "waiting…");
  assert.deepEqual(text({ state: "lost", scores: null })[1], "connection lost?");
  assert.deepEqual(text({ state: "paused", scores: null })[1], "paused");
});
