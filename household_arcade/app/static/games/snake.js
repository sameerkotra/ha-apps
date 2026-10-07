/* Household Arcade — Snake: input and drawing around the rules in snake-logic.js.
   Registers itself as "snake" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.SnakeLogic;

  var MODES = [
    { id: "walls-slow", label: "Walls · Slow" }, { id: "walls-normal", label: "Walls · Normal" },
    { id: "walls-fast", label: "Walls · Fast" }, { id: "wrap-slow", label: "Wrap · Slow" },
    { id: "wrap-normal", label: "Wrap · Normal" }, { id: "wrap-fast", label: "Wrap · Fast" },
    { id: "maze", label: "Maze" },
  ];
  var SWIPE_PX = 18; // CSS pixels of finger travel that count as a swipe

  function modeLabel(id) {
    for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i].label;
    return "";
  }
  function lerp(a, b, t) { return a + (b - a) * t; }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, swipe = null;
    var best = typeof opts.best === "number" && isFinite(opts.best) ? opts.best : null;

    function geometry(g) {
      // Cell size per look so cells sit exactly on the coarse grid of Retro LCD (7 dots) and Pixel (5 pixels).
      var cs = g.kind === "lcd" ? 10.5 : g.kind === "pixel" ? 10 : 11;
      var size = cs * s.cols;
      return { cs: cs, ox: (Kit.W - size) / 2, oy: g.kind === "lcd" ? 45 : 46, size: size };
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); swipe = null; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); swipe = null; },
      step: function () { return Logic.step(s); },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { eat: "eat", bonus: "bonus", speed: "speed", level: "speed", die: "gameover", win: "win" },
      input: function (action, down) {
        if (down && Logic.DIRS[action]) Logic.turn(s, action);
      },
      pointer: function (kind, lx, ly, x, y) {
        if (kind === "down") { swipe = { x: x, y: y }; return; }
        if (!swipe) return;
        if (kind === "up") { swipe = null; return; }
        var dx = x - swipe.x, dy = y - swipe.y;
        if (Math.max(Math.abs(dx), Math.abs(dy)) < SWIPE_PX) return;
        Logic.turn(s, Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up"));
        swipe = { x: x, y: y }; // keep swiping for the next turn without lifting the finger
      },
      draw: function (g, info) {
        var G = geometry(g), cs = G.cs, ox = G.ox, oy = G.oy;
        var fmt = Kit.fmt;
        g.hud("SCORE " + fmt(s.score), s.maze ? "MAZE " + s.level + "/" + s.mazes.length
          : best != null ? "BEST " + fmt(Math.max(best, s.score)) : "LEVEL " + s.level);
        g.board(ox, oy, G.size, G.size);

        // Maze walls: cached, redrawn only for a new maze (or a new look)
        if (s.walls) {
          g.layer("walls", s.wallsVersion + ":" + cs, function (lg) {
            for (var w = 0; w < s.walls.length; w++) {
              if (!s.walls[w]) continue;
              var wx = ox + (w % s.cols) * cs, wy = oy + Math.floor(w / s.cols) * cs;
              lg.rect(wx + 0.8, wy + 0.8, cs - 1.6, cs - 1.6, 6, { solid: g.kind !== "lcd", r: g.kind === "modern" ? 2 : 0, a: 0.85 });
            }
          });
        }

        // food and bonus
        if (s.food) g.circle(ox + s.food.x * cs + cs / 2, oy + s.food.y * cs + cs / 2, cs * 0.41, 1);
        if (s.bonus) {
          var b = s.bonus, blinkOff = !info.reduce && b.ttl < 90 && Math.floor(b.ttl / 15) % 2 === 1;
          var bx = ox + b.x * cs + cs / 2, by = oy + b.y * cs + cs / 2;
          if (!blinkOff) g.star(bx, by, cs * 0.55, 3);
          if (info.reduce && !g.lowres) g.ring(bx, by, cs * 0.75, 3, 1, { a: b.ttl / b.life });
        }

        // the snake: body on its cells; head and tail slide between cells in the smooth looks
        var body = s.snake, n = body.length, i;
        var smooth = !g.lowres && info.state !== "over";
        var p = smooth ? Math.min(0.999, s.progress + info.alpha * s.cps / 60) : 0;
        var dead = s.over && !s.won;
        var nextDir = s.queue.length ? s.queue[0] : s.dir;
        var next = smooth ? Logic.nextCell(s, nextDir) : null;
        var head = body[0];
        if (next && (Math.abs(next.x - head.x) + Math.abs(next.y - head.y) !== 1)) next = null; // wrapping: no slide
        var tailMoves = smooth && s.grow === 0 && !(next && s.food && next.x === s.food.x && next.y === s.food.y);

        var bodyEnd = smooth ? 0 : 1; // sliding head: its cell is drawn as body underneath
        for (i = n - 1; i >= bodyEnd; i--) {
          var c = body[i], cx = c.x, cy = c.y;
          if (i === n - 1 && tailMoves && n > 1) {
            var t2 = body[n - 2];
            if (Math.abs(t2.x - c.x) + Math.abs(t2.y - c.y) === 1) { cx = lerp(c.x, t2.x, p); cy = lerp(c.y, t2.y, p); }
          }
          g.cell(ox + cx * cs, oy + cy * cs, cs, 4, { a: 0.85 });
        }
        var hx = head.x, hy = head.y;
        if (next && !Logic.isSnake(s, next.x, next.y)) { hx = lerp(head.x, next.x, p); hy = lerp(head.y, next.y, p); }
        g.cell(ox + hx * cs, oy + hy * cs, cs, dead ? 1 : 4);
        if (!g.lowres) {
          var d = Logic.DIRS[s.dir], ex = ox + hx * cs + cs / 2 + d[0] * cs * 0.18, ey = oy + hy * cs + cs / 2 + d[1] * cs * 0.18;
          var px = -d[1] * cs * 0.2, py = d[0] * cs * 0.2;
          g.circle(ex + px, ey + py, 1.3, 9, { edge: false });
          g.circle(ex - px, ey - py, 1.3, 9, { edge: false });
        }

        if (s.maze) {
          g.text((s.mazeName + " · " + s.mazeFoods + "/" + s.mazeGoal).toUpperCase(), Kit.W / 2, 288, { size: 11, align: "center", a: 0.7 });
          if (s.updates < s.mazeShownUntil && info.state !== "over") {
            g.text("MAZE " + s.level, Kit.W / 2, 140, { size: 18, align: "center" });
            g.text(s.mazeName.toUpperCase(), Kit.W / 2, 158, { size: 10, align: "center", a: 0.8 });
          }
        } else {
          g.text(modeLabel(s.mode).toUpperCase(), Kit.W / 2, 288, { size: 11, align: "center", a: 0.7 });
        }
      },
    };

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "snake",
    name: "Snake",
    modes: MODES,
    defaultMode: "walls-normal",
    controls: "dpad",
    help: "Arrow keys or WASD turn the snake; on a phone, swipe or use the arrow pad. Eat to grow; don't bite your tail. In Maze, eat the number of foods shown to reach the next maze. P or Esc pauses.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
