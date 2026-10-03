/* Household Arcade — Falling Blocks rules (pure: no DOM, deterministic for a given seed).

   A 10 × 20 well (plus two hidden rows on top where pieces appear). One call to step() is one
   update (60 a second). Pieces come from a shuffled bag of all seven shapes, so no shape is
   ever missing for long. */
(function () {
  "use strict";

  var COLS = 10, ROWS = 22, HIDDEN = 2;          // rows 0–1 are above the visible well
  var UPS = 60;
  // Updates per row of fall, by level (1–20).
  var GRAVITY = [0, 48, 42, 36, 31, 26, 22, 18, 15, 12, 10, 9, 8, 7, 6, 5, 4, 4, 3, 3, 2];
  var MAX_LEVEL = 20;
  var LINES_PER_LEVEL = 10;
  var LINE_POINTS = [0, 100, 300, 500, 800];    // × level
  var SOFT_POINTS = 1, HARD_POINTS = 2;          // per row
  var LOCK_DELAY = 30, LOCK_RESETS = 15;         // updates on the floor before a piece locks; moves that restart it
  var ENTRY = 10;                                // updates before the next piece appears
  var CLEAR_TIME = 20;                           // updates the full rows flash before they go
  var DAS = 10, ARR = 3;                         // held left/right: first repeat after 10 updates, then every 3
  var SOFT_EVERY = 2;                            // held down: a row every 2 updates
  var RISE_START = 12 * UPS, RISE_STEP = 30, RISE_MIN = 5 * UPS; // Rising: a row every 12 s, 0.5 s sooner a level, at least 5 s

  // Shapes in their spawn rotation; colours are palette indices.
  var SHAPES = {
    I: { rows: ["....", "####", "....", "...."], ci: 7 },
    O: { rows: ["##", "##"], ci: 3 },
    T: { rows: [".#.", "###", "..."], ci: 6 },
    S: { rows: [".##", "##.", "..."], ci: 4 },
    Z: { rows: ["##.", ".##", "..."], ci: 1 },
    J: { rows: ["#..", "###", "..."], ci: 5 },
    L: { rows: ["..#", "###", "..."], ci: 2 },
  };
  var KINDS = ["I", "O", "T", "S", "Z", "J", "L"];
  var KICKS = [[0, 0], [-1, 0], [1, 0], [-2, 0], [2, 0], [0, -1], [-1, -1], [1, -1]];
  var MODES = {
    classic: { start: 1, rise: false },
    fast: { start: 6, rise: false },
    rising: { start: 1, rise: true },
    challenge: { start: 1, rise: false },    // Challenges: the numbers come from the level list
  };

  // Challenges (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A challenge
  // is { name, rows, goal, speed }: `rows` are 0–12 rows of 10 "." (empty) / "#" (filled) put at the bottom
  // of the well at its start (each has 1–7 gaps), `goal` the rows to clear, `speed` the fall speed 1–15,
  // which is also the points multiplier for rows.
  var LEVELS = [
    { name: "First rows", rows: [], goal: 5, speed: 1 },
    { name: "Little hill", rows: ["...###....", "..######.."], goal: 8, speed: 2 },
    { name: "Steps", rows: ["###.......", "####......", "#####....."], goal: 10, speed: 3 },
    { name: "Two wells", rows: ["##.####.##", "##.####.##", "##.####.##", "##.####.##"], goal: 10, speed: 4 },
    { name: "Checkers", rows: ["#.#.#.#.#.", ".#.#.#.#.#", "#.#.#.#.#.", ".#.#.#.#.#"], goal: 12, speed: 5 },
    { name: "Valley", rows: ["###....###", "####..####", "####..####", "#####.####", "#####.####"], goal: 15, speed: 6 },
    { name: "Swiss cheese", rows: [".###.##.##", "##.###.###", "#.####.##.", "###.##.###", ".##.####.#", "####.##.##"], goal: 15, speed: 7 },
    { name: "Tall tower", rows: ["...###....", "...####...", "...####...", "..######..", "..######..", ".########.",
      ".########.", "#########."], goal: 20, speed: 8 },
    { name: "Speedway", rows: ["#########.", ".#########", "#########.", ".#########"], goal: 20, speed: 10 },
    { name: "Grand finale", rows: ["#.#.#.#.#.", "##..##..##", "#.##.##.##", "###.###.##", ".####.####", "##.####.##",
      "####.#####", "#.########", "########.#", "#####.####"], goal: 25, speed: 12 },
  ];
  var MAX_ROWS = 12, CHALLENGE_SPEED = 15;
  var INTRO = 150;                               // updates before a challenge's first piece (its name shows)

  /** The challenges to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c.name !== "string" || !Array.isArray(c.rows) || c.rows.length > MAX_ROWS) return false;
      if (!(c.goal === Math.floor(c.goal) && c.goal >= 5 && c.goal <= 40)) return false;
      if (!(c.speed === Math.floor(c.speed) && c.speed >= 1 && c.speed <= CHALLENGE_SPEED)) return false;
      return c.rows.every(function (r) {
        if (typeof r !== "string" || !/^[.#]{10}$/.test(r)) return false;
        var n = r.split("#").length - 1;
        return n >= 3 && n <= 9;
      });
    });
    return out.length ? out : LEVELS;
  }
  function challenge(s) { return s.challenges ? s.challenges[Math.min(s.level, s.challenges.length) - 1] : null; }
  /** The fall-speed level (1–20): the level, or the challenge's speed. */
  function speedLevel(s) { var c = challenge(s); return c ? c.speed : s.level; }

  // cells of each shape in each of its 4 rotations: [[x, y], ...] inside its box
  var CELLS = {};
  KINDS.forEach(function (k) {
    var m = SHAPES[k].rows.map(function (r) { return r.split("").map(function (c) { return c === "#"; }); });
    var n = m.length, rots = [];
    for (var r = 0; r < 4; r++) {
      var cells = [];
      for (var y = 0; y < n; y++) for (var x = 0; x < n; x++) if (m[y][x]) cells.push([x, y]);
      rots.push(cells);
      var next = [];   // rotate clockwise
      for (y = 0; y < n; y++) { next.push([]); for (x = 0; x < n; x++) next[y].push(m[n - 1 - x][y]); }
      m = next;
    }
    CELLS[k] = rots;
  });

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, cols: COLS, rows: ROWS,
      grid: new Array(COLS * ROWS).fill(0),
      bag: [], next: [], piece: null, hold: null, canHold: true,
      score: 0, lines: 0, level: MODES[mode].start, startLevel: MODES[mode].start,
      fall: 0, lockT: 0, resets: 0, wait: 0, clearing: null,
      riseT: 0, over: false, updates: 0, version: 1,
      held: { left: 0, right: 0, down: 0 },
      stats: { pieces: 0, singles: 0, doubles: 0, triples: 0, fours: 0, rowsRisen: 0, holds: 0, challenges: 0, cause: null },
      challenges: null, goalLeft: 0, won: false,
    };
    fillNext(s);
    if (mode === "challenge") {
      s.challenges = usableLevels(o.levels);
      startChallenge(s, 1, []);
    } else spawn(s, []);
    return s;
  }

  /** A fresh well with challenge n's rows; its first piece comes after a pause (Hold and next carry over). */
  function startChallenge(s, n, evs) {
    s.level = n;
    var c = challenge(s), g = new Array(COLS * ROWS).fill(0);
    for (var i = 0; i < c.rows.length; i++) {
      var row = ROWS - c.rows.length + i;
      for (var x = 0; x < COLS; x++) if (c.rows[i].charAt(x) === "#") g[row * COLS + x] = 8;
    }
    s.grid = g; s.version++;
    s.piece = null; s.clearing = null; s.wait = INTRO; s.canHold = true;
    s.goalLeft = c.goal;
    if (n > 1) evs.push({ type: "level", level: n });
  }

  function fillNext(s) {
    while (s.next.length < 4) {
      if (!s.bag.length) {
        var b = KINDS.slice();
        for (var i = b.length - 1; i > 0; i--) { var j = Math.floor(rand(s) * (i + 1)), t = b[i]; b[i] = b[j]; b[j] = t; }
        s.bag = b;
      }
      s.next.push(s.bag.shift());
    }
  }

  function cellsOf(p, dx, dy, r) {
    var rot = CELLS[p.t][r == null ? p.r : r], out = [];
    for (var i = 0; i < rot.length; i++) out.push([p.x + rot[i][0] + (dx || 0), p.y + rot[i][1] + (dy || 0)]);
    return out;
  }
  function fits(s, cells) {
    for (var i = 0; i < cells.length; i++) {
      var x = cells[i][0], y = cells[i][1];
      if (x < 0 || x >= COLS || y >= ROWS) return false;
      if (y >= 0 && s.grid[y * COLS + x]) return false;
    }
    return true;
  }
  function canMove(s, dx, dy) { return !!s.piece && fits(s, cellsOf(s.piece, dx, dy)); }
  function onFloor(s) { return !canMove(s, 0, 1); }

  function place(s, t, evs) {
    s.piece = { t: t, r: 0, x: t === "O" ? 4 : 3, y: 0 };
    s.fall = 0; s.lockT = 0; s.resets = 0;
    if (!fits(s, cellsOf(s.piece))) { gameOver(s, evs, "top"); return false; }
    return true;
  }

  function spawn(s, evs) {
    var t = s.next.shift();
    fillNext(s);
    s.stats.pieces++;
    s.canHold = true;
    if (!place(s, t, evs)) return;
    // a held key keeps acting on the new piece after the repeat delay, as players expect
    if (s.held.left) s.held.left = 1;
    if (s.held.right) s.held.right = 1;
  }

  function gameOver(s, evs, cause) {
    s.over = true; s.piece = null; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause });
  }

  function moved(s) {
    // a successful move or turn on the floor restarts the lock timer (a limited number of times)
    if (onFloor(s) && s.resets < LOCK_RESETS) { s.lockT = 0; s.resets++; }
  }

  function shift(s, dx, evs) {
    if (!s.piece || !canMove(s, dx, 0)) return false;
    s.piece.x += dx; moved(s);
    evs.push({ type: "move" });
    return true;
  }

  function rotate(s, dir, evs) {
    var p = s.piece;
    if (!p || p.t === "O") return false;
    var r = (p.r + (dir < 0 ? 3 : 1)) % 4;
    for (var i = 0; i < KICKS.length; i++) {
      var k = KICKS[i];
      if (p.t !== "I" && Math.abs(k[0]) === 2) continue;
      if (fits(s, cellsOf(p, k[0], k[1], r))) {
        p.r = r; p.x += k[0]; p.y += k[1]; moved(s);
        evs.push({ type: "turn" });
        return true;
      }
    }
    return false;
  }

  /** Put the falling piece aside (or swap it with the one put aside before); once per piece. */
  function hold(s, evs) {
    if (!s.piece || !s.canHold || s.over) return false;
    var t = s.piece.t, back = s.hold;
    s.hold = t; s.canHold = false; s.stats.holds++;
    evs.push({ type: "hold" });
    if (back) place(s, back, evs);
    else { var n = s.next.shift(); fillNext(s); place(s, n, evs); }
    return true;
  }

  function dropDistance(s) {
    if (!s.piece) return 0;
    var d = 0;
    while (canMove(s, 0, d + 1)) d++;
    return d;
  }

  function hardDrop(s, evs) {
    if (!s.piece) return;
    var d = dropDistance(s);
    s.piece.y += d;
    s.score += d * HARD_POINTS;
    lock(s, evs, true);
  }

  function lock(s, evs, hard) {
    var p = s.piece, cells = cellsOf(p), ci = SHAPES[p.t].ci, above = true;
    for (var i = 0; i < cells.length; i++) {
      var x = cells[i][0], y = cells[i][1];
      if (y >= 0) s.grid[y * COLS + x] = ci;
      if (y >= HIDDEN) above = false;
    }
    s.piece = null; s.version++;
    evs.push({ type: hard ? "drop" : "lock" });
    if (above) { gameOver(s, evs, "top"); return; }   // locked entirely above the well
    var full = [];
    for (var row = 0; row < ROWS; row++) {
      var n = 0;
      for (var c = 0; c < COLS; c++) if (s.grid[row * COLS + c]) n++;
      if (n === COLS) full.push(row);
    }
    if (full.length) {
      s.clearing = { rows: full, t: CLEAR_TIME };
      var lv = speedLevel(s), n2 = full.length;
      s.score += LINE_POINTS[n2] * lv;
      s.lines += n2;
      s.stats[["", "singles", "doubles", "triples", "fours"][n2]]++;
      evs.push({ type: "row", lines: n2, points: LINE_POINTS[n2] * lv });
      if (s.challenges) {
        s.goalLeft = Math.max(0, s.goalLeft - n2);
        if (!s.goalLeft) {
          s.stats.challenges++;
          if (s.level >= s.challenges.length) {      // the last challenge: the game is won
            s.won = true; s.over = true; s.stats.cause = "won";
            evs.push({ type: "win", score: s.score });
          } else s.clearing.done = true;              // the next challenge once the rows have gone
        }
        return;
      }
      var newLevel = Math.min(MAX_LEVEL, s.startLevel + Math.floor(s.lines / LINES_PER_LEVEL));
      if (newLevel > s.level) { s.level = newLevel; evs.push({ type: "level", level: newLevel }); }
    } else {
      s.wait = ENTRY;
    }
  }

  function collapse(s) {
    var rows = s.clearing.rows, g = s.grid, out = [];
    for (var r = ROWS - 1; r >= 0; r--) if (rows.indexOf(r) < 0) out.unshift(g.slice(r * COLS, r * COLS + COLS));
    while (out.length < ROWS) out.unshift(new Array(COLS).fill(0));
    s.grid = [].concat.apply([], out);
    s.clearing = null; s.version++;
  }

  function riseEvery(s) { return Math.max(RISE_MIN, RISE_START - (s.level - 1) * RISE_STEP); }

  function rise(s, evs) {
    // the top row must be empty or the stack is pushed out of the well
    for (var c = 0; c < COLS; c++) if (s.grid[c]) { gameOver(s, evs, "rise"); return; }
    var hole = Math.floor(rand(s) * COLS), row = [];
    for (c = 0; c < COLS; c++) row.push(c === hole ? 0 : 8);
    s.grid = s.grid.slice(COLS).concat(row);
    s.version++; s.stats.rowsRisen++;
    evs.push({ type: "rise" });
    if (s.piece && !fits(s, cellsOf(s.piece))) {
      if (fits(s, cellsOf(s.piece, 0, -1))) s.piece.y--;
      else gameOver(s, evs, "rise");
    }
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;

    if (MODES[s.mode].rise && !s.clearing) {
      if (++s.riseT >= riseEvery(s)) { s.riseT = 0; rise(s, evs); if (s.over) return evs; }
    }

    if (s.clearing) {
      if (--s.clearing.t <= 0) {
        if (s.clearing.done) startChallenge(s, s.level + 1, evs);
        else { collapse(s); s.wait = ENTRY; }
      }
      return evs;
    }
    if (!s.piece) {
      if (--s.wait <= 0) spawn(s, evs);
      return evs;
    }

    // held left / right repeat
    ["left", "right"].forEach(function (k) {
      if (!s.held[k]) return;
      s.held[k]++;
      var n = s.held[k] - 1 - DAS;
      if (n >= 0 && n % ARR === 0) shift(s, k === "left" ? -1 : 1, evs);
    });

    // falling: gravity, or soft drop while down is held
    var soft = s.held.down > 0;
    if (soft) s.held.down++;
    var grav = GRAVITY[Math.min(MAX_LEVEL, speedLevel(s))];
    var every = soft ? Math.min(SOFT_EVERY, grav) : grav;
    if (!onFloor(s)) {
      s.lockT = 0;
      if (++s.fall >= every) {
        s.fall = 0; s.piece.y++;
        if (soft) s.score += SOFT_POINTS;
      }
    } else {
      s.fall = 0;
      if (++s.lockT >= LOCK_DELAY) lock(s, evs, false);
    }
    return evs;
  }

  /** A key or button: left/right/down held (down = soft drop), up rotates, fire drops. */
  function press(s, action, down) {
    var evs = [];
    if (s.over) return evs;
    if (action === "left" || action === "right") {
      if (down) {
        if (s.held[action]) return evs;
        s.held[action] = 1;
        s.held[action === "left" ? "right" : "left"] = 0;  // the last one pressed wins
        if (s.piece) shift(s, action === "left" ? -1 : 1, evs);
      } else s.held[action] = 0;
    } else if (action === "down") {
      s.held.down = down ? 1 : 0;
      if (down && s.piece && !onFloor(s)) { s.piece.y++; s.fall = 0; s.score += SOFT_POINTS; }
    } else if (down && (action === "up" || action === "rotate")) {
      rotate(s, 1, evs);
    } else if (down && action === "rotateBack") {
      rotate(s, -1, evs);
    } else if (down && action === "fire") {
      hardDrop(s, evs);
    } else if (down && action === "alt") {
      hold(s, evs);
    }
    return evs;
  }

  /** Move the falling piece by whole columns (a finger drag); returns how far it went. */
  function slide(s, dx) {
    var evs = [], n = 0, dir = dx < 0 ? -1 : 1;
    for (var i = 0; i < Math.abs(dx); i++) { if (!shift(s, dir, evs)) break; n += dir; }
    return n;
  }

  /** Rows of the falling piece's landing place (the "ghost"). */
  function ghostY(s) { return s.piece ? s.piece.y + dropDistance(s) : null; }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "challenges") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && Array.isArray(data.grid) && data.grid.length === COLS * ROWS &&
      Array.isArray(data.next) && Array.isArray(data.bag) && typeof data.score === "number" && data.level >= 1 &&
      typeof data.rng === "number" && MODES[data.mode] && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.held = { left: 0, right: 0, down: 0 };
    if (s.hold === undefined) s.hold = null;            // saves from before Hold
    if (s.canHold === undefined) s.canHold = true;
    if (s.stats.holds === undefined) s.stats.holds = 0;
    if (s.stats.challenges === undefined) s.stats.challenges = 0;    // saves from before Challenges
    if (s.goalLeft === undefined) s.goalLeft = 0;
    s.won = !!s.won;
    s.challenges = s.mode === "challenge" ? usableLevels(levels) : null;
    if (s.challenges && s.level > s.challenges.length) throw new Error("That saved game can't be continued.");
    s.version = (s.version || 0) + 1;
    if (!s.piece && !s.clearing && !(s.wait > 0)) s.wait = ENTRY;
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { lines: s.lines, pieces: s.stats.pieces, singles: s.stats.singles, doubles: s.stats.doubles,
        triples: s.stats.triples, fours: s.stats.fours, holds: s.stats.holds, rowsRisen: s.stats.rowsRisen,
        challenges: s.stats.challenges || 0, won: !!s.won, cause: s.stats.cause, mode: s.mode },
    };
  }

  var BlocksLogic = {
    COLS: COLS, ROWS: ROWS, HIDDEN: HIDDEN, UPS: UPS, GRAVITY: GRAVITY, MAX_LEVEL: MAX_LEVEL,
    LINE_POINTS: LINE_POINTS, LOCK_DELAY: LOCK_DELAY, ENTRY: ENTRY, CLEAR_TIME: CLEAR_TIME, DAS: DAS, ARR: ARR,
    SHAPES: SHAPES, KINDS: KINDS, CELLS: CELLS, MODES: MODES, STATE_VERSION: STATE_VERSION,
    LEVELS: LEVELS, usableLevels: usableLevels, INTRO: INTRO, speedLevel: speedLevel, challenge: challenge,
    create: create, step: step, press: press, slide: slide, rotate: rotate, hardDrop: hardDrop, hold: hold, ghostY: ghostY,
    cellsOf: cellsOf, fits: fits, dropDistance: dropDistance, riseEvery: riseEvery, rise: rise,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = BlocksLogic; else self.BlocksLogic = BlocksLogic;
})();
