/* Household Arcade — Sea Battle rules (pure: no DOM, deterministic for a seed).

   Each player places a fleet on their own square sea, hidden from the other, then they take turns firing one shot
   at a square of the other's sea and hear only "miss", "hit" or "sunk" (a sunk ship's squares are shown). Sinking the
   whole other fleet wins. Fleets (the Fleet option; a match keeps the inviter's): classic — 10 × 10 with ships of 5,
   4, 3, 3 and 2; small — 8 × 8 with 4, 3, 3 and 2. Ships lie across or up and down, inside the sea, may touch but never
   overlap. Both place first (in any order); then the first player (from the seed) fires, and turns alternate — a hit
   doesn't give another shot. Squares are row × size + column. Moves: { place: [[row, col, length, 0 across | 1 down],
   …] } once each, then { shot: square }. The same rules as the server's app/rules/seabattle.py.

   Hidden information: against the computer both fleets are in this phone (the computer only ever looks at the
   results of its own shots); on one screen a "pass the phone" screen hides the boards between turns; turn by turn
   from two phones the other fleet never reaches this phone — the server sends this phone's own fleet and the results
   of the shots (fromView), and a shot's result comes back from the server.

   The computer: Easy fires at random (now and then at a square next to a hit); Medium hunts at random and, after a
   hit, tries the squares around it, then along the line; Hard does that too, and hunts where the ships still afloat
   could most often lie. */
