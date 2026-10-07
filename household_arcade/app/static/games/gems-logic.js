/* Household Arcade — Gem Swap rules (pure: no DOM, deterministic for a given seed).

   An 8 × 8 board of gems of up to six kinds (each kind its own shape and colour). Swap two neighbours to
   make a line of three or more of a kind; the line clears, the gems above fall, new gems drop in from the
   top (from the seed), and any new lines clear too (a cascade: each step counts one more, up to ×5). A swap
   that makes no line swaps back. Special gems: four in a line leaves a line gem (it clears its whole row,
   or column, when it goes), an L or T shape a blast gem (the 3 × 3 around it), five in a line a star (swap
   it with any gem to clear every gem of that kind; two stars clear the board). Locked gems can't be moved;
   a line through one breaks the lock. With no move left the board is shuffled.
   Timed: 90 seconds. Moves: the level list — reach each level's points (and break its locks) within its
   moves. Zen: no clock, no end. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var N = 8, CELL = 28, X0 = 8, Y0 = 58, FALL_V = 4;
  var SWAP_T = 8, CLEAR_T = 12, SHUFFLE_T = 40, LEVEL_T = 90, TIMED = 90 * 60, MAX_CHAIN = 5, MAX_LEVEL = 100;
  var GEM_POINTS = 10, LINE_BONUS = 50, BLAST_BONUS = 100, STAR_BONUS = 200, MOVE_BONUS = 50;
  var LINE_H = 1, LINE_V = 2, BLAST = 3, STAR = 4;
  var MODES = { timed: { kinds: 6 }, moves: {}, zen: { kinds: 6 } };

  // Levels for Moves (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A level is { name, moves, goal (points to make in it), kinds (4–6), locks: 8 rows of "." and "L" }.
  var EMPTY = ["........", "........", "........", "........", "........", "........", "........", "........"];
  var LEVELS = [
    { name: "First sparkle", moves: 20, goal: 600, kinds: 4, locks: EMPTY },
    { name: "Five colours", moves: 20, goal: 900, kinds: 5, locks: EMPTY },
    { name: "Middle lock", moves: 22, goal: 900, kinds: 5, locks: ["........", "........", "........", "...LL...", "...LL...", "........", "........", "........"] },
    { name: "Full set", moves: 25, goal: 1300, kinds: 6, locks: EMPTY },
    { name: "Four spots", moves: 25, goal: 1200, kinds: 5, locks: ["........", "........", "..L..L..", "........", "........", "..L..L..", "........", "........"] },
    { name: "Ring road", moves: 30, goal: 1400, kinds: 6, locks: ["........", "........", "...LL...", "..L..L..", "..L..L..", "...LL...", "........", "........"] },
    { name: "Low shelf", moves: 26, goal: 1400, kinds: 6, locks: ["........", "........", "........", "........", "........", "........", "..LLLL..", "........"] },
    { name: "Treasure box", moves: 34, goal: 1800, kinds: 6, locks: ["........", "........", "..LLLL..", "..L..L..", "..L..L..", "..LLLL..", "........", "........"] },
  ];
  function locksIn(rows) { var n = 0; for (var r = 0; r < N; r++) for (var c = 0; c < N; c++) if (rows[r][c] === "L") n++; return n; }
  /** Why a level can't be played, or null. */
  function levelProblem(l) {
    if (l.goal > l.moves * 80) return "the goal needs more moves (at most 80 points a move)";
    if (locksIn(l.locks) * 2 > l.moves) return "too many locks for the moves (at most one for every two moves)";
    return null;
  }
  function isInt(v, lo, hi) { return typeof v === "number" && v === Math.floor(v) && v >= lo && v <= hi; }
  /** The levels to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (l) {
      if (!l || typeof l !== "object" || typeof l.name !== "string" || !l.name) return false;
      if (!isInt(l.moves, 10, 40) || !isInt(l.goal, 300, 6000) || !isInt(l.kinds, 4, 6)) return false;
      if (!Array.isArray(l.locks) || l.locks.length !== N) return false;
      for (var r = 0; r < N; r++) if (typeof l.locks[r] !== "string" || !/^[.L]{8}$/.test(l.locks[r])) return false;
      if (locksIn(l.locks) > 16) return false;
      return levelProblem(l) === null;
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function idx(r, c) { return r * N + c; }
  function gem(s, k) { return { k: k, sp: 0, lock: false, id: ++s.ids, fy: 0 }; }
  function kindAt(b, i) { var g = b[i]; return g && g.sp !== STAR ? g.k : -1; }

  // ---------- lines ----------
  /** Every line of three or more: [{ cells: [index …], dir: "h" | "v", k }]. */
  function findRuns(b) {
    var runs = [], r, c, k, n;
    for (r = 0; r < N; r++) {
      for (c = 0; c < N;) {
        k = kindAt(b, idx(r, c)); n = 1;
        while (k >= 0 && c + n < N && kindAt(b, idx(r, c + n)) === k) n++;
        if (k >= 0 && n >= 3) { var h = []; for (var i = 0; i < n; i++) h.push(idx(r, c + i)); runs.push({ cells: h, dir: "h", k: k }); }
        c += n;
      }
    }
    for (c = 0; c < N; c++) {
      for (r = 0; r < N;) {
        k = kindAt(b, idx(r, c)); n = 1;
        while (k >= 0 && r + n < N && kindAt(b, idx(r + n, c)) === k) n++;
        if (k >= 0 && n >= 3) { var v = []; for (var j = 0; j < n; j++) v.push(idx(r + j, c)); runs.push({ cells: v, dir: "v", k: k }); }
        r += n;
      }
    }
    return runs;
  }
  function adjacent(a, b) {
    var ra = Math.floor(a / N), ca = a % N, rb = Math.floor(b / N), cb = b % N;
    return Math.abs(ra - rb) + Math.abs(ca - cb) === 1;
  }
  function canSwap(b, a, c) { return b[a] && b[c] && !b[a].lock && !b[c].lock && adjacent(a, c); }
  function swapCells(b, a, c) { var t = b[a]; b[a] = b[c]; b[c] = t; }
  /** A move that works, or null: [a, b]. */
  function findMove(b) {
    for (var i = 0; i < N * N; i++) {
      var r = Math.floor(i / N), c = i % N, ns = [];
      if (c + 1 < N) ns.push(i + 1);
      if (r + 1 < N) ns.push(i + N);
      for (var k = 0; k < ns.length; k++) {
        var j = ns[k];
        if (!canSwap(b, i, j)) continue;
        if (b[i].sp === STAR || b[j].sp === STAR) return [i, j];
        swapCells(b, i, j);
        var ok = findRuns(b).length > 0;
        swapCells(b, i, j);
        if (ok) return [i, j];
      }
    }
    return null;
  }

  // ---------- the board ----------
  function freshKind(s, b, i) {
    var r = Math.floor(i / N), c = i % N, k, tries = 0;
    do {
      k = Math.floor(rand(s) * s.kinds);
      var bad = (c >= 2 && kindAt(b, i - 1) === k && kindAt(b, i - 2) === k) || (r >= 2 && kindAt(b, i - N) === k && kindAt(b, i - 2 * N) === k);
      if (!bad) return k;
    } while (++tries < 20);
    return k;
  }
  function fillBoard(s, locks) {
    for (var tries = 0; tries < 50; tries++) {
      var b = [];
      for (var i = 0; i < N * N; i++) { b.push(null); b[i] = gem(s, freshKind(s, b, i)); }
      if (locks) for (var r = 0; r < N; r++) for (var c = 0; c < N; c++) if (locks[r][c] === "L") b[idx(r, c)].lock = true;
      if (!findRuns(b).length && findMove(b)) return b;
    }
    return b;
  }
  /** New kinds for every plain gem that can move, until there are no lines and a move exists. */
  function shuffle(s) {
    for (var tries = 0; tries < 50; tries++) {
      for (var i = 0; i < N * N; i++) {
        var g = s.board[i];
        if (g && !g.lock && g.sp === 0) g.k = -1;
      }
      for (i = 0; i < N * N; i++) { g = s.board[i]; if (g && g.k === -1 && g.sp === 0) g.k = freshKind(s, s.board, i); }
      if (!findRuns(s.board).length && findMove(s.board)) return;
    }
    // still stuck: a new board, keeping the locks where they were
    var locks = [];
    for (var r = 0; r < N; r++) {
      var row = "";
      for (var c = 0; c < N; c++) row += s.board[idx(r, c)] && s.board[idx(r, c)].lock ? "L" : ".";
      locks.push(row);
    }
    s.board = fillBoard(s, locks);
  }

  function level(s) { return s.list ? s.list[Math.min(s.level, s.list.length) - 1] : null; }
  function startLevel(s) {
    var l = level(s);
    s.kinds = l ? l.kinds : MODES[s.mode].kinds;
    s.board = fillBoard(s, l ? l.locks : null);
    s.moves = l ? l.moves : 0; s.goal = l ? l.goal : 0; s.levelScore = 0;
    s.locksLeft = l ? locksIn(l.locks) : 0;
    s.phase = "idle"; s.t = 0; s.sel = -1; s.dying = []; s.make = []; s.chain = 0; s.hint = null;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "timed";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, ids: 0, level: 1, score: 0, over: false, won: false, updates: 0,
      board: [], kinds: 6, phase: "idle", t: 0, swap: null, sel: -1, cur: idx(4, 3), chain: 0, dying: [], make: [],
      moves: 0, goal: 0, levelScore: 0, locksLeft: 0, time: mode === "timed" ? TIMED : 0, hint: null, lastGain: 0,
      list: mode === "moves" ? usableLevels(o.levels) : null,
      stats: { swaps: 0, gems: 0, bestChain: 0, lines: 0, blasts: 0, stars: 0, locks: 0, shuffles: 0, levels: 0, cause: null },
    };
    startLevel(s);
    return s;
  }

  // ---------- clearing ----------
  function startClear(s, runs, moved, extra, evs) {
    var clear = {}, make = [], i, j, k, inH = {}, inV = {};
    for (i = 0; i < runs.length; i++) {
      for (j = 0; j < runs[i].cells.length; j++) {
        clear[runs[i].cells[j]] = true;
        (runs[i].dir === "h" ? inH : inV)[runs[i].cells[j]] = i;
      }
    }
    if (extra) for (i = 0; i < extra.length; i++) clear[extra[i]] = true;
    // the special gems these lines leave behind
    var used = {};
    for (var cell in inH) {
      if (cell in inV) {
        var a = runs[inH[cell]], b = runs[inV[cell]];
        if (a.k !== b.k || used[inH[cell]] || used[inV[cell]]) continue;
        used[inH[cell]] = used[inV[cell]] = true;
        make.push({ at: Number(cell), sp: a.cells.length >= 5 || b.cells.length >= 5 ? STAR : BLAST, k: a.k });
      }
    }
    for (i = 0; i < runs.length; i++) {
      if (used[i] || runs[i].cells.length < 4) continue;
      var run = runs[i], pivot = run.cells[1];
      for (j = 0; j < run.cells.length; j++) if (moved && moved.indexOf(run.cells[j]) >= 0) pivot = run.cells[j];
      make.push({ at: pivot, sp: run.cells.length >= 5 ? STAR : run.dir === "h" ? LINE_H : LINE_V, k: run.k });
    }
    // specials that go off take more gems with them (each once)
    var queue = Object.keys(clear).map(Number), fired = {};
    while (queue.length) {
      var at = queue.shift(), g = s.board[at];
      if (!g || !g.sp || fired[at]) continue;
      fired[at] = true;
      var more = blastCells(s, at, g);
      for (k = 0; k < more.length; k++) if (!clear[more[k]]) { clear[more[k]] = true; queue.push(more[k]); }
    }
    for (i = 0; i < make.length; i++) delete clear[make[i].at];
    var cells = Object.keys(clear).map(Number).filter(function (c) { return s.board[c]; }).sort(function (x, y) { return x - y; });
    s.chain++;
    s.stats.bestChain = Math.max(s.stats.bestChain, s.chain);
    var gain = cells.length * GEM_POINTS * Math.min(s.chain, MAX_CHAIN);
    for (i = 0; i < make.length; i++) {
      gain += make[i].sp === STAR ? STAR_BONUS : make[i].sp === BLAST ? BLAST_BONUS : LINE_BONUS;
      if (make[i].sp === STAR) s.stats.stars++; else if (make[i].sp === BLAST) s.stats.blasts++; else s.stats.lines++;
    }
    for (i = 0; i < cells.length; i++) if (s.board[cells[i]].lock) { s.board[cells[i]].lock = false; s.locksLeft--; s.stats.locks++; }
    for (i = 0; i < make.length; i++) if (s.board[make[i].at] && s.board[make[i].at].lock) { s.board[make[i].at].lock = false; s.locksLeft--; s.stats.locks++; }
    s.score += gain; s.levelScore += gain; s.lastGain = gain; s.stats.gems += cells.length;
    s.dying = cells; s.make = make; s.phase = "clear"; s.t = CLEAR_T;
    evs.push({ type: s.chain > 1 ? "cascade" : "match", chain: s.chain, gems: cells.length, score: s.score });
    if (make.length) evs.push({ type: "special", count: make.length });
  }
  /** The cells a special gem takes with it. */
  function blastCells(s, at, g) {
    var r = Math.floor(at / N), c = at % N, out = [], i, j;
    if (g.sp === LINE_H) for (i = 0; i < N; i++) out.push(idx(r, i));
    else if (g.sp === LINE_V) for (i = 0; i < N; i++) out.push(idx(i, c));
    else if (g.sp === BLAST) { for (i = -1; i <= 1; i++) for (j = -1; j <= 1; j++) if (r + i >= 0 && r + i < N && c + j >= 0 && c + j < N) out.push(idx(r + i, c + j)); }
    else if (g.sp === STAR) {
      var count = [], best = 0;
      for (i = 0; i < N * N; i++) { var k = kindAt(s.board, i); if (k >= 0) count[k] = (count[k] || 0) + 1; }
      for (i = 0; i < count.length; i++) if ((count[i] || 0) > (count[best] || 0)) best = i;
      for (i = 0; i < N * N; i++) if (kindAt(s.board, i) === best) out.push(i);
    }
    return out;
  }
  /** Remove what cleared, place the new specials, let everything fall and fill the gaps from the top. */
  function settle(s) {
    var i;
    for (i = 0; i < s.dying.length; i++) s.board[s.dying[i]] = null;
    for (i = 0; i < s.make.length; i++) {
      var m = s.make[i], g = s.board[m.at] || gem(s, m.k);
      g.sp = m.sp; g.k = m.sp === STAR ? -1 : m.k; g.lock = false;
      s.board[m.at] = g;
    }
    s.dying = []; s.make = [];
    for (var c = 0; c < N; c++) {
      var write = N - 1;
      for (var r = N - 1; r >= 0; r--) {
        var gg = s.board[idx(r, c)];
        if (!gg) continue;
        if (write !== r) { s.board[idx(write, c)] = gg; s.board[idx(r, c)] = null; gg.fy += (write - r) * CELL; }
        write--;
      }
      var empty = write + 1;
      for (var e = write; e >= 0; e--) {
        var ng = gem(s, Math.floor(rand(s) * s.kinds));
        ng.fy = empty * CELL;
        s.board[idx(e, c)] = ng;
      }
    }
    s.phase = "fall";
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [], i;
    if (s.over) return evs;
    s.updates++;
    if (s.hint && --s.hint.t <= 0) s.hint = null;
    if (s.mode === "timed" && s.time > 0) {
      s.time--;
      if (s.time === 600) evs.push({ type: "hurry" });
    }
    if (s.phase === "swap" || s.phase === "back") {
      if (--s.t > 0) return evs;
      if (s.phase === "back") { s.phase = "idle"; s.swap = null; return evs; }   // the board was never changed
      var a = s.swap[0], b = s.swap[1];
      swapCells(s.board, a, b);
      var ga = s.board[a], gb = s.board[b], star = ga.sp === STAR ? a : gb.sp === STAR ? b : -1;
      var runs = findRuns(s.board);
      if (star < 0 && !runs.length) {
        swapCells(s.board, a, b);                 // no line: it swaps back
        s.phase = "back"; s.t = SWAP_T;
        evs.push({ type: "nope" });
        return evs;
      }
      s.stats.swaps++;
      if (s.list) s.moves--;
      s.chain = 0; s.swap = null;
      var extra = null;
      if (star >= 0) {
        var other = star === a ? b : a, og = s.board[other];
        extra = [star];
        if (og.sp === STAR) for (i = 0; i < N * N; i++) extra.push(i);
        else for (i = 0; i < N * N; i++) if (kindAt(s.board, i) === og.k) extra.push(i);
        s.board[star].sp = 0;                    // the star itself goes (it isn't set off again)
        s.board[star].k = -2;
      }
      startClear(s, runs, [a, b], extra, evs);
      return evs;
    }
    if (s.phase === "clear") {
      if (--s.t <= 0) settle(s);
      return evs;
    }
    if (s.phase === "fall") {
      var moving = false;
      for (i = 0; i < N * N; i++) {
        var g = s.board[i];
        if (g && g.fy > 0) { g.fy = Math.max(0, g.fy - FALL_V); moving = true; }
      }
      if (moving) return evs;
      var more = findRuns(s.board);
      if (more.length) { startClear(s, more, null, null, evs); return evs; }
      endOfMove(s, evs);
      return evs;
    }
    if (s.phase === "shuffle") {
      if (--s.t <= 0) s.phase = "idle";
      return evs;
    }
    if (s.phase === "level") {
      if (--s.t <= 0) {
        s.level = Math.min(MAX_LEVEL, s.level + 1);
        startLevel(s);
        evs.push({ type: "level", level: s.level });
      }
      return evs;
    }
    // idle
    if (s.mode === "timed" && s.time <= 0) {
      s.over = true; s.stats.cause = "time";
      evs.push({ type: "gameover", score: s.score, cause: "time" });
    }
    return evs;
  }

  function endOfMove(s, evs) {
    s.phase = "idle"; s.chain = 0;
    var l = level(s);
    if (l) {
      if (s.levelScore >= s.goal && s.locksLeft <= 0) {
        var bonus = s.moves * MOVE_BONUS;
        s.score += bonus; s.stats.levels++;
        evs.push({ type: "clear", level: s.level, bonus: bonus, score: s.score });
        if (s.level >= s.list.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          evs.push({ type: "win", score: s.score });
          return;
        }
        s.phase = "level"; s.t = LEVEL_T; s.lastGain = bonus;
        return;
      }
      if (s.moves <= 0) {
        s.over = true; s.stats.cause = "moves";
        evs.push({ type: "gameover", score: s.score, cause: "moves" });
        return;
      }
    }
    if (!findMove(s.board)) {
      shuffle(s); s.stats.shuffles++;
      s.phase = "shuffle"; s.t = SHUFFLE_T;
      evs.push({ type: "shuffle" });
    }
  }

  // ---------- input ----------
  function trySwap(s, a, b, evs) {
    if (s.phase !== "idle" || s.over || (s.mode === "timed" && s.time <= 0)) return false;
    if (b < 0 || b >= N * N || !canSwap(s.board, a, b)) return false;
    s.swap = [a, b]; s.phase = "swap"; s.t = SWAP_T; s.sel = -1; s.hint = null;
    evs.push({ type: "swap" });
    return true;
  }
  function neighbour(i, action) {
    var r = Math.floor(i / N), c = i % N;
    if (action === "up") return r > 0 ? i - N : -1;
    if (action === "down") return r < N - 1 ? i + N : -1;
    if (action === "left") return c > 0 ? i - 1 : -1;
    if (action === "right") return c < N - 1 ? i + 1 : -1;
    return -1;
  }
  function press(s, action, down) {
    var evs = [];
    if (s.over || !down) return evs;
    if (action === "up" || action === "down" || action === "left" || action === "right") {
      if (s.sel >= 0) {
        var to = neighbour(s.sel, action);
        if (to >= 0) { var from = s.sel; if (!trySwap(s, from, to, evs)) s.sel = -1; else s.cur = to; }
      } else {
        var n = neighbour(s.cur, action);
        if (n >= 0) { s.cur = n; evs.push({ type: "cursor" }); }
      }
    } else if (action === "fire") {
      if (s.sel >= 0) s.sel = -1;
      else if (s.board[s.cur] && !s.board[s.cur].lock) { s.sel = s.cur; evs.push({ type: "pick" }); }
    } else if (action === "alt") {
      var mv = s.phase === "idle" ? findMove(s.board) : null;
      if (mv) { s.hint = { a: mv[0], b: mv[1], t: 120 }; evs.push({ type: "hint" }); }
    }
    return evs;
  }
  /** A finger on cell i: pick it, or swap it with the one picked before when they are neighbours. */
  function tap(s, i) {
    var evs = [];
    if (s.over || i < 0 || i >= N * N) return evs;
    s.cur = i;
    if (s.sel >= 0 && s.sel !== i && adjacent(s.sel, i)) { trySwap(s, s.sel, i, evs); return evs; }
    if (s.sel === i) { s.sel = -1; return evs; }
    s.sel = s.board[i] && !s.board[i].lock ? i : -1;
    if (s.sel >= 0) evs.push({ type: "pick" });
    return evs;
  }
  /** A finger dragged from cell i toward a neighbour. */
  function drag(s, i, action) {
    var evs = [];
    if (s.over) return evs;
    var to = neighbour(i, action);
    if (to >= 0) { s.sel = i; trySwap(s, i, to, evs); if (s.phase !== "swap") s.sel = -1; else s.cur = to; }
    return evs;
  }
  function cellAt(x, y) {
    var c = Math.floor((x - X0) / CELL), r = Math.floor((y - Y0) / CELL);
    return r >= 0 && r < N && c >= 0 && c < N ? idx(r, c) : -1;
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "list") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.board) && data.board.length === N * N &&
      typeof data.score === "number" && typeof data.level === "number" && typeof data.rng === "number" &&
      typeof data.phase === "string" && data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.list = s.mode === "moves" ? usableLevels(levels) : null;
    if (s.list && s.level > s.list.length) s.level = s.list.length;
    if (!Array.isArray(s.dying)) s.dying = [];
    if (!Array.isArray(s.make)) s.make = [];
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { swaps: s.stats.swaps, gems: s.stats.gems, bestChain: s.stats.bestChain,
      lines: s.stats.lines, blasts: s.stats.blasts, stars: s.stats.stars, locks: s.stats.locks, shuffles: s.stats.shuffles,
      levels: s.stats.levels, won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var GemsLogic = {
    N: N, CELL: CELL, X0: X0, Y0: Y0, FALL_V: FALL_V, SWAP_T: SWAP_T, CLEAR_T: CLEAR_T, LEVEL_T: LEVEL_T, TIMED: TIMED,
    MAX_CHAIN: MAX_CHAIN, GEM_POINTS: GEM_POINTS, LINE_BONUS: LINE_BONUS, BLAST_BONUS: BLAST_BONUS, STAR_BONUS: STAR_BONUS,
    MOVE_BONUS: MOVE_BONUS, LINE_H: LINE_H, LINE_V: LINE_V, BLAST: BLAST, STAR: STAR, MODES: MODES,
    STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels, levelProblem: levelProblem, locksIn: locksIn,
    level: level, findRuns: findRuns, findMove: findMove, canSwap: canSwap, idx: idx, cellAt: cellAt, neighbour: neighbour,
    create: create, step: step, press: press, tap: tap, drag: drag, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = GemsLogic; else self.GemsLogic = GemsLogic;
})();
