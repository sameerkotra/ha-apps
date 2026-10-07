/* Household Arcade — Flap: input and drawing around the rules in flap-logic.js.
   Registers itself as "flap" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.FlapLogic;

  var MODES = [{ id: "easy", label: "Easy" }, { id: "normal", label: "Normal" }, { id: "moving", label: "Moving gates" },
    { id: "course", label: "Courses" }];

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prevY = 0, prevX = [], groundOff = 0, prevGround = 0;
    var best = typeof opts.best === "number" && isFinite(opts.best) ? opts.best : null;

    function snap() { prevY = s.y; prevX = s.gates.map(function (gt) { return gt.x; }); prevGround = groundOff; }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; groundOff = 0; snap(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; snap(); },
      step: function () {
        var before = s.gates.length ? s.gates[0] : null;
        snap();
        var evs = pending.concat(Logic.step(s));
        pending = [];
        if (s.started && !s.over) groundOff = (groundOff + Logic.speed(s)) % 24;
        // the first gate went off screen: line the previous positions up with the new list
        if (before && s.gates[0] !== before) prevX.shift();
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { flap: "turn", gate: "eat", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      pointer: function (kind) { if (kind === "down") impl.input("fire", true); },
      draw: function (g, info) {
        var a = info.alpha, fmt = Kit.fmt, i;
        g.hud("SCORE " + fmt(s.score), s.courses ? "COURSE " + s.level + "/" + s.courses.length
          : best != null ? "BEST " + fmt(Math.max(best, s.score)) : "LEVEL " + s.level);

        // far hills (a cached layer)
        g.layer("hills", 1, function (lg) {
          for (var hx = -10; hx < Kit.W + 40; hx += 56) lg.circle(hx, Logic.GROUND + 8, 36, 8, { a: 0.55, edge: false });
        });

        // gates
        for (i = 0; i < s.gates.length; i++) {
          var gt = s.gates[i], px = prevX[i] != null ? prevX[i] : gt.x;
          var x = g.snap(px + (gt.x - px) * a), mid = Logic.gateMid(s, gt);
          var top = mid - gt.gap / 2, bot = mid + gt.gap / 2;
          if (x > Kit.W || x + Logic.GATE_W < 0) continue;
          g.rect(x, Logic.TOP, Logic.GATE_W, g.snap(top) - Logic.TOP, 4, { r: 0, bevel: true });
          g.rect(x - 3, g.snap(top) - 10, Logic.GATE_W + 6, 10, 4, { r: 2, bevel: true });
          g.rect(x, g.snap(bot), Logic.GATE_W, Logic.GROUND - g.snap(bot), 4, { r: 0, bevel: true });
          g.rect(x - 3, g.snap(bot), Logic.GATE_W + 6, 10, 4, { r: 2, bevel: true });
        }

        // the ground, moving with the gates
        g.rect(0, Logic.GROUND, Kit.W, Kit.H - Logic.GROUND, 2, { r: 0, a: 0.8, edge: false });
        g.line(0, Logic.GROUND, Kit.W, Logic.GROUND, 0, 2);
        var go = prevGround + (((groundOff - prevGround) + 24) % 24) * a;
        for (var gx = -go; gx < Kit.W; gx += 24) g.line(g.snap(gx), Logic.GROUND + 6, g.snap(gx + 10), Logic.GROUND + 6, 0, 2, { a: 0.4 });

        // the flyer: hovers gently before the first flap, leans with its speed
        var y = prevY + (s.y - prevY) * a;
        if (!s.started && !info.reduce && info.state !== "over") y += Math.sin(info.t * 4) * 3;
        drawFlyer(g, Logic.X, y, s.vy, s.over);

        // a course's name while its first gates come
        if (s.courses && info.state !== "over" && s.stats.gates - gatesBefore(s) < 2) {
          g.text(s.courses[s.level - 1].name.toUpperCase(), Kit.W / 2, 60, { size: 12, align: "center", a: 0.85 });
        }

        if (!s.started && info.state !== "over") {
          g.text("TAP OR SPACE TO FLAP", Kit.W / 2, 112, { size: 11, align: "center" });
          g.text("FLY THROUGH THE GAPS", Kit.W / 2, 128, { size: 9, align: "center", a: 0.7 });
        }
      },
    };

    function gatesBefore(st) {        // gates in the courses already finished
      var n = 0;
      for (var i = 0; i < st.level - 1; i++) n += st.courses[i].gates;
      return n;
    }

    function drawFlyer(g, x, y, vy, dead) {
      var r = Logic.R + 2;
      g.circle(g.snap(x), g.snap(y), r, dead ? 1 : 3, { solid: true });
      if (g.lowres) {
        g.rect(x + 2, y - 4, 3, 3, 0, { solid: true });
        return;
      }
      // a wing that's up when climbing and down when falling, an eye and a beak
      var wing = vy < 0 ? -5 : 3;
      g.ellipse(x - 4, y + wing * 0.5, 6, 3.5, 2, { a: 0.95 });
      g.circle(x + 4, y - 3, 2.6, 9, { edge: false });
      g.circle(x + 4.8, y - 3, 1.2, 0, { edge: false });
      g.poly([[x + r - 1, y - 1], [x + r + 5, y + 1], [x + r - 1, y + 3]], 1, {});
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "flap",
    name: "Flap",
    modes: MODES,
    defaultMode: "normal",
    controls: "buttons",
    buttons: [{ action: "fire", label: "Flap", aria: "Flap", wide: true }],
    help: "Space, ↑ or a tap anywhere on the game flaps. Fly through the gaps; touching a gate or the ground ends the game. The game waits for your first flap.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
