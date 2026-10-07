// Random games of a wave 8 turn-by-turn game (Ludo, Snakes and Ladders, Chess) played by its JavaScript rules, for
// tests/test_wave8.py to replay through the server's Python rules (app/rules/<game>.py): every move with the die
// (as the server would roll it), the legal moves before it (a hash of the sorted list), the move the roll forces (if
// any), the public state after it, and now and then a probe — a move that may or may not be legal (a random one, or
// one by a player whose move it isn't) with whether the JavaScript rules took it. Not a *.test.js: run by Python.
//
//     node tests/js/wave8-fuzz.js <game> <games> [first seed]   → one JSON line per game
"use strict";
const crypto = require("node:crypto");
const path = require("node:path");
const GAMES = path.join(__dirname, "..", "..", "app", "static", "games");
const game = process.argv[2], count = Number(process.argv[3] || 50), seed0 = Number(process.argv[4] || 1);
const L = require(path.join(GAMES, `${game}-logic.js`));
const BK = require(path.join(GAMES, "boardkit.js"));
const DICE = game === "ludo" || game === "snakes";

function rng(seed) { const s = { rng: seed >>> 0 || 1 }; return () => BK.rand(s); }
function hashOf(moves) {
  const list = moves.map(BK.canonical).sort();
  return crypto.createHash("md5").update(list.join("\n")).digest("hex");
}
function int(r, lo, hi) { return lo + Math.floor(r() * (hi - lo + 1)); }
const SQ = (r) => "abcdefgh"[int(r, 0, 7)] + int(r, 1, 8);
function probe(s, r, seat) {
  if (game === "ludo") {
    const k = int(r, 0, 5);
    return k === 0 ? { pass: true } : k === 1 ? { token: int(r, -1, 4) } : k === 2 ? { token: 1.5 } : k === 3 ? { pass: 1 } : { token: int(r, 0, 3) };
  }
  if (game === "snakes") return r() < 0.7 ? { go: true } : r() < 0.5 ? { go: 1 } : { stay: true };
  // chess: a random pair of squares, sometimes a legal move's squares without (or with a wrong) promotion
  const legal = L.R.moves(s, seat);
  if (legal.length && r() < 0.4) {
    const m = Object.assign({}, legal[int(r, 0, legal.length - 1)]);
    if (m.promo) { if (r() < 0.5) delete m.promo; else m.promo = ["k", "p", "x", "q"][int(r, 0, 3)]; }
    else if (r() < 0.3) m.promo = "q";
    return m;
  }
  return r() < 0.1 ? { from: SQ(r) } : { from: SQ(r), to: SQ(r) };
}
function accepts(s, seat, m, dice) {
  const c = BK.copy(s);
  if (DICE) c._roll = { seat: seat + 1, value: dice };
  try { L.R.apply(c, seat, m); return true; } catch (e) { return false; }
}

for (let g = 0; g < count; g++) {
  const seed = seed0 + g, r = rng(seed * 7919 + 13);
  const options = {};
  for (const k of Object.keys(L.R.OPTIONS || {})) options[k] = L.R.OPTIONS[k].choices[int(r, 0, L.R.OPTIONS[k].choices.length - 1)];
  const players = DICE ? int(r, 2, 4) : 2;
  const s = L.create({ mode: "phones", seed, options, seat: 0, players });
  const steps = [];
  let guard = 0;
  const limit = game === "chess" ? 400 : 3000;
  while (!s.over && guard++ < limit) {
    const seat = L.toMove(s)[0];
    const dice = DICE ? int(r, 1, 6) : null;
    if (r() < 0.12) {                                       // a probe: maybe illegal, maybe the wrong player
      const who = r() < 0.3 ? (seat + 1) % s.n : seat, m = probe(s, r, who);
      steps.push({ probe: true, seat: who, dice, move: m, ok: accepts(s, who, m, dice) });
    }
    if (DICE) s._roll = { seat: seat + 1, value: dice };
    const legal = DICE ? L.R.legal(s, seat, dice) : L.R.moves(s, seat);
    const forced = DICE ? L.R.forced(s, seat) : null;
    const m = forced && r() < 0.5 ? forced : legal[int(r, 0, legal.length - 1)];
    L.R.apply(s, seat, m);
    steps.push({ seat, dice, move: m, legal: hashOf(legal), n: legal.length, forced: forced ? BK.canonical(forced) : null, digest: BK.copy(L.R.digest(s)) });
  }
  process.stdout.write(JSON.stringify({ game, seed, options, players, steps, over: s.over, winner: s.winner, reason: s.reason || null }) + "\n");
}
