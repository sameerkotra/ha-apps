/* Household Arcade — Four in a Row rules (pure: no DOM, deterministic for a seed).

   A 7 × 6 frame stood upright; a disc dropped into a column falls to the lowest free place. Four of your discs in a
   row — across, up and down or slanting — wins; a full frame with no row is a draw. Cells are numbered row by row from
   the top: cell = row × 7 + column. A move is { col: 0–6 }. The same rules as the server's app/rules/fourrow.py
   (checked by the tests on thousands of random games).

   The computer: Easy wins when it can, blocks a row of yours most of the time and otherwise drops near the middle at
   random; Medium looks 4 moves ahead, Hard 6 (a search over every reply, scoring the open rows of four), always
   taking a win and blocking one. Ties between equally good columns are broken from the seed. */
(function () {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : self.BoardKit;

  var COLS = 7, ROWS = 6, BONUS_CELL = 10;
  var ORDER = [3, 2, 4, 1, 5, 0, 6];

  function lineThrough(board, cell) {
    var who = board[cell], r0 = Math.floor(cell / COLS), c0 = cell % COLS;
    var dirs = [[0, 1], [1, 0], [1, 1], [1, -1]];
    for (var d = 0; d < 4; d++) {
      var run = [cell];
      for (var sgn = 1; sgn >= -1; sgn -= 2) {
        var r = r0 + dirs[d][0] * sgn, c = c0 + dirs[d][1] * sgn;
        while (r >= 0 && r < ROWS && c >= 0 && c < COLS && board[r * COLS + c] === who) {
          run.push(r * COLS + c);
          r += dirs[d][0] * sgn; c += dirs[d][1] * sgn;
        }
      }
      if (run.length >= 4) return run.sort(function (a, b) { return a - b; });
    }
    return [];
  }
  function dropRow(board, col) {
    for (var r = ROWS - 1; r >= 0; r--) if (board[r * COLS + col] === 0) return r;
    return -1;
  }
  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }

  var R = {
    id: "fourrow", STATE_VERSION: 1, OPTIONS: {},
    init: function (s) {
      s.board = []; for (var i = 0; i < COLS * ROWS; i++) s.board.push(0);
      s.line = []; s.drop = null;
    },
    moves: function (s, seat) {
      if (s.over || seat !== s.turn) return [];
      var out = [];
      for (var c = 0; c < COLS; c++) if (s.board[c] === 0) out.push({ col: c });
      return out;
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1 || !isWhole(move.col, 0, COLS - 1)) throw new Error("Pick a column, 0 to 6.");
      var col = move.col;
      if (s.board[col] !== 0) throw new Error("That column is full.");
      var row = dropRow(s.board, col), cell = row * COLS + col;
      s.board[cell] = seat + 1;
      s.ply++; s.last = cell;
      s.drop = { cell: cell, at: s.updates };
      var evs = [{ type: "drop", cell: cell, col: col, row: row, seat: seat }];
      var line = lineThrough(s.board, cell);
      if (line.length) { s.over = true; s.winner = seat; s.line = line; evs.push({ type: "line" }); }
      else if (s.board.every(function (v) { return v !== 0; })) { s.over = true; s.winner = -1; }
      else s.turn = 1 - seat;
      return evs;
    },
    bonus: function (s) { var n = 0; for (var i = 0; i < s.board.length; i++) if (s.board[i] === 0) n++; return BONUS_CELL * n; },
    digest: function (s) { return { board: s.board, turn: s.turn, over: s.over, winner: s.winner, line: s.line }; },
    cursorStart: function () { return 3; },
    cursorMove: function (s, dir) {
      if (dir === "left") s.cursor = (s.cursor + COLS - 1) % COLS;
      else if (dir === "right") s.cursor = (s.cursor + 1) % COLS;
      else if (dir === "down") return "fire";
      return null;
    },
    activate: function (s, seat) {
      if (seat < 0) return null;
      if (s.board[s.cursor] !== 0) return { events: [{ type: "nope" }] };
      return { move: { col: s.cursor } };
    },
    tap: function (s, col, seat) { s.cursor = col; return R.activate(s, seat); },
    ai: function (s, seat, level) { return { col: aiColumn(s, seat, level) }; },
    // a move that came from the other phone falls in like one made here
    keep: function (old, s) { if (s.number > old.number && s.drop) s.drop.at = s.updates; },
    check: function (d) { return Array.isArray(d.board) && d.board.length === COLS * ROWS && d.board.every(function (v) { return v === 0 || v === 1 || v === 2; }); },
    summary: function (s) {
      var n = 0; for (var i = 0; i < s.board.length; i++) if (s.board[i]) n++;
      return [n + " discs dropped" + (s.line.length ? " · four in a row" : "")];
    },
  };

  // ---------- the computer ----------
  var WEIGHT = [0, 1, 6, 40];
  var WINDOWS = (function () {
    var out = [], dirs = [[0, 1], [1, 0], [1, 1], [1, -1]];
    for (var r = 0; r < ROWS; r++) for (var c = 0; c < COLS; c++) for (var d = 0; d < 4; d++) {
      var er = r + 3 * dirs[d][0], ec = c + 3 * dirs[d][1];
      if (er < 0 || er >= ROWS || ec < 0 || ec >= COLS) continue;
      var w = []; for (var k = 0; k < 4; k++) w.push((r + k * dirs[d][0]) * COLS + c + k * dirs[d][1]);
      out.push(w);
    }
    return out;
  })();
  function evaluate(b, me) {
    var them = 3 - me, sc = 0;
    for (var i = 0; i < WINDOWS.length; i++) {
      var w = WINDOWS[i], a = 0, z = 0;
      for (var k = 0; k < 4; k++) { var v = b[w[k]]; if (v === me) a++; else if (v === them) z++; }
      if (a && !z) sc += WEIGHT[a]; else if (z && !a) sc -= WEIGHT[z];
    }
    for (var r = 0; r < ROWS; r++) { var v3 = b[r * COLS + 3]; if (v3 === me) sc += 3; else if (v3 === them) sc -= 3; }
    return sc;
  }
  var WIN = 1000000;
  function negamax(b, me, depth, alpha, beta, filled) {
    if (filled === COLS * ROWS) return 0;
    if (depth === 0) return evaluate(b, me);
    var best = -Infinity;
    for (var i = 0; i < 7; i++) {
      var c = ORDER[i];
      if (b[c] !== 0) continue;
      var r = dropRow(b, c), cell = r * COLS + c;
      b[cell] = me;
      var v = lineThrough(b, cell).length ? WIN + depth : -negamax(b, 3 - me, depth - 1, -beta, -alpha, filled + 1);
      b[cell] = 0;
      if (v > best) best = v;
      if (best > alpha) alpha = best;
      if (alpha >= beta) break;
    }
    return best;
  }
  function wins(b, col, who) {
    if (b[col] !== 0) return false;
    var r = dropRow(b, col), cell = r * COLS + col;
    b[cell] = who; var w = lineThrough(b, cell).length > 0; b[cell] = 0;
    return w;
  }
  function aiColumn(s, seat, level) {
    var b = s.board.slice(), me = seat + 1, them = 2 - seat, cols = [], c, filled = 0;
    for (var i = 0; i < b.length; i++) if (b[i]) filled++;
    for (c = 0; c < COLS; c++) if (b[c] === 0) cols.push(c);
    for (c = 0; c < cols.length; c++) if (wins(b, cols[c], me)) return cols[c];
    var block = level >= 2 || BK.rand(s) < 0.6;
    if (block) for (c = 0; c < cols.length; c++) if (wins(b, cols[c], them)) return cols[c];
    if (level <= 1) {
      // near the middle, at random
      var pool = [];
      for (c = 0; c < cols.length; c++) { var wgt = 4 - Math.abs(cols[c] - 3); for (var k = 0; k < wgt; k++) pool.push(cols[c]); }
      return BK.pick(s, pool);
    }
    // leaves are judged with as many discs of each colour (an even count), so neither side is flattered by a tempo
    var depth = level === 2 ? 4 : 7, best = -Infinity, bestCols = [];
    if ((filled + depth) % 2) depth++;
    for (i = 0; i < 7; i++) {
      c = ORDER[i];
      if (b[c] !== 0) continue;
      var r = dropRow(b, c), cell = r * COLS + c;
      b[cell] = me;
      var v = -negamax(b, them, depth - 1, -Infinity, -best + 1, filled + 1);
      b[cell] = 0;
      if (v > best) { best = v; bestCols = [c]; } else if (v === best) bestCols.push(c);
    }
    return BK.pick(s, bestCols);
  }

  var L = BK.game(R);
  L.COLS = COLS; L.ROWS = ROWS; L.BONUS_CELL = BONUS_CELL; L.lineThrough = lineThrough; L.dropRow = dropRow;
  L.aiColumn = aiColumn;
  if (typeof module === "object" && module.exports) module.exports = L; else self.FourRowLogic = L;
})();
