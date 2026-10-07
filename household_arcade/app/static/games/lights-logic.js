/* Household Arcade — Lights Out rules (pure: no DOM, deterministic for a given seed).

   An n × n board of lights. Pressing a light switches it and its four neighbours (up, down, left, right) on or off;
   switching every light off solves the board. Boards are made from the seed by pressing random lights on a dark
   board, so every board can be solved; the fewest presses that solve it are worked out exactly (Gaussian
   elimination over on/off, then the smallest of the few equivalent answers), which is also what the optional hint
   uses. Modes by size: Little 3 × 3 (gentle), 5 × 5, 7 × 7, and Climb — five boards from 3 × 3 up to 7 × 7, the
   last one wins.
   The score of a solved game is TOP − presses − HINT_COST × hints (fewest presses ranks first); unsolved scores 0.
   In a race the first to solve wins, fewer presses breaks a tie (games.py: {"rule": "fastest", "tiebreak": "score"});
   the Hints option is off in races, so both play without them.
   Only + − × ÷ and Math.floor/abs/min/max/imul are used. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, HINT_COST = 5, CLEAR_T = 75, STATE_VERSION = 1;
  var MODES = {
    little: { sizes: [3] },
    classic: { sizes: [5] },
    big: { sizes: [7] },
    climb: { sizes: [3, 4, 5, 6, 7] },
  };
  // presses used to make a board (a range), and the fewest presses its answer must need
  var MAKE = { 3: [3, 5, 3], 4: [4, 7, 4], 5: [6, 10, 6], 6: [8, 14, 8], 7: [10, 18, 10] };

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function neighbours(n, i) {
    var r = Math.floor(i / n), c = i % n, out = [i];
    if (r > 0) out.push(i - n);
    if (r < n - 1) out.push(i + n);
    if (c > 0) out.push(i - 1);
    if (c < n - 1) out.push(i + 1);
    return out;
  }
  function toggle(lights, n, i) { var nb = neighbours(n, i); for (var k = 0; k < nb.length; k++) lights[nb[k]] ^= 1; }

  /** The fewest presses that switch every light off (a list of cells), or null if the board can't be solved. */
  function solve(lights, n) {
    var N = n * n, rows = [], i, j, k;
    for (i = 0; i < N; i++) {
      var row = new Uint8Array(N + 1), nb = neighbours(n, i);
      for (k = 0; k < nb.length; k++) row[nb[k]] = 1;          // pressing nb[k] switches light i
      row[N] = lights[i] ? 1 : 0;
      rows.push(row);
    }
    var pivotCol = [], r = 0;
    for (var c = 0; c < N && r < N; c++) {
      var p = -1;
      for (i = r; i < N; i++) if (rows[i][c]) { p = i; break; }
      if (p < 0) continue;
      var tmp = rows[p]; rows[p] = rows[r]; rows[r] = tmp;
      for (i = 0; i < N; i++) {
        if (i !== r && rows[i][c]) for (j = c; j <= N; j++) rows[i][j] ^= rows[r][j];
      }
      pivotCol.push(c); r++;
    }
    for (i = r; i < N; i++) if (rows[i][N]) return null;        // 0 = 1: no answer
    var isPivot = new Uint8Array(N);
    for (i = 0; i < pivotCol.length; i++) isPivot[pivotCol[i]] = 1;
    var free = [];
    for (c = 0; c < N; c++) if (!isPivot[c]) free.push(c);
    // every answer: the free presses chosen, the pivot presses follow
    var best = null, bestN = N + 1;
    var combos = 1 << Math.min(free.length, 12);
    for (var m = 0; m < combos; m++) {
      var x = new Uint8Array(N);
      for (k = 0; k < free.length; k++) x[free[k]] = (m >> k) & 1;
      for (i = 0; i < pivotCol.length; i++) {
        var v = rows[i][N];
        for (k = 0; k < free.length; k++) if (rows[i][free[k]] && x[free[k]]) v ^= 1;
        x[pivotCol[i]] = v;
      }
      var cnt = 0;
      for (k = 0; k < N; k++) cnt += x[k];
      if (cnt < bestN) { bestN = cnt; best = x; }
    }
    var out = [];
    for (k = 0; k < N; k++) if (best[k]) out.push(k);
    return out;
  }

  function makeBoard(s, n) {
    var spec = MAKE[n], best = null;
    for (var tries = 0; tries < 60; tries++) {
      var lights = [], N = n * n, i;
      for (i = 0; i < N; i++) lights.push(0);
      var k = spec[0] + Math.floor(rand(s) * (spec[1] - spec[0] + 1)), cells = [];
      for (i = 0; i < N; i++) cells.push(i);
      for (i = N - 1; i > 0; i--) { var j = Math.floor(rand(s) * (i + 1)), t = cells[i]; cells[i] = cells[j]; cells[j] = t; }
      for (i = 0; i < k; i++) toggle(lights, n, cells[i]);
      var sol = solve(lights, n), need = sol ? sol.length : 0;
      if (!best || need > best.need) best = { lights: lights, need: need };
      if (need >= spec[2]) break;
    }
    return best;
  }

  function startBoard(s) {
    s.n = s.sizes[s.level - 1];
    var b = makeBoard(s, s.n);
    s.lights = b.lights; s.fewest = b.need; s.boardMoves = 0;
    s.cursor = Math.floor(s.n * s.n / 2); s.hint = -1; s.phase = "play"; s.t = 0;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, sizes: MODES[mode].sizes.slice(), level: 1, n: 3, lights: null, fewest: 0,
      moves: 0, boardMoves: 0, hints: 0, hintsOn: o.hints === "on", hint: -1, cursor: 0, phase: "play", t: 0,
      updates: 0, won: false, over: false, score: 0, last: -1, lastAt: -100, noHintAt: -1000,
      stats: { boards: 0, fewestTotal: 0, cause: null },
    };
    if (mode === "climb") s.level = startAt(o.startLevel, s.sizes.length);
    startBoard(s);
    return s;
  }

  function score(s) { return s.won ? Math.max(MIN_SCORE, TOP - s.moves - HINT_COST * s.hints) : 0; }
  function lit(s) { var n = 0; for (var i = 0; i < s.lights.length; i++) n += s.lights[i]; return n; }

  /** Press the light at cell i. */
  function pressCell(s, i) {
    var evs = [];
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.lights.length)) return evs;
    toggle(s.lights, s.n, i);
    s.moves++; s.boardMoves++; s.cursor = i; s.last = i; s.lastAt = s.updates;
    if (s.hint >= 0) s.hint = -1;
    evs.push({ type: "press", cell: i, on: s.lights[i] });
    if (lit(s) === 0) {
      s.stats.boards++; s.stats.fewestTotal += s.fewest;
      evs.push({ type: "clear", level: s.level, moves: s.boardMoves, fewest: s.fewest });
      if (s.level >= s.sizes.length) {
        s.won = true; s.over = true; s.score = score(s); s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
      } else { s.phase = "clear"; s.t = CLEAR_T; }
    }
    return evs;
  }
  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    if (s.hint >= 0) return [];
    var sol = solve(s.lights, s.n);
    if (!sol || !sol.length) return [];
    var best = sol[0], bd = 1e9, cr = Math.floor(s.cursor / s.n), cc = s.cursor % s.n;
    for (var k = 0; k < sol.length; k++) {
      var d = Math.abs(Math.floor(sol[k] / s.n) - cr) + Math.abs(sol[k] % s.n - cc);
      if (d < bd) { bd = d; best = sol[k]; }
    }
    s.hint = best; s.hints++;
    return [{ type: "hint", cell: best }];
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      startBoard(s);
      evs.push({ type: "level", level: s.level, size: s.n });
    }
    return evs;
  }

  /** The shell's actions: arrows move the cursor, fire presses it, alt asks for a hint (when hints are on). */
  function press(s, action, down) {
    if (!down || s.over) return [];
    var n = s.n, r = Math.floor(s.cursor / n), c = s.cursor % n;
    switch (action) {
      case "up": s.cursor = ((r + n - 1) % n) * n + c; return [{ type: "cursor" }];
      case "down": s.cursor = ((r + 1) % n) * n + c; return [{ type: "cursor" }];
      case "left": s.cursor = r * n + (c + n - 1) % n; return [{ type: "cursor" }];
      case "right": s.cursor = r * n + (c + 1) % n; return [{ type: "cursor" }];
      case "fire": return pressCell(s, s.cursor);
      case "alt": case "hint": return hint(s);
    }
    return [];
  }

  function save(s) { return JSON.parse(JSON.stringify(s)); }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.sizes) && Array.isArray(data.lights) &&
      typeof data.level === "number" && data.level >= 1 && data.level <= data.sizes.length && MAKE[data.n] &&
      data.sizes[data.level - 1] === data.n && data.lights.length === data.n * data.n &&
      data.lights.every(function (v) { return v === 0 || v === 1; }) && typeof data.moves === "number" &&
      typeof data.updates === "number" && typeof data.rng === "number" && data.stats && typeof data.stats === "object" &&
      solve(data.lights, data.n) !== null;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    if (!(s.hint >= 0 && s.hint < s.lights.length)) s.hint = -1;
    if (!(s.cursor >= 0 && s.cursor < s.lights.length)) s.cursor = 0;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = null;
    if (s.won) {
      lines = ["Solved in " + s.moves + (s.moves === 1 ? " press" : " presses") + " (fewest possible " + s.stats.fewestTotal + ")"];
      if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints") + " (+" + HINT_COST * s.hints + ")");
      lines.push("Time " + clock(seconds(s)));
    }
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, moves: s.moves, hints: s.hints, boards: s.stats.boards,
        fewest: s.stats.fewestTotal, summary: lines || undefined },
    };
  }

  var LightsLogic = {
    UPS: UPS, TOP: TOP, MIN_SCORE: MIN_SCORE, HINT_COST: HINT_COST, CLEAR_T: CLEAR_T, STATE_VERSION: STATE_VERSION, MODES: MODES,
    MAKE: MAKE, create: create, step: step, press: press, pressCell: pressCell, hint: hint, solve: solve, toggle: toggle,
    neighbours: neighbours, lit: lit, score: score, save: save, restore: restore, result: result, seconds: seconds, clock: clock,
    rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = LightsLogic; else self.LightsLogic = LightsLogic;
})();
