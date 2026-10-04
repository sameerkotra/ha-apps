/* Household Arcade — Word Search: input and drawing around the rules in wordsearch-logic.js.
   Registers itself as "wordsearch" with window.ArcadeGames.

   Played on the canvas: drag across a word, or tap its first and last letter. The words to find are listed
   under the grid and struck through when found. The shell's Hint button points at the first letter of a word. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.WordSearchLogic;

  var MODES = [{ id: "little", label: "Little ones (5–7)" }, { id: "kids", label: "Kids (8–11)" }, { id: "family", label: "Everyone (12 and up)" }, { id: "puzzler", label: "Puzzler (big grid)" }];
  var BUTTONS = [{ action: "hint", label: "💡 Hint", aria: "Hint: show where a word starts" }];
  var GX_MID = 120, GY = 38, MAX_GRID = 208, LIST_Y = 254;
  var COLOURS = [5, 4, 2, 6, 7, 1];       // the found words take these in turn

  // An outline without a fill: a stroked rect is filled in on the coarse looks, which would hide the letters.
  function frame(g, x, y, w, h, ci, wd, a) {
    g.line(x, y, x + w, y, ci, wd, { a: a, cap: "butt" }); g.line(x, y + h, x + w, y + h, ci, wd, { a: a, cap: "butt" });
    g.line(x, y, x, y + h, ci, wd, { a: a, cap: "butt" }); g.line(x + w, y, x + w, y + h, ci, wd, { a: a, cap: "butt" });
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], cell = 20, gx = 0;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function layout() { cell = Math.floor(MAX_GRID / s.n); gx = GX_MID - s.n * cell / 2; }
    function cellAt(x, y, clamp) {
      var c = Math.floor((x - gx) / cell), r = Math.floor((y - GY) / cell);
      if (clamp) { c = Math.max(0, Math.min(s.n - 1, c)); r = Math.max(0, Math.min(s.n - 1, r)); }
      else if (c < 0 || c >= s.n || r < 0 || r >= s.n) return -1;
      return r * s.n + c;
    }
    function cx(i) { return gx + (i % s.n) * cell + cell / 2; }
    function cy(i) { return GY + Math.floor(i / s.n) * cell + cell / 2; }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed }); layout(); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); layout(); pending = []; },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return Logic.scoreOf(s); },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      status: function () { return Logic.status(s); },
      sounds: { found: "powerup", win: "win", miss: "wall", hint: "turn", select: "turn" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (s.over) return;
        if (kind === "down") { var i = cellAt(lx, ly, false); if (i >= 0) Logic.dragStart(s, i); }
        else if (kind === "move") Logic.dragMove(s, cellAt(lx, ly, true));
        else if (kind === "up" && s.drag) queue(Logic.dragEnd(s, cellAt(lx, ly, true)));
      },
      draw: draw,
    };

    function band(g, a, b, ci, alpha) {
      var lcd = g.kind === "lcd";
      if (lcd) {
        var cells = Logic.line(s, a, b) || [a];
        cells.forEach(function (i) { frame(g, cx(i) - cell / 2 + 1, cy(i) - cell / 2 + 1, cell - 2, cell - 2, 0, 1.2, 1); });
        return;
      }
      g.line(cx(a), cy(a), cx(b), cy(b), ci, cell * 0.78, { a: alpha, cap: "round" });
    }

    function draw(g, info) {
      var i, k, lowres = g.lowres;
      g.hud(s.themeName.toUpperCase(), Logic.clock(Logic.seconds(s)));
      g.text(s.message || (s.foundCount + " / " + s.words.length), 120, 24, { size: 9, align: "center", a: s.message ? 1 : 0.8 });

      // found words under the letters
      frame(g, gx - 1, GY - 1, s.n * cell + 2, s.n * cell + 2, 8, 1.2, 1);
      var colour = 0;
      s.words.forEach(function (w) {
        if (!w.found) return;
        var cells = Logic.cellsOf(s, w);
        band(g, cells[0], cells[cells.length - 1], COLOURS[colour++ % COLOURS.length], 0.4);
      });
      if (s.drag && s.drag.to !== s.drag.from) band(g, s.drag.from, s.drag.to, 3, 0.55);
      else if (s.drag) frame(g, cx(s.drag.from) - cell / 2 + 1, cy(s.drag.from) - cell / 2 + 1, cell - 2, cell - 2, 3, 2, 1);
      if (s.anchor >= 0) frame(g, cx(s.anchor) - cell / 2 + 1, cy(s.anchor) - cell / 2 + 1, cell - 2, cell - 2, 5, 2, 1);
      if (s.cursor >= 0 && s.cursor !== s.anchor) frame(g, cx(s.cursor) - cell / 2 + 1, cy(s.cursor) - cell / 2 + 1, cell - 2, cell - 2, 8, 1.4, 1);
      if (s.hint) g.ring(cx(s.hint.cell), cy(s.hint.cell), cell * 0.5, 1, 2.2);

      // the letters
      var size = cell * 0.62;
      for (i = 0; i < s.n * s.n; i++) g.text(s.letters[i].toUpperCase(), cx(i), cy(i) + 1, { size: size, align: "center", base: "middle", ci: 0 });

      // the word list
      var cols = 3, colW = 76, rowH = lowres ? (s.words.length > 9 ? 11 : 12.5) : s.words.length > 9 ? 10 : 11.5, x0 = 120 - cols * colW / 2 + 4;
      s.words.forEach(function (w, n) {
        var x = x0 + (n % cols) * colW, y = LIST_Y + 7 + Math.floor(n / cols) * rowH, label = w.w.toUpperCase();
        g.text(label, x, y, { size: lowres ? 6.5 : 8.5, a: w.found ? (lowres ? 0.3 : 0.45) : 1 });
        if (w.found) g.line(x, y - 3, x + label.length * (lowres ? 5.3 : 5.6), y - 3, 0, 1, { a: lowres ? 1 : 0.7 });
      });

      if (s.won) {
        var lcd = g.kind === "lcd", tc = lcd ? 9 : 0;
        if (lcd) g.rect(35, 112, 170, 46, 0, { solid: true });
        else {
          var surface = g.kind === "neon" || g.kind === "pixel" ? 8 : 9;      // neon and pixel have a light "paper": use a dark panel
          for (var n = 0; n < (g.kind === "neon" ? 3 : 1); n++) g.rect(35, 112, 170, 46, surface, { r: 6, solid: true, a: 1 });
          frame(g, 35, 112, 170, 46, 0, 1.5, 1);
        }
        g.text("ALL FOUND!", 120, 133, { size: 15, align: "center", ci: tc });
        g.text(String(Logic.scoreOf(s)), 120, 149, { size: 10, align: "center", ci: tc });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "wordsearch",
    name: "Word Search",
    modes: MODES,
    defaultMode: "kids",
    controls: "touch",
    buttons: BUTTONS,
    help: "Find every word in the list. Drag a finger or the mouse across the letters of a word (or tap its first letter, then its last). Words can run across, down and slantwise — and in the bigger puzzles backwards too. Hint shows where a word starts and takes off 25 points. A quick finish earns a bonus.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
