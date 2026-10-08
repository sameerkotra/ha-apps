/* Household Arcade — Brick Breaker: input and drawing around the rules in brick-logic.js.
   Registers itself as "brick" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.BrickLogic;

  var MODES = [{ id: "powerups", label: "Power-ups" }, { id: "classic", label: "Classic" }];
  var ROW_COLOURS = [1, 2, 3, 4, 5, 6];
  var POWER_LETTER = { wide: "W", slow: "S", multi: "B", life: "+" };

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, prev = null, trails = [], parts = [], shake = 0;
    var reduceFlag = !!opts.reduceMotion;

    function snapshot() {
      return { px: s.paddle.x, balls: s.balls.map(function (b) { return { x: b.x, y: b.y }; }) };
    }

    var impl = {
      init: function (seed) {
        s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel });
        prev = snapshot(); trails = []; parts = []; shake = 0;
      },
      save: function () { return Logic.save(s); },
      restore: function (data) {
        s = Logic.restore(data, opts.levels);
        prev = snapshot(); trails = []; parts = []; shake = 0;
      },
      step: function () {
        prev = snapshot();
        var evs = Logic.step(s);
        var reduce = Kit.prefersReducedMotion() || reduceFlag;
        for (var i = 0; i < evs.length; i++) {
          var e = evs[i];
          if (e.type === "break" && !reduce) {
            for (var k = 0; k < 6; k++) {
              parts.push({ x: e.x + Math.random() * e.w, y: e.y + Math.random() * e.h,
                vx: (Math.random() - 0.5) * 2.4, vy: -Math.random() * 1.6, life: 30, ci: ROW_COLOURS[e.row % ROW_COLOURS.length] });
            }
          }
          if (e.type === "lifeLost" || e.type === "gameover") { if (!reduce) shake = 18; trails = []; }
          if (e.type === "level") { trails = []; parts = []; }
          if (e.type === "level" || e.type === "lifeLost") prev = snapshot();
        }
        for (var j = parts.length - 1; j >= 0; j--) {
          var p = parts[j]; p.x += p.vx; p.y += p.vy; p.vy += 0.12;
          if (--p.life <= 0) parts.splice(j, 1);
        }
        // a short trail behind each ball (Neon draws it)
        while (trails.length < s.balls.length) trails.push([]);
        trails.length = s.balls.length;
        for (var b = 0; b < s.balls.length; b++) {
          var tr = trails[b]; tr.push({ x: s.balls[b].x, y: s.balls[b].y });
          if (tr.length > 7) tr.shift();
          if (s.serving) tr.length = 0;
        }
        if (shake > 0) shake--;
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { paddle: "bounce", wall: "wall", hit: "hit", "break": "break", powerup: "powerup",
        launch: "launch", lifeLost: "lose", level: "level", gameover: "gameover" },
      input: function (action, down) {
        if (action === "left" || action === "right") Logic.press(s, action, down);
        else if ((action === "fire" || action === "up") && down) Logic.press(s, "fire", true);
      },
      pointer: function (kind, lx, ly) {
        // The paddle follows the finger or mouse along x from anywhere on the canvas,
        // so the finger can stay below the paddle and not hide it. Releasing a tap launches.
        if (kind === "down" || kind === "move") Logic.setTarget(s, lx);
        if (kind === "up" && s.serving) Logic.press(s, "fire", true);
      },
      draw: function (g, info) {
        var a = info.alpha, fmt = Kit.fmt, reduce = info.reduce;
        if (shake > 0 && !reduce) {
          var m = shake / 18 * 2.5;
          g.ctx.translate(g.snap((Math.random() - 0.5) * 2 * m), g.snap((Math.random() - 0.5) * 2 * m));
        }
        g.hud("SCORE " + fmt(s.score), "LEVEL " + s.level);

        // bricks: cached, redrawn only when one is hit
        g.layer("bricks", s.version, function (lg) {
          for (var i = 0; i < s.bricks.length; i++) {
            var br = s.bricks[i];
            if (br.alive) drawBrick(lg, br);
          }
        });

        // particles from broken bricks
        for (var i = 0; i < parts.length; i++) {
          var p = parts[i];
          g.rect(p.x - 1.5, p.y - 1.5, 3, 3, p.ci, { a: Math.min(1, p.life / 20), r: 0, edge: false });
        }

        // power-up capsules
        for (i = 0; i < s.capsules.length; i++) {
          var c = s.capsules[i];
          var lcd = g.kind === "lcd"; // Retro LCD: an outlined capsule with a dark letter
          var cw = lcd ? 24 : Logic.CAPSULE_W, chh = lcd ? 12 : Logic.CAPSULE_H; // drawing only; catching uses the rules' size
          g.rect(c.x - cw / 2, c.y - chh / 2, cw, chh, lcd ? 9 : 7, { r: 4, solid: !lcd });
          g.text(POWER_LETTER[c.type], c.x, c.y + 0.5, { size: 8, align: "center", base: "middle", ci: lcd ? 0 : 9, abs: "#000000" });
        }

        // paddle (interpolated between updates for smooth movement on fast screens)
        var px = prev.px + (s.paddle.x - prev.px) * a, pw = s.paddle.w;
        g.rect(g.snap(px - pw / 2), Logic.PADDLE_Y, pw, Logic.PADDLE_H, 0, { r: 4, bevel: true });

        // balls (+ trail in Neon)
        for (i = 0; i < s.balls.length; i++) {
          var b = s.balls[i], pb = prev.balls[i] || b;
          var bx = pb.x + (b.x - pb.x) * a, by = pb.y + (b.y - pb.y) * a;
          if (g.kind === "neon" && !reduce && trails[i]) {
            var tr = trails[i];
            for (var t = 0; t < tr.length - 1; t++) g.circle(tr[t].x, tr[t].y, Logic.BALL_R * (0.4 + t / tr.length * 0.5), 7, { a: 0.12 + t * 0.08 });
          }
          g.circle(g.snap(bx), g.snap(by), Logic.BALL_R, 0, { solid: true });
        }

        // serve: level name and how to launch
        if (s.serving && info.state !== "over") {
          g.text("LEVEL " + s.level, Kit.W / 2, 206, { size: 18, align: "center" });
          g.text(s.layout.toUpperCase(), Kit.W / 2, 222, { size: 10, align: "center", a: 0.7 });
          g.text("SPACE OR TAP TO LAUNCH", Kit.W / 2, 246, { size: 9, align: "center", a: 0.7 });
        }

        // lives and power-up timers along the bottom
        for (i = 0; i < s.lives; i++) g.rect(12 + i * 16, 286, 12, 4, 0, { r: 2, a: 0.6 });
        var x = 228;
        ["slow", "wide"].forEach(function (k) {
          var left = s.timers[k];
          if (left <= 0) return;
          var full = Logic.POWER_SECONDS[k] * 60, w = 30 * left / full;
          g.rect(x - 30, 286, 30, 4, 8, { r: 2 });
          g.rect(x - w, 286, w, 4, 7, { r: 2, solid: true });
          g.text(POWER_LETTER[k], x - 34, 291, { size: 9, align: "right" });
          x -= 52;
        });
      },
    };

    // Toughness is shown by a pattern as well as colour: one stripe per extra hit
    // still needed (Retro LCD: hollow / dotted / solid).
    function drawBrick(g, br) {
      var ci = ROW_COLOURS[br.row % ROW_COLOURS.length];
      if (g.kind === "lcd") {
        g.rect(br.x, br.y, br.w, br.h, br.hp === 1 ? 9 : ci, { solid: br.hp >= 3 });
        return;
      }
      g.rect(br.x, br.y, br.w, br.h, ci, { bevel: true, a: br.hp < br.max ? 0.8 : 1 });
      var stripeCi = 9;
      if (br.hp === 2) g.line(br.x + 4, br.y + br.h / 2, br.x + br.w - 4, br.y + br.h / 2, stripeCi, 1.4, { a: 0.75 });
      if (br.hp >= 3) {
        g.line(br.x + 4, br.y + 3.5, br.x + br.w - 4, br.y + 3.5, stripeCi, 1.4, { a: 0.75 });
        g.line(br.x + 4, br.y + br.h - 3.5, br.x + br.w - 4, br.y + br.h - 3.5, stripeCi, 1.4, { a: 0.75 });
      }
    }

    var inst = Kit.createSession(canvas, opts, impl);
    var setRM = inst.setReduceMotion;
    inst.setReduceMotion = function (on) { reduceFlag = !!on; if (reduceFlag) { shake = 0; parts = []; } setRM(on); };
    return inst;
  }

  root.ArcadeGames.register({
    id: "brick",
    name: "Brick Breaker",
    modes: MODES,
    defaultMode: "powerups",
    controls: "paddle",
    help: "Move the paddle with the mouse, the ← → keys, or by dragging anywhere along the bottom. Space or a tap launches the ball. Where the ball hits the paddle sets its angle.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
