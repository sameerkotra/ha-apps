/* Household Arcade — Number Dash: input and drawing around the rules in numbers-logic.js.
   Registers itself as "numbers" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.NumbersLogic;

  var MODES = [{ id: "add", label: "Add & take away" }, { id: "times", label: "Times tables" }, { id: "mixed", label: "Mixed" },
    { id: "challenge", label: "Challenges" }];
  // The four answer tiles in a diamond, in the order up, left, right, down (multiples of 6).
  var TW = 84, TH = 42;
  var TILES = [[78, 108], [24, 156], [132, 156], [78, 204]];
  var CX = 120, CY = 177;

  function modeLabel(id) {
    for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i].label;
    return "";
  }
  /** The tile under a logical point (a little generous round the edges), or -1. */
  function tileAt(x, y) {
    for (var i = 0; i < 4; i++) {
      var t = TILES[i];
      if (x >= t[0] - 6 && x <= t[0] + TW + 6 && y >= t[1] - 4 && y <= t[1] + TH + 4) return i;
    }
    return -1;
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
      sounds: { right: "eat", wrong: "lose", streak: "bonus", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = tileAt(lx, ly);
        if (i >= 0) queue(Logic.answer(s, Logic.DIRS[i]));
      },
      draw: function (g) {
        var fmt = Kit.fmt, i;
        var ch = s.challenges ? Logic.challenge(s, s.level) : null;
        g.hud("SCORE " + fmt(s.score), ch ? "CHALLENGE " + s.level + "/" + s.challenges.length : "LEVEL " + s.level);

        // the sum
        var text = s.q.text;
        g.text(text, Kit.W / 2, 76, { size: text.length > 9 ? 26 : 32, align: "center", base: "middle" });
        g.text("= ?", Kit.W / 2, 96, { size: 11, align: "center", base: "middle", a: 0.6 });

        // the tiles
        var last = s.last;
        for (i = 0; i < 4; i++) {
          var t = TILES[i], x = t[0], y = t[1];
          var isRight = last && i === last.right, isPicked = last && i === last.pick;
          var ci = isRight ? 4 : isPicked ? 1 : 5;
          var dim = last && !isRight && !isPicked;
          g.rect(x, y, TW, TH, ci, { r: 8, solid: !!isRight, a: dim ? 0.5 : 1, bevel: true });
          if (g.kind === "lcd" && isRight) g.rect(x + 3, y + 3, TW - 6, TH - 6, 9, { r: 6 });
          var label = fmt(s.q.choices[i]);
          g.text(label, x + TW / 2, y + TH / 2 + 1, { size: label.length > 4 ? 16 : 20, align: "center", base: "middle",
            ci: isRight && g.kind !== "lcd" ? 9 : 0, a: dim ? 0.6 : 1 });
          // a tick on the right answer, a cross on a wrong pick (shape as well as colour)
          if (isRight) {
            g.line(x + 6, y + 8, x + 9, y + 12, 0, 2.4);
            g.line(x + 9, y + 12, x + 15, y + 4, 0, 2.4);
          } else if (isPicked) {
            g.line(x + 6, y + 4, x + 14, y + 12, 0, 2.4);
            g.line(x + 6, y + 12, x + 14, y + 4, 0, 2.4);
          }
        }
        // a small compass in the middle: which arrow picks which tile
        var a = 6;
        g.poly([[CX, CY - 2 * a], [CX - a, CY - a], [CX + a, CY - a]], 8, { solid: true });
        g.poly([[CX, CY + 2 * a], [CX - a, CY + a], [CX + a, CY + a]], 8, { solid: true });
        g.poly([[CX - 2 * a, CY], [CX - a, CY - a], [CX - a, CY + a]], 8, { solid: true });
        g.poly([[CX + 2 * a, CY], [CX + a, CY - a], [CX + a, CY + a]], 8, { solid: true });

        // streak and the clock
        var mult = s.streak >= 10 ? 3 : s.streak >= 5 ? 2 : 1;
        g.text(s.streak ? "IN A ROW " + s.streak + (mult > 1 ? "  ×" + mult : "") : "", 12, 262, { size: 11, ci: mult > 1 ? 2 : 0 });
        var secs = Math.ceil(s.clock / 60), frac = Math.min(1, s.clock / Logic.startClock(s));
        g.text(secs + " S", 228, 262, { size: 11, align: "right" });
        g.rect(12, 270, 216, 10, 8, { r: 4 });
        g.rect(12, 270, Math.max(0, 216 * frac), 10, secs <= 10 ? 1 : 4, { r: 4, solid: true });
        if (ch) {
          // the challenge's name, and how many right answers it still needs
          g.text(ch.name.toUpperCase(), 12, 294, { size: 9, a: 0.85 });
          g.text(Math.max(0, ch.right - s.cright) + " TO GO", 228, 294, { size: 10, align: "right" });
        } else g.text(modeLabel(s.mode).toUpperCase(), Kit.W / 2, 294, { size: 9, align: "center", a: 0.7 });
      },
    };

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "numbers",
    name: "Number Dash",
    modes: MODES,
    defaultMode: "add",
    controls: "touch",
    help: "Tap the right answer, or press the arrow that points to it (↑ ← → ↓). A right answer adds a second to the clock, a wrong one takes three off. Answer 5 or 10 in a row for double or triple points. In Challenges, get enough right before the clock runs out to clear each one.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
