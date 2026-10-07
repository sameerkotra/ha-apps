/* Household Arcade — Tic-tac-toe rules (pure: no DOM, deterministic for a seed).

   A 3 × 3 grid; players take turns marking an empty square — the one who moves first is ✕, the other ○. Three in a
   row across, down or corner to corner wins; a full grid is a draw. Squares are 0–8 row by row; a move is
   { cell: 0–8 }. The same rules as the server's app/rules/tictactoe.py.

   The computer: Easy takes a win when it sees one, blocks half the time and otherwise plays anywhere; Medium always
   wins or blocks and likes the middle; Hard plays perfectly (it never loses — a draw against it is a good result).
   Ties between equally good squares are broken from the seed. */
(function () {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : self.BoardKit;

  var LINES = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]];
  var BONUS_CELL = 25;

  function lineOf(b, who) {
    for (var i = 0; i < LINES.length; i++) {
      var l = LINES[i];
      if (b[l[0]] === who && b[l[1]] === who && b[l[2]] === who) return l.slice();
    }
    return null;
  }
  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }

  var R = {
    id: "tictactoe", STATE_VERSION: 1, OPTIONS: {},
    init: function (s) { s.board = [0, 0, 0, 0, 0, 0, 0, 0, 0]; s.line = []; s.markAt = -100; },
    moves: function (s, seat) {
      if (s.over || seat !== s.turn) return [];
      var out = [];
      for (var i = 0; i < 9; i++) if (s.board[i] === 0) out.push({ cell: i });
      return out;
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1 || !isWhole(move.cell, 0, 8)) throw new Error("Pick a square, 0 to 8.");
      var i = move.cell;
      if (s.board[i] !== 0) throw new Error("That square is taken.");
      s.board[i] = seat + 1; s.ply++; s.last = i; s.markAt = s.updates;
      var evs = [{ type: "mark", cell: i, seat: seat }];
      var line = lineOf(s.board, seat + 1);
      if (line) { s.over = true; s.winner = seat; s.line = line; evs.push({ type: "line" }); }
      else if (s.board.every(function (v) { return v !== 0; })) { s.over = true; s.winner = -1; }
      else s.turn = 1 - seat;
      return evs;
    },
    bonus: function (s) { var n = 0; for (var i = 0; i < 9; i++) if (!s.board[i]) n++; return BONUS_CELL * n; },
    digest: function (s) { return { board: s.board, turn: s.turn, over: s.over, winner: s.winner, line: s.line }; },
    cursorStart: function () { return 4; },
    cursorMove: function (s, dir) {
      var r = Math.floor(s.cursor / 3), c = s.cursor % 3;
      if (dir === "up") r = (r + 2) % 3; else if (dir === "down") r = (r + 1) % 3;
      else if (dir === "left") c = (c + 2) % 3; else if (dir === "right") c = (c + 1) % 3;
      s.cursor = r * 3 + c;
    },
    activate: function (s, seat) {
      if (seat < 0) return null;
      if (s.board[s.cursor]) return { events: [{ type: "nope" }] };
      return { move: { cell: s.cursor } };
    },
    tap: function (s, cell, seat) { s.cursor = cell; return R.activate(s, seat); },
    keep: function (old, s) { if (s.number > old.number) s.markAt = s.updates; },
    ai: function (s, seat, level) { return { cell: aiCell(s, seat, level) }; },
    check: function (d) { return Array.isArray(d.board) && d.board.length === 9 && d.board.every(function (v) { return v === 0 || v === 1 || v === 2; }); },
  };

  // ---------- the computer ----------
  function finding(b, who) {
    for (var i = 0; i < 9; i++) if (!b[i]) { b[i] = who; var w = lineOf(b, who); b[i] = 0; if (w) return i; }
    return -1;
  }
  function minimax(b, me, toMove) {
    // the result for `me` with `toMove` to play: 1 win, 0 draw, −1 loss (sooner wins / later losses preferred)
    var other = 3 - toMove, best = toMove === me ? -10 : 10, any = false;
    for (var i = 0; i < 9; i++) {
      if (b[i]) continue;
      any = true;
      b[i] = toMove;
      var v;
      if (lineOf(b, toMove)) v = toMove === me ? 5 : -5;
      else v = minimax(b, me, other) * 0.9;
      b[i] = 0;
      if (toMove === me ? v > best : v < best) best = v;
    }
    return any ? best : 0;
  }
  function aiCell(s, seat, level) {
    var b = s.board.slice(), me = seat + 1, them = 2 - seat, free = [], i;
    for (i = 0; i < 9; i++) if (!b[i]) free.push(i);
    var w = finding(b, me);
    if (w >= 0) return w;
    if (level >= 2 || BK.rand(s) < 0.5) { var bl = finding(b, them); if (bl >= 0) return bl; }
    if (level <= 1) return BK.pick(s, free);
    if (level === 2) {
      if (!b[4] && BK.rand(s) < 0.8) return 4;
      return BK.pick(s, free);
    }
    var best = -100, bestCells = [];
    for (i = 0; i < free.length; i++) {
      b[free[i]] = me;
      var v = lineOf(b, me) ? 5 : minimax(b, me, them) * 0.9;
      b[free[i]] = 0;
      if (v > best + 1e-9) { best = v; bestCells = [free[i]]; } else if (Math.abs(v - best) < 1e-9) bestCells.push(free[i]);
    }
    return BK.pick(s, bestCells);
  }

  var L = BK.game(R);
  L.LINES = LINES; L.BONUS_CELL = BONUS_CELL; L.aiCell = aiCell; L.lineOf = lineOf;
  if (typeof module === "object" && module.exports) module.exports = L; else self.TicTacToeLogic = L;
})();
