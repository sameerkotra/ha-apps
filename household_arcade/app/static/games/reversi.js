/* Household Arcade — Reversi: input and drawing around the rules in reversi-logic.js (BoardKit does the rest).
   Registers itself as "reversi" with window.ArcadeGames.

   Tap a square marked with a dot (the places you may play), or move the cursor with the arrows and press Space.
   Dark discs are solid, light ones have a ring in the middle (Retro LCD: solid and hollow). A player with no place to
   play passes automatically; the line at the bottom says so. */
(function (root) {
  "use strict";
  var Logic = root.ReversiLogic, BK = root.BoardKit;
  var CS = 27, X0 = 12, Y0 = 44;

  function disc(g, x, y, dark, r) {
    var lcd = g.kind === "lcd";
    if (lcd) {
      g.circle(x, y, r, 0, { solid: true });
      if (!dark) g.circle(x, y, r - 2.5, 9, { solid: true, white: true });
      return;
    }
    g.circle(x, y, r, BK.toneCi(g, dark), { solid: true, stroke: true });
    if (!dark) g.ring(x, y, r * 0.5, BK.toneCi(g, true), 1.4, { a: 0.6 });
  }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", i;
    var neon = g.kind === "neon";
    g.rect(X0, Y0, CS * 8, CS * 8, lcd ? 9 : 4, { r: 2, solid: !lcd && !neon, a: neon ? 0.5 : g.kind === "modern" ? 0.75 : 0.85 });
    for (i = 0; i <= 8; i++) {
      g.line(X0 + i * CS, Y0, X0 + i * CS, Y0 + 8 * CS, lcd ? 0 : 0, 1, { a: lcd ? 1 : 0.35 });
      g.line(X0, Y0 + i * CS, X0 + 8 * CS, Y0 + i * CS, lcd ? 0 : 0, 1, { a: lcd ? 1 : 0.35 });
    }
    var anim = info.reduce ? 1 : Math.min(1, (s.updates - s.moveAt + info.alpha) / 10);
    var flipped = {};
    for (i = 0; i < (s.flipped || []).length; i++) flipped[s.flipped[i]] = true;
    for (i = 0; i < 64; i++) {
      var v = s.board[i];
      if (!v) continue;
      var x = X0 + (i & 7) * CS + CS / 2, y = Y0 + (i >> 3) * CS + CS / 2;
      var r = CS * 0.4 * (flipped[i] || i === s.last ? 0.45 + 0.55 * anim : 1);
      disc(g, x, y, v - 1 === s.first, r);
    }
    if (s.last >= 0) g.circle(X0 + (s.last & 7) * CS + CS / 2, Y0 + (s.last >> 3) * CS + CS / 2, 2, lcd ? 9 : 7, { solid: true, white: lcd });
    var mine = Logic.mySeat(s);
    if (mine >= 0 && info.state === "running") {
      var cs = Logic.cells(s.board, mine);
      for (i = 0; i < cs.length; i++) g.circle(X0 + (cs[i] & 7) * CS + CS / 2, Y0 + (cs[i] >> 3) * CS + CS / 2, 3, lcd ? 0 : 7, { solid: true, a: 0.8 });
      var cx = X0 + (s.cursor & 7) * CS, cy = Y0 + (s.cursor >> 3) * CS, e = CS - 2;
      g.line(cx + 1, cy + 1, cx + e, cy + 1, lcd ? 0 : 5, 2, { cap: "butt" }); g.line(cx + 1, cy + e, cx + e, cy + e, lcd ? 0 : 5, 2, { cap: "butt" });
      g.line(cx + 1, cy + 1, cx + 1, cy + e, lcd ? 0 : 5, 2, { cap: "butt" }); g.line(cx + e, cy + 1, cx + e, cy + e, lcd ? 0 : 5, 2, { cap: "butt" });
    }
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["Dark", "Light"],
      counts: function (s) { return [Logic.count(s.board, 0), Logic.count(s.board, 1)]; },
      marker: function (g, x, y, seat, r, s) { disc(g, x, y, seat === (s ? s.first : 0), r); },
      hit: function (s, lx, ly) {
        if (lx < X0 || ly < Y0 || lx >= X0 + 8 * CS || ly >= Y0 + 8 * CS) return null;
        return Math.floor((ly - Y0) / CS) * 8 + Math.floor((lx - X0) / CS);
      },
      statusText: function (s, names) {
        if (!s.over && s.passed >= 0 && s.updates - s.moveAt < 150) return names[s.passed] + " can't place — passes";
        return null;
      },
      yourMove: function () { return "Your move — tap a dot"; },
      sounds: { place: null, pass: "wall" },
      nopeText: "That square turns nothing over",
    });
  }

  root.ArcadeGames.register({
    id: "reversi",
    name: "Reversi",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    help: "Place a disc so that it traps a line of the other's discs between it and one of yours — they turn over to your side. Dots show where you may play; with nowhere to play you pass. When neither can play, more discs wins. Tap a dot, or the arrows and Space. Play the computer, two on one screen, or turn by turn from two phones (👥 Play with someone).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
