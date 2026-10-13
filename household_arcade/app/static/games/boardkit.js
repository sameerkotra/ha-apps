/* Household Arcade — what the turn-by-turn family games share (window.BoardKit, or module.exports in Node).

   Wave 7's games (Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes, Sea Battle) are two-player board
   games played three ways: against the computer (Easy, Medium, Hard), two players on one screen, and turn by turn
   from two phones (SPEC §13.5) — each move then goes to the server, whose Python rules (app/rules/<game>.py) check it;
   the game's own rules here are the same rules, used to draw the board and to play the other two ways.

   - BoardKit.game(R) turns a game's rules R into its Logic (pure: no DOM, deterministic for a seed; no Math.random or
     Date): modes, whose move, the computer's moves after a short pause, the keyboard cursor, scores, saving, and the
     turn-by-turn picture from the server (fromTurns / sync).
   - BoardKit.session(canvas, opts, spec) is the drawing side every game file shares (browser only): the two players
     at the top, whose move it is at the bottom, the end banner, the "pass the phone" screen and sending a move to
     the server; the game draws its board.

   R (a game's rules): { id, STATE_VERSION, OPTIONS: {id: [choices…], default}, init(s), moves(s, seat), apply(s, seat,
   move) → events (throws on an illegal move), bonus(s, seat), ai(s, seat, level) → move, digest(s), counts(s) → [n0, n1]
   (what the header shows), cursor helpers: cursorStart(s), cursorMove(s, dir), activate(s) → { move } | { events } |
   null, tap(s, target) → the same; optional toMove(s), step(s), fromView(s, view, moves), check(saved), status(s, ctx). */
