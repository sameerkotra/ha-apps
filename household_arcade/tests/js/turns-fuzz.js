// Random games of a wave 7 game played by its JavaScript rules, for tests/test_wave7.py to replay through the
// server's Python rules (app/rules/<game>.py): every move, the legal moves before it (a hash of the sorted list),
// the public state after it, and now and then a probe — a move that may or may not be legal (a random one, or one
// by the player whose move it isn't) with whether the JavaScript rules took it. Not a *.test.js: run by Python.
//
//     node tests/js/turns-fuzz.js <game> <games> [first seed]   → one JSON line per game
"use strict";
const crypto = require("node:crypto");
const path = require("node:path");
const GAMES = path.join(__dirname, "..", "..", "app", "static", "games");
const game = process.argv[2], count = Number(process.argv[3] || 50), seed0 = Number(process.argv[4] || 1);
const L = require(path.join(GAMES, `${game}-logic.js`));
const BK = require(path.join(GAMES, "boardkit.js"));

function rng(seed) { const s = { rng: seed >>> 0 || 1 }; return () => BK.rand(s); }
function hashOf(moves) {
  const list = moves.map(BK.canonical).sort();
  return crypto.createHash("md5").update(list.join("\n")).digest("hex");
}
function int(r, lo, hi) { return lo + Math.floor(r() * (hi - lo + 1)); }
/** A move that may or may not be legal. */
function probe(s, r) {
  switch (game) {
    case "fourrow": return { col: int(r, -1, 7) };
    case "tictactoe": return { cell: int(r, -1, 9) };
    case "reversi": return r() < 0.1 ? { cell: 27.5 } : { cell: int(r, -1, 64) };
    case "dots": return { edge: int(r, -1, s.edges.length) };
    case "checkers": {
      const from = int(r, 0, 63), n = int(r, 1, 3), path_ = [from];
      for (let i = 0; i < n; i++) path_.push(path_[path_.length - 1] + [-18, -14, -9, -7, 7, 9, 14, 18][int(r, 0, 7)]);
      return { path: path_ };
    }
    case "seabattle": {
      if (s.phase === "place") {
        const p = L.randomFleet(s);
        const k = int(r, 0, 3);
        if (k === 0) p[0][3] = 1 - p[0][3];                 // may stick out or overlap now
        else if (k === 1) p.pop();                         // a ship short
        else if (k === 2) p[0] = [p[0][0], p[0][1], 6, 0]; // too long
        return { place: p };
      }
      return { shot: int(r, -1, s.size * s.size) };
    }
  }
  return { nothing: 1 };
}
function accepts(s, seat, m) {
  const c = BK.copy(s);
  try { L.R.apply(c, seat, m); return true; } catch (e) { return false; }
}

for (let g = 0; g < count; g++) {
  const seed = seed0 + g, r = rng(seed * 7919 + 13);
  const options = {};
  for (const k of Object.keys(L.R.OPTIONS || {})) options[k] = L.R.OPTIONS[k].choices[int(r, 0, L.R.OPTIONS[k].choices.length - 1)];
  const s = L.create({ mode: "phones", seed, options, seat: 0 });
  const steps = [];
  let guard = 0;
  while (!s.over && guard++ < 600) {
    const tm = L.toMove(s);
    const seat = tm[int(r, 0, tm.length - 1)];
    if (r() < 0.15) {                                       // a probe: maybe illegal, maybe the wrong player
      const who = r() < 0.3 ? 1 - seat : seat, m = probe(s, r);
      steps.push({ probe: true, seat: who, move: m, ok: accepts(s, who, m) });
    }
    const legal = L.R.moves(s, seat);
    let m;
    if (game === "seabattle" && s.phase === "place") {
      const fr = { rng: (seed * 31 + guard) | 0, size: s.size, fleet: s.fleet };
      m = { place: L.randomFleet(fr) };
    } else m = legal[int(r, 0, legal.length - 1)];
    L.R.apply(s, seat, m);
    steps.push({ seat, move: m, legal: hashOf(legal), n: legal.length, digest: BK.copy(L.R.digest(s)) });
  }
  process.stdout.write(JSON.stringify({ game, seed, options, steps, over: s.over, winner: s.winner }) + "\n");
}
