/* Household Arcade — what wave 8's dice games share (window.DiceKit, or module.exports in Node).

   Ludo and Snakes and Ladders are games for 2–4 players with a die, played three ways: you and the computer (the
   other seats), everyone on one phone (pass and play; optionally the computer fills the empty seats up to four), and
   turn by turn from 2–4 phones (SPEC §13.5) — then the server rolls the die (POST /api/matches/{id}/roll), checks
   every move with its Python rules (app/rules/<game>.py) and plays a move the roll leaves no choice about at once.

   - DiceKit.game(R) turns a game's rules R into its Logic (pure: no DOM, deterministic for a seed: on one phone the
     die comes from the seed; no Math.random or Date): modes, whose turn, rolling, the computer's moves after a short
     pause, moves the roll leaves no choice about played by themselves, the keyboard cursor, scores, saving, and the
     turn-by-turn picture from the server (fromTurns / sync).
   - DiceKit.session(canvas, opts, spec) is the drawing side both game files share (browser only): the die and the
     line under the board, the end banner, the roll sent to the server; the game draws its board and players.

   R (a game's rules, the same as the server's): { id, STATE_VERSION, OPTIONS: {id: {choices, default}}, PLAYERS
   (the start screen's default), init(s), legal(s, seat, roll) → moves, apply(s, seat, move) → events (reads the roll
   from s._roll as the server does; throws on an illegal move), forced(s, seat) → a move or null, ai(s, seat) → move,
   digest(s), bonus(s, seat), cpuBase(s), animLength(s), seatName(s, seat), summary(s, names), cursorMove(s, dir, seat),
   activate(s, seat) → { move } | null, tap(s, target, seat) → { move } | { events } | null, check(saved). */
