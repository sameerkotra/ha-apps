/* Household Arcade — Merge: input and drawing around the rules in merge-logic.js.
   Registers itself as "merge" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.MergeLogic;

  var MODES = [{ id: "classic", label: "4 × 4" }, { id: "big", label: "5 × 5" }, { id: "small", label: "3 × 3 (hard)" },
    { id: "goals", label: "Goals" }];
  // Board layout per size: tile pitch and gap are multiples of 6 so tiles sit on the Retro LCD / Pixel grids.
  var LAYOUT = { 3: { pitch: 72, ox: 9 }, 4: { pitch: 54, ox: 9 }, 5: { pitch: 42, ox: 12 } };
  var GAP = 6, OY = 48;
  // colour by the tile's power of two (2 → 1, 4 → 2, …), repeating after the tenth
  var TILE_CI = [8, 9, 2, 1, 6, 5, 7, 4, 3, 2, 1, 6];

  function modeLabel(id) {
    for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i].label;
    return "";
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [];

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
      sounds: { move: "turn", merge: "eat", goal: "bonus", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, n = s.n, L = LAYOUT[n], ts = L.pitch - GAP, bw = GAP + n * L.pitch, i;
        var lcd = g.kind === "lcd";
        g.hud("SCORE " + fmt(s.score), s.goals ? "MAKE " + fmt(s.goal) : "LEVEL " + s.level);
        g.board(L.ox, OY, bw, bw);

        // the empty squares (cached; Retro LCD leaves them blank so outlined tiles stand out)
        g.layer("slots", n + (lcd ? "l" : "") + (s.stones ? s.stones.join("") : ""), function (lg) {
          for (var c = 0; c < n * n; c++) {
            var p = pos(c);
            if (Logic.isStone(s, c)) drawStone(lg, p[0], p[1], ts);
            else if (!lcd) lg.rect(p[0], p[1], ts, ts, 8, { r: 5, a: g.kind === "modern" ? 0.9 : 0.6, edge: false });
          }
        });

        var a = s.anim, sliding = a && s.cool > 0 && !s.over;
        if (sliding) {
          var t = Math.min(1, Math.max(0, (Logic.MOVE_GAP - s.cool + info.alpha) / Logic.MOVE_GAP));
          var e = 1 - (1 - t) * (1 - t);            // ease out
          for (i = 0; i < a.slides.length; i++) {
            var sl = a.slides[i], p0 = pos(sl.from), p1 = pos(sl.to);
            drawTile(g, p0[0] + (p1[0] - p0[0]) * e, p0[1] + (p1[1] - p0[1]) * e, ts, sl.v, 1);
          }
          return footer(g);
        }

        var since = a ? s.updates - a.end + info.alpha : 99, pop = !info.reduce && since >= 0 && since < 6;
        for (i = 0; i < s.grid.length; i++) {
          if (!s.grid[i]) continue;
          var p = pos(i), scale = 1;
          if (pop && a.merged.indexOf(i) >= 0) scale = 1 + 0.1 * (1 - since / 6);
          else if (pop && a.spawn === i) scale = 0.55 + 0.45 * since / 6;
          drawTile(g, p[0], p[1], ts, s.grid[i], scale);
        }
        footer(g);
        if (s.won && s.goals) banner("ALL GOALS MADE!", s.stats.goals + " GOALS · YOU WIN");
        else if (s.over) banner("NO MOVES LEFT", "");
        else if (s.pause > 0) {
          var next = Logic.goalFor(s, s.level + 1);
          banner("GOAL MADE!", s.level < s.goals.length ? "NEXT: MAKE " + fmt(next.goal) : "THAT WAS THE LAST ONE");
        }

        function banner(title, sub) {
          g.rect(30, 140, 180, sub ? 46 : 34, 0, { solid: true, r: 6, a: 0.92 });
          g.text(title, 120, 162, { size: 14, align: "center", ci: 9 });
          if (sub) g.text(sub, 120, 177, { size: 10, align: "center", ci: 9 });
        }
      },
    };

    function pos(c) {
      var L = LAYOUT[s.n];
      return [L.ox + GAP + (c % s.n) * L.pitch, OY + GAP + Math.floor(c / s.n) * L.pitch];
    }

    function footer(g) {
      if (s.goals) {
        var name = String(s.goalName || "").toUpperCase();
        if (name.length > 18) name = name.slice(0, 17) + "…";
        g.text("GOAL " + s.level + "/" + s.goals.length + " · " + name, 10, 292, { size: 10, a: 0.75 });
        g.text("BEST " + Kit.fmt(s.best), 230, 292, { size: 10, align: "right", a: 0.75 });
        return;
      }
      g.text("BEST TILE " + Kit.fmt(s.best), 10, 292, { size: 10, a: 0.75 });
      g.text(s.won ? "BIG TILE MADE · KEEP GOING" : modeLabel(s.mode).toUpperCase(), 230, 292, { size: 10, align: "right", a: 0.75 });
    }

    // A stone: a fixed square, drawn as an outlined block with cross-hatching (no number), so it reads as
    // a stone in every look, Retro LCD included.
    function drawStone(g, x, y, ts) {
      g.rect(x + 1, y + 1, ts - 2, ts - 2, 0, { r: 3, stroke: true });
      var step = ts / 4;
      for (var k = 1; k < 4; k++) {
        g.line(x + 3, y + k * step, x + k * step, y + 3, 0, 1.5, { a: 0.7 });
        g.line(x + k * step, y + ts - 3, x + ts - 3, y + k * step, 0, 1.5, { a: 0.7 });
      }
      g.line(x + 3, y + ts - 3, x + ts - 3, y + 3, 0, 1.5, { a: 0.7 });
    }

    // A tile: small values outlined, bigger ones filled, the biggest solid (so Retro LCD tells them
    // apart too); the number is always written on it.
    function drawTile(g, x, y, ts, v, scale) {
      var e = Logic.levelOf(v), ci = TILE_CI[(e - 1) % TILE_CI.length], filled = e >= 3;
      var sz = ts * scale, ox = x + (ts - sz) / 2, oy = y + (ts - sz) / 2;
      if (g.lowres && scale === 1) { ox = g.snap(ox); oy = g.snap(oy); }
      g.rect(ox, oy, sz, sz, ci, { r: Math.max(3, ts * 0.12), stroke: !filled, solid: e >= 7 });
      if (e >= 7 && !g.lowres) {   // the biggest tiles get an inner frame as well
        var x0 = ox + 4, y0 = oy + 4, x1 = ox + sz - 4, y1 = oy + sz - 4, fo = { a: 0.75, cap: "square" };
        g.line(x0, y0, x1, y0, 9, 1.5, fo); g.line(x0, y1, x1, y1, 9, 1.5, fo);
        g.line(x0, y0, x0, y1, 9, 1.5, fo); g.line(x1, y0, x1, y1, 9, 1.5, fo);
      }
      var digits = String(v).length;
      var size = Math.round(Math.min(ts * 0.46, 30, (ts * 1.55) / digits) * scale);
      var tci = filled && (g.kind === "lcd" || g.kind === "contrast") ? 9 : 0;
      g.text(String(v), ox + sz / 2, oy + sz / 2 + 0.5, { size: size, align: "center", base: "middle", ci: tci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "merge",
    name: "Merge",
    modes: MODES,
    defaultMode: "classic",
    controls: "dpad",
    help: "Arrow keys (or swipe the game, or the arrow pad) slide every tile that way. Two equal tiles that meet join into one worth double, and that's your score. A new tile appears after each move; the game ends when nothing can move. Goals: make the tile shown at the top to move on to the next board; hatched stones never move and stop tiles sliding.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
