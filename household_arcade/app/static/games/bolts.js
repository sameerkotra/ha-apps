/* Household Arcade — Bolt Sort: input and drawing around the rules in bolts-logic.js.
   Registers itself as "bolts" with window.ArcadeGames.

   Each bolt is a threaded rod on a base; nuts are hexagons stacked on it, each with its colour's number (so the
   colours can be told apart without colour; Retro LCD shows only the numbers). A hidden nut is grey with "?". Tap a
   bolt to pick it (its top nut lifts), then another to move that nut. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.BoltsLogic;

  var MODES = [{ id: "little", label: "3 colours (gentle)" }, { id: "classic", label: "6 colours" }, { id: "hidden", label: "6 colours, hidden" },
    { id: "levels", label: "Levels" }, { id: "book", label: "Puzzle book" }];
  var LABELS = { little: "3 COLOURS", classic: "6 COLOURS", hidden: "HIDDEN" };
  var HUES = [1, 5, 4, 3, 6, 7, 2], AX = 8, AY = 44, AW = 224, MSG_Y = 276;

  function layout(count) {
    var rows = count > 6 ? 2 : 1, per = Math.ceil(count / rows), slot = AW / per;
    var w = Math.min(32, slot - 6), nh = rows === 1 ? 26 : 18, h = nh * Logic.CAP + 18;
    var out = [];
    for (var i = 0; i < count; i++) {
      var r = Math.floor(i / per), c = i % per, inRow = r === rows - 1 ? count - per * (rows - 1) : per;
      var x0 = AX + (AW - inRow * slot) / 2;
      out.push({ x: x0 + c * slot + (slot - w) / 2, y: AY + 20 + r * (h + 20) + (rows === 1 ? 36 : 0), w: w, nh: nh, h: h });
    }
    return out;
  }

  function create(canvas, opts) {
    opts = opts || {};
    var o = opts.options || {}, s = null, pending = [];
    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function boltAt(x, y) {
      var L = layout(s.bolts.length);
      for (var i = 0; i < L.length; i++) {
        var t = L[i];
        if (x >= t.x - 6 && x <= t.x + t.w + 6 && y >= t.y - 18 && y <= t.y + t.h + 6) return i;
      }
      return -1;
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
      sounds: { pick: "bounce", move: "turn", reveal: "bonus", full: "powerup", nope: "wall", undo: "bounce", restart: "bounce",
        hint: "powerup", nohint: "wall", stuck: "wall", clear: "row", level: "level", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = boltAt(lx, ly);
        if (i >= 0) queue(Logic.tapBolt(s, i));
      },
      draw: draw,
    };

    function nut(g, cx, y, w, h, c, hidden, lcd) {
      var ci = hidden ? 8 : lcd ? 0 : HUES[c % HUES.length], hw = w / 2, k = Math.min(6, h / 2);
      g.poly([[cx - hw + k, y], [cx + hw - k, y], [cx + hw, y + h / 2], [cx + hw - k, y + h], [cx - hw + k, y + h], [cx - hw, y + h / 2]], ci, { solid: true });
      if (!hidden && c >= 7 && !lcd) g.line(cx - hw + 4, y + h - 2, cx + hw - 4, y + 2, 9, 2, { a: 0.6 });
      g.text(hidden ? "?" : String(c + 1), cx, y + h / 2 + 4, { size: h >= 22 ? 11 : 9, align: "center", ci: hidden && !lcd ? 0 : 9 });
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", L = layout(s.bolts.length), i, j;
      g.hud(s.levels ? "LEVEL " + s.level : LABELS[s.mode], Logic.clock(Logic.seconds(s)));
      for (i = 0; i < s.bolts.length; i++) {
        var t = L[i], bolt = s.bolts[i], cx = t.x + t.w / 2;
        // the rod and its base
        g.rect(cx - 3, t.y - 8, 6, t.h, 8, { r: 2, solid: true, a: lcd ? 1 : 0.9 });
        g.rect(t.x - 4, t.y + t.h - 10, t.w + 8, 8, 8, { r: 3, solid: true });
        for (j = 0; j < bolt.length; j++) {
          var lift = s.sel === i && j === bolt.length - 1 ? 12 : 0;
          nut(g, cx, t.y + t.h - 12 - (j + 1) * t.nh - lift, t.w, t.nh - 2, bolt[j], s.hidden[i][j], lcd);
        }
        if (bolt.length === Logic.CAP && Logic.uniformRun(bolt) === Logic.CAP) {
          g.line(cx - 5, t.y - 14, cx - 1, t.y - 10, lcd ? 0 : 4, 2); g.line(cx - 1, t.y - 10, cx + 6, t.y - 18, lcd ? 0 : 4, 2);
        }
        if (s.hint && (s.hint.from === i || s.hint.to === i)) {
          g.rect(t.x - 7, t.y - 20, t.w + 14, t.h + 18, lcd ? 0 : 3, { r: 9, dash: [3, 3] });
          g.text(s.hint.from === i ? "FROM" : "TO", cx, t.y + t.h + 14, { size: 8, align: "center", ci: lcd ? 0 : 3 });
        }
        if ((info.state === "running" || info.state === "paused") && s.cursor === i)
          g.rect(t.x - 5, t.y + t.h + 1, t.w + 10, 3, lcd ? 0 : 5, { solid: true });
      }
      g.text("MOVES " + s.boardMoves + "   COLOURS " + s.k, 120, MSG_Y, { size: 12, align: "center" });
      var tip = s.stuck ? "Stuck — Undo or Restart" : s.hint ? "Move a nut from FROM to TO" :
        s.updates - s.noHintAt < 180 ? "Hints are off (start screen)" : s.sel >= 0 ? "Now tap where it goes" :
        s.boardMoves === 0 ? (s.hiddenMode ? "Hidden nuts show when they reach the top" : "One colour to each bolt") :
        s.levels ? "Score so far " + s.runScore : "Undo · Restart start over";
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
    id: "bolts",
    name: "Bolt Sort",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "hint", label: "💡 Hint", aria: "Hint: show a move" }, { action: "undo", label: "↶ Undo", aria: "Undo the last move" },
      { action: "erase", label: "⟲ Restart", aria: "Start this board again" }],
    options: [{ id: "hints", label: "Hints", default: "off", offInRaces: true,
      choices: [{ id: "off", label: "Off" }, { id: "on", label: "On (each costs 200 points)" }] }],
    help: "Fill each bolt with nuts of one colour. Tap a bolt, then another: its top nut moves across if that bolt is empty or has the same colour on top and room. One nut at a time. In Hidden (and later Levels) the nuts under the top show “?” until they reach the top. Every nut shows its number too. Fewer moves score more. Undo takes a move back, Restart starts the board again. Keyboard: arrows move between bolts, Space picks and moves. With the Hints option on, Hint shows a good move. 3 colours is the gentle one; Levels carry on from your next level. The Puzzle book is a list of named puzzles that also carries on from your next one (an AI model can add more).",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
