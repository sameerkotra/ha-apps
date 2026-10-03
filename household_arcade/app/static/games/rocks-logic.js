/* Household Arcade — Rocks rules (pure: no DOM, deterministic for a given seed).

   A small ship in the middle of a space whose edges wrap around: turn left and right, thrust (it drifts on
   and slows gently), fire short-lived shots (four in the air at most, one every 10 updates at most). A big rock
   that is shot breaks into two medium rocks, a medium rock into two small ones. Clearing every rock ends the
   wave; the next one starts. Bumping into a rock (or a saucer or its shot) costs a life; after a new ship
   comes there's a short safe time. From wave 3 a saucer sometimes crosses and shoots at the ship.
   Classic and Calm make their waves endlessly; Waves plays the wave list. One call to step() is one update. */
(function () {
  "use strict";

  var W = 240, TOP = 38, BOTTOM = 284, SH = BOTTOM - TOP;
  var CX = W / 2, CY = TOP + SH / 2;
  var SHIP_R = 6, ROT = 0.075, THRUST = 0.075, MAX_V = 3.2, DRAG = 0.991;
  var SHOT_V = 4.2, SHOT_LIFE = 45, MAX_SHOTS = 4, FIRE_GAP = 10, FIRE_REPEAT = 15;
  var RADIUS = [0, 7, 13, 22];                  // by size: 1 small, 2 medium, 3 big
  var POINTS = [0, 100, 50, 20];
  var SAUCER_R = 8, SAUCER_V = 1.1, SAUCER_POINTS = 300, SAUCER_CHECK = 600, SAUCER_FIRE = 80;
  var SAUCER_SHOT_V = 2.4, SAUCER_SHOT_LIFE = 90;
  var RESPAWN = 90, SAFE = 150, WAVE_PAUSE = 90, BANNER = 120, MAX_LEVEL = 100;
  var MODES = {
    classic: { lives: 3 },
    calm: { lives: 5 },
    waves: { lives: 3 },
  };

  // Waves (SPEC §11.9): the built-in list for the Waves mode; the session's list (opts.levels) replaces it.
  // A wave is { name, big, medium, speed, saucer }.
  var LEVELS = [
    { name: "First drift", big: 2, medium: 0, speed: 0.5, saucer: 0 },
    { name: "Quiet belt", big: 3, medium: 1, speed: 0.6, saucer: 0 },
    { name: "Stone circle", big: 4, medium: 2, speed: 0.7, saucer: 10 },
    { name: "Busy sky", big: 5, medium: 2, speed: 0.8, saucer: 20 },
    { name: "Moon crumbs", big: 6, medium: 3, speed: 0.9, saucer: 25 },
    { name: "Comet trail", big: 6, medium: 4, speed: 1.05, saucer: 30 },
    { name: "Boulder field", big: 8, medium: 3, speed: 1.1, saucer: 35 },
    { name: "Star dust", big: 7, medium: 4, speed: 1.3, saucer: 40 },
    { name: "Big crunch", big: 9, medium: 5, speed: 1.2, saucer: 50 },
  ];
  function isInt(v, lo, hi) { return typeof v === "number" && v === Math.floor(v) && v >= lo && v <= hi; }
  /** The waves to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (w) {
      if (!w || typeof w !== "object" || typeof w.name !== "string" || !w.name) return false;
      if (!isInt(w.big, 2, 10) || !isInt(w.medium, 0, 6) || !isInt(w.saucer, 0, 60)) return false;
      if (typeof w.speed !== "number" || !(w.speed >= 0.4 && w.speed <= 1.6)) return false;
      var load = w.big * 2 + w.medium;
      return load <= 24 && !(w.speed > 1.2 && load > 18);
    });
    return out.length ? out : LEVELS;
  }

  /** Wave n of a mode: from the list (Waves) or made up (Classic, Calm). */
  function waveSpec(s, n) {
    if (s.waves) return s.waves[Math.min(n, s.waves.length) - 1];
    if (s.mode === "calm") {
      return { name: "Wave " + n, big: Math.min(8, 2 + n), medium: Math.min(4, Math.floor((n - 1) / 3)),
        speed: Math.min(1.1, 0.45 + 0.05 * (n - 1)), saucer: n >= 4 ? Math.min(30, 15 + 3 * (n - 4)) : 0 };
    }
    return { name: "Wave " + n, big: Math.min(10, 3 + n), medium: Math.min(6, Math.floor((n - 1) / 2)),
      speed: Math.min(1.6, 0.7 + 0.08 * (n - 1)), saucer: n >= 3 ? Math.min(60, 25 + 5 * (n - 3)) : 0 };
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function wrapX(x) { return ((x % W) + W) % W; }
  function wrapY(y) { return TOP + ((((y - TOP) % SH) + SH) % SH); }
  /** The shortest way from a to b on the wrapping screen. */
  function delta(ax, ay, bx, by) {
    var dx = bx - ax, dy = by - ay;
    dx -= W * Math.round(dx / W); dy -= SH * Math.round(dy / SH);
    return [dx, dy];
  }
  function dist(ax, ay, bx, by) { var d = delta(ax, ay, bx, by); return Math.sqrt(d[0] * d[0] + d[1] * d[1]); }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, wave: 1, level: 1, score: 0, lives: MODES[mode].lives, over: false, won: false,
      updates: 0, ship: null, rocks: [], shots: [], saucer: null, sshots: [], booms: [],
      sinceSaucer: 0, sinceShot: FIRE_GAP, pause: 0, banner: BANNER, name: "", speed: 0.5, saucerChance: 0,
      held: { left: false, right: false, up: false }, fireHeld: false, fireReq: false,
      waves: mode === "waves" ? usableLevels(o.levels) : null,
      stats: { rocks: 0, shots: 0, saucers: 0, livesLost: 0, waves: 0, cause: null },
    };
    newShip(s);
    s.ship.safe = 0;
    startWave(s);
    return s;
  }

  function newShip(s) { s.ship = { x: CX, y: CY, a: -Math.PI / 2, vx: 0, vy: 0, dead: 0, safe: SAFE, thrust: false }; }

  function shapeOf(s) {
    var n = 8 + Math.floor(rand(s) * 4), out = [];
    for (var i = 0; i < n; i++) out.push(Math.round((0.72 + rand(s) * 0.28) * 100) / 100);
    return out;
  }
  function addRock(s, size, x, y, vx, vy) {
    s.rocks.push({ x: x, y: y, vx: vx, vy: vy, size: size, shape: shapeOf(s), rot: rand(s) * 6.283,
      spin: (rand(s) - 0.5) * 0.04 });
  }
  function startWave(s) {
    var w = waveSpec(s, s.wave);
    s.name = w.name; s.speed = w.speed; s.saucerChance = w.saucer; s.banner = BANNER;
    var list = [], i;
    for (i = 0; i < w.big; i++) list.push(3);
    for (i = 0; i < w.medium; i++) list.push(2);
    for (i = 0; i < list.length; i++) {
      var x = 0, y = 0;
      for (var tries = 0; tries < 40; tries++) {
        x = rand(s) * W; y = TOP + rand(s) * SH;
        if (dist(x, y, s.ship.x, s.ship.y) >= 90) break;
      }
      var an = rand(s) * Math.PI * 2, v = w.speed * (0.5 + 0.5 * rand(s)) * (list[i] === 2 ? 1.25 : 1);
      addRock(s, list[i], x, y, Math.cos(an) * v, Math.sin(an) * v);
    }
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [], i;
    if (s.over) return evs;
    s.updates++;
    s.sinceShot++;
    if (s.banner > 0) s.banner--;
    for (i = s.booms.length - 1; i >= 0; i--) if (++s.booms[i].t > 30) s.booms.splice(i, 1);
    moveShip(s, evs);
    for (i = 0; i < s.rocks.length; i++) {
      var r = s.rocks[i];
      r.x = wrapX(r.x + r.vx); r.y = wrapY(r.y + r.vy); r.rot += r.spin;
    }
    moveSaucer(s, evs);
    moveShots(s, evs);
    if (s.over) return evs;
    shipHits(s, evs);
    if (s.over) return evs;
    // the wave is clear: a short pause, then the next one (or the end of the list)
    if (!s.rocks.length) {
      if (s.pause === 0) {
        s.stats.waves++;
        evs.push({ type: "clear", wave: s.wave });
        if (s.waves && s.wave >= s.waves.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          evs.push({ type: "win", score: s.score });
          return evs;
        }
        s.pause = WAVE_PAUSE;
      } else if (--s.pause === 0) {
        s.wave++; s.level = Math.min(MAX_LEVEL, s.wave);
        startWave(s);
        evs.push({ type: "level", level: s.level, wave: s.wave });
      }
    }
    return evs;
  }

  function moveShip(s, evs) {
    var p = s.ship;
    if (p.dead > 0) {
      s.fireReq = false;
      if (--p.dead === 0) newShip(s);
      return;
    }
    if (p.safe > 0) p.safe--;
    if (s.held.left) p.a -= ROT;
    if (s.held.right) p.a += ROT;
    if (p.a > Math.PI) p.a -= Math.PI * 2; else if (p.a < -Math.PI) p.a += Math.PI * 2;
    p.thrust = s.held.up;
    if (p.thrust) { p.vx += Math.cos(p.a) * THRUST; p.vy += Math.sin(p.a) * THRUST; }
    p.vx *= DRAG; p.vy *= DRAG;
    var v = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
    if (v > MAX_V) { p.vx *= MAX_V / v; p.vy *= MAX_V / v; }
    p.x = wrapX(p.x + p.vx); p.y = wrapY(p.y + p.vy);
    var want = s.fireReq || (s.fireHeld && s.sinceShot >= FIRE_REPEAT);
    if (want && s.sinceShot >= FIRE_GAP && s.shots.length < MAX_SHOTS) {
      s.shots.push({ x: wrapX(p.x + Math.cos(p.a) * 8), y: wrapY(p.y + Math.sin(p.a) * 8),
        vx: Math.cos(p.a) * SHOT_V + p.vx, vy: Math.sin(p.a) * SHOT_V + p.vy, life: SHOT_LIFE });
      s.sinceShot = 0; s.stats.shots++; s.fireReq = false;
      evs.push({ type: "fire" });
    } else if (s.sinceShot >= FIRE_GAP) s.fireReq = false;   // a tap waits at most for the gap between shots
  }

  function moveSaucer(s, evs) {
    s.sinceSaucer++;
    if (!s.saucer && s.sinceSaucer >= SAUCER_CHECK) {
      s.sinceSaucer = 0;
      if (s.saucerChance > 0 && s.rocks.length && rand(s) * 100 < s.saucerChance) {
        var fromLeft = rand(s) < 0.5;
        s.saucer = { x: fromLeft ? -SAUCER_R : W + SAUCER_R, y: TOP + 30 + rand(s) * (SH - 60), vx: fromLeft ? SAUCER_V : -SAUCER_V,
          phase: rand(s) * 6.283, cool: 40 };
        evs.push({ type: "saucer" });
      }
    }
    var u = s.saucer;
    if (u) {
      u.x += u.vx; u.phase += 0.03; u.y = wrapY(u.y + Math.sin(u.phase) * 0.7);
      if (u.x < -SAUCER_R - 2 || u.x > W + SAUCER_R + 2) { s.saucer = null; }
      else if (--u.cool <= 0 && !s.ship.dead) {
        u.cool = SAUCER_FIRE;
        var d = delta(u.x, u.y, s.ship.x, s.ship.y), an = Math.atan2(d[1], d[0]) + (rand(s) - 0.5) * 0.5;
        s.sshots.push({ x: u.x, y: u.y, vx: Math.cos(an) * SAUCER_SHOT_V, vy: Math.sin(an) * SAUCER_SHOT_V, life: SAUCER_SHOT_LIFE });
      }
    }
    for (var i = s.sshots.length - 1; i >= 0; i--) {
      var b = s.sshots[i];
      b.x = wrapX(b.x + b.vx); b.y = wrapY(b.y + b.vy);
      if (--b.life <= 0) s.sshots.splice(i, 1);
    }
  }

  /** Split (or remove) rock i; its pieces fly off a little faster. */
  function breakRock(s, i, evs, scored) {
    var r = s.rocks[i];
    s.rocks.splice(i, 1);
    s.booms.push({ x: r.x, y: r.y, t: 0, r: RADIUS[r.size] });
    if (scored) { s.score += POINTS[r.size]; s.stats.rocks++; }
    if (r.size > 1) {
      var base = Math.atan2(r.vy, r.vx), v = Math.min(2.6, Math.sqrt(r.vx * r.vx + r.vy * r.vy) * 1.25 + 0.15);
      for (var k = 0; k < 2; k++) {
        var an = base + (k ? 1 : -1) * (0.4 + rand(s) * 0.6);
        addRock(s, r.size - 1, r.x, r.y, Math.cos(an) * v, Math.sin(an) * v);
      }
    }
    evs.push({ type: "break", size: r.size, score: s.score });
  }

  function moveShots(s, evs) {
    for (var i = s.shots.length - 1; i >= 0; i--) {
      var b = s.shots[i], hit = false;
      for (var k = 0; k < 3 && !hit; k++) {
        b.x = wrapX(b.x + b.vx / 3); b.y = wrapY(b.y + b.vy / 3);
        for (var j = 0; j < s.rocks.length; j++) {
          var r = s.rocks[j];
          if (dist(b.x, b.y, r.x, r.y) < RADIUS[r.size] * 0.9 + 1) { breakRock(s, j, evs, true); hit = true; break; }
        }
        if (!hit && s.saucer && dist(b.x, b.y, s.saucer.x, s.saucer.y) < SAUCER_R + 1) {
          s.booms.push({ x: s.saucer.x, y: s.saucer.y, t: 0, r: SAUCER_R });
          s.saucer = null; s.score += SAUCER_POINTS; s.stats.saucers++; hit = true;
          evs.push({ type: "saucerHit", score: s.score });
        }
      }
      if (hit || --b.life <= 0) s.shots.splice(i, 1);
    }
  }

  function shipHits(s, evs) {
    var p = s.ship;
    if (p.dead || p.safe > 0) return;
    for (var j = 0; j < s.rocks.length; j++) {
      var r = s.rocks[j];
      if (dist(p.x, p.y, r.x, r.y) < RADIUS[r.size] * 0.85 + SHIP_R - 1) { breakRock(s, j, evs, false); loseLife(s, evs); return; }
    }
    if (s.saucer && dist(p.x, p.y, s.saucer.x, s.saucer.y) < SAUCER_R + SHIP_R - 1) {
      s.booms.push({ x: s.saucer.x, y: s.saucer.y, t: 0, r: SAUCER_R });
      s.saucer = null; loseLife(s, evs); return;
    }
    for (var i = 0; i < s.sshots.length; i++) {
      if (dist(p.x, p.y, s.sshots[i].x, s.sshots[i].y) < SHIP_R + 1) { s.sshots.splice(i, 1); loseLife(s, evs); return; }
    }
  }

  function loseLife(s, evs) {
    var p = s.ship;
    s.booms.push({ x: p.x, y: p.y, t: 0, r: 12 });
    s.lives--; s.stats.livesLost++;
    evs.push({ type: "lifeLost", lives: s.lives });
    if (s.lives <= 0) {
      p.dead = 1e9; s.over = true; s.stats.cause = "lives";
      evs.push({ type: "gameover", score: s.score, cause: "lives" });
      return;
    }
    p.dead = RESPAWN; p.thrust = false;
  }

  // ---------- input ----------
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "left" || action === "right" || action === "up") s.held[action] = !!down;
    else if (action === "fire") { s.fireHeld = !!down; if (down) s.fireReq = true; }
    return [];
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "waves") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.held = { left: false, right: false, up: false }; out.fireHeld = false; out.fireReq = false;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && data.ship && typeof data.ship.x === "number" &&
      Array.isArray(data.rocks) && Array.isArray(data.shots) && typeof data.score === "number" && data.wave >= 1 &&
      typeof data.lives === "number" && typeof data.rng === "number" && data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.waves = s.mode === "waves" ? usableLevels(levels) : null;
    if (!Array.isArray(s.sshots)) s.sshots = [];
    if (!Array.isArray(s.booms)) s.booms = [];
    s.held = { left: false, right: false, up: false }; s.fireHeld = false; s.fireReq = false;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { rocks: s.stats.rocks, shots: s.stats.shots, saucers: s.stats.saucers,
      waves: s.stats.waves, livesLeft: Math.max(0, s.lives), livesLost: s.stats.livesLost, won: !!s.won,
      cause: s.stats.cause, mode: s.mode } };
  }

  var RocksLogic = {
    W: W, TOP: TOP, BOTTOM: BOTTOM, SH: SH, CX: CX, CY: CY, SHIP_R: SHIP_R, RADIUS: RADIUS, POINTS: POINTS,
    MAX_SHOTS: MAX_SHOTS, FIRE_GAP: FIRE_GAP, SAUCER_R: SAUCER_R, SAUCER_POINTS: SAUCER_POINTS, SAUCER_CHECK: SAUCER_CHECK,
    SAFE: SAFE, RESPAWN: RESPAWN, WAVE_PAUSE: WAVE_PAUSE, BANNER: BANNER, MODES: MODES, STATE_VERSION: STATE_VERSION,
    LEVELS: LEVELS, usableLevels: usableLevels, waveSpec: waveSpec, create: create, step: step, press: press,
    save: save, restore: restore, result: result, rand: rand, delta: delta, dist: dist, breakRock: breakRock,
    wrapX: wrapX, wrapY: wrapY,
  };
  if (typeof module === "object" && module.exports) module.exports = RocksLogic; else self.RocksLogic = RocksLogic;
})();
