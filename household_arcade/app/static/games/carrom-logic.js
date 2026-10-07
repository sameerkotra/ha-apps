/* Household Arcade — Carrom rules and physics (pure: no DOM, deterministic for a seed and the shots).

   Everything that moves is worked out in whole numbers (fixed point), so two phones playing the same shot get exactly
   the same result, bit for bit: positions in 1/256 px, speeds in 1/256 px a sub-step (4 sub-steps an update), angles
   in tenths of a degree from a table of sines made once with + − × ÷ (no Math.sin, cos, atan2, pow or random — the
   tests run the rules with those made to throw), friction, cushions and collisions with integer division (exact,
   truncated towards zero, the same in every engine). A checksum (FNV-1a) of the state follows every shot.

   The board: a 216 px square with a pocket in each corner; 9 white coins, 9 black and the red queen start in the
   middle (the queen in the centre, a ring of six around it, a ring of twelve around that, colours alternating). Each
   player shoots from their own baseline: the striker is placed anywhere along it (a value 0–1000 from the player's
   left to right; a place overlapping a coin is moved along the line to the nearest free place), aimed at an angle in
   tenths of a degree in the player's own view (100–1700: 900 straight ahead, never backwards) with a power 1–100.

   The rules (a common family rule set, documented in spec/GAMES.md):
   - Two players: the first (Player 1 against the computer and on one screen; from two phones the seat from the seed)
     plays White and breaks. Doubles: four on one screen, partners opposite (seats 1 and 3 White, 2 and 4 Black),
     turns going round to the right.
   - Pocketing one of your own coins (or the queen) without a foul gives another shot; otherwise the turn passes.
     Opponents' coins you pocket stay pocketed — for them.
   - The queen must be covered: pocket one of your own coins in the same shot or your next one; if you don't, the
     queen goes back to the middle. A covered queen is yours.
   - Foul — the striker goes into a pocket: the turn ends, everything pocketed in that shot goes back to the middle,
     and one of your coins already pocketed (if any) comes back as a penalty; a queen waiting for its cover goes back.
   - Your last coin can't be pocketed while the queen is still on the board (or waiting for a cover by the other
     side): it comes back to the middle and the turn ends. The same for pocketing the other side's last coin.
   - The board is won by the side that pockets all nine of its coins (the queen covered by someone): it scores the
     other side's coins still on the board, + 3 if it covered the queen.
   - A board still going after 300 shots ends there: the side with more points (its coins pocketed, + 3 for a covered
     queen) wins by the difference; the same points is a draw. (It keeps a board between two players who can't reach
     the last coins from ending never.)
   - A shot that hasn't stopped after 60 seconds is stopped where it is (it never happens on a real shot).

   The computer: cuts of each coin it may pocket into each pocket from seven places on its baseline, the same off each
   cushion, and a few shots from the seed; each is played on a copy and the best kept (Easy 6 tried and aimed a
   little off, Medium 16, Hard 46); of shots that pocket nothing, the one leaving its coins nearer the pockets. */
