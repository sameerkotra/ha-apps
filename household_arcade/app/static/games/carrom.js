/* Household Arcade — Carrom: input and drawing around the rules and physics in carrom-logic.js. Registers itself as
   "carrom".

   Shooting: slide the striker along your baseline (drag along the line, or ← →), then pull back from the striker
   like a catapult — the line shows where it goes and how hard — and let go. With keys: ← → move the striker, ↑ ↓ turn
   the aim, hold Space for power (it rises and falls) and let go to shoot; C sets the aim straight again. A controller:
   the d-pad and A. The on-screen buttons do the same.

   Live from two phones (SPEC §13.4, "taking turns"): each shot goes to the server as one input and comes back to both
   phones, which play it with the same whole-number physics; the second phone sees the board turned round, so your own
   baseline is always at the bottom. 30 seconds a shot, then the server plays a weak one for you.

   The board is drawn from shapes: the frame, four pockets, the baselines with their red circles, the middle circles
   and the corner arrows. White coins are light, black ones dark, the queen red — and on Retro LCD hollow, solid and
   solid with a dot. */
(function (root) {
  "use strict";
  var Logic = root.CarromLogic, BK = root.BoardKit, Kit = root.ArcadeKit;
  var U = Logic.U, X0 = 12, Y0 = 40, SIDE_PX = Logic.BOARD / U, PULL_PX = 70, SHOT_SECONDS = 30;

  function create(canvas, opts) {
    opts = opts || {};
    var live = opts.live && opts.live.lockstep ? opts.live : null;
    var s = null, aim = null, drag = null, held = {}, sent = -1, lastTurnKey = "", turnStart = 0, overAt = 0, pend = [];

    function flip() { return !!(live && s && s.me === 1); }
    function toScreen(x, y) { var f = flip(); return [X0 + (f ? Logic.BOARD - x : x) / U, Y0 + (f ? Logic.BOARD - y : y) / U]; }
    function toBoard(px, py) { var x = (px - X0) * U, y = (py - Y0) * U; return flip() ? [Logic.BOARD - x, Logic.BOARD - y] : [x, y]; }
    /** The seat the person at this phone may shoot for now, or −1. */
    function shooter() {
      if (!s || s.over || s.moving) return -1;
      if (live) return s.turn === s.me && sent !== s.shots ? s.me : -1;
      if (s.cpu >= 0 && s.turn === s.cpu) return -1;
      return s.turn;
    }
    function freshAim() { return { pos: 500, angle: 900, power: 0, charging: false, dir: 1 }; }
    function syncTurn() {
      var key = s.shots + "/" + s.turn + "/" + (s.moving ? 1 : 0);
      if (key !== lastTurnKey) {
        lastTurnKey = key;
        if (!s.moving) { aim = freshAim(); turnStart = s.updates; drag = null; }
      }
    }
    function value() { return [Math.round(aim.pos), Math.round(aim.angle), Math.max(1, Math.min(100, Math.round(aim.power)))]; }
    function fire(act) {
      var seat = shooter();
      if (seat < 0 || aim.power < 3) { aim.power = 0; aim.charging = false; return; }
      var v = value();
      aim.charging = false;
      if (live) { if (act) act("shot", v); sent = s.shots; return; }
      pend = pend.concat(Logic.shoot(s, seat, v));
    }
    /** A board point's place along the shooter's baseline (0–1000) and how far in front of it (px). */
    function alongBase(seat, bx, by) {
      var S = Logic.SIDES[Logic.sideOf(s, seat)], dx = bx - S.cx, dy = by - S.cy;
      var u = dx * S.ux + dy * S.uy, v = dx * S.vx + dy * S.vy;
      return { pos: Math.round((u + Logic.BASE_HALF) * 1000 / (2 * Logic.BASE_HALF)), v: v / U, u: u / U };
    }

    var impl = {
      init: function (seed) {
        s = Logic.create({ mode: opts.mode, seed: seed, seat: live ? live.seat - 1 : 0 });
        aim = freshAim(); lastTurnKey = ""; sent = -1; pend = [];
      },
      save: live ? undefined : function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); aim = freshAim(); lastTurnKey = ""; },
      step: function () {
        syncTurn();
        if (shooter() >= 0) {
          var S = 1;
          if (held.left) aim.pos = Math.max(0, aim.pos - 7 * S);
          if (held.right) aim.pos = Math.min(1000, aim.pos + 7 * S);
          if (held.up) aim.angle = Math.min(1700, aim.angle + 6);
          if (held.down) aim.angle = Math.max(100, aim.angle - 6);
          if (aim.charging) {
            aim.power += 1.6 * aim.dir;
            if (aim.power >= 100) { aim.power = 100; aim.dir = -1; } else if (aim.power <= 2) { aim.power = 2; aim.dir = 1; }
          }
        }
        var evs = pend.concat(Logic.step(s));
        pend = [];
        if (s.over && !overAt) overAt = s.updates;
        // one sound of a kind an update
        var seen = {}, out = [];
        for (var i = 0; i < evs.length; i++) { if (seen[evs[i].type] && evs[i].type !== "settled") continue; seen[evs[i].type] = 1; out.push(evs[i]); }
        if (s.over && out.some(function (e) { return e.type === "board"; })) {
          var me = live ? s.me : 0, won = s.winnerTeam === Logic.teamOf(s, me);
          out.push({ type: s.mode === "two" || s.mode === "doubles" || won ? "win" : s.winnerTeam < 0 ? "draw" : "lose" });
        }
        return out;
      },
      logic: function () { return s; },
      score: function () { return Logic.score(s, live ? s.me : 0); },
      level: function () { return 1; },
      isOver: function () { return s.over && s.updates - overAt >= 90; },
      result: function () { return Logic.result(s, live ? s.me : 0); },
      sounds: { shot: "launch", clack: "hit", cushion: "bounce", pocket: "eat", queen: "bonus", foul: "lose", fouled: null,
        covered: "powerup", queenBack: "wall", lastCoin: "wall", board: null, settled: null, win: "win", lose: "gameover", draw: "level" },
      // live play (taking turns): the kit plays each shot from the server on both phones
      apply: function (player, action, v) { return Logic.press(s, action, v, player); },
      turn: function () { return Logic.turn(s); },
      shots: function () { return s.shots; },
      settledShots: function () { return s.moving ? s.shots - 1 : s.shots; },
      nextSeat: function () { return s.over ? 0 : s.turn + 1; },
      checksum: function () { return Logic.checksum(s); },
      report: function () { return Logic.report(s); },
      input: function (action, down, act) {
        if (action === "left" || action === "right" || action === "up" || action === "down") { held[action] = down; return; }
        if (shooter() < 0) { if (!down) aim.charging = false; return; }
        if (action === "fire") {
          if (down) { aim.charging = true; aim.power = Math.max(2, aim.power); aim.dir = 1; }
          else if (aim.charging) fire(act);
        } else if (action === "alt" && down) { var p = aim.pos; aim = freshAim(); aim.pos = p; }
      },
      pointer: function (kind, lx, ly, x, y, act) {
        var seat = shooter();
        if (seat < 0) { drag = null; return; }
        var b = toBoard(lx, ly);
        if (kind === "down") {
          var st = Logic.strikerPreview(s, seat, aim.pos), dx = (b[0] - st[0]) / U, dy = (b[1] - st[1]) / U;
          var a = alongBase(seat, b[0], b[1]);
          if (dx * dx + dy * dy < 17 * 17) drag = { kind: "aim" };
          else if (Math.abs(a.v) < 16 && Math.abs(a.u) < Logic.BASE_HALF / U + 12) { drag = { kind: "place" }; aim.pos = Math.max(0, Math.min(1000, a.pos)); }
          else drag = null;
          return;
        }
        if (!drag) return;
        if (drag.kind === "place") {
          var a2 = alongBase(seat, b[0], b[1]);
          aim.pos = Math.max(0, Math.min(1000, a2.pos));
          if (kind === "up") drag = null;
          return;
        }
        // pulling back: the shot goes the other way from the finger, harder the further it is pulled
        var p0 = Logic.strikerPreview(s, seat, aim.pos), S = Logic.SIDES[Logic.sideOf(s, seat)];
        var ex = p0[0] - b[0], ey = p0[1] - b[1], du = ex * S.ux + ey * S.uy, dv = ex * S.vx + ey * S.vy;
        var len = Math.sqrt(ex * ex + ey * ey) / U;
        if (dv > U * 2) { aim.angle = Math.max(100, Math.min(1700, Logic.angleTo(du, dv))); aim.power = Math.min(100, len / PULL_PX * 100); aim.ok = true; }
        else { aim.power = 0; aim.ok = false; }
        if (kind === "up") { drag = null; if (aim.ok) fire(act); else aim.power = 0; }
      },
      draw: draw,
    };

    // ---------------- drawing ----------------
    function coin(g, x, y, k, r, a) {
      var lcd = g.kind === "lcd", o = { a: a == null ? 1 : a };
      if (k === "w") {
        if (lcd) g.circle(x, y, r, 9, {});
        else { g.circle(x, y, r, BK.toneCi(g, false), { solid: true, stroke: true, a: o.a }); g.ring(x, y, r * 0.55, 8, 1, { a: 0.7 * o.a }); }
      } else if (k === "b") {
        // dark coins: on the looks with a dark board they get a light rim so they never disappear
        var rim = g.kind === "neon" || g.kind === "pixel" || g.kind === "contrast" || (g.kind === "modern" && g.dark);
        g.circle(x, y, r, lcd ? 0 : g.kind === "neon" ? 6 : BK.toneCi(g, true), { solid: true, stroke: !lcd, a: o.a });
        if (rim) g.ring(x, y, r - 0.6, 0, 1.1, { a: 0.85 * o.a });
        else if (!lcd) g.ring(x, y, r * 0.55, 8, 1, { a: 0.5 * o.a });
      } else if (k === "q") {
        g.circle(x, y, r, lcd ? 0 : 1, { solid: true, stroke: !lcd, a: o.a });
        g.circle(x, y, Math.max(1, r * 0.35), 9, { solid: true, white: true, a: o.a });
      } else {
        g.circle(x, y, r, lcd ? 9 : 9, { solid: !lcd, stroke: !lcd, a: o.a });
        g.ring(x, y, r - 1.2, lcd ? 0 : 5, 2, { a: o.a });
        g.circle(x, y, Math.max(1, r * 0.3), lcd ? 0 : 5, { solid: true, a: o.a });
      }
    }
    function drawBoard(g) {
      var lcd = g.kind === "lcd", low = g.lowres, i, k;
      var x0 = X0, y0 = Y0, w = SIDE_PX;
      // the frame, then the playing surface: a warm light board in Modern and Paper, the look's own dark elsewhere
      if (lcd) { g.rect(x0 - 5, y0 - 5, w + 10, w + 10, 9, {}); g.rect(x0 - 1, y0 - 1, w + 2, w + 2, 9, {}); }
      else if (g.kind === "neon") {
        g.curve(function (c) { c.rect(x0 - 4, y0 - 4, w + 8, w + 8); }, 2, 3);
        g.curve(function (c) { c.rect(x0, y0, w, w); }, 2, 1, { a: 0.6 });
      } else {
        g.rect(x0 - 6, y0 - 6, w + 12, w + 12, 2, { r: low ? 0 : 5, solid: true, a: 0.9, edge: false });
        g.rect(x0, y0, w, w, g.kind === "pixel" ? 2 : 9, { r: 0, solid: true, edge: false, a: g.kind === "pixel" ? 0.22 : 1 });
        if (g.kind === "modern" || g.kind === "paper") g.rect(x0, y0, w, w, 3, { r: 0, solid: true, a: g.dark ? 0.12 : 0.3, edge: false });
      }
      // the corner pockets
      var pp = Logic.POCKET_AT / U, pr = Logic.RP / U, pocket = g.kind === "neon" || g.kind === "pixel" ? 8 : BK.toneCi(g, true);
      [[pp, pp], [w - pp, pp], [pp, w - pp], [w - pp, w - pp]].forEach(function (p) {
        g.circle(x0 + p[0], y0 + p[1], pr, lcd ? 0 : pocket, { solid: true, stroke: g.kind === "neon" });
        if (g.kind === "pixel") g.ring(x0 + p[0], y0 + p[1], pr, 0, 1);
      });
      // baselines on all four sides, with the red circles at their ends
      var off = Logic.BASE_OFF / U, half = Logic.BASE_HALF / U, gap = Logic.RS / U;
      for (k = 0; k < 4; k++) {
        var S = Logic.SIDES[k], cx = x0 + S.cx / U, cy = y0 + S.cy / U;
        for (var l = -1; l <= 1; l += 2) {
          var ox = S.vx * gap * l, oy = S.vy * gap * l;
          g.line(cx - S.ux * half + ox, cy - S.uy * half + oy, cx + S.ux * half + ox, cy + S.uy * half + oy, lcd ? 0 : 0, 1, { a: lcd ? 1 : 0.55 });
        }
        for (l = -1; l <= 1; l += 2) g.circle(cx + S.ux * half * l, cy + S.uy * half * l, gap, lcd ? 0 : 1, { solid: !lcd, a: lcd ? 1 : 0.8 });
      }
      void off;
      // the middle circles
      var mx = x0 + w / 2, my = y0 + w / 2;
      g.ring(mx, my, 30, lcd ? 0 : 0, 1, { a: lcd ? 1 : 0.45 });
      g.ring(mx, my, 6, lcd ? 0 : 1, 1, { a: lcd ? 1 : 0.6 });
      // the corner arrows
      for (k = 0; k < 4; k++) {
        var sx = k % 2 ? -1 : 1, sy = k < 2 ? 1 : -1, ax = k % 2 ? x0 + w : x0, ay = k < 2 ? y0 : y0 + w;
        g.line(ax + sx * 24, ay + sy * 24, ax + sx * 58, ay + sy * 58, lcd ? 0 : 0, 1, { a: lcd ? 1 : 0.45 });
        g.ring(ax + sx * 61, ay + sy * 61, 3, lcd ? 0 : 0, 1, { a: lcd ? 1 : 0.45 });
      }
      void i;
    }
    function names() {
      if (live) {
        var n = opts.names || [];
        return [first(n[0]) || "Player 1", first(n[1]) || "Player 2"];
      }
      if (s.mode === "doubles") return ["Player 1", "Player 2", "Player 3", "Player 4"];
      if (s.mode === "two") return ["Player 1", "Player 2"];
      return ["You", "Computer"];
    }
    function first(n) { return String(n || "").trim().split(" ")[0]; }
    function status(nm, info) {
      if (s.over) {
        var me = live ? s.me : 0, t = s.winnerTeam;
        if (t < 0) return "A draw — the same points after " + Logic.MAX_SHOTS + " shots";
        if (s.mode === "doubles") return "Team " + (t + 1) + " (" + nm[t] + " & " + nm[t + 2] + ") wins · " + s.points + " pts";
        if (s.mode === "two") return nm[t] + " wins the board · " + s.points + " pts";
        return (t === Logic.teamOf(s, me) ? "You win the board" : nm[t] + " wins the board") + " · " + s.points + " pts";
      }
      if (s.moving) return "";
      var seat = s.turn, mine = shooter() === seat;
      var secs = live ? Math.max(0, SHOT_SECONDS - Math.floor((s.updates - turnStart) / 60)) : -1;
      var clock = live && info.state === "running" ? " · " + secs + " s" : "";
      if (live && s.turn !== s.me) return nm[seat] + "'s shot" + clock;
      if (live && sent === s.shots) return "Shooting…";
      if (s.cpu >= 0 && seat === s.cpu) return info.short ? "Computer aiming…" : "The computer is aiming…";
      var who = s.mode === "two" || s.mode === "doubles" ? nm[seat] + ": " : "";
      if (!mine) return "";
      if (drag && drag.kind === "aim") return aim.ok ? "Let go to shoot · power " + Math.round(aim.power) : "Pull back, away from the board";
      return who + (info.short ? "place, pull back" : "place the striker, pull back from it") + clock;
    }
    function header(g, nm) {
      var lcd = g.kind === "lcd", white = s.white;
      for (var t = 0; t < 2; t++) {
        var right = t === 1, colour = t === white ? "w" : "b";
        var label = s.mode === "doubles" ? (nm[t] + " & " + nm[t + 2]) : nm[t];
        var on = !s.over && Logic.teamOf(s, s.turn) === t;
        var x = right ? 230 : 10;
        coin(g, right ? x - 6 : x + 6, 16, colour, 5.4);
        g.text(label.slice(0, 14), right ? x - 15 : x + 15, 14, { size: 10, align: right ? "right" : "left", ci: 0 });
        g.text(s.pocketed[t].length + " / 9" + (s.queenBy === t ? " + queen" : ""), right ? x - 15 : x + 15, 26, { size: 9, align: right ? "right" : "left", ci: 0, a: 0.8 });
        if (on) g.line(right ? 140 : 10, 32, right ? 230 : 100, 32, lcd ? 0 : 7, 2.5, { cap: "butt" });
      }
      // the queen in the middle: on the board, waiting for its cover, or whose
      coin(g, 120, 16, "q", 5.4, s.queen === "on" ? 1 : 0.9);
      g.text(s.queen === "on" ? "queen" : s.queen === "pending" ? "cover!" : "taken", 120, 30, { size: 7, align: "center", ci: 0, a: 0.8 });
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", nm = names(), i;
      header(g, nm);
      g.layer("carrom-board", 1, drawBoard);
      // the current shooter's side
      var seat = s.turn, side = Logic.sideOf(s, seat);
      for (i = 1; i < s.pieces.length; i++) {
        var p = s.pieces[i];
        if (!p.on) continue;
        var xy = toScreen(p.x, p.y);
        coin(g, xy[0], xy[1], p.k, p.r / U);
      }
      var me = shooter();
      var st = s.pieces[0], sp;
      if (s.moving && st.on) { sp = toScreen(st.x, st.y); coin(g, sp[0], sp[1], "s", Logic.RS / U); }
      else if (!s.over && !s.moving) {
        var pt = Logic.strikerPreview(s, seat, me >= 0 ? aim.pos : 500);
        sp = toScreen(pt[0], pt[1]);
        coin(g, sp[0], sp[1], "s", Logic.RS / U, me >= 0 ? 1 : 0.75);
        if (me >= 0 && info.state === "running") {
          // where it goes: the aim line, longer for more power
          var S = Logic.SIDES[side], c = Logic.cosT(Math.round(aim.angle)) / 16384, sn = Logic.sinT(Math.round(aim.angle)) / 16384;
          var dx = c * S.ux + sn * S.vx, dy = c * S.uy + sn * S.vy;
          if (flip()) { dx = -dx; dy = -dy; }
          var len = 22 + aim.power * 0.9;
          g.line(sp[0] + dx * 9, sp[1] + dy * 9, sp[0] + dx * (9 + len), sp[1] + dy * (9 + len), lcd ? 0 : 7, 1.6, { dash: [3, 3], a: 0.9 });
          if (drag && drag.kind === "aim" && aim.ok) g.line(sp[0], sp[1], sp[0] - dx * aim.power * PULL_PX / 100, sp[1] - dy * aim.power * PULL_PX / 100, lcd ? 0 : 1, 2, { a: 0.7 });
          if (!info.reduce && Math.floor(s.updates / 30) % 2 === 0) g.ring(sp[0], sp[1], Logic.RS / U + 3, lcd ? 0 : 7, 1.2);
        }
      }
      // the line under the board: what's happening, and the power
      info.short = g.lowres;                       // the coarse looks: the short way to say it
      var text = status(nm, info);
      g.text(text, 120, 274, { size: text.length > 38 ? 9 : 10.5, align: "center", ci: 0, a: 0.92 });
      if (me >= 0 && (aim.charging || (drag && drag.kind === "aim" && aim.ok))) {
        g.rect(40, 283, 160, 8, 8, { r: 3, solid: !lcd });
        g.rect(40, 283, 160 * aim.power / 100, 8, lcd ? 0 : aim.power > 75 ? 1 : aim.power > 40 ? 3 : 4, { r: 3, solid: true });
      } else if (!s.over && s.foul && !s.moving) g.text("Foul — the striker was pocketed", 120, 290, { size: 9, align: "center", ci: lcd ? 0 : 1 });
      if (s.over && info.state !== "running" && info.state !== "paused") {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(24, 122, 192, 50, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("BOARD OVER", 120, 143, { size: 16, align: "center", ci: lcd ? 9 : 0 });
        g.text(text, 120, 161, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "carrom",
    name: "Carrom",
    modes: Logic.MODES.map(function (m) { return { id: m.id, label: m.label }; }),
    defaultMode: "easy",
    controls: "touch",
    buttons: [
      { action: "left", label: "◀", aria: "Striker left" },
      { action: "right", label: "▶", aria: "Striker right" },
      { action: "up", label: "⟲", aria: "Aim left" },
      { action: "down", label: "⟳", aria: "Aim right" },
      { action: "fire", label: "Shoot", aria: "Hold for power, let go to shoot", wide: true },
    ],
    race: false,
    lockstep: "turns",
    liveModes: ["phones"],
    help: "Pocket your own coins (the first player plays White) with the striker, then the red queen — and cover it by pocketing one of yours in the same or the next shot. Pocketing yours or the queen gives another shot. Pocketing the striker is a foul: a coin comes back. Drag along your baseline to place the striker, then pull back from it and let go (or ← → place, ↑ ↓ aim, hold Space for power). Play the computer, two or four (two teams) on one screen, or live from two phones (👥 Play with someone; 30 s a shot).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
