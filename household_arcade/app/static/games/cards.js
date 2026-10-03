/* Household Arcade — Memory Cards: input and drawing around the rules in cards-logic.js.
   Registers itself as "cards" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.CardsLogic;

  var MODES = [{ id: "small", label: "4 × 4" }, { id: "medium", label: "4 × 5" }, { id: "large", label: "5 × 6" },
    { id: "challenge", label: "Challenges" }];

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], keys = false;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; keys = false; },
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
      sounds: { hide: "launch", turn: "turn", pair: "eat", miss: "wall", clear: "level", win: "win", gameover: "gameover" },
      input: function (action, down) { if (down) keys = true; queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        keys = false;
        var i = Logic.cardAt(s, lx, ly);
        if (i >= 0) queue(Logic.tap(s, i));
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, L = Logic.layout(s), i;
        var secs = Math.ceil(s.timeLeft / Logic.UPS), left = "SCORE " + fmt(s.score), right;
        if (s.boards) {
          right = s.level + "/" + s.boards.length + "  " + secs + "s";
          if (left.length + right.length <= 19) right = "BOARD " + right;    // when it fits beside the score
        } else right = "BOARD " + s.level + "  " + secs + "s";
        g.hud(left, right);

        // the time bar (it shrinks; the last quarter is red and striped)
        var frac = s.timeLimit > 0 ? s.timeLeft / s.timeLimit : 0, low = frac < 0.25;
        g.rect(12, 38, 216, 6, 8, { r: 2 });
        g.rect(12, 38, Math.max(0, 216 * frac), 6, low ? 1 : 4, { r: 2, solid: true });
        if (low && !g.lowres) for (var bx = 18; bx < 12 + 216 * frac; bx += 12) g.line(bx, 44, bx + 4, 38, 9, 1.5, { a: 0.7 });

        for (i = 0; i < s.cards.length; i++) {
          var c = i % L.cols, r = Math.floor(i / L.cols);
          var x = L.ox + c * L.px + L.gap / 2, y = L.oy + r * L.py + L.gap / 2;
          drawCard(g, info, s.cards[i], x, y, L.px - L.gap, L.py - L.gap, s.phase === "peek");
        }

        // the keyboard cursor (after an arrow key has been used)
        if (keys && !s.over && s.phase === "play") {
          var cc = s.cursor % L.cols, cr = Math.floor(s.cursor / L.cols);
          var x0 = L.ox + cc * L.px, y0 = L.oy + cr * L.py, x1 = x0 + L.px, y1 = y0 + L.py;
          g.line(x0, y0, x1, y0, 0, 2); g.line(x0, y1, x1, y1, 0, 2);
          g.line(x0, y0, x0, y1, 0, 2); g.line(x1, y0, x1, y1, 0, 2);
        }

        // a challenge board: its name (and the peek countdown) over the time bar at its start
        if (s.boards && !s.over && (s.phase === "peek" || (s.phase === "play" && s.timeLimit - s.timeLeft < 120))) {
          var look = s.phase === "peek" ? "  LOOK " + Math.ceil(s.peekT / Logic.UPS) : "";
          var label = short(s.boards[s.level - 1].name, 26 - look.length) + look;
          g.rect(12, 35, 216, 12, 9, { r: 3, stroke: true, a: 0.95 });
          g.text(label, 120, 41, { size: 9, align: "center", base: "middle" });
        }

        if (s.phase === "clear") {
          g.rect(36, 132, 168, 60, 9, { r: 6, stroke: true, a: 0.95 });
          g.text("BOARD CLEAR", 120, 156, { size: 14, align: "center" });
          g.text("BONUS +" + fmt(s.lastBonus), 120, 178, { size: 11, align: "center", a: 0.85 });
        }
        if (s.over && s.stats.cause === "time") {
          g.rect(48, 140, 144, 40, 9, { r: 6, stroke: true, a: 0.95 });
          g.text("TIME'S UP", 120, 166, { size: 14, align: "center" });
        }
      },
    };

    // A name cut to fit n characters (the low-resolution looks draw wide letters).
    function short(name, n) { name = name.toUpperCase(); return name.length > n ? name.slice(0, n - 1) + "." : name; }

    function drawCard(g, info, card, x, y, w, h, peek) {
      var up = card.up || card.done || peek, sx = 1;
      if (card.turnT > 0 && !info.reduce) {
        var p = 1 - card.turnT / Logic.TURN;      // 0 → 1 while turning: squash to nothing, then open out
        sx = Math.abs(1 - 2 * p);
        if (p < 0.5) up = !up;
      }
      var cw = Math.max(2, w * sx), cx = x + (w - cw) / 2;
      var mx = x + w / 2, my = y + h / 2, rad = Math.min(w, h) * 0.3;
      if (!up) {
        g.rect(g.snap(cx), g.snap(y), g.snap(cw), g.snap(h), 5, { r: 4, bevel: true });
        if (!g.lowres && sx > 0.5) g.ring(mx, my, Math.min(cw, h) * 0.16, 9, 1.5, { a: 0.6 });
        return;
      }
      if (card.done && card.turnT === 0 && !peek) {          // a pair found: no card left, just its symbol, smaller
        drawSymbol(g, card.sym, mx, my, rad * 0.8, 0.7);
        return;
      }
      g.rect(g.snap(cx), g.snap(y), g.snap(cw), g.snap(h), 9, { r: 4, stroke: true });
      if (sx > 0.6) drawSymbol(g, card.sym, mx, my, rad * sx, 1);
    }

    // Fifteen symbols, each a different shape (they're told apart by shape alone in Retro LCD).
    function drawSymbol(g, id, x, y, r, a) {
      var ci = Logic.SYMBOL_CI[id], o = { solid: true, a: a, r: 0 }, i, pts;
      switch (id) {
        case 0: g.circle(x, y, r, ci, o); break;                                              // disc
        case 1: g.ring(x, y, r * 0.75, ci, Math.max(2, r * 0.38), { a: a }); break;             // ring
        case 2: g.rect(x - r * 0.8, y - r * 0.8, r * 1.6, r * 1.6, ci, o); break;               // square
        case 3: g.poly([[x, y - r * 1.1], [x + r * 0.8, y], [x, y + r * 1.1], [x - r * 0.8, y]], ci, o); break; // diamond
        case 4: g.poly([[x, y - r], [x + r, y + r * 0.8], [x - r, y + r * 0.8]], ci, o); break;   // triangle
        case 5:                                                                                // five-point star
          pts = [];
          for (i = 0; i < 10; i++) {
            var an = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * 0.45 : r * 1.1;
            pts.push([x + Math.cos(an) * rr, y + Math.sin(an) * rr]);
          }
          g.poly(pts, ci, o); break;
        case 6:                                                                                // plus
          g.rect(x - r, y - r * 0.3, r * 2, r * 0.6, ci, o); g.rect(x - r * 0.3, y - r, r * 0.6, r * 2, ci, o); break;
        case 7:                                                                                // cross (×)
          g.line(x - r * 0.8, y - r * 0.8, x + r * 0.8, y + r * 0.8, ci, Math.max(2, r * 0.45), { a: a });
          g.line(x - r * 0.8, y + r * 0.8, x + r * 0.8, y - r * 0.8, ci, Math.max(2, r * 0.45), { a: a }); break;
        case 8:                                                                                // heart
          g.poly([[x, y + r], [x - r, y - r * 0.05], [x - r, y - r * 0.55], [x - r * 0.55, y - r * 0.9], [x, y - r * 0.5],
            [x + r * 0.55, y - r * 0.9], [x + r, y - r * 0.55], [x + r, y - r * 0.05]], ci, o); break;
        case 9:                                                                                // hexagon
          pts = [];
          for (i = 0; i < 6; i++) pts.push([x + Math.cos(i * Math.PI / 3) * r, y + Math.sin(i * Math.PI / 3) * r]);
          g.poly(pts, ci, o); break;
        case 10:                                                                               // house
          g.poly([[x, y - r], [x + r, y - r * 0.1], [x + r * 0.7, y - r * 0.1], [x + r * 0.7, y + r], [x - r * 0.7, y + r],
            [x - r * 0.7, y - r * 0.1], [x - r, y - r * 0.1]], ci, o); break;
        case 11:                                                                               // hourglass
          g.poly([[x - r * 0.8, y - r], [x + r * 0.8, y - r], [x, y]], ci, o);
          g.poly([[x, y], [x + r * 0.8, y + r], [x - r * 0.8, y + r]], ci, o); break;
        case 12:                                                                               // two dots
          g.circle(x - r * 0.5, y - r * 0.5, r * 0.42, ci, o); g.circle(x + r * 0.5, y + r * 0.5, r * 0.42, ci, o); break;
        case 13:                                                                               // three bars
          for (i = -1; i <= 1; i++) g.rect(x - r, y + i * r * 0.7 - r * 0.2, r * 2, r * 0.4, ci, o);
          break;
        default:                                                                               // arrow up
          g.poly([[x, y - r], [x + r, y], [x + r * 0.35, y], [x + r * 0.35, y + r], [x - r * 0.35, y + r],
            [x - r * 0.35, y], [x - r, y]], ci, o);
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "cards",
    name: "Memory Cards",
    modes: MODES,
    defaultMode: "small",
    controls: "touch",
    help: "Tap a card to turn it, then another: two the same stay face up, otherwise they turn back. Keyboard: arrows move the ring, Space turns the card. Clear the board before the time bar runs out; pairs in a row score more. Challenges: a list of boards, some with a look at every face first.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