(function (root) {
  "use strict";
  var BK = typeof module === "object" && module.exports ? require("./boardkit.js") : root.BoardKit;

  var CPU_WAIT = 32, FORCED_WAIT = 40, ROLL_SHOW = 18, STATE_VERSION = 1, PHONES_BASE = 500;
  var MODES = [
    { id: "cpu", label: "You and the computer" },
    { id: "pass", label: "One phone (pass and play)" },
    { id: "phones", label: "Phones (turn by turn, 2–4)" },
  ];
  var KIT_OPTIONS = {
    players: { choices: ["2", "3", "4"] },
    fill: { choices: ["no", "yes"], default: "no" },
  };

  function modeOk(id) { for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return true; return false; }

  function game(R) {
    var SPECS = {};
    SPECS.players = { choices: KIT_OPTIONS.players.choices, default: String(R.PLAYERS || 2) };
    SPECS.fill = KIT_OPTIONS.fill;
    for (var k0 in (R.OPTIONS || {})) SPECS[k0] = R.OPTIONS[k0];

    function options(o) {
      var out = {};
      for (var k in SPECS) {
        var spec = SPECS[k], v = o && o[k] !== undefined && o[k] !== null ? String(o[k]) : null;
        out[k] = v !== null && spec.choices.indexOf(v) >= 0 ? v : spec.default;
      }
      return out;
    }
    function toMove(s) { return s.over ? [] : [s.turn]; }
    function legal(s, seat, move) {
      if (!s.rolled || seat !== s.turn) return false;
      var want = BK.canonical(move), ms = R.legal(s, seat, s.rolled);
      for (var i = 0; i < ms.length; i++) if (BK.canonical(ms[i]) === want) return true;
      return false;
    }

    function create(o) {
      o = o || {};
      var mode = modeOk(o.mode) ? o.mode : "cpu", seed = (Number(o.seed) >>> 0) || 1, phones = mode === "phones";
      var opt = options(o.options), n, people = [], cpu = [], i;
      if (phones) {
        n = Math.max(2, Math.min(4, Number(o.players) || 2));
        for (i = 0; i < n; i++) { people.push(i === (o.seat | 0)); cpu.push(false); }
      } else if (mode === "cpu") {
        n = Number(opt.players);
        for (i = 0; i < n; i++) { people.push(i === 0); cpu.push(i !== 0); }
      } else {
        var humans = Number(opt.players);
        n = opt.fill === "yes" ? 4 : humans;
        for (i = 0; i < n; i++) { people.push(i < humans); cpu.push(i >= humans); }
      }
      var s = {
        game: R.id, mode: mode, seed: seed, rng: seed, n: n, first: phones ? seed % n : 0, over: false, winner: null,
        ply: 0, people: people, cpu: cpu, left: cpu.map(function () { return false; }), me: phones ? (o.seat | 0) : 0,
        options: opt, updates: 0, wait: 0, rolled: 0, rollAt: -1000, lastRoll: 0, lastRollBy: -1, cursor: -1,
        sending: false, number: 0, msg: "", msgAt: -1000, lastBy: -1, overAt: 0, busy: 0, note: "", noteAt: -1000,
      };
      s.turn = s.first;
      R.init(s);
      return s;
    }

    /** The seat the person (or people) at this screen may act for now, or −1. */
    function mySeat(s) {
      if (s.over || s.busy > 0) return -1;
      if (s.mode === "phones") return !s.sending && s.turn === s.me ? s.me : -1;
      return s.people[s.turn] ? s.turn : -1;
    }

    function endEvents(s, evs) {
      if (!s.over) return;
      if (s.mode === "pass") { evs.push({ type: "win", winner: s.winner }); return; }
      var me = s.mode === "phones" ? s.me : 0;
      evs.push({ type: s.winner === me ? "win" : "lose", winner: s.winner });
    }

    function roll(s) {
      var v = 1 + Math.floor(BK.rand(s) * 6);
      s.rolled = v; s._roll = { seat: s.turn + 1, value: v }; s.rollAt = s.updates; s.lastRoll = v; s.lastRollBy = s.turn;
      s.wait = 0; s.cursor = -1;
      return [{ type: "roll", value: v, seat: s.turn }];
    }

    /** Play a move for `seat` with the roll on the table. Phones: the move is sent, not played. */
    function play(s, seat, move) {
      if (!legal(s, seat, move)) { s.msg = "nope"; s.msgAt = s.updates; return [{ type: "nope" }]; }
      if (s.mode === "phones") { s.sending = true; return [{ type: "send", move: move }]; }
      var evs = R.apply(s, seat, move) || [];
      s.rolled = 0; s.ply++; s.lastBy = seat; s.wait = 0; s.cursor = -1;
      s.busy = R.animLength ? R.animLength(s) : 0;
      evs.unshift({ type: "move", seat: seat });
      if (s.over) s.overAt = s.updates + s.busy;
      endEvents(s, evs);
      return evs;
    }

    function step(s) {
      var evs = [];
      s.updates++;
      if (s.busy > 0) { s.busy--; return evs; }
      if (s.over || s.mode === "phones") return evs;      // from phones the server rolls and plays the computer's seats
      var seat = s.turn;
      if (!s.rolled) {
        if (s.cpu[seat] && ++s.wait >= CPU_WAIT) evs = roll(s);
        return evs;
      }
      var f = R.forced(s, seat);
      if ((f || s.cpu[seat]) && s.updates - s.rollAt >= (f ? FORCED_WAIT : CPU_WAIT)) evs = play(s, seat, f || R.ai(s, seat));
      return evs;
    }

    function rollNow(s) {
      if (s.mode === "phones") { s.sending = true; return [{ type: "rollsend" }]; }
      return roll(s);
    }
    /** The shell's actions: fire rolls (or plays the token in the cursor); the arrows move the cursor. */
    function press(s, action, down) {
      if (!down || s.over) return [];
      var seat = mySeat(s);
      if (seat < 0) return [];
      if (!s.rolled) return action === "fire" ? rollNow(s) : [];
      if (R.forced(s, seat)) return [];                    // it plays by itself in a moment
      if (action === "up" || action === "down" || action === "left" || action === "right") {
        if (R.cursorMove) R.cursorMove(s, action, seat);
        return [{ type: "cursor" }];
      }
      if (action === "fire" && R.activate) {
        var r = R.activate(s, seat);
        return r && r.move ? play(s, seat, r.move) : r && r.events ? r.events : [];
      }
      return [];
    }
    /** A tap: `target` is what the game file found under the finger ("die", a token …, or "board"). */
    function tap(s, target) {
      if (s.over) return [];
      var seat = mySeat(s);
      if (seat < 0) return [];
      if (!s.rolled) return rollNow(s);
      if (R.forced(s, seat)) return [];
      var r = R.tap ? R.tap(s, target, seat) : null;
      return r && r.move ? play(s, seat, r.move) : r && r.events ? r.events : [];
    }

    function score(s, seat) {
      if (!s.over || s.mode === "pass") return 0;
      if (seat === undefined) seat = s.mode === "phones" ? s.me : 0;
      var base = s.mode === "phones" ? PHONES_BASE : R.cpuBase(s);
      return BK.marginScore(s.winner === seat, false, base, R.bonus(s, seat));
    }

    function names(s, given) {
      var out = [];
      for (var i = 0; i < s.n; i++) {
        var nm;
        if (s.mode === "phones") nm = first(given && given[i]) || "Player " + (i + 1);
        else if (s.mode === "cpu") nm = i === 0 ? "You" : s.n === 2 ? "Computer" : "Computer " + i;
        else nm = s.cpu[i] ? "Computer" : R.seatName(s, i);
        out.push(nm);
      }
      return out;
    }
    function first(n) { return String(n || "").trim().split(" ")[0]; }

    function result(s, nm) {
      nm = nm || names(s);
      var me = s.mode === "phones" ? s.me : 0;
      var won = s.over && s.mode !== "pass" && s.winner === me, lost = s.over && s.mode !== "pass" && !won;
      var lines = R.summary ? R.summary(s, nm) || [] : [];
      var note;
      if (s.mode === "pass") note = "Everyone on one phone — not saved";
      else if (lost) note = "Not a win — not saved";
      return { score: score(s), level: 1,
        stats: { won: won, lost: lost, draw: false, winner: s.winner, mode: s.mode, moves: s.ply, players: s.n,
          summary: lines.length ? lines : undefined, notSaved: note } };
    }

    function save(s) { return BK.copy(s); }
    function restore(data) {
      var ok = data && typeof data === "object" && data.game === R.id && modeOk(data.mode) && data.mode !== "phones" &&
        typeof data.turn === "number" && typeof data.rng === "number" && typeof data.n === "number" &&
        Array.isArray(data.cpu) && (!R.check || R.check(data));
      if (!ok) throw new Error("That saved game can't be continued.");
      var s = BK.copy(data);
      s.sending = false; s.busy = 0;
      return s;
    }

    /** The match as the server sends it (GET /api/matches/{id}/turns): the moves replayed with the server's rolls. */
    function fromTurns(p) {
      var players = p.players || [];
      var s = create({ mode: "phones", seed: p.seed, options: p.options, seat: (p.seat || 1) - 1, players: Math.max(2, players.length) });
      var list = p.moves || [];
      for (var i = 0; i < list.length; i++) {
        var mv = list[i], seat = mv.seat - 1;
        s._roll = { seat: mv.seat, value: mv.dice };
        R.apply(s, seat, mv.move);
        s.ply++; s.lastBy = seat; s.lastRoll = mv.dice; s.lastRollBy = seat;
      }
      for (i = 0; i < players.length; i++) if (players[i].seat >= 1 && players[i].seat <= s.n) s.left[players[i].seat - 1] = !!(players[i].left || players[i].computer);
      s.rolled = 0;
      if (p.roll && p.roll.seat === p.seat && !s.over) {
        s.rolled = p.roll.value; s._roll = { seat: p.roll.seat, value: p.roll.value }; s.lastRoll = p.roll.value; s.lastRollBy = s.me;
      }
      s.number = p.number || list.length;
      if (s.anim) s.anim.at = -1000;
      if (p.over && p.result) {                     // the server's word on the end (resigned, timeout, or the rules'
        var w = p.result.winner === null || p.result.winner === 0 ? -1 : p.result.winner - 1;      // winner was a seat
        if (s.over && s.winner !== w) s.firstHome = s.winner;                // the computer played: the best-placed person wins)
        s.over = true;
        s.winner = w;
      }
      s.endReason = p.over ? p.endReason || null : null;
      return s;
    }
    /** A new picture from the server: the new state (keeping this phone's cursor and clock), and the events to play. */
    function sync(old, p) {
      var s = fromTurns(p), evs = [];
      if (old) {
        s.updates = old.updates; s.cursor = old.cursor;
        if (s.number > old.number) {
          if (s.anim) s.anim.at = s.updates;
          s.busy = R.animLength ? R.animLength(s) : 0;
          evs.push({ type: "move", seat: s.lastBy });
        }
        if (s.rolled && !old.rolled) { s.rollAt = s.updates; evs.push({ type: "roll", value: s.rolled, seat: s.me }); }
        else if (s.number > old.number && s.lastRoll) s.rollAt = s.updates - ROLL_SHOW;
        if (s.over && !old.over) { s.overAt = s.updates + s.busy; endEvents(s, evs); }
      }
      return { s: s, events: evs };
    }

    var L = {
      UPS: 60, STATE_VERSION: R.STATE_VERSION || STATE_VERSION, MODES: MODES, PHONES_BASE: PHONES_BASE, CPU_WAIT: CPU_WAIT,
      FORCED_WAIT: FORCED_WAIT, ROLL_SHOW: ROLL_SHOW, R: R, OPTION_SPECS: SPECS,
      create: create, step: step, press: press, tap: tap, play: play, roll: roll, legal: legal, toMove: toMove, mySeat: mySeat,
      score: score, result: result, names: names, save: save, restore: restore, fromTurns: fromTurns, sync: sync, options: options,
      digest: function (s) { return R.digest(s); },
      /** Roll (from the seed) and play for whoever's turn it is, without the screen's checks: the tests' way in. */
      force: function (s, move) {
        var seat = s.turn;
        if (!s.rolled) roll(s);
        var m = move || R.forced(s, seat) || R.ai(s, seat);
        var evs = R.apply(s, seat, m); s.rolled = 0; s.ply++; s.lastBy = seat; return evs;
      },
    };
    return L;
  }

  // =====================================================================
  // The drawing side (browser)
  // =====================================================================
  var PIPS = { 1: [[0, 0]], 2: [[-1, -1], [1, 1]], 3: [[-1, -1], [0, 0], [1, 1]], 4: [[-1, -1], [1, -1], [-1, 1], [1, 1]],
    5: [[-1, -1], [1, -1], [0, 0], [-1, 1], [1, 1]], 6: [[-1, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [1, 1]] };

  /** A die face at (x, y) (top left), `size` across. */
  function drawDie(g, x, y, size, value, o) {
    o = o || {};
    var lcd = g.kind === "lcd";
    // a light face with dark pips in every look (Retro LCD: an outlined face)
    var face = BK.toneCi(g, false), pip = BK.toneCi(g, true);
    if (g.kind === "neon") { face = 8; pip = 0; }
    if (lcd) g.rect(x, y, size, size, 9, {});
    else g.rect(x, y, size, size, face, { r: size / 5, solid: true, stroke: true });
    if (o.ring) g.ring(x + size / 2, y + size / 2, size * 0.78, lcd ? 0 : 7, 2.5, { a: 0.95 });
    var pts = PIPS[value] || [];
    var r = Math.max(1.6, size * 0.09), d = size * 0.27;
    for (var i = 0; i < pts.length; i++) g.circle(x + size / 2 + pts[i][0] * d, y + size / 2 + pts[i][1] * d, r, lcd ? 0 : pip, { solid: true });
  }

  /** spec: { Logic, drawBoard(g, s, info, V), hit(s, lx, ly) → target, sounds, chooseText(s), noteText(s) } */
  function session(canvas, opts, spec) {
    var Kit = root.ArcadeKit, Logic = spec.Logic;
    opts = opts || {};
    var s = null, pending = [], turns = opts.turns || null, err = "", errAt = -1000;

    function names() { return Logic.names(s, opts.names); }
    function handle(evs) {
      for (var i = 0; i < evs.length; i++) {
        var ev = evs[i], sent = s;
        if (ev.type === "send" && turns && turns.send) {
          Promise.resolve(turns.send(ev.move)).then(null, fail(sent, "That move wasn't taken."));
        } else if (ev.type === "rollsend" && turns && turns.roll) {
          Promise.resolve(turns.roll()).then(null, fail(sent, "The roll didn't go through — try again."));
        }
        pending.push(ev);
      }
    }
    function fail(sent, text) {
      return function (e) { if (s === sent) { s.sending = false; err = (e && e.message) || text; errAt = s.updates; } };
    }

    var impl = {
      init: function (seed) {
        s = turns ? Logic.fromTurns(turns.picture) : Logic.create({ mode: opts.mode, seed: seed, options: opts.options });
        pending = [];
      },
      save: turns ? undefined : function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return 1; },
      isOver: function () { return s.over && s.updates - s.overAt >= 80; },
      result: function () { return Logic.result(s, names()); },
      sounds: Object.assign({ roll: "launch", move: "turn", cursor: null, nope: "wall", win: "win", lose: "gameover" }, spec.sounds || {}),
      input: function (action, down) { handle(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        handle(Logic.tap(s, spec.hit(s, lx, ly)));
      },
      turnSync: function (p) {
        var r = Logic.sync(s, p);
        s = r.s; err = "";
        return r.events;
      },
      draw: draw,
    };

    function status(nm, short) {
      if (err && s.updates - errAt < 240) return err;
      if (s.msg === "nope" && s.updates - s.msgAt < 50) return spec.nopeText || "That can't move";
      if (s.over) {
        var me = s.mode === "phones" ? s.me : 0;
        if (s.winner === -1) return "No winner";
        if (s.mode === "pass") return nm[s.winner] + " wins!";
        if (s.firstHome !== undefined && s.firstHome >= 0) return (s.winner === me ? "You win" : nm[s.winner] + " wins") + " (" + nm[s.firstHome] + "'s seat was the computer's)";
        if (s.endReason === "resigned" || s.endReason === "timeout") return s.winner === me ? "The others left — you win!" : "You left the game";
        return s.winner === me ? "You win!" : nm[s.winner] + " wins";
      }
      if (s.note && s.updates - s.noteAt < 90) return s.note;
      var seat = s.turn, mine = Logic.mySeat(s) === seat;
      if (s.mode === "phones") {
        if (s.sending) return s.rolled ? "Sending your move…" : "Rolling…";
        if (s.turn !== s.me) return nm[seat] + (s.left[seat] ? " (the computer)" : "") + "'s turn" + (short ? "" : " — they'll be told");
      }
      if (s.cpu[seat]) return nm[seat] + (s.rolled ? " is moving…" : " is rolling…");
      if (s.busy > 0) return "";
      var who = s.mode === "pass" ? nm[seat] + ": " : "";
      if (!s.rolled) return mine ? who + "tap the die to roll" : "";
      return who + (spec.chooseText ? spec.chooseText(s, seat) : "Your move");
    }

    function draw(g, info) {
      var nm = names(), V = { names: nm, info: info, reduce: info.reduce };
      spec.drawBoard(g, s, info, V);
      // the die and the line under the board
      var lcd = g.kind === "lcd", rolling = s.updates - s.rollAt < Logic.ROLL_SHOW && !info.reduce;
      var face = rolling ? 1 + ((s.updates * 7 + s.rollAt) % 6) : s.rolled || s.lastRoll || 0;
      var canRoll = !s.rolled && Logic.mySeat(s) >= 0 && info.state === "running";
      var dx = 10, dy = 266, ds = 28;
      if (face) drawDie(g, dx, dy, ds, face, { ring: canRoll && (info.reduce || Math.floor(s.updates / 30) % 2 === 0) });
      else { g.rect(dx, dy, ds, ds, 8, { r: 6, solid: !lcd }); g.text("?", dx + ds / 2, dy + 19, { size: 14, align: "center", ci: 0 }); if (canRoll) g.ring(dx + ds / 2, dy + ds / 2, ds * 0.78, lcd ? 0 : 7, 2.5); }
      if (s.lastRollBy >= 0 && spec.marker && face) spec.marker(g, dx + ds + 9, dy + 7, s.lastRollBy, 5, s);
      var st = status(nm, g.kind === "pixel");            // Pixel's coarse letters: the short way to say it
      g.text(st, 54, 285, { size: st.length > 30 ? 9 : 11, align: "left", ci: 0, a: 0.92 });
      if (s.over && info.state !== "running" && info.state !== "paused") {
        var t = status(nm, true), dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(30, 120, 180, 52, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text(t.length > 18 ? "GAME OVER" : t.toUpperCase(), 120, 143, { size: 16, align: "center", ci: lcd ? 9 : 0 });
        if (t.length > 18) g.text(t, 120, 162, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  var DiceKit = { game: game, session: session, drawDie: drawDie, MODES: MODES, PHONES_BASE: PHONES_BASE, CPU_WAIT: CPU_WAIT,
    FORCED_WAIT: FORCED_WAIT, KIT_OPTIONS: KIT_OPTIONS };
  if (typeof module === "object" && module.exports) module.exports = DiceKit; else root.DiceKit = DiceKit;
})(typeof window !== "undefined" ? window : this);
