/* Household Arcade — Snake Duel rules (pure: no DOM, deterministic for a given seed).

   Two snakes on one 24 × 20 board eat food and try to make the other crash: into a wall or the board's edge,
   into itself or into the other snake. Heads meeting (head-on) crash both: a drawn round. First to win 3 rounds
   wins the match; then the next arena (cpu mode). One call to step() is one update (60 a second).

   Player 1 (index 0, green) starts on the right heading left; player 2 (index 1, blue, the computer in `cpu`)
   starts on the left heading right. The score is player 1's. */
(function () {
  "use strict";

  var COLS = 24, ROWS = 20, UPS = 60;
  var DIRS = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
  var OPP = { up: "down", down: "up", left: "right", right: "left" };
  var DIR_LIST = ["up", "right", "down", "left"];
  var MODES = ["cpu", "two"];
  var START_LEN = 4;
  // where each snake's head starts and which way it heads (the body runs behind it)
  var STARTS = [{ x: 18, y: 10, dir: "left" }, { x: 5, y: 9, dir: "right" }];
  // cells that must be free in every arena: the bodies plus 3 cells ahead of each head
  var START_FREE = { 9: [2, 8], 10: [15, 21] };
  var MIN_FREE = 0.65;
  var FOOD_POINTS = 10, ROUND_POINTS = 100, MATCH_POINTS = 500, MATCH_CAP = 10;   // match: 500 × min(arena, 10)
  var WIN_ROUNDS = 3, MAX_ROUNDS = 9;
  var READY_FIRST = 120, READY = 60, ROUND_END = 90, MATCH_END = 120;   // updates
  var ROUND_LIMIT = 60 * UPS;      // a round lasts at most a minute; then the longer snake wins it
  var MAX_QUEUE = 3, EARLY_TURN = 0.5, CELL = UPS * 10;    // progress in 1/600ths of a cell
  var SPEED_RANGE = [6, 12], FOODS_RANGE = [1, 4];

  // The arenas (SPEC §11.9): the built-in list, the same as app/level_kinds/snakeduel.py. An arena is
  // { name, walls: 20 rows of 24 "." / "#", speed: cells a second 6–12, foods: on the board at once 1–4 }.
  // All are the same turned half-way round, so both starts are equally good.
  var LEVELS = [
    { name: "Open field", speed: 6, foods: 3, walls: [
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
    ] },
    { name: "Four stones", speed: 7, foods: 3, walls: [
      "........................", "........................",
      "........................", "........................",
      ".....##..........##.....", ".....##..........##.....",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      ".....##..........##.....", ".....##..........##.....",
      "........................", "........................",
      "........................", "........................",
    ] },
    { name: "Centre post", speed: 7, foods: 3, walls: [
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "..........####..........", "..........####..........",
      "..........####..........", "..........####..........",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
    ] },
    { name: "Two bars", speed: 8, foods: 3, walls: [
      "........................", "........................",
      "........................", "........................",
      "....############........", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........############....",
      "........................", "........................",
      "........................", "........................",
    ] },
    { name: "Crossroads", speed: 8, foods: 2, walls: [
      "........................", "........................",
      "...####....##...........", "...#.......##...........",
      "...#.......##...........", "...#.......##...........",
      "...........##...........", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "...........##...........",
      "...........##.......#...", "...........##.......#...",
      "...........##.......#...", "...........##....####...",
      "........................", "........................",
    ] },
    { name: "Islands", speed: 9, foods: 3, walls: [
      "........................", "...............##.......",
      "...............##.......", "...###..................",
      "...###....##............", "..........##............",
      ".................###....", ".................###....",
      "........................", "........................",
      "........................", "........................",
      "....###.................", "....###.................",
      "............##..........", "............##....###...",
      "..................###...", ".......##...............",
      ".......##...............", "........................",
    ] },
    { name: "Lanes", speed: 9, foods: 2, walls: [
      "........................", "........................",
      "........................", "#########...########....",
      "........................", "........................",
      "....############........", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........############....",
      "........................", "........................",
      "....########...#########", "........................",
      "........................", "........................",
    ] },
    { name: "Little rooms", speed: 10, foods: 2, walls: [
      "........#......#........", "........#......#........",
      "........#......#........", "........#......#........",
      "........#......#........", "........###....#........",
      "...............#........", "........................",
      "........................", "........................",
      "........................", "........................",
      "........................", "........#...............",
      "........#....###........", "........#......#........",
      "........#......#........", "........#......#........",
      "........#......#........", "........#......#........",
    ] },
    { name: "Hourglass", speed: 10, foods: 2, walls: [
      "........................", "........................",
      "...##################...", "........................",
      "........................", "......####....####......",
      "...........##...........", "...........##...........",
      "........................", "........................",
      "........................", "........................",
      "...........##...........", "...........##...........",
      "......####....####......", "........................",
      "........................", "...##################...",
      "........................", "........................",
    ] },
    { name: "Gauntlet", speed: 11, foods: 1, walls: [
      "........................", "........................",
      "..########....########..", "..#..................#..",
      "..#..................#..", "..#...############...#..",
      "..#..................#..", "...........##...........",
      "...........##...........", "........................",
      "........................", "...........##...........",
      "...........##...........", "..#..................#..",
      "..#...############...#..", "..#..................#..",
      "..#..................#..", "..########....########..",
      "........................", "........................",
    ] },
  ];

  function rand(s) { // mulberry32, state kept in s.rng so a game can be copied or replayed
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** How many free cells of these walls can be reached from player 2's start (-1 if the start isn't free). */
  function reachable(walls) {
    var sx = STARTS[1].x, sy = STARTS[1].y;
    if (walls[sy][sx] !== ".") return -1;
    var seen = new Uint8Array(COLS * ROWS), stack = [sy * COLS + sx], n = 0;
    seen[sy * COLS + sx] = 1;
    while (stack.length) {
      var c = stack.pop(), x = c % COLS, y = (c - x) / COLS;
      n++;
      for (var d = 0; d < 4; d++) {
        var v = DIRS[DIR_LIST[d]], nx = x + v[0], ny = y + v[1];
        if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS || walls[ny][nx] !== ".") continue;
        if (!seen[ny * COLS + nx]) { seen[ny * COLS + nx] = 1; stack.push(ny * COLS + nx); }
      }
    }
    return n;
  }

  /** The arenas to play: the usable ones from `levels` (shape, ranges, free starts, ≥ 65 % free, every free cell
      reachable), else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (a) {
      if (!a || typeof a !== "object" || typeof a.name !== "string" || !Array.isArray(a.walls) || a.walls.length !== ROWS) return false;
      if (typeof a.speed !== "number" || a.speed !== Math.floor(a.speed) || a.speed < SPEED_RANGE[0] || a.speed > SPEED_RANGE[1]) return false;
      if (typeof a.foods !== "number" || a.foods !== Math.floor(a.foods) || a.foods < FOODS_RANGE[0] || a.foods > FOODS_RANGE[1]) return false;
      var free = 0, r, x;
      for (r = 0; r < ROWS; r++) {
        if (typeof a.walls[r] !== "string" || !/^[.#]{24}$/.test(a.walls[r])) return false;
        for (x = 0; x < COLS; x++) if (a.walls[r][x] === ".") free++;
      }
      for (r in START_FREE) for (x = START_FREE[r][0]; x <= START_FREE[r][1]; x++) if (a.walls[r][x] !== ".") return false;
      return free >= MIN_FREE * COLS * ROWS && reachable(a.walls) === free;
    });
    return out.length ? out : LEVELS;
  }

  function isWall(s, x, y) { return x < 0 || y < 0 || x >= COLS || y >= ROWS || !!s.walls[y * COLS + x]; }
  function foodAt(s, x, y) {
    for (var i = 0; i < s.food.length; i++) if (s.food[i].x === x && s.food[i].y === y) return i;
    return -1;
  }
  function bodyAt(s, x, y) {      // [snake, index in its body] or null
    for (var j = 0; j < 2; j++) {
      var b = s.snakes[j].body;
      for (var k = 0; k < b.length; k++) if (b[k].x === x && b[k].y === y) return [j, k];
    }
    return null;
  }

  function placeFood(s) {
    var taken = new Uint8Array(COLS * ROWS), free = [], i;
    for (i = 0; i < taken.length; i++) if (s.walls[i]) taken[i] = 1;
    s.snakes.forEach(function (sn) { sn.body.forEach(function (c) { taken[c.y * COLS + c.x] = 1; }); });
    s.food.forEach(function (f) { taken[f.y * COLS + f.x] = 1; });
    for (i = 0; i < taken.length; i++) if (!taken[i]) free.push(i);
    if (!free.length) return null;
    var c = free[Math.floor(rand(s) * free.length)];
    var f = { x: c % COLS, y: Math.floor(c / COLS) };
    s.food.push(f);
    return f;
  }

  function newSnake(i, cpu) {
    var st = STARTS[i], d = DIRS[st.dir], body = [];
    for (var k = 0; k < START_LEN; k++) body.push({ x: st.x - d[0] * k, y: st.y - d[1] * k });
    return { body: body, dir: st.dir, queue: [], grow: 0, pu: 0, progress: 0, crashed: false, cpu: !!cpu };
  }

  /** A new round on the current arena: snakes back at their starts, fresh food, a short "ready". */
  function startRound(s, ready) {
    s.round++;
    s.snakes = [newSnake(0, false), newSnake(1, s.mode === "cpu")];
    s.food = [];
    for (var i = 0; i < s.foodCount; i++) placeFood(s);
    s.phase = "ready"; s.timer = ready || READY; s.roundTime = 0;
    s.roundWinner = null; s.roundCause = null;
    if (s.snakes[1].cpu) cpuPlan(s, 1);
  }

  /** Arena number n (1-based) of the list: its walls, speed and food, and a new match. */
  function loadArena(s, n) {
    var A = s.arenas[n - 1];
    s.level = n; s.arenaName = A.name; s.cps = A.speed; s.foodCount = A.foods;
    s.walls = new Uint8Array(COLS * ROWS);
    for (var y = 0; y < ROWS; y++) for (var x = 0; x < COLS; x++) if (A.walls[y][x] === "#") s.walls[y * COLS + x] = 1;
    s.wallsVersion = (s.wallsVersion || 0) + 1;
    s.wins = [0, 0]; s.round = 0; s.matchWinner = null;
    startRound(s, READY_FIRST);
  }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES.indexOf(o.mode) >= 0 ? o.mode : "cpu", rng: (Number(o.seed) >>> 0) || 1,
      arenas: usableLevels(o.levels), level: 1, arenaName: "", cps: 6, foodCount: 1, walls: null, wallsVersion: 0,
      snakes: null, food: [], phase: "ready", timer: 0, round: 0, roundTime: 0, wins: [0, 0],
      roundWinner: null, roundCause: null, matchWinner: null,
      score: 0, over: false, won: false, updates: 0, pending: [],
      stats: { foods: 0, foodsOther: 0, rounds: 0, roundsLost: 0, draws: 0, matches: 0, matchesLost: 0, arenas: 0,
        moves: 0, winner: null, cause: null },
    };
    // Against the computer the arenas come in order; two players play one match on an arena picked by the seed.
    var first = s.mode === "two" ? 1 + Math.floor(rand(s) * s.arenas.length) : 1;
    loadArena(s, first);
    return s;
  }

  // ---------- the computer's snake ----------
  /** Free cells reachable from (x, y), counted up to `cap`. Bodies block, except tails that move away. */
  function space(s, x, y, cap, blocked) {
    var seen = new Uint8Array(COLS * ROWS), stack = [y * COLS + x], n = 0;
    seen[y * COLS + x] = 1;
    while (stack.length && n < cap) {
      var c = stack.pop(), cx = c % COLS, cy = (c - cx) / COLS;
      n++;
      for (var d = 0; d < 4; d++) {
        var v = DIRS[DIR_LIST[d]], nx = cx + v[0], ny = cy + v[1], k = ny * COLS + nx;
        if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS || seen[k] || blocked[k]) continue;
        seen[k] = 1; stack.push(k);
      }
    }
    return n;
  }
  /** Steps from (x, y) to the nearest food around the obstacles (99 if none can be reached). */
  function foodDistance(s, x, y, blocked) {
    if (!s.food.length) return 0;
    var dist = new Int16Array(COLS * ROWS).fill(-1), q = [y * COLS + x], h = 0;
    dist[y * COLS + x] = 0;
    while (h < q.length) {
      var c = q[h++], cx = c % COLS, cy = (c - cx) / COLS;
      if (foodAt(s, cx, cy) >= 0) return dist[c];
      for (var d = 0; d < 4; d++) {
        var v = DIRS[DIR_LIST[d]], nx = cx + v[0], ny = cy + v[1], k = ny * COLS + nx;
        if (nx < 0 || ny < 0 || nx >= COLS || ny >= ROWS || dist[k] >= 0 || blocked[k]) continue;
        dist[k] = dist[c] + 1; q.push(k);
      }
    }
    return 99;
  }
  /** How good the computer is on this arena: 1 (arena 1) to 10 (arena 10 and later). */
  function skill(s) { return Math.min(MATCH_CAP, s.level); }

  /** Choose the computer's next direction: never straight into something if it can help it, then the most room
      (it looks further ahead on later arenas), away from the other head, toward the nearest food. */
  function cpuDecide(s, i) {
    var sn = s.snakes[i], other = s.snakes[1 - i], head = sn.body[0], k = skill(s);
    var cap = 6 + 4 * k, blocked = new Uint8Array(COLS * ROWS), c, j;
    for (c = 0; c < blocked.length; c++) if (s.walls[c]) blocked[c] = 1;
    for (j = 0; j < 2; j++) s.snakes[j].body.forEach(function (b) { blocked[b.y * COLS + b.x] = 1; });
    var tail = sn.body[sn.body.length - 1];
    if (sn.grow === 0) blocked[tail.y * COLS + tail.x] = 0;
    var oh = other.body[0], best = null, bestV = -Infinity, safe = [];
    var lastDir = sn.queue.length ? sn.queue[sn.queue.length - 1] : sn.dir;
    var mistake = rand(s) < 0.12 - 0.01 * k;
    for (var n = 0; n < 4; n++) {
      var d = DIR_LIST[n];
      if (d === OPP[lastDir]) continue;
      var v = DIRS[d], x = head.x + v[0], y = head.y + v[1], val;
      if (x < 0 || y < 0 || x >= COLS || y >= ROWS || blocked[y * COLS + x]) val = -1e6;
      else {
        safe.push(d);
        blocked[y * COLS + x] = 1;
        var room = space(s, x, y, cap, blocked);
        blocked[y * COLS + x] = 0;
        val = room >= Math.min(cap, sn.body.length + 2) ? 0 : -400 + room * 8;
        if (Math.abs(x - oh.x) + Math.abs(y - oh.y) === 1) val -= k >= 3 ? 250 : 40;   // the other head could come here
        var fd = k >= 4 ? foodDistance(s, x, y, blocked) : Math.min.apply(null, s.food.map(function (f) { return Math.abs(f.x - x) + Math.abs(f.y - y); }).concat([99]));
        val -= fd * 3;
        if (d === lastDir) val += 1;                       // a slight wish to go straight
        val += rand(s) * Math.max(2, 26 - 2.5 * k);       // and some unpredictability
      }
      if (val > bestV) { bestV = val; best = d; }
    }
    if (mistake && safe.length) best = safe[Math.floor(rand(s) * safe.length)];
    return best || lastDir;
  }
  /** The computer decides its next move as soon as it has made one (so it's drawn sliding the right way). */
  function cpuPlan(s, i) {
    var sn = s.snakes[i];
    sn.queue = [];
    var d = cpuDecide(s, i);
    if (d !== sn.dir) sn.queue.push(d);
  }

  // ---------- moving ----------
  /** Move the snakes in `idx` one cell each, at the same moment: crashes, head-ons, food. */
  function moveSnakes(s, idx, ev) {
    var t = [], eat = [], tailFree = [], crash = [], i, j;
    var moving = [false, false];
    idx.forEach(function (n) { moving[n] = true; });
    for (i = 0; i < 2; i++) {
      if (!moving[i]) continue;
      var sn = s.snakes[i];
      if (sn.cpu && sn.queue.length && Math.floor(rand(s) * 10) < skill(s)) {   // a quick second look
        var q = DIRS[sn.queue[0]], qx = sn.body[0].x + q[0], qy = sn.body[0].y + q[1];
        if (isWall(s, qx, qy) || bodyAt(s, qx, qy)) cpuPlan(s, i);
      }
      if (sn.queue.length) sn.dir = sn.queue.shift();
      var d = DIRS[sn.dir], h = sn.body[0];
      t[i] = { x: h.x + d[0], y: h.y + d[1] };
      eat[i] = isWall(s, t[i].x, t[i].y) ? -1 : foodAt(s, t[i].x, t[i].y);
      tailFree[i] = sn.grow === 0 && eat[i] < 0;
      crash[i] = isWall(s, t[i].x, t[i].y) ? "wall" : null;
    }
    for (i = 0; i < 2; i++) {
      if (!moving[i] || crash[i]) continue;
      for (j = 0; j < 2 && !crash[i]; j++) {
        var b = s.snakes[j].body;
        for (var k = 0; k < b.length; k++) {
          if (b[k].x !== t[i].x || b[k].y !== t[i].y) continue;
          if (k === b.length - 1 && moving[j] && tailFree[j]) continue;      // that tail moves out of the way
          if (j !== i && k === 0) {
            var swap = moving[j] && t[j] && t[j].x === s.snakes[i].body[0].x && t[j].y === s.snakes[i].body[0].y;
            // into the other's head: head-on, unless that head moves on (then it's the other's neck)
            if (!moving[j] || swap) { crash[i] = "head"; crash[j] = "head"; }
            else crash[i] = "snake";
          } else crash[i] = j === i ? "self" : "snake";
          break;
        }
      }
    }
    if (moving[0] && moving[1] && !crash[0] && !crash[1] && t[0].x === t[1].x && t[0].y === t[1].y) { crash[0] = "head"; crash[1] = "head"; }
    var eaten = 0;
    for (i = 0; i < 2; i++) {
      if (!moving[i] || crash[i]) continue;
      var me = s.snakes[i];
      me.body.unshift(t[i]);
      if (i === 0) s.stats.moves++;
      var fi = foodAt(s, t[i].x, t[i].y);
      if (fi >= 0) {
        s.food.splice(fi, 1); eaten++;
        me.grow++;
        if (i === 0) { s.score += FOOD_POINTS; s.stats.foods++; } else s.stats.foodsOther++;
        ev.push({ type: "eat", player: i + 1, x: t[i].x, y: t[i].y });
      }
      if (me.grow > 0) me.grow--; else me.body.pop();
    }
    for (i = 0; i < eaten; i++) placeFood(s);
    if (crash[0] || crash[1]) {
      for (i = 0; i < 2; i++) if (crash[i]) { s.snakes[i].crashed = true; ev.push({ type: "crash", player: i + 1, cause: crash[i] }); }
      endRound(s, crash[0] && crash[1] ? 0 : crash[0] ? 2 : 1, crash[0] && crash[1] ? (crash[0] === "head" ? "head" : "both") : crash[0] || crash[1], ev);
      return;
    }
    for (i = 0; i < 2; i++) if (moving[i] && s.snakes[i].cpu) cpuPlan(s, i);
  }

  function endRound(s, winner, cause, ev) {
    s.phase = "end"; s.timer = ROUND_END; s.roundWinner = winner; s.roundCause = cause;
    if (winner) s.wins[winner - 1]++;
    if (winner === 1) { s.score += ROUND_POINTS; s.stats.rounds++; } else if (winner === 2) s.stats.roundsLost++; else s.stats.draws++;
    ev.push({ type: "round", winner: winner, cause: cause, wins: s.wins.slice(), round: s.round });
    if (s.wins[0] >= WIN_ROUNDS || s.wins[1] >= WIN_ROUNDS) endMatch(s, s.wins[0] >= WIN_ROUNDS ? 1 : 2, ev);
    else if (s.round >= MAX_ROUNDS) endMatch(s, s.wins[0] > s.wins[1] ? 1 : s.wins[1] > s.wins[0] ? 2 : 0, ev);
  }

  function endMatch(s, winner, ev) {
    s.matchWinner = winner;
    if (winner === 1) { s.score += MATCH_POINTS * Math.min(s.level, MATCH_CAP); s.stats.matches++; }
    else if (winner === 2) s.stats.matchesLost++;
    ev.push({ type: "match", winner: winner, arena: s.level });
    if (s.mode === "two") { finish(s, winner === 1, "match", ev); s.stats.winner = winner; return; }
    if (winner !== 1) { finish(s, false, winner === 2 ? "lost" : "draw", ev); return; }
    s.stats.arenas++;
    if (s.level >= s.arenas.length) {
      s.over = true; s.won = true; s.stats.cause = "won";
      ev.push({ type: "win", score: s.score });
      return;
    }
    s.phase = "match"; s.timer = MATCH_END;
  }

  function finish(s, won, cause, ev) {
    s.over = true; s.won = won; s.stats.cause = cause;
    ev.push({ type: "gameover", score: s.score, cause: cause });
  }

  /** One update (1/60 s). Returns the events that happened (and those queued since the last update). */
  function step(s) {
    var ev = s.pending.length ? s.pending.splice(0) : [];
    if (s.over) return ev;
    s.updates++;
    if (s.phase === "ready") {
      if (--s.timer <= 0) { s.phase = "play"; ev.push({ type: "go", round: s.round }); }
    } else if (s.phase === "end") {
      if (--s.timer <= 0) startRound(s);
    } else if (s.phase === "match") {
      if (--s.timer <= 0) {
        if (s.level >= s.arenas.length) {            // a continued game whose list got shorter: that was the last
          s.over = true; s.won = true; s.stats.cause = "won"; ev.push({ type: "win", score: s.score });
        } else { loadArena(s, s.level + 1); ev.push({ type: "level", level: s.level, name: s.arenaName }); }
      }
    } else if (s.phase === "play") {
      s.roundTime++;
      var add = Math.round(s.cps * 10), movers = [];
      for (var i = 0; i < 2; i++) {
        var sn = s.snakes[i];
        sn.pu += add;
        if (sn.pu >= CELL) { sn.pu -= CELL; movers.push(i); }
      }
      if (movers.length) moveSnakes(s, movers, ev);
      if (s.phase === "play" && s.roundTime >= ROUND_LIMIT) {
        var l0 = s.snakes[0].body.length, l1 = s.snakes[1].body.length;
        endRound(s, l0 > l1 ? 1 : l1 > l0 ? 2 : 0, "time", ev);
      }
    }
    for (var n = 0; n < 2; n++) s.snakes[n].progress = Math.max(0, s.snakes[n].pu) / CELL;
    return ev;
  }

  /** Queue a turn for player p (0 or 1). As in Snake: a turn into the same or opposite direction of the last
      queued one is ignored, at most 3 wait, and a turn pressed past half-way into the next cell is made at once.
      That early move is taken from the next one (the snake is never more than half a cell ahead of its
      speed), so turning quickly never makes a snake faster. Its events come with the next step(). */
  function turn(s, p, dir) {
    var sn = s.snakes && s.snakes[p];
    if (!sn || sn.cpu || !DIRS[dir] || s.over || (s.phase !== "ready" && s.phase !== "play")) return false;
    var lastDir = sn.queue.length ? sn.queue[sn.queue.length - 1] : sn.dir;
    if (dir === lastDir || dir === OPP[lastDir] || sn.queue.length >= MAX_QUEUE) return false;
    sn.queue.push(dir);
    if (s.phase === "play" && sn.queue.length === 1 && sn.pu >= EARLY_TURN * CELL) {
      sn.pu -= CELL; sn.progress = 0;
      moveSnakes(s, [p], s.pending);
    }
    return true;
  }

  var P1 = { up: "up", down: "down", left: "left", right: "right" };
  var P2 = { up2: "up", down2: "down", left2: "left", right2: "right" };
  /** Keys: up/down/left/right steer player 1; up2/down2/left2/right2 (W A S D) player 2 — or player 1 too when
      playing the computer. fire does nothing. */
  function press(s, action, down) {
    if (!down) return [];
    if (P1[action]) turn(s, 0, P1[action]);
    else if (P2[action]) turn(s, s.mode === "two" ? 1 : 0, P2[action]);
    return [];
  }

  /** A saved game (SPEC §12): the rules state as plain data, without the arena list (the server keeps it). */
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "arenas" && k !== "pending") out[k] = s[k];
    out.walls = Array.prototype.slice.call(s.walls);
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES.indexOf(data.mode) >= 0 && Array.isArray(data.snakes) &&
      data.snakes.length === 2 && Array.isArray(data.food) && typeof data.score === "number" && data.level >= 1 &&
      ["ready", "play", "end", "match"].indexOf(data.phase) >= 0 && Array.isArray(data.wins) && data.stats &&
      typeof data.rng === "number" && typeof data.cps === "number";
    for (var i = 0; ok && i < 2; i++) {
      var sn = data.snakes[i];
      ok = sn && Array.isArray(sn.body) && sn.body.length > 0 && DIRS[sn.dir] && Array.isArray(sn.queue) &&
        sn.body.every(function (c) { return c && c.x >= 0 && c.y >= 0 && c.x < COLS && c.y < ROWS; });
    }
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.pending = [];
    s.arenas = usableLevels(levels);
    if (Array.isArray(data.walls) && data.walls.length === COLS * ROWS) s.walls = Uint8Array.from(data.walls);
    else { var A = s.arenas[Math.min(s.level, s.arenas.length) - 1]; s.walls = new Uint8Array(COLS * ROWS);
      for (var y = 0; y < ROWS; y++) for (var x = 0; x < COLS; x++) if (A.walls[y][x] === "#") s.walls[y * COLS + x] = 1; }
    s.wallsVersion = (s.wallsVersion || 0) + 1;
    return s;
  }

  function result(s) {
    var t = s.stats;
    return { score: s.score, level: s.level, stats: { won: !!s.won, cause: t.cause, foods: t.foods, rounds: t.rounds,
      roundsLost: t.roundsLost, draws: t.draws, matches: t.matches, matchesLost: t.matchesLost, arenas: t.arenas,
      winner: t.winner, moves: t.moves, mode: s.mode } };
  }

  var SnakeDuelLogic = {
    COLS: COLS, ROWS: ROWS, UPS: UPS, DIRS: DIRS, OPP: OPP, MODES: MODES, STARTS: STARTS, START_LEN: START_LEN,
    FOOD_POINTS: FOOD_POINTS, ROUND_POINTS: ROUND_POINTS, MATCH_POINTS: MATCH_POINTS, MATCH_CAP: MATCH_CAP,
    WIN_ROUNDS: WIN_ROUNDS, MAX_ROUNDS: MAX_ROUNDS, READY: READY, READY_FIRST: READY_FIRST, ROUND_END: ROUND_END,
    MATCH_END: MATCH_END, ROUND_LIMIT: ROUND_LIMIT, MAX_QUEUE: MAX_QUEUE, EARLY_TURN: EARLY_TURN,
    LEVELS: LEVELS, usableLevels: usableLevels, STATE_VERSION: STATE_VERSION,
    create: create, step: step, turn: turn, press: press, save: save, restore: restore, result: result,
    isWall: isWall, foodAt: foodAt, bodyAt: bodyAt, placeFood: placeFood, loadArena: loadArena, startRound: startRound,
    cpuDecide: cpuDecide, skill: skill, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = SnakeDuelLogic; else self.SnakeDuelLogic = SnakeDuelLogic;
})();
