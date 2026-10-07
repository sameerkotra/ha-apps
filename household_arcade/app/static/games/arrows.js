/* Household Arcade — Arrow Release: input and drawing around the rules in arrows-logic.js.
   Registers itself as "arrows" with window.ArcadeGames.

   Each arrow is a thick line through its cells with a big chevron head, so its direction never depends on colour
   (High contrast and Retro LCD draw them in one ink). A released arrow flies off with a short trail; a bumped one
   shakes and its blocker flashes. The keyboard cursor is a ring; a hint a dashed ring on the arrow's head. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.ArrowsLogic;

  var MODES = [{ id: "little", label: "Little 5 × 5 (gentle)" }, { id: "classic", label: "8 × 8" }, { id: "big", label: "12 × 12" },
    { id: "twisty", label: "Twisty 10 × 10" }, { id: "levels", label: "Levels" }, { id: "book", label: "Picture boards" }];
  var LABELS = { little: "LITTLE", classic: "8 × 8", big: "12 × 12", twisty: "TWISTY" };
  var BX = 10, BY = 42, BS = 220, MSG_Y = 276, INKS = [1, 5, 4, 6, 2, 7];

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [];
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function cellAt(x, y) {
      var n = s.board.n, cs = BS / n, c = Math.floor((x - BX) / cs), r = Math.floor((y - BY) / cs);
      return c < 0 || r < 0 || c >= n || r >= n ? -1 : r * n + c;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints, startLevel: opts.startLevel, levels: opts.levels }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { release: "launch", bump: "wall", heart: "hit", hint: "powerup", nohint: "wall", clear: "row", level: "level", win: "win", lose: "gameover" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = cellAt(lx, ly);
        if (i >= 0) queue(Logic.tapCell(s, i));
      },
      draw: draw,
    };

    function centre(n, cs, i) { return [BX + (i % n) * cs + cs / 2, BY + Math.floor(i / n) * cs + cs / 2]; }
    function inkFor(g, a) { return g.kind === "lcd" || g.kind === "contrast" ? 0 : INKS[a % INKS.length]; }
    function head(g, x, y, dir, size, ci) {
      var dx = Logic.DX[dir], dy = Logic.DY[dir], px = -dy, py = dx;
      g.poly([[x + dx * size, y + dy * size], [x - dx * size * 0.4 + px * size * 0.85, y - dy * size * 0.4 + py * size * 0.85],
        [x - dx * size * 0.1, y - dy * size * 0.1], [x - dx * size * 0.4 - px * size * 0.85, y - dy * size * 0.4 - py * size * 0.85]], ci, { solid: true });
    }
    function arrow(g, n, cs, cells, dir, ci, off, a) {
      var w = Math.max(2, cs * 0.28), pts = [];
      for (var i = cells.length - 1; i >= 0; i--) { var c = centre(n, cs, cells[i]); pts.push([c[0] + off[0], c[1] + off[1]]); }
      var tail = pts[0];
      g.circle(tail[0], tail[1], w * 0.55, ci, { solid: true, a: a });
      for (i = 1; i < pts.length; i++) g.line(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1], ci, w, { a: a, cap: "round" });
      var h = pts[pts.length - 1];
      head(g, h[0], h[1], dir, cs * 0.42, ci);
    }
    function ring(g, n, cs, i, ci, w, dash) {
      var x = BX + (i % n) * cs, y = BY + Math.floor(i / n) * cs, o2 = dash ? { dash: [3, 3], cap: "butt" } : { cap: "butt" };
      g.line(x + 1, y + 1, x + cs - 1, y + 1, ci, w, o2); g.line(x + 1, y + cs - 1, x + cs - 1, y + cs - 1, ci, w, o2);
      g.line(x + 1, y + 1, x + 1, y + cs - 1, ci, w, o2); g.line(x + cs - 1, y + 1, x + cs - 1, y + cs - 1, ci, w, o2);
    }

    function draw(g, info) {
      var b = s.board, n = b.n, cs = BS / n, i, lcd = g.kind === "lcd";
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      g.board(BX, BY, BS, BS);
      if (cs >= 14 && !lcd) for (i = 0; i < n * n; i++) {
        if (b.mask && !b.mask[i]) continue;              // a picture board: dots only on the picture
        var c = centre(n, cs, i);
        g.circle(c[0], c[1], 1, 8, { solid: true, a: 0.7 });
      }
      for (var a = 0; a < b.arrows.length; a++) {
        var arr = b.arrows[a];
        if (arr.gone) continue;
        var off = [0, 0];
        if (s.bump && s.bump.arrow === a && !info.reduce) {
          var k = s.bump.t < 9 ? s.bump.t / 9 : (18 - s.bump.t) / 9;
          off = [Logic.DX[arr.dir] * cs * 0.3 * k, Logic.DY[arr.dir] * cs * 0.3 * k];
        }
        arrow(g, n, cs, arr.cells, arr.dir, inkFor(g, a), off, 1);
      }
      if (!info.reduce) for (i = 0; i < s.flying.length; i++) {
        var f = s.flying[i], d = f.t * f.t * cs * 0.06;
        arrow(g, n, cs, f.cells, f.dir, lcd ? 0 : 8, [Logic.DX[f.dir] * d, Logic.DY[f.dir] * d], Math.max(0, 1 - f.t / Logic.FLY_T));
      }
      if (s.bump && s.bump.at >= 0) ring(g, n, cs, s.bump.at, lcd ? 0 : 1, 2, false);
      if (s.hint >= 0 && !b.arrows[s.hint].gone) ring(g, n, cs, b.arrows[s.hint].cells[0], lcd ? 0 : 3, 3, true);
      if (info.state === "running" || info.state === "paused") ring(g, n, cs, s.cursor, lcd ? 0 : 5, 2, false);
      var hearts = "";
      for (i = 0; i < s.maxHearts; i++) hearts += i < s.hearts ? "♥" : "·";
      g.text((s.maxHearts ? hearts + "   " : "") + "ARROWS " + Logic.left(s), 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.hint >= 0 ? "Tap the ringed arrow" : s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" :
        Logic.left(s) === b.arrows.length ? (s.book ? "“" + s.list[s.level - 1].name + "”" : "Tap an arrow with a clear way out") : s.levels ? "Score so far " + s.runScore : "A bump costs " + (s.maxHearts ? "a heart" : "nothing");
      g.text(tip, 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      if (s.phase === "clear" || s.won || (s.over && s.cause === "hearts")) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text(s.over && !s.won ? "OUT OF HEARTS" : s.won ? "ALL CLEAR!" : "CLEARED!", 120, 147, { size: 15, align: "center", ci: lcd ? 9 : 0 });
        g.text(s.over && !s.won ? "SCORE " + s.runScore : s.won ? "SCORE " + s.runScore : "+" + s.lastBoardScore + "  NEXT: LEVEL " + (s.level + 1),
          120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "arrows",
    name: "Arrow Release",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "alt", label: "💡 Hint", aria: "Hint: show an arrow that can leave", wide: true }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Clear the board. Tap an arrow and it flies off the way it points — if nothing is in its way. If something is, it bumps and you lose a heart (three hearts; Little has none). Releasing arrows only ever makes room, so look for the ones with a clear way out. Keyboard: arrows move the ring, Space releases. With the Hints option on, Hint (or C) rings an arrow that can leave. Levels: numbered boards that get harder; Picture boards: arrows filling a picture (an AI model can add more). Both carry on from your next level.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
