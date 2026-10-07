/* Household Arcade — Lander: input and drawing around the rules in lander-logic.js.
   Registers itself as "lander" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.LanderLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "easy", label: "Easy" }, { id: "levels", label: "Levels" }];
  var W = Kit.W, FLOOR = Logic.FLOOR, TOP = Logic.TOP;
  var CAUSES = { ground: "MISSED THE PAD", tilt: "NOT UPRIGHT", fast: "TOO FAST" };

  var STARS = [];
  (function () {
    var r = 4242;
    for (var i = 0; i < 36; i++) {
      r = (r * 1103515245 + 12345) & 0x7fffffff; var x = r % W;
      r = (r * 1103515245 + 12345) & 0x7fffffff; var y = TOP + 18 + (r % 150);
      STARS.push([x, y]);
    }
  })();

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prev = null, landVer = 0, landRef = null;

    function snap() { prev = { x: s.x, y: s.y }; }
    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; snap(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; snap(); },
      step: function () {
        snap();
        var evs = pending.concat(Logic.step(s));
        pending = [];
        if (Math.abs(s.y - prev.y) > 40) snap();               // a new level: no sweep across the screen
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { land: "win", crash: "lose", empty: "wall", retry: "launch", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      // a finger held on the game fires the engine
      pointer: function (kind) {
        if (kind === "down") impl.input("up", true);
        else if (kind === "up") impl.input("up", false);
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, lcd = g.kind === "lcd", a = info.state === "running" ? info.alpha : 1, i;
        var l = s.land;
        if (l !== landRef) { landRef = l; landVer++; }
        g.hud("SCORE " + fmt(s.score), s.list ? "LEVEL " + s.level + "/" + s.list.length : "LEVEL " + s.level);

        // fuel, speeds (green when safe), lives, wind
        var frac = Math.max(0, s.fuel / l.fuel);
        g.text("FUEL", 10, 50, { size: 9, a: 0.85 });
        g.rect(36, 43, 60, 7, 8, { r: 2, a: lcd ? 1 : 0.6 });
        if (frac > 0) g.rect(36, 43, Math.max(2, 60 * frac), 7, frac < 0.2 ? 1 : 3, { r: 2, solid: true });
        var okV = s.vy <= Logic.SAFE_VY, okH = Math.abs(s.vx) <= Logic.SAFE_VX, okT = Math.abs(s.a) <= Logic.SAFE_TILT;
        // speeds and tilt: shown in green when they are safe for a landing (and underlined, for every look)
        var gauges = [["V", Math.max(0, s.vy).toFixed(1), okV, 106], ["H", Math.abs(s.vx).toFixed(1), okH, 148], ["A", String(Math.abs(s.a)), okT, 190]];
        for (i = 0; i < gauges.length; i++) {
          var gg = gauges[i];
          g.text(gg[0] + " " + gg[1], gg[3], 50, { size: 9, ci: gg[2] ? 4 : 1 });
          if (gg[2]) g.line(gg[3], 54, gg[3] + 30, 54, 4, 1);
        }
        for (i = 0; i < s.lives; i++) drawLander(g, 14 + i * 14, 64, 0, false, 0.6);
        if (l.wind) {
          g.text("WIND", 200, 66, { size: 9, align: "right", a: 0.85 });
          var wd = l.wind > 0 ? 1 : -1;
          g.line(206, 63, 226, 63, 0, 1.5); g.line(216 + 10 * wd, 63, 216 + 6 * wd, 60, 0, 1.5); g.line(216 + 10 * wd, 63, 216 + 6 * wd, 66, 0, 1.5);
        }

        if (!lcd) g.layer("lander-stars", 1, function (lg) {
          for (var k = 0; k < STARS.length; k++) lg.rect(STARS[k][0], STARS[k][1], 1.5, 1.5, 8, { r: 0, a: lg.kind === "paper" ? 0.35 : 0.9, edge: false });
        });
        // the ground and its pads (a cached layer for the level)
        g.layer("lander-ground", landVer, function (lg) {
          var pts = [[0, Kit.H]];
          for (var k = 0; k < Logic.N; k++) pts.push([k * Logic.STEP_X, FLOOR - l.heights[k]]);
          pts.push([W, Kit.H]);
          lg.poly(pts, 8, { a: lg.kind === "lcd" ? 0.3 : 0.75, stroke: true });
          for (k = 1; k < pts.length - 2; k++) lg.line(pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], 0, 1.5, { a: 0.9 });
          var pads = Logic.pads(l);
          for (k = 0; k < pads.length; k++) {
            var p = pads[k];
            lg.rect(p.from + 1, p.y - 2, p.to - p.from - 2, 4, 4, { r: 1, solid: true });
            lg.text("×" + p.mult, (p.from + p.to) / 2, p.y + 13 > Kit.H - 4 ? p.y - 6 : p.y + 13, { size: 10, align: "center", ci: lg.kind === "lcd" ? 0 : 3 });
          }
        });

        // the lander
        var x = prev ? prev.x + (s.x - prev.x) * a : s.x, y = prev ? prev.y + (s.y - prev.y) * a : s.y;
        if (s.phase === "crashed") {
          if (info.reduce) g.star(s.x, s.y + 2, 9, 2, { solid: true });
          else { var t = Logic.CRASH_T - s.t; g.ring(s.x, s.y + 2, 4 + Math.min(16, t * 0.6), 2, 2, { a: Math.max(0.5, 1 - t / 90) }); g.star(s.x, s.y + 2, 7, 1, { solid: true }); }
        } else {
          var flicker = info.reduce ? 1 : 0.8 + 0.4 * ((Math.floor(info.t * 20) % 3) / 2);
          drawLander(g, g.snap(x), g.snap(y), s.a, s.thrust && info.state === "running", 1, flicker);
        }

        if (info.state !== "over") {
          if (s.phase === "landed") banner(g, "LANDED ×" + s.lastMult, "+" + fmt(s.lastPoints) + (s.list && s.level >= s.list.length ? "" : "  ·  NEXT LEVEL"));
          else if (s.phase === "crashed") banner(g, CAUSES[s.stats.cause] || "CRASHED", s.lives > 0 ? s.lives + (s.lives === 1 ? " LANDER LEFT" : " LANDERS LEFT") : "");
          else if (s.updates < 1 || (s.fuel === l.fuel && s.vy < 0.6 && s.y < 80)) {
            g.text(s.list ? l.name.toUpperCase() : "LAND ON A PAD", W / 2, 96, { size: 12, align: "center", a: 0.9 });
          } else if (s.fuel === 0) g.text("OUT OF FUEL", W / 2, 96, { size: 12, align: "center", ci: 1 });
        }
      },
    };

    function drawLander(g, x, y, ang, thrust, scale, flicker) {
      var k = scale || 1, i = ang + 90, c = Logic.COS[i], sn = Logic.SIN[i];
      function at(dx, dy) { return [x + (c * dx - sn * dy) * k, y + (sn * dx + c * dy) * k]; }
      if (thrust) {
        var f = 7 + 5 * (flicker || 1);
        g.poly([at(-3, 5), at(0, 5 + f), at(3, 5)], 2, { solid: true });
      }
      var l1 = at(-3, 3), f1 = at(-6, 7), l2 = at(3, 3), f2 = at(6, 7);
      g.line(l1[0], l1[1], f1[0], f1[1], 0, 1.5);
      g.line(l2[0], l2[1], f2[0], f2[1], 0, 1.5);
      if (k >= 1) { var p1 = at(-8, 7), p2 = at(-4, 7), p3 = at(4, 7), p4 = at(8, 7); g.line(p1[0], p1[1], p2[0], p2[1], 0, 1.5); g.line(p3[0], p3[1], p4[0], p4[1], 0, 1.5); }
      g.poly([at(-5, 4), at(5, 4), at(4, 0), at(-4, 0)], 8, { stroke: true, solid: g.kind !== "lcd" });
      var hc = at(0, -2);
      g.circle(hc[0], hc[1], 4.5 * k, 7, { stroke: true, solid: true });
      if (k >= 1) { var w = at(1, -3); g.circle(w[0], w[1], 1.5, 9, { solid: true, white: true, edge: false }); }
    }
    function banner(g, a, b) {
      var y = 130, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "lander",
    name: "Lander",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    buttons: [
      { action: "left", label: "⟲", aria: "Turn left", place: [1, 1, 1, 1] },
      { action: "right", label: "⟳", aria: "Turn right", place: [2, 1, 1, 1] },
      { action: "up", label: "▲ Engine", aria: "Engine", wide: true, place: [3, 1, 2, 1] },
    ],
    help: "← → turn the lander, ↑ or Space fires the engine (it pushes the way the lander points and uses fuel). On a phone use the buttons, or hold a finger on the game for the engine. Touch down on a pad upright and slowly — the numbers at the top turn green when it's safe. Small pads score more; fuel left adds to the score.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
