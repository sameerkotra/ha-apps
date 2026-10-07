/* Household Arcade — Memory Cards rules (pure: no DOM, deterministic for a given seed).

   Face-down cards in a grid, two of each symbol. Turn two: a pair stays face up, otherwise both
   turn back after a moment. Clear the board before the time runs out to get the next one (a little
   less time each board). One call to step() is one update (60 a second).
   Challenges: a list of boards (built-in, or the session's list), each with its own size, time, a peek
   at all the faces first and how many kinds of symbol it uses; clearing the last board wins. */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60;
  var SYMBOLS = 15;                 // circle, ring, square, diamond, triangle, star, plus, cross, heart,
                                    // hexagon, house, hourglass, two dots, bars, arrow
  var SYMBOL_CI = [1, 5, 4, 6, 2, 3, 7, 1, 1, 4, 5, 6, 2, 7, 3];
  var BACK_DELAY = 45;              // a pair that doesn't match turns back after this many updates
  var TURN = 6;                     // updates a card takes to turn over (also the least time between two turns)
  var CLEAR_PAUSE = 90;             // "board clear" before the next board
  var MAX_LEVEL = 100;              // clearing board 100 wins the game
  var PAIR_POINTS = 20;             // × the streak of pairs in a row
  var TIME_BONUS = 5;               // per whole second left
  var MISS_BONUS = 10;              // per pair on the board, less one per miss
  var MODES = {
    small: { cols: 4, rows: 4, time: 60, min: 30 },
    medium: { cols: 4, rows: 5, time: 75, min: 40 },
    large: { cols: 5, rows: 6, time: 110, min: 60 },
    challenge: { cols: 4, rows: 4, time: 60, min: 20 },       // the numbers come from the list
  };

  // Challenges (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A board is
  // { name, cols, rows, time, peek, symbols }: cols × rows cards (even, at most 30), `time` seconds to clear
  // it, `peek` seconds with every face shown before play (the clock waits), and `symbols` kinds of symbol at
  // most (fewer kinds than pairs: some symbols are on 4 or 6 cards, and any two alike make a pair).
  var LEVELS = [
    { name: "Warm up", cols: 3, rows: 4, time: 60, peek: 3, symbols: 6 },
    { name: "Garden path", cols: 4, rows: 4, time: 70, peek: 3, symbols: 8 },
    { name: "Twins", cols: 4, rows: 4, time: 50, peek: 2, symbols: 4 },
    { name: "Picnic", cols: 4, rows: 5, time: 80, peek: 2, symbols: 10 },
    { name: "Quick look", cols: 4, rows: 5, time: 70, peek: 1, symbols: 10 },
    { name: "Shoebox", cols: 6, rows: 4, time: 90, peek: 2, symbols: 12 },
    { name: "No peeking", cols: 4, rows: 6, time: 85, peek: 0, symbols: 12 },
    { name: "Big table", cols: 5, rows: 6, time: 120, peek: 1, symbols: 15 },
    { name: "Look alikes", cols: 6, rows: 5, time: 90, peek: 0, symbols: 8 },
    { name: "Grand memory", cols: 5, rows: 6, time: 95, peek: 0, symbols: 15 },
  ];
  var FIELDS = { cols: [3, 6], rows: [3, 6], time: [20, 150], peek: [0, 5], symbols: [4, 15] };

  /** The boards to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c !== "object" || typeof c.name !== "string") return false;
      for (var k in FIELDS) {
        if (typeof c[k] !== "number" || c[k] !== Math.floor(c[k]) || !(c[k] >= FIELDS[k][0] && c[k] <= FIELDS[k][1])) return false;
      }
      var n = c.cols * c.rows;
      return n % 2 === 0 && n <= 30 && c.symbols <= n / 2;
    });
    return out.length ? out : LEVELS;
  }
  function board(s) { return s.boards ? s.boards[Math.min(s.level, s.boards.length) - 1] : null; }
  var LEVEL_LESS = 2;               // seconds less on each later board
  // The card area of the playfield (below the time bar).
  var AREA_X = 12, AREA_W = 216, AREA_Y = 48, AREA_H = 248;

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** Where the cards go: pitch on multiples of 6, as big as fit, centred. For a mode, or a game's board. */
  function layout(x) {
    var m = typeof x === "string" ? MODES[x] : x.cols ? x : MODES[x.mode];
    var px = Math.floor(AREA_W / m.cols / 6) * 6, py = Math.floor(AREA_H / m.rows / 6) * 6;
    var ox = AREA_X + Math.round((AREA_W - m.cols * px) / 2 / 6) * 6;
    var oy = AREA_Y + Math.round((AREA_H - m.rows * py) / 2 / 6) * 6;
    return { cols: m.cols, rows: m.rows, px: px, py: py, ox: ox, oy: oy, gap: 4 };
  }

  function timeFor(mode, level) { var m = MODES[mode]; return Math.max(m.min, m.time - LEVEL_LESS * (level - 1)) * UPS; }
  function timeOf(s) { var b = board(s); return b ? b.time * UPS : timeFor(s.mode, s.level); }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES[o.mode] ? o.mode : "small", rng: (o.seed >>> 0) || 1,
      cards: [], first: -1, second: -1, backT: 0, cool: 0, queued: -1, cursor: 0,
      phase: "play", clearT: 0, timeLimit: 0, timeLeft: 0, peekT: 0, cols: 0, rows: 0, boards: null,
      streak: 0, misses: 0, lastBonus: 0,
      score: 0, level: 1, over: false, won: false, updates: 0,
      stats: { pairs: 0, misses: 0, boards: 0, bestStreak: 0, turns: 0, cause: null },
    };
    if (s.mode === "challenge") { s.boards = usableLevels(o.levels); s.level = startAt(o.startLevel, s.boards.length); }
    deal(s);
    return s;
  }

  function deal(s) {
    var b = board(s), m = b || MODES[s.mode], n = m.cols * m.rows, pairs = n / 2, i, j, t;
    var kinds = b ? Math.min(b.symbols, pairs) : pairs;
    s.cols = m.cols; s.rows = m.rows;
    var syms = [];
    for (i = 0; i < SYMBOLS; i++) syms.push(i);
    for (i = syms.length - 1; i > 0; i--) { j = Math.floor(rand(s) * (i + 1)); t = syms[i]; syms[i] = syms[j]; syms[j] = t; }
    var deck = [];
    for (i = 0; i < pairs; i++) deck.push(syms[i % kinds], syms[i % kinds]);
    for (i = deck.length - 1; i > 0; i--) { j = Math.floor(rand(s) * (i + 1)); t = deck[i]; deck[i] = deck[j]; deck[j] = t; }
    s.cards = deck.map(function (sym) { return { sym: sym, up: false, done: false, turnT: 0 }; });
    s.first = -1; s.second = -1; s.backT = 0; s.queued = -1; s.misses = 0; s.streak = 0;   // each board starts a new streak
    s.timeLimit = timeOf(s); s.timeLeft = s.timeLimit;
    s.peekT = b ? b.peek * UPS : 0;
    s.phase = s.peekT > 0 ? "peek" : "play";
    if (s.cursor >= n) s.cursor = 0;
  }

  function pairsOf(s) { return s.cards.length / 2; }

  function turnBack(s) {
    [s.first, s.second].forEach(function (i) { if (i >= 0) { s.cards[i].up = false; s.cards[i].turnT = TURN; } });
    s.first = -1; s.second = -1; s.backT = 0;
  }

  // Turn card i now (the cool-down has passed).
  function turn(s, i, evs) {
    var c = s.cards[i];
    if (s.backT > 0) {                         // a third card: the two that didn't match go back at once
      var shown = i === s.first || i === s.second;
      turnBack(s);
      if (shown) { s.cool = TURN; return; }    // tapping one of them only turns them back
    }
    if (c.up || c.done) return;
    c.up = true; c.turnT = TURN; s.cool = TURN; s.stats.turns++;
    evs.push({ type: "turn", card: i });
    if (s.first < 0) { s.first = i; return; }
    s.second = i;
    var a = s.cards[s.first];
    if (a.sym === c.sym) {
      a.done = true; c.done = true;
      s.streak++; s.stats.pairs++; s.stats.bestStreak = Math.max(s.stats.bestStreak, s.streak);
      var pts = PAIR_POINTS * s.streak;
      s.score += pts;
      evs.push({ type: "pair", sym: c.sym, streak: s.streak, points: pts, score: s.score });
      s.first = -1; s.second = -1;
      if (s.cards.every(function (k) { return k.done; })) boardClear(s, evs);
    } else {
      s.streak = 0; s.misses++; s.stats.misses++; s.backT = BACK_DELAY;
      evs.push({ type: "miss" });
    }
  }

  function boardClear(s, evs) {
    var secs = Math.floor(s.timeLeft / UPS), pairs = pairsOf(s);
    var bonus = TIME_BONUS * secs + MISS_BONUS * Math.max(0, pairs - s.misses);
    s.score += bonus; s.lastBonus = bonus; s.stats.boards++;
    evs.push({ type: "clear", bonus: bonus, seconds: secs, misses: s.misses, score: s.score });
    if (s.boards ? s.level >= s.boards.length : s.level >= MAX_LEVEL) {
      s.over = true; s.won = true; s.phase = "over"; s.stats.cause = "won";
      evs.push({ type: "win", score: s.score });
      return;
    }
    s.phase = "clear"; s.clearT = CLEAR_PAUSE;
  }

  /** Tap card i (also moves the cursor there). A tap within the turning time of the last one waits for it. */
  function tap(s, i) {
    var evs = [];
    if (s.over || s.phase !== "play" || !(i >= 0 && i < s.cards.length)) return evs;
    s.cursor = i;
    if (s.cool > 0) { s.queued = i; return evs; }
    turn(s, i, evs);
    return evs;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    for (var i = 0; i < s.cards.length; i++) if (s.cards[i].turnT > 0) s.cards[i].turnT--;
    if (s.phase === "peek") {                  // every face shown; the clock waits; then they all turn down
      if (--s.peekT <= 0) {
        s.peekT = 0; s.phase = "play";
        for (i = 0; i < s.cards.length; i++) s.cards[i].turnT = TURN;
        evs.push({ type: "hide" });
      }
      return evs;
    }
    if (s.phase === "clear") {
      if (--s.clearT <= 0) {
        s.level++;
        deal(s);
        evs.push({ type: "level", level: s.level, name: s.boards ? board(s).name : undefined });
      }
      return evs;
    }
    if (s.cool > 0) s.cool--;
    if (s.cool === 0 && s.queued >= 0) { var q = s.queued; s.queued = -1; turn(s, q, evs); if (s.phase !== "play") return evs; }
    if (s.backT > 0 && --s.backT === 0) { turnBack(s); evs.push({ type: "back" }); }
    if (--s.timeLeft <= 0) {
      s.timeLeft = 0; s.over = true; s.phase = "over"; s.stats.cause = "time";
      evs.push({ type: "gameover", score: s.score, cause: "time" });
    }
    return evs;
  }

  function press(s, action, down) {
    if (!down || s.over) return [];
    var m = { cols: s.cols, rows: s.rows }, c = s.cursor % m.cols, r = Math.floor(s.cursor / m.cols);
    if (action === "left") c = (c + m.cols - 1) % m.cols;
    else if (action === "right") c = (c + 1) % m.cols;
    else if (action === "up") r = (r + m.rows - 1) % m.rows;
    else if (action === "down") r = (r + 1) % m.rows;
    else if (action === "fire") return tap(s, s.cursor);
    else return [];
    s.cursor = r * m.cols + c;
    return [{ type: "cursor", card: s.cursor }];
  }

  /** The card under a point of the playfield, or -1. */
  function cardAt(s, x, y) {
    var L = layout(s);
    var c = Math.floor((x - L.ox) / L.px), r = Math.floor((y - L.oy) / L.py);
    if (c < 0 || c >= L.cols || r < 0 || r >= L.rows) return -1;
    return r * L.cols + c;
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "boards") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var m = data && typeof data === "object" ? MODES[data.mode] : null;
    if (m && data.cols) m = { cols: data.cols, rows: data.rows };          // the board as it was dealt
    var ok = m && m.cols >= 3 && m.cols <= 6 && m.rows >= 3 && m.rows <= 6 &&
      Array.isArray(data.cards) && data.cards.length === m.cols * m.rows &&
      data.cards.every(function (c) { return c && typeof c === "object" && c.sym >= 0 && c.sym < SYMBOLS; }) &&
      typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" &&
      typeof data.timeLeft === "number" && typeof data.phase === "string" && data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.cols = m.cols; s.rows = m.rows;                                     // older saves: the mode's size
    if (s.peekT == null) s.peekT = 0;
    s.boards = s.mode === "challenge" ? usableLevels(levels) : null;
    if (s.boards && s.level > s.boards.length) s.level = s.boards.length;  // a shorter list now
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { boards: s.stats.boards, pairs: s.stats.pairs, misses: s.stats.misses, bestStreak: s.stats.bestStreak,
        turns: s.stats.turns, won: s.won, cause: s.stats.cause, mode: s.mode },
    };
  }

  var CardsLogic = {
    UPS: UPS, SYMBOLS: SYMBOLS, SYMBOL_CI: SYMBOL_CI, BACK_DELAY: BACK_DELAY, TURN: TURN, CLEAR_PAUSE: CLEAR_PAUSE,
    MAX_LEVEL: MAX_LEVEL, PAIR_POINTS: PAIR_POINTS, TIME_BONUS: TIME_BONUS, MISS_BONUS: MISS_BONUS, MODES: MODES,
    LEVEL_LESS: LEVEL_LESS, STATE_VERSION: STATE_VERSION,
    LEVELS: LEVELS, usableLevels: usableLevels, timeOf: timeOf,
    create: create, step: step, press: press, tap: tap, cardAt: cardAt, layout: layout, timeFor: timeFor,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = CardsLogic; else self.CardsLogic = CardsLogic;
})();
