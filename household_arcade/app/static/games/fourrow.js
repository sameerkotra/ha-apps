/* Household Arcade — Four in a Row: input and drawing around the rules in fourrow-logic.js (BoardKit does the rest).
   Registers itself as "fourrow" with window.ArcadeGames.

   Tap a column (or ← → and Space / ↓) to drop a disc. The first player's discs are red and plain, the second's
   yellow with a ring inside, so the two never differ by colour alone (Retro LCD: solid and hollow). */
(function (root) {
  "use strict";
  var Logic = root.FourRowLogic, BK = root.BoardKit;
  var COLS = 7, ROWS = 6, CS = 32, X0 = 8, Y0 = 62, R = 13;

  function disc(g, x, y, seat, r, a) {
    var lcd = g.kind === "lcd", o = { solid: true, a: a == null ? 1 : a };
    if (lcd) {
      g.circle(x, y, r, 0, o);
      if (seat === 1) g.circle(x, y, r - 3, 9, { solid: true, white: true });
      return;
    }
    g.circle(x, y, r, seat === 0 ? 1 : 3, o);
    if (seat === 1) g.ring(x, y, r * 0.55, g.kind === "neon" ? 3 : 0, 1.6, { a: 0.55 * (o.a) });
  }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", w = COLS * CS, h = ROWS * CS;
    var mine = Logic.mySeat(s);
    // the disc waiting above the cursor's column (whoever moves at this screen)
    if (mine >= 0 && info.state === "running") {
      var cx = X0 + s.cursor * CS + CS / 2;
      disc(g, cx, Y0 - 16, mine, R - 2, 0.85);
      g.line(cx - 6, Y0 - 2, cx + 6, Y0 - 2, lcd ? 0 : 7, 2, { cap: "butt" });
    }
    var neon = g.kind === "neon";
    g.rect(X0 - 4, Y0 - 4, w + 8, h + 8, lcd ? 8 : 5, { r: 8, solid: !lcd && !neon, a: neon ? 0.6 : 1 });
    var drop = s.drop && !info.reduce ? s.updates - s.drop.at : 99;
    for (var i = 0; i < COLS * ROWS; i++) {
      var r = Math.floor(i / COLS), c = i % COLS, x = X0 + c * CS + CS / 2, y = Y0 + r * CS + CS / 2;
      var v = s.board[i];
      if (v && s.drop && i === s.drop.cell && drop < 12) {
        // falling: from above the frame to its place over 12 updates
        var t = (drop + info.alpha) / 12, yy = (Y0 - 16) + (y - (Y0 - 16)) * t * t;
        if (!lcd) g.circle(x, y, R, neon ? 8 : 9, { solid: true, a: neon ? 0.5 : 1 });
        disc(g, x, Math.min(y, yy), v - 1, R);
        continue;
      }
      // an empty place: a hole (Retro LCD: a small dot, so it never looks like the second player's hollow disc)
      if (!v) { if (lcd) g.circle(x, y, 2, 0, { solid: true }); else g.circle(x, y, R, neon ? 8 : 9, { solid: true, a: neon ? 0.5 : 1 }); continue; }
      disc(g, x, y, v - 1, R);
    }
    if (s.line.length) {
      var a = s.line[0], z = s.line[s.line.length - 1];
      g.line(X0 + (a % COLS) * CS + CS / 2, Y0 + Math.floor(a / COLS) * CS + CS / 2, X0 + (z % COLS) * CS + CS / 2,
        Y0 + Math.floor(z / COLS) * CS + CS / 2, lcd ? 0 : g.kind === "contrast" ? 9 : 0, 4, { a: 0.85 });
    }
    // the last disc dropped: a small dot on it
    if (s.last >= 0 && !s.line.length) {
      var lr = Math.floor(s.last / COLS), lc = s.last % COLS;
      g.circle(X0 + lc * CS + CS / 2, Y0 + lr * CS + CS / 2, 2.5, lcd ? 9 : 0, { solid: true, white: lcd, a: 0.8 });
    }
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["Red", "Yellow"],
      marker: function (g, x, y, seat) { disc(g, x, y, seat, 6); },
      hit: function (s, lx, ly) {
        if (ly < 30 || ly > Y0 + ROWS * CS + 4 || lx < X0 || lx >= X0 + COLS * CS) return null;
        return Math.floor((lx - X0) / CS);
      },
      sounds: { drop: null, line: "row" },
      nopeText: "That column is full",
    });
  }

  root.ArcadeGames.register({
    id: "fourrow",
    name: "Four in a Row",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    help: "Drop discs into the frame in turn; four of yours in a row — across, up and down or slanting — wins. Tap a column, or move with ← → and drop with Space or ↓. Play the computer (Easy, Medium, Hard), two players on one screen, or turn by turn from two phones (👥 Play with someone).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
