/* Household Arcade — Bolt Sort rules (pure: no DOM, deterministic for a given seed). spec/GAMES.md "Wave 10".

   Bolts holding coloured nuts, four to a bolt. Move one nut at a time: the top nut onto an empty bolt, or onto a nut
   of its colour on a bolt with room. Fill each bolt with one colour. In Hidden (and on Levels from level 40) the nuts
   under the top start hidden ("?") and show their colour once they come to the top, so planning is part guesswork.
   Boards: nuts shuffled from the seed onto k bolts plus two empty ones, checked solvable with every colour known (a
   depth-first search with the positions seen remembered and a budget), else made again. Modes: 3 colours (gentle),
   6, 6 hidden and Levels (200 numbered boards, 3 rising to 10 colours, board n from the seed "bolts-level-n",
   carrying on, SPEC §14). Undo; Restart puts the board back as it was dealt.
   One board's score: 10,000 − 5 × seconds − 10 × moves − 200 × hints, at least 10, only when sorted. */
(function () {
  "use strict";

  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, SEC_COST = 5, MOVE_COST = 10, HINT_COST = 200;
  var CAP = 4, EMPTY = 2, LEVELS = 200, CLEAR_T = 80, MOVE_T = 14, BUDGET = 250000, HINT_BUDGET = 80000, STATE_VERSION = 1;
  var MODES = { little: { k: 3, hidden: false }, classic: { k: 6, hidden: false }, hidden: { k: 6, hidden: true }, levels: { levels: true } };

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
  function levelSpec(n) { return { k: Math.min(10, 3 + Math.floor((n - 1) / 15)), hidden: n >= 40 }; }

  function top(b) { return b.length ? b[b.length - 1] : -1; }
  function canMove(bolts, a, b) {
    if (a === b) return false;
    var A = bolts[a], B = bolts[b];
    return A.length > 0 && B.length < CAP && (!B.length || top(B) === top(A));
  }
  function isSorted(bolts) {
    for (var i = 0; i < bolts.length; i++) {
      var t = bolts[i];
      if (!t.length) continue;
      if (t.length !== CAP) return false;
      for (var j = 1; j < t.length; j++) if (t[j] !== t[0]) return false;
    }
    return true;
  }
  function uniformRun(t) { var c = top(t), n = 0; for (var i = t.length - 1; i >= 0 && t[i] === c; i--) n++; return n; }
  function keyOf(bolts) { return bolts.map(function (t) { return t.join(","); }).sort().join("|"); }

  /** Moves [from, to] that sort the bolts ([] when sorted), or null (none found within the budget). */
  function solve(bolts, budget) {
    var seen = new Set(), work = bolts.map(function (t) { return t.slice(); }), path = [], count = 0;
    budget = budget || BUDGET;
    function dfs() {
      if (isSorted(work)) return true;
      if (++count > budget) return false;
      var k = keyOf(work);
      if (seen.has(k)) return false;
      seen.add(k);
      for (var a = 0; a < work.length; a++) {
        var A = work[a];
        if (!A.length) continue;
        if (A.length === CAP && uniformRun(A) === CAP) continue;            // a finished bolt stays
        for (var b = 0; b < work.length; b++) {
          if (!canMove(work, a, b)) continue;
          if (!work[b].length && uniformRun(A) === A.length) continue;      // one colour onto an empty bolt: no use
          work[b].push(A.pop());
          path.push([a, b]);
          if (dfs()) return true;
          path.pop();
          A.push(work[b].pop());
        }
      }
      return false;
    }
    return dfs() ? path : null;
  }

  function makeBoard(s, k) {
    for (var tries = 0; tries < 40; tries++) {
      var nuts = [], i, j;
      for (i = 0; i < k; i++) for (j = 0; j < CAP; j++) nuts.push(i);
      for (i = nuts.length - 1; i > 0; i--) { j = pick(s, i + 1); var t = nuts[i]; nuts[i] = nuts[j]; nuts[j] = t; }
      var bolts = [];
      for (i = 0; i < k; i++) bolts.push(nuts.slice(i * CAP, i * CAP + CAP));
      for (i = 0; i < EMPTY; i++) bolts.push([]);
      if (isSorted(bolts)) continue;
      if (solve(bolts, BUDGET)) return bolts;
    }
    var out = [];
    for (i = 0; i < k; i++) out.push([i, i, i, i]);
    for (i = 0; i < EMPTY; i++) out.push([]);
    return out;
  }

  // ---------- the game ----------
  function reveal(s) {
    for (var i = 0; i < s.bolts.length; i++) {
      var t = s.bolts[i], h = s.hidden[i];
      while (h.length > t.length) h.pop();
      while (h.length < t.length) h.push(false);
      if (h.length) h[h.length - 1] = false;           // the top nut is always shown
    }
  }
  function startBoard(s) {
    if (s.levels) s.rng = hashSeed("bolts-level-" + s.level);
    var spec = s.levels ? levelSpec(s.level) : MODES[s.mode];
    s.k = spec.k; s.hiddenMode = spec.hidden;
    s.bolts = makeBoard(s, s.k);
    s.hidden = s.bolts.map(function (t) { return t.map(function () { return !!spec.hidden; }); });
    reveal(s);
    s.dealt = { bolts: s.bolts.map(function (t) { return t.slice(); }), hidden: s.hidden.map(function (h) { return h.slice(); }) };
    s.boardUpdates = 0; s.boardMoves = 0; s.boardHints = 0; s.undo = [];
    s.sel = -1; s.hint = null; s.phase = "play"; s.t = 0; s.cursor = 0; s.moving = null; s.stuck = false;
  }
  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, levels: !!MODES[mode].levels, rng: (o.seed >>> 0) || 1, level: 1, hintsOn: o.hints === "on",
      runScore: 0, moves: 0, hints: 0, boards: 0, updates: 0, k: 3, hiddenMode: false, bolts: null, hidden: null, dealt: null,
      boardUpdates: 0, boardMoves: 0, boardHints: 0, undo: [], sel: -1, hint: null, phase: "play", t: 0, cursor: 0,
      moving: null, won: false, over: false, cause: null, lastBoardScore: 0, noHintAt: -1000, stuck: false,
    };
    if (s.levels) s.level = startAt(o.startLevel, LEVELS);
    startBoard(s);
    return s;
  }
  function boardScore(s) {
    return Math.max(MIN_SCORE, TOP - SEC_COST * Math.floor(s.boardUpdates / UPS) - MOVE_COST * s.boardMoves - HINT_COST * s.boardHints);
  }
  function score(s) { return s.runScore; }
  function snapshot(s) { return { bolts: s.bolts.map(function (t) { return t.slice(); }), hidden: s.hidden.map(function (h) { return h.slice(); }) }; }

  function move(s, a, b) {
    if (!canMove(s.bolts, a, b)) return [{ type: "nope" }];
    s.undo.push(snapshot(s));
    if (s.undo.length > 300) s.undo.shift();
    var colour = s.bolts[a].pop();
    s.hidden[a].pop();
    s.bolts[b].push(colour); s.hidden[b].push(false);
    var shown = s.hidden[a].length && s.hidden[a][s.hidden[a].length - 1];
    reveal(s);
    s.moves++; s.boardMoves++; s.hint = null; s.stuck = false;
    s.moving = { from: a, to: b, colour: colour, t: 0 };
    var evs = [{ type: "move", from: a, to: b }];
    if (shown) evs.push({ type: "reveal", bolt: a });
    if (isSorted(s.bolts)) {
      s.lastBoardScore = boardScore(s);
      s.runScore += s.lastBoardScore; s.boards++;
      evs.push({ type: "clear", level: s.level, moves: s.boardMoves });
      if (!s.levels || s.level >= LEVELS) { s.won = true; s.over = true; s.cause = "won"; evs.push({ type: "win", score: s.runScore }); }
      else { s.phase = "clear"; s.t = CLEAR_T; }
    } else if (s.bolts[b].length === CAP && uniformRun(s.bolts[b]) === CAP) evs.push({ type: "full", bolt: b });
    return evs;
  }

  /** Tap bolt i: pick it, move its top nut onto it, or pick another. */
  function tapBolt(s, i) {
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.bolts.length)) return [];
    s.cursor = i;
    if (s.sel < 0) {
      if (!s.bolts[i].length) return [{ type: "nope" }];
      s.sel = i;
      return [{ type: "pick", bolt: i }];
    }
    if (s.sel === i) { s.sel = -1; return [{ type: "drop" }]; }
    var from = s.sel;
    if (canMove(s.bolts, from, i)) { s.sel = -1; return move(s, from, i); }
    if (s.bolts[i].length) { s.sel = i; return [{ type: "nope" }, { type: "pick", bolt: i }]; }
    s.sel = -1;
    return [{ type: "nope" }];
  }
  function undo(s) {
    if (s.over || s.phase !== "play" || !s.undo.length) return [];
    var u = s.undo.pop();
    s.bolts = u.bolts; s.hidden = u.hidden; s.sel = -1; s.hint = null; s.stuck = false;
    return [{ type: "undo" }];
  }
  function restart(s) {
    if (s.over || s.phase !== "play") return [];
    s.undo.push(snapshot(s));
    s.bolts = s.dealt.bolts.map(function (t) { return t.slice(); });
    s.hidden = s.dealt.hidden.map(function (h) { return h.slice(); });
    s.sel = -1; s.hint = null; s.stuck = false;
    return [{ type: "restart" }];
  }
  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    if (s.hint) return [];
    var path = solve(s.bolts, HINT_BUDGET);
    if (!path || !path.length) { s.stuck = true; return [{ type: "stuck" }]; }
    s.hint = { from: path[0][0], to: path[0][1] }; s.hints++; s.boardHints++;
    return [{ type: "hint" }];
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "play") s.boardUpdates++;
    if (s.moving && ++s.moving.t > MOVE_T) s.moving = null;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      startBoard(s);
      evs.push({ type: "level", level: s.level, colours: s.k });
    }
    return evs;
  }

  function press(s, action, down) {
    if (!down || s.over || !s.bolts) return [];
    var n = s.bolts.length;
    switch (action) {
      case "left": case "up": s.cursor = (s.cursor + n - 1) % n; return [{ type: "cursor" }];
      case "right": case "down": s.cursor = (s.cursor + 1) % n; return [{ type: "cursor" }];
      case "fire": return tapBolt(s, s.cursor);
      case "alt": case "hint": return hint(s);
      case "undo": return undo(s);
      case "erase": return restart(s);
    }
    return [];
  }

  function save(s) { var out = JSON.parse(JSON.stringify(s)); out.moving = null; return out; }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.bolts) && Array.isArray(data.hidden) &&
      data.bolts.length >= 3 && data.bolts.length === data.hidden.length && data.dealt && Array.isArray(data.dealt.bolts) &&
      data.bolts.every(function (t, i) {
        return Array.isArray(t) && t.length <= CAP && Array.isArray(data.hidden[i]) && data.hidden[i].length === t.length &&
          t.every(function (c) { return c >= 0 && c < 10; });
      }) &&
      typeof data.level === "number" && data.level >= 1 && data.level <= LEVELS && typeof data.runScore === "number" &&
      typeof data.updates === "number" && typeof data.rng === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.moving = null; s.sel = -1; s.hint = null;
    if (!Array.isArray(s.undo)) s.undo = [];
    if (!(s.cursor >= 0 && s.cursor < s.bolts.length)) s.cursor = 0;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = [];
    if (s.boards) lines.push(s.levels ? "Boards sorted: " + s.boards : "Sorted in " + s.moves + (s.moves === 1 ? " move" : " moves"));
    if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints"));
    lines.push("Time " + clock(seconds(s)));
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.cause, mode: s.mode, moves: s.moves, hints: s.hints, boards: s.boards, colours: s.k,
        summary: lines, notSaved: s.boards ? undefined : "Not sorted — not saved" },
    };
  }

  var BoltsLogic = {
    UPS: UPS, TOP: TOP, CAP: CAP, EMPTY: EMPTY, LEVELS: LEVELS, MIN_SCORE: MIN_SCORE, SEC_COST: SEC_COST, MOVE_COST: MOVE_COST,
    HINT_COST: HINT_COST, CLEAR_T: CLEAR_T, MOVE_T: MOVE_T, STATE_VERSION: STATE_VERSION, MODES: MODES, create: create, step: step,
    press: press, tapBolt: tapBolt, move: move, undo: undo, restart: restart, hint: hint, canMove: canMove, isSorted: isSorted,
    solve: solve, makeBoard: makeBoard, levelSpec: levelSpec, hashSeed: hashSeed, score: score, boardScore: boardScore,
    save: save, restore: restore, result: result, seconds: seconds, clock: clock, rand: rand, top: top, uniformRun: uniformRun,
  };
  if (typeof module === "object" && module.exports) module.exports = BoltsLogic; else self.BoltsLogic = BoltsLogic;
})();
