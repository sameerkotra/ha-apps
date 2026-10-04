/* Household Arcade — Word Guess: input and drawing around the rules in wordguess-logic.js.
   Registers itself as "wordguess" with window.ArcadeGames.

   Played on the canvas: the guess grid on top and an on-screen QWERTY keyboard below (a touch or click on a key
   types it); a real keyboard works too (the shell sends letters, Enter and Backspace to games marked `typed`).
   Colours are blue (right place) and orange (elsewhere) — a pair that stays apart for colour-blind eyes — and
   every tile also carries a mark: a dot for "right place", a diamond for "elsewhere"; letters that aren't in the
   word are dimmed. The Retro LCD look uses a solid tile, an outlined tile with a diamond, and a plain outline. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.WordGuessLogic;

  var MODES = [{ id: "classic", label: "Six tries" }, { id: "easy", label: "Eight tries" }, { id: "strict", label: "Strict (use every clue)" }];

  // layout on the 240 × 300 playfield
  var GRID_TOP = 38, GRID_BOTTOM = 200, MSG_Y = 208, KEY_TOP = 222, KEY_PITCH = 25, KEY_H = 22, UNIT = 22.8, KEY_LEFT = 6;

  function keyRects() {
    var out = [], rows = Logic.ROWS, r, i;
    for (r = 0; r < rows.length; r++) {
      var letters = rows[r].split(""), keys = letters.map(function (c) { return { label: c, w: 1 }; });
      if (r === 2) { keys.unshift({ label: "ENTER", w: 1.6 }); keys.push({ label: "BACK", w: 1.6 }); }
      var total = keys.reduce(function (a, k) { return a + k.w; }, 0), x = KEY_LEFT + (10.2 - total) * UNIT / 2;
      for (i = 0; i < keys.length; i++) {
        out.push({ label: keys[i].label, x: x, y: KEY_TOP + r * KEY_PITCH, w: keys[i].w * UNIT - 2, h: KEY_H });
        x += keys[i].w * UNIT;
      }
    }
    return out;
  }
  var KEYS = keyRects();

  // An outline without a fill. (On the coarse looks a stroked rect is filled in, which would hide the letter.)
  function frame(g, x, y, w, h, ci, a, wd) {
    wd = wd || 1.2;
    if (!g.lowres) { g.rect(x, y, w, h, ci, { r: 3, stroke: true, a: a }); return; }
    g.line(x, y, x + w, y, ci, wd, { a: a, cap: "butt" }); g.line(x, y + h, x + w, y + h, ci, wd, { a: a, cap: "butt" });
    g.line(x, y, x, y + h, ci, wd, { a: a, cap: "butt" }); g.line(x + w, y, x + w, y + h, ci, wd, { a: a, cap: "butt" });
  }
  function lines(g, x, y, w, h, ci, wd) {
    g.line(x, y, x + w, y, ci, wd, { cap: "butt" }); g.line(x, y + h, x + w, y + h, ci, wd, { cap: "butt" });
    g.line(x, y, x, y + h, ci, wd, { cap: "butt" }); g.line(x + w, y, x + w, y + h, ci, wd, { cap: "butt" });
  }
  // A panel for a few words: light with dark text, or on the monochrome screen dark with light text. Returns the text colour.
  function panel(g, x, y, w, h) {
    if (g.kind === "lcd") { g.rect(x, y, w, h, 0, { solid: true }); return 9; }
    var surface = g.kind === "neon" || g.kind === "pixel" ? 8 : 9;      // neon and pixel have a light "paper": use a dark panel
    g.rect(x, y, w, h, surface, { r: 6, solid: true, a: 1 });
    if (g.kind === "neon") { g.rect(x, y, w, h, surface, { r: 6, solid: true, a: 1 }); g.rect(x, y, w, h, surface, { r: 6, solid: true, a: 1 }); }
    frame(g, x, y, w, h, 0, 1, 1.5);
    return 0;
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [];

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function keyAt(x, y) {
      for (var i = 0; i < KEYS.length; i++) {
        var k = KEYS[i];
        if (x >= k.x - 1 && x <= k.x + k.w + 1 && y >= k.y - 1 && y <= k.y + k.h + 1) return k.label;
      }
      return null;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, clue: (opts.options || {}).clue }); pending = []; },
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
      sounds: { type: "turn", back: "turn", reject: "wall", guess: "bounce", win: "win", lose: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var k = keyAt(lx, ly);
        if (!k) return;
        queue(Logic.press(s, "key:" + (k === "BACK" ? "BACKSPACE" : k), true));
      },
      draw: draw,
    };

    function tile(g, x, y, size, ch, state, o) {
      var lcd = g.kind === "lcd", lowres = g.lowres, a = o && o.dim ? 0.5 : 1;
      if (state === Logic.CORRECT) {
        g.rect(x, y, size, size, 5, { r: 3, solid: true });
        g.text(ch, x + size / 2, y + size / 2 + 1, { size: size * 0.62, align: "center", base: "middle", ci: 9 });
        if (!lcd) g.circle(x + size - 4.5, y + 4.5, 1.8, 9, { solid: true });
      } else if (state === Logic.ELSEWHERE) {
        if (lcd) { frame(g, x, y, size, size, 0, 1, 1.4); frame(g, x + 2.5, y + 2.5, size - 5, size - 5, 0, 1, 1); }
        else g.rect(x, y, size, size, 2, { r: 3, solid: true });
        g.text(ch, x + size / 2, y + size / 2 + 1, { size: size * 0.62, align: "center", base: "middle", ci: 0 });
        g.poly([[x + size - 4.5, y + 2], [x + size - 2, y + 4.5], [x + size - 4.5, y + 7], [x + size - 7, y + 4.5]], 0, { solid: true });
      } else if (state === Logic.ABSENT) {
        if (lowres) frame(g, x, y, size, size, 8, 1, 1);
        else g.rect(x, y, size, size, 8, { r: 3, solid: true, a: 0.5 });
        if (lcd) g.line(x + 3, y + 3, x + size - 3, y + size - 3, 0, 1, { cap: "butt" });        // a cross: not in the word
        g.text(ch, x + size / 2, y + size / 2 + 1, { size: size * 0.62, align: "center", base: "middle", ci: 0, a: lcd ? 1 : lowres ? 0.45 : 0.6 });
      } else {      // not marked yet
        frame(g, x, y, size, size, 8, 0.85, 1);
        if (ch) lines(g, x, y, size, size, 0, 1.8);
        if (ch) g.text(ch, x + size / 2, y + size / 2 + 1, { size: size * 0.62, align: "center", base: "middle", ci: 0 });
      }
    }

    // What the colours mean, under the grid once there's a guess to read (the message line, when free).
    function legend(g) {
      var items = [[Logic.CORRECT, "right spot"], [Logic.ELSEWHERE, "in the word"], [Logic.ABSENT, "not in it"]];
      var x = 14, y = MSG_Y + 1, sz = 12, i;
      for (i = 0; i < items.length; i++) {
        tile(g, x, y, sz, "A", items[i][0]);
        g.text(items[i][1], x + sz + 4, y + sz / 2 + 1, { size: 8, base: "middle", a: 0.9 });
        x += i === 0 ? 74 : 80;
      }
    }

    function draw(g, info) {
      var mode = Logic.MODES[s.mode], rows = s.tries, pitch = Math.min(28, Math.floor((GRID_BOTTOM - GRID_TOP) / rows)), size = pitch - 3;
      var gx = 120 - (5 * pitch - 3) / 2, r, c, i;
      g.hud(mode.name.toUpperCase(), Logic.clock(Logic.seconds(s)));
      var age = s.updates - s.guessAt, revealing = age >= 0 && age < Logic.REVEAL_UPDATES;
      var shakeX = s.shake > 0 ? Math.sin(s.shake * 1.3) * 3 : 0;

      for (r = 0; r < rows; r++) {
        var word = r < s.guesses.length ? s.guesses[r] : r === s.guesses.length && !s.over ? s.cur : "";
        for (c = 0; c < 5; c++) {
          var state = -1, ch = word[c] ? word[c].toUpperCase() : "";
          if (r < s.guesses.length) {
            state = s.marks[r][c];
            if (revealing && r === s.guesses.length - 1 && age < c * 8) state = -1;
          }
          var tx = gx + c * pitch + (r === s.guesses.length ? shakeX : 0), ty = GRID_TOP + r * pitch;
          tile(g, tx, ty, size, ch, state);
          // the clue letter, faint, in its place on the row being typed until a letter covers it
          if (s.clue === c && r === s.guesses.length && !s.over && !ch)
            g.text(s.answer[c].toUpperCase(), tx + size / 2, ty + size / 2 + 1, { size: size * 0.62, align: "center", base: "middle", ci: 5, a: g.lowres ? 0.6 : 0.45 });
        }
      }

      var text = s.message;
      if (!text) text = s.strict ? "Strict: use every clue you have found." : "";
      if (s.won) text = "Found it in " + s.guesses.length + (s.guesses.length === 1 ? " try!" : " tries!");
      if (!text && s.guesses.length === 0 && !s.over) text = s.clue >= 0 ? Logic.clueText(s) : "Type any 5-letter word to start.";     // short enough for the coarse fonts; the colour key shows after it
      if (text) g.text(text, 120, MSG_Y + 8, { size: 9, align: "center", a: 0.95 });
      else legend(g);

      for (i = 0; i < KEYS.length; i++) {
        var k = KEYS[i], st = s.keys[k.label], special = k.label.length > 1;
        var shown = !(revealing && st != null && age < Logic.REVEAL_UPDATES) || special;
        var lcd = g.kind === "lcd", solid = false;
        if (st === Logic.CORRECT && shown) { g.rect(k.x, k.y, k.w, k.h, 5, { r: 3, solid: true }); solid = true; }
        else if (st === Logic.ELSEWHERE && shown) {
          if (lcd) { frame(g, k.x, k.y, k.w, k.h, 0, 1, 1.6); frame(g, k.x + 2, k.y + 2, k.w - 4, k.h - 4, 0, 1, 1); }
          else { g.rect(k.x, k.y, k.w, k.h, 2, { r: 3, solid: true }); }
        }
        else if (st === Logic.ABSENT && shown) { if (g.lowres) frame(g, k.x, k.y, k.w, k.h, 8, 0.45, 1); else g.rect(k.x, k.y, k.w, k.h, 8, { r: 3, solid: true, a: 0.35 }); }
        else frame(g, k.x, k.y, k.w, k.h, special && !g.lowres ? 5 : g.lowres ? 0 : 8, 0.95, 1);
        var label = k.label === "BACK" ? "DEL" : k.label === "ENTER" && g.lowres ? "GO" : k.label;      // the coarse fonts are wide
        var dim = st === Logic.ABSENT && shown;
        g.text(label, k.x + k.w / 2, k.y + k.h / 2 + 1, { size: special ? 7 : 10, align: "center", base: "middle", ci: solid ? 9 : 0, a: dim ? (g.lowres ? 0.3 : 0.45) : 1 });
      }
      if (s.over && !s.won) {
        var tc = panel(g, 40, 100, 160, 40);
        g.text("THE WORD WAS", 120, 116, { size: 9, align: "center", ci: tc });
        g.text(s.answer.toUpperCase(), 120, 131, { size: 14, align: "center", ci: tc });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "wordguess",
    name: "Word Guess",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    // offInRaces: the shell uses the default in a race or a daily challenge, so everyone plays the same game
    options: [{ id: "clue", label: "Clue at the start", default: "none", offInRaces: true,
      choices: [{ id: "none", label: "None" }, { id: "letter", label: "Show one letter (−500 points)" }] }],
    typed: true,
    help: "Find the secret five-letter word. Type a real word and press Enter. Blue with a dot: right letter, right place. Orange with a diamond: the letter is in the word, but somewhere else. Dim: not in the word. Strict mode makes you use every clue you've found. Clue at the start shows one letter in its place before your first guess (it costs 500 points, and is off in races and daily challenges). A faster win with fewer tries scores more. Esc pauses.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
