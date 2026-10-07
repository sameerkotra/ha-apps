/* Household Arcade — Chess rules and the computer's engine (pure: no DOM, deterministic; no Math.random or Date).
   The same rules as the server's app/rules/chess.py, move for move (the same legal moves, the same position after
   every move — FEN — and the same end):

   - FIDE moves: castling (king and rook unmoved, the squares between empty, the king not in check and not crossing or
     landing on an attacked square), en passant (only right after the double step), promotion to a queen, rook, bishop
     or knight (the move names it: { promo: "q" | "r" | "b" | "n" }); a move may never leave your king in check.
   - No legal move: checkmate (in check) or stalemate (a draw). Draws are automatic: the third repetition of a position
     (same pieces, side to move, castling rights, en passant possible), 50 moves each without a capture or a pawn move,
     and material that can't mate (bare kings, a lone bishop or knight, or only bishops on one colour of square).
   - White moves first. Two phones: White's seat comes from the seed (seed % 2, as the server); against the computer
     the start screen's Side (White, Black or either — from the seed); one screen: Player 1 is White.

   Squares are "a1"–"h8"; a move is { from: "e2", to: "e4" } (+ promo). Inside, a 10 × 12 mailbox (Int8Array) with
   pieces 1–6 (pawn, knight, bishop, rook, queen, king) for White and 9–14 for Black, moves packed into numbers.

   The computer (think): negamax with alpha-beta, iterative deepening, a quiescence search of captures, a transposition
   table (Zobrist keys from a fixed generator), MVV-LVA, killer and history move ordering, and an evaluation of
   material and piece squares (the king's table changing for the endgame). Strength comes from fixed node budgets, not
   the clock, so the same position always gets the same move: Easy looks 2 plies and picks among the moves within
   ~1.5 pawns of the best (and now and then any move); Medium 3 plies (+ captures) with a little seeded variety; Hard
   deepens up to 7 plies within 220,000 nodes (≈ 1 s on a phone, in a Web Worker in the browser: chess-worker.js). */
