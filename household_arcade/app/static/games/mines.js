/* Household Arcade — Mines: input and drawing around the rules in mines-logic.js.
   Registers itself as "mines" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.MinesLogic;

  var MODES = [{ id: "easy", label: "Easy" }, { id: "medium", label: "Medium" }, { id: "hard", label: "Hard" },
    { id: "boards", label: "Shaped boards" }];
  // number colours: 1 blue, 2 green, 3 red, 4 purple, 5 orange, 6 cyan, 7 ink, 8 muted ink
  var NUM_CI = [0, 5, 4, 1, 6, 2, 7, 0, 0];

  function modeLabel(id) {
    for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i].label;
    return "";
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [];

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { open: "turn", flag: "bounce", unflag: "wall", mode: "powerup", clear: "row", level: "level", boom: "lose", gameover: "gameover", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind === "down") queue(Logic.touchDown(s, lx, ly));
        else if (kind === "move") queue(Logic.touchMove(s, lx, ly));
        else if (kind === "up") queue(Logic.touchUp(s, lx, ly));
      },
      draw: function (g, info) {
        var m = Logic.geom(s), cs = m.cs, fmt = Kit.fmt, lcd = g.kind === "lcd";
        var over = s.over, i, x, y;
        g.hud("SCORE " + fmt(s.score), "MINES " + (s.mines - s.flags));
        g.board(m.ox, m.oy, s.cols * cs, s.rows * cs);

        // the squares: a cached layer, redrawn only when the board changes
        g.layer("board", s.version + (over ? 0.5 : 0), function (lg) {
          for (var c = 0; c < s.cell.length; c++) {
            if (Logic.isHole(s, c)) continue;
            drawSquare(lg, c, m.ox + (c % s.cols) * cs, m.oy + ((c / s.cols) | 0) * cs, cs, over, lcd);
          }
        });

        // a finger held on a square: a ring fills toward the flag
        var t = s.touch;
        if (t && !t.moved && !t.done && t.i >= 0 && t.t > 4 && s.cell[t.i] !== Logic.OPEN) {
          var frac = Math.min(1, (t.t + info.alpha) / Logic.LONG_PRESS);
          x = m.ox + (t.i % s.cols) * cs + cs / 2; y = m.oy + ((t.i / s.cols) | 0) * cs + cs / 2;
          g.curve(function (c) { c.arc(x, y, cs * 0.62, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2); }, 6, 2.5);
        }

        // the keyboard cursor: a double outline
        if (s.showCur && !over) {
          x = m.ox + s.cur.x * cs; y = m.oy + s.cur.y * cs;
          outline(g, x - 1, y - 1, cs + 2, cs + 2, 6, 2.5);
          outline(g, x + 2.5, y + 2.5, cs - 5, cs - 5, 9, 1);
        }

        // bottom line: level and mode, flag mode
        if (s.boards) {
          g.text("BOARD " + s.level + "/" + s.boards.length + " · " + short(s.boardName, s.flagMode && !over ? 8 : 20), 10, 296, { size: 10, a: 0.75 });
        } else {
          g.text("LEVEL " + s.level + " · " + modeLabel(s.mode).toUpperCase(), 10, 296, { size: 10, a: 0.75 });
        }
        if (s.flagMode && !over) {
          drawFlag(g, 162, 291, 10, lcd);
          g.text("FLAG MODE", 230, 296, { size: 10, align: "right" });
        }

        var next = Logic.boardFor(s, s.level);
        if (s.pause > 0) {
          banner(g, "BOARD CLEAR!", next ? "NEXT: " + short(next.name, 14) + " · " + next.mines + " MINES"
            : "NEXT: " + Logic.minesFor(s.mode, s.level) + " MINES");
        } else if (s.won) banner(g, "ALL BOARDS CLEAR!", s.stats.boards + " BOARDS · YOU WIN");
        else if (over) banner(g, "BOOM!", s.stats.boards ? s.stats.boards + " BOARDS CLEARED" : "");
      },
    };

    function short(name, max) {
      name = String(name || "").toUpperCase();
      return name.length > max ? name.slice(0, max - 1) + "…" : name;
    }

    function outline(g, x, y, w, h, ci, lw) {
      g.line(x, y, x + w, y, ci, lw, { cap: "square" }); g.line(x, y + h, x + w, y + h, ci, lw, { cap: "square" });
      g.line(x, y, x, y + h, ci, lw, { cap: "square" }); g.line(x + w, y, x + w, y + h, ci, lw, { cap: "square" });
    }

    function banner(g, title, sub) {
      var by = 128;
      g.rect(30, by, 180, sub ? 46 : 32, 0, { solid: true, r: 6, a: 0.92 });
      g.text(title, 120, by + 20, { size: 16, align: "center", ci: 9 });
      if (sub) g.text(sub, 120, by + 37, { size: 10, align: "center", ci: 9 });
    }

    function drawSquare(g, c, x, y, cs, over, lcd) {
      var st = s.cell[c], isMine = s.mine[c];
      var showMine = over && isMine && st !== Logic.FLAG;
      if (st === Logic.OPEN || showMine) {
        // open: a flat, pale square (Retro LCD: nothing but a dot in the middle)
        if (c === s.hit) g.rect(x + 1, y + 1, cs - 2, cs - 2, 1, { solid: true, r: 2 });
        else if (!lcd) g.rect(x + 1, y + 1, cs - 2, cs - 2, 8, { a: 0.55, r: 2, edge: false });
        if (showMine) { drawMine(g, x + cs / 2, y + cs / 2, cs, lcd && c === s.hit); return; }
        var n = s.adj[c];
        if (n) g.text(String(n), x + cs / 2, y + cs / 2 + 0.5, { size: Math.round(cs * 0.66), align: "center", base: "middle", ci: NUM_CI[n] });
        else if (lcd) g.rect(x + cs / 2 - 1, y + cs / 2 - 1, 2, 2, 0, { solid: true });
        return;
      }
      // covered: a raised square (Retro LCD: an outlined square)
      if (lcd) g.rect(x + 1.5, y + 1.5, cs - 3, cs - 3, 8);
      else g.cell(x, y, cs, 5);
      if (st === Logic.FLAG) {
        drawFlag(g, x + cs / 2, y + cs / 2, cs, lcd);
        if (over && !isMine) {   // a wrong flag: crossed out
          g.line(x + 3, y + 3, x + cs - 3, y + cs - 3, 0, 2); g.line(x + cs - 3, y + 3, x + 3, y + cs - 3, 0, 2);
        }
      }
    }

    // a flag on a pole, centred on (cx, cy) in a square of size cs
    function drawFlag(g, cx, cy, cs, lcd) {
      var h = cs * 0.62, top = cy - h / 2, px = cx - cs * 0.12;
      g.line(px, top, px, top + h, lcd ? 0 : 0, Math.max(1.5, cs * 0.08), { cap: "butt" });
      g.poly([[px, top], [px + cs * 0.34, top + h * 0.24], [px, top + h * 0.48]], 1, { solid: true });
      g.line(px - cs * 0.16, top + h, px + cs * 0.16, top + h, 0, Math.max(1.5, cs * 0.08), { cap: "butt" });
    }

    // a mine: a round body with four spikes (white on the solid hit square in Retro LCD)
    function drawMine(g, cx, cy, cs, white) {
      var r = cs * 0.24, sp = cs * 0.36, ci = white ? 9 : 0;
      g.line(cx - sp, cy, cx + sp, cy, ci, Math.max(1.5, cs * 0.08));
      g.line(cx, cy - sp, cx, cy + sp, ci, Math.max(1.5, cs * 0.08));
      if (white) g.rect(cx - r, cy - r, r * 2, r * 2, 9, { solid: true });
      else g.circle(cx, cy, r, 0, { solid: true });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "mines",
    name: "Mines",
    modes: MODES,
    defaultMode: "easy",
    controls: "touch",
    buttons: [{ action: "alt", label: "🚩 Flag", aria: "Flag mode", wide: true }],
    help: "Tap a square to open it; hold a finger on a square (or turn on 🚩 Flag and tap) to flag a mine. Numbers say how many mines touch a square; tapping a number whose flags are all placed opens the rest around it. Keyboard: arrows move the cursor, Space opens, C or Shift flags. Shaped boards: clear each picture board in turn to win.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
