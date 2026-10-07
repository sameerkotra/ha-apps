/* Household Arcade — Lander rules (pure: no DOM, deterministic for a given seed).

   Bring a small lander down onto a landing pad. Gravity pulls it down; the engine pushes it the way it points
   (turned up to 90° either way, 3° an update) and burns one unit of fuel an update; out of fuel, it falls. A
   wind pushes it sideways on some levels. Touching down on a pad, upright (within 10°), slowly (at most 0.9 px
   an update down and 0.6 sideways) lands: pad multiplier × 100 + half the fuel left. Anything else crashes and
   costs one of 3 lives (the level starts again). The ground is 13 heights 20 px apart; a pad is a flat stretch
   marked with its multiplier (×1 wide and easy … ×5 small). Classic and Easy make their levels from the seed;
   Levels plays the level list. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var W = 240, TOP = 38, FLOOR = 290, STEP_X = 20, N = 13, START_Y = 76, FOOT = 7, HALF = 6;
  var THRUST = 0.06, TURN = 3, MAX_TILT = 90, SAFE_VY = 0.9, SAFE_VX = 0.6, SAFE_TILT = 10;
  var LAND_T = 120, CRASH_T = 90, MAX_LEVEL = 100, PAD_POINTS = 100;
  var MODES = { classic: { lives: 3 }, easy: { lives: 3 }, levels: { lives: 3 } };
  // sin by its series (only + − × ÷, so every browser gets exactly the same numbers; Math.sin may differ in the last digit)
  function dsin(x) { var x2 = x * x, t = x, sum = x; for (var n = 1; n <= 9; n++) { t = -t * x2 / ((2 * n) * (2 * n + 1)); sum += t; } return sum; }
  var HALF_PI = 1.5707963267948966;
  var SIN = [], COS = [];      // by whole degrees
  for (var d = -MAX_TILT; d <= MAX_TILT; d++) { SIN[d + MAX_TILT] = dsin(d * HALF_PI / 90); COS[d + MAX_TILT] = dsin(HALF_PI - Math.abs(d) * HALF_PI / 90); }

  // Levels (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A level is { name, heights: [13 heights 0–150 px], pads: "12 characters . or 1–5", start, drift, gravity, wind, fuel }.
  var LEVELS = [
    { name: "Practice field", heights: [40, 40, 40, 30, 30, 30, 30, 30, 50, 60, 60, 70, 80], pads: "...111......", start: 70, drift: 0, gravity: 0.015, wind: 0, fuel: 800 },
    { name: "Two valleys", heights: [90, 60, 30, 30, 70, 110, 80, 40, 40, 60, 100, 120, 130], pads: "..2....3....", start: 120, drift: 0.3, gravity: 0.018, wind: 0, fuel: 700 },
    { name: "Crater rim", heights: [120, 100, 70, 40, 20, 20, 20, 40, 80, 80, 110, 130, 140], pads: "....11..4...", start: 200, drift: -0.4, gravity: 0.02, wind: 0, fuel: 650 },
    { name: "Windy ridge", heights: [30, 50, 80, 80, 60, 30, 30, 50, 90, 120, 120, 90, 60], pads: "..3..2...4..", start: 40, drift: 0.2, gravity: 0.02, wind: 0.004, fuel: 650 },
    { name: "Needle peaks", heights: [20, 90, 140, 60, 60, 130, 50, 50, 120, 140, 140, 70, 20], pads: "...4..3..5..", start: 120, drift: 0, gravity: 0.024, wind: 0, fuel: 600 },
    { name: "Deep canyon", heights: [140, 140, 130, 100, 50, 10, 10, 10, 60, 110, 130, 140, 140], pads: "5....22.....", start: 180, drift: -0.5, gravity: 0.026, wind: -0.003, fuel: 600 },
    { name: "High shelf", heights: [60, 80, 120, 150, 150, 110, 70, 40, 40, 40, 80, 100, 100], pads: "...4...11..3", start: 60, drift: 0.6, gravity: 0.03, wind: 0.002, fuel: 550 },
    { name: "Storm plain", heights: [50, 50, 70, 40, 40, 80, 100, 60, 60, 30, 30, 50, 70], pads: "3..2...4.5..", start: 200, drift: -0.3, gravity: 0.03, wind: -0.007, fuel: 550 },
    { name: "Last stop", heights: [130, 100, 60, 60, 120, 150, 140, 90, 90, 140, 80, 20, 20], pads: "..3....5...2", start: 120, drift: 0.5, gravity: 0.036, wind: 0.005, fuel: 500 },
  ];

  /** The least fuel a level needs: a free fall from the start to the lowest ground, then a full burn — twice, + 100. */
  function fuelNeeded(gravity) {
    var dd = FLOOR - START_Y - FOOT, net = THRUST - gravity;
    var v = Math.sqrt(2 * dd * gravity * net / THRUST);
    return 100 + 2 * v / net;
  }
  /** Why a level can't be played, or null. */
  function levelProblem(l) {
    var pads = 0;
    for (var i = 0; i < 12; i++) {
      if (l.pads[i] === ".") continue;
      pads++;
      if (l.heights[i] !== l.heights[i + 1]) return "a pad must be flat";
    }
    if (!pads) return "no pad";
    if (l.fuel < fuelNeeded(l.gravity) - 1) return "not enough fuel";
    return null;
  }
  function inRange(v, lo, hi) { return typeof v === "number" && v >= lo && v <= hi; }
  function isInt(v, lo, hi) { return inRange(v, lo, hi) && v === Math.floor(v); }
  /** The levels to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (l) {
      if (!l || typeof l !== "object" || typeof l.name !== "string" || !l.name) return false;
      if (!Array.isArray(l.heights) || l.heights.length !== N || !l.heights.every(function (h) { return isInt(h, 0, 150); })) return false;
      if (typeof l.pads !== "string" || !/^[.1-5]{12}$/.test(l.pads)) return false;
      if (!isInt(l.start, 10, 230) || !inRange(l.drift, -1, 1) || !inRange(l.gravity, 0.01, 0.04) ||
        !inRange(l.wind, -0.01, 0.01) || !isInt(l.fuel, 150, 1000)) return false;
      return levelProblem(l) === null;
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function r3(v) { return Math.round(v * 1000) / 1000; }

  /** A made-up level for Classic and Easy (level n), from the seed. */
  function makeLevel(s, n) {
    var easy = s.mode === "easy", h = [], i;
    var top = easy ? 110 : Math.min(150, 90 + 6 * n);
    h[0] = 20 + Math.floor(rand(s) * (top - 20));
    for (i = 1; i < N; i++) h[i] = Math.max(0, Math.min(top, h[i - 1] + Math.floor((rand(s) - 0.5) * (easy ? 60 : 90))));
    var pads = "............".split(""), count = easy ? 2 : 2 + Math.floor(rand(s) * 2), tries = 0;
    while (count > 0 && tries++ < 60) {
      var wide = easy || rand(s) < 0.4, at = Math.floor(rand(s) * (wide ? 11 : 12));
      var ok = true;
      for (var k = Math.max(0, at - 1); k <= Math.min(11, at + (wide ? 2 : 1)); k++) if (pads[k] !== ".") ok = false;
      if (!ok) continue;
      var mult = wide ? (easy ? 1 : 1 + Math.floor(rand(s) * 2)) : 3 + Math.floor(rand(s) * 3);
      pads[at] = String(mult); h[at + 1] = h[at];
      if (wide) { pads[at + 1] = String(mult); h[at + 2] = h[at]; }
      count--;
    }
    if (pads.join("") === "............") { pads[5] = "1"; pads[6] = "1"; h[6] = h[5]; h[7] = h[5]; }
    var gravity = easy ? Math.min(0.025, 0.012 + 0.001 * (n - 1)) : Math.min(0.04, 0.016 + 0.002 * (n - 1));
    var wind = easy || n < 4 ? 0 : r3((rand(s) - 0.5) * 2 * Math.min(0.008, 0.001 * n));
    var fuel = easy ? 900 : Math.max(250, 700 - 30 * (n - 1));
    fuel = Math.max(fuel, Math.ceil(fuelNeeded(gravity)));
    return { name: "Level " + n, heights: h, pads: pads.join(""), start: 30 + Math.floor(rand(s) * 180),
      drift: easy ? 0 : r3((rand(s) - 0.5) * 1.2), gravity: r3(gravity), wind: wind, fuel: fuel };
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, level: 1, score: 0, lives: MODES[mode].lives, over: false, won: false,
      updates: 0, phase: "fly", t: 0, land: null, x: 0, y: 0, vx: 0, vy: 0, a: 0, fuel: 0, thrust: false,
      held: { left: false, right: false, up: false }, lastPoints: 0, lastMult: 0,
      list: mode === "levels" ? usableLevels(o.levels) : null,
      stats: { landings: 0, crashes: 0, fuelUsed: 0, best: 0, levels: 0, cause: null },
    };
    if (s.list) s.level = startAt(o.startLevel, s.list.length);
    startLevel(s, true);
    return s;
  }

  function startLevel(s, fresh) {
    if (fresh || !s.land) s.land = s.list ? s.list[Math.min(s.level, s.list.length) - 1] : makeLevel(s, s.level);
    var l = s.land;
    s.x = l.start; s.y = START_Y; s.vx = l.drift; s.vy = 0; s.a = 0; s.fuel = l.fuel; s.thrust = false;
    s.phase = "fly"; s.t = 0;
  }

  // ---------- the ground ----------
  function groundAt(l, x) {
    var i = Math.max(0, Math.min(N - 2, Math.floor(x / STEP_X))), f = (x - i * STEP_X) / STEP_X;
    f = Math.max(0, Math.min(1, f));
    return FLOOR - (l.heights[i] + (l.heights[i + 1] - l.heights[i]) * f);
  }
  /** The pad under both feet, or null: { from, to, mult }. */
  function padUnder(l, x) {
    var i = Math.floor((x - HALF) / STEP_X), j = Math.floor((x + HALF) / STEP_X);
    if (i < 0 || j > 11) return null;
    for (var k = i; k <= j; k++) if (l.pads[k] === "." || l.pads[k] !== l.pads[i] || l.heights[k] !== l.heights[i]) return null;
    return { mult: Number(l.pads[i]), y: FLOOR - l.heights[i] };
  }
  /** The pads as runs: [{ from, to, mult, y }] (for drawing). */
  function pads(l) {
    var out = [], i = 0;
    while (i < 12) {
      if (l.pads[i] === ".") { i++; continue; }
      var j = i;
      while (j + 1 < 12 && l.pads[j + 1] === l.pads[i] && l.heights[j + 1] === l.heights[i]) j++;
      out.push({ from: i * STEP_X, to: (j + 1) * STEP_X, mult: Number(l.pads[i]), y: FLOOR - l.heights[i] });
      i = j + 1;
    }
    return out;
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "landed") {
      if (--s.t <= 0) {
        if (s.list && s.level >= s.list.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          evs.push({ type: "win", score: s.score });
          return evs;
        }
        s.level = Math.min(MAX_LEVEL, s.level + 1);
        startLevel(s, true);
        evs.push({ type: "level", level: s.level });
      }
      return evs;
    }
    if (s.phase === "crashed") {
      if (--s.t <= 0) {
        if (s.lives <= 0) { s.over = true; evs.push({ type: "gameover", score: s.score, cause: s.stats.cause }); return evs; }
        startLevel(s, false);
        evs.push({ type: "retry", level: s.level });
      }
      return evs;
    }
    var l = s.land;
    if (s.held.left) s.a = Math.max(-MAX_TILT, s.a - TURN);
    if (s.held.right) s.a = Math.min(MAX_TILT, s.a + TURN);
    s.thrust = s.held.up && s.fuel > 0;
    s.vy += l.gravity; s.vx += l.wind;
    if (s.thrust) {
      s.vx += SIN[s.a + MAX_TILT] * THRUST; s.vy -= COS[s.a + MAX_TILT] * THRUST;
      s.fuel--; s.stats.fuelUsed++;
      if (s.fuel === 0) evs.push({ type: "empty" });
    }
    s.x += s.vx; s.y += s.vy;
    if (s.x < 8) { s.x = 8; s.vx = 0; } else if (s.x > W - 8) { s.x = W - 8; s.vx = 0; }
    if (s.y < TOP + 8) { s.y = TOP + 8; if (s.vy < 0) s.vy = 0; }

    // touching the ground: a foot, the middle, or a side of the body
    var feet = s.y + FOOT, gl = groundAt(l, s.x - HALF), gr = groundAt(l, s.x + HALF), gm = groundAt(l, s.x);
    if (feet >= gl || feet >= gr || feet >= gm || s.y >= groundAt(l, s.x - HALF - 2) || s.y >= groundAt(l, s.x + HALF + 2)) {
      var pad = padUnder(l, s.x);
      if (pad && s.vy <= SAFE_VY && Math.abs(s.vx) <= SAFE_VX && Math.abs(s.a) <= SAFE_TILT) {
        var pts = pad.mult * PAD_POINTS + Math.floor(s.fuel / 2);
        s.y = pad.y - FOOT; s.vx = 0; s.vy = 0; s.thrust = false;
        s.score += pts; s.lastPoints = pts; s.lastMult = pad.mult;
        s.stats.landings++; s.stats.levels++; s.stats.best = Math.max(s.stats.best, pts);
        s.phase = "landed"; s.t = LAND_T;
        evs.push({ type: "land", mult: pad.mult, points: pts, score: s.score });
      } else {
        s.stats.cause = !pad ? "ground" : Math.abs(s.a) > SAFE_TILT ? "tilt" : "fast";
        s.lives--; s.stats.crashes++; s.thrust = false;
        s.y = Math.min(s.y, Math.max(gl, gr, gm) - FOOT);
        s.phase = "crashed"; s.t = CRASH_T;
        evs.push({ type: "crash", lives: s.lives, cause: s.stats.cause });
      }
    }
    return evs;
  }

  // ---------- input ----------
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "left" || action === "right") s.held[action] = !!down;
    else if (action === "up" || action === "fire") s.held.up = !!down;
    return [];
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "list") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.held = { left: false, right: false, up: false }; out.thrust = false;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && data.land && Array.isArray(data.land.heights) &&
      data.land.heights.length === N && typeof data.land.pads === "string" && typeof data.x === "number" &&
      typeof data.score === "number" && typeof data.level === "number" && typeof data.rng === "number" &&
      typeof data.lives === "number" && typeof data.fuel === "number" && data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.list = s.mode === "levels" ? usableLevels(levels) : null;
    if (s.list && s.level > s.list.length) s.level = s.list.length;
    s.held = { left: false, right: false, up: false }; s.thrust = false;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { landings: s.stats.landings, crashes: s.stats.crashes,
      fuelUsed: s.stats.fuelUsed, best: s.stats.best, levels: s.stats.levels, livesLeft: Math.max(0, s.lives), won: !!s.won,
      cause: s.stats.cause, mode: s.mode } };
  }

  var LanderLogic = {
    W: W, TOP: TOP, FLOOR: FLOOR, STEP_X: STEP_X, N: N, START_Y: START_Y, FOOT: FOOT, HALF: HALF, THRUST: THRUST,
    TURN: TURN, SAFE_VY: SAFE_VY, SAFE_VX: SAFE_VX, SAFE_TILT: SAFE_TILT, LAND_T: LAND_T, CRASH_T: CRASH_T,
    PAD_POINTS: PAD_POINTS, MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    levelProblem: levelProblem, fuelNeeded: fuelNeeded, makeLevel: makeLevel, groundAt: groundAt, padUnder: padUnder,
    pads: pads, create: create, step: step, press: press, save: save, restore: restore, result: result, rand: rand,
    SIN: SIN, COS: COS,
  };
  if (typeof module === "object" && module.exports) module.exports = LanderLogic; else self.LanderLogic = LanderLogic;
})();
