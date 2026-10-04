/* Household Arcade — Solitaire (Klondike): input and drawing around the rules in solitaire-logic.js.
   Registers itself as "solitaire" with window.ArcadeGames.

   Played on the canvas: tap the stock to turn cards over; tap a card to send it where it can go (up to a
   foundation first, else onto a column); or drag a card or a run of cards to the column or foundation you
   choose. The shell's buttons are Undo, Hint and Auto. Suits are drawn as four different shapes (and the red
   ones in red), so they can be told apart without colour. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.SolitaireLogic;

  var MODES = [{ id: "draw1", label: "Draw one" }, { id: "draw3", label: "Draw three" }];
  var BUTTONS = [
    { action: "undo", label: "↶ Undo", aria: "Undo the last move" },
    { action: "hint", label: "💡 Hint", aria: "Hint: show a move" },
    { action: "auto", label: "Auto", aria: "Move every safe card up to the foundations" },
  ];

  var CW = 30, CH = 40, PITCH = 33.8, X0 = 3.5, TOP_Y = 38, TAB_Y = 86, DOWN_OFF = 4, UP_OFF = 11, BOTTOM = 280, DRAG_PX = 5;
  var FOUND_COL = 3;

  function colX(c) { return X0 + c * PITCH; }

  // An outline without a fill (a stroked rect is filled in on the coarse looks, which would hide the cards).
  function frame(g, x, y, w, h, ci, wd, a) {
    g.line(x, y, x + w, y, ci, wd, { a: a, cap: "butt" }); g.line(x, y + h, x + w, y + h, ci, wd, { a: a, cap: "butt" });
    g.line(x, y, x, y + h, ci, wd, { a: a, cap: "butt" }); g.line(x + w, y, x + w, y + h, ci, wd, { a: a, cap: "butt" });
  }

  function suitIcon(g, sd, x, y, r, ci) {
    var o = { solid: true };
    if (sd === 2) g.poly([[x, y - r], [x + r * 0.8, y], [x, y + r], [x - r * 0.8, y]], ci, o);
    else if (sd === 1) {
      g.circle(x - r * 0.5, y - r * 0.3, r * 0.58, ci, o); g.circle(x + r * 0.5, y - r * 0.3, r * 0.58, ci, o);
      g.poly([[x - r * 1.04, y - r * 0.05], [x + r * 1.04, y - r * 0.05], [x, y + r]], ci, o);
    } else if (sd === 0) {
      g.circle(x - r * 0.5, y + r * 0.1, r * 0.58, ci, o); g.circle(x + r * 0.5, y + r * 0.1, r * 0.58, ci, o);
      g.poly([[x - r * 1.04, y + r * 0.0], [x + r * 1.04, y + r * 0.0], [x, y - r]], ci, o);
      g.poly([[x - r * 0.3, y + r], [x + r * 0.3, y + r], [x, y + r * 0.2]], ci, o);
    } else {
      g.circle(x, y - r * 0.5, r * 0.55, ci, o); g.circle(x - r * 0.56, y + r * 0.2, r * 0.55, ci, o); g.circle(x + r * 0.56, y + r * 0.2, r * 0.55, ci, o);
      g.poly([[x - r * 0.3, y + r], [x + r * 0.3, y + r], [x, y]], ci, o);
    }
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], press = null, drag = null;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }

    // the y of card k in column c (the face-up spacing shrinks when a column gets long)
    function upOff(c) {
      var col = s.tab[c], ups = col.length - s.down[c];
      if (ups < 2) return UP_OFF;
      var room = BOTTOM - TAB_Y - CH - s.down[c] * DOWN_OFF;
      return Math.max(5, Math.min(UP_OFF, room / (ups - 1)));
    }
    function cardY(c, k) {
      var d = s.down[c];
      return k <= d ? TAB_Y + k * DOWN_OFF : TAB_Y + d * DOWN_OFF + (k - d) * upOff(c);
    }
    function wasteX(i, n) { return colX(1) + i * (s.draw === 3 ? 9 : 0); }

    function hit(x, y) {
      var c, k, f;
      if (y >= TOP_Y - 2 && y <= TOP_Y + CH + 2) {
        if (x >= colX(0) - 2 && x <= colX(0) + CW + 2) return { t: "stock" };
        var fan = Logic.wasteFan(s), wx = fan.length ? wasteX(fan.length - 1) : colX(1);
        if (fan.length && x >= wx - 1 && x <= wx + CW + 1) return { t: "waste" };
        for (f = 0; f < 4; f++) if (x >= colX(FOUND_COL + f) - 2 && x <= colX(FOUND_COL + f) + CW + 2) return { t: "found", f: f };
        return null;
      }
      if (y < TAB_Y - 4) return null;
      c = Math.floor((x - X0 + (PITCH - CW) / 2) / PITCH);
      if (c < 0 || c > 6 || x > colX(c) + CW + 2) return null;
      var col = s.tab[c];
      if (!col.length) return y <= TAB_Y + CH ? { t: "empty", c: c } : null;
      for (k = col.length - 1; k >= 0; k--) {
        var y0 = cardY(c, k), y1 = k === col.length - 1 ? y0 + CH : cardY(c, k + 1);
        if (y >= y0 && y <= y1) return k < s.down[c] ? { t: "down", c: c } : { t: "tab", c: c, k: k };
      }
      return null;
    }

    function dropTarget(x, y, from) {
      var cards = Logic.carried(s, from);
      if (!cards.length) return null;
      var c = Math.max(0, Math.min(6, Math.floor((x - X0 + (PITCH - CW) / 2) / PITCH)));
      if (y < TAB_Y - 6) {
        var slot = Logic.slotFor(s, cards[0]);
        return c >= FOUND_COL && slot >= 0 ? { t: "found", f: slot } : null;
      }
      return { t: "tab", c: c };
    }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, deck: opts.preview ? Logic.deck(seed) : undefined }); pending = []; press = null; drag = null; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); pending = []; press = null; drag = null; },
      step: function () {
        var evs = pending.concat(Logic.step(s));
        pending = [];
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      status: function () { return Logic.status(s); },
      sounds: { draw: "turn", recycle: "turn", move: "bounce", foundation: "powerup", flip: "turn", undo: "turn", hint: "turn", nomove: "wall", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (s.over) { press = null; drag = null; return; }
        if (kind === "down") {
          var h = hit(lx, ly);
          press = { hit: h, x: lx, y: ly, moved: false };
          drag = null;
          if (h && (h.t === "waste" || h.t === "tab" || h.t === "found") && Logic.carried(s, h).length) {
            var x0 = h.t === "tab" ? colX(h.c) : h.t === "waste" ? wasteX(Logic.wasteFan(s).length - 1) : colX(FOUND_COL + h.f);
            var y0 = h.t === "tab" ? cardY(h.c, h.k) : TOP_Y;
            press.ox = lx - x0; press.oy = ly - y0;
          }
        } else if (kind === "move" && press) {
          if (!press.moved && Math.abs(lx - press.x) + Math.abs(ly - press.y) > DRAG_PX && press.ox !== undefined) {
            press.moved = true; drag = { from: press.hit, x: lx, y: ly, ox: press.ox, oy: press.oy };
          } else if (drag) { drag.x = lx; drag.y = ly; }
        } else if (kind === "up" && press) {
          var p = press, d = drag; press = null; drag = null;
          if (d) {
            var to = dropTarget(lx, ly, d.from), evs = to ? Logic.move(s, d.from, to) : [];
            queue(evs.length ? evs : [{ type: "nomove" }]);
          } else if (!p.moved && p.hit) {
            if (p.hit.t === "stock") queue(Logic.draw(s));
            else if (p.hit.t === "waste" || p.hit.t === "tab") queue(Logic.tapCard(s, p.hit));
          }
        }
      },
      draw: draw,
    };

    // a rect that hides what is under it (Neon draws a solid rect see-through, so it is laid down three times)
    function opaque(g, x, y, w, h, ci, r) {
      for (var n = 0; n < (g.kind === "neon" ? 3 : 1); n++) g.rect(x, y, w, h, ci, { r: r, solid: true, a: 1 });
    }
    function surface(g) { return g.kind === "neon" || g.kind === "pixel" ? 8 : 9; }     // a card colour the ink reads on
    function card(g, x, y, id, o) {
      o = o || {};
      var lowres = g.lowres, lcd = g.kind === "lcd";
      if (id < 0) {         // face down
        opaque(g, x, y, CW, CH, lcd ? 0 : 5, 3);
        if (lcd) g.circle(x + CW / 2, y + CH / 2, 5, 9, { solid: true, white: true });
        else frame(g, x + 3, y + 3, CW - 6, CH - 6, 9, 1, 0.7);
        return;
      }
      if (lcd) frame(g, x, y, CW, CH, 0, 1.2, 1);          // the screen is already white
      else { opaque(g, x, y, CW, CH, surface(g), 3); frame(g, x, y, CW, CH, 0, 1, 0.7); }
      var sd = Logic.suit(id), ci = Logic.red(id) ? 1 : 0, lab = Logic.RANK_NAMES[Logic.rank(id)];
      g.text(lab, x + 3, y + 9.5, { size: 9, ci: ci });
      suitIcon(g, sd, x + CW - 7, y + 6.5, 3.6, ci);
      if (!o.strip) suitIcon(g, sd, x + CW / 2, y + CH * 0.66, 7, ci);
    }
    function slot(g, x, y, ci) { frame(g, x, y, CW, CH, ci == null ? 8 : ci, 1, 0.7); }
    function mark(g, x, y, w, h, ci) { frame(g, x - 1.5, y - 1.5, w + 3, h + 3, ci, 2.4, 1); }

    function draw(g, info) {
      var i, c, k, f, lcd = g.kind === "lcd";
      g.hud("SCORE " + s.score, Logic.clock(Logic.seconds(s)));
      var lifted = drag ? drag.from : null, liftedCards = lifted ? Logic.carried(s, lifted) : [];

      // stock
      slot(g, colX(0), TOP_Y);
      if (s.p < s.pile.length) card(g, colX(0), TOP_Y, -1);
      else if (s.pile.length) g.ring(colX(0) + CW / 2, TOP_Y + CH / 2, 8, 8, 2.2);
      // waste
      var fan = Logic.wasteFan(s);
      slot(g, colX(1), TOP_Y);
      for (i = 0; i < fan.length; i++) {
        if (i === fan.length - 1 && lifted && lifted.t === "waste") continue;
        card(g, wasteX(i), TOP_Y, fan[i]);
      }
      // foundations
      for (f = 0; f < 4; f++) {
        var fx = colX(FOUND_COL + f), pile = s.found[f];
        slot(g, fx, TOP_Y);
        if (!pile.length) { g.text("A", fx + CW / 2, TOP_Y + CH / 2 + 1, { size: 11, align: "center", base: "middle", a: lcd ? 0.3 : 0.35 }); continue; }
        var top = pile.length - (lifted && lifted.t === "found" && lifted.f === f ? 1 : 0);
        if (top > 0) card(g, fx, TOP_Y, pile[top - 1]);
        else slot(g, fx, TOP_Y);
      }
      // tableau
      for (c = 0; c < 7; c++) {
        var col = s.tab[c], x = colX(c);
        slot(g, x, TAB_Y);
        for (k = 0; k < col.length; k++) {
          if (lifted && lifted.t === "tab" && lifted.c === c && k >= lifted.k) break;
          var last = k === col.length - 1, up = k >= s.down[c];
          card(g, x, cardY(c, k), up ? col[k] : -1, { strip: !last && (up ? upOff(c) < CH - 6 : true) });
        }
      }
      // the hint
      if (s.hint) {
        var from = s.hint.from, to = s.hint.to, hc = 3;
        if (from.t === "stock") mark(g, colX(0), TOP_Y, CW, CH, hc);
        else if (from.t === "waste") mark(g, wasteX(fan.length - 1), TOP_Y, CW, CH, hc);
        else if (from.t === "tab") { var y0 = cardY(from.c, from.k); mark(g, colX(from.c), y0, CW, s.tab[from.c].length - 1 - from.k > 0 ? cardY(from.c, s.tab[from.c].length - 1) + CH - y0 : CH, hc); }
        if (to && to.t === "tab") { var tl = s.tab[to.c].length; mark(g, colX(to.c), tl ? cardY(to.c, tl - 1) : TAB_Y, CW, CH, 4); }
        else if (to && to.t === "found") mark(g, colX(FOUND_COL + to.f), TOP_Y, CW, CH, 4);
      }
      // what is being dragged
      if (drag && liftedCards.length) {
        var dx = drag.x - drag.ox, dy = drag.y - drag.oy;
        for (i = 0; i < liftedCards.length; i++) card(g, dx, dy + i * Math.min(UP_OFF, 12), liftedCards[i]);
      }
      // the message
      if (s.message && !s.won) {
        if (lcd) g.rect(8, 283, 224, 14, 0, { solid: true });
        else { opaque(g, 8, 283, 224, 14, surface(g), 4); frame(g, 8, 283, 224, 14, 8, 1, 0.8); }
        g.text(s.message, 120, 290.5, { size: g.lowres ? 5.5 : 8, align: "center", base: "middle", ci: lcd ? 9 : 0 });
      }
      if (s.won) {
        var tc = lcd ? 9 : 0;
        if (lcd) g.rect(35, 120, 170, 52, 0, { solid: true });
        else { opaque(g, 35, 120, 170, 52, surface(g), 6); frame(g, 35, 120, 170, 52, 0, 1.5, 1); }
        g.text("YOU WON!", 120, 142, { size: 16, align: "center", ci: tc });
        g.text(String(s.score), 120, 160, { size: 11, align: "center", ci: tc });
      }
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "solitaire",
    name: "Solitaire",
    modes: MODES,
    defaultMode: "draw1",
    controls: "touch",
    buttons: BUTTONS,
    help: "Klondike. Build the four foundations up from ace to king, one suit each. Build columns down in alternating colours; only a king can start an empty column. Tap the stock to turn cards over (tap it again when it's empty to turn the waste back over). Tap a card to send it where it can go, or drag cards where you want them. Hint shows a move; Auto sends safe cards up. Every deal can be won.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
