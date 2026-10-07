/* Household Arcade — Chess: input and drawing around the rules in chess-logic.js. Registers itself as "chess".

   The pieces are drawn from shapes (no font or picture): a pawn, a rook's battlements, a knight's head, a bishop's
   mitre, a queen's crown and a king's cross; White's are light with a dark edge, Black's dark (Retro LCD: hollow and
   solid). Tap a piece (dots show where it can go), then the square; a pawn reaching the last row asks what it becomes
   (Queen, Rook, Bishop, Knight). The arrows and Space do the same with a square cursor; C lets go. The last move is
   marked, a king in check is ringed. Black's view (you play Black against the computer, or Black's phone) is the
   board turned round, so your pieces are always at the bottom.

   The computer thinks in a Web Worker (chess-worker.js, from this app's own address — the strict CSP allows it); if a
   worker can't be made, it thinks on the page a moment after its turn starts. Either way the move is the same. */
(function (root) {
  "use strict";
  var Logic = root.ChessLogic, BK = root.BoardKit;
  var CS = 27, X0 = 12, Y0 = 40;

  // ---------- the computer, off the main thread ----------
  var workerSrc = null;
  try {
    var cur = root.document && root.document.currentScript && root.document.currentScript.src;
    if (cur && /chess\.js(\?|$)/.test(cur)) workerSrc = cur.replace(/chess\.js(\?|$)/, "chess-worker.js$1");
  } catch (e) { workerSrc = null; }
  var worker = null, workerFailed = false, jobs = {}, nextJob = 1;
  function fallback(job, done) {
    setTimeout(function () { var m = null; try { m = Logic.think(job.fen, job.level, job.seed, job.past); } catch (e) { m = null; } done(m); }, 30);
  }
  function thinker(job, done) {
    if (!workerFailed && workerSrc && typeof root.Worker === "function") {
      try {
        if (!worker) {
          worker = new root.Worker(workerSrc);
          worker.onmessage = function (e) {
            var d = e.data || {}, j = jobs[d.id];
            delete jobs[d.id];
            if (!j) return;
            if (d.move || !d.error) j.done(d.move); else fallback(j.job, j.done);
          };
          worker.onerror = function () {
            workerFailed = true; worker = null;
            var left = jobs; jobs = {};
            for (var id in left) fallback(left[id].job, left[id].done);
          };
        }
        var id = nextJob++;
        jobs[id] = { job: job, done: done };
        worker.postMessage({ id: id, fen: job.fen, level: job.level, seed: job.seed, past: job.past });
        return;
      } catch (e) { workerFailed = true; worker = null; }
    }
    fallback(job, done);
  }
  Logic.setThinker(thinker);

  // ---------- drawing ----------
  var SHAPES = {
    P: function (g, x, y, u, ci, o) {
      g.poly([[x - 4.6 * u, y + 6 * u], [x - 2.2 * u, y - 1 * u], [x + 2.2 * u, y - 1 * u], [x + 4.6 * u, y + 6 * u]], ci, o);
      g.circle(x, y - 3.8 * u, 3.4 * u, ci, o);
    },
    R: function (g, x, y, u, ci, o) {
      g.poly([[x - 5 * u, y + 6 * u], [x - 4.4 * u, y - 3 * u], [x + 4.4 * u, y - 3 * u], [x + 5 * u, y + 6 * u]], ci, o);
      g.poly([[x - 6.3 * u, y - 9 * u], [x - 6.3 * u, y - 3 * u], [x + 6.3 * u, y - 3 * u], [x + 6.3 * u, y - 9 * u], [x + 4 * u, y - 9 * u],
        [x + 4 * u, y - 6.6 * u], [x + 1.3 * u, y - 6.6 * u], [x + 1.3 * u, y - 9 * u], [x - 1.3 * u, y - 9 * u], [x - 1.3 * u, y - 6.6 * u],
        [x - 4 * u, y - 6.6 * u], [x - 4 * u, y - 9 * u]], ci, o);
    },
    N: function (g, x, y, u, ci, o) {
      g.poly([[x - 5.5 * u, y + 6 * u], [x - 4.6 * u, y], [x - 5.8 * u, y - 3 * u], [x - 2.2 * u, y - 8 * u], [x - 1.2 * u, y - 10.5 * u],
        [x + 0.8 * u, y - 8.2 * u], [x + 4.6 * u, y - 6.4 * u], [x + 6.8 * u, y - 2.2 * u], [x + 5.8 * u, y - 0.2 * u], [x + 3 * u, y - 1.4 * u],
        [x + 1 * u, y + 0.8 * u], [x + 4 * u, y + 6 * u]], ci, o);
    },
    B: function (g, x, y, u, ci, o) {
      g.poly([[x - 5 * u, y + 6 * u], [x - 3.2 * u, y - 1 * u], [x - 4.8 * u, y - 4.2 * u], [x, y - 9.6 * u], [x + 4.8 * u, y - 4.2 * u],
        [x + 3.2 * u, y - 1 * u], [x + 5 * u, y + 6 * u]], ci, o);
      g.circle(x, y - 10.6 * u, 1.6 * u, ci, o);
    },
    Q: function (g, x, y, u, ci, o) {
      g.poly([[x - 6.6 * u, y + 6 * u], [x - 8 * u, y - 6 * u], [x - 3.8 * u, y - 1.2 * u], [x - 2.4 * u, y - 8.4 * u], [x, y - 1.6 * u],
        [x + 2.4 * u, y - 8.4 * u], [x + 3.8 * u, y - 1.2 * u], [x + 8 * u, y - 6 * u], [x + 6.6 * u, y + 6 * u]], ci, o);
      var tips = [[-8, -6.6], [-2.4, -9.2], [2.4, -9.2], [8, -6.6]];
      for (var i = 0; i < tips.length; i++) g.circle(x + tips[i][0] * u, y + tips[i][1] * u, 1.4 * u, ci, o);
    },
    K: function (g, x, y, u, ci, o) {
      g.poly([[x - 6 * u, y + 6 * u], [x - 7 * u, y - 3 * u], [x - 3 * u, y - 4.4 * u], [x, y - 5.2 * u], [x + 3 * u, y - 4.4 * u],
        [x + 7 * u, y - 3 * u], [x + 6 * u, y + 6 * u]], ci, o);
      g.rect(x - 1 * u, y - 11.5 * u, 2 * u, 6.6 * u, ci, Object.assign({ r: 0 }, o));
      g.rect(x - 3.2 * u, y - 9.6 * u, 6.4 * u, 2 * u, ci, Object.assign({ r: 0 }, o));
    },
  };
  /** A piece (letter PNBRQK, `white`), centred at (x, y), a cell `size` across. */
  function piece(g, ch, white, x, y, size, a) {
    var lcd = g.kind === "lcd", u = size / 27, o = { solid: true, stroke: !lcd, a: a == null ? 1 : a };
    var ci = lcd ? (white ? 9 : 0) : g.kind === "pixel" ? (white ? 0 : 1) : BK.toneCi(g, !white);
    if (lcd && white) o = { solid: false, a: o.a };
    if (g.kind === "contrast" && white) o.edge = false;       // a black edge would eat into a white piece on a black square
    // the base under every piece
    g.rect(x - 7.2 * u, y + 5.6 * u, 14.4 * u, 3.6 * u, ci, Object.assign({ r: 1.2 * u }, o));
    SHAPES[ch](g, x, y, u, ci, o);
    if (!lcd && !white && g.kind !== "contrast") {
      // a light line inside Black's pieces, so their shapes read on a dark square too
      if (ch === "K") g.line(x, y - 3 * u, x, y + 4 * u, 8, 1, { a: 0.6 });
      else if (ch === "B") g.line(x + 1.6 * u, y - 6 * u, x - 1 * u, y - 3 * u, 8, 1.1, { a: 0.75 });
      else if (ch === "N") g.circle(x + 1 * u, y - 5 * u, 0.9 * u, 8, { solid: true, a: 0.85 });
    } else if (!lcd && ch === "N") g.circle(x + 1 * u, y - 5 * u, 0.9 * u, 0, { solid: true, a: 0.8 });
    else if (lcd && !white && ch === "N") g.circle(x + 1 * u, y - 5 * u, 1, 9, { solid: true, white: true });
  }

  function cellXY(s, sq) { var d = Logic.flipped(s) ? 63 - sq : sq; return [X0 + (d & 7) * CS, Y0 + (d >> 3) * CS]; }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", i, xy, green = g.kind === "modern" || g.kind === "paper";
    g.board(X0, Y0, CS * 8, CS * 8);
    for (i = 0; i < 64; i++) {
      var r = i >> 3, c = i & 7;
      if ((r + c) % 2 === 1) g.rect(X0 + c * CS, Y0 + r * CS, CS, CS, green ? 4 : 8, { r: 0, solid: !lcd, a: g.kind === "neon" ? 0.55 : green ? 0.45 : 1, edge: false });
      else if (g.kind === "pixel") g.rect(X0 + c * CS, Y0 + r * CS, CS, CS, 5, { r: 0, solid: true, a: 0.3, edge: false });
    }
    // files and ranks, as seen from this side
    var fl = Logic.flipped(s);
    for (i = 0; i < 8; i++) {
      g.text("abcdefgh"[fl ? 7 - i : i], X0 + i * CS + CS / 2, Y0 + 8 * CS + 9, { size: 7, align: "center", ci: 0, a: 0.6, bold: false });
      g.text(String(fl ? i + 1 : 8 - i), X0 - 6, Y0 + i * CS + CS / 2 + 3, { size: 7, align: "center", ci: 0, a: 0.6, bold: false });
    }
    // the last move
    if (s.last) for (var k = 0; k < 2; k++) {
      xy = cellXY(s, k ? s.last.to : s.last.from);
      if (lcd) g.rect(xy[0] + 2, xy[1] + 2, CS - 4, CS - 4, 9, {});
      else g.rect(xy[0], xy[1], CS, CS, 3, { r: 0, solid: true, a: g.kind === "neon" ? 0.3 : 0.38, edge: false });
    }
    var pos = Logic.fromFen(s.fen), b = pos.b;
    // a king in check
    if (!s.over || s.reason === "checkmate") {
      if (Logic.inCheck(pos)) {
        xy = cellXY(s, Logic.ext(pos.kings[pos.white ? 0 : 1]));
        g.ring(xy[0] + CS / 2, xy[1] + CS / 2, CS * 0.47, lcd ? 0 : 1, 2.5);
        if (!lcd) g.rect(xy[0], xy[1], CS, CS, 1, { r: 0, solid: true, a: 0.25, edge: false });
      }
    }
    var mine = Logic.mySeat(s), dests = {}, movable = {};
    if (mine >= 0 && info.state === "running" && !s.promo) {
      var list = Logic.legalList(s);
      for (i = 0; i < list.length; i++) { movable[list[i][0]] = true; if (s.sel === list[i][0]) dests[list[i][1]] = true; }
    }
    for (i = 0; i < 64; i++) {
      var p = b[Logic.mb(i)];
      xy = cellXY(s, i);
      if (p) {
        var ch = Logic.CHARS[p];
        piece(g, ch.toUpperCase(), ch === ch.toUpperCase(), xy[0] + CS / 2, xy[1] + CS / 2 + 0.5, CS);
      }
      if (dests[i]) {
        if (p) g.ring(xy[0] + CS / 2, xy[1] + CS / 2, CS * 0.44, lcd ? 0 : 7, 2);
        else g.circle(xy[0] + CS / 2, xy[1] + CS / 2, 3.6, lcd ? 0 : 7, { solid: true, a: 0.85 });
      }
    }
    if (s.sel !== null && s.sel !== undefined) { xy = cellXY(s, s.sel); g.ring(xy[0] + CS / 2, xy[1] + CS / 2, CS * 0.48, lcd ? 0 : 7, 2.6); }
    if (mine >= 0 && info.state === "running" && !s.promo) {
      xy = cellXY(s, s.cursor);
      var e = CS - 2, cc = lcd ? 0 : 5;
      g.line(xy[0] + 1, xy[1] + 1, xy[0] + 7, xy[1] + 1, cc, 2, { cap: "butt" }); g.line(xy[0] + 1, xy[1] + 1, xy[0] + 1, xy[1] + 7, cc, 2, { cap: "butt" });
      g.line(xy[0] + e, xy[1] + e, xy[0] + e - 6, xy[1] + e, cc, 2, { cap: "butt" }); g.line(xy[0] + e, xy[1] + e, xy[0] + e, xy[1] + e - 6, cc, 2, { cap: "butt" });
      g.line(xy[0] + e, xy[1] + 1, xy[0] + e - 6, xy[1] + 1, cc, 2, { cap: "butt" }); g.line(xy[0] + e, xy[1] + 1, xy[0] + e, xy[1] + 7, cc, 2, { cap: "butt" });
      g.line(xy[0] + 1, xy[1] + e, xy[0] + 7, xy[1] + e, cc, 2, { cap: "butt" }); g.line(xy[0] + 1, xy[1] + e, xy[0] + 1, xy[1] + e - 6, cc, 2, { cap: "butt" });
    }
    if (s.promo) drawPromo(g, s);
  }

  // the promotion chooser: four pieces in a row across the middle of the board
  var PX = 30, PY = 128, PW = 180, PH = 52;
  function drawPromo(g, s) {
    var lcd = g.kind === "lcd", white = s.turn === s.white;
    // opaque in every look (Paper and Neon fill shapes see-through: lay the panel down three times)
    for (var k = 0; k < (g.kind === "paper" || g.kind === "neon" ? 3 : 1); k++) g.rect(PX - 4, PY - 18, PW + 8, PH + 22, 9, { r: 6, solid: true, stroke: k === 0 });
    if (lcd) g.rect(PX - 4, PY - 18, PW + 8, PH + 22, 9, {});
    g.text("The pawn becomes…", 120, PY - 5, { size: 10, align: "center", ci: 0 });
    for (var i = 0; i < 4; i++) {
      var cx = PX + 22 + i * 45, cy = PY + 24;
      piece(g, "QRBN"[i], white, cx, cy, 34);
      if (i === s.promoAt) g.ring(cx, cy, 20, lcd ? 0 : 7, 2.5);
    }
  }

  function hit(s, lx, ly) {
    if (s.promo) {
      if (ly >= PY && ly <= PY + PH && lx >= PX && lx <= PX + PW) return { promo: "qrbn"[Math.max(0, Math.min(3, Math.floor((lx - PX) / 45)))] };
      return "cancel";
    }
    if (lx < X0 || ly < Y0 || lx >= X0 + 8 * CS || ly >= Y0 + 8 * CS) return null;
    var d = Math.floor((ly - Y0) / CS) * 8 + Math.floor((lx - X0) / CS);
    return Logic.flipped(s) ? 63 - d : d;
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["White", "Black"],
      counts: function (s) { return Logic.R.counts(s); },
      marker: function (g, x, y, seat, r, s) { piece(g, "K", s ? seat === s.white : seat === 0, x, y + 1, r * 2.8); },
      hit: hit,
      yourMove: function (s) { return s.promo ? "Pick what the pawn becomes" : s.sel !== null && s.sel !== undefined ? "Tap where it goes" : "Your move — tap a piece"; },
      statusText: function (s, nm) {
        if (s.over || s.sending) return null;
        var pos = Logic.fromFen(s.fen);
        if (Logic.inCheck(pos)) {
          var mine = (s.mode === "phones" ? s.turn === s.me : s.mode === "two" || s.turn !== s.cpu);
          return mine ? "Check! Your move" : nm[s.turn] + " is in check";
        }
        return null;
      },
      sounds: { step: null, capture: "hit", castle: "turn", promote: "powerup", check: "wall", pick: "turn" },
      nopeText: "That move isn't allowed",
    });
  }

  root.ArcadeGames.register({
    id: "chess",
    name: "Chess",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    options: [{ id: "side", label: "Against the computer, play", default: "white",
      choices: [{ id: "white", label: "White (move first)" }, { id: "black", label: "Black" }, { id: "either", label: "Either (from the game)" }] }],
    help: "Chess with all the rules: castling, en passant, promotion (you pick the piece), check, checkmate and stalemate; draws by repetition, 50 moves and too few pieces come by themselves. Tap a piece (dots show where it can go), then its square; or the arrows and Space, C to let go. Play the computer (Easy, Medium, Hard — it thinks for up to a second or two), two on one screen, or turn by turn from two phones (👥 Play with someone).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
