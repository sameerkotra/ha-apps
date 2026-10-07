/* Household Arcade — Paddle Duel: input and drawing around the rules in duel-logic.js.
   Registers itself as "duel" with window.ArcadeGames. Mode "phones" is a live duel on two phones (SPEC §13.4):
   each player sees their own paddle at the bottom — seat 2's phone draws the court turned half-way round, and
   its left/right and finger positions are turned round the same way before they are sent. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.DuelLogic;

  var MODES = [{ id: "easy", label: "Easy" }, { id: "normal", label: "Normal" }, { id: "hard", label: "Hard" }, { id: "phones", label: "Two phones" }];
  var FLIP = { left: "right", right: "left" };

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, prev = null, trail = [];
    var me = opts.live && opts.live.seat === 2 ? 1 : 0, flip = me === 1, lastAim = null;
    var other = String((opts.names && opts.names[1 - me]) || "").split(" ")[0].toUpperCase().slice(0, 10) || "THEM";

    function snapshot() {
      if (s.mode === "phones") return { p0: s.pads[0].x / Logic.FP, p1: s.pads[1].x / Logic.FP, bx: s.ball.x / Logic.FP, by: s.ball.y / Logic.FP };
      return { px: s.paddle.x, cx: s.cpuX, bx: s.ball.x, by: s.ball.y };
    }
    function ballNow() { return s.mode === "phones" ? { x: s.ball.x / Logic.FP, y: s.ball.y / Logic.FP } : s.ball; }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); prev = snapshot(); trail = []; },
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
      score: function () { return me ? s.score2 : s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s, me); },
      // live duel (two phones): inputs reach the rules here, on both phones on the same update
      apply: function (player, action, value) { return Logic.press(s, action, value, player); },
      checksum: function () { return Logic.checksum(s); },
      report: function () { return Logic.report(s); },
      sounds: { paddle: "bounce", hit: "hit", wall: "wall", point: "eat", miss: "lose", launch: "launch",
        level: "level", win: "win", gameover: "gameover" },
      input: function (action, down, act) {
        if (s.mode !== "phones") { Logic.press(s, action, down); return; }
        // two phones: my left and right are the court's turned round on seat 2's phone
        var a = flip && FLIP[action] ? FLIP[action] : action === "up" ? "fire" : action;
        if (a !== "left" && a !== "right" && a !== "fire") return;
        if (a === "fire" && !down) return;
        if (act) act(a, down ? 1 : 0); else Logic.press(s, a, down, me);
      },
      pointer: function (kind, lx, ly, x, y, act) {
        if (s.mode !== "phones") {
          if (kind === "down" || kind === "move") Logic.setTarget(s, lx);
          if (kind === "up") Logic.press(s, "fire", true);
          return;
        }
        if (kind === "down" || kind === "move") {
          var ax = Math.max(0, Math.min(Kit.W, Math.round(flip ? Kit.W - lx : lx)));
          if (ax === lastAim) return;
          lastAim = ax;
          if (act) act("aim", ax); else Logic.press(s, "aim", ax, me);
        } else if (kind === "up") { if (act) act("fire", 1); else Logic.press(s, "fire", true, me); }
      },
      draw: function (g, info) {
        if (s.mode === "phones") { drawPhones(g, info); return; }
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

    /* Two phones: my paddle at the bottom. Seat 2 draws every point at (240 − x, 320 − y). */
    function drawPhones(g, info) {
      var a = info.alpha, fmt = Kit.fmt, reduce = info.reduce, i, M = Logic.MIRROR;
      var X = function (x) { return flip ? Kit.W - x : x; }, Y = function (y) { return flip ? M - y : y; };
      g.hud("SCORE " + fmt(me ? s.score2 : s.score), "FIRST TO " + Logic.PHONES_TO_WIN);
      g.layer("court2", 1, function (lg) {
        for (var x = 10; x < 232; x += 14) lg.line(x, M / 2, x + 7, M / 2, 8, 1.5);
        lg.line(Logic.WALL_L, 38, Logic.WALL_L, 286, 8, 1.5);
        lg.line(Logic.WALL_R, 38, Logic.WALL_R, 286, 8, 1.5);
      });
      var mine = s.points[me], theirs = s.points[1 - me];
      g.text(String(theirs), 220, M / 2 - 10, { size: 18, align: "right", a: 0.6 });
      g.text(String(mine), 220, M / 2 + 26, { size: 18, align: "right", a: 0.6 });
      g.text(other, 20, M / 2 - 10, { size: 9, a: 0.55 });
      g.text("YOU", 20, M / 2 + 22, { size: 9, a: 0.55 });
      // paddles: mine (colour 0) at the bottom of my screen, theirs (colour 1) at the top
      for (i = 0; i < 2; i++) {
        var px = prev["p" + i] + (s.pads[i].x / Logic.FP - prev["p" + i]) * a;
        var top = i === 0 ? Logic.P_BOTTOM : Logic.P_TOP, y = flip ? M - top - Logic.PH : top;
        g.rect(g.snap(X(px) - Logic.PW / 2), y, Logic.PW, Logic.PH, i === me ? 0 : 1, { r: 4, bevel: true });
      }
      if (s.serve === 0) {
        if (g.kind === "neon" && !reduce) {
          for (i = 0; i < trail.length - 1; i++) g.circle(X(trail[i].x), Y(trail[i].y), Logic.BALL_R * (0.4 + i / trail.length * 0.5), 7, { a: 0.12 + i * 0.08 });
        }
        var bx = prev.bx + (s.ball.x / Logic.FP - prev.bx) * a, by = prev.by + (s.ball.y / Logic.FP - prev.by) * a;
        g.circle(g.snap(X(bx)), g.snap(Y(by)), Logic.BALL_R, 0, { solid: true });
      } else if (info.state !== "over" && !s.over) {
        g.circle(Kit.W / 2, M / 2, Logic.BALL_R, 0, { solid: true, a: 0.6 });
        var mineServe = s.serveTo === me;
        g.text(mineServe ? "THE BALL COMES TO YOU" : "THE BALL GOES TO " + other, Kit.W / 2, M / 2 + 44, { size: 10, align: "center", a: 0.8 });
        g.text(mineServe ? "TAP OR SPACE TO SERVE" : "FIRST TO " + Logic.PHONES_TO_WIN + " POINTS", Kit.W / 2, M / 2 + 58, { size: 9, align: "center", a: 0.7 });
      }
      if (s.over) {
        var won = s.winner === me + 1;
        var lcd = g.kind === "lcd";
        g.rect(30, M / 2 - 30, 180, 44, lcd ? 0 : 9, { r: 6, solid: true, a: 0.92 });
        g.text(won ? "YOU WIN!" : other + " WINS", Kit.W / 2, M / 2 - 6, { size: 16, align: "center", ci: lcd ? 9 : 0 });
        g.text(mine + " - " + theirs, Kit.W / 2, M / 2 + 9, { size: 11, align: "center", a: 0.8, ci: lcd ? 9 : 0 });
      }
    }

    // an opponent's name, shortened to fit
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
    lockstep: true,
    liveModes: ["phones"],
    help: "Move your paddle (the bottom one) with the mouse, the ← → keys, or by dragging anywhere on the game. Where the ball hits your paddle sets its angle; edge shots are harder to return. Win the match (first to 5, or as many points as the opponent needs) and the next, quicker opponent comes on; beat them all to win. Space or a tap serves. Two phones: each of you has your own paddle at the bottom of your own screen; first to 7 points wins.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