(function () {
  "use strict";
  var isNode = typeof module === "object" && module.exports;
  var BK = isNode ? require("./boardkit.js") : self.BoardKit;

  var START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  var FILES = "abcdefgh";
  var EMPTY = 0, OFF = 7, P = 1, N = 2, B = 3, R_ = 4, Q = 5, K = 6, BLACK = 8;
  var CHARS = ".PNBRQK..pnbrqk";
  var KNIGHT = [-21, -19, -12, -8, 8, 12, 19, 21], KING = [-11, -10, -9, -1, 1, 9, 10, 11];
  var DIAG = [-11, -9, 9, 11], ORTHO = [-10, -1, 1, 10];
  var PROMO_TYPES = [Q, R_, B, N], PROMO_CH = { 5: "q", 4: "r", 3: "b", 2: "n" }, CH_PROMO = { q: Q, r: R_, b: B, n: N };
  var CASTLE_LOSS = new Int8Array(120);       // castling rights lost when a piece moves from / to a square
  CASTLE_LOSS[95] = 3; CASTLE_LOSS[25] = 12; CASTLE_LOSS[98] = 1; CASTLE_LOSS[91] = 2; CASTLE_LOSS[28] = 4; CASTLE_LOSS[21] = 8;
  var VALUE = [0, 1, 3, 3, 5, 9, 0];
  var BONUS_POINT = 10;

  function mb(i) { return 21 + ((i / 8) | 0) * 10 + (i % 8); }
  function ext(m) { var r = ((m - 21) / 10) | 0, c = (m - 21) % 10; return r * 8 + c; }
  function sqName(i) { return FILES[i % 8] + String(8 - ((i / 8) | 0)); }
  function sqIndex(name) {
    if (typeof name !== "string" || name.length !== 2) return -1;
    var f = FILES.indexOf(name[0]), r = "12345678".indexOf(name[1]);
    if (f < 0 || r < 0) return -1;
    return (7 - r) * 8 + f;
  }
  function colourOf(p) { return p & BLACK; }
  function typeOf(p) { return p & 7; }

  // ---------- positions ----------
  function fromFen(fen) {
    var parts = fen.split(" "), rows = parts[0].split("/"), b = new Int8Array(120), i;
    for (i = 0; i < 120; i++) b[i] = OFF;
    var kings = [0, 0];
    for (var r = 0; r < 8; r++) {
      var c = 0, row = rows[r];
      for (var k = 0; k < row.length; k++) {
        var ch = row[k];
        if (ch >= "1" && ch <= "8") { for (var e = 0; e < +ch; e++) b[21 + r * 10 + c++] = EMPTY; }
        else {
          var pc = CHARS.indexOf(ch);
          b[21 + r * 10 + c] = pc;
          if (pc === K) kings[0] = 21 + r * 10 + c; else if (pc === K + BLACK) kings[1] = 21 + r * 10 + c;
          c++;
        }
      }
    }
    var cs = parts[2], castle = 0;
    if (cs.indexOf("K") >= 0) castle |= 1; if (cs.indexOf("Q") >= 0) castle |= 2;
    if (cs.indexOf("k") >= 0) castle |= 4; if (cs.indexOf("q") >= 0) castle |= 8;
    var pos = { b: b, white: parts[1] === "w", castle: castle, ep: parts[3] === "-" ? 0 : mb(sqIndex(parts[3])),
      half: +parts[4], full: +parts[5], kings: kings, hash: 0, hash2: 0, stack: [] };
    rehash(pos);
    return pos;
  }
  function boardText(pos) {
    var out = [];
    for (var r = 0; r < 8; r++) {
      var row = "", empty = 0;
      for (var c = 0; c < 8; c++) {
        var p = pos.b[21 + r * 10 + c];
        if (p === EMPTY) empty++;
        else { if (empty) { row += empty; empty = 0; } row += CHARS[p]; }
      }
      out.push(row + (empty ? empty : ""));
    }
    return out.join("/");
  }
  function castleText(c) { var s = (c & 1 ? "K" : "") + (c & 2 ? "Q" : "") + (c & 4 ? "k" : "") + (c & 8 ? "q" : ""); return s || "-"; }
  function toFen(pos) {
    return [boardText(pos), pos.white ? "w" : "b", castleText(pos.castle), pos.ep ? sqName(ext(pos.ep)) : "-", pos.half, pos.full].join(" ");
  }

  // ---------- Zobrist keys (two 32-bit halves, from a fixed generator) ----------
  var ZK = (function () {
    var st = { rng: 0x2545F491 }, n = 15 * 120 + 16 + 120 + 1, a = new Int32Array(n), b2 = new Int32Array(n);
    for (var i = 0; i < n; i++) { a[i] = (BK.rand(st) * 4294967296) | 0; b2[i] = (BK.rand(st) * 4294967296) | 0; }
    return { a: a, b: b2, CASTLE: 15 * 120, EP: 15 * 120 + 16, SIDE: 15 * 120 + 16 + 120 };
  })();
  function rehash(pos) {
    var h = 0, h2 = 0;
    for (var m = 21; m < 99; m++) { var p = pos.b[m]; if (p !== EMPTY && p !== OFF) { h ^= ZK.a[p * 120 + m]; h2 ^= ZK.b[p * 120 + m]; } }
    h ^= ZK.a[ZK.CASTLE + pos.castle]; h2 ^= ZK.b[ZK.CASTLE + pos.castle];
    if (pos.ep) { h ^= ZK.a[ZK.EP + pos.ep]; h2 ^= ZK.b[ZK.EP + pos.ep]; }
    if (!pos.white) { h ^= ZK.a[ZK.SIDE]; h2 ^= ZK.b[ZK.SIDE]; }
    pos.hash = h; pos.hash2 = h2;
  }

  // ---------- attacks and moves ----------
  function attacked(b, sq, byWhite) {
    var pawn, kn, kg, bq1, bq2, rq1, rq2, d, t, i, p;
    if (byWhite) { if (b[sq + 9] === P || b[sq + 11] === P) return true; kn = N; kg = K; bq1 = B; bq2 = Q; rq1 = R_; rq2 = Q; }
    else { if (b[sq - 9] === P + BLACK || b[sq - 11] === P + BLACK) return true; kn = N + BLACK; kg = K + BLACK; bq1 = B + BLACK; bq2 = Q + BLACK; rq1 = R_ + BLACK; rq2 = Q + BLACK; }
    for (i = 0; i < 8; i++) { if (b[sq + KNIGHT[i]] === kn) return true; if (b[sq + KING[i]] === kg) return true; }
    for (i = 0; i < 4; i++) {
      d = DIAG[i]; t = sq + d; while (b[t] === EMPTY) t += d; p = b[t]; if (p === bq1 || p === bq2) return true;
      d = ORTHO[i]; t = sq + d; while (b[t] === EMPTY) t += d; p = b[t]; if (p === rq1 || p === rq2) return true;
    }
    void pawn;
    return false;
  }
  function enc(f, t, promo, flag) { return f | (t << 7) | (promo << 14) | (flag << 17); }
  function mFrom(m) { return m & 127; }
  function mTo(m) { return (m >> 7) & 127; }
  function mPromo(m) { return (m >> 14) & 7; }
  function mFlag(m) { return (m >> 17) & 3; }       // 0 plain, 1 en passant, 2 castling, 3 double step

  function theirs(p, white) { return p !== EMPTY && p !== OFF && (white ? p >= BLACK : p < BLACK); }

  /** Pseudo-legal moves into `out` (only captures and promotions when `caps`). */
  function pseudo(pos, out, caps) {
    var b = pos.b, w = pos.white, mine = w ? 0 : BLACK, fwd = w ? -10 : 10, startRow = w ? 6 : 1, lastRow = w ? 0 : 7;
    for (var f = 21; f < 99; f++) {
      var pc = b[f];
      if (pc === EMPTY || pc === OFF || (pc & BLACK) !== mine) continue;
      var k = pc & 7, t, i, d;
      if (k === P) {
        t = f + fwd;
        var row = ((t - 21) / 10) | 0;
        if (b[t] === EMPTY) {
          if (row === lastRow) for (i = 0; i < 4; i++) out.push(enc(f, t, PROMO_TYPES[i], 0));
          else if (!caps) {
            out.push(enc(f, t, 0, 0));
            if ((((f - 21) / 10) | 0) === startRow && b[t + fwd] === EMPTY) out.push(enc(f, t + fwd, 0, 3));
          }
        }
        for (var s2 = -1; s2 <= 1; s2 += 2) {
          t = f + fwd + s2;
          if (theirs(b[t], w)) {
            if ((((t - 21) / 10) | 0) === lastRow) for (i = 0; i < 4; i++) out.push(enc(f, t, PROMO_TYPES[i], 0));
            else out.push(enc(f, t, 0, 0));
          } else if (pos.ep && t === pos.ep) out.push(enc(f, t, 0, 1));
        }
      } else if (k === N || k === K) {
        var dirs = k === N ? KNIGHT : KING;
        for (i = 0; i < 8; i++) {
          t = f + dirs[i];
          var q = b[t];
          if (q === EMPTY ? !caps : theirs(q, w)) out.push(enc(f, t, 0, 0));
        }
        if (k === K && !caps) castles(pos, f, out);
      } else {
        var list = k === B ? DIAG : k === R_ ? ORTHO : null;
        for (var j = 0; j < (list ? 4 : 8); j++) {
          d = list ? list[j] : j < 4 ? DIAG[j] : ORTHO[j - 4];
          t = f + d;
          while (b[t] === EMPTY) { if (!caps) out.push(enc(f, t, 0, 0)); t += d; }
          if (theirs(b[t], w)) out.push(enc(f, t, 0, 0));
        }
      }
    }
    return out;
  }
  function castles(pos, f, out) {
    var b = pos.b, w = pos.white, home = w ? 95 : 25;
    if (f !== home || attacked(b, home, !w)) return;
    var rook = w ? R_ : R_ + BLACK, kRight = w ? 1 : 4, qRight = w ? 2 : 8;
    if ((pos.castle & kRight) && b[home + 1] === EMPTY && b[home + 2] === EMPTY && b[home + 3] === rook &&
      !attacked(b, home + 1, !w) && !attacked(b, home + 2, !w)) out.push(enc(home, home + 2, 0, 2));
    if ((pos.castle & qRight) && b[home - 1] === EMPTY && b[home - 2] === EMPTY && b[home - 3] === EMPTY && b[home - 4] === rook &&
      !attacked(b, home - 1, !w) && !attacked(b, home - 2, !w)) out.push(enc(home, home - 2, 0, 2));
  }

  function zx(pos, idx) { pos.hash ^= ZK.a[idx]; pos.hash2 ^= ZK.b[idx]; }
  function make(pos, m) {
    var b = pos.b, f = mFrom(m), t = mTo(m), pr = mPromo(m), flag = mFlag(m), piece = b[f], cap = b[t];
    pos.stack.push(f, t, piece, cap, pos.castle, pos.ep, pos.half, pos.full, pos.hash, pos.hash2, flag, pos.kings[0], pos.kings[1]);
    var placed = pr ? (pos.white ? pr : pr + BLACK) : piece;
    zx(pos, piece * 120 + f);
    if (cap !== EMPTY) zx(pos, cap * 120 + t);
    b[t] = placed; b[f] = EMPTY;
    zx(pos, placed * 120 + t);
    if (flag === 1) { var vs = t + (pos.white ? 10 : -10); zx(pos, b[vs] * 120 + vs); b[vs] = EMPTY; cap = pos.white ? P + BLACK : P; }
    else if (flag === 2) {
      var rf = t > f ? t + 1 : t - 2, rt = t > f ? t - 1 : t + 1, rk = b[rf];
      zx(pos, rk * 120 + rf); zx(pos, rk * 120 + rt); b[rt] = rk; b[rf] = EMPTY;
    }
    if ((piece & 7) === K) pos.kings[pos.white ? 0 : 1] = t;
    zx(pos, ZK.CASTLE + pos.castle);
    pos.castle &= ~(CASTLE_LOSS[f] | CASTLE_LOSS[t]);
    zx(pos, ZK.CASTLE + pos.castle);
    if (pos.ep) zx(pos, ZK.EP + pos.ep);
    pos.ep = flag === 3 ? (f + t) >> 1 : 0;
    if (pos.ep) zx(pos, ZK.EP + pos.ep);
    pos.half = (piece & 7) === P || cap !== EMPTY ? 0 : pos.half + 1;
    if (!pos.white) pos.full++;
    pos.white = !pos.white;
    zx(pos, ZK.SIDE);
  }
  function unmake(pos) {
    var st = pos.stack, n = st.length;
    var kb = st[n - 1], kw = st[n - 2], flag = st[n - 3], h2 = st[n - 4], h = st[n - 5], full = st[n - 6], half = st[n - 7],
      ep = st[n - 8], castle = st[n - 9], cap = st[n - 10], piece = st[n - 11], t = st[n - 12], f = st[n - 13];
    st.length = n - 13;
    var b = pos.b;
    pos.white = !pos.white;
    b[f] = piece; b[t] = cap;
    if (flag === 1) b[t + (pos.white ? 10 : -10)] = pos.white ? P + BLACK : P;
    else if (flag === 2) {
      var rf = t > f ? t + 1 : t - 2, rt = t > f ? t - 1 : t + 1;
      b[rf] = b[rt]; b[rt] = EMPTY;
    }
    pos.castle = castle; pos.ep = ep; pos.half = half; pos.full = full; pos.hash = h; pos.hash2 = h2;
    pos.kings[0] = kw; pos.kings[1] = kb;
  }
  function inCheck(pos) { return attacked(pos.b, pos.kings[pos.white ? 0 : 1], !pos.white); }
  function legalMoves(pos) {
    var out = [], ms = pseudo(pos, [], false), us = pos.white ? 0 : 1;
    for (var i = 0; i < ms.length; i++) {
      make(pos, ms[i]);
      if (!attacked(pos.b, pos.kings[us], pos.white)) out.push(ms[i]);
      unmake(pos);
    }
    return out;
  }
  function perft(fenOrPos, depth) {
    var pos = typeof fenOrPos === "string" ? fromFen(fenOrPos) : fenOrPos;
    if (depth === 0) return 1;
    var ms = legalMoves(pos);
    if (depth === 1) return ms.length;
    var n = 0;
    for (var i = 0; i < ms.length; i++) { make(pos, ms[i]); n += perft(pos, depth - 1); unmake(pos); }
    return n;
  }
  function asMove(m) {
    var o = { from: sqName(ext(mFrom(m))), to: sqName(ext(mTo(m))) };
    if (mPromo(m)) o.promo = PROMO_CH[mPromo(m)];
    return o;
  }
  function key(pos) {
    var ep = "-";
    if (pos.ep) { var ms = legalMoves(pos); for (var i = 0; i < ms.length; i++) if (mFlag(ms[i]) === 1) { ep = sqName(ext(pos.ep)); break; } }
    return [boardText(pos), pos.white ? "w" : "b", castleText(pos.castle), ep].join(" ");
  }
  function insufficient(pos) {
    var minors = [];
    for (var m = 21; m < 99; m++) {
      var p = pos.b[m];
      if (p === EMPTY || p === OFF || (p & 7) === K) continue;
      var t = p & 7;
      if (t === P || t === R_ || t === Q) return false;
      var e = ext(m);
      minors.push([t, (((e / 8) | 0) + (e % 8)) % 2]);
    }
    if (minors.length <= 1) return true;
    for (var i = 0; i < minors.length; i++) if (minors[i][0] !== B || minors[i][1] !== minors[0][1]) return false;
    return true;
  }
  function materialOf(fen, white) {
    var board = fen.split(" ")[0], n = 0;
    for (var i = 0; i < board.length; i++) {
      var pc = CHARS.indexOf(board[i]);
      if (pc > 0 && pc !== OFF && ((pc & BLACK) === 0) === white) n += VALUE[pc & 7];
    }
    return n;
  }

  // =====================================================================
  // The computer
  // =====================================================================
  var MV = [0, 100, 320, 330, 500, 900, 0];
  // piece-square tables from White's side, a8 first (row 0 = rank 8)
  var PST = {
    1: [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10, 5, 5, 10, 25, 25, 10, 5, 5,
      0, 0, 0, 22, 22, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5, 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    2: [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30, -30, 5, 15, 20, 20, 15, 5, -30,
      -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30, -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
    3: [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10, -10, 5, 5, 10, 10, 5, 5, -10,
      -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10, -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20],
    4: [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5,
      -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0],
    5: [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10, -5, 0, 5, 5, 5, 5, 0, -5,
      0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10, -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20],
    6: [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
      -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10, 20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
    7: [-50, -40, -30, -20, -20, -30, -40, -50, -30, -20, -10, 0, 0, -10, -20, -30, -30, -10, 20, 30, 30, 20, -10, -30, -30, -10, 30, 40, 40, 30, -10, -30,
      -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 20, 30, 30, 20, -10, -30, -30, -30, 0, 0, 0, 0, -30, -30, -50, -30, -30, -30, -30, -30, -30, -50],
  };
  /** The position's value for the side to move (centipawns). */
  function evaluate(pos) {
    var b = pos.b, sc = 0, phase = 0, wk = 0, bk = 0, m;
    for (m = 21; m < 99; m++) { var p = b[m]; if (p === EMPTY || p === OFF) continue; var t = p & 7; if (t === N || t === B) phase += 1; else if (t === R_) phase += 2; else if (t === Q) phase += 4; }
    var endgame = phase <= 6;
    for (m = 21; m < 99; m++) {
      var pc = b[m];
      if (pc === EMPTY || pc === OFF) continue;
      var ty = pc & 7, e = ext(m), white = pc < BLACK, idx = white ? e : (7 - ((e / 8) | 0)) * 8 + (e % 8);
      var v = MV[ty] + (ty === K ? PST[endgame ? 7 : 6][idx] : PST[ty][idx]);
      if (ty === K) { if (white) wk = m; else bk = m; }
      sc += white ? v : -v;
    }
    if (endgame && wk && bk) {                     // in the endgame, the side ahead brings its king closer
      var dist = Math.abs(((wk - 21) / 10 | 0) - ((bk - 21) / 10 | 0)) + Math.abs((wk - 21) % 10 - (bk - 21) % 10);
      if (sc > 200) sc += (14 - dist) * 4; else if (sc < -200) sc -= (14 - dist) * 4;
    }
    return pos.white ? sc : -sc;
  }

  var MATE = 100000, TT_SIZE = 1 << 18, TT_MASK = TT_SIZE - 1;
  function Engine() {
    this.ttKey = new Int32Array(TT_SIZE); this.ttKey2 = new Int32Array(TT_SIZE);
    this.ttMove = new Int32Array(TT_SIZE); this.ttScore = new Int32Array(TT_SIZE);
    this.ttDepth = new Int8Array(TT_SIZE); this.ttFlag = new Int8Array(TT_SIZE);
    this.history = new Int32Array(15 * 120);
    this.killers = [];
  }
  Engine.prototype.reset = function () {
    this.ttKey.fill(0); this.ttKey2.fill(0); this.ttMove.fill(0); this.ttDepth.fill(0); this.ttFlag.fill(0); this.history.fill(0);
    this.killers = [];
  };
  Engine.prototype.order = function (pos, ms, ttm, ply) {
    var b = pos.b, sc = new Array(ms.length), k = this.killers[ply] || [0, 0];
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i], cap = b[mTo(m)], s;
      if (m === ttm) s = 10000000;
      else if (cap !== EMPTY || mFlag(m) === 1) s = 1000000 + MV[cap & 7 || P] * 10 - MV[b[mFrom(m)] & 7] / 10;
      else if (mPromo(m)) s = 900000 + MV[mPromo(m)];
      else if (m === k[0]) s = 800000; else if (m === k[1]) s = 790000;
      else s = this.history[b[mFrom(m)] * 120 + mTo(m)];
      sc[i] = s;
    }
    // insertion sort, best first (stable, so ties keep the generator's order)
    for (i = 1; i < ms.length; i++) {
      var mv = ms[i], sv = sc[i], j = i - 1;
      while (j >= 0 && sc[j] < sv) { ms[j + 1] = ms[j]; sc[j + 1] = sc[j]; j--; }
      ms[j + 1] = mv; sc[j + 1] = sv;
    }
    return ms;
  };
  Engine.prototype.quiesce = function (pos, alpha, beta, ply) {
    this.nodes++;
    var stand = evaluate(pos);
    if (stand >= beta) return stand;
    if (stand > alpha) alpha = stand;
    if (ply > 40) return stand;
    var ms = this.order(pos, pseudo(pos, [], true), 0, ply), us = pos.white ? 0 : 1;
    for (var i = 0; i < ms.length; i++) {
      make(pos, ms[i]);
      if (attacked(pos.b, pos.kings[us], pos.white)) { unmake(pos); continue; }
      var v = -this.quiesce(pos, -beta, -alpha, ply + 1);
      unmake(pos);
      if (v >= beta) return v;
      if (v > alpha) alpha = v;
      if (this.stop) return alpha;
    }
    return alpha;
  };
  Engine.prototype.repeated = function (pos) {
    // the same position earlier in this line or in the game (since the last capture or pawn move): a draw
    var st = pos.stack, n = st.length, back = Math.min(pos.half, n / 13);
    for (var k = 2; k <= back; k += 2) {
      var base = n - 13 * k;
      if (st[base + 8] === pos.hash && st[base + 9] === pos.hash2) return true;
    }
    for (var i = 0; i < this.past.length; i += 2) if (this.past[i] === pos.hash && this.past[i + 1] === pos.hash2) return true;
    return false;
  };
  Engine.prototype.search = function (pos, depth, alpha, beta, ply) {
    if (this.nodes >= this.limit) { this.stop = true; return 0; }
    if (ply > 0 && (pos.half >= 100 || this.repeated(pos))) return 0;
    var check = inCheck(pos);
    if (check) depth++;
    if (depth <= 0) return this.quiesce(pos, alpha, beta, ply);
    this.nodes++;
    var slot = pos.hash & TT_MASK, ttm = 0, a0 = alpha;
    if (this.ttKey[slot] === pos.hash && this.ttKey2[slot] === pos.hash2) {
      ttm = this.ttMove[slot];
      if (ply > 0 && this.ttDepth[slot] >= depth) {
        var tsc = this.ttScore[slot], fl = this.ttFlag[slot];
        if (fl === 0 || (fl === 1 && tsc >= beta) || (fl === 2 && tsc <= alpha)) return tsc;
      }
    }
    var ms = this.order(pos, pseudo(pos, [], false), ttm, ply), us = pos.white ? 0 : 1, best = -MATE * 2, bestM = 0, legal = 0;
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      make(pos, m);
      if (attacked(pos.b, pos.kings[us], pos.white)) { unmake(pos); continue; }
      legal++;
      var v = -this.search(pos, depth - 1, -beta, -alpha, ply + 1);
      unmake(pos);
      if (this.stop) return 0;
      if (v > best) { best = v; bestM = m; }
      if (v > alpha) alpha = v;
      if (alpha >= beta) {
        if (pos.b[mTo(m)] === EMPTY && mFlag(m) !== 1) {
          var k = this.killers[ply] || (this.killers[ply] = [0, 0]);
          if (k[0] !== m) { k[1] = k[0]; k[0] = m; }
          this.history[pos.b[mFrom(m)] * 120 + mTo(m)] += depth * depth;
        }
        break;
      }
    }
    if (!legal) return check ? -MATE + ply : 0;
    this.ttKey[slot] = pos.hash; this.ttKey2[slot] = pos.hash2; this.ttMove[slot] = bestM; this.ttScore[slot] = best;
    this.ttDepth[slot] = depth; this.ttFlag[slot] = best >= beta ? 1 : best <= a0 ? 2 : 0;
    return best;
  };
  /** Scores of every root move at `depth` (null if the node budget ran out during it). */
  /** Scores of every root move at `depth` (null if the node budget ran out during it). The moves within `margin` of
      the best get exact scores; the others only an upper bound below that (enough to leave them out). */
  Engine.prototype.root = function (pos, depth, ms, margin) {
    var out = [], floor = -MATE * 2;
    for (var i = 0; i < ms.length; i++) {
      make(pos, ms[i]);
      var v = -this.search(pos, depth - 1, -MATE * 2, -floor, 1);
      unmake(pos);
      if (this.stop) return null;
      out.push(v);
      if (v - margin - 1 > floor) floor = v - margin - 1;
    }
    return out;
  };
  var ENGINE = null;
  var LEVELS = { 1: { depth: 2, nodes: 20000, margin: 150, wild: 0.2 }, 2: { depth: 3, nodes: 60000, margin: 15, wild: 0 },
    3: { depth: 7, nodes: 220000, margin: 0, wild: 0 } };

  /** The computer's move for a position (FEN), at a level (1–3); `past` = the earlier positions' FENs since the last
      capture or pawn move (for repetitions); `seed` a number for the variety (the same seed, the same move). */
  function think(fen, level, seed, past) {
    var lv = LEVELS[level] || LEVELS[2];
    var pos = fromFen(fen);
    var ms = legalMoves(pos);
    if (!ms.length) return null;
    var rs = { rng: (seed >>> 0) || 1 };
    if (ms.length === 1) return asMove(ms[0]);
    if (lv.wild && BK.rand(rs) < lv.wild) return asMove(ms[Math.floor(BK.rand(rs) * ms.length)]);
    if (!ENGINE) ENGINE = new Engine();
    var E = ENGINE;
    E.reset();
    E.past = [];
    for (var i = 0; i < (past || []).length; i++) { var pp = fromFen(past[i] + (past[i].split(" ").length < 6 ? " 0 1" : "")); E.past.push(pp.hash, pp.hash2); }
    E.nodes = 0; E.limit = lv.nodes; E.stop = false;
    var order = E.order(pos, ms.slice(), 0, 0), scores = null;
    for (var d = 1; d <= lv.depth; d++) {
      var sc = E.root(pos, d, order, lv.margin);
      if (!sc) break;
      scores = sc;
      // the next depth starts with the best moves first (a stable sort keeps equal ones in order)
      var idx = order.map(function (m, k) { return k; });
      idx.sort(function (a, b2) { return scores[b2] - scores[a] || a - b2; });
      order = idx.map(function (k) { return order[k]; });
      scores = idx.map(function (k) { return sc[k]; });
      if (scores[0] > MATE - 100) break;
    }
    if (!scores) return asMove(order[0]);
    var best = scores[0], pool = [];
    for (i = 0; i < order.length; i++) if (scores[i] >= best - lv.margin) pool.push(order[i]);
    return asMove(pool.length > 1 ? pool[Math.floor(BK.rand(rs) * pool.length)] : order[0]);
  }

  // =====================================================================
  // The game's rules for BoardKit (and the server agreement)
  // =====================================================================
  var thinker = null;
  function applyMove(s, seat, move) {
    if (s.over) throw new Error("The game is over.");
    if (seat !== s.turn) throw new Error("It isn't your move.");
    var keys = move && typeof move === "object" && !Array.isArray(move) ? Object.keys(move) : null;
    if (!keys || keys.indexOf("from") < 0 || keys.indexOf("to") < 0 || keys.some(function (k) { return k !== "from" && k !== "to" && k !== "promo"; })) {
      throw new Error("A move is {from, to} (and promo when a pawn reaches the last row).");
    }
    var pos = fromFen(s.fen), ms = legalMoves(pos), want = BK.canonical(move), found = 0;
    for (var i = 0; i < ms.length; i++) if (BK.canonical(asMove(ms[i])) === want) { found = ms[i]; break; }
    if (!found) {
      var f = sqIndex(move.from), missing = false;
      if (f >= 0 && (pos.b[mb(f)] & 7) === P && move.promo === undefined)
        for (i = 0; i < ms.length; i++) if (mPromo(ms[i]) && ext(mFrom(ms[i])) === f && sqName(ext(mTo(ms[i]))) === move.to) missing = true;
      throw new Error(missing ? "Choose what the pawn becomes." : "That move isn't allowed.");
    }
    var capture = pos.b[mTo(found)] !== EMPTY || mFlag(found) === 1;
    var before = pos.half;
    make(pos, found);
    s.fen = toFen(pos);
    s.ply++;
    s.turn = 1 - seat;
    var k = key(pos);
    s.keys[k] = (s.keys[k] || 0) + 1;
    s.hist = pos.half === 0 ? [] : (s.hist || []).concat([k]);
    void before;
    s.last = { from: ext(mFrom(found)), to: ext(mTo(found)) };
    s.moveAt = s.updates; s.sel = null; s.promo = null;
    var evs = [{ type: capture ? "capture" : mFlag(found) === 2 ? "castle" : "step", seat: seat }];
    if (mPromo(found)) evs.push({ type: "promote" });
    var replies = legalMoves(pos);
    if (!replies.length) {
      s.over = true;
      if (inCheck(pos)) { s.winner = seat; s.reason = "checkmate"; } else { s.winner = -1; s.reason = "stalemate"; }
    } else if (pos.half >= 100) { s.over = true; s.winner = -1; s.reason = "fifty"; }
    else if (s.keys[k] >= 3) { s.over = true; s.winner = -1; s.reason = "repetition"; }
    else if (insufficient(pos)) { s.over = true; s.winner = -1; s.reason = "material"; }
    else if (inCheck(pos)) evs.push({ type: "check" });
    return evs;
  }
  function seatWhite(s, seat) { return seat === s.white; }
  function boardAt(s) { return fromFen(s.fen).b; }
  /** Every legal move as [from, to, promo] (squares 0–63) for the person at this screen, by piece. */
  function legalList(s) {
    var pos = fromFen(s.fen);
    return legalMoves(pos).map(function (m) { return [ext(mFrom(m)), ext(mTo(m)), mPromo(m) ? PROMO_CH[mPromo(m)] : ""]; });
  }

  var R = {
    id: "chess", STATE_VERSION: 1,
    OPTIONS: { side: { choices: ["white", "black", "either"], default: "white" } },
    init: function (s) {
      if (s.mode === "phones") s.white = s.first;
      else if (s.mode === "two") s.white = 0;
      else s.white = s.options.side === "black" ? 1 : s.options.side === "either" ? s.seed % 2 : 0;
      s.first = s.white; s.turn = s.white;
      s.fen = START; s.keys = {}; s.keys[key(fromFen(START))] = 1; s.hist = [];
      s.reason = null; s.last = null; s.sel = null; s.promo = null; s.moveAt = -100; s.promoAt = 0;
      s.aiMove = null; s.aiFor = null; s.aiAsked = null;
    },
    moves: function (s, seat) {
      if (s.over || seat !== s.turn) return [];
      return legalMoves(fromFen(s.fen)).map(asMove);
    },
    apply: applyMove,
    bonus: function (s, seat) { return BONUS_POINT * materialOf(s.fen, seatWhite(s, seat)); },
    digest: function (s) { return { fen: s.fen, turn: s.turn, over: s.over, winner: s.winner, reason: s.reason }; },
    counts: function (s) { return [materialOf(s.fen, seatWhite(s, 0)), materialOf(s.fen, seatWhite(s, 1))]; },
    ai: function (s, seat, level) {
      if (s.aiMove && s.aiFor === s.fen) { var m = s.aiMove; s.aiMove = null; s.aiFor = null; return m; }
      if (thinker) {
        if (s.aiAsked !== s.fen) {
          s.aiAsked = s.fen;
          var st = s, fen = s.fen;
          thinker({ fen: fen, level: level, seed: (s.rng ^ (s.ply * 2654435761)) >>> 0, past: s.hist.slice() }, function (mv) {
            if (st.fen === fen && mv) { st.aiMove = mv; st.aiFor = fen; }
          });
        }
        return null;
      }
      return think(s.fen, level, (s.rng ^ (s.ply * 2654435761)) >>> 0, s.hist.slice());
    },
    restored: function (s) { s.aiMove = null; s.aiFor = null; s.aiAsked = null; s.sel = null; s.promo = null; },
    check: function (d) { return typeof d.fen === "string" && d.fen.split(" ").length === 6 && d.keys && typeof d.keys === "object" && typeof d.white === "number"; },
    cursorStart: function (s) { return flipped(s) ? 12 : 52; },
    cursorMove: function (s, dir) {
      if (s.promo) {
        if (dir === "left" || dir === "up") s.promoAt = (s.promoAt + 3) % 4; else s.promoAt = (s.promoAt + 1) % 4;
        return;
      }
      var r = s.cursor >> 3, c = s.cursor & 7, dr = 0, dc = 0;
      if (dir === "up") dr = -1; else if (dir === "down") dr = 1; else if (dir === "left") dc = -1; else dc = 1;
      if (flipped(s)) { dr = -dr; dc = -dc; }
      r = Math.max(0, Math.min(7, r + dr)); c = Math.max(0, Math.min(7, c + dc));
      s.cursor = r * 8 + c;
    },
    activate: function (s, seat) {
      if (s.promo) return R.tap(s, { promo: "qrbn"[s.promoAt] }, seat);
      return R.tap(s, s.cursor, seat);
    },
    alt: function (s) { s.sel = null; s.promo = null; return { events: [{ type: "cursor" }] }; },
    tap: function (s, target, seat) {
      if (seat < 0) return null;
      if (s.promo) {
        if (target && typeof target === "object" && target.promo) {
          var mv = { from: sqName(s.promo.from), to: sqName(s.promo.to), promo: target.promo };
          s.promo = null;
          return { move: mv };
        }
        s.promo = null; s.sel = null;
        return { events: [{ type: "cursor" }] };
      }
      if (typeof target !== "number") return null;
      s.cursor = target;
      var list = legalList(s), b = boardAt(s), pc = b[mb(target)], own = pc !== EMPTY && ((pc < BLACK) === seatWhite(s, seat));
      var from = list.filter(function (x) { return x[0] === target; });
      if (s.sel === target) { s.sel = null; return { events: [{ type: "cursor" }] }; }
      if (own) {
        if (!from.length) { s.sel = null; return { events: [{ type: "nope" }] }; }
        s.sel = target; return { events: [{ type: "pick" }] };
      }
      if (s.sel === null) return { events: [{ type: "nope" }] };
      var hits = list.filter(function (x) { return x[0] === s.sel && x[1] === target; });
      if (!hits.length) { s.sel = null; return { events: [{ type: "nope" }] }; }
      if (hits.length > 1) { s.promo = { from: s.sel, to: target }; s.promoAt = 0; return { events: [{ type: "pick" }] }; }
      return { move: { from: sqName(s.sel), to: sqName(target) } };
    },
    keep: function (old, s) { if (s.number > old.number) s.moveAt = s.updates; },
    summary: function (s, names) {
      var how = { checkmate: "Checkmate", stalemate: "Stalemate — a draw", fifty: "50 moves without a capture or pawn move — a draw",
        repetition: "The same position three times — a draw", material: "Not enough pieces left to mate — a draw" }[s.reason];
      var lines = [];
      if (how) lines.push(how + " after " + Math.ceil(s.ply / 2) + " moves");
      lines.push(names[0] + " " + materialOf(s.fen, seatWhite(s, 0)) + " · " + names[1] + " " + materialOf(s.fen, seatWhite(s, 1)) + " points of pieces left");
      return lines;
    },
  };
  /** The person at this screen sees their own colour at the bottom: Black's view is the board turned round. */
  function flipped(s) { return s.mode !== "two" && (s.mode === "phones" ? s.me : 0) !== s.white; }

  var L = BK.game(R);
  L.START = START; L.fromFen = fromFen; L.toFen = toFen; L.perft = perft; L.legalMoves = legalMoves; L.asMove = asMove;
  L.think = think; L.key = key; L.insufficient = insufficient; L.inCheck = inCheck; L.materialOf = materialOf; L.LEVELS = LEVELS;
  L.sqName = sqName; L.sqIndex = sqIndex; L.mb = mb; L.ext = ext; L.CHARS = CHARS; L.legalList = legalList; L.boardAt = boardAt;
  L.flipped = flipped; L.evaluate = evaluate;
  L.setThinker = function (fn) { thinker = typeof fn === "function" ? fn : null; };
  if (isNode) module.exports = L; else self.ChessLogic = L;
})();
