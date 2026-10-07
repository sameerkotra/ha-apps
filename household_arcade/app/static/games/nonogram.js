/* Household Arcade — Picture Logic (nonograms): input and drawing around the rules in nonogram-logic.js.
   Registers itself as "nonogram" with window.ArcadeGames.

   The numbers sit left of the rows and above the columns; a line whose filled squares already match its numbers
   has them dimmed. A tap fills a square (or crosses it in cross mode) and dragging carries on along that row or
   column; tapping it again empties it. The layout is worked out from the numbers and the look's font (the coarse
   looks' pixel font is wider), so the grid is as big as the room allows. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.NonogramLogic;

  var MODES = [{ id: "five", label: "5 × 5 (gentle)" }, { id: "eight", label: "8 × 8" }, { id: "ten", label: "10 × 10" },
    { id: "fifteen", label: "15 × 15" }];
  var AX = 4, AY = 38, AW = 232, AH = 226, MSG_Y = 270;
  var FILL = Logic.FILL, CROSS = Logic.CROSS;

  /** Where everything goes for a puzzle in a look: { cs, gx, gy, digitW, lineH, size } */
  function layout(s, kind) {
    var lowres = kind === "lcd" || kind === "pixel";
    var digitW = kind === "pixel" ? 8 : kind === "lcd" ? 6 : 5.6, gapW = lowres ? 4 : 3.4, lineH = kind === "pixel" ? 12 : kind === "lcd" ? 9 : 9;
    function textW(clue) {
      if (!clue.length) return digitW;
      var w = 0;
      clue.forEach(function (v, i) { w += String(v).length * digitW + (i ? gapW : 0); });
      return w;
    }
    var rowW = 0, colN = 1;
    s.rows.forEach(function (c) { rowW = Math.max(rowW, textW(c)); });
    s.cols.forEach(function (c) { colN = Math.max(colN, c.length); });
    var colH = colN * lineH;
    var cs = Math.floor(Math.min((AW - rowW - 4) / s.n, (AH - colH - 4) / s.n));
    cs = Math.max(6, Math.min(36, cs));
    var total = rowW + 4 + cs * s.n, gx = AX + Math.floor((AW - total) / 2) + rowW + 4;
    var totalH = colH + 4 + cs * s.n, gy = AY + Math.floor((AH - totalH) / 2) + colH + 4;
    return { cs: cs, gx: gx, gy: gy, digitW: digitW, gapW: gapW, lineH: lineH, rowW: rowW, colH: colH };
  }

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [], lay = null, pressing = false;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function cellAt(x, y) {
      if (!lay) return -1;
      var c = Math.floor((x - lay.gx) / lay.cs), r = Math.floor((y - lay.gy) / lay.cs);
      return c < 0 || r < 0 || c >= s.n || r >= s.n ? -1 : r * s.n + c;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, mistakes: o.mistakes }); pending = []; lay = layout(s, "modern"); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; lay = layout(s, "modern"); },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.scoreOf(s); },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      status: function () { return Logic.status(s); },
      sounds: { fill: "turn", cross: "bounce", clear: "turn", wrong: "wall", hint: "powerup", nohint: "wall", undo: "turn", mode: "powerup", check: "wall", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        var i = cellAt(lx, ly);
        if (kind === "down") { pressing = i >= 0; if (i >= 0) queue(Logic.tap(s, i)); }
        else if (kind === "move" && pressing) { if (i >= 0) queue(Logic.drag(s, i)); }
        else if (kind === "up") { pressing = false; queue(Logic.endDrag(s)); }
      },
      draw: draw,
    };

    function cross(g, x, y, cs, ci, a) {
      var p = Math.max(2, cs * 0.25);
      g.line(x + p, y + p, x + cs - p, y + cs - p, ci, cs < 12 ? 1 : 1.6, { a: a });
      g.line(x + cs - p, y + p, x + p, y + cs - p, ci, cs < 12 ? 1 : 1.6, { a: a });
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", lowres = g.lowres, n = s.n, i, r, c, x, y;
      lay = layout(s, g.kind);
      var cs = lay.cs, gx = lay.gx, gy = lay.gy, W = cs * n;
      var hl = Logic.hintsLeft(s);
      g.hud((Logic.LABELS[s.mode] || "").toUpperCase(), Logic.clock(Logic.effective(s)));
      g.text("HINTS " + hl, 120, 24, { size: 9, align: "center", a: 0.85 });

      // the cursor's row and column, softly
      var sr = Math.floor(s.sel / n), sc = s.sel % n, playing = info.state === "running" || info.state === "paused";
      var ctx = g.ctx;
      if (!lowres && playing) {         // straight fills, so no look adds an edge or a glow to the band
        ctx.fillStyle = g.col(7, g.kind === "neon" ? 0.07 : 0.14);
        ctx.fillRect(gx - lay.rowW - 4, gy + sr * cs, lay.rowW + 4 + W, cs);
        ctx.fillRect(gx + sc * cs, gy - lay.colH - 4, cs, lay.colH + 4 + W);
      }
      // every other column's numbers on a faint stripe, so neighbouring numbers never run together
      if (!(g.kind === "lcd")) {
        ctx.fillStyle = g.col(8, g.kind === "pixel" ? 0.55 : g.kind === "neon" ? 0.3 : 0.35);
        for (c = 1; c < n; c += 2) ctx.fillRect(gx + c * cs, gy - lay.colH - 4, cs, lay.colH + 2);
        for (r = 1; r < n; r += 2) ctx.fillRect(gx - lay.rowW - 4, gy + r * cs, lay.rowW + 2, cs);
      }
      // the numbers
      var fs = lowres ? 8 : cs >= 14 ? 9 : 8;
      for (r = 0; r < n; r++) {
        var done = Logic.lineDone(s, "r", r), clue = s.rows[r], tx = gx - 4;
        var parts = clue.length ? clue.slice() : [0];
        // a finished line's numbers are dimmed; Retro LCD can't dim (no grey), so they are shown light on ink instead
        if (done && lcd) g.rect(gx - lay.rowW - 3, gy + r * cs + 1, lay.rowW + 1, cs - 2, 0, { solid: true });
        for (i = parts.length - 1; i >= 0; i--) {
          g.text(String(parts[i]), tx, gy + r * cs + cs / 2 + 0.5, { size: fs, align: "right", base: "middle", a: done && !lcd ? 0.35 : 1, ci: done ? (lcd ? 9 : 0) : (r === sr && !lowres ? 5 : 0) });
          tx -= String(parts[i]).length * lay.digitW + lay.gapW;
        }
      }
      for (c = 0; c < n; c++) {
        var cdone = Logic.lineDone(s, "c", c), cc = s.cols[c].length ? s.cols[c] : [0];
        if (cdone && lcd) g.rect(gx + c * cs + 1, gy - lay.colH - 3, cs - 2, lay.colH + 1, 0, { solid: true });
        for (i = 0; i < cc.length; i++) {
          var ty = gy - 4 - (cc.length - 1 - i) * lay.lineH - lay.lineH / 2;
          g.text(String(cc[i]), gx + c * cs + cs / 2, ty, { size: fs, align: "center", base: "middle", a: cdone && !lcd ? 0.35 : 1, ci: cdone ? (lcd ? 9 : 0) : (c === sc && !lowres ? 5 : 0) });
        }
      }
      // the squares
      var wrong = {};
      if (s.checking) Logic.wrongCells(s).forEach(function (w) { wrong[w] = true; });
      if (!lcd && g.kind !== "neon") { ctx.fillStyle = g.col(9, g.kind === "pixel" ? 0.08 : 0.9); ctx.fillRect(gx, gy, W, W); }
      for (i = 0; i < n * n; i++) {
        x = gx + (i % n) * cs; y = gy + Math.floor(i / n) * cs;
        var v = s.cells[i];
        if (v === FILL) {
          var ci = s.won ? 5 : wrong[i] ? 1 : 0;
          g.rect(x + 1, y + 1, cs - 1, cs - 1, ci, { r: 0, solid: true, edge: false });
          if (s.fixed[i] && !s.won && cs >= 10 && !lcd) g.circle(x + cs / 2, y + cs / 2, Math.max(1, cs * 0.12), 9, { solid: true, a: 0.7 });     // a given / revealed square
          if (wrong[i] && !lowres) cross(g, x, y, cs, 9, 0.9);
        } else if (v === CROSS && !s.won) {
          var recent = s.wrongAt[i] >= 0 && s.updates - s.wrongAt[i] < 90;
          cross(g, x, y, cs, recent ? 1 : lcd ? 0 : 8, lcd ? 1 : recent ? 1 : 0.95);
        }
      }
      // grid lines: thin, and thicker every five
      for (i = 0; i <= n; i++) {
        var thick = i % 5 === 0 || i === n;
        g.line(gx, gy + i * cs, gx + W, gy + i * cs, thick ? 0 : 8, thick ? 1.5 : 1, { a: thick ? 1 : 0.9, cap: "butt" });
        g.line(gx + i * cs, gy, gx + i * cs, gy + W, thick ? 0 : 8, thick ? 1.5 : 1, { a: thick ? 1 : 0.9, cap: "butt" });
      }
      if (playing && !s.won) {
        x = gx + sc * cs; y = gy + sr * cs;
        var w2 = cs >= 12 ? 2 : 1;
        g.line(x, y, x + cs, y, lcd ? 0 : 5, w2, { cap: "butt" }); g.line(x, y + cs, x + cs, y + cs, lcd ? 0 : 5, w2, { cap: "butt" });
        g.line(x, y, x, y + cs, lcd ? 0 : 5, w2, { cap: "butt" }); g.line(x + cs, y, x + cs, y + cs, lcd ? 0 : 5, w2, { cap: "butt" });
      }
      if (s.hint >= 0 && !s.won) {
        x = gx + (s.hint % n) * cs; y = gy + Math.floor(s.hint / n) * cs;
        g.line(x - 1, y - 1, x + cs + 1, y - 1, 3, 2); g.line(x - 1, y + cs + 1, x + cs + 1, y + cs + 1, 3, 2);
        g.line(x - 1, y - 1, x - 1, y + cs + 1, 3, 2); g.line(x + cs + 1, y - 1, x + cs + 1, y + cs + 1, 3, 2);
      }

      // the line under the grid: the tap mode, then a message
      var modeText = s.crossMode ? "TAP: ✕ CROSS" : "TAP: ■ FILL";
      if (lowres) modeText = s.crossMode ? "TAP: CROSS" : "TAP: FILL";
      var ct = Logic.counts(s);
      g.text(modeText, 8, MSG_Y + 2, { size: 10 });
      g.text(ct.filled + "/" + ct.need, 232, MSG_Y + 2, { size: 10, align: "right", a: 0.85 });
      var msg = s.message || (s.won ? "" : s.mistakesNow ? "Mistake +10 s · Hint +30 s" : "Mistakes show when it's full");
      var lines = wrap(msg, g.kind === "pixel" ? 28 : lcd ? 38 : 54);
      for (i = 0; i < Math.min(2, lines.length); i++) g.text(lines[i], 120, MSG_Y + 16 + i * 10, { size: 8, align: "center", a: 0.85 });
      if (s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 118, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("SOLVED!", 120, 137, { size: 16, align: "center", ci: lcd ? 9 : 0 });
        g.text(Logic.clock(Logic.effective(s)), 120, 154, { size: 10, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  function wrap(text, width) {
    var words = String(text).split(" "), out = [], cur = "";
    for (var i = 0; i < words.length; i++) {
      if (!words[i]) continue;
      if ((cur + " " + words[i]).trim().length > width && cur) { out.push(cur); cur = words[i]; } else cur = (cur + " " + words[i]).trim();
    }
    if (cur) out.push(cur);
    return out;
  }

  root.ArcadeGames.register({
    id: "nonogram",
    name: "Picture Logic",
    modes: MODES,
    defaultMode: "ten",
    controls: "touch",
    buttons: [{ action: "notes", label: "■ / ✕", aria: "Switch the tap between fill and cross" },
      { action: "hint", label: "💡 Hint", aria: "Hint" }, { action: "undo", label: "↶ Undo", aria: "Undo" }],
    options: [{ id: "mistakes", label: "Mistakes", default: "now",
      choices: [{ id: "now", label: "Shown at once (+10 s each)" }, { id: "end", label: "Shown when the grid is full" }] }],
    typed: true,
    help: "Fill squares to find the hidden picture. The numbers beside each row and above each column are its runs of filled squares, in order (3 1: three filled, a gap, then one). Tap to fill, tap again to empty, drag to fill a line; ■ / ✕ switches the tap to crosses — your notes for squares that stay empty. Numbers dim when their line is right. Hint puts one square right (+30 s, 3 a puzzle). Keyboard: arrows, Space fills, X crosses, M switches, H hint, U undo. 5 × 5 is the gentle one.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
