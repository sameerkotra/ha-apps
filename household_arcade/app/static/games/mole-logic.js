/* Household Arcade — Tap the Mole rules (pure: no DOM, deterministic for a given seed).

   Friendly moles pop up out of holes in a garden, stay up a little while, then duck down again.
   Tap one while it's up to bonk it (10 points; a rarer golden mole, up for a shorter time, 50).
   From level 3 a hedgehog sometimes pops up instead: leave it alone. A level every 10 bonks; moles
   come more often and stay up for less time (never less than 0.6 s; golden ones 0.5 s).
   Modes: Classic (3 × 3, three lives: a mole that gets away or a tapped hedgehog costs one), Big
   garden (4 × 4, three lives), 60 seconds (3 × 3, no lives, a 60 s clock; a tapped hedgehog takes
   5 s off it), Gardens (a list of gardens of different shapes, SPEC §11.9: each sets its holes, the bonks
   to clear it, how long moles stay up, how often critters come and how many are hedgehogs or golden;
   three lives for the whole game; clearing the last garden wins). One call to step() is one update.

   Honest score: a new critter needs at least MIN_GAP updates since the last one, and a golden one at
   least GOLD_GAP since the last golden one, so even a perfect player scores at most
   10 × (1 + u / 24) + 40 × (1 + u / 180) after u updates (38.4 a second + 50). */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var W = 240, H = 300;
  var MODES = {
    classic: { cols: 3, rows: 3, lives: 3, timer: 0, maxUp: 3 },
    big: { cols: 4, rows: 4, lives: 3, timer: 0, maxUp: 4 },
    rush: { cols: 3, rows: 3, lives: 0, timer: 3600, maxUp: 3 },
    gardens: { cols: 3, rows: 3, lives: 3, timer: 0, maxUp: 4 },   // the size comes from each garden
  };
  var MOLE = 10, GOLD = 50;
  var BONKS_PER_LEVEL = 10, MAX_LEVEL = 60;
  var RISE = 8;              // updates to come up (and to go down again)
  var MIN_UP = 36, MIN_GOLD_UP = 30;
  var MIN_GAP = 24;          // updates between two new critters, at the fastest
  var GOLD_GAP = 180;        // updates between two golden moles, at least
  var HIT_SHOW = 18;         // a bonked critter shows dazed this long, then the hole is free
  var REST = 12;             // a hole stays empty at least this long before the next critter
  var HOG_FROM = 3, HOG_COST = 300;
  // The play area: holes in a grid of cells 72 × 72 (3 wide/tall), 54 × 54 (4) or 42 × 42 (5), multiples of 6,
  // centred in the 216 × 216 box from (LEFT, TOP).
  var TOP = 54, LEFT = 12, BOX = 216;

  // Gardens (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A garden is
  // { name, holes, bonks, up, every, hedgehogs, golden }: `holes` is 3–5 rows of 3–5 characters, "o" a
  // hole and "." grass (at least 5 holes); `bonks` (8–40) clears it; `up` (36–120) updates a mole stays up;
  // `every` (24–90) updates between critters; `hedgehogs` (0–0.3) and `golden` (0–0.2) their shares.
  var LEVELS = [
    { name: "Little patch", holes: [".o.", "ooo", ".o."], bonks: 8, up: 110, every: 70, hedgehogs: 0, golden: 0.1 },
    { name: "Square bed", holes: ["ooo", "ooo", "ooo"], bonks: 10, up: 100, every: 60, hedgehogs: 0, golden: 0.12 },
    { name: "Two long rows", holes: ["oooo", "....", "oooo"], bonks: 12, up: 92, every: 56, hedgehogs: 0.1, golden: 0.12 },
    { name: "Flower ring", holes: ["oooo", "o..o", "o..o", "oooo"], bonks: 14, up: 84, every: 50, hedgehogs: 0.12, golden: 0.1 },
    { name: "Cabbage rows", holes: ["o.o.o", "o.o.o", "o.o.o"], bonks: 16, up: 76, every: 46, hedgehogs: 0.15, golden: 0.1 },
    { name: "Diamond", holes: ["..o..", ".ooo.", "ooooo", ".ooo.", "..o.."], bonks: 18, up: 70, every: 42, hedgehogs: 0.18, golden: 0.1 },
    { name: "Big lawn", holes: ["oooo", "oooo", "oooo", "oooo"], bonks: 20, up: 62, every: 38, hedgehogs: 0.2, golden: 0.12 },
    { name: "Checkers", holes: ["o.o.o", ".o.o.", "o.o.o", ".o.o.", "o.o.o"], bonks: 22, up: 56, every: 34, hedgehogs: 0.22, golden: 0.1 },
    { name: "Hedgehog hill", holes: ["ooooo", "o.o.o", "ooooo"], bonks: 24, up: 50, every: 30, hedgehogs: 0.28, golden: 0.08 },
    { name: "Grand garden", holes: ["ooooo", "ooooo", "ooooo", "ooooo", "ooooo"], bonks: 30, up: 44, every: 26, hedgehogs: 0.25, golden: 0.15 },
  ];
  var FIELDS = { bonks: [8, 40, 1], up: [36, 120, 1], every: [24, 90, 1], hedgehogs: [0, 0.3, 0], golden: [0, 0.2, 0] };

  /** The gardens to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (g) {
      if (!g || typeof g !== "object" || typeof g.name !== "string" || !Array.isArray(g.holes)) return false;
      var rows = g.holes, n = 0;
      if (rows.length < 3 || rows.length > 5) return false;
      for (var r = 0; r < rows.length; r++) {
        if (typeof rows[r] !== "string" || rows[r].length !== rows[0].length || !/^[o.]{3,5}$/.test(rows[r])) return false;
        n += rows[r].split("o").length - 1;
      }
      if (n < 5) return false;
      for (var k in FIELDS) {
        var f = FIELDS[k], v = g[k];
        if (typeof v !== "number" || !(v >= f[0] && v <= f[1]) || (f[2] && v !== Math.floor(v))) return false;
      }
      return true;
    });
    return out.length ? out : LEVELS;
  }
  function garden(s, n) { return s.gardens ? s.gardens[Math.min(n, s.gardens.length) - 1] : null; }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function cellSize(s) { var n = Math.max(s.cols, s.rows); return n >= 5 ? 42 : n === 4 ? 54 : 72; }
  /** The top-left corner of the garden's grid (centred in the box, on multiples of 6). */
  function origin(s) {
    var cs = cellSize(s);
    return { x: LEFT + Math.floor((BOX - s.cols * cs) / 12) * 6, y: TOP + Math.floor((BOX - s.rows * cs) / 12) * 6 };
  }
  /** The cell box of hole i: { x, y, w, h } in logical px. */
  function cellBox(s, i) {
    var cs = cellSize(s), o = origin(s);
    return { x: o.x + (i % s.cols) * cs, y: o.y + Math.floor(i / s.cols) * cs, w: cs, h: cs };
  }
  /** The cell under a logical point (a hole or, in a garden, grass), or -1. */
  function holeAt(s, x, y) {
    var cs = cellSize(s), o = origin(s), c = Math.floor((x - o.x) / cs), r = Math.floor((y - o.y) / cs);
    if (c < 0 || r < 0 || c >= s.cols || r >= s.rows) return -1;
    return r * s.cols + c;
  }
  function isHole(s, i) { return !!s.holes[i] && !s.holes[i].grass; }

  // How the garden speeds up with the level.
  // In Gardens the numbers come from the garden, held to the same minimums.
  function upTime(level) { return Math.max(MIN_UP, 120 - 8 * (level - 1)); }
  function goldUp(level) { return Math.max(MIN_GOLD_UP, Math.round(upTime(level) * 0.6)); }
  function gap(level) { return Math.max(MIN_GAP, 66 - 4 * (level - 1)); }
  function maxUp(s) {
    if (s.gardens) return Math.min(MODES.gardens.maxUp, 1 + Math.floor(holeCount(s) / 5));
    return Math.min(MODES[s.mode].maxUp, 1 + Math.floor(s.level / 3));
  }
  function hogChance(level) { return level < HOG_FROM ? 0 : Math.min(0.3, 0.15 + 0.015 * (level - HOG_FROM)); }
  function sUp(s) { return s.gardens ? Math.max(MIN_UP, garden(s, s.level).up) : upTime(s.level); }
  function sGoldUp(s) { return s.gardens ? Math.max(MIN_GOLD_UP, Math.round(sUp(s) * 0.6)) : goldUp(s.level); }
  function sGap(s) { return s.gardens ? Math.max(MIN_GAP, garden(s, s.level).every) : gap(s.level); }
  function sHog(s) { return s.gardens ? Math.min(0.3, garden(s, s.level).hedgehogs) : hogChance(s.level); }
  function sGold(s) { return s.gardens ? Math.min(0.2, garden(s, s.level).golden) : 0.12; }
  function holeCount(s) { var n = 0; for (var i = 0; i < s.holes.length; i++) if (!s.holes[i].grass) n++; return n; }

  /** Lay out garden n: its holes, the ring on the hole nearest the middle. */
  function plant(s, n) {
    var gd = garden(s, n);
    s.rows = gd.holes.length; s.cols = gd.holes[0].length; s.holes = []; s.gbonks = 0; s.gt = 0;
    for (var r = 0; r < s.rows; r++) for (var c = 0; c < s.cols; c++) {
      var h = { k: null, t: 0, up: 0, hit: 0, rest: 0 };
      if (gd.holes[r].charAt(c) !== "o") h.grass = true;
      s.holes.push(h);
    }
    var best = -1, bd = Infinity, mr = (s.rows - 1) / 2, mc = (s.cols - 1) / 2;
    for (var i = 0; i < s.holes.length; i++) {
      if (s.holes[i].grass) continue;
      var d = Math.abs(Math.floor(i / s.cols) - mr) + Math.abs(i % s.cols - mc);
      if (d < bd) { bd = d; best = i; }
    }
    s.cur = best;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic", m = MODES[mode];
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, cols: m.cols, rows: m.rows,
      holes: [], cur: Math.floor(m.cols * m.rows / 2) - (m.cols === 4 ? m.cols / 2 + 1 : 0),
      lives: m.lives, clock: m.timer, score: 0, level: 1, bonks: 0,
      since: gap(1) - 40, sinceGold: GOLD_GAP, over: false, won: false, updates: 0,
      gbonks: 0, gt: 0,
      stats: { bonks: 0, golden: 0, missed: 0, hedgehogs: 0, popped: 0, gardens: 0, cause: null },
      gardens: null,   // the list (kept out of save(): restore() is given it again); last, so restore keeps the key order
    };
    if (mode === "gardens") {
      s.gardens = usableLevels(o.levels);
      s.level = startAt(o.startLevel, s.gardens.length);
      plant(s, s.level);
      s.since = sGap(s) - 40;
    } else for (var i = 0; i < m.cols * m.rows; i++) s.holes.push({ k: null, t: 0, up: 0, hit: 0, rest: 0 });
    return s;
  }

  /** How far a critter is out of its hole, 0 … 1 (for drawing). */
  function height(h) {
    if (!h.k) return 0;
    if (h.hit) return Math.max(0, 1 - h.hit / HIT_SHOW) * Math.min(1, h.hitH);
    return Math.max(0, Math.min(1, h.t / RISE, (h.up - h.t) / RISE));
  }

  function upCount(s) {
    var n = 0;
    for (var i = 0; i < s.holes.length; i++) if (s.holes[i].k && !s.holes[i].hit) n++;
    return n;
  }

  function spawn(s, evs) {
    var free = [];
    for (var i = 0; i < s.holes.length; i++) if (!s.holes[i].grass && !s.holes[i].k && s.holes[i].rest <= 0) free.push(i);
    if (!free.length || upCount(s) >= maxUp(s)) return;
    var at = free[Math.floor(rand(s) * free.length)], r = rand(s), k = "mole";
    if (r < sHog(s)) k = "hog";
    else if (s.sinceGold >= GOLD_GAP && r >= 1 - sGold(s)) k = "gold";
    var h = s.holes[at];
    h.k = k; h.t = 0; h.hit = 0; h.hitH = 0;
    h.up = k === "gold" ? sGoldUp(s) : sUp(s);
    s.since = 0;
    if (k === "gold") s.sinceGold = 0;
    s.stats.popped++;
    evs.push({ type: "pop", hole: at, kind: k });
  }

  function loseLife(s, evs, cause) {
    if (!s.lives) return;
    s.lives--;
    evs.push({ type: "life", lives: s.lives, cause: cause });
    if (s.lives <= 0) end(s, evs, cause);
  }

  function end(s, evs, cause) {
    if (s.over) return;
    s.over = true; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause });
  }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    s.since++; s.sinceGold++;
    if (s.gardens) s.gt++;
    var m = MODES[s.mode], i;
    for (i = 0; i < s.holes.length && !s.over; i++) {
      var h = s.holes[i];
      if (!h.k) { if (h.rest > 0) h.rest--; continue; }
      if (h.hit) {
        if (++h.hit > HIT_SHOW) { h.k = null; h.hit = 0; h.rest = REST; }
        continue;
      }
      h.t++;
      if (h.t >= h.up) {          // it went back down
        var k = h.k;
        h.k = null; h.rest = REST;
        if (k === "mole") {
          s.stats.missed++;
          evs.push({ type: "miss", hole: i });
          if (m.lives) loseLife(s, evs, "missed");
        }
      }
    }
    if (s.over) return evs;
    if (m.timer) {
      s.clock--;
      if (s.clock <= 0) { s.clock = 0; end(s, evs, "time"); return evs; }
    }
    if (s.since >= sGap(s)) spawn(s, evs);
    return evs;
  }

  /** Bonk hole i (a tap, or Space on the cursor). */
  function bonk(s, i) {
    var evs = [];
    if (s.over || !(i >= 0 && i < s.holes.length) || s.holes[i].grass) return evs;
    var h = s.holes[i];
    if (!h.k || h.hit || h.t >= h.up) return [{ type: "whiff", hole: i }];
    h.hitH = height(h);
    h.hit = 1;
    if (h.k === "hog") {
      s.stats.hedgehogs++;
      evs.push({ type: "hog", hole: i });
      if (MODES[s.mode].lives) loseLife(s, evs, "hedgehog");
      else {
        s.clock = Math.max(0, s.clock - HOG_COST);
        if (s.clock <= 0) end(s, evs, "time");
      }
      return evs;
    }
    var pts = h.k === "gold" ? GOLD : MOLE;
    s.score += pts; s.bonks++; s.stats.bonks++;
    if (h.k === "gold") s.stats.golden++;
    evs.push({ type: h.k === "gold" ? "gold" : "bonk", hole: i, points: pts, score: s.score });
    if (s.gardens) { if (++s.gbonks >= garden(s, s.level).bonks) cleared(s, evs); return evs; }
    var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.bonks / BONKS_PER_LEVEL));
    if (lv > s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
    return evs;
  }

  /** The garden is cleared: the next one (critters still up go away), or the game is won. */
  function cleared(s, evs) {
    s.stats.gardens++;
    if (s.level >= s.gardens.length) {
      s.won = true; s.over = true; s.stats.cause = "won";
      evs.push({ type: "win", score: s.score });
      return;
    }
    s.level++;
    plant(s, s.level);
    s.since = Math.min(s.since, sGap(s) - 60);   // a short breather (never sooner than the gap rule)
    evs.push({ type: "level", level: s.level, name: garden(s, s.level).name });
  }

  function moveCursor(s, action) {
    var c = s.cur % s.cols, r = Math.floor(s.cur / s.cols);
    if (action === "left") c = Math.max(0, c - 1);
    else if (action === "right") c = Math.min(s.cols - 1, c + 1);
    else if (action === "up") r = Math.max(0, r - 1);
    else if (action === "down") r = Math.min(s.rows - 1, r + 1);
    s.cur = r * s.cols + c;
  }

  function press(s, action, down) {
    if (!down || s.over) return [];
    if (action === "fire") return bonk(s, s.cur);
    if (action === "up" || action === "down" || action === "left" || action === "right") moveCursor(s, action);
    return [];
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "gardens") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var m = data && typeof data === "object" ? MODES[data.mode] : null;
    var gd = m && data.mode === "gardens";
    var ok = m && Array.isArray(data.holes) &&
      (gd ? data.cols >= 3 && data.cols <= 5 && data.rows >= 3 && data.rows <= 5 && data.holes.length === data.cols * data.rows
        : data.holes.length === m.cols * m.rows) &&
      data.holes.every(function (h) { return h && typeof h === "object" && typeof h.t === "number" && typeof h.up === "number"; }) &&
      typeof data.score === "number" && data.level >= 1 && typeof data.rng === "number" &&
      typeof data.lives === "number" && typeof data.clock === "number" && data.stats && typeof data.cur === "number";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.gardens = gd ? usableLevels(levels) : null;
    if (gd && s.level > s.gardens.length) s.level = s.gardens.length;   // a shorter list now: play its last
    if (s.gbonks == null) { s.gbonks = 0; s.gt = 0; }
    if (s.won == null) s.won = false;
    if (!s.stats.gardens) s.stats.gardens = 0;
    return s;
  }

  function result(s) {
    var st = s.stats;
    return { score: s.score, level: s.level, stats: { bonks: st.bonks, golden: st.golden, missed: st.missed,
      hedgehogs: st.hedgehogs, livesLeft: s.lives, gardens: st.gardens || 0, won: !!s.won, cause: st.cause, mode: s.mode } };
  }

  var MoleLogic = {
    W: W, H: H, TOP: TOP, LEFT: LEFT, MODES: MODES, MOLE: MOLE, GOLD: GOLD, BONKS_PER_LEVEL: BONKS_PER_LEVEL,
    MAX_LEVEL: MAX_LEVEL, RISE: RISE, MIN_UP: MIN_UP, MIN_GOLD_UP: MIN_GOLD_UP, MIN_GAP: MIN_GAP, GOLD_GAP: GOLD_GAP,
    HIT_SHOW: HIT_SHOW, REST: REST, HOG_FROM: HOG_FROM, HOG_COST: HOG_COST, STATE_VERSION: STATE_VERSION,
    create: create, step: step, press: press, bonk: bonk, holeAt: holeAt, cellBox: cellBox, cellSize: cellSize,
    LEVELS: LEVELS, usableLevels: usableLevels, garden: garden, isHole: isHole, origin: origin, holeCount: holeCount,
    height: height, upTime: upTime, goldUp: goldUp, gap: gap, maxUp: maxUp, hogChance: hogChance,
    save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = MoleLogic; else self.MoleLogic = MoleLogic;
})();
