/* Household Arcade — City Defense rules (pure: no DOM, deterministic for a given seed).

   Missiles fall from the sky toward six cities and three launch bases. Tap (or aim the crosshair and fire)
   where an interceptor should go: the nearest base with ammo launches one, it flies to that point and
   bursts into a growing cloud that stops every missile (and flier) inside it. Some missiles split into three
   on the way down; fliers cross the sky and drop missiles of their own. A wave ends when everything it sent
   is gone: each base's ammo left scores 5, each city still standing 100; then the bases are rebuilt and
   refilled, and every third wave a lost city comes back. The game ends when no city is left.
   Classic and Easy make their waves endlessly; Waves plays the wave list. One call to step() is one update. */
(function () {
  "use strict";

  var W = 240, TOP = 38, GROUND = 274, LAUNCH_Y = 262;
  var CITIES = [44, 68, 92, 148, 172, 196], BASES = [14, 120, 226], BASE_V = [5, 7, 5];
  var R_MAX = 20, GROW = 18, HOLD = 10, SHRINK = 16, SMALL_R = 10, MAX_SHOTS = 8;
  var MISSILE_POINTS = 25, FLIER_POINTS = 100, AMMO_POINTS = 5, CITY_POINTS = 100;
  var BONUS_T = 150, END_T = 90, START_T = 60, REBUILD_EVERY = 3, MAX_LEVEL = 100, CROSS_V = 3;
  var MODES = { classic: {}, easy: {}, waves: {} };

  // Waves (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A wave is { name, missiles, speed, splits, fliers, ammo }.
  var LEVELS = [
    { name: "First light", missiles: 6, speed: 0.4, splits: 0, fliers: 0, ammo: 10 },
    { name: "Evening rain", missiles: 8, speed: 0.45, splits: 0, fliers: 1, ammo: 10 },
    { name: "Split sky", missiles: 10, speed: 0.5, splits: 2, fliers: 0, ammo: 10 },
    { name: "Night watch", missiles: 12, speed: 0.55, splits: 2, fliers: 1, ammo: 10 },
    { name: "Shower", missiles: 16, speed: 0.6, splits: 3, fliers: 1, ammo: 11 },
    { name: "Fast hail", missiles: 12, speed: 0.85, splits: 2, fliers: 2, ammo: 10 },
    { name: "Starfall", missiles: 18, speed: 0.75, splits: 4, fliers: 2, ammo: 12 },
    { name: "Long night", missiles: 22, speed: 0.8, splits: 5, fliers: 3, ammo: 13 },
    { name: "Last stand", missiles: 26, speed: 0.9, splits: 6, fliers: 3, ammo: 14 },
  ];
  /** Why a wave can't be played, or null: three bases must have enough ammo for what comes. */
  function waveProblem(w) {
    var threats = w.missiles + 2 * w.splits + 2 * w.fliers;
    if (threats > 3 * w.ammo * 1.5) return "too little ammo for the missiles";
    if (w.splits > w.missiles) return "more splitting missiles than missiles";
    if (w.speed > 1 && w.missiles > 20) return "fast waves (speed over 1) may have at most 20 missiles";
    return null;
  }
  function isInt(v, lo, hi) { return typeof v === "number" && v === Math.floor(v) && v >= lo && v <= hi; }
  /** The waves to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (w) {
      if (!w || typeof w !== "object" || typeof w.name !== "string" || !w.name) return false;
      if (!isInt(w.missiles, 4, 30) || !isInt(w.splits, 0, 8) || !isInt(w.fliers, 0, 4) || !isInt(w.ammo, 6, 15)) return false;
      if (typeof w.speed !== "number" || !(w.speed >= 0.3 && w.speed <= 1.4)) return false;
      return waveProblem(w) === null;
    });
    return out.length ? out : LEVELS;
  }

  /** Wave n of a mode: from the list (Waves) or made up (Classic, Easy). */
  function waveSpec(s, n) {
    if (s.waves) return s.waves[Math.min(n, s.waves.length) - 1];
    if (s.mode === "easy") {
      return { name: "Wave " + n, missiles: Math.min(24, 5 + 2 * (n - 1)), speed: Math.min(1, Math.round((0.3 + 0.05 * (n - 1)) * 100) / 100),
        splits: Math.min(4, Math.floor((n - 1) / 2)), fliers: Math.min(2, Math.floor((n - 1) / 3)), ammo: 12 };
    }
    return { name: "Wave " + n, missiles: Math.min(30, 6 + 2 * (n - 1)), speed: Math.min(1.4, Math.round((0.4 + 0.07 * (n - 1)) * 100) / 100),
      splits: Math.min(8, Math.max(0, n - 2)), fliers: Math.min(4, Math.floor((n - 1) / 2)), ammo: n > 8 ? 15 : 10 + Math.floor(n / 2) };
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, wave: 1, level: 1, score: 0, over: false, won: false, updates: 0,
      cities: CITIES.map(function () { return true; }), bases: [], phase: "wave", t: 0, clock: 0,
      schedule: [], enemies: [], fliers: [], shots: [], booms: [], cross: { x: W / 2, y: 150 },
      held: { left: false, right: false, up: false, down: false }, fireReq: false, name: "", speed: 0, bonus: null,
      waves: mode === "waves" ? usableLevels(o.levels) : null,
      stats: { missiles: 0, fliers: 0, shots: 0, citiesLost: 0, waves: 0, cause: null },
    };
    if (s.waves) { s.wave = startAt(o.startLevel, s.waves.length); s.level = Math.min(MAX_LEVEL, s.wave); }
    startWave(s);
    return s;
  }

  function startWave(s) {
    var w = waveSpec(s, s.wave), i;
    s.name = w.name; s.speed = w.speed;
    s.bases = BASES.map(function () { return { alive: true, ammo: w.ammo }; });
    s.phase = "wave"; s.clock = 0; s.t = START_T; s.enemies = []; s.fliers = []; s.shots = []; s.bonus = null;
    var spread = Math.max(240, w.missiles * 24), sched = [];
    for (i = 0; i < w.missiles; i++) sched.push({ at: START_T + Math.floor(rand(s) * spread), k: i < w.splits ? "split" : "m" });
    for (i = 0; i < w.fliers; i++) sched.push({ at: START_T + 60 + Math.floor(rand(s) * spread), k: "flier" });
    sched.sort(function (a, b) { return a.at - b.at || (a.k < b.k ? -1 : a.k > b.k ? 1 : 0); });
    sched[sched.length - 1].at = START_T + spread + 60;      // a wave always lasts its whole spread
    s.schedule = sched;
  }

  /** The places that can be aimed at: [x, y, kind, index]. */
  function targetPick(s) {
    var list = [], i;
    for (i = 0; i < CITIES.length; i++) if (s.cities[i]) list.push([CITIES[i], GROUND - 4, "city", i]);
    for (i = 0; i < BASES.length; i++) if (s.bases[i].alive) list.push([BASES[i], LAUNCH_Y, "base", i]);
    if (!list.length) return [W / 2, GROUND - 4, "city", -1];
    return list[Math.floor(rand(s) * list.length)];
  }
  function addMissile(s, x, y, split) {
    var tg = targetPick(s), dx = tg[0] - x, dy = tg[1] - y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    var v = s.speed * (0.85 + rand(s) * 0.3);
    s.enemies.push({ x0: x, y0: y, x: x, y: y, vx: dx / d * v, vy: dy / d * v, tx: tg[0], ty: tg[1], tk: tg[2], ti: tg[3],
      split: split ? 110 + Math.floor(rand(s) * 60) : 0 });
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [], i, j;
    if (s.over) return evs;
    s.updates++;
    moveCross(s);
    for (i = s.booms.length - 1; i >= 0; i--) {
      var b = s.booms[i];
      b.t++;
      var full = b.small ? SMALL_R : R_MAX, gr = b.small ? 8 : GROW, sh = b.small ? 12 : SHRINK, ho = b.small ? 4 : HOLD;
      b.r = b.t <= gr ? full * b.t / gr : b.t <= gr + ho ? full : full * Math.max(0, 1 - (b.t - gr - ho) / sh);
      if (b.t > gr + ho + sh) s.booms.splice(i, 1);
    }
    if (s.phase === "bonus") {
      if (--s.t <= 0) {
        if (s.waves && s.wave >= s.waves.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          evs.push({ type: "win", score: s.score });
          return evs;
        }
        s.wave++; s.level = Math.min(MAX_LEVEL, s.wave);
        if ((s.wave - 1) % REBUILD_EVERY === 0) {
          var lost = [];
          for (i = 0; i < CITIES.length; i++) if (!s.cities[i]) lost.push(i);
          if (lost.length) { s.cities[lost[Math.floor(rand(s) * lost.length)]] = true; evs.push({ type: "rebuild" }); }
        }
        startWave(s);
        evs.push({ type: "level", level: s.level, wave: s.wave });
      }
      return evs;
    }
    if (s.phase === "end") {
      moveAll(s, evs);
      if (--s.t <= 0) { s.over = true; s.stats.cause = "cities"; evs.push({ type: "gameover", score: s.score }); }
      return evs;
    }
    s.clock++;
    if (s.t > 0) s.t--;
    // what the wave sends, when it is due
    while (s.schedule.length && s.schedule[0].at <= s.clock) {
      var due = s.schedule.shift();
      if (due.k === "flier") {
        var left = rand(s) < 0.5;
        s.fliers.push({ x: left ? -10 : W + 10, y: 70 + Math.floor(rand(s) * 40), vx: left ? 0.8 : -0.8,
          drop: 50 + Math.floor(rand(s) * 140), dropped: false });
        evs.push({ type: "flier" });
      } else addMissile(s, 10 + rand(s) * (W - 20), TOP + 2, due.k === "split");
    }
    if (s.fireReq) { s.fireReq = false; launch(s, s.cross.x, s.cross.y, evs); }
    moveAll(s, evs);
    if (s.over || s.phase !== "wave") return evs;
    // the wave is over when everything it sent is gone
    if (!s.schedule.length && !s.enemies.length && !s.fliers.length && !s.shots.length) {
      var ammo = 0, cities = 0;
      for (i = 0; i < s.bases.length; i++) if (s.bases[i].alive) ammo += s.bases[i].ammo;
      for (j = 0; j < s.cities.length; j++) if (s.cities[j]) cities++;
      s.bonus = { ammo: ammo, cities: cities, points: ammo * AMMO_POINTS + cities * CITY_POINTS };
      s.score += s.bonus.points; s.stats.waves++;
      s.phase = "bonus"; s.t = BONUS_T;
      evs.push({ type: "clear", wave: s.wave, bonus: s.bonus.points, score: s.score });
    }
    return evs;
  }

  function moveCross(s) {
    var c = s.cross;
    if (s.held.left) c.x -= CROSS_V;
    if (s.held.right) c.x += CROSS_V;
    if (s.held.up) c.y -= CROSS_V;
    if (s.held.down) c.y += CROSS_V;
    c.x = Math.max(6, Math.min(W - 6, c.x)); c.y = Math.max(TOP + 6, Math.min(GROUND - 24, c.y));
  }

  function launch(s, x, y, evs) {
    y = Math.max(TOP + 6, Math.min(GROUND - 24, y)); x = Math.max(4, Math.min(W - 4, x));
    if (s.shots.length >= MAX_SHOTS) return;
    var best = -1, bd = 1e9;
    for (var i = 0; i < BASES.length; i++) {
      var b = s.bases[i];
      if (!b.alive || b.ammo <= 0) continue;
      var d = Math.abs(BASES[i] - x) - (i === 1 ? 20 : 0);
      if (d < bd) { bd = d; best = i; }
    }
    if (best < 0) { evs.push({ type: "empty" }); return; }
    s.bases[best].ammo--; s.stats.shots++;
    var dx = x - BASES[best], dy = y - LAUNCH_Y, dd = Math.sqrt(dx * dx + dy * dy) || 1, v = BASE_V[best];
    s.shots.push({ x0: BASES[best], y0: LAUNCH_Y, x: BASES[best], y: LAUNCH_Y, vx: dx / dd * v, vy: dy / dd * v, tx: x, ty: y, left: dd / v });
    evs.push({ type: "fire", base: best });
  }

  function moveAll(s, evs) {
    var i, j;
    // interceptors: burst where they were sent
    for (i = s.shots.length - 1; i >= 0; i--) {
      var sh = s.shots[i];
      if (--sh.left <= 0) { s.booms.push({ x: sh.tx, y: sh.ty, t: 0, r: 0, small: false }); s.shots.splice(i, 1); evs.push({ type: "burst" }); }
      else { sh.x += sh.vx; sh.y += sh.vy; }
    }
    // fliers cross and drop one missile
    for (i = s.fliers.length - 1; i >= 0; i--) {
      var f = s.fliers[i];
      f.x += f.vx;
      if (!f.dropped && ((f.vx > 0 && f.x >= f.drop) || (f.vx < 0 && f.x <= W - f.drop))) {
        f.dropped = true; addMissile(s, f.x, f.y + 4, false);
      }
      if (f.x < -14 || f.x > W + 14) s.fliers.splice(i, 1);
    }
    // missiles fall, split, and hit what they were aimed at
    for (i = s.enemies.length - 1; i >= 0; i--) {
      var m = s.enemies[i];
      m.x += m.vx; m.y += m.vy;
      if (m.split && m.y >= m.split) {
        m.split = 0;
        for (j = 0; j < 2; j++) addMissile(s, m.x, m.y, false);
        evs.push({ type: "split" });
      }
      if (m.y >= m.ty) {
        s.enemies.splice(i, 1);
        s.booms.push({ x: m.tx, y: m.ty, t: 0, r: 0, small: true, hurt: true });
        if (m.tk === "city" && m.ti >= 0 && s.cities[m.ti]) { s.cities[m.ti] = false; s.stats.citiesLost++; evs.push({ type: "cityLost", city: m.ti }); }
        else if (m.tk === "base" && s.bases[m.ti].alive) { s.bases[m.ti].alive = false; s.bases[m.ti].ammo = 0; evs.push({ type: "baseLost", base: m.ti }); }
        else evs.push({ type: "thud" });
      }
    }
    // clouds stop whatever is inside them (a stopped missile makes a small cloud of its own)
    for (var k = 0; k < s.booms.length; k++) {
      var b = s.booms[k];
      if (b.hurt || b.r <= 0) continue;
      for (i = s.enemies.length - 1; i >= 0; i--) {
        var e = s.enemies[i], dx = e.x - b.x, dy = e.y - b.y;
        if (dx * dx + dy * dy <= b.r * b.r) {
          s.enemies.splice(i, 1); s.score += MISSILE_POINTS; s.stats.missiles++;
          s.booms.push({ x: e.x, y: e.y, t: 0, r: 0, small: true });
          evs.push({ type: "hit", score: s.score });
        }
      }
      for (i = s.fliers.length - 1; i >= 0; i--) {
        var fl = s.fliers[i], fx = fl.x - b.x, fy = fl.y - b.y;
        if (fx * fx + fy * fy <= (b.r + 5) * (b.r + 5)) {
          s.fliers.splice(i, 1); s.score += FLIER_POINTS; s.stats.fliers++;
          s.booms.push({ x: fl.x, y: fl.y, t: 0, r: 0, small: true });
          evs.push({ type: "flierHit", score: s.score });
        }
      }
    }
    if (s.phase === "wave") {
      var left = 0;
      for (i = 0; i < s.cities.length; i++) if (s.cities[i]) left++;
      if (!left) { s.phase = "end"; s.t = END_T; evs.push({ type: "lastCity" }); }
    }
  }

  // ---------- input ----------
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "left" || action === "right" || action === "up" || action === "down") s.held[action] = !!down;
    else if (action === "fire" && down) s.fireReq = true;
    return [];
  }
  /** A tap on the game: send an interceptor there (and move the crosshair there). */
  function aim(s, x, y) {
    if (s.over || s.phase !== "wave") return [];
    s.cross.x = Math.max(6, Math.min(W - 6, x)); s.cross.y = Math.max(TOP + 6, Math.min(GROUND - 24, y));
    var evs = [];
    launch(s, s.cross.x, s.cross.y, evs);
    return evs;
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "waves") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.held = { left: false, right: false, up: false, down: false }; out.fireReq = false;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.cities) && data.cities.length === 6 &&
      Array.isArray(data.bases) && data.bases.length === 3 && Array.isArray(data.enemies) && Array.isArray(data.schedule) &&
      typeof data.score === "number" && data.wave >= 1 && typeof data.rng === "number" && data.cross &&
      data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.waves = s.mode === "waves" ? usableLevels(levels) : null;
    if (s.waves && s.wave > s.waves.length) { s.wave = s.waves.length; s.level = Math.min(MAX_LEVEL, s.wave); }
    ["fliers", "shots", "booms"].forEach(function (k) { if (!Array.isArray(s[k])) s[k] = []; });
    s.held = { left: false, right: false, up: false, down: false }; s.fireReq = false;
    return s;
  }

  function result(s) {
    var cities = 0;
    for (var i = 0; i < s.cities.length; i++) if (s.cities[i]) cities++;
    return { score: s.score, level: s.level, stats: { missiles: s.stats.missiles, fliers: s.stats.fliers, shots: s.stats.shots,
      waves: s.stats.waves, citiesLeft: cities, citiesLost: s.stats.citiesLost, won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var DefenseLogic = {
    W: W, TOP: TOP, GROUND: GROUND, LAUNCH_Y: LAUNCH_Y, CITIES: CITIES, BASES: BASES, BASE_V: BASE_V, R_MAX: R_MAX,
    GROW: GROW, HOLD: HOLD, SHRINK: SHRINK, MAX_SHOTS: MAX_SHOTS, MISSILE_POINTS: MISSILE_POINTS, FLIER_POINTS: FLIER_POINTS,
    AMMO_POINTS: AMMO_POINTS, CITY_POINTS: CITY_POINTS, BONUS_T: BONUS_T, START_T: START_T, REBUILD_EVERY: REBUILD_EVERY,
    MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels, waveProblem: waveProblem,
    waveSpec: waveSpec, create: create, step: step, press: press, aim: aim, save: save, restore: restore, result: result,
    rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = DefenseLogic; else self.DefenseLogic = DefenseLogic;
})();
