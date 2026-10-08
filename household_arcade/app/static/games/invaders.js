/* Household Arcade — Sky Defenders: input and drawing around the rules in invaders-logic.js.
   Registers itself as "invaders" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.InvadersLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "easy", label: "Easy" }, { id: "waves", label: "Waves" }];
  var TAP_UPDATES = 20, DRAG_START = 6;
  var COLOURS = { a: 6, b: 7, c: 4 };

  // The critters on the coarse grids (Retro LCD, Pixel): 6 × 5 dots of 2 logical px, two frames each.
  var SPRITES = {
    a: [["..##..", ".####.", "##..##", ".####.", "#....#"], ["..##..", ".####.", "##..##", ".####.", ".#..#."]],
    b: [["#....#", ".####.", "#.##.#", "######", ".#..#."], [".#..#.", ".####.", "#.##.#", "######", "#....#"]],
    c: [[".####.", "#.##.#", "######", "######", "#.#.#."], [".####.", "#.##.#", "######", "######", ".#.#.#"]],
  };

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], touch = null;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; touch = null; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; touch = null; },
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
      sounds: { fire: "turn", hit: "break", bonus: "bonus", ship: "powerup", zap: "bounce", chip: "wall",
        lifeLost: "lose", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        // drag on the game: the cannon follows the finger; a tap fires
        if (kind === "down") { touch = { x: lx, t: s.updates, moved: false }; return; }
        if (!touch) return;
        if (kind === "move") {
          if (!touch.moved && Math.abs(lx - touch.x) > DRAG_START) touch.moved = true;
          if (touch.moved) Logic.aim(s, lx);
          return;
        }
        if (kind === "up") {
          if (!touch.moved && s.updates - touch.t <= TAP_UPDATES) { queue(Logic.press(s, "fire", true)); queue(Logic.press(s, "fire", false)); }
          touch = null;
        }
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, i;
        var total = Logic.waveCount(s);
        g.hud("SCORE " + fmt(s.score), s.waves ? "WAVE " + s.level + "/" + total : "WAVE " + s.level);

        // the shields: cached, redrawn only when a block wears away
        g.layer("shields", s.shieldVer, function (lg) {
          for (var k = 0; k < s.shields.length; k++) {
            var sh = s.shields[k];
            for (var r = 0; r < Logic.SHIELD_ROWS; r++) {
              var run = -1;
              for (var c = 0; c <= Logic.SHIELD_COLS; c++) {
                var on = c < Logic.SHIELD_COLS && sh.blocks[r * Logic.SHIELD_COLS + c];
                if (on && run < 0) run = c;
                if (!on && run >= 0) {
                  lg.rect(sh.x + run * Logic.BLOCK, Logic.SHIELD_Y + r * Logic.BLOCK, (c - run) * Logic.BLOCK, Logic.BLOCK, 4, { r: 0, edge: false, solid: true });
                  run = -1;
                }
              }
            }
          }
        });

        // the formation
        for (i = 0; i < s.grid.length; i++) {
          if (!s.grid[i]) continue;
          var col = i % Logic.COLS, row = (i - col) / Logic.COLS;
          drawCritter(g, s.grid[i], Logic.critterX(s, col), Logic.critterY(s, row), s.frame);
        }

        // the bonus ship
        if (s.ship) drawShip(g, s.ship.x - s.ship.dir * Logic.SHIP_SPEED * (1 - info.alpha));

        // bombs (zigzags) and the shot (a straight line)
        for (i = 0; i < s.bombs.length; i++) drawBomb(g, s.bombs[i]);
        if (s.shot) g.line(g.snap(s.shot.x), g.snap(s.shot.y), g.snap(s.shot.x), g.snap(s.shot.y + Logic.SHOT_LEN), 0, 2, { cap: "butt" });

        // the ground, the cannon, the lives left
        g.line(0, Logic.GROUND + 1, Kit.W, Logic.GROUND + 1, 4, 2);
        if (s.phase !== "dead" && !(s.over && s.stats.cause === "bombed")) drawCannon(g, s.x, Logic.CANNON_Y, 4);
        for (i = 0; i < s.lives - (s.phase === "dead" ? 0 : 1); i++) drawCannon(g, 18 + i * 24, 288, 8, 0.75);

        // explosions
        for (i = 0; i < s.pops.length; i++) {
          var p = s.pops[i];
          if (p.k === "ship") { g.text(String(p.points), p.x < 20 ? 20 : p.x > 220 ? 220 : p.x, Logic.SHIP_Y + 4, { size: 11, align: "center", ci: 2 }); continue; }
          var r0 = p.k === "cannon" ? 9 : p.k === "zap" ? 4 : 6;
          var grow = info.reduce ? 1 : 0.7 + 0.3 * Math.min(1, (15 - Math.min(15, p.t)) / 6);
          g.star(g.snap(p.x), g.snap(p.y), r0 * grow, p.k === "cannon" ? 1 : 3, { a: 0.9 });
        }

        // messages
        if (info.state !== "over") {
          if (s.phase === "start" && s.waveT < 90) {
            var w = Logic.wave(s, s.level);
            banner(g, "WAVE " + s.level, s.waves ? w.name.toUpperCase() : "GET READY");
          } else if (s.phase === "clear") banner(g, "WAVE CLEARED", "");
          else if (s.phase === "dead") banner(g, s.lives + (s.lives === 1 ? " CANNON LEFT" : " CANNONS LEFT"), "");
        }
      },
    };

    function banner(g, a, b) {
      var y = 204, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, Kit.W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: g.kind === "lcd" ? 18 : 13, align: "center", ci: ci });
      if (b) g.text(b, Kit.W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    function drawCritter(g, kind, x, y, frame) {
      var ci = COLOURS[kind] || 4;
      if (g.lowres) {
        var rows = SPRITES[kind][frame], x0 = x - 6, y0 = y - 5;
        for (var r = 0; r < 5; r++) {
          var run = -1, line = rows[r];
          for (var c = 0; c <= 6; c++) {
            var on = c < 6 && line[c] === "#";
            if (on && run < 0) run = c;
            if (!on && run >= 0) { g.rect(x0 + run * 2, y0 + r * 2, (c - run) * 2, 2, ci, { solid: true }); run = -1; }
          }
        }
        return;
      }
      var up = frame ? 1 : -1;
      if (kind === "a") {            // the spinner: a diamond with one big eye and flapping side spikes
        g.line(x - 4, y, x - 7, y + 4 * up, ci, 1.6);
        g.line(x + 4, y, x + 7, y + 4 * up, ci, 1.6);
        g.poly([[x, y - 5.5], [x + 5.5, y], [x, y + 5.5], [x - 5.5, y]], ci, { solid: true });
        g.circle(x, y, 1.8, 9, { edge: false });
      } else if (kind === "b") {     // the hopper: a box with antennae and kicking legs
        g.line(x - 2, y - 3, x - 4, y - 6, ci, 1.4);
        g.line(x + 2, y - 3, x + 4, y - 6, ci, 1.4);
        g.line(x - 4, y + 2, x - 6 + 2 * (frame ? 1 : 0), y + 5.5, ci, 1.6);
        g.line(x + 4, y + 2, x + 6 - 2 * (frame ? 1 : 0), y + 5.5, ci, 1.6);
        g.rect(x - 5.5, y - 3.5, 11, 7, ci, { r: 2, solid: true });
        g.circle(x - 2.2, y - 0.5, 1.3, 9, { edge: false });
        g.circle(x + 2.2, y - 0.5, 1.3, 9, { edge: false });
      } else {                       // the blob: a round body on three stubby feet
        var sh = frame ? 1 : -1;
        g.line(x - 3, y + 2, x - 4 + sh, y + 5.5, ci, 1.6);
        g.line(x, y + 2, x, y + 5.5, ci, 1.6);
        g.line(x + 3, y + 2, x + 4 + sh, y + 5.5, ci, 1.6);
        g.circle(x, y - 0.5, 5, ci, { solid: true });
        g.circle(x - 2, y - 1.5, 1.3, 9, { edge: false });
        g.circle(x + 2, y - 1.5, 1.3, 9, { edge: false });
      }
    }

    function drawShip(g, x) {
      var y = Logic.SHIP_Y;
      if (g.lowres) {
        g.rect(x - 10, y - 1, 20, 4, 1, { solid: true });
        g.rect(x - 4, y - 5, 8, 4, 1, { solid: true });
        return;
      }
      g.ellipse(x, y + 1, 10, 3.5, 1, { solid: true });
      g.circle(x, y - 2, 4, 1, { solid: true });
      g.circle(x - 5, y + 1, 1, 9, { edge: false });
      g.circle(x, y + 1.5, 1, 9, { edge: false });
      g.circle(x + 5, y + 1, 1, 9, { edge: false });
    }

    function drawBomb(g, b) {
      var x = g.snap(b.x), y = g.snap(b.y);
      if (g.lowres) { g.rect(x - 1, y, 2, Logic.BOMB_LEN, 1, { solid: true }); return; }
      var d = b.k ? 2 : -2;
      g.line(x, y, x + d, y + 2.7, 1, 1.8);
      g.line(x + d, y + 2.7, x - d, y + 5.3, 1, 1.8);
      g.line(x - d, y + 5.3, x, y + Logic.BOMB_LEN, 1, 1.8);
    }

    function drawCannon(g, x, y, ci, scale) {
      var k = scale || 1, hw = Logic.CANNON_HALF * k;
      x = g.snap(x);
      g.rect(x - hw, y, hw * 2, 8 * k, ci, { r: 2, solid: true });
      g.rect(x - 2 * k, y - 4 * k, 4 * k, 5 * k, ci, { r: 1, solid: true });
    }

    return Kit.createSession(canvas, opts, impl);
  }


  root.ArcadeGames.register({
    id: "invaders",
    name: "Sky Defenders",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    buttons: [
      { action: "left", label: "◀", aria: "Left", place: [1, 1, 1, 1] },
      { action: "right", label: "▶", aria: "Right", place: [2, 1, 1, 1] },
      { action: "fire", label: "Fire", aria: "Fire", place: [4, 1, 2, 1] },
    ],
    help: "← → move the cannon, Space fires (hold to keep firing). On a phone: drag on the game and the cannon follows, tap to fire, or use ◀ ▶ and Fire. One shot at a time; hide behind the shields.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
