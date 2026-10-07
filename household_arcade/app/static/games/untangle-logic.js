/* Household Arcade — Untangle rules (pure: no DOM, deterministic for a given seed). spec/GAMES.md "Wave 10".

   Points joined by lines, all tangled up. Drag the points until no two lines cross.
   Boards: points on a grid from the seed; lines added (shortest first, with a little randomness) only where they
   cross no other line and pass through no other point, so an untangled drawing exists (where the points were
   made); every point gets at least two lines where it can. Then the points are set on a circle in a shuffled order
   (shuffled again if nothing crosses). Crossings are counted with whole numbers only: two lines that share a point
   don't cross. Modes: 6 points (gentle), 10, 16 and Levels (200 numbered boards, 6 rising to 30 points, board n
   from the seed "untangle-level-n", carrying on, SPEC §14).
   One board's score: 10,000 − 10 × seconds − 20 × moves − 200 × hints, at least 10, only when untangled. */
(function () {
  "use strict";

  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, SEC_COST = 10, MOVE_COST = 20, HINT_COST = 200, LEVEL_COUNT = 200, CLEAR_T = 80;
  var STATE_VERSION = 1, X0 = 16, Y0 = 46, SIZE = 208, CX = 120, CY = 150, RADIUS = 96, STEP_KEY = 6;
  var MODES = { little: { k: 6 }, classic: { k: 10 }, big: { k: 16 }, levels: { levels: true }, book: { book: true } };
  var SPAN = 8, GRID_STEP = SIZE / SPAN;            // a puzzle-book point at (x, y), 0-8, sits at X0 + 26x, Y0 + 26y

  // The puzzle book (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A puzzle is its
  // untangled drawing: points on a 9 × 9 grid and the lines between them, no two crossing. The server
  // (level_kinds/untangle.py) checks the same.
  var LEVELS = [
    {"name": "Little house", "points": [[2, 4], [6, 4], [6, 8], [2, 8], [4, 1], [4, 6]], "edges": [[0, 1], [1, 2], [2, 3], [3, 0], [0, 4], [1, 4], [5, 0], [5, 1], [5, 2], [5, 3]]},
    {"name": "Fishing net", "points": [[1, 8], [8, 0], [0, 8], [3, 5], [1, 6], [7, 0], [6, 3], [6, 6]], "edges": [[1, 5], [0, 2], [0, 4], [2, 4], [3, 4], [6, 7], [3, 7], [5, 6], [3, 6], [0, 3], [1, 6], [0, 7]]},
    {"name": "Spider web", "points": [[7, 3], [0, 0], [5, 4], [8, 3], [0, 5], [3, 4], [2, 1], [0, 4], [8, 7], [4, 8]], "edges": [[4, 7], [0, 3], [2, 5], [0, 2], [1, 6], [5, 7], [2, 3], [5, 6], [4, 5], [6, 7], [1, 7], [8, 9], [3, 8], [2, 9], [0, 5], [5, 9]]},
    {"name": "Climbing frame", "points": [[3, 3], [8, 3], [7, 6], [1, 7], [5, 2], [6, 6], [8, 2], [0, 8], [0, 1], [8, 7], [3, 6], [3, 2]], "edges": [[0, 11], [2, 9], [2, 5], [1, 6], [3, 7], [4, 11], [5, 9], [0, 4], [3, 10], [4, 6], [8, 11], [0, 10], [5, 10], [1, 2], [1, 4], [1, 5], [0, 8], [7, 10], [1, 9], [4, 5]]},
    {"name": "Star map", "points": [[7, 4], [4, 1], [5, 7], [2, 7], [6, 7], [8, 5], [3, 3], [6, 2], [8, 6], [6, 8], [0, 4], [3, 1], [5, 8], [8, 1], [3, 4]], "edges": [[6, 14], [4, 9], [0, 5], [2, 12], [1, 11], [4, 12], [2, 4], [9, 12], [5, 8], [1, 7], [7, 13], [4, 8], [6, 11], [0, 7], [1, 6], [0, 8], [8, 9], [10, 14], [6, 10], [2, 3], [0, 4], [1, 14], [0, 13], [3, 14], [3, 12], [2, 14], [0, 2]]},
  ];

  function levelProblem(l) {
    if (!l || typeof l !== "object" || typeof l.name !== "string" || !Array.isArray(l.points) || !Array.isArray(l.edges)) return "not a puzzle";
    var k = l.points.length, deg = [], seen = {}, i;
    if (k < 6 || k > 30 || l.edges.length < 6 || l.edges.length > 80) return "size";
    for (i = 0; i < k; i++) {
      var p = l.points[i];
      if (!Array.isArray(p) || p.length !== 2 || !(p[0] >= 0 && p[0] <= SPAN && p[1] >= 0 && p[1] <= SPAN) ||
          p[0] !== Math.floor(p[0]) || p[1] !== Math.floor(p[1]) || seen[p[0] + "," + p[1]]) return "point";
      seen[p[0] + "," + p[1]] = 1; deg.push(0);
    }
    for (i = 0; i < l.edges.length; i++) {
      var e = l.edges[i];
      if (!Array.isArray(e) || e.length !== 2 || !(e[0] >= 0 && e[0] < k && e[1] >= 0 && e[1] < k) || e[0] === e[1]) return "line";
      deg[e[0]]++; deg[e[1]]++;
    }
    for (i = 0; i < k; i++) if (deg[i] < 2) return "lines per point";
    if (crossings(l.points, l.edges).count) return "crossing";
    return null;
  }
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (l) { return levelProblem(l) === null; });
    return out.length ? out : LEVELS;
  }
  function total(s) { return s.book ? s.list.length : LEVEL_COUNT; }

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
  function levelPoints(n) { return Math.min(30, 6 + Math.floor((n - 1) / 8)); }

  // ---------- geometry, in whole numbers ----------
  function orient(ax, ay, bx, by, cx, cy) { var v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); return v > 0 ? 1 : v < 0 ? -1 : 0; }
  function onSegment(ax, ay, bx, by, px, py) {
    return Math.min(ax, bx) <= px && px <= Math.max(ax, bx) && Math.min(ay, by) <= py && py <= Math.max(ay, by);
  }
  /** Do segments p1–p2 and p3–p4 cross (touching ends that are the same point don't count)? */
  function segmentsCross(p1, p2, p3, p4) {
    var o1 = orient(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]), o2 = orient(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1]);
    var o3 = orient(p3[0], p3[1], p4[0], p4[1], p1[0], p1[1]), o4 = orient(p3[0], p3[1], p4[0], p4[1], p2[0], p2[1]);
    if (o1 !== o2 && o3 !== o4 && o1 && o2 && o3 && o4) return true;
    if (!o1 && onSegment(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1])) return true;
    if (!o2 && onSegment(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1])) return true;
    if (!o3 && onSegment(p3[0], p3[1], p4[0], p4[1], p1[0], p1[1])) return true;
    if (!o4 && onSegment(p3[0], p3[1], p4[0], p4[1], p2[0], p2[1])) return true;
    return false;
  }
  function edgesCross(pts, e, f) {
    if (e[0] === f[0] || e[0] === f[1] || e[1] === f[0] || e[1] === f[1]) {
      // sharing a point: they cross only if they lie on top of each other
      var shared = e[0] === f[0] || e[0] === f[1] ? e[0] : e[1], a = e[0] === shared ? e[1] : e[0], b = f[0] === shared ? f[1] : f[0];
      var P = pts[shared], A = pts[a], B = pts[b];
      if (orient(P[0], P[1], A[0], A[1], B[0], B[1]) !== 0) return false;
      return (A[0] - P[0]) * (B[0] - P[0]) + (A[1] - P[1]) * (B[1] - P[1]) > 0;
    }
    return segmentsCross(pts[e[0]], pts[e[1]], pts[f[0]], pts[f[1]]);
  }
  /** Which lines cross another, and how many crossings there are. */
  function crossings(pts, edges) {
    var bad = new Uint8Array(edges.length), count = 0;
    for (var i = 0; i < edges.length; i++) for (var j = i + 1; j < edges.length; j++) {
      if (edgesCross(pts, edges[i], edges[j])) { bad[i] = 1; bad[j] = 1; count++; }
    }
    return { count: count, bad: bad };
  }
  function through(pts, a, b, skip) {
    for (var i = 0; i < pts.length; i++) {
      if (i === a || i === b || i === skip) continue;
      if (orient(pts[a][0], pts[a][1], pts[b][0], pts[b][1], pts[i][0], pts[i][1]) === 0 &&
          onSegment(pts[a][0], pts[a][1], pts[b][0], pts[b][1], pts[i][0], pts[i][1])) return true;
    }
    return false;
  }

  function makeBoard(s, k) {
    var m = Math.max(3, Math.ceil(Math.sqrt(k * 1.8))), cell = Math.floor(SIZE / m), slots = [], i, j;
    for (i = 0; i < m * m; i++) slots.push(i);
    for (i = slots.length - 1; i > 0; i--) { j = pick(s, i + 1); var t = slots[i]; slots[i] = slots[j]; slots[j] = t; }
    var home = [];
    for (i = 0; i < k; i++) {
      var sl = slots[i], gx = sl % m, gy = Math.floor(sl / m);
      home.push([X0 + gx * cell + Math.floor(cell / 2) + pick(s, 7) - 3, Y0 + gy * cell + Math.floor(cell / 2) + pick(s, 7) - 3]);
    }
    var cand = [];
    for (i = 0; i < k; i++) for (j = i + 1; j < k; j++) {
      var dx = home[i][0] - home[j][0], dy = home[i][1] - home[j][1];
      cand.push({ a: i, b: j, d: dx * dx + dy * dy + pick(s, cell * cell) });
    }
    cand.sort(function (p, q) { return p.d - q.d || p.a - q.a || p.b - q.b; });
    var edges = [], deg = new Array(k).fill(0), limit = Math.floor(k * 1.6);
    function tryAdd(c) {
      if (through(home, c.a, c.b, -1)) return false;
      for (var e = 0; e < edges.length; e++) if (edgesCross(home, edges[e], [c.a, c.b])) return false;
      edges.push([c.a, c.b]); deg[c.a]++; deg[c.b]++;
      return true;
    }
    for (i = 0; i < cand.length && edges.length < limit; i++) tryAdd(cand[i]);
    for (i = 0; i < cand.length; i++) {                        // every point at least two lines where it can
      var c = cand[i];
      if ((deg[c.a] < 2 || deg[c.b] < 2) && !edges.some(function (e) { return (e[0] === c.a && e[1] === c.b); })) tryAdd(c);
    }
    return { home: home, pts: scramble(s, k, edges), edges: edges };
  }
  /** The start: the points on a circle, in a shuffled order, until something crosses. */
  function scramble(s, k, edges) {
    var pts = null, i, j;
    for (var tries = 0; tries < 30; tries++) {
      var order = [];
      for (i = 0; i < k; i++) order.push(i);
      for (i = k - 1; i > 0; i--) { j = pick(s, i + 1); var t2 = order[i]; order[i] = order[j]; order[j] = t2; }
      pts = new Array(k);
      for (i = 0; i < k; i++) pts[order[i]] = circlePoint(i, k);
      if (crossings(pts, edges).count > 0) break;
    }
    return pts;
  }
  function bookBoard(s, l) {
    var home = l.points.map(function (p) { return [X0 + p[0] * GRID_STEP, Y0 + p[1] * GRID_STEP]; });
    var edges = l.edges.map(function (e) { return [Math.min(e[0], e[1]), Math.max(e[0], e[1])]; });
    return { home: home, pts: scramble(s, home.length, edges), edges: edges };
  }
  /** Point i of k on the start circle, whole numbers (a table of the circle, no trigonometry). */
  function circlePoint(i, k) {
    // round the circle without trigonometry: each quarter uses x = (1 − t²)/(1 + t²), y = 2t/(1 + t²) for t in [0, 1)
    var u = (i * 4096 / k) % 4096, quarter = Math.floor(u / 1024), f = (u % 1024) / 1024;   // f in [0, 1)
    var t = f, x = (1 - t * t) / (1 + t * t), y = 2 * t / (1 + t * t);
    var X = quarter === 0 ? x : quarter === 1 ? -y : quarter === 2 ? -x : y;
    var Y = quarter === 0 ? y : quarter === 1 ? x : quarter === 2 ? -y : -x;
    return [Math.round(CX + RADIUS * X), Math.round(CY + RADIUS * Y)];
  }

  // ---------- the game ----------
  function startBoard(s) {
    var b;
    if (s.book) {
      var l = s.list[s.level - 1];
      s.rng = hashSeed("untangle-book-" + JSON.stringify(l.points) + JSON.stringify(l.edges));
      s.k = l.points.length;
      b = bookBoard(s, l);
    } else {
      if (s.levels) s.rng = hashSeed("untangle-level-" + s.level);
      s.k = s.levels ? levelPoints(s.level) : MODES[s.mode].k;
      b = makeBoard(s, s.k);
    }
    s.home = b.home; s.pts = b.pts; s.edges = b.edges;
    s.boardUpdates = 0; s.boardMoves = 0; s.boardHints = 0; s.sel = -1; s.last = -1; s.phase = "play"; s.t = 0;
    s.crossCount = crossings(s.pts, s.edges).count;
  }
  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, levels: !!(MODES[mode].levels || MODES[mode].book), book: !!MODES[mode].book,
      list: MODES[mode].book ? usableLevels(o.levels) : null, rng: (o.seed >>> 0) || 1, level: 1, hintsOn: o.hints === "on",
      runScore: 0, moves: 0, hints: 0, boards: 0, updates: 0, k: 6, home: null, pts: null, edges: null, boardUpdates: 0,
      boardMoves: 0, boardHints: 0, sel: -1, last: -1, phase: "play", t: 0, crossCount: 0, won: false, over: false,
      cause: null, lastBoardScore: 0, noHintAt: -1000, hinted: -1,
    };
    if (s.levels) s.level = startAt(o.startLevel, total(s));
    startBoard(s);
    return s;
  }
  function boardScore(s) {
    return Math.max(MIN_SCORE, TOP - SEC_COST * Math.floor(s.boardUpdates / UPS) - MOVE_COST * s.boardMoves - HINT_COST * s.boardHints);
  }
  function score(s) { return s.runScore; }
  function clampX(x) { return Math.max(X0, Math.min(X0 + SIZE, Math.round(x))); }
  function clampY(y) { return Math.max(Y0, Math.min(Y0 + SIZE, Math.round(y))); }

  function pointAt(s, x, y, r) {
    var best = -1, bd = (r || 14) * (r || 14);
    for (var i = 0; i < s.pts.length; i++) {
      var dx = s.pts[i][0] - x, dy = s.pts[i][1] - y, d = dx * dx + dy * dy;
      if (d <= bd) { bd = d; best = i; }
    }
    return best;
  }
  /** Move point i to (x, y). A new point moved counts as a move (moving the same one again doesn't). */
  function movePoint(s, i, x, y) {
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.pts.length)) return [];
    s.pts[i] = [clampX(x), clampY(y)];
    if (s.last !== i) { s.moves++; s.boardMoves++; s.last = i; }
    s.crossCount = crossings(s.pts, s.edges).count;
    s.hinted = -1;
    return [{ type: "drag", point: i }];
  }
  /** Let go: untangled ends the board. */
  function release(s) {
    var evs = [];
    if (s.over || s.phase !== "play") return evs;
    if (s.crossCount === 0) {
      s.lastBoardScore = boardScore(s);
      s.runScore += s.lastBoardScore; s.boards++;
      evs.push({ type: "clear", level: s.level, moves: s.boardMoves });
      if (!s.levels || s.level >= total(s)) { s.won = true; s.over = true; s.cause = "won"; evs.push({ type: "win", score: s.runScore }); }
      else { s.phase = "clear"; s.t = CLEAR_T; }
    } else evs.push({ type: "drop", crossings: s.crossCount });
    return evs;
  }
  /** Hint: move the point whose return to where it was made helps most. */
  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    var best = -1, bc = s.crossCount;
    for (var i = 0; i < s.pts.length; i++) {
      if (s.pts[i][0] === s.home[i][0] && s.pts[i][1] === s.home[i][1]) continue;
      var keep = s.pts[i];
      s.pts[i] = s.home[i];
      var c = crossings(s.pts, s.edges).count;
      s.pts[i] = keep;
      if (best < 0 || c < bc) { bc = c; best = i; }
    }
    if (best < 0) return [];
    s.pts[best] = s.home[best].slice();
    s.crossCount = bc; s.hints++; s.boardHints++; s.hinted = best; s.last = -1;
    var evs = [{ type: "hint", point: best }];
    return evs.concat(release(s));
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "play") s.boardUpdates++;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      startBoard(s);
      evs.push({ type: "level", level: s.level, points: s.k });
    }
    return evs;
  }

  /** Keyboard: Space picks the next point; the arrows move it a little; it counts when you pick another or stop. */
  function press(s, action, down) {
    if (!down || s.over || !s.pts) return [];
    if (action === "alt" || action === "hint") return hint(s);
    if (action === "fire") { s.sel = (s.sel + 1) % s.pts.length; return [{ type: "pick", point: s.sel }].concat(s.crossCount === 0 ? release(s) : []); }
    var dx = action === "left" ? -STEP_KEY : action === "right" ? STEP_KEY : 0, dy = action === "up" ? -STEP_KEY : action === "down" ? STEP_KEY : 0;
    if ((!dx && !dy) || s.sel < 0) return [];
    var evs = movePoint(s, s.sel, s.pts[s.sel][0] + dx, s.pts[s.sel][1] + dy);
    return s.crossCount === 0 ? evs.concat(release(s)) : evs;
  }

  function save(s) { var out = JSON.parse(JSON.stringify(s)); out.list = null; return out; }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.pts) && Array.isArray(data.home) &&
      Array.isArray(data.edges) && data.pts.length === data.home.length && data.pts.length >= 3 &&
      data.pts.every(function (p) { return Array.isArray(p) && p.length === 2 && typeof p[0] === "number" && typeof p[1] === "number"; }) &&
      data.edges.every(function (e) { return Array.isArray(e) && e[0] >= 0 && e[1] >= 0 && e[0] < data.pts.length && e[1] < data.pts.length; }) &&
      typeof data.level === "number" && data.level >= 1 && typeof data.runScore === "number" &&
      typeof data.updates === "number" && typeof data.rng === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.book = data.mode === "book"; s.list = s.book ? usableLevels(levels) : null;
    if (s.level > total(s)) throw new Error("That saved game can't be continued.");
    s.crossCount = crossings(s.pts, s.edges).count;
    if (!(s.sel >= -1 && s.sel < s.pts.length)) s.sel = -1;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = [];
    if (s.boards) lines.push(s.levels ? "Boards untangled: " + s.boards : "Untangled in " + s.moves + (s.moves === 1 ? " move" : " moves"));
    if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints"));
    lines.push("Time " + clock(seconds(s)));
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.cause, mode: s.mode, moves: s.moves, hints: s.hints, boards: s.boards, points: s.k,
        summary: lines, notSaved: s.boards ? undefined : "Still tangled — not saved" },
    };
  }

  var UntangleLogic = {
    UPS: UPS, TOP: TOP, LEVEL_COUNT: LEVEL_COUNT, LEVELS: LEVELS, usableLevels: usableLevels, levelProblem: levelProblem,
    total: total, bookBoard: bookBoard, MIN_SCORE: MIN_SCORE, SEC_COST: SEC_COST, MOVE_COST: MOVE_COST, HINT_COST: HINT_COST,
    CLEAR_T: CLEAR_T, STATE_VERSION: STATE_VERSION, MODES: MODES, X0: X0, Y0: Y0, SIZE: SIZE, create: create, step: step,
    press: press, movePoint: movePoint, release: release, hint: hint, pointAt: pointAt, crossings: crossings,
    segmentsCross: segmentsCross, edgesCross: edgesCross, makeBoard: makeBoard, levelPoints: levelPoints, hashSeed: hashSeed,
    circlePoint: circlePoint, score: score, boardScore: boardScore, save: save, restore: restore, result: result,
    seconds: seconds, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = UntangleLogic; else self.UntangleLogic = UntangleLogic;
})();
