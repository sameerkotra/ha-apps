/* Household Arcade — Checkers rules (pure: no DOM, deterministic for a seed). English draughts:

   - 8 × 8, pieces on the dark squares only ((row + column) odd, row 0 at the top), 12 a side. The side that moves
     first plays dark and starts on the three bottom rows; the other starts on the three top rows.
   - A man moves one square diagonally forward; a king (crowned on the far row) one square diagonally either way.
   - Capturing is compulsory: when any jump is possible you must jump. A jump that can carry on must carry on
     (multi-jumps) until no further jump is possible; of several captures you may choose any (not necessarily the
     longest). Jumped pieces come off at the end and can't be jumped twice. A man reaching the far row is crowned and
     its move ends there.
   - No legal move (no pieces, or all blocked) loses. 40 moves each (80 in all) without a capture or a man moving is
     a draw.

   Squares 0–63 (row × 8 + column); a move is { path: [from, to, (to, …)] }. Cells: 0 empty, 1 / 2 a man of seat 0 / 1,
   3 / 4 a king. The same rules as the server's app/rules/checkers.py.

   The computer: Easy plays any legal move; Medium looks 3 moves ahead counting pieces (a king is worth more);
   Hard 6 moves ahead and also likes advancing men, the middle and keeping its back row. */
