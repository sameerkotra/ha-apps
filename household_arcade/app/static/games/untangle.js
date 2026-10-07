/* Household Arcade — Untangle: input and drawing around the rules in untangle-logic.js.
   Registers itself as "untangle" with window.ArcadeGames.

   Drag the points. Lines that cross another are drawn dashed (and red, where the look has colour), so crossings
   show without colour; the count is under the board. With the keyboard, Space picks the next point (ringed) and
   the arrows move it. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.UntangleLogic;

  var MODES = [{ id: "little", label: "6 points (gentle)" }, { id: "classic", label: "10 points" }, { id: "big", label: "16 points" },
    { id: "levels", label: "Levels" }];
  var LABELS = { little: "6 POINTS", classic: "10 POINTS", big: "16 POINTS" }, MSG_Y = 276;

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [], drag = -1;
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints, startLevel: opts.startLevel }); pending = []; drag = -1; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; drag = -1; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { pick: "bounce", drop: "turn", hint: "powerup", nohint: "wall", clear: "row", level: "level", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind === "down") { drag = Logic.pointAt(s, lx, ly, 16); if (drag >= 0) s.sel = drag; }
        else if (kind === "move" && drag >= 0) Logic.movePoint(s, drag, lx, ly);
        else if (kind === "up" && drag >= 0) { drag = -1; queue(Logic.release(s)); }
      },
      draw: draw,
    };

    function draw(g, info) {
      var lcd = g.kind === "lcd", i, cr = Logic.crossings(s.pts, s.edges);
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      g.board(Logic.X0 - 6, Logic.Y0 - 6, Logic.SIZE + 12, Logic.SIZE + 12);
      for (i = 0; i < s.edges.length; i++) {
        var a = s.pts[s.edges[i][0]], b = s.pts[s.edges[i][1]], bad = cr.bad[i];
        g.line(a[0], a[1], b[0], b[1], lcd ? 0 : bad ? 1 : 4, bad ? 2 : 2.5, bad ? { dash: [4, 3] } : {});
      }
      for (i = 0; i < s.pts.length; i++) {
        var p = s.pts[i], picked = s.sel === i, hinted = s.hinted === i;
        g.circle(p[0], p[1], picked ? 8 : 6.5, lcd ? 0 : picked ? 3 : 5, { solid: true, stroke: !lcd });
        if (picked || hinted) g.ring(p[0], p[1], 11, lcd ? 0 : hinted ? 3 : 0, 1.5);
      }
      g.text("CROSSINGS " + s.crossCount + "   MOVES " + s.boardMoves, 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" : s.hinted >= 0 ? "Hint moved the ringed point" :
        s.boardMoves === 0 ? "Drag the points until no lines cross" : s.levels ? "Score so far " + s.runScore : "Dashed lines cross another";
      g.text(tip, 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      if (s.phase === "clear" || s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("UNTANGLED!", 120, 147, { size: 15, align: "center", ci: lcd ? 9 : 0 });
        g.text(s.won ? "SCORE " + s.runScore : "+" + s.lastBoardScore + "  NEXT: LEVEL " + (s.level + 1), 120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "untangle",
    name: "Untangle",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "alt", label: "💡 Hint", aria: "Hint: move one point to a good place", wide: true }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Drag the points until no two lines cross. Lines that cross another are dashed; the number of crossings is under the board. Moving a point counts as a move (moving it again straight after doesn't); fewer moves and less time score more. Keyboard: Space picks the next point, the arrows move it. With the Hints option on, Hint moves one point to a good place. 6 points is the gentle one; Levels carry on from your next level.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
