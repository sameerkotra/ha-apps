/* Household Arcade — Picture Logic rules (nonograms; pure: no DOM, deterministic for a given seed).

   A square grid hides a picture. The numbers beside each row and above each column are the runs of filled squares in
   that line, in order (3 1 = three filled, a gap of at least one, then one). Filling the right squares shows the
   picture; crosses are the player's own notes for squares that stay empty.
   Puzzles are made from the seed: a random pattern (smoothed into blobs on the bigger grids, often mirrored so it
   looks like a picture; on 15 × 15 no run longer than 9, so every number is one digit), then a line solver — the same reasoning a person uses, one row or column at a time — works
   it out from the numbers alone. If the solver gets stuck somewhere, a few squares are given at the start (shown and
   fixed) until it doesn't, so every puzzle has exactly one answer and can be solved without guessing.
   Mistakes are shown at once (+10 s each; the square is put right) or only when the grid has as many filled squares
   as the answer. Hints put one square right that the numbers decide (+30 s each, 3 a puzzle). The score of a solved
   puzzle is TOP − the counted seconds (time + penalties), at least MIN_SCORE; unsolved scores 0.
   Only + − × ÷ and Math.floor/abs/min/max/imul are used. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, HINT_PENALTY = 30, MISTAKE_PENALTY = 10, HINTS = 3, STATE_VERSION = 1;
  var HISTORY_MAX = 300, MESSAGE_UPDATES = 240;
  var UNKNOWN = 0, FILL = 1, CROSS = 2;
  var MODES = {
    five: { n: 5, smooth: 0, density: 0.6 },          // gentle
    eight: { n: 8, smooth: 1, density: 0.55 },
    ten: { n: 10, smooth: 1, density: 0.55 },
    fifteen: { n: 15, smooth: 2, density: 0.5, maxRun: 9 },     // single-digit numbers: they fit the narrow columns
  };
  var LABELS = { five: "5 × 5", eight: "8 × 8", ten: "10 × 10", fifteen: "15 × 15" };

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  // ---------- clues and the line solver ----------
  /** The runs of filled squares in a line of 0/1 (or FILL / other) values. */
  function runs(line, filled) {
    var out = [], run = 0;
    for (var i = 0; i < line.length; i++) {
      if (line[i] === filled) run++;
      else if (run) { out.push(run); run = 0; }
    }
    if (run) out.push(run);
    return out;
  }
  function lineCells(n, kind, k) { var out = []; for (var i = 0; i < n; i++) out.push(kind === "r" ? k * n + i : i * n + k); return out; }

  /** One line: what its numbers decide. `line` holds UNKNOWN / FILL / CROSS. Returns the line with every square the
      numbers force filled in, or null when the line can't match its numbers at all. */
  function solveLine(clue, line) {
    var n = line.length, k = clue.length, K = k + 1, i, j, t;
    var pe = new Int16Array(n + 1);
    for (i = 0; i < n; i++) pe[i + 1] = pe[i] + (line[i] === CROSS ? 1 : 0);
    var f = new Uint8Array((n + 2) * K);              // f[i][j]: squares i… can hold the runs j…
    f[n * K + k] = 1;
    for (i = n - 1; i >= 0; i--) {
      for (j = k; j >= 0; j--) {
        var ok = 0;
        if (line[i] !== FILL && f[(i + 1) * K + j]) ok = 1;
        if (!ok && j < k) {
          var L = clue[j], e = i + L;
          if (e <= n && pe[e] - pe[i] === 0) {
            if (e === n) ok = f[n * K + j + 1];
            else if (line[e] !== FILL && f[(e + 1) * K + j + 1]) ok = 1;
          }
        }
        f[i * K + j] = ok;
      }
    }
    if (!f[0]) return null;
    var reach = new Uint8Array((n + 2) * K), canF = new Uint8Array(n), canE = new Uint8Array(n);
    reach[0] = 1;
    for (i = 0; i < n; i++) {
      for (j = 0; j <= k; j++) {
        if (!reach[i * K + j]) continue;
        if (line[i] !== FILL && f[(i + 1) * K + j]) { canE[i] = 1; reach[(i + 1) * K + j] = 1; }
        if (j < k) {
          var L2 = clue[j], e2 = i + L2;
          if (e2 <= n && pe[e2] - pe[i] === 0) {
            if (e2 === n) {
              if (f[n * K + j + 1]) { for (t = i; t < e2; t++) canF[t] = 1; reach[n * K + j + 1] = 1; }
            } else if (line[e2] !== FILL && f[(e2 + 1) * K + j + 1]) {
              for (t = i; t < e2; t++) canF[t] = 1;
              canE[e2] = 1; reach[(e2 + 1) * K + j + 1] = 1;
            }
          }
        }
      }
    }
    var out = line.slice();
    for (i = 0; i < n; i++) {
      if (out[i] !== UNKNOWN) continue;
      if (canF[i] && !canE[i]) out[i] = FILL;
      else if (canE[i] && !canF[i]) out[i] = CROSS;
    }
    return out;
  }

  /** Line by line until nothing more follows. `cells` (UNKNOWN / FILL / CROSS) is changed in place.
      Returns { ok, unknown, first }: ok false on a contradiction; first = the first square it decided. */
  function solveGrid(rows, cols, cells, n, onePass) {
    var dirty = [], i, first = -1;
    for (i = 0; i < n; i++) { dirty.push(["r", i]); dirty.push(["c", i]); }
    var queued = {};
    dirty.forEach(function (d) { queued[d[0] + d[1]] = 1; });
    while (dirty.length) {
      var d = dirty.shift(), idx = lineCells(n, d[0], d[1]);
      queued[d[0] + d[1]] = 0;
      var line = idx.map(function (c) { return cells[c]; });
      var got = solveLine(d[0] === "r" ? rows[d[1]] : cols[d[1]], line);
      if (!got) return { ok: false, unknown: -1, first: first };
      for (var p = 0; p < n; p++) {
        if (line[p] === UNKNOWN && got[p] !== UNKNOWN) {
          cells[idx[p]] = got[p];
          if (first < 0) { first = idx[p]; if (onePass) return { ok: true, unknown: -1, first: first }; }
          var other = d[0] === "r" ? ["c", p] : ["r", p];
          if (!queued[other[0] + other[1]]) { queued[other[0] + other[1]] = 1; dirty.push(other); }
        }
      }
    }
    var unknown = 0;
    for (i = 0; i < n * n; i++) if (cells[i] === UNKNOWN) unknown++;
    return { ok: true, unknown: unknown, first: first };
  }

  function cluesOf(sol, n) {
    var rows = [], cols = [], r, c;
    for (r = 0; r < n; r++) { var line = []; for (c = 0; c < n; c++) line.push(sol[r * n + c]); rows.push(runs(line, 1)); }
    for (c = 0; c < n; c++) { var col = []; for (r = 0; r < n; r++) col.push(sol[r * n + c]); cols.push(runs(col, 1)); }
    return { rows: rows, cols: cols };
  }

  function pattern(n, m, st) {
    var g = [], i, r, c;
    for (i = 0; i < n * n; i++) g.push(rand(st) < m.density ? 1 : 0);
    for (var pass = 0; pass < m.smooth; pass++) {
      var h = g.slice();
      for (r = 0; r < n; r++) for (c = 0; c < n; c++) {
        var cnt = 0, tot = 0;
        for (var dr = -1; dr <= 1; dr++) for (var dc = -1; dc <= 1; dc++) {
          var rr = r + dr, cc = c + dc;
          if (rr < 0 || cc < 0 || rr >= n || cc >= n) continue;
          tot++; cnt += g[rr * n + cc];
        }
        if (cnt * 2 > tot) h[r * n + c] = 1; else if (cnt * 2 < tot) h[r * n + c] = 0;
      }
      g = h;
    }
    if (rand(st) < 0.5) for (r = 0; r < n; r++) for (c = 0; c < n / 2; c++) g[r * n + (n - 1 - c)] = g[r * n + c];
    return g;
  }

  /** A puzzle for a mode and seed: { sol: [0/1], given: [cell indices], rows, cols }. Always one answer. */
  function generate(mode, seed) {
    var m = MODES[mode] || MODES.ten, n = m.n, st = { rng: (seed >>> 0) || 1 }, best = null, i;
    for (var tries = 0; tries < 150; tries++) {
      var sol = pattern(n, m, st), filled = 0;
      for (i = 0; i < n * n; i++) filled += sol[i];
      if (filled < n * n * 0.3 || filled > n * n * 0.75) continue;
      var empties = 0, longest = 0, cl = cluesOf(sol, n);
      cl.rows.concat(cl.cols).forEach(function (x) { if (!x.length) empties++; x.forEach(function (v) { longest = Math.max(longest, v); }); });
      if (empties > 1 || (m.maxRun && longest > m.maxRun)) continue;
      var cells = [];
      for (i = 0; i < n * n; i++) cells.push(UNKNOWN);
      var res = solveGrid(cl.rows, cl.cols, cells, n);
      if (!best || res.unknown < best.unknown) best = { sol: sol, clues: cl, unknown: res.unknown };
      if (res.unknown === 0) break;
    }
    if (!best) {                                     // never happens in practice: a plain checker-free fallback
      var fb = [];
      for (i = 0; i < n * n; i++) fb.push((Math.floor(i / n) + i % n) % 3 ? 1 : 0);
      best = { sol: fb, clues: cluesOf(fb, n), unknown: n * n };
    }
    var given = [];
    for (var guard = 0; guard < n * n; guard++) {
      var start = [];
      for (i = 0; i < n * n; i++) start.push(UNKNOWN);
      given.forEach(function (gi) { start[gi] = best.sol[gi] ? FILL : CROSS; });
      var r2 = solveGrid(best.clues.rows, best.clues.cols, start, n);
      if (r2.unknown === 0) break;
      var open = [];
      for (i = 0; i < n * n; i++) if (start[i] === UNKNOWN && best.sol[i]) open.push(i);
      if (!open.length) for (i = 0; i < n * n; i++) if (start[i] === UNKNOWN) open.push(i);
      given.push(open[Math.floor(rand(st) * open.length)]);
    }
    return { sol: best.sol, given: given, rows: best.clues.rows, cols: best.clues.cols, n: n };
  }

  // ---------- the game ----------
  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "ten";
    var gen = generate(mode, o.seed);
    var s = {
      mode: mode, n: gen.n, sol: gen.sol.join(""), rows: gen.rows, cols: gen.cols, cells: [], fixed: [], wrongAt: [],
      sel: Math.floor(gen.n / 2) * gen.n + Math.floor(gen.n / 2), crossMode: false, mistakesNow: o.mistakes !== "end",
      hintsAllowed: HINTS, hintsUsed: 0, mistakes: 0, hint: -1, history: [], stroke: 0, checking: false,
      message: "", messageT: 0, updates: 0, won: false, over: false, score: 0, givens: gen.given.length,
      stats: { filled: 0, crossed: 0, undone: 0, cause: null },
    };
    for (var i = 0; i < s.n * s.n; i++) { s.cells.push(UNKNOWN); s.fixed.push(false); s.wrongAt.push(-1); }
    gen.given.forEach(function (gi) { s.cells[gi] = gen.sol[gi] ? FILL : CROSS; s.fixed[gi] = true; });
    return s;
  }

  function solAt(s, i) { return s.sol.charCodeAt(i) - 48; }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function effective(s) { return seconds(s) + HINT_PENALTY * s.hintsUsed + (s.mistakesNow ? MISTAKE_PENALTY * s.mistakes : 0); }
  function scoreOf(s) { return s.won ? Math.max(MIN_SCORE, TOP - effective(s)) : 0; }
  function hintsLeft(s) { return Math.max(0, s.hintsAllowed - s.hintsUsed); }
  function say(s, text) { s.message = text; s.messageT = MESSAGE_UPDATES; }
  function wrongCells(s) {
    var out = [];
    for (var i = 0; i < s.cells.length; i++) if (s.cells[i] === FILL && !solAt(s, i)) out.push(i);
    return out;
  }
  function counts(s) {
    var filled = 0, need = 0;
    for (var i = 0; i < s.cells.length; i++) { if (s.cells[i] === FILL) filled++; need += solAt(s, i); }
    return { filled: filled, need: need };
  }
  /** Does a row ("r") or column ("c") already match its numbers? */
  function lineDone(s, kind, k) {
    var idx = lineCells(s.n, kind, k), clue = kind === "r" ? s.rows[k] : s.cols[k];
    var got = runs(idx.map(function (c) { return s.cells[c]; }), FILL);
    return got.length === clue.length && got.every(function (v, q) { return v === clue[q]; });
  }

  function checkEnd(s, evs) {
    var c = counts(s), wrong = wrongCells(s);
    if (c.filled === c.need && !wrong.length) {
      s.won = true; s.over = true; s.score = scoreOf(s); s.stats.cause = "won"; s.hint = -1; s.checking = false;
      evs.push({ type: "win", score: s.score, seconds: effective(s) });
      return;
    }
    if (!s.mistakesNow && c.filled >= c.need && wrong.length) {
      if (!s.checking) { s.checking = true; say(s, wrong.length + (wrong.length === 1 ? " filled square is" : " filled squares are") + " wrong: marked in red."); evs.push({ type: "check", wrong: wrong.length }); }
    } else s.checking = false;
  }

  /** Set one square to FILL / CROSS / UNKNOWN (part of the current stroke, for Undo). */
  function setCell(s, i, v, evs) {
    if (s.over || !(i >= 0 && i < s.cells.length) || s.fixed[i] || s.cells[i] === v) return;
    var old = s.cells[i], right = solAt(s, i);
    if (s.mistakesNow && ((v === FILL && !right) || (v === CROSS && right))) {
      s.mistakes++; s.cells[i] = right ? FILL : CROSS; s.fixed[i] = true; s.wrongAt[i] = s.updates;
      say(s, "Not right (+" + MISTAKE_PENALTY + " s): that square is " + (right ? "filled." : "empty."));
      evs.push({ type: "wrong", cell: i, penalty: MISTAKE_PENALTY });
    } else {
      s.cells[i] = v;
      s.history.push({ i: i, v0: old, k: s.stroke });
      if (s.history.length > HISTORY_MAX) s.history.shift();
      if (v === FILL) s.stats.filled++; else if (v === CROSS) s.stats.crossed++;
      evs.push({ type: v === FILL ? "fill" : v === CROSS ? "cross" : "clear", cell: i });
    }
    if (s.hint === i) s.hint = -1;
    checkEnd(s, evs);
  }
  /** What a tap on square i does: fill (or cross in cross mode), or empty it again. */
  function targetFor(s, i, cross) {
    var want = cross ? CROSS : FILL;
    return s.cells[i] === want ? UNKNOWN : want;
  }
  /** A tap (pointer down) on a square: starts a stroke; drag() continues it along the row or column. */
  function tap(s, i, cross) {
    var evs = [];
    if (s.over || !(i >= 0 && i < s.cells.length)) return evs;
    s.sel = i;
    if (s.fixed[i]) { s.drag = null; return [{ type: "cursor" }]; }
    s.stroke++;
    var from = s.cells[i], to = targetFor(s, i, cross === undefined ? s.crossMode : cross);
    s.drag = { start: i, from: from, to: to, axis: null };
    setCell(s, i, to, evs);
    return evs;
  }
  function drag(s, i) {
    var evs = [], d = s.drag;
    if (s.over || !d || !(i >= 0 && i < s.cells.length) || i === s.sel) return evs;
    var n = s.n, r0 = Math.floor(d.start / n), c0 = d.start % n, r = Math.floor(i / n), c = i % n;
    if (!d.axis) { if (r === r0 && c !== c0) d.axis = "r"; else if (c === c0 && r !== r0) d.axis = "c"; else return evs; }
    var target = d.axis === "r" ? r0 * n + c : r * n + c0, a = d.axis === "r" ? c0 : r0, b = d.axis === "r" ? c : r;
    s.sel = target;
    for (var k = Math.min(a, b); k <= Math.max(a, b); k++) {
      var cell = d.axis === "r" ? r0 * n + k : k * n + c0;
      if (s.cells[cell] === d.from && !s.fixed[cell]) setCell(s, cell, d.to, evs);
      if (s.over) break;
    }
    return evs;
  }
  function endDrag(s) { s.drag = null; return []; }

  function undo(s) {
    if (s.over || !s.history.length) return [];
    var k = s.history[s.history.length - 1].k;
    while (s.history.length && s.history[s.history.length - 1].k === k) {
      var e = s.history.pop();
      if (!s.fixed[e.i]) s.cells[e.i] = e.v0;
      s.sel = e.i;
    }
    s.stats.undone++; s.checking = false;
    return [{ type: "undo" }];
  }

  function hint(s) {
    if (s.over) return [];
    if (hintsLeft(s) === 0) { say(s, "No hints left for this puzzle."); return [{ type: "nohint" }]; }
    var i, cell = -1, n = s.n;
    for (i = 0; i < s.cells.length && cell < 0; i++) {           // a wrong square first
      if (!s.fixed[i] && ((s.cells[i] === FILL && !solAt(s, i)) || (s.cells[i] === CROSS && solAt(s, i)))) cell = i;
    }
    var why = "That square was wrong, now put right.";
    if (cell < 0) {
      var known = s.cells.slice();
      var res = solveGrid(s.rows, s.cols, known, n, true);
      cell = res.first;
      why = "The numbers decide this square.";
      if (cell < 0) for (i = 0; i < s.cells.length; i++) if (s.cells[i] === UNKNOWN && solAt(s, i)) { cell = i; break; }
    }
    if (cell < 0) return [];
    s.hintsUsed++;
    var v = solAt(s, cell) ? FILL : CROSS, evs = [{ type: "hint", cell: cell, penalty: HINT_PENALTY }];
    s.cells[cell] = v; s.fixed[cell] = true; s.hint = cell; s.sel = cell;
    say(s, why + " (+" + HINT_PENALTY + " s)");
    checkEnd(s, evs);
    return evs;
  }

  function step(s) {
    if (s.over) return [];
    s.updates++;
    if (s.messageT > 0 && --s.messageT === 0) s.message = "";
    return [];
  }

  /** Arrows move the cursor; fire fills (or empties), alt crosses; notes switches the tap between fill and cross;
      hint, undo; typed keys H, U / Z, X (cross), M (switch). */
  function press(s, action, down) {
    if (!down || s.over) return [];
    var n = s.n, r = Math.floor(s.sel / n), c = s.sel % n, evs;
    switch (action) {
      case "up": s.sel = ((r + n - 1) % n) * n + c; return [{ type: "cursor" }];
      case "down": s.sel = ((r + 1) % n) * n + c; return [{ type: "cursor" }];
      case "left": s.sel = r * n + (c + n - 1) % n; return [{ type: "cursor" }];
      case "right": s.sel = r * n + (c + 1) % n; return [{ type: "cursor" }];
      case "fire": evs = tap(s, s.sel, false); s.drag = null; return evs;
      case "alt": evs = tap(s, s.sel, true); s.drag = null; return evs;
      case "notes": s.crossMode = !s.crossMode; return [{ type: "mode", cross: s.crossMode }];
      case "hint": return hint(s);
      case "undo": return undo(s);
    }
    if (action.indexOf("key:") === 0) {
      var k = action.slice(4);
      if (k === "H") return hint(s);
      if (k === "U" || k === "Z") return undo(s);
      if (k === "X") { evs = tap(s, s.sel, true); s.drag = null; return evs; }
      if (k === "M") { s.crossMode = !s.crossMode; return [{ type: "mode", cross: s.crossMode }]; }
      if (k === "ENTER") { evs = tap(s, s.sel, false); s.drag = null; return evs; }
    }
    return [];
  }

  function status(s) { return { score: scoreOf(s), level: 1, over: s.over, done: s.won, seconds: effective(s) }; }
  function save(s) { var d = JSON.parse(JSON.stringify(s)); d.drag = null; return d; }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && data.n === MODES[data.mode].n && typeof data.sol === "string" &&
      data.sol.length === data.n * data.n && /^[01]+$/.test(data.sol) && Array.isArray(data.cells) && data.cells.length === data.n * data.n &&
      data.cells.every(function (v) { return v === 0 || v === 1 || v === 2; }) && Array.isArray(data.fixed) && data.fixed.length === data.n * data.n &&
      Array.isArray(data.history) && typeof data.updates === "number" && typeof data.hintsUsed === "number" && typeof data.mistakes === "number" &&
      data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data)), sol = [];
    for (var i = 0; i < s.sol.length; i++) sol.push(s.sol.charCodeAt(i) - 48);
    var cl = cluesOf(sol, s.n);
    s.rows = cl.rows; s.cols = cl.cols; s.drag = null;
    if (!Array.isArray(s.wrongAt) || s.wrongAt.length !== s.cells.length) { s.wrongAt = []; for (i = 0; i < s.cells.length; i++) s.wrongAt.push(-1); }
    if (!(s.sel >= 0 && s.sel < s.cells.length)) s.sel = 0;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function summary(s) {
    var out = ["Time " + clock(seconds(s))];
    if (s.hintsUsed) out.push(s.hintsUsed + (s.hintsUsed === 1 ? " hint" : " hints") + " (+" + HINT_PENALTY * s.hintsUsed + " s)");
    if (s.mistakesNow && s.mistakes) out.push(s.mistakes + (s.mistakes === 1 ? " mistake" : " mistakes") + " (+" + MISTAKE_PENALTY * s.mistakes + " s)");
    out.push("Counted time " + clock(effective(s)));
    return out;
  }
  function result(s) {
    return {
      score: scoreOf(s), level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, size: s.n, hints: s.hintsUsed, mistakes: s.mistakes, givens: s.givens,
        effective: effective(s), filled: s.stats.filled, crossed: s.stats.crossed, summary: s.won ? summary(s) : undefined },
    };
  }

  var NonogramLogic = {
    UPS: UPS, TOP: TOP, MIN_SCORE: MIN_SCORE, HINT_PENALTY: HINT_PENALTY, MISTAKE_PENALTY: MISTAKE_PENALTY, HINTS: HINTS,
    STATE_VERSION: STATE_VERSION, UNKNOWN: UNKNOWN, FILL: FILL, CROSS: CROSS, MODES: MODES, LABELS: LABELS,
    runs: runs, solveLine: solveLine, solveGrid: solveGrid, cluesOf: cluesOf, generate: generate, lineCells: lineCells,
    create: create, step: step, press: press, tap: tap, drag: drag, endDrag: endDrag, setCell: setCell, undo: undo, hint: hint,
    lineDone: lineDone, wrongCells: wrongCells, counts: counts, hintsLeft: hintsLeft, seconds: seconds, effective: effective,
    scoreOf: scoreOf, status: status, save: save, restore: restore, result: result, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = NonogramLogic; else self.NonogramLogic = NonogramLogic;
})();
