/* Household Arcade — City Defense: input and drawing around the rules in defense-logic.js.
   Registers itself as "defense" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.DefenseLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "easy", label: "Easy" }, { id: "waves", label: "Waves" }];
  var W = Kit.W, GROUND = Logic.GROUND, TOP = Logic.TOP;

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [];

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
      sounds: { fire: "launch", burst: "bounce", hit: "break", flierHit: "bonus", cityLost: "lose", baseLost: "wall",
        empty: "wall", split: "turn", clear: "row", rebuild: "powerup", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      // a tap on the sky sends an interceptor there
      pointer: function (kind, lx, ly) {
        if (kind !== "down" || ly < TOP || ly > Kit.H) return;
        var evs = Logic.aim(s, lx, ly);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, lcd = g.kind === "lcd", i;
        g.hud("SCORE " + fmt(s.score), s.waves ? "WAVE " + s.wave + "/" + s.waves.length : "WAVE " + s.wave);

        // the ground and the cities (destroyed: rubble)
        g.rect(0, GROUND, W, Kit.H - GROUND, 2, { r: 0, a: lcd ? 1 : 0.6, solid: g.kind === "modern" || g.kind === "pixel" || g.kind === "contrast" || lcd, edge: false });
        g.line(0, GROUND, W, GROUND, 0, 1.5, { a: 0.8 });
        for (i = 0; i < Logic.CITIES.length; i++) drawCity(g, Logic.CITIES[i], s.cities[i]);
        for (i = 0; i < Logic.BASES.length; i++) drawBase(g, Logic.BASES[i], s.bases[i]);

        // missile trails and heads, fliers
        for (i = 0; i < s.enemies.length; i++) {
          var m = s.enemies[i];
          g.line(m.x0, m.y0, m.x, m.y, 1, 1.2, { a: lcd ? 1 : 0.7 });
          g.rect(m.x - 1.5, m.y - 1.5, 3, 3, 1, { r: 0, solid: true });
        }
        for (i = 0; i < s.fliers.length; i++) drawFlier(g, s.fliers[i]);
        // interceptors: a trail from the base and an X where they will burst
        for (i = 0; i < s.shots.length; i++) {
          var sh = s.shots[i];
          g.line(sh.x0, sh.y0, sh.x, sh.y, 7, 1.2, { a: lcd ? 1 : 0.8 });
          g.line(sh.tx - 3, sh.ty - 3, sh.tx + 3, sh.ty + 3, 7, 1);
          g.line(sh.tx + 3, sh.ty - 3, sh.tx - 3, sh.ty + 3, 7, 1);
        }
        // clouds
        for (i = 0; i < s.booms.length; i++) {
          var b = s.booms[i];
          if (b.r <= 0.5) continue;
          if (lcd) g.ring(b.x, b.y, b.r, 0, b.small ? 1.5 : 2);
          else {
            g.circle(b.x, b.y, b.r, b.hurt ? 1 : b.small ? 2 : 3, { a: 0.65, edge: false });
            if (!b.small) g.ring(b.x, b.y, b.r, 9, 1, { a: 0.6 });
          }
        }
        // the crosshair (keys and controller)
        var c = s.cross;
        if (info.state === "running" && s.phase === "wave") {
          g.ring(c.x, c.y, 5, 0, 1, { a: 0.8 });
          g.line(c.x - 9, c.y, c.x - 5, c.y, 0, 1, { a: 0.8 }); g.line(c.x + 5, c.y, c.x + 9, c.y, 0, 1, { a: 0.8 });
          g.line(c.x, c.y - 9, c.x, c.y - 5, 0, 1, { a: 0.8 }); g.line(c.x, c.y + 5, c.x, c.y + 9, 0, 1, { a: 0.8 });
        }

        if (info.state !== "over") {
          if (s.phase === "bonus" && s.bonus) {
            var shown = Logic.BONUS_T - s.t;
            banner(g, "WAVE " + s.wave + " CLEAR", shown > 30 ? s.bonus.cities + " × 100 + " + s.bonus.ammo + " × 5 = " + fmt(s.bonus.points) : "");
          } else if (s.phase === "end") banner(g, "THE LAST CITY FELL", "");
          else if (s.clock < Logic.START_T + 40) banner(g, s.waves ? s.name.toUpperCase() : "WAVE " + s.wave, s.waves ? "WAVE " + s.wave + "/" + s.waves.length : "DEFEND THE CITIES");
        }
      },
    };

    function drawCity(g, x, alive) {
      if (!alive) {
        g.rect(x - 9, GROUND - 3, 18, 3, 8, { r: 0, solid: true, edge: false });
        g.rect(x - 4, GROUND - 5, 5, 2, 8, { r: 0, solid: true, edge: false });
        return;
      }
      var o = { r: 0, solid: g.kind !== "lcd", edge: false };
      g.rect(x - 9, GROUND - 8, 6, 8, 5, o);
      g.rect(x - 3, GROUND - 15, 6, 15, 7, o);
      g.rect(x + 3, GROUND - 11, 6, 11, 5, o);
      if (!g.lowres) { g.rect(x - 1.5, GROUND - 12, 1.5, 1.5, 3, { r: 0, edge: false }); g.rect(x + 0.5, GROUND - 8, 1.5, 1.5, 3, { r: 0, edge: false }); g.rect(x + 5, GROUND - 8, 1.5, 1.5, 3, { r: 0, edge: false }); }
    }
    function drawBase(g, x, b) {
      g.poly([[x - 12, GROUND], [x - 6, GROUND - 10], [x + 6, GROUND - 10], [x + 12, GROUND]], b.alive ? 4 : 8, { stroke: true, solid: g.kind !== "lcd" });
      if (!b.alive) return;
      // ammo left: little marks in rows of five, under the base
      for (var k = 0; k < b.ammo; k++) {
        var px = x - 8 + (k % 5) * 4, py = GROUND + 5 + Math.floor(k / 5) * 6;
        g.line(px, py, px, py + 3, g.kind === "lcd" ? 9 : 7, 1.5);
      }
    }
    function drawFlier(g, f) {
      var d = f.vx > 0 ? 1 : -1;
      g.poly([[f.x + 7 * d, f.y], [f.x - 6 * d, f.y - 3], [f.x - 6 * d, f.y + 3]], 6, { solid: true, stroke: true });
      g.line(f.x - 1 * d, f.y, f.x - 4 * d, f.y - 6, 6, 1.5);
      g.line(f.x - 1 * d, f.y, f.x - 4 * d, f.y + 6, 6, 1.5);
    }
    function banner(g, a, b) {
      var y = 130, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(20, y - 18, 200, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "defense",
    name: "City Defense",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    help: "Tap the sky where an interceptor should burst — the nearest base with ammo fires it. With keys or a controller, the arrows move the crosshair and Space fires. Stop the falling missiles (some split in three) and the fliers before they reach your six cities.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
