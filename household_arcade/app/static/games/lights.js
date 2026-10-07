/* Household Arcade — Lights Out: input and drawing around the rules in lights-logic.js.
   Registers itself as "lights" with window.ArcadeGames.

   A tap presses a light (it and its four neighbours switch). Lit lights are bright with rays, dark ones a plain
   outline, so on and off never depend on colour alone (Retro LCD: solid ink and an outline). The keyboard cursor
   is a ring; the hint (when the Hints option is on) a dashed ring on the light to press. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.LightsLogic;

  var MODES = [{ id: "little", label: "Little 3 × 3 (gentle)" }, { id: "classic", label: "5 × 5" }, { id: "big", label: "7 × 7" },
    { id: "climb", label: "Climb (3 × 3 to 7 × 7)" }];
  var BX = 14, BY = 44, BS = 212, MSG_Y = 274;

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [];

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function cellAt(x, y) {
      var cs = BS / s.n, c = Math.floor((x - BX) / cs), r = Math.floor((y - BY) / cs);
      return c < 0 || r < 0 || c >= s.n || r >= s.n ? -1 : r * s.n + c;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { press: "turn", hint: "powerup", nohint: "wall", clear: "row", level: "level", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = cellAt(lx, ly);
        if (i >= 0) queue(Logic.pressCell(s, i));
      },
      draw: draw,
    };

    function light(g, x, y, cs, on, recent) {
      var lcd = g.kind === "lcd", pad = Math.max(2, cs * 0.08), r = cs / 2 - pad, cx = x + cs / 2, cy = y + cs / 2;
      if (on) {
        if (lcd) { g.rect(x + pad, y + pad, cs - 2 * pad, cs - 2 * pad, 0, { solid: true }); return; }
        g.rect(x + pad, y + pad, cs - 2 * pad, cs - 2 * pad, 3, { r: Math.min(8, cs * 0.18), solid: true, stroke: g.kind === "modern" });
        // rays: four short strokes from the middle (the "on" sign besides the colour)
        var a = r * 0.35, b = r * 0.7, ink = g.kind === "neon" ? 3 : 0;
        if (cs >= 20) {
          g.circle(cx, cy, r * 0.24, ink, { solid: true, a: g.kind === "neon" ? 1 : 0.55 });
          g.line(cx, cy - a, cx, cy - b, ink, 1.5, { a: 0.55 }); g.line(cx, cy + a, cx, cy + b, ink, 1.5, { a: 0.55 });
          g.line(cx - a, cy, cx - b, cy, ink, 1.5, { a: 0.55 }); g.line(cx + a, cy, cx + b, cy, ink, 1.5, { a: 0.55 });
        }
      } else {
        if (lcd) { g.rect(x + pad, y + pad, cs - 2 * pad, cs - 2 * pad, 9, {}); return; }
        g.rect(x + pad, y + pad, cs - 2 * pad, cs - 2 * pad, 8, { r: Math.min(8, cs * 0.18), solid: true, a: g.kind === "neon" ? 0.6 : 0.8 });
      }
      if (recent > 0 && !lcd) g.rect(x + pad, y + pad, cs - 2 * pad, cs - 2 * pad, 9, { r: Math.min(8, cs * 0.18), a: 0.25 * recent, solid: true, edge: false });
    }
    function ring(g, x, y, cs, ci, w, dash) {
      var o2 = dash ? { dash: [3, 3], cap: "butt" } : { cap: "butt" };
      g.line(x + 1, y + 1, x + cs - 1, y + 1, ci, w, o2); g.line(x + 1, y + cs - 1, x + cs - 1, y + cs - 1, ci, w, o2);
      g.line(x + 1, y + 1, x + 1, y + cs - 1, ci, w, o2); g.line(x + cs - 1, y + 1, x + cs - 1, y + cs - 1, ci, w, o2);
    }

    function draw(g, info) {
      var n = s.n, cs = BS / n, i, lcd = g.kind === "lcd";
      var left = s.mode === "climb" ? "BOARD " + s.level + "/" + s.sizes.length : n + " × " + n;
      g.hud(left, Logic.clock(Logic.seconds(s)));
      g.board(BX, BY, BS, BS);
      var recent = info.reduce || s.phase !== "play" ? 0 : Math.max(0, 1 - (s.updates - s.lastAt) / 12);
      var nb = recent > 0 && s.last >= 0 && s.last < s.lights.length ? Logic.neighbours(n, s.last) : [];
      for (i = 0; i < s.lights.length; i++) {
        light(g, BX + (i % n) * cs, BY + Math.floor(i / n) * cs, cs, s.lights[i], nb.indexOf(i) >= 0 ? recent : 0);
      }
      if (s.hint >= 0) {
        ring(g, BX + (s.hint % n) * cs, BY + Math.floor(s.hint / n) * cs, cs, lcd ? 0 : 1, 3, true);
      }
      if (info.state === "running" || info.state === "paused") ring(g, BX + (s.cursor % n) * cs, BY + Math.floor(s.cursor / n) * cs, cs, lcd ? 0 : 5, 2, false);
      var lit = Logic.lit(s);
      g.text("PRESSES " + s.moves + "   LIT " + lit, 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.hint >= 0 ? "Press the ringed light" : s.updates - (s.noHintAt == null ? -1000 : s.noHintAt) < 180 ? "Hints are off (start screen)" :
        s.moves === 0 ? "Switch every light off" :
        s.hintsOn ? "Hint shows a light (+" + Logic.HINT_COST + ")" : "Fewest presses: " + s.fewest;
      g.text(tip, 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      if (s.phase === "clear" || s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text(s.won ? "ALL OFF!" : "LIGHTS OUT!", 120, 147, { size: 15, align: "center", ci: lcd ? 9 : 0 });
        g.text(s.won ? s.moves + " PRESSES" : "NEXT: " + s.sizes[s.level] + " × " + s.sizes[s.level], 120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "lights",
    name: "Lights Out",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "alt", label: "💡 Hint", aria: "Hint: show a light to press", wide: true }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 5 points)" }] }],
    help: "Switch every light off. Pressing a light switches it and its four neighbours — on ones off, off ones on. Tap a light, or move the ring with the arrows and press Space. Fewer presses score more. With the Hints option on, Hint (or C) rings a light to press (it costs 5 points; hints are off in races). Little 3 × 3 is the gentle one; Climb goes from 3 × 3 up to 7 × 7.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
