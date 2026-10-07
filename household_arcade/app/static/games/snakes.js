/* Household Arcade — Snakes and Ladders: input and drawing around the rules in snakes-logic.js. Registers itself as
   "snakes".

   The board is drawn from shapes on every start (no pictures): a 10 × 10 grid numbered 1–100, ladders as two rails and
   rungs, snakes as wavy bodies with a head and eyes, each in its own colour. Players' tokens differ by shape and
   colour (red ●, blue ■, green ▲, yellow ◆); the top row shows each player and their square. Tap anywhere (or Space)
   to roll; the token hops square by square, then climbs or slides. */
(function (root) {
  "use strict";
  var Logic = root.SnakesLogic, DK = root.DiceKit;
  var C = 22, X0 = 10, Y0 = 37, CI = [1, 5, 4, 3], SNAKE_CI = [4, 6, 2, 1, 7];

  function centre(n) { var c = Logic.cell(n); return [X0 + c[0] * C + C / 2, Y0 + c[1] * C + C / 2]; }

  function token(g, x, y, seat, r, o) {
    o = o || {};
    var lcd = g.kind === "lcd", ci = CI[seat], opt = { solid: true, stroke: !lcd };
    if (seat === 0) g.circle(x, y, r, ci, opt);
    else if (seat === 1) g.rect(x - r * 0.88, y - r * 0.88, r * 1.76, r * 1.76, ci, { solid: true, stroke: !lcd, r: 1 });
    else if (seat === 2) g.poly([[x, y - r * 1.05], [x + r * 1.05, y + r * 0.8], [x - r * 1.05, y + r * 0.8]], ci, opt);
    else g.poly([[x, y - r * 1.1], [x + r * 1.05, y], [x, y + r * 1.1], [x - r * 1.05, y]], ci, opt);
    g.circle(x, y + (seat === 2 ? 1 : 0), Math.max(1, r * 0.3), 9, { solid: true, white: true, a: lcd ? 1 : 0.85 });
  }

  function ladder(g, a, b, lcd) {
    var dx = b[0] - a[0], dy = b[1] - a[1], len = Math.sqrt(dx * dx + dy * dy), nx = -dy / len * 4.2, ny = dx / len * 4.2;
    var ci = lcd ? 0 : 2;
    g.line(a[0] + nx, a[1] + ny, b[0] + nx, b[1] + ny, ci, 2.2);
    g.line(a[0] - nx, a[1] - ny, b[0] - nx, b[1] - ny, ci, 2.2);
    var rungs = Math.max(2, Math.floor(len / 9));
    for (var i = 1; i < rungs; i++) {
      var t = i / rungs, x = a[0] + dx * t, y = a[1] + dy * t;
      g.line(x + nx, y + ny, x - nx, y - ny, ci, 1.6);
    }
  }

  function snake(g, head, tail, k, lcd) {
    var dx = tail[0] - head[0], dy = tail[1] - head[1], len = Math.sqrt(dx * dx + dy * dy), nx = -dy / len, ny = dx / len;
    var waves = Math.max(1, Math.round(len / 45)), amp = Math.min(7, 3 + len / 30), pts = [], N = 24;
    for (var i = 0; i <= N; i++) {
      var t = i / N, w = Math.sin(t * Math.PI * 2 * waves) * amp * (1 - t * 0.5);
      pts.push([head[0] + dx * t + nx * w, head[1] + dy * t + ny * w]);
    }
    var ci = lcd ? 0 : SNAKE_CI[k % SNAKE_CI.length];
    var body = function (c) { c.moveTo(pts[0][0], pts[0][1]); for (var j = 1; j < pts.length; j++) c.lineTo(pts[j][0], pts[j][1]); };
    g.curve(body, lcd ? 0 : 0, lcd ? 5 : 6.2, { a: lcd ? 1 : 0.35 });
    g.curve(body, ci, lcd ? 3.4 : 4.6);
    if (!lcd) g.curve(body, 9, 1, { a: 0.5 });
    g.circle(head[0], head[1], 4.6, ci, { solid: true, stroke: !lcd });
    g.circle(head[0] - 1.6, head[1] - 1, 1.1, 9, { solid: true, white: true });
    g.circle(head[0] + 1.6, head[1] - 1, 1.1, 9, { solid: true, white: true });
  }

  function drawStatic(board) {
    return function (g) {
      var lcd = g.kind === "lcd", n, k;
      g.board(X0, Y0, 10 * C, 10 * C);
      for (n = 1; n <= 100; n++) {
        var c = Logic.cell(n), x = X0 + c[0] * C, y = Y0 + c[1] * C, odd = (c[0] + c[1]) % 2;
        if (lcd) { if (odd) g.rect(x + 0.5, y + 0.5, C - 1, C - 1, 9, {}); }
        else if (g.kind === "neon" || g.kind === "pixel") g.rect(x, y, C, C, 8, { r: 0, solid: true, edge: false, a: odd ? 0.95 : 0.45 });
        else g.rect(x, y, C, C, odd ? 9 : 8, { r: 0, solid: true, edge: false, a: odd ? 1 : 0.55 });
        // Pixel's coarse font: the numbers at the ends of the rows and every fifth only, so they don't run together
        if (g.kind !== "pixel" || n % 5 === 0 || n % 10 === 1) g.text(String(n), x + 2, y + 8, { size: 7, ci: 0, a: lcd ? 1 : 0.6, bold: false });
      }
      var goal = centre(100);
      g.star(goal[0], goal[1] + 2, 6, lcd ? 0 : 3, { solid: true });
      var B = Logic.BOARDS[board];
      for (var foot in B.ladders) ladder(g, centre(+foot), centre(B.ladders[foot]), lcd);
      k = 0;
      for (var head in B.snakes) snake(g, centre(+head), centre(B.snakes[head]), k++, lcd);
    };
  }

  function drawBoard(g, s, info, V) {
    var lcd = g.kind === "lcd", i;
    g.layer("snakes-" + s.board, 1, drawStatic(s.board));
    // the players along the top: token, name, square
    var w = 220 / s.n;
    for (i = 0; i < s.n; i++) {
      var x = 10 + i * w, on = i === s.turn && !s.over;
      token(g, x + 8, 15, i, 5.5);
      var nm = V.names[i] + (s.mode === "phones" && s.left[i] ? "*" : "");
      g.text(nm.length > (s.n > 3 ? 6 : 9) ? nm.slice(0, s.n > 3 ? 5 : 8) + "…" : nm, x + 17, 14, { size: 9, ci: 0 });
      g.text(s.pos[i] ? "on " + shown(s, i) : "start", x + 17, 25, { size: 8, ci: 0, a: 0.75 });
      if (on) g.line(x + 2, 31, x + w - 4, 31, lcd ? 0 : 7, 2.5, { cap: "butt" });
    }
    // tokens: off the board until they move; a moving token hops, then climbs or slides
    var spots = {}, list = [];
    for (i = 0; i < s.n; i++) {
      var p = posNow(s, i);
      if (!p) continue;
      (spots[p.key] = spots[p.key] || []).push(list.length);
      list.push({ seat: i, x: p.x, y: p.y });
    }
    for (var key in spots) {
      var ids = spots[key];
      if (ids.length < 2) continue;
      for (var k = 0; k < ids.length; k++) { list[ids[k]].x += (k % 2 ? 4.5 : -4.5); list[ids[k]].y += (k < 2 ? -4.5 : 4.5); list[ids[k]].small = true; }
    }
    for (i = 0; i < list.length; i++) {
      var t = list[i];
      token(g, t.x, t.y, t.seat, t.small ? 4.6 : 6.2);
      if (t.seat === s.turn && !s.over && !info.reduce && Math.floor(s.updates / 25) % 2 === 0) g.ring(t.x, t.y, (t.small ? 4.6 : 6.2) + 2.6, lcd ? 0 : 7, 1.4);
    }
  }
  function shown(s, seat) {
    var p = posNow(s, seat);
    return p ? p.n : s.pos[seat];
  }
  /** Where a token is drawn now (mid-move during its animation): { x, y, n, key } or null (still at the start). */
  function posNow(s, seat) {
    var a = s.anim, n = s.pos[seat];
    if (a && a.seat === seat) {
      var el = s.updates - a.at, hops = (a.mid - a.from) * Logic.HOP;
      if (el >= 0 && el < hops) n = Math.min(a.mid, a.from + 1 + Math.floor(el / Logic.HOP));
      else if (el >= hops && el < hops + Logic.SLIDE && a.to !== a.mid) {
        var t = (el - hops) / Logic.SLIDE, p0 = centre(a.mid), p1 = centre(a.to);
        return { x: p0[0] + (p1[0] - p0[0]) * t, y: p0[1] + (p1[1] - p0[1]) * t, n: a.mid, key: "m" + seat };
      } else if (el < 0) n = a.from;
    }
    if (!n) return null;
    var c = centre(n);
    return { x: c[0], y: c[1], n: n, key: "n" + n };
  }

  function create(canvas, opts) {
    return DK.session(canvas, opts, {
      Logic: Logic,
      drawBoard: drawBoard,
      hit: function (s, lx, ly) { return lx < 42 && ly > 260 ? "die" : "board"; },
      marker: function (g, x, y, seat, r) { token(g, x, y, seat, r); },
      chooseText: function () { return "Moving…"; },
      sounds: { hop: "turn", stay: "wall", ladder: "powerup", snake: "lose" },
    });
  }

  root.ArcadeGames.register({
    id: "snakes",
    name: "Snakes and Ladders",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "cpu",
    controls: "touch",
    buttons: [],
    race: false,
    turns: true,
    turnModes: ["phones"],
    options: [
      { id: "players", label: "Players", default: "2", choices: [{ id: "2", label: "2 players" }, { id: "3", label: "3 players" }, { id: "4", label: "4 players" }] },
      { id: "fill", label: "One phone: empty seats", default: "no", choices: [{ id: "no", label: "Left empty" }, { id: "yes", label: "The computer fills them" }] },
      { id: "board", label: "Board", default: "classic", choices: [{ id: "classic", label: "Classic" }, { id: "gentle", label: "Gentle (short snakes)" }] },
      { id: "finish", label: "Finish", default: "any", choices: [{ id: "any", label: "Reach 100" }, { id: "exact", label: "Exactly on 100" }] },
    ],
    help: "Roll the die and race to square 100. Ladders take you up, snakes slide you down. Tap anywhere (or Space) to roll — your token moves by itself. Finish: reach or pass 100, or (Exactly on 100) land on it exactly. Play the computer, everyone on one phone, or 2–4 phones turn by turn (👥 Play with someone; the server rolls).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
