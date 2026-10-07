/* Household Arcade — Sudoku: input and drawing around the rules in sudoku-logic.js.
   Registers itself as "sudoku" with window.ArcadeGames.

   The number pad and the tools (Notes, Fill notes, Hint, Undo, Erase) are the shell's buttons (big, always the
   same size); a tap on the grid selects a cell. Number lines are drawn here: the cells holding the focus number
   are highlighted and every cell in their rows, columns and boxes gets one even, soft shade (worked out once
   by Logic.lines, so crossings don't get darker); the empty cells left clear are the only places that number
   can still go. The start-screen option picks rows, columns and boxes (default), rows and columns only, or
   none; the selected cell's own row/column/box tint follows the same choice. The focus number's cells and the
   selected cell are always highlighted. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.SudokuLogic;

  var MODES = [{ id: "easy", label: "Easy" }, { id: "medium", label: "Medium" }, { id: "hard", label: "Hard" }, { id: "expert", label: "Expert" }];
  var OPTIONS = [
    { id: "mistakes", label: "Mistakes", default: "now", choices: [{ id: "now", label: "Shown at once (+10 s each)" }, { id: "end", label: "Shown when the grid is full" }] },
    // "on" keeps its id from when this was On / Off, so a saved On stays rows, columns and boxes and a saved Off is None
    { id: "lines", label: "Number lines", default: "on", choices: [{ id: "on", label: "Rows, columns and boxes" }, { id: "rows", label: "Rows and columns only" }, { id: "off", label: "None" }] },
  ];
  var BUTTONS = [
    { action: "n1", label: "1", place: [1, 1, 1, 1] }, { action: "n2", label: "2", place: [2, 1, 1, 1] }, { action: "n3", label: "3", place: [3, 1, 1, 1] },
    { action: "n4", label: "4", place: [1, 2, 1, 1] }, { action: "n5", label: "5", place: [2, 2, 1, 1] }, { action: "n6", label: "6", place: [3, 2, 1, 1] },
    { action: "n7", label: "7", place: [1, 3, 1, 1] }, { action: "n8", label: "8", place: [2, 3, 1, 1] }, { action: "n9", label: "9", place: [3, 3, 1, 1] },
    { action: "notes", label: "✎ Notes", aria: "Notes on or off", place: [4, 1, 2, 1] },
    { action: "undo", label: "↶ Undo", aria: "Undo", place: [4, 2, 1, 1] }, { action: "erase", label: "⌫ Erase", aria: "Erase", place: [5, 2, 1, 1] },
    { action: "hint", label: "💡 Hint", aria: "Hint", place: [4, 3, 1, 1] }, { action: "fill", label: "Fill notes", aria: "Fill in all the notes", place: [5, 3, 1, 1] },
  ];

  // the board on the 240 × 300 playfield
  var CS = 23, GX = 16, GY = 38, GW = CS * 9, MSG_Y = 250, SHADE_CI = 7;     // 7: the looks' accent (teal/cyan)

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [], lineMode = Logic.lineMode(o.lines), linesOn = lineMode !== "off";

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function cellAt(x, y) {
      var c = Math.floor((x - GX) / CS), r = Math.floor((y - GY) / CS);
      return c < 0 || c > 8 || r < 0 || r > 8 ? -1 : r * 9 + c;
    }

    var impl = {
      init: function (seed) {
        s = Logic.create({ mode: opts.mode, seed: seed, hints: opts.config ? opts.config.hints : undefined, mistakes: o.mistakes });
        pending = [];
      },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.won ? s.score : 0; },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      status: function () { return Logic.status(s); },
      sounds: { place: "turn", wrong: "wall", hint: "powerup", hintFill: "turn", undo: "turn", erase: "hit", fill: "bounce", win: "win", check: "wall", nohint: "wall" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = cellAt(lx, ly);
        if (i >= 0) queue(Logic.tap(s, i));
      },
      draw: draw,
    };

    function draw(g, info) {
      var fmt = Kit.fmt, i, r, c, x, y, lowres = g.lowres, neon = g.kind === "neon", fillSolid = !neon;     // Neon glows a solid rect: shade with outlines there
      var surface = g.kind === "neon" || g.kind === "pixel" ? 8 : 9;      // a panel colour the text reads on (neon and pixel have light "paper")
      var left = (Logic.LEVEL_NAMES[s.mode] || "").toUpperCase();
      var hl = Logic.hintsLeft(s);
      g.hud(left, Logic.clock(Logic.effective(s)));
      g.text(hl < 0 ? "HINTS ANY" : "HINTS " + hl, 120, 24, { size: 9, align: "center", a: 0.85 });

      var focus = s.focus, ln = linesOn && focus ? Logic.lines(s, focus, lineMode) : null;
      var wrong = {};
      if (s.mistakesNow || s.checking) Logic.wrongCells(s).forEach(function (w) { wrong[w] = true; });
      var hintCells = {};
      if (s.hint) (s.hint.cells || []).forEach(function (h) { hintCells[h] = true; });

      // backgrounds: the number lines' shade first (one even shade per covered cell), then the rest on top
      if (ln) for (i = 0; i < 81; i++) if (ln.covered[i] && s.vals[i] !== focus && !(g.kind === "lcd" && s.vals[i])) shade(g, i);
      for (i = 0; i < 81; i++) {
        r = Math.floor(i / 9); c = i % 9; x = GX + c * CS; y = GY + r * CS;
        // the selected cell's own row, column and box get a faint tint, following the same kind of lines (none with None)
        if (s.sel >= 0 && i !== s.sel && !(ln && ln.covered[i]) && Logic.linked(s.sel, i, lineMode) && !lowres)
          g.rect(x, y, CS, CS, 8, { a: 0.35, r: 0, solid: true });
        if (hintCells[i]) g.rect(x, y, CS, CS, 3, { a: 0.4, r: 0, solid: fillSolid });
        if (wrong[i] && !lowres) g.rect(x + 1, y + 1, CS - 2, CS - 2, 1, { a: 0.28, r: 2, solid: fillSolid });
        if (focus && s.vals[i] === focus) g.rect(x + 1, y + 1, CS - 2, CS - 2, 3, { a: 0.7, r: 2, solid: fillSolid });
      }
      if (s.sel >= 0) {
        x = GX + (s.sel % 9) * CS; y = GY + Math.floor(s.sel / 9) * CS;
        g.rect(x + 1, y + 1, CS - 2, CS - 2, 5, { a: 0.32, r: 2, solid: fillSolid });
      }
      // grid lines
      for (i = 0; i <= 9; i++) {
        var thick = i % 3 === 0;
        g.line(GX, GY + i * CS, GX + GW, GY + i * CS, thick ? 0 : 8, thick ? 2 : 1, { a: thick ? 1 : 0.9, cap: "butt" });
        g.line(GX + i * CS, GY, GX + i * CS, GY + GW, thick ? 0 : 8, thick ? 2 : 1, { a: thick ? 1 : 0.9, cap: "butt" });
      }
      // numbers and notes
      for (i = 0; i < 81; i++) {
        r = Math.floor(i / 9); c = i % 9; x = GX + c * CS; y = GY + r * CS;
        if (s.vals[i]) {
          var bad = wrong[i];
          var inkOnInk = g.kind === "lcd" && focus && s.vals[i] === focus;      // the LCD highlight is solid ink: the digit goes light
          g.text(String(s.vals[i]), x + CS / 2, y + CS / 2 + 1, { size: 16, align: "center", base: "middle", ci: inkOnInk ? 9 : bad ? 1 : s.fixed[i] ? 0 : 5 });
          if (bad) g.poly([[x + CS - 8, y + 1], [x + CS - 1, y + 1], [x + CS - 1, y + 8]], 1, { solid: true });
        } else if (s.notes[i]) {
          for (var d = 1; d <= 9; d++) {
            if (!(s.notes[i] >> (d - 1) & 1)) continue;
            var nx = x + ((d - 1) % 3 + 0.5) * CS / 3, ny = y + (Math.floor((d - 1) / 3) + 0.5) * CS / 3;
            if (lowres) g.rect(nx - 0.6, ny - 0.6, 1.4, 1.4, 0, { solid: true });
            else g.text(String(d), nx, ny + 0.5, { size: 7, align: "center", base: "middle", ci: focus === d ? 5 : 0, a: focus === d ? 1 : 0.62 });
          }
        }
      }
      if (s.sel >= 0) {
        x = GX + (s.sel % 9) * CS; y = GY + Math.floor(s.sel / 9) * CS;
        g.line(x + 1, y + 1, x + CS - 1, y + 1, 5, 2); g.line(x + 1, y + CS - 1, x + CS - 1, y + CS - 1, 5, 2);
        g.line(x + 1, y + 1, x + 1, y + CS - 1, 5, 2); g.line(x + CS - 1, y + 1, x + CS - 1, y + CS - 1, 5, 2);
      }
      if (s.hint) {
        x = GX + (s.hint.cell % 9) * CS; y = GY + Math.floor(s.hint.cell / 9) * CS;
        g.line(x, y, x + CS, y, 3, 2.5); g.line(x, y + CS, x + CS, y + CS, 3, 2.5);
        g.line(x, y, x, y + CS, 3, 2.5); g.line(x + CS, y, x + CS, y + CS, 3, 2.5);
      }

      // the line under the board: the message, or what is on
      var text = s.message, lines, small = lowres ? (g.kind === "pixel" ? 25 : 34) : 37;
      if (!text) {
        var bits = [];
        if (s.notesOn) bits.push("Notes on");
        if (s.mistakesNow && s.mistakes) bits.push(s.mistakes + (s.mistakes === 1 ? " mistake" : " mistakes"));
        if (linesOn && focus) bits.push("Number lines: " + focus);
        text = bits.join(" · ");
      }
      if (s.hint) text = text + "  Tap Hint again to fill it in.";
      lines = wrap(text, small, 4);
      if (lines.length >= 4 && lines[3].slice(-1) === "…") lines = wrap(text, small + 2, 4);        // a little wider rather than cut off
      g.rect(GX, MSG_Y, GW, 46, surface, { r: 4, stroke: true, a: 0.6 });
      for (i = 0; i < lines.length; i++) g.text(lines[i], GX + 5, MSG_Y + 11 + i * (lowres ? 10 : 10.5), { size: lowres ? 9 : 9, a: 0.95 });
      if (s.won) {
        var lcd = g.kind === "lcd", tc = lcd ? 9 : 0;
        if (lcd) g.rect(30, 110, 180, 52, 0, { solid: true }); else g.rect(30, 110, 180, 52, surface, { r: 6, stroke: true, a: 0.97 });
        g.text("SOLVED!", 120, 132, { size: 16, align: "center", ci: tc });
        g.text(Logic.clock(Logic.effective(s)), 120, 150, { size: 11, align: "center", ci: tc });
      }
    }

    /** One cell of the number lines' shade: the look's accent at low strength, filled once with no outline and
        no glow. Retro LCD has only ink and no ink, so its empty covered cells get a sparse dither (one dot in
        sixteen) instead, which keeps the digits clear. */
    function shade(g, i) {
      var x = GX + (i % 9) * CS, y = GY + Math.floor(i / 9) * CS;
      if (g.kind === "lcd") {
        var k = g.k, x0 = Math.ceil(x * k) + 1, y0 = Math.ceil(y * k) + 1, x1 = Math.floor((x + CS) * k) - 1, y1 = Math.floor((y + CS) * k) - 1, px, py;
        for (py = y0; py < y1; py += 3) for (px = x0 + ((py - y0) / 3 % 2) * 2; px < x1; px += 4) g.rect(px / k, py / k, 1 / k, 1 / k, 0, { solid: true });
        return;
      }
      if (g.lowres) { g.rect(x, y, CS, CS, SHADE_CI, { a: 0.24, solid: true }); return; }
      g.ctx.fillStyle = g.col(SHADE_CI, g.kind === "neon" ? 0.08 : g.kind === "contrast" ? 0.3 : 0.2);
      g.ctx.fillRect(x, y, CS, CS);
    }

    function wrap(text, width, maxLines) {
      var words = String(text).split(" "), out = [], cur = "";
      for (var i = 0; i < words.length; i++) {
        var w = words[i];
        if (!w) continue;
        if ((cur + " " + w).trim().length > width && cur) { out.push(cur); cur = w; } else cur = (cur + " " + w).trim();
      }
      if (cur) out.push(cur);
      if (out.length > maxLines) { out = out.slice(0, maxLines); out[maxLines - 1] = out[maxLines - 1].slice(0, width - 1) + "…"; }
      return out;
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "sudoku",
    name: "Sudoku",
    modes: MODES,
    defaultMode: "easy",
    controls: "buttons",
    buttons: BUTTONS,
    options: OPTIONS,
    typed: true,
    help: "Fill the grid so every row, column and 3 × 3 box has 1–9 once. Tap a cell, then a number (keyboard: arrows and 1–9). Notes writes small pencil marks; Fill notes writes every possible number; placing a number clears it from the notes around it. Number lines (start screen: rows, columns and boxes, rows and columns only, or none) show where a number can still go. Hint explains a move first, tap it again to fill it in (+30 s). Keyboard: N notes, H hint, U undo, Backspace erase. Esc pauses.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
