/* Household Arcade — Colour Memory: input and drawing around the rules in colours-logic.js.
   Registers itself as "colours" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.ColoursLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "fast", label: "Fast" }, { id: "reverse", label: "Backwards" },
    { id: "challenge", label: "Challenges" }];
  // The four pads in a diamond around the middle (multiples of 6 so they sit on the low-resolution grids).
  var CX = 120, CY = 168, R = 42;
  var POS = [[CX, CY - 72], [CX - 72, CY], [CX + 72, CY], [CX, CY + 72]];   // up, left, right, down
  var NAMES = ["RED", "GREEN", "BLUE", "YELLOW"];

  /** The pad under a point of the playfield (the four quarters of the diamond), or -1. */
  function padAt(x, y) {
    var dx = x - CX, dy = y - CY;
    if (dx * dx + dy * dy < 20 * 20 || y < 36) return -1;
    if (Math.abs(dx) > Math.abs(dy)) return dx < 0 ? 1 : 2;
    return dy < 0 ? 0 : 3;
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [];

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

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
      sounds: { toneUp: "eat", toneLeft: "bounce", toneRight: "wall", toneDown: "turn", round: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var p = padAt(lx, ly);
        if (p >= 0) queue(Logic.tap(s, p));
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, i;
        var left = "SCORE " + fmt(s.score), right = "ROUND " + s.level;
        if (s.challenges) {
          right = s.level + "/" + s.challenges.length;
          if (left.length + right.length <= 14) right = "CHALLENGE " + right;   // when it fits beside the score
        }
        g.hud(left, right);
        for (i = 0; i < 4; i++) drawPad(g, i, s.lit === i);

        var over = s.over || info.state === "over";
        if (over && !s.won && s.expected >= 0) {
          // show the pad that was wanted, and cross out a wrong one
          var e = POS[s.expected];
          g.ring(e[0], e[1], R + 6, 0, 3);
          if (!g.lowres) g.ring(e[0], e[1], R + 11, 0, 1.5, { a: 0.7 });
          if (s.wrongPad >= 0) {
            var w = POS[s.wrongPad], d = 16;
            g.line(w[0] - d, w[1] - d, w[0] + d, w[1] + d, 0, 4);
            g.line(w[0] - d, w[1] + d, w[0] + d, w[1] - d, 0, 4);
          }
        }

        // the middle: what's happening
        var msg = "", sub = "";
        if (over) { msg = s.won ? "WON!" : s.stats.cause === "time" ? "SLOW" : "WRONG"; sub = s.expected >= 0 && !s.won ? NAMES[s.expected] : ""; }
        else if (s.phase === "lead" || s.phase === "show") { msg = "WATCH"; sub = s.phase === "show" ? (s.idx + 1) + "/" + s.seq.length : ""; }
        else if (s.phase === "input") { msg = "YOUR"; sub = "TURN"; }
        else if (s.phase === "done") { msg = s.cleared ? "CLEAR!" : "GOOD"; sub = "+" + fmt(10 * s.seq.length); }
        g.text(msg, CX, CY - 2, { size: 11, align: "center", base: "middle" });
        if (sub) g.text(sub, CX, CY + 12, { size: 9, align: "center", base: "middle", a: 0.8 });

        // a challenge: its name while its first sequence plays, and how long the sequence must grow
        if (s.challenges && !over) {
          var ch = s.challenges[s.level - 1];
          if (s.phase !== "input" && s.seq.length === ch.start) g.text(ch.name.length > 26 ? ch.name.slice(0, 25).toUpperCase() + "." : ch.name.toUpperCase(), 10, 48, { size: 10, a: 0.85 });
          g.text("TO " + ch.length, 230, 282, { size: 9, align: "right", a: 0.8 });
        }

        // bottom: your progress through the sequence and the time left to press the next pad
        if (s.phase === "input" && !over) {
          var left = 1 - s.idle / Logic.IDLE_LIMIT;
          g.rect(30, 288, 180, 6, 8, { r: 2 });
          g.rect(30, 288, Math.max(0, 180 * left), 6, left < 0.35 ? 1 : 4, { r: 2, solid: true });
          g.text(s.input + " / " + s.seq.length + (Logic.isReverse(s) ? "  BACKWARDS" : ""), 10, 282, { size: 9, a: 0.8 });
        } else if (Logic.isReverse(s)) {
          g.text("BACKWARDS", 10, 282, { size: 9, a: 0.7 });
        }
      },
    };

    // A pad: dim with an outline when off; solid and a little bigger when lit. Each carries its arrow.
    function drawPad(g, i, lit) {
      var p = POS[i], ci = Logic.PAD_CI[i], x = p[0], y = p[1];
      if (lit) {
        g.circle(x, y, R + 2, ci, { solid: true });
        if (!g.lowres) g.ring(x, y, R + 2, 0, 2, { a: 0.8 });
      } else {
        g.circle(x, y, R - 2, ci, { a: 0.32, edge: false });
        g.ring(x, y, R - 2, ci, 3);
      }
      drawArrow(g, i, x, y, lit ? 9 : 0, lit);
    }

    function drawArrow(g, i, x, y, ci, lit) {
      var a = 16, b = 12, pts;
      if (i === 0) pts = [[x, y - a], [x + b, y + 6], [x - b, y + 6]];
      else if (i === 3) pts = [[x, y + a], [x - b, y - 6], [x + b, y - 6]];
      else if (i === 1) pts = [[x - a, y], [x + 6, y - b], [x + 6, y + b]];
      else pts = [[x + a, y], [x - 6, y + b], [x - 6, y - b]];
      g.poly(pts, ci, { a: lit ? 1 : 0.85 });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "colours",
    name: "Colour Memory",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    help: "Watch the pads light up, then repeat the order: tap the pads or press ↑ ← → ↓ (red up, green left, blue right, yellow down). Each round adds one more. A wrong pad, or 5 seconds without one on your turn, ends the game. Backwards: repeat the order from last to first. Challenges: grow the sequence to the length shown to clear each challenge.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
