/* Household Arcade — Arrow Release rules (pure: no DOM, deterministic for a given seed). spec/GAMES.md "Wave 9".

   A grid with arrows on it. Each arrow covers a line of cells (Twisty: a bent path) with its head at one end,
   pointing one way. Tap an arrow: if every cell from its head to the edge, in the head's direction, is empty, the
   arrow slides out head first and leaves the board; if not, it bumps — a mistake and a heart lost (Little has no
   hearts). Clear the board.
   Releasing only ever frees cells, so a board is solvable exactly when releasing free arrows one after another
   clears it; the board maker checks that and, while it gets stuck, turns a stuck arrow round (or, after many tries,
   takes it away). Modes: Little 5 × 5 (gentle), 8 × 8, 12 × 12, Twisty 10 × 10 and Levels — 200 numbered boards,
   board n made from the seed "arrows-level-n" (the same for everyone), harder as n grows, carrying on from the next
   uncleared board (SPEC §14).
   One board's score: 10,000 − 10 × seconds − 300 × mistakes − 200 × hints, at least 10, only when it is cleared; in
   Levels the boards' scores add up. Only + − × ÷ and Math.floor/abs/min/max/imul are used. One step() = 1/60 s. */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, SEC_COST = 10, MISTAKE_COST = 300, HINT_COST = 200;
  var HEARTS = 3, LEVEL_COUNT = 200, CLEAR_T = 70, FLY_T = 24, BUMP_T = 18, STATE_VERSION = 1;
  var DX = [0, 1, 0, -1], DY = [-1, 0, 1, 0];          // up, right, down, left
  var MODES = {
    little: { n: 5, min: 1, max: 2, fill: 60, twisty: false, hearts: 0 },
    classic: { n: 8, min: 1, max: 4, fill: 85, twisty: false, hearts: HEARTS },
    big: { n: 12, min: 1, max: 5, fill: 90, twisty: false, hearts: HEARTS },
    twisty: { n: 10, min: 2, max: 6, fill: 85, twisty: true, hearts: HEARTS },
    levels: { levels: true, hearts: HEARTS },
    book: { book: true, hearts: HEARTS },
  };

  // Picture boards (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A board is a shape
  // ("#" squares the arrows may cover, "." empty) that the board maker fills, with the longest arrow, how full (%)
  // and whether arrows bend. The server (level_kinds/arrows.py) has the same list and checks the same rules.
  var LEVELS = [
    {"name": "Little heart", "shape": [".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."], "longest": 2, "fill": 75, "twisty": false},
    {"name": "Fish", "shape": ["...####...", ".########.", "##########", ".########.", "...####...", "......##.."], "longest": 3, "fill": 80, "twisty": false},
    {"name": "Rocket", "shape": ["...##...", "..####..", "..####..", "..####..", "..####..", ".######.", "########", "##.##.##"], "longest": 3, "fill": 85, "twisty": false},
    {"name": "Christmas tree", "shape": ["....##....", "...####...", "..######..", "...####...", "..######..", ".########.", "##########", "....##....", "....##...."], "longest": 4, "fill": 85, "twisty": false},
    {"name": "Snake pit", "shape": ["##########", "#........#", "#.######.#", "#.#....#.#", "#.#.##.#.#", "#.#.##.#.#", "#.#....#.#", "#.######.#", "#........#", "##########"], "longest": 4, "fill": 90, "twisty": true},
    {"name": "Big smile", "shape": ["...######...", ".##########.", "############", "###..##..###", "############", "############", "##.######.##", "###.####.###", ".####..####.", "...######..."], "longest": 5, "fill": 90, "twisty": true},
  ];

  function levelProblem(l) {
    if (!l || typeof l !== "object" || typeof l.name !== "string" || !Array.isArray(l.shape)) return "not a board";
    var h = l.shape.length, w = h ? String(l.shape[0]).length : 0, sq = 0;
    if (h < 5 || h > 12 || w < 5 || w > 12) return "shape size";
    for (var r = 0; r < h; r++) {
      if (typeof l.shape[r] !== "string" || l.shape[r].length !== w || /[^#.]/.test(l.shape[r])) return "shape rows";
      for (var c = 0; c < w; c++) if (l.shape[r][c] === "#") sq++;
    }
    if (sq < 12) return "too small";
    if (!(l.longest >= 1 && l.longest <= 6 && l.longest === Math.floor(l.longest))) return "longest";
    if (!(l.fill >= 60 && l.fill <= 100 && l.fill === Math.floor(l.fill))) return "fill";
    if (typeof l.twisty !== "boolean") return "twisty";
    return null;
  }
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (l) { return levelProblem(l) === null; });
    return out.length ? out : LEVELS;
  }
  /** The board spec of a picture board: the shape centred on a square grid, the rest empty. */
  function bookSpec(l) {
    var h = l.shape.length, w = l.shape[0].length, n = Math.max(h, w), ox = Math.floor((n - w) / 2), oy = Math.floor((n - h) / 2);
    var mask = [];
    for (var i = 0; i < n * n; i++) mask.push(false);
    for (var r = 0; r < h; r++) for (var c = 0; c < w; c++) if (l.shape[r][c] === "#") mask[(r + oy) * n + c + ox] = true;
    return { n: n, min: 1, max: l.longest, fill: l.fill, twisty: l.twisty, mask: mask };
  }
  function total(s) { return s.book ? s.list.length : LEVEL_COUNT; }

  function rand(s) { // mulberry32 on s.rng
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function pick(s, n) { return Math.floor(rand(s) * n); }
  /** A number from a word ("arrows-level-12"): the seed of a numbered board, the same everywhere. */
  function hashSeed(text) {
    var h = 2166136261 | 0;
    for (var i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) || 1;
  }

  /** The board of level n of Levels: 5 × 5 growing to 12 × 12, fuller, longer and (from 40) bent arrows. */
  function levelSpec(n) {
    var size = Math.min(12, 5 + Math.floor((n - 1) / 12));
    return { n: size, min: n >= 40 ? 2 : 1, max: Math.min(6, 2 + Math.floor(n / 25)),
      fill: Math.min(95, 70 + Math.floor(n / 4)), twisty: n >= 40 && n % 2 === 0 };
  }

  // ---------- the board ----------
  function arrowDir(cells, n) {
    var h = cells[0], b = cells[1];
    var dx = (h % n) - (b % n), dy = Math.floor(h / n) - Math.floor(b / n);
    return dy < 0 ? 0 : dx > 0 ? 1 : dy > 0 ? 2 : 3;
  }
  /** Can arrow a leave the board now? Its head's way to the edge must be empty (its own body counts as in the way). */
  function isFree(b, a) {
    var arr = b.arrows[a];
    if (arr.gone) return false;
    var n = b.n, x = arr.cells[0] % n, y = Math.floor(arr.cells[0] / n);
    for (;;) {
      x += DX[arr.dir]; y += DY[arr.dir];
      if (x < 0 || y < 0 || x >= n || y >= n) return true;
      if (b.grid[y * n + x] >= 0) return false;
    }
  }
  /** The first cell in arrow a's way (or -1). */
  function blocker(b, a) {
    var arr = b.arrows[a], n = b.n, x = arr.cells[0] % n, y = Math.floor(arr.cells[0] / n);
    for (;;) {
      x += DX[arr.dir]; y += DY[arr.dir];
      if (x < 0 || y < 0 || x >= n || y >= n) return -1;
      if (b.grid[y * n + x] >= 0) return y * n + x;
    }
  }
  function remove(b, a) {
    var arr = b.arrows[a];
    arr.gone = true;
    for (var i = 0; i < arr.cells.length; i++) b.grid[arr.cells[i]] = -1;
  }
  function copyBoard(b) {
    return { n: b.n, grid: b.grid.slice(), arrows: b.arrows.map(function (a) { return { cells: a.cells.slice(), dir: a.dir, gone: a.gone }; }) };
  }
  /** Release free arrows one after another: the arrows left over (none: the board can be solved). */
  function stuckArrows(b) {
    var c = copyBoard(b), moved = true;
    while (moved) {
      moved = false;
      for (var a = 0; a < c.arrows.length; a++) if (!c.arrows[a].gone && isFree(c, a)) { remove(c, a); moved = true; }
    }
    var left = [];
    for (a = 0; a < c.arrows.length; a++) if (!c.arrows[a].gone) left.push(a);
    return left;
  }
  function solvable(b) { return stuckArrows(b).length === 0; }

  function makeBoard(s, spec) {
    var n = spec.n, N = n * n, grid = [], arrows = [], i, k;
    for (i = 0; i < N; i++) grid.push(-1);
    var order = [], mask = spec.mask, open = 0;
    for (i = 0; i < N; i++) if (!mask || mask[i]) { order.push(i); open++; }
    for (i = order.length - 1; i > 0; i--) { k = pick(s, i + 1); var t = order[i]; order[i] = order[k]; order[k] = t; }
    function ok(c) { return grid[c] < 0 && (!mask || mask[c]); }
    var filled = 0, goal = Math.floor(open * spec.fill / 100);
    for (var oi = 0; oi < order.length && filled < goal; oi++) {
      var start = order[oi];
      if (grid[start] >= 0) continue;
      var len = spec.min + pick(s, spec.max - spec.min + 1), cells = [start];
      if (spec.twisty) {
        while (cells.length < len) {
          var last = cells[cells.length - 1], lx = last % n, ly = Math.floor(last / n), opts = [];
          for (var d = 0; d < 4; d++) {
            var nx = lx + DX[d], ny = ly + DY[d];
            if (nx >= 0 && ny >= 0 && nx < n && ny < n && ok(ny * n + nx) && cells.indexOf(ny * n + nx) < 0) opts.push(ny * n + nx);
          }
          if (!opts.length) break;
          cells.push(opts[pick(s, opts.length)]);
        }
      } else {
        var dir = pick(s, 4), cx = start % n, cy = Math.floor(start / n);
        while (cells.length < len) {
          cx += DX[dir]; cy += DY[dir];
          if (cx < 0 || cy < 0 || cx >= n || cy >= n || !ok(cy * n + cx)) break;
          cells.push(cy * n + cx);
        }
      }
      if (cells.length < Math.max(1, spec.min)) continue;
      var dirOut;
      if (cells.length === 1) dirOut = pick(s, 4);
      else { cells.reverse(); dirOut = arrowDir(cells, n); }        // the head is the end the line grew to
      var id = arrows.length;
      arrows.push({ cells: cells, dir: dirOut, gone: false });
      for (i = 0; i < cells.length; i++) grid[cells[i]] = id;
      filled += cells.length;
    }
    var b = { n: n, grid: grid, arrows: arrows };
    if (mask) b.mask = mask.slice();
    // make it solvable: turn a stuck arrow round (a different one each time); after many tries take one away
    for (var tries = 0; tries < 400; tries++) {
      var stuck = stuckArrows(b);
      if (!stuck.length) break;
      var a = stuck[(tries * 7) % stuck.length], arr = b.arrows[a];
      if (tries < 300) {
        if (arr.cells.length === 1) arr.dir = (arr.dir + 1 + pick(s, 3)) % 4;
        else { arr.cells.reverse(); arr.dir = arrowDir(arr.cells, n); }
      } else remove(b, a);
    }
    // the arrows still on the board, numbered again
    var keep = b.arrows.filter(function (x) { return !x.gone; });
    for (i = 0; i < N; i++) b.grid[i] = -1;
    for (a = 0; a < keep.length; a++) for (i = 0; i < keep[a].cells.length; i++) b.grid[keep[a].cells[i]] = a;
    b.arrows = keep;
    return b;
  }

  // ---------- the game ----------
  function boardSpec(s) { return s.book ? bookSpec(s.list[s.level - 1]) : s.levels ? levelSpec(s.level) : MODES[s.mode]; }
  function startBoard(s) {
    if (s.book) { var l = s.list[s.level - 1]; s.rng = hashSeed("arrows-book-" + l.shape.join("/") + "-" + l.longest + "-" + l.fill + "-" + l.twisty); }
    else if (s.levels) s.rng = hashSeed("arrows-level-" + s.level);
    s.board = makeBoard(s, boardSpec(s));
    s.boardUpdates = 0; s.boardMistakes = 0; s.boardHints = 0;
    s.phase = "play"; s.t = 0; s.hint = -1; s.flying = []; s.bump = null;
    s.cursor = Math.floor(s.board.n * s.board.n / 2);
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic", m = MODES[mode];
    var s = {
      mode: mode, levels: !!(m.levels || m.book), book: !!m.book, list: m.book ? usableLevels(o.levels) : null, rng: (o.seed >>> 0) || 1, level: 1, hearts: m.hearts, maxHearts: m.hearts,
      hintsOn: o.hints === "on", runScore: 0, released: 0, mistakes: 0, hints: 0, boards: 0, updates: 0,
      board: null, boardUpdates: 0, boardMistakes: 0, boardHints: 0, phase: "play", t: 0, hint: -1, cursor: 0,
      flying: [], bump: null, won: false, over: false, cause: null, lastBoardScore: 0, noHintAt: -1000,
    };
    if (s.levels) s.level = startAt(o.startLevel, total(s));
    startBoard(s);
    return s;
  }

  function left(s) { var n = 0; for (var a = 0; a < s.board.arrows.length; a++) if (!s.board.arrows[a].gone) n++; return n; }
  function boardScore(s) {
    return Math.max(MIN_SCORE, TOP - SEC_COST * Math.floor(s.boardUpdates / UPS) - MISTAKE_COST * s.boardMistakes - HINT_COST * s.boardHints);
  }
  function score(s) { return s.runScore; }

  function boardCleared(s, evs) {
    s.lastBoardScore = boardScore(s);
    s.runScore += s.lastBoardScore; s.boards++;
    evs.push({ type: "clear", level: s.level, score: s.lastBoardScore });
    if (!s.levels || s.level >= total(s)) {
      s.won = true; s.over = true; s.cause = "won";
      evs.push({ type: "win", score: s.runScore });
    } else { s.phase = "clear"; s.t = CLEAR_T; }
  }

  /** Tap the arrow at cell i. */
  function tapCell(s, i) {
    var evs = [], b = s.board;
    if (s.over || s.phase !== "play" || !(i >= 0 && i < b.grid.length)) return evs;
    s.cursor = i;
    var a = b.grid[i];
    if (a < 0) return evs;
    if (isFree(b, a)) {
      s.flying.push({ cells: b.arrows[a].cells.slice(), dir: b.arrows[a].dir, t: 0 });
      remove(b, a);
      s.released++;
      if (s.hint === a) s.hint = -1;
      evs.push({ type: "release", arrow: a });
      if (left(s) === 0) boardCleared(s, evs);
    } else {
      s.bump = { arrow: a, at: blocker(b, a), t: 0 };
      s.mistakes++; s.boardMistakes++;
      evs.push({ type: "bump", arrow: a });
      if (s.maxHearts) {
        s.hearts--;
        evs.push({ type: "heart", hearts: s.hearts });
        if (s.hearts <= 0) { s.over = true; s.cause = "hearts"; evs.push({ type: "lose" }); }
      }
    }
    return evs;
  }

  function hint(s) {
    if (s.over || s.phase !== "play") return [];
    if (!s.hintsOn) { s.noHintAt = s.updates; return [{ type: "nohint" }]; }
    if (s.hint >= 0 && !s.board.arrows[s.hint].gone) return [];
    var b = s.board, n = b.n, best = -1, bd = 1e9, cx = s.cursor % n, cy = Math.floor(s.cursor / n);
    for (var a = 0; a < b.arrows.length; a++) {
      if (!isFree(b, a)) continue;
      var h = b.arrows[a].cells[0], d = Math.abs(h % n - cx) + Math.abs(Math.floor(h / n) - cy);
      if (d < bd) { bd = d; best = a; }
    }
    if (best < 0) return [];
    s.hint = best; s.hints++; s.boardHints++;
    return [{ type: "hint", arrow: best }];
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "play") s.boardUpdates++;
    for (var i = s.flying.length - 1; i >= 0; i--) if (++s.flying[i].t > FLY_T) s.flying.splice(i, 1);
    if (s.bump && ++s.bump.t > BUMP_T) s.bump = null;
    if (s.phase === "clear" && --s.t <= 0) {
      s.level++;
      if (s.maxHearts) s.hearts = Math.min(s.maxHearts, s.hearts + 1);
      startBoard(s);
      evs.push({ type: "level", level: s.level, size: s.board.n });
    }
    return evs;
  }

  /** The shell's actions: arrows move the cursor, fire taps it, alt / hint asks for a hint. */
  function press(s, action, down) {
    if (!down || s.over || !s.board) return [];
    var n = s.board.n, r = Math.floor(s.cursor / n), c = s.cursor % n;
    switch (action) {
      case "up": s.cursor = ((r + n - 1) % n) * n + c; return [{ type: "cursor" }];
      case "down": s.cursor = ((r + 1) % n) * n + c; return [{ type: "cursor" }];
      case "left": s.cursor = r * n + (c + n - 1) % n; return [{ type: "cursor" }];
      case "right": s.cursor = r * n + (c + 1) % n; return [{ type: "cursor" }];
      case "fire": return tapCell(s, s.cursor);
      case "alt": case "hint": return hint(s);
    }
    return [];
  }

  function save(s) { var out = JSON.parse(JSON.stringify(s)); out.flying = []; out.bump = null; out.list = null; return out; }
  function restore(data, levels) {
    var b = data && data.board;
    var ok = data && typeof data === "object" && MODES[data.mode] && b && typeof b.n === "number" && b.n >= 3 && b.n <= 12 &&
      Array.isArray(b.grid) && b.grid.length === b.n * b.n && Array.isArray(b.arrows) &&
      b.arrows.every(function (a) { return a && Array.isArray(a.cells) && a.cells.length >= 1 && a.dir >= 0 && a.dir <= 3; }) &&
      typeof data.level === "number" && data.level >= 1 && typeof data.runScore === "number" &&
      typeof data.updates === "number" && typeof data.rng === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.book = data.mode === "book"; s.list = s.book ? usableLevels(levels) : null;
    if (s.level > total(s)) throw new Error("That saved game can't be continued.");
    s.flying = []; s.bump = null;
    if (!(s.cursor >= 0 && s.cursor < s.board.grid.length)) s.cursor = 0;
    if (!(s.hint >= 0 && s.hint < s.board.arrows.length)) s.hint = -1;
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    var lines = [];
    if (s.boards) lines.push(s.levels ? "Boards cleared: " + s.boards : "Cleared with " + s.mistakes + (s.mistakes === 1 ? " mistake" : " mistakes"));
    lines.push(s.released + (s.released === 1 ? " arrow" : " arrows") + " released");
    if (s.hints) lines.push(s.hints + (s.hints === 1 ? " hint" : " hints"));
    return {
      score: score(s), level: s.level,
      stats: { won: s.won, cause: s.cause, mode: s.mode, released: s.released, mistakes: s.mistakes, hints: s.hints,
        boards: s.boards, summary: lines, notSaved: s.boards ? undefined : "Board not cleared — not saved" },
    };
  }

  var ArrowsLogic = {
    UPS: UPS, TOP: TOP, MIN_SCORE: MIN_SCORE, SEC_COST: SEC_COST, MISTAKE_COST: MISTAKE_COST, HINT_COST: HINT_COST,
    HEARTS: HEARTS, LEVEL_COUNT: LEVEL_COUNT, LEVELS: LEVELS, usableLevels: usableLevels, levelProblem: levelProblem, bookSpec: bookSpec, total: total, CLEAR_T: CLEAR_T, FLY_T: FLY_T, STATE_VERSION: STATE_VERSION, MODES: MODES, DX: DX, DY: DY,
    create: create, step: step, press: press, tapCell: tapCell, hint: hint, isFree: isFree, blocker: blocker, solvable: solvable,
    stuckArrows: stuckArrows, makeBoard: makeBoard, levelSpec: levelSpec, hashSeed: hashSeed, left: left, score: score,
    boardScore: boardScore, save: save, restore: restore, result: result, seconds: seconds, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = ArrowsLogic; else self.ArrowsLogic = ArrowsLogic;
})();
