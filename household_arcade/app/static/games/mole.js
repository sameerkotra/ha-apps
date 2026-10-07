/* Household Arcade — Tap the Mole: input and drawing around the rules in mole-logic.js.
   Registers itself as "mole" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.MoleLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "big", label: "Big garden" }, { id: "rush", label: "60 seconds" },
    { id: "gardens", label: "Gardens" }];
  var POP_SHOW = 36;   // updates a "+10" stays on screen

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], showCur = false, pops = [];

    function queue(evs) {
      for (var i = 0; i < evs.length; i++) {
        var e = evs[i];
        pending.push(e);
        if (e.type === "bonk" || e.type === "gold" || e.type === "hog") {
          var b = Logic.cellBox(s, e.hole);
          var txt = e.type === "hog" ? (Logic.MODES[s.mode].lives ? "OUCH!" : "-5 S") : "+" + e.points;
          pops.push({ x: b.x + b.w / 2, y: b.y + 10, text: txt, t: 0 });
        }
      }
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; pops = []; showCur = false; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; pops = []; },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        for (var i = pops.length - 1; i >= 0; i--) if (++pops[i].t > POP_SHOW) pops.splice(i, 1);
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { bonk: "hit", gold: "bonus", hog: "wall", miss: "turn", life: "lose", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        if (down && action !== "fire") showCur = true;
        queue(Logic.press(s, action, down));
      },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = Logic.holeAt(s, lx, ly);
        if (i >= 0) queue(Logic.bonk(s, i));
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, i, m = Logic.MODES[s.mode];
        var gd = s.gardens ? Logic.garden(s, s.level) : null;
        g.hud("SCORE " + fmt(s.score), gd ? "GARDEN " + s.level + "/" + s.gardens.length : "LEVEL " + s.level);
        var cs = Logic.cellSize(s), n = s.holes.length, o = Logic.origin(s);
        var spanX = cs * s.cols, spanY = cs * s.rows;
        var light = (g.kind === "modern" && !g.dark) || g.kind === "paper" || g.kind === "lcd";

        // the garden: a cached layer with grass tufts (one per garden shape in Gardens)
        var shape = s.cols + "x" + s.rows + ":" + s.holes.map(function (h) { return h.grass ? "." : "o"; }).join("");
        g.layer("garden", shape, function (lg) {
          lg.rect(o.x - 6, o.y - 12, spanX + 12, spanY + 18, 4, { a: lg.kind === "lcd" ? 0.3 : 0.18, r: 8, edge: false });
          for (var t = 0; t < 14; t++) {
            var tx = o.x + ((t * 53) % spanX), ty = o.y + ((t * 37) % spanY) + 4;
            lg.line(tx, ty, tx - 2, ty - 5, 4, 1.5, { a: 0.6 });
            lg.line(tx + 3, ty, tx + 4, ty - 6, 4, 1.5, { a: 0.6 });
          }
          // grass cells (no hole): a little flower, so they read as "not a hole" by shape
          for (var j = 0; j < n; j++) {
            if (!s.holes[j].grass) continue;
            var b = Logic.cellBox(s, j), fx = b.x + b.w / 2, fy = b.y + b.h * 0.62, fr = Math.max(3, cs * 0.07);
            lg.line(fx, fy + fr, fx, fy + cs * 0.2, 4, 1.5, { a: 0.8 });
            for (var pk = 0; pk < 4; pk++) lg.circle(fx + Math.cos(pk * Math.PI / 2) * fr, fy + Math.sin(pk * Math.PI / 2) * fr, fr * 0.8, 6, { a: 0.8 });
            lg.circle(fx, fy, fr * 0.6, 3, { solid: true });
          }
        });

        for (i = 0; i < n; i++) if (!s.holes[i].grass) drawHole(g, i, cs, light, info);

        // a garden's name for its first two seconds
        if (gd && s.gt < 120 && info.state !== "over")
          g.text(gd.name.toUpperCase(), Kit.W / 2, 46, { size: 11, align: "center", a: 0.85 });

        // the keyboard cursor: a ring round the cell
        if (showCur && info.state !== "over") {
          var cb = Logic.cellBox(s, s.cur);
          g.curve(function (c) {
            if (c.roundRect) c.roundRect(cb.x + 2, cb.y + 2, cb.w - 4, cb.h - 4, 8); else c.rect(cb.x + 2, cb.y + 2, cb.w - 4, cb.h - 4);
          }, 5, 3);
        }

        // "+10" and friends, rising (still with reduce motion)
        for (i = 0; i < pops.length; i++) {
          var p = pops[i], up = info.reduce ? 0 : p.t * 0.4;
          g.text(p.text, p.x, g.snap(p.y - up), { size: 12, align: "center", ci: p.text.charAt(0) === "+" ? 0 : 1 });
        }

        // lives or the clock at the bottom
        if (m.lives) {
          var hearts = "";
          for (i = 0; i < m.lives; i++) hearts += i < s.lives ? "♥" : "·";
          g.text(hearts.split("").join(" "), 12, 292, { size: 14, ci: 1 });
          if (gd) g.text(Math.max(0, gd.bonks - s.gbonks) + " TO GO", 228, 292, { size: 10, align: "right" });
          else g.text(MODES[s.mode === "big" ? 1 : 0].label.toUpperCase(), 228, 292, { size: 9, align: "right", a: 0.7 });
        } else {
          var secs = Math.ceil(s.clock / 60), frac = s.clock / m.timer;
          g.rect(12, 282, 180, 8, 8, { r: 3 });
          g.rect(12, 282, Math.max(0, 180 * frac), 8, secs <= 10 ? 1 : 5, { r: 3, solid: true });
          g.text(secs + " S", 228, 291, { size: 11, align: "right" });
        }
      },
    };

    function drawHole(g, i, cs, light, info) {
      var b = Logic.cellBox(s, i), h = s.holes[i];
      var cx = b.x + b.w / 2, hy = b.y + b.h * 0.74, rx = cs * 0.38, ry = cs * 0.13;
      // the mound and the hole
      g.ellipse(cx, hy + ry * 0.5, rx + 5, ry + 4, 2, { a: 0.55, edge: false });
      g.ellipse(cx, hy, rx, ry, light ? 0 : 8, { a: 0.9, solid: true });
      var ht = Logic.height(h);
      if (!h.k || ht <= 0) return;
      var r = cs * 0.24, top = hy - ht * r * 2.6;
      var ctx = g.ctx;
      ctx.save();
      ctx.beginPath(); ctx.rect(b.x, b.y - 10, b.w, hy - b.y + 10); ctx.clip();
      if (h.k === "hog") drawHog(g, cx, top, r, !!h.hit, info);
      else drawMole(g, cx, top, r, h.k === "gold", !!h.hit, info);
      ctx.restore();
      // the front lip of the hole over the critter's base
      g.line(cx - rx, hy, cx + rx, hy, 2, 3, { a: 0.9 });
    }

    // A round friendly mole: head and body in one, big nose, two teeth. Golden ones wear a crown.
    function drawMole(g, cx, top, r, gold, hit, info) {
      var ci = gold ? 3 : 2;
      g.rect(cx - r, top + r, r * 2, r * 2.2, ci, { r: 0, solid: true, edge: false });
      g.circle(cx, top + r, r, ci, { solid: true });
      if (gold) g.poly([[cx - r * 0.7, top + 2], [cx - r * 0.7, top - r * 0.5], [cx - r * 0.35, top - r * 0.1], [cx, top - r * 0.6],
        [cx + r * 0.35, top - r * 0.1], [cx + r * 0.7, top - r * 0.5], [cx + r * 0.7, top + 2]], 3, { solid: true });
      var ey = top + r * 0.85, ex = r * 0.38;
      if (hit) {
        // dazed: crossed eyes and a small star above
        for (var k = -1; k <= 1; k += 2) {
          g.line(cx + k * ex - 3, ey - 3, cx + k * ex + 3, ey + 3, 0, 1.6);
          g.line(cx + k * ex - 3, ey + 3, cx + k * ex + 3, ey - 3, 0, 1.6);
        }
        var sa = info.reduce ? 0 : info.t * 3;
        g.star(cx + Math.cos(sa) * r * 0.8, top - 4 + Math.sin(sa) * 2, 5, 3, { solid: true });
      } else {
        var lcd = g.kind === "lcd";
        g.circle(cx - ex, ey, Math.max(2, r * 0.14), 0, { solid: true, edge: false, white: lcd });
        g.circle(cx + ex, ey, Math.max(2, r * 0.14), 0, { solid: true, edge: false, white: lcd });
      }
      // nose and teeth
      g.ellipse(cx, top + r * 1.3, r * 0.3, r * 0.22, 1, { solid: true });
      g.rect(cx - 4, top + r * 1.55, 8, 5, 9, { r: 1 });
      if (!g.lowres) g.line(cx, top + r * 1.55, cx, top + r * 1.55 + 5, 0, 1, { a: 0.6 });
    }

    // A hedgehog: a wide spiky dome with a pointed snout — a different shape from the round moles.
    function drawHog(g, cx, top, r, hit, info) {
      var pts = [], R = r * 1.35, base = top + R + 6, n = 13;
      for (var k = 0; k <= n; k++) {
        var an = Math.PI + (k / n) * Math.PI, rad = k % 2 ? R : R * 0.72;
        pts.push([cx + Math.cos(an) * rad, base + Math.sin(an) * rad]);
      }
      pts.push([cx + R, base + r * 2], [cx - R, base + r * 2]);
      g.poly(pts, 8, { a: 0.95 });
      // the face: a pale snout pointing right with a dark nose tip, a frown when tapped
      g.poly([[cx - r * 0.2, base - r * 0.7], [cx + r * 1.15, base - r * 0.15], [cx - r * 0.2, base + r * 0.3]], 3, {});
      g.circle(cx + r * 1.1, base - r * 0.15, Math.max(2, r * 0.16), 0, { solid: true, edge: false });
      g.circle(cx + r * 0.25, base - r * 0.35, Math.max(1.5, r * 0.1), 0, { solid: true, edge: false });
      g.text("!", cx - r * 0.75, base - r * 0.1, { size: 14, align: "center", ci: hit ? 1 : 0 });
      if (hit && !info.reduce) g.ring(cx, base - r * 0.3, R + 4, 1, 2, { a: 0.8 });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "mole",
    name: "Tap the Mole",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    help: "Tap a mole while it's up to bonk it (golden ones score more). Don't tap the spiky hedgehogs! In Gardens, bonk enough moles to clear each garden. Keyboard: arrows move the ring, Space bonks.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
