/* Household Arcade — Dots and Boxes rules (pure: no DOM, deterministic for a seed).

   A grid of dots — 3 × 3, 4 × 4 or 5 × 5 boxes (the Size option; a match keeps the inviter's). Players take turns
   joining two neighbouring dots with a line. Drawing the fourth side of a box claims it and the same player must draw
   another line; when every line is drawn, more boxes wins (the same number is a draw). Lines are numbered: the across
   lines row by row (row 0–size, column 0–size−1), then the up-and-down lines (row 0–size−1, column 0–size). A move is
   { edge }. The same rules as the server's app/rules/dots.py.

   The computer: Easy takes a box it can see most of the time and otherwise draws anywhere; Medium always takes boxes,
   avoids drawing a box's third side while it can, and when it must, gives away the fewest boxes; Hard also keeps
   control at the end — at the tail of a long run of boxes it leaves the last two for the other player (a
   "double-cross"), so the other has to open the next run for it. */
(function () {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : self.BoardKit;

  var BONUS_BOX = 20;
  var SIZES = ["3", "4", "5"];

  function dims(n) { var h = (n + 1) * n, v = n * (n + 1); return [h, v, h + v]; }
  function boxEdges(n, r, c) { var h = dims(n)[0]; return [r * n + c, (r + 1) * n + c, h + r * (n + 1) + c, h + r * (n + 1) + c + 1]; }
  function edgeBoxes(n, e) {
    var h = dims(n)[0], out = [], r, c;
    if (e < h) { r = Math.floor(e / n); c = e % n; if (r > 0) out.push((r - 1) * n + c); if (r < n) out.push(r * n + c); }
    else { r = Math.floor((e - h) / (n + 1)); c = (e - h) % (n + 1); if (c > 0) out.push(r * n + c - 1); if (c < n) out.push(r * n + c); }
    return out;
  }
  function sides(edges, n, b) {
    var be = boxEdges(n, Math.floor(b / n), b % n), k = 0;
    for (var i = 0; i < 4; i++) if (edges[be[i]]) k++;
    return k;
  }
  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }
  function count(boxes, seat) { var k = 0; for (var i = 0; i < boxes.length; i++) if (boxes[i] === seat + 1) k++; return k; }

  // the cursor walks a (2n+1) × (2n+1) lattice of dots, lines and boxes, on the lines only ((row + column) odd)
  function slotToEdge(n, i, j) {
    var h = dims(n)[0];
    if (i % 2 === 0) return (i / 2) * n + (j - 1) / 2;          // across: row i/2, column (j−1)/2
    return h + ((i - 1) / 2) * (n + 1) + j / 2;                 // up and down
  }
  function edgeToSlot(n, e) {
    var h = dims(n)[0];
    if (e < h) return [2 * Math.floor(e / n), 2 * (e % n) + 1];
    return [2 * Math.floor((e - h) / (n + 1)) + 1, 2 * ((e - h) % (n + 1))];
  }

  var R = {
    id: "dots", STATE_VERSION: 1,
    OPTIONS: { size: { choices: SIZES, default: "4" } },
    init: function (s) {
      var n = parseInt(s.options.size, 10);
      s.size = n;
      s.edges = []; for (var i = 0; i < dims(n)[2]; i++) s.edges.push(0);
      s.boxes = []; for (i = 0; i < n * n; i++) s.boxes.push(0);
      s.made = []; s.moveAt = -100;
    },
    moves: function (s, seat) {
      if (s.over || seat !== s.turn) return [];
      var out = [];
      for (var e = 0; e < s.edges.length; e++) if (!s.edges[e]) out.push({ edge: e });
      return out;
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1 || !isWhole(move.edge, 0, s.edges.length - 1)) throw new Error("Pick a line.");
      var e = move.edge, n = s.size;
      if (s.edges[e]) throw new Error("That line is already drawn.");
      s.edges[e] = seat + 1; s.ply++; s.last = e; s.moveAt = s.updates;
      var bs = edgeBoxes(n, e), made = [];
      for (var i = 0; i < bs.length; i++) if (!s.boxes[bs[i]] && sides(s.edges, n, bs[i]) === 4) { s.boxes[bs[i]] = seat + 1; made.push(bs[i]); }
      s.made = made;
      var evs = [{ type: made.length ? "box" : "line", edge: e, boxes: made.length, seat: seat }];
      if (s.edges.every(function (v) { return v !== 0; })) {
        s.over = true;
        var a = count(s.boxes, 0), z = count(s.boxes, 1);
        s.winner = a > z ? 0 : z > a ? 1 : -1;
      } else if (!made.length) s.turn = 1 - seat;
      else evs.push({ type: "again", seat: seat });
      return evs;
    },
    bonus: function (s, seat) { return BONUS_BOX * (count(s.boxes, seat) - count(s.boxes, 1 - seat)); },
    digest: function (s) { return { size: s.size, edges: s.edges, boxes: s.boxes, turn: s.turn, over: s.over, winner: s.winner }; },
    counts: function (s) { return [count(s.boxes, 0), count(s.boxes, 1)]; },
    cursorStart: function (s) { return Math.floor(s.size / 2) * s.size + Math.floor(s.size / 2); },
    cursorMove: function (s, dir) {
      var n = s.size, sl = edgeToSlot(n, s.cursor), i = sl[0], j = sl[1], M = 2 * n;
      if (dir === "left" && j - 2 >= 0) j -= 2;
      else if (dir === "right" && j + 2 <= M) j += 2;
      else if (dir === "up") {
        if (i % 2 === 0 && i - 1 >= 0) { i -= 1; j = j - 1 >= 0 ? j - 1 : j + 1; }
        else if (i % 2 === 1 && i - 1 >= 0) { i -= 1; j = j + 1 <= M - 1 ? j + 1 : j - 1; }
      } else if (dir === "down") {
        if (i % 2 === 0 && i + 1 <= M) { i += 1; j = j - 1 >= 0 ? j - 1 : j + 1; }
        else if (i % 2 === 1 && i + 1 <= M) { i += 1; j = j + 1 <= M - 1 ? j + 1 : j - 1; }
      }
      s.cursor = slotToEdge(n, i, j);
    },
    activate: function (s, seat) {
      if (seat < 0) return null;
      if (s.edges[s.cursor]) return { events: [{ type: "nope" }] };
      return { move: { edge: s.cursor } };
    },
    tap: function (s, e, seat) { s.cursor = e; return R.activate(s, seat); },
    keep: function (old, s) { if (s.number > old.number) s.moveAt = s.updates; },
    ai: function (s, seat, level) { return { edge: aiEdge(s, seat, level) }; },
    check: function (d) { return SIZES.indexOf(String(d.size)) >= 0 && Array.isArray(d.edges) && d.edges.length === dims(d.size)[2] && Array.isArray(d.boxes); },
    summary: function (s, names) { return [names[0] + " " + count(s.boxes, 0) + " · " + names[1] + " " + count(s.boxes, 1) + " boxes"]; },
  };

  // ---------- the computer ----------
  function takers(edges, n) {
    var out = [];
    for (var e = 0; e < edges.length; e++) {
      if (edges[e]) continue;
      var bs = edgeBoxes(n, e);
      for (var i = 0; i < bs.length; i++) if (sides(edges, n, bs[i]) === 3) { out.push(e); break; }
    }
    return out;
  }
  function safeEdges(edges, n) {
    var out = [];
    for (var e = 0; e < edges.length; e++) {
      if (edges[e]) continue;
      var bs = edgeBoxes(n, e), ok = true;
      for (var i = 0; i < bs.length; i++) if (sides(edges, n, bs[i]) >= 2) { ok = false; break; }
      if (ok) out.push(e);
    }
    return out;
  }
  /** How many boxes the next player takes by taking everything they can, after `e` is drawn. */
  function giveaway(edges, n, e) {
    var b = edges.slice(), got = 0;
    b[e] = 1;
    for (;;) {
      var t = takers(b, n);
      if (!t.length) return got;
      var e2 = t[0], bs = edgeBoxes(n, e2);
      b[e2] = 1;
      for (var i = 0; i < bs.length; i++) if (sides(b, n, bs[i]) === 4) got++;
    }
  }
  /** The boxes taken one after another from here (each take keeps the turn), and the board after. */
  function run(edges, n) {
    var b = edges.slice(), got = 0, order = [];
    for (;;) {
      var t = takers(b, n);
      if (!t.length) return { got: got, board: b, order: order };
      var e = t[0], bs = edgeBoxes(n, e);
      b[e] = 1; order.push(e);
      for (var i = 0; i < bs.length; i++) if (sides(b, n, bs[i]) === 4) got++;
    }
  }
  /** At the tail of a run of exactly two boxes, the line that leaves both to the other player, or −1. */
  function doubleDeal(edges, n) {
    var t = takers(edges, n);
    if (t.length !== 1) return -1;
    var e = t[0], bs = edgeBoxes(n, e), A = -1, B = -1, i, k;
    for (i = 0; i < bs.length; i++) { if (sides(edges, n, bs[i]) === 3) A = bs[i]; else if (sides(edges, n, bs[i]) === 2) B = bs[i]; }
    if (A < 0 || B < 0) return -1;
    var be = boxEdges(n, Math.floor(B / n), B % n);
    for (k = 0; k < 4; k++) {
      var f = be[k];
      if (f === e || edges[f]) continue;
      var others = edgeBoxes(n, f);
      for (i = 0; i < others.length; i++) if (others[i] !== B && sides(edges, n, others[i]) >= 2) return -1;
      return f;
    }
    return -1;
  }
  function aiEdge(s, seat, level) {
    var n = s.size, edges = s.edges, free = [], e;
    for (e = 0; e < edges.length; e++) if (!edges[e]) free.push(e);
    var take = takers(edges, n);
    if (level <= 1) {
      if (take.length && BK.rand(s) < 0.75) return BK.pick(s, take);
      return BK.pick(s, free);
    }
    if (take.length) {
      if (level >= 3) {
        var r = run(edges, n), left = 0;
        for (var i = 0; i < s.boxes.length; i++) if (!s.boxes[i]) left++;
        // keep control: the run here ends in two boxes and the rest of the board has no safe line left
        if (r.got === 2 && !safeEdges(r.board, n).length && left - r.got >= 3) {
          var dd = doubleDeal(edges, n);
          if (dd >= 0) return dd;
        }
      }
      return BK.pick(s, take);
    }
    var safe = safeEdges(edges, n);
    if (safe.length) return BK.pick(s, safe);
    var best = Infinity, bestEdges = [];
    for (i = 0; i < free.length; i++) {
      var g = giveaway(edges, n, free[i]);
      if (g < best) { best = g; bestEdges = [free[i]]; } else if (g === best) bestEdges.push(free[i]);
    }
    return BK.pick(s, bestEdges);
  }

  var L = BK.game(R);
  L.BONUS_BOX = BONUS_BOX; L.SIZES = SIZES; L.dims = dims; L.boxEdges = boxEdges; L.edgeBoxes = edgeBoxes; L.sides = sides;
  L.count = count; L.aiEdge = aiEdge; L.edgeToSlot = edgeToSlot; L.slotToEdge = slotToEdge;
  if (typeof module === "object" && module.exports) module.exports = L; else self.DotsLogic = L;
})();
