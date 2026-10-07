/* Household Arcade — Gem Swap: input and drawing around the rules in gems-logic.js.
   Registers itself as "gems" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.GemsLogic;

  var MODES = [{ id: "timed", label: "Timed (90 s)" }, { id: "moves", label: "Moves (levels)" }, { id: "zen", label: "Zen (no clock)" }];
  var N = Logic.N, CELL = Logic.CELL, X0 = Logic.X0, Y0 = Logic.Y0, W = Kit.W;
  var COLOURS = [1, 2, 3, 4, 5, 6];

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], drag = null, keys = false;

    function push(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; },
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
      sounds: { swap: "turn", nope: "wall", match: "eat", cascade: "bonus", special: "powerup", shuffle: "speed",
        hurry: "speed", clear: "row", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { keys = true; push(Logic.press(s, action, down)); },
      // tap a gem, then a neighbour; or drag a gem toward the one it should swap with
      pointer: function (kind, lx, ly) {
        if (kind === "down") {
          keys = false;
          var i = Logic.cellAt(lx, ly);
          drag = i >= 0 ? { i: i, x: lx, y: ly, done: false } : null;
          if (i >= 0) push(Logic.tap(s, i));
        } else if (kind === "move" && drag && !drag.done) {
          var dx = lx - drag.x, dy = ly - drag.y;
          if (Math.max(Math.abs(dx), Math.abs(dy)) >= 12) {
            drag.done = true;
            var dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
            push(Logic.drag(s, drag.i, dir));
          }
        } else if (kind === "up") drag = null;
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, lcd = g.kind === "lcd", a = info.state === "running" ? info.alpha : 0, i;
        var l = Logic.level(s);
        g.hud("SCORE " + fmt(s.score), s.list ? "LEVEL " + s.level + "/" + s.list.length : s.mode === "zen" ? "ZEN" : "TIME " + Math.ceil(s.time / 60));

        // the second line: the clock, or the level's moves, points and locks
        if (s.mode === "timed") {
          var frac = Math.max(0, s.time / Logic.TIMED);
          g.rect(10, 41, 220, 7, 8, { r: 2, a: lcd ? 1 : 0.6 });
          if (frac > 0) g.rect(10, 41, Math.max(2, 220 * frac), 7, s.time < 600 ? 1 : 4, { r: 2, solid: true });
        } else if (l) {
          g.text("MOVES " + s.moves, 10, 50, { size: 10, ci: s.moves <= 3 ? 1 : 0 });
          var pf = Math.min(1, s.levelScore / s.goal);
          g.rect(84, 42, 90, 7, 8, { r: 2, a: lcd ? 1 : 0.6 });
          if (pf > 0) g.rect(84, 42, Math.max(2, 90 * pf), 7, pf >= 1 ? 4 : 3, { r: 2, solid: true });
          g.text(s.locksLeft > 0 ? "LOCKS " + s.locksLeft : "GOAL " + fmt(s.goal), 230, 50, { size: 10, align: "right" });
        } else if (s.lastGain > 0 && s.phase !== "idle") g.text("+" + fmt(s.lastGain), W / 2, 50, { size: 10, align: "center" });

        // the board's squares (cached)
        g.layer("gems-board", 1, function (lg) {
          lg.board(X0, Y0, N * CELL, N * CELL);
          if (lg.kind === "lcd") return;
          for (var r = 0; r < N; r++) for (var c = 0; c < N; c++) if ((r + c) % 2 === 0) lg.rect(X0 + c * CELL, Y0 + r * CELL, CELL, CELL, 8, { r: 0, a: 0.35, edge: false });
        });

        // where each gem is drawn: swapping, falling, going
        var swapA = -1, swapB = -1, sp = 0;
        if ((s.phase === "swap" || s.phase === "back") && s.swap) {
          swapA = s.swap[0]; swapB = s.swap[1];
          sp = Math.min(1, (Logic.SWAP_T - s.t + a) / Logic.SWAP_T);
          if (s.phase === "back") sp = 1 - sp;
        }
        var dying = {};
        for (i = 0; i < s.dying.length; i++) dying[s.dying[i]] = true;
        var shrink = s.phase === "clear" ? Math.max(0, (s.t - a) / Logic.CLEAR_T) : 1;
        var ctx = g.ctx;
        ctx.save(); ctx.beginPath(); ctx.rect(X0, Y0, N * CELL, N * CELL); ctx.clip();
        for (i = 0; i < N * N; i++) {
          var gm = s.board[i];
          if (!gm) continue;
          var r0 = Math.floor(i / N), c0 = i % N, cx = X0 + c0 * CELL + CELL / 2, cy = Y0 + r0 * CELL + CELL / 2;
          if (i === swapA || i === swapB) {
            var o = i === swapA ? swapB : swapA;
            cx += ((o % N) - c0) * CELL * sp; cy += (Math.floor(o / N) - r0) * CELL * sp;
          }
          if (gm.fy > 0) cy -= Math.max(0, gm.fy - (s.phase === "fall" ? Logic.FALL_V * a : 0));
          var k = dying[i] ? shrink : 1;
          if (k <= 0.05) continue;
          drawGem(g, g.snap(cx), g.snap(cy), gm, k);
          if (dying[i] && !info.reduce && !lcd) g.ring(cx, cy, 13 * (1.4 - shrink * 0.4), 9, 1.2, { a: shrink });
        }
        ctx.restore();

        // picked gem, keyboard ring, hint
        if (s.sel >= 0) ringAt(g, s.sel, 7, 2.5);
        if (keys && s.sel < 0 && info.state === "running") ringAt(g, s.cur, 0, 1.5, true);
        if (s.hint) {
          var pulse = info.reduce ? 1 : 0.6 + 0.4 * Math.abs(Math.sin(info.t * Math.PI));
          ringAt(g, s.hint.a, 3, 2, false, pulse); ringAt(g, s.hint.b, 3, 2, false, pulse);
        }

        if (info.state !== "over") {
          if (s.phase === "shuffle") banner(g, "NO MOVES LEFT", "SHUFFLING");
          else if (s.phase === "level") banner(g, "LEVEL CLEAR!", s.lastGain > 0 ? "+" + fmt(s.lastGain) + " FOR MOVES LEFT" : "");
          else if (s.mode === "timed" && s.time <= 0) banner(g, "TIME!", "");
          else if (l && s.stats.swaps === 0 && s.levelScore === 0 && s.phase === "idle") {
            banner(g, l.name.toUpperCase(), fmt(s.goal) + " POINTS IN " + s.moves + " MOVES" + (s.locksLeft ? ", BREAK THE LOCKS" : ""));
          } else if (s.chain > 1 && s.phase === "clear") g.text("CASCADE ×" + Math.min(s.chain, Logic.MAX_CHAIN), W / 2, Y0 + N * CELL + 14, { size: 11, align: "center", ci: 3 });
        }
      },
    };

    function ringAt(g, i, ci, w, dashed, alpha) {
      var x = X0 + (i % N) * CELL, y = Y0 + Math.floor(i / N) * CELL;
      if (dashed && !g.lowres) {
        var o = { a: alpha == null ? 0.9 : alpha, dash: [3, 3] };
        g.line(x + 1, y + 1, x + CELL - 1, y + 1, ci, w, o); g.line(x + 1, y + CELL - 1, x + CELL - 1, y + CELL - 1, ci, w, o);
        g.line(x + 1, y + 1, x + 1, y + CELL - 1, ci, w, o); g.line(x + CELL - 1, y + 1, x + CELL - 1, y + CELL - 1, ci, w, o);
        return;
      }
      g.curve(function (c) { c.rect(x + 1.5, y + 1.5, CELL - 3, CELL - 3); }, ci, w, { a: alpha == null ? 1 : alpha });
    }

    function drawGem(g, x, y, gm, k) {
      var R = 10 * k, lcd = g.kind === "lcd", o = { solid: true, stroke: true };
      if (gm.sp === Logic.STAR || gm.k < 0) {          // a star (or one going off)
        g.star(x, y, 12 * k, lcd ? 0 : 9, { solid: true, stroke: true });
        g.circle(x, y, 3.5 * k, lcd ? 9 : 7, { solid: true, white: true });
      } else {
        var ci = COLOURS[gm.k % COLOURS.length];
        switch (gm.k) {
          case 0: g.circle(x, y, R, ci, o); break;
          case 1: g.rect(x - R * 0.85, y - R * 0.85, R * 1.7, R * 1.7, ci, { solid: true, stroke: true, r: 3 * k }); break;
          case 2: g.poly([[x, y - R * 1.1], [x + R, y], [x, y + R * 1.1], [x - R, y]], ci, o); break;
          case 3: g.poly([[x, y - R * 1.05], [x + R * 1.05, y + R * 0.85], [x - R * 1.05, y + R * 0.85]], ci, o); break;
          case 4: g.poly([[x - R * 0.5, y - R * 0.9], [x + R * 0.5, y - R * 0.9], [x + R, y], [x + R * 0.5, y + R * 0.9], [x - R * 0.5, y + R * 0.9], [x - R, y]], ci, o); break;
          default: {
            var t = R * 0.38;
            g.poly([[x - t, y - R], [x + t, y - R], [x + t, y - t], [x + R, y - t], [x + R, y + t], [x + t, y + t], [x + t, y + R],
              [x - t, y + R], [x - t, y + t], [x - R, y + t], [x - R, y - t], [x - t, y - t]], ci, o);
          }
        }
        if (!g.lowres && k > 0.6 && g.kind !== "contrast") g.circle(x - R * 0.35, y - R * 0.4, R * 0.18, g.dark ? 0 : 9, { a: 0.6, edge: false });
        if (gm.sp === Logic.LINE_H || gm.sp === Logic.LINE_V) {
          var hz = gm.sp === Logic.LINE_H;
          if (g.lowres) {
            for (var d = -1; d <= 1; d++) g.circle(x + (hz ? d * 5 : 0), y + (hz ? 0 : d * 5), 1.2, lcd ? 9 : 9, { solid: true, white: true });
          } else {
            for (var s2 = -1; s2 <= 1; s2 += 2) {
              if (hz) g.line(x - R, y + s2 * 2.5, x + R, y + s2 * 2.5, 9, 1.6, { a: 0.95 });
              else g.line(x + s2 * 2.5, y - R, x + s2 * 2.5, y + R, 9, 1.6, { a: 0.95 });
            }
          }
        } else if (gm.sp === Logic.BLAST) {
          g.ring(x, y, 12.5 * k, 0, 1.6);
          g.circle(x, y, 2.5 * k, 9, { solid: true, white: true });
        }
      }
      if (gm.lock) {
        g.curve(function (c) { c.rect(x - 12, y - 12, 24, 24); }, 0, 2, { a: 0.85 });
        g.line(x - 12, y - 12, x + 12, y + 12, 0, 1.2, { a: 0.5 });
        g.line(x + 12, y - 12, x - 12, y + 12, 0, 1.2, { a: 0.5 });
        g.rect(x + 5, y + 5, 7, 6, 8, { r: 1, solid: true, stroke: true });
      }
    }
    function banner(g, a, b) {
      var y = 160, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(14, y - 18, 212, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, W / 2, y + 15, { size: 9, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "gems",
    name: "Gem Swap",
    modes: MODES,
    defaultMode: "timed",
    controls: "touch",
    buttons: [{ action: "alt", label: "Hint", aria: "Show a move" }],
    help: "Swap two neighbouring gems to line up three or more of a kind: tap one and then its neighbour, or drag it that way. With keys or a controller, the arrows move the ring, Space picks a gem up and an arrow swaps it; C (or Hint) shows a move. Four in a line makes a line gem, an L or T a blast gem, five a star.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
