/* Household Arcade — Road Hop: input and drawing around the rules in hop-logic.js.
   Registers itself as "hop" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.HopLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "easy", label: "Easy" }, { id: "levels", label: "Levels" }];
  var CS = Logic.CS, OX = Logic.OX, BW = Logic.COLS * Logic.CS;
  var CAR_COLOURS = [1, 2, 3, 6, 7];
  var CAUSES = { car: "SQUASHED!", water: "SPLASH!", swept: "SWEPT AWAY!", time: "OUT OF TIME!" };

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], runCache = {};

    function runsOf(pattern) {
      if (!runCache[pattern]) runCache[pattern] = Logic.runs(pattern);
      return runCache[pattern];
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { hop: "turn", bump: "wall", home: "bonus", splat: "lose", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, a = info.alpha, i, r;
        var total = Logic.levelCount(s), lcd = g.kind === "lcd";
        g.hud("SCORE " + fmt(s.score), s.list ? "LEVEL " + s.level + "/" + total : "LEVEL " + s.level);

        // lives (little hoppers) and the time left
        for (i = 0; i < s.lives; i++) drawHopper(g, 14 + i * 16, 46, "up", 0.7);
        var frac = Math.max(0, s.timer / s.time), low = s.timer < 10 * 60;
        g.text("TIME", 112, 50, { size: 9, align: "right", a: 0.85 });
        g.rect(116, 42, 110, 8, 8, { r: 2, a: lcd ? 1 : 0.6 });
        if (frac > 0) g.rect(116, 42, Math.max(2, 110 * frac), 8, low ? 1 : 4, { r: 2, solid: true });
        if (low && s.phase === "play") g.text(String(Math.ceil(s.timer / 60)), 230, 50, { size: 9, align: "right" });

        // the board: banks, lanes and homes (cached for the level)
        var top = Logic.rowY(s, s.n + 1);
        g.layer("board", s.boardVer, function (lg) {
          var lcdL = lg.kind === "lcd";
          if (top > 58) {                                 // above a short board: hedges
            for (var hx = OX; hx < OX + BW; hx += 18) lg.circle(hx + 9, top - 8, 8, 4, { a: lcdL ? 1 : 0.55, edge: false });
          }
          for (var rr = 0; rr <= s.n + 1; rr++) {
            var y = Logic.rowY(s, rr), ln = Logic.lane(s, rr);
            if (rr === 0 || (ln && ln.kind === "grass")) {
              if (!lcdL) lg.rect(OX, y, BW, CS, 4, { r: 0, a: 0.3, edge: false });
              for (var tx = OX + 6 + (rr % 2) * 9; tx < OX + BW - 4; tx += 18) {
                lg.line(tx, y + 12, tx + 2, y + 8, 4, 1, { a: lcdL ? 1 : 0.8 });
                lg.line(tx + 2, y + 8, tx + 4, y + 12, 4, 1, { a: lcdL ? 1 : 0.8 });
              }
            } else if (ln && ln.kind === "road") {
              if (!lcdL) lg.rect(OX, y, BW, CS, 8, { r: 0, a: 0.55, edge: false });
              var above = Logic.lane(s, rr + 1);
              if (above && above.kind === "road") {
                for (var dx = OX + 2; dx < OX + BW; dx += 18) lg.line(dx, y, dx + 8, y, 0, 1, { a: lcdL ? 1 : 0.45 });
              }
            } else if (ln && ln.kind === "river") {
              if (!lcdL) lg.rect(OX, y, BW, CS, 5, { r: 0, a: 0.4, edge: false });
              for (var wx = OX + 4 + (rr % 2) * 18; wx < OX + BW - 8; wx += 36) {
                lg.line(wx, y + 10, wx + 3, y + 8, 5, 1, { a: lcdL ? 1 : 0.7 });
                lg.line(wx + 3, y + 8, wx + 6, y + 10, 5, 1, { a: lcdL ? 1 : 0.7 });
              }
            } else if (rr === s.n + 1) {                 // the homes row: a hedge with five bays
              for (var c = 0; c < Logic.COLS; c++) {
                var bx = OX + c * CS;
                if (Logic.HOMES.indexOf(c) < 0) lg.rect(bx, y, CS, CS, 4, { r: 0, solid: true, edge: false });
                else if (!lcdL) lg.rect(bx + 1, y + 3, CS - 2, CS - 3, 5, { r: 3, a: 0.4 });
              }
            }
          }
          lg.line(OX, Logic.BOTTOM, OX + BW, Logic.BOTTOM, 0, 1, { a: 0.6 });
        });

        // filled homes: a hopper sitting in each
        for (i = 0; i < Logic.HOMES.length; i++) if (s.homes[i]) drawHopper(g, OX + Logic.HOMES[i] * CS + CS / 2, top + CS / 2 + 1, "down", 0.9);

        // traffic and floaters
        for (r = 1; r <= s.n; r++) {
          var lane = Logic.lane(s, r);
          if (lane.kind === "grass") continue;
          var y0 = Logic.rowY(s, r), off = lane.off - lane.v * (1 - a), rs = runsOf(lane.pattern);
          for (i = 0; i < rs.length; i++) {
            var x0 = OX + ((rs[i].start * CS + off) % lane.len + lane.len) % lane.len;
            for (var x = x0 - lane.len; x < OX + BW; x += lane.len) {
              if (x + rs[i].len * CS <= OX - 2) continue;
              drawThing(g, rs[i].ch, x, y0, rs[i].len * CS, lane.v > 0, r + i);
            }
          }
        }

        // the hopper
        var hx = s.x, hy = Logic.rowY(s, s.row) + CS / 2, lift = 0;
        if (s.phase === "play" && s.air > 0) {
          var t = Math.min(1, (Logic.HOP_T - s.air + a) / Logic.HOP_T);
          hx = s.fromX + (s.x - s.fromX) * t;
          hy = Logic.rowY(s, s.fromRow) + CS / 2 + (Logic.rowY(s, s.row) - Logic.rowY(s, s.fromRow)) * t;
          lift = info.reduce ? 0 : Math.sin(t * Math.PI) * 3;
        } else if (s.phase === "play") {
          var on = Logic.lane(s, s.row);
          if (on && on.kind === "river") hx = s.x - on.v * (1 - a);
        }
        if (s.phase === "dead" || (s.over && !s.won)) {
          drawSplat(g, s.x, hy, s.cause);
        } else if (s.phase === "play") {
          drawHopper(g, g.snap(hx), g.snap(hy - lift), s.facing, 1);
        }

        // messages
        if (info.state !== "over") {
          if (s.phase === "dead") banner(g, CAUSES[s.cause] || "OOPS!", s.lives + (s.lives === 1 ? " HOPPER LEFT" : " HOPPERS LEFT"));
          else if (s.phase === "clear") banner(g, "ALL HOME!", "LEVEL " + s.level + " NEXT");
          else if (s.levelT < 120 && s.phase === "play") {
            var d = Logic.levelDef(s, s.level);
            banner(g, "LEVEL " + s.level, s.list ? d.def.name.toUpperCase() : "HOP HOME");
          }
        }
      },
    };

    function banner(g, a, b) {
      var y = 150, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, Kit.W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: g.kind === "lcd" ? 18 : 13, align: "center", ci: ci });
      if (b) g.text(b, Kit.W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    // Cars: a rounded body with a windscreen at the front. Trucks: a long box and a separate cab.
    // Logs: long bars with rings at the ends. Shell rafts: round shells, one a cell.
    function drawThing(g, ch, x, y, w, right, n) {
      if (ch === "c") {
        var ci = CAR_COLOURS[n % CAR_COLOURS.length];
        g.rect(x + 1, y + 3, w - 2, 12, ci, { r: 3, solid: true });
        var fx = right ? x + w - 7 : x + 3;
        g.rect(fx, y + 5, 4, 8, 9, { r: 1, a: 0.85 });
        if (!g.lowres) { g.rect(x + 3, y + 1.5, 4, 2, 0, { r: 1 }); g.rect(x + w - 7, y + 1.5, 4, 2, 0, { r: 1 }); g.rect(x + 3, y + 14.5, 4, 2, 0, { r: 1 }); g.rect(x + w - 7, y + 14.5, 4, 2, 0, { r: 1 }); }
      } else if (ch === "t") {
        var cab = right ? x + w - 13 : x + 1;
        var box = right ? x + 1 : x + 14;
        g.rect(box, y + 2, w - 15, 14, 8, { r: 1, solid: true });
        g.rect(cab, y + 4, 12, 10, 2, { r: 2, solid: true });
        g.rect(right ? cab + 7 : cab + 1, y + 6, 4, 6, 9, { r: 1, a: 0.85 });
      } else if (ch === "l") {
        g.rect(x + 1, y + 3, w - 2, 12, 2, { r: 5, solid: true });
        if (!g.lowres) {
          g.circle(x + 6, y + 9, 3, 0, { a: 0.35, edge: false });
          g.line(x + 12, y + 7, x + w - 10, y + 7, 0, 1, { a: 0.3 });
          g.line(x + 16, y + 11, x + w - 6, y + 11, 0, 1, { a: 0.3 });
        } else g.rect(x + 4, y + 8, w - 8, 2, 0, { a: g.kind === "lcd" ? 0 : 0.35 });
      } else {
        for (var k = 0; k < Math.round(w / CS); k++) {
          var cx = x + k * CS + CS / 2;
          g.circle(cx, y + CS / 2, 7, 7, { solid: true });
          if (g.kind === "lcd") g.circle(cx, y + CS / 2, 3, 9, { solid: true, white: true });
          else g.circle(cx, y + CS / 2, 3, 0, { a: 0.4, edge: false });
        }
      }
    }

    function drawHopper(g, x, y, facing, scale) {
      var k = scale || 1;
      g.circle(x, y, 6.5 * k, 3, { solid: true });
      if (g.lowres && k < 1) return;
      var ex = facing === "left" ? -1.5 : facing === "right" ? 1.5 : 0, ey = facing === "down" ? 1.5 : -1.5;
      if (g.kind === "lcd") {
        g.circle(x - 2.5 + ex, y - 1.5 + ey, 1.4, 9, { solid: true, white: true });
        g.circle(x + 2.5 + ex, y - 1.5 + ey, 1.4, 9, { solid: true, white: true });
        return;
      }
      if (g.lowres) {
        g.rect(x - 3 + ex, y - 3 + ey, 2, 2, 0, { solid: true });
        g.rect(x + 1 + ex, y - 3 + ey, 2, 2, 0, { solid: true });
        return;
      }
      g.circle(x - 2.5 * k + ex, y - 1.5 * k + ey, 2 * k, 9, { edge: false });
      g.circle(x + 2.5 * k + ex, y - 1.5 * k + ey, 2 * k, 9, { edge: false });
      g.circle(x - 2.5 * k + ex * 1.3, y - 1.5 * k + ey * 1.3, 0.9 * k, 0, { edge: false });
      g.circle(x + 2.5 * k + ex * 1.3, y - 1.5 * k + ey * 1.3, 0.9 * k, 0, { edge: false });
      g.line(x - 5 * k, y + 4 * k, x - 7 * k, y + 6.5 * k, 3, 2);
      g.line(x + 5 * k, y + 4 * k, x + 7 * k, y + 6.5 * k, 3, 2);
    }

    function drawSplat(g, x, y, cause) {
      if (cause === "water" || cause === "swept") {
        g.ring(x, y, 4, 5, 2);
        g.ring(x, y, 8, 5, 1.5, { a: 0.8 });
      } else if (cause === "time") {
        g.line(x - 6, y - 6, x + 6, y + 6, 1, 3);
        g.line(x + 6, y - 6, x - 6, y + 6, 1, 3);
      } else g.star(x, y, 9, 1, { solid: true });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "hop",
    name: "Road Hop",
    modes: MODES,
    defaultMode: "classic",
    controls: "dpad",
    tap: "up",
    help: "Arrow keys (or WASD), the arrow pad or a swipe on the game hop one square; Space or a tap hops forward. Dodge the traffic, ride the logs and shell rafts across the river and hop into an empty home at the top before the time runs out.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
