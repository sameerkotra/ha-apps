/* Household Arcade — Colour Memory rules (pure: no DOM, deterministic for a given seed).

   Four pads: up (red), left (green), right (blue), down (yellow). Each round the game plays the
   sequence so far plus one new pad, then you repeat it (Backwards: in reverse order). A wrong pad,
   or 5 seconds with no pad pressed on your turn, ends the game. One call to step() is one update
   (60 a second).
   Challenges: a list of levels (built-in, or the session's list): repeat a sequence that grows to the
   challenge's length to clear it, at its own speed and direction; then a fresh sequence for the next
   one; clearing the last wins. */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var PADS = ["up", "left", "right", "down"];
  var PAD_CI = [1, 4, 5, 3];                     // red, green, blue, yellow
  var MIN_LIGHT = 10, MIN_GAP = 10;              // never quicker than 3 lights a second
  var LEAD = 40;                                 // "WATCH" before the playback starts
  var DONE = 40;                                 // pause after a round you got right
  var IDLE_LIMIT = 300;                          // 5 s without a pad on your turn ends the game
  var FEEDBACK = 12;                             // a pad you press stays lit this long
  var MAX_ROUND = 100;                           // getting round 100 right wins the game
  var MODES = {
    classic: { light: 34, gap: 20, lightStep: 2, gapStep: 1, reverse: false },
    fast: { light: 20, gap: 13, lightStep: 1, gapStep: 1, reverse: false },
    reverse: { light: 34, gap: 20, lightStep: 2, gapStep: 1, reverse: true },
    challenge: { light: 30, gap: 20, lightStep: 0, gapStep: 0, reverse: false },   // the numbers come from the list
  };

  // Challenges (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A challenge is
  // { name, length, light, gap, reverse, start }: the sequence starts `start` pads long and grows by one a
  // round up to `length`; each pad lights for `light` updates with `gap` updates between (never quicker than
  // 3 lights a second); `reverse` wants it repeated from last to first.
  var LEVELS = [
    { name: "First steps", length: 4, light: 36, gap: 24, reverse: false, start: 1 },
    { name: "Little tune", length: 6, light: 32, gap: 22, reverse: false, start: 2 },
    { name: "Look back", length: 5, light: 34, gap: 22, reverse: true, start: 2 },
    { name: "Quick fingers", length: 7, light: 24, gap: 16, reverse: false, start: 3 },
    { name: "Long song", length: 10, light: 28, gap: 18, reverse: false, start: 4 },
    { name: "Mirror walk", length: 8, light: 26, gap: 18, reverse: true, start: 3 },
    { name: "Hummingbird", length: 10, light: 16, gap: 12, reverse: false, start: 5 },
    { name: "Upside down", length: 10, light: 20, gap: 14, reverse: true, start: 4 },
    { name: "Marathon", length: 14, light: 20, gap: 14, reverse: false, start: 5 },
    { name: "Grand finale", length: 14, light: 12, gap: 10, reverse: true, start: 5 },
  ];
  var FIELDS = { length: [4, 30], light: [10, 40], gap: [10, 30], start: [1, 5] };

  /** The challenges to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c !== "object" || typeof c.name !== "string" || typeof c.reverse !== "boolean") return false;
      for (var k in FIELDS) {
        if (typeof c[k] !== "number" || c[k] !== Math.floor(c[k]) || !(c[k] >= FIELDS[k][0] && c[k] <= FIELDS[k][1])) return false;
      }
      return c.start <= c.length;
    });
    return out.length ? out : LEVELS;
  }
  function challenge(s) { return s.challenges ? s.challenges[Math.min(s.level, s.challenges.length) - 1] : null; }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** Updates a pad stays lit during the playback of round n, and the gap after it. */
  function lightTime(mode, n) { var m = MODES[mode]; return Math.max(MIN_LIGHT, m.light - m.lightStep * (n - 1)); }
  function gapTime(mode, n) { var m = MODES[mode]; return Math.max(MIN_GAP, m.gap - m.gapStep * (n - 1)); }
  // The same for a game (a challenge has its own numbers).
  function lightOf(s, n) { var c = challenge(s); return c ? Math.max(MIN_LIGHT, c.light) : lightTime(s.mode, n); }
  function gapOf(s, n) { var c = challenge(s); return c ? Math.max(MIN_GAP, c.gap) : gapTime(s.mode, n); }
  function isReverse(s) { var c = challenge(s); return c ? c.reverse : MODES[s.mode].reverse; }
  /** Pads in the sequence that clears this challenge (0: the classic modes, which go on to round 100). */
  function goal(s) { var c = challenge(s); return c ? c.length : 0; }

  /** A fresh sequence of n pads. */
  function fresh(s, n) { s.seq = []; for (var i = 0; i < n; i++) s.seq.push(Math.floor(rand(s) * 4)); }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES[o.mode] ? o.mode : "classic", rng: (o.seed >>> 0) || 1,
      seq: [], phase: "lead", t: LEAD, idx: 0, on: false,
      input: 0, idle: 0, lit: -1, litT: 0,
      score: 0, level: 1, over: false, won: false, updates: 0,
      wrongPad: -1, expected: -1, challenges: null, cleared: false,
      stats: { rounds: 0, longest: 0, presses: 0, challenges: 0, cause: null },
    };
    if (s.mode === "challenge") { s.challenges = usableLevels(o.levels); s.level = startAt(o.startLevel, s.challenges.length); fresh(s, challenge(s).start); }
    else fresh(s, 1);
    return s;
  }

  function tone(pad) { return { type: "tone" + PADS[pad].charAt(0).toUpperCase() + PADS[pad].slice(1), pad: pad }; }

  /** The pad expected next on your turn. */
  function expectedPad(s) {
    var n = s.seq.length;
    return isReverse(s) ? s.seq[n - 1 - s.input] : s.seq[s.input];
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.litT > 0 && --s.litT === 0 && s.phase !== "show") s.lit = -1;
    var n = s.seq.length;
    if (s.phase === "lead") {
      if (--s.t <= 0) {
        s.phase = "show"; s.idx = 0; s.on = true; s.t = lightOf(s, n);
        s.lit = s.seq[0]; s.litT = 0;
        evs.push(tone(s.seq[0]));
      }
    } else if (s.phase === "show") {
      if (--s.t <= 0) {
        if (s.on) { s.on = false; s.lit = -1; s.t = gapOf(s, n); }
        else if (++s.idx < n) {
          s.on = true; s.t = lightOf(s, n); s.lit = s.seq[s.idx];
          evs.push(tone(s.seq[s.idx]));
        } else {
          s.phase = "input"; s.input = 0; s.idle = 0;
          evs.push({ type: "yourTurn" });
        }
      }
    } else if (s.phase === "input") {
      if (++s.idle >= IDLE_LIMIT) {
        s.expected = expectedPad(s);
        end(s, evs, "time");
      }
    } else if (s.phase === "done") {
      if (--s.t <= 0) {
        s.phase = "lead"; s.t = LEAD;
        if (s.challenges) {
          if (s.cleared) {                       // the next challenge: a fresh sequence
            s.cleared = false; s.level++;
            fresh(s, challenge(s).start);
            evs.push({ type: "level", level: s.level, name: challenge(s).name });
          } else s.seq.push(Math.floor(rand(s) * 4));
        } else {
          s.seq.push(Math.floor(rand(s) * 4));
          s.level = s.seq.length;
          evs.push({ type: "level", level: s.level });
        }
      }
    }
    return evs;
  }

  function end(s, evs, cause) {
    s.over = true; s.phase = "over"; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause, expected: s.expected });
  }

  /** You press a pad (0 up, 1 left, 2 right, 3 down). Ignored except on your turn. */
  function tap(s, pad) {
    var evs = [];
    if (s.over || s.phase !== "input" || !(pad >= 0 && pad < 4)) return evs;
    s.stats.presses++;
    var want = expectedPad(s);
    s.idle = 0;
    if (pad !== want) {
      s.wrongPad = pad; s.expected = want; s.lit = -1; s.litT = 0;
      end(s, evs, "wrong");
      return evs;
    }
    s.lit = pad; s.litT = FEEDBACK;
    evs.push(tone(pad));
    s.input++;
    var n = s.seq.length;
    if (s.input >= n) {
      var pts = 10 * n;
      s.score += pts; s.stats.rounds++; s.stats.longest = n;
      var cleared = s.challenges ? n >= goal(s) : false;
      evs.push({ type: "round", round: n, points: pts, score: s.score, cleared: cleared });
      if (cleared) { s.stats.challenges++; s.cleared = true; }
      if (s.challenges ? cleared && s.level >= s.challenges.length : n >= MAX_ROUND) {
        s.over = true; s.won = true; s.phase = "over"; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
      } else { s.phase = "done"; s.t = DONE; }
    }
    return evs;
  }

  function press(s, action, down) {
    if (!down) return [];
    var p = PADS.indexOf(action);
    return p >= 0 ? tap(s, p) : [];
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "challenges") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.seq) && data.seq.length >= 1 &&
      data.seq.length <= MAX_ROUND && data.seq.every(function (p) { return p === 0 || p === 1 || p === 2 || p === 3; }) &&
      typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" && typeof data.phase === "string" &&
      data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.challenges = s.mode === "challenge" ? usableLevels(levels) : null;
    if (s.cleared == null) s.cleared = false;
    if (!s.stats.challenges) s.stats.challenges = 0;
    if (s.challenges && s.level > s.challenges.length) s.level = s.challenges.length;   // a shorter list now
    // A game saved while the sequence played or on your turn plays the whole sequence again first.
    if (s.phase === "show" || s.phase === "input") {
      s.phase = "lead"; s.t = LEAD; s.input = 0; s.idle = 0; s.lit = -1; s.litT = 0; s.on = false;
    }
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { rounds: s.stats.rounds, longest: s.stats.longest, presses: s.stats.presses,
        challenges: s.stats.challenges || 0, won: s.won, cause: s.stats.cause, mode: s.mode },
    };
  }

  var ColoursLogic = {
    PADS: PADS, PAD_CI: PAD_CI, MIN_LIGHT: MIN_LIGHT, MIN_GAP: MIN_GAP, LEAD: LEAD, DONE: DONE,
    IDLE_LIMIT: IDLE_LIMIT, FEEDBACK: FEEDBACK, MAX_ROUND: MAX_ROUND, MODES: MODES, STATE_VERSION: STATE_VERSION,
    LEVELS: LEVELS, usableLevels: usableLevels, goal: goal, isReverse: isReverse, lightOf: lightOf, gapOf: gapOf,
    create: create, step: step, press: press, tap: tap, expectedPad: expectedPad, lightTime: lightTime, gapTime: gapTime,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = ColoursLogic; else self.ColoursLogic = ColoursLogic;
})();
