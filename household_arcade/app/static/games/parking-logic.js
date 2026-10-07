/* Household Arcade — Car Park rules (pure: no DOM, deterministic for a given seed). spec/GAMES.md "Wave 10".

   A 6 × 6 car park with one exit on the right of the third row. Cars (2 long) and lorries (3 long) lie across or
   along and only move forwards and backwards along their length. Slide them to get the red car (vehicle 0) out of
   the exit. Sliding one vehicle any number of squares is one move (sliding the same one again straight after counts
   as the same move).
   Boards: vehicles placed from the seed, every position they can reach worked out (at most MAX_STATES), each
   position's fewest moves to free the red car found by a search outwards from the solved positions, and the
   position closest to the board's target chosen — so the fewest moves is known exactly. Modes: Little (gentle),
   Classic, Hard and Levels (200 numbered boards, board n from the seed "parking-level-n", carrying on, SPEC §14).
   One board's score: 10,000 − 5 × seconds − 50 × (moves − fewest) − 200 × hints, at least 10, only when solved. */
(function () {
  "use strict";

  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, SEC_COST = 5, MOVE_COST = 50, HINT_COST = 200;
  var N = 6, EXIT_ROW = 2, LEVEL_COUNT = 200, CLEAR_T = 80, MAX_STATES = 12000, TRIES = 24, STATE_VERSION = 1;
  var MODES = {
    little: { lo: 2, hi: 5, cars: [3, 5] },
    classic: { lo: 8, hi: 14, cars: [7, 10] },
    hard: { lo: 15, hi: 25, cars: [9, 13] },
    levels: { levels: true },
    book: { book: true },
  };
  var LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

  // The puzzle book (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A car park is six
  // rows of six letters: "A" the red car (across the exit row), each other letter one vehicle, "." empty. The server
  // (level_kinds/parking.py) checks the same drawing and that the red car can get out.
  var LEVELS = [
    {"name": "Quick exit", "grid": ["......", "..BCCD", "AAB..D", "......", "......", "......"]},
    {"name": "Morning rush", "grid": ["......", "..BCCD", "AABE.D", "FF.E..", "...EGG", "...HH."]},
    {"name": "Lorry in the way", "grid": ["....B.", "...CBD", ".AACBD", "..EEE.", "......", "...FF."]},
    {"name": "Market day", "grid": ["BBCCDE", "...FDE", "GAAF.E", "GH....", ".H.III", ".H..JJ"]},
    {"name": "Full house", "grid": ["...BCC", ".D.BE.", "FDAAE.", "FGGGHH", "IIJKKL", "..J..L"]},
    {"name": "Rush hour", "grid": ["...BCC", "DE.BF.", "DEAAF.", ".GHHF.", ".GIJJJ", "KKI.LL"]},
  ];

  /** The vehicles and their places from a drawing ({ vs, pos }), or null when it isn't a proper car park. */
  function fromGrid(grid) {
    if (!Array.isArray(grid) || grid.length !== N) return null;
    var cells = {}, r, c;
    for (r = 0; r < N; r++) {
      if (typeof grid[r] !== "string" || grid[r].length !== N) return null;
      for (c = 0; c < N; c++) {
        var ch = grid[r][c];
        if (ch === ".") continue;
        if (LETTERS.indexOf(ch) < 0) return null;
        (cells[ch] = cells[ch] || []).push([r, c]);
      }
    }
    if (!cells.A) return null;
    var names = Object.keys(cells).sort(function (a, b) { return a === "A" ? -1 : b === "A" ? 1 : a < b ? -1 : 1; });
    var vs = [], pos = [];
    for (var i = 0; i < names.length; i++) {
      var sq = cells[names[i]], len = sq.length;
      if (len !== 2 && len !== 3) return null;
      var across = sq.every(function (q) { return q[0] === sq[0][0]; }), along = sq.every(function (q) { return q[1] === sq[0][1]; });
      if (across && sq[len - 1][1] - sq[0][1] === len - 1) { vs.push({ h: true, len: len, lane: sq[0][0] }); pos.push(sq[0][1]); }
      else if (along && sq[len - 1][0] - sq[0][0] === len - 1) { vs.push({ h: false, len: len, lane: sq[0][1] }); pos.push(sq[0][0]); }
      else return null;
    }
    if (!(vs[0].h && vs[0].len === 2 && vs[0].lane === EXIT_ROW) || solved(pos)) return null;
    return { vs: vs, pos: pos };
  }
  function levelProblem(l) {
    if (!l || typeof l !== "object" || typeof l.name !== "string") return "not a car park";
    return fromGrid(l.grid) ? null : "drawing";
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
  function levelSpec(n) {
    var lo = Math.min(22, 2 + Math.floor((n - 1) / 8));
    return { lo: lo, hi: lo + 3, cars: [Math.min(12, 3 + Math.floor(n / 18)), Math.min(13, 5 + Math.floor(n / 15))] };
  }

  // A vehicle: { h: across?, len, lane (row if across, column if along) }; a position: the list of each one's
  // first square along its lane.
  function cellsOf(v, p) {
    var out = [];
    for (var k = 0; k < v.len; k++) out.push(v.h ? v.lane * N + p + k : (p + k) * N + v.lane);
    return out;
  }
  function occupancy(vs, pos, skip) {
    var g = new Int8Array(N * N).fill(-1);
    for (var i = 0; i < vs.length; i++) {
      if (i === skip) continue;
      var c = cellsOf(vs[i], pos[i]);
      for (var k = 0; k < c.length; k++) g[c[k]] = i;
    }
    return g;
  }
  /** How far vehicle i can slide: [lowest start, highest start] with the others where they are. */
  function range(vs, pos, i) {
    var g = occupancy(vs, pos, i), v = vs[i], lo = pos[i], hi = pos[i];
    function free(p) { var c = cellsOf(v, p); for (var k = 0; k < c.length; k++) if (g[c[k]] >= 0) return false; return true; }
    while (lo - 1 >= 0 && free(lo - 1)) lo--;
    while (hi + 1 + v.len <= N && free(hi + 1)) hi++;
    return [lo, hi];
  }
  function key(pos) { var k = 0; for (var i = 0; i < pos.length; i++) k = k * N + pos[i]; return k; }
  function solved(pos) { return pos[0] === N - 2; }

  function fillGrid(vs, pos, g) {
    g.fill(-1);
    for (var i = 0; i < vs.length; i++) {
      var v = vs[i], p = pos[i];
      for (var k = 0; k < v.len; k++) g[v.h ? v.lane * N + p + k : (p + k) * N + v.lane] = i;
    }
  }
  /** Like range(), with the occupancy grid already filled. */
  function rangeIn(vs, pos, i, g) {
    var v = vs[i], lo = pos[i], hi = pos[i], c;
    while (lo > 0) { c = v.h ? v.lane * N + lo - 1 : (lo - 1) * N + v.lane; if (g[c] >= 0) break; lo--; }
    while (hi + v.len < N) { c = v.h ? v.lane * N + hi + v.len : (hi + v.len) * N + v.lane; if (g[c] >= 0) break; hi++; }
    return [lo, hi];
  }
  /** Every position reachable from `start` (up to MAX_STATES): { list, index, adj }. */
  function explore(vs, start) {
    var list = [start.slice()], index = new Map(), adj = [], g = new Int8Array(N * N);
    index.set(key(start), 0);
    for (var q = 0; q < list.length; q++) {
      var pos = list[q], nb = [];
      fillGrid(vs, pos, g);
      for (var i = 0; i < vs.length; i++) {
        var r = rangeIn(vs, pos, i, g);
        for (var p = r[0]; p <= r[1]; p++) {
          if (p === pos[i]) continue;
          var old = pos[i]; pos[i] = p;
          var k = key(pos), j = index.get(k);
          if (j === undefined && list.length < MAX_STATES) { j = list.length; list.push(pos.slice()); index.set(k, j); }
          pos[i] = old;
          if (j !== undefined) nb.push(j);
        }
      }
      adj.push(nb);
    }
    return { list: list, index: index, adj: adj };
  }
  /** Fewest moves from each position to a solved one (−1: can't be solved), searching out from the solved ones. */
  function distances(space) {
    var d = new Int32Array(space.list.length).fill(-1), queue = [];
    for (var i = 0; i < space.list.length; i++) if (solved(space.list[i])) { d[i] = 0; queue.push(i); }
    for (var q = 0; q < queue.length; q++) {
      var a = queue[q], nb = space.adj[a] || [];
      for (var k = 0; k < nb.length; k++) if (d[nb[k]] < 0) { d[nb[k]] = d[a] + 1; queue.push(nb[k]); }
    }
    return d;
  }

  function placeVehicles(s, count) {
    var vs = [{ h: true, len: 2, lane: EXIT_ROW }], pos = [pick(s, 3)];
    var g = occupancy(vs, pos, -1);
    for (var tries = 0; vs.length < count && tries < 300; tries++) {
      var h = rand(s) < 0.5, len = rand(s) < 0.75 ? 2 : 3, lane = pick(s, N), p = pick(s, N - len + 1);
      if (h && lane === EXIT_ROW) continue;              // nothing else lies across the exit row
      var v = { h: h, len: len, lane: lane }, c = cellsOf(v, p), ok = true;
      for (var k = 0; k < c.length; k++) if (g[c[k]] >= 0) { ok = false; break; }
      if (!ok) continue;
      vs.push(v); pos.push(p);
      for (k = 0; k < c.length; k++) g[c[k]] = vs.length - 1;
    }
    return { vs: vs, pos: pos };
  }

  function makeBoard(s, spec) {
    var best = null;
    for (var t = 0; t < TRIES; t++) {
      var count = spec.cars[0] + pick(s, spec.cars[1] - spec.cars[0] + 1), b = placeVehicles(s, count);
      var space = explore(b.vs, b.pos), d = distances(space), choice = -1, cd = -1, score = 1e9;
      for (var i = 0; i < d.length; i++) {
        if (d[i] < 1) continue;
        var off = d[i] < spec.lo ? spec.lo - d[i] : d[i] > spec.hi ? d[i] - spec.hi : 0;
        if (off < score || (off === score && d[i] > cd)) { score = off; choice = i; cd = d[i]; }
      }
      if (choice < 0) continue;
      if (!best || score < best.score || (score === best.score && cd > best.fewest)) {
        best = { vs: b.vs, pos: space.list[choice].slice(), fewest: cd, score: score };
      }
      if (score === 0) break;
    }
    if (!best) best = { vs: [{ h: true, len: 2, lane: EXIT_ROW }, { h: false, len: 2, lane: 4 }], pos: [0, 1], fewest: 2, score: 0 };
    var exact = fewestFrom(best.vs, best.pos);          // the search above may have stopped short of the true fewest
    if (exact > 0) best.fewest = exact;
    return { vs: best.vs, pos: best.pos, fewest: best.fewest };
  }

  /** The fewest moves from `pos` to get the red car out, searching out from `pos` (−1: not found). */
  function fewestFrom(vs, pos) {
    if (solved(pos)) return 0;
    var dist = new Map([[key(pos), 0]]), queue = [pos.slice()], g = new Int8Array(N * N);
    for (var q = 0; q < queue.length && q < MAX_STATES * 16; q++) {
      var cur = queue[q], d = dist.get(key(cur));
      fillGrid(vs, cur, g);
      for (var i = 0; i < vs.length; i++) {
        var r = rangeIn(vs, cur, i, g);
        for (var p = r[0]; p <= r[1]; p++) {
          if (p === cur[i]) continue;
          var next = cur.slice(); next[i] = p;
          var k = key(next);
          if (dist.has(k)) continue;
          if (solved(next)) return d + 1;
          dist.set(k, d + 1); queue.push(next);
        }
      }
    }
    return -1;
  }

  /** The next move of a fewest-moves answer from `pos`: { v, to } or null. */
  function nextMove(vs, pos) {
    if (solved(pos)) return null;
    var start = key(pos), seen = new Map([[start, null]]), queue = [pos.slice()], g = new Int8Array(N * N);
    for (var q = 0; q < queue.length && q < MAX_STATES * 4; q++) {
      var cur = queue[q], ck = key(cur);
      fillGrid(vs, cur, g);
      for (var i = 0; i < vs.length; i++) {
        var r = rangeIn(vs, cur, i, g);
        for (var p = r[0]; p <= r[1]; p++) {
          if (p === cur[i]) continue;
          var next = cur.slice(); next[i] = p;
          var k = key(next);
          if (seen.has(k)) continue;
          seen.set(k, { from: ck, v: i, to: p });
          if (solved(next)) {
            var step = seen.get(k);
            while (step.from !== start) step = seen.get(step.from);
            return { v: step.v, to: step.to };
          }
          queue.push(next);
        }
      }
    }
    return null;
  }

  // ---------- the game ----------
  function startBoard(s) {
    var b;
    if (s.book) {
      b = fromGrid(s.list[s.level - 1].grid);
      b.fewest = Math.max(1, fewestFrom(b.vs, b.pos));
    } else {
      if (s.levels) s.rng = hashSeed("parking-level-" + s.level);
      b = makeBoard(s, s.levels ? levelSpec(s.level) : MODES[s.mode]);
    }
    s.vs = b.vs; s.pos = b.pos; s.fewest = b.fewest; s.start = b.pos.slice();
    s.boardUpdates = 0; s.boardMoves = 0; s.boardHints = 0; s.last = -1; s.undo = [];
    s.sel = -1; s.hint = null; s.phase = "play"; s.t = 0; s.cursor = EXIT_ROW * N + b.pos[0];
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, levels: !!(MODES[mode].levels || MODES[mode].book), book: !!MODES[mode].book,
      list: MODES[mode].book ? usableLevels(o.levels) : null, rng: (o.seed >>> 0) || 1, level: 1, hintsOn: o.hints === "on",
      runScore: 0, moves: 0, hints: 0, boards: 0, updates: 0, vs: null, pos: null, fewest: 0, start: null,
      boardUpdates: 0, boardMoves: 0, boardHints: 0, last: -1, undo: [], sel: -1, hint: null, phase: "play", t: 0,
      cursor: 0, won: false, over: false, cause: null, lastBoardScore: 0, noHintAt: -1000, fewestTotal: 0,
    };
    if (s.levels) s.level = startAt(o.startLevel, total(s));
    startBoard(s);
    return s;
  }

  function boardScore(s) {
    return Math.max(MIN_SCORE, TOP - SEC_COST * Math.floor(s.boardUpdates / UPS) - MOVE_COST * Math.max(0, s.boardMoves - s.fewest) - HINT_COST * s.boardHints);
  }
  function score(s) { return s.runScore; }
  function vehicleAt(s, cell) { return occupancy(s.vs, s.pos, -1)[cell]; }

  /** Slide vehicle i to start square p (if it can get there). */
  function slide(s, i, p) {
    var evs = [];
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.vs.length) || p === s.pos[i]) return evs;
    var r = range(s.vs, s.pos, i);
    if (p < r[0] || p > r[1]) return [{ type: "nope" }];
    s.undo.push({ pos: s.pos.slice(), last: s.last });
    if (s.undo.length > 200) s.undo.shift();
    s.pos[i] = p;
    if (s.last !== i) { s.moves++; s.boardMoves++; s.last = i; }
    s.hint = null;
    evs.push({ type: "slide", vehicle: i });
    if (solved(s.pos)) {
      s.lastBoardScore = boardScore(s);
      s.runScore += s.lastBoardScore; s.boards++; s.fewestTotal += s.fewest;
      evs.push({ type: "clear", level: s.level, moves: s.boardMoves, fewest: s.fewest });
      if (!s.levels || s.level >= total(s)) { s.won = true; s.over = true; s.cause = "won"; evs.push({ type: "win", score: s.runScore }); }
      else { s.phase = "clear"; s.t = CLEAR_T; }
    }
    return evs;
  }
  function undo(s) {
    if (s.over || s.phase !== "play" || !s.undo.length) return [];
    var u = s.undo.pop();
    s.pos = u.pos; s.last = u.last; s.hint = null;
    return [{ type: "undo" }];
  }
  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    if (s.hint) return [];
    var m = nextMove(s.vs, s.pos);
    if (!m) return [];
    s.hint = m; s.hints++; s.boardHints++;
    return [{ type: "hint", vehicle: m.v }];
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "play") s.boardUpdates++;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      startBoard(s);
      evs.push({ type: "level", level: s.level });
    }
    return evs;
  }

  /** Keyboard: arrows move the cursor (or, with a vehicle picked, slide it one square); Space picks / lets go. */
  function press(s, action, down) {
    if (!down || s.over) return [];
    var r = Math.floor(s.cursor / N), c = s.cursor % N;
    if (action === "alt" || action === "hint") return hint(s);
    if (action === "undo") return undo(s);
    if (action === "fire") {
      if (s.sel >= 0) { s.sel = -1; return [{ type: "cursor" }]; }
      var v = vehicleAt(s, s.cursor);
      if (v >= 0) { s.sel = v; return [{ type: "pick" }]; }
      return [];
    }
    var dx = action === "left" ? -1 : action === "right" ? 1 : 0, dy = action === "up" ? -1 : action === "down" ? 1 : 0;
    if (!dx && !dy) return [];
    if (s.sel >= 0) {
      var veh = s.vs[s.sel];
      if ((veh.h && dy) || (!veh.h && dx)) return [{ type: "nope" }];
      var evs = slide(s, s.sel, s.pos[s.sel] + (veh.h ? dx : dy));
      if (evs.length && evs[0].type === "slide") s.cursor = Math.max(0, Math.min(N * N - 1, s.cursor + dx + dy * N));
      if (s.phase !== "play") s.sel = -1;
      return evs;
    }
    s.cursor = ((r + dy + N) % N) * N + (c + dx + N) % N;
    return [{ type: "cursor" }];
  }

  function save(s) { var out = JSON.parse(JSON.stringify(s)); out.list = null; return out; }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.vs) && Array.isArray(data.pos) &&
      data.vs.length === data.pos.length && data.vs.length >= 1 && typeof data.level === "number" && data.level >= 1 &&
      typeof data.runScore === "number" && typeof data.updates === "number" && typeof data.rng === "number" &&
      data.vs.every(function (v, i) { return v && (v.len === 2 || v.len === 3) && v.lane >= 0 && v.lane < N && data.pos[i] >= 0 && data.pos[i] + v.len <= N; });
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.book = data.mode === "book"; s.list = s.book ? usableLevels(levels) : null;
    if (s.level > total(s)) throw new Error("That saved game can't be continued.");
    var g = new Int8Array(N * N).fill(-1);
    for (var i = 0; i < s.vs.length; i++) {
      var c = cellsOf(s.vs[i], s.pos[i]);
      for (var k = 0; k < c.length; k++) { if (g[c[k]] >= 0) throw new Error("That saved game can't be continued."); g[c[k]] = i; }
    }
    if (!Array.isArray(s.undo)) s.undo = [];
    s.sel = -1; s.hint = null;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = [];
    if (s.boards) lines.push(s.levels ? "Car parks cleared: " + s.boards : "Out in " + s.moves + (s.moves === 1 ? " move" : " moves") + " (fewest " + s.fewestTotal + ")");
    if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints"));
    lines.push("Time " + clock(seconds(s)));
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.cause, mode: s.mode, moves: s.moves, hints: s.hints, boards: s.boards, fewest: s.fewestTotal,
        summary: lines, notSaved: s.boards ? undefined : "Not out yet — not saved" },
    };
  }

  var ParkingLogic = {
    UPS: UPS, TOP: TOP, N: N, EXIT_ROW: EXIT_ROW, LEVEL_COUNT: LEVEL_COUNT, LEVELS: LEVELS, usableLevels: usableLevels, levelProblem: levelProblem,
    fromGrid: fromGrid, fewestFrom: fewestFrom, total: total, MIN_SCORE: MIN_SCORE, SEC_COST: SEC_COST, MOVE_COST: MOVE_COST,
    HINT_COST: HINT_COST, CLEAR_T: CLEAR_T, STATE_VERSION: STATE_VERSION, MODES: MODES, create: create, step: step, press: press,
    slide: slide, undo: undo, hint: hint, range: range, cellsOf: cellsOf, occupancy: occupancy, explore: explore,
    distances: distances, nextMove: nextMove, solved: solved, makeBoard: makeBoard, levelSpec: levelSpec, hashSeed: hashSeed,
    vehicleAt: vehicleAt, score: score, boardScore: boardScore, save: save, restore: restore, result: result,
    seconds: seconds, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = ParkingLogic; else self.ParkingLogic = ParkingLogic;
})();
