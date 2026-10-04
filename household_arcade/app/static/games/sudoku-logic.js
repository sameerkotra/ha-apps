/* Household Arcade — Sudoku rules (pure: no DOM, deterministic for a given seed).

   A 9 × 9 grid; every row, column and 3 × 3 box holds 1–9 once. The puzzle is made from the seed: a full
   grid is built, then numbers are taken away one at a time as long as exactly one solution is left; a
   human-style solver then grades the puzzle by the hardest technique it needed:
     Easy    only "one number fits this cell" (naked singles), 38–44 numbers given
     Medium  also "this is the only cell for the number in its row, column or box" (hidden singles), 32–37
     Hard    needs locked candidates, pairs or triples, 27–31
     Expert  needs X-Wing / XY-Wing or something harder, 22–26
   Notes (pencil marks, auto-removed when a number is placed in the row, column or box), Fill notes, Undo,
   Erase, Hints that first explain and then fill (each adds 30 s; the number allowed is set by the app, null =
   unlimited), mistakes shown at once (+10 s each) or only when the grid is full, and "number lines": the focus
   number's cells, rows, columns and boxes (see sudoku.js; the rules only keep the focus number).
   The clock is the updates the game ran (60 a second). The score is TOP − the effective seconds (time + the
   penalties) once the puzzle is solved, otherwise 0, so the fastest effective time ranks first.
   One call to step() is one update. */
