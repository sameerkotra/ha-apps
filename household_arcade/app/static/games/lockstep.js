/* Household Arcade — lockstep for live duels on two phones (window.ArcadeLockstep; spec/SPEC.md §13.4).

   Both phones run the same game rules from the same seed. Each phone sends only its own inputs, stamped with
   the update ("tick") they are played on, `delay` ticks after they were made; a tick is played only when both
   players' inputs for it are known, so both games apply every input on the same tick and stay identical.

   This file is the bookkeeping, with no network and no DOM (it runs in Node for the tests):
   - local(action, value): an input made on this phone, played at tick + delay;
   - receive(msg): the other phone's input messages (relayed by the server, numbered by the sender's `seq`; a
     duplicate is ignored, a message that comes early waits for the ones before it), and the server's acks;
   - next(): the inputs of the next tick, ordered seat 1 then seat 2 (each in the order they were made), or null
     while the other phone's inputs for it haven't arrived (the game waits);
   - advance(sumFn): that tick was played; every SUM_EVERY ticks the game's checksum goes out with the inputs;
   - flush(): what this phone has to say goes out as one message {t:"in", seq, at, upto, ev, sum?}: `at` is the
     tick this phone is at, `upto` the last tick whose inputs from this phone are final (no input can be added at
     or before it any more). The game calls it once a frame: it sends when there is something new to say.
   Messages are kept until the server acknowledges them and are sent again after a reconnection (resend()).

   The delay adapts to the connection. The input delay is this phone's own: the server's `delay` is the least it
   can be; an input is played `delay` ticks after it is made, and `upto` runs `delay - 1` ticks ahead of the phone.
   The two phones' delays added together are the time a word has to travel round: when this phone finds itself
   waiting for the other (next() gives null) in a few ticks of a second, it raises its own delay by one, which
   lets the other phone run further ahead and so stall less, and the other does the same — a slow link costs a
   little input lag rather than a game that stops and starts; after ten clean seconds a step goes back.

   Wire format of an input: [tick, action, value] — value 1/0 for a button pressed/let go, or a small whole
   number (Paddle Duel's "aim": where the finger is, Tank Battle's "steer": the direction toward the finger). */
