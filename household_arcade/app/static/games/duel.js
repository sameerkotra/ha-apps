/* Household Arcade — Paddle Duel: input and drawing around the rules in duel-logic.js.
   Registers itself as "duel" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.DuelLogic;

  var MODES = [{ id: "easy", label: "Easy" }, { id: "normal", label: "Normal" }, { id: "hard", label: "Hard" }];

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, prev = null, trail = [];

    function snapshot() {
      return { px: s.paddle.x, cx: s.cpuX, bx: s.ball.x, by: s.ball.y };
    }
    function ballNow() { return s.ball; }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); prev = snapshot(); trail = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); prev = snapshot(); trail = []; },
      step: function () {
        prev = snapshot();
        var evs = Logic.step(s);
        for (var i = 0; i < evs.length; i++) {
          var t = evs[i].type;
          if (t === "point" || t === "miss" || t === "level" || t === "launch") { trail = []; prev = snapshot(); }
        }
        if (s.serve === 0) { var bn = ballNow(); trail.push({ x: bn.x, y: bn.y }); if (trail.length > 7) trail.shift(); }
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s, 0); },
      sounds: { paddle: "bounce", hit: "hit", wall: "wall", point: "eat", miss: "lose", launch: "launch",
        level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { Logic.press(s, action, down); },
      pointer: function (kind, lx) {
        if (kind === "down" || kind === "move") Logic.setTarget(s, lx);
        if (kind === "up") Logic.press(s, "fire", true);
      },
      draw: function (g, info) {
        var a = info.alpha, fmt = Kit.fmt, reduce = info.reduce, i;
        var opp = Logic.opponent(s), oppName = fitName(opp.name, g.lowres ? 16 : 22);
        g.hud("SCORE " + fmt(s.score), "MATCH " + s.level + "/" + s.opponents.length);

        // the court: centre line and the two match scores
        g.layer("court", 1, function (lg) {
          for (var x = 10; x < 232; x += 14) lg.line(x, Logic.H / 2 + 8, x + 7, Logic.H / 2 + 8, 8, 1.5);
          lg.line(Logic.WALL_L, 38, Logic.WALL_L, 300, 8, 1.5);
          lg.line(Logic.WALL_R, 38, Logic.WALL_R, 300, 8, 1.5);
        });
        g.text(String(s.cpu), 220, Logic.H / 2 - 4, { size: 18, align: "right", a: 0.6 });
        g.text(String(s.me), 220, Logic.H / 2 + 30, { size: 18, align: "right", a: 0.6 });
        g.text(fitName(opp.name, g.lowres ? 12 : 18), 20, Logic.H / 2 - 4, { size: 9, a: 0.55 });
        g.text("YOU", 20, Logic.H / 2 + 26, { size: 9, a: 0.55 });

        // paddles (interpolated)
        var cx = prev.cx + (s.cpuX - prev.cx) * a, px = prev.px + (s.paddle.x - prev.px) * a;
        g.rect(g.snap(cx - Logic.CW / 2), Logic.CY, Logic.CW, Logic.PH, 1, { r: 4, bevel: true });
        g.rect(g.snap(px - Logic.PW / 2), Logic.PY, Logic.PW, Logic.PH, 0, { r: 4, bevel: true });

        // the ball (+ trail in Neon)
        if (s.serve === 0) {
          if (g.kind === "neon" && !reduce) {
            for (i = 0; i < trail.length - 1; i++) g.circle(trail[i].x, trail[i].y, Logic.BALL_R * (0.4 + i / trail.length * 0.5), 7, { a: 0.12 + i * 0.08 });
          }
          var bx = prev.bx + (s.ball.x - prev.bx) * a, by = prev.by + (s.ball.y - prev.by) * a;
          g.circle(g.snap(bx), g.snap(by), Logic.BALL_R, 0, { solid: true });
        } else if (info.state !== "over") {
          if (s.updates < s.shownUntil) {
            g.text("MATCH " + s.level, Kit.W / 2, 118, { size: 18, align: "center" });
            g.text("VS " + oppName, Kit.W / 2, 134, { size: 10, align: "center", a: 0.8 });
          }
          g.circle(Kit.W / 2, Logic.H / 2, Logic.BALL_R, 0, { solid: true, a: 0.6 });
          g.text("FIRST TO " + opp.toWin + " · TAP TO SERVE", Kit.W / 2, 196, { size: 9, align: "center", a: 0.7 });
        }
      },
    };

    function fitName(n, max) { n = String(n).toUpperCase(); return n.length > max ? n.slice(0, max - 1).trim() + "." : n; }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "duel",
    name: "Paddle Duel",
    modes: MODES,
    defaultMode: "normal",
    controls: "paddle",
    padLabel: "Serve",
    help: "Move your paddle (the bottom one) with the mouse, the ← → keys, or by dragging anywhere on the game. Where the ball hits your paddle sets its angle; edge shots are harder to return. Win the match (first to 5, or as many points as the opponent needs) and the next, quicker opponent comes on; beat them all to win. Space or a tap serves.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
