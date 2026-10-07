/* Household Arcade — Road Hop rules (pure: no DOM, deterministic for a given seed).

   Guide a little hopper from the bank at the bottom, across lanes of road traffic and a river of floating
   logs and shell rafts, into one of the 5 homes at the top. One hop per press, on a 13-column grid of 18 px
   cells. Traffic squashes, the water swallows, a log or raft carries you (off the edge is the end of that
   hopper too), and each crossing has a time limit. Filling all 5 homes is the next level. 3 lives.
   One call to step() is one update (60 a second).

   Levels (SPEC §11.9): every mode plays a list of levels { name, lanes, time }. Classic and Easy play the
   built-in list round and round (a little quicker each round, up to level 100, which wins); Levels plays
   the session's list (opts.levels) once, and its last level wins. A lane is
   { kind: "road" | "river" | "grass", pattern: 13–26 characters, speed: px an update, dir: "left" | "right" }:
   the pattern goes round and round; "." is empty, on a road "c" is a car and "t" a truck, on a river "l"
   is a log and "s" a shell raft (a run of the same letter is one of them). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var W = 240, H = 300;
  var COLS = 13, CS = 18, OX = 3, BOTTOM = 294;   // the board: 13 × 18 px columns from x = 3; rows up from y = 294
  var HOP_T = 8;                                  // updates a hop takes (no other hop meanwhile: ≤ 7.5 hops a second)
  var HOMES = [2, 4, 6, 8, 10], HOME_REACH = 8;   // home columns; how near a home's middle a hop must be
  var LIVES = 3, MAX_LEVELS = 100;
  var DEATH_PAUSE = 60, HOME_PAUSE = 24, LEVEL_PAUSE = 90;
  var ROW_POINTS = 10, HOME_POINTS = 50, LEVEL_POINTS = 250;
  var START_X = OX + 6 * CS + CS / 2;
  var MODES = {
    classic: { speed: 1, time: 1 },
    easy: { speed: 0.7, time: 1.25 },
    levels: { speed: 1, time: 1 },        // the session's level list, once
  };
  var LANE_KINDS = { road: "ct", river: "ls", grass: "" };
  var RUNS = { c: [1, 2], t: [2, 4], l: [2, 6], s: [2, 4] };   // how long one car / truck / log / raft may be (cells)
  var SPEEDS = { road: [0.3, 2], river: [0.3, 1.2], grass: [0.3, 2] };

  function L(kind, pattern, speed, dir) { return { kind: kind, pattern: pattern, speed: speed, dir: dir }; }
  var G = L("grass", ".............", 0.5, "left");

  // The built-in levels (bottom lane first); the session's list (opts.levels) replaces them in Levels mode.
  var LEVELS = [
    { name: "Quiet lane", time: 60, lanes: [
      L("road", "...c.....c.....", 0.5, "left"), L("road", "..tt.........", 0.4, "right"), G,
      L("river", "llll...llll...", 0.4, "right"), L("river", "ss..ss..ss..ss..", 0.5, "left"), L("river", "lllll...lllll...", 0.4, "right")] },
    { name: "Market day", time: 60, lanes: [
      L("road", "..c...c.....c...", 0.6, "left"), L("road", ".tt......tt.....", 0.5, "right"), L("road", "c....c....c...", 0.8, "left"), G,
      L("river", "lll...lll....", 0.5, "right"), L("river", "ss...ss...ss..", 0.6, "left"), L("river", "llll...llll..", 0.5, "right")] },
    { name: "Rush hour", time: 55, lanes: [
      L("road", "c..c....c..c....", 0.7, "left"), L("road", "ttt.....ttt.....", 0.5, "right"), L("road", "..c...c...c..", 0.9, "left"),
      L("road", "cc......cc.....", 1, "right"), G,
      L("river", "llll....llll....", 0.6, "right"), L("river", "sss...sss....", 0.7, "left"), L("river", "lll..lll...ll..", 0.8, "right"),
      L("river", "ss...ss...ss...", 0.5, "left")] },
    { name: "Duck pond", time: 55, lanes: [
      L("road", "...c....c....", 0.7, "right"), L("road", ".tt.....tt....", 0.6, "left"), G,
      L("river", "ss..ss...ss..", 0.6, "left"), L("river", "llll...lll....", 0.9, "right"), L("river", "sss....sss...", 0.5, "left"),
      L("river", "lll...llll...", 1, "right"), L("river", "ss...ss...ss..", 0.7, "left"), L("river", "lllll....lll...", 0.6, "right")] },
    { name: "Motorway", time: 50, lanes: [
      L("road", "c...c...c....", 1.1, "left"), L("road", "tttt.......tt.....", 0.8, "right"), L("road", "..cc.....cc...", 1.3, "left"),
      L("road", "c.....c...c...", 1.5, "right"), L("road", "ttt......ttt....", 0.9, "left"), G,
      L("river", "llll...llll...", 0.7, "right"), L("river", "ss...ss...ss..", 0.8, "left"), L("river", "lll....lll...", 1, "right"),
      L("river", "sss...sss....", 0.6, "left")] },
    { name: "Swift river", time: 50, lanes: [
      L("road", "..c....c...c..", 1, "right"), L("road", "tt.....tt.....", 0.8, "left"), L("road", "c...c....c...", 1.2, "right"), G,
      L("river", "lll...lll....", 1, "left"), L("river", "ss...ss..ss...", 1.1, "right"), L("river", "llll....lll...", 0.7, "left"),
      L("river", "sss...ss....s", 1.2, "right"), L("river", "ll...lll...ll...", 0.8, "left"), L("river", "ss...sss....s", 1.1, "right")] },
    { name: "Night drive", time: 45, lanes: [
      L("road", "c..c...c..c...", 1.2, "left"), L("road", "tttt....ttt......", 1, "right"), L("road", "cc...c....cc...", 1.4, "left"),
      L("road", "..c..c...c..c..", 1.6, "right"), L("road", "ttt.....tt......", 1.1, "left"), G,
      L("river", "lll...lll....", 0.9, "right"), L("river", "ss...ss...ss..", 1.1, "left"), L("river", "llll....llll....", 0.7, "right"),
      L("river", "sss....ss....", 1.2, "left"), L("river", "lll...ll....l", 1, "right")] },
    { name: "Long way home", time: 45, lanes: [
      L("road", "c...cc...c....", 1.3, "right"), L("road", "ttt...tt.......", 1.1, "left"), L("road", ".c..c..c...c..", 1.7, "right"),
      L("road", "tt....tttt.......", 1.2, "left"), L("road", "c..c...cc....", 1.8, "right"), G,
      L("river", "ll...ll...ll..", 1.1, "left"), L("river", "sss...sss....", 0.8, "right"), L("river", "lll....ll....", 1.2, "left"),
      L("river", "ss...ss...ss...", 0.9, "right"), L("river", "llll....lll....", 1.2, "left")] },
  ];

  // ---- checking a level (the same rules as app/level_kinds/hop.py) ----
  /** Runs of the same letter, going round: [{ ch, start, len }]. */
  function runs(pattern) {
    var n = pattern.length, out = [], i, first = -1;
    for (i = 0; i < n; i++) if (pattern[i] !== pattern[(i + n - 1) % n]) { first = i; break; }
    if (first < 0) return pattern[0] === "." ? [] : [{ ch: pattern[0], start: 0, len: n }];
    for (var k = 0; k < n;) {
      var at = (first + k) % n, ch = pattern[at], len = 0;
      while (len < n && pattern[(at + len) % n] === ch) len++;
      if (ch !== ".") out.push({ ch: ch, start: at, len: len });
      k += len;
    }
    return out;
  }
  function longestGap(pattern) {
    var best = 0, n = pattern.length;
    if (pattern.indexOf(".") < 0) return 0;
    if (!/[^.]/.test(pattern)) return n;
    for (var i = 0; i < n; i++) {
      if (pattern[i] !== "." || pattern[(i + n - 1) % n] === ".") continue;
      var len = 0;
      while (pattern[(i + len) % n] === ".") len++;
      if (len > best) best = len;
    }
    return best;
  }
  /** Why this lane can't be played, or "" when it can. */
  function laneProblem(ln) {
    if (!ln || typeof ln !== "object") return "a lane must be an object";
    var allowed = LANE_KINDS[ln.kind];
    if (allowed == null || !Object.prototype.hasOwnProperty.call(LANE_KINDS, ln.kind)) return "kind";
    if (typeof ln.pattern !== "string" || ln.pattern.length < 13 || ln.pattern.length > 26) return "pattern length";
    if (!new RegExp("^[." + allowed + "]+$").test(ln.pattern)) return "pattern letters";
    if (ln.dir !== "left" && ln.dir !== "right") return "dir";
    var sp = SPEEDS[ln.kind];
    if (typeof ln.speed !== "number" || !(ln.speed >= sp[0] && ln.speed <= sp[1])) return "speed";
    var rs = runs(ln.pattern), filled = 0;
    for (var i = 0; i < rs.length; i++) {
      var lim = RUNS[rs[i].ch];
      if (rs[i].len < lim[0] || rs[i].len > lim[1]) return "run length";
      filled += rs[i].len;
    }
    if (ln.kind === "road" && (!rs.length || filled * 2 > ln.pattern.length || longestGap(ln.pattern) < 3)) return "road traffic";
    if (ln.kind === "river" && (!rs.length || longestGap(ln.pattern) > 4)) return "river floaters";
    return "";
  }
  function levelProblem(lv) {
    if (!lv || typeof lv !== "object" || typeof lv.name !== "string" || !lv.name) return "name";
    if (typeof lv.time !== "number" || lv.time !== Math.floor(lv.time) || lv.time < 20 || lv.time > 90) return "time";
    if (!Array.isArray(lv.lanes) || lv.lanes.length < 6 || lv.lanes.length > 11) return "lanes";
    var busy = 0;
    for (var i = 0; i < lv.lanes.length; i++) {
      var p = laneProblem(lv.lanes[i]);
      if (p) return p;
      if (lv.lanes[i].kind !== "grass") busy++;
      var a = lv.lanes[i - 1], b = lv.lanes[i];
      if (a && a.kind === "river" && b.kind === "river" && a.dir === b.dir && Math.abs(a.speed - b.speed) < 0.3 - 1e-9) return "rivers in step";
    }
    return busy >= 2 ? "" : "too few busy lanes";
  }
  /** The levels to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (lv) { return !levelProblem(lv); });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  /** Level n's data and the factors the mode and round give it. */
  function levelDef(s, n) {
    var list = s.list || LEVELS, base, round = 0;
    if (s.list) base = list[Math.min(n, list.length) - 1];
    else { base = list[(n - 1) % list.length]; round = Math.floor((n - 1) / list.length); }
    var m = MODES[s.mode];
    return { def: base, speed: Math.min(1.5, 1 + 0.1 * round) * m.speed, time: Math.min(120, Math.round(base.time * m.time)) };
  }
  function levelCount(s) { return s.list ? s.list.length : MAX_LEVELS; }

  function create(o) {
    o = o || {};
    var s = {
      mode: MODES[o.mode] ? o.mode : "classic", rng: (o.seed >>> 0) || 1, updates: 0,
      score: 0, level: 1, lives: LIVES, over: false, won: false, list: null,
      lanes: [], n: 0, time: 0, timer: 0, boardVer: 0,
      x: START_X, row: 0, air: 0, fromX: START_X, fromRow: 0, facing: "up", queued: null, best: 0,
      homes: [false, false, false, false, false],
      phase: "play", pause: 0, levelT: 0, cause: null,
      stats: { hops: 0, homes: 0, levels: 0, deaths: 0, cause: null },
    };
    if (s.mode === "levels") { s.list = usableLevels(o.levels); s.level = startAt(o.startLevel, s.list.length); }
    buildLevel(s);
    return s;
  }

  function buildLevel(s) {
    var d = levelDef(s, s.level);
    s.lanes = d.def.lanes.map(function (ln) {
      var len = ln.pattern.length * CS;
      return { kind: ln.kind, pattern: ln.pattern, v: (ln.dir === "right" ? 1 : -1) * ln.speed * d.speed,
        off: ln.kind === "grass" ? 0 : Math.floor(rand(s) * ln.pattern.length) * CS, len: len };
    });
    s.n = s.lanes.length;
    s.time = d.time * 60;
    s.homes = [false, false, false, false, false];
    s.boardVer++;
    s.levelT = 0;
    respawn(s);
  }

  function respawn(s) {
    s.x = s.fromX = START_X; s.row = s.fromRow = 0; s.air = 0; s.queued = null; s.facing = "up";
    s.best = 0; s.timer = s.time; s.phase = "play"; s.cause = null;
  }

  /** The row's top y on the screen (row 0 the starting bank, rows 1 … n the lanes, n + 1 the homes). */
  function rowY(s, r) { return BOTTOM - (r + 1) * CS; }
  function lane(s, r) { return r >= 1 && r <= s.n ? s.lanes[r - 1] : null; }

  /** What is at screen x in lane ln ("." for nothing). */
  function at(ln, x) {
    var p = ((x - OX - ln.off) % ln.len + ln.len) % ln.len;
    return ln.pattern[Math.floor(p / CS)] || ".";
  }
  function squashed(ln, x) { return at(ln, x - 5) !== "." || at(ln, x) !== "." || at(ln, x + 5) !== "."; }
  function colX(x) { return OX + CS / 2 + clamp(Math.round((x - OX - CS / 2) / CS), 0, COLS - 1) * CS; }
  function homeAt(x) {
    for (var i = 0; i < HOMES.length; i++) if (Math.abs(x - (OX + HOMES[i] * CS + CS / 2)) <= HOME_REACH) return i;
    return -1;
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    s.levelT++;
    for (var i = 0; i < s.lanes.length; i++) {
      var ln = s.lanes[i];
      if (ln.kind !== "grass") ln.off = ((ln.off + ln.v) % ln.len + ln.len) % ln.len;
    }
    if (s.phase !== "play") {
      if (--s.pause > 0) return evs;
      if (s.phase === "clear") buildLevel(s);
      else respawn(s);
      return evs;
    }
    if (--s.timer <= 0) { die(s, evs, "time"); return evs; }
    if (s.air > 0) {
      if (--s.air === 0) {
        land(s, evs);
        if (s.phase === "play" && !s.over && s.queued) { var q = s.queued; s.queued = null; hop(s, q, evs); }
      }
      return evs;
    }
    check(s, evs, true);
    return evs;
  }

  /** On the ground: carried by a log or raft, squashed by traffic, or in the water. */
  function check(s, evs, carry) {
    var ln = lane(s, s.row);
    if (!ln) return;
    if (ln.kind === "river") {
      if (carry) s.x += ln.v;
      if (at(ln, s.x) === ".") { die(s, evs, "water"); return; }
      if (s.x < OX + 3 || s.x > OX + COLS * CS - 3) { die(s, evs, "swept"); return; }
    } else if (ln.kind === "road" && squashed(ln, s.x)) die(s, evs, "car");
  }

  function land(s, evs) {
    if (s.row > s.best) { s.best = s.row; s.score += ROW_POINTS; }
    if (s.row === s.n + 1) { reachHome(s, evs); return; }
    evs.push({ type: "land", row: s.row });
    check(s, evs, false);
  }

  function reachHome(s, evs) {
    var h = homeAt(s.x);
    s.homes[h] = true; s.stats.homes++;
    s.x = OX + HOMES[h] * CS + CS / 2;
    s.score += HOME_POINTS + Math.floor(s.timer / 60);
    evs.push({ type: "home", home: h, score: s.score });
    if (s.homes.every(Boolean)) {
      s.score += LEVEL_POINTS; s.stats.levels++;
      if (s.level >= levelCount(s)) {
        s.won = true; s.over = true; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
        return;
      }
      s.level++;
      evs.push({ type: "level", level: s.level });
      s.phase = "clear"; s.pause = LEVEL_PAUSE;
      return;
    }
    s.phase = "home"; s.pause = HOME_PAUSE;
  }

  function die(s, evs, cause) {
    s.lives--; s.stats.deaths++; s.cause = cause; s.air = 0; s.queued = null;
    evs.push({ type: "splat", cause: cause, lives: s.lives });
    if (s.lives <= 0) {
      s.over = true; s.stats.cause = cause;
      evs.push({ type: "gameover", score: s.score, cause: cause });
      return;
    }
    s.phase = "dead"; s.pause = DEATH_PAUSE;
  }

  /** One hop (up, down, left, right). Returns false when it's blocked. */
  function hop(s, dir, evs) {
    var row = s.row, x = s.x, ln;
    s.facing = dir;
    if (dir === "up") {
      if (row === s.n) {                          // into a home: only an empty one, near its middle
        var h = homeAt(x);
        if (h < 0 || s.homes[h]) { evs.push({ type: "bump" }); return false; }
      }
      row++;
    } else if (dir === "down") {
      if (row === 0) return false;
      row--;
    } else {
      x += dir === "left" ? -CS : CS;
      if (x < OX + CS / 2 - 0.01 || x > OX + COLS * CS - CS / 2 + 0.01) {
        ln = lane(s, row);
        if (!ln || ln.kind !== "river") return false;        // the edge of the board
        x = clamp(x, OX + CS / 2, OX + COLS * CS - CS / 2);
        if (x === s.x) return false;
      }
    }
    ln = lane(s, row);
    if (!ln || ln.kind !== "river") x = colX(x);   // on firm ground, back on the grid
    s.fromX = s.x; s.fromRow = s.row;
    s.x = x; s.row = row; s.air = HOP_T; s.stats.hops++;
    evs.push({ type: "hop", dir: dir });
    return true;
  }

  /** A press of up / down / left / right (fire = up). During a hop the next one waits. */
  function press(s, action, down) {
    if (!down || s.over) return [];
    if (action === "fire") action = "up";
    if (action !== "up" && action !== "down" && action !== "left" && action !== "right") return [];
    if (s.phase !== "play") return [];
    if (s.air > 0) { s.queued = action; return []; }
    var evs = [];
    hop(s, action, evs);
    return evs;
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "list") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.lanes) && data.lanes.length >= 1 &&
      Array.isArray(data.homes) && data.homes.length === 5 && typeof data.score === "number" && data.level >= 1 &&
      typeof data.rng === "number" && typeof data.x === "number" && typeof data.row === "number" && data.lives >= 1 && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.list = s.mode === "levels" ? usableLevels(levels) : null;
    if (s.list && s.level > s.list.length) s.level = s.list.length;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { hops: s.stats.hops, homes: s.stats.homes, levels: s.stats.levels,
      deaths: s.stats.deaths, livesLeft: s.lives, won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var HopLogic = {
    W: W, H: H, COLS: COLS, CS: CS, OX: OX, BOTTOM: BOTTOM, HOP_T: HOP_T, HOMES: HOMES, LIVES: LIVES, MAX_LEVELS: MAX_LEVELS,
    ROW_POINTS: ROW_POINTS, HOME_POINTS: HOME_POINTS, LEVEL_POINTS: LEVEL_POINTS, START_X: START_X, MODES: MODES,
    DEATH_PAUSE: DEATH_PAUSE, HOME_PAUSE: HOME_PAUSE, LEVEL_PAUSE: LEVEL_PAUSE,
    STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels, levelProblem: levelProblem, runs: runs,
    create: create, step: step, press: press, levelDef: levelDef, levelCount: levelCount, rowY: rowY, lane: lane, at: at,
    squashed: squashed, homeAt: homeAt, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = HopLogic; else self.HopLogic = HopLogic;
})();
