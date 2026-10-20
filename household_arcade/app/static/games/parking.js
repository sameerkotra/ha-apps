/* Household Arcade — Car Park: input and drawing around the rules in parking-logic.js.
   Registers itself as "parking" with window.ArcadeGames.

   Drag a car or lorry along its length (or pick it with the cursor and Space, then the arrows). The red car has a
   white arrow on its roof, so it is known by shape as well as colour; the exit is a gap in the right wall.

   Classic and Hard boards take a moment to make (every position a car park can reach is worked out): a Web Worker
   (parking-worker.js) makes them, and the page shows "Making the car park…" meanwhile; without a worker the page
   makes it a moment later. The Levels boards are made ahead (parking-levels.js). */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.ParkingLogic;

  // ---------- making Classic and Hard boards off the main thread ----------
  var workerSrc = null;
  try {
    var cur = root.document && root.document.currentScript && root.document.currentScript.src;
    if (cur && /parking\.js(\?|$)/.test(cur)) workerSrc = cur.replace(/parking\.js(\?|$)/, "parking-worker.js$1");
  } catch (e) { workerSrc = null; }
  var worker = null, workerFailed = false, jobs = {}, nextJob = 1;
  function makeHere(job) { setTimeout(function () { var b = null; try { b = Logic.boardFor(job.mode, job.seed); } catch (e) { b = null; } job.done(b); }, 30); }
  function makeBoard(mode, seed, done) {
    var job = { mode: mode, seed: seed, done: done };
    if (!workerFailed && workerSrc && typeof root.Worker === "function") {
      try {
        if (!worker) {
          worker = new root.Worker(workerSrc);
          worker.onmessage = function (e) {
            var d = e.data || {}, j = jobs[d.id];
            delete jobs[d.id];
            if (j) { if (d.board) j.done(d.board); else makeHere(j); }
          };
          worker.onerror = function () {
            workerFailed = true; worker = null;
            var left = jobs; jobs = {};
            for (var id in left) makeHere(left[id]);
          };
        }
        var id = nextJob++;
        jobs[id] = job;
        worker.postMessage({ id: id, mode: mode, seed: seed });
        return;
      } catch (e) { workerFailed = true; worker = null; }
    }
    makeHere(job);
  }
  function slow(mode) { return mode === "classic" || mode === "hard"; }

  var MODES = [{ id: "little", label: "Little (gentle)" }, { id: "classic", label: "Classic" }, { id: "hard", label: "Hard" },
    { id: "levels", label: "Levels" }, { id: "book", label: "Puzzle book" }];
  var LABELS = { little: "LITTLE", classic: "CLASSIC", hard: "HARD" };
  var N = Logic.N, BX = 12, BY = 44, BS = 210, CS = BS / N, MSG_Y = 276, INKS = [5, 4, 6, 2, 7, 3];

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [], drag = null;
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function cellAt(x, y) {
      var c = Math.floor((x - BX) / CS), r = Math.floor((y - BY) / CS);
      return c < 0 || r < 0 || c >= N || r >= N ? -1 : r * N + c;
    }

    var impl = {
      init: function (seed) {
        var later = slow(opts.mode);
        s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints, startLevel: opts.startLevel, levels: opts.levels, deferBoard: later });
        pending = []; drag = null;
        if (later) {
          var mine = s;
          makeBoard(opts.mode, seed >>> 0, function (b) {
            if (s !== mine || destroyed) return;            // another game (or none) by now
            queue(b ? Logic.useBoard(s, b) : Logic.useBoard(s, Logic.boardFor(opts.mode, seed >>> 0)));
            if (inst) inst.invalidate();
          });
        }
      },
      save: function () { return s.phase === "wait" ? null : Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; drag = null; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { slide: "turn", nope: "wall", pick: "bounce", undo: "bounce", hint: "powerup", nohint: "wall", clear: "row", level: "level", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind === "down") {
          var cell = cellAt(lx, ly), v = cell >= 0 ? Logic.vehicleAt(s, cell) : -1;
          drag = v >= 0 ? { v: v, from: s.pos[v], x: lx, y: ly } : null;
          if (v >= 0) { s.sel = -1; s.cursor = cell; }
        } else if (kind === "move" && drag) {
          var veh = s.vs[drag.v], d = veh.h ? lx - drag.x : ly - drag.y;
          var want = drag.from + Math.round(d / CS), r = Logic.range(s.vs, s.pos, drag.v);
          want = Math.max(r[0], Math.min(r[1], want));
          if (want !== s.pos[drag.v]) { queue(Logic.slide(s, drag.v, want, drag.moved)); drag.moved = true; }   // one undo a drag
          if (s.phase !== "play") drag = null;
        } else if (kind === "up") drag = null;
      },
      draw: draw,
    };

    function vehicle(g, v, p, ci, red, lcd, picked) {
      var c = Logic.cellsOf(v, p), x = BX + (c[0] % N) * CS + 3, y = BY + Math.floor(c[0] / N) * CS + 3;
      var w = v.h ? v.len * CS - 6 : CS - 6, h = v.h ? CS - 6 : v.len * CS - 6;
      g.rect(x, y, w, h, ci, { r: 7, solid: true, stroke: !lcd });
      // windows (the front is towards the exit for the red car: a white arrow on its roof)
      var wi = lcd ? 9 : 9;
      if (v.h) { g.rect(x + 6, y + 5, 7, h - 10, wi, { r: 2, solid: true, a: 0.75 }); g.rect(x + w - 13, y + 5, 7, h - 10, wi, { r: 2, solid: true, a: 0.75 }); }
      else { g.rect(x + 5, y + 6, w - 10, 7, wi, { r: 2, solid: true, a: 0.75 }); g.rect(x + 5, y + h - 13, w - 10, 7, wi, { r: 2, solid: true, a: 0.75 }); }
      if (red) {
        var cx = x + w / 2, cy = y + h / 2;
        g.poly([[cx + 9, cy], [cx - 3, cy - 7], [cx - 3, cy + 7]], 9, { solid: true });
      }
      if (picked) g.rect(x - 2, y - 2, w + 4, h + 4, lcd ? 0 : 3, { r: 8, a: 0.9 });
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", i;
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      g.board(BX, BY, BS, BS);
      if (s.phase === "wait") { g.text("Making the car park…", 120, BY + BS / 2, { size: 12, align: "center", a: 0.85 }); return; }
      for (i = 1; i < N; i++) {
        g.line(BX + i * CS, BY + 2, BX + i * CS, BY + BS - 2, 8, 1, { a: 0.5 });
        g.line(BX + 2, BY + i * CS, BX + BS - 2, BY + i * CS, 8, 1, { a: 0.5 });
      }
      // the exit: a gap in the right wall with an arrow out
      var ey = BY + Logic.EXIT_ROW * CS;
      g.rect(BX + BS - 2, ey + 4, 8, CS - 8, lcd ? 9 : 4, { solid: true });
      g.poly([[BX + BS + 14, ey + CS / 2], [BX + BS + 7, ey + CS / 2 - 6], [BX + BS + 7, ey + CS / 2 + 6]], lcd ? 0 : 4, { solid: true });
      for (i = 0; i < s.vs.length; i++) {
        var ci = i === 0 ? 1 : lcd ? 8 : INKS[i % INKS.length];
        vehicle(g, s.vs[i], s.pos[i], lcd && i === 0 ? 0 : ci, i === 0, lcd, s.sel === i || (s.hint && s.hint.v === i));
      }
      if (s.hint) {
        var hv = s.vs[s.hint.v], hc = Logic.cellsOf(hv, s.hint.to);
        var hx = BX + (hc[0] % N) * CS + 3, hy = BY + Math.floor(hc[0] / N) * CS + 3;
        g.rect(hx, hy, hv.h ? hv.len * CS - 6 : CS - 6, hv.h ? CS - 6 : hv.len * CS - 6, lcd ? 0 : 3, { r: 7, dash: [3, 3], a: 0.9 });
      }
      if ((info.state === "running" || info.state === "paused") && s.sel < 0) {
        var cx = BX + (s.cursor % N) * CS, cy = BY + Math.floor(s.cursor / N) * CS;
        g.rect(cx + 1, cy + 1, CS - 2, CS - 2, lcd ? 0 : 5, { r: 4, a: 0.8 });
      }
      g.text("MOVES " + s.boardMoves + "   FEWEST " + s.fewest, 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.hint ? "Slide the ringed one to the dashed place" : s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" :
        s.sel >= 0 ? "Arrows slide it · Space lets go" : s.boardMoves === 0 ? "Get the red car out on the right" :
        s.levels ? "Score so far " + s.runScore : "Undo takes a move back";
      g.text(tip, 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      if (s.phase === "clear" || s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text(s.won ? "OUT!" : "OUT! NEXT CAR PARK", 120, 147, { size: 14, align: "center", ci: lcd ? 9 : 0 });
        g.text(s.won ? "SCORE " + s.runScore : "+" + s.lastBoardScore + "  LEVEL " + (s.level + 1), 120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    var destroyed = false, inst = Kit.createSession(canvas, opts, impl);
    var destroy = inst.destroy;
    inst.destroy = function () { destroyed = true; destroy(); };
    return inst;
  }

  root.ArcadeGames.register({
    id: "parking",
    name: "Car Park",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    slots: { hint: "hint", undo: "undo" },   // the shell's Hint / Undo / Restart buttons
    buttons: [{ action: "hint", label: "💡 Hint", aria: "Hint: show the next move" }, { action: "undo", label: "↶ Undo", aria: "Undo the last slide" }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Get the red car out through the gap on the right. Cars and lorries only move forwards and backwards along their length: drag one, or move the ring with the arrows, press Space to pick it and slide it with the arrows (Space again lets go). Sliding one vehicle as far as you like is one move; the fewest moves is shown, and extra moves cost points. Undo takes a slide back. With the Hints option on, Hint shows the next move. Little is the gentle one; Levels get harder and carry on from your next level. The Puzzle book is a list of named puzzles that also carries on from your next one (an AI model can add more).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
