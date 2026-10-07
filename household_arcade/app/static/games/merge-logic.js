/* Household Arcade — Merge rules (pure: no DOM, deterministic for a given seed).

   Number tiles on a square grid. A move slides every tile as far as it goes; two equal tiles that
   meet become one of double the value (a tile merges at most once a move) and the new value is
   scored. After every move that changed the board a 2 (90 %) or a 4 (10 %) appears on an empty
   square. Moves are at least MOVE_GAP updates apart (the slide animation); one move pressed
   meanwhile is kept and made right after. Goals (mode "goals") play a level list: each goal is a board
   (3–5 squares a side, maybe with fixed stones that tiles can't slide past) and a tile to make; making it
   brings the next goal on a fresh board, and the last one wins. One call to step() is one update (60 a second). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var UPS = 60;
  var MODES = { classic: { n: 4 }, big: { n: 5 }, small: { n: 3 }, goals: { n: 0, list: true } };
  var MOVE_GAP = 6;          // updates a slide takes; the soonest the next move can be made
  var WIN_VALUE = 2048;
  var FOUR_CHANCE = 0.1;
  var DIRS = ["up", "down", "left", "right"];
  var GOAL_PAUSE = 60;       // updates between a goal made (after its slide) and the next board

  // Goals (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A goal is
  // { name, size (3–5), goal (32 … 4096), stones }: `stones` is `size` rows of `size` characters, "." free
  // and "#" a stone; 0–3 stones, at most size − 1 in a row or column, ≥ 7 free squares all joined side by
  // side, and goal ≤ 2^power (see goalPower).
  var LEVELS = [
    { name: "Warm up", size: 4, goal: 64, stones: ["....", "....", "....", "...."] },
    { name: "Little box", size: 3, goal: 64, stones: ["...", "...", "..."] },
    { name: "One stone", size: 4, goal: 128, stones: ["....", ".#..", "....", "...."] },
    { name: "Garden path", size: 5, goal: 256, stones: [".....", ".#...", ".....", "...#.", "....."] },
    { name: "Corner rocks", size: 4, goal: 256, stones: ["#...", "....", "....", "...#"] },
    { name: "Twin stones", size: 4, goal: 128, stones: ["#...", ".#..", "....", "...."] },
    { name: "Big meadow", size: 5, goal: 512, stones: [".....", ".....", "#...#", ".....", "....."] },
    { name: "Stepping stone", size: 5, goal: 1024, stones: [".....", ".....", "...#.", ".....", "....."] },
    { name: "Mountain", size: 4, goal: 1024, stones: ["....", "....", "....", "...."] },
    { name: "Summit", size: 5, goal: 2048, stones: [".....", ".....", "..#..", ".....", "....."] },
  ];
  var GOALS = [32, 64, 128, 256, 512, 1024, 2048, 4096];

  /** The biggest goal a board allows is 2^power: its free squares, less 2 × (size − 2), less 2 for each stone
      on an edge but not in a corner and 3 for each stone away from the edges (those split lines in the
      middle and make big tiles much harder; measured with a look-ahead player). */
  function goalPower(n, st) {
    var p = 0;
    for (var i = 0; i < n * n; i++) {
      if (!st[i]) { p++; continue; }
      var x = i % n, y = (i / n) | 0, edges = (x === 0 || x === n - 1 ? 1 : 0) + (y === 0 || y === n - 1 ? 1 : 0);
      p -= edges === 2 ? 0 : edges === 1 ? 2 : 3;
    }
    return p - 2 * (n - 2);
  }

  /** A goal's stones as 0/1 squares (row by row), or null when it breaks a rule. */
  function stonesOf(lv) {
    var n = lv && lv.size, rows = lv && lv.stones;
    if (!(n === 3 || n === 4 || n === 5) || !Array.isArray(rows) || rows.length !== n) return null;
    var out = [], count = 0, col = [0, 0, 0, 0, 0], x, y;
    for (y = 0; y < n; y++) {
      if (typeof rows[y] !== "string" || rows[y].length !== n || !/^[#.]+$/.test(rows[y])) return null;
      var inRow = 0;
      for (x = 0; x < n; x++) {
        var st = rows[y].charAt(x) === "#" ? 1 : 0;
        out.push(st); count += st; inRow += st; col[x] += st;
      }
      if (inRow > n - 1) return null;
    }
    for (x = 0; x < n; x++) if (col[x] > n - 1) return null;
    var free = n * n - count;
    if (count > 3 || free < 7 || GOALS.indexOf(lv.goal) < 0 || lv.goal > Math.pow(2, goalPower(n, out))) return null;
    var first = out.indexOf(0), seen = {}, stack = [first], reached = 0;   // free squares all joined
    seen[first] = true;
    while (stack.length) {
      var i = stack.pop(), ix = i % n, iy = (i / n) | 0;
      reached++;
      var nb = [[ix + 1, iy], [ix - 1, iy], [ix, iy + 1], [ix, iy - 1]];
      for (var k = 0; k < 4; k++) {
        var nx = nb[k][0], ny = nb[k][1], j = ny * n + nx;
        if (nx >= 0 && ny >= 0 && nx < n && ny < n && !out[j] && !seen[j]) { seen[j] = true; stack.push(j); }
      }
    }
    return reached === free ? out : null;
  }

  /** The goals to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (lv) { return lv && typeof lv.name === "string" && !!stonesOf(lv); });
    return out.length ? out : LEVELS;
  }
  function goalFor(s, n) { return s.goals ? s.goals[Math.min(n, s.goals.length) - 1] : null; }
  function isStone(s, i) { return !!(s.stones && s.stones[i]); }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic", n = MODES[mode].n;
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, n: n, grid: new Array(n * n).fill(0),
      score: 0, level: 1, best: 0, won: false, over: false, dead: false,
      cool: 0, queued: null, anim: null, updates: 0,
      goals: null, stones: null, goal: 0, goalName: "", pause: 0,
      stats: { moves: 0, merges: 0, goals: 0, cause: null },
    };
    if (MODES[mode].list) {
      s.goals = usableLevels(o.levels);
      s.level = startAt(o.startLevel, s.goals.length);
      freshBoard(s);
      return s;
    }
    spawn(s); spawn(s);
    s.best = Math.max.apply(null, s.grid);
    s.level = levelOf(s.best);
    s.anim = null;
    return s;
  }

  // Goals: the board for the current level, with two starting tiles.
  function freshBoard(s) {
    var lv = goalFor(s, s.level);
    s.n = lv.size; s.stones = stonesOf(lv); s.goal = lv.goal; s.goalName = lv.name;
    s.grid = new Array(s.n * s.n).fill(0);
    s.anim = null; s.queued = null; s.cool = 0; s.dead = false;
    spawn(s); spawn(s);
    s.best = Math.max.apply(null, s.grid);
    s.anim = null;
  }

  function levelOf(v) { var e = 0; while (v > 1) { v /= 2; e++; } return Math.max(1, e); }

  function spawn(s) {
    var empty = [];
    for (var i = 0; i < s.grid.length; i++) if (!s.grid[i] && !isStone(s, i)) empty.push(i);
    if (!empty.length) return -1;
    var at = empty[Math.floor(rand(s) * empty.length)];
    s.grid[at] = rand(s) < FOUR_CHANCE ? 4 : 2;
    return at;
  }

  // The squares of line k, starting from the edge the tiles slide toward. A stone splits a line: the
  // tiles beyond it slide up to the stone and no further.
  function line(n, dir, k) {
    var out = [];
    for (var j = 0; j < n; j++) {
      if (dir === "left") out.push(k * n + j);
      else if (dir === "right") out.push(k * n + (n - 1 - j));
      else if (dir === "up") out.push(j * n + k);
      else out.push((n - 1 - j) * n + k);
    }
    return out;
  }

  /** Where a move in dir takes the tiles (doesn't change s): { grid, slides, merged, points, changed }. */
  function plan(s, dir) {
    var n = s.n, grid = new Array(n * n).fill(0), slides = [], merged = [], points = 0, changed = false;
    for (var k = 0; k < n; k++) {
      var ln = line(n, dir, k), pos = 0, last = 0;
      for (var j = 0; j < n; j++) {
        var from = ln[j], v = s.grid[from];
        if (isStone(s, from)) { pos = j + 1; last = 0; continue; }
        if (!v) continue;
        if (last === v) {
          var to = ln[pos - 1];
          grid[to] = v * 2; points += v * 2; merged.push(to); last = 0;
          slides.push({ v: v, from: from, to: to });
          changed = true;
        } else {
          grid[ln[pos]] = v; last = v;
          slides.push({ v: v, from: from, to: ln[pos] });
          if (ln[pos] !== from) changed = true;
          pos++;
        }
      }
    }
    return { grid: grid, slides: slides, merged: merged, points: points, changed: changed };
  }

  function canMove(s, dir) { return plan(s, dir).changed; }
  function anyMove(s) { for (var i = 0; i < DIRS.length; i++) if (canMove(s, DIRS[i])) return true; return false; }

  function move(s, dir) {
    var evs = [], p = plan(s, dir);
    if (!p.changed) return evs;
    s.grid = p.grid; s.score += p.points; s.stats.moves++; s.stats.merges += p.merged.length;
    var at = spawn(s);
    s.cool = MOVE_GAP;
    s.anim = { slides: p.slides, merged: p.merged, spawn: at, end: s.updates + MOVE_GAP };
    evs.push({ type: "move", dir: dir });
    if (p.merged.length) evs.push({ type: "merge", count: p.merged.length, points: p.points });
    var best = Math.max.apply(null, s.grid);
    if (s.goals) {
      if (best > s.best) s.best = best;
      if (best >= s.goal) {                // made it: the next goal (or the win) once the slide is done
        s.stats.goals++; s.queued = null;
        s.pause = MOVE_GAP + GOAL_PAUSE;
        evs.push({ type: "goal", tile: best, goal: s.level });
        return evs;
      }
      if (!anyMove(s)) s.dead = true;
      return evs;
    }
    if (best > s.best) {
      s.best = best;
      var lv = levelOf(best);
      if (lv > s.level) { s.level = lv; evs.push({ type: "level", level: lv, tile: best }); }
      if (best >= WIN_VALUE && !s.won) { s.won = true; evs.push({ type: "win", tile: best }); }
    }
    if (!anyMove(s)) s.dead = true;    // the game ends once this slide has finished
    return evs;
  }

  function press(s, action, down) {
    if (!down || s.over || s.pause > 0 || DIRS.indexOf(action) < 0) return [];
    if (s.cool > 0 || s.dead) { if (!s.dead) s.queued = action; return []; }
    return move(s, action);
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    if (s.cool > 0) --s.cool;
    if (s.pause > 0) {
      if (--s.pause > 0) return evs;
      if (s.level >= s.goals.length) {     // the last goal: the game is won
        s.won = true; s.over = true; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score, tile: s.best });
        return evs;
      }
      s.level++;
      freshBoard(s);
      evs.push({ type: "level", level: s.level, tile: s.goal });
      return evs;
    }
    if (s.cool === 0 && (s.dead || s.queued)) {
      if (s.dead) {
        s.over = true; s.stats.cause = "stuck";
        evs.push({ type: "gameover", score: s.score, tile: s.best });
      } else if (s.queued) {
        var d = s.queued;
        s.queued = null;
        evs = move(s, d);
      }
    }
    return evs;
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "goals") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var m = data && typeof data === "object" && MODES[data.mode];
    var n = m && (m.list ? data.n : m.n);
    var sized = m && (m.list ? (n === 3 || n === 4 || n === 5) && Array.isArray(data.stones) && data.stones.length === n * n &&
      typeof data.goal === "number" && data.goal >= 2 && Array.isArray(data.grid) &&
      data.stones.every(function (st, i) { return !st || !data.grid[i]; }) : data.n === m.n);
    var ok = sized && Array.isArray(data.grid) && data.grid.length === n * n &&
      data.grid.every(function (v) { return v === 0 || (typeof v === "number" && v >= 2 && (v & (v - 1)) === 0); }) &&
      typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    if (s.dead && !s.over && !(s.cool > 0)) s.cool = 1;    // a board with no moves left still ends
    if (!(s.cool >= 0)) s.cool = 0;
    if (DIRS.indexOf(s.queued) < 0) s.queued = null;
    s.goals = m.list ? usableLevels(levels) : null;
    if (!m.list) { s.stones = null; s.pause = 0; }
    if (!(s.pause >= 0)) s.pause = 0;
    if (!s.stats.goals) s.stats.goals = 0;
    if (s.stats.cause === undefined) s.stats.cause = null;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { moves: s.stats.moves, merges: s.stats.merges, bestTile: s.best, won: !!s.won,
      goals: s.stats.goals || 0, cause: s.stats.cause || null, mode: s.mode } };
  }

  var MergeLogic = {
    UPS: UPS, MODES: MODES, MOVE_GAP: MOVE_GAP, WIN_VALUE: WIN_VALUE, FOUR_CHANCE: FOUR_CHANCE, DIRS: DIRS,
    STATE_VERSION: STATE_VERSION, GOAL_PAUSE: GOAL_PAUSE, LEVELS: LEVELS, GOALS: GOALS, usableLevels: usableLevels,
    stonesOf: stonesOf, goalPower: goalPower, goalFor: goalFor, isStone: isStone,
    create: create, step: step, press: press, move: move, plan: plan, canMove: canMove, anyMove: anyMove,
    levelOf: levelOf, spawn: spawn, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = MergeLogic; else self.MergeLogic = MergeLogic;
})();