(function () {
  "use strict";

  var SUM_EVERY = 60;          // a checksum every second of play
  var MAX_PER_TICK = 6;        // inputs one player can put on one tick (the server allows 8)
  var MAX_EV = 32;             // inputs in one message (the server's limit)
  var MAX_HELD = 4000;         // remote messages waiting for an earlier one (more = something is badly wrong)
  var MAX_DELAY = 30;          // the input delay can grow to this (half a second)
  var RAISE_STALLS = 3;        // this many waits within RAISE_WINDOW ticks …
  var RAISE_WINDOW = 60;       // … (a second) raise the delay by one (by two from RAISE_HARD waits)
  var RAISE_HARD = 8;
  var RAISE_COOL = 30;         // then no more for half a second (the other phone has to hear of it: a round trip)
  var LOWER_AFTER = 600;       // ten clean seconds lower it by one

  /** FNV-1a over a string, as an unsigned 32-bit number: the rules' checksums. */
  function hash(str) {
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return h >>> 0;
  }
  /** A checksum of plain data (numbers, strings, booleans, arrays, objects with their keys in a fixed order). */
  function sum(value) { return hash(JSON.stringify(value)); }

  function create(o) {
    o = o || {};
    var seat = o.seat === 2 ? 2 : 1, other = 3 - seat;
    var delay0 = Math.max(2, Math.min(MAX_DELAY, (o.delay | 0) || 4));   // the agreed delay: the least
    var delay = delay0, adapt = o.adapt !== false;
    var send = typeof o.send === "function" ? o.send : function () {};
    var sumEvery = o.sumEvery || SUM_EVERY;
    var t = 0;                              // ticks played
    var upto = {}; upto[1] = delay - 1; upto[2] = delay - 1;   // ticks 1 … delay-1 never have inputs
    var at = {}; at[1] = 0; at[2] = 0;      // the tick each phone was at, by its last word
    var frames = {}; frames[1] = {}; frames[2] = {};            // seat → tick → [[action, value], …]
    var outEv = [];                         // my inputs not sent yet: [tick, action, value]
    var sentUpto = delay - 1, sentAt = -1, seq = 0, unacked = [], pendingSum = null;
    var rSeq = 0, held = {}, heldN = 0;     // the other's messages: last applied seq, and those waiting for a gap
    var waitSince = null, lastNext = null, stats = { sent: 0, received: 0, dup: 0, early: 0, resent: 0, raised: 0, lowered: 0 };
    var stallTicks = [], coolUntil = 0, cleanSince = 0, stalledAt = -1;   // the delay's bookkeeping

    function frame(s, tick) { return frames[s][tick] || (frames[s][tick] = []); }

    /** The last tick this phone's inputs are final up to: `delay - 1` ahead, and never behind what was said. */
    function horizon() { return Math.max(t + delay - 1, sentUpto); }

    /** An input made on this phone now: played at tick t + delay on both phones (later, if that tick was already
        given as final after the delay came down). */
    function local(action, value) {
      if (typeof action !== "string") return false;
      var tick = horizon() + 1, f = frame(seat, tick);
      value = value === true ? 1 : value === false || value == null ? 0 : Math.round(Number(value)) || 0;
      // an aim/steer that changes again before it is sent replaces the last one on the same tick
      for (var i = outEv.length - 1; i >= 0; i--) {
        var e = outEv[i];
        if (e[0] !== tick) break;
        if (e[1] === action && (action === "aim" || action === "steer")) {
          e[2] = value;
          for (var k = f.length - 1; k >= 0; k--) if (f[k][0] === action) { f[k][1] = value; break; }
          return true;
        }
      }
      if (f.length >= MAX_PER_TICK) return false;
      f.push([action, value]);
      outEv.push([tick, action, value]);
      return true;
    }

    /** Send what is final: inputs up to the horizon, with a checksum when one is due. Nothing new: nothing sent. */
    function flush(force) {
      var u = horizon();
      var ready = [];
      while (outEv.length && outEv[0][0] <= u && ready.length < MAX_EV) ready.push(outEv.shift());
      // more than one message's worth: this one stops before the first tick it can't carry whole (a message
      // claims every input of the ticks up to its `upto`)
      if (outEv.length && outEv[0][0] <= u) {
        var cut = outEv[0][0];
        while (ready.length && ready[ready.length - 1][0] === cut) outEv.unshift(ready.pop());
        u = cut - 1;
      }
      if (u <= sentUpto && t <= sentAt && !ready.length && pendingSum === null && !force) return null;
      var msg = { t: "in", seq: ++seq, at: t, upto: Math.max(u, sentUpto), ev: ready };
      // a checksum goes with the message that is final up to that tick's inputs and the agreed delay after it
      if (pendingSum !== null && pendingSum[0] <= msg.upto - delay0 + 1) { msg.sum = pendingSum; pendingSum = null; }
      sentUpto = msg.upto; sentAt = t;
      unacked.push(msg);
      stats.sent++;
      send(msg);
      return msg;
    }

    function apply(msg) {
      for (var i = 0; i < msg.ev.length; i++) {
        var e = msg.ev[i];
        if (e[0] > t) frame(other, e[0]).push([e[1], e[2]]);
      }
      if (msg.upto > upto[other]) upto[other] = msg.upto;
      if (typeof msg.at === "number" && msg.at > at[other]) at[other] = msg.at;
    }

    /** Messages from the server: the other phone's inputs, acks, and the welcome after a (re)connection. */
    function receive(msg) {
      if (!msg || typeof msg !== "object") return;
      if (msg.t === "in" && msg.seat === other) {
        stats.received++;
        if (typeof msg.seq !== "number" || msg.seq <= rSeq || held[msg.seq]) { stats.dup++; return; }
        if (!Array.isArray(msg.ev) || typeof msg.upto !== "number") return;
        if (msg.seq > rSeq + 1) {                 // came early: wait for the ones before it
          if (heldN < MAX_HELD) { held[msg.seq] = msg; heldN++; stats.early++; }
          return;
        }
        apply(msg); rSeq = msg.seq;
        while (held[rSeq + 1]) { var m = held[rSeq + 1]; delete held[rSeq + 1]; heldN--; apply(m); rSeq = m.seq; }
      } else if (msg.t === "ack") {
        ackTo(msg.seq);
      } else if (msg.t === "welcome") {
        // the server has my messages up to inSeq: anything after it goes again (same seq, so never twice)
        if (typeof msg.inSeq === "number") { ackTo(msg.inSeq); resend(); }
      } else if (msg.t === "error" && msg.why === "gap" && typeof msg.inSeq === "number") {
        // a message reached the server before the one before it (which it dropped): everything after inSeq again
        ackTo(msg.inSeq); resend();
      }
    }
    function ackTo(n) {
      if (typeof n !== "number") return;
      while (unacked.length && unacked[0].seq <= n) unacked.shift();
    }
    function resend() {
      for (var i = 0; i < unacked.length; i++) { stats.resent++; send(unacked[i]); }
    }

    /** The inputs of tick t + 1 — [[seat, action, value], …] — or null while they aren't all known. */
    function next(nowMs) {
      var n = t + 1;
      // my own inputs are always final up to t + delay - 1 (an input made now is played at t + delay)
      if (upto[other] < n) {
        if (waitSince === null) waitSince = nowMs == null ? 0 : nowMs;
        lastNext = nowMs;
        if (stalledAt !== n) { stalledAt = n; stallTicks.push(n); }     // one wait a tick, however many frames
        return null;
      }
      waitSince = null;
      var out = [], s, f, i;
      for (s = 1; s <= 2; s++) {
        f = frames[s][n];
        if (f) for (i = 0; i < f.length; i++) out.push([s, f[i][0], f[i][1]]);
      }
      return out;
    }
    /** Tick t + 1 has been played. sumFn() is the game's checksum, asked for every SUM_EVERY ticks. */
    function advance(sumFn) {
      t++;
      delete frames[1][t]; delete frames[2][t];
      if (t % sumEvery === 0 && typeof sumFn === "function") pendingSum = [t, sumFn() >>> 0];
      if (adapt) adjust();
      return t;
    }
    /** The delay follows the connection: up a step after RAISE_STALLS waits in a second, down after a clean while. */
    function adjust() {
      while (stallTicks.length && stallTicks[0] <= t - RAISE_WINDOW) stallTicks.shift();
      if (stallTicks.length) cleanSince = t;
      if (stallTicks.length >= RAISE_STALLS && t >= coolUntil && delay < MAX_DELAY) {
        delay = Math.min(MAX_DELAY, delay + (stallTicks.length >= RAISE_HARD ? 2 : 1)); stats.raised++;
        stallTicks = []; coolUntil = t + RAISE_COOL; cleanSince = t;
      } else if (delay > delay0 && t - cleanSince >= LOWER_AFTER) {
        delay--; stats.lowered++;
        cleanSince = t;
      }
    }

    return {
      get tick() { return t; },
      get seat() { return seat; },
      get delay() { return delay; },
      get upto() { var u = { 1: upto[1], 2: upto[2] }; u[seat] = horizon(); return u; },
      get delay0() { return delay0; },
      get unacked() { return unacked.length; },
      get stats() { return stats; },
      local: local, flush: flush, receive: receive, next: next, advance: advance, resend: resend,
      /** How long (ms, by the clock given to next()) the game has been waiting for the other phone; 0 if it isn't. */
      waiting: function (nowMs) { return waitSince === null ? 0 : Math.max(0, (nowMs == null ? lastNext : nowMs) - waitSince); },
      /** How many ticks the other phone is ahead of this one (from what it has said): > 2 means catch up. */
      behind: function () { return Math.max(0, at[other] - t); },
      /** The final word at the end of the game (my inputs are complete up to here and beyond). */
      finish: function () { return flush(true); },
    };
  }

  /** Taking turns live (Carrom, SPEC §13.4): each move ("shot") is one input, numbered 1, 2, 3 … It is sent once and
      played on both phones only when it comes back from the server (the server's order is the only order, so a weak
      shot the server played for a player who ran out of time simply takes that number); after a shot has stopped
      each phone reports its checksum and whose turn is next. `local("shot", value)` sends this phone's shot (once for
      a number); `nextShot()` gives the next shot to play ([seat, "shot", value]) or null; `report(k, sum, next)`. */
  function createTurns(o) {
    o = o || {};
    var seat = o.seat === 2 ? 2 : 1;
    var send = typeof o.send === "function" ? o.send : function () {};
    var seq = 0, unacked = [], shots = {}, played = 0, mine = {}, stats = { sent: 0, received: 0, dup: 0, resent: 0, late: 0, timer: 0 };
    function out(msg) { unacked.push(msg); stats.sent++; send(msg); return msg; }
    function local(action, value) {
      if (action !== "shot" || !Array.isArray(value)) return false;
      var k = played + 1;
      if (mine[k]) return false;
      mine[k] = true;
      out({ t: "in", seq: ++seq, upto: k, ev: [[k, "shot", value.slice()]] });
      return true;
    }
    function report(k, sumValue, next) { return out({ t: "in", seq: ++seq, upto: k, ev: [], sum: [k, sumValue >>> 0], next: next }); }
    function ackTo(n) { if (typeof n !== "number") return; while (unacked.length && unacked[0].seq <= n) unacked.shift(); }
    function resend() { for (var i = 0; i < unacked.length; i++) { stats.resent++; send(unacked[i]); } }
    function receive(msg) {
      if (!msg || typeof msg !== "object") return;
      if (msg.t === "in") {
        stats.received++;
        var e = Array.isArray(msg.ev) ? msg.ev[0] : null, k = msg.upto;
        if (!e || e[1] !== "shot" || typeof k !== "number" || e[0] !== k || (msg.seat !== 1 && msg.seat !== 2)) return;
        if (k <= played || shots[k]) { stats.dup++; return; }
        if (msg.timer) stats.timer++;
        shots[k] = [msg.seat, "shot", e[2]];
      } else if (msg.t === "ack") {
        if (msg.late) stats.late++;
        ackTo(msg.seq);
      } else if (msg.t === "welcome" || (msg.t === "error" && msg.why === "gap")) {
        if (typeof msg.inSeq === "number") { ackTo(msg.inSeq); resend(); }
      }
    }
    function nextShot() {
      var sh = shots[played + 1];
      if (!sh) return null;
      delete shots[played + 1];
      played++;
      return sh;
    }
    return {
      turns: true,
      get tick() { return played; },
      get seat() { return seat; },
      get delay() { return 0; },
      get unacked() { return unacked.length; },
      get stats() { return stats; },
      local: local, report: report, receive: receive, nextShot: nextShot, resend: resend,
      flush: function () { return null; }, finish: function () { return null; }, advance: function () { return played; },
      next: function () { return null; }, waiting: function () { return 0; }, behind: function () { return 0; },
    };
  }

  var ArcadeLockstep = { create: create, createTurns: createTurns, hash: hash, sum: sum, SUM_EVERY: SUM_EVERY, MAX_PER_TICK: MAX_PER_TICK,
    MAX_EV: MAX_EV, MAX_DELAY: MAX_DELAY, RAISE_STALLS: RAISE_STALLS, RAISE_WINDOW: RAISE_WINDOW, RAISE_COOL: RAISE_COOL, LOWER_AFTER: LOWER_AFTER };
  if (typeof module === "object" && module.exports) module.exports = ArcadeLockstep; else self.ArcadeLockstep = ArcadeLockstep;
})();
