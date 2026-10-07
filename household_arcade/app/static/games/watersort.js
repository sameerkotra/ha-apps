/* Household Arcade — Colour Sort: input and drawing around the rules in watersort-logic.js.
   Registers itself as "watersort" with window.ArcadeGames.

   Tap a tube to pick it (it lifts), then another to pour. Every layer carries its colour's number as well as its
   colour (colours 8–12 are also striped), so the colours can be told apart without colour — Retro LCD draws only
   the numbers. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.WaterSortLogic;

  var MODES = [{ id: "little", label: "3 colours (gentle)" }, { id: "classic", label: "7 colours" }, { id: "big", label: "10 colours" },
    { id: "levels", label: "Levels" }];
  var LABELS = { little: "3 COLOURS", classic: "7 COLOURS", big: "10 COLOURS" };
  var HUES = [1, 5, 4, 3, 6, 7, 2], AX = 8, AY = 44, AW = 224, AH = 214, MSG_Y = 276;

  /** Where tube i is: { x, y, w, lh } (lh: one layer's height). */
  function layout(count) {
    var rows = count > 7 ? 2 : 1, per = Math.ceil(count / rows), slot = AW / per;
    var w = Math.min(26, slot - 8), lh = rows === 1 ? 34 : 21, h = lh * Logic.CAP + 8;
    var out = [];
    for (var i = 0; i < count; i++) {
      var r = Math.floor(i / per), c = i % per, inRow = r === rows - 1 ? count - per * (rows - 1) : per;
      var x0 = AX + (AW - inRow * slot) / 2;
      out.push({ x: x0 + c * slot + (slot - w) / 2, y: AY + 16 + r * (h + 22) + (rows === 1 ? 30 : 0), w: w, lh: lh, h: h });
    }
    return out;
  }

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [];
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function tubeAt(x, y) {
      var L = layout(s.tubes.length);
      for (var i = 0; i < L.length; i++) {
        var t = L[i];
        if (x >= t.x - 6 && x <= t.x + t.w + 6 && y >= t.y - 14 && y <= t.y + t.h + 6) return i;
      }
      return -1;
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, hints: o.hints, startLevel: opts.startLevel }); pending = []; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.score(s); },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { pick: "bounce", pour: "turn", full: "powerup", nope: "wall", undo: "bounce", restart: "bounce", hint: "powerup",
        nohint: "wall", stuck: "wall", clear: "row", level: "level", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = tubeAt(lx, ly);
        if (i >= 0) queue(Logic.tapTube(s, i));
      },
      draw: draw,
    };

    function layer(g, x, y, w, h, c, lcd) {
      var ci = lcd ? 0 : HUES[c % HUES.length];
      g.rect(x, y, w, h, ci, { r: 2, solid: true });
      if (c >= 7 && !lcd) g.line(x + 2, y + h - 2, x + w - 2, y + 2, 9, 2, { a: 0.6 });
      if (h >= 14) g.text(String(c + 1), x + w / 2, y + h / 2 + 4, { size: h >= 24 ? 11 : 9, align: "center", ci: 9, a: lcd ? 1 : 0.85 });
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", L = layout(s.tubes.length), i, j;
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      for (i = 0; i < s.tubes.length; i++) {
        var t = L[i], lift = s.sel === i ? 10 : 0, tube = s.tubes[i], y0 = t.y - lift;
        // the glass
        g.rect(t.x - 3, y0 - 4, t.w + 6, t.h + 4, 8, { r: 8, a: lcd ? 1 : 0.45, solid: !lcd });
        for (j = 0; j < tube.length; j++) layer(g, t.x, y0 + t.h - 4 - (j + 1) * t.lh, t.w, t.lh - 1, tube[j], lcd);
        var full = tube.length === Logic.CAP && Logic.topRun(tube) === Logic.CAP;
        if (full) {                                           // a tick over a finished tube
          var cx = t.x + t.w / 2, cy = y0 - 10;
          g.line(cx - 5, cy, cx - 1, cy + 4, lcd ? 0 : 4, 2); g.line(cx - 1, cy + 4, cx + 6, cy - 4, lcd ? 0 : 4, 2);
        }
        if (s.hint && (s.hint.from === i || s.hint.to === i)) {
          g.rect(t.x - 6, y0 - 7, t.w + 12, t.h + 10, lcd ? 0 : 3, { r: 9, dash: [3, 3] });
          g.text(s.hint.from === i ? "FROM" : "TO", t.x + t.w / 2, y0 + t.h + 16, { size: 8, align: "center", ci: lcd ? 0 : 3 });
        }
        if ((info.state === "running" || info.state === "paused") && s.cursor === i)
          g.rect(t.x - 5, t.y + t.h + 4, t.w + 10, 3, lcd ? 0 : 5, { solid: true });
      }
      g.text("POURS " + s.boardMoves + "   COLOURS " + s.k, 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.stuck ? "Stuck — Undo or Restart" : s.hint ? "Pour from FROM into TO" :
        s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" : s.sel >= 0 ? "Now tap where to pour" :
        s.boardMoves === 0 ? "One colour to each tube" : s.levels ? "Score so far " + s.runScore : "Undo · Restart start over";
      g.text(tip, 120, MSG_Y + 16, { size: 9, align: "center", a: 0.8 });
      if (s.phase === "clear" || s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("SORTED!", 120, 147, { size: 15, align: "center", ci: lcd ? 9 : 0 });
        g.text(s.won ? "SCORE " + s.runScore : "+" + s.lastBoardScore + "  NEXT: LEVEL " + (s.level + 1), 120, 164, { size: 9, align: "center", ci: lcd ? 9 : 0 });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "watersort",
    name: "Colour Sort",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "hint", label: "💡 Hint", aria: "Hint: show a pour" }, { action: "undo", label: "↶ Undo", aria: "Undo the last pour" },
      { action: "erase", label: "⟲ Restart", aria: "Start this board again" }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Sort the colours: each tube one colour, full. Tap a tube, then another to pour: the top colour pours across (every layer of it that fits) if the other tube is empty or has the same colour on top. Every layer shows its number too. Fewer pours score more. Undo takes a pour back, Restart starts the board again. Keyboard: arrows move between tubes, Space picks and pours. With the Hints option on, Hint shows a good pour. 3 colours is the gentle one; Levels carry on from your next level.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
