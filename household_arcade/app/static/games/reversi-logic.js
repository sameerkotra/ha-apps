/* Household Arcade — Reversi rules (pure: no DOM, deterministic for a seed).

   8 × 8, two discs each in the middle; the side that moves first plays dark. A disc must close at least one straight
   line (across, up and down or slanting) of the other side's discs between it and another of yours, and those turn
   over. A player with no such place passes — automatically: the other simply moves again. When neither can place the
   game ends; more discs wins (the same number is a draw). Squares 0–63; a move is { cell }. The same rules as the
   server's app/rules/reversi.py.

   The computer: Easy places anywhere it may; Medium picks the best square by a table of square values (corners good,
   the squares next to them bad); Hard looks 4 moves ahead with the same table and how many moves each side has. */
(function () {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : self.BoardKit;

  var BONUS_DISC = 5;
  var DIRS = [[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1], [1, -1], [1, 0], [1, 1]];
  var VALUES = [
    100, -20, 10, 5, 5, 10, -20, 100,
    -20, -50, -2, -2, -2, -2, -50, -20,
    10, -2, 1, 1, 1, 1, -2, 10,
    5, -2, 1, 0, 0, 1, -2, 5,
    5, -2, 1, 0, 0, 1, -2, 5,
    10, -2, 1, 1, 1, 1, -2, 10,
    -20, -50, -2, -2, -2, -2, -50, -20,
    100, -20, 10, 5, 5, 10, -20, 100];

  function flips(board, cell, seat) {
    if (board[cell] !== 0) return [];
    var me = seat + 1, out = [], r0 = cell >> 3, c0 = cell & 7;
    for (var d = 0; d < 8; d++) {
      var run = [], r = r0 + DIRS[d][0], c = c0 + DIRS[d][1];
      while (r >= 0 && r < 8 && c >= 0 && c < 8 && board[r * 8 + c] !== 0 && board[r * 8 + c] !== me) {
        run.push(r * 8 + c); r += DIRS[d][0]; c += DIRS[d][1];
      }
      if (run.length && r >= 0 && r < 8 && c >= 0 && c < 8 && board[r * 8 + c] === me) out = out.concat(run);
    }
    return out;
  }
  function cells(board, seat) {
    var out = [];
    for (var i = 0; i < 64; i++) if (board[i] === 0 && flips(board, i, seat).length) out.push(i);
    return out;
  }
  function count(board, seat) { var n = 0; for (var i = 0; i < 64; i++) if (board[i] === seat + 1) n++; return n; }
  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }

  var R = {
    id: "reversi", STATE_VERSION: 1, OPTIONS: {},
    init: function (s) {
      var b = []; for (var i = 0; i < 64; i++) b.push(0);
      var other = 1 - s.first;
      b[27] = other + 1; b[36] = other + 1; b[28] = s.first + 1; b[35] = s.first + 1;
      s.board = b; s.passed = -1; s.flipped = []; s.moveAt = -100;
    },
    moves: function (s, seat) {
      if (s.over || seat !== s.turn) return [];
      return cells(s.board, seat).map(function (c) { return { cell: c }; });
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1 || !isWhole(move.cell, 0, 63)) throw new Error("Pick a square, 0 to 63.");
      var cell = move.cell, fl = flips(s.board, cell, seat);
      if (!fl.length) throw new Error("A disc must turn over at least one of the other side's.");
      s.board[cell] = seat + 1;
      for (var i = 0; i < fl.length; i++) s.board[fl[i]] = seat + 1;
      s.ply++; s.last = cell; s.passed = -1; s.flipped = fl; s.moveAt = s.updates;
      var evs = [{ type: "place", cell: cell, flips: fl.length, seat: seat }], other = 1 - seat;
      if (cells(s.board, other).length) s.turn = other;
      else if (cells(s.board, seat).length) { s.turn = seat; s.passed = other; evs.push({ type: "pass", seat: other }); }
      else {
        s.over = true;
        var a = count(s.board, seat), z = count(s.board, other);
        s.winner = a > z ? seat : z > a ? other : -1;
      }
      return evs;
    },
    bonus: function (s, seat) { return BONUS_DISC * (count(s.board, seat) - count(s.board, 1 - seat)); },
    digest: function (s) { return { board: s.board, turn: s.turn, over: s.over, winner: s.winner, passed: s.passed }; },
    counts: function (s) { return [count(s.board, 0), count(s.board, 1)]; },
    cursorStart: function () { return 19; },
    cursorMove: function (s, dir) {
      var r = s.cursor >> 3, c = s.cursor & 7;
      if (dir === "up") r = Math.max(0, r - 1); else if (dir === "down") r = Math.min(7, r + 1);
      else if (dir === "left") c = Math.max(0, c - 1); else if (dir === "right") c = Math.min(7, c + 1);
      s.cursor = r * 8 + c;
    },
    activate: function (s, seat) {
      if (seat < 0) return null;
      if (!flips(s.board, s.cursor, seat).length) return { events: [{ type: "nope" }] };
      return { move: { cell: s.cursor } };
    },
    tap: function (s, cell, seat) { s.cursor = cell; return R.activate(s, seat); },
    keep: function (old, s) { if (s.number > old.number) s.moveAt = s.updates; },
    ai: function (s, seat, level) { return { cell: aiCell(s, seat, level) }; },
    check: function (d) { return Array.isArray(d.board) && d.board.length === 64 && typeof d.passed === "number"; },
    summary: function (s, names) { return [names[0] + " " + count(s.board, 0) + " · " + names[1] + " " + count(s.board, 1) + " discs"]; },
  };

  // ---------- the computer ----------
  function evaluate(board, me, rich) {
    var sc = 0;
    for (var i = 0; i < 64; i++) if (board[i]) sc += (board[i] === me + 1 ? 1 : -1) * VALUES[i];
    if (rich) sc += 4 * (cells(board, me).length - cells(board, 1 - me).length);
    return sc;
  }
  function search(board, toMove, me, depth, alpha, beta) {
    var ms = cells(board, toMove);
    if (!ms.length) {
      if (!cells(board, 1 - toMove).length) {
        var d = count(board, me) - count(board, 1 - me);
        return d > 0 ? 100000 + d : d < 0 ? -100000 + d : 0;
      }
      return search(board, 1 - toMove, me, depth, alpha, beta);          // a pass
    }
    if (depth === 0) return evaluate(board, me, true);
    var maxing = toMove === me, best = maxing ? -Infinity : Infinity;
    for (var i = 0; i < ms.length; i++) {
      var b = board.slice(), fl = flips(b, ms[i], toMove);
      b[ms[i]] = toMove + 1;
      for (var k = 0; k < fl.length; k++) b[fl[k]] = toMove + 1;
      var v = search(b, 1 - toMove, me, depth - 1, alpha, beta);
      if (maxing) { if (v > best) best = v; if (best > alpha) alpha = best; }
      else { if (v < best) best = v; if (best < beta) beta = best; }
      if (alpha >= beta) break;
    }
    return best;
  }
  function aiCell(s, seat, level) {
    var ms = cells(s.board, seat);
    if (ms.length === 1 || level <= 1) return BK.pick(s, ms);
    var best = -Infinity, bestCells = [];
    for (var i = 0; i < ms.length; i++) {
      var b = s.board.slice(), fl = flips(b, ms[i], seat);
      b[ms[i]] = seat + 1;
      for (var k = 0; k < fl.length; k++) b[fl[k]] = seat + 1;
      var v = level === 2 ? evaluate(b, seat, false) : search(b, 1 - seat, seat, 3, best - 1, Infinity);
      if (v > best) { best = v; bestCells = [ms[i]]; } else if (v === best) bestCells.push(ms[i]);
    }
    return BK.pick(s, bestCells);
  }

  var L = BK.game(R);
  L.BONUS_DISC = BONUS_DISC; L.flips = flips; L.cells = cells; L.count = count; L.aiCell = aiCell;
  if (typeof module === "object" && module.exports) module.exports = L; else self.ReversiLogic = L;
})();