(function () {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : self.BoardKit;

  var BONUS_PIECE = 30, DRAW_PLIES = 80;

  function owner(v) { return v === 0 ? -1 : (v - 1) % 2; }
  function isKing(v) { return v >= 3; }
  function fwdOf(first, seat) { return seat === first ? -1 : 1; }

  function jumps(board, sq, seat, king, fwd, captured, start) {
    var out = [], r = sq >> 3, c = sq & 7;
    var dirs = king ? [[fwd, -1], [fwd, 1], [-fwd, -1], [-fwd, 1]] : [[fwd, -1], [fwd, 1]];
    for (var d = 0; d < dirs.length; d++) {
      var dr = dirs[d][0], dc = dirs[d][1], lr = r + 2 * dr, lc = c + 2 * dc;
      if (lr < 0 || lr > 7 || lc < 0 || lc > 7) continue;
      var mid = (r + dr) * 8 + c + dc, land = lr * 8 + lc, mv = board[mid];
      if (mv === 0 || owner(mv) === seat || captured.indexOf(mid) >= 0) continue;
      if (board[land] !== 0 && land !== start) continue;
      if (!king && lr === (fwd === -1 ? 0 : 7)) { out.push([land]); continue; }
      var more = jumps(board, land, seat, king, fwd, captured.concat([mid]), start);
      if (more.length) for (var k = 0; k < more.length; k++) out.push([land].concat(more[k]));
      else out.push([land]);
    }
    return out;
  }
  /** Every legal move of `seat` (paths), captures only when there is one. */
  function allMoves(board, first, seat) {
    var fwd = fwdOf(first, seat), js = [], steps = [], sq, k;
    for (sq = 0; sq < 64; sq++) {
      var v = board[sq];
      if (v === 0 || owner(v) !== seat) continue;
      var king = isKing(v), jp = jumps(board, sq, seat, king, fwd, [], sq);
      for (k = 0; k < jp.length; k++) js.push([sq].concat(jp[k]));
      if (js.length) continue;
      var r = sq >> 3, c = sq & 7, drs = king ? [fwd, -fwd] : [fwd];
      for (var a = 0; a < drs.length; a++) for (var dc = -1; dc <= 1; dc += 2) {
        var nr = r + drs[a], nc = c + dc;
        if (nr >= 0 && nr < 8 && nc >= 0 && nc < 8 && board[nr * 8 + nc] === 0) steps.push([sq, nr * 8 + nc]);
      }
    }
    return js.length ? js : steps;
  }
  /** Play a path on a board (in place). Returns { captured, crowned, man }. */
  function playPath(board, first, seat, path) {
    var piece = board[path[0]], cap = 0;
    board[path[0]] = 0;
    for (var i = 1; i < path.length; i++) {
      var a = path[i - 1], z = path[i];
      if (Math.abs((a >> 3) - (z >> 3)) === 2) { board[(a + z) >> 1] = 0; cap++; }
    }
    var end = path[path.length - 1];
    var crowned = !isKing(piece) && (end >> 3) === (fwdOf(first, seat) === -1 ? 0 : 7);
    board[end] = crowned ? piece + 2 : piece;
    return { captured: cap, crowned: crowned, man: !isKing(piece) };
  }
  function samePath(a, b) {
    if (a.length !== b.length) return false;
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
    return true;
  }
  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }

  var R = {
    id: "checkers", STATE_VERSION: 1, OPTIONS: {},
    init: function (s) {
      var b = [], other = 1 - s.first;
      for (var i = 0; i < 64; i++) {
        var r = i >> 3, c = i & 7;
        b.push((r + c) % 2 === 1 ? (r <= 2 ? other + 1 : r >= 5 ? s.first + 1 : 0) : 0);
      }
      s.board = b; s.quiet = 0; s.last = []; s.moveAt = -100; s.taken = [];
    },
    moves: function (s, seat) {
      if (s.over || seat !== s.turn) return [];
      return allMoves(s.board, s.first, seat).map(function (p) { return { path: p }; });
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1 || !Array.isArray(move.path) ||
        move.path.length < 2 || move.path.length > 13) throw new Error("A move is the squares the piece lands on.");
      var path = move.path, ok = false, all = allMoves(s.board, s.first, seat);
      for (var i = 0; i < path.length; i++) if (!isWhole(path[i], 0, 63)) throw new Error("A move is the squares the piece lands on.");
      for (i = 0; i < all.length; i++) if (samePath(all[i], path)) { ok = true; break; }
      if (!ok) throw new Error("That move isn't allowed (a capture may be possible — captures are compulsory).");
      var before = s.board.slice();
      var res = playPath(s.board, s.first, seat, path);
      s.taken = [];
      for (i = 0; i < 64; i++) if (before[i] && !s.board[i] && i !== path[0]) s.taken.push({ sq: i, v: before[i] });
      s.quiet = res.captured || res.man ? 0 : s.quiet + 1;
      s.ply++; s.last = path.slice(); s.moveAt = s.updates;
      var evs = [{ type: res.captured ? "capture" : "step", seat: seat, n: res.captured }];
      if (res.crowned) evs.push({ type: "king" });
      var other = 1 - seat;
      s.turn = other;
      if (!allMoves(s.board, s.first, other).length) { s.over = true; s.winner = seat; }
      else if (s.quiet >= DRAW_PLIES) { s.over = true; s.winner = -1; }
      return evs;
    },
    bonus: function (s, seat) { return BONUS_PIECE * pieces(s.board, seat); },
    digest: function (s) { return { board: s.board, turn: s.turn, over: s.over, winner: s.winner, quiet: s.quiet }; },
    counts: function (s) { return [pieces(s.board, 0), pieces(s.board, 1)]; },
    cursorStart: function (s) { return s.me === s.first || s.mode !== "phones" ? 5 * 8 + 2 : 2 * 8 + 1; },
    cursorMove: function (s, dir) {
      // seat 2 of a phones match sees the board turned round: its arrows turn round too
      var flip = s.mode === "phones" && s.me !== s.first;
      var r = s.cursor >> 3, c = s.cursor & 7, dr = 0, dc = 0;
      if (dir === "up") dr = -1; else if (dir === "down") dr = 1; else if (dir === "left") dc = -1; else if (dir === "right") dc = 1;
      if (flip) { dr = -dr; dc = -dc; }
      r = Math.max(0, Math.min(7, r + dr)); c = Math.max(0, Math.min(7, c + dc));
      s.cursor = r * 8 + c;
    },
    activate: function (s, seat) { return R.tap(s, s.cursor, seat); },
    tap: function (s, sq, seat) {
      s.cursor = sq;
      if (seat < 0) return null;
      var all = allMoves(s.board, s.first, seat), v = s.board[sq], i;
      var mine = v !== 0 && owner(v) === seat && all.some(function (p) { return p[0] === sq; });
      if (s.sel && s.sel.length === 1 && s.sel[0] === sq) { s.sel = null; return { events: [{ type: "cursor" }] }; }
      if (mine && (!s.sel || s.sel.length === 1)) { s.sel = [sq]; return { events: [{ type: "pick" }] }; }
      if (!s.sel) return { events: [{ type: "nope" }] };
      var want = s.sel.concat([sq]), cands = [];
      for (i = 0; i < all.length; i++) {
        var p = all[i], pre = true;
        if (p.length < want.length) continue;
        for (var k = 0; k < want.length; k++) if (p[k] !== want[k]) { pre = false; break; }
        if (pre) cands.push(p);
      }
      if (!cands.length) return { events: [{ type: "nope" }] };
      if (cands.length === 1) return { move: { path: cands[0] } };
      for (i = 0; i < cands.length; i++) if (cands[i].length === want.length) return { move: { path: cands[i] } };
      s.sel = want;
      return { events: [{ type: "pick" }] };
    },
    alt: function (s) { s.sel = null; return { events: [{ type: "cursor" }] }; },
    keep: function (old, s) { if (s.number > old.number) s.moveAt = s.updates; },
    ai: function (s, seat, level) { return { path: aiPath(s, seat, level) }; },
    check: function (d) { return Array.isArray(d.board) && d.board.length === 64 && typeof d.quiet === "number"; },
    summary: function (s, names) {
      return [names[0] + " " + pieces(s.board, 0) + " · " + names[1] + " " + pieces(s.board, 1) + " pieces left" + (s.over && s.winner === -1 ? " (40 moves without a capture)" : "")];
    },
  };

  function pieces(board, seat) { var n = 0; for (var i = 0; i < 64; i++) if (board[i] && owner(board[i]) === seat) n++; return n; }

  // ---------- the computer ----------
  function evaluate(board, first, seat, rich) {
    var sc = 0;
    for (var i = 0; i < 64; i++) {
      var v = board[i];
      if (!v) continue;
      var who = owner(v), sgn = who === seat ? 1 : -1, val = isKing(v) ? 175 : 100;
      if (rich) {
        var r = i >> 3, c = i & 7, fwd = fwdOf(first, who);
        if (!isKing(v)) {
          val += (fwd === -1 ? 7 - r : r) * 3;                                   // advanced men
          if (r === (fwd === -1 ? 7 : 0)) val += 6;                              // the back row guarded
        }
        if (c >= 2 && c <= 5 && r >= 2 && r <= 5) val += 4;                      // the middle
      }
      sc += sgn * val;
    }
    return sc;
  }
  function search(board, first, toMove, me, depth, alpha, beta, rich) {
    var ms = allMoves(board, first, toMove);
    if (!ms.length) return toMove === me ? -100000 - depth : 100000 + depth;
    if (depth === 0) return evaluate(board, first, me, rich);
    var maxing = toMove === me, best = maxing ? -Infinity : Infinity;
    for (var i = 0; i < ms.length; i++) {
      var b = board.slice();
      playPath(b, first, toMove, ms[i]);
      // a capture that must carry on is already one move; extend the search a little after captures
      var v = search(b, first, 1 - toMove, me, depth - 1, alpha, beta, rich);
      if (maxing) { if (v > best) best = v; if (best > alpha) alpha = best; }
      else { if (v < best) best = v; if (best < beta) beta = best; }
      if (alpha >= beta) break;
    }
    return best;
  }
  function aiPath(s, seat, level) {
    var ms = allMoves(s.board, s.first, seat);
    if (ms.length === 1 || level <= 1) return BK.pick(s, ms);
    var depth = level === 2 ? 3 : 6, rich = level >= 3, best = -Infinity, bestMoves = [];
    for (var i = 0; i < ms.length; i++) {
      var b = s.board.slice();
      playPath(b, s.first, seat, ms[i]);
      var v = search(b, s.first, 1 - seat, seat, depth - 1, best - 1, Infinity, rich);
      if (v > best) { best = v; bestMoves = [ms[i]]; } else if (v === best) bestMoves.push(ms[i]);
    }
    return BK.pick(s, bestMoves);
  }

  var L = BK.game(R);
  L.BONUS_PIECE = BONUS_PIECE; L.DRAW_PLIES = DRAW_PLIES; L.allMoves = allMoves; L.owner = owner; L.isKing = isKing;
  L.pieces = pieces; L.aiPath = aiPath; L.playPath = playPath;
  if (typeof module === "object" && module.exports) module.exports = L; else self.CheckersLogic = L;
})();
