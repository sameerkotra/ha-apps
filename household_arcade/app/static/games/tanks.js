/* Household Arcade — Tank Battle: input and drawing around the rules in tanks-logic.js.
   Registers itself as "tanks" with window.ArcadeGames. Modes "together" and "against" are live duels on two
   phones (SPEC §13.4): each phone drives its own tank (seat 1 green-ish, seat 2 blue); against each other, seat
   2's phone draws the arena turned half-way round so each player's own flag is at the bottom. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.TanksLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "easy", label: "Easy" },
    { id: "together", label: "Two phones · Together" }, { id: "against", label: "Two phones · Against each other" }];
  var TURN = { up: "down", down: "up", left: "right", right: "left" };
  var KEYS = ["up", "down", "left", "right", "fire"];
  var OX = Logic.OX, OY = Logic.OY, C = Logic.CELL, T = Logic.TILE, N = Logic.N;

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prev = {}, game = 0, lastSteer = 0;
    var me = opts.live && opts.live.seat === 2 ? 1 : 0;
    var other = String((opts.names && opts.names[1 - me]) || "").split(" ")[0].toUpperCase().slice(0, 7) || (me ? "P1" : "P2");
    function flipped() { return s.two === "against" && me === 1; }
    // the arena as I see it: seat 2 against each other sees every box (x, w) at (ARENA − x − w)
    function AX(x, w) { return flipped() ? Logic.ARENA - x - (w || 0) : x; }

    function snap() {
      prev = {};
      prev.p = [s.player.x, s.player.y];
      if (s.p2) prev.p2 = [s.p2.x, s.p2.y];
      for (var i = 0; i < s.enemies.length; i++) prev[s.enemies[i].id] = [s.enemies[i].x, s.enemies[i].y];
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; game++; snap(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; game++; snap(); },
      step: function () {
        snap();
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return me && s.two ? s.score2 : s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s, me); },
      // live duel (two phones): inputs reach the rules here, on both phones on the same update
      apply: function (player, action, value) { return Logic.press(s, action, value, player); },
      checksum: function () { return Logic.checksum(s); },
      report: function () { return Logic.report(s); },
      sounds: { fire: "turn", brick: "hit", steel: "wall", hit: "break", lifeLost: "lose", clear: "bonus", level: "level",
        win: "win", gameover: "gameover" },
      input: function (action, down, act) {
        if (!s.two) {
          var evs = Logic.press(s, action, down);
          for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
          return;
        }
        var a = flipped() && TURN[action] ? TURN[action] : action;     // my up is the arena's down, turned round
        if (KEYS.indexOf(a) < 0) return;
        if (act) act(a, down ? 1 : 0); else Logic.press(s, a, down, me);
      },
      pointer: function (kind, lx, ly, x, y, act) {
        if (!s.two) {
          if (kind === "up") Logic.touch(s, null);
          else Logic.touch(s, lx, ly, kind === "down");
          return;
        }
        var send = function (k, v) { if (act) act(k, v); else Logic.press(s, k, v, me); };
        if (kind === "up") { if (lastSteer) { lastSteer = 0; send("steer", 0); } return; }
        var ax = lx - OX, ay = ly - OY;
        if (flipped()) { ax = Logic.ARENA - ax; ay = Logic.ARENA - ay; }
        var v = Logic.steerFor(s, me, ax, ay);
        if (v === "fire") {                       // a tap on my own tank fires
          if (kind === "down") { send("fire", 1); send("fire", 0); }
          if (lastSteer) { lastSteer = 0; send("steer", 0); }
        } else if (v !== lastSteer) { lastSteer = v; send("steer", v); }
      },
      draw: function (g, info) {
        if (s.two) { drawTwo(g, info); return; }
        var a = info.alpha, lcd = g.kind === "lcd", i;
        var enemyCi = lcd ? 8 : 1;
        g.hud("SCORE " + Kit.fmt(s.score), "ARENA " + s.level + "/" + s.arenas.length);
        g.board(OX, OY, Logic.ARENA, Logic.ARENA);
        if (!lcd) g.rect(OX, OY, Logic.ARENA, Logic.ARENA, 8, { r: 0, a: g.kind === "contrast" ? 0.35 : 0.22, edge: false });

        // walls and water (a cached layer, redrawn when a brick breaks)
        g.layer("tanks-map", game + ":" + s.mapVer, function (lg) { drawMap(lg, false); });

        drawFlag(g);

        // tanks
        for (i = 0; i < s.enemies.length; i++) {
          var e = s.enemies[i], pe = prev[e.id] || [e.x, e.y];
          var ex = pe[0] + (e.x - pe[0]) * a, ey = pe[1] + (e.y - pe[1]) * a;
          if (e.arrive > 0) {
            var grow = info.reduce ? 1 : 1 - e.arrive / Logic.ARRIVE * 0.6;
            g.star(OX + ex + 9, OY + ey + 9, 8 * grow, enemyCi, { a: 0.9 });
          } else drawTank(g, ex, ey, e.dir, enemyCi, false);
        }
        var p = s.player;
        if (!p.dead) {
          var pp = prev.p || [p.x, p.y];
          var px = pp[0] + (p.x - pp[0]) * a, py = pp[1] + (p.y - pp[1]) * a;
          drawTank(g, px, py, p.dir, 3, true);
          if (p.shield > 0 && info.state !== "over") g.ring(OX + px + 9, OY + py + 9, 13, 7, 1.5, { a: 0.9 });
        }

        // shells
        for (i = 0; i < s.shells.length; i++) {
          var sh = s.shells[i];
          g.rect(OX + sh.x - 2, OY + sh.y - 2, 4, 4, 0, { r: 1, solid: true });
        }
        // bushes over the tanks (a cached layer: bushes never change)
        g.layer("tanks-bush", game + ":" + s.level, function (lg) { drawMap(lg, true); });

        // explosions: a star and a ring that grows (just the star with reduce motion)
        for (i = 0; i < s.booms.length; i++) {
          var b = s.booms[i], r = b.big ? 14 : 10;
          g.star(OX + b.x, OY + b.y, r * (info.reduce ? 0.8 : 0.5 + b.t / 60), 2, { a: 0.9 });
          if (!info.reduce && !g.lowres) g.ring(OX + b.x, OY + b.y, r * (0.5 + b.t / 25), 3, 2, { a: Math.max(0, 1 - b.t / 30) });
        }

        // the bottom line: lives, and a mark for each enemy still to beat in this arena
        g.text("LIVES " + Math.max(0, s.lives), 6, 293, { size: 10 });
        var left = s.total - s.spawned + s.enemies.length;
        for (i = 0; i < left; i++) {
          var col = i % 10, row = Math.floor(i / 10);
          g.rect(231 - col * 7 - 5, 282 + row * 7, 5, 5, enemyCi, { r: 1, solid: true });
        }
        if (left) g.text(String(left), 231 - Math.min(left, 10) * 7 - 3, 293, { size: 10, align: "right" });

        // the arena's name at its start, and "arena clear" between arenas
        if (info.state !== "over") {
          if (s.intro > 0) banner(g, "ARENA " + s.level + "/" + s.arenas.length, s.name.toUpperCase());
          else if (s.clear > 0) banner(g, "ARENA CLEAR", "+" + Logic.CLEAR_POINTS);
        } else if (s.flagDown) banner(g, "FLAG DOWN", "");
        else if (s.won) banner(g, "ALL CLEAR", "");
      },
    };

    /* Two tanks: both players, their lives, one flag (together) or two (against each other). */
    function drawTwo(g, info) {
      var a = info.alpha, lcd = g.kind === "lcd", i, against = s.two === "against";
      var enemyCi = lcd ? 8 : 1, ci = [3, 5], myScore = me ? s.score2 : s.score;
      var right = against ? "ROUND " + s.level + " · " + s.wins[me] + "-" + s.wins[1 - me] : "ARENA " + s.level + "/" + s.arenas.length;
      g.hud("SCORE " + Kit.fmt(myScore), right);
      g.board(OX, OY, Logic.ARENA, Logic.ARENA);
      if (!lcd) g.rect(OX, OY, Logic.ARENA, Logic.ARENA, 8, { r: 0, a: g.kind === "contrast" ? 0.35 : 0.22, edge: false });
      g.layer("tanks-map", game + ":" + s.mapVer + ":" + (flipped() ? 1 : 0), function (lg) { drawMap(lg, false); });
      drawFlagAt(g, Logic.FLAG, s.flagDown, against ? ci[0] : 4);
      if (against) drawFlagAt(g, Logic.FLAG2, s.flag2Down, ci[1]);
      for (i = 0; i < s.enemies.length; i++) {
        var e = s.enemies[i], pe = prev[e.id] || [e.x, e.y];
        var ex = pe[0] + (e.x - pe[0]) * a, ey = pe[1] + (e.y - pe[1]) * a;
        if (e.arrive > 0) {
          var grow = info.reduce ? 1 : 1 - e.arrive / Logic.ARRIVE * 0.6;
          g.star(OX + AX(ex + 9), OY + AX(ey + 9), 8 * grow, enemyCi, { a: 0.9 });
        } else drawTank(g, ex, ey, e.dir, enemyCi, false);
      }
      [s.player, s.p2].forEach(function (p, n) {
        if (!p || p.dead) return;
        var pp = prev[n ? "p2" : "p"] || [p.x, p.y];
        var px = pp[0] + (p.x - pp[0]) * a, py = pp[1] + (p.y - pp[1]) * a;
        drawTank(g, px, py, p.dir, ci[n], true, n !== me);
        if (p.shield > 0 && info.state !== "over") g.ring(OX + AX(px + 9), OY + AX(py + 9), 13, 7, 1.5, { a: 0.9 });
      });
      for (i = 0; i < s.shells.length; i++) {
        var sh = s.shells[i];
        g.rect(OX + AX(sh.x) - 2, OY + AX(sh.y) - 2, 4, 4, 0, { r: 1, solid: true });
      }
      g.layer("tanks-bush", game + ":" + s.level + ":" + (flipped() ? 1 : 0), function (lg) { drawMap(lg, true); });
      for (i = 0; i < s.booms.length; i++) {
        var b = s.booms[i], r = b.big ? 14 : 10;
        g.star(OX + AX(b.x), OY + AX(b.y), r * (info.reduce ? 0.8 : 0.5 + b.t / 60), 2, { a: 0.9 });
        if (!info.reduce && !g.lowres) g.ring(OX + AX(b.x), OY + AX(b.y), r * (0.5 + b.t / 25), 3, 2, { a: Math.max(0, 1 - b.t / 30) });
      }
      // the bottom line: my lives and theirs (and the enemies still to come, together)
      var lives = [Math.max(0, s.lives), Math.max(0, s.lives2)];
      g.text("YOU " + lives[me] + "  " + other + " " + lives[1 - me], 6, 293, { size: 10 });
      if (!against) {
        var left = s.total - s.spawned + s.enemies.length;
        for (i = 0; i < Math.min(left, 10); i++) g.rect(231 - i * 7 - 5, 284, 5, 5, enemyCi, { r: 1, solid: true });
        if (left) g.text(String(left), 231 - Math.min(left, 10) * 7 - 3, 293, { size: 10, align: "right" });
      }
      var who = function (n) { return n === me + 1 ? "YOU" : other; };
      if (info.state !== "over" && !s.over) {
        if (s.intro > 0) banner(g, against ? "ROUND " + s.level : "ARENA " + s.level + "/" + s.arenas.length, s.name.toUpperCase());
        else if (s.clear > 0) {
          if (against) banner(g, s.roundWinner ? (s.roundWinner === me + 1 ? "YOU WIN THE ROUND" : other + " WINS THE ROUND") : "ROUND DRAWN", "+" + (s.roundWinner ? Logic.V_ROUND : 0));
          else banner(g, "ARENA CLEAR", "+" + Logic.CLEAR_POINTS);
        }
      } else if (against) banner(g, s.matchWinner ? (who(s.matchWinner) === "YOU" ? "YOU WIN!" : other + " WINS") : "A DRAW", s.wins[me] + " - " + s.wins[1 - me]);
      else if (s.flagDown) banner(g, "FLAG DOWN", "");
      else if (s.won) banner(g, "ALL CLEAR", "");
      else banner(g, "GAME OVER", "");
    }
    function drawFlagAt(g, f, down, ci) {
      var x = OX + AX(f.x, T), y = OY + AX(f.y, T);
      if (down) {
        g.line(x + 3, y + 15, x + 15, y + 12, 0, 2);
        g.line(x + 5, y + 4, x + 13, y + 12, 1, 2); g.line(x + 13, y + 4, x + 5, y + 12, 1, 2);
        return;
      }
      g.rect(x + 3, y + 14, 12, 3, 0, { r: 1, solid: true });
      g.line(x + 6, y + 2, x + 6, y + 15, 0, 1.6);
      g.poly([[x + 7, y + 2], [x + 16, y + 5.5], [x + 7, y + 9]], ci, {});
    }

    function banner(g, title, sub) {
      var lcd = g.kind === "lcd";
      g.rect(24, 124, 192, sub ? 50 : 34, lcd ? 0 : 9, { r: 6, solid: true, a: 0.94 });
      g.text(title, Kit.W / 2, sub ? 145 : 146, { size: 14, align: "center", ci: lcd ? 9 : 0 });
      if (sub) g.text(sub, Kit.W / 2, 164, { size: 11, align: "center", ci: lcd ? 9 : 0, bold: false });
    }

    function drawMap(g, bushes) {
      var lcd = g.kind === "lcd";
      for (var cy = 0; cy < N; cy++) for (var cx = 0; cx < N; cx++) {
        var c = s.map[cy * N + cx], x = OX + AX(cx * C, C), y = OY + AX(cy * C, C);
        if (bushes) {
          if (c !== Logic.BUSH) continue;
          if (g.lowres) {
            g.rect(x + 1, y + 1, 3, 3, 4, { solid: true }); g.rect(x + 5, y + 5, 3, 3, 4, { solid: true });
            if (!lcd) { g.rect(x + 5, y + 1, 3, 3, 4, { a: 0.7 }); g.rect(x + 1, y + 5, 3, 3, 4, { a: 0.7 }); }
          } else {
            g.circle(x + 3, y + 3.2, 3.4, 4, { a: 0.95, edge: false });
            g.circle(x + 6.4, y + 5.8, 3.4, 4, { a: 0.95, edge: false });
          }
          continue;
        }
        if (c === Logic.BRICK) {
          g.rect(x, y, C, C, 2, { r: 0, solid: true, bevel: true, edge: false });
          if (!g.lowres) {
            g.line(x, y + 4.5, x + C, y + 4.5, 0, 0.8, { a: 0.4, cap: "butt" });
            g.line(x + ((cx + cy) % 2 ? 3 : 6), y, x + ((cx + cy) % 2 ? 3 : 6), y + 4.5, 0, 0.8, { a: 0.4, cap: "butt" });
          }
        } else if (c === Logic.STEEL) {
          if (g.kind === "pixel") g.rect(x + 1, y + 1, C - 2, C - 2, 0, { a: 0.7 });
          else g.rect(x + 0.5, y + 0.5, C - 1, C - 1, 8, { r: 1, stroke: true });
          if (!g.lowres) g.rect(x + 3, y + 3, 3, 3, 9, { r: 0, edge: false });
        } else if (c === Logic.WATER) {
          if (!lcd) g.rect(x, y, C, C, 5, { r: 0, a: 0.55, edge: false });
          g.line(x + 1, y + 3, x + 4, y + 3, lcd ? 0 : 9, 1, { a: 0.9 });
          g.line(x + 5, y + 6, x + 8, y + 6, lcd ? 0 : 9, 1, { a: 0.9 });
        }
      }
    }

    function drawFlag(g) {
      var f = Logic.FLAG, x = OX + f.x, y = OY + f.y;
      if (s.flagDown) {
        g.line(x + 3, y + 15, x + 15, y + 12, 0, 2);
        g.line(x + 5, y + 4, x + 13, y + 12, 1, 2); g.line(x + 13, y + 4, x + 5, y + 12, 1, 2);
        return;
      }
      g.rect(x + 3, y + 14, 12, 3, 0, { r: 1, solid: true });
      g.line(x + 6, y + 2, x + 6, y + 15, 0, 1.6);
      g.poly([[x + 7, y + 2], [x + 16, y + 5.5], [x + 7, y + 9]], 4, {});
    }

    /** A tank seen from above: two treads, the hull, a round (yours) or square (enemy) turret and the barrel. */
    function drawTank(g, tx, ty, dir, ci, mine, theirs) {
      if (s.two) { tx = AX(tx, T); ty = AX(ty, T); if (flipped()) dir = (dir + 2) % 4; }
      var x = OX + g.snap(tx), y = OY + g.snap(ty), cx = x + 9, cy = y + 9;
      var vert = (dir & 1) === 0;
      if (vert) { g.rect(x + 1, y + 1, 4, 16, 0, { r: 1, a: 0.85, solid: true }); g.rect(x + 13, y + 1, 4, 16, 0, { r: 1, a: 0.85, solid: true }); }
      else { g.rect(x + 1, y + 1, 16, 4, 0, { r: 1, a: 0.85, solid: true }); g.rect(x + 1, y + 13, 16, 4, 0, { r: 1, a: 0.85, solid: true }); }
      g.rect(x + 3, y + 3, 12, 12, ci, { r: 2, bevel: true });
      g.line(cx, cy, cx + Logic.DX[dir] * 9, cy + Logic.DY[dir] * 9, 0, 3, { cap: "butt" });
      if (mine && !theirs) g.circle(cx, cy, 3.6, 0, { solid: true });
      else if (mine) g.ring(cx, cy, 3.2, 0, 1.6);              // the other player's tank: a hollow turret
      else g.rect(cx - 3, cy - 3, 6, 6, 0, { r: 0, solid: true });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "tanks",
    name: "Tank Battle",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    buttons: [
      { action: "left", label: "◀", aria: "Left", place: [1, 1, 1, 2] },
      { action: "up", label: "▲", aria: "Up", place: [2, 1, 1, 1] },
      { action: "down", label: "▼", aria: "Down", place: [2, 2, 1, 1] },
      { action: "right", label: "▶", aria: "Right", place: [3, 1, 1, 2] },
      { action: "fire", label: "Fire", aria: "Fire", wide: true, place: [4, 1, 2, 2] },
    ],
    lockstep: true,
    liveModes: ["together", "against"],
    help: "Arrow keys drive, Space fires (one shell at a time). On a phone use the arrows and Fire, or hold a finger on the game to drive toward it and tap your tank to fire. Guard the flag beside you; beat every enemy tank to reach the next arena. Two phones: Together, you both guard one flag; Against each other, each guards their own flag (at the bottom of your screen) — hit the other tank or their flag, first to 2 rounds.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
