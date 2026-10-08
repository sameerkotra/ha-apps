/* Household Arcade — Rocks: input and drawing around the rules in rocks-logic.js.
   Registers itself as "rocks" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.RocksLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "calm", label: "Calm" }, { id: "waves", label: "Waves" }];
  var W = Logic.W, TOP = Logic.TOP, SH = Logic.SH;

  // a fixed sprinkle of far stars (the same every game)
  var STARS = [];
  (function () {
    var r = 12345;
    for (var i = 0; i < 40; i++) {
      r = (r * 1103515245 + 12345) & 0x7fffffff; var x = r % W;
      r = (r * 1103515245 + 12345) & 0x7fffffff; var y = TOP + (r % SH);
      STARS.push([x, y]);
    }
  })();

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prev = null;

    function snap() { prev = { x: s.ship.x, y: s.ship.y, a: s.ship.a }; }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; snap(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; snap(); },
      step: function () {
        snap();
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { fire: "turn", "break": "break", saucer: "powerup", saucerHit: "bonus", lifeLost: "lose", clear: "row",
        level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      pointer: function (kind) {
        // a tap on the game fires
        if (kind === "down") impl.input("fire", true);
        else if (kind === "up") impl.input("fire", false);
      },
      draw: function (g, info) {
        var a = info.alpha, lcd = g.kind === "lcd", i;
        var rockCi = g.kind === "pixel" ? 2 : g.kind === "neon" ? 6 : 8;
        g.hud("SCORE " + Kit.fmt(s.score), s.waves ? "WAVE " + s.wave + "/" + s.waves.length : "WAVE " + s.wave);

        var ctx = g.ctx;
        ctx.save();
        ctx.beginPath(); ctx.rect(0, TOP, W, SH); ctx.clip();
        if (!lcd) {
          g.layer("rocks-stars", 1, function (lg) {
            for (var k = 0; k < STARS.length; k++) lg.rect(STARS[k][0], STARS[k][1], 1.5, 1.5, 8, { r: 0, a: 0.9, edge: false });
          });
        }

        // rocks: irregular outlines, turning slowly
        for (i = 0; i < s.rocks.length; i++) {
          var r = s.rocks[i];
          wrapDraw(r.x, r.y, Logic.RADIUS[r.size], function (x, y) { drawRock(g, r, x, y, rockCi); });
        }
        // the saucer and its shots (squares)
        if (s.saucer) {
          var u = s.saucer;
          wrapDraw(u.x, u.y, 10, function (x, y) {
            g.ellipse(x, y + 1, 9, 4, 6, { stroke: true });
            g.circle(x, y - 2.5, 4, 6, { stroke: true });
          });
        }
        for (i = 0; i < s.sshots.length; i++) g.rect(s.sshots[i].x - 2, s.sshots[i].y - 2, 4, 4, 1, { r: 0, solid: true });
        // your shots (dots)
        for (i = 0; i < s.shots.length; i++) g.circle(s.shots[i].x, s.shots[i].y, 1.8, 0, { solid: true });

        // the ship
        var p = s.ship;
        if (!p.dead) {
          var px = p.x, py = p.y, pa = p.a;
          if (prev && Math.abs(p.x - prev.x) < W / 2 && Math.abs(p.y - prev.y) < SH / 2) {
            px = prev.x + (p.x - prev.x) * a; py = prev.y + (p.y - prev.y) * a;
            var da = p.a - prev.a;
            if (Math.abs(da) < 1) pa = prev.a + da * a;
          }
          wrapDraw(px, py, 14, function (x, y) { drawShip(g, x, y, pa, p.thrust && info.state === "running"); });
          if (p.safe > 0 && info.state !== "over") g.ring(px, py, 12, 7, 1.5, { a: 0.85 });
        }

        // bursts: a ring that grows (a small star with reduce motion)
        for (i = 0; i < s.booms.length; i++) {
          var b = s.booms[i];
          if (info.reduce) { if (b.t < 15) g.star(b.x, b.y, Math.min(8, b.r * 0.5), 2, { a: 0.9 }); }
          else g.ring(b.x, b.y, b.r * (0.4 + b.t / 30), 2, 1.5, { a: Math.max(0.5, 1 - b.t / 40) });
        }
        ctx.restore();
        g.line(0, Logic.BOTTOM + 1, W, Logic.BOTTOM + 1, 8, 1, { a: lcd ? 1 : 0.9 });

        // lives left as little ships, and the wave's name
        for (i = 0; i < Math.min(s.lives, 8); i++) drawShip(g, 12 + i * 13, 292, -Math.PI / 2, false, 0.7);
        if (s.waves) g.text(s.name.toUpperCase(), 232, 296, { size: 9, align: "right", a: 0.8 });

        if (info.state !== "over") {
          if (s.pause > 0) g.text("WAVE CLEAR", W / 2, 120, { size: 14, align: "center" });
          else if (s.banner > 0) {
            g.text(s.waves ? "WAVE " + s.wave + "/" + s.waves.length : "WAVE " + s.wave, W / 2, 104, { size: 14, align: "center" });
            if (s.waves) g.text(s.name.toUpperCase(), W / 2, 122, { size: 11, align: "center", a: 0.85 });
          }
        }
      },
    };

    /** Draw something at (x, y) and again across any edge it overlaps (the space wraps around). */
    function wrapDraw(x, y, r, fn) {
      var xs = [x], ys = [y];
      if (x < r) xs.push(x + W); else if (x > W - r) xs.push(x - W);
      if (y < TOP + r) ys.push(y + SH); else if (y > TOP + SH - r) ys.push(y - SH);
      for (var i = 0; i < xs.length; i++) for (var j = 0; j < ys.length; j++) fn(xs[i], ys[j]);
    }

    function drawRock(g, r, x, y, ci) {
      var R = Logic.RADIUS[r.size], n = r.shape.length, pts = [];
      for (var i = 0; i < n; i++) {
        var an = r.rot + i * Math.PI * 2 / n;
        pts.push([x + Math.cos(an) * R * r.shape[i], y + Math.sin(an) * R * r.shape[i]]);
      }
      g.poly(pts, ci, { stroke: true });
      if (g.kind === "lcd") g.curve(function (c) { c.moveTo(pts[0][0], pts[0][1]); for (var k = 1; k < n; k++) c.lineTo(pts[k][0], pts[k][1]); c.closePath(); }, 0, 2);
      if (r.size === 3 && !g.lowres) g.circle(x + R * 0.25, y - R * 0.2, R * 0.18, 0, { a: 0.15, edge: false });
    }

    function drawShip(g, x, y, an, thrust, scale) {
      var k = scale || 1, c = Math.cos(an), sn = Math.sin(an);
      function at(dx, dy) { return [x + (c * dx - sn * dy) * k, y + (sn * dx + c * dy) * k]; }
      if (thrust) g.poly([at(-6, -3), at(-12, 0), at(-6, 3)], 2, {});
      g.poly([at(9, 0), at(-6, -6), at(-3, 0), at(-6, 6)], 7, { stroke: true, edge: false });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "rocks",
    name: "Rocks",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    buttons: [
      { action: "left", label: "⟲", aria: "Turn left", place: [1, 1, 1, 1] },
      { action: "right", label: "⟳", aria: "Turn right", place: [2, 1, 1, 1] },
      { action: "up", label: "▲", aria: "Thrust", place: [3, 1, 1, 1] },
      { action: "fire", label: "Fire", aria: "Fire", wide: true, place: [4, 1, 2, 1] },
    ],
    help: "← → turn, ↑ thrusts, Space fires (hold to keep firing). On a phone use the buttons; a tap on the game fires too. Shoot every rock to clear the wave; big rocks break into smaller ones. The edges wrap around.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
