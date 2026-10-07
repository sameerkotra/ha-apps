/* Household Arcade — Bubble Pop rules (pure: no DOM, deterministic for a given seed).

   Coloured bubbles hang from the ceiling in rows of eight (every other row set half a bubble to the right).
   Aim the launcher at the bottom and shoot the next bubble: it flies straight, bounces off the side walls and
   sticks where it touches the ceiling or another bubble. Three or more of a colour touching each other pop
   (10 each), and every bubble no longer hanging from the ceiling drops (20 each). The next bubbles are always
   colours still on the board (from the seed). Every few shots the ceiling comes down a row (Endless: a new
   row pushes in from the top); a bubble below the line ends the game. Clearing the board brings the next one
   (500). Classic makes its boards from the seed (Relaxed too: gentler, at most four colours and the ceiling
   comes down only every 14 shots); Puzzles plays the puzzle list; Endless never clears.
   One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var W = 240, COLS = 8, D = 24, R = 12, RH = 21, X0 = 18, X1 = 222, TOP = 42, LOSE_Y = 244, MAX_ROWS = 12;
  var LX = 120, LY = 268, SPEED = 7, SUB = 2, TURN = 1.6, MAX_AIM = 78, STICK = 20, RELOAD = 10, CLEAR_T = 90, MAX_LEVEL = 100;
  var POP_POINTS = 10, DROP_POINTS = 20, CLEAR_POINTS = 500;
  var MODES = { classic: {}, relaxed: {}, puzzle: {}, endless: {} };
  // sin by its series (only + − × ÷, so every browser gets exactly the same numbers; Math.sin may differ in the last digit)
  function dsin(x) { var x2 = x * x, t = x, sum = x; for (var n = 1; n <= 9; n++) { t = -t * x2 / ((2 * n) * (2 * n + 1)); sum += t; } return sum; }
  var HALF_PI = 1.5707963267948966;
  var SIN = [], COS = [];      // by tenths of a degree, so both phones of a race aim the same
  for (var d = -MAX_AIM * 10; d <= MAX_AIM * 10; d++) { SIN[d + MAX_AIM * 10] = dsin(d * HALF_PI / 900); COS[d + MAX_AIM * 10] = dsin(HALF_PI - Math.abs(d) * HALF_PI / 900); }

  // Puzzles (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it.
  // A puzzle is { name, rows: 3–8 rows of 8 characters ("." empty, "1"–"6" a colour), colours, drop }.
  var LEVELS = [
    { name: "Warm up", rows: ["11223344", "11223344", "22334411"], colours: 4, drop: 10 },
    { name: "Stripes", rows: ["11111111", "22222222", "33333333", "11111111"], colours: 3, drop: 9 },
    { name: "Checks", rows: ["11221122", "22112211", "11221122", "33443344", "33443344"], colours: 4, drop: 9 },
    { name: "Hanging garden", rows: ["12341234", "1.3.1.3.", "1...1...", "4...4...", "4...4..."], colours: 4, drop: 8 },
    { name: "Rainbow", rows: ["11223355", "11223355", "44556611", "44556611", "22334466"], colours: 6, drop: 8 },
    { name: "Arrow", rows: ["55555555", "4444444.", ".333333.", "..2222..", "..111...", "...6...."], colours: 6, drop: 8 },
    { name: "Grapes", rows: ["12121212", "21212121", "33333333", "45454545", "54545454", "66666666"], colours: 6, drop: 7 },
    { name: "Big wall", rows: ["11223344", "22334455", "33445566", "44556611", "55661122", "66112233", "11223344"], colours: 6, drop: 7 },
  ];
  /** Is cell (r, c) set half a bubble to the right? */
  function shifted(s, r) { return ((r + s.parity) & 1) === 1; }
  function neighbours(par, r, c) {
    var sh = ((r + par) & 1) === 1, out = [[r, c - 1], [r, c + 1]];
    if (sh) out.push([r - 1, c], [r - 1, c + 1], [r + 1, c], [r + 1, c + 1]);
    else out.push([r - 1, c - 1], [r - 1, c], [r + 1, c - 1], [r + 1, c]);
    return out.filter(function (p) { return p[0] >= 0 && p[0] < MAX_ROWS && p[1] >= 0 && p[1] < COLS; });
  }
  /** Why a puzzle can't be played, or null. */
  function levelProblem(l) {
    var n = 0, r, c, seen = {}, queue = [];
    for (r = 0; r < l.rows.length; r++) for (c = 0; c < COLS; c++) {
      var ch = l.rows[r][c];
      if (ch === ".") continue;
      n++;
      if (Number(ch) > l.colours) return "a bubble uses a colour beyond the puzzle's colours";
      if (r === 0) { seen[r + "," + c] = true; queue.push([r, c]); }
    }
    if (n < 12) return "fewer than 12 bubbles";
    while (queue.length) {
      var p = queue.shift(), ns = neighbours(0, p[0], p[1]);
      for (var i = 0; i < ns.length; i++) {
        var q = ns[i], key = q[0] + "," + q[1];
        if (q[0] < l.rows.length && l.rows[q[0]][q[1]] !== "." && !seen[key]) { seen[key] = true; queue.push(q); }
      }
    }
    if (Object.keys(seen).length !== n) return "every bubble must hang from the top row";
    return null;
  }
  function isInt(v, lo, hi) { return typeof v === "number" && v === Math.floor(v) && v >= lo && v <= hi; }
  /** The puzzles to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (l) {
      if (!l || typeof l !== "object" || typeof l.name !== "string" || !l.name) return false;
      if (!isInt(l.colours, 3, 6) || !isInt(l.drop, 4, 12)) return false;
      if (!Array.isArray(l.rows) || l.rows.length < 3 || l.rows.length > 8) return false;
      for (var r = 0; r < l.rows.length; r++) if (typeof l.rows[r] !== "string" || !/^[.1-6]{8}$/.test(l.rows[r])) return false;
      return levelProblem(l) === null;
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  // ---------- the grid ----------
  function emptyGrid() { var g = []; for (var r = 0; r < MAX_ROWS; r++) { g.push([]); for (var c = 0; c < COLS; c++) g[r].push(0); } return g; }
  function ceil(s) { return TOP + s.drops * RH; }
  function cellX(s, r, c) { return X0 + R + c * D + (shifted(s, r) ? R : 0); }
  function cellY(s, r) { return ceil(s) + R + r * RH; }
  function count(s) { var n = 0; for (var r = 0; r < MAX_ROWS; r++) for (var c = 0; c < COLS; c++) if (s.grid[r][c]) n++; return n; }
  function coloursOnBoard(s) {
    var have = [];
    for (var r = 0; r < MAX_ROWS; r++) for (var c = 0; c < COLS; c++) if (s.grid[r][c] && have.indexOf(s.grid[r][c]) < 0) have.push(s.grid[r][c]);
    return have.sort();
  }
  function pickColour(s) {
    var have = coloursOnBoard(s);
    if (!have.length) return 1 + Math.floor(rand(s) * s.colours);
    return have[Math.floor(rand(s) * have.length)];
  }
  function randomRow(s, r) {
    for (var c = 0; c < COLS; c++) {
      var left = c > 0 ? s.grid[r][c - 1] : 0, up = r > 0 ? s.grid[r - 1][c] : 0, roll = rand(s);
      s.grid[r][c] = left && roll < 0.25 ? left : up && roll < 0.4 ? up : 1 + Math.floor(rand(s) * s.colours);
    }
  }

  function puzzle(s) { return s.list ? s.list[Math.min(s.level, s.list.length) - 1] : null; }
  function startBoard(s) {
    var p = puzzle(s), r, c, n = s.level;
    s.grid = emptyGrid(); s.parity = 0; s.drops = 0; s.sinceDrop = 0; s.phase = "aim"; s.t = 0; s.shot = null; s.reload = 0;
    if (p) {
      s.colours = p.colours; s.dropEvery = p.drop;
      for (r = 0; r < p.rows.length; r++) for (c = 0; c < COLS; c++) s.grid[r][c] = p.rows[r][c] === "." ? 0 : Number(p.rows[r][c]);
    } else if (s.mode === "endless") {
      s.colours = Math.min(6, 4 + Math.floor((n - 1) / 3)); s.dropEvery = Math.max(4, 8 - Math.floor((n - 1) / 2));
      for (r = 0; r < 5; r++) randomRow(s, r);
    } else if (s.mode === "relaxed") {   // gentle (for little ones): few colours, short boards, a slow ceiling
      s.colours = Math.min(4, 3 + Math.floor((n - 1) / 3)); s.dropEvery = 14;
      for (r = 0; r < Math.min(6, 3 + Math.floor(n / 2)); r++) randomRow(s, r);
    } else {
      s.colours = Math.min(6, 3 + Math.floor((n - 1) / 2)); s.dropEvery = Math.max(5, 10 - n);
      for (r = 0; r < Math.min(8, 3 + n); r++) randomRow(s, r);
    }
    s.cur = pickColour(s); s.next = pickColour(s);
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, level: 1, score: 0, over: false, won: false, updates: 0,
      grid: null, parity: 0, drops: 0, sinceDrop: 0, dropEvery: 8, colours: 4, aim: 0, cur: 1, next: 1,
      shot: null, reload: 0, phase: "aim", t: 0, pops: [], falls: [], pushed: 0,
      held: { left: false, right: false }, fireReq: false,
      list: mode === "puzzle" ? usableLevels(o.levels) : null,
      stats: { shots: 0, popped: 0, dropped: 0, boards: 0, bestPop: 0, cause: null },
    };
    startBoard(s);
    return s;
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [], i;
    if (s.over) return evs;
    s.updates++;
    for (i = s.pops.length - 1; i >= 0; i--) if (++s.pops[i].t > 16) s.pops.splice(i, 1);
    for (i = s.falls.length - 1; i >= 0; i--) { var f = s.falls[i]; f.vy += 0.35; f.y += f.vy; if (f.y > 320) s.falls.splice(i, 1); }
    if (s.held.left) s.aim = Math.max(-MAX_AIM, Math.round((s.aim - TURN) * 10) / 10);
    if (s.held.right) s.aim = Math.min(MAX_AIM, Math.round((s.aim + TURN) * 10) / 10);
    if (s.phase === "clear") {
      if (--s.t <= 0) {
        if (s.list && s.level >= s.list.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          evs.push({ type: "win", score: s.score });
          return evs;
        }
        if (s.mode === "endless") { startBoard(s); evs.push({ type: "refill" }); return evs; }   // Endless: its level counts the rows pushed in
        s.level = Math.min(MAX_LEVEL, s.level + 1);
        startBoard(s);
        evs.push({ type: "level", level: s.level });
      }
      return evs;
    }
    if (s.phase === "lost") {
      if (--s.t <= 0) { s.over = true; evs.push({ type: "gameover", score: s.score, cause: "low" }); }
      return evs;
    }
    if (s.reload > 0) s.reload--;
    if (s.fireReq && !s.shot && s.reload === 0) {
      s.fireReq = false;
      var a = Math.round(s.aim * 10) + MAX_AIM * 10;
      s.shot = { x: LX, y: LY, dx: SIN[a], dy: -COS[a], k: s.cur };
      s.cur = s.next; s.next = pickColour(s); s.stats.shots++;
      evs.push({ type: "shoot" });
    }
    if (s.shot) moveShot(s, evs);
    return evs;
  }

  function moveShot(s, evs) {
    var b = s.shot, v = SPEED / SUB;
    for (var k = 0; k < SUB; k++) {
      b.x += b.dx * v; b.y += b.dy * v;
      if (b.x < X0 + R) { b.x = 2 * (X0 + R) - b.x; b.dx = -b.dx; evs.push({ type: "bounce" }); }
      else if (b.x > X1 - R) { b.x = 2 * (X1 - R) - b.x; b.dx = -b.dx; evs.push({ type: "bounce" }); }
      if (b.y - R <= ceil(s) || touches(s, b.x, b.y)) { stick(s, evs); return; }
    }
  }
  function touches(s, x, y) {
    var r0 = Math.max(0, Math.floor((y - ceil(s) - R) / RH) - 1);
    for (var r = r0; r <= Math.min(MAX_ROWS - 1, r0 + 3); r++) for (var c = 0; c < COLS; c++) {
      if (!s.grid[r][c]) continue;
      var dx = cellX(s, r, c) - x, dy = cellY(s, r) - y;
      if (dx * dx + dy * dy < STICK * STICK) return true;
    }
    return false;
  }
  /** The empty cell nearest to (x, y) that touches the ceiling or a bubble. */
  function snapCell(s, x, y, wide) {
    var best = null, bd = 1e9;
    var r0 = wide ? 0 : Math.max(0, Math.round((y - ceil(s) - R) / RH) - 1), r1 = wide ? MAX_ROWS - 1 : Math.min(MAX_ROWS - 1, r0 + 2);
    for (var r = r0; r <= r1; r++) for (var c = 0; c < COLS; c++) {
      if (s.grid[r][c]) continue;
      var ok = r === 0, ns = neighbours(s.parity, r, c);
      for (var i = 0; i < ns.length && !ok; i++) if (s.grid[ns[i][0]][ns[i][1]]) ok = true;
      if (!ok) continue;
      var dx = cellX(s, r, c) - x, dy = cellY(s, r) - y, dd = dx * dx + dy * dy;
      if (dd < bd) { bd = dd; best = [r, c]; }
    }
    return best || (wide ? null : snapCell(s, x, y, true));
  }

  function stick(s, evs) {
    var b = s.shot, at = snapCell(s, b.x, b.y);
    s.shot = null; s.reload = RELOAD;
    if (!at) { evs.push({ type: "fizzle" }); return; }
    s.grid[at[0]][at[1]] = b.k;
    evs.push({ type: "stick" });
    var group = flood(s, at[0], at[1], b.k), gained = 0;
    if (group.length >= 3) {
      for (var i = 0; i < group.length; i++) {
        var g = group[i];
        s.pops.push({ x: cellX(s, g[0], g[1]), y: cellY(s, g[0]), k: s.grid[g[0]][g[1]], t: 0 });
        s.grid[g[0]][g[1]] = 0;
      }
      gained += group.length * POP_POINTS; s.stats.popped += group.length;
      var fell = dropLoose(s);
      gained += fell * DROP_POINTS; s.stats.dropped += fell;
      s.stats.bestPop = Math.max(s.stats.bestPop, group.length + fell);
      s.score += gained;
      evs.push({ type: "pop", count: group.length, dropped: fell, score: s.score });
    }
    if (!count(s)) {
      s.score += CLEAR_POINTS; s.stats.boards++;
      s.phase = "clear"; s.t = CLEAR_T;
      evs.push({ type: "clear", level: s.level, score: s.score });
      return;
    }
    // the next bubbles are colours still on the board
    var have = coloursOnBoard(s);
    if (have.indexOf(s.cur) < 0) s.cur = pickColour(s);
    if (have.indexOf(s.next) < 0) s.next = pickColour(s);
    // the ceiling comes down (Endless: a new row pushes in)
    if (++s.sinceDrop >= s.dropEvery) {
      s.sinceDrop = 0;
      if (s.mode === "endless") pushRow(s, evs); else { s.drops++; evs.push({ type: "lower" }); }
    }
    if (tooLow(s)) { s.phase = "lost"; s.t = 50; s.stats.cause = "low"; evs.push({ type: "low" }); }
  }
  function flood(s, r, c, k) {
    var seen = {}, out = [], queue = [[r, c]];
    seen[r + "," + c] = true;
    while (queue.length) {
      var p = queue.shift();
      out.push(p);
      var ns = neighbours(s.parity, p[0], p[1]);
      for (var i = 0; i < ns.length; i++) {
        var q = ns[i], key = q[0] + "," + q[1];
        if (!seen[key] && s.grid[q[0]][q[1]] === k) { seen[key] = true; queue.push(q); }
      }
    }
    return out;
  }
  /** Drop every bubble no longer connected to the ceiling; how many fell. */
  function dropLoose(s) {
    var seen = {}, queue = [], r, c;
    for (c = 0; c < COLS; c++) if (s.grid[0][c]) { seen["0," + c] = true; queue.push([0, c]); }
    while (queue.length) {
      var p = queue.shift(), ns = neighbours(s.parity, p[0], p[1]);
      for (var i = 0; i < ns.length; i++) {
        var q = ns[i], key = q[0] + "," + q[1];
        if (!seen[key] && s.grid[q[0]][q[1]]) { seen[key] = true; queue.push(q); }
      }
    }
    var fell = 0;
    for (r = 0; r < MAX_ROWS; r++) for (c = 0; c < COLS; c++) {
      if (s.grid[r][c] && !seen[r + "," + c]) {
        s.falls.push({ x: cellX(s, r, c), y: cellY(s, r), k: s.grid[r][c], vy: 0 });
        s.grid[r][c] = 0; fell++;
      }
    }
    return fell;
  }
  function pushRow(s, evs) {
    s.grid.pop(); s.grid.unshift([0, 0, 0, 0, 0, 0, 0, 0]);
    s.parity ^= 1;
    randomRow(s, 0);
    s.pushed++;
    var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.pushed / 5));
    if (lv !== s.level) {
      s.level = lv; s.colours = Math.min(6, 4 + Math.floor((lv - 1) / 3)); s.dropEvery = Math.max(4, 8 - Math.floor((lv - 1) / 2));
      evs.push({ type: "level", level: lv });
    }
    evs.push({ type: "push" });
  }
  function tooLow(s) {
    for (var r = MAX_ROWS - 1; r >= 0; r--) for (var c = 0; c < COLS; c++) if (s.grid[r][c] && cellY(s, r) > LOSE_Y) return true;
    return false;
  }

  // ---------- input ----------
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "left" || action === "right") s.held[action] = !!down;
    else if ((action === "fire" || action === "up") && down) { if (s.phase === "aim") s.fireReq = true; }
    else if ((action === "alt" || action === "down") && down && s.phase === "aim") {
      var t = s.cur; s.cur = s.next; s.next = t;
      return [{ type: "swapNext" }];
    }
    return [];
  }
  /** Aim at a point (a finger or the mouse); below the launcher the finger's x sets the angle. */
  function aimAt(s, x, y) {
    if (s.over) return;
    var deg;
    if (y < LY - 12) deg = Math.atan2(x - LX, LY - y) * 180 / Math.PI;
    else deg = (x - LX) / LX * MAX_AIM;
    s.aim = Math.max(-MAX_AIM, Math.min(MAX_AIM, Math.round(deg * 10) / 10));
  }
  /** Where a shot fired now would go: the path's points (for the aiming guide). */
  function guide(s, len) {
    var a = Math.round(s.aim * 10) + MAX_AIM * 10, x = LX, y = LY, dx = SIN[a], dy = -COS[a], pts = [[x, y]], left = len;
    while (left > 0) {
      var st = Math.min(4, left);
      x += dx * st; y += dy * st; left -= st;
      if (x < X0 + R) { x = 2 * (X0 + R) - x; dx = -dx; pts.push([X0 + R, y]); }
      else if (x > X1 - R) { x = 2 * (X1 - R) - x; dx = -dx; pts.push([X1 - R, y]); }
      if (y - R <= ceil(s) || touches(s, x, y)) break;
    }
    pts.push([x, y]);
    return pts;
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "list") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.held = { left: false, right: false }; out.fireReq = false;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.grid) && data.grid.length === MAX_ROWS &&
      data.grid.every(function (row) { return Array.isArray(row) && row.length === COLS; }) && typeof data.score === "number" &&
      typeof data.level === "number" && typeof data.rng === "number" && typeof data.aim === "number" && data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.list = s.mode === "puzzle" ? usableLevels(levels) : null;
    if (s.list && s.level > s.list.length) s.level = s.list.length;
    if (!Array.isArray(s.pops)) s.pops = [];
    if (!Array.isArray(s.falls)) s.falls = [];
    s.held = { left: false, right: false }; s.fireReq = false;
    return s;
  }

  function result(s) {
    return { score: s.score, level: s.level, stats: { shots: s.stats.shots, popped: s.stats.popped, dropped: s.stats.dropped,
      boards: s.stats.boards, bestPop: s.stats.bestPop, won: !!s.won, cause: s.stats.cause, mode: s.mode } };
  }

  var BubblesLogic = {
    W: W, COLS: COLS, D: D, R: R, RH: RH, X0: X0, X1: X1, TOP: TOP, LOSE_Y: LOSE_Y, MAX_ROWS: MAX_ROWS, LX: LX, LY: LY,
    SPEED: SPEED, MAX_AIM: MAX_AIM, RELOAD: RELOAD, CLEAR_T: CLEAR_T, POP_POINTS: POP_POINTS, DROP_POINTS: DROP_POINTS,
    CLEAR_POINTS: CLEAR_POINTS, MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    levelProblem: levelProblem, neighbours: neighbours, cellX: cellX, cellY: cellY, ceil: ceil, count: count,
    coloursOnBoard: coloursOnBoard, snapCell: snapCell, flood: flood, create: create, step: step, press: press, aimAt: aimAt, guide: guide, save: save,
    restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = BubblesLogic; else self.BubblesLogic = BubblesLogic;
})();
