/* Household Arcade — Type Rain rules (pure: no DOM, deterministic for a given seed).

   Words fall from the sky; type a word before it reaches the ground. The first letter typed picks the lowest word
   that starts with it, and the next letters must follow it (a wrong letter is a slip: it breaks the run, the word
   stays picked; Backspace lets go of it). A finished word scores 10 a letter × the run bonus (×2 from 10 words in a
   row without a slip or a landing, ×3 from 25). A word that lands costs a life (3; Little ones 5).
   Which words fall, where and when comes only from the seed and the clock — never from the typing — so two players
   in a race get the same words in the same order: Little ones (single letters), Easy (3–4 letters) and Classic (longer
   words as it goes) speed up every LEVEL_T updates; Stages plays the stage list (SPEC §11.9), each stage its own
   words from the seed, and finishing the last one wins.
   Only + − × ÷ and Math.floor/abs/min/max/imul are used. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var Words = typeof module === "object" && module.exports ? require("./typerain-words.js") : self.TypeRainWords;

  var GROUND = 196, TOP_Y = 46, LEFT = 8, RIGHT = 232, CHAR_W = 8, PAD = 6;
  var LEVEL_T = 1200, MAX_LEVEL = 30, FIRST_T = 40, CLEAR_T = 120, STATE_VERSION = 1;
  var LETTER_POINTS = 10, RUN2 = 10, RUN3 = 25, STAGE_POINTS = 200, RECENT = 10, MAX_LEN = 10, GAP_MIN = 40;
  var MODES = {
    letters: { lives: 5, speed: 0.22, ramp: 0.02, vmax: 0.8, gap: 120, gapStep: 4, gapMin: 60 },      // gentle: single letters
    easy: { lives: 3, speed: 0.22, ramp: 0.02, vmax: 0.7, gap: 150, gapStep: 5, gapMin: 70 },
    classic: { lives: 3, speed: 0.3, ramp: 0.035, vmax: 1.3, gap: 130, gapStep: 4, gapMin: GAP_MIN },
    stages: { lives: 3 },
  };
  var ALPHABET = "abcdefghijklmnopqrstuvwxyz";

  // Stages (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A stage is { name, words, speed, gap, shortest, longest } — shortest = longest = 1 is single letters.
  var LEVELS = [
    { name: "First drops", words: 10, speed: 0.25, gap: 120, shortest: 1, longest: 1 },
    { name: "Little words", words: 10, speed: 0.25, gap: 140, shortest: 3, longest: 3 },
    { name: "Four letters", words: 12, speed: 0.3, gap: 130, shortest: 3, longest: 4 },
    { name: "Drizzle", words: 14, speed: 0.35, gap: 110, shortest: 4, longest: 5 },
    { name: "Shower", words: 15, speed: 0.4, gap: 100, shortest: 4, longest: 6 },
    { name: "Long words", words: 12, speed: 0.3, gap: 140, shortest: 6, longest: 8 },
    { name: "Downpour", words: 18, speed: 0.45, gap: 80, shortest: 3, longest: 6 },
    { name: "Storm", words: 20, speed: 0.5, gap: 70, shortest: 4, longest: 7 },
    { name: "Big storm", words: 20, speed: 0.4, gap: 90, shortest: 6, longest: 10 },
    { name: "Cloudburst", words: 25, speed: 0.55, gap: 60, shortest: 3, longest: 8 },
  ];
  var FIELDS = { words: [5, 40], speed: [0.15, 1.4], gap: [GAP_MIN, 200], shortest: [1, MAX_LEN], longest: [1, MAX_LEN] };
  /** Seconds a word takes to fall at this speed (px an update). */
  function fallSeconds(speed) { return (GROUND - TOP_Y) / speed / 60; }
  function stageOk(t) {
    if (!t || typeof t !== "object" || typeof t.name !== "string" || !t.name) return false;
    for (var k in FIELDS) if (typeof t[k] !== "number" || !(t[k] >= FIELDS[k][0] && t[k] <= FIELDS[k][1])) return false;
    if (t.words !== Math.floor(t.words) || t.gap !== Math.floor(t.gap) || t.shortest !== Math.floor(t.shortest) || t.longest !== Math.floor(t.longest)) return false;
    if (t.shortest > t.longest) return false;
    if (!((t.shortest === 1 && t.longest === 1) || t.shortest >= 3)) return false;      // letters, or words of 3 and more
    return fallSeconds(t.speed) >= 1 + 0.35 * t.longest;                                // time to type the longest word
  }
  /** The stages to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(stageOk);
    return out.length ? out : LEVELS;
  }

  var BY_LEN = {};
  (function () {
    for (var n = 1; n <= MAX_LEN; n++) BY_LEN[n] = [];
    Words.WORDS.forEach(function (w) { if (w.length <= MAX_LEN) BY_LEN[w.length].push(w); });
  })();

  function rand(st) { // mulberry32 on st.wrng (the words' own stream)
    st.wrng = (st.wrng + 0x6D2B79F5) | 0;
    var t = Math.imul(st.wrng ^ (st.wrng >>> 15), 1 | st.wrng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function r2(v) { return Math.round(v * 1000) / 1000; }

  /** The falling settings now: { speed, gap, shortest, longest }. */
  function settings(s) {
    if (s.stages) return s.stages[s.level - 1];
    var m = MODES[s.mode], lv = s.level;
    var speed = Math.min(m.vmax, m.speed + m.ramp * (lv - 1)), gap = Math.max(m.gapMin, m.gap - m.gapStep * (lv - 1));
    if (s.mode === "letters") return { speed: speed, gap: gap, shortest: 1, longest: 1 };
    if (s.mode === "easy") return { speed: speed, gap: gap, shortest: 3, longest: 4 };
    return { speed: speed, gap: gap, shortest: Math.min(6, 3 + Math.floor((lv - 1) / 4)), longest: Math.min(MAX_LEN, 4 + Math.floor((lv - 1) / 2)) };
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, seed: (o.seed >>> 0) || 1, wrng: (o.seed >>> 0) || 1, level: 1, score: 0, lives: MODES[mode].lives,
      words: [], nextId: 1, target: -1, nextSpawn: FIRST_T, spawned: 0, recent: [], updates: 0, phase: "play", t: 0,
      run: 0, slipT: -100, over: false, won: false,
      stages: mode === "stages" ? usableLevels(o.levels) : null,
      stats: { typed: 0, letters: 0, slips: 0, landed: 0, bestRun: 0, stages: 0, cause: null },
    };
    if (s.stages) startStage(s);
    return s;
  }
  function startStage(s) {
    s.wrng = (s.seed ^ Math.imul(s.level, 0x9E3779B1)) | 0;      // each stage's own words, whatever the time
    s.spawned = 0; s.recent = []; s.words = []; s.target = -1; s.phase = "play";
    s.nextSpawn = s.updates + FIRST_T;
  }

  function pickWord(s, set) {
    if (set.longest === 1) return ALPHABET[Math.floor(rand(s) * 26)];
    var word = "";
    for (var tries = 0; tries < 6; tries++) {
      var len = set.shortest + Math.floor(rand(s) * (set.longest - set.shortest + 1));
      while (!BY_LEN[len].length && len > 3) len--;
      var list = BY_LEN[len];
      word = list[Math.floor(rand(s) * list.length)];
      if (s.recent.indexOf(word) < 0) break;
    }
    return word;
  }
  function spawn(s, evs) {
    var set = settings(s), text = pickWord(s, set), w = text.length * CHAR_W + PAD;
    var x = r2(LEFT + rand(s) * (RIGHT - LEFT - w));
    var v = r2(set.speed * (text.length >= 7 ? 0.85 : 1));
    s.words.push({ id: s.nextId++, text: text, x: x, y: TOP_Y, v: v, typed: 0 });
    s.recent.push(text); if (s.recent.length > RECENT) s.recent.shift();
    s.spawned++;
    s.nextSpawn += set.gap;
    evs.push({ type: "spawn", word: text });
  }
  function find(s, id) { for (var i = 0; i < s.words.length; i++) if (s.words[i].id === id) return i; return -1; }

  function step(s) {
    var evs = [], i;
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "clear") {
      if (--s.t <= 0) { s.level++; startStage(s); evs.push({ type: "level", level: s.level }); }
      return evs;
    }
    if (!s.stages) {
      var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.updates / LEVEL_T));
      if (lv !== s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
    }
    var more = !s.stages || s.spawned < settings(s).words;
    if (more && s.updates >= s.nextSpawn) spawn(s, evs);
    for (i = s.words.length - 1; i >= 0; i--) {
      var w = s.words[i];
      w.y = r2(w.y + w.v);
      if (w.y >= GROUND) {
        s.words.splice(i, 1);
        if (s.target === w.id) s.target = -1;
        s.lives--; s.run = 0; s.stats.landed++;
        evs.push({ type: "land", word: w.text, lives: s.lives });
        if (s.lives <= 0) {
          s.over = true; s.stats.cause = "landed";
          evs.push({ type: "gameover", score: s.score });
          return evs;
        }
      }
    }
    if (s.stages && !more && !s.words.length) {
      s.score += STAGE_POINTS; s.stats.stages++;
      evs.push({ type: "stage", level: s.level, score: s.score });
      if (s.level >= s.stages.length) {
        s.won = true; s.over = true; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
      } else { s.phase = "clear"; s.t = CLEAR_T; }
    }
    return evs;
  }

  function slip(s, evs, letter) {
    s.run = 0; s.stats.slips++; s.slipT = s.updates;
    evs.push({ type: "slip", letter: letter });
  }
  /** A typed letter (a–z). */
  function type(s, letter) {
    var evs = [];
    if (s.over || s.phase !== "play") return evs;
    letter = String(letter).toLowerCase();
    if (!/^[a-z]$/.test(letter)) return evs;
    var at = s.target >= 0 ? find(s, s.target) : -1;
    if (at < 0) {
      s.target = -1;
      var best = -1;
      for (var i = 0; i < s.words.length; i++) if (s.words[i].text[0] === letter && (best < 0 || s.words[i].y > s.words[best].y)) best = i;
      if (best < 0) { slip(s, evs, letter); return evs; }
      at = best; s.target = s.words[best].id; s.words[best].typed = 0;
    }
    var w = s.words[at];
    if (w.text[w.typed] !== letter) { slip(s, evs, letter); return evs; }
    w.typed++;
    evs.push({ type: "key", letter: letter });
    if (w.typed >= w.text.length) {
      s.run++; s.stats.bestRun = Math.max(s.stats.bestRun, s.run);
      var mult = s.run >= RUN3 ? 3 : s.run >= RUN2 ? 2 : 1, pts = LETTER_POINTS * w.text.length * mult;
      s.score += pts; s.stats.typed++; s.stats.letters += w.text.length;
      s.words.splice(at, 1); s.target = -1;
      evs.push({ type: "word", word: w.text, points: pts, run: s.run });
    }
    return evs;
  }
  function letGo(s) {
    var at = s.target >= 0 ? find(s, s.target) : -1;
    if (at >= 0) s.words[at].typed = 0;
    s.target = -1;
    return at >= 0 ? [{ type: "letgo" }] : [];
  }

  /** Typed keys ("key:A" … "key:Z", Backspace); the on-screen keyboard sends the same. */
  function press(s, action, down) {
    if (!down || s.over) return [];
    if (action.indexOf("key:") === 0) {
      var k = action.slice(4);
      if (/^[A-Z]$/.test(k)) return type(s, k);
      if (k === "BACKSPACE" || k === "DELETE") return letGo(s);
    }
    return [];
  }

  function wpm(s) { var sec = s.updates / 60; return sec >= 1 ? Math.round(s.stats.letters / 5 / (sec / 60)) : 0; }
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "stages") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.words) && typeof data.score === "number" &&
      typeof data.level === "number" && data.level >= 1 && typeof data.lives === "number" && typeof data.updates === "number" &&
      typeof data.wrng === "number" && typeof data.nextSpawn === "number" && data.stats && typeof data.stats === "object" &&
      data.words.every(function (w) { return w && typeof w.text === "string" && /^[a-z]{1,10}$/.test(w.text) && typeof w.y === "number" && typeof w.x === "number"; });
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.stages = s.mode === "stages" ? usableLevels(levels) : null;
    if (s.stages && s.level > s.stages.length) s.level = s.stages.length;
    if (!s.stages && s.level > MAX_LEVEL) s.level = MAX_LEVEL;
    return s;
  }
  function result(s) {
    return { score: s.score, level: s.level, stats: { typed: s.stats.typed, letters: s.stats.letters, slips: s.stats.slips,
      landed: s.stats.landed, bestRun: s.stats.bestRun, stages: s.stats.stages, wpm: wpm(s), livesLeft: Math.max(0, s.lives),
      won: !!s.won, cause: s.stats.cause, mode: s.mode,
      summary: [s.stats.typed + (s.stats.typed === 1 ? " word" : " words") + " · " + wpm(s) + " words a minute", "Best run " + s.stats.bestRun] } };
  }

  var TypeRainLogic = {
    GROUND: GROUND, TOP_Y: TOP_Y, LEFT: LEFT, RIGHT: RIGHT, CHAR_W: CHAR_W, PAD: PAD, LEVEL_T: LEVEL_T, MAX_LEVEL: MAX_LEVEL,
    FIRST_T: FIRST_T, CLEAR_T: CLEAR_T, STATE_VERSION: STATE_VERSION, LETTER_POINTS: LETTER_POINTS, RUN2: RUN2, RUN3: RUN3,
    STAGE_POINTS: STAGE_POINTS, MAX_LEN: MAX_LEN, GAP_MIN: GAP_MIN, MODES: MODES, LEVELS: LEVELS, BY_LEN: BY_LEN,
    usableLevels: usableLevels, stageOk: stageOk, fallSeconds: fallSeconds, settings: settings,
    create: create, step: step, press: press, type: type, letGo: letGo, find: find, wpm: wpm, save: save, restore: restore,
    result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = TypeRainLogic; else self.TypeRainLogic = TypeRainLogic;
})();
