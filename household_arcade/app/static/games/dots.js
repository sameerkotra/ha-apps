/* Household Arcade — Dots and Boxes: input and drawing around the rules in dots-logic.js (BoardKit does the rest).
   Registers itself as "dots" with window.ArcadeGames.

   Tap between two dots to draw the line there (the nearest line not yet drawn), or move the highlighted line with
   the arrows (↑ ↓ step to the lines that cross) and press Space. A box shows its owner's mark — a round one for the
   first player, a diamond for the second — so boxes never differ by colour alone. */
(function (root) {
  "use strict";
  var Logic = root.DotsLogic, BK = root.BoardKit;
  var X0 = 20, Y0 = 50, SPAN = 200;

  function sp(s) { return SPAN / s.size; }
  function edgeLine(s, e) {
    var n = s.size, d = sp(s), h = Logic.dims(n)[0], r, c;
    if (e < h) { r = Math.floor(e / n); c = e % n; return [X0 + c * d, Y0 + r * d, X0 + (c + 1) * d, Y0 + r * d]; }
    r = Math.floor((e - h) / (n + 1)); c = (e - h) % (n + 1);
    return [X0 + c * d, Y0 + r * d, X0 + c * d, Y0 + (r + 1) * d];
  }
  function mark(g, x, y, seat, r) {
    var lcd = g.kind === "lcd";
    if (seat === 0) g.circle(x, y, r, lcd ? 0 : 1, { solid: true });
    else g.poly([[x, y - r * 1.2], [x + r * 1.2, y], [x, y + r * 1.2], [x - r * 1.2, y]], lcd ? 0 : 5, { solid: true });
  }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", n = s.size, d = sp(s), i, L;
    var grow = info.reduce ? 1 : Math.min(1, (s.updates - s.moveAt + info.alpha) / 10);
    for (i = 0; i < s.boxes.length; i++) {
      var who = s.boxes[i];
      if (!who) continue;
      var bx = X0 + (i % n) * d, by = Y0 + Math.floor(i / n) * d, fresh = s.made.indexOf(i) >= 0 ? grow : 1;
      if (!lcd) g.rect(bx + 3, by + 3, d - 6, d - 6, who === 1 ? 1 : 5, { r: 3, solid: g.kind !== "neon", a: (g.kind === "neon" ? 0.6 : 0.3) * fresh, edge: false });
      mark(g, bx + d / 2, by + d / 2, who - 1, Math.max(3, d * 0.16) * (0.5 + 0.5 * fresh));
    }
    var mine = Logic.mySeat(s);
    if (mine >= 0 && info.state === "running" && !s.edges[s.cursor]) {
      L = edgeLine(s, s.cursor);
      g.line(L[0], L[1], L[2], L[3], lcd ? 0 : 7, 4, { a: lcd ? 1 : 0.55, dash: [4, 4] });
    }
    for (i = 0; i < s.edges.length; i++) {
      L = edgeLine(s, i);
      if (!s.edges[i]) { if (!lcd) g.line(L[0], L[1], L[2], L[3], 8, 1, { a: 0.6, dash: [2, 5] }); continue; }
      var last = i === s.last;
      g.line(L[0], L[1], L[2], L[3], lcd ? 0 : last ? 7 : 0, last ? 4 : 3, { a: last ? 1 : 0.85 });
    }
    for (var r = 0; r <= n; r++) for (var c = 0; c <= n; c++) g.circle(X0 + c * d, Y0 + r * d, 3.2, 0, { solid: true });
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["Red", "Blue"],
      counts: function (s) { return [Logic.count(s.boxes, 0), Logic.count(s.boxes, 1)]; },
      marker: function (g, x, y, seat, r) { mark(g, x, y, seat, r * 0.8); },
      hit: function (s, lx, ly) {
        var best = -1, bd = Infinity, d = sp(s);
        for (var e = 0; e < s.edges.length; e++) {
          if (s.edges[e]) continue;
          var L = edgeLine(s, e), mx = (L[0] + L[2]) / 2, my = (L[1] + L[3]) / 2;
          var dist = Math.abs(lx - mx) + Math.abs(ly - my);
          if (dist < bd) { bd = dist; best = e; }
        }
        return best >= 0 && bd < d * 0.6 ? best : null;
      },
      statusText: function (s, names) {
        if (!s.over && s.made && s.made.length && s.updates - s.moveAt < 120 && Logic.toMove(s)[0] === s.lastBy) {
          var who = Logic.toMove(s)[0];
          var mine = s.mode === "phones" ? who === s.me : s.mode === "two" || who !== s.cpu;
          return mine && s.mode !== "two" ? "A box! Draw another line" : names[who] + " gets another go";
        }
        return null;
      },
      yourMove: function () { return "Your move — draw a line"; },
      sounds: { line: null, box: "row", again: null },
      nopeText: "That line is already drawn",
    });
  }

  root.ArcadeGames.register({
    id: "dots",
    name: "Dots and Boxes",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    options: [{ id: "size", label: "Size", default: "4",
      choices: [{ id: "3", label: "3 × 3 boxes" }, { id: "4", label: "4 × 4 boxes" }, { id: "5", label: "5 × 5 boxes" }] }],
    help: "Take turns drawing a line between two dots next to each other. Draw the fourth side of a box and it's yours — and you must draw another line. When all the lines are drawn, more boxes wins. Tap between two dots, or move the dashed line with the arrows and press Space. Pick the size on the start screen. Play the computer, two on one screen, or turn by turn from two phones (👥 Play with someone).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
