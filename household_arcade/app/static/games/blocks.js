/* Household Arcade — Falling Blocks: input and drawing around the rules in blocks-logic.js.
   Registers itself as "blocks" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.BlocksLogic;

  var MODES = [{ id: "classic", label: "Classic" }, { id: "fast", label: "Fast start" }, { id: "rising", label: "Rising floor" },
    { id: "challenge", label: "Challenges" }];
  // Cell sizes and positions are multiples of 6 so cells sit exactly on the Retro LCD and Pixel grids.
  var CS = 12, OX = 18, OY = 42, PANEL_X = 150;
  var TAP_UPDATES = 14;   // a touch shorter than this that hardly moved is a tap (turn)
  var FLICK = 54;         // logical px down within FLICK_UPDATES: drop

  function modeLabel(id) {
    for (var i = 0; i < MODES.length; i++) if (MODES[i].id === id) return MODES[i].label;
    return "";
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], touch = null;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; touch = null; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; touch = null; },
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
      sounds: { turn: "turn", hold: "powerup", drop: "hit", lock: "wall", row: "row", level: "level", rise: "lose", win: "win", gameover: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        // Drag sideways to move by columns, drag down to drop row by row, flick down to drop at once,
        // tap to turn.
        if (kind === "down") { touch = { x: lx, y: ly, cols: 0, rows: 0, t: s.updates, moved: false, dropped: false }; return; }
        if (!touch) return;
        var dx = lx - touch.x, dy = ly - touch.y;
        if (kind === "move") {
          if (touch.dropped) return;
          var cols = Math.round(dx / CS);
          if (cols !== touch.cols) { touch.cols += Logic.slide(s, cols - touch.cols) || 0; touch.moved = true; }
          if (dy > FLICK && s.updates - touch.t <= 10 && Math.abs(dy) > Math.abs(dx) * 1.5) {
            queue(Logic.press(s, "fire", true)); touch.dropped = true; touch.moved = true; return;
          }
          var rows = Math.floor(Math.max(0, dy) / CS);
          while (rows > touch.rows) { Logic.press(s, "down", true); Logic.press(s, "down", false); touch.rows++; touch.moved = true; }
          return;
        }
        if (kind === "up") {
          if (!touch.moved && Math.abs(dx) < 8 && Math.abs(dy) < 8 && s.updates - touch.t <= TAP_UPDATES) queue(Logic.press(s, "up", true));
          touch = null;
        }
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, i;
        var ch = s.challenges ? Logic.challenge(s) : null;
        var left = "SCORE " + fmt(s.score), right = "LEVEL " + s.level;
        if (ch) {
          right = s.level + "/" + s.challenges.length;
          if (left.length + right.length < 18) right = "CHALLENGE " + right;   // room for the word
        }
        g.hud(left, right);
        var visible = Logic.ROWS - Logic.HIDDEN;
        g.board(OX, OY, Logic.COLS * CS, visible * CS);

        // the stack: cached, redrawn only when it changes
        g.layer("stack", s.version, function (lg) {
          for (var r = Logic.HIDDEN; r < Logic.ROWS; r++) {
            if (s.clearing && s.clearing.rows.indexOf(r) >= 0) continue;
            for (var c = 0; c < Logic.COLS; c++) {
              var v = s.grid[r * Logic.COLS + c];
              if (v) lg.cell(OX + c * CS, OY + (r - Logic.HIDDEN) * CS, CS, v, { a: v === 8 ? 0.9 : 1 });
            }
          }
        });

        // full rows fade out (no flashing)
        if (s.clearing) {
          var fade = s.clearing.t / Logic.CLEAR_TIME;
          for (i = 0; i < s.clearing.rows.length; i++) {
            var ry = OY + (s.clearing.rows[i] - Logic.HIDDEN) * CS;
            g.rect(OX, ry, Logic.COLS * CS, CS, 9, { a: info.reduce ? 0.6 : 0.3 + 0.7 * fade, solid: true, r: 0 });
          }
        }

        // the falling piece and where it will land
        if (s.piece && info.state !== "over") {
          var ci = Logic.SHAPES[s.piece.t].ci, gy = Logic.ghostY(s);
          var cells = Logic.cellsOf(s.piece), ghost = Logic.cellsOf(s.piece, 0, gy - s.piece.y);
          if (gy !== s.piece.y) {
            for (i = 0; i < ghost.length; i++) {
              if (ghost[i][1] < Logic.HIDDEN) continue;
              g.rect(OX + ghost[i][0] * CS + 1.5, OY + (ghost[i][1] - Logic.HIDDEN) * CS + 1.5, CS - 3, CS - 3, 8, { r: 2, a: 0.9, stroke: true });
            }
          }
          for (i = 0; i < cells.length; i++) {
            if (cells[i][1] < Logic.HIDDEN) continue;
            g.cell(OX + cells[i][0] * CS, OY + (cells[i][1] - Logic.HIDDEN) * CS, CS, ci);
          }
        }

        // the side panel: the next pieces, lines, mode
        g.text("NEXT", PANEL_X, 54, { size: 10, a: 0.7 });
        for (i = 0; i < 3; i++) drawPiece(g, s.next[i], PANEL_X + 6, 60 + i * 30, 12, i ? 0.6 : 1);
        g.text("HOLD", PANEL_X, 166, { size: 10, a: 0.7 });
        if (s.hold) drawPiece(g, s.hold, PANEL_X + 6, 174, 12, s.canHold ? 1 : 0.5);
        else g.rect(PANEL_X + 6, 174, 48, 24, 8, { r: 3, a: 0.6 });
        g.text(ch ? "ROWS LEFT" : "LINES", PANEL_X, 218, { size: 10, a: 0.7 });
        g.text(fmt(ch ? s.goalLeft : s.lines), PANEL_X, 236, { size: 16 });
        if (ch) g.text("SPEED " + ch.speed, PANEL_X, 256, { size: 9, a: 0.7 });
        if (Logic.MODES[s.mode].rise) {
          var every = Logic.riseEvery(s), left = Math.max(0, every - s.riseT);
          g.text("RISE", PANEL_X, 252, { size: 10, a: 0.7 });
          g.rect(PANEL_X, 257, 72, 5, 8, { r: 2 });
          g.rect(PANEL_X, 257, 72 * left / every, 5, 1, { r: 2, solid: true });
        }
        g.text(modeLabel(s.mode).toUpperCase(), PANEL_X, 276, { size: 9, a: 0.7 });

        // a challenge's name and goal while its first piece waits
        if (ch && !s.piece && !s.clearing && s.wait > Logic.ENTRY && info.state !== "over") {
          var mid = OX + Logic.COLS * CS / 2;
          g.rect(OX + 6, 54, Logic.COLS * CS - 12, 60, 9, { r: 6, stroke: true, a: 0.95 });
          g.text("CHALLENGE " + s.level, mid, 72, { size: 11, align: "center" });
          g.text(fitName(ch.name, g.lowres ? 13 : 18), mid, 88, { size: 9, align: "center" });
          g.text("CLEAR " + ch.goal + " ROWS", mid, 104, { size: 9, align: "center", a: 0.85 });
        }
      },
    };

    // a challenge name, shortened to fit inside the well
    function fitName(n, max) { n = String(n).toUpperCase(); return n.length > max ? n.slice(0, max - 1).trim() + "." : n; }

    // a piece in the side panel, its top row at y (the I piece's box has an empty first row)
    function drawPiece(g, t, x, y, cs, a) {
      if (!t) return;
      var cells = Logic.CELLS[t][0], ci = Logic.SHAPES[t].ci, top = t === "I" ? 1 : 0;
      for (var i = 0; i < cells.length; i++) g.cell(x + cells[i][0] * cs, y + (cells[i][1] - top) * cs, cs, ci, { a: a });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "blocks",
    name: "Falling Blocks",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    // Hold on the left, ◀ ↻ ▶ in the middle with ▼ under ↻, Drop on the right: [col, row, colSpan, rowSpan]
    buttons: [
      { action: "alt", label: "Hold", aria: "Hold", place: [1, 1, 1, 2] },
      { action: "left", label: "◀", aria: "Left", place: [2, 1, 1, 1] }, { action: "up", label: "↻", aria: "Turn", place: [3, 1, 1, 1] },
      { action: "right", label: "▶", aria: "Right", place: [4, 1, 1, 1] }, { action: "down", label: "▼", aria: "Down", place: [3, 2, 1, 1] },
      { action: "fire", label: "Drop", aria: "Drop", place: [5, 1, 1, 2] },
    ],
    help: "← → move, ↑ turns, ↓ drops faster, Space drops at once, C or Shift holds a piece for later (once per piece). On a phone, drag the piece sideways, tap to turn, drag down to drop faster or flick down to drop. Full rows clear; more rows at once score more.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
