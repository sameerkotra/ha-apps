/* Household Arcade — Word Search rules (pure: no DOM, deterministic for a given seed).

   Find the hidden words in the grid by marking a straight line of letters: drag across them, or tap the first
   letter and then the last. The puzzle is made from the seed: a theme (animals, food, space…), words from its list
   of the right length for the age group, placed in the allowed directions, the rest filled with letters (and
   checked so that no word appears a second time by accident; a line read backwards is the same line).
     little   7 × 7,   5 short words (3–5 letters), across and down
     kids     9 × 9,   8 words (4–6),   also diagonally down
     family   11 × 11, 10 words (5–8),  also diagonally up and backwards across and up
     puzzler  13 × 13, 12 words (6–11), every direction, backwards too
   Score = 100 a word found − 25 a hint, and for finishing a bonus of up to 900 that shrinks with the time. The
   clock is the updates the game ran (60 a second). One call to step() is one update. */
(function () {
  "use strict";

  var Words = typeof module === "object" && module.exports ? require("./wordsearch-words.js") : self.WordSearchWords;

  var UPS = 60, STATE_VERSION = 1, WORD_POINTS = 100, HINT_PENALTY = 25, BONUS = 900, HINT_UPDATES = 300, MESSAGE_UPDATES = 120;
  // the eight directions as [row step, column step]: right, down, down-right, up-right, left, up, down-left, up-left
  var DIRS = [[0, 1], [1, 0], [1, 1], [-1, 1], [0, -1], [-1, 0], [1, -1], [-1, -1]];
  var MODES = {
    little: { name: "Little ones", n: 7, words: 5, lens: [3, 5], dirs: [0, 1], rate: 10 },
    kids: { name: "Kids", n: 9, words: 8, lens: [4, 6], dirs: [0, 1, 2], rate: 6 },
    family: { name: "Everyone", n: 11, words: 10, lens: [5, 8], dirs: [0, 1, 2, 3, 4, 5], rate: 4 },
    puzzler: { name: "Puzzler", n: 13, words: 12, lens: [6, 11], dirs: [0, 1, 2, 3, 4, 5, 6, 7], rate: 3 },
  };
  var MODE_IDS = ["little", "kids", "family", "puzzler"];
  var FILL = "EEEEEEEAAAAAAIIIIIOOOOOUUTTTTNNNNSSSSRRRRLLLDDCCMMHHPPBBGGFFWWYYKVJXQZ";

  function rand(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function shuffle(list, r) {
    for (var i = list.length - 1; i > 0; i--) { var j = Math.floor(r() * (i + 1)), t = list[i]; list[i] = list[j]; list[j] = t; }
    return list;
  }
  function reversed(w) { return w.split("").reverse().join(""); }

  /** How many times `word` can be read in the grid (every direction, forwards). */
  function occurrences(letters, n, word) {
    var count = 0, r, c, d, k, len = word.length;
    for (r = 0; r < n; r++) for (c = 0; c < n; c++) for (d = 0; d < 8; d++) {
      var er = r + DIRS[d][0] * (len - 1), ec = c + DIRS[d][1] * (len - 1);
      if (er < 0 || er >= n || ec < 0 || ec >= n) continue;
      for (k = 0; k < len; k++) if (letters[(r + DIRS[d][0] * k) * n + c + DIRS[d][1] * k] !== word[k]) break;
      if (k === len) count++;
    }
    return count;
  }

  function pickWords(mode, r, theme) {
    var m = MODES[mode];
    var pool = shuffle(theme.words.filter(function (w) { return w.length >= m.lens[0] && w.length <= m.lens[1] && w !== reversed(w); }), r), chosen = [];
    for (var i = 0; i < pool.length && chosen.length < m.words; i++) {
      var w = pool[i], clash = false;
      for (var j = 0; j < chosen.length; j++) {
        var o = chosen[j];
        if (o.indexOf(w) >= 0 || w.indexOf(o) >= 0 || o.indexOf(reversed(w)) >= 0 || reversed(w).indexOf(o) >= 0 || w.indexOf(reversed(o)) >= 0) clash = true;
      }
      if (!clash) chosen.push(w);
    }
    return chosen;
  }

  /** {theme, n, letters (string, lower case), words: [{w, r, c, d}]} */
  function generate(mode, seed) {
    var m = MODES[mode], r = rand(seed), n = m.n;
    for (var attempt = 0; attempt < 60; attempt++) {
      var theme = Words.THEMES[Math.floor(r() * Words.THEMES.length)];
      var chosen = pickWords(mode, r, theme);
      if (chosen.length < m.words) continue;
      var cells = new Array(n * n).fill(""), placed = [], ok = true;
      chosen.slice().sort(function (a, b) { return b.length - a.length; }).forEach(function (w) {
        if (!ok) return;
        for (var t = 0; t < 400; t++) {
          var d = m.dirs[Math.floor(r() * m.dirs.length)], dr = DIRS[d][0], dc = DIRS[d][1];
          var sr = Math.floor(r() * n), sc = Math.floor(r() * n), er = sr + dr * (w.length - 1), ec = sc + dc * (w.length - 1), k;
          if (er < 0 || er >= n || ec < 0 || ec >= n) continue;
          for (k = 0; k < w.length; k++) { var cur = cells[(sr + dr * k) * n + sc + dc * k]; if (cur && cur !== w[k]) break; }
          if (k < w.length) continue;
          for (k = 0; k < w.length; k++) cells[(sr + dr * k) * n + sc + dc * k] = w[k];
          placed.push({ w: w, r: sr, c: sc, d: d });
          return;
        }
        ok = false;
      });
      if (!ok) continue;
      for (var fill = 0; fill < 30; fill++) {
        var letters = cells.map(function (ch) { return ch || FILL[Math.floor(r() * FILL.length)].toLowerCase(); });
        if (placed.every(function (p) { return occurrences(letters, n, p.w) === 1; }))
          return { theme: theme.id, themeName: theme.name, n: n, letters: letters.join(""), words: placed.sort(function (a, b) { return chosen.indexOf(a.w) - chosen.indexOf(b.w); }) };
      }
    }
    throw new Error("Couldn't make a word search for " + mode + " " + seed);
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "kids", g = generate(mode, o.seed == null ? 1 : o.seed);
    return {
      v: STATE_VERSION, mode: mode, theme: g.theme, themeName: g.themeName, n: g.n, letters: g.letters,
      words: g.words.map(function (p) { return { w: p.w, r: p.r, c: p.c, d: p.d, found: false }; }),
      foundCount: 0, hints: 0, hint: null, hintT: 0, anchor: -1, drag: null, cursor: -1,
      message: "", messageT: 0, updates: 0, won: false, over: false, score: 0, level: 1, stats: { misses: 0, cause: "" },
    };
  }

  function seconds(s) { return Math.floor(s.updates / UPS); }
  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function bonusOf(s) { return Math.max(0, BONUS - MODES[s.mode].rate * seconds(s)); }
  function scoreOf(s) { return Math.max(0, WORD_POINTS * s.foundCount - HINT_PENALTY * s.hints + (s.won ? bonusOf(s) : 0)); }
  function say(s, t) { s.message = t; s.messageT = MESSAGE_UPDATES; }
  function cellsOf(s, w) { var out = []; for (var k = 0; k < w.w.length; k++) out.push((w.r + DIRS[w.d][0] * k) * s.n + w.c + DIRS[w.d][1] * k); return out; }
  function foundCells(s) { var out = {}; s.words.forEach(function (w) { if (w.found) cellsOf(s, w).forEach(function (c) { out[c] = true; }); }); return out; }

  function step(s) {
    if (s.over) return [];
    s.updates++;
    if (s.hintT > 0 && --s.hintT === 0) s.hint = null;
    if (s.messageT > 0 && --s.messageT === 0) s.message = "";
    return [];
  }

  /** The cells of the straight line from cell a to cell b, or null when they aren't in line. */
  function line(s, a, b) {
    var n = s.n, ar = Math.floor(a / n), ac = a % n, br = Math.floor(b / n), bc = b % n, dr = br - ar, dc = bc - ac;
    if (a === b) return [a];
    if (!(dr === 0 || dc === 0 || Math.abs(dr) === Math.abs(dc))) return null;
    var len = Math.max(Math.abs(dr), Math.abs(dc)), sr = Math.sign(dr), sc = Math.sign(dc), out = [];
    for (var k = 0; k <= len; k++) out.push((ar + sr * k) * n + ac + sc * k);
    return out;
  }
  /** Pull a loose end point to the nearest of the eight directions from a, keeping inside the grid. */
  function snap(s, a, b) {
    var n = s.n, ar = Math.floor(a / n), ac = a % n, dr = Math.floor(b / n) - ar, dc = b % n - ac;
    if (!dr && !dc) return a;
    var k = ((Math.round(Math.atan2(dr, dc) / (Math.PI / 4)) % 8) + 8) % 8;
    var vr = [0, 1, 1, 1, 0, -1, -1, -1][k], vc = [1, 1, 0, -1, -1, -1, 0, 1][k];
    var len = Math.max(0, Math.round((dr * vr + dc * vc) / (vr * vr + vc * vc)));
    while (len > 0 && (ar + vr * len < 0 || ar + vr * len >= n || ac + vc * len < 0 || ac + vc * len >= n)) len--;
    return (ar + vr * len) * n + ac + vc * len;
  }

  /** Try the line a → b as a word. */
  function submit(s, a, b) {
    var evs = [], cells = line(s, a, b);
    if (!cells || cells.length < 2 || s.over) return evs;
    var text = cells.map(function (i) { return s.letters[i]; }).join(""), back = reversed(text);
    for (var i = 0; i < s.words.length; i++) {
      var w = s.words[i];
      if (w.found || (w.w !== text && w.w !== back)) continue;
      w.found = true; s.foundCount++; s.hint = null; s.hintT = 0;
      s.score = scoreOf(s);
      evs.push({ type: "found", word: w.w, index: i, cells: cells });
      say(s, w.w.toUpperCase() + "!");
      if (s.foundCount === s.words.length) {
        s.won = true; s.over = true; s.stats.cause = "won"; s.score = scoreOf(s);
        evs.push({ type: "win", score: s.score });
      }
      return evs;
    }
    s.stats.misses++;
    evs.push({ type: "miss" });
    return evs;
  }

  function tap(s, i) {
    var evs = [];
    if (s.over || !(i >= 0 && i < s.n * s.n)) return evs;
    if (s.anchor < 0) { s.anchor = i; evs.push({ type: "select", cell: i }); return evs; }
    if (s.anchor === i) { s.anchor = -1; evs.push({ type: "select", cell: -1 }); return evs; }
    var a = s.anchor; s.anchor = -1;
    return submit(s, a, snap(s, a, i));
  }
  function dragStart(s, i) { if (s.over || !(i >= 0 && i < s.n * s.n)) return; s.drag = { from: i, to: i }; }
  function dragMove(s, i) { if (s.drag && i >= 0 && i < s.n * s.n) s.drag.to = snap(s, s.drag.from, i); }
  function dragEnd(s, i) {
    var d = s.drag; s.drag = null;
    if (!d || s.over) return [];
    var to = i >= 0 && i < s.n * s.n ? snap(s, d.from, i) : d.to;
    if (to === d.from) return tap(s, d.from);
    s.anchor = -1;
    return submit(s, d.from, to);
  }

  function hint(s) {
    if (s.over) return [];
    if (s.hint && !s.words[s.hint.word].found) { s.hintT = HINT_UPDATES; return [{ type: "hint", word: s.hint.word, again: true }]; }
    var idx = -1;
    for (var i = 0; i < s.words.length; i++) if (!s.words[i].found) { idx = i; break; }
    if (idx < 0) return [];
    var w = s.words[idx];
    s.hints++; s.hint = { word: idx, cell: w.r * s.n + w.c }; s.hintT = HINT_UPDATES;
    say(s, "Find " + w.w.toUpperCase());
    return [{ type: "hint", word: idx }];
  }

  function press(s, action, down) {
    if (!down || s.over) return [];
    var n = s.n, dr = 0, dc = 0;
    if (action === "hint") return hint(s);
    if (action === "fire") { if (s.cursor < 0) s.cursor = Math.floor(n / 2) * n + Math.floor(n / 2); return s.cursor >= 0 ? tap(s, s.cursor) : []; }
    if (action === "left") dc = -1; else if (action === "right") dc = 1; else if (action === "up") dr = -1; else if (action === "down") dr = 1; else return [];
    if (s.cursor < 0) s.cursor = Math.floor(n / 2) * n + Math.floor(n / 2);
    else s.cursor = ((Math.floor(s.cursor / n) + dr + n) % n) * n + (s.cursor % n + dc + n) % n;
    return [{ type: "select", cell: s.cursor }];
  }

  function status(s) {
    return { score: scoreOf(s), level: 1, over: s.over, done: s.won, seconds: seconds(s), found: s.foundCount, total: s.words.length };
  }
  function summary(s) {
    var out = ["Found " + s.foundCount + " of " + s.words.length + " words", "Time " + clock(seconds(s))];
    if (s.hints) out.push(s.hints + (s.hints === 1 ? " hint" : " hints") + " (−" + HINT_PENALTY * s.hints + ")");
    if (s.won) out.push("Speed bonus " + bonusOf(s));
    return out;
  }
  function result(s) {
    return {
      score: scoreOf(s), level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, theme: s.themeName, found: s.foundCount, total: s.words.length, hints: s.hints, seconds: seconds(s), summary: summary(s) },
    };
  }

  function save(s) {
    var c = JSON.parse(JSON.stringify(s));
    c.hint = null; c.hintT = 0; c.drag = null; c.anchor = -1; c.message = ""; c.messageT = 0;
    return c;
  }
  function restore(data) {
    var m = data && MODES[data.mode];
    var ok = m && typeof data.letters === "string" && data.n === m.n && data.letters.length === m.n * m.n && /^[a-z]+$/.test(data.letters) &&
      Array.isArray(data.words) && data.words.length === m.words && typeof data.updates === "number" && data.updates >= 0 && isFinite(data.updates) &&
      typeof data.hints === "number" && data.hints >= 0 && data.hints % 1 === 0 && typeof data.themeName === "string" && typeof data.theme === "string";
    if (ok) ok = data.words.every(function (w) {
      if (!w || typeof w.w !== "string" || !/^[a-z]{3,11}$/.test(w.w) || !(w.d >= 0 && w.d < 8) || w.d % 1 !== 0 || w.r % 1 !== 0 || w.c % 1 !== 0) return false;
      var er = w.r + DIRS[w.d][0] * (w.w.length - 1), ec = w.c + DIRS[w.d][1] * (w.w.length - 1);
      if (w.r < 0 || w.c < 0 || w.r >= m.n || w.c >= m.n || er < 0 || ec < 0 || er >= m.n || ec >= m.n) return false;
      for (var k = 0; k < w.w.length; k++) if (data.letters[(w.r + DIRS[w.d][0] * k) * m.n + w.c + DIRS[w.d][1] * k] !== w.w[k]) return false;
      return true;
    });
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.words = s.words.map(function (w) { return { w: w.w, r: w.r, c: w.c, d: w.d, found: !!w.found }; });
    s.foundCount = s.words.filter(function (w) { return w.found; }).length;
    if (s.foundCount === s.words.length) throw new Error("That saved game can't be continued.");
    s.hint = null; s.hintT = 0; s.drag = null; s.anchor = -1; s.cursor = -1; s.message = ""; s.messageT = 0;
    s.won = false; s.over = false; s.score = scoreOf(s);
    s.stats = { misses: data.stats && data.stats.misses > 0 ? Math.floor(data.stats.misses) : 0, cause: "" };
    return s;
  }

  var WordSearchLogic = {
    UPS: UPS, MODES: MODES, MODE_IDS: MODE_IDS, DIRS: DIRS, STATE_VERSION: STATE_VERSION, WORD_POINTS: WORD_POINTS, HINT_PENALTY: HINT_PENALTY, BONUS: BONUS,
    rand: rand, generate: generate, occurrences: occurrences, create: create, step: step, line: line, snap: snap, submit: submit, tap: tap,
    dragStart: dragStart, dragMove: dragMove, dragEnd: dragEnd, hint: hint, press: press, seconds: seconds, clock: clock, scoreOf: scoreOf,
    bonusOf: bonusOf, cellsOf: cellsOf, foundCells: foundCells, status: status, result: result, save: save, restore: restore,
  };
  if (typeof module === "object" && module.exports) module.exports = WordSearchLogic; else self.WordSearchLogic = WordSearchLogic;
})();
