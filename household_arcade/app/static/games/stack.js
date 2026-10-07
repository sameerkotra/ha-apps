/* Household Arcade — Tower Stack: input and drawing around the rules in stack-logic.js.
   Registers itself as "stack" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.StackLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "fast", label: "Fast" }, { id: "easy", label: "Easy (3 tries)" }, { id: "towers", label: "Towers" }];
  var BH = Logic.BH, GROUND = 282, VIEW_TOP = 96;
  var COLS = [1, 2, 3, 4, 7, 5, 6];

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prevX = 0, cam = 0, camFor = -1;

    function camTarget() { return Math.max(0, (s.height + 2) * BH - (GROUND - VIEW_TOP)); }
    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; prevX = s.block ? s.block.x : 0; cam = camTarget(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; prevX = s.block ? s.block.x : 0; cam = camTarget(); },
      step: function () {
        var b0 = s.block;
        prevX = b0 ? b0.x : 0;
        var evs = pending.concat(Logic.step(s));
        pending = [];
        if (s.block !== b0 && s.block) prevX = s.block.x;
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { place: "bounce", perfect: "eat", miss: "lose", tower: "row", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      pointer: function (kind) { if (kind === "down") impl.input("fire", true); },
      draw: function (g, info) {
        var fmt = Kit.fmt, lcd = g.kind === "lcd", i;
        var t = Logic.tower(s);
        g.hud("SCORE " + fmt(s.score), s.towers ? "TOWER " + s.level + "/" + s.towers.length : "FLOOR " + s.height);

        // the camera follows the top of the tower (eased; at once with reduce motion or a new tower)
        var want = camTarget();
        if (camFor !== s.level || info.reduce || Math.abs(want - cam) > 200) cam = want; else cam += (want - cam) * 0.15;
        camFor = s.level;
        var sy = function (wy) { return GROUND - (wy - cam); };

        var ctx = g.ctx;
        ctx.save();
        ctx.beginPath(); ctx.rect(0, 36, Kit.W, Kit.H - 36); ctx.clip();
        // far city skyline (cached), sinking slowly as the tower grows; the ground under the first floor
        var sink = Math.min(120, cam * 0.3);
        if (!lcd && sink < 110) {
          ctx.save(); ctx.translate(0, sink);
          g.layer("stack-sky", 1, function (lg) {
            var xs = [0, 22, 38, 64, 82, 110, 128, 150, 176, 196, 220];
            var hs = [40, 64, 30, 76, 46, 58, 34, 70, 44, 60, 36];
            for (var k = 0; k < xs.length; k++) lg.rect(xs[k], GROUND - hs[k], 20, hs[k], 8, { r: 0, a: 0.45, edge: false });
          });
          ctx.restore();
        }
        var gy = sy(0);
        if (gy < Kit.H) g.rect(0, gy, Kit.W, Kit.H - gy, 4, { r: 0, a: lcd ? 1 : 0.7, solid: g.kind === "modern" || g.kind === "pixel" || g.kind === "contrast" || lcd });

        // the tower: the floors kept (the lowest may be off screen)
        var first = s.height - (s.floors.length - 1);
        for (i = 0; i < s.floors.length; i++) {
          var f = s.floors[i], wy = (first + i) * BH, y = sy(wy + BH);
          if (y > Kit.H || y + BH < 36) continue;
          drawBlock(g, f.x, y, f.w, i === 0 && first === 0 ? 8 : COLS[f.c % 7]);
        }
        // pieces falling away
        for (i = 0; i < s.chunks.length; i++) {
          var ch = s.chunks[i];
          drawBlock(g, ch.x, sy((ch.h + 1) * BH) + ch.y, ch.w, COLS[ch.c % 7], 0.75);
        }
        // the moving block, with a guide line down to the tower
        if (s.block && s.phase === "play") {
          var b = s.block, x = info.state === "running" ? prevX + (b.x - prevX) * info.alpha : b.x;
          var by = sy((s.height + 2) * BH);
          drawBlock(g, g.snap(x), by, b.w, COLS[b.c % 7]);
          if (!g.lowres) {
            g.line(x, by + BH, x, sy((s.height + 1) * BH), 0, 1, { a: 0.18, dash: [2, 3] });
            g.line(x + b.w, by + BH, x + b.w, sy((s.height + 1) * BH), 0, 1, { a: 0.18, dash: [2, 3] });
          }
        }
        ctx.restore();

        // lives (Easy, Towers), the tower's progress, perfect streak
        if (Logic.MODES[s.mode].lives > 1) for (i = 0; i < s.lives; i++) g.rect(10 + i * 14, 42, 10, 6, 4, { r: 1, solid: true });
        if (s.towers) {
          var frac = Math.min(1, s.height / t.floors);
          g.rect(60, 42, 170, 6, 8, { r: 2, a: lcd ? 1 : 0.6 });
          if (frac > 0) g.rect(60, 42, Math.max(2, 170 * frac), 6, 4, { r: 2, solid: true });
        }
        if (s.perfectT > 0 && s.streak > 0) {
          g.text(s.streak > 1 ? "PERFECT ×" + s.streak : "PERFECT", Kit.W / 2, 70, { size: 13, align: "center", ci: 3, a: lcd ? 1 : Math.min(1, s.perfectT / 20) });
        }
        if (info.state !== "over") {
          if (s.phase === "clear") banner(g, "TOWER BUILT!", s.towers ? "TOWER " + (s.level + 1) + " NEXT" : "");
          else if (s.phase === "miss") banner(g, "MISSED!", s.lives > 0 ? s.lives + (s.lives === 1 ? " LIFE LEFT" : " LIVES LEFT") : "");
          else if (s.towers && s.height === 0) banner(g, t.name.toUpperCase(), t.floors + " FLOORS");
          else if (!s.towers && s.height === 0) g.text("TAP TO DROP", Kit.W / 2, 70, { size: 12, align: "center", a: 0.8 });
        }
      },
    };

    function drawBlock(g, x, y, w, ci, a) {
      if (w <= 0) return;
      g.rect(x, y, w, BH, ci, { r: 2, solid: g.kind !== "lcd", bevel: true, a: a == null ? 1 : a, stroke: true });
      if (!g.lowres && w > 8) g.line(x + 2, y + 3, x + w - 2, y + 3, 9, 1, { a: 0.35 });
    }
    function banner(g, a, b) {
      var y = 150, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, Kit.W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, Kit.W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "stack",
    name: "Tower Stack",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    buttons: [{ action: "fire", label: "Drop", aria: "Drop the block", wide: true }],
    help: "Space, ↑ or a tap on the game drops the sliding block. Whatever hangs over the edge is cut off, so line it up: a perfect drop keeps the full width, and a run of them grows the block back. Missing the tower ends the game (Easy and Towers: 3 tries). Easy is wide and slow — good for little ones.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
