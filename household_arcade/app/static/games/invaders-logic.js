/* Household Arcade — Sky Defenders rules (pure: no DOM, deterministic for a given seed).

   A formation of little critters marches side to side, stepping down at the edges and getting quicker as
   fewer are left; they drop bombs. You move a cannon along the bottom and fire one shot at a time, from
   behind four shields that wear away block by block. Now and then a bonus ship crosses the top. A bomb on
   the cannon costs a life (3); the game ends when the lives run out or the formation reaches the bottom.
   Clearing the formation brings the next wave. One call to step() is one update (60 a second).

   Waves (SPEC §11.9): every mode plays a list of waves { name, formation, speed, bombs, shields }.
   Classic and Easy play the built-in list round and round (a little quicker each round, up to wave 100,
   which wins); Waves plays the session's list (opts.levels) once, and its last wave wins. */
(function () {
  "use strict";

  var W = 240, H = 300;
  var COLS = 11, CELL = 18;                 // the formation's grid: 11 columns, 18 px apart (rows too)
  var FORM_TOP = 54, FORM_X = 21;           // where a wave starts (the top row's top, column 0's left)
  var HALF_W = 6, HALF_H = 5;               // a critter's size for hits (12 × 10 around its middle)
  var EDGE_L = 6, EDGE_R = 234;             // the formation turns before it passes these
  var STEP_X = 3, STEP_Y = 6;
  var LAND_Y = 262;                         // a critter's bottom this low: the formation has landed
  var CANNON_Y = 266, CANNON_HALF = 8, CANNON_MIN = 12, CANNON_MAX = 228;
  var CANNON_SPEED = 2, DRAG_SPEED = 3;     // px an update with the keys / following a finger
  var SHOT_SPEED = 5, SHOT_LEN = 6, FIRE_GAP = 15;   // at most one shot on screen, ≥ 15 updates apart
  var BOMB_LEN = 8, BOMB_HALF = 2, GROUND = 280;
  var SHIELD_Y = 230, BLOCK = 3, SHIELD_COLS = 8, SHIELD_ROWS = 6;
  var SHIELD_SHAPE = [".######.", "########", "########", "########", "###..###", "##....##"];
  var SHIP_Y = 44, SHIP_HALF = 10, SHIP_SPEED = 1, SHIP_GAP = 1200, SHIP_FIRST = 900, SHIPS_PER_WAVE = 2;
  var SHIP_POINTS = [50, 100, 150, 300];
  var POINTS = { a: 30, b: 20, c: 10 };
  var START_PAUSE = 90, DEATH_PAUSE = 90, CLEAR_PAUSE = 60;
  var LIVES = 3, MAX_WAVES = 100;
  var MODES = {
    classic: { speed: 1, bombGap: 1, bombSpeed: 2 },
    easy: { speed: 0.8, bombGap: 1.6, bombSpeed: 1.5 },
    waves: { speed: 1, bombGap: 1, bombSpeed: 2 },      // the session's wave list, once
  };

  // The built-in waves; the session's list (opts.levels) replaces them in Waves mode. A wave is
  // { name, formation: 3–6 rows of 11 of "." (empty) "a" (30) "b" (20) "c" (10), speed 0.5–2,
  //   bombs 1–10 (how often bombs drop), shields 0–4 }.
  var LEVELS = [
    { name: "First visitors", formation: [".aaaaaaaaa.", "bbbbbbbbbbb", "ccccccccccc"], speed: 0.6, bombs: 2, shields: 4 },
    { name: "Busy bees", formation: ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ccccccccccc"], speed: 0.7, bombs: 3, shields: 4 },
    { name: "Checkerboard", formation: ["a.a.a.a.a.a", ".b.b.b.b.b.", "c.c.c.c.c.c", ".c.c.c.c.c.", "c.c.c.c.c.c"], speed: 0.8, bombs: 3, shields: 4 },
    { name: "Big parade", formation: ["aaaaaaaaaaa", "bbbbbbbbbbb", "bbbbbbbbbbb", "ccccccccccc", "ccccccccccc"], speed: 0.8, bombs: 4, shields: 4 },
    { name: "Arrowhead", formation: [".....a.....", "....aaa....", "...bbbbb...", "..bbbbbbb..", ".ccccccccc.", "ccccccccccc"], speed: 0.9, bombs: 5, shields: 3 },
    { name: "Twin towers", formation: ["aaa.....aaa", "bbb.....bbb", "bbb.....bbb", "ccc.....ccc", "ccc.....ccc"], speed: 1, bombs: 5, shields: 3 },
    { name: "Storm front", formation: ["a.a.a.a.a.a", "bbbbbbbbbbb", ".b.b.b.b.b.", "ccccccccccc", "c.c.c.c.c.c"], speed: 1.1, bombs: 7, shields: 3 },
    { name: "Last stand", formation: ["aaaaaaaaaaa", "aaaaaaaaaaa", "bbbbbbbbbbb", "bbbbbbbbbbb", "ccccccccccc", "ccccccccccc"], speed: 1.3, bombs: 8, shields: 2 },
  ];
  var MIN_CRITTERS = 6;

  function isInt(v, lo, hi) { return typeof v === "number" && v === Math.floor(v) && v >= lo && v <= hi; }

  /** The waves to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (w) {
      if (!w || typeof w !== "object" || typeof w.name !== "string" || !w.name) return false;
      if (!Array.isArray(w.formation) || w.formation.length < 3 || w.formation.length > 6) return false;
      var n = 0;
      for (var i = 0; i < w.formation.length; i++) {
        var row = w.formation[i];
        if (typeof row !== "string" || !/^[.abc]{11}$/.test(row)) return false;
        n += row.replace(/\./g, "").length;
      }
      if (n < MIN_CRITTERS) return false;
      if (typeof w.speed !== "number" || !(w.speed >= 0.5 && w.speed <= 2)) return false;
      return isInt(w.bombs, 1, 10) && isInt(w.shields, 0, 4);
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  /** Wave n's numbers: { name, formation, speed, bombs, shields } with the mode applied. */
  function wave(s, n) {
    var list = s.waves || LEVELS, base, round = 0;
    if (s.waves) base = list[Math.min(n, list.length) - 1];
    else { base = list[(n - 1) % list.length]; round = Math.floor((n - 1) / list.length); }
    var m = MODES[s.mode];
    return {
      name: base.name, formation: base.formation, shields: base.shields,
      speed: Math.min(2.4, base.speed * (1 + 0.15 * round)) * m.speed,
      bombs: Math.min(10, base.bombs + 2 * round),
      bombGap: m.bombGap, bombSpeed: m.bombSpeed,
    };
  }
  function waveCount(s) { return s.waves ? s.waves.length : MAX_WAVES; }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES[o.mode] ? o.mode : "classic", rng: (o.seed >>> 0) || 1, updates: 0,
      score: 0, level: 1, lives: LIVES, over: false, won: false,
      waves: null,
      rows: 0, grid: [], total: 0, alive: 0, fx: FORM_X, fy: FORM_TOP, dir: 1, moveT: 0, frame: 0,
      x: W / 2, hold: { left: false, right: false }, target: null, fireHeld: false, fireBuf: 0, lastShot: -FIRE_GAP,
      shot: null, bombs: [], bombT: 0, shields: [], shieldVer: 0,
      ship: null, lastShip: SHIP_FIRST - SHIP_GAP, shipsThisWave: 0,
      phase: "start", pause: START_PAUSE, waveT: 0, pops: [],
      stats: { critters: 0, ships: 0, shots: 0, waves: 0, cause: null },
    };
    if (s.mode === "waves") s.waves = usableLevels(o.levels);
    startWave(s);
    return s;
  }

  function startWave(s) {
    var w = wave(s, s.level);
    s.rows = w.formation.length;
    s.grid = [];
    for (var r = 0; r < s.rows; r++) for (var c = 0; c < COLS; c++) {
      var ch = w.formation[r][c];
      s.grid.push(ch === "." ? "" : ch);
    }
    s.total = s.alive = s.grid.filter(function (v) { return v; }).length;
    s.fx = FORM_X; s.fy = FORM_TOP; s.dir = 1; s.frame = 0;
    s.moveT = interval(s);
    s.shot = null; s.bombs = []; s.ship = null; s.shipsThisWave = 0;
    s.bombT = nextBomb(s);
    s.shields = [];
    for (var i = 0; i < w.shields; i++) {
      var cx = Math.round(W * (i + 0.5) / w.shields / 3) * 3;
      s.shields.push({ x: cx - SHIELD_COLS * BLOCK / 2, blocks: SHIELD_SHAPE.join("").split("").map(function (ch) { return ch === "#" ? 1 : 0; }) });
    }
    s.shieldVer++;
    s.phase = "start"; s.pause = START_PAUSE; s.waveT = 0;
  }

  /** Updates between the formation's steps: slower when full, quicker as fewer are left. */
  function interval(s) {
    var w = wave(s, s.level);
    return Math.max(2, Math.round((4 + 30 * s.alive / Math.max(1, s.total)) / w.speed));
  }
  function nextBomb(s) {
    var w = wave(s, s.level);
    return Math.round((15 + Math.floor(rand(s) * 400 / w.bombs)) * w.bombGap);
  }
  function maxBombs(s) { return 1 + Math.floor(wave(s, s.level).bombs / 3); }

  function critterX(s, c) { return s.fx + c * CELL + CELL / 2; }
  function critterY(s, r) { return s.fy + r * CELL + HALF_H; }

  /** The alive formation's extent: { minC, maxC, maxR } (null when empty). */
  function extent(s) {
    var minC = COLS, maxC = -1, maxR = -1;
    for (var i = 0; i < s.grid.length; i++) if (s.grid[i]) {
      var c = i % COLS, r = (i - c) / COLS;
      if (c < minC) minC = c;
      if (c > maxC) maxC = c;
      if (r > maxR) maxR = r;
    }
    return maxC < 0 ? null : { minC: minC, maxC: maxC, maxR: maxR };
  }

  function moveCannon(s) {
    var l = s.hold.left, r = s.hold.right;
    if (l !== r) { s.target = null; s.x += l ? -CANNON_SPEED : CANNON_SPEED; }
    else if (s.target != null) s.x += clamp(s.target - s.x, -DRAG_SPEED, DRAG_SPEED);
    s.x = clamp(s.x, CANNON_MIN, CANNON_MAX);
  }

  function canFire(s) { return s.phase === "play" && !s.shot && s.updates - s.lastShot >= FIRE_GAP; }

  function fire(s, evs) {
    s.shot = { x: s.x, y: CANNON_Y - 4 - SHOT_LEN };
    s.lastShot = s.updates; s.fireBuf = 0; s.stats.shots++;
    evs.push({ type: "fire" });
  }

  // ---- shields ----
  function shieldBlock(s, x, y) {     // { sh, i } of the standing block at (x, y), or null
    if (y < SHIELD_Y || y >= SHIELD_Y + SHIELD_ROWS * BLOCK) return null;
    for (var k = 0; k < s.shields.length; k++) {
      var sh = s.shields[k], c = Math.floor((x - sh.x) / BLOCK);
      if (c < 0 || c >= SHIELD_COLS) continue;
      var i = Math.floor((y - SHIELD_Y) / BLOCK) * SHIELD_COLS + c;
      if (sh.blocks[i]) return { sh: sh, i: i };
    }
    return null;
  }
  function chip(s, hit, dr) {          // wear away the block hit and the one next to it (dr rows on)
    hit.sh.blocks[hit.i] = 0;
    var j = hit.i + dr * SHIELD_COLS;
    if (j >= 0 && j < hit.sh.blocks.length) hit.sh.blocks[j] = 0;
    s.shieldVer++;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    s.waveT++;
    for (var p = s.pops.length - 1; p >= 0; p--) if (--s.pops[p].t <= 0) s.pops.splice(p, 1);
    if (s.fireBuf > 0) s.fireBuf--;
    if (s.phase !== "dead") moveCannon(s);
    if (s.phase !== "play") {
      if (--s.pause > 0) return evs;
      if (s.phase === "clear") startWave(s);
      else s.phase = "play";
      return evs;
    }

    if ((s.fireHeld || s.fireBuf > 0) && canFire(s)) fire(s, evs);
    moveShot(s, evs);
    if (s.over || s.phase !== "play") return evs;

    if (--s.moveT <= 0) { march(s, evs); s.moveT = interval(s); if (s.over) return evs; }

    moveBombs(s, evs);
    if (s.over || s.phase !== "play") return evs;
    moveShip(s, evs);
    return evs;
  }

  function moveShot(s, evs) {
    var sh = s.shot;
    if (!sh) return;
    for (var sub = 0; sub < 2; sub++) {
      sh.y -= SHOT_SPEED / 2;
      var i;
      for (i = 0; i < s.bombs.length; i++) {          // a shot meeting a bomb: both go
        var b = s.bombs[i];
        if (Math.abs(b.x - sh.x) <= BOMB_HALF + 1 && sh.y < b.y + BOMB_LEN && sh.y + SHOT_LEN > b.y) {
          s.bombs.splice(i, 1); s.shot = null;
          s.pops.push({ x: sh.x, y: sh.y, t: 10, k: "zap" });
          evs.push({ type: "zap" });
          return;
        }
      }
      var hit = shieldBlock(s, sh.x, sh.y);
      if (hit) { chip(s, hit, -1); s.shot = null; evs.push({ type: "chip" }); return; }
      for (i = 0; i < s.grid.length; i++) {
        if (!s.grid[i]) continue;
        var c = i % COLS, r = (i - c) / COLS, cx = critterX(s, c), cy = critterY(s, r);
        if (Math.abs(sh.x - cx) <= HALF_W && sh.y <= cy + HALF_H && sh.y + SHOT_LEN >= cy - HALF_H) {
          var pts = POINTS[s.grid[i]];
          s.grid[i] = ""; s.alive--; s.shot = null;
          s.score += pts; s.stats.critters++;
          s.pops.push({ x: cx, y: cy, t: 15, k: "hit" });
          evs.push({ type: "hit", points: pts, score: s.score });
          if (!s.alive) clearWave(s, evs);
          return;
        }
      }
      var ship = s.ship;
      if (ship && Math.abs(sh.x - ship.x) <= SHIP_HALF && sh.y <= SHIP_Y + 4 && sh.y + SHOT_LEN >= SHIP_Y - 4) {
        s.score += ship.points; s.stats.ships++;
        s.pops.push({ x: ship.x, y: SHIP_Y, t: 60, k: "ship", points: ship.points });
        evs.push({ type: "bonus", points: ship.points, score: s.score });
        s.ship = null; s.shot = null;
        return;
      }
      if (sh.y < 36) { s.shot = null; return; }
    }
  }

  function clearWave(s, evs) {
    s.stats.waves++;
    s.bombs = []; s.ship = null; s.shot = null;
    if (s.level >= waveCount(s)) {
      s.won = true; s.over = true; s.stats.cause = "won";
      evs.push({ type: "win", score: s.score });
      return;
    }
    s.level++;
    evs.push({ type: "level", level: s.level });
    s.phase = "clear"; s.pause = CLEAR_PAUSE;
  }

  function march(s, evs) {
    var e = extent(s);
    if (!e) return;
    var left = critterX(s, e.minC) - HALF_W + STEP_X * s.dir, right = critterX(s, e.maxC) + HALF_W + STEP_X * s.dir;
    if (left < EDGE_L || right > EDGE_R) { s.fy += STEP_Y; s.dir = -s.dir; }
    else s.fx += STEP_X * s.dir;
    s.frame ^= 1;
    // critters wear away the shields they march through
    for (var i = 0; i < s.grid.length; i++) {
      if (!s.grid[i]) continue;
      var c = i % COLS, r = (i - c) / COLS, cx = critterX(s, c), cy = critterY(s, r);
      if (cy + HALF_H < SHIELD_Y) continue;
      for (var y = cy - HALF_H; y <= cy + HALF_H; y += BLOCK) for (var x = cx - HALF_W; x <= cx + HALF_W; x += BLOCK) {
        var hit = shieldBlock(s, x, y);
        if (hit) { hit.sh.blocks[hit.i] = 0; s.shieldVer++; }
      }
    }
    if (critterY(s, e.maxR) + HALF_H >= LAND_Y) end(s, evs, "landed");
  }

  function moveBombs(s, evs) {
    var i;
    if (s.bombs.length < maxBombs(s) && --s.bombT <= 0) {
      dropBomb(s);
      s.bombT = nextBomb(s);
    }
    var speed = wave(s, s.level).bombSpeed;
    for (i = s.bombs.length - 1; i >= 0; i--) {
      var b = s.bombs[i];
      b.y += speed;
      var hit = shieldBlock(s, b.x, b.y + BOMB_LEN);
      if (hit) { chip(s, hit, 1); s.bombs.splice(i, 1); continue; }
      if (Math.abs(b.x - s.x) <= CANNON_HALF + BOMB_HALF && b.y + BOMB_LEN >= CANNON_Y && b.y <= CANNON_Y + 12) {
        loseLife(s, evs);
        return;
      }
      if (b.y > GROUND) s.bombs.splice(i, 1);
    }
  }

  function dropBomb(s) {
    var cols = [], best = -1, bestD = 1e9, c, r;
    for (c = 0; c < COLS; c++) for (r = s.rows - 1; r >= 0; r--) if (s.grid[r * COLS + c]) {
      cols.push(c);
      var d = Math.abs(critterX(s, c) - s.x);
      if (d < bestD) { bestD = d; best = c; }
      break;
    }
    if (!cols.length) return;
    c = rand(s) < 0.35 ? best : cols[Math.floor(rand(s) * cols.length)];
    for (r = s.rows - 1; r >= 0; r--) if (s.grid[r * COLS + c]) break;
    s.bombs.push({ x: critterX(s, c), y: critterY(s, r) + HALF_H, k: Math.floor(rand(s) * 2) });
  }

  function moveShip(s, evs) {
    if (s.ship) {
      s.ship.x += s.ship.dir * SHIP_SPEED;
      if (s.ship.x < -SHIP_HALF - 4 || s.ship.x > W + SHIP_HALF + 4) s.ship = null;
      return;
    }
    if (s.shipsThisWave >= SHIPS_PER_WAVE || s.alive < 6 || s.updates - s.lastShip < SHIP_GAP) return;
    if (rand(s) >= 1 / 360) return;
    var dir = rand(s) < 0.5 ? 1 : -1;
    s.ship = { x: dir > 0 ? -SHIP_HALF : W + SHIP_HALF, dir: dir, points: SHIP_POINTS[Math.floor(rand(s) * SHIP_POINTS.length)] };
    s.lastShip = s.updates; s.shipsThisWave++;
    evs.push({ type: "ship" });
  }

  function loseLife(s, evs) {
    s.lives--;
    s.pops.push({ x: s.x, y: CANNON_Y + 4, t: DEATH_PAUSE, k: "cannon" });
    s.bombs = []; s.shot = null; s.target = null;
    evs.push({ type: "lifeLost", lives: s.lives });
    if (s.lives <= 0) { end(s, evs, "bombed"); return; }
    s.phase = "dead"; s.pause = DEATH_PAUSE;
  }

  function end(s, evs, cause) {
    s.over = true; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause });
  }

  /** Keys and buttons: ← → held move; fire (or ↑) shoots — held, it shoots again as soon as it may. */
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "left" || action === "right") { s.hold[action] = !!down; if (down) s.target = null; return []; }
    if (action === "fire" || action === "up") {
      s.fireHeld = !!down;
      if (down) s.fireBuf = 12;          // a quick tap while the last shot is still flying isn't lost
    }
    return [];
  }

  /** A finger dragged on the game: the cannon follows its x (at most 3 px an update). */
  function aim(s, x) { if (!s.over && typeof x === "number" && isFinite(x)) s.target = clamp(x, CANNON_MIN, CANNON_MAX); }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "waves") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.grid) && Array.isArray(data.bombs) &&
      Array.isArray(data.shields) && typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" &&
      typeof data.x === "number" && data.lives >= 1 && data.stats && typeof data.phase === "string";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.waves = s.mode === "waves" ? usableLevels(levels) : null;
    if (s.waves && s.level > s.waves.length) s.level = s.waves.length;
    s.hold = { left: false, right: false }; s.fireHeld = false; s.target = null;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { critters: s.stats.critters, ships: s.stats.ships, shots: s.stats.shots,
      waves: s.stats.waves, livesLeft: s.lives, won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var InvadersLogic = {
    W: W, H: H, COLS: COLS, CELL: CELL, HALF_W: HALF_W, HALF_H: HALF_H, LAND_Y: LAND_Y, CANNON_Y: CANNON_Y, CANNON_HALF: CANNON_HALF,
    SHOT_LEN: SHOT_LEN, FIRE_GAP: FIRE_GAP, BOMB_LEN: BOMB_LEN, GROUND: GROUND, SHIELD_Y: SHIELD_Y, BLOCK: BLOCK,
    SHIELD_COLS: SHIELD_COLS, SHIELD_ROWS: SHIELD_ROWS, SHIP_Y: SHIP_Y, SHIP_SPEED: SHIP_SPEED, SHIP_HALF: SHIP_HALF, SHIP_GAP: SHIP_GAP,
    SHIP_FIRST: SHIP_FIRST, SHIP_POINTS: SHIP_POINTS, POINTS: POINTS, LIVES: LIVES, MAX_WAVES: MAX_WAVES, MODES: MODES,
    STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    create: create, step: step, press: press, aim: aim, wave: wave, waveCount: waveCount, interval: interval,
    critterX: critterX, critterY: critterY, extent: extent, canFire: canFire,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = InvadersLogic; else self.InvadersLogic = InvadersLogic;
})();
