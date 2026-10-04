/* Household Arcade — Word Guess rules (pure: no DOM, deterministic for a given seed).

   Find the secret five-letter word. Each guess must be a real word (see wordguess-words.js); after it, every
   letter is marked:  correct (right letter, right place),  elsewhere (in the word, another place) or  absent
   (not in the word — or the word has no more of that letter). A letter that is repeated in a guess is only
   marked as often as it appears in the secret word, correct ones first.
   Modes: classic (six tries), easy (eight tries), strict (six tries, and every clue found so far must be used
   in the next guess: correct letters stay in place, elsewhere letters must be included).
   Score = 1,000 × (tries left + 1) + (999 − seconds, at least 0) for a word found, otherwise 0, so fewer tries
   rank first and then the faster time. The clock is the updates the game ran (60 a second).
   One call to step() is one update. */
(function () {
  "use strict";

  var Words = typeof module === "object" && module.exports ? require("./wordguess-words.js") : self.WordGuessWords;

  var UPS = 60, LEN = 5;
  var MODES = { classic: { tries: 6, strict: false, name: "Six tries" }, easy: { tries: 8, strict: false, name: "Eight tries" }, strict: { tries: 6, strict: true, name: "Strict" } };
  var MODE_IDS = ["classic", "easy", "strict"];
  var MESSAGE_UPDATES = 150;
  var REVEAL_UPDATES = 8 * LEN;     // the marks of a new guess show one tile at a time (drawing only)
  var STATE_VERSION = 1;
  var ABSENT = 0, ELSEWHERE = 1, CORRECT = 2;
  var ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"];

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

  /** Marks for a guess against the answer: an array of 5 of ABSENT / ELSEWHERE / CORRECT. */
  function mark(guess, answer) {
    var out = [ABSENT, ABSENT, ABSENT, ABSENT, ABSENT], left = {}, i;
    for (i = 0; i < LEN; i++) {
      if (guess[i] === answer[i]) out[i] = CORRECT;
      else left[answer[i]] = (left[answer[i]] || 0) + 1;
    }
    for (i = 0; i < LEN; i++) {
      if (out[i] === CORRECT) continue;
      if (left[guess[i]] > 0) { out[i] = ELSEWHERE; left[guess[i]]--; }
    }
    return out;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var r = rand(o.seed == null ? 1 : o.seed);
    var answer = Words.ANSWERS[Math.floor(r() * Words.ANSWERS.length)];
    return fresh(mode, answer);
  }
  function fresh(mode, answer) {
    return {
      v: STATE_VERSION, mode: mode, answer: answer, tries: MODES[mode].tries, strict: MODES[mode].strict,
      guesses: [], marks: [], cur: "", message: "", messageT: 0, shake: 0, guessAt: -1000,
      updates: 0, won: false, over: false, score: 0, level: 1, keys: {}, stats: { cause: "" },
    };
  }

  function seconds(s) { return Math.floor(s.updates / UPS); }
  function scoreOf(s) { return s.won ? 1000 * (s.tries - s.guesses.length + 1) + Math.max(0, 999 - seconds(s)) : 0; }
  function ordinal(n) { return ["1st", "2nd", "3rd", "4th", "5th"][n - 1] || n + "th"; }
  function say(s, text) { s.message = text; s.messageT = MESSAGE_UPDATES; }
  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.shake > 0) s.shake--;
    if (s.messageT > 0 && --s.messageT === 0) s.message = "";
    return evs;
  }

  function type(s, ch) {
    var evs = [];
    if (s.over || !/^[A-Za-z]$/.test(ch) || s.cur.length >= LEN) return evs;
    s.cur += ch.toLowerCase();
    evs.push({ type: "type", letter: ch.toUpperCase() });
    return evs;
  }
  function back(s) {
    if (s.over || !s.cur) return [];
    s.cur = s.cur.slice(0, -1);
    return [{ type: "back" }];
  }

  /** What a strict guess breaks, as a sentence, or "" when it uses every clue so far. */
  function strictProblem(s, guess) {
    var i, g, mk, c, need = {};
    for (g = 0; g < s.guesses.length; g++) {
      mk = s.marks[g];
      var inGuess = {};
      for (i = 0; i < LEN; i++) {
        c = s.guesses[g][i];
        if (mk[i] === CORRECT) {
          if (guess[i] !== c) return c.toUpperCase() + " must stay in the " + ordinal(i + 1) + " place.";
          inGuess[c] = (inGuess[c] || 0) + 1;
        } else if (mk[i] === ELSEWHERE) inGuess[c] = (inGuess[c] || 0) + 1;
      }
      for (c in inGuess) need[c] = Math.max(need[c] || 0, inGuess[c]);
    }
    for (c in need) {
      var have = 0;
      for (i = 0; i < LEN; i++) if (guess[i] === c) have++;
      if (have < need[c]) return "Use every clue: the word has " + (need[c] > 1 ? need[c] + " of " : "a ") + c.toUpperCase() + ".";
    }
    return "";
  }

  function enter(s) {
    var evs = [];
    if (s.over) return evs;
    if (s.cur.length < LEN) { say(s, "Not enough letters."); s.shake = 20; evs.push({ type: "reject" }); return evs; }
    if (!Words.isWord(s.cur)) { say(s, "That isn't in the word list."); s.shake = 20; evs.push({ type: "reject" }); return evs; }
    if (s.strict) {
      var why = strictProblem(s, s.cur);
      if (why) { say(s, why); s.shake = 20; evs.push({ type: "reject" }); return evs; }
    }
    var guess = s.cur, mk = mark(guess, s.answer);
    s.guesses.push(guess); s.marks.push(mk); s.cur = ""; s.guessAt = s.updates;
    for (var i = 0; i < LEN; i++) {
      var c = guess[i].toUpperCase();
      if (mk[i] > (s.keys[c] == null ? -1 : s.keys[c])) s.keys[c] = mk[i];
    }
    evs.push({ type: "guess", marks: mk, number: s.guesses.length });
    if (guess === s.answer) {
      s.won = true; s.over = true; s.score = scoreOf(s); s.stats.cause = "won";
      evs.push({ type: "win", score: s.score, tries: s.guesses.length });
    } else if (s.guesses.length >= s.tries) {
      s.over = true; s.score = 0; s.stats.cause = "lost";
      say(s, "The word was " + s.answer.toUpperCase() + ".");
      evs.push({ type: "lose", answer: s.answer });
    }
    return evs;
  }

  function press(s, action, down) {
    if (!down || s.over || typeof action !== "string" || action.indexOf("key:") !== 0) return [];
    var k = action.slice(4);
    if (k === "ENTER") return enter(s);
    if (k === "BACKSPACE" || k === "DELETE") return back(s);
    if (/^[A-Z]$/.test(k)) return type(s, k);
    return [];
  }

  function status(s) {
    return { score: s.won ? s.score : 0, level: 1, over: s.over, done: s.won, seconds: seconds(s), tries: s.guesses.length };
  }
  function summary(s) {
    var out = [];
    if (s.won) out.push("Found " + s.answer.toUpperCase() + " in " + s.guesses.length + (s.guesses.length === 1 ? " try" : " tries") + " of " + s.tries);
    else out.push("The word was " + s.answer.toUpperCase());
    out.push("Time " + clock(seconds(s)));
    return out;
  }
  function result(s) {
    return {
      score: s.won ? s.score : 0, level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, tries: s.guesses.length, answer: s.over ? s.answer : undefined, seconds: seconds(s), summary: s.over ? summary(s) : undefined },
    };
  }

  function save(s) { return JSON.parse(JSON.stringify(s)); }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && typeof data.answer === "string" && Words.ANSWERS.indexOf(data.answer) >= 0 &&
      Array.isArray(data.guesses) && data.guesses.length <= MODES[data.mode].tries && typeof data.cur === "string" && /^[a-z]{0,5}$/.test(data.cur) &&
      typeof data.updates === "number" && data.updates >= 0 && isFinite(data.updates) &&
      data.guesses.every(function (w) { return typeof w === "string" && Words.isWord(w); });
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = fresh(data.mode, data.answer);
    s.updates = Math.floor(data.updates);
    s.cur = data.cur;
    for (var g = 0; g < data.guesses.length; g++) {
      // a saved game with a guess that is already the answer (or too many) isn't one this game could have saved
      if (s.over) throw new Error("That saved game can't be continued.");
      s.cur = data.guesses[g];
      var evs = enter(s);
      if (!evs.length || evs[0].type === "reject") throw new Error("That saved game can't be continued.");
    }
    s.cur = data.cur; if (s.over) s.cur = "";
    s.message = ""; s.messageT = 0; s.shake = 0; s.guessAt = -1000;
    return s;
  }

  var WordGuessLogic = {
    UPS: UPS, LEN: LEN, MODES: MODES, MODE_IDS: MODE_IDS, STATE_VERSION: STATE_VERSION, ABSENT: ABSENT, ELSEWHERE: ELSEWHERE, CORRECT: CORRECT,
    ROWS: ROWS, REVEAL_UPDATES: REVEAL_UPDATES, rand: rand, mark: mark, create: create, step: step, type: type, back: back, enter: enter,
    press: press, seconds: seconds, scoreOf: scoreOf, status: status, result: result, save: save, restore: restore, clock: clock,
  };
  if (typeof module === "object" && module.exports) module.exports = WordGuessLogic; else self.WordGuessLogic = WordGuessLogic;
})();
