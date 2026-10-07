/* Household Arcade — Dot Connect: input and drawing around the rules in connect-logic.js.
   Registers itself as "connect" with window.ArcadeGames.

   Drag from a dot to its partner. Each pair's dots carry the pair's number as well as its colour (pairs 8 and up are
   also ringed), so pairs can be told apart without colour; Retro LCD draws every line in one ink and relies on the
   numbers. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.ConnectLogic;

  var MODES = [{ id: "little", label: "Little 5 × 5 (gentle)" }, { id: "classic", label: "7 × 7" }, { id: "big", label: "9 × 9" },
    { id: "levels", label: "Levels" }];
  var LABELS = { little: "LITTLE", classic: "7 × 7", big: "9 × 9" };
  var HUES = [1, 5, 4, 3, 6, 7, 2], BX = 10, BY = 42, BS = 220, MSG_Y = 276;

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [], last = -1;
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function cellAt(x, y) {
      var cs = BS / s.n, c = Math.floor((x - BX) / cs), r = Math.floor((y - BY) / cs);
      return c < 0 || r < 0 || c >= s.n || r >= s.n ? -1 : r * s.n + c;
    }
    /** Carry the line on towards square `to`, one neighbouring square at a time (a fast finger skips squares). */
    function towards(to) {
      for (var guard = 0; guard < 40 && s.pen >= 0 && last !== to; guard++) {
        var n = s.n, lr = Math.floor(last / n), lc = last % n, tr = Math.floor(to / n), tc = to % n, next;
        if (Math.abs(tr - lr) >= Math.abs(tc - lc)) next = (lr + (tr > lr ? 1 : -1)) * n + lc;
        else next = lr * n + lc + (tc > lc ? 1 : -1);
        var evs = Logic.extend(s, next);
        queue(evs);
        if (!evs.length || evs[0].type === "blocked") break;
        last = next;
      }
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints, startLevel: opts.startLevel }); pending = []; last = -1; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; last = -1; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { start: "bounce", join: "powerup", cut: "hit", blocked: "wall", hint: "powerup", nohint: "wall", clear: "row", level: "level", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        var i = cellAt(lx, ly);
        if (kind === "down") { if (i >= 0) { queue(Logic.begin(s, i)); last = s.pen >= 0 ? i : -1; } }
        else if (kind === "move") { if (i >= 0 && s.pen >= 0 && last >= 0 && i !== last) towards(i); }
        else if (kind === "up") { Logic.stop(s); last = -1; }
      },
      draw: draw,
    };

    function centre(n, cs, i) { return [BX + (i % n) * cs + cs / 2, BY + Math.floor(i / n) * cs + cs / 2]; }
    function draw(g, info) {
      var n = s.n, cs = BS / n, lcd = g.kind === "lcd", i, k;
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      g.board(BX, BY, BS, BS);
      for (i = 1; i < n; i++) {
        g.line(BX + i * cs, BY + 1, BX + i * cs, BY + BS - 1, 8, 1, { a: 0.5 });
        g.line(BX + 1, BY + i * cs, BX + BS - 1, BY + i * cs, 8, 1, { a: 0.5 });
      }
      for (k = 0; k < s.paths.length; k++) {
        var p = s.paths[k], ci = lcd ? 0 : HUES[k % HUES.length], ok = Logic.joined(s, k);
        if (ok && !lcd) for (i = 0; i < p.length; i++) {           // a joined pair tints its squares
          g.rect(BX + (p[i] % n) * cs + 1, BY + Math.floor(p[i] / n) * cs + 1, cs - 2, cs - 2, ci, { solid: true, a: 0.18, r: 0 });
        }
        for (i = 1; i < p.length; i++) {
          var a = centre(n, cs, p[i - 1]), b = centre(n, cs, p[i]);
          g.line(a[0], a[1], b[0], b[1], ci, Math.max(3, cs * 0.3), { cap: "round" });
        }
      }
      for (i = 0; i < s.dots.length; i++) {
        var d = s.dots[i];
        if (d < 0) continue;
        var c = centre(n, cs, i), r = cs * 0.36;
        g.circle(c[0], c[1], r, lcd ? 0 : HUES[d % HUES.length], { solid: true });
        if (d >= 7 && !lcd) g.ring(c[0], c[1], r + 2, 0, 1.5);
        if (cs >= 18) g.text(String(d + 1), c[0], c[1] + 4, { size: cs >= 30 ? 11 : 9, align: "center", ci: 9 });
      }
      if (info.state === "running" || info.state === "paused") {
        var x = BX + (s.cursor % n) * cs, y = BY + Math.floor(s.cursor / n) * cs;
        g.rect(x + 1, y + 1, cs - 2, cs - 2, lcd ? 0 : s.pen >= 0 ? 3 : 5, { r: 3, a: 0.9 });
      }
      var joinedCount = 0;
      for (k = 0; k < s.pairs; k++) if (Logic.joined(s, k)) joinedCount++;
      g.text("JOINED " + joinedCount + "/" + s.pairs + (s.fill ? "   FILL EVERY SQUARE" : ""), 120, MSG_Y, { size: 11, align: "center" });
      var tip = s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" : s.pen >= 0 ? "Draw to its partner" :
        Logic.allJoined(s) && s.fill ? "Every square must be filled" : s.levels ? "Score so far " + s.runScore : "Drag from a dot to its partner";
      g.text(tip, 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      if (s.phase === "clear" || s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("ALL JOINED!", 120, 147, { size: 15, align: "center", ci: lcd ? 9 : 0 });
        g.text(s.won ? "SCORE " + s.runScore : "+" + s.lastBoardScore + "  NEXT: LEVEL " + (s.level + 1), 120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "connect",
    name: "Dot Connect",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "alt", label: "💡 Hint", aria: "Hint: draw one pair's line", wide: true }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Join each pair of dots with the same number by drawing a line through the squares between them. Lines can't cross — drawing over a line cuts it — and every square must be filled (Little only asks for the pairs to be joined). Drag from a dot, or from a line's end to carry it on. Keyboard: move the ring with the arrows, Space on a dot starts its line, the arrows draw, Space lets go. Faster scores more. With the Hints option on, Hint draws one pair's line. Levels carry on from your next level.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
