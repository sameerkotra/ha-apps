/* Household Arcade — Type Rain: input and drawing around the rules in typerain-logic.js.
   Registers itself as "typerain" with window.ArcadeGames.

   Words fall in the sky above the ground line; an on-screen keyboard fills the bottom of the game (a tap types the
   key; a real keyboard types too — the shell sends letters and Backspace to games marked `typed`). The picked word
   is boxed, its typed letters coloured and underlined; the key for the next letter is lit (and, in the gentle modes,
   the key for the lowest word's first letter when nothing is picked). A slip flashes the ground line, never faster
   than three times a second. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.TypeRainLogic;

  var MODES = [{ id: "letters", label: "Little ones (letters)" }, { id: "easy", label: "Easy (short words)" },
    { id: "classic", label: "Classic" }, { id: "stages", label: "Stages" }];
  var LABELS = { letters: "LETTERS", easy: "EASY", classic: "CLASSIC", stages: "STAGES" };
  var ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"];
  var KEY_TOP = 205, KEY_PITCH = 31, KEY_H = 27, UNIT = 23.2, KEY_LEFT = 4;
  var GROUND = Logic.GROUND;

  function keyRects() {
    var out = [];
    for (var r = 0; r < ROWS.length; r++) {
      var keys = ROWS[r].split("").map(function (c) { return { label: c, w: 1 }; });
      if (r === 2) keys.push({ label: "BACK", w: 1.8 });
      var total = keys.reduce(function (a, k) { return a + k.w; }, 0), x = KEY_LEFT + (10.2 - total) * UNIT / 2;
      for (var i = 0; i < keys.length; i++) {
        out.push({ label: keys[i].label, x: x, y: KEY_TOP + r * KEY_PITCH, w: keys[i].w * UNIT - 2, h: KEY_H });
        x += keys[i].w * UNIT;
      }
    }
    return out;
  }
  var KEYS = keyRects();

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], pressed = null, pressedT = 0;

    var ring = -1;          // a controller's (or the arrow keys') ring on the on-screen keyboard; -1 until first used
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    /** Move the ring to the nearest key that way (the arrow keys too: typing never needs them). */
    function moveRing(dir) {
      if (ring < 0) { ring = 0; return; }
      var k = KEYS[ring], cx = k.x + k.w / 2, cy = k.y + k.h / 2, best = -1, bd = 1e9;
      for (var i = 0; i < KEYS.length; i++) {
        if (i === ring) continue;
        var q = KEYS[i], dx = q.x + q.w / 2 - cx, dy = q.y + q.h / 2 - cy;
        var along = dir === "left" ? -dx : dir === "right" ? dx : dir === "up" ? -dy : dy, across = dir === "left" || dir === "right" ? Math.abs(dy) : Math.abs(dx);
        if (along <= 1) continue;
        var d = along + across * 3;
        if (d < bd) { bd = d; best = i; }
      }
      if (best >= 0) ring = best;
    }
    function keyAt(x, y) {
      for (var i = 0; i < KEYS.length; i++) {
        var k = KEYS[i];
        if (x >= k.x - 1 && x <= k.x + k.w + 1 && y >= k.y - 2 && y <= k.y + k.h + 2) return k.label;
      }
      return null;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; if (pressedT > 0) pressedT--; return evs; },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { key: "turn", word: "eat", slip: "wall", land: "lose", stage: "row", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        if (down && (action === "up" || action === "down" || action === "left" || action === "right")) { moveRing(action); return; }
        if (down && (action === "fire" || action === "alt") && ring >= 0) {       // a controller: A types the ringed key, X lets go
          var lab = action === "alt" ? "BACK" : KEYS[ring].label;
          impl.input("key:" + (lab === "BACK" ? "BACKSPACE" : lab), true);
          return;
        }
        if (down && action.indexOf("key:") === 0) { pressed = action.slice(4); pressedT = 8; }
        queue(Logic.press(s, action, down));
      },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var k = keyAt(lx, ly);
        if (k) impl.input("key:" + (k === "BACK" ? "BACKSPACE" : k), true);
      },
      draw: draw,
    };

    function nextKey() {
      var at = s.target >= 0 ? Logic.find(s, s.target) : -1;
      if (at >= 0) return s.words[at].text[s.words[at].typed].toUpperCase();
      if ((s.mode === "letters" || s.mode === "easy" || (s.stages && s.level <= 2)) && s.words.length) {
        var low = s.words[0];
        for (var i = 1; i < s.words.length; i++) if (s.words[i].y > low.y) low = s.words[i];
        return low.text[0].toUpperCase();
      }
      return null;
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", lowres = g.lowres, i, fmt = Kit.fmt;
      var right = s.stages ? "STAGE " + s.level + "/" + s.stages.length : "LEVEL " + s.level;
      g.hud("SCORE " + fmt(s.score), right);
      // lives: raindrops caught in the cloud
      for (i = 0; i < Logic.MODES[s.mode].lives; i++) {
        var on = i < s.lives;
        if (lowres) g.rect(10 + i * 9, 38, 5, 6, on ? 5 : 8, on ? { solid: true } : {});
        else g.poly([[13 + i * 11, 37], [17 + i * 11, 43], [13 + i * 11, 46], [9 + i * 11, 43]], on ? 5 : 8, { solid: true, a: on ? 1 : 0.5 });
      }
      if (s.run >= Logic.RUN2) g.text("RUN " + s.run + " ×" + (s.run >= Logic.RUN3 ? 3 : 2), 230, 45, { size: 9, align: "right", ci: lcd ? 0 : 4 });

      // the ground (flashes on a slip, at most 3 times a second)
      var slipNow = !info.reduce && s.updates - s.slipT < 20;
      g.line(6, GROUND + 2, 234, GROUND + 2, slipNow ? 1 : 8, slipNow ? 3 : 2, { a: lcd ? 1 : 0.9 });

      // the words: drawn whole (so every look's own letter spacing is kept), the typed part again over it in green
      // and underlined (the coarse looks: underlined); a box behind each, the picked one stronger
      var big = s.mode === "letters", size = big ? 16 : 12;
      for (i = 0; i < s.words.length; i++) {
        var w = s.words[i], y = g.snap(w.y + (info.state === "running" ? w.v * info.alpha : 0));
        var text = big || lowres ? w.text.toUpperCase() : w.text, picked = w.id === s.target;
        var tw = textWidth(g, text, size), bw = tw + Logic.PAD + 2, bh = big ? 20 : 15;
        var x = Math.max(4, Math.min(w.x, 236 - bw));
        if (lcd) { if (picked) g.rect(x, y - bh + 3, bw, bh, 0, { solid: true }); else g.rect(x, y - bh + 3, bw, bh, 9, {}); }
        else if (g.kind === "neon") {
          g.rect(x, y - bh + 3, bw, bh, 8, { r: 4, solid: true });
          if (picked) g.rect(x, y - bh + 3, bw, bh, 7, { r: 4 });
        } else g.rect(x, y - bh + 3, bw, bh, picked ? 5 : 9, { r: 4, solid: true, stroke: !picked, a: picked ? 0.3 : (g.kind === "pixel" ? 0.12 : 0.85) });
        var tx = x + (bw - tw) / 2;
        g.text(text, tx, y - 1, { size: size, ci: lcd && picked ? 9 : 0 });
        if (picked && w.typed > 0) {
          var done = text.slice(0, w.typed), dw = textWidth(g, done, size);
          if (!lowres) g.text(done, tx, y - 1, { size: size, ci: 4 });
          g.line(tx, y + 1.5, tx + dw, y + 1.5, lcd ? 9 : 4, 1.5, { cap: "butt" });
        }
      }

      // the keyboard
      var lit = nextKey();
      for (i = 0; i < KEYS.length; i++) {
        var k = KEYS[i], isLit = k.label === lit, isPressed = pressedT > 0 && (k.label === pressed || (k.label === "BACK" && pressed === "BACKSPACE"));
        if (lcd) {
          if (isLit || isPressed) g.rect(k.x, k.y, k.w, k.h, 0, { solid: true }); else g.rect(k.x, k.y, k.w, k.h, 9, {});
        } else {
          g.rect(k.x, k.y, k.w, k.h, isLit ? 4 : isPressed ? 5 : 8, { r: 4, solid: true, a: isLit ? 0.85 : isPressed ? 0.6 : g.kind === "neon" ? 0.5 : 0.55, edge: !lowres });
        }
        if (i === ring) {
          g.line(k.x - 1, k.y - 1, k.x + k.w + 1, k.y - 1, lcd ? 0 : 7, 2, { cap: "butt" }); g.line(k.x - 1, k.y + k.h + 1, k.x + k.w + 1, k.y + k.h + 1, lcd ? 0 : 7, 2, { cap: "butt" });
          g.line(k.x - 1, k.y - 1, k.x - 1, k.y + k.h + 1, lcd ? 0 : 7, 2, { cap: "butt" }); g.line(k.x + k.w + 1, k.y - 1, k.x + k.w + 1, k.y + k.h + 1, lcd ? 0 : 7, 2, { cap: "butt" });
        }
        var label = k.label === "BACK" ? (lowres ? "DEL" : "⌫") : k.label;
        g.text(label, k.x + k.w / 2, k.y + k.h / 2 + 1, { size: k.label === "BACK" && lowres ? 8 : 12, align: "center", base: "middle",
          ci: lcd ? (isLit || isPressed ? 9 : 0) : isLit && g.kind !== "neon" && g.kind !== "paper" ? 9 : 0 });
      }

      if (info.state !== "over") {
        if (s.phase === "clear") banner(g, "STAGE CLEAR!", s.stages && s.level < s.stages.length ? "NEXT: " + s.stages[s.level].name.toUpperCase() : "");
        else if (s.stages && s.spawned === 0) banner(g, s.stages[s.level - 1].name.toUpperCase(), s.stages[s.level - 1].words + " WORDS");
        else if (!s.stages && s.updates < Logic.FIRST_T + 30) banner(g, "TYPE THE WORDS", "BEFORE THEY LAND");
      }
    }
    /** The width of a word in this look's font (the coarse looks: the built-in pixel font). */
    function textWidth(g, text, size) {
      if (g.lowres) return Kit.bitmapWidth(text, Math.max(1, Math.floor(size * g.k / 6 + 0.25))) / g.k;
      g.text("", 0, 0, { size: size });                  // sets the look's font for this size
      var m = g.ctx.measureText ? g.ctx.measureText(text) : null;
      return m && m.width > 0 ? m.width : text.length * size * 0.6;
    }
    function banner(g, a, b) {
      var y = 110, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, 120, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, 120, y + 15, { size: 10, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "typerain",
    name: "Type Rain",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    typed: true,
    help: "Words fall from the sky: type each one before it lands. The first letter picks the lowest word starting with it; keep typing its letters (the next key is lit). Backspace lets go of a word. A word scores 10 a letter, doubled after 10 words in a row without a slip and tripled after 25. A word that lands costs a raindrop (a life). Type on your keyboard, or tap the keys on the game; with a controller, move the ring over the keys and press A (X lets go). Little ones has single letters; Stages is a list of stages, the last one wins.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
