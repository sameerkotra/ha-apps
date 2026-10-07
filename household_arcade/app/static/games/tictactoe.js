/* Household Arcade — Tic-tac-toe: input and drawing around the rules in tictactoe-logic.js (BoardKit does the rest).
   Registers itself as "tictactoe" with window.ArcadeGames.

   Tap a square (or move the ring with the arrows and press Space). ✕ and ○ differ by shape, not only colour. */
(function (root) {
  "use strict";
  var Logic = root.TicTacToeLogic, BK = root.BoardKit;
  var CS = 64, X0 = 24, Y0 = 62;

  function mark(g, x, y, isX, size, t) {
    var lcd = g.kind === "lcd", w = Math.max(3, size / 7);
    t = t == null ? 1 : t;
    if (isX) {
      var d = size * 0.32 * t;
      g.line(x - d, y - d, x + d, y + d, lcd ? 0 : 5, w);
      if (t > 0.5) g.line(x + d, y - d, x - d, y + d, lcd ? 0 : 5, w);
    } else {
      g.ring(x, y, size * 0.3 * t, lcd ? 0 : 1, w);
    }
  }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", ink = lcd ? 0 : 8;
    var mine = Logic.mySeat(s);
    if (mine >= 0 && info.state === "running") {
      var cr = Math.floor(s.cursor / 3), cc = s.cursor % 3;
      g.rect(X0 + cc * CS + 3, Y0 + cr * CS + 3, CS - 6, CS - 6, lcd ? 9 : 7, { r: 6, solid: !lcd && g.kind !== "neon", a: lcd ? 1 : 0.18, edge: false });
      if (lcd || g.kind === "contrast" || g.kind === "pixel") {
        var x0 = X0 + cc * CS + 5, y0 = Y0 + cr * CS + 5, e = CS - 10;
        g.line(x0, y0, x0 + e, y0, lcd ? 0 : 7, 2, { cap: "butt" }); g.line(x0, y0 + e, x0 + e, y0 + e, lcd ? 0 : 7, 2, { cap: "butt" });
        g.line(x0, y0, x0, y0 + e, lcd ? 0 : 7, 2, { cap: "butt" }); g.line(x0 + e, y0, x0 + e, y0 + e, lcd ? 0 : 7, 2, { cap: "butt" });
      }
    }
    for (var k = 1; k < 3; k++) {
      g.line(X0 + k * CS, Y0 + 4, X0 + k * CS, Y0 + 3 * CS - 4, lcd ? 0 : g.kind === "neon" ? 6 : 0, 3, { a: lcd ? 1 : 0.7 });
      g.line(X0 + 4, Y0 + k * CS, X0 + 3 * CS - 4, Y0 + k * CS, lcd ? 0 : g.kind === "neon" ? 6 : 0, 3, { a: lcd ? 1 : 0.7 });
    }
    var grow = info.reduce ? 1 : Math.min(1, (s.updates - s.markAt + info.alpha) / 8);
    for (var i = 0; i < 9; i++) {
      var v = s.board[i];
      if (!v) continue;
      mark(g, X0 + (i % 3) * CS + CS / 2, Y0 + Math.floor(i / 3) * CS + CS / 2, v - 1 === s.first, CS, i === s.last ? grow : 1);
    }
    if (s.line.length) {
      var a = s.line[0], z = s.line[2];
      g.line(X0 + (a % 3) * CS + CS / 2, Y0 + Math.floor(a / 3) * CS + CS / 2, X0 + (z % 3) * CS + CS / 2, Y0 + Math.floor(z / 3) * CS + CS / 2,
        lcd ? 0 : g.kind === "contrast" ? 3 : 4, 5, { a: 0.85 });
    }
    void ink;
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["Player 1", "Player 2"],
      marker: function (g, x, y, seat, r, s) { mark(g, x, y, seat === (s ? s.first : 0), r * 2.6); },
      hit: function (s, lx, ly) {
        if (lx < X0 || ly < Y0 || lx >= X0 + 3 * CS || ly >= Y0 + 3 * CS) return null;
        return Math.floor((ly - Y0) / CS) * 3 + Math.floor((lx - X0) / CS);
      },
      sounds: { mark: null, line: "row" },
      nopeText: "That square is taken",
    });
  }

  root.ArcadeGames.register({
    id: "tictactoe",
    name: "Tic-tac-toe",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    help: "Take turns marking a square; three of yours in a row — across, down or corner to corner — wins. ✕ goes first. Tap a square, or move the ring with the arrows and press Space. Hard never loses, so a draw against it is a good result. Play the computer, two players on one screen, or turn by turn from two phones (👥 Play with someone).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
