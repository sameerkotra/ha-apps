/* Household Arcade — Flap rules (pure: no DOM, deterministic for a given seed).

   A small round flyer falls unless you flap; fly it through the gaps in the gates coming from the
   right. One point a gate. Every 10 gates the gaps get a little narrower and the gates a little
   quicker. The game waits for the first flap. One call to step() is one update (60 a second). */
(function () {
  "use strict";

  var W = 240, H = 300;
  var TOP = 36, GROUND = 278;
  var X = 72, R = 8;                              // the flyer's x and size (for hits)
  var GRAVITY = 0.15, FLAP = -2.7, MAX_FALL = 3.8;   // gentle: one flap lifts about 24 px
  var GATE_W = 34, SPACING = 124;                 // gate width; from one gate to the next
  var CHANGE_MAX = 56;                            // how far a gap's middle can move from the last one
  var GATES_PER_LEVEL = 10;
  var MODES = {
    easy: { gap: 100, minGap: 82, speed: 1.4, move: 0 },
    normal: { gap: 86, minGap: 68, speed: 1.6, move: 0 },
    moving: { gap: 90, minGap: 72, speed: 1.6, move: 16 },
    course: { gap: 86, minGap: 66, speed: 1.6, move: 0 },     // Courses: the numbers come from the level list
  };

  // Courses (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A course is
  // { name, gates, gap, speed, sway, spacing, heights }: `heights` is a pattern of digits 0 (high) – 9 (low)
  // for the gaps' middles, used in turn; next to each other they differ by at most 4.
  var LEVELS = [
    { name: "First flight", gates: 8, gap: 104, speed: 1.4, sway: 0, spacing: 150, heights: "4455" },
    { name: "Gentle hills", gates: 10, gap: 100, speed: 1.4, sway: 0, spacing: 140, heights: "34565432" },
    { name: "Up and down", gates: 12, gap: 96, speed: 1.5, sway: 0, spacing: 136, heights: "2468642" },
    { name: "Breeze", gates: 12, gap: 94, speed: 1.5, sway: 8, spacing: 134, heights: "5544332211" },
    { name: "Staircase", gates: 14, gap: 90, speed: 1.6, sway: 0, spacing: 130, heights: "0123456789876543" },
    { name: "Zigzag", gates: 14, gap: 88, speed: 1.7, sway: 0, spacing: 128, heights: "373737" },
    { name: "Windy valley", gates: 16, gap: 88, speed: 1.7, sway: 14, spacing: 126, heights: "6789876" },
    { name: "Treetops", gates: 16, gap: 84, speed: 1.8, sway: 0, spacing: 124, heights: "1212343" },
    { name: "Storm", gates: 18, gap: 82, speed: 1.9, sway: 18, spacing: 122, heights: "48484" },
    { name: "Long way home", gates: 20, gap: 80, speed: 2, sway: 10, spacing: 120, heights: "5373537573" },
  ];
  var FIELDS = { gates: [8, 40], gap: [66, 110], speed: [1.2, 2.6], sway: [0, 24], spacing: [110, 170] };

  /** The courses to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c.name !== "string" || typeof c.heights !== "string" || !/^[0-9]{4,40}$/.test(c.heights)) return false;
      for (var k in FIELDS) if (typeof c[k] !== "number" || !(c[k] >= FIELDS[k][0] && c[k] <= FIELDS[k][1])) return false;
      for (var i = 1; i < c.heights.length; i++) if (Math.abs(c.heights.charCodeAt(i) - c.heights.charCodeAt(i - 1)) > 4) return false;
      return true;
    });
    return out.length ? out : LEVELS;
  }
  function course(s, n) { return s.courses ? s.courses[Math.min(n, s.courses.length) - 1] : null; }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function gap(s) {
    if (s.courses) return course(s, s.spawnLevel).gap;
    var m = MODES[s.mode]; return Math.max(m.minGap, m.gap - 3 * (s.level - 1));
  }
  function speed(s) {
    if (s.courses) return course(s, s.level).speed;
    return Math.min(2.6, MODES[s.mode].speed + 0.08 * (s.level - 1));
  }
  function spacing(s) { return s.courses ? course(s, s.spawnLevel).spacing : SPACING; }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES[o.mode] ? o.mode : "normal", rng: (o.seed >>> 0) || 1,
      y: (TOP + GROUND) / 2, vy: 0, started: false, score: 0, level: 1,
      gates: [], lastMid: (TOP + GROUND) / 2, over: false, won: false, updates: 0, t: 0,
      courses: null, spawnLevel: 1, spawned: 0,
      stats: { gates: 0, flaps: 0, courses: 0, cause: null },
    };
    if (s.mode === "course") s.courses = usableLevels(o.levels);
    for (var x = W + 40; x < W + 40 + SPACING * 3 && addGate(s, x); x += spacing(s)) { /* the first gates */ }
    return s;
  }

  /** Add the next gate at x; false when a course list has no more gates to give. */
  function addGate(s, x) {
    var g = gap(s), lo = TOP + 26 + g / 2, hi = GROUND - 26 - g / 2, mid, last = false, sway = 0;
    if (s.courses) {
      var c = course(s, s.spawnLevel);
      if (s.spawnLevel > s.courses.length) return false;
      mid = lo + (hi - lo) * (+c.heights[s.spawned % c.heights.length]) / 9;
      sway = c.sway;
      s.spawned++;
      if (s.spawned >= c.gates) { last = true; s.spawnLevel++; s.spawned = 0; }
    } else {
      mid = clamp(s.lastMid + (rand(s) * 2 - 1) * CHANGE_MAX, lo, hi);
      sway = MODES[s.mode].move;
    }
    s.lastMid = mid;
    s.gates.push({ x: x, mid: mid, gap: g, sway: sway, last: last, phase: rand(s) * Math.PI * 2, passed: false });
    return true;
  }

  /** The middle of a gate's gap now (gates sway up and down in Moving). */
  function gateMid(s, gt) {
    var m = gt.sway != null ? gt.sway : MODES[s.mode].move;
    if (!m) return gt.mid;
    var lo = TOP + 8 + gt.gap / 2, hi = GROUND - 8 - gt.gap / 2;
    return clamp(gt.mid + Math.sin(gt.phase + s.t * 0.03) * m, lo, hi);
  }

  function hitsGate(s, gt) {
    var mid = gateMid(s, gt), top = mid - gt.gap / 2, bot = mid + gt.gap / 2;
    // circle against the two blocks of the gate
    var cx = clamp(X, gt.x, gt.x + GATE_W);
    if (Math.abs(cx - X) >= R) return false;
    function boxHit(y0, y1) {
      var cy = clamp(s.y, y0, y1), dx = cx - X, dy = cy - s.y;
      return dx * dx + dy * dy < R * R;
    }
    return boxHit(TOP - 50, top) || boxHit(bot, GROUND + 50);
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (!s.started) return evs;     // hovering until the first flap
    s.t++;
    s.vy = Math.min(MAX_FALL, s.vy + GRAVITY);
    s.y += s.vy;
    if (s.y < TOP + R) { s.y = TOP + R; s.vy = 0; }
    var v = speed(s), i;
    for (i = 0; i < s.gates.length; i++) s.gates[i].x -= v;
    while (s.gates.length && s.gates[0].x + GATE_W < -4) s.gates.shift();
    var last = s.gates[s.gates.length - 1];
    if (!last || last.x < W + 40 - spacing(s)) addGate(s, last ? last.x + spacing(s) : W + 40);
    for (i = 0; i < s.gates.length; i++) {
      var gt = s.gates[i];
      if (hitsGate(s, gt)) { end(s, evs, "gate"); return evs; }
      if (!gt.passed && gt.x + GATE_W < X - R) {
        gt.passed = true; s.score++; s.stats.gates++;
        evs.push({ type: "gate", score: s.score });
        if (s.courses) {
          if (gt.last) {                       // the end of a course: the next one, or the game is won
            s.stats.courses++;
            if (s.level >= s.courses.length) { s.won = true; s.over = true; s.stats.cause = "won"; evs.push({ type: "win", score: s.score }); return evs; }
            s.level++; evs.push({ type: "level", level: s.level });
          }
        } else {
          var lv = 1 + Math.floor(s.score / GATES_PER_LEVEL);
          if (lv > s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
        }
      }
    }
    if (s.y + R >= GROUND) { s.y = GROUND - R; end(s, evs, "ground"); }
    return evs;
  }

  function end(s, evs, cause) {
    s.over = true; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause });
  }

  function flap(s) {
    if (s.over) return [];
    s.started = true;
    s.vy = FLAP; s.stats.flaps++;
    return [{ type: "flap" }];
  }

  function press(s, action, down) {
    if (down && (action === "fire" || action === "up")) return flap(s);
    return [];
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "courses") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.gates) &&
      typeof data.y === "number" && typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.started = false; s.vy = 0;     // a continued game waits for a flap, so it doesn't fall straight away
    s.courses = s.mode === "course" ? usableLevels(levels) : null;
    if (s.spawnLevel == null) { s.spawnLevel = 1; s.spawned = 0; }
    if (!s.stats.courses) s.stats.courses = 0;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { gates: s.stats.gates, flaps: s.stats.flaps, courses: s.stats.courses || 0,
      won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var FlapLogic = {
    W: W, H: H, TOP: TOP, GROUND: GROUND, X: X, R: R, GRAVITY: GRAVITY, FLAP: FLAP, GATE_W: GATE_W, SPACING: SPACING,
    GATES_PER_LEVEL: GATES_PER_LEVEL, MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    create: create, step: step, press: press, flap: flap, gap: gap, speed: speed, gateMid: gateMid, hitsGate: hitsGate,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = FlapLogic; else self.FlapLogic = FlapLogic;
})();
