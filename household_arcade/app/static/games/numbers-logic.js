/* Household Arcade — Number Dash rules (pure: no DOM, deterministic for a given seed).

   A sum at the top, four answers in a diamond (up, left, right, down): pick the right one before the
   clock runs out. The clock starts at 60 s; a right answer adds 1 s (at most 60), a wrong one takes
   3 s off and shows the right answer. The next sum comes 12 updates after a right answer (45 after a
   wrong one, so the right answer can be seen); answers in between are ignored. A level every 8 right
   answers; the numbers grow with the level. Score: 10 + 2 × level for a right answer, doubled from 5
   right in a row and tripled from 10. Modes: Add & take away, Times tables (× and ÷), Mixed, and
   Challenges (a list of challenges, SPEC §11.9: each sets its sums, how big the numbers get, the right
   answers to clear it and the seconds on a fresh clock; clearing the last one wins).
   One call to step() is one update (60 a second).

   Honest score: at most one answer per 12 updates (5 a second), each worth at most
   3 × (10 + 2 × MAX_LEVEL) = 180 (the level in the points is capped at MAX_LEVEL, also in Challenges). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var MODES = { add: 1, times: 1, mixed: 1, challenge: 1 };
  var DIRS = ["up", "left", "right", "down"];
  var CLOCK = 3600, RIGHT_BONUS = 60, WRONG_COST = 180;
  var NEXT_RIGHT = 12, NEXT_WRONG = 45;
  var RIGHT_PER_LEVEL = 8, MAX_LEVEL = 25;
  var MINUS = "−", TIMES = "×", DIVIDE = "÷";

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function ri(s, a, b) { return a + Math.floor(rand(s) * (b - a + 1)); }   // a whole number a … b

  function points(level, streak) {
    return (10 + 2 * Math.min(MAX_LEVEL, level)) * (streak >= 10 ? 3 : streak >= 5 ? 2 : 1);
  }

  // Challenges (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A challenge is
  // { name, ops, top, right, time }: `ops` the sums ("+" add, "-" take away, "x" times, "/" share), `top`
  // the biggest number in a + or − sum (the total, or the number taken from), and for × and ÷ the biggest
  // table: min(top, 12), the other factor 1 … max(10, that); `right` answers clear it; `time` the seconds
  // on the clock at its start (a right answer adds 1 s, never above that; a wrong one takes 3 s off).
  var LEVELS = [
    { name: "Warm up", ops: "+", top: 10, right: 6, time: 60 },
    { name: "Take away", ops: "-", top: 10, right: 6, time: 50 },
    { name: "Small tables", ops: "x", top: 5, right: 8, time: 60 },
    { name: "Up to twenty", ops: "+-", top: 20, right: 8, time: 50 },
    { name: "Sharing out", ops: "/", top: 6, right: 8, time: 60 },
    { name: "Fifty", ops: "+-", top: 50, right: 10, time: 60 },
    { name: "Times tables", ops: "x/", top: 10, right: 12, time: 60 },
    { name: "Hundred club", ops: "+-", top: 100, right: 12, time: 60 },
    { name: "All sorts", ops: "+-x/", top: 12, right: 15, time: 60 },
    { name: "Big numbers", ops: "+-x/", top: 500, right: 20, time: 90 },
  ];
  var FIELDS = { top: [5, 999], right: [5, 25], time: [20, 90] };

  /** The challenges to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c !== "object" || typeof c.name !== "string" || typeof c.ops !== "string" || !/^[-+x/]{1,4}$/.test(c.ops)) return false;
      for (var k in FIELDS) if (typeof c[k] !== "number" || c[k] !== Math.floor(c[k]) || !(c[k] >= FIELDS[k][0] && c[k] <= FIELDS[k][1])) return false;
      return c.time >= minTime(c);
    });
    return out.length ? out : LEVELS;
  }
  /** The fewest seconds a challenge may give: 2 a right answer, 3 with + or − past 99. */
  function minTime(c) { return c.right * (/[-+]/.test(c.ops) && c.top > 99 ? 3 : 2); }
  function challenge(s, n) { return s.challenges ? s.challenges[Math.min(n, s.challenges.length) - 1] : null; }
  function startClock(s) { return s.challenges ? challenge(s, s.level).time * 60 : CLOCK; }

  // Two numbers that add up, harder with the level:
  // 1 small, 2 one digit, 3 two digits + one (no carrying), 4 two digits + two (no carrying),
  // 5–6 two digits with carrying, 7–9 any two-digit numbers, 10–12 three digits + two, 13+ three + three.
  function addPair(s, L) {
    var a, b, a0, b0, a1, b1;
    if (L <= 1) { a = ri(s, 1, 5); b = ri(s, 1, 5); }
    else if (L === 2) { a = ri(s, 2, 9); b = ri(s, 1, 9); }
    else if (L === 3) { a1 = ri(s, 1, 8); a0 = ri(s, 0, 8); b = ri(s, 1, 9 - a0); a = a1 * 10 + a0; }
    else if (L === 4) { a1 = ri(s, 1, 7); b1 = ri(s, 1, 8 - a1); a0 = ri(s, 0, 8); b0 = ri(s, 0, 9 - a0); a = a1 * 10 + a0; b = b1 * 10 + b0; }
    else if (L <= 6) { a0 = ri(s, 2, 9); b0 = ri(s, 11 - a0, 9); a1 = ri(s, 1, 7); b1 = ri(s, L === 5 ? 0 : 1, 8 - a1); a = a1 * 10 + a0; b = b1 * 10 + b0; }
    else if (L <= 9) { a = ri(s, 11, 99); b = ri(s, 11, 99); }
    else if (L <= 12) { a = ri(s, 100, 899); b = ri(s, 11, 99); }
    else { a = ri(s, 100, 899); b = ri(s, 100, 899); }
    return [a, b];
  }

  // Tables by level: 2, 5, 10 → + 3 → + 4 → + 6 → 2 … 10 → 2 … 12 (factors up to 12 from level 6).
  var TABLES = [[2, 5, 10], [2, 3, 5, 10], [2, 3, 4, 5, 10], [2, 3, 4, 5, 6, 10], [2, 3, 4, 5, 6, 7, 8, 9, 10]];
  function timesPair(s, L) {
    var t = L <= TABLES.length ? TABLES[L - 1] : [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    var a = t[ri(s, 0, t.length - 1)], b = ri(s, 1, L >= 6 ? 12 : 10);
    return rand(s) < 0.5 ? [a, b] : [b, a];
  }

  /** A question for a challenge: an op from its list, numbers up to its top. */
  function challengeQuestion(s) {
    var c = challenge(s, s.level), ops = [];
    for (var i = 0; i < c.ops.length; i++) if (ops.indexOf(c.ops.charAt(i)) < 0) ops.push(c.ops.charAt(i));
    var op = ops[ri(s, 0, ops.length - 1)];
    if (op === "+" || op === "-") {
      var total = ri(s, 2, c.top), a = ri(s, 1, total - 1), b = total - a;
      return op === "+" ? { text: a + " + " + b, answer: total, a: a, b: b, op: "+" }
        : { text: total + " " + MINUS + " " + b, answer: a, a: total, b: b, op: "-" };
    }
    var T = Math.min(12, c.top), t = ri(s, 2, T), f = ri(s, 1, Math.max(10, T)), prod = t * f;
    if (op === "x") return rand(s) < 0.5 ? { text: t + " " + TIMES + " " + f, answer: prod, a: t, b: f, op: "x" }
      : { text: f + " " + TIMES + " " + t, answer: prod, a: f, b: t, op: "x" };
    return { text: prod + " " + DIVIDE + " " + t, answer: f, a: prod, b: t, op: "/" };
  }

  function makeQuestion(s) {
    if (s.challenges) { var cq = challengeQuestion(s); cq.choices = choices(s, cq); return cq; }
    var L = s.level, kind = s.mode === "mixed" ? (rand(s) < 0.5 ? "add" : "times") : s.mode, q;
    if (kind === "add") {
      var p = addPair(s, L), sum = p[0] + p[1];
      if (rand(s) < 0.5) q = { text: p[0] + " + " + p[1], answer: sum, a: p[0], b: p[1], op: "+" };
      else q = { text: sum + " " + MINUS + " " + p[1], answer: p[0], a: sum, b: p[1], op: "-" };
    } else {
      var t = timesPair(s, L), prod = t[0] * t[1];
      if (rand(s) < 0.6) q = { text: t[0] + " " + TIMES + " " + t[1], answer: prod, a: t[0], b: t[1], op: "x" };
      else q = { text: prod + " " + DIVIDE + " " + t[0], answer: t[1], a: prod, b: t[0], op: "/" };
    }
    q.choices = choices(s, q);
    return q;
  }

  /** Four different, non-negative answers (one right, three near misses), in the order up, left, right, down. */
  function choices(s, q) {
    var ans = q.answer, cand = [ans + 1, ans - 1, ans + 10, ans - 10, ans + 2, ans - 2];
    var str = String(ans);
    if (str.length >= 2) {
      var sw = str.slice(0, -2) + str.charAt(str.length - 1) + str.charAt(str.length - 2);
      cand.push(Number(sw), Number(sw), ans + 100 * (ans >= 100 ? 1 : 0));
    }
    if (q.op === "x") cand.push(q.a * (q.b + 1), q.a * (q.b - 1), (q.a + 1) * q.b, ans + q.a, ans - q.b);
    if (q.op === "/") cand.push(ans + 1, ans - 1, q.a - q.b > 0 ? Math.round(q.a / (q.b + 1)) : ans + 3);
    if (q.op === "+") cand.push(Math.abs(q.a - q.b));      // took away instead of adding
    var wrong = [];
    while (cand.length && wrong.length < 3) {
      var c = cand.splice(Math.floor(rand(s) * cand.length), 1)[0];
      if (c >= 0 && c !== ans && wrong.indexOf(c) < 0 && isFinite(c)) wrong.push(c);
    }
    for (var k = 3; wrong.length < 3; k++) if (wrong.indexOf(ans + k) < 0) wrong.push(ans + k);
    var at = ri(s, 0, 3), out = [];
    for (var i = 0, w = 0; i < 4; i++) out.push(i === at ? ans : wrong[w++]);
    return out;
  }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES[o.mode] ? o.mode : "add", rng: (o.seed >>> 0) || 1,
      clock: CLOCK, score: 0, level: 1, right: 0, streak: 0, wait: 0, q: null, last: null,
      over: false, won: false, updates: 0, n: 0, cright: 0,
      stats: { right: 0, wrong: 0, bestStreak: 0, challenges: 0, cause: null },
      challenges: null,   // the list (kept out of save(): restore() is given it again); last, so restore keeps the key order
    };
    if (s.mode === "challenge") { s.challenges = usableLevels(o.levels); s.level = startAt(o.startLevel, s.challenges.length); s.clock = startClock(s); }
    s.q = makeQuestion(s); s.n = 1;
    return s;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    s.clock--;
    if (s.clock <= 0) {
      s.clock = 0; s.over = true; s.stats.cause = "time";
      evs.push({ type: "gameover", score: s.score });
      return evs;
    }
    if (s.wait > 0 && --s.wait === 0) {
      s.q = makeQuestion(s); s.n++; s.last = null;
      evs.push({ type: "question", n: s.n });
    }
    return evs;
  }

  /** Pick the answer in direction dir ("up", "left", "right", "down"). */
  function answer(s, dir) {
    var i = DIRS.indexOf(dir);
    if (s.over || s.wait > 0 || i < 0) return [];
    var ok = s.q.choices[i] === s.q.answer, evs = [];
    s.last = { pick: i, ok: ok, right: s.q.choices.indexOf(s.q.answer) };
    if (ok) {
      s.streak++; s.right++; s.stats.right++;
      if (s.streak > s.stats.bestStreak) s.stats.bestStreak = s.streak;
      var pts = points(s.level, s.streak);
      s.score += pts;
      s.clock = Math.min(startClock(s), s.clock + RIGHT_BONUS);
      s.wait = NEXT_RIGHT;
      evs.push({ type: "right", points: pts, streak: s.streak, score: s.score });
      if (s.streak === 5 || s.streak === 10) evs.push({ type: "streak", streak: s.streak, times: s.streak === 5 ? 2 : 3 });
      if (s.challenges) {
        if (++s.cright >= challenge(s, s.level).right) {     // cleared: the next challenge, or the game is won
          s.stats.challenges++;
          if (s.level >= s.challenges.length) {
            s.won = true; s.over = true; s.stats.cause = "won";
            evs.push({ type: "win", score: s.score });
          } else {
            s.level++; s.cright = 0; s.clock = startClock(s);
            evs.push({ type: "level", level: s.level, name: challenge(s, s.level).name });
          }
        }
        return evs;
      }
      var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.right / RIGHT_PER_LEVEL));
      if (lv > s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
    } else {
      s.streak = 0; s.stats.wrong++;
      s.clock = Math.max(0, s.clock - WRONG_COST);
      s.wait = NEXT_WRONG;
      evs.push({ type: "wrong", answer: s.q.answer });
      if (s.clock <= 0) { s.over = true; s.stats.cause = "time"; evs.push({ type: "gameover", score: s.score }); }
    }
    return evs;
  }

  function press(s, action, down) {
    if (!down) return [];
    return answer(s, action);
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "challenges") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && data.q && Array.isArray(data.q.choices) &&
      data.q.choices.length === 4 && typeof data.q.answer === "number" && typeof data.q.text === "string" &&
      typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" &&
      typeof data.clock === "number" && typeof data.wait === "number" && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.challenges = s.mode === "challenge" ? usableLevels(levels) : null;
    if (s.challenges && s.level > s.challenges.length) s.level = s.challenges.length;   // a shorter list now
    if (s.cright == null) s.cright = 0;
    if (s.won == null) s.won = false;
    if (!s.stats.challenges) s.stats.challenges = 0;
    if (s.stats.cause === undefined) s.stats.cause = null;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { right: s.stats.right, wrong: s.stats.wrong,
      bestStreak: s.stats.bestStreak, challenges: s.stats.challenges || 0, won: !!s.won,
      cause: s.stats.cause || null, mode: s.mode } };
  }

  var NumbersLogic = {
    MODES: MODES, DIRS: DIRS, CLOCK: CLOCK, RIGHT_BONUS: RIGHT_BONUS, WRONG_COST: WRONG_COST,
    NEXT_RIGHT: NEXT_RIGHT, NEXT_WRONG: NEXT_WRONG, RIGHT_PER_LEVEL: RIGHT_PER_LEVEL, MAX_LEVEL: MAX_LEVEL,
    STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels, challenge: challenge,
    startClock: startClock, minTime: minTime,
    create: create, step: step, press: press, answer: answer, points: points, makeQuestion: makeQuestion,
    addPair: addPair, timesPair: timesPair, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = NumbersLogic; else self.NumbersLogic = NumbersLogic;
})();
