/* Household Arcade — Ludo: input and drawing around the rules in ludo-logic.js. Registers itself as "ludo".

   The board is drawn from shapes: the yards in the corners (each with its player's name and four token spots), the
   track, the coloured home columns and the home triangles in the middle; the safe squares carry a star. Tokens differ
   by shape as well as colour (red ●, green ■, yellow ▲, blue ◆), so every look — Retro LCD's ink too — tells them
   apart. Tap the die to roll; tap one of your tokens that can move (they're ringed); or Space rolls and moves, the
   arrows ring the next token. A move animates square by square. */
(function (root) {
  "use strict";
  var Logic = root.LudoLogic, BK = root.BoardKit, DK = root.DiceKit;
  var C = 15, X0 = 7.5, Y0 = 34, CI = [1, 4, 3, 5];

  /** A rectangle's outline in any look (Retro LCD: the one-dot ring). */
  function outline(g, x, y, w, h, ci, lw) {
    if (g.kind === "lcd") { g.rect(x, y, w, h, 9, {}); return; }
    g.curve(function (c) { c.rect(x, y, w, h); }, ci, lw || 1.5);
  }
  function cellXY(rc) { return [X0 + rc[1] * C + C / 2, Y0 + rc[0] * C + C / 2]; }
  /** Where a token of `seat` at progress p (−1 yard … 56 home) is drawn, for token number t. */
  function where(s, seat, t, p) {
    var col = s.cols[seat];
    if (p === -1) {
      var y = Logic.YARD[col];
      return [X0 + y[1] * C + (t % 2 ? 4 : 2) * C, Y0 + y[0] * C + (t < 2 ? 3 : 4.6) * C];
    }
    if (p <= Logic.LAST_TRACK) return cellXY(Logic.PATH[Logic.square(s, seat, p)]);
    if (p < Logic.FINISH) return cellXY(Logic.homeCell(col, p - 51));
    // home: the colour's triangle in the middle, the four tokens side by side
    var cx = X0 + 7.5 * C, cy = Y0 + 7.5 * C, off = (t - 1.5) * 4.2;
    switch (col) {
      case 0: return [cx - 13, cy + off];
      case 1: return [cx + off, cy - 13];
      case 2: return [cx + 13, cy + off];
      default: return [cx + off, cy + 13];
    }
  }

  /** A token: a circle, square, triangle or diamond by colour. */
  function token(g, x, y, col, r, o) {
    o = o || {};
    var lcd = g.kind === "lcd", ci = CI[col], a = o.a == null ? 1 : o.a, opt = { solid: true, stroke: !lcd, a: a };
    if (col === 0) g.circle(x, y, r, ci, opt);
    else if (col === 1) g.rect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76, ci, { solid: true, stroke: !lcd, a: a, r: 1 });
    else if (col === 2) g.poly([[x, y - r * 1.05], [x + r * 1.05, y + r * 0.8], [x - r * 1.05, y + r * 0.8]], ci, opt);
    else g.poly([[x, y - r * 1.1], [x + r * 1.05, y], [x, y + r * 1.1], [x - r * 1.05, y]], ci, opt);
    if (lcd) g.circle(x, y + (col === 2 ? 1 : 0), Math.max(1, r * 0.32), 9, { solid: true, white: true });
    else g.circle(x, y + (col === 2 ? 1 : 0), Math.max(0.9, r * 0.28), 9, { solid: true, a: 0.85 * a });
  }

  function drawStatic(g) {
    var lcd = g.kind === "lcd", low = g.lowres, i, k, col, xy;
    g.board(X0, Y0, 15 * C, 15 * C);
    // the yards
    for (col = 0; col < 4; col++) {
      var y = Logic.YARD[col], x0 = X0 + y[1] * C, y0 = Y0 + y[0] * C;
      if (lcd) { outline(g, x0 + 1, y0 + 1, 6 * C - 2, 6 * C - 2, 0); outline(g, x0 + 3, y0 + 3, 6 * C - 6, 6 * C - 6, 0); }
      else g.rect(x0 + 1, y0 + 1, 6 * C - 2, 6 * C - 2, CI[col], { r: low ? 0 : 5, solid: true, a: g.kind === "neon" ? 0.45 : 0.85, edge: false });
      // the yard's middle: light in Modern and Paper, the look's dark muted colour where light would glare or hide text
      var dimLook = g.kind === "neon" || g.kind === "pixel";
      g.rect(x0 + C, y0 + C, 4 * C, 4 * C, dimLook ? 8 : 9, { r: low ? 0 : 4, solid: !lcd, edge: g.kind === "contrast", stroke: g.kind === "modern", a: g.kind === "neon" ? 0.9 : 1 });
      for (k = 0; k < 4; k++) {
        var sx = x0 + (k % 2 ? 4 : 2) * C, sy = y0 + (k < 2 ? 3 : 4.6) * C;
        g.ring(sx, sy, 5.6, lcd ? 0 : CI[col], 1.2, { a: 0.75 });
      }
    }
    // the track
    for (i = 0; i < Logic.PATH.length; i++) {
      var rc = Logic.PATH[i];
      var startCol = i % 13 === 0 ? i / 13 : -1;
      var x = X0 + rc[1] * C, yy = Y0 + rc[0] * C;
      if (startCol >= 0 && !lcd) g.rect(x + 0.5, yy + 0.5, C - 1, C - 1, CI[startCol], { r: 0, solid: true, a: g.kind === "neon" ? 0.5 : 0.8, edge: false });
      else if (g.kind === "neon") g.rect(x + 1, yy + 1, C - 2, C - 2, 8, { r: 0, solid: false, a: 0.8 });
      else g.rect(x + 0.5, yy + 0.5, C - 1, C - 1, g.kind === "pixel" ? 8 : 9, { r: 0, solid: !lcd, edge: g.kind === "contrast", stroke: g.kind === "modern" || g.kind === "paper" });
      if (lcd && startCol >= 0) g.rect(x + 3, yy + 3, C - 6, C - 6, 0, { r: 0, solid: false });
      if (Logic.SAFE.indexOf(i) >= 0 && startCol < 0) g.star(x + C / 2, yy + C / 2, 5, lcd ? 0 : 8, { solid: true, a: lcd ? 1 : 0.9 });
    }
    // the home columns
    for (col = 0; col < 4; col++) {
      for (k = 0; k < 5; k++) {
        xy = Logic.homeCell(col, k);
        var hx = X0 + xy[1] * C, hy = Y0 + xy[0] * C;
        if (lcd) { g.rect(hx + 0.5, hy + 0.5, C - 1, C - 1, 9, { r: 0 }); g.circle(hx + C / 2, hy + C / 2, 1.2, 0, { solid: true }); }
        else g.rect(hx + 0.5, hy + 0.5, C - 1, C - 1, CI[col], { r: 0, solid: true, a: g.kind === "neon" ? 0.5 : 0.75, edge: false });
      }
    }
    // the middle: four triangles pointing in
    var c0x = X0 + 6 * C, c0y = Y0 + 6 * C, c1x = c0x + 3 * C, c1y = c0y + 3 * C, mx = c0x + 1.5 * C, my = c0y + 1.5 * C;
    var tris = [[[c0x, c0y], [c0x, c1y], [mx, my]], [[c0x, c0y], [c1x, c0y], [mx, my]], [[c1x, c0y], [c1x, c1y], [mx, my]], [[c0x, c1y], [c1x, c1y], [mx, my]]];
    for (col = 0; col < 4; col++) {
      if (lcd) g.curve(function (c) { var t = tris[col]; c.moveTo(t[0][0], t[0][1]); c.lineTo(t[1][0], t[1][1]); c.lineTo(t[2][0], t[2][1]); c.closePath(); }, 0, 1);
      else g.poly(tris[col], CI[col], { solid: true, a: g.kind === "neon" ? 0.5 : 0.85 });
    }
  }

  function drawBoard(g, s, info, V) {
    var lcd = g.kind === "lcd", i, j;
    g.layer("ludo-board", 1, drawStatic);
    // names in the yards; the one whose turn it is gets a ring round the yard
    for (i = 0; i < s.n; i++) {
      var col = s.cols[i], y = Logic.YARD[col], x0 = X0 + y[1] * C, y0 = Y0 + y[0] * C;
      var label = V.names[i] + (s.mode === "phones" && s.left[i] ? " (cpu)" : "");
      if (g.lowres) label = label.replace("Computer ", "CPU ").replace("Computer", "CPU");
      g.text(label.length > 11 ? label.slice(0, 10) + "…" : label, x0 + 3 * C, y0 + C + 11, { size: 9, align: "center", ci: 0 });
      var hn = Logic.home(s, i);
      if (hn) g.text(hn + " home", x0 + 3 * C, y0 + 5 * C - 2, { size: 7, align: "center", ci: 0, a: 0.8 });
      if (i === s.turn && !s.over) {
        var pulse = info.reduce ? 1 : 0.65 + 0.35 * Math.abs(((s.updates % 60) / 30) - 1);
        if (lcd) outline(g, x0 + 5, y0 + 5, 6 * C - 10, 6 * C - 10, 0);
        else g.curve(function (c) { c.rect(x0 + 1.5, y0 + 1.5, 6 * C - 3, 6 * C - 3); }, 7, 3, { a: pulse });
        g.ring(x0 + 3 * C, y0 + C - 6, 2.5, lcd ? 0 : 7, 2);
      }
    }
    // tokens (a token being moved goes square by square; tokens it took wait until it lands)
    var a = s.anim, el = a ? s.updates - a.at : 1e9, moving = null, capsUntil = -1;
    if (a && a.kind === "move" && el >= 0 && el < Logic.R.animLength(s) + 4) {
      var steps = a.from === -1 ? 1 : a.to - a.from, done = Math.min(steps, Math.floor(el / Logic.STEP_UPDATES));
      moving = { seat: a.seat, token: a.token, p: a.from === -1 ? (done >= 1 ? 0 : -1) : a.from + done };
      capsUntil = steps * Logic.STEP_UPDATES;
    }
    var spots = {}, list = [];
    for (i = 0; i < s.n; i++) for (j = 0; j < 4; j++) {
      var p = s.tokens[i][j];
      if (moving && moving.seat === i && moving.token === j) p = moving.p;
      if (a && a.kind === "move" && el < capsUntil) for (var k = 0; k < a.caps.length; k++) if (a.caps[k][0] === i && a.caps[k][1] === j) p = a.caps[k][2];
      var xy = where(s, i, j, p);
      var key = p === -1 || p === Logic.FINISH ? "y" + i + j : Math.round(xy[0]) + "," + Math.round(xy[1]);
      (spots[key] = spots[key] || []).push(list.length);
      list.push({ seat: i, t: j, p: p, x: xy[0], y: xy[1] });
    }
    for (var key2 in spots) {
      var ids = spots[key2];
      if (ids.length < 2) continue;
      for (k = 0; k < ids.length; k++) { var e = list[ids[k]]; e.x += (k % 2 ? 3 : -3); e.y += (k < 2 ? -3 : 3); e.small = true; }
    }
    s._drawn = list;
    var mine = Logic.mySeat(s), movable = mine >= 0 && info.state === "running" ? Logic.movable(s, mine) : [];
    var forcedNow = mine >= 0 && s.rolled && Logic.R.forced(s, mine);
    for (i = 0; i < list.length; i++) {
      var tk = list[i], r = tk.small ? 4.3 : 5.4;
      token(g, tk.x, tk.y, s.cols[tk.seat], r);
      if (!forcedNow && tk.seat === mine && movable.indexOf(tk.t) >= 0) {
        var ring = info.reduce ? 1 : 0.6 + 0.4 * Math.abs(((s.updates % 40) / 20) - 1);
        g.ring(tk.x, tk.y, r + 3, lcd ? 0 : 7, tk.t === s.cursor ? 3 : 1.6, { a: lcd ? 1 : ring });
      }
    }
    // a header line: whose turn
    var mineTurn = s.mode !== "pass" && s.turn === (s.mode === "phones" ? s.me : 0);
    var who = s.over ? "" : mineTurn ? "Your turn" : V.names[s.turn] + "'s turn";
    token(g, 16, 18, s.cols[s.over ? s.winner < 0 ? 0 : s.winner : s.turn], 5);
    g.text(s.over ? "Game over" : who, 28, 22, { size: 11, ci: 0 });
    if (s.sixes && !s.over) g.text("6 × " + s.sixes, 230, 22, { size: 10, align: "right", ci: 0, a: 0.85 });
    g.line(8, 30, 232, 30, 8, 1, { a: lcd ? 1 : 0.6 });
  }

  function hit(s, lx, ly) {
    if (lx < 42 && ly > 260) return "die";
    var best = null, bd = 13 * 13, list = s._drawn || [];
    for (var i = 0; i < list.length; i++) {
      var d = (list[i].x - lx) * (list[i].x - lx) + (list[i].y - ly) * (list[i].y - ly);
      // prefer a token of the player who may move (stacks): a small bonus
      if (list[i].seat === s.turn) d -= 20;
      if (d < bd) { bd = d; best = list[i]; }
    }
    return best ? { seat: best.seat, token: best.t } : "board";
  }

  function create(canvas, opts) {
    return DK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      hit: hit,
      marker: function (g, x, y, seat, r, s) { token(g, x, y, s.cols[seat], r); },
      chooseText: function (s) { return "Rolled " + s.rolled + " — tap a ringed token"; },
      nopeText: "That token can't move with this roll",
      sounds: { enter: "launch", step: "turn", home: "level", capture: "hit", pass: null },
    });
  }

  root.ArcadeGames.register({
    id: "ludo",
    name: "Ludo",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "cpu",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    options: [
      { id: "players", label: "Players", default: "4", choices: [{ id: "2", label: "2 players" }, { id: "3", label: "3 players" }, { id: "4", label: "4 players" }] },
      { id: "fill", label: "One phone: empty seats", default: "no", choices: [{ id: "no", label: "Left empty" }, { id: "yes", label: "The computer fills them" }] },
    ],
    help: "Get all four of your tokens home first. Tap the die to roll (Space), then tap a ringed token to move it that many squares (or the arrows and Space). A 6 brings a token out of your yard and gives another roll — three 6s in a row lose the turn. Landing on other colours sends them home, except on the starred and start squares. Home needs the exact roll. Play the computer, everyone on one phone, or 2–4 phones turn by turn (👥 Play with someone; the server rolls).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
