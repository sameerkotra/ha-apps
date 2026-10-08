/* Household Arcade — Brick Breaker rules (pure: no DOM, deterministic for a given seed).

   Coordinates are the game's logical 240 × 300 area. One call to step() is one
   update (60 a second). The ball moves in small sub-steps so it never passes
   through a brick or the paddle, whatever its speed. */
(function () {
  "use strict";

  var W = 240, H = 300;
  var WALL_L = 4, WALL_R = 236, WALL_T = 38;
  var COLS = 8, BX0 = 8, BPX = 28, BW = 26, BY0 = 46, BPY = 14, BH = 11;
  var PADDLE_Y = 268, PADDLE_H = 8, PADDLE_W = 52, PADDLE_WIDE = 78;
  var BALL_R = 4.5;
  var BASE_SPEED = 2.6;        // px per update at level 1 (156 px a second)
  var LEVEL_GAIN = 0.04;       // per level within a round of ten layouts
  var LATE_GAIN = 0.01;        // per level after the tenth in a long list
  var ROUND_GAIN = 0.15;       // each repeat of the whole list is faster
  var RALLY_GAIN = 0.006, RALLY_MAX = 0.15; // a little faster with each paddle hit, until a ball is lost
  var MAX_SPEED = 5.8;
  var SLOW_FACTOR = 0.7;
  var MAX_ANGLE = 60 * Math.PI / 180; // from straight up, at the paddle's edges
  var KEY_SPEED = 5, KEY_ACCEL = 0.6;   // keyboard: px per update, smoothing (reaches full speed in ~3 updates)
  var FOLLOW = 0.75, FOLLOW_MAX = 30;    // pointer: share of the distance per update, cap (within ~2 updates)
  var START_LIVES = 3, MAX_LIVES = 5, MAX_BALLS = 4;
  var DROP_CHANCE = 0.14, CAPSULE_SPEED = 1.3, CAPSULE_W = 20, CAPSULE_H = 9;
  var POWER_SECONDS = { wide: 15, slow: 10 };
  var UPS = 60;

  // Ten original layouts, 8 bricks wide. 1/2/3 = hits needed, "." = empty.
  var LAYOUTS = [
    { name: "First wall", rows: ["........", "11111111", "11111111", "11111111", "11111111", "11111111"] },
    { name: "Staircase", rows: ["2.......", "11......", "111.....", "2111....", "11111...", "111112..", "1111111.", "11111111"] },
    { name: "Diamond", rows: ["...22...", "..1111..", ".111111.", "22222222", ".111111.", "..1111..", "...22..."] },
    { name: "Checkers", rows: ["1.1.1.1.", ".2.2.2.2", "1.1.1.1.", ".2.2.2.2", "1.1.1.1.", ".2.2.2.2"] },
    { name: "Gate", rows: ["33333333", "3......3", "3.2222.3", "3.2..2.3", "3.2..2.3", "3.1111.3", "3......3"] },
    { name: "Waves", rows: ["11....11", ".11..11.", "..1111..", "2..22..2", "22....22", ".22..22.", "..2222.."] },
    { name: "Heart", rows: [".11..11.", "11111111", "12222221", ".122221.", "..1221..", "...11..."] },
    { name: "Pillars", rows: ["32.11.23", "32.11.23", "32.11.23", "11.22.11", "11.22.11", "........", "11111111"] },
    { name: "Target", rows: ["22222222", "2......2", "2.3333.2", "2.3113.2", "2.3113.2", "2.3..3.2", "2......2"] },
    { name: "Finale", rows: ["33333333", "23222232", "22333322", "12222221", "11222211", "11111111", ".111111.", "..1111.."] },
  ];
  var POWER_TYPES = ["wide", "slow", "multi", "life"];

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  /** Levels given by the server (SPEC §11): [{ name, rows }]. Checked again here; a level that doesn't fit is
      skipped. Returns the built-in LAYOUTS when nothing usable was given. */
  function usableLevels(list) {
    var out = [];
    if (Array.isArray(list)) {
      for (var i = 0; i < list.length; i++) {
        var L = list[i], ok = L && typeof L === "object" && Array.isArray(L.rows) && L.rows.length > 0 && L.rows.length <= 10;
        var bricks = 0;
        for (var r = 0; ok && r < L.rows.length; r++) {
          var row = L.rows[r];
          if (typeof row !== "string" || !/^[.123]{8}$/.test(row)) ok = false;
          else bricks += row.replace(/\./g, "").length;
        }
        if (ok && bricks > 0) out.push({ name: typeof L.name === "string" ? L.name.slice(0, 40) : "Level " + (out.length + 1), rows: L.rows.slice() });
      }
    }
    return out.length ? out : LAYOUTS;
  }

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  function create(opts) {
    var o = opts || {};
    var layouts = usableLevels(o.levels);
    var s = {
      layouts: layouts,
      mode: o.mode === "classic" ? "classic" : "powerups",
      powerups: o.mode !== "classic",
      level: o.startLevel ? startAt(o.startLevel, layouts.length) : Math.max(1, o.level | 0 || 1), score: 0, lives: START_LIVES,
      bricks: [], bricksLeft: 0, layout: null, version: 0,
      balls: [], serving: true,
      paddle: { x: W / 2, w: PADDLE_W, vx: 0 },
      input: { left: false, right: false, target: null, fire: false },
      capsules: [], timers: { wide: 0, slow: 0 }, rally: 0,
      over: false, updates: 0, rng: (Number(o.seed) >>> 0) || 1,
      stats: { bricks: 0, levels: 0, powerups: 0, paddleHits: 0, livesLost: 0 },
    };
    loadLevel(s);
    serve(s);
    return s;
  }

  function loadLevel(s) {
    var list = s.layouts || LAYOUTS;
    var L = list[(s.level - 1) % list.length];
    s.layout = L.name;
    s.bricks = []; s.bricksLeft = 0;
    for (var r = 0; r < L.rows.length; r++) {
      for (var c = 0; c < COLS; c++) {
        var ch = L.rows[r][c];
        if (ch === "1" || ch === "2" || ch === "3") {
          var hp = +ch;
          s.bricks.push({ x: BX0 + c * BPX, y: BY0 + r * BPY, w: BW, h: BH, hp: hp, max: hp, row: r, col: c, alive: true });
          s.bricksLeft++;
        }
      }
    }
    s.version++;
  }

  function serve(s) {
    s.balls = [{ x: s.paddle.x, y: PADDLE_Y - BALL_R - 0.5, dx: 0, dy: -1 }];
    s.serving = true; s.rally = 0; s.capsules = [];
    s.timers.wide = 0; s.timers.slow = 0;
    s.paddle.w = PADDLE_W;
    s.paddle.x = clamp(s.paddle.x, WALL_L + PADDLE_W / 2, WALL_R - PADDLE_W / 2);
    s.input.fire = false;
  }

  /** Ball speed at the start of a level. Within one pass through the levels it grows 4 % a level for the first
      ten, then 1 % a level (so a long list doesn't run away); each repeat of the whole list is 15 % faster. */
  function levelSpeed(level, count) {
    var n = count || LAYOUTS.length;
    var inRound = (level - 1) % n, round = Math.floor((level - 1) / n);
    var gain = LEVEL_GAIN * Math.min(10, inRound) + LATE_GAIN * Math.max(0, inRound - 10);
    return Math.min(MAX_SPEED, BASE_SPEED * (1 + gain) * (1 + ROUND_GAIN * round));
  }
  function ballSpeed(s) {
    var v = levelSpeed(s.level, (s.layouts || LAYOUTS).length) * (1 + Math.min(RALLY_MAX, s.rally * RALLY_GAIN));
    if (s.timers.slow > 0) v *= SLOW_FACTOR;
    return Math.min(MAX_SPEED, v);
  }

  /** Keyboard / buttons: "left", "right", "fire" (launch). */
  function press(s, key, down) {
    if (key === "left" || key === "right") { s.input[key] = !!down; if (down) s.input.target = null; }
    else if (key === "fire" && down) s.input.fire = true;
  }
  /** Pointer (mouse or finger): the paddle follows this x smoothly. null stops following. */
  function setTarget(s, x) { s.input.target = x == null ? null : clamp(x, 0, W); }

  function stepPaddle(s) {
    var p = s.paddle, nx = p.x, inp = s.input;
    var half = p.w / 2;
    if (inp.left || inp.right) {
      var dir = (inp.right ? 1 : 0) - (inp.left ? 1 : 0);
      p.vx += (dir * KEY_SPEED - p.vx) * KEY_ACCEL;
      nx = p.x + p.vx;
    } else if (inp.target != null) {
      var d = inp.target - p.x;
      nx = Math.abs(d) < 0.25 ? inp.target : p.x + clamp(d * FOLLOW, -FOLLOW_MAX, FOLLOW_MAX);
    } else {
      p.vx *= 0.5; if (Math.abs(p.vx) < 0.05) p.vx = 0;
      nx = p.x + p.vx;
    }
    nx = clamp(nx, WALL_L + half, WALL_R - half);
    if (!(inp.left || inp.right)) p.vx = nx - p.x;
    p.x = nx;
  }

  function launch(s, ev) {
    var p = s.paddle, sign;
    if (p.vx > 0.4) sign = 1; else if (p.vx < -0.4) sign = -1; else sign = rand(s) < 0.5 ? -1 : 1;
    var ang = sign * (15 + rand(s) * 20) * Math.PI / 180;
    var b = s.balls[0];
    b.dx = Math.sin(ang); b.dy = -Math.cos(ang);
    s.serving = false;
    ev.push({ type: "launch" });
  }

  function apply(s, type, ev) {
    s.stats.powerups++;
    if (type === "wide") {
      s.timers.wide = POWER_SECONDS.wide * UPS; s.paddle.w = PADDLE_WIDE;
      s.paddle.x = clamp(s.paddle.x, WALL_L + PADDLE_WIDE / 2, WALL_R - PADDLE_WIDE / 2);
    } else if (type === "slow") {
      s.timers.slow = POWER_SECONDS.slow * UPS;
    } else if (type === "multi") {
      var b0 = s.balls[0];
      if (b0 && s.balls.length < MAX_BALLS) {
        var dx = Math.abs(b0.dx) < 0.1 ? 0.5 : -b0.dx, dy = b0.dy < 0 ? b0.dy : -Math.abs(b0.dy);
        var n = Math.sqrt(dx * dx + dy * dy);
        s.balls.push({ x: b0.x, y: b0.y, dx: dx / n, dy: dy / n });
      }
    } else if (type === "life") {
      s.lives = Math.min(MAX_LIVES, s.lives + 1);
    }
    ev.push({ type: "powerup", power: type });
  }

  function hitBrick(s, br, ev) {
    br.hp--; s.version++;
    if (br.hp > 0) { ev.push({ type: "hit", hp: br.hp, x: br.x, y: br.y, w: br.w, h: br.h }); return; }
    br.alive = false; s.bricksLeft--;
    s.score += 10 * br.max; s.stats.bricks++;
    ev.push({ type: "break", x: br.x, y: br.y, w: br.w, h: br.h, row: br.row, max: br.max });
    if (s.powerups && rand(s) < DROP_CHANCE) {
      var r = rand(s), type = r < 0.35 ? "wide" : r < 0.65 ? "slow" : r < 0.9 ? "multi" : "life";
      s.capsules.push({ x: br.x + br.w / 2, y: br.y + br.h / 2, type: type });
      ev.push({ type: "drop", power: type });
    }
  }

  // Move one ball a short distance and resolve walls, paddle and bricks. false = lost.
  function moveBall(s, b, d, ev) {
    b.x += b.dx * d; b.y += b.dy * d;
    var R = BALL_R;
    if (b.x - R < WALL_L) { b.x = WALL_L + R; b.dx = Math.abs(b.dx); ev.push({ type: "wall" }); }
    else if (b.x + R > WALL_R) { b.x = WALL_R - R; b.dx = -Math.abs(b.dx); ev.push({ type: "wall" }); }
    if (b.y - R < WALL_T) { b.y = WALL_T + R; b.dy = Math.abs(b.dy); ev.push({ type: "wall" }); }

    var p = s.paddle;
    if (b.dy > 0 && b.y + R >= PADDLE_Y && b.y + R <= PADDLE_Y + PADDLE_H + d + 1 &&
        b.x >= p.x - p.w / 2 - R && b.x <= p.x + p.w / 2 + R) {
      b.y = PADDLE_Y - R;
      // Where the ball meets the paddle sets the angle: the edges send it out wider.
      var t = clamp((b.x - p.x) / (p.w / 2 + R * 0.5), -1, 1);
      var ang = t * MAX_ANGLE;
      b.dx = Math.sin(ang); b.dy = -Math.cos(ang);
      s.rally++; s.stats.paddleHits++;
      ev.push({ type: "paddle", offset: t });
    }

    var hit = null, best = Infinity, i, br;
    for (i = 0; i < s.bricks.length; i++) {
      br = s.bricks[i];
      if (!br.alive) continue;
      var cx = clamp(b.x, br.x, br.x + br.w), cy = clamp(b.y, br.y, br.y + br.h);
      var ex = b.x - cx, ey = b.y - cy, d2 = ex * ex + ey * ey;
      if (d2 < R * R && d2 < best) { best = d2; hit = br; }
    }
    if (hit) {
      var ox = Math.min(b.x + R - hit.x, hit.x + hit.w - (b.x - R));
      var oy = Math.min(b.y + R - hit.y, hit.y + hit.h - (b.y - R));
      if (ox < oy) {
        if (b.x < hit.x + hit.w / 2) { b.x -= ox; b.dx = -Math.abs(b.dx); } else { b.x += ox; b.dx = Math.abs(b.dx); }
      } else {
        if (b.y < hit.y + hit.h / 2) { b.y -= oy; b.dy = -Math.abs(b.dy); } else { b.y += oy; b.dy = Math.abs(b.dy); }
      }
      hitBrick(s, hit, ev);
    }
    return b.y - R <= H;
  }

  /** One update (1/60 s). Returns the events that happened. */
  function step(s) {
    var ev = [], i;
    if (s.over) return ev;
    s.updates++;
    stepPaddle(s);

    if (s.timers.wide > 0 && --s.timers.wide === 0) {
      s.paddle.w = PADDLE_W; ev.push({ type: "powerEnd", power: "wide" });
      s.paddle.x = clamp(s.paddle.x, WALL_L + PADDLE_W / 2, WALL_R - PADDLE_W / 2);
    }
    if (s.timers.slow > 0 && --s.timers.slow === 0) ev.push({ type: "powerEnd", power: "slow" });

    for (i = s.capsules.length - 1; i >= 0; i--) {
      var c = s.capsules[i], p = s.paddle;
      c.y += CAPSULE_SPEED;
      if (c.y + CAPSULE_H / 2 >= PADDLE_Y && c.y - CAPSULE_H / 2 <= PADDLE_Y + PADDLE_H &&
          c.x + CAPSULE_W / 2 >= p.x - p.w / 2 && c.x - CAPSULE_W / 2 <= p.x + p.w / 2) {
        s.capsules.splice(i, 1); apply(s, c.type, ev);
      } else if (c.y - CAPSULE_H / 2 > H) s.capsules.splice(i, 1);
    }

    if (s.serving) {
      var b0 = s.balls[0];
      b0.x = s.paddle.x; b0.y = PADDLE_Y - BALL_R - 0.5;
      if (s.input.fire) { s.input.fire = false; launch(s, ev); }
      return ev;
    }
    s.input.fire = false;

    var speed = ballSpeed(s), n = Math.max(1, Math.ceil(speed / 2)), d = speed / n;
    for (i = s.balls.length - 1; i >= 0; i--) {
      var alive = true;
      for (var k = 0; k < n && alive && s.bricksLeft > 0; k++) alive = moveBall(s, s.balls[i], d, ev);
      if (!alive) s.balls.splice(i, 1);
    }

    if (s.bricksLeft === 0) {
      var bonus = 100 * s.level;
      s.score += bonus; s.stats.levels++;
      ev.push({ type: "level", cleared: s.level, bonus: bonus, next: s.level + 1 });
      s.level++;
      loadLevel(s); serve(s);
      return ev;
    }
    if (!s.balls.length) {
      s.lives--; s.stats.livesLost++;
      if (s.lives <= 0) { s.lives = 0; s.over = true; ev.push({ type: "gameover" }); }
      else { ev.push({ type: "lifeLost", lives: s.lives }); serve(s); }
    }
    return ev;
  }

  /** A saved game (SPEC §12): the rules state as plain data, without the level list (the server keeps it with the
      save and gives it back as `levels`). */
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "layouts") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  /** The state back from save() (and the level list). Throws if it isn't one. */
  function restore(data, levels) {
    var ok = data && typeof data === "object" && Array.isArray(data.bricks) && Array.isArray(data.balls) &&
      data.paddle && typeof data.paddle.x === "number" && typeof data.score === "number" && data.level >= 1 &&
      data.lives >= 0 && data.timers && typeof data.rng === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.layouts = usableLevels(levels);
    s.input = { left: false, right: false, target: null, fire: false };
    if (!s.balls.length) serve(s);
    s.version = (s.version || 0) + 1;
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { bricks: s.stats.bricks, levelsCleared: s.stats.levels, powerups: s.stats.powerups,
        paddleHits: s.stats.paddleHits, livesLeft: s.lives, mode: s.mode },
    };
  }

  var BrickLogic = {
    W: W, H: H, WALL_L: WALL_L, WALL_R: WALL_R, WALL_T: WALL_T,
    COLS: COLS, BX0: BX0, BPX: BPX, BW: BW, BY0: BY0, BPY: BPY, BH: BH,
    PADDLE_Y: PADDLE_Y, PADDLE_H: PADDLE_H, PADDLE_W: PADDLE_W, PADDLE_WIDE: PADDLE_WIDE, BALL_R: BALL_R,
    FOLLOW: FOLLOW, FOLLOW_MAX: FOLLOW_MAX, KEY_SPEED: KEY_SPEED,
    BASE_SPEED: BASE_SPEED, MAX_SPEED: MAX_SPEED, SLOW_FACTOR: SLOW_FACTOR, MAX_ANGLE: MAX_ANGLE,
    START_LIVES: START_LIVES, MAX_LIVES: MAX_LIVES, MAX_BALLS: MAX_BALLS, DROP_CHANCE: DROP_CHANCE,
    CAPSULE_W: CAPSULE_W, CAPSULE_H: CAPSULE_H, POWER_SECONDS: POWER_SECONDS, POWER_TYPES: POWER_TYPES,
    LAYOUTS: LAYOUTS, UPS: UPS, usableLevels: usableLevels, STATE_VERSION: STATE_VERSION, save: save, restore: restore,
    create: create, step: step, press: press, setTarget: setTarget, loadLevel: loadLevel, serve: serve,
    levelSpeed: levelSpeed, ballSpeed: ballSpeed, apply: apply, hitBrick: hitBrick, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = BrickLogic; else self.BrickLogic = BrickLogic;
})();
