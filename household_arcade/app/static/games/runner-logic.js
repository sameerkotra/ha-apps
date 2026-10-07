/* Household Arcade — Runner rules (pure: no DOM, deterministic for a given seed).

   A runner races to the right along the ground on its own; the world scrolls past. Jump (hold for a higher
   jump) over boxes and pits and over or under fliers, duck under hanging bars. Coins lie along the way. The
   world is made of 60 px segments, each holding at most one thing ("." nothing, "b" a box, "B" a tall box,
   "p" a pit, "h" a hanging bar, "f" a flier, "c" coins on the ground, "o" an arc of coins in the air); after a
   hazard the next one is always at least gapSegs(speed) segments further on, so there is room to land and go
   again at any speed. Endless and Easy make the segments from the seed and speed up every 3,000 px;
   Courses plays the course list. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var W = 240, TOP = 38, GY = 250, PX = 48, SEG = 60, LEAD = 6, VMAX = 8, LEVEL_PX = 3000, MAX_LEVEL = 100;
  var JUMP_V = 7.4, G = 0.55, G_HOLD = 0.3, HOLD_MAX = 12, G_FAST = 1.3, FALL_OUT = 40;
  var STAND_H = 26, DUCK_H = 14, HALF_W = 6, SAFE = 90, DEAD_T = 70, CLEAR_T = 90;
  var COIN_POINTS = 10, COURSE_POINTS = 100, AHEAD = 320;
  var HAZARDS = "bBphf";
  var MODES = {
    classic: { v0: 3.6, vmax: 8, lives: 1 },
    easy: { v0: 3, vmax: 6.5, lives: 3 },
    courses: { lives: 3 },
  };

  // Courses (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A course is { name, speed, ramp, pattern } — ramp is added to the speed every 10 segments (at most 8).
  var LEVELS = [
    { name: "Park run", speed: 3.2, ramp: 0, pattern: "..c..b...o...b...c..b...." },
    { name: "Hop and skip", speed: 3.4, ramp: 0.2, pattern: "..b...b..c.B...o..b...f...c..b..." },
    { name: "Duck season", speed: 3.6, ramp: 0.2, pattern: "..f...h..c.f...b..o..h...f...c..b..." },
    { name: "Mind the gap", speed: 3.8, ramp: 0.2, pattern: "..p...b..c.p...o..B...p...f..c..h...p...." },
    { name: "Market street", speed: 4, ramp: 0.3, pattern: "..b...f...h..c.p...B...o..f....b....h..c..p....b...." },
    { name: "Rooftops", speed: 4.4, ramp: 0.3, pattern: "..p....p....c.b....h....o..f....B....p..c..h....b....p...." },
    { name: "Night train", speed: 4.8, ramp: 0.3, pattern: "..h....f....b..c..p....B....o..h....p....f..c..b.....h.....p......" },
    { name: "Express", speed: 5.2, ramp: 0.4, pattern: "..b.....p.....h..c..f.....B.....o..p.....h.....b..c..f.....p......B......" },
  ];

  function speedAtSeg(spec, i) {           // a course's speed at segment i (lead-in included)
    return Math.min(VMAX, spec.speed + spec.ramp * Math.floor(Math.max(0, i - LEAD) / 10));
  }
  /** The fewest segments from one hazard to the next at speed v: room to land and jump again. */
  function gapSegs(v) { return Math.ceil(50 * v / SEG); }
  /** Why a course can't be played, or null. */
  function courseProblem(c) {
    var last = -1e9, hz = 0;
    for (var i = 0; i < c.pattern.length; i++) {
      if (HAZARDS.indexOf(c.pattern[i]) < 0) continue;
      hz++;
      if (i - last < gapSegs(speedAtSeg(c, i + LEAD))) return "hazards too close";
      last = i;
    }
    return hz < 3 ? "too few hazards" : null;
  }
  /** The courses to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c !== "object" || typeof c.name !== "string" || !c.name || typeof c.pattern !== "string") return false;
      if (!/^[._bBphfco]{10,120}$/.test(c.pattern)) return false;
      if (typeof c.speed !== "number" || !(c.speed >= 3 && c.speed <= 7)) return false;
      if (typeof c.ramp !== "number" || !(c.ramp >= 0 && c.ramp <= 1)) return false;
      return courseProblem(c) === null;
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function course(s) { return s.courses ? s.courses[Math.min(s.level, s.courses.length) - 1] : null; }
  /** The running speed at a segment of the current stretch. */
  function speedAt(s, i) {
    var c = course(s);
    if (c) return speedAtSeg(c, i);
    var m = MODES[s.mode];
    return Math.min(m.vmax, m.v0 + 0.3 * Math.floor(i * SEG / LEVEL_PX));
  }
  function speed(s) { return speedAt(s, Math.floor((s.dist + PX) / SEG)); }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, level: 1, score: 0, lives: MODES[mode].lives, over: false, won: false,
      updates: 0, dist: 0, acc: 0, y: GY, vy: 0, ground: true, duck: false, hold: 0,
      jumpHeld: false, duckHeld: false, jumpReq: false, safe: 0, phase: "run", t: 0,
      segs: [], nextSeg: 0, nextHazard: LEAD + 1, obs: [],
      courses: mode === "courses" ? usableLevels(o.levels) : null,
      stats: { distance: 0, coins: 0, jumps: 0, hits: 0, courses: 0, cause: null },
    };
    if (s.courses) s.level = startAt(o.startLevel, s.courses.length);
    fill(s);
    return s;
  }

  // ---------- the world ----------
  function segChar(s, i) {
    if (i < LEAD) return ".";
    var c = course(s);
    if (c) return i - LEAD < c.pattern.length ? (c.pattern[i - LEAD] === "_" ? "." : c.pattern[i - LEAD]) : ".";
    if (i < s.nextHazard) {
      var r = rand(s);
      return r < 0.12 ? "c" : r < 0.2 ? "o" : ".";
    }
    var lv = 1 + Math.floor(i * SEG / LEVEL_PX);
    var pool = lv >= 4 ? "bBphfbf" : lv >= 3 ? "bphfb" : lv >= 2 ? "bfbB" : "bbB";
    var k = pool[Math.floor(rand(s) * pool.length)];
    s.nextHazard = i + gapSegs(speedAt(s, i + 8)) + Math.floor(rand(s) * 3);
    return k;
  }
  function fill(s) {
    var c = course(s), end = c ? c.pattern.length + LEAD + 4 : Infinity;
    while (s.nextSeg * SEG < s.dist + PX + AHEAD && s.nextSeg < end) {
      var i = s.nextSeg++, ch = segChar(s, i), x = i * SEG + 20;
      s.segs.push(ch);
      if (ch === "b") s.obs.push({ k: "b", x: x, w: 18, top: GY - 22, bot: GY });
      else if (ch === "B") s.obs.push({ k: "B", x: x, w: 18, top: GY - 36, bot: GY });
      else if (ch === "p") s.obs.push({ k: "p", x: x - 10, w: 40, top: GY, bot: GY + 60 });
      else if (ch === "h") s.obs.push({ k: "h", x: x, w: 22, top: TOP, bot: GY - 20 });
      else if (ch === "f") s.obs.push({ k: "f", x: x, w: 16, top: GY - 32, bot: GY - 20 });
      else if (ch === "c") for (var a = 0; a < 3; a++) s.obs.push({ k: "coin", x: x - 5 + a * 15, w: 8, top: GY - 14, bot: GY - 6 });
      else if (ch === "o") {
        var arc = [26, 40, 46, 40, 26];
        for (var b = 0; b < 5; b++) s.obs.push({ k: "coin", x: x - 14 + b * 12, w: 8, top: GY - arc[b] - 8, bot: GY - arc[b] });
      }
    }
    while (s.segs.length > 12) s.segs.shift();
  }
  function overPit(s) {
    for (var i = 0; i < s.obs.length; i++) {
      var o = s.obs[i];
      if (o.k === "p" && s.dist + PX - 3 > o.x && s.dist + PX + 3 < o.x + o.w) return true;
    }
    return false;
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [], i;
    if (s.over) return evs;
    s.updates++;
    if (s.phase === "dead") {
      if (--s.t <= 0) {
        if (s.lives <= 0) { s.over = true; evs.push({ type: "gameover", score: s.score, cause: s.stats.cause }); return evs; }
        s.phase = "run"; s.safe = SAFE; s.y = GY; s.vy = 0; s.ground = true;
      }
      return evs;
    }
    if (s.phase === "clear") {
      if (--s.t <= 0) {
        s.level++; s.dist = 0; s.nextSeg = 0; s.segs = []; s.obs = []; s.acc = 0; s.phase = "run";
        fill(s);
        evs.push({ type: "level", level: s.level });
      }
      return evs;
    }
    if (s.safe > 0) s.safe--;
    var v = speed(s);
    s.dist += v; s.acc += v; s.stats.distance += v;
    while (s.acc >= 10) { s.acc -= 10; s.score++; }
    if (!s.courses) {
      var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.dist / LEVEL_PX));
      if (lv !== s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
    }
    fill(s);
    for (i = s.obs.length - 1; i >= 0; i--) if (s.obs[i].x + s.obs[i].w < s.dist - 20) s.obs.splice(i, 1);

    // jumping, ducking, falling (a pit under a runner on the ground: down it goes)
    var solid = !overPit(s) || s.safe > 0;
    if (s.jumpReq && s.ground) {
      s.vy = -JUMP_V; s.ground = false; s.hold = 0; s.stats.jumps++;
      evs.push({ type: "jump" });
    }
    s.jumpReq = false;
    if (s.ground && !solid) s.ground = false;
    s.duck = s.duckHeld;
    if (!s.ground) {
      var g = G, was = s.y;
      if (s.duckHeld) g = G_FAST;
      else if (s.jumpHeld && s.vy < 0 && s.hold < HOLD_MAX) { g = G_HOLD; s.hold++; }
      s.vy = Math.min(12, s.vy + g);
      s.y += s.vy;
      if (s.y >= GY && s.vy > 0 && was <= GY && solid) {
        s.y = GY; s.vy = 0; s.ground = true;
        evs.push({ type: "land" });
      }
      if (s.y > GY + FALL_OUT) { hit(s, evs, "pit"); return evs; }
    }

    // what the runner touches
    var h = s.duck ? DUCK_H : STAND_H, hw = s.duck ? HALF_W + 2 : HALF_W;
    var l = s.dist + PX - hw, r = s.dist + PX + hw, top = s.y - h, bot = s.y;
    for (i = s.obs.length - 1; i >= 0; i--) {
      var o = s.obs[i];
      if (o.x >= r || o.x + o.w <= l) continue;
      if (o.k === "coin") {
        if (o.top < bot && o.bot > top) {
          s.obs.splice(i, 1); s.score += COIN_POINTS; s.stats.coins++;
          evs.push({ type: "coin", score: s.score });
        }
        continue;
      }
      if (o.k === "p" || s.safe > 0) continue;
      // a little forgiveness at the edges
      if (o.top + 2 < bot && o.bot - 2 > top && o.x + 2 < r && o.x + o.w - 2 > l) { hit(s, evs, o.k); return evs; }
    }

    // the end of a course
    var c = course(s);
    if (c && s.dist + PX > (c.pattern.length + LEAD + 1) * SEG) {
      s.stats.courses++; s.score += COURSE_POINTS;
      evs.push({ type: "course", level: s.level, score: s.score });
      if (s.level >= s.courses.length) {
        s.won = true; s.over = true; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
        return evs;
      }
      s.phase = "clear"; s.t = CLEAR_T;
    }
    return evs;
  }

  function hit(s, evs, what) {
    s.lives--; s.stats.hits++;
    s.stats.cause = what === "pit" ? "pit" : what === "h" ? "bar" : what === "f" ? "flier" : "box";
    s.phase = "dead"; s.t = DEAD_T;
    evs.push({ type: "hit", lives: s.lives, what: s.stats.cause });
    // the hazard that was hit is out of the way when the runner comes back
    if (s.lives > 0 && s.y > GY) { s.y = GY; s.vy = 0; s.ground = true; }
  }

  // ---------- input ----------
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "up" || action === "fire") {
      s.jumpHeld = !!down;
      if (down) s.jumpReq = true;
    } else if (action === "down") s.duckHeld = !!down;
    return [];
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "courses") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.jumpHeld = false; out.duckHeld = false; out.jumpReq = false;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && typeof data.dist === "number" && Array.isArray(data.obs) &&
      typeof data.score === "number" && typeof data.level === "number" && typeof data.rng === "number" &&
      typeof data.lives === "number" && typeof data.nextSeg === "number" && data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.courses = s.mode === "courses" ? usableLevels(levels) : null;
    if (s.courses && s.level > s.courses.length) s.level = s.courses.length;
    if (!Array.isArray(s.segs)) s.segs = [];
    s.jumpHeld = false; s.duckHeld = false; s.jumpReq = false;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { distance: Math.round(s.stats.distance), coins: s.stats.coins,
      jumps: s.stats.jumps, hits: s.stats.hits, courses: s.stats.courses, livesLeft: Math.max(0, s.lives), won: !!s.won,
      cause: s.stats.cause, mode: s.mode } };
  }

  var RunnerLogic = {
    W: W, TOP: TOP, GY: GY, PX: PX, SEG: SEG, LEAD: LEAD, VMAX: VMAX, LEVEL_PX: LEVEL_PX, JUMP_V: JUMP_V, G: G,
    STAND_H: STAND_H, DUCK_H: DUCK_H, SAFE: SAFE, DEAD_T: DEAD_T, CLEAR_T: CLEAR_T, COIN_POINTS: COIN_POINTS,
    COURSE_POINTS: COURSE_POINTS, HAZARDS: HAZARDS, MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS,
    usableLevels: usableLevels, courseProblem: courseProblem, gapSegs: gapSegs, speedAtSeg: speedAtSeg, speed: speed,
    course: course, create: create, step: step, press: press, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = RunnerLogic; else self.RunnerLogic = RunnerLogic;
})();
