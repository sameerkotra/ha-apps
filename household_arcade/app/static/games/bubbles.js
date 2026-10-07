/* Household Arcade — Bubble Pop: input and drawing around the rules in bubbles-logic.js.
   Registers itself as "bubbles" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.BubblesLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "relaxed", label: "Relaxed (gentle)" }, { id: "puzzle", label: "Puzzles" }, { id: "endless", label: "Endless" }];
  var W = Kit.W, R = Logic.R, LX = Logic.LX, LY = Logic.LY, NX = LX - 52, NY = LY + 8;
  var COLOURS = [1, 5, 3, 4, 6, 2];          // colour 1 red, 2 blue, 3 yellow, 4 green, 5 purple, 6 orange

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prevShot = null, held = false;

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; prevShot = null; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; prevShot = null; },
      step: function () {
        prevShot = s.shot ? { x: s.shot.x, y: s.shot.y } : null;
        var evs = pending.concat(Logic.step(s));
        pending = [];
        if (s.shot && !prevShot) prevShot = { x: Logic.LX, y: Logic.LY };
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { shoot: "launch", bounce: "bounce", stick: "turn", pop: "break", clear: "row", lower: "wall", push: "wall",
        swapNext: "turn", low: "lose", level: "level", refill: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      // a finger (or the mouse) aims; letting go shoots; a tap on the next bubble swaps it in
      pointer: function (kind, lx, ly) {
        if (kind === "down") {
          if (Math.abs(lx - NX) < 16 && Math.abs(ly - NY) < 16) { held = false; impl.input("alt", true); return; }
          held = true; Logic.aimAt(s, lx, ly);
        } else if (kind === "move") Logic.aimAt(s, lx, ly);
        else if (kind === "up") {
          if (held || ly >= Kit.H - 4) { Logic.aimAt(s, lx, ly); impl.input("fire", true); }
          held = false;
        }
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, lcd = g.kind === "lcd", a = info.state === "running" ? info.alpha : 1, i, r, c;
        g.hud("SCORE " + fmt(s.score), s.list ? "PUZZLE " + s.level + "/" + s.list.length : s.mode === "endless" ? "LEVEL " + s.level : "BOARD " + s.level);

        // walls, the ceiling coming down, the line not to cross
        var top = Logic.ceil(s);
        g.line(Logic.X0 - 1, Logic.TOP, Logic.X0 - 1, LY + 14, 0, 2, { a: 0.7 });
        g.line(Logic.X1 + 1, Logic.TOP, Logic.X1 + 1, LY + 14, 0, 2, { a: 0.7 });
        g.rect(Logic.X0 - 2, Logic.TOP - 4, Logic.X1 - Logic.X0 + 4, top - Logic.TOP + 4, 8, { r: 0, solid: true, edge: false });
        g.line(Logic.X0, Logic.LOSE_Y + R, Logic.X1, Logic.LOSE_Y + R, 1, 1, { a: 0.6, dash: [4, 4] });

        // the bubbles
        for (r = 0; r < Logic.MAX_ROWS; r++) for (c = 0; c < Logic.COLS; c++) {
          var k = s.grid[r][c];
          if (k) drawBubble(g, Logic.cellX(s, r, c), Logic.cellY(s, r), k, 1);
        }
        for (i = 0; i < s.falls.length; i++) drawBubble(g, s.falls[i].x, s.falls[i].y, s.falls[i].k, 1);
        for (i = 0; i < s.pops.length; i++) {
          var p = s.pops[i];
          if (info.reduce) { if (p.t < 8) g.star(p.x, p.y, 7, COLOURS[(p.k - 1) % 6], { a: 0.9 }); }
          else g.ring(p.x, p.y, R * (0.6 + p.t / 16), COLOURS[(p.k - 1) % 6], 1.5, { a: Math.max(0.5, 1 - p.t / 20) });
        }

        // the aiming guide, the launcher, the bubble in it and the next one
        if (s.phase === "aim" && !s.shot && info.state !== "over") {
          var pts = Logic.guide(s, 150);
          for (i = 0; i + 1 < pts.length; i++) g.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], 0, 1, { a: 0.5, dash: [2, 5] });
        }
        var an = s.aim * Math.PI / 180, bx = LX + Math.sin(an) * 22, by = LY - Math.cos(an) * 22;
        g.line(LX, LY, bx, by, 0, 4, { a: 0.8 });
        g.circle(LX, LY + 17, 13, 8, { solid: true, stroke: true });
        if (!s.shot) drawBubble(g, LX, LY, s.cur, 1);
        drawBubble(g, NX, NY, s.next, 0.75);
        g.text("NEXT", NX, NY + 18, { size: 8, align: "center", a: 0.8 });
        // shots left before the ceiling comes down (or a new row)
        var left = s.dropEvery - s.sinceDrop;
        for (i = 0; i < Math.min(left, 12); i++) g.circle(LX + 30 + i * 7, NY + 6, 2.4, left <= 2 ? 1 : 0, { solid: true, a: 0.8 });
        g.text(s.mode === "endless" ? "NEW ROW" : "CEILING", LX + 30, NY + 20, { size: 8, a: 0.8 });

        if (s.shot) {
          var sx = prevShot ? prevShot.x + (s.shot.x - prevShot.x) * a : s.shot.x, sy = prevShot ? prevShot.y + (s.shot.y - prevShot.y) * a : s.shot.y;
          drawBubble(g, g.snap(sx), g.snap(sy), s.shot.k, 1);
        }

        if (info.state !== "over") {
          if (s.phase === "clear") banner(g, "BOARD CLEAR!", "+" + fmt(Logic.CLEAR_POINTS));
          else if (s.phase === "lost") banner(g, "TOO LOW!", "");
          else if (s.stats.shots === 0 || (s.list && s.updates < 1)) {
            var pz = s.list ? s.list[s.level - 1] : null;
            if (pz) g.text(pz.name.toUpperCase(), W / 2, 230, { size: 12, align: "center", a: 0.9 });
          }
        }
      },
    };

    // A bubble: its colour and, so colours never have to be told apart by colour alone, its own small mark.
    function drawBubble(g, x, y, k, scale) {
      var rr = (R - 1) * scale, ci = COLOURS[(k - 1) % 6], lcd = g.kind === "lcd";
      g.circle(x, y, rr, ci, { solid: true, stroke: true });
      if (!g.lowres && g.kind !== "contrast" && scale >= 1) g.circle(x - rr * 0.35, y - rr * 0.4, rr * 0.22, g.dark ? 0 : 9, { a: 0.55, edge: false });   // a shine (light in both themes)
      mark(g, x, y, k, scale, lcd);
    }
    function mark(g, x, y, k, sc, lcd) {
      var m = 4.5 * sc, w = lcd ? 1.5 : 1.6, mc = g.kind === "contrast" ? 0 : 9;
      function bar(x1, y1, x2, y2) {
        if (lcd) {            // Retro LCD: a light mark made of light dots on the dark bubble
          var n = 4;
          for (var j = 0; j <= n; j++) g.circle(x1 + (x2 - x1) * j / n, y1 + (y2 - y1) * j / n, 0.9, 9, { solid: true, white: true });
        } else g.line(x1, y1, x2, y2, mc, w, { a: 0.9 });
      }
      switch ((k - 1) % 6) {
        case 0: break;                                                   // red: plain
        case 1: bar(x - m, y, x + m, y); break;                          // blue: a line across
        case 2: g.circle(x, y, 2.2 * sc, lcd ? 9 : mc, { solid: true, white: true, a: 0.9, edge: false }); break;   // yellow: a dot
        case 3: bar(x, y - m, x, y + m); break;                          // green: a line down
        case 4: bar(x - m * 0.8, y - m * 0.8, x + m * 0.8, y + m * 0.8); bar(x + m * 0.8, y - m * 0.8, x - m * 0.8, y + m * 0.8); break;   // purple: a cross
        default: bar(x - m, y + m * 0.6, x, y - m * 0.7); bar(x, y - m * 0.7, x + m, y + m * 0.6);     // orange: a peak
      }
    }
    function banner(g, a, b) {
      var y = 160, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "bubbles",
    name: "Bubble Pop",
    modes: MODES,
    defaultMode: "classic",
    controls: "paddle",
    padLabel: "Shoot",
    help: "← → aim, Space or ↑ shoots, ↓ or C swaps in the next bubble. On a phone drag on the game (or the strip below it) to aim and let go to shoot; tap the next bubble to swap. Three or more of a colour pop, and anything left hanging drops. Every few shots the ceiling comes down.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
