/* Household Arcade — Snake Duel: input and drawing around the rules in snakeduel-logic.js.
   Registers itself as "snakeduel" with window.ArcadeGames (a two-player game: the shell sends player 2's keys,
   W A S D, as up2 / down2 / left2 / right2). */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.SnakeDuelLogic;

  var MODES = [{ id: "cpu", label: "Against the computer" }, { id: "two", label: "Two players" }];
  var SWIPE_PX = 18;     // CSS pixels of finger travel that count as a swipe (as in Snake)
  var COLOURS = [4, 5];  // player 1 green, player 2 blue

  function lerp(a, b, t) { return a + (b - a) * t; }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, touches = [];

    function names() {
      return s.mode === "two" ? ["P1", "P2"] : ["YOU", "CPU"];
    }
    function geometry(g) {
      // Cells sit exactly on the coarse grids: 9 px = 6 Retro LCD dots, 10 px = 5 Pixel pixels.
      var cs = g.kind === "lcd" ? 9 : 10;
      return { cs: cs, ox: (Kit.W - cs * Logic.COLS) / 2, oy: g.kind === "lcd" ? 45 : 42 };
    }

    /* Touch: the shell passes each finger's down / move / up with positions only (no finger id), so each finger
       is followed by where it was last seen: a move belongs to the nearest finger being followed. A finger
       steers the player of the half it began on (two players: left half blue, right half green), even if it
       slides across the middle. Against the computer any finger steers you. */
    function nearest(x, y) {
      var best = -1, bd = Infinity;
      for (var i = 0; i < touches.length; i++) {
        var d = Math.abs(touches[i].x - x) + Math.abs(touches[i].y - y);
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); touches = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); touches = []; },
      step: function () { return Logic.step(s); },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s, 0); },
      sounds: { eat: "eat", crash: "hit", go: "turn", match: "bonus", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { Logic.press(s, action, down); },
      pointer: function (kind, lx, ly, x, y) {
        if (kind === "down") {
          var p = s.mode === "two" && lx < Kit.W / 2 ? 1 : 0;
          touches = s.mode === "two" ? touches.filter(function (t) { return t.p !== p; }) : [];
          touches.push({ ax: x, ay: y, x: x, y: y, p: p });
          return;
        }
        var i = nearest(x, y);
        if (i < 0) return;
        var t = touches[i];
        if (kind === "up") { touches.splice(i, 1); return; }
        t.x = x; t.y = y;
        var dx = x - t.ax, dy = y - t.ay;
        if (Math.max(Math.abs(dx), Math.abs(dy)) < SWIPE_PX) return;
        var dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
        Logic.turn(s, t.p, dir);
        t.ax = x; t.ay = y;          // keep swiping for the next turn without lifting the finger
      },
      draw: function (g, info) {
        var G = geometry(g), cs = G.cs, ox = G.ox, oy = G.oy, fmt = Kit.fmt, i;
        var who = names(), lcd = g.kind === "lcd";
        g.hud("SCORE " + fmt(s.score), "ARENA " + s.level + "/" + s.arenas.length);
        g.board(ox, oy, cs * Logic.COLS, cs * Logic.ROWS);

        // walls: a cached layer, redrawn for a new arena or look (solid, so they differ from the snakes)
        g.layer("walls", s.wallsVersion + ":" + cs, function (lg) {
          for (var w = 0; w < s.walls.length; w++) {
            if (!s.walls[w]) continue;
            var wx = ox + (w % Logic.COLS) * cs, wy = oy + Math.floor(w / Logic.COLS) * cs;
            lg.rect(wx + 0.5, wy + 0.5, cs - 1, cs - 1, 6, { solid: true, r: g.kind === "modern" ? 2 : 0, bevel: true });
          }
        });

        // food: round, red
        for (i = 0; i < s.food.length; i++) {
          var f = s.food[i];
          g.circle(ox + f.x * cs + cs / 2, oy + f.y * cs + cs / 2, cs * 0.38, 1);
          if (!g.lowres) g.line(ox + f.x * cs + cs / 2, oy + f.y * cs + 2, ox + f.x * cs + cs / 2 + 2, oy + f.y * cs + 0.5, 4, 1.4);
        }

        var moving = s.phase === "play" && info.state === "running" && !g.lowres;
        drawSnake(g, info, G, 1, moving);
        drawSnake(g, info, G, 0, moving);

        // rounds won: filled pips (blue / player 2 on the left, green / player 1 on the right, like the board)
        var py = 256;
        g.text(who[1], 10, py + 4, { size: 11, ci: lcd ? 0 : 5 });
        g.text(who[0], 230, py + 4, { size: 11, ci: lcd ? 0 : 4, align: "right" });
        // the pips start after the name (a name is at most 5 letters, about 8 px each)
        var l0 = Math.max(44, 16 + who[1].length * 8), r0 = Math.min(196, 224 - who[0].length * 8);
        for (i = 0; i < Logic.WIN_ROUNDS; i++) {
          pip(g, l0 + i * 12, py, s.wins[1] > i, 5);
          pip(g, r0 - i * 12, py, s.wins[0] > i, 4);
        }
        g.text("ROUND " + s.round, Kit.W / 2, py + 4, { size: 11, align: "center", a: 0.8 });

        // messages under the board (over it they'd be hard to read on the walls), else the arena's name
        var msg = null, sub = null;
        if (s.over) {
          if (s.won) { msg = "ALL ARENAS WON!"; sub = "WELL PLAYED"; }
          else if (s.mode === "two") { msg = s.matchWinner ? who[s.matchWinner - 1] + " WINS THE MATCH" : "A DRAWN MATCH"; sub = "ARENA " + s.level; }
          else { msg = s.matchWinner === 2 ? "CPU WINS THE MATCH" : "A DRAWN MATCH"; sub = "ARENA " + s.level; }
        } else if (s.phase === "ready") {
          msg = s.round === 1 ? "ARENA " + s.level : "GET READY";
          sub = s.round === 1 ? s.arenaName.toUpperCase() : null;
        } else if (s.phase === "end") {
          msg = s.roundWinner ? who[s.roundWinner - 1] + (who[s.roundWinner - 1] === "YOU" ? " WIN" : " WINS") + " THE ROUND"
            : s.roundCause === "time" ? "TIME UP: A DRAW" : "BOTH CRASHED: A DRAW";
          sub = s.roundCause === "time" ? "THE LONGER SNAKE WINS" : s.roundCause === "head" ? "HEAD ON" : null;
        } else if (s.phase === "match") {
          msg = "YOU WIN THE MATCH!"; sub = "NEXT: ARENA " + (s.level + 1);
        }
        if (msg) {
          g.text(msg, Kit.W / 2, 276, { size: 12, align: "center" });
          if (sub) g.text(sub, Kit.W / 2, 291, { size: 9, align: "center", a: 0.8 });
        } else {
          g.text((s.arenaName + " · first to " + Logic.WIN_ROUNDS).toUpperCase(), Kit.W / 2, 282, { size: 10, align: "center", a: 0.7 });
        }
      },
    };

    function pip(g, x, y, on, ci) {
      if (on) g.circle(x, y, 4, ci, { solid: true });
      else if (g.lowres) g.circle(x, y, 4, 8);
      else g.ring(x, y, 3.5, 8, 1.5);
    }

    /* A snake: body cells (player 1 solid, player 2 with a hole in each cell, so they differ without colour),
       the head sliding into its next cell in the smooth looks, eyes, and a cross on a crashed head. */
    function drawSnake(g, info, G, n, moving) {
      var sn = s.snakes[n], body = sn.body, len = body.length, cs = G.cs, ox = G.ox, oy = G.oy, ci = COLOURS[n], k;
      var p = moving ? Math.min(0.999, sn.progress + info.alpha * s.cps / 60) : 0;
      var dir = sn.queue.length ? sn.queue[0] : sn.dir, d = Logic.DIRS[dir], head = body[0];
      var nx = head.x + d[0], ny = head.y + d[1];
      var slide = moving && !sn.crashed && !Logic.isWall(s, nx, ny) && !Logic.bodyAt(s, nx, ny);
      var tailMoves = slide && sn.grow === 0 && Logic.foodAt(s, nx, ny) < 0;
      for (k = len - 1; k >= (slide ? 0 : 1); k--) {
        var c = body[k], cx = c.x, cy = c.y;
        if (k === len - 1 && tailMoves && len > 1) { cx = lerp(c.x, body[len - 2].x, p); cy = lerp(c.y, body[len - 2].y, p); }
        bodyCell(g, ox + cx * cs, oy + cy * cs, cs, ci, n);
      }
      var hx = slide ? lerp(head.x, nx, p) : head.x, hy = slide ? lerp(head.y, ny, p) : head.y;
      var x = ox + hx * cs, y = oy + hy * cs;
      if (g.lowres) g.rect(x, y, cs - 1.6, cs - 1.6, sn.crashed ? 1 : ci, { solid: true });   // on the cells' grid, with their gap
      else g.rect(x + 0.5, y + 0.5, cs - 1, cs - 1, sn.crashed ? 1 : ci, { solid: true, r: g.kind === "modern" ? 3 : 0 });
      if (sn.crashed) {
        g.line(x + 2, y + 2, x + cs - 2, y + cs - 2, 0, 1.6);
        g.line(x + cs - 2, y + 2, x + 2, y + cs - 2, 0, 1.6);
      } else if (!g.lowres) {
        var hd = Logic.DIRS[sn.dir], ex = x + cs / 2 + hd[0] * cs * 0.18, ey = y + cs / 2 + hd[1] * cs * 0.18;
        var px = -hd[1] * cs * 0.22, py = hd[0] * cs * 0.22;
        g.circle(ex + px, ey + py, 1.3, 9, { edge: false });
        g.circle(ex - px, ey - py, 1.3, 9, { edge: false });
      }
      if (s.phase === "ready" && !s.over) {     // who is who, over each head
        g.text(names()[n], ox + head.x * cs + cs / 2, oy + head.y * cs - 4 + (n === 0 ? cs * 2 + 10 : 0), { size: 9, align: "center", ci: g.kind === "lcd" ? 0 : ci });
      }
    }
    function bodyCell(g, x, y, cs, ci, n) {
      if (g.kind === "lcd") {
        if (n === 0) g.cell(x, y, cs, ci);                  // outline with a centre dot
        else g.rect(x + 0.75, y + 0.75, cs - 1.5, cs - 1.5, 9);   // a hollow outline
        return;
      }
      g.cell(x, y, cs, ci, { a: 0.9 });
      if (n === 1) g.rect(x + cs * 0.35, y + cs * 0.35, cs * 0.3, cs * 0.3, 9, { solid: true, r: 0, edge: false });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "snakeduel",
    name: "Snake Duel",
    modes: MODES,
    defaultMode: "cpu",
    controls: "touch",
    players: 2,
    help: "Make the other snake crash! Green (player 1, on the right): arrow keys. Blue (player 2, on the left): W A S D. " +
      "On a phone, swipe on the right half of the game to steer green and on the left half to steer blue (against the " +
      "computer, swipe anywhere). Heads meeting is a draw. First to 3 rounds wins the match. P or Esc pauses.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
