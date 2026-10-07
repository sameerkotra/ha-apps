/* Household Arcade — Slide Puzzle: input and drawing around the rules in slide-logic.js.
   Registers itself as "slide" with window.ArcadeGames.

   A tap on a tile in the gap's row or column slides it (and the tiles between) into the gap; a swipe or an arrow
   slides the tile on that side of the gap. Picture modes draw one of a few pictures made of simple shapes (no image
   files): the picture is drawn once into a cached layer and each tile shows its own square of it. Peek (held: the
   button, C or a controller's X) shows the finished picture or the numbers in order. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.SlideLogic;

  var MODES = [{ id: "three", label: "3 × 3" }, { id: "four", label: "4 × 4" }, { id: "five", label: "5 × 5" },
    { id: "picture", label: "Picture 3 × 3 (gentle)" }, { id: "picture4", label: "Picture 4 × 4" }];
  var LABELS = { three: "3 × 3", four: "4 × 4", five: "5 × 5", picture: "PICTURE 3 × 3", picture4: "PICTURE 4 × 4" };
  var BX = 14, BY = 42, BS = 212, MSG_Y = 272;

  // ---------- the pictures (drawn in a BS × BS square at BX, BY; coordinates are fractions of it) ----------
  // In Retro LCD large areas are drawn as outlines (colour 9) and details in ink, so the picture stays readable.
  function scene(g, n) {
    var lcd = g.kind === "lcd", neon = g.kind === "neon", sol = !lcd && !neon;   // Neon: outlines with a faint fill (a solid fill would glow white)
    function X(f) { return BX + f * BS; }
    function Y(f) { return BY + f * BS; }
    function area(ci) { return lcd ? 9 : ci; }
    function rect(x, y, w, h, ci, o) { g.rect(X(x), Y(y), w * BS, h * BS, ci, Object.assign({ r: 0, edge: false }, o || {})); }
    function disc(x, y, r, ci, o) { g.circle(X(x), Y(y), r * BS, ci, o || {}); }
    function poly(pts, ci, o) { g.poly(pts.map(function (p) { return [X(p[0]), Y(p[1])]; }), ci, Object.assign({ edge: false }, o || {})); }
    function line(x1, y1, x2, y2, ci, w) { g.line(X(x1), Y(y1), X(x2), Y(y2), ci, w || 2); }
    var i, ctx = g.ctx;
    ctx.save(); ctx.beginPath(); ctx.rect(BX, BY, BS, BS); ctx.clip();
    if (n === 0) {                                  // a house on a hill, a tree and the sun
      if (!lcd) rect(0, 0, 1, 1, 7, { a: 0.35, solid: !neon });
      disc(0.8, 0.18, 0.1, area(3), { solid: sol });
      disc(0.3, 1.25, 0.65, area(4), { solid: sol }); disc(0.95, 1.2, 0.45, area(4), { solid: sol, a: 0.85 });
      rect(0.18, 0.45, 0.36, 0.3, area(2), { solid: sol });
      poly([[0.13, 0.46], [0.36, 0.24], [0.59, 0.46]], area(1), { solid: sol });
      rect(0.31, 0.58, 0.1, 0.17, 0, { solid: !neon });
      rect(0.22, 0.52, 0.07, 0.07, lcd ? 0 : 9, { solid: !neon }); rect(0.44, 0.52, 0.07, 0.07, lcd ? 0 : 9, { solid: !neon });
      rect(0.72, 0.5, 0.05, 0.22, 0, { solid: !neon });
      disc(0.745, 0.44, 0.12, area(4), { solid: sol });
      disc(0.745, 0.44, 0.05, lcd ? 0 : 4, { solid: !neon, a: 0.6 });
    } else if (n === 1) {                           // a sailing boat on the waves
      if (!lcd) rect(0, 0, 1, 0.62, 7, { a: 0.35, solid: !neon });
      disc(0.2, 0.2, 0.09, area(3), { solid: sol });
      rect(0, 0.62, 1, 0.38, area(5), { solid: sol, a: lcd ? 1 : 0.8 });
      for (i = 0; i < 4; i++) line(0.08 + i * 0.24, 0.8 + (i % 2) * 0.08, 0.2 + i * 0.24, 0.8 + (i % 2) * 0.08, lcd ? 0 : 9, 2);
      poly([[0.3, 0.6], [0.8, 0.6], [0.72, 0.7], [0.38, 0.7]], area(1), { solid: sol });
      line(0.55, 0.6, 0.55, 0.15, 0, 2);
      poly([[0.56, 0.17], [0.56, 0.56], [0.82, 0.56]], area(3), { solid: sol });
      poly([[0.54, 0.22], [0.54, 0.56], [0.36, 0.56]], area(9), { solid: sol });
      line(0.7, 0.18, 0.74, 0.15, 0, 1.5); line(0.74, 0.15, 0.78, 0.18, 0, 1.5);
      line(0.82, 0.26, 0.85, 0.23, 0, 1.5); line(0.85, 0.23, 0.88, 0.26, 0, 1.5);
    } else if (n === 2) {                           // a rocket, a ringed planet and stars
      if (!lcd) rect(0, 0, 1, 1, 6, { a: 0.28, solid: !neon });
      for (i = 0; i < 7; i++) g.star(X([0.1, 0.3, 0.85, 0.65, 0.12, 0.9, 0.45][i]), Y([0.12, 0.3, 0.12, 0.85, 0.7, 0.5, 0.92][i]), 5, lcd ? 0 : 3, { solid: !neon });
      disc(0.25, 0.48, 0.13, area(2), { solid: sol });
      line(0.05, 0.56, 0.45, 0.4, lcd ? 0 : 3, 3);
      poly([[0.62, 0.12], [0.72, 0.3], [0.72, 0.68], [0.52, 0.68], [0.52, 0.3]], area(9), { solid: sol, stroke: true });
      disc(0.62, 0.38, 0.05, area(5), { solid: sol });
      poly([[0.52, 0.52], [0.44, 0.72], [0.52, 0.68]], area(1), { solid: sol });
      poly([[0.72, 0.52], [0.8, 0.72], [0.72, 0.68]], area(1), { solid: sol });
      poly([[0.55, 0.7], [0.62, 0.9], [0.69, 0.7]], lcd ? 0 : 2, { solid: !neon });
    } else if (n === 3) {                           // a flower in a pot with a butterfly
      if (!lcd) rect(0, 0, 1, 1, 4, { a: 0.22, solid: !neon });
      line(0.45, 0.62, 0.45, 0.32, 4, 3);
      poly([[0.45, 0.5], [0.3, 0.42], [0.45, 0.46]], area(4), { solid: sol });
      for (i = 0; i < 6; i++) {
        var px = [0.45, 0.56, 0.56, 0.45, 0.34, 0.34][i], py = [0.17, 0.24, 0.36, 0.43, 0.36, 0.24][i];
        disc(px, py, 0.075, area(1), { solid: sol });
      }
      disc(0.45, 0.3, 0.065, lcd ? 0 : 3, { solid: !neon });
      poly([[0.3, 0.62], [0.6, 0.62], [0.55, 0.88], [0.35, 0.88]], area(2), { solid: sol });
      disc(0.78, 0.6, 0.06, area(6), { solid: sol }); disc(0.88, 0.6, 0.06, area(6), { solid: sol });
      line(0.83, 0.53, 0.83, 0.68, 0, 2);
    } else if (n === 4) {                           // a fish under the sea
      if (!lcd) rect(0, 0, 1, 1, 5, { a: 0.35, solid: !neon });
      rect(0, 0.86, 1, 0.14, area(3), { solid: sol });
      for (i = 0; i < 3; i++) { line(0.1 + i * 0.08, 0.86, 0.12 + i * 0.08, 0.58 + i * 0.06, 4, 3); }
      g.ellipse(X(0.52), Y(0.45), 0.22 * BS, 0.13 * BS, area(2), { solid: sol });
      poly([[0.72, 0.45], [0.9, 0.32], [0.9, 0.58]], area(2), { solid: sol });
      disc(0.4, 0.42, 0.03, 0, { solid: !neon });
      line(0.5, 0.33, 0.56, 0.57, lcd ? 0 : 1, 2);
      g.ring(X(0.3), Y(0.2), 0.04 * BS, lcd ? 0 : 9, 1.5); g.ring(X(0.24), Y(0.1), 0.03 * BS, lcd ? 0 : 9, 1.5);
      g.ring(X(0.82), Y(0.15), 0.035 * BS, lcd ? 0 : 9, 1.5);
    } else {                                        // a hot-air balloon over the hills
      if (!lcd) rect(0, 0, 1, 1, 7, { a: 0.3, solid: !neon });
      disc(0.15, 0.2, 0.07, area(9), { solid: sol }); disc(0.24, 0.2, 0.09, area(9), { solid: sol });
      disc(0.85, 0.32, 0.07, area(9), { solid: sol });
      disc(0.25, 1.2, 0.5, area(4), { solid: sol }); disc(0.85, 1.25, 0.5, area(4), { solid: sol, a: 0.8 });
      disc(0.55, 0.36, 0.2, area(1), { solid: sol });
      rect(0.42, 0.33, 0.26, 0.06, lcd ? 0 : 3, { solid: !neon });
      line(0.42, 0.48, 0.5, 0.66, 0, 1.5); line(0.68, 0.48, 0.6, 0.66, 0, 1.5);
      rect(0.49, 0.66, 0.12, 0.08, area(2), { solid: sol });
    }
    ctx.restore();
  }

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, numbersOn = o.numbers !== "off", s = null, pending = [], down = null;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function ts() { return BS / s.n; }
    function cellAt(x, y) {
      var t = ts(), c = Math.floor((x - BX) / t), r = Math.floor((y - BY) / t);
      return c < 0 || r < 0 || c >= s.n || r >= s.n ? -1 : r * s.n + c;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed }); pending = []; down = null; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { slide: "turn", nope: "wall", peek: "bounce", win: "win" },
      input: function (action, isDown) { queue(Logic.press(s, action, isDown)); },
      pointer: function (kind, lx, ly) {
        if (kind === "down") { down = { x: lx, y: ly }; return; }
        if (kind !== "up" || !down) return;
        var dx = lx - down.x, dy = ly - down.y, d0 = down;
        down = null;
        if (Math.max(Math.abs(dx), Math.abs(dy)) >= 12) {
          queue(Logic.arrow(s, Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up")));
          return;
        }
        var i = cellAt(d0.x, d0.y);
        if (i >= 0) queue(Logic.move(s, i));
      },
      draw: draw,
    };

    function textCi(g) { return g.kind === "paper" || g.kind === "neon" || g.kind === "lcd" ? 0 : 9; }
    function tileAt(g, t, x, y, size, home) {
      var lcd = g.kind === "lcd", inPlace = home;
      if (s.picture) {
        var ctx = g.ctx, hx = BX + ((t - 1) % s.n) * size, hy = BY + Math.floor((t - 1) / s.n) * size;
        ctx.save();
        ctx.beginPath(); ctx.rect(x + 0.5, y + 0.5, size - 1, size - 1); ctx.clip();
        ctx.translate(x - hx, y - hy);
        g.layer("slide-pic", s.scene, function (lg) { scene(lg, s.scene); });
        ctx.restore();
        if (!g.lowres) g.rect(x + 0.5, y + 0.5, size - 1, size - 1, 0, { r: 2, stroke: true, a: 0.25, edge: false }); else frame(g, x, y, size, 0, lcd ? 1 : 0.5);
        if (numbersOn) {
          // the coarse looks' pixel font needs a bigger size to stay readable (two dots a stroke on Retro LCD)
          var big = g.lowres, bw = big ? (t > 9 ? 22 : 13) : (t > 9 ? 15 : 10), bh = big ? 17 : 10;
          g.rect(x + 2, y + 2, bw, bh, lcd ? 0 : g.kind === "neon" || g.kind === "pixel" ? 8 : 9, { r: 2, solid: true, a: 0.85 });   // a badge the number reads on
          g.text(String(t), x + 2 + bw / 2, y + 2 + bh / 2 + 0.5, { size: big ? 16 : 8, align: "center", base: "middle", ci: lcd ? 9 : 0 });
        }
        return;
      }
      var ci = inPlace ? 4 : 5;
      if (lcd) { g.rect(x + 1, y + 1, size - 2, size - 2, 9, {}); if (inPlace) g.rect(x + 3, y + 3, size - 6, size - 6, 9, {}); }
      else g.rect(x + 1, y + 1, size - 2, size - 2, ci, { r: 4, solid: g.kind !== "neon", stroke: g.kind === "modern", bevel: true });   // Neon: a glowing outline
      g.text(String(t), x + size / 2, y + size / 2 + 1, { size: Math.min(26, size * 0.42), align: "center", base: "middle", ci: textCi(g) });
      if (inPlace && !lcd) g.circle(x + size - 6, y + 6, 2, textCi(g), { solid: true });       // in its place: a dot as well as the colour
    }
    function frame(g, x, y, size, ci, a) {
      g.line(x, y, x + size, y, ci, 1, { a: a, cap: "butt" }); g.line(x, y + size, x + size, y + size, ci, 1, { a: a, cap: "butt" });
      g.line(x, y, x, y + size, ci, 1, { a: a, cap: "butt" }); g.line(x + size, y, x + size, y + size, ci, 1, { a: a, cap: "butt" });
    }

    function draw(g, info) {
      var size = ts(), n = s.n, i, lcd = g.kind === "lcd";
      g.hud(LABELS[s.mode] || "", Logic.clock(Logic.seconds(s)));
      g.board(BX, BY, BS, BS);
      if (!lcd) g.rect(BX, BY, BS, BS, 8, { r: 2, a: 0.35, solid: true, edge: false });
      var moving = {};
      if (s.anim && !info.reduce) {
        var p = Math.min(1, (s.updates - s.anim.at + (info.state === "running" ? info.alpha : 1)) / Logic.SLIDE_T);
        p = 1 - (1 - p) * (1 - p);
        s.anim.moved.forEach(function (m) { moving[m[0]] = { from: m[1], to: m[2], p: p }; });
      }
      for (i = 0; i < s.tiles.length && !(s.won && s.picture); i++) {     // solved picture: the whole picture alone
        var t = s.tiles[i];
        if (!t) continue;
        var r = Math.floor(i / n), c = i % n, x = BX + c * size, y = BY + r * size, mv = moving[t];
        if (mv && mv.p < 1) {
          x = BX + ((mv.from % n) + ((mv.to % n) - (mv.from % n)) * mv.p) * size;
          y = BY + (Math.floor(mv.from / n) + (Math.floor(mv.to / n) - Math.floor(mv.from / n)) * mv.p) * size;
        }
        tileAt(g, t, g.snap(x), g.snap(y), size, t === i + 1);
      }
      if (s.peek || (s.won && s.picture)) {
        if (s.picture) {
          g.layer("slide-pic", s.scene, function (lg) { scene(lg, s.scene); });
        } else {
          g.rect(BX, BY, BS, BS, lcd ? 9 : 8, { r: 2, solid: !lcd, a: 0.9 });
          for (i = 1; i < n * n; i++) g.text(String(i), BX + ((i - 1) % n + 0.5) * size, BY + (Math.floor((i - 1) / n) + 0.5) * size + 1,
            { size: Math.min(22, size * 0.36), align: "center", base: "middle", a: 0.8 });
        }
        if (!s.won) g.text("PEEK", BX + BS - 4, BY + BS - 6, { size: 10, align: "right", ci: lcd ? 0 : 1 });
      }
      var text = s.won ? "SOLVED IN " + s.moves + (s.moves === 1 ? " MOVE" : " MOVES") : "MOVES " + s.moves;
      g.text(text, 120, MSG_Y, { size: 12, align: "center" });
      if (!s.won && s.moves === 0) g.text("Tap a tile next to the gap", 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      else if (!s.won) g.text(s.picture ? "Hold Peek to see the picture" : "Hold Peek to see the order", 120, MSG_Y + 16, { size: 9, align: "center", a: 0.7 });
      if (s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("SOLVED!", 120, 147, { size: 16, align: "center", ci: lcd ? 9 : 0 });
        g.text(Logic.clock(Logic.seconds(s)) + " · " + s.moves + " MOVES", 120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "slide",
    name: "Slide Puzzle",
    modes: MODES,
    defaultMode: "four",
    controls: "touch",
    buttons: [{ action: "alt", label: "👁 Peek", aria: "Hold to peek at the finished puzzle", wide: true }],
    options: [{ id: "numbers", label: "Numbers on picture tiles", default: "on", choices: [{ id: "on", label: "On" }, { id: "off", label: "Off" }] }],
    help: "Put the tiles back in order — 1, 2, 3 … with the gap at the bottom right (or the picture back together). Tap a tile in the gap's row or column to slide it, or swipe; the arrow keys slide the tile next to the gap that way. Hold Peek (or C) to see how it should look. Each tile moved is a move: fewer moves score more. Picture 3 × 3 is the gentle one for little ones.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