(function () {
  "use strict";

  var UPS = 60;
  var LEVELS = ["easy", "medium", "hard", "expert"];
  var LEVEL_NAMES = { easy: "Easy", medium: "Medium", hard: "Hard", expert: "Expert" };
  var GIVENS = { easy: [38, 44], medium: [32, 37], hard: [27, 31], expert: [22, 26] };
  var HINT_PENALTY = 30, MISTAKE_PENALTY = 10, TOP = 10000, MIN_SCORE = 10;
  var DEFAULT_HINTS = 3;
  var MAX_TRIES = 90;               // puzzles tried for a level before the closest one is used
  var HISTORY_MAX = 400;
  var MESSAGE_UPDATES = 240;        // a short message stays 4 s
  var STATE_VERSION = 1;

  // ---------- geometry ----------
  var ROW = [], COL = [], BOX = [], UNITS = [], PEERS = [], UNITS_OF = [];
  (function () {
    var i, u, k;
    for (i = 0; i < 81; i++) { ROW[i] = Math.floor(i / 9); COL[i] = i % 9; BOX[i] = 3 * Math.floor(ROW[i] / 3) + Math.floor(COL[i] / 3); }
    for (u = 0; u < 27; u++) UNITS[u] = [];
    for (i = 0; i < 81; i++) { UNITS[ROW[i]].push(i); UNITS[9 + COL[i]].push(i); UNITS[18 + BOX[i]].push(i); }
    for (i = 0; i < 81; i++) {
      UNITS_OF[i] = [ROW[i], 9 + COL[i], 18 + BOX[i]];
      var seen = {}, list = [];
      for (k = 0; k < 3; k++) UNITS[UNITS_OF[i][k]].forEach(function (c) { if (c !== i && !seen[c]) { seen[c] = 1; list.push(c); } });
      PEERS[i] = list;
    }
  })();
  var POP = [], LOWBIT = [];
  (function () {
    for (var m = 0; m < 512; m++) {
      var n = 0, low = 0;
      for (var b = 0; b < 9; b++) if (m >> b & 1) { n++; if (!low) low = b + 1; }
      POP[m] = n; LOWBIT[m] = low;
    }
  })();
  var BOX_NAMES = ["top-left", "top-middle", "top-right", "middle-left", "centre", "middle-right", "bottom-left", "bottom-middle", "bottom-right"];
  function unitName(u) { return u < 9 ? "row " + (u + 1) : u < 18 ? "column " + (u - 8) : "the " + BOX_NAMES[u - 18] + " box"; }
  function unitNameShort(u) { return u < 9 ? "this row" : u < 18 ? "this column" : "this box"; }
  function cellName(i) { return "row " + (ROW[i] + 1) + ", column " + (COL[i] + 1); }

  // ---------- random ----------
  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function shuffle(a, st) {
    for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(rand(st) * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }

  // ---------- a full grid, and counting solutions ----------
  function randomSolution(st) {
    var g = new Int8Array(81), rm = new Int16Array(9), cm = new Int16Array(9), bm = new Int16Array(9);
    function rec(pos) {
      if (pos === 81) return true;
      var avail = ~(rm[ROW[pos]] | cm[COL[pos]] | bm[BOX[pos]]) & 511, ds = [], k;
      for (k = 0; k < 9; k++) if (avail >> k & 1) ds.push(k);
      shuffle(ds, st);
      for (k = 0; k < ds.length; k++) {
        var bit = 1 << ds[k];
        g[pos] = ds[k] + 1; rm[ROW[pos]] |= bit; cm[COL[pos]] |= bit; bm[BOX[pos]] |= bit;
        if (rec(pos + 1)) return true;
        g[pos] = 0; rm[ROW[pos]] &= ~bit; cm[COL[pos]] &= ~bit; bm[BOX[pos]] &= ~bit;
      }
      return false;
    }
    rec(0);
    return g;
  }

  /** How many solutions the grid has, counting no further than `limit`. */
  function countSolutions(puz, limit) {
    var g = Int8Array.from(puz), rm = new Int16Array(9), cm = new Int16Array(9), bm = new Int16Array(9), i, count = 0;
    for (i = 0; i < 81; i++) {
      if (g[i]) {
        var bit = 1 << (g[i] - 1);
        if ((rm[ROW[i]] | cm[COL[i]] | bm[BOX[i]]) & bit) return 0;      // the givens clash
        rm[ROW[i]] |= bit; cm[COL[i]] |= bit; bm[BOX[i]] |= bit;
      }
    }
    function rec() {
      var best = -1, bestN = 10, bestM = 0, m, n, k;
      for (var p = 0; p < 81; p++) {
        if (g[p]) continue;
        m = ~(rm[ROW[p]] | cm[COL[p]] | bm[BOX[p]]) & 511; n = POP[m];
        if (n < bestN) { best = p; bestN = n; bestM = m; if (n <= 1) break; }
      }
      if (best < 0) { count++; return count >= limit; }
      if (bestN === 0) return false;
      for (k = 0; k < 9; k++) {
        if (!(bestM >> k & 1)) continue;
        var b = 1 << k;
        g[best] = k + 1; rm[ROW[best]] |= b; cm[COL[best]] |= b; bm[BOX[best]] |= b;
        var stop = rec();
        g[best] = 0; rm[ROW[best]] &= ~b; cm[COL[best]] &= ~b; bm[BOX[best]] &= ~b;
        if (stop) return true;
      }
      return false;
    }
    rec();
    return count;
  }

  // ---------- the human solver: candidates and techniques ----------
  var T = { NS: 1, HS: 2, LC: 3, NP: 4, HP: 5, NT: 6, XW: 7, XY: 8 };
  var TECH_NAMES = { 1: "a single", 2: "a hidden single", 3: "locked candidates", 4: "a naked pair", 5: "a hidden pair", 6: "a naked triple", 7: "an X-Wing", 8: "an XY-Wing" };

  /** The candidate masks of a grid (bit d−1 = digit d could go there; 0 for a filled cell). */
  function candidates(g) {
    var cand = new Int16Array(81), i, k;
    var rm = new Int16Array(9), cm = new Int16Array(9), bm = new Int16Array(9);
    for (i = 0; i < 81; i++) if (g[i]) { var b = 1 << (g[i] - 1); rm[ROW[i]] |= b; cm[COL[i]] |= b; bm[BOX[i]] |= b; }
    for (i = 0; i < 81; i++) cand[i] = g[i] ? 0 : (~(rm[ROW[i]] | cm[COL[i]] | bm[BOX[i]]) & 511);
    return cand;
  }
  function place(g, cand, i, d) {
    g[i] = d; cand[i] = 0;
    var bit = ~(1 << (d - 1));
    for (var k = 0; k < PEERS[i].length; k++) cand[PEERS[i][k]] &= bit;
  }
  function nakedSingle(g, cand, from) {
    for (var n = 0; n < 81; n++) {
      var i = (n + (from || 0)) % 81;
      if (!g[i] && POP[cand[i]] === 1) return { tech: T.NS, cell: i, digit: LOWBIT[cand[i]] };
    }
    return null;
  }
  function hiddenSingle(g, cand, from) {
    for (var n = 0; n < 27; n++) {
      var u = (n + (from || 0)) % 27, cells = UNITS[u], placed = 0, k;
      for (k = 0; k < 9; k++) if (g[cells[k]]) placed |= 1 << (g[cells[k]] - 1);
      for (var d = 0; d < 9; d++) {
        if (placed >> d & 1) continue;
        var found = -1, cnt = 0;
        for (k = 0; k < 9; k++) if (cand[cells[k]] >> d & 1) { cnt++; found = cells[k]; }
        if (cnt === 1) return { tech: T.HS, cell: found, digit: d + 1, unit: u };
      }
    }
    return null;
  }
  function elim(cand, cells, mask, except) {
    var changed = false;
    for (var k = 0; k < cells.length; k++) {
      var c = cells[k];
      if (except && except.indexOf(c) >= 0) continue;
      if (cand[c] & mask) { cand[c] &= ~mask; changed = true; }
    }
    return changed;
  }
  function cellsWith(cand, cells, d) { return cells.filter(function (c) { return cand[c] >> d & 1; }); }

  function lockedCandidates(g, cand) {
    var b, d, u, k, cells, r, c, rest;
    for (b = 0; b < 9; b++) {                       // pointing: a box's cells for d all in one line
      for (d = 0; d < 9; d++) {
        cells = cellsWith(cand, UNITS[18 + b], d);
        if (cells.length < 2) continue;
        if (cells.every(function (x) { return ROW[x] === ROW[cells[0]]; })) {
          if (elim(cand, UNITS[ROW[cells[0]]], 1 << d, cells.concat(UNITS[18 + b]))) return { tech: T.LC, digit: d + 1, unit: 18 + b, line: ROW[cells[0]], cells: cells, kind: "pointing" };
        }
        if (cells.every(function (x) { return COL[x] === COL[cells[0]]; })) {
          if (elim(cand, UNITS[9 + COL[cells[0]]], 1 << d, UNITS[18 + b])) return { tech: T.LC, digit: d + 1, unit: 18 + b, line: 9 + COL[cells[0]], cells: cells, kind: "pointing" };
        }
      }
    }
    for (u = 0; u < 18; u++) {                      // claiming: a line's cells for d all in one box
      for (d = 0; d < 9; d++) {
        cells = cellsWith(cand, UNITS[u], d);
        if (cells.length < 2) continue;
        if (cells.every(function (x) { return BOX[x] === BOX[cells[0]]; })) {
          if (elim(cand, UNITS[18 + BOX[cells[0]]], 1 << d, UNITS[u])) return { tech: T.LC, digit: d + 1, unit: u, line: 18 + BOX[cells[0]], cells: cells, kind: "claiming" };
        }
      }
    }
    return null;
  }
  function nakedGroups(g, cand, size) {
    for (var u = 0; u < 27; u++) {
      var cells = UNITS[u].filter(function (c) { return !g[c] && POP[cand[c]] >= 2 && POP[cand[c]] <= size; });
      var n = cells.length, i, j, k;
      if (size === 2) {
        for (i = 0; i < n; i++) for (j = i + 1; j < n; j++) {
          if (cand[cells[i]] === cand[cells[j]] && POP[cand[cells[i]]] === 2) {
            if (elim(cand, UNITS[u], cand[cells[i]], [cells[i], cells[j]])) return { tech: T.NP, unit: u, cells: [cells[i], cells[j]], mask: cand[cells[i]] };
          }
        }
      } else {
        for (i = 0; i < n; i++) for (j = i + 1; j < n; j++) for (k = j + 1; k < n; k++) {
          var m = cand[cells[i]] | cand[cells[j]] | cand[cells[k]];
          if (POP[m] === 3) {
            if (elim(cand, UNITS[u], m, [cells[i], cells[j], cells[k]])) return { tech: T.NT, unit: u, cells: [cells[i], cells[j], cells[k]], mask: m };
          }
        }
      }
    }
    return null;
  }
  function hiddenPair(g, cand) {
    for (var u = 0; u < 27; u++) {
      var cells = UNITS[u];
      for (var a = 0; a < 9; a++) {
        var ca = cellsWith(cand, cells, a);
        if (ca.length !== 2) continue;
        for (var b = a + 1; b < 9; b++) {
          var cb = cellsWith(cand, cells, b);
          if (cb.length === 2 && cb[0] === ca[0] && cb[1] === ca[1]) {
            var keep = (1 << a) | (1 << b), changed = false;
            ca.forEach(function (c) { if (cand[c] & ~keep) { cand[c] &= keep; changed = true; } });
            if (changed) return { tech: T.HP, unit: u, cells: ca, mask: keep };
          }
        }
      }
    }
    return null;
  }
  function xWing(g, cand) {
    for (var d = 0; d < 9; d++) {
      for (var pass = 0; pass < 2; pass++) {          // 0: rows decide, columns lose; 1: the other way
        var lines = [];
        for (var l = 0; l < 9; l++) {
          var cells = cellsWith(cand, UNITS[(pass ? 9 : 0) + l], d);
          if (cells.length === 2) lines.push({ l: l, a: pass ? ROW[cells[0]] : COL[cells[0]], b: pass ? ROW[cells[1]] : COL[cells[1]] });
        }
        for (var i = 0; i < lines.length; i++) for (var j = i + 1; j < lines.length; j++) {
          if (lines[i].a === lines[j].a && lines[i].b === lines[j].b) {
            var changed = false, keep = [];
            [lines[i].a, lines[i].b].forEach(function (x) {
              UNITS[(pass ? 0 : 9) + x].forEach(function (c) {
                var lineOf = pass ? COL[c] : ROW[c];
                if (lineOf === lines[i].l || lineOf === lines[j].l) { if (cand[c] >> d & 1) keep.push(c); return; }
                if (cand[c] >> d & 1) { cand[c] &= ~(1 << d); changed = true; }
              });
            });
            if (changed) return { tech: T.XW, digit: d + 1, cells: keep, lines: [lines[i].l, lines[j].l], rows: !pass };
          }
        }
      }
    }
    return null;
  }
  function xyWing(g, cand) {
    for (var p = 0; p < 81; p++) {
      if (g[p] || POP[cand[p]] !== 2) continue;
      var pm = cand[p], x = LOWBIT[pm] - 1, y = LOWBIT[pm & (pm - 1)] - 1;
      var wa = [], wb = [];
      PEERS[p].forEach(function (q) {
        if (g[q] || POP[cand[q]] !== 2) return;
        var m = cand[q];
        if ((m >> x & 1) && !(m >> y & 1)) wa.push(q);
        else if ((m >> y & 1) && !(m >> x & 1)) wb.push(q);
      });
      for (var i = 0; i < wa.length; i++) for (var j = 0; j < wb.length; j++) {
        var zm = (cand[wa[i]] & ~(1 << x)) & (cand[wb[j]] & ~(1 << y));
        if (!zm || POP[zm] !== 1 || wa[i] === wb[j]) continue;
        var changed = false;
        PEERS[wa[i]].forEach(function (c) {
          if (c !== p && c !== wb[j] && PEERS[wb[j]].indexOf(c) >= 0 && (cand[c] & zm)) { cand[c] &= ~zm; changed = true; }
        });
        if (changed) return { tech: T.XY, pivot: p, cells: [p, wa[i], wb[j]], digit: LOWBIT[zm] };
      }
    }
    return null;
  }
  /** Apply the easiest technique that makes progress; returns it ({tech, …}) or null when stuck. */
  function humanStep(g, cand, from) {
    var r = nakedSingle(g, cand, from) || hiddenSingle(g, cand, from);
    if (r) { place(g, cand, r.cell, r.digit); return r; }
    return lockedCandidates(g, cand) || nakedGroups(g, cand, 2) || hiddenPair(g, cand) || nakedGroups(g, cand, 3) ||
      xWing(g, cand) || xyWing(g, cand);
  }
  function solved(g) { for (var i = 0; i < 81; i++) if (!g[i]) return false; return true; }

  /** { solved, hardest } — the hardest technique a human-style solve needed (T.*); unsolved = stuck. */
  function gradeSolve(puz) {
    var g = Int8Array.from(puz), cand = candidates(g), hardest = 0;
    for (var guard = 0; guard < 2000; guard++) {
      if (solved(g)) return { solved: true, hardest: hardest };
      var r = humanStep(g, cand, 0);
      if (!r) return { solved: false, hardest: 99 };
      if (r.tech > hardest) hardest = r.tech;
    }
    return { solved: false, hardest: 99 };
  }
  function levelOfGrade(gr) {
    if (!gr.solved || gr.hardest >= T.XW) return "expert";
    if (gr.hardest <= T.NS) return "easy";
    if (gr.hardest <= T.HS) return "medium";
    return "hard";
  }

  /** A puzzle for a level and seed: { puzzle, solution } as 81-character strings, the grade it got and how many
      numbers are given. Always exactly one solution. The same seed always gives the same puzzle. */
  function generate(level, seed) {
    level = GIVENS[level] ? level : "medium";
    var st = { rng: (seed >>> 0) || 1 }, best = null, want = LEVELS.indexOf(level);
    var range = GIVENS[level];
    for (var attempt = 0; attempt < MAX_TRIES; attempt++) {
      var sol = randomSolution(st), puz = Int8Array.from(sol), givens = 81, i;
      var order = [];
      for (i = 0; i < 81; i++) order.push(i);
      shuffle(order, st);
      var target = range[0] + Math.floor(rand(st) * (range[1] - range[0] + 1));
      for (i = 0; i < 81 && givens > target; i++) {
        var c = order[i], keep = puz[c];
        puz[c] = 0;
        if (countSolutions(puz, 2) === 1) givens--; else puz[c] = keep;
      }
      var gr = gradeSolve(puz), got = levelOfGrade(gr);
      var off = Math.abs(LEVELS.indexOf(got) - want) * 100 + Math.abs(givens - target) + (givens < range[0] || givens > range[1] ? 20 : 0);
      if (!best || off < best.off) best = { off: off, puz: Int8Array.from(puz), sol: sol, grade: got, hardest: gr.hardest, givens: givens, tries: attempt + 1 };
      if (got === level && givens >= range[0] - 2 && givens <= range[1] + 2) break;
    }
    return { puzzle: str(best.puz), solution: str(best.sol), grade: best.grade, hardest: best.hardest, givens: best.givens, tries: best.tries };
  }
  function str(g) { var s = ""; for (var i = 0; i < 81; i++) s += g[i]; return s; }
  function parse(s) { var g = new Int8Array(81); for (var i = 0; i < 81; i++) g[i] = s.charCodeAt(i) - 48; return g; }

  // ---------- text for the hints ----------
  function digitList(ds) {          // [1,2,3,4,5,6,9] → "1–6 and 9"
    ds = ds.slice().sort(function (a, b) { return a - b; });
    var parts = [], i = 0;
    while (i < ds.length) {
      var j = i;
      while (j + 1 < ds.length && ds[j + 1] === ds[j] + 1) j++;
      parts.push(j - i >= 2 ? ds[i] + "–" + ds[j] : ds.slice(i, j + 1).join(", "));
      i = j + 1;
    }
    return parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0] || "";
  }
  function valsIn(vals, cells, skip) { var out = []; cells.forEach(function (c) { if (vals[c] && out.indexOf(vals[c]) < 0 && skip.indexOf(vals[c]) < 0) out.push(vals[c]); }); return out; }

  function singleText(vals, cell, digit) {
    var seen = [digit], parts = [], k, names = ["this row", "the column", "the box"], list;
    for (k = 0; k < 3; k++) {
      list = valsIn(vals, UNITS[UNITS_OF[cell][k]], seen);
      if (list.length) { parts.push(names[k] + " has " + digitList(list)); seen = seen.concat(list); }
    }
    return "Only " + digit + " fits here: " + parts.join(", ") + ".";
  }
  function hiddenText(vals, cell, digit, unit) {
    return digit + " can only go here in " + unitName(unit) + ": every other empty cell there is blocked for " + digit + ".";
  }

  /** The next hint for a grid: { kind, cell, digit, text, cells (to point at), unit } (digit 0 = erase the cell).
      kind: wrong | naked | hidden | advanced | guess. `solution` is the 81-character answer. */
  function findHint(vals, solution, prefer) {
    var i, sol = parse(solution), g = Int8Array.from(vals);
    for (i = 0; i < 81; i++) {
      if (g[i] && g[i] !== sol[i]) {
        var clash = [];
        PEERS[i].forEach(function (p) { if (g[p] === g[i]) clash.push(p); });
        return { kind: "wrong", cell: i, digit: 0, cells: clash,
          text: clash.length ? "This " + g[i] + " can't stay: the same number is in its " + (ROW[clash[0]] === ROW[i] ? "row" : COL[clash[0]] === COL[i] ? "column" : "box") + ". Erase it."
            : "This " + g[i] + " isn't right: the puzzle has only one answer and this isn't it. Erase it." };
      }
    }
    var cand = candidates(g), r = nakedSingle(g, cand, prefer || 0);
    if (r) return { kind: "naked", cell: r.cell, digit: r.digit, cells: [], text: singleText(g, r.cell, r.digit) };
    r = hiddenSingle(g, cand, prefer ? UNITS_OF[prefer][0] : 0);
    if (r) return { kind: "hidden", cell: r.cell, digit: r.digit, cells: UNITS[r.unit].slice(), unit: r.unit, text: hiddenText(g, r.cell, r.digit, r.unit) };
    var first = null;
    for (var guard = 0; guard < 400; guard++) {
      var s = humanStep(g, cand, 0);
      if (!s) break;
      if (!first && s.tech >= T.LC) first = s;
      if (s.tech <= T.HS) {
        var t = first || s;
        return { kind: "advanced", cell: s.cell, digit: s.digit, cells: t.cells || [], unit: t.unit,
          text: "No single move here. Look for " + TECH_NAMES[t.tech] + (t.unit !== undefined ? " in " + unitName(t.unit) : "") + ": it makes " + cellName(s.cell) + " a " + s.digit + "." };
      }
    }
    var best = -1, bestN = 10;
    for (i = 0; i < 81; i++) if (!vals[i] && POP[cand[i]] < bestN) { best = i; bestN = POP[cand[i]]; }
    if (best < 0) return null;
    return { kind: "guess", cell: best, digit: sol[best], cells: [],
      text: "This one needs a trickier step than the usual rules; " + cellName(best) + " is a " + sol[best] + "." };
  }

  // ---------- the game ----------
  function create(o) {
    o = o || {};
    var level = GIVENS[o.mode] ? o.mode : "medium";
    var gen = generate(level, o.seed);
    var hints = o.hints === undefined || o.hints === null ? (o.hints === null ? -1 : DEFAULT_HINTS) : o.hints;
    var s = {
      mode: level, rng: (o.seed >>> 0) || 1, puzzle: gen.puzzle, solution: gen.solution, grade: gen.grade, givens: gen.givens,
      vals: [], notes: [], fixed: [], sel: -1, focus: 0, notesOn: false,
      mistakesNow: o.mistakes !== "end",               // show a wrong number at once (+10 s each)
      hintsAllowed: hints,                             // -1 = unlimited
      hintsUsed: 0, mistakes: 0, hint: null, history: [], checking: false, message: "", messageT: 0,
      updates: 0, won: false, over: false, score: 0, level: 1,
      stats: { placed: 0, erased: 0, undone: 0, notesMade: 0, cause: null },
    };
    for (var i = 0; i < 81; i++) {
      var d = gen.puzzle.charCodeAt(i) - 48;
      s.vals.push(d); s.fixed.push(d > 0); s.notes.push(0);
    }
    return s;
  }

  function seconds(s) { return Math.floor(s.updates / UPS); }
  /** Seconds on the clock: the time played plus 30 for each hint and 10 for each mistake shown at once. */
  function effective(s) { return seconds(s) + HINT_PENALTY * s.hintsUsed + (s.mistakesNow ? MISTAKE_PENALTY * s.mistakes : 0); }
  function scoreOf(s) { return s.won ? Math.max(MIN_SCORE, TOP - effective(s)) : 0; }
  function hintsLeft(s) { return s.hintsAllowed < 0 ? -1 : Math.max(0, s.hintsAllowed - s.hintsUsed); }
  function say(s, text) { s.message = text; s.messageT = MESSAGE_UPDATES; }
  function filled(s) { for (var i = 0; i < 81; i++) if (!s.vals[i]) return false; return true; }
  function wrongCells(s) { var out = []; for (var i = 0; i < 81; i++) if (s.vals[i] && s.vals[i] !== s.solution.charCodeAt(i) - 48) out.push(i); return out; }

  /** Select a cell (the cursor); tapping the selected cell again lets go of it. */
  function tap(s, i) {
    var evs = [];
    if (s.over || !(i >= 0 && i < 81)) return evs;
    if (s.hint) s.hint = null;
    if (s.sel === i) { s.sel = -1; s.focus = 0; evs.push({ type: "select", cell: -1 }); return evs; }
    s.sel = i;
    if (s.vals[i]) { s.focus = s.vals[i]; evs.push({ type: "focus", digit: s.focus }); }
    evs.push({ type: "select", cell: i });
    return evs;
  }
  function removeNotes(s, i, d, changes) {
    var bit = 1 << (d - 1);
    for (var k = 0; k < PEERS[i].length; k++) {
      var p = PEERS[i][k];
      if (s.notes[p] & bit) { changes.push([p, s.notes[p]]); s.notes[p] &= ~bit; }
    }
  }
  function setValue(s, i, d, evs, viaHint) {
    var v0 = s.vals[i], n0 = s.notes[i], changes = [];
    s.vals[i] = d; s.notes[i] = 0;
    removeNotes(s, i, d, changes);
    s.history.push({ i: i, v0: v0, n0: n0, peers: changes });
    if (s.history.length > HISTORY_MAX) s.history.shift();
    s.stats.placed++;
    s.focus = d;
    var wrong = d !== s.solution.charCodeAt(i) - 48;
    if (wrong && !viaHint) {
      s.mistakes++;
      if (s.mistakesNow) { evs.push({ type: "wrong", cell: i, digit: d, penalty: MISTAKE_PENALTY }); say(s, "Not right (+" + MISTAKE_PENALTY + " s)."); }
      else evs.push({ type: "place", cell: i, digit: d });
    } else evs.push({ type: "place", cell: i, digit: d });
    s.checking = false;
    checkEnd(s, evs);
  }
  function checkEnd(s, evs) {
    if (!filled(s)) return;
    if (wrongCells(s).length === 0) {
      s.won = true; s.over = true; s.score = scoreOf(s); s.stats.cause = "won"; s.hint = null;
      evs.push({ type: "win", score: s.score, seconds: effective(s) });
    } else if (!s.mistakesNow) {
      s.checking = true;
      say(s, "The grid is full, but some numbers are wrong. They are marked in red.");
      evs.push({ type: "check", wrong: wrongCells(s).length });
    }
  }

  /** A number from the pad or keyboard: places it (or writes a note) in the selected cell; with no editable cell
      selected it only picks the number for the number lines. */
  function digit(s, d) {
    var evs = [];
    if (s.over || !(d >= 1 && d <= 9)) return evs;
    if (s.hint) s.hint = null;
    var i = s.sel;
    if (i < 0 || s.fixed[i]) { s.focus = s.focus === d ? 0 : d; evs.push({ type: "focus", digit: s.focus }); return evs; }
    if (s.notesOn) {
      if (s.vals[i]) { say(s, "Erase the number first to write notes."); return evs; }
      var n0 = s.notes[i];
      s.notes[i] ^= 1 << (d - 1);
      s.history.push({ i: i, v0: 0, n0: n0, peers: [] });
      s.stats.notesMade++;
      evs.push({ type: "note", cell: i, digit: d, on: !!(s.notes[i] >> (d - 1) & 1) });
      return evs;
    }
    if (s.vals[i] === d) { s.focus = d; evs.push({ type: "focus", digit: d }); return evs; }
    setValue(s, i, d, evs, false);
    return evs;
  }
  function erase(s) {
    var evs = [], i = s.sel;
    if (s.over || i < 0 || s.fixed[i]) return evs;
    if (s.hint) s.hint = null;
    if (!s.vals[i] && !s.notes[i]) return evs;
    s.history.push({ i: i, v0: s.vals[i], n0: s.notes[i], peers: [] });
    s.vals[i] = 0; s.notes[i] = 0; s.checking = false;
    s.stats.erased++;
    evs.push({ type: "erase", cell: i });
    return evs;
  }
  function toggleNotes(s) {
    if (s.over) return [];
    s.notesOn = !s.notesOn;
    say(s, s.notesOn ? "Notes on: numbers are written small." : "Notes off.");
    return [{ type: "mode", notes: s.notesOn }];
  }
  /** Write every possible number into every empty cell. */
  function fillNotes(s) {
    if (s.over) return [];
    var cand = candidates(Int8Array.from(s.vals)), i;
    s.history.push({ all: s.notes.slice() });
    if (s.history.length > HISTORY_MAX) s.history.shift();
    for (i = 0; i < 81; i++) s.notes[i] = s.vals[i] ? 0 : cand[i];
    if (s.hint) s.hint = null;
    return [{ type: "fill" }];
  }
  function undo(s) {
    if (s.over || !s.history.length) return [];
    var e = s.history.pop(), i;
    if (e.all) { s.notes = e.all.slice(); }
    else {
      s.vals[e.i] = e.v0; s.notes[e.i] = e.n0;
      (e.peers || []).forEach(function (p) { s.notes[p[0]] = p[1]; });
      s.sel = e.i; s.checking = false;
    }
    s.hint = null; s.stats.undone++;
    return [{ type: "undo" }];
  }

  /** Hint button: the first press explains (and counts: 30 s), pressing it again fills the cell in. */
  function hint(s) {
    var evs = [];
    if (s.over) return evs;
    if (s.hint) {
      var h = s.hint;
      s.hint = null;
      if (h.digit === 0) { s.history.push({ i: h.cell, v0: s.vals[h.cell], n0: s.notes[h.cell], peers: [] }); s.vals[h.cell] = 0; s.notes[h.cell] = 0; evs.push({ type: "erase", cell: h.cell }); }
      else setValue(s, h.cell, h.digit, evs, true);
      s.sel = h.cell;
      evs.push({ type: "hintFill", cell: h.cell });
      return evs;
    }
    var left = hintsLeft(s);
    if (left === 0) { say(s, "No hints left for this puzzle."); return [{ type: "nohint" }]; }
    var found = findHint(s.vals, s.solution, s.sel >= 0 ? s.sel : 0);
    if (!found) return evs;
    s.hintsUsed++;
    s.hint = found; s.sel = found.cell; s.focus = found.digit || 0;
    say(s, found.text); s.messageT = 1 << 30;
    evs.push({ type: "hint", cell: found.cell, kind: found.kind, penalty: HINT_PENALTY });
    return evs;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.messageT > 0 && !s.hint && --s.messageT === 0) s.message = "";
    return evs;
  }

  /** The shell's actions: arrows, n1–n9 (the pad), notes, fill, hint, undo, erase, and "key:<K>" from the keyboard. */
  function press(s, action, down) {
    if (!down || s.over) return [];
    var m = /^n([1-9])$/.exec(action);
    if (m) return digit(s, +m[1]);
    switch (action) {
      case "left": return arrow(s, -1, 0);
      case "right": return arrow(s, 1, 0);
      case "up": return arrow(s, 0, -1);
      case "down": return arrow(s, 0, 1);
      case "notes": return toggleNotes(s);
      case "fill": return fillNotes(s);
      case "hint": return hint(s);
      case "undo": return undo(s);
      case "erase": return erase(s);
    }
    if (action.indexOf("key:") === 0) {
      var k = action.slice(4);
      if (/^[1-9]$/.test(k)) return digit(s, +k);
      if (k === "BACKSPACE" || k === "DELETE" || k === "0") return erase(s);
      if (k === "N") return toggleNotes(s);
      if (k === "H") return hint(s);
      if (k === "U" || k === "Z") return undo(s);
      if (k === "F") return fillNotes(s);
    }
    return [];
  }
  function arrow(s, dx, dy) {
    if (s.sel < 0) { s.sel = 40; return [{ type: "select", cell: 40 }]; }
    var r = (ROW[s.sel] + dy + 9) % 9, c = (COL[s.sel] + dx + 9) % 9;
    s.hint = null; s.sel = r * 9 + c;
    if (s.vals[s.sel]) s.focus = s.vals[s.sel];
    return [{ type: "select", cell: s.sel }];
  }

  /** Cells the focus number's lines cover: the rows, columns and boxes of every cell holding it. */
  function lines(s, d) {
    var rows = {}, cols = {}, boxes = {}, holders = [], i;
    if (!d) return null;
    for (i = 0; i < 81; i++) if (s.vals[i] === d) { holders.push(i); rows[ROW[i]] = 1; cols[COL[i]] = 1; boxes[BOX[i]] = 1; }
    var covered = [], open = [];
    for (i = 0; i < 81; i++) {
      var c = !!(rows[ROW[i]] || cols[COL[i]] || boxes[BOX[i]]);
      covered.push(c);
      if (!c && !s.vals[i]) open.push(i);
    }
    return { holders: holders, rows: Object.keys(rows).map(Number), cols: Object.keys(cols).map(Number), boxes: Object.keys(boxes).map(Number), covered: covered, open: open };
  }
  function counts(s) { var n = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]; for (var i = 0; i < 81; i++) if (s.vals[i] && s.vals[i] === s.solution.charCodeAt(i) - 48) n[s.vals[i]]++; return n; }

  function status(s) {
    return { score: s.won ? s.score : 0, level: 1, over: s.over, done: s.won, seconds: effective(s), filled: s.vals.filter(Boolean).length };
  }
  function save(s) { return JSON.parse(JSON.stringify(s)); }
  function restore(data) {
    var ok = data && typeof data === "object" && GIVENS[data.mode] && typeof data.puzzle === "string" && data.puzzle.length === 81 &&
      typeof data.solution === "string" && data.solution.length === 81 && /^[0-9]{81}$/.test(data.puzzle) && /^[1-9]{81}$/.test(data.solution) &&
      Array.isArray(data.vals) && data.vals.length === 81 && Array.isArray(data.notes) && data.notes.length === 81 &&
      Array.isArray(data.fixed) && data.fixed.length === 81 && Array.isArray(data.history) &&
      data.vals.every(function (v) { return v >= 0 && v <= 9 && v === Math.floor(v); }) &&
      data.notes.every(function (n) { return n >= 0 && n < 512 && n === Math.floor(n); }) &&
      typeof data.updates === "number" && data.stats && typeof data.stats === "object" && typeof data.hintsUsed === "number" &&
      countSolutions(parse(data.puzzle), 2) === 1;
    if (!ok) throw new Error("That saved game can't be continued.");
    for (var i = 0; i < 81; i++) {
      var g = data.puzzle.charCodeAt(i) - 48;
      if ((g > 0) !== !!data.fixed[i] || (g > 0 && data.vals[i] !== g)) throw new Error("That saved game can't be continued.");
    }
    var sol = parse(data.solution);
    for (var u = 0; u < UNITS.length; u++) {
      var seen = 0;
      for (var q = 0; q < 9; q++) seen |= 1 << sol[UNITS[u][q]];
      if (seen !== 1022) throw new Error("That saved game can't be continued.");
    }
    for (var j = 0; j < 81; j++) if (data.puzzle.charCodeAt(j) > 48 && data.puzzle.charCodeAt(j) - 48 !== sol[j]) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.hint = null;
    return s;
  }

  function summary(s) {
    var lines_ = ["Time " + clock(seconds(s))];
    if (s.hintsUsed) lines_.push(s.hintsUsed + (s.hintsUsed === 1 ? " hint" : " hints") + " (+" + (HINT_PENALTY * s.hintsUsed) + " s)");
    if (s.mistakesNow && s.mistakes) lines_.push(s.mistakes + (s.mistakes === 1 ? " mistake" : " mistakes") + " (+" + (MISTAKE_PENALTY * s.mistakes) + " s)");
    else if (!s.mistakesNow && s.mistakes) lines_.push(s.mistakes + (s.mistakes === 1 ? " wrong number" : " wrong numbers") + " along the way");
    lines_.push("Counted time " + clock(effective(s)));
    return lines_;
  }
  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }

  function result(s) {
    return {
      score: s.won ? s.score : 0, level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, hints: s.hintsUsed, mistakes: s.mistakes, effective: effective(s),
        placed: s.stats.placed, erased: s.stats.erased, undone: s.stats.undone, givens: s.givens, grade: s.grade, summary: s.won ? summary(s) : undefined },
    };
  }

  var SudokuLogic = {
    UPS: UPS, LEVELS: LEVELS, LEVEL_NAMES: LEVEL_NAMES, GIVENS: GIVENS, HINT_PENALTY: HINT_PENALTY, MISTAKE_PENALTY: MISTAKE_PENALTY,
    TOP: TOP, MIN_SCORE: MIN_SCORE, DEFAULT_HINTS: DEFAULT_HINTS, STATE_VERSION: STATE_VERSION, T: T, TECH_NAMES: TECH_NAMES,
    ROW: ROW, COL: COL, BOX: BOX, UNITS: UNITS, PEERS: PEERS,
    generate: generate, countSolutions: countSolutions, gradeSolve: gradeSolve, levelOfGrade: levelOfGrade, candidates: candidates,
    findHint: findHint, digitList: digitList, parse: parse,
    create: create, step: step, tap: tap, digit: digit, erase: erase, toggleNotes: toggleNotes, fillNotes: fillNotes, undo: undo, hint: hint,
    press: press, lines: lines, counts: counts, wrongCells: wrongCells, hintsLeft: hintsLeft,
    seconds: seconds, effective: effective, scoreOf: scoreOf, status: status, clock: clock,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = SudokuLogic; else self.SudokuLogic = SudokuLogic;
})();
