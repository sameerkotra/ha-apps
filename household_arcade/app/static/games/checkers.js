/* Household Arcade — Checkers (English draughts): input and drawing around the rules in checkers-logic.js.
   Registers itself as "checkers" with window.ArcadeGames.

   Tap one of your pieces (a ring shows it, and dots where it can go), then the square to move to; a multi-jump is
   finished for you when only one way on is left, else tap each landing square in turn. The arrows move a square
   cursor and Space does the same; C lets go of a piece. On the second phone of a match the board is turned round,
   so your own pieces are always at the bottom. Dark pieces are solid, light ones are rings with a light middle, and a
   king wears a star — never told apart by colour alone. */
(function (root) {
  "use strict";
  var Logic = root.CheckersLogic, BK = root.BoardKit;
  var CS = 27, X0 = 12, Y0 = 44;

  function flipped(s) { return s.mode === "phones" && s.me !== s.first; }
  function toScreen(s, sq) { return flipped(s) ? 63 - sq : sq; }
  function cellXY(s, sq) { var d = toScreen(s, sq); return [X0 + (d & 7) * CS, Y0 + (d >> 3) * CS]; }

  function piece(g, x, y, dark, king, r, a) {
    var lcd = g.kind === "lcd", o = { solid: true, a: a == null ? 1 : a };
    if (lcd) {
      g.circle(x, y, r, 0, o);
      if (!dark) g.circle(x, y, r - 2.5, 9, { solid: true, white: true });
    } else {
      g.circle(x, y, r, dark ? 1 : BK.toneCi(g, false), { solid: true, stroke: true, a: o.a });
      g.ring(x, y, r * 0.62, dark ? BK.toneCi(g, true) : 8, 1.2, { a: 0.5 * o.a });
    }
    if (king) g.star(x, y, r * 0.55, lcd ? (dark ? 9 : 0) : 3, { solid: true, a: o.a });
  }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", i, xy;
    g.board(X0, Y0, CS * 8, CS * 8);
    for (i = 0; i < 64; i++) {
      var r = i >> 3, c = i & 7;
      // the dark squares: green on Modern and Paper (a board you can see in either theme), the look's muted colour elsewhere
      var green = g.kind === "modern" || g.kind === "paper";
      if ((r + c) % 2 === 1) g.rect(X0 + c * CS, Y0 + r * CS, CS, CS, green ? 4 : 8, { r: 0, solid: !lcd, a: g.kind === "neon" ? 0.55 : green ? 0.45 : 1, edge: false });
    }
    // the last move: a thin line along its path
    if (s.last && s.last.length > 1 && !s.over) {
      for (i = 1; i < s.last.length; i++) {
        var a = cellXY(s, s.last[i - 1]), z = cellXY(s, s.last[i]);
        g.line(a[0] + CS / 2, a[1] + CS / 2, z[0] + CS / 2, z[1] + CS / 2, lcd ? 0 : 7, 2, { a: 0.55, dash: [3, 3] });
      }
    }
    var fade = info.reduce ? 0 : Math.max(0, 1 - (s.updates - s.moveAt) / 18);
    if (fade > 0) for (i = 0; i < (s.taken || []).length; i++) {
      xy = cellXY(s, s.taken[i].sq);
      piece(g, xy[0] + CS / 2, xy[1] + CS / 2, Logic.owner(s.taken[i].v) === s.first, Logic.isKing(s.taken[i].v), CS * 0.38, fade);
    }
    var mine = Logic.mySeat(s), movable = {}, dests = {};
    if (mine >= 0 && info.state === "running") {
      var all = Logic.allMoves(s.board, s.first, mine);
      for (i = 0; i < all.length; i++) {
        movable[all[i][0]] = true;
        if (s.sel) {
          var ok = s.sel.length < all[i].length;
          for (var k = 0; k < s.sel.length && ok; k++) if (all[i][k] !== s.sel[k]) ok = false;
          if (ok) dests[all[i][s.sel.length]] = true;
        }
      }
    }
    for (i = 0; i < 64; i++) {
      var v = s.board[i];
      xy = cellXY(s, i);
      if (v) {
        piece(g, xy[0] + CS / 2, xy[1] + CS / 2, Logic.owner(v) === s.first, Logic.isKing(v), CS * 0.38);
        if (movable[i] && !s.sel) g.circle(xy[0] + CS - 5, xy[1] + 5, 2.2, lcd ? 0 : 7, { solid: true });
      }
      if (dests[i]) g.circle(xy[0] + CS / 2, xy[1] + CS / 2, 4, lcd ? 0 : 7, { solid: true, a: 0.9 });
    }
    if (s.sel) for (i = 0; i < s.sel.length; i++) {
      xy = cellXY(s, s.sel[i]);
      g.ring(xy[0] + CS / 2, xy[1] + CS / 2, CS * 0.46, lcd ? 0 : 7, 2.5);
    }
    if (mine >= 0 && info.state === "running") {
      xy = cellXY(s, s.cursor);
      var e = CS - 2;
      g.line(xy[0] + 1, xy[1] + 1, xy[0] + e, xy[1] + 1, lcd ? 0 : 5, 2, { cap: "butt" }); g.line(xy[0] + 1, xy[1] + e, xy[0] + e, xy[1] + e, lcd ? 0 : 5, 2, { cap: "butt" });
      g.line(xy[0] + 1, xy[1] + 1, xy[0] + 1, xy[1] + e, lcd ? 0 : 5, 2, { cap: "butt" }); g.line(xy[0] + e, xy[1] + 1, xy[0] + e, xy[1] + e, lcd ? 0 : 5, 2, { cap: "butt" });
    }
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["Red", "White"],
      counts: function (s) { return [Logic.pieces(s.board, 0), Logic.pieces(s.board, 1)]; },
      marker: function (g, x, y, seat, r, s) { piece(g, x, y, seat === (s ? s.first : 0), false, r); },
      hit: function (s, lx, ly) {
        if (lx < X0 || ly < Y0 || lx >= X0 + 8 * CS || ly >= Y0 + 8 * CS) return null;
        var d = Math.floor((ly - Y0) / CS) * 8 + Math.floor((lx - X0) / CS);
        return flipped(s) ? 63 - d : d;
      },
      yourMove: function (s) { return s.sel ? "Tap where to go" : "Your move — tap a piece"; },
      sounds: { step: null, capture: "hit", king: "powerup", pick: "turn" },
      nopeText: "Not there (captures are compulsory)",
    });
  }

  root.ArcadeGames.register({
    id: "checkers",
    name: "Checkers",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    help: "English draughts. Men move one square diagonally forward; reach the far row to be crowned king (★, moves both ways). Jumping is compulsory and a jump that can carry on must carry on. Take all the other's pieces, or leave them no move, to win. Tap a piece, then where it goes (dots show where); or the arrows and Space, C to let go. Play the computer, two on one screen, or turn by turn from two phones (👥 Play with someone).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
