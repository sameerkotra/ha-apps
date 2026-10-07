/* Household Arcade — Sea Battle: input and drawing around the rules in seabattle-logic.js (BoardKit does the rest).
   Registers itself as "seabattle" with window.ArcadeGames.

   Placing: your fleet starts in a random arrangement. Tap a ship to pick it, tap it again to turn it, tap a square to
   move it there; Shuffle deals a new arrangement, Ready places it (the keyboard: the arrows walk the sea and, below it,
   the three buttons; Space acts; C turns the picked ship). Firing: tap a square of their sea (top). Your own fleet is
   the small sea at the bottom, with the tally of both fleets beside it. A hit is a cross on a filled square, a miss a
   small dot, a sunk ship is outlined — told apart by shape, not only colour. On one screen a "pass the phone" screen
   hides the seas between turns. */
(function (root) {
  "use strict";
  var Logic = root.SeaBattleLogic, BK = root.BoardKit;
  var Y0 = 50, BTN_Y = 226, BTN_H = 28, MINI_Y = 222;

  function cellSize(s) { return s.size === 10 ? 17 : 21; }
  function x0(s) { return (240 - s.size * cellSize(s)) / 2; }
  /** The seat whose seas are shown: this phone's, the computer's opponent, or (one screen) the player of the moment. */
  function viewer(s) {
    if (s.mode === "phones") return s.me;
    if (s.mode !== "two") return 0;
    if (s.phase === "place") { var p = Logic.placer(s); return p >= 0 ? p : s.lastBy >= 0 ? s.lastBy : 0; }
    if (s.over || s.passIn > 0) return s.lastBy >= 0 ? s.lastBy : 0;
    return s.turn;
  }

  function sea(g, s, X, Y, cs) {
    var lcd = g.kind === "lcd", n = s.size;
    if (!lcd) g.rect(X, Y, n * cs, n * cs, 5, { r: 2, solid: g.kind !== "neon", a: g.kind === "neon" ? 0.5 : 0.22, edge: false });
    for (var i = 0; i <= n; i++) {
      g.line(X + i * cs, Y, X + i * cs, Y + n * cs, lcd ? 0 : 5, 1, { a: lcd ? 1 : 0.45, cap: "butt" });
      g.line(X, Y + i * cs, X + n * cs, Y + i * cs, lcd ? 0 : 5, 1, { a: lcd ? 1 : 0.45, cap: "butt" });
    }
  }
  function shipShape(g, s, X, Y, cs, cells, ci, o) {
    var n = s.size, a = cells[0], z = cells[cells.length - 1];
    var r0 = Math.floor(a / n), c0 = a % n, r1 = Math.floor(z / n), c1 = z % n, pad = Math.max(1.5, cs * 0.14);
    g.rect(X + c0 * cs + pad, Y + r0 * cs + pad, (c1 - c0 + 1) * cs - 2 * pad, (r1 - r0 + 1) * cs - 2 * pad, ci, Object.assign({ r: cs * 0.4, solid: true }, o || {}));
  }
  function hitMark(g, x, y, cs, sunk) {
    var lcd = g.kind === "lcd", d = cs * 0.28;
    if (!lcd) g.rect(x - cs / 2 + 1, y - cs / 2 + 1, cs - 2, cs - 2, 1, { r: 1, solid: true, a: sunk ? 0.35 : 0.55, edge: false });
    g.line(x - d, y - d, x + d, y + d, lcd ? 0 : g.kind === "contrast" ? 0 : 1, Math.max(1.5, cs / 7));
    g.line(x + d, y - d, x - d, y + d, lcd ? 0 : g.kind === "contrast" ? 0 : 1, Math.max(1.5, cs / 7));
  }
  function missMark(g, x, y, cs) { g.circle(x, y, Math.max(1, cs * 0.12), g.kind === "lcd" ? 0 : 0, { solid: true, a: 0.6 }); }

  /** A sea with shots on it: `ships` drawn (known squares), `res` the shots fired at it, `sunk` ships sunk on it. */
  function drawSea(g, s, X, Y, cs, ships, res, sunk, ghost, lastCell, info) {
    var lcd = g.kind === "lcd", i;
    sea(g, s, X, Y, cs);
    // ships: the look's ink, half strength (light on a dark sea, dark on a light one); revealed ones at the end fainter
    for (i = 0; i < (ships || []).length; i++) shipShape(g, s, X, Y, cs, ships[i], lcd ? 8 : 0, { a: ghost ? 0.3 : 0.55, solid: !lcd, stroke: true });
    for (i = 0; i < (sunk || []).length; i++) shipShape(g, s, X, Y, cs, sunk[i], lcd ? 0 : 0, { a: 0.35, solid: !lcd, stroke: true });
    for (var key in res) {
      var c = Number(key), x = X + (c % s.size) * cs + cs / 2, y = Y + Math.floor(c / s.size) * cs + cs / 2;
      if (res[key]) hitMark(g, x, y, cs, false); else missMark(g, x, y, cs);
    }
    if (lastCell !== null && lastCell !== undefined && !info.reduce) {
      var t = (s.updates - s.shotAt + info.alpha) / 24;
      if (t < 1) g.ring(X + (lastCell % s.size) * cs + cs / 2, Y + Math.floor(lastCell / s.size) * cs + cs / 2, cs * (0.4 + 0.9 * t), lcd ? 0 : 3, 2, { a: 1 - t });
    }
  }
  function tally(g, s, x, y, seat, label) {
    var lcd = g.kind === "lcd", sunkLens = s.sunk[1 - seat].map(function (c) { return c.length; });
    g.text(label, x, y, { size: 9, ci: 0, a: 0.85 });
    var cx = x, used = {};
    for (var i = 0; i < s.fleet.length; i++) {
      var len = s.fleet[i], at = -1;
      for (var k = 0; k < sunkLens.length; k++) if (!used[k] && sunkLens[k] === len) { at = k; break; }
      if (at >= 0) used[at] = true;
      var w = len * 5;
      g.rect(cx, y + 4, w, 6, at >= 0 ? 8 : lcd ? 0 : 5, { r: 2, solid: true });
      if (at >= 0) g.line(cx - 1, y + 7, cx + w + 1, y + 7, lcd ? 0 : 1, 1.5);
      cx += w + 4;
    }
  }

  function drawBoard(g, s, info) {
    var lcd = g.kind === "lcd", cs = cellSize(s), X = x0(s), V = viewer(s), other = 1 - V, i;
    var placing = Logic.placer(s);
    if (s.phase === "place") {
      var showDraft = placing >= 0 && s.draft;
      g.text(showDraft ? "PLACE YOUR SHIPS" : "YOUR FLEET", 120, Y0 - 5, { size: 8, align: "center", ci: 0, a: 0.8 });
      sea(g, s, X, Y0, cs);
      var ships = [];
      if (showDraft) for (i = 0; i < s.draft.length; i++) { var d = s.draft[i]; ships.push(Logic.shipCells(s.size, d[0], d[1], d[2], d[3])); }
      else ships = s.ships[V] || [];
      for (i = 0; i < ships.length; i++) shipShape(g, s, X, Y0, cs, ships[i], showDraft && i === s.dsel ? 7 : lcd ? 8 : 0, { stroke: true, a: showDraft && i === s.dsel ? 1 : 0.6, solid: !lcd || (showDraft && i === s.dsel) });
      if (showDraft) {
        var labels = ["🔀 Shuffle", "↻ Turn", "✔ Ready"], words = ["SHUFFLE", "TURN", "READY"];
        for (i = 0; i < 3; i++) {
          var bx = 10 + i * 76, on = s.cursor === s.size * s.size + i && info.state === "running";
          g.rect(bx, BTN_Y, 68, BTN_H, i === 2 ? 4 : 8, { r: 6, solid: true, stroke: true, a: on ? 1 : 0.85 });
          g.text(g.lowres ? words[i] : labels[i], bx + 34, BTN_Y + 18, { size: 10, align: "center", ci: lcd ? 9 : 0 });
          if (on) g.rect(bx - 2, BTN_Y - 2, 72, BTN_H + 4, 7, { r: 7, solid: false, a: 0.9 });
        }
        if (s.cursor < s.size * s.size && info.state === "running") cursorBox(g, s, X, Y0, cs);
      }
      return;
    }
    // the battle: their sea (big), mine (small), the tally
    g.text(s.mode === "two" ? (info.state === "running" ? "FIRE AT THEIR SEA" : "THEIR SEA") : "THEIR SEA", 120, Y0 - 5, { size: 8, align: "center", ci: 0, a: 0.8 });
    var theirShips = s.over ? (s.ships[other] || s.theirs || null) : null;
    var last = s.lastShot && s.lastShot.seat === V ? s.lastShot.cell : null;
    drawSea(g, s, X, Y0, cs, theirShips, s.res[V], s.sunk[V], true, last, info);
    if (Logic.mySeat(s) === V && info.state === "running") cursorBox(g, s, X, Y0, cs);
    var mcs = s.size === 10 ? 5 : 6;
    var lastMine = s.lastShot && s.lastShot.seat === other ? s.lastShot.cell : null;
    drawSea(g, s, 12, MINI_Y + 8, mcs, s.ships[V], s.res[other], s.sunk[other], false, lastMine, info);
    g.text("YOURS", 12, MINI_Y + 4, { size: 8, ci: 0, a: 0.7 });
    tally(g, s, 80, MINI_Y + 12, other, "Theirs afloat");
    tally(g, s, 80, MINI_Y + 38, V, "Yours afloat");
  }
  function cursorBox(g, s, X, Y, cs) {
    var c = s.cursor, x = X + (c % s.size) * cs, y = Y + Math.floor(c / s.size) * cs, lcd = g.kind === "lcd";
    g.line(x, y, x + cs, y, lcd ? 0 : 3, 2, { cap: "butt" }); g.line(x, y + cs, x + cs, y + cs, lcd ? 0 : 3, 2, { cap: "butt" });
    g.line(x, y, x, y + cs, lcd ? 0 : 3, 2, { cap: "butt" }); g.line(x + cs, y, x + cs, y + cs, lcd ? 0 : 3, 2, { cap: "butt" });
  }

  function create(canvas, opts) {
    return BK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      seatNames: ["Player 1", "Player 2"],
      counts: function (s) { return [Logic.afloat(s, 0), Logic.afloat(s, 1)]; },
      marker: function (g, x, y, seat) {
        if (seat === 0) g.rect(x - 6, y - 3, 12, 6, g.kind === "lcd" ? 0 : 5, { r: 3, solid: true });
        else { g.rect(x - 6, y - 3, 12, 6, g.kind === "lcd" ? 0 : 1, { r: 3, solid: g.kind !== "lcd" }); }
      },
      hit: function (s, lx, ly) {
        var cs = cellSize(s), X = x0(s), n = s.size;
        if (lx >= X && lx < X + n * cs && ly >= Y0 && ly < Y0 + n * cs) return { cell: Math.floor((ly - Y0) / cs) * n + Math.floor((lx - X) / cs) };
        if (Logic.placer(s) >= 0 && ly >= BTN_Y - 4 && ly <= BTN_Y + BTN_H + 4) {
          var b = Math.floor((lx - 10 + 4) / 76);
          if (b >= 0 && b <= 2) return { btn: Logic.BUTTONS[b] };
        }
        return null;
      },
      statusText: function (s, names) {
        var p = Logic.placer(s);
        if (p >= 0) return s.mode === "two" ? names[p] + ": place your ships" : "Place your ships, then Ready";
        if (!s.over && s.phase === "place") {
          var waiting = Logic.toMove(s).filter(function (x) { return s.mode !== "phones" || x !== s.me; });
          if (waiting.length && s.mode === "phones") return "Waiting for " + names[waiting[0]] + " to place their ships";
        }
        if (!s.over && s.lastShot && s.updates - s.shotAt < 100) {
          var who = s.lastShot.seat, mine = s.mode === "phones" ? who === s.me : s.mode === "two" ? true : who !== s.cpu;
          var what = s.lastShot.sunk ? "Sunk! (" + s.lastShot.sunk + ")" : s.lastShot.hit ? "Hit!" : "Miss";
          return (mine && s.mode !== "two" ? "You: " : names[who] + ": ") + what;
        }
        return null;
      },
      yourMove: function () { return "Your shot — tap their sea"; },
      sounds: { placed: "launch", hit: "hit", miss: "wall", sunk: "break", shuffle: "turn", turnship: "turn", moveship: "turn", pick: "turn" },
      nopeText: "Not there",
    });
  }

  root.ArcadeGames.register({
    id: "seabattle",
    name: "Sea Battle",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    options: [{ id: "fleet", label: "Fleet", default: "classic",
      choices: [{ id: "classic", label: "Classic 10 × 10 (5 ships)" }, { id: "small", label: "Small 8 × 8 (4 ships)" }] }],
    help: "Place your fleet (tap a ship to pick it, again to turn it, tap a square to move it; Shuffle for a new arrangement, then Ready), then take turns firing at the other's hidden sea: a cross is a hit, a dot a miss; sink every ship to win. Your own fleet is the small sea at the bottom. Arrows and Space work too. On one screen, pass the phone when asked — no peeking! From two phones, the other fleet never reaches your phone.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
