/* Household Arcade — Ludo rules (pure: no DOM, deterministic for a seed). The same rules as the server's
   app/rules/ludo.py, move for move:

   - A track of 52 squares around a cross-shaped board, a yard in each corner, a home column of 5 squares for each
     colour and the home in the middle. 4 tokens each (2 players: red and yellow, opposite; 3: red, green, yellow;
     4: all four). The die is the server's from two or more phones, the seed's on one phone.
   - A 6 brings a token out of the yard onto its start square. Tokens move clockwise by the roll, then up their own
     home column; home needs the exact roll.
   - Landing on other colours' tokens sends them all back to their yards, except on the 8 safe squares (the starts and
     the stars eight squares after each); your own tokens may share any square.
   - A 6 gives another roll; a third 6 in a row loses the turn. A roll no token can use is passed.
   - The first with all four tokens home wins.

   A token's place: −1 yard, 0–50 along the track from its own start, 51–55 its home column, 56 home. A move is
   { token: 0–3 } or { pass: true }.

   The computer: capture first, then get home, then out of the yard, into the home column or onto a safe square,
   else the token furthest along (ties from the seed). */
(function () {
  "use strict";
  var isNode = typeof module === "object" && module.exports;
  var BK = isNode ? require("./boardkit.js") : self.BoardKit;
  var DK = isNode ? require("./dicekit.js") : self.DiceKit;

  var TRACK = 52, LAST_TRACK = 50, FINISH = 56, SAFE = [0, 8, 13, 21, 26, 34, 39, 47];
  var COLOURS = { 2: [0, 2], 3: [0, 1, 2], 4: [0, 1, 2, 3] };
  var COLOUR_NAMES = ["Red", "Green", "Yellow", "Blue"];
  var BONUS_PHONES = 25, BONUS_CPU = 10, STEP_UPDATES = 6;

  // The board: 15 × 15 cells, [row, col]. The track starts at red's start square and goes clockwise.
  var PATH = (function () {
    var p = [], i;
    for (i = 1; i <= 5; i++) p.push([6, i]);
    for (i = 5; i >= 0; i--) p.push([i, 6]);
    p.push([0, 7]);
    for (i = 0; i <= 5; i++) p.push([i, 8]);
    for (i = 9; i <= 14; i++) p.push([6, i]);
    p.push([7, 14]);
    for (i = 14; i >= 9; i--) p.push([8, i]);
    for (i = 9; i <= 14; i++) p.push([i, 8]);
    p.push([14, 7]);
    for (i = 14; i >= 9; i--) p.push([i, 6]);
    for (i = 5; i >= 0; i--) p.push([8, i]);
    p.push([7, 0]);
    p.push([6, 0]);
    return p;
  })();
  /** A colour's home column, squares 51–55 (from the track inwards). */
  function homeCell(col, k) {
    switch (col) {
      case 0: return [7, 1 + k];
      case 1: return [1 + k, 7];
      case 2: return [7, 13 - k];
      default: return [13 - k, 7];
    }
  }
  /** The yards' corners [row, col] of their 6 × 6 squares. */
  var YARD = [[0, 0], [0, 9], [9, 9], [9, 0]];

  function square(s, seat, p) { return (13 * s.cols[seat] + p) % TRACK; }
  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }

  function legalFor(s, seat, r) {
    if (s.sixes === 2 && r === 6) return [{ pass: true }];
    var out = [], toks = s.tokens[seat];
    for (var i = 0; i < 4; i++) {
      var p = toks[i];
      if (p === -1) { if (r === 6) out.push({ token: i }); }
      else if (p < FINISH && p + r <= FINISH) out.push({ token: i });
    }
    return out.length ? out : [{ pass: true }];
  }
  function rollOf(s, seat) {
    var r = s._roll;
    if (!r || r.seat !== seat + 1 || !isWhole(r.value, 1, 6)) throw new Error("Roll the dice first.");
    return r.value;
  }

  var R = {
    id: "ludo", STATE_VERSION: 1, PLAYERS: 4, OPTIONS: {},
    init: function (s) {
      s.cols = COLOURS[s.n].slice();
      s.tokens = [];
      for (var i = 0; i < s.n; i++) s.tokens.push([-1, -1, -1, -1]);
      s.sixes = 0; s.anim = null;
    },
    legal: function (s, seat, r) { return s.over || seat !== s.turn ? [] : legalFor(s, seat, r); },
    forced: function (s, seat) {
      var r;
      try { r = rollOf(s, seat); } catch (e) { return null; }
      var ms = legalFor(s, seat, r);
      if (ms.length === 1) return ms[0];
      var first = s.tokens[seat][ms[0].token];
      for (var i = 1; i < ms.length; i++) if (s.tokens[seat][ms[i].token] !== first) return null;
      return ms[0];
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      var r = rollOf(s, seat);
      var keys = move && typeof move === "object" ? Object.keys(move) : [];
      var isTok = keys.length === 1 && keys[0] === "token" && isWhole(move.token, 0, 3);
      var isPass = keys.length === 1 && keys[0] === "pass" && move.pass === true;
      if (!isTok && !isPass) throw new Error("A move is {token: 0–3} (or a pass when no token can move).");
      var want = BK.canonical(move), ms = legalFor(s, seat, r), ok = false, i;
      for (i = 0; i < ms.length; i++) if (BK.canonical(ms[i]) === want) ok = true;
      if (!ok) throw new Error(isTok ? "That token can't move with this roll." : "A token can move — pick one.");
      delete s._roll;
      var n = s.n, evs = [];
      if (isPass) {
        var third = s.sixes === 2 && r === 6;
        if (r === 6 && s.sixes < 2) s.sixes++;
        else { s.sixes = 0; s.turn = (seat + 1) % n; }
        s.anim = { kind: "pass", seat: seat, at: s.updates };
        s.note = third ? "A third 6 — the turn is lost" : "No move with a " + r + (r === 6 ? " — roll again" : "");
        s.noteAt = s.updates;
        evs.push({ type: "pass", seat: seat });
        return evs;
      }
      var t = move.token, p = s.tokens[seat][t], np = p === -1 ? 0 : p + r, caps = [];
      s.tokens[seat][t] = np;
      if (np <= LAST_TRACK) {
        var sq = square(s, seat, np);
        if (SAFE.indexOf(sq) < 0) {
          for (var o = 0; o < n; o++) {
            if (o === seat) continue;
            for (var j = 0; j < 4; j++) {
              var q = s.tokens[o][j];
              if (q >= 0 && q <= LAST_TRACK && square(s, o, q) === sq) { caps.push([o, j, q]); s.tokens[o][j] = -1; }
            }
          }
        }
      }
      s.anim = { kind: "move", seat: seat, token: t, from: p, to: np, caps: caps, at: s.updates };
      s.note = "";
      evs.push({ type: p === -1 ? "enter" : np === FINISH ? "home" : "step", seat: seat });
      if (caps.length) evs.push({ type: "capture", seat: seat, n: caps.length });
      var home = true;
      for (i = 0; i < 4; i++) if (s.tokens[seat][i] !== FINISH) home = false;
      if (home) { s.over = true; s.winner = seat; }
      else if (r === 6) s.sixes++;
      else { s.sixes = 0; s.turn = (seat + 1) % n; }
      return evs;
    },
    animLength: function (s) {
      var a = s.anim;
      if (!a) return 0;
      if (a.kind === "pass") return 24;
      var steps = a.from === -1 ? 1 : a.to - a.from;
      return steps * STEP_UPDATES + (a.caps.length ? 18 : 0) + 6;
    },
    ai: function (s, seat) {
      var r = rollOf(s, seat), ms = legalFor(s, seat, r);
      if (ms.length === 1) return ms[0];
      var best = -Infinity, top = [];
      for (var i = 0; i < ms.length; i++) {
        var v = value(s, seat, ms[i].token, r);
        if (v > best) { best = v; top = [ms[i]]; } else if (v === best) top.push(ms[i]);
      }
      return BK.pick(s, top);
    },
    digest: function (s) { return { turn: s.turn, tokens: s.tokens, sixes: s.sixes, over: s.over, winner: s.winner }; },
    bonus: function (s, seat) {
      var left = 0;
      for (var o = 0; o < s.n; o++) if (o !== seat) for (var j = 0; j < 4; j++) if (s.tokens[o][j] !== FINISH) left++;
      return left * (s.mode === "phones" ? BONUS_PHONES : BONUS_CPU);
    },
    cpuBase: function (s) { return 100 * (s.n - 1); },
    seatName: function (s, seat) { return COLOUR_NAMES[s.cols[seat]]; },
    summary: function (s, names) {
      var parts = [];
      for (var i = 0; i < s.n; i++) parts.push(names[i] + " " + home(s, i));
      return [parts.join(" · ") + " tokens home"];
    },
    check: function (d) { return Array.isArray(d.tokens) && d.tokens.length === d.n && Array.isArray(d.cols) && typeof d.sixes === "number"; },
    // the keyboard: the arrows ring each token that can move in turn; Space (fire) moves it
    cursorMove: function (s, dir, seat) {
      var ms = movable(s, seat);
      if (!ms.length) return;
      var at = ms.indexOf(s.cursor), step = dir === "left" || dir === "up" ? -1 : 1;
      s.cursor = ms[at < 0 ? 0 : (at + step + ms.length) % ms.length];
    },
    activate: function (s, seat) {
      var ms = movable(s, seat);
      if (!ms.length) return null;
      if (ms.indexOf(s.cursor) < 0) { s.cursor = ms[0]; return { events: [{ type: "cursor" }] }; }
      return { move: { token: s.cursor } };
    },
    tap: function (s, target, seat) {
      if (target && target.seat === seat && typeof target.token === "number") {
        if (movable(s, seat).indexOf(target.token) < 0) { s.msg = "nope"; s.msgAt = s.updates; return { events: [{ type: "nope" }] }; }
        return { move: { token: target.token } };
      }
      return null;
    },
  };

  function home(s, seat) { var n = 0; for (var j = 0; j < 4; j++) if (s.tokens[seat][j] === FINISH) n++; return n; }
  function movable(s, seat) {
    if (!s.rolled) return [];
    var ms = legalFor(s, seat, s.rolled), out = [];
    for (var i = 0; i < ms.length; i++) if (typeof ms[i].token === "number") out.push(ms[i].token);
    return out;
  }
  function value(s, seat, t, r) {
    var p = s.tokens[seat][t], np = p === -1 ? 0 : p + r, v = np / 100;
    if (np === FINISH) v += 80;
    else if (p === -1) v += 60;
    if (np <= LAST_TRACK) {
      var sq = square(s, seat, np);
      if (SAFE.indexOf(sq) >= 0) v += 20;
      else {
        for (var o = 0; o < s.n; o++) {
          if (o === seat) continue;
          for (var j = 0; j < 4; j++) { var q = s.tokens[o][j]; if (q >= 0 && q <= LAST_TRACK && square(s, o, q) === sq) { v += 100; o = s.n; break; } }
        }
      }
    } else if (p <= LAST_TRACK) v += 30;
    return v;
  }

  var L = DK.game(R);
  L.PATH = PATH; L.homeCell = homeCell; L.YARD = YARD; L.SAFE = SAFE; L.FINISH = FINISH; L.LAST_TRACK = LAST_TRACK;
  L.COLOUR_NAMES = COLOUR_NAMES; L.square = square; L.legalFor = legalFor; L.movable = movable; L.STEP_UPDATES = STEP_UPDATES;
  L.home = home;
  if (isNode) module.exports = L; else self.LudoLogic = L;
})();
