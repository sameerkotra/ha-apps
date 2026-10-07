/* Household Arcade — Slide Puzzle rules (pure: no DOM, deterministic for a given seed).

   An n × n tray of numbered tiles with one gap. A tile next to the gap (or a whole line of tiles in the gap's row or
   column) slides into it; putting the tiles back in order — 1, 2, 3 … with the gap at the bottom right — solves it.
   Picture modes show a picture drawn from the seed instead (slide.js draws it), with small numbers as a help.
   The shuffle is a random order of the tiles with the gap at the bottom right, made even when it comes out odd
   (swapping two tiles), so every tray from the seed can be solved; very easy trays are made again.
   Each tile moved is one move. The score of a solved tray is TOP − moves (fewest moves ranks first; at least
   MIN_SCORE); an unsolved one scores 0. In a race the first to solve wins, and fewer moves breaks a tie
   (games.py: {"rule": "fastest", "tiebreak": "score"}).
   Only + − × ÷ and Math.floor/abs/min/max/imul are used, so every browser plays the same tray.
   One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, STATE_VERSION = 1;
  var MODES = {
    three: { n: 3, picture: false, minDist: 8 },
    four: { n: 4, picture: false, minDist: 24 },
    five: { n: 5, picture: false, minDist: 50 },
    picture: { n: 3, picture: true, minDist: 8 },         // gentle: a picture in nine pieces
    picture4: { n: 4, picture: true, minDist: 24 },
  };
  var SCENES = 6;            // pictures slide.js can draw (scene 0 … 5)
  var SLIDE_T = 6;           // updates a tile takes to slide (drawing only)

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** How far every tile is from its home (sum of rows + columns): 0 when solved. */
  function distance(tiles, n) {
    var d = 0;
    for (var i = 0; i < tiles.length; i++) {
      var t = tiles[i];
      if (!t) continue;
      d += Math.abs(Math.floor(i / n) - Math.floor((t - 1) / n)) + Math.abs(i % n - (t - 1) % n);
    }
    return d;
  }
  function inversions(tiles) {
    var inv = 0, a = tiles.filter(function (t) { return t > 0; });
    for (var i = 0; i < a.length; i++) for (var j = i + 1; j < a.length; j++) if (a[i] > a[j]) inv++;
    return inv;
  }
  /** Can these tiles be put in order? (The standard rule: inversions, and for an even size the gap's row.) */
  function solvable(tiles, n) {
    var inv = inversions(tiles), gap = tiles.indexOf(0);
    if (n % 2 === 1) return inv % 2 === 0;
    var rowFromBottom = n - Math.floor(gap / n);
    return (inv + rowFromBottom) % 2 === 1;
  }
  function solvedTiles(n) { var a = []; for (var i = 1; i < n * n; i++) a.push(i); a.push(0); return a; }
  function isSolved(tiles) {
    for (var i = 0; i < tiles.length - 1; i++) if (tiles[i] !== i + 1) return false;
    return tiles[tiles.length - 1] === 0;
  }

  /** A shuffled tray for a size and seed state: always solvable, never close to solved. */
  function shuffle(n, st, minDist) {
    var best = null;
    for (var tries = 0; tries < 40; tries++) {
      var a = [];
      for (var i = 1; i < n * n; i++) a.push(i);
      for (var k = a.length - 1; k > 0; k--) { var j = Math.floor(rand(st) * (k + 1)), t = a[k]; a[k] = a[j]; a[j] = t; }
      a.push(0);
      if (inversions(a) % 2 === 1) { var x = a[0]; a[0] = a[1]; a[1] = x; }       // gap at the bottom right: even = solvable
      var d = distance(a, n);
      if (!best || d > best.d) best = { a: a, d: d };
      if (d >= minDist) break;
    }
    return best.a;
  }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "four", m = MODES[mode];
    var s = {
      mode: mode, n: m.n, picture: m.picture, rng: (o.seed >>> 0) || 1, tiles: null, scene: 0,
      moves: 0, updates: 0, won: false, over: false, score: 0, level: 1, peek: false,
      anim: null, stats: { slides: 0, cause: null },
    };
    s.scene = Math.floor(rand(s) * SCENES);
    s.tiles = shuffle(m.n, s, m.minDist);
    return s;
  }

  function score(s) { return s.won ? Math.max(MIN_SCORE, TOP - s.moves) : 0; }

  /** Slide the tile at cell i (and the tiles between it and the gap) into the gap. */
  function move(s, i) {
    var evs = [];
    if (s.over || !(i >= 0 && i < s.tiles.length) || !s.tiles[i]) return evs;
    var n = s.n, gap = s.tiles.indexOf(0), gr = Math.floor(gap / n), gc = gap % n, r = Math.floor(i / n), c = i % n;
    if (r !== gr && c !== gc) { evs.push({ type: "nope", cell: i }); return evs; }
    var stepIdx = r === gr ? (c > gc ? 1 : -1) : (r > gr ? n : -n), moved = [];
    var at = gap;
    while (at !== i) {
      var from = at + stepIdx;
      s.tiles[at] = s.tiles[from]; s.tiles[from] = 0;
      moved.push([s.tiles[at], from, at]);
      at = from;
    }
    s.moves += moved.length; s.stats.slides++;
    s.anim = { moved: moved, at: s.updates };
    evs.push({ type: "slide", tiles: moved.length, moves: s.moves });
    if (isSolved(s.tiles)) {
      s.won = true; s.over = true; s.score = score(s); s.stats.cause = "won"; s.peek = false;
      evs.push({ type: "win", score: s.score, moves: s.moves });
    }
    return evs;
  }
  /** An arrow: the tile on that side of the gap moves the arrow's way (↑ moves the tile below the gap up). */
  function arrow(s, dir) {
    var n = s.n, gap = s.tiles.indexOf(0), r = Math.floor(gap / n), c = gap % n;
    var from = dir === "up" ? (r < n - 1 ? gap + n : -1) : dir === "down" ? (r > 0 ? gap - n : -1)
      : dir === "left" ? (c < n - 1 ? gap + 1 : -1) : dir === "right" ? (c > 0 ? gap - 1 : -1) : -1;
    if (from < 0) return [{ type: "nope", cell: gap }];
    return move(s, from);
  }

  function step(s) {
    if (s.over) return [];
    s.updates++;
    if (s.anim && s.updates - s.anim.at > SLIDE_T) s.anim = null;
    return [];
  }

  /** The shell's actions: the arrows slide a tile, alt (held) peeks at the finished picture. */
  function press(s, action, down) {
    if (s.over) return [];
    if (action === "alt") { s.peek = !!down; return down ? [{ type: "peek" }] : []; }
    if (!down) return [];
    if (action === "up" || action === "down" || action === "left" || action === "right") return arrow(s, action);
    return [];
  }

  function save(s) { var d = JSON.parse(JSON.stringify(s)); d.anim = null; d.peek = false; return d; }
  function restore(data) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.tiles) && data.n === MODES[data.mode].n &&
      data.tiles.length === data.n * data.n && typeof data.moves === "number" && typeof data.updates === "number" &&
      typeof data.rng === "number" && data.stats && typeof data.stats === "object";
    if (ok) {
      var seen = {};
      for (var i = 0; i < data.tiles.length; i++) {
        var t = data.tiles[i];
        if (t !== Math.floor(t) || t < 0 || t >= data.tiles.length || seen[t]) { ok = false; break; }
        seen[t] = 1;
      }
    }
    if (!ok || !solvable(data.tiles, data.n)) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.anim = null; s.peek = false; s.picture = MODES[s.mode].picture;
    s.scene = Math.max(0, Math.min(SCENES - 1, Math.floor(Number(s.scene) || 0)));
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function seconds(s) { return Math.floor(s.updates / UPS); }
  function result(s) {
    return {
      score: score(s), level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, moves: s.moves, slides: s.stats.slides, size: s.n,
        summary: s.won ? ["Solved in " + s.moves + (s.moves === 1 ? " move" : " moves"), "Time " + clock(seconds(s))] : undefined },
    };
  }

  var SlideLogic = {
    UPS: UPS, TOP: TOP, MIN_SCORE: MIN_SCORE, STATE_VERSION: STATE_VERSION, MODES: MODES, SCENES: SCENES, SLIDE_T: SLIDE_T,
    create: create, step: step, press: press, move: move, arrow: arrow, save: save, restore: restore, result: result,
    score: score, solvable: solvable, isSolved: isSolved, solvedTiles: solvedTiles, distance: distance, inversions: inversions,
    shuffle: shuffle, seconds: seconds, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = SlideLogic; else self.SlideLogic = SlideLogic;
})();
