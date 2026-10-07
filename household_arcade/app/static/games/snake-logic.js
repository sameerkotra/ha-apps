/* Household Arcade — Snake rules (pure: no DOM, deterministic for a given seed).

   One call to step() is one update (60 a second). The snake moves one cell
   whenever its progress reaches a whole cell, so speed is the same on every
   device. Turns are queued: two quick turns within one move are both kept. */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var DIRS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
  var OPP = { up: "down", down: "up", left: "right", right: "left" };
  var SPEEDS = {
    slow: { cps: 6, mult: 1 },     // cells per second at the start, score multiplier
    normal: { cps: 8, mult: 1.5 },
    fast: { cps: 11, mult: 2 },
  };
  var MODES = ["walls-slow", "walls-normal", "walls-fast", "wrap-slow", "wrap-normal", "wrap-fast", "maze"];
  var UPS = 60;                // updates per second
  var FOOD_POINTS = 10;        // × speed multiplier
  var BONUS_POINTS = 50;
  var BONUS_SECONDS = 5;
  var BONUS_CHANCE = 0.25;     // after each food, once at least 3 foods are eaten
  var SPEEDUP_EVERY = 5;       // foods
  var SPEEDUP_CPS = 0.6;
  var MAX_CPS = 16;
  var MAX_QUEUE = 3;
  var EARLY_TURN = 0.5;        // share of a cell after which a turn is made at once
  var MAZE_SIZE = 20;
  var MAZE_CLEAR_POINTS = 50;  // × the maze's number
  var MAZE_CPS_STEP = 0.3;     // each maze a little faster
  var MAZE_START = { x: 9, y: 10 }; // the head; the body runs left of it, heading right
  // Ten original mazes (Maze mode), 20 × 20: "#" a wall. foods = how many to eat to clear it.
  // The same as app/level_data.py (tests/test_levels.py checks they match).
  var MAZES = [
    { name: "Four posts", foods: 5, walls: [
      "....................", "....................",
      "....................", "....................",
      "....##........##....", "....##........##....",
      "....................", "....................",
      "....................", "....................",
      "....................", "....................",
      "....................", "....................",
      "....##........##....", "....##........##....",
      "....................", "....................",
      "....................", "....................",
    ] },
    { name: "Two bars", foods: 6, walls: [
      "....................", "....................",
      "....................", "....................",
      "....############....", "....................",
      "....................", "....................",
      "....................", "....................",
      "....................", "....................",
      "....................", "....................",
      "....................", "....############....",
      "....................", "....................",
      "....................", "....................",
    ] },
    { name: "Open box", foods: 7, walls: [
      "....................", "....................",
      "....................", "...######..######...",
      "...#............#...", "...#............#...",
      "...#............#...", "...#............#...",
      "...#............#...", "....................",
      "....................", "...#............#...",
      "...#............#...", "...#............#...",
      "...#............#...", "...#............#...",
      "...######..######...", "....................",
      "....................", "....................",
    ] },
    { name: "Pillars", foods: 8, walls: [
      "....................", ".........#..........",
      "...#.....#......#...", "...#.....#......#...",
      "...#.....#......#...", "...#.....#......#...",
      "...#.....#......#...", "...#............#...",
      "....................", "....................",
      "....................", "....................",
      "...#............#...", "...#......#.....#...",
      "...#......#.....#...", "...#......#.....#...",
      "...#......#.....#...", "...#......#.....#...",
      "..........#.........", "....................",
    ] },
    { name: "Corners", foods: 9, walls: [
      "....................", "....................",
      "..#####..##..#####..", "..#......##......#..",
      "..#..............#..", "..#..............#..",
      "..#..............#..", "....................",
      "....................", "....................",
      "....................", "....................",
      "....................", "..#..............#..",
      "..#..............#..", "..#..............#..",
      "..#......##......#..", "..#####..##..#####..",
      "....................", "....................",
    ] },
    { name: "Lanes", foods: 10, walls: [
      "....................", "....................",
      "....................", "################....",
      "....................", "....................",
      "....................", "....################",
      "....................", "....................",
      "....................", "....................",
      "....................", "################....",
      "....................", "....................",
      "....................", "....################",
      "....................", "....................",
    ] },
    { name: "Rooms", foods: 11, walls: [
      "...............#....", "...............#....",
      "....................", "....................",
      "...............#....", "#######..########..#",
      "...............#....", "...............#....",
      "...............#....", "...............#....",
      "...............#....", "...............#....",
      "....................", "....................",
      "#######........#....", "...............#....",
      "....................", "...............#....",
      "...............#....", "...............#....",
    ] },
    { name: "Spiral", foods: 12, walls: [
      "....................", "....................",
      "..########.#######..", ".................#..",
      ".................#..", "..#..##########..#..",
      "..#...........#..#..", "..#...........#..#..",
      "..#..#........#..#..", "..#..#........#.....",
      ".....#...........#..", "..#...........#..#..",
      "..#..#........#..#..", "..#..#........#..#..",
      "..#..##########..#..", "..#..............#..",
      "..#..............#..", "..#######.########..",
      "....................", "....................",
    ] },
    { name: "Comb", foods: 13, walls: [
      "....................", ".##################.",
      "..#...#......#...#..", "..#...#......#...#..",
      "..#...#......#...#..", "..#...#......#...#..",
      "..#...#......#...#..", "..#...#......#...#..",
      "....................", "....................",
      "....................", "....................",
      "..#...#......#...#..", "..#...#......#...#..",
      "..#...#......#...#..", "..#...#......#...#..",
      "..#...#......#...#..", "..#...#......#...#..",
      ".##################.", "....................",
    ] },
    { name: "Labyrinth", foods: 14, walls: [
      "....................", "....................",
      "..#######..#######..", "..#.......#......#..",
      "..#.......#......#..", "..#.......#......#..",
      "..#...########...#..", "..#...#..........#..",
      "..#...#..........#..", "....................",
      "....................", "..#..........#...#..",
      "..#..........#...#..", "..#...########...#..",
      "..#......#.......#..", "..#......#.......#..",
      "..#......#.......#..", "..#######..#######..",
      "....................", "....................",
    ] },
  ];

  function rand(s) { // mulberry32, state kept in s.rng so a game can be copied or replayed
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function parseMode(mode) {
    if (mode === "maze") return { wrap: false, speed: "normal", maze: true };
    var parts = String(mode || "").split("-");
    return { wrap: parts[0] === "wrap", speed: SPEEDS[parts[1]] ? parts[1] : "normal", maze: false };
  }

  /** Mazes given by the server (SPEC §11): [{ name, foods, walls }]. Checked again here (size, characters, a free
      start); one that doesn't fit is skipped. Returns the built-in MAZES when nothing usable was given. */
  function usableMazes(list) {
    var out = [];
    if (Array.isArray(list)) {
      for (var i = 0; i < list.length; i++) {
        var M = list[i];
        var ok = M && typeof M === "object" && Array.isArray(M.walls) && M.walls.length === MAZE_SIZE &&
          typeof M.foods === "number" && M.foods >= 1 && M.foods <= 99 && Math.floor(M.foods) === M.foods;
        for (var r = 0; ok && r < MAZE_SIZE; r++) {
          if (typeof M.walls[r] !== "string" || !/^[.#]{20}$/.test(M.walls[r])) ok = false;
        }
        for (var k = -3; ok && k <= 3; k++) if (M.walls[MAZE_START.y][MAZE_START.x + k] !== ".") ok = false;
        if (ok) out.push({ name: typeof M.name === "string" ? M.name.slice(0, 40) : "Maze " + (out.length + 1), foods: M.foods, walls: M.walls.slice() });
      }
    }
    return out.length ? out : MAZES;
  }

  /** Put the snake at the start of maze number `n` (1-based), keeping the score. */
  function loadMaze(s, n) {
    var M = s.mazes[n - 1];
    s.level = n; s.mazeName = M.name; s.mazeGoal = M.foods; s.mazeFoods = 0;
    s.walls = new Uint8Array(s.cols * s.rows);
    for (var y = 0; y < s.rows; y++) for (var x = 0; x < s.cols; x++) if (M.walls[y][x] === "#") s.walls[y * s.cols + x] = 1;
    s.snake = [];
    for (var i = 0; i < 4; i++) s.snake.push({ x: MAZE_START.x - i, y: MAZE_START.y });
    s.dir = "right"; s.queue = []; s.grow = 0; s.pu = 0; s.progress = 0; s.bonus = null;
    s.cps = Math.min(s.maxCps, Math.round((s.baseCps + MAZE_CPS_STEP * (n - 1)) * 10) / 10);
    s.wallsVersion = (s.wallsVersion || 0) + 1;
    s.mazeShownUntil = s.updates + 2 * UPS;
    placeFood(s);
  }
  function isWall(s, x, y) { return !!(s.walls && s.walls[y * s.cols + x]); }

  function create(opts) {
    var o = opts || {};
    var m = parseMode(o.mode || "walls-normal"), sp = SPEEDS[m.speed];
    var cols = m.maze ? MAZE_SIZE : o.cols || 20, rows = m.maze ? MAZE_SIZE : o.rows || 20;
    var hx = (cols >> 1) - 1, hy = rows >> 1;
    var len = Math.max(2, Math.min(o.length || 4, hx + 1));
    var snake = [];
    for (var i = 0; i < len; i++) snake.push({ x: hx - i, y: hy });
    var s = {
      cols: cols, rows: rows, mode: m.maze ? "maze" : (m.wrap ? "wrap-" : "walls-") + m.speed, wrap: m.wrap, speed: m.speed,
      maze: m.maze, mazes: m.maze ? usableMazes(o.levels) : null, walls: null, mazeName: null, mazeGoal: 0, mazeFoods: 0,
      mazesCleared: 0, wallsVersion: 0, mazeShownUntil: 0,
      mult: sp.mult, baseCps: sp.cps, cps: sp.cps, maxCps: Math.min(MAX_CPS, sp.cps * 1.8),
      // progress toward the next cell, counted in whole 1/600ths of a cell per update so
      // speeds like 6.6 cells a second add up exactly (no floating-point drift)
      pu: 0, progress: 0, dir: "right", queue: [], snake: snake, grow: 0,
      food: null, bonus: null, foods: 0, bonusEaten: 0, score: 0, level: 1,
      over: false, won: false, cause: null, updates: 0, moves: 0, pending: [],
      rng: (Number(o.seed) >>> 0) || 1,
    };
    if (s.maze) loadMaze(s, startAt(o.startLevel, s.mazes.length)); else placeFood(s);
    return s;
  }

  function isSnake(s, x, y) {
    for (var i = 0; i < s.snake.length; i++) if (s.snake[i].x === x && s.snake[i].y === y) return true;
    return false;
  }
  function freeCells(s) {
    var taken = new Uint8Array(s.cols * s.rows), out = [];
    s.snake.forEach(function (c) { taken[c.y * s.cols + c.x] = 1; });
    if (s.walls) for (var w = 0; w < s.walls.length; w++) if (s.walls[w]) taken[w] = 1;
    if (s.food) taken[s.food.y * s.cols + s.food.x] = 1;
    if (s.bonus) taken[s.bonus.y * s.cols + s.bonus.x] = 1;
    for (var i = 0; i < taken.length; i++) if (!taken[i]) out.push(i);
    return out;
  }
  function placeFood(s) {
    s.food = null;
    var free = freeCells(s);
    if (!free.length) return null;
    var i = free[Math.floor(rand(s) * free.length)];
    s.food = { x: i % s.cols, y: Math.floor(i / s.cols) };
    return s.food;
  }
  function spawnBonus(s) {
    var free = freeCells(s);
    if (!free.length) return null;
    var i = free[Math.floor(rand(s) * free.length)];
    s.bonus = { x: i % s.cols, y: Math.floor(i / s.cols), ttl: BONUS_SECONDS * UPS, life: BONUS_SECONDS * UPS };
    return s.bonus;
  }

  /** Queue a turn. A turn into the opposite (or same) direction of the last queued one is ignored.
      A turn pressed when the snake is already past half-way into its next cell happens at once (the move
      comes a little early) instead of waiting for the next cell: turns feel immediate at any speed. Any
      events of that move come with the next step(). */
  function turn(s, dir) {
    if (!DIRS[dir] || s.over) return false;
    var lastDir = s.queue.length ? s.queue[s.queue.length - 1] : s.dir;
    if (dir === lastDir || dir === OPP[lastDir]) return false;
    if (s.queue.length >= MAX_QUEUE) return false;
    s.queue.push(dir);
    if (s.queue.length === 1 && s.pu >= EARLY_TURN * UPS * 10) {
      s.pu = 0; s.progress = 0;
      move(s, s.pending);
    }
    return true;
  }

  /** The cell the head moves into next (after wrapping), or null if that's a wall. */
  function nextCell(s, dir) {
    var d = DIRS[dir || s.dir], h = s.snake[0];
    var x = h.x + d[0], y = h.y + d[1];
    if (x < 0 || y < 0 || x >= s.cols || y >= s.rows) {
      if (!s.wrap) return null;
      x = (x + s.cols) % s.cols; y = (y + s.rows) % s.rows;
    }
    if (isWall(s, x, y)) return null;
    return { x: x, y: y };
  }

  function die(s, ev, cause) {
    s.over = true; s.cause = cause;
    ev.push({ type: "die", cause: cause });
  }

  function move(s, ev) {
    if (s.queue.length) s.dir = s.queue.shift();
    var n = nextCell(s, s.dir);
    if (!n) { die(s, ev, "wall"); return; }
    var eating = !!(s.food && s.food.x === n.x && s.food.y === n.y);
    var tailMoves = s.grow === 0 && !eating;
    for (var i = 0; i < s.snake.length; i++) {
      var c = s.snake[i];
      if (c.x === n.x && c.y === n.y) {
        if (i === s.snake.length - 1 && tailMoves) continue; // the tail moves out of the way
        die(s, ev, "self"); return;
      }
    }
    s.snake.unshift({ x: n.x, y: n.y });
    s.moves++;
    if (eating) {
      s.grow += 1; s.foods++;
      s.score += Math.round(FOOD_POINTS * s.mult);
      ev.push({ type: "eat", x: n.x, y: n.y });
      if (s.maze) s.mazeFoods++;
      else if (s.foods % SPEEDUP_EVERY === 0) {
        s.level++;
        s.cps = Math.min(s.maxCps, Math.round((s.cps + SPEEDUP_CPS) * 10) / 10);
        ev.push({ type: "speed", level: s.level, cps: s.cps });
      }
    }
    if (s.bonus && s.bonus.x === n.x && s.bonus.y === n.y) {
      s.score += BONUS_POINTS; s.bonusEaten++; s.bonus = null;
      ev.push({ type: "bonus", x: n.x, y: n.y });
    }
    if (s.grow > 0) s.grow--; else s.snake.pop();
    if (s.maze && s.mazeFoods >= s.mazeGoal) {       // maze cleared
      var bonus = MAZE_CLEAR_POINTS * s.level;
      s.score += bonus; s.mazesCleared++;
      if (s.level >= s.mazes.length) {
        s.over = true; s.won = true; s.food = null;
        ev.push({ type: "level", cleared: s.level, bonus: bonus, next: null });
        ev.push({ type: "win" });
        return;
      }
      ev.push({ type: "level", cleared: s.level, bonus: bonus, next: s.level + 1 });
      loadMaze(s, s.level + 1);
      return;
    }
    if (s.snake.length >= s.cols * s.rows) {
      s.over = true; s.won = true; s.food = null;
      ev.push({ type: "win" });
      return;
    }
    if (eating) {
      placeFood(s);
      if (!s.bonus && s.foods >= 3 && rand(s) < BONUS_CHANCE && spawnBonus(s)) ev.push({ type: "bonusSpawn", x: s.bonus.x, y: s.bonus.y });
    }
  }

  /** One update (1/60 s). Returns the events that happened. */
  function step(s) {
    var ev = s.pending.length ? s.pending.splice(0) : [];
    if (s.over) return ev;
    s.updates++;
    if (s.bonus && --s.bonus.ttl <= 0) { s.bonus = null; ev.push({ type: "bonusGone" }); }
    if (!s.food) placeFood(s);
    s.pu += Math.round(s.cps * 10);
    while (s.pu >= UPS * 10 && !s.over) { s.pu -= UPS * 10; move(s, ev); }
    s.progress = s.pu / (UPS * 10);
    return ev;
  }

  /** A saved game (SPEC §12): the rules state as plain data. The maze list isn't in it (the server keeps the
      level list with the save and gives it back as `levels`). */
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "mazes" && k !== "pending") out[k] = s[k];
    out.walls = s.walls ? Array.prototype.slice.call(s.walls) : null;
    return JSON.parse(JSON.stringify(out));
  }
  /** The state back from save() (and the level list). Throws if it isn't one. */
  function restore(data, levels) {
    var ok = data && typeof data === "object" && Array.isArray(data.snake) && data.snake.length > 0 &&
      MODES.indexOf(data.mode) >= 0 && data.cols > 0 && data.cols <= 40 && data.rows > 0 && data.rows <= 40 &&
      DIRS[data.dir] && Array.isArray(data.queue) && typeof data.score === "number";
    for (var i = 0; ok && i < data.snake.length; i++) {
      var c = data.snake[i];
      ok = c && c.x >= 0 && c.y >= 0 && c.x < data.cols && c.y < data.rows;
    }
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.pending = [];
    s.walls = Array.isArray(data.walls) && data.walls.length === s.cols * s.rows ? Uint8Array.from(data.walls) : null;
    s.mazes = s.maze ? usableMazes(levels) : null;
    if (s.maze && !s.walls) loadMaze(s, Math.min(s.level, s.mazes.length));
    s.wallsVersion = (s.wallsVersion || 0) + 1;
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { length: s.snake.length, foods: s.foods, bonus: s.bonusEaten, won: s.won, cause: s.cause, mode: s.mode, moves: s.moves,
        mazesCleared: s.mazesCleared },
    };
  }

  var SnakeLogic = {
    DIRS: DIRS, OPP: OPP, SPEEDS: SPEEDS, MODES: MODES, UPS: UPS,
    FOOD_POINTS: FOOD_POINTS, BONUS_POINTS: BONUS_POINTS, BONUS_SECONDS: BONUS_SECONDS,
    SPEEDUP_EVERY: SPEEDUP_EVERY, SPEEDUP_CPS: SPEEDUP_CPS, MAX_CPS: MAX_CPS, MAX_QUEUE: MAX_QUEUE, EARLY_TURN: EARLY_TURN,
    MAZES: MAZES, MAZE_SIZE: MAZE_SIZE, MAZE_CLEAR_POINTS: MAZE_CLEAR_POINTS, MAZE_START: MAZE_START,
    usableMazes: usableMazes, loadMaze: loadMaze, isWall: isWall, STATE_VERSION: STATE_VERSION, save: save, restore: restore,
    parseMode: parseMode, create: create, turn: turn, step: step, nextCell: nextCell,
    placeFood: placeFood, spawnBonus: spawnBonus, isSnake: isSnake, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = SnakeLogic; else self.SnakeLogic = SnakeLogic;
})();