(function (root) {
  "use strict";

  var UPS = 60, CPU_WAIT = 36, STATE_VERSION = 1;
  // After a change (a move, a tap, a message) the board is drawn every frame this long (its animations and timed
  // messages run at most 240 updates); after that only when something changes (kit.js drawKey).
  var ANIM_HOLD = 250;
  var MODES = [
    { id: "easy", label: "Computer · Easy (gentle)", level: 1 },
    { id: "medium", label: "Computer · Medium", level: 2 },
    { id: "hard", label: "Computer · Hard", level: 3 },
    { id: "two", label: "Two players (one screen)", level: 0 },
    { id: "phones", label: "Two phones (turn by turn)", level: 0 },
  ];
  var LEVEL_BASE = { 1: 100, 2: 250, 3: 500 }, PHONES_BASE = 500;

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function pick(s, list) { return list[Math.floor(rand(s) * list.length)]; }
  function canonical(m) {
    if (m === null || typeof m !== "object") return JSON.stringify(m);
    if (Array.isArray(m)) return "[" + m.map(canonical).join(",") + "]";
    return "{" + Object.keys(m).sort().map(function (k) { return JSON.stringify(k) + ":" + canonical(m[k]); }).join(",") + "}";
  }
  function copy(x) { return JSON.parse(JSON.stringify(x)); }
  /** A win: the base and a bonus (at most the base again); a draw a quarter of the base; a loss 0 (as app/rules/base.py). */
  function marginScore(won, draw, base, bonus) {
    if (won) return base + Math.max(0, Math.min(base, bonus));
    if (draw) return Math.floor(base / 4);
    return 0;
  }
  function modeInfo(id) { for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i]; return null; }

  function game(R) {
    function options(o) {
      var out = {};
      for (var k in (R.OPTIONS || {})) {
        var spec = R.OPTIONS[k], v = o && o[k] !== undefined ? String(o[k]) : null;
        out[k] = v !== null && spec.choices.indexOf(v) >= 0 ? v : spec.default;
      }
      return out;
    }
    function toMove(s) { if (s.over) return []; return R.toMove ? R.toMove(s) : [s.turn]; }
    function moves(s, seat) { return R.moves(s, seat === undefined ? s.turn : seat); }
    function legal(s, seat, m) {
      if (R.legal) return R.legal(s, seat, m);
      var want = canonical(m), ms = R.moves(s, seat);
      for (var i = 0; i < ms.length; i++) if (canonical(ms[i]) === want) return true;
      return false;
    }

    function create(o) {
      o = o || {};
      var info = modeInfo(o.mode) || MODES[0];
      var seed = (Number(o.seed) >>> 0) || 1;
      var phones = info.id === "phones";
      var first = phones ? seed % 2 : 0;
      var s = {
        game: R.id, mode: info.id, level: info.level, seed: seed, rng: seed, n: 2, first: first, turn: first,
        over: false, winner: null, ply: 0, last: -1, options: options(o.options),
        me: phones ? (o.seat === 1 ? 1 : 0) : 0,
        cpu: info.level > 0 ? 1 : -1,
        updates: 0, wait: 0, cursor: 0, sel: null, sending: false, number: 0, pass: -1, passIn: 0, passTo: -1, msg: "", msgAt: -1000,
        lastBy: -1, overAt: 0,
      };
      R.init(s);
      s.cursor = R.cursorStart ? R.cursorStart(s) : 0;
      return s;
    }

    /** Whose move the person (or people) at this screen can make now: a seat, or −1. */
    function mySeat(s) {
      if (s.over || s.pass >= 0 || s.passIn > 0) return -1;
      var tm = toMove(s);
      if (s.mode === "phones") return !s.sending && tm.indexOf(s.me) >= 0 ? s.me : -1;
      if (s.mode === "two") return tm.length ? tm[0] : -1;
      for (var i = 0; i < tm.length; i++) if (tm[i] !== s.cpu) return tm[i];
      return -1;
    }

    function endEvents(s, evs) {
      if (!s.over) return;
      if (s.mode === "two") { evs.push({ type: s.winner === -1 ? "draw" : "win", winner: s.winner }); return; }
      var me = s.mode === "phones" ? s.me : 0;
      evs.push({ type: s.winner === -1 ? "draw" : s.winner === me ? "win" : "lose", winner: s.winner });
    }

    /** Make a move for `seat` (the person at this screen, or the computer). Phones: the move is sent, not played. */
    function play(s, seat, move) {
      if (!legal(s, seat, move)) { s.msg = "nope"; s.msgAt = s.updates; return [{ type: "nope" }]; }
      if (s.mode === "phones") { s.sending = true; s.sel = null; return [{ type: "send", move: move }]; }
      var evs = R.apply(s, seat, move) || [];
      s.lastBy = seat; s.sel = null;
      if (s.over) s.overAt = s.updates;
      evs.unshift({ type: "move", seat: seat });
      if (!s.over && s.cpu >= 0 && toMove(s).indexOf(s.cpu) >= 0) s.wait = CPU_WAIT;
      // one screen, hidden fleets (Sea Battle): the result shows a moment, then "pass the phone" hides the board
      if (!s.over && s.mode === "two" && R.passDelay && toMove(s)[0] !== seat) { s.passIn = R.passDelay; s.passTo = toMove(s)[0]; }
      endEvents(s, evs);
      return evs;
    }

    // The computer can think elsewhere (setThinker: a Web Worker, so the page never stops while it thinks): it gets a
    // copy of the game and answers { move, rng } — the move R.ai makes there and the random state after it, so the
    // game goes on exactly as if it had thought here.
    var thinker = null;
    function step(s) {
      var evs = [];
      s.updates++;
      if (s.passIn > 0 && --s.passIn === 0) s.pass = s.passTo;
      if (R.step) evs = evs.concat(R.step(s) || []);
      if (s.over || s.cpu < 0 || toMove(s).indexOf(s.cpu) < 0) return evs;
      if (s.wait > 0) { s.wait--; return evs; }
      var m = null;
      if (thinker) {
        if (s.aiFor !== s.ply || !s.aiReady) {
          if (s.aiAsked !== s.ply) {
            var st = s, ply = s.ply;
            s.aiAsked = ply;
            thinker({ s: save(s), seat: s.cpu, level: s.level }, function (res) {
              if (st.aiAsked !== ply || st.ply !== ply || st.over) return;            // the game moved on
              if (res && res.move) { st.aiReady = res.move; st.aiRng = res.rng; st.aiFor = ply; }
              else st.aiAsked = null;                                                // ask again
            });
          }
          return evs;
        }
        m = s.aiReady; s.rng = s.aiRng; s.aiReady = null; s.aiFor = null; s.aiAsked = null;
      } else m = R.ai(s, s.cpu, s.level);
      if (m) evs = evs.concat(play(s, s.cpu, m));
      return evs;
    }

    function act(s, r) {
      if (!r) return [];
      if (r.events) return r.events;
      var seat = mySeat(s);
      if (seat < 0 || !r.move) return [];
      return play(s, seat, r.move);
    }
    /** The shell's actions: the arrows move the cursor, fire plays where it is, alt is the game's own (if any). */
    function press(s, action, down) {
      if (!down) return [];
      if (s.pass >= 0) { if (action === "fire") { s.pass = -1; return [{ type: "ready" }]; } return []; }
      if (s.over) return [];
      if (action === "up" || action === "down" || action === "left" || action === "right") {
        // a game may make an arrow play (Four in a Row: ↓ drops the disc)
        if (R.cursorMove && R.cursorMove(s, action) === "fire") return act(s, R.activate(s, mySeat(s)));
        return [{ type: "cursor" }];
      }
      if (action === "fire") return act(s, R.activate(s, mySeat(s)));
      if (action === "alt" && R.alt) return act(s, R.alt(s, mySeat(s)));
      return [];
    }
    /** A tap on the board: `target` is what the game file found under the finger (a column, a square, a line …). */
    function tap(s, target) {
      if (s.pass >= 0) { s.pass = -1; return [{ type: "ready" }]; }
      if (s.over || target === null || target === undefined) return [];
      return act(s, R.tap(s, target, mySeat(s)));
    }

    function score(s, seat) {
      if (!s.over || s.mode === "two") return 0;
      if (seat === undefined) seat = s.mode === "phones" ? s.me : 0;
      var base = s.mode === "phones" ? PHONES_BASE : LEVEL_BASE[s.level] || 0;
      return marginScore(s.winner === seat, s.winner === -1, base, R.bonus(s, seat));
    }

    function result(s, names) {
      var me = s.mode === "phones" ? s.me : 0, lines = [];
      names = names || (s.mode === "two" || s.mode === "phones" ? ["Player 1", "Player 2"] : ["You", "Computer"]);
      var won = s.over && s.winner === me && s.mode !== "two", draw = s.over && s.winner === -1;
      var lost = s.over && !won && !draw && s.mode !== "two";
      if (R.summary) lines = R.summary(s, names) || [];
      var note;
      if (s.mode === "two") note = "Two players on one screen — not saved";
      else if (lost) note = "Not a win — not saved";
      return {
        score: score(s), level: 1,
        stats: { won: won, lost: lost, draw: draw, winner: s.winner, mode: s.mode, moves: s.ply, summary: lines.length ? lines : undefined,
          notSaved: note },
      };
    }

    function save(s) { return copy(s); }
    function restore(data) {
      var ok = data && typeof data === "object" && data.game === R.id && modeInfo(data.mode) && data.mode !== "phones" &&
        typeof data.turn === "number" && typeof data.rng === "number" && typeof data.updates === "number" &&
        (!R.check || R.check(data));
      if (!ok) throw new Error("That saved game can't be continued.");
      var s = copy(data);
      s.sending = false;
      s.aiAsked = null; s.aiReady = null; s.aiFor = null;        // a move being thought elsewhere is asked for again
      if (R.restored) R.restored(s);      // a game's own clean-up (Chess: no computer's move half thought)
      return s;
    }

    /** The match as the server sends it (GET /api/matches/{id}/turns): replay the moves, or (Sea Battle) take the
        view — what this phone may see. */
    function fromTurns(p) {
      var s = create({ mode: "phones", seed: p.seed, options: p.options, seat: (p.seat || 1) - 1 });
      var list = p.moves || [];
      if (R.fromView) R.fromView(s, p.view || {}, list);
      else {
        for (var i = 0; i < list.length; i++) {
          R.apply(s, list[i].seat - 1, list[i].move);
          s.lastBy = list[i].seat - 1;
        }
        s.applied = list.length;                    // the moves this state has played (syncOn goes on from here)
      }
      s.number = p.number || list.length;
      if (p.over && p.result && (!s.over || s.winner === null || s.winner === undefined)) {      // ended another way (resigned, timeout): the server's word
        s.over = true;
        s.winner = p.result.winner === 0 ? -1 : p.result.winner === null ? -1 : p.result.winner - 1;
      }
      s.endReason = p.over ? p.endReason || null : null;
      return s;
    }
    /** The match moved on by a few moves: the old state with only the new moves played (a long game isn't replayed
        from its first move at every move); null when that can't be done (the board is the server's view, or the
        picture isn't a continuation of the old one). The result is the state fromTurns(p) would make. */
    function syncOn(old, p) {
      var list = p.moves || [];
      if (R.fromView || !old || old.over || old.mode !== "phones" || old.seed !== p.seed || old.me !== (p.seat || 1) - 1 ||
          typeof old.number !== "number" || old.number > list.length || old.number !== old.applied || old.endReason) return null;
      if (JSON.stringify(old.options) !== JSON.stringify(options(p.options))) return null;
      var s = copy(old), updates = s.updates;
      s.updates = 0;                                  // as when replayed: a move's time marks are those of a fresh game
      s.sending = false; s.sel = null; s.msg = ""; s.msgAt = -1000; s.wait = 0;
      for (var i = old.number; i < list.length; i++) {
        R.apply(s, list[i].seat - 1, list[i].move);
        s.lastBy = list[i].seat - 1;
      }
      s.updates = updates;
      return s;
    }
    /** A new picture from the server: the new state (keeping this phone's cursor) and the events to play. */
    function sync(old, p) {
      var s = syncOn(old, p) || fromTurns(p), evs = [];
      if (!R.fromView) s.applied = (p.moves || []).length;
      s.number = p.number || s.applied;
      if (p.over && p.result && (!s.over || s.winner === null || s.winner === undefined)) {
        s.over = true;
        s.winner = p.result.winner === 0 ? -1 : p.result.winner === null ? -1 : p.result.winner - 1;
      }
      s.endReason = p.over ? p.endReason || null : null;
      if (old) {
        s.cursor = old.cursor; s.updates = old.updates; s.sel = null;
        if (R.keep) R.keep(old, s);
        if (s.number > old.number) evs.push({ type: "move", seat: s.lastBy });
        if (s.over && !old.over) { s.overAt = s.updates; endEvents(s, evs); }
      }
      return { s: s, events: evs };
    }

    var L = {
      UPS: UPS, STATE_VERSION: R.STATE_VERSION || STATE_VERSION, MODES: MODES, LEVEL_BASE: LEVEL_BASE, PHONES_BASE: PHONES_BASE,
      CPU_WAIT: CPU_WAIT, R: R,
      create: create, step: step, press: press, tap: tap, play: play, moves: moves, legal: legal, toMove: toMove,
      mySeat: mySeat, score: score, result: result, save: save, restore: restore, fromTurns: fromTurns, sync: sync,
      digest: function (s) { return R.digest(s); }, options: options, rand: rand,
      /** fn({ s, seat, level }, done({ move, rng })) thinks for the computer elsewhere; null: here (Chess has its own). */
      setThinker: function (fn) { thinker = typeof fn === "function" ? fn : null; },
      /** Play a move for whoever's turn it is, without the screen's checks (the tests' and the fuzz runs' way in). */
      force: function (s, move) {
        var seat = toMove(s)[0];
        if (!legal(s, seat, move) && !(R.anyMove && R.anyMove(s, seat, move))) throw new Error("illegal move");
        var evs = R.apply(s, seat, move); s.lastBy = seat; return evs;
      },
    };
    return L;
  }

  // =====================================================================
  // The drawing side (browser): every wave 7 game file builds its session with this.
  // =====================================================================
  /** spec: { Logic, drawBoard(g, s, info, V), hit(s, lx, ly) → target, sounds, marker(g, x, y, seat, r, s) } */
  function session(canvas, opts, spec) {
    var Kit = root.ArcadeKit, Logic = spec.Logic;
    opts = opts || {};
    var s = null, pending = [], turns = opts.turns || null, err = "", errAt = -1000, changedAt = -1e9;
    function changed() { changedAt = s ? s.updates : 0; }

    function names() {
      if (s && s.mode === "phones") {
        var n = opts.names || [];
        return [first(n[0]) || "Player 1", first(n[1]) || "Player 2"];
      }
      if (s && s.mode === "two") return spec.seatNames || ["Player 1", "Player 2"];
      return ["You", "Computer"];
    }
    function first(n) { return String(n || "").trim().split(" ")[0]; }
    function handle(evs) {
      for (var i = 0; i < evs.length; i++) {
        var ev = evs[i];
        if (ev.type === "send" && turns && turns.send) {
          var sent = s;
          Promise.resolve(turns.send(ev.move)).then(null, function (e) {
            if (s === sent) { s.sending = false; err = (e && e.message) || "That move wasn't taken."; errAt = s.updates; changed(); }
          });
        }
        pending.push(ev);
      }
    }

    var impl = {
      init: function (seed) {
        s = turns ? Logic.fromTurns(turns.picture) : Logic.create({ mode: opts.mode, seed: seed, options: opts.options });
        pending = []; changed();
      },
      save: turns ? undefined : function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; changed(); },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; if (evs.length) changed(); return evs; },
      // still between moves: drawn again only when whose move it is, the end, a send or the pass screen changes
      drawKey: function () {
        if (!s || s.updates - changedAt < ANIM_HOLD) return null;
        return [s.over ? 1 : 0, s.sending ? 1 : 0, s.pass, Logic.toMove(s).join(","), s.sel === undefined ? "" : JSON.stringify(s.sel)].join("|");
      },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return 1; },
      // the last move stays on the board a moment (the winning row) before the result card
      isOver: function () { return s.over && s.updates - s.overAt >= 80; },
      result: function () { return Logic.result(s, names()); },
      sounds: Object.assign({ move: "turn", cursor: null, nope: "wall", win: "win", lose: "gameover", draw: "level", ready: "launch" }, spec.sounds || {}),
      input: function (action, down) { handle(Logic.press(s, action, down)); changed(); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        handle(Logic.tap(s, spec.hit(s, lx, ly)));
        changed();
      },
      turnSync: function (p) {
        var r = Logic.sync(s, p);
        s = r.s; err = "";
        changed();
        return r.events;
      },
      draw: draw,
    };

    function status() {
      var nm = names();
      if (err && s.updates - errAt < 240) return err;
      if (s.msg === "nope" && s.updates - s.msgAt < 50) return spec.nopeText || "That move isn't allowed";
      if (spec.statusText) { var t = spec.statusText(s, nm); if (t) return t; }
      if (s.over) {
        if (s.winner === -1) return "A draw";
        if (s.mode === "two") return nm[s.winner] + " wins!";
        var me = s.mode === "phones" ? s.me : 0;
        if (s.endReason === "resigned") return s.winner === me ? nm[1 - me] + " gave up — you win!" : "You gave up";
        if (s.endReason === "timeout") return s.winner === me ? nm[1 - me] + " didn't move for 7 days — you win" : "No move for 7 days — a loss";
        return s.winner === me ? "You win!" : nm[s.winner] + " wins";
      }
      var tm = Logic.toMove(s);
      if (s.mode === "phones") {
        if (s.sending) return "Sending your move…";
        if (tm.indexOf(s.me) >= 0) return spec.yourMove ? spec.yourMove(s) : "Your move";
        return nm[tm[0]] + "'s move — they'll be told";
      }
      if (s.mode === "two") return nm[tm[0]] + "'s move";
      if (tm.indexOf(s.cpu) >= 0) return "The computer is thinking…";
      return spec.yourMove ? spec.yourMove(s) : "Your move";
    }

    function header(g) {
      var nm = names(), counts = spec.counts ? spec.counts(s) : null, tm = Logic.toMove(s), lcd = g.kind === "lcd";
      for (var seat = 0; seat < 2; seat++) {
        var right = seat === 1, x = right ? 230 : 10, on = tm.indexOf(seat) >= 0 && !s.over;
        var label = nm[seat].toUpperCase().slice(0, 9) + (counts ? " · " + counts[seat] : "");
        var mx = right ? x - 6 : x + 6;
        spec.marker(g, mx, 18, seat, 6, s);
        g.text(label, right ? x - 16 : x + 16, 23, { size: 11, align: right ? "right" : "left", ci: 0 });
        if (on) g.line(right ? 120 : 10, 31, right ? 230 : 120, 31, lcd ? 0 : 7, 2.5, { cap: "butt" });
      }
      g.line(10, 33, 230, 33, 8, 1, { a: lcd ? 1 : 0.6 });
    }

    function banner(g, title, sub) {
      var lcd = g.kind === "lcd", dim = g.kind === "pixel" || g.kind === "neon";
      g.rect(30, 120, 180, 52, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
      g.text(title, 120, 143, { size: 16, align: "center", ci: lcd ? 9 : 0 });
      if (sub) g.text(sub, 120, 162, { size: 9, align: "center", ci: lcd ? 9 : 0 });
    }

    function draw(g, info) {
      var V = { names: names(), info: info, reduce: info.reduce };
      header(g);
      spec.drawBoard(g, s, info, V);
      g.text(status(), 120, 291, { size: 11, align: "center", ci: 0, a: 0.9 });
      if (s.pass >= 0) {
        var lcd = g.kind === "lcd";
        g.rect(0, 36, 240, 244, lcd ? 9 : 9, { solid: true, edge: false, r: 0 });
        g.text("Pass to " + V.names[s.pass], 120, 140, { size: 16, align: "center", ci: 0 });
        g.text("Tap when " + V.names[s.pass] + " is ready", 120, 162, { size: 10, align: "center", ci: 0, a: 0.85 });
        g.text("(no peeking!)", 120, 178, { size: 9, align: "center", ci: 0, a: 0.7 });
      } else if (s.over && info.state !== "running" && info.state !== "paused") {
        var st = status();
        banner(g, st.length > 18 ? (s.winner === -1 ? "A DRAW" : "GAME OVER") : st.toUpperCase(), st.length > 18 ? st : null);
      }
    }

    var inst = Kit.createSession(canvas, opts, impl), destroy = inst.destroy, closed = false;
    liveSessions++;
    inst.destroy = function () { destroy(); if (!closed) { closed = true; sessionClosed(); } };
    return inst;
  }

  /** The palette index of a "dark" or a "light" playing piece in a look (the looks' own ink and paper swap round in
      Modern's dark theme, and Neon and Pixel have no dark ink), so dark pieces look dark and light ones light. */
  function toneCi(g, dark) {
    if (g.kind === "neon") return dark ? 6 : 7;
    if (g.kind === "pixel") return dark ? 8 : 0;
    if (g.kind === "contrast") return dark ? 9 : 0;
    if (g.kind === "modern" && g.dark) return dark ? 9 : 0;
    return dark ? 0 : 9;
  }

  // ---------- the computer's thinking within a budget ----------
  // The deeper searches (Hard) used to take a phone half a second or more on a busy board, with the page frozen. They
  // now deepen step by step and stop when they have looked at `budget` positions, keeping the deepest answer they
  // finished: the same answer as before whenever the budget is enough (nearly always), and the same on every phone
  // (it counts positions, not milliseconds).
  var OUT = { outOfBudget: true };
  /** run(depth) for each depth in turn while the budget lasts; the last answer run finished (null if none). The
      search calls spend() at every position it looks at. */
  function deepen(depths, budget, run) {
    var last = null;
    meter.n = 0; meter.max = budget;
    try {
      for (var i = 0; i < depths.length; i++) last = run(depths[i]);
    } catch (e) {
      if (e !== OUT) throw e;
    } finally { meter.max = Infinity; }
    return last;
  }
  var meter = { n: 0, max: Infinity };
  function spend() { if (++meter.n > meter.max) throw OUT; }

  // ---------- thinking in a Web Worker (board-worker.js, from this app's own address — the strict CSP allows it) ----
  var workerSrc = null;
  try {
    var cur = root.document && root.document.currentScript && root.document.currentScript.src;
    if (cur && /boardkit\.js(\?|$)/.test(cur)) workerSrc = cur.replace(/boardkit\.js(\?|$)/, "board-worker.js$1");
  } catch (e) { workerSrc = null; }
  var worker = null, workerFailed = false, jobs = {}, nextJob = 1, liveSessions = 0;
  function thinkHere(j) {
    setTimeout(function () {
      var s = copy(j.job.s), move = null;
      try { move = j.Logic.R.ai(s, j.job.seat, j.job.level); } catch (e) { move = null; }
      j.done({ move: move, rng: s.rng });
    }, 0);
  }
  /** Can the computer think in a worker here? (Not in Node, nor where workers can't be made.) */
  function canThinkElsewhere() { return !workerFailed && !!workerSrc && typeof root.Worker === "function"; }
  /** A thinker for Logic.setThinker: the game's rules (`file`, defining the global `name`) loaded in the worker. */
  function workerThinker(file, name, Logic) {
    return function (job, done) {
      var j = { job: job, done: done, Logic: Logic };
      if (canThinkElsewhere()) {
        try {
          if (!worker) {
            worker = new root.Worker(workerSrc);
            worker.onmessage = function (e) {
              var d = e.data || {}, w = jobs[d.id];
              delete jobs[d.id];
              if (!w) return;
              if (d.error) thinkHere(w); else w.done({ move: d.move, rng: d.rng });
            };
            worker.onerror = function () {
              workerFailed = true; worker = null;
              var left = jobs; jobs = {};
              for (var id in left) thinkHere(left[id]);
            };
          }
          var id = nextJob++;
          jobs[id] = j;
          worker.postMessage({ id: id, file: file, name: name, s: job.s, seat: job.seat, level: job.level });
          return;
        } catch (e) { workerFailed = true; worker = null; }
      }
      thinkHere(j);
    };
  }
  /** The last board game on the page closed: let the worker go (its memory with it). */
  function sessionClosed() {
    liveSessions = Math.max(0, liveSessions - 1);
    if (!liveSessions && worker) { try { worker.terminate(); } catch (e) { /* gone already */ } worker = null; jobs = {}; }
  }

  /** A one-entry cache: the last answer of fn for the same key (a board drawn many times between moves). */
  function memo(fn) {
    var lastKey, lastVal, have = false;
    return function (key) {
      if (!have || key !== lastKey) { lastVal = fn.apply(null, Array.prototype.slice.call(arguments, 1)); lastKey = key; have = true; }
      return lastVal;
    };
  }

  var BoardKit = { memo: memo, deepen: deepen, spend: spend, meter: meter, workerThinker: workerThinker,
    canThinkElsewhere: canThinkElsewhere, rand: rand, toneCi: toneCi, pick: pick, canonical: canonical, copy: copy, marginScore: marginScore, MODES: MODES,
    LEVEL_BASE: LEVEL_BASE, PHONES_BASE: PHONES_BASE, CPU_WAIT: CPU_WAIT, game: game, session: session };
  if (typeof module === "object" && module.exports) module.exports = BoardKit; else root.BoardKit = BoardKit;
})(typeof window !== "undefined" ? window : this);
