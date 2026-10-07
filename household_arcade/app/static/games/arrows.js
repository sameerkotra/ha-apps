/* Household Arcade — Arrow Release: input and drawing around the rules in arrows-logic.js.
   Registers itself as "arrows" with window.ArcadeGames.

   Each arrow is a thin line through its cells — its tail — with a chevron head, so its direction never depends on
   colour (High contrast and Retro LCD draw them in one ink). A released arrow flies off with a short trail; a bumped
   one shakes and its blocker flashes. The keyboard cursor is a ring; a hint a dashed ring on the arrow's head.

   Zoom: the big boards hold a lot of small arrows, so the board can be looked at up close — pinch with two fingers,
   turn the mouse wheel, or press − / ＋ under the game; drag with one finger (or the mouse) to move around while
   zoomed in. A tap that didn't move releases the arrow under it. The zoom is the view only, never part of the game. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.ArrowsLogic;

  var MODES = [{ id: "little", label: "Little 5 × 5 (gentle)" }, { id: "classic", label: "14 × 14" }, { id: "big", label: "20 × 20" },
    { id: "twisty", label: "Twisty 16 × 16" }, { id: "levels", label: "Levels" }, { id: "book", label: "Picture boards" }];
  var LABELS = { little: "LITTLE", classic: "14 × 14", big: "20 × 20", twisty: "TWISTY" };
  var BX = 10, BY = 42, BS = 220, MSG_Y = 276, INKS = [1, 5, 4, 6, 2, 7];
  var MIN_CELL = 26, TAP_SLOP = 6, ZOOM_STEP = 1.5;

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [];
    // the view: zoom z (1 = whole board) and the board point at the view's top left (camX, camY, in board px)
    var z = 1, camX = 0, camY = 0, touches = {}, pinch = null, press = null, lastBoard = null;
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    function maxZoom() { return Math.max(1, MIN_CELL * s.board.n / BS); }
    function clampView() {
      z = Math.max(1, Math.min(maxZoom(), z));
      var span = BS - BS / z;
      camX = Math.max(0, Math.min(span, camX)); camY = Math.max(0, Math.min(span, camY));
    }
    function resetView() { z = 1; camX = 0; camY = 0; touches = {}; pinch = null; press = null; lastBoard = s ? s.board : null; }
    /** Zoom by `f` keeping the board point under (lx, ly) where it is. */
    function zoomAt(f, lx, ly) {
      var wx = (lx - BX) / z + camX, wy = (ly - BY) / z + camY;
      z = z * f; clampView();
      camX = wx - (lx - BX) / z; camY = wy - (ly - BY) / z; clampView();
    }
    function cellSize() { return BS / s.board.n * z; }
    function originX() { return BX - camX * z; }
    function originY() { return BY - camY * z; }
    function cellAt(x, y) {
      if (x < BX || y < BY || x >= BX + BS || y >= BY + BS) return -1;
      var n = s.board.n, cs = cellSize(), c = Math.floor((x - originX()) / cs), r = Math.floor((y - originY()) / cs);
      return c < 0 || r < 0 || c >= n || r >= n ? -1 : r * n + c;
    }
    /** Keep the keyboard ring in view when zoomed in. */
    function follow(i) {
      if (z <= 1 || i < 0) return;
      var n = s.board.n, w = BS / n, x = (i % n) * w, y = Math.floor(i / n) * w, view = BS / z;
      if (x < camX) camX = x; else if (x + w > camX + view) camX = x + w - view;
      if (y < camY) camY = y; else if (y + w > camY + view) camY = y + w - view;
      clampView();
    }
    function count() { var k = 0; for (var id in touches) if (touches.hasOwnProperty(id)) k++; return k; }
    function two() { var out = []; for (var id in touches) if (touches.hasOwnProperty(id)) out.push(touches[id]); return out.slice(0, 2); }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints, startLevel: opts.startLevel, levels: opts.levels }); pending = []; resetView(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; resetView(); },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        if (s.board !== lastBoard) resetView();           // a new board: the whole board again
        return evs;
      },
      logic: function () { return s; },
      view: function () { return { z: z, camX: camX, camY: camY, maxZoom: maxZoom() }; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { release: "launch", bump: "wall", heart: "hit", hint: "powerup", nohint: "wall", clear: "row", level: "level", win: "win", lose: "gameover" },
      input: function (action, down) {
        if (action === "zoomin" || action === "zoomout") {
          if (down) zoomAt(action === "zoomin" ? ZOOM_STEP : 1 / ZOOM_STEP, BX + BS / 2, BY + BS / 2);
          return;
        }
        queue(Logic.press(s, action, down));
        if (down) follow(s.cursor);
      },
      pointer: function (kind, lx, ly, cx, cy, id) {
        id = id == null ? 0 : id;
        if (kind === "down") {
          touches[id] = { x: lx, y: ly };
          if (count() >= 2) {
            var p = two(), dx = p[1].x - p[0].x, dy = p[1].y - p[0].y;
            pinch = { d: Math.max(8, Math.sqrt(dx * dx + dy * dy)), z: z, mx: (p[0].x + p[1].x) / 2, my: (p[0].y + p[1].y) / 2,
              wx: ((p[0].x + p[1].x) / 2 - BX) / z + camX, wy: ((p[0].y + p[1].y) / 2 - BY) / z + camY };
            press = null;
          } else press = { id: id, x: lx, y: ly, lx: lx, ly: ly, moved: false };
        } else if (kind === "move") {
          if (!touches[id]) return;
          touches[id] = { x: lx, y: ly };
          if (pinch && count() >= 2) {
            var q = two(), ex = q[1].x - q[0].x, ey = q[1].y - q[0].y, d = Math.max(8, Math.sqrt(ex * ex + ey * ey));
            var mx = (q[0].x + q[1].x) / 2, my = (q[0].y + q[1].y) / 2;
            z = pinch.z * d / pinch.d; clampView();
            camX = pinch.wx - (mx - BX) / z; camY = pinch.wy - (my - BY) / z; clampView();
          } else if (press && press.id === id) {
            if (!press.moved && Math.abs(lx - press.x) + Math.abs(ly - press.y) > TAP_SLOP) press.moved = true;
            if (press.moved && z > 1) { camX -= (lx - press.lx) / z; camY -= (ly - press.ly) / z; clampView(); }
            press.lx = lx; press.ly = ly;
          }
        } else if (kind === "up") {
          if (press && press.id === id && !press.moved && !pinch) {
            var i = cellAt(press.x, press.y);
            if (i >= 0) queue(Logic.tapCell(s, i));
          }
          delete touches[id];
          if (press && press.id === id) press = null;
          if (count() < 2) pinch = null;
          if (count() === 0) press = null;
        }
      },
      wheel: function (lx, ly, dy) {
        if (!(lx >= BX && ly >= BY && lx < BX + BS && ly < BY + BS)) return;
        zoomAt(dy < 0 ? 1.25 : 1 / 1.25, lx, ly);
      },
      draw: draw,
    };

    function inkFor(g, a) { return g.kind === "lcd" || g.kind === "contrast" ? 0 : INKS[a % INKS.length]; }
    function head(g, x, y, dir, size, ci) {
      var dx = Logic.DX[dir], dy = Logic.DY[dir], px = -dy, py = dx;
      g.poly([[x + dx * size, y + dy * size], [x - dx * size * 0.45 + px * size * 0.75, y - dy * size * 0.45 + py * size * 0.75],
        [x - dx * size * 0.15, y - dy * size * 0.15], [x - dx * size * 0.45 - px * size * 0.75, y - dy * size * 0.45 - py * size * 0.75]], ci, { solid: true });
    }

    function draw(g, info) {
      var b = s.board, n = b.n, cs = cellSize(), ox = originX(), oy = originY(), i, lcd = g.kind === "lcd";
      function centre(c) { return [ox + (c % n) * cs + cs / 2, oy + Math.floor(c / n) * cs + cs / 2]; }
      function arrow(cells, dir, ci, off, a) {
        var w = Math.max(1.2, cs * 0.16), pts = [];
        for (var k = cells.length - 1; k >= 0; k--) { var c = centre(cells[k]); pts.push([c[0] + off[0], c[1] + off[1]]); }
        var tail = pts[0];
        g.circle(tail[0], tail[1], w * 0.9, ci, { solid: true, a: a });
        for (k = 1; k < pts.length; k++) g.line(pts[k - 1][0], pts[k - 1][1], pts[k][0], pts[k][1], ci, w, { a: a, cap: "round" });
        var h = pts[pts.length - 1];
        head(g, h[0], h[1], dir, cs * 0.4, ci);
      }
      function ring(c, ci, w, dash) {
        var x = ox + (c % n) * cs, y = oy + Math.floor(c / n) * cs, o2 = dash ? { dash: [3, 3], cap: "butt" } : { cap: "butt" };
        g.line(x + 1, y + 1, x + cs - 1, y + 1, ci, w, o2); g.line(x + 1, y + cs - 1, x + cs - 1, y + cs - 1, ci, w, o2);
        g.line(x + 1, y + 1, x + 1, y + cs - 1, ci, w, o2); g.line(x + cs - 1, y + 1, x + cs - 1, y + cs - 1, ci, w, o2);
      }
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      g.board(BX, BY, BS, BS);
      var ctx = g.ctx, clip = !!(ctx && ctx.save && ctx.clip);
      if (clip) { ctx.save(); ctx.beginPath(); ctx.rect(BX, BY, BS, BS); ctx.clip(); }
      if (cs >= 9 && !lcd) for (i = 0; i < n * n; i++) {
        if (b.mask && !b.mask[i]) continue;              // a picture board: dots only on the picture
        var d = centre(i);
        if (d[0] < BX - cs || d[1] < BY - cs || d[0] > BX + BS + cs || d[1] > BY + BS + cs) continue;
        g.circle(d[0], d[1], Math.max(0.6, cs * 0.05), 8, { solid: true, a: 0.7 });
      }
      for (var a = 0; a < b.arrows.length; a++) {
        var arr = b.arrows[a];
        if (arr.gone) continue;
        var off = [0, 0];
        if (s.bump && s.bump.arrow === a && !info.reduce) {
          var k = s.bump.t < 9 ? s.bump.t / 9 : (18 - s.bump.t) / 9;
          off = [Logic.DX[arr.dir] * cs * 0.3 * k, Logic.DY[arr.dir] * cs * 0.3 * k];
        }
        arrow(arr.cells, arr.dir, inkFor(g, a), off, 1);
      }
      if (!info.reduce) for (i = 0; i < s.flying.length; i++) {
        var f = s.flying[i], dd = f.t * f.t * cs * 0.06;
        arrow(f.cells, f.dir, lcd ? 0 : 8, [Logic.DX[f.dir] * dd, Logic.DY[f.dir] * dd], Math.max(0, 1 - f.t / Logic.FLY_T));
      }
      if (s.bump && s.bump.at >= 0) ring(s.bump.at, lcd ? 0 : 1, 2, false);
      if (s.hint >= 0 && !b.arrows[s.hint].gone) ring(b.arrows[s.hint].cells[0], lcd ? 0 : 3, 3, true);
      if (info.state === "running" || info.state === "paused") ring(s.cursor, lcd ? 0 : 5, 2, false);
      if (clip) ctx.restore();
      if (z > 1.01) {                                    // where the view is, on a little map of the board
        var mw = 30, mx = BX + BS - mw - 3, my = BY + 3;
        g.rect(mx, my, mw, mw, lcd ? 9 : 8, { solid: true, a: 0.75, r: 2 });
        var vx = mx + camX / BS * mw, vy = my + camY / BS * mw, vw = mw / z, vc = lcd ? 0 : 3;
        g.line(vx, vy, vx + vw, vy, vc, 1.2); g.line(vx, vy + vw, vx + vw, vy + vw, vc, 1.2);
        g.line(vx, vy, vx, vy + vw, vc, 1.2); g.line(vx + vw, vy, vx + vw, vy + vw, vc, 1.2);
      }
      var hearts = "";
      for (i = 0; i < s.maxHearts; i++) hearts += i < s.hearts ? "♥" : "·";
      g.text((s.maxHearts ? hearts + "   " : "") + "ARROWS " + Logic.left(s), 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.hint >= 0 ? "Tap the ringed arrow" : s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" :
        Logic.left(s) === b.arrows.length ? (s.book ? "“" + s.list[s.level - 1].name + "”" : n >= 12 && z <= 1 ? "Pinch or ＋ to look closer" : "Tap an arrow with a clear way out") :
        s.levels ? "Score so far " + s.runScore : "A bump costs " + (s.maxHearts ? "a heart" : "nothing");
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
    buttons: [{ action: "zoomout", label: "－", aria: "Zoom out" }, { action: "alt", label: "💡 Hint", aria: "Hint: show an arrow that can leave", wide: true },
      { action: "zoomin", label: "＋", aria: "Zoom in" }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Clear the board. Tap an arrow and it flies off the way it points — if nothing is in its way, tail and all. Every arrow's tail blocks the others too. If something is in the way, it bumps and you lose a heart (three hearts; Little has none). Releasing arrows only ever makes room, so look for the ones with a clear way out. The big boards hold lots of small arrows: pinch (or turn the mouse wheel, or press − / ＋) to look closer, and drag to move around. Keyboard: arrows move the ring, Space releases. With the Hints option on, Hint (or C) rings an arrow that can leave. Levels: numbered boards that get harder; Picture boards: arrows filling a picture (an AI model can add more). Both carry on from your next level.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
