/* Household Arcade — Dot Connect rules (pure: no DOM, deterministic for a given seed). spec/GAMES.md "Wave 10".

   Pairs of coloured dots on an n × n grid. Draw a line from each dot to its partner through squares next to each
   other; lines can't cross, and drawing over another line cuts it there. Join every pair and fill every square
   (Little: joining every pair is enough).
   Boards: a path through every square made from the seed (a zig-zag bent at random many times by "backbite" moves),
   cut into pieces 3 squares or longer; each piece's ends are a pair of dots, so every board can be joined and
   filled. The pieces are kept as the answer the hint draws from. Modes: Little 5 × 5 (gentle), 7 × 7, 9 × 9 and
   Levels (200 numbered boards, 5 × 5 growing to 12 × 12, board n from the seed "connect-level-n", carrying on,
   SPEC §14). One board's score: 10,000 − 10 × seconds − 200 × hints, at least 10, only when finished. */
(function () {
  "use strict";

  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, SEC_COST = 10, HINT_COST = 200, LEVELS = 200, CLEAR_T = 80, STATE_VERSION = 1;
  var MODES = { little: { n: 5, fill: false }, classic: { n: 7, fill: true }, big: { n: 9, fill: true }, levels: { levels: true } };

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
  function levelSize(n) { return Math.min(12, 5 + Math.floor((n - 1) / 15)); }

  function neighbours(n, i) {
    var r = Math.floor(i / n), c = i % n, out = [];
    if (r > 0) out.push(i - n);
    if (c < n - 1) out.push(i + 1);
    if (r < n - 1) out.push(i + n);
    if (c > 0) out.push(i - 1);
    return out;
  }
  function adjacent(n, a, b) { return neighbours(n, a).indexOf(b) >= 0; }

  /** A path through every square: a zig-zag, then many random backbite moves. */
  function hamiltonian(s, n) {
    var path = [], r, c;
    for (r = 0; r < n; r++) for (c = 0; c < n; c++) path.push(r * n + (r % 2 ? n - 1 - c : c));
    var N = n * n, pos = new Array(N);
    for (var i = 0; i < N; i++) pos[path[i]] = i;
    function reverse(a, b) {
      while (a < b) { var t = path[a]; path[a] = path[b]; path[b] = t; pos[path[a]] = a; pos[path[b]] = b; a++; b--; }
    }
    var moves = N * 30;
    for (var m = 0; m < moves; m++) {
      var atEnd = rand(s) < 0.5, end = atEnd ? path[N - 1] : path[0], nb = neighbours(n, end), v = nb[pick(s, nb.length)];
      var j = pos[v];
      if (atEnd) { if (j === N - 2) continue; reverse(j + 1, N - 1); }
      else { if (j === 1) continue; reverse(0, j - 1); }
    }
    return path;
  }
  /** Cut the path into pieces of 3 or more squares: about n pairs. */
  function cut(s, path, n) {
    var N = path.length, pairs = Math.max(3, n + pick(s, Math.max(1, Math.floor(n / 3)))), pieces = [], at = 0;
    for (var p = pairs; p > 0; p--) {
      var left = N - at;
      if (p === 1 || left < 6) { pieces.push(path.slice(at)); break; }
      var most = left - 3 * (p - 1), avg = Math.floor(left / p);
      var len = Math.max(3, Math.min(most, avg - 2 + pick(s, 5)));
      pieces.push(path.slice(at, at + len)); at += len;
    }
    return pieces.filter(function (x) { return x.length >= 2; });
  }

  function makeBoard(s, n) {
    var pieces = cut(s, hamiltonian(s, n), n), dots = new Array(n * n).fill(-1);
    for (var k = 0; k < pieces.length; k++) { dots[pieces[k][0]] = k; dots[pieces[k][pieces[k].length - 1]] = k; }
    return { n: n, pairs: pieces.length, dots: dots, answer: pieces };
  }

  // ---------- the game ----------
  function startBoard(s) {
    if (s.levels) s.rng = hashSeed("connect-level-" + s.level);
    s.n = s.levels ? levelSize(s.level) : MODES[s.mode].n;
    s.fill = s.levels ? true : MODES[s.mode].fill;
    var b = makeBoard(s, s.n);
    s.pairs = b.pairs; s.dots = b.dots; s.answer = b.answer;
    s.paths = [];
    for (var k = 0; k < s.pairs; k++) s.paths.push([]);
    s.boardUpdates = 0; s.boardHints = 0; s.pen = -1; s.phase = "play"; s.t = 0;
    s.cursor = Math.floor(s.n * s.n / 2);
  }
  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, levels: !!MODES[mode].levels, rng: (o.seed >>> 0) || 1, level: 1, hintsOn: o.hints === "on",
      runScore: 0, hints: 0, boards: 0, updates: 0, n: 5, fill: true, pairs: 0, dots: null, answer: null, paths: null,
      boardUpdates: 0, boardHints: 0, pen: -1, phase: "play", t: 0, cursor: 0, won: false, over: false, cause: null,
      lastBoardScore: 0, noHintAt: -1000, lines: 0,
    };
    if (s.levels) s.level = startAt(o.startLevel, LEVELS);
    startBoard(s);
    return s;
  }
  function boardScore(s) { return Math.max(MIN_SCORE, TOP - SEC_COST * Math.floor(s.boardUpdates / UPS) - HINT_COST * s.boardHints); }
  function score(s) { return s.runScore; }

  /** Which colour's line covers square i (−1: none). Dots count as their own colour. */
  function owner(s, i) {
    if (s.dots[i] >= 0) return s.dots[i];
    for (var k = 0; k < s.paths.length; k++) if (s.paths[k].indexOf(i) >= 0) return k;
    return -1;
  }
  function joined(s, k) {
    var p = s.paths[k];
    return p.length >= 2 && s.dots[p[0]] === k && s.dots[p[p.length - 1]] === k && p[0] !== p[p.length - 1];
  }
  function allJoined(s) { for (var k = 0; k < s.pairs; k++) if (!joined(s, k)) return false; return true; }
  function filled(s) {
    var N = s.n * s.n, used = new Uint8Array(N), i, k;
    for (i = 0; i < N; i++) if (s.dots[i] >= 0) used[i] = 1;
    for (k = 0; k < s.paths.length; k++) for (i = 0; i < s.paths[k].length; i++) used[s.paths[k][i]] = 1;
    for (i = 0; i < N; i++) if (!used[i]) return false;
    return true;
  }
  function done(s) { return allJoined(s) && (!s.fill || filled(s)); }

  /** Cut colour k's line at square i (i and after are removed). */
  function cutAt(s, k, i) {
    var p = s.paths[k], j = p.indexOf(i);
    if (j >= 0) p.length = j;
  }

  function finishCheck(s, evs) {
    if (s.phase !== "play" || !done(s)) return;
    s.pen = -1;
    s.lastBoardScore = boardScore(s);
    s.runScore += s.lastBoardScore; s.boards++;
    evs.push({ type: "clear", level: s.level });
    if (!s.levels || s.level >= LEVELS) { s.won = true; s.over = true; s.cause = "won"; evs.push({ type: "win", score: s.runScore }); }
    else { s.phase = "clear"; s.t = CLEAR_T; }
  }

  /** Start drawing at square i: a dot starts its colour again; a line's square carries on from there. */
  function begin(s, i) {
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.dots.length)) return [];
    s.cursor = i;
    var d = s.dots[i];
    if (d >= 0) { s.paths[d] = [i]; s.pen = d; return [{ type: "start", colour: d }]; }
    for (var k = 0; k < s.paths.length; k++) {
      var j = s.paths[k].indexOf(i);
      if (j >= 0) { s.paths[k].length = j + 1; s.pen = k; return [{ type: "start", colour: k }]; }
    }
    s.pen = -1;
    return [];
  }
  /** Carry the line being drawn on to square i (next to its end). */
  function extend(s, i) {
    var evs = [];
    if (s.over || s.phase !== "play" || s.pen < 0) return evs;
    var k = s.pen, p = s.paths[k], end = p[p.length - 1];
    if (i === end) return evs;
    if (p.length >= 2 && joined(s, k)) {           // already joined: only going back along it shortens it
      var back = p.indexOf(i);
      if (back >= 0) { p.length = back + 1; return [{ type: "back" }]; }
      return evs;
    }
    if (!adjacent(s.n, end, i)) return evs;
    var own = p.indexOf(i);
    if (own >= 0) { p.length = own + 1; return [{ type: "back" }]; }
    var d = s.dots[i];
    if (d >= 0 && d !== k) return [{ type: "blocked" }];
    for (var o = 0; o < s.paths.length; o++) {
      if (o !== k && s.paths[o].indexOf(i) >= 0) { cutAt(s, o, i); evs.push({ type: "cut", colour: o }); }
    }
    p.push(i);
    s.cursor = i;
    if (d === k) { s.lines++; evs.push({ type: "join", colour: k }); s.pen = -1; finishCheck(s, evs); }
    else evs.push({ type: "draw" });
    return evs;
  }
  function stop(s) { s.pen = -1; return []; }

  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    var k = -1;
    for (var a = 0; a < s.pairs; a++) {
      var want = s.answer[a].join(","), have = s.paths[a].join(","), rev = s.answer[a].slice().reverse().join(",");
      if (have !== want && have !== rev) { k = a; break; }
    }
    if (k < 0) return [];
    var sq = s.answer[k];
    for (var o = 0; o < s.paths.length; o++) {
      if (o === k) continue;
      for (var j = 0; j < sq.length; j++) if (s.paths[o].indexOf(sq[j]) >= 0) cutAt(s, o, sq[j]);
    }
    s.paths[k] = sq.slice(); s.pen = -1;
    s.hints++; s.boardHints++;
    var evs = [{ type: "hint", colour: k }];
    finishCheck(s, evs);
    return evs;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "play") s.boardUpdates++;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      startBoard(s);
      evs.push({ type: "level", level: s.level, size: s.n });
    }
    return evs;
  }

  /** Keyboard: arrows move the cursor (drawing while a line is picked); Space picks a dot or line end / lets go. */
  function press(s, action, down) {
    if (!down || s.over || !s.dots) return [];
    var n = s.n, r = Math.floor(s.cursor / n), c = s.cursor % n;
    if (action === "alt" || action === "hint") return hint(s);
    if (action === "fire") return s.pen >= 0 ? stop(s) : begin(s, s.cursor);
    var dr = action === "up" ? -1 : action === "down" ? 1 : 0, dc = action === "left" ? -1 : action === "right" ? 1 : 0;
    if (!dr && !dc) return [];
    var nr = r + dr, nc = c + dc;
    if (nr < 0 || nc < 0 || nr >= n || nc >= n) return [];
    var to = nr * n + nc;
    if (s.pen >= 0) { var evs = extend(s, to); if (!evs.length || evs[0].type === "blocked") return evs; s.cursor = to; return evs; }
    s.cursor = to;
    return [{ type: "cursor" }];
  }

  function save(s) { return JSON.parse(JSON.stringify(s)); }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && typeof data.n === "number" && data.n >= 3 && data.n <= 12 &&
      Array.isArray(data.dots) && data.dots.length === data.n * data.n && Array.isArray(data.paths) &&
      data.paths.length === data.pairs && Array.isArray(data.answer) && data.answer.length === data.pairs &&
      data.paths.every(function (p) { return Array.isArray(p) && p.every(function (i) { return i >= 0 && i < data.n * data.n; }); }) &&
      typeof data.level === "number" && data.level >= 1 && data.level <= LEVELS && typeof data.runScore === "number" &&
      typeof data.updates === "number" && typeof data.rng === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.pen = -1;
    if (!(s.cursor >= 0 && s.cursor < s.dots.length)) s.cursor = 0;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = [];
    if (s.boards) lines.push(s.levels ? "Boards finished: " + s.boards : "Finished in " + clock(seconds(s)));
    if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints"));
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.cause, mode: s.mode, hints: s.hints, boards: s.boards, size: s.n, summary: lines,
        notSaved: s.boards ? undefined : "Not finished — not saved" },
    };
  }

  var ConnectLogic = {
    UPS: UPS, TOP: TOP, LEVELS: LEVELS, MIN_SCORE: MIN_SCORE, SEC_COST: SEC_COST, HINT_COST: HINT_COST, CLEAR_T: CLEAR_T,
    STATE_VERSION: STATE_VERSION, MODES: MODES, create: create, step: step, press: press, begin: begin, extend: extend, stop: stop,
    hint: hint, owner: owner, joined: joined, allJoined: allJoined, filled: filled, done: done, makeBoard: makeBoard,
    hamiltonian: hamiltonian, levelSize: levelSize, hashSeed: hashSeed, neighbours: neighbours, adjacent: adjacent,
    score: score, boardScore: boardScore, save: save, restore: restore, result: result, seconds: seconds, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = ConnectLogic; else self.ConnectLogic = ConnectLogic;
})();