(function () {
  "use strict";
  var isNode = typeof module === "object" && module.exports;

  var U = 256, SUB = 4;
  var BOARD = 216 * U, RC = Math.round(5.4 * U), RS = Math.round(7 * U), RP = Math.round(8.6 * U), POCKET_AT = Math.round(8 * U);
  var BASE_OFF = 27 * U, BASE_HALF = 66 * U;          // baselines 27 px in from each edge, ±66 px from the middle
  var M_COIN = 5, M_STRIKER = 14;
  var E_WALL = 172, E_HIT = 454;                         // restitution × 256: cushions 0.67; coins 1 + 0.77
  var FRICTION = 1, DRAG = 150;                          // a sub-step: −1 (1/256 px) and −1/150 of the speed
  var SPEED_MIN = 50, SPEED_PER_POWER = 8;               // power 1–100 → 58–850 (1/256 px a sub-step)
  var MAX_UPDATES = 3600;
  var MAX_SHOTS = 300;                                   // a board that goes on this long ends on the points so far
  var LEVEL_BASE = { 1: 100, 2: 250, 3: 500 }, PHONES_BASE = 500, BONUS_POINT = 20, QUEEN_POINTS = 3;
  var TIMEOUT_SHOT = [500, 900, 12];
  var MODES = [
    { id: "easy", label: "Computer · Easy (gentle)", level: 1, n: 2 },
    { id: "medium", label: "Computer · Medium", level: 2, n: 2 },
    { id: "hard", label: "Computer · Hard", level: 3, n: 2 },
    { id: "two", label: "Two players (one screen)", level: 0, n: 2 },
    { id: "doubles", label: "Doubles: four, two teams (one screen)", level: 0, n: 4 },
    { id: "phones", label: "Two phones (live, taking turns)", level: 0, n: 2 },
  ];
  var STATE_VERSION = 1;

  // ---------- exact integer helpers ----------
  /** a ÷ b truncated towards zero, exact for whole numbers (b > 0). */
  function tdiv(a, b) {
    var q = a / b;
    q = q < 0 ? Math.ceil(q) : Math.floor(q);
    var r = a - q * b;
    if (a >= 0) { if (r < 0) q--; else if (r >= b) q++; } else { if (r > 0) q++; else if (-r >= b) q--; }
    return q;
  }
  function isqrt(n) {
    if (n <= 0) return 0;
    var s = Math.floor(Math.sqrt(n));
    while (s * s > n) s--;
    while ((s + 1) * (s + 1) <= n) s++;
    return s;
  }
  /** Sines of 0–180° in tenths of a degree, × 16384, from the series (only + − × ÷), rounded: the same everywhere. */
  var SIN = (function () {
    var t = new Int32Array(1801), PI = 3.141592653589793;
    for (var i = 0; i <= 1800; i++) {
      var x = i * PI / 1800;
      if (x > PI / 2) x = PI - x;
      var x2 = x * x, term = x, sum = x;
      for (var k = 1; k < 12; k++) { term = -term * x2 / ((2 * k) * (2 * k + 1)); sum += term; }
      t[i] = Math.round(sum * 16384);
    }
    return t;
  })();
  function sinT(a) { return SIN[a]; }                     // a: 0–1800
  function cosT(a) { return a <= 900 ? SIN[900 - a] : -SIN[a - 900]; }

  function hashStr(str) {
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  function rand(s) { // mulberry32, for the computer's choices (never during a live game)
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function modeInfo(id) { for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i]; return null; }

  // ---------- the sides of the board ----------
  /** Side 0 bottom, 1 right, 2 top, 3 left: the baseline's middle, the player's right (u) and forward (v) as unit
      vectors in board coordinates (y grows downwards). */
  var SIDES = [
    { cx: BOARD / 2, cy: BOARD - BASE_OFF, ux: 1, uy: 0, vx: 0, vy: -1 },
    { cx: BOARD - BASE_OFF, cy: BOARD / 2, ux: 0, uy: -1, vx: -1, vy: 0 },
    { cx: BOARD / 2, cy: BASE_OFF, ux: -1, uy: 0, vx: 0, vy: 1 },
    { cx: BASE_OFF, cy: BOARD / 2, ux: 0, uy: 1, vx: 1, vy: 0 },
  ];
  function sideOf(s, seat) { return s.n === 2 ? seat * 2 : seat; }
  function teamOf(s, seat) { return s.n === 2 ? seat : seat % 2; }
  /** The board point at u along a side's baseline (u in 1/256 px from its middle). */
  function basePoint(side, u) { var S = SIDES[side]; return [S.cx + S.ux * u, S.cy + S.uy * u]; }
  function posToU(pos) { return -BASE_HALF + tdiv(2 * BASE_HALF * pos, 1000); }

  // ---------- the start ----------
  function newPieces() {
    // 0 striker, 1–9 white, 10–18 black, 19 queen
    var P = [], i, c = BOARD / 2, gap = RC * 2 + 40;
    P.push({ k: "s", x: c, y: BOARD - BASE_OFF, vx: 0, vy: 0, r: RS, m: M_STRIKER, on: false });
    // inner ring of 6 (60° apart) and outer ring of 12 (30° apart), from the sine table (the same everywhere)
    var spots = [];
    for (i = 0; i < 6; i++) spots.push(ring(c, gap, i * 600 + 300));
    for (i = 0; i < 12; i++) spots.push(ring(c, gap * 2 + 24, i * 300));
    // colours alternate around each ring, white starting at the top of the inner ring
    var whites = 0, blacks = 0;
    for (i = 0; i < 18; i++) {
      var white = i < 6 ? i % 2 === 0 : i % 2 === 1;
      if (white) whites++; else blacks++;
      P.push({ k: white ? "w" : "b", x: spots[i][0], y: spots[i][1], vx: 0, vy: 0, r: RC, m: M_COIN, on: true });
    }
    // keep white 1–9 and black 10–18
    var w = P.slice(1).filter(function (p) { return p.k === "w"; }), b = P.slice(1).filter(function (p) { return p.k === "b"; });
    P = [P[0]].concat(w, b);
    P.push({ k: "q", x: c, y: c, vx: 0, vy: 0, r: RC, m: M_COIN, on: true });
    void whites; void blacks;
    return P;
  }
  /** A point at distance d from the middle c, at angle a (tenths of a degree, 0–3599, 0 = straight up). */
  function ring(c, d, a) {
    a = a % 3600;
    var sx = a <= 1800 ? sinT(a) : -sinT(a - 1800), cy = a <= 1800 ? cosT(a) : -cosT(a - 1800);
    return [c + tdiv(d * sx, 16384), c - tdiv(d * cy, 16384)];
  }

  function create(o) {
    o = o || {};
    var info = modeInfo(o.mode) || MODES[0], seed = (Number(o.seed) >>> 0) || 1, phones = info.id === "phones";
    var n = info.n, first = phones ? seed % 2 : 0;
    var s = {
      game: "carrom", mode: info.id, level: info.level, seed: seed, rng: seed, n: n, first: first, turn: first,
      // the team that plays White: the first to shoot's
      white: teamOf({ n: n }, first), pieces: newPieces(), moving: false, shots: 0, ticks: 0,
      pocketed: [[], []],               // by team: coins of that team's colour pocketed ("w"/"b" indices), in order
      queen: "on",                      // on | pending (pocketed, waiting for its cover) | team0 | team1
      queenBy: -1, pendingTeam: -1, shotBy: -1, inShot: [], foul: false, last: null,
      over: false, winner: null, points: 0, updates: 0, me: phones ? (o.seat === 1 ? 1 : 0) : 0,
      cpu: info.level > 0 ? 1 : -1, wait: 0, ai: null, events: [],
    };
    placeStriker(s, s.turn, 500);
    return s;
  }
  function colourOfTeam(s, team) { return team === s.white ? "w" : "b"; }

  // ---------- placing the striker ----------
  function overlapsAt(s, x, y) {
    for (var i = 1; i < s.pieces.length; i++) {
      var p = s.pieces[i];
      if (!p.on) continue;
      var dx = p.x - x, dy = p.y - y, R = p.r + RS;
      if (dx * dx + dy * dy < R * R) return true;
    }
    return false;
  }
  /** Put the striker on `seat`'s baseline at `pos` (0–1000), moved along the line to the nearest free place. */
  function placeStriker(s, seat, pos) {
    var side = sideOf(s, seat), u0 = posToU(Math.max(0, Math.min(1000, pos | 0))), st = s.pieces[0];
    var step = U, best = null;
    for (var k = 0; k <= 2 * BASE_HALF / step && best === null; k++) {
      for (var sg = 0; sg < 2 && best === null; sg++) {
        if (k === 0 && sg === 1) continue;
        var u = u0 + (sg ? -k : k) * step;
        if (u < -BASE_HALF || u > BASE_HALF) continue;
        var pt = basePoint(side, u);
        if (!overlapsAt(s, pt[0], pt[1])) best = pt;
      }
    }
    if (best === null) best = basePoint(side, u0);
    st.x = best[0]; st.y = best[1]; st.vx = 0; st.vy = 0; st.on = true;
    return best;
  }
  /** The striker's place (0–1000) and the board point for a seat's chosen place, as the rules would put it. */
  function strikerPreview(s, seat, pos) {
    var st = s.pieces[0], keep = [st.x, st.y, st.on];
    var pt = placeStriker(s, seat, pos);
    st.x = keep[0]; st.y = keep[1]; st.on = keep[2];
    return pt;
  }

  // ---------- a shot ----------
  function turn(s) { return s.over || s.moving ? -1 : s.turn; }
  function validShot(v) {
    return Array.isArray(v) && v.length === 3 && v.every(function (x) { return typeof x === "number" && Math.floor(x) === x; }) &&
      v[0] >= 0 && v[0] <= 1000 && v[1] >= 100 && v[1] <= 1700 && v[2] >= 1 && v[2] <= 100;
  }
  /** Play a shot for `seat`: [place 0–1000, angle 100–1700, power 1–100]. Returns events (nothing if it isn't theirs). */
  function shoot(s, seat, v) {
    if (s.over || s.moving || seat !== s.turn || !validShot(v)) return [];
    var side = sideOf(s, seat), S = SIDES[side];
    placeStriker(s, seat, v[0]);
    var speed = SPEED_MIN + v[2] * SPEED_PER_POWER, c = cosT(v[1]), sn = sinT(v[1]);
    var du = tdiv(speed * c, 16384), dv = tdiv(speed * sn, 16384);
    var st = s.pieces[0];
    st.vx = du * S.ux + dv * S.vx; st.vy = du * S.uy + dv * S.vy;
    s.moving = true; s.shots++; s.shotBy = seat; s.inShot = []; s.foul = false; s.ticks = 0;
    s.last = { seat: seat, value: v.slice() };
    return [{ type: "shot", seat: seat, power: v[2] }];
  }

  function moveAll(s, evs) {
    var P = s.pieces, i, j, p, q;
    for (var sub = 0; sub < SUB; sub++) {
      for (i = 0; i < P.length; i++) {
        p = P[i];
        if (!p.on || (p.vx === 0 && p.vy === 0)) continue;
        p.x += p.vx; p.y += p.vy;
        // cushions
        if (p.x < p.r) { p.x = 2 * p.r - p.x; p.vx = -tdiv(p.vx * E_WALL, 256); if (i === 0 || p.vx > 60) evs.push({ type: "cushion" }); }
        else if (p.x > BOARD - p.r) { p.x = 2 * (BOARD - p.r) - p.x; p.vx = -tdiv(p.vx * E_WALL, 256); evs.push({ type: "cushion" }); }
        if (p.y < p.r) { p.y = 2 * p.r - p.y; p.vy = -tdiv(p.vy * E_WALL, 256); evs.push({ type: "cushion" }); }
        else if (p.y > BOARD - p.r) { p.y = 2 * (BOARD - p.r) - p.y; p.vy = -tdiv(p.vy * E_WALL, 256); evs.push({ type: "cushion" }); }
      }
      // collisions, every pair in a fixed order
      for (i = 0; i < P.length; i++) {
        p = P[i];
        if (!p.on) continue;
        for (j = i + 1; j < P.length; j++) {
          q = P[j];
          if (!q.on) continue;
          var dx = q.x - p.x, dy = q.y - p.y, R = p.r + q.r;
          if (dx > R || dx < -R || dy > R || dy < -R) continue;
          var d2 = dx * dx + dy * dy;
          if (d2 >= R * R) continue;
          if (d2 === 0) { dx = 1; d2 = 1; }
          var dot = (p.vx - q.vx) * dx + (p.vy - q.vy) * dy, msum = p.m + q.m;
          if (dot > 0) {
            var k = tdiv(dot * E_HIT, d2);                      // (1 + e) × (Δv · d) / |d|², in 1/256ths
            p.vx -= tdiv(k * dx * q.m, 256 * msum); p.vy -= tdiv(k * dy * q.m, 256 * msum);
            q.vx += tdiv(k * dx * p.m, 256 * msum); q.vy += tdiv(k * dy * p.m, 256 * msum);
            if (dot > 200000) evs.push({ type: "clack" });
          }
          // pull them apart (what overlaps, shared by mass)
          var d = isqrt(d2), over = R - d;
          if (over > 0 && d > 0) {
            var ax = tdiv(dx * over, d), ay = tdiv(dy * over, d);
            p.x -= tdiv(ax * q.m, msum); p.y -= tdiv(ay * q.m, msum);
            q.x += tdiv(ax * p.m, msum); q.y += tdiv(ay * p.m, msum);
          }
        }
      }
      // pockets
      for (i = 0; i < P.length; i++) {
        p = P[i];
        if (!p.on) continue;
        var px = p.x < BOARD / 2 ? POCKET_AT : BOARD - POCKET_AT, py = p.y < BOARD / 2 ? POCKET_AT : BOARD - POCKET_AT;
        var ex = p.x - px, ey = p.y - py;
        if (ex * ex + ey * ey < RP * RP) {
          p.on = false; p.vx = 0; p.vy = 0; p.x = px; p.y = py;
          s.inShot.push(i);
          evs.push({ type: i === 0 ? "foul" : p.k === "q" ? "queen" : "pocket", piece: i });
        }
      }
      // friction
      for (i = 0; i < P.length; i++) {
        p = P[i];
        if (!p.on || (p.vx === 0 && p.vy === 0)) continue;
        var sp = isqrt(p.vx * p.vx + p.vy * p.vy);
        var ns = sp - FRICTION - tdiv(sp, DRAG);
        if (ns <= 0) { p.vx = 0; p.vy = 0; }
        else { p.vx = tdiv(p.vx * ns, sp); p.vy = tdiv(p.vy * ns, sp); }
      }
    }
  }
  function still(s) {
    for (var i = 0; i < s.pieces.length; i++) { var p = s.pieces[i]; if (p.on && (p.vx !== 0 || p.vy !== 0)) return false; }
    return true;
  }

  /** Put a coin back near the middle: the free place nearest to it (a spiral of whole-number points). */
  function respot(s, i) {
    var p = s.pieces[i], c = BOARD / 2;
    p.vx = 0; p.vy = 0;
    for (var rr = 0; rr < 40; rr++) {
      var d = rr * (RC + 40), n = rr === 0 ? 1 : 6 * rr;
      for (var k = 0; k < n; k++) {
        var pt = rr === 0 ? [c, c] : ring(c, d, Math.floor(k * 3600 / n));
        var free = true;
        for (var j = 0; j < s.pieces.length && free; j++) {
          var q = s.pieces[j];
          if (j === i || !q.on) continue;
          var dx = q.x - pt[0], dy = q.y - pt[1], R = q.r + p.r + 20;
          if (dx * dx + dy * dy < R * R) free = false;
        }
        if (free) { p.x = pt[0]; p.y = pt[1]; p.on = true; return; }
      }
    }
    p.x = c; p.y = c; p.on = true;
  }
  function onBoard(s, colour) {
    var n = 0;
    for (var i = 1; i < s.pieces.length; i++) if (s.pieces[i].on && s.pieces[i].k === colour) n++;
    return n;
  }
  function removeFromPocketed(s, i) {
    for (var t = 0; t < 2; t++) { var at = s.pocketed[t].indexOf(i); if (at >= 0) s.pocketed[t].splice(at, 1); }
  }

  /** The shot has stopped: the rules (pockets, the queen and its cover, fouls, the last coin, the board's end). */
  function settle(s, evs) {
    var seat = s.shotBy, team = teamOf(s, seat), mine = colourOfTeam(s, team), theirs = mine === "w" ? "b" : "w";
    var got = s.inShot.slice(), striker = got.indexOf(0) >= 0, own = 0, opp = 0, queenNow = false, i, keepTurn = false;
    s.moving = false;
    if (striker) {
      // a foul: everything pocketed in this shot comes back, and one of yours as a penalty
      for (i = 0; i < got.length; i++) if (got[i] !== 0) respot(s, got[i]);
      var mineList = s.pocketed[team];
      if (mineList.length) { var back = mineList.pop(); respot(s, back); }
      if (s.queen === "pending" && s.pendingTeam === team) { respot(s, 19); s.queen = "on"; s.pendingTeam = -1; }
      s.foul = true;
      evs.push({ type: "fouled", seat: seat });
    } else {
      for (i = 0; i < got.length; i++) {
        var k = s.pieces[got[i]].k;
        if (k === "q") queenNow = true;
        else if (k === mine) { own++; s.pocketed[team].push(got[i]); }
        else { opp++; s.pocketed[1 - team].push(got[i]); }
      }
      if (queenNow) {
        if (own > 0) { s.queen = "team" + team; s.queenBy = team; evs.push({ type: "covered", seat: seat }); }
        else { s.queen = "pending"; s.pendingTeam = team; }
        keepTurn = true;
      } else if (s.queen === "pending" && s.pendingTeam === team) {
        if (own > 0) { s.queen = "team" + team; s.queenBy = team; s.pendingTeam = -1; evs.push({ type: "covered", seat: seat }); }
        else { respot(s, 19); s.queen = "on"; s.pendingTeam = -1; evs.push({ type: "queenBack" }); }
      }
      if (own > 0) keepTurn = true;
      // the last coin can't go before the queen is covered: it comes back and the turn ends
      var queenOpen = s.queenBy < 0;
      for (var c = 0; c < 2; c++) {
        var col = c ? theirs : mine, tm = c ? 1 - team : team;
        if (queenOpen && onBoard(s, col) === 0 && s.pocketed[tm].length) {
          var last = s.pocketed[tm].pop();
          respot(s, last);
          keepTurn = false;
          evs.push({ type: "lastCoin", seat: seat });
        }
      }
    }
    // the striker goes back to whoever shoots next
    s.pieces[0].on = false;
    // the end of the board: a side with all its coins pocketed (the queen covered)
    for (var t = 0; t < 2; t++) {
      if (s.queenBy >= 0 && onBoard(s, colourOfTeam(s, t)) === 0) {
        s.over = true; s.winnerTeam = t;
        s.points = onBoard(s, colourOfTeam(s, 1 - t)) + (s.queenBy === t ? QUEEN_POINTS : 0);
        s.winner = s.n === 2 ? t : t;          // two players: the seat; doubles: the team (seats t and t + 2)
        evs.push({ type: "board", team: t });
        break;
      }
    }
    if (!s.over && s.shots >= MAX_SHOTS) {
      // a board that hasn't ended after MAX_SHOTS shots ends here: the side with more points (coins pocketed, the
      // covered queen 3) wins by the difference; the same points: a draw
      var pts = [0, 1].map(function (tm) { return s.pocketed[tm].length + (s.queenBy === tm ? QUEEN_POINTS : 0); });
      s.over = true;
      s.winnerTeam = pts[0] === pts[1] ? -1 : pts[0] > pts[1] ? 0 : 1;
      s.winner = s.winnerTeam;
      s.points = Math.abs(pts[0] - pts[1]);
      s.longBoard = true;
      evs.push({ type: "board", team: s.winnerTeam });
    }
    if (!s.over) {
      if (!keepTurn) s.turn = (s.turn + 1) % s.n;
      placeStriker(s, s.turn, 500);
    }
    evs.push({ type: "settled", shot: s.shots, keep: keepTurn });
  }

  /** One update: the moving shot (4 sub-steps), then the rules when it stops; the computer aims on its turn. */
  function step(s) {
    var evs = [];
    s.updates++;
    if (s.moving) {
      moveAll(s, evs);
      s.ticks++;
      if (still(s) || s.ticks >= MAX_UPDATES) {
        if (!still(s)) for (var i = 0; i < s.pieces.length; i++) { s.pieces[i].vx = 0; s.pieces[i].vy = 0; }
        settle(s, evs);
      }
      return evs;
    }
    if (!s.over && s.cpu >= 0 && s.turn === s.cpu) evs = evs.concat(aiStep(s));
    return evs;
  }

  // ---------- the computer ----------
  /** The angle (tenths of a degree, 0–1800) in a side's own view that points along (du, dv) — found from the table. */
  function angleTo(du, dv) {
    var best = 900, bestDot = -Infinity, lo = 0, hi = 1800, stepA = 50;
    for (var pass = 0; pass < 3; pass++) {
      for (var a = lo; a <= hi; a += stepA) {
        var d = du * cosT(a) + dv * sinT(a);
        if (d > bestDot) { bestDot = d; best = a; }
      }
      lo = Math.max(0, best - stepA); hi = Math.min(1800, best + stepA); stepA = pass === 0 ? 5 : 1;
    }
    return best;
  }
  /** Candidate shots for `seat`: a cut of each coin it may pocket into each pocket, from several places. */
  function candidates(s, seat, count) {
    var side = sideOf(s, seat), S = SIDES[side], team = teamOf(s, seat), mine = colourOfTeam(s, team), out = [];
    var targets = [];
    for (var i = 1; i < s.pieces.length; i++) {
      var p = s.pieces[i];
      if (!p.on) continue;
      if (p.k === mine || (p.k === "q" && s.queen === "on")) targets.push(i);
    }
    var pockets = [[POCKET_AT, POCKET_AT], [BOARD - POCKET_AT, POCKET_AT], [POCKET_AT, BOARD - POCKET_AT], [BOARD - POCKET_AT, BOARD - POCKET_AT]];
    var places = [100, 250, 400, 500, 600, 750, 900];
    var banks = [];                                               // off a cushion: aim at the hit point's mirror image
    for (var t = 0; t < targets.length; t++) {
      var c = s.pieces[targets[t]];
      for (var k = 0; k < 4; k++) {
        var dx = pockets[k][0] - c.x, dy = pockets[k][1] - c.y, dl = isqrt(dx * dx + dy * dy);
        if (dl === 0) continue;
        var gx = c.x - tdiv(dx * (RC + RS), dl), gy = c.y - tdiv(dy * (RC + RS), dl);     // where the striker must hit
        for (var pl = 0; pl < places.length; pl++) {
          var bp = strikerPreview(s, seat, places[pl]);
          var ex = gx - bp[0], ey = gy - bp[1];
          var du = ex * S.ux + ey * S.uy, dv = ex * S.vx + ey * S.vy;
          var dist = isqrt(ex * ex + ey * ey) + dl;
          var cosCut = tdiv((ex * dx + ey * dy) * 100, (isqrt(ex * ex + ey * ey) || 1) * dl);
          // a cut needs the striker to arrive in line: very thin cuts are left out
          if (dv > 0 && cosCut >= 20) {
            var ang = angleTo(du, dv);
            if (ang >= 100 && ang <= 1700) {
              var power = Math.max(18, Math.min(100, 14 + tdiv(dist * 52, BOARD) + tdiv((100 - cosCut) * 30, 100)));
              out.push({ v: [places[pl], ang, power], pre: cosCut * 4 - tdiv(dist, U) });
            }
          }
          // the same hit off each cushion (for coins behind the line or in the corners)
          for (var w = 0; w < 4; w++) {
            var mx = w === 0 ? -gx + 2 * RS : w === 1 ? 2 * (BOARD - RS) - gx : gx;
            var my = w === 2 ? -gy + 2 * RS : w === 3 ? 2 * (BOARD - RS) - gy : gy;
            var fx = mx - bp[0], fy = my - bp[1], bu = fx * S.ux + fy * S.uy, bv = fx * S.vx + fy * S.vy;
            if (bv <= 0) continue;
            var ba = angleTo(bu, bv);
            if (ba < 100 || ba > 1700) continue;
            var bd = isqrt(fx * fx + fy * fy) + dl;
            banks.push({ v: [places[pl], ba, Math.max(25, Math.min(100, 20 + tdiv(bd * 60, BOARD)))], pre: -tdiv(bd, U) });
          }
        }
      }
    }
    out.sort(function (a, b) { return b.pre - a.pre; });
    banks.sort(function (a, b) { return b.pre - a.pre; });
    out = out.slice(0, count).concat(banks.slice(0, Math.max(2, count >> 1)));
    // a few shots from the seed too (off the cushions, a break), so coins out of a straight line still get played —
    // different every shot (the seed mixed with the shot number), so two sides can't repeat a shot that does nothing
    out.push({ v: [500, 900, 70], pre: -1e9 });
    var g = { rng: (s.rng ^ Math.imul(s.shots + 1, 0x9E3779B1)) >>> 0 };
    for (var r = 0; r < Math.max(3, count >> 2); r++) {
      out.push({ v: [Math.floor(rand(g) * 1001), 100 + Math.floor(rand(g) * 1601), 30 + Math.floor(rand(g) * 71)], pre: -1e9 });
    }
    return out;
  }
  /** How far (px, summed) `colour`'s coins on the board are from their nearest pockets. */
  function pocketDistance(s, colour) {
    var sum = 0;
    for (var i = 1; i < s.pieces.length; i++) {
      var p = s.pieces[i];
      if (!p.on || p.k !== colour) continue;
      var dx = Math.min(p.x - POCKET_AT, BOARD - POCKET_AT - p.x), dy = Math.min(p.y - POCKET_AT, BOARD - POCKET_AT - p.y);
      sum += tdiv(isqrt(dx * dx + dy * dy), U);
    }
    return sum;
  }
  /** Play a shot on a copy and say how good it was for `seat`'s side. */
  function trial(s, seat, v) {
    var c = JSON.parse(JSON.stringify(s));
    c.cpu = -1; c.ai = null;
    var team = teamOf(c, seat), mine = colourOfTeam(c, team), theirs = mine === "w" ? "b" : "w";
    var before = [onBoard(c, mine), onBoard(c, theirs)], queenBefore = c.queen, near = pocketDistance(c, mine);
    shoot(c, seat, v);
    var guard = 0;
    while (c.moving && guard++ < MAX_UPDATES) step(c);
    if (c.over) return c.winnerTeam === team ? 10000 : -10000;
    var sc = 0;
    if (c.foul) sc -= 400;
    sc += (before[0] - onBoard(c, mine)) * 100;
    sc -= (before[1] - onBoard(c, theirs)) * 60;
    if (c.queen === "team" + team && queenBefore !== c.queen) sc += 150;
    if (c.queen === "pending") sc += 60;
    if (c.turn === seat) sc += 30;
    // when nothing goes in, a shot that leaves your coins nearer the pockets (for the next shot) is the better one
    if (before[0] === onBoard(c, mine)) sc += Math.max(-25, Math.min(25, tdiv((near - pocketDistance(c, mine)) * 2, 3)));
    return sc;
  }
  var AI = { 1: { count: 6, noise: 22, powerNoise: 8 }, 2: { count: 16, noise: 5, powerNoise: 3 }, 3: { count: 46, noise: 0, powerNoise: 0 } };
  /** The computer thinks over several updates (a few trial shots each), then shoots after a short pause. */
  function aiStep(s) {
    var lv = AI[s.level] || AI[1];
    if (!s.ai || s.ai.shot !== s.shots) {
      s.ai = { shot: s.shots, list: candidates(s, s.turn, lv.count), at: 0, best: null, bestScore: -Infinity, wait: 30 };
    }
    var A = s.ai;
    for (var k = 0; k < 2 && A.at < A.list.length; k++, A.at++) {
      var v = A.list[A.at].v, sc = trial(s, s.turn, v);
      if (sc > A.bestScore) { A.bestScore = sc; A.best = v.slice(); }
    }
    if (A.at < A.list.length) return [];
    if (--A.wait > 0) return [];
    var shot = A.best || [500, 900, 60];
    if (lv.noise) {
      shot = [shot[0], Math.max(100, Math.min(1700, shot[1] + Math.round((rand(s) * 2 - 1) * lv.noise))),
        Math.max(5, Math.min(100, shot[2] + Math.round((rand(s) * 2 - 1) * lv.powerNoise)))];
    }
    s.ai = null;
    return shoot(s, s.turn, shot);
  }

  // ---------- results ----------
  function teamName(team) { return team === 0 ? "first" : "second"; }
  function playerScore(s, team) {
    if (!s.over || s.winnerTeam !== team || s.mode === "two" || s.mode === "doubles") return 0;
    var base = s.mode === "phones" ? PHONES_BASE : LEVEL_BASE[s.level] || 0;
    return base + Math.max(0, Math.min(base, BONUS_POINT * s.points));
  }
  function score(s, seat) {
    if (seat === undefined) seat = s.mode === "phones" ? s.me : 0;
    return playerScore(s, teamOf(s, seat));
  }
  function result(s, player) {
    var me = player === undefined ? (s.mode === "phones" ? s.me : 0) : player, team = teamOf(s, me);
    var won = s.over && s.winnerTeam === team && s.mode !== "two" && s.mode !== "doubles";
    var draw = s.over && s.winnerTeam < 0;
    var lost = s.over && !won && !draw && s.mode !== "two" && s.mode !== "doubles";
    var note;
    if (s.mode === "two" || s.mode === "doubles") note = "Players on one screen — not saved";
    else if (lost) note = "Not a win — not saved";
    var lines = [];
    if (s.over && s.winnerTeam < 0) lines.push("A draw: " + MAX_SHOTS + " shots and the same points");
    else if (s.over) lines.push((s.mode === "doubles" ? "Team " + (s.winnerTeam + 1) : "The " + teamName(s.winnerTeam) + " player") + " won the board: " + s.points + " points" + (s.longBoard ? " (after " + MAX_SHOTS + " shots)" : ""));
    if (!s.over && s.mode !== "two" && s.mode !== "doubles") note = "Board not finished — not saved";
    lines.push("Pocketed — White " + s.pocketed[s.white].length + " · Black " + s.pocketed[1 - s.white].length + (s.queenBy >= 0 ? " · the queen covered" : ""));
    if (draw && !note) note = "A draw — not saved";
    return { score: score(s, me), level: 1, stats: { won: won, lost: lost, draw: draw, winner: s.over ? s.winnerTeam : -1, mode: s.mode,
      shots: s.shots, points: s.points, summary: lines, notSaved: note } };
  }
  /** The end both phones of a live game must agree on (SPEC §13.4). */
  function report(s) {
    return { scores: [playerScore(s, 0), playerScore(s, 1)], levels: [1, 1], winner: s.over && s.winnerTeam >= 0 ? s.winnerTeam + 1 : 0 };
  }
  /** FNV-1a of everything that decides what happens next. */
  function checksum(s) {
    var parts = [s.turn, s.shots, s.moving ? 1 : 0, s.queen, s.queenBy, s.pendingTeam, s.over ? 1 : 0, s.winner === null ? -1 : s.winner,
      s.pocketed[0].join("."), s.pocketed[1].join(".")];
    for (var i = 0; i < s.pieces.length; i++) { var p = s.pieces[i]; parts.push(p.on ? 1 : 0, p.x, p.y, p.vx, p.vy); }
    return hashStr(parts.join(","));
  }
  /** Live: the input a phone plays ("shot" = [place, angle, power]) for player 0 or 1. */
  function press(s, action, value, player) {
    if (action === "shot") return shoot(s, player, value);
    return [];
  }

  function save(s) { var c = JSON.parse(JSON.stringify(s)); c.ai = null; return c; }
  function restore(data) {
    var ok = data && typeof data === "object" && data.game === "carrom" && modeInfo(data.mode) && data.mode !== "phones" &&
      Array.isArray(data.pieces) && data.pieces.length === 20 && Array.isArray(data.pocketed) && typeof data.turn === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.ai = null;
    return s;
  }

  var L = {
    UPS: 60, SUB: SUB, U: U, BOARD: BOARD, RC: RC, RS: RS, RP: RP, POCKET_AT: POCKET_AT, BASE_OFF: BASE_OFF, BASE_HALF: BASE_HALF,
    SIDES: SIDES, MODES: MODES, STATE_VERSION: STATE_VERSION, TIMEOUT_SHOT: TIMEOUT_SHOT, LEVEL_BASE: LEVEL_BASE, PHONES_BASE: PHONES_BASE,
    BONUS_POINT: BONUS_POINT, SIN: SIN, MAX_UPDATES: MAX_UPDATES, MAX_SHOTS: MAX_SHOTS,
    create: create, step: step, shoot: shoot, press: press, turn: turn, validShot: validShot, sideOf: sideOf, teamOf: teamOf,
    placeStriker: placeStriker, strikerPreview: strikerPreview, basePoint: basePoint, posToU: posToU, colourOfTeam: colourOfTeam,
    onBoard: onBoard, score: score, result: result, report: report, checksum: checksum, save: save, restore: restore,
    timeoutShot: function () { return TIMEOUT_SHOT.slice(); }, candidates: candidates, trial: trial, angleTo: angleTo,
    tdiv: tdiv, isqrt: isqrt, sinT: sinT, cosT: cosT, still: still, modeInfo: modeInfo,
  };
  void removeFromPocketed;
  if (isNode) module.exports = L; else self.CarromLogic = L;
})();
