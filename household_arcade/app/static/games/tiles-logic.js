/* Household Arcade — Tile Match rules (pure: no DOM, deterministic for a given seed).

   Tiles lie in a layered heap. A tile is free when no tile lies on it and its left or right side is open (no tile
   right beside it on the same layer). Picking two free tiles with the same symbol takes both away; clearing the heap
   solves it. Symbols are drawn by tiles.js: plain shapes for Little, and a number 1–9 with a suit shape (circle,
   diamond, triangle) for the others — four of each.
   Every deal from the seed can be cleared: it is made by clearing the empty heap first — taking pairs of places that
   are free at that moment away from it and giving each pair the next symbol — so that order is a way to clear it; and
   two tiles of one symbol never lie on each other (else the last pair could end up stacked: a dead end). Shuffle deals the tiles left the
   same way (+SHUFFLE_PENALTY s); Hint rings a free pair (+HINT_PENALTY s); Undo puts the last pair back (free).
   Heap positions are in half-tile units: a tile at (x, y, z) covers [x, x+2) × [y, y+2) on layer z.
   The score of a cleared heap is TOP − the counted seconds (time + penalties), at least MIN_SCORE; uncleared 0.
   Only + − × ÷ and Math.floor/abs/min/max/imul are used. One call to step() is one update (1/60 s). */
