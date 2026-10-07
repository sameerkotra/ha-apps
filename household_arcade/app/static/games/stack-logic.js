/* Household Arcade — Tower Stack rules (pure: no DOM, deterministic for a given seed).

   A block slides from side to side above the tower; a press drops it onto the floor below. The part that
   hangs over the edge is cut off and falls away, so the next block is only as wide as the overlap. A drop
   within PERFECT px of the floor below is a perfect drop: it lines up exactly, and a run of perfect drops
   (in modes that allow it) grows the block back a little. A block that misses the tower completely ends the
   game (in Easy and Towers, it costs one of 3 lives and that floor is tried again). Each floor is a little faster.
   Towers plays the tower list: build each tower's floors to move on; the last one wins.
   One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var W = 240, LEFT = 8, RIGHT = 232, BH = 14;
  var PERFECT = 3, SPAWN = 10, GROW = 4, GROW_AFTER = 3, MAX_V = 5, CLEAR_T = 90, MISS_T = 60, MAX_LEVEL = 100;
  var FLOOR_POINTS = 10, PERFECT_POINTS = 10, PERFECT_CAP = 5, TOWER_POINTS = 200;
  var MODES = {
    classic: { width: 120, speed: 1.3, ramp: 0.03, grow: true, lives: 1 },
    fast: { width: 100, speed: 2.2, ramp: 0.04, grow: true, lives: 1 },
    easy: { width: 150, speed: 1, ramp: 0.015, grow: true, lives: 3 },     // gentle: wide, slow, three misses
    towers: { lives: 3 },
  };

  // Towers (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A tower is { name, floors, width, speed, ramp, grow }.
  var LEVELS = [
    { name: "Garden shed", floors: 10, width: 140, speed: 1.2, ramp: 0.02, grow: true },
    { name: "Corner shop", floors: 12, width: 130, speed: 1.5, ramp: 0.02, grow: true },
    { name: "Town hall", floors: 15, width: 120, speed: 1.6, ramp: 0.03, grow: true },
    { name: "Clock tower", floors: 18, width: 110, speed: 1.8, ramp: 0.03, grow: true },
    { name: "Lighthouse", floors: 20, width: 90, speed: 1.8, ramp: 0.04, grow: true },
    { name: "Office block", floors: 24, width: 120, speed: 2.2, ramp: 0.03, grow: false },
    { name: "Needle", floors: 20, width: 70, speed: 2, ramp: 0.04, grow: true },
    { name: "Sky hotel", floors: 30, width: 130, speed: 2.4, ramp: 0.04, grow: false },
    { name: "Cloud piercer", floors: 36, width: 110, speed: 2.6, ramp: 0.06, grow: true },
  ];
  var FIELDS = { floors: [8, 60], width: [40, 160], speed: [1, 3.5], ramp: [0, 0.06] };
  function topSpeed(t) { return t.speed + t.ramp * t.floors; }
  /** The towers to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (t) {
      if (!t || typeof t !== "object" || typeof t.name !== "string" || !t.name || typeof t.grow !== "boolean") return false;
      for (var k in FIELDS) if (typeof t[k] !== "number" || !(t[k] >= FIELDS[k][0] && t[k] <= FIELDS[k][1])) return false;
      if (t.floors !== Math.floor(t.floors) || t.width !== Math.floor(t.width)) return false;
      return topSpeed(t) <= MAX_V;
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function r2(v) { return Math.round(v * 100) / 100; }

  /** The tower being built: the mode's settings, or the list's tower. */
  function tower(s) {
    if (s.towers) return s.towers[Math.min(s.level, s.towers.length) - 1];
    var m = MODES[s.mode];
    return { name: "", floors: 0, width: m.width, speed: m.speed, ramp: m.ramp, grow: m.grow };
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, level: 1, score: 0, lives: MODES[mode].lives, over: false, won: false,
      updates: 0, floors: [], block: null, chunks: [], wait: 0, streak: 0, phase: "play", t: 0, perfectT: 0,
      height: 0, dropReq: false,
      towers: mode === "towers" ? usableLevels(o.levels) : null,
      stats: { floors: 0, perfects: 0, bestStreak: 0, towers: 0, misses: 0, cause: null },
    };
    startTower(s);
    return s;
  }

  function startTower(s) {
    var t = tower(s);
    s.floors = [{ x: r2((W - t.width) / 2), w: t.width, c: 0 }];
    s.height = 0; s.streak = 0; s.chunks = []; s.phase = "play"; s.t = 0;
    newBlock(s);
  }

  function speedAt(s, floor) { var t = tower(s); return Math.min(MAX_V, t.speed + t.ramp * floor); }
  function newBlock(s) {
    var top = s.floors[s.floors.length - 1], fromLeft = rand(s) < 0.5;
    var v = speedAt(s, s.height);
    s.block = { x: fromLeft ? LEFT : r2(RIGHT - top.w), w: top.w, v: fromLeft ? v : -v, c: (s.height + 1) % 7 };
    s.wait = SPAWN; s.dropReq = false;
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.perfectT > 0) s.perfectT--;
    for (var i = s.chunks.length - 1; i >= 0; i--) {
      var ch = s.chunks[i];
      ch.vy = Math.min(6, ch.vy + 0.25); ch.y += ch.vy; ch.x += ch.vx;
      if (ch.y > 320) s.chunks.splice(i, 1);
    }
    if (s.phase === "clear") {
      if (--s.t <= 0) {
        s.level = Math.min(MAX_LEVEL, s.level + 1);
        startTower(s);
        evs.push({ type: "level", level: s.level });
      }
      return evs;
    }
    if (s.phase === "miss") {
      if (--s.t <= 0) {
        if (s.lives <= 0) { s.over = true; s.stats.cause = "miss"; evs.push({ type: "gameover", score: s.score }); }
        else { s.phase = "play"; newBlock(s); }
      }
      return evs;
    }
    var b = s.block;
    b.x = r2(b.x + b.v);
    if (b.x <= LEFT) { b.x = LEFT; b.v = Math.abs(b.v); }
    else if (b.x + b.w >= RIGHT) { b.x = r2(RIGHT - b.w); b.v = -Math.abs(b.v); }
    if (s.wait > 0) s.wait--;
    if (s.dropReq && s.wait === 0) { s.dropReq = false; drop(s, evs); }
    return evs;
  }

  function drop(s, evs) {
    var b = s.block, top = s.floors[s.floors.length - 1], t = tower(s);
    var l = Math.max(b.x, top.x), r = Math.min(b.x + b.w, top.x + top.w);
    if (r - l <= 0.5) {                          // missed the tower
      s.chunks.push({ x: b.x, y: 0, h: s.height + 1, w: b.w, c: b.c, vy: 0, vx: b.v > 0 ? 0.6 : -0.6 });
      s.streak = 0; s.lives--; s.stats.misses++;
      s.phase = "miss"; s.t = MISS_T; s.block = null;
      evs.push({ type: "miss", lives: s.lives });
      return;
    }
    var nf;
    if (Math.abs(b.x - top.x) <= PERFECT) {      // a perfect drop: lines up exactly
      s.streak++; s.stats.perfects++;
      s.stats.bestStreak = Math.max(s.stats.bestStreak, s.streak);
      nf = { x: top.x, w: top.w, c: b.c };
      if (t.grow && s.streak >= GROW_AFTER && nf.w < t.width) {
        var grow = Math.min(GROW, t.width - nf.w);
        nf.x = r2(Math.max(LEFT, Math.min(RIGHT - nf.w - grow, nf.x - grow / 2))); nf.w = r2(nf.w + grow);
      }
      s.score += FLOOR_POINTS + PERFECT_POINTS * Math.min(s.streak, PERFECT_CAP);
      s.perfectT = 40;
      evs.push({ type: "perfect", streak: s.streak });
    } else {
      s.streak = 0;
      nf = { x: r2(l), w: r2(r - l), c: b.c };
      // the overhang falls away
      if (b.x < l) s.chunks.push({ x: b.x, y: 0, h: s.height + 1, w: r2(l - b.x), c: b.c, vy: 0, vx: -0.5 });
      if (b.x + b.w > r) s.chunks.push({ x: r2(r), y: 0, h: s.height + 1, w: r2(b.x + b.w - r), c: b.c, vy: 0, vx: 0.5 });
      s.score += FLOOR_POINTS;
      evs.push({ type: "cut", width: nf.w });
    }
    s.floors.push(nf);
    if (s.floors.length > 40) s.floors.shift();
    s.height++; s.stats.floors++;
    evs.push({ type: "place", height: s.height, score: s.score });
    if (s.towers) {
      if (s.height >= t.floors) {
        s.stats.towers++; s.score += TOWER_POINTS; s.block = null;
        evs.push({ type: "tower", level: s.level, score: s.score });
        if (s.level >= s.towers.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          evs.push({ type: "win", score: s.score });
          return;
        }
        s.phase = "clear"; s.t = CLEAR_T;
        return;
      }
    } else {
      var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.height / 10));
      if (lv !== s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
    }
    newBlock(s);
  }

  // ---------- input ----------
  function press(s, action, down) {
    if (s.over || !down) return [];
    if (action === "fire" || action === "up" || action === "down") {
      if (s.phase === "play" && s.block) s.dropReq = true;
    }
    return [];
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "towers") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.dropReq = false;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.floors) && data.floors.length > 0 &&
      typeof data.score === "number" && typeof data.level === "number" && typeof data.height === "number" &&
      typeof data.rng === "number" && typeof data.lives === "number" && data.stats && typeof data.stats === "object" &&
      (data.block === null || (data.block && typeof data.block.x === "number"));
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.towers = s.mode === "towers" ? usableLevels(levels) : null;
    if (s.towers && s.level > s.towers.length) s.level = s.towers.length;
    if (!Array.isArray(s.chunks)) s.chunks = [];
    s.dropReq = false;
    if (s.phase === "play" && !s.block) newBlock(s);
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { floors: s.stats.floors, perfects: s.stats.perfects,
      bestStreak: s.stats.bestStreak, towers: s.stats.towers, misses: s.stats.misses, livesLeft: Math.max(0, s.lives),
      won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var StackLogic = {
    W: W, LEFT: LEFT, RIGHT: RIGHT, BH: BH, PERFECT: PERFECT, SPAWN: SPAWN, GROW: GROW, GROW_AFTER: GROW_AFTER, MAX_V: MAX_V,
    CLEAR_T: CLEAR_T, MISS_T: MISS_T, FLOOR_POINTS: FLOOR_POINTS, PERFECT_POINTS: PERFECT_POINTS, PERFECT_CAP: PERFECT_CAP,
    TOWER_POINTS: TOWER_POINTS, MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    tower: tower, speedAt: speedAt, create: create, step: step, press: press, save: save, restore: restore, result: result,
    rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = StackLogic; else self.StackLogic = StackLogic;
})();
