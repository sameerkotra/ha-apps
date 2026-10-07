/* Household Arcade — Colour Sort rules (pure: no DOM, deterministic for a given seed). spec/GAMES.md "Wave 10".

   Tubes of coloured water, four layers each. Pick a tube, then another: the top colour pours across — every layer
   of it that fits — if the other tube is empty, or has the same colour on top and room. Sort every colour into a
   tube of its own (a full tube of one colour; the others empty).
   Boards: 4 × k layers shuffled from the seed into k tubes, plus two empty tubes; a depth-first search over the
   pours (with the positions already seen remembered, and a budget) checks the board can be sorted, else it is made
   again. Modes: 3 colours (gentle), 7, 10 and Levels (200 numbered boards, 3 rising to 12 colours, board n from
   the seed "watersort-level-n", carrying on, SPEC §14). Undo; Restart puts the board back as it was dealt.
   One board's score: 10,000 − 5 × seconds − 10 × pours − 200 × hints, at least 10, only when sorted. */
(function () {
  "use strict";

  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, SEC_COST = 5, MOVE_COST = 10, HINT_COST = 200;
  var CAP = 4, EMPTY = 2, LEVELS = 200, CLEAR_T = 80, POUR_T = 16, BUDGET = 200000, HINT_BUDGET = 60000, STATE_VERSION = 1;
  var MODES = { little: { k: 3 }, classic: { k: 7 }, big: { k: 10 }, levels: { levels: true } };

  function rand(s) {
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function pick(s, n) { return Math.floor(rand(s) * n); }
  function hashSeed(text) {
    var h = 2166136261 | 0;
    for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) || 1;
  }
  function levelColours(n) { return Math.min(12, 3 + Math.floor((n - 1) / 12)); }

  // ---------- the rules (tubes: arrays bottom → top) ----------
  function top(t) { return t.length ? t[t.length - 1] : -1; }
  function topRun(t) { var c = top(t), n = 0; for (var i = t.length - 1; i >= 0 && t[i] === c; i--) n++; return n; }
  /** How many layers would pour from tube a into tube b (0: none). */
  function pourable(tubes, a, b) {
    if (a === b) return 0;
    var A = tubes[a], B = tubes[b];
    if (!A.length || B.length >= CAP) return 0;
    if (B.length && top(B) !== top(A)) return 0;
    return Math.min(topRun(A), CAP - B.length);
  }
  function pourIn(tubes, a, b) {
    var n = pourable(tubes, a, b);
    for (var i = 0; i < n; i++) tubes[b].push(tubes[a].pop());
    return n;
  }
  function isSorted(tubes) {
    for (var i = 0; i < tubes.length; i++) {
      var t = tubes[i];
      if (!t.length) continue;
      if (t.length !== CAP) return false;
      for (var j = 1; j < t.length; j++) if (t[j] !== t[0]) return false;
    }
    return true;
  }
  function keyOf(tubes) { return tubes.map(function (t) { return t.join(","); }).sort().join("|"); }
  function useless(tubes, a, b) {
    // pouring a whole one-colour tube into an empty one changes nothing
    var A = tubes[a];
    return !tubes[b].length && topRun(A) === A.length;
  }

  /** A list of pours that sorts the tubes ([] when already sorted), or null (none found within the budget). */
  function solve(tubes, budget) {
    var seen = new Set(), work = tubes.map(function (t) { return t.slice(); }), path = [], count = 0;
    budget = budget || BUDGET;
    function dfs() {
      if (isSorted(work)) return true;
      if (++count > budget) return false;
      var k = keyOf(work);
      if (seen.has(k)) return false;
      seen.add(k);
      for (var a = 0; a < work.length; a++) {
        if (!work[a].length) continue;
        for (var b = 0; b < work.length; b++) {
          if (!pourable(work, a, b) || useless(work, a, b)) continue;
          var n = pourIn(work, a, b);
          path.push([a, b]);
          if (dfs()) return true;
          path.pop();
          for (var i = 0; i < n; i++) work[a].push(work[b].pop());
        }
      }
      return false;
    }
    return dfs() ? path : null;
  }

  function makeBoard(s, k) {
    for (var tries = 0; tries < 40; tries++) {
      var layers = [], i, j;
      for (i = 0; i < k; i++) for (j = 0; j < CAP; j++) layers.push(i);
      for (i = layers.length - 1; i > 0; i--) { j = pick(s, i + 1); var t = layers[i]; layers[i] = layers[j]; layers[j] = t; }
      var tubes = [];
      for (i = 0; i < k; i++) tubes.push(layers.slice(i * CAP, i * CAP + CAP));
      for (i = 0; i < EMPTY; i++) tubes.push([]);
      if (isSorted(tubes)) continue;
      if (solve(tubes, BUDGET)) return tubes;
    }
    // (practically never) a board built backwards from sorted tubes, which can always be sorted
    var out = [];
    for (i = 0; i < k; i++) out.push([i, i, i, i]);
    for (i = 0; i < EMPTY; i++) out.push([]);
    return out;
  }

  // ---------- the game ----------
  function startBoard(s) {
    if (s.levels) s.rng = hashSeed("watersort-level-" + s.level);
    s.k = s.levels ? levelColours(s.level) : MODES[s.mode].k;
    s.tubes = makeBoard(s, s.k);
    s.dealt = s.tubes.map(function (t) { return t.slice(); });
    s.boardUpdates = 0; s.boardMoves = 0; s.boardHints = 0; s.undo = [];
    s.sel = -1; s.hint = null; s.phase = "play"; s.t = 0; s.cursor = 0; s.pour = null; s.stuck = false;
  }
  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, levels: !!MODES[mode].levels, rng: (o.seed >>> 0) || 1, level: 1, hintsOn: o.hints === "on",
      runScore: 0, moves: 0, hints: 0, boards: 0, updates: 0, k: 3, tubes: null, dealt: null, boardUpdates: 0,
      boardMoves: 0, boardHints: 0, undo: [], sel: -1, hint: null, phase: "play", t: 0, cursor: 0, pour: null,
      won: false, over: false, cause: null, lastBoardScore: 0, noHintAt: -1000, stuck: false,
    };
    if (s.levels) s.level = startAt(o.startLevel, LEVELS);
    startBoard(s);
    return s;
  }
  function boardScore(s) {
    return Math.max(MIN_SCORE, TOP - SEC_COST * Math.floor(s.boardUpdates / UPS) - MOVE_COST * s.boardMoves - HINT_COST * s.boardHints);
  }
  function score(s) { return s.runScore; }

  function pour(s, a, b) {
    var evs = [];
    var n = pourable(s.tubes, a, b);
    if (!n) return [{ type: "nope" }];
    s.undo.push(s.tubes.map(function (t) { return t.slice(); }));
    if (s.undo.length > 300) s.undo.shift();
    var colour = top(s.tubes[a]);
    pourIn(s.tubes, a, b);
    s.moves++; s.boardMoves++; s.hint = null; s.stuck = false;
    s.pour = { from: a, to: b, colour: colour, n: n, t: 0 };
    evs.push({ type: "pour", from: a, to: b, layers: n });
    if (isSorted(s.tubes)) {
      s.lastBoardScore = boardScore(s);
      s.runScore += s.lastBoardScore; s.boards++;
      evs.push({ type: "clear", level: s.level, pours: s.boardMoves });
      if (!s.levels || s.level >= LEVELS) { s.won = true; s.over = true; s.cause = "won"; evs.push({ type: "win", score: s.runScore }); }
      else { s.phase = "clear"; s.t = CLEAR_T; }
    } else {
      var t = s.tubes[b];
      if (t.length === CAP && topRun(t) === CAP) evs.push({ type: "full", tube: b });
    }
    return evs;
  }

  /** Tap tube i: pick it, pour into it, or pick another. */
  function tapTube(s, i) {
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.tubes.length)) return [];
    s.cursor = i;
    if (s.sel < 0) {
      if (!s.tubes[i].length) return [{ type: "nope" }];
      s.sel = i;
      return [{ type: "pick", tube: i }];
    }
    if (s.sel === i) { s.sel = -1; return [{ type: "drop" }]; }
    var from = s.sel;
    if (pourable(s.tubes, from, i)) { s.sel = -1; return pour(s, from, i); }
    if (s.tubes[i].length) { s.sel = i; return [{ type: "nope" }, { type: "pick", tube: i }]; }
    s.sel = -1;
    return [{ type: "nope" }];
  }
  function undo(s) {
    if (s.over || s.phase !== "play" || !s.undo.length) return [];
    s.tubes = s.undo.pop(); s.sel = -1; s.hint = null; s.stuck = false;
    return [{ type: "undo" }];
  }
  function restart(s) {
    if (s.over || s.phase !== "play") return [];
    s.undo.push(s.tubes.map(function (t) { return t.slice(); }));
    s.tubes = s.dealt.map(function (t) { return t.slice(); }); s.sel = -1; s.hint = null; s.stuck = false;
    return [{ type: "restart" }];
  }
  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    if (s.hint) return [];
    var path = solve(s.tubes, HINT_BUDGET);
    if (!path || !path.length) { s.stuck = true; return [{ type: "stuck" }]; }
    s.hint = { from: path[0][0], to: path[0][1] }; s.hints++; s.boardHints++;
    return [{ type: "hint" }];
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "play") s.boardUpdates++;
    if (s.pour && ++s.pour.t > POUR_T) s.pour = null;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      startBoard(s);
      evs.push({ type: "level", level: s.level, colours: s.k });
    }
    return evs;
  }

  /** Keyboard: left / right (and up / down) move between tubes, Space picks and pours. */
  function press(s, action, down) {
    if (!down || s.over || !s.tubes) return [];
    var n = s.tubes.length;
    switch (action) {
      case "left": case "up": s.cursor = (s.cursor + n - 1) % n; return [{ type: "cursor" }];
      case "right": case "down": s.cursor = (s.cursor + 1) % n; return [{ type: "cursor" }];
      case "fire": return tapTube(s, s.cursor);
      case "alt": case "hint": return hint(s);
      case "undo": return undo(s);
      case "erase": return restart(s);
    }
    return [];
  }

  function save(s) { var out = JSON.parse(JSON.stringify(s)); out.pour = null; return out; }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.tubes) && Array.isArray(data.dealt) &&
      data.tubes.length >= 3 && data.tubes.length === data.dealt.length &&
      data.tubes.every(function (t) { return Array.isArray(t) && t.length <= CAP && t.every(function (c) { return c >= 0 && c < 12; }); }) &&
      typeof data.level === "number" && data.level >= 1 && data.level <= LEVELS && typeof data.runScore === "number" &&
      typeof data.updates === "number" && typeof data.rng === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.pour = null; s.sel = -1; s.hint = null;
    if (!Array.isArray(s.undo)) s.undo = [];
    if (!(s.cursor >= 0 && s.cursor < s.tubes.length)) s.cursor = 0;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = [];
    if (s.boards) lines.push(s.levels ? "Boards sorted: " + s.boards : "Sorted in " + s.moves + (s.moves === 1 ? " pour" : " pours"));
    if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints"));
    lines.push("Time " + clock(seconds(s)));
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.cause, mode: s.mode, pours: s.moves, hints: s.hints, boards: s.boards, colours: s.k,
        summary: lines, notSaved: s.boards ? undefined : "Not sorted — not saved" },
    };
  }

  var WaterSortLogic = {
    UPS: UPS, TOP: TOP, CAP: CAP, EMPTY: EMPTY, LEVELS: LEVELS, MIN_SCORE: MIN_SCORE, SEC_COST: SEC_COST, MOVE_COST: MOVE_COST,
    HINT_COST: HINT_COST, CLEAR_T: CLEAR_T, POUR_T: POUR_T, STATE_VERSION: STATE_VERSION, MODES: MODES, create: create, step: step,
    press: press, tapTube: tapTube, pour: pour, undo: undo, restart: restart, hint: hint, pourable: pourable, isSorted: isSorted,
    solve: solve, makeBoard: makeBoard, levelColours: levelColours, hashSeed: hashSeed, score: score, boardScore: boardScore,
    save: save, restore: restore, result: result, seconds: seconds, clock: clock, rand: rand, top: top, topRun: topRun,
  };
  if (typeof module === "object" && module.exports) module.exports = WaterSortLogic; else self.WaterSortLogic = WaterSortLogic;
})();
