/* Household Arcade — Code Breaker rules (pure: no DOM, deterministic for a given seed).

   A hidden code of `pegs` symbols, each one of `colours` (numbered 1 …, each with its own colour and shape in
   codebreak.js). Each guess gets feedback: a black peg for every symbol in the right place, a white peg for every
   other symbol that is in the code but somewhere else (each code symbol counted once). Find the code within the rows.
   Modes: Little (3 of 4, no repeats, 8 rows — gentle), Classic (4 of 6, repeats allowed, 10 rows), No repeats
   (4 of 6, 10 rows), Master (5 of 8, repeats, 12 rows).
   The score of a cracked code is 1,000 × (rows left + 1) + (999 − seconds, at least 0): fewer rows rank first, then
   the faster time. An uncracked code scores 0.
   Only + − × ÷ and Math.floor/max/min/imul are used. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var UPS = 60, ROW_POINTS = 1000, TIME_POINTS = 999, STATE_VERSION = 1, MESSAGE_UPDATES = 180;
  var MODES = {
    little: { pegs: 3, colours: 4, repeats: false, rows: 8 },
    classic: { pegs: 4, colours: 6, repeats: true, rows: 10 },
    norepeat: { pegs: 4, colours: 6, repeats: false, rows: 10 },
    master: { pegs: 5, colours: 8, repeats: true, rows: 12 },
  };

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** { black, white } for a guess against the code (both lists of 1 … colours). */
  function feedback(code, guess) {
    var black = 0, cc = {}, gc = {}, i;
    for (i = 0; i < code.length; i++) {
      if (guess[i] === code[i]) black++;
      else { cc[code[i]] = (cc[code[i]] || 0) + 1; gc[guess[i]] = (gc[guess[i]] || 0) + 1; }
    }
    var white = 0;
    for (var k in gc) if (cc[k]) white += Math.min(cc[k], gc[k]);
    return { black: black, white: white };
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic", m = MODES[mode];
    var s = {
      mode: mode, pegs: m.pegs, colours: m.colours, repeats: m.repeats, maxRows: m.rows, rng: (o.seed >>> 0) || 1,
      code: [], rows: [], cur: [], slot: 0, pal: 0, updates: 0, won: false, over: false, score: 0,
      message: "", messageT: 0, lastAt: -100, stats: { placed: 0, cleared: 0, refused: 0, cause: null },
    };
    var pool = [];
    for (var c = 1; c <= m.colours; c++) pool.push(c);
    for (var i = 0; i < m.pegs; i++) {
      if (m.repeats) s.code.push(1 + Math.floor(rand(s) * m.colours));
      else { var j = Math.floor(rand(s) * pool.length); s.code.push(pool[j]); pool.splice(j, 1); }
    }
    for (i = 0; i < m.pegs; i++) s.cur.push(0);
    return s;
  }

  function seconds(s) { return Math.floor(s.updates / UPS); }
  function scoreOf(s) {
    if (!s.won) return 0;
    return ROW_POINTS * (s.maxRows - s.rows.length + 1) + Math.max(0, TIME_POINTS - seconds(s));
  }
  function say(s, text) { s.message = text; s.messageT = MESSAGE_UPDATES; }
  function filled(s) { for (var i = 0; i < s.pegs; i++) if (!s.cur[i]) return false; return true; }

  /** Put colour c in the chosen slot (or the first empty one), then move on to the next empty slot. */
  function place(s, c) {
    if (s.over || !(c >= 1 && c <= s.colours)) return [];
    var at = s.cur[s.slot] ? s.cur.indexOf(0) : s.slot;
    if (at < 0) at = s.slot;
    s.cur[at] = c; s.stats.placed++; s.pal = c - 1;
    var next = -1;
    for (var k = 1; k <= s.pegs; k++) { var q = (at + k) % s.pegs; if (!s.cur[q]) { next = q; break; } }
    s.slot = next >= 0 ? next : at;
    return [{ type: "place", slot: at, colour: c }];
  }
  /** Empty a slot (a tap on it), or with no slot the last filled one (Backspace). */
  function clear(s, slot) {
    if (s.over) return [];
    if (slot === undefined) { slot = -1; for (var i = s.pegs - 1; i >= 0; i--) if (s.cur[i]) { slot = i; break; } }
    if (!(slot >= 0 && slot < s.pegs)) return [];
    var had = s.cur[slot];
    s.cur[slot] = 0; s.slot = slot;
    if (!had) return [{ type: "slot", slot: slot }];
    s.stats.cleared++;
    return [{ type: "clear", slot: slot }];
  }
  function check(s) {
    if (s.over) return [];
    if (!filled(s)) { say(s, "Fill every place first"); s.stats.refused++; return [{ type: "refuse" }]; }
    if (!s.repeats) {
      var seen = {};
      for (var i = 0; i < s.pegs; i++) {
        if (seen[s.cur[i]]) { say(s, "No repeats in this mode"); s.stats.refused++; return [{ type: "refuse" }]; }
        seen[s.cur[i]] = 1;
      }
    }
    var fb = feedback(s.code, s.cur);
    s.rows.push({ guess: s.cur.slice(), black: fb.black, white: fb.white });
    s.lastAt = s.updates;
    var evs = [{ type: "guess", black: fb.black, white: fb.white, row: s.rows.length }];
    s.cur = []; for (var k = 0; k < s.pegs; k++) s.cur.push(0);
    s.slot = 0;
    if (fb.black === s.pegs) {
      s.won = true; s.over = true; s.score = scoreOf(s); s.stats.cause = "won";
      evs.push({ type: "win", score: s.score, rows: s.rows.length });
    } else if (s.rows.length >= s.maxRows) {
      s.over = true; s.stats.cause = "rows";
      evs.push({ type: "lose" });
    }
    return evs;
  }

  function step(s) {
    if (s.over) return [];
    s.updates++;
    if (s.messageT > 0 && --s.messageT === 0) s.message = "";
    return [];
  }

  /** Keys 1 … colours place a symbol, Enter checks, Backspace / Delete clears; a controller or the arrows: ← → pick
      on the palette, fire places it, ↑ checks, ↓ or alt clears. */
  function press(s, action, down) {
    if (!down || s.over) return [];
    switch (action) {
      case "left": s.pal = (s.pal + s.colours - 1) % s.colours; return [{ type: "cursor" }];
      case "right": s.pal = (s.pal + 1) % s.colours; return [{ type: "cursor" }];
      case "fire": return place(s, s.pal + 1);
      case "up": return check(s);
      case "down": case "alt": return clear(s);
    }
    if (action.indexOf("key:") === 0) {
      var k = action.slice(4);
      if (/^[1-9]$/.test(k)) return place(s, +k);
      if (k === "ENTER") return check(s);
      if (k === "BACKSPACE" || k === "DELETE" || k === "0") return clear(s);
    }
    return [];
  }

  function status(s) { return { score: scoreOf(s), level: 1, over: s.over, done: s.won, rows: s.rows.length }; }
  function save(s) { return JSON.parse(JSON.stringify(s)); }
  function restore(data) {
    var m = data && MODES[data.mode];
    var ok = m && data.pegs === m.pegs && data.colours === m.colours && Array.isArray(data.code) && data.code.length === m.pegs &&
      data.code.every(function (c) { return c >= 1 && c <= m.colours && c === Math.floor(c); }) && Array.isArray(data.rows) &&
      data.rows.length < m.rows && Array.isArray(data.cur) && data.cur.length === m.pegs &&
      data.cur.every(function (c) { return c >= 0 && c <= m.colours && c === Math.floor(c); }) && typeof data.updates === "number" &&
      typeof data.rng === "number" && data.stats && typeof data.stats === "object";
    if (ok) data.rows.forEach(function (r) {
      if (!r || !Array.isArray(r.guess) || r.guess.length !== m.pegs) { ok = false; return; }
      var fb = feedback(data.code, r.guess);
      if (fb.black !== r.black || fb.white !== r.white) ok = false;
    });
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.maxRows = m.rows; s.repeats = m.repeats;
    if (!(s.slot >= 0 && s.slot < s.pegs)) s.slot = 0;
    if (!(s.pal >= 0 && s.pal < s.colours)) s.pal = 0;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function result(s) {
    return {
      score: scoreOf(s), level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, rows: s.rows.length, maxRows: s.maxRows, code: s.over ? s.code.slice() : undefined,
        summary: s.won ? ["Cracked in " + s.rows.length + (s.rows.length === 1 ? " row" : " rows") + " of " + s.maxRows, "Time " + clock(seconds(s))] : undefined },
    };
  }

  var CodeBreakLogic = {
    UPS: UPS, ROW_POINTS: ROW_POINTS, TIME_POINTS: TIME_POINTS, STATE_VERSION: STATE_VERSION, MODES: MODES,
    feedback: feedback, create: create, step: step, press: press, place: place, clear: clear, check: check,
    seconds: seconds, scoreOf: scoreOf, status: status, save: save, restore: restore, result: result, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = CodeBreakLogic; else self.CodeBreakLogic = CodeBreakLogic;
})();