(function () {
  "use strict";

  var UPS = 60, TOP = 10000, MIN_SCORE = 10, HINT_PENALTY = 15, SHUFFLE_PENALTY = 30, HINT_T = 150, STATE_VERSION = 1;
  var MESSAGE_UPDATES = 240;

  // Layouts: layers of rows ("#" a tile), each layer placed at (dx, dy) half-tiles.
  var LAYOUTS = {
    little: [
      { dx: 0, dy: 0, rows: ["####", "####", "####", "####"] },
      { dx: 2, dy: 2, rows: ["##", "##"] },
    ],
    classic: [
      { dx: 0, dy: 0, rows: [".######.", "########", "########", "########", "########", ".######."] },
      { dx: 2, dy: 2, rows: ["######", "######", "######", "######"] },
      { dx: 6, dy: 4, rows: ["##", "##"] },
    ],
    big: [
      { dx: 0, dy: 0, rows: [".########.", "##########", "##########", "##########", "##########", ".########."] },
      { dx: 2, dy: 2, rows: ["########", "########", "########", "########"] },
      { dx: 4, dy: 4, rows: ["######", "######"] },
      { dx: 8, dy: 4, rows: ["##", "##"] },
    ],
  };
  // kinds: Little 5 shapes; the others suits × numbers (suit = floor(kind / 9), number = kind % 9 + 1)
  var KINDS = { little: 5, classic: 18, big: 26 };

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  /** The heap's places for a layout: [{ x, y, z }] in drawing order (lower layers first, then top to bottom, left to right). */
  function places(mode) {
    var out = [], L = LAYOUTS[mode];
    for (var z = 0; z < L.length; z++) {
      var ly = L[z];
      for (var r = 0; r < ly.rows.length; r++) {
        for (var c = 0; c < ly.rows[r].length; c++) if (ly.rows[r][c] === "#") out.push({ x: ly.dx + 2 * c, y: ly.dy + 2 * r, z: z });
      }
    }
    return out;
  }
  function size(mode) {
    var p = places(mode), w = 0, h = 0;
    p.forEach(function (q) { w = Math.max(w, q.x + 2); h = Math.max(h, q.y + 2); });
    return { w: w, h: h, layers: LAYOUTS[mode].length };
  }

  /** Is place i free, among the places still there (`alive[j]` true)? */
  function isFree(pl, alive, i) {
    var a = pl[i], left = false, right = false;
    for (var j = 0; j < pl.length; j++) {
      if (j === i || !alive[j]) continue;
      var b = pl[j];
      if (b.z === a.z + 1 && Math.abs(b.x - a.x) < 2 && Math.abs(b.y - a.y) < 2) return false;     // something on top
      if (b.z === a.z && Math.abs(b.y - a.y) < 2) {
        if (b.x === a.x - 2) left = true;
        if (b.x === a.x + 2) right = true;
      }
    }
    return !left || !right;
  }
  function freeList(pl, alive) {
    var out = [];
    for (var i = 0; i < pl.length; i++) if (alive[i] && isFree(pl, alive, i)) out.push(i);
    return out;
  }

  /** Do places a and b overlap on neighbouring layers (one lies on the other)? */
  function stacked(pl, a, b) {
    return Math.abs(pl[a].z - pl[b].z) === 1 && Math.abs(pl[a].x - pl[b].x) < 2 && Math.abs(pl[a].y - pl[b].y) < 2;
  }
  /** Deal `pairs` (a list of kinds, one per pair) onto the alive places so they can be cleared: by clearing them,
      taking two places that are free at the same moment for each pair. With `strict`, two tiles of one symbol never
      lie on each other (so the last pair can't end up stacked, which would be a dead end). Returns
      { kinds (per place), order } or null if it got stuck. */
  function dealBackwards(pl, alive0, pairs, st, strict) {
    var alive = alive0.slice(), kinds = [], order = [], i;
    for (i = 0; i < pl.length; i++) kinds.push(-1);
    for (var p = 0; p < pairs.length; p++) {
      var free = freeList(pl, alive), A = -1, B = -1;
      if (free.length < 2) return null;
      for (var t = 0; t < (strict ? 16 : 1); t++) {
        var a = Math.floor(rand(st) * free.length), b = Math.floor(rand(st) * (free.length - 1));
        if (b >= a) b++;
        var ok = true;
        if (strict) for (i = 0; i < pl.length && ok; i++) if (kinds[i] === pairs[p] && (stacked(pl, i, free[a]) || stacked(pl, i, free[b]))) ok = false;
        if (ok) { A = free[a]; B = free[b]; break; }
      }
      if (A < 0) return null;
      kinds[A] = pairs[p]; kinds[B] = pairs[p];
      alive[A] = false; alive[B] = false;
      order.push([A, B]);
    }
    return { kinds: kinds, order: order };
  }
  /** A deal of `pairs` on the alive places: strict first, else any clearable one, else null. */
  function deal(pl, alive, pairs, s) {
    var d = null, tries;
    for (tries = 0; tries < 120 && !d; tries++) d = dealBackwards(pl, alive, shuffled(pairs, s), s, true);
    for (tries = 0; tries < 120 && !d; tries++) d = dealBackwards(pl, alive, shuffled(pairs, s), s, false);
    return d;
  }
  function shuffled(list, st) {
    var a = list.slice();
    for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(rand(st) * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }

  function create(o) {
    o = o || {};
    var mode = LAYOUTS[o.mode] ? o.mode : "classic";
    var pl = places(mode), N = pl.length, s = {
      mode: mode, rng: (o.seed >>> 0) || 1, kinds: [], alive: [], sel: -1, cursor: 0, hint: null, hintT: 0, history: [],
      hintsUsed: 0, shuffles: 0, pairs: 0, updates: 0, won: false, over: false, score: 0, message: "", messageT: 0,
      stuck: false, stats: { misses: 0, undone: 0, cause: null },
    };
    var pairs = [], nk = KINDS[mode], k;
    for (k = 0; k < N / 2; k++) pairs.push(k % nk);
    var alive = [];
    for (k = 0; k < N; k++) alive.push(true);
    s.kinds = deal(pl, alive, pairs, s).kinds;
    s.alive = alive;
    s.cursor = topAt(s, pl, Math.floor(size(mode).w / 2), Math.floor(size(mode).h / 2));
    s.stuck = !anyPair(s);
    return s;
  }

  var PLACES = {};
  function pl(s) { return PLACES[s.mode] || (PLACES[s.mode] = places(s.mode)); }
  function free(s, i) { return s.alive[i] && isFree(pl(s), s.alive, i); }
  /** The free pairs (as [i, j]) right now. */
  function pairsFree(s) {
    var f = freeList(pl(s), s.alive), out = [];
    for (var a = 0; a < f.length; a++) for (var b = a + 1; b < f.length; b++) if (s.kinds[f[a]] === s.kinds[f[b]]) out.push([f[a], f[b]]);
    return out;
  }
  function anyPair(s) { return pairsFree(s).length > 0; }
  function left(s) { var n = 0; for (var i = 0; i < s.alive.length; i++) if (s.alive[i]) n++; return n; }
  /** The top tile covering half-tile point (hx, hy), or -1. */
  function topAt(s, P, hx, hy) {
    P = P || pl(s);
    var best = -1;
    for (var i = 0; i < P.length; i++) {
      if (!s.alive[i]) continue;
      var q = P[i];
      if (hx >= q.x && hx < q.x + 2 && hy >= q.y && hy < q.y + 2 && (best < 0 || q.z >= P[best].z)) best = i;
    }
    return best;
  }

  function seconds(s) { return Math.floor(s.updates / UPS); }
  function effective(s) { return seconds(s) + HINT_PENALTY * s.hintsUsed + SHUFFLE_PENALTY * s.shuffles; }
  function scoreOf(s) { return s.won ? Math.max(MIN_SCORE, TOP - effective(s)) : 0; }
  function say(s, text) { s.message = text; s.messageT = MESSAGE_UPDATES; }

  function afterChange(s, evs) {
    if (left(s) === 0) {
      s.won = true; s.over = true; s.score = scoreOf(s); s.stats.cause = "won"; s.hint = null;
      evs.push({ type: "win", score: s.score });
      return;
    }
    s.stuck = !anyPair(s);
    if (s.stuck) { say(s, "No pairs left to take. Shuffle (+" + SHUFFLE_PENALTY + " s) or Undo."); evs.push({ type: "stuck" }); }
  }

  /** Pick tile i: select it, or take it with the selected one when they match. */
  function pick(s, i) {
    var evs = [];
    if (s.over || !(i >= 0 && i < s.alive.length) || !s.alive[i]) return evs;
    s.cursor = i;
    if (!free(s, i)) { s.stats.misses++; evs.push({ type: "blocked", tile: i }); say(s, "That tile isn't free: it needs nothing on top and one side open."); return evs; }
    if (s.sel === i) { s.sel = -1; evs.push({ type: "unselect" }); return evs; }
    if (s.sel >= 0 && s.kinds[s.sel] === s.kinds[i]) {
      s.alive[s.sel] = false; s.alive[i] = false;
      s.history.push([s.sel, i]);
      s.pairs++; s.sel = -1; s.hint = null;
      evs.push({ type: "match", a: s.history[s.history.length - 1][0], b: i, left: left(s) });
      afterChange(s, evs);
      return evs;
    }
    if (s.sel >= 0) evs.push({ type: "nomatch" });
    s.sel = i;
    evs.push({ type: "select", tile: i });
    return evs;
  }
  function undo(s) {
    if (s.over || !s.history.length) return [];
    var p = s.history.pop();
    s.alive[p[0]] = true; s.alive[p[1]] = true; s.sel = -1; s.hint = null; s.pairs--; s.stats.undone++;
    s.stuck = !anyPair(s);
    return [{ type: "undo" }];
  }
  function hint(s) {
    if (s.over) return [];
    var f = pairsFree(s);
    if (!f.length) { say(s, "No pairs left to take. Shuffle (+" + SHUFFLE_PENALTY + " s) or Undo."); return [{ type: "stuck" }]; }
    var pick_ = f[Math.floor(rand(s) * f.length)];
    s.hint = pick_; s.hintT = HINT_T; s.hintsUsed++;
    say(s, "These two match (+" + HINT_PENALTY + " s).");
    return [{ type: "hint", a: pick_[0], b: pick_[1] }];
  }
  /** Deal the tiles left again so they can still be cleared. */
  function shuffle(s) {
    if (s.over) return [];
    var P = pl(s), pairs = [], counts = {}, i;
    for (i = 0; i < s.alive.length; i++) if (s.alive[i]) counts[s.kinds[i]] = (counts[s.kinds[i]] || 0) + 1;
    Object.keys(counts).sort(function (a, b) { return a - b; }).forEach(function (k) { for (var c = 0; c < counts[k] / 2; c++) pairs.push(+k); });
    var dl = deal(P, s.alive, pairs, s);
    if (!dl) { say(s, "These tiles can't be shuffled into a way out. Try Undo."); return [{ type: "stuck" }]; }
    for (i = 0; i < s.alive.length; i++) if (s.alive[i]) s.kinds[i] = dl.kinds[i];
    s.shuffles++; s.sel = -1; s.hint = null;          // the history stays: an undone pair keeps its own symbol
    say(s, "Shuffled (+" + SHUFFLE_PENALTY + " s).");
    var evs = [{ type: "shuffle" }];
    afterChange(s, evs);
    return evs;
  }
  /** Move the cursor to the nearest tile that way (the top one where tiles overlap). */
  function moveCursor(s, dx, dy) {
    var P = pl(s), cur = s.cursor >= 0 && s.alive[s.cursor] ? P[s.cursor] : null, best = -1, bestD = 1e9;
    if (!cur) { for (var k = 0; k < P.length; k++) if (s.alive[k]) { s.cursor = k; return [{ type: "cursor" }]; } return []; }
    for (var i = 0; i < P.length; i++) {
      if (!s.alive[i] || i === s.cursor) continue;
      var q = P[i], ddx = q.x - cur.x, ddy = q.y - cur.y;
      var along = dx ? ddx * dx : ddy * dy, across = dx ? Math.abs(ddy) : Math.abs(ddx);
      if (along <= 0) continue;
      var d = along * 2 + across * 3 - q.z * 0.5;
      if (topAt(s, P, q.x + 1, q.y + 1) !== i) d += 6;          // under another tile
      if (d < bestD) { bestD = d; best = i; }
    }
    if (best >= 0) s.cursor = best;
    return [{ type: "cursor" }];
  }

  function step(s) {
    if (s.over) return [];
    s.updates++;
    if (s.hintT > 0 && --s.hintT === 0) s.hint = null;
    if (s.messageT > 0 && --s.messageT === 0) s.message = "";
    return [];
  }

  /** Arrows move the cursor, fire picks, alt / hint rings a pair, shuffle, undo; typed keys H, S, U / Z. */
  function press(s, action, down) {
    if (!down || s.over) return [];
    switch (action) {
      case "up": return moveCursor(s, 0, -1);
      case "down": return moveCursor(s, 0, 1);
      case "left": return moveCursor(s, -1, 0);
      case "right": return moveCursor(s, 1, 0);
      case "fire": return pick(s, s.cursor);
      case "alt": case "hint": return hint(s);
      case "shuffle": return shuffle(s);
      case "undo": return undo(s);
    }
    if (action.indexOf("key:") === 0) {
      var k = action.slice(4);
      if (k === "H") return hint(s);
      if (k === "S") return shuffle(s);
      if (k === "U" || k === "Z") return undo(s);
      if (k === "ENTER") return pick(s, s.cursor);
    }
    return [];
  }

  function status(s) { return { score: scoreOf(s), level: 1, over: s.over, done: s.won, seconds: effective(s) }; }
  function save(s) { return JSON.parse(JSON.stringify(s)); }
  function restore(data) {
    var ok = data && typeof data === "object" && LAYOUTS[data.mode] && Array.isArray(data.kinds) && Array.isArray(data.alive) &&
      data.kinds.length === places(data.mode).length && data.alive.length === data.kinds.length && Array.isArray(data.history) &&
      typeof data.updates === "number" && typeof data.rng === "number" && typeof data.hintsUsed === "number" &&
      typeof data.shuffles === "number" && data.stats && typeof data.stats === "object";
    if (ok) {
      var counts = {};
      for (var i = 0; i < data.kinds.length; i++) {
        var k = data.kinds[i];
        if (k !== Math.floor(k) || k < 0 || k >= KINDS[data.mode]) { ok = false; break; }
        if (data.alive[i]) counts[k] = (counts[k] || 0) + 1;
      }
      for (var key in counts) if (counts[key] % 2) ok = false;
    }
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    if (!(s.sel >= 0 && s.alive[s.sel])) s.sel = -1;
    if (!(s.cursor >= 0 && s.cursor < s.alive.length)) s.cursor = 0;
    s.stuck = !anyPair(s);
    return s;
  }

  function clock(sec) { return Math.floor(sec / 60) + ":" + ("0" + (sec % 60)).slice(-2); }
  function summary(s) {
    var out = ["Time " + clock(seconds(s))];
    if (s.hintsUsed) out.push(s.hintsUsed + (s.hintsUsed === 1 ? " hint" : " hints") + " (+" + HINT_PENALTY * s.hintsUsed + " s)");
    if (s.shuffles) out.push(s.shuffles + (s.shuffles === 1 ? " shuffle" : " shuffles") + " (+" + SHUFFLE_PENALTY * s.shuffles + " s)");
    out.push("Counted time " + clock(effective(s)));
    return out;
  }
  function result(s) {
    return {
      score: scoreOf(s), level: 1,
      stats: { won: s.won, cause: s.stats.cause, mode: s.mode, pairs: s.pairs, left: left(s), hints: s.hintsUsed, shuffles: s.shuffles,
        undone: s.stats.undone, effective: effective(s), summary: s.won ? summary(s) : undefined },
    };
  }

  var TilesLogic = {
    UPS: UPS, TOP: TOP, MIN_SCORE: MIN_SCORE, HINT_PENALTY: HINT_PENALTY, SHUFFLE_PENALTY: SHUFFLE_PENALTY, HINT_T: HINT_T,
    STATE_VERSION: STATE_VERSION, LAYOUTS: LAYOUTS, KINDS: KINDS,
    places: places, size: size, isFree: isFree, freeList: freeList, dealBackwards: dealBackwards, deal: deal, stacked: stacked,
    create: create, step: step, press: press, pick: pick, undo: undo, hint: hint, shuffle: shuffle, moveCursor: moveCursor,
    free: free, pairsFree: pairsFree, left: left, topAt: topAt, seconds: seconds, effective: effective, scoreOf: scoreOf,
    status: status, save: save, restore: restore, result: result, clock: clock, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = TilesLogic; else self.TilesLogic = TilesLogic;
})();
