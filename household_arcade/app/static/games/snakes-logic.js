/* Household Arcade — Snakes and Ladders rules (pure: no DOM, deterministic for a seed). The same rules as the
   server's app/rules/snakes.py:

   - A 10 × 10 board, 1–100 (1 at the bottom left, rows going back and forth). Everyone starts off the board (0).
   - Roll and move that many squares; the foot of a ladder climbs it, a snake's head slides down to its tail.
     Several tokens may share a square. No extra rolls.
   - 100 wins. Option `finish`: "any" (a roll past 100 wins too — the default, kind to small children) or "exact"
     (it must land on 100; a roll that would pass it is lost).
   - Two original boards (`board`): "classic" (9 ladders, 10 snakes) and "gentle" (7 ladders, 6 short snakes).

   A move is { go: true } — always the only one, so it plays by itself a moment after the roll. */
(function () {
  "use strict";
  var isNode = typeof module === "object" && module.exports;
  var BK = isNode ? require("./boardkit.js") : self.BoardKit;
  var DK = isNode ? require("./dicekit.js") : self.DiceKit;

  var GOAL = 100, HOP = 5, SLIDE = 26, BONUS_SQUARE = 5;
  var BOARDS = {
    classic: {
      ladders: { 3: 18, 6: 16, 14: 34, 20: 39, 28: 52, 36: 56, 54: 70, 55: 73, 75: 87 },
      snakes: { 26: 17, 38: 23, 58: 45, 62: 41, 77: 64, 79: 57, 89: 72, 94: 66, 96: 85, 99: 83 },
    },
    gentle: {
      ladders: { 4: 24, 9: 30, 23: 43, 39: 64, 41: 59, 51: 68, 70: 90 },
      snakes: { 15: 7, 28: 16, 47: 36, 54: 46, 85: 75, 97: 86 },
    },
  };
  var NAMES = ["Red", "Blue", "Green", "Yellow"];

  function jump(board, sq) {
    var b = BOARDS[board];
    if (b.ladders[sq]) return b.ladders[sq];
    if (b.snakes[sq]) return b.snakes[sq];
    return sq;
  }
  function land(s, pos, r) {
    var np = pos + r;
    if (np > GOAL) np = s.finish === "any" ? GOAL : pos;
    return [np, jump(s.board, np)];
  }
  function rollOf(s, seat) {
    var r = s._roll;
    if (!r || r.seat !== seat + 1 || typeof r.value !== "number" || Math.floor(r.value) !== r.value || r.value < 1 || r.value > 6) throw new Error("Roll the dice first.");
    return r.value;
  }

  var R = {
    id: "snakes", STATE_VERSION: 1, PLAYERS: 2,
    OPTIONS: { board: { choices: ["classic", "gentle"], default: "classic" }, finish: { choices: ["any", "exact"], default: "any" } },
    init: function (s) {
      s.board = BOARDS[s.options.board] ? s.options.board : "classic";
      s.finish = s.options.finish === "exact" ? "exact" : "any";
      s.pos = [];
      for (var i = 0; i < s.n; i++) s.pos.push(0);
      s.anim = null;
    },
    legal: function (s, seat) { return s.over || seat !== s.turn ? [] : [{ go: true }]; },
    forced: function (s, seat) {
      try { rollOf(s, seat); } catch (e) { return null; }
      return { go: true };
    },
    apply: function (s, seat, move) {
      if (s.over) throw new Error("The game is over.");
      if (seat !== s.turn) throw new Error("It isn't your move.");
      var r = rollOf(s, seat);
      if (!move || typeof move !== "object" || Object.keys(move).length !== 1 || move.go !== true) throw new Error("A move is {go: true}.");
      delete s._roll;
      var from = s.pos[seat], l = land(s, from, r);
      s.pos[seat] = l[1];
      s.anim = { seat: seat, from: from, mid: l[0], to: l[1], at: s.updates };
      var evs = [{ type: l[0] === from ? "stay" : "hop", seat: seat }];
      if (l[1] > l[0]) evs.push({ type: "ladder", seat: seat });
      else if (l[1] < l[0]) evs.push({ type: "snake", seat: seat });
      s.note = l[0] === from && r ? "Needs exactly " + (GOAL - from) + " to finish" : "";
      s.noteAt = s.updates;
      if (l[1] === GOAL) { s.over = true; s.winner = seat; }
      else s.turn = (seat + 1) % s.n;
      return evs;
    },
    animLength: function (s) {
      var a = s.anim;
      if (!a) return 0;
      return (a.mid - a.from) * HOP + (a.to !== a.mid ? SLIDE : 0) + 6;
    },
    ai: function () { return { go: true }; },
    digest: function (s) { return { turn: s.turn, pos: s.pos, over: s.over, winner: s.winner }; },
    bonus: function (s, seat) {
      var best = 0;
      for (var o = 0; o < s.n; o++) if (o !== seat && s.pos[o] > best) best = s.pos[o];
      return BONUS_SQUARE * (GOAL - best);
    },
    cpuBase: function () { return 100; },
    seatName: function (s, seat) { return NAMES[seat]; },
    summary: function (s, names) {
      var parts = [];
      for (var i = 0; i < s.n; i++) parts.push(names[i] + " " + s.pos[i]);
      return [parts.join(" · ")];
    },
    check: function (d) { return Array.isArray(d.pos) && d.pos.length === d.n && !!BOARDS[d.board]; },
    tap: function () { return null; },
  };

  /** Square n's cell: [column 0–9, row 0–9 from the top]. */
  function cell(n) {
    var r = Math.floor((n - 1) / 10), c = (n - 1) % 10;
    return [r % 2 ? 9 - c : c, 9 - r];
  }

  var L = DK.game(R);
  L.BOARDS = BOARDS; L.GOAL = GOAL; L.HOP = HOP; L.SLIDE = SLIDE; L.cell = cell; L.jump = jump; L.NAMES = NAMES; L.land = land;
  if (isNode) module.exports = L; else self.SnakesLogic = L;
})();