(function () {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : self.BoardKit;

  var FLEETS = { classic: [10, [5, 4, 3, 3, 2]], small: [8, [4, 3, 3, 2]] };
  var BONUS_CELL = 15, PASS_DELAY = 70;

  function isWhole(v, lo, hi) { return typeof v === "number" && Math.floor(v) === v && v >= lo && v <= hi; }
  function shipCells(n, r, c, len, down) {
    var out = [];
    for (var i = 0; i < len; i++) out.push((r + (down ? i : 0)) * n + c + (down ? 0 : i));
    return out;
  }
  /** The fleet as lists of squares, or throws (the same checks as the server's check_fleet). */
  function checkFleet(s, place) {
    var n = s.size, fleet = s.fleet;
    if (!Array.isArray(place) || place.length !== fleet.length) throw new Error("Place all " + fleet.length + " ships.");
    var used = {}, out = [], lens = [];
    for (var i = 0; i < place.length; i++) {
      var it = place[i];
      if (!Array.isArray(it) || it.length !== 4) throw new Error("A ship is [row, column, length, 0 across | 1 down].");
      var r = it[0], c = it[1], len = it[2], down = it[3];
      if (!(isWhole(r, 0, n - 1) && isWhole(c, 0, n - 1) && isWhole(len, 1, 5) && isWhole(down, 0, 1))) throw new Error("A ship is [row, column, length, 0 across | 1 down].");
      if ((down ? r + len : c + len) > n) throw new Error("A ship must lie inside the sea.");
      var cells = shipCells(n, r, c, len, down);
      for (var k = 0; k < cells.length; k++) { if (used[cells[k]]) throw new Error("Ships can't overlap."); }
      for (k = 0; k < cells.length; k++) used[cells[k]] = true;
      out.push(cells); lens.push(cells.length);
    }
    var a = lens.slice().sort(), b = fleet.slice().sort();
    for (i = 0; i < a.length; i++) if (a[i] !== b[i]) throw new Error("That isn't the fleet: " + fleet.join(", ") + ".");
    return out;
  }
  function randomFleet(s) {
    var n = s.size;
    for (;;) {
      var used = {}, out = [];
      for (var i = 0; i < s.fleet.length; i++) {
        var len = s.fleet[i];
        for (var tries = 0; tries < 200; tries++) {
          var down = BK.rand(s) < 0.5 ? 1 : 0;
          var r = Math.floor(BK.rand(s) * (n - (down ? len - 1 : 0))), c = Math.floor(BK.rand(s) * (n - (down ? 0 : len - 1)));
          var cells = shipCells(n, r, c, len, down), ok = true;
          for (var k = 0; k < cells.length; k++) if (used[cells[k]]) { ok = false; break; }
          if (!ok) continue;
          for (k = 0; k < cells.length; k++) used[cells[k]] = true;
          out.push([r, c, len, down]);
          break;
        }
      }
      if (out.length === s.fleet.length) return out;
    }
  }
  function shipAt(ships, cell) {
    if (!ships) return null;
    for (var i = 0; i < ships.length; i++) if (ships[i].indexOf(cell) >= 0) return ships[i];
    return null;
  }
  /** The seat placing its fleet at this screen now, or −1. */
  function placer(s) {
    if (s.over || s.phase !== "place") return -1;
    if (s.mode === "phones") return s.placed[s.me] ? -1 : s.me;
    if (s.mode === "two") return s.pass >= 0 || s.passIn > 0 ? -1 : (!s.placed[0] ? 0 : !s.placed[1] ? 1 : -1);
    return s.placed[0] ? -1 : 0;
  }
  function ensureDraft(s) {
    var p = placer(s);
    if (p >= 0 && (!s.draft || s.draftSeat !== p)) { s.draft = randomFleet(s); s.draftSeat = p; s.dsel = -1; s.cursor = 0; }
  }
  function draftCells(s, skip) {
    var used = {};
    for (var i = 0; i < s.draft.length; i++) {
      if (i === skip) continue;
      var d = s.draft[i], cells = shipCells(s.size, d[0], d[1], d[2], d[3]);
      for (var k = 0; k < cells.length; k++) used[cells[k]] = i + 1;
    }
    return used;
  }
  function fits(s, idx, r, c, down) {
    var len = s.draft[idx][2], n = s.size;
    if (r < 0 || c < 0 || (down ? r + len : c + len) > n || r >= n || c >= n) return false;
    var used = draftCells(s, idx), cells = shipCells(n, r, c, len, down);
    for (var k = 0; k < cells.length; k++) if (used[cells[k]]) return false;
    return true;
  }
  function draftShipAt(s, cell) {
    var used = draftCells(s, -1);
    return used[cell] ? used[cell] - 1 : -1;
  }

  var BUTTONS = ["shuffle", "turn", "ready"];

  var R = {
    id: "seabattle", STATE_VERSION: 1, passDelay: PASS_DELAY,
    OPTIONS: { fleet: { choices: ["classic", "small"], default: "classic" } },
    init: function (s) {
      var f = FLEETS[s.options.fleet] || FLEETS.classic;
      s.fleetId = FLEETS[s.options.fleet] ? s.options.fleet : "classic";
      s.size = f[0]; s.fleet = f[1].slice();
      s.phase = "place"; s.ships = [null, null]; s.placed = [false, false];
      s.shots = [[], []]; s.res = [{}, {}]; s.sunk = [[], []]; s.shotAt = -100; s.lastShot = null;
      s.draft = null; s.draftSeat = -1; s.dsel = -1; s.theirs = null;
      if (s.cpu >= 0) {                      // the computer's fleet, placed at once
        s.ships[s.cpu] = checkFleet(s, randomFleet(s)); s.placed[s.cpu] = true;
      }
      ensureDraft(s);
    },
    toMove: function (s) {
      if (s.over) return [];
      if (s.phase === "place") { var out = []; for (var i = 0; i < 2; i++) if (!s.placed[i]) out.push(i); return out; }
      return [s.turn];
    },
    moves: function (s, seat) {
      if (s.over || s.phase !== "play" || seat !== s.turn) return [];
      var out = [], done = {};
      for (var i = 0; i < s.shots[seat].length; i++) done[s.shots[seat][i]] = true;
      for (var c = 0; c < s.size * s.size; c++) if (!done[c]) out.push({ shot: c });
      return out;
    },
    legal: function (s, seat, m) {
      if (s.over || !m || typeof m !== "object" || Object.keys(m).length !== 1) return false;
      if ("place" in m) {
        if (s.phase !== "place" || s.placed[seat]) return false;
        try { checkFleet(s, m.place); return true; } catch (e) { return false; }
      }
      if (!("shot" in m) || s.phase !== "play" || seat !== s.turn || !isWhole(m.shot, 0, s.size * s.size - 1)) return false;
      return s.shots[seat].indexOf(m.shot) < 0;
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1) throw new Error("Place your fleet or fire a shot.");
      if ("place" in move) {
        if (s.phase !== "place" || s.placed[seat]) throw new Error("Your fleet is already placed.");
        s.ships[seat] = checkFleet(s, move.place); s.placed[seat] = true;
        if (s.placed[0] && s.placed[1]) { s.phase = "play"; s.turn = s.first; }
        s.draft = null; s.draftSeat = -1;
        ensureDraft(s);
        return [{ type: "placed", seat: seat }];
      }
      if (!("shot" in move)) throw new Error("Place your fleet or fire a shot.");
      if (s.phase !== "play") throw new Error("Both fleets must be placed first.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      var cell = move.shot;
      if (!isWhole(cell, 0, s.size * s.size - 1)) throw new Error("Pick a square of their sea.");
      if (s.shots[seat].indexOf(cell) >= 0) throw new Error("You've already fired there.");
      var other = 1 - seat;
      if (!s.ships[other]) throw new Error("Their fleet isn't known here.");
      var ship = shipAt(s.ships[other], cell);
      return record(s, seat, cell, ship !== null, ship);
    },
    bonus: function (s, seat) { return BONUS_CELL * unhit(s, seat); },
    digest: function (s) { return { ships: s.ships, shots: s.shots, phase: s.phase, turn: s.turn, over: s.over, winner: s.winner }; },
    counts: function (s) { return [afloat(s, 0), afloat(s, 1)]; },
    step: function (s) { ensureDraft(s); return []; },
    cursorStart: function () { return 0; },
    cursorMove: function (s, dir) {
      var n = s.size, N = n * n, cur = s.cursor;
      if (placer(s) >= 0 && cur >= N) {               // on the buttons row
        var b = cur - N;
        if (dir === "left") b = (b + 2) % 3; else if (dir === "right") b = (b + 1) % 3;
        else if (dir === "up") { s.cursor = (n - 1) * n + Math.min(n - 1, b * Math.floor(n / 2)); return null; }
        s.cursor = N + b;
        return null;
      }
      var r = Math.floor(cur / n), c = cur % n;
      if (dir === "up") r = Math.max(0, r - 1);
      else if (dir === "down") { if (r === n - 1 && placer(s) >= 0) { s.cursor = N + Math.min(2, Math.floor(c / (n / 3))); return null; } r = Math.min(n - 1, r + 1); }
      else if (dir === "left") c = Math.max(0, c - 1); else if (dir === "right") c = Math.min(n - 1, c + 1);
      s.cursor = r * n + c;
      return null;
    },
    activate: function (s, seat) {
      if (placer(s) >= 0) return s.cursor >= s.size * s.size ? R.tap(s, { btn: BUTTONS[s.cursor - s.size * s.size] }, seat) : R.tap(s, { cell: s.cursor }, seat);
      return R.tap(s, { cell: s.cursor }, seat);
    },
    alt: function (s, seat) {
      if (placer(s) < 0) return null;
      return R.tap(s, { btn: s.dsel >= 0 ? "turn" : "shuffle" }, seat);
    },
    tap: function (s, t, seat) {
      var p = placer(s);
      if (p >= 0) {
        if (t.btn === "shuffle") { s.draft = randomFleet(s); s.dsel = -1; return { events: [{ type: "shuffle" }] }; }
        if (t.btn === "turn") {
          if (s.dsel < 0) return { events: [{ type: "nope" }] };
          var d = s.draft[s.dsel];
          if (!fits(s, s.dsel, d[0], d[1], 1 - d[3])) return { events: [{ type: "nope" }] };
          d[3] = 1 - d[3];
          return { events: [{ type: "turnship" }] };
        }
        if (t.btn === "ready") { s.dsel = -1; return { move: { place: BK.copy(s.draft) } }; }
        if (t.cell === undefined) return null;
        s.cursor = t.cell;
        var hit = draftShipAt(s, t.cell);
        if (hit >= 0 && hit === s.dsel) return R.tap(s, { btn: "turn" }, seat);
        if (hit >= 0) { s.dsel = hit; return { events: [{ type: "pick" }] }; }
        if (s.dsel < 0) return { events: [{ type: "nope" }] };
        var dd = s.draft[s.dsel], r = Math.floor(t.cell / s.size), c = t.cell % s.size;
        // the tapped square becomes the ship's front end; moved back inside the sea when it would stick out
        if (dd[3]) r = Math.min(r, s.size - dd[2]); else c = Math.min(c, s.size - dd[2]);
        if (!fits(s, s.dsel, r, c, dd[3])) return { events: [{ type: "nope" }] };
        dd[0] = r; dd[1] = c;
        return { events: [{ type: "moveship" }] };
      }
      if (seat < 0 || s.phase !== "play" || t.cell === undefined) return null;
      s.cursor = t.cell;
      if (s.shots[seat].indexOf(t.cell) >= 0) return { events: [{ type: "nope" }] };
      return { move: { shot: t.cell } };
    },
    keep: function (old, s) {
      if (old.draft && old.draftSeat === s.draftSeat && placer(s) >= 0) { s.draft = old.draft; s.dsel = old.dsel; }
      if (s.number > old.number) s.shotAt = s.updates;
    },
    fromView: function (s, v, moves) {
      if (!v || !v.size) return;
      s.phase = v.phase; s.turn = v.turn; s.placed = v.placed.slice(); s.over = !!v.over;
      s.winner = v.winner === null || v.winner === undefined ? null : v.winner;
      s.ships = [null, null];
      if (v.ships) s.ships[s.me] = v.ships;
      var other = 1 - s.me;
      s.shots = [[], []]; s.res = [{}, {}];
      for (var i = 0; i < (v.myShots || []).length; i++) { s.shots[s.me].push(v.myShots[i][0]); s.res[s.me][v.myShots[i][0]] = v.myShots[i][1] ? 1 : 0; }
      for (i = 0; i < (v.atMe || []).length; i++) { s.shots[other].push(v.atMe[i][0]); s.res[other][v.atMe[i][0]] = v.atMe[i][1] ? 1 : 0; }
      s.sunk = [[], []];
      s.sunk[s.me] = (v.sunk || []).slice(); s.sunk[other] = (v.lost || []).slice();
      s.theirs = v.theirShips || null;
      s.ply = s.shots[0].length + s.shots[1].length;
      var last = moves && moves.length ? moves[moves.length - 1] : null;
      if (last && last.move && last.move.shot !== undefined) { s.lastShot = { seat: last.seat - 1, cell: last.move.shot, hit: !!last.move.hit, sunk: last.move.sunk || 0 }; s.lastBy = last.seat - 1; }
      s.draft = null; s.draftSeat = -1;
      ensureDraft(s);
    },
    ai: function (s, seat, level) {
      if (s.phase === "place") return { place: randomFleet(s) };
      return { shot: aiShot(s, seat, level) };
    },
    check: function (d) { return FLEETS[d.fleetId] && Array.isArray(d.ships) && Array.isArray(d.shots) && (d.phase === "place" || d.phase === "play"); },
    summary: function (s, names) {
      return [names[0] + ": " + afloat(s, 0) + " ships afloat · " + names[1] + ": " + afloat(s, 1),
        "Shots: " + s.shots[0].length + " and " + s.shots[1].length];
    },
  };

  function record(s, seat, cell, hit, ship) {
    var other = 1 - seat;
    s.shots[seat].push(cell); s.res[seat][cell] = hit ? 1 : 0;
    s.ply++; s.last = cell; s.shotAt = s.updates;
    var sunk = false;
    if (ship) {
      sunk = true;
      for (var i = 0; i < ship.length; i++) if (!(ship[i] in s.res[seat]) || !s.res[seat][ship[i]]) { sunk = false; break; }
      if (sunk) s.sunk[seat].push(ship.slice());
    }
    s.lastShot = { seat: seat, cell: cell, hit: hit, sunk: sunk ? ship.length : 0 };
    var evs = [{ type: sunk ? "sunk" : hit ? "hit" : "miss", seat: seat, cell: cell }];
    var all = true;
    for (i = 0; i < s.ships[other].length && all; i++) for (var k = 0; k < s.ships[other][i].length; k++) if (!s.res[seat][s.ships[other][i][k]]) { all = false; break; }
    if (all) { s.over = true; s.winner = seat; }
    else s.turn = other;
    return evs;
  }
  /** Ships of `seat` still afloat (from what this screen knows: its own fleet, or the sunk ones counted). */
  function afloat(s, seat) { return s.fleet.length - s.sunk[1 - seat].length; }
  function unhit(s, seat) {
    var n = 0, ships = s.ships[seat] || [], res = s.res[1 - seat];
    for (var i = 0; i < ships.length; i++) for (var k = 0; k < ships[i].length; k++) if (!res[ships[i][k]]) n++;
    return n;
  }

  // ---------- the computer (only the results of its own shots) ----------
  function aiShot(s, seat, level) {
    var n = s.size, res = s.res[seat], sunkCells = {}, i, k;
    for (i = 0; i < s.sunk[seat].length; i++) for (k = 0; k < s.sunk[seat][i].length; k++) sunkCells[s.sunk[seat][i][k]] = true;
    var free = [], open = [];
    for (i = 0; i < n * n; i++) { if (!(i in res)) free.push(i); else if (res[i] && !sunkCells[i]) open.push(i); }
    function nbrs(c) {
      var r = Math.floor(c / n), q = c % n, out = [];
      if (r > 0) out.push(c - n); if (r < n - 1) out.push(c + n); if (q > 0) out.push(c - 1); if (q < n - 1) out.push(c + 1);
      return out;
    }
    if (level <= 1) {
      if (open.length && BK.rand(s) < 0.35) {
        var near = [];
        for (i = 0; i < open.length; i++) { var nb = nbrs(open[i]); for (k = 0; k < nb.length; k++) if (!(nb[k] in res)) near.push(nb[k]); }
        if (near.length) return BK.pick(s, near);
      }
      return BK.pick(s, free);
    }
    if (open.length) {
      // two hits in a line: carry on along it
      var line = [];
      for (i = 0; i < open.length; i++) for (k = 0; k < open.length; k++) {
        var a = open[i], b = open[k];
        if (b === a + 1 && Math.floor(a / n) === Math.floor(b / n)) {
          var lo = a, hi = b; while (lo % n > 0 && res[lo - 1] === 1 && !sunkCells[lo - 1]) lo--; while (hi % n < n - 1 && res[hi + 1] === 1 && !sunkCells[hi + 1]) hi++;
          if (lo % n > 0 && !((lo - 1) in res)) line.push(lo - 1);
          if (hi % n < n - 1 && !((hi + 1) in res)) line.push(hi + 1);
        }
        if (b === a + n) {
          var lo2 = a, hi2 = b; while (lo2 >= n && res[lo2 - n] === 1 && !sunkCells[lo2 - n]) lo2 -= n; while (hi2 < n * (n - 1) && res[hi2 + n] === 1 && !sunkCells[hi2 + n]) hi2 += n;
          if (lo2 >= n && !((lo2 - n) in res)) line.push(lo2 - n);
          if (hi2 < n * (n - 1) && !((hi2 + n) in res)) line.push(hi2 + n);
        }
      }
      if (line.length) return BK.pick(s, line);
      var around = [];
      for (i = 0; i < open.length; i++) { var nb2 = nbrs(open[i]); for (k = 0; k < nb2.length; k++) if (!(nb2[k] in res)) around.push(nb2[k]); }
      if (around.length) return BK.pick(s, around);
    }
    if (level === 2) return BK.pick(s, free);
    // Hard: where the ships still afloat could lie most often (misses and sunk ships rule squares out)
    var left = s.fleet.slice();
    for (i = 0; i < s.sunk[seat].length; i++) { var at = left.indexOf(s.sunk[seat][i].length); if (at >= 0) left.splice(at, 1); }
    var heat = [], best = -1, bestCells = [];
    for (i = 0; i < n * n; i++) heat.push(0);
    for (var L = 0; L < left.length; L++) {
      var len = left[L];
      for (var down = 0; down < 2; down++) for (var r = 0; r + (down ? len - 1 : 0) < n; r++) for (var c = 0; c + (down ? 0 : len - 1) < n; c++) {
        var cells = shipCells(n, r, c, len, down), ok = true;
        for (k = 0; k < cells.length; k++) if (cells[k] in res && (!res[cells[k]] || sunkCells[cells[k]])) { ok = false; break; }
        if (ok) for (k = 0; k < cells.length; k++) heat[cells[k]]++;
      }
    }
    for (i = 0; i < free.length; i++) {
      var h = heat[free[i]];
      if (h > best) { best = h; bestCells = [free[i]]; } else if (h === best) bestCells.push(free[i]);
    }
    return BK.pick(s, bestCells.length ? bestCells : free);
  }

  var L0 = BK.game(R);
  L0.FLEETS = FLEETS; L0.BONUS_CELL = BONUS_CELL; L0.PASS_DELAY = PASS_DELAY; L0.checkFleet = checkFleet; L0.randomFleet = randomFleet;
  L0.shipCells = shipCells; L0.placer = placer; L0.draftShipAt = draftShipAt; L0.afloat = afloat; L0.aiShot = aiShot; L0.BUTTONS = BUTTONS;
  if (typeof module === "object" && module.exports) module.exports = L0; else self.SeaBattleLogic = L0;
})();
