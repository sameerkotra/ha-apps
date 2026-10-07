/* Household Arcade — Code Breaker: input and drawing around the rules in codebreak-logic.js.
   Registers itself as "codebreak" with window.ArcadeGames.

   Played on the canvas: the rows of guesses on top, the symbol keys and Delete / Check below (a real keyboard works
   too: 1 … 8, Enter, Backspace). Every symbol has a number, a colour and its own shape (circle, square, triangle,
   diamond, star, hexagon, plus, a triangle pointing down), so colours never have to be told apart; the coarse looks
   show the number in a box. Feedback: a solid dot for right symbol, right place, a hollow ring for right symbol,
   wrong place. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.CodeBreakLogic;

  var MODES = [{ id: "little", label: "Little (3 of 4, gentle)" }, { id: "classic", label: "Classic (4 of 6)" },
    { id: "norepeat", label: "No repeats (4 of 6)" }, { id: "master", label: "Master (5 of 8)" }];
  var LABELS = { little: "LITTLE", classic: "CLASSIC", norepeat: "NO REPEATS", master: "MASTER" };
  var CI = [0, 1, 5, 3, 4, 6, 2, 7, 0];                 // colour of symbol 1 … 8 (8 is ink: its shape tells it apart)
  var ROWS_TOP = 40, ROWS_BOTTOM = 218, PAL_Y = 226, PAL_H = 28, BTN_Y = 262, BTN_H = 30;

  function shape(g, c, cx, cy, r, ci, o) {
    o = o || { solid: true };
    var k = r * 0.92;
    switch (c) {
      case 1: g.circle(cx, cy, r, ci, o); break;
      case 2: g.rect(cx - k, cy - k, 2 * k, 2 * k, ci, Object.assign({ r: 1 }, o)); break;
      case 3: g.poly([[cx, cy - r * 1.05], [cx + r * 1.05, cy + r * 0.85], [cx - r * 1.05, cy + r * 0.85]], ci, o); break;
      case 4: g.poly([[cx, cy - r * 1.15], [cx + r, cy], [cx, cy + r * 1.15], [cx - r, cy]], ci, o); break;
      case 5: g.star(cx, cy, r * 1.2, ci, o); break;
      case 6: {
        var h = r * 0.87;
        g.poly([[cx - r * 0.5, cy - h], [cx + r * 0.5, cy - h], [cx + r, cy], [cx + r * 0.5, cy + h], [cx - r * 0.5, cy + h], [cx - r, cy]], ci, o); break;
      }
      case 7: {
        var t = r * 0.38;
        g.poly([[cx - t, cy - r], [cx + t, cy - r], [cx + t, cy - t], [cx + r, cy - t], [cx + r, cy + t], [cx + t, cy + t], [cx + t, cy + r],
          [cx - t, cy + r], [cx - t, cy + t], [cx - r, cy + t], [cx - r, cy - t], [cx - t, cy - t]], ci, o); break;
      }
      default: g.poly([[cx - r * 1.05, cy - r * 0.85], [cx + r * 1.05, cy - r * 0.85], [cx, cy + r * 1.05]], ci, o);
    }
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [];

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function geom() {
      var pitch = Math.min(20, Math.floor((ROWS_BOTTOM - ROWS_TOP) / s.maxRows)), gap = Math.min(28, 150 / s.pegs);
      var keyW = Math.min(30, Math.floor(232 / s.colours) - 2);
      return { pitch: pitch, r: Math.min(7.5, pitch * 0.4), gap: gap, px: 34, fx: 196, keyW: keyW,
        keyX: 120 - (s.colours * (keyW + 2) - 2) / 2 };
    }
    function hit(x, y) {
      var m = geom();
      if (y >= PAL_Y && y <= PAL_Y + PAL_H) {
        var k = Math.floor((x - m.keyX) / (m.keyW + 2));
        if (k >= 0 && k < s.colours) return { key: k + 1 };
      }
      if (y >= BTN_Y && y <= BTN_Y + BTN_H) { if (x >= 12 && x <= 108) return { del: true }; if (x >= 120 && x <= 228) return { check: true }; }
      var row = s.rows.length, ry = ROWS_TOP + row * m.pitch;
      if (y >= ry - 2 && y <= ry + m.pitch + 2) {
        var slot = Math.floor((x - m.px + m.gap / 2) / m.gap);
        if (slot >= 0 && slot < s.pegs) return { slot: slot };
      }
      return null;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.scoreOf(s); },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      status: function () { return Logic.status(s); },
      sounds: { place: "turn", clear: "turn", slot: "turn", refuse: "wall", guess: "bounce", win: "win", lose: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var h = hit(lx, ly);
        if (!h) return;
        if (h.key) queue(Logic.place(s, h.key));
        else if (h.del) queue(Logic.clear(s));
        else if (h.check) queue(Logic.check(s));
        else if (h.slot >= 0) queue(Logic.clear(s, h.slot));
      },
      draw: draw,
    };

    function peg(g, c, cx, cy, r) {
      if (g.lowres) {
        var lcd = g.kind === "lcd";
        if (lcd) g.rect(cx - r, cy - r, 2 * r, 2 * r, 9, {}); else g.rect(cx - r, cy - r, 2 * r, 2 * r, CI[c], { solid: true });
        g.text(String(c), cx, cy + 0.5, { size: 8, align: "center", base: "middle", ci: lcd ? 0 : pegInk(g, c) });
        return;
      }
      shape(g, c, cx, cy, r, CI[c], { solid: true, stroke: true });
      if (r >= 6.5) g.text(String(c), cx, cy + (c === 3 ? 1.5 : c === 8 ? -1 : 0.5), { size: Math.max(7, r * 1.05), align: "center", base: "middle", ci: pegInk(g, c) });
    }
    /** The number's colour on a symbol: dark on the light fills (yellow, cyan, green, and the ink-coloured 8 where ink
        is light), light on the others. */
    function pegInk(g, c) {
      if (g.kind === "neon" || g.kind === "paper") return 0;
      var dark = g.kind === "pixel" ? 8 : g.kind === "contrast" ? 9 : g.dark ? 9 : 0;
      var light = g.kind === "pixel" ? 9 : g.kind === "contrast" ? 0 : g.dark ? 0 : 9;
      var lightFill = c === 3 || c === 7 || c === 5 ? true : c === 8 ? g.kind !== "modern" || g.dark : false;
      return lightFill ? dark : light;
    }
    function hole(g, cx, cy, r, sel) {
      if (g.lowres) { g.rect(cx - r, cy - r, 2 * r, 2 * r, sel ? 0 : 8, {}); return; }
      g.ring(cx, cy, r, sel ? 5 : 8, sel ? 2 : 1.2, { a: sel ? 1 : 0.9 });
    }
    function marks(g, black, white, x, y, pitch) {
      var n = black + white, i;
      if (g.lowres) {           // the coarse looks: one row of square marks, solid or hollow (1 dot wide), whole dots apart
        var slot = g.kind === "pixel" ? 8 : 9, m = 6, my = g.snap(y + pitch / 2 - m / 2), lw = 1 / g.k;
        for (i = 0; i < s.pegs; i++) {
          var mx = g.snap(x - 4 + i * slot);
          if (i < black) g.rect(mx, my, m, m, 0, { solid: true });
          else if (i < n) {
            g.rect(mx, my, m, lw, 0, { solid: true }); g.rect(mx, my + m - lw, m, lw, 0, { solid: true });
            g.rect(mx, my, lw, m, 0, { solid: true }); g.rect(mx + m - lw, my, lw, m, 0, { solid: true });
          }
        }
        return;
      }
      var per = Math.ceil(s.pegs / 2), d = Math.min(7, (pitch - 2) / 2);
      for (i = 0; i < s.pegs; i++) {
        var cx = x + (i % per) * d + d / 2, cy = y + Math.floor(i / per) * d + d / 2 + 1, rr = d * 0.36;
        if (i < black) g.circle(cx, cy, rr, 0, { solid: true });
        else if (i < n) g.ring(cx, cy, rr * 0.9, 0, 1.2);
        else g.circle(cx, cy, 1, 8, { solid: true });
      }
    }

    function draw(g, info) {
      var m = geom(), lcd = g.kind === "lcd", i, j, y;
      g.hud(LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      g.text("ROW " + Math.min(s.rows.length + 1, s.maxRows) + "/" + s.maxRows, 120, 24, { size: 9, align: "center", a: 0.85 });
      for (i = 0; i < s.maxRows; i++) {
        y = ROWS_TOP + i * m.pitch;
        var cy = y + m.pitch / 2, current = i === s.rows.length && !s.over;
        if (current && !lcd) { g.ctx.fillStyle = g.col(7, g.kind === "neon" ? 0.08 : 0.16); g.ctx.fillRect(6, y, 228, m.pitch); }     // a plain band: no edge, no glow
        g.text(String(i + 1), 14, cy + 0.5, { size: 8, align: "center", base: "middle", a: current ? 1 : 0.55 });
        for (j = 0; j < s.pegs; j++) {
          var cx = m.px + j * m.gap;
          if (i < s.rows.length) peg(g, s.rows[i].guess[j], cx, cy, m.r);
          else if (current && s.cur[j]) { peg(g, s.cur[j], cx, cy, m.r); if (j === s.slot) hole(g, cx, cy, m.r + 2, true); }
          else hole(g, cx, cy, Math.min(m.r, 5), current && j === s.slot);
        }
        if (i < s.rows.length) marks(g, s.rows[i].black, s.rows[i].white, m.fx, y, m.pitch);
      }
      // the keys: one per symbol, then Delete and Check
      for (i = 0; i < s.colours; i++) {
        var kx = m.keyX + i * (m.keyW + 2), sel = i === s.pal && !s.over;
        g.rect(kx, PAL_Y, m.keyW, PAL_H, sel ? 5 : 8, { r: 4, a: sel ? 0.35 : lcd ? 1 : 0.5, solid: !lcd, edge: false });
        if (sel && lcd) g.rect(kx + 1, PAL_Y + 1, m.keyW - 2, PAL_H - 2, 9, {});
        peg(g, i + 1, kx + m.keyW / 2, PAL_Y + PAL_H / 2, Math.min(9, m.keyW / 2 - 3));
      }
      button(g, 12, BTN_Y, 96, BTN_H, g.lowres ? "DEL" : "⌫ Delete", false);
      var full = s.cur.every(function (c) { return c > 0; });
      button(g, 120, BTN_Y, 108, BTN_H, g.lowres ? "CHECK" : "Check ✓", full);
      if (s.message) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(20, 120, 200, 26, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text(s.message, 120, 134, { size: 9, align: "center", base: "middle", ci: lcd ? 9 : 0 });
      }
      if (s.over) {
        var dm = g.kind === "pixel" || g.kind === "neon", tc = lcd ? 9 : 0;
        g.rect(30, 96, 180, 64, lcd ? 0 : dm ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.97 });
        g.text(s.won ? "CRACKED!" : "THE CODE WAS", 120, 113, { size: s.won ? 15 : 10, align: "center", ci: tc });
        for (j = 0; j < s.pegs; j++) {
          var ox = 120 - (s.pegs - 1) * 12 + j * 24;
          if (lcd) { g.rect(ox - 8, 125, 16, 16, 9, { solid: true }); g.text(String(s.code[j]), ox, 133.5, { size: 9, align: "center", base: "middle", ci: 0 }); }
          else peg(g, s.code[j], ox, 134, 8);
        }
        if (s.won) g.text(s.rows.length + (s.rows.length === 1 ? " ROW" : " ROWS"), 120, 153, { size: 9, align: "center", ci: tc });
      }
    }
    function button(g, x, y, w, h, label, strong) {
      var lcd = g.kind === "lcd";
      if (lcd) { g.rect(x, y, w, h, strong ? 0 : 9, strong ? { solid: true } : {}); }
      else g.rect(x, y, w, h, strong ? 5 : 8, { r: 5, solid: true, a: strong ? 0.9 : 0.6, edge: false });
      g.text(label, x + w / 2, y + h / 2 + 1, { size: 11, align: "center", base: "middle", ci: lcd ? (strong ? 9 : 0) : strong && g.kind !== "neon" && g.kind !== "paper" ? 9 : 0 });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "codebreak",
    name: "Code Breaker",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    typed: true,
    help: "Find the hidden code. Tap symbols (or type their numbers) to fill a row, then Check (Enter). A solid dot means one symbol is right and in the right place; a hollow ring, one is in the code but somewhere else. Tap a placed symbol (or Backspace) to take it out. Every symbol has a number and a shape as well as a colour. Fewer rows score more, then a faster time. Little (3 symbols of 4, no repeats) is the gentle one; Master is 5 of 8 with repeats. Controller: ← → pick, A places, ↑ checks, ↓ deletes.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
