/* Household Arcade — Mines rules (pure: no DOM, deterministic for a given seed and the same moves).

   A grid of covered squares hides mines. The first square opened on a board is never a mine and
   never next to one (the mines are laid only then). A square with no mines around it opens its
   neighbours; numbers say how many mines touch a square. Clearing every safe square clears the
   board and the next, slightly harder board comes after a short pause. Opening a mine ends the game.
   Shaped boards (mode "boards") play a level list instead: each board is a shape with holes in it and
   its own mine count; clearing the last one wins the game.
   One call to step() is one update (60 a second); the touch helpers work in logical pixels. */
(function () {
  "use strict";

  var UPS = 60;
  // Board sizes; cell sizes chosen so the board fits 240 × ~256 below the HUD as large as it can.
  var MODES = {
    easy: { cols: 9, rows: 9, mines: 10, inc: 2, cap: 20, cs: 24, ox: 12, oy: 48 },
    medium: { cols: 10, rows: 12, mines: 18, inc: 2, cap: 30, cs: 20, ox: 18, oy: 42 },
    hard: { cols: 12, rows: 15, mines: 32, inc: 3, cap: 45, cs: 16, ox: 24, oy: 42 },
    boards: { list: true },    // Shaped boards: size, layout and mines come from the level list
  };

  // Shaped boards (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A board is
  // { name, shape, mines }: `shape` is 6–15 rows of 6–12 characters, "#" a square and "." none; at least
  // 30 squares, all joined side by side, touching all four sides of its box; mines 8–22 % of the squares.
  var LEVELS = [
    { name: "Picnic blanket", mines: 8, shape: ["########", "########", "########", "########", "########", "########", "########", "########"] },
    { name: "Plus sign", mines: 6, shape: ["...###...", "...###...", "...###...", "#########", "#########", "#########", "...###...", "...###...", "...###..."] },
    { name: "Stairs", mines: 9, shape: ["##........", "###.......", "####......", "#####.....", "######....", "#######...", "########..", "#########.", "##########", "##########"] },
    { name: "Big heart", mines: 9, shape: [".###..###.", "##########", "##########", "##########", ".########.", "..######..", "...####...", "....##...."] },
    { name: "Two ponds", mines: 10, shape: ["####....####", "#####..#####", "############", "############", "#####..#####", "####....####"] },
    { name: "Ring", mines: 12, shape: ["..######..", ".########.", "##########", "###....###", "###....###", "###....###", "###....###", "##########", ".########.", "..######.."] },
    { name: "Diamond", mines: 11, shape: [".....#.....", "....###....", "...#####...", "..#######..", ".#########.", "###########", ".#########.", "..#######..", "...#####...", "....###....", ".....#....."] },
    { name: "Tall tree", mines: 15, shape: [".....#.....", "....###....", "...#####...", "..#######..", "....###....", "...#####...", "..#######..", ".#########.", "....###....", "..#######..", ".#########.", "###########", "....###....", "....###....", "...#####..."] },
    { name: "Grand field", mines: 30, shape: ["############", "############", "############", "############", "#####..#####", "####....####", "####....####", "#####..#####", "############", "############", "############", "############", "############"] },
  ];
  var SHAPE_ROWS = [6, 15], SHAPE_COLS = [6, 12], MIN_SQUARES = 30;
  var CELL_SIZES = [30, 24, 18, 16];   // the biggest that fits 228 × 240; multiples of 6 where they can be

  /** A shape's { cols, rows, hole[], squares }, or null when it breaks a rule. */
  function shapeInfo(shape) {
    if (!Array.isArray(shape) || shape.length < SHAPE_ROWS[0] || shape.length > SHAPE_ROWS[1]) return null;
    var rows = shape.length, cols = typeof shape[0] === "string" ? shape[0].length : 0;
    if (cols < SHAPE_COLS[0] || cols > SHAPE_COLS[1]) return null;
    var hole = [], squares = 0, first = -1, y, x;
    for (y = 0; y < rows; y++) {
      if (typeof shape[y] !== "string" || shape[y].length !== cols || !/^[#.]+$/.test(shape[y])) return null;
      for (x = 0; x < cols; x++) {
        var sq = shape[y].charAt(x) === "#";
        hole.push(sq ? 0 : 1);
        if (sq) { squares++; if (first < 0) first = y * cols + x; }
      }
    }
    if (squares < MIN_SQUARES || shape[0].indexOf("#") < 0 || shape[rows - 1].indexOf("#") < 0) return null;
    var left = false, right = false;
    for (y = 0; y < rows; y++) { left = left || !hole[y * cols]; right = right || !hole[y * cols + cols - 1]; }
    if (!left || !right) return null;
    var seen = {}, stack = [first], count = 0;   // all one piece, side by side
    seen[first] = true;
    while (stack.length) {
      var i = stack.pop(), ix = i % cols, iy = (i / cols) | 0;
      count++;
      var nb = [[ix + 1, iy], [ix - 1, iy], [ix, iy + 1], [ix, iy - 1]];
      for (var k = 0; k < 4; k++) {
        var nx = nb[k][0], ny = nb[k][1], j = ny * cols + nx;
        if (nx >= 0 && ny >= 0 && nx < cols && ny < rows && !hole[j] && !seen[j]) { seen[j] = true; stack.push(j); }
      }
    }
    if (count !== squares) return null;
    return { cols: cols, rows: rows, hole: hole, squares: squares };
  }
  function mineRange(squares) { return [Math.ceil(squares * 8 / 100), Math.floor(squares * 22 / 100)]; }

  /** The boards to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (b) {
      if (!b || typeof b.name !== "string" || typeof b.mines !== "number" || b.mines !== Math.floor(b.mines)) return false;
      var info = shapeInfo(b.shape);
      if (!info) return false;
      var r = mineRange(info.squares);
      return b.mines >= r[0] && b.mines <= r[1];
    });
    return out.length ? out : LEVELS;
  }

  /** Square size and position for a shape cols × rows: centred below the HUD, on the 6 px grid. */
  function layoutFor(cols, rows) {
    var cs = CELL_SIZES[CELL_SIZES.length - 1];
    for (var i = 0; i < CELL_SIZES.length; i++) {
      if (cols * CELL_SIZES[i] <= 228 && rows * CELL_SIZES[i] <= 240) { cs = CELL_SIZES[i]; break; }
    }
    return { cs: cs, ox: Math.floor((240 - cols * cs) / 12) * 6, oy: 42 + Math.floor((242 - rows * cs) / 12) * 6 };
  }
  /** Where the squares are drawn: { cs, ox, oy }. */
  function geom(s) { return s.lay || MODES[s.mode]; }
  function isHole(s, i) { return !!(s.hole && s.hole[i]); }
  var MAX_LEVEL = 99;
  var LONG_PRESS = 24;        // updates a finger must stay on a square to flag it
  var MOVE_TOL = 6;           // logical px a finger may wander and still be a tap / long press
  var CLEAR_PAUSE = 90;       // updates between a cleared board and the next one
  var SQUARE_POINTS = 1;      // a safe square opened
  var MINE_BONUS = 10;        // board bonus: 10 a mine
  var SPEED_BONUS = 5;        // ... plus up to 5 a mine, one less for every second the board took

  var COVERED = 0, OPEN = 1, FLAG = 2;

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function minesFor(mode, level) {
    var m = MODES[mode];
    if (m.list) return 0;
    return Math.min(m.cap, m.mines + (level - 1) * m.inc);
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "easy", m = MODES[mode];
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, cols: m.cols || 0, rows: m.rows || 0,
      level: 1, score: 0, over: false, won: false, updates: 0, version: 1,
      cur: { x: (m.cols || 0) >> 1, y: (m.rows || 0) >> 1 }, showCur: false, flagMode: false,
      touch: null, pause: 0, hit: -1, boards: null, hole: null, lay: null, boardName: "",
      stats: { boards: 0, squares: 0, flags: 0, cause: null },
    };
    if (m.list) s.boards = usableLevels(o.levels);
    newBoard(s);
    return s;
  }

  /** The board from the list for level n (the last one if the list got shorter). */
  function boardFor(s, n) { return s.boards ? s.boards[Math.min(n, s.boards.length) - 1] : null; }

  // The square nearest (x, y), for the keyboard cursor on a shape with a hole in the middle.
  function nearestSquare(s, x, y) {
    var best = null, bd = 1e9;
    for (var i = 0; i < s.cols * s.rows; i++) {
      if (isHole(s, i)) continue;
      var dx = i % s.cols - x, dy = ((i / s.cols) | 0) - y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = { x: i % s.cols, y: (i / s.cols) | 0 }; }
    }
    return best || { x: x, y: y };
  }

  function newBoard(s) {
    var b = boardFor(s, s.level), squares;
    if (b) {
      var info = shapeInfo(b.shape);
      s.cols = info.cols; s.rows = info.rows; s.hole = info.hole; s.lay = layoutFor(info.cols, info.rows);
      s.mines = b.mines; s.boardName = b.name; squares = info.squares;
      s.cur = nearestSquare(s, s.cols >> 1, s.rows >> 1);
    } else {
      s.mines = minesFor(s.mode, s.level); squares = s.cols * s.rows;
    }
    var n = s.cols * s.rows;
    s.mine = new Array(n).fill(0);
    s.adj = new Array(n).fill(0);
    s.cell = new Array(n).fill(COVERED);
    s.placed = false; s.flags = 0; s.safeLeft = squares - s.mines;
    s.boardStart = s.updates; s.hit = -1;
    s.version++;
  }

  function neighbours(s, i) {
    var x = i % s.cols, y = (i / s.cols) | 0, out = [];
    for (var dy = -1; dy <= 1; dy++) {
      for (var dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        var nx = x + dx, ny = y + dy;
        if (nx >= 0 && ny >= 0 && nx < s.cols && ny < s.rows && !isHole(s, ny * s.cols + nx)) out.push(ny * s.cols + nx);
      }
    }
    return out;
  }

  // Lay the mines, keeping the first square and its neighbours clear (a shaped board has room: at
  // least 30 squares and at most 22 % mines leaves ≥ 23 squares for at most 9 kept clear and the mines).
  function place(s, first) {
    var n = s.cols * s.rows, keep = {}, spots = [], i;
    keep[first] = true;
    neighbours(s, first).forEach(function (j) { keep[j] = true; });
    for (i = 0; i < n; i++) if (!keep[i] && !isHole(s, i)) spots.push(i);
    for (i = 0; i < s.mines; i++) {
      var j = i + Math.floor(rand(s) * (spots.length - i)), t = spots[i];
      spots[i] = spots[j]; spots[j] = t;
      s.mine[spots[i]] = 1;
    }
    for (i = 0; i < n; i++) {
      var c = 0, nb = neighbours(s, i);
      for (var k = 0; k < nb.length; k++) c += s.mine[nb[k]];
      s.adj[i] = c;
    }
    s.placed = true;
    s.boardStart = s.updates;
  }

  function busy(s) { return s.over || s.pause > 0; }

  // Open covered squares from i outward (through squares with no mines around); returns how many.
  function flood(s, i) {
    var stack = [i], opened = 0;
    while (stack.length) {
      var j = stack.pop();
      if (s.cell[j] !== COVERED || s.mine[j]) continue;
      s.cell[j] = OPEN; opened++;
      if (s.adj[j] === 0) {
        var nb = neighbours(s, j);
        for (var k = 0; k < nb.length; k++) if (s.cell[nb[k]] === COVERED) stack.push(nb[k]);
      }
    }
    return opened;
  }

  function boom(s, i, evs) {
    s.hit = i; s.over = true; s.stats.cause = "mine"; s.version++;
    evs.push({ type: "boom", index: i });
    evs.push({ type: "gameover", score: s.score, cause: "mine" });
  }

  function opened(s, n, evs) {
    if (!n) return;
    s.score += n * SQUARE_POINTS; s.safeLeft -= n; s.stats.squares += n; s.version++;
    evs.push({ type: "open", count: n });
    if (s.safeLeft <= 0) clearBoard(s, evs);
  }

  function clearBoard(s, evs) {
    var secs = Math.floor((s.updates - s.boardStart) / UPS);
    var bonus = MINE_BONUS * s.mines + Math.max(0, SPEED_BONUS * s.mines - secs);
    s.score += bonus; s.stats.boards++;
    // flag the remaining mines for the picture
    for (var i = 0; i < s.cell.length; i++) if (s.mine[i] && s.cell[i] !== FLAG) { s.cell[i] = FLAG; s.flags++; }
    s.version++;
    evs.push({ type: "clear", bonus: bonus, seconds: secs });
    if (s.boards) {                       // Shaped boards: the next board in the list, or the game is won
      if (s.level >= s.boards.length) {
        s.won = true; s.over = true; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
        return;
      }
      s.pause = CLEAR_PAUSE; s.level++;
      evs.push({ type: "level", level: s.level });
      return;
    }
    s.pause = CLEAR_PAUSE;
    if (s.level < MAX_LEVEL) { s.level++; evs.push({ type: "level", level: s.level }); }
  }

  /** Open square i (or, on an open number whose flags match it, the rest of its neighbours). */
  function open(s, i) {
    var evs = [];
    if (busy(s) || i < 0 || i >= s.cell.length || isHole(s, i)) return evs;
    var st = s.cell[i];
    if (st === FLAG) return evs;
    if (st === OPEN) return chord(s, i);
    if (!s.placed) place(s, i);
    if (s.mine[i]) { boom(s, i, evs); return evs; }
    opened(s, flood(s, i), evs);
    return evs;
  }

  function chord(s, i) {
    var evs = [], nb = neighbours(s, i), flags = 0, todo = [], k;
    if (busy(s) || s.cell[i] !== OPEN || !s.adj[i]) return evs;
    for (k = 0; k < nb.length; k++) {
      if (s.cell[nb[k]] === FLAG) flags++;
      else if (s.cell[nb[k]] === COVERED) todo.push(nb[k]);
    }
    if (flags !== s.adj[i] || !todo.length) return evs;
    for (k = 0; k < todo.length; k++) if (s.mine[todo[k]]) { boom(s, todo[k], evs); return evs; }
    var n = 0;
    for (k = 0; k < todo.length; k++) n += flood(s, todo[k]);
    opened(s, n, evs);
    return evs;
  }

  /** Put a flag on covered square i, or take it off again. */
  function flag(s, i) {
    var evs = [];
    if (busy(s) || i < 0 || i >= s.cell.length || isHole(s, i)) return evs;
    if (s.cell[i] === COVERED) { s.cell[i] = FLAG; s.flags++; s.stats.flags++; evs.push({ type: "flag", index: i }); }
    else if (s.cell[i] === FLAG) { s.cell[i] = COVERED; s.flags--; evs.push({ type: "unflag", index: i }); }
    else return evs;
    s.version++;
    return evs;
  }

  /** A tap: opens, or flags while flag mode is on (an open number still opens around itself). */
  function tap(s, i) {
    if (s.flagMode && s.cell[i] !== OPEN) return flag(s, i);
    return open(s, i);
  }

  // ---- touch, in logical px ----
  function cellAt(s, x, y) {
    var m = geom(s);
    var cx = Math.floor((x - m.ox) / m.cs), cy = Math.floor((y - m.oy) / m.cs);
    if (!(cx >= 0 && cy >= 0 && cx < s.cols && cy < s.rows) || isHole(s, cy * s.cols + cx)) return -1;
    return cy * s.cols + cx;
  }
  function touchDown(s, x, y) {
    if (busy(s)) return [];
    s.touch = { x: x, y: y, i: cellAt(s, x, y), t: 0, moved: false, done: false };
    s.showCur = false;
    return [];
  }
  function touchMove(s, x, y) {
    var t = s.touch;
    if (t && !t.moved && Math.max(Math.abs(x - t.x), Math.abs(y - t.y)) > MOVE_TOL) t.moved = true;
    return [];
  }
  function touchUp(s, x, y) {
    var t = s.touch, evs = [];
    s.touch = null;
    if (!t || busy(s)) return evs;
    if (Math.max(Math.abs(x - t.x), Math.abs(y - t.y)) > MOVE_TOL) t.moved = true;
    if (!t.done && !t.moved && t.i >= 0) evs = tap(s, t.i);
    return evs;
  }

  // ---- keyboard ----
  function press(s, action, down) {
    var evs = [];
    if (!down || busy(s)) return evs;
    var d = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] }[action];
    if (d) {
      if (s.showCur) {
        s.cur.x = Math.max(0, Math.min(s.cols - 1, s.cur.x + d[0]));
        s.cur.y = Math.max(0, Math.min(s.rows - 1, s.cur.y + d[1]));
      }
      s.showCur = true;            // the first arrow just shows the cursor
      evs.push({ type: "cursor" });
      return evs;
    }
    var i = s.cur.y * s.cols + s.cur.x;
    if (action === "fire") { s.showCur = true; return open(s, i); }
    if (action === "alt") {
      if (s.showCur) return flag(s, i);
      s.flagMode = !s.flagMode;    // the on-screen Flag button: flag mode for taps
      evs.push({ type: "mode", flagMode: s.flagMode });
    }
    return evs;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.pause > 0) {
      if (--s.pause === 0) newBoard(s);
      return evs;
    }
    var t = s.touch;
    if (t && !t.moved && !t.done && t.i >= 0) {
      if (++t.t >= LONG_PRESS) { t.done = true; evs = flag(s, t.i); }
    }
    return evs;
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "boards") out[k] = s[k];
    var d = JSON.parse(JSON.stringify(out)); d.touch = null; return d;
  }
  function restore(data, levels) {
    var m = data && typeof data === "object" && MODES[data.mode];
    var n = -1, sized = false;
    if (m && m.list) {
      sized = data.cols >= SHAPE_COLS[0] && data.cols <= SHAPE_COLS[1] && data.rows >= SHAPE_ROWS[0] && data.rows <= SHAPE_ROWS[1];
      n = sized ? data.cols * data.rows : -1;
      sized = sized && Array.isArray(data.hole) && data.hole.length === n && data.lay && typeof data.lay.cs === "number";
    } else if (m) {
      n = m.cols * m.rows; sized = data.cols === m.cols && data.rows === m.rows;
    }
    var ok = m && sized && Array.isArray(data.mine) && data.mine.length === n &&
      Array.isArray(data.cell) && data.cell.length === n && Array.isArray(data.adj) && data.adj.length === n &&
      typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" && data.stats &&
      data.cur && typeof data.safeLeft === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.touch = null; s.version = (s.version || 0) + 1;
    s.boards = m.list ? usableLevels(levels) : null;
    if (!m.list) { s.hole = null; s.lay = null; }
    s.won = !!s.won;
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { boards: s.stats.boards, squares: s.stats.squares, flags: s.stats.flags, mines: s.mines, won: !!s.won,
        cause: s.stats.cause, mode: s.mode },
    };
  }

  var MinesLogic = {
    UPS: UPS, MODES: MODES, MAX_LEVEL: MAX_LEVEL, LONG_PRESS: LONG_PRESS, MOVE_TOL: MOVE_TOL, CLEAR_PAUSE: CLEAR_PAUSE,
    SQUARE_POINTS: SQUARE_POINTS, MINE_BONUS: MINE_BONUS, SPEED_BONUS: SPEED_BONUS,
    COVERED: COVERED, OPEN: OPEN, FLAG: FLAG, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    shapeInfo: shapeInfo, mineRange: mineRange, layoutFor: layoutFor, geom: geom, isHole: isHole, boardFor: boardFor,
    create: create, step: step, press: press, open: open, flag: flag, tap: tap, chord: chord,
    touchDown: touchDown, touchMove: touchMove, touchUp: touchUp, cellAt: cellAt,
    neighbours: neighbours, minesFor: minesFor, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = MinesLogic; else self.MinesLogic = MinesLogic;
})();
