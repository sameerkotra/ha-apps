/* Household Arcade — Tile Match: input and drawing around the rules in tiles-logic.js.
   Registers itself as "tiles" with window.ArcadeGames.

   Tiles are drawn with a little depth (each layer up and to the left), lower layers first. Symbols are drawn here,
   no images: Little uses five plain shapes (circle, square, triangle, star — a plus on the coarse looks — and diamond,
   each its own colour as well); the others a number 1–9 with a suit shape under it (circle, diamond, triangle; on
   the coarse looks one, two or three square dots), so a pair can be told apart by shape and number, never by colour
   alone. Tiles that aren't free are a little dimmer. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.TilesLogic;

  var MODES = [{ id: "little", label: "Little (gentle)" }, { id: "classic", label: "Classic" }, { id: "big", label: "Big heap" }];
  var LABELS = { little: "LITTLE", classic: "CLASSIC", big: "BIG HEAP" };
  var AX = 6, AY = 40, AW = 228, AH = 220, MSG_Y = 272;
  var SUIT_CI = [1, 5, 4], SHAPE_CI = [1, 5, 4, 2, 6];

  /** Tile size and where the heap goes: { hu, hv, shift, ox, oy, tw, th } */
  function geom(mode) {
    var sz = Logic.size(mode), shift = mode === "little" ? 4 : 3;
    var hu = Math.min(20, (AW - sz.layers * shift) / sz.w), hv = hu * 1.3;
    if (sz.h * hv + sz.layers * shift > AH) { hv = (AH - sz.layers * shift) / sz.h; hu = hv / 1.3; }
    var wPx = sz.w * hu, hPx = sz.h * hv;
    return { hu: hu, hv: hv, shift: shift, tw: 2 * hu, th: 2 * hv,
      ox: AX + (AW - wPx) / 2 + (sz.layers - 1) * shift / 2, oy: AY + (AH - hPx) / 2 + (sz.layers - 1) * shift / 2 };
  }

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], G = null, P = null;

    function queue(evs) { for (var i = 0; i < evs.length; i++) pending.push(evs[i]); }
    function box(i) { var q = P[i]; return { x: G.ox + q.x * G.hu - q.z * G.shift, y: G.oy + q.y * G.hv - q.z * G.shift }; }
    function tileAt(x, y) {
      for (var i = P.length - 1; i >= 0; i--) {
        if (!s.alive[i]) continue;
        var b = box(i);
        if (x >= b.x && x < b.x + G.tw && y >= b.y && y < b.y + G.th) return i;
      }
      return -1;
    }
    function setup() { G = geom(s.mode); P = Logic.places(s.mode); pending = []; }

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed }); setup(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data); setup(); },
      step: function () { var evs = pending.concat(Logic.step(s)); pending = []; return evs; },
      logic: function () { return s; },
      score: function () { return Logic.scoreOf(s); },
      level: function () { return 1; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      status: function () { return Logic.status(s); },
      sounds: { select: "turn", match: "eat", nomatch: "turn", blocked: "wall", hint: "powerup", shuffle: "bonus", undo: "turn", stuck: "lose", win: "win" },
      input: function (action, down) { queue(Logic.press(s, action, down)); },
      pointer: function (kind, lx, ly) {
        if (kind !== "down") return;
        var i = tileAt(lx, ly);
        if (i >= 0) queue(Logic.pick(s, i));
      },
      draw: draw,
    };

    function symbol(g, k, cx, cy, th, ink) {
      var lcd = g.kind === "lcd", r = th * 0.2;
      if (s.mode === "little") {
        var ci = lcd ? ink : SHAPE_CI[k], o = { solid: true };
        r = th * 0.26;
        if (k === 0) g.circle(cx, cy, r, ci, o);
        else if (k === 1) g.rect(cx - r * 0.85, cy - r * 0.85, r * 1.7, r * 1.7, ci, { r: 1, solid: true });
        else if (k === 2) g.poly([[cx, cy - r], [cx + r, cy + r * 0.8], [cx - r, cy + r * 0.8]], ci, o);
        else if (k === 3 && g.lowres) {      // the coarse looks draw a star as a diamond: a plus instead, so it stays its own shape
          var q = r * 0.38;
          g.poly([[cx - q, cy - r], [cx + q, cy - r], [cx + q, cy - q], [cx + r, cy - q], [cx + r, cy + q], [cx + q, cy + q], [cx + q, cy + r],
            [cx - q, cy + r], [cx - q, cy + q], [cx - r, cy + q], [cx - r, cy - q], [cx - q, cy - q]], ci, o);
        }
        else if (k === 3) g.star(cx, cy, r * 1.1, ci, o);
        else g.poly([[cx, cy - r * 1.1], [cx + r * 0.85, cy], [cx, cy + r * 1.1], [cx - r * 0.85, cy]], ci, o);
        return;
      }
      var suit = Math.floor(k / 9), num = k % 9 + 1, sci = lcd ? ink : SUIT_CI[suit];
      g.text(String(num), cx, cy - th * 0.12, { size: Math.max(8, th * 0.42), align: "center", base: "middle", ci: sci });
      var sy = cy + th * 0.24, sr = Math.max(1.6, th * 0.1), so = { solid: true };
      if (g.lowres) {          // too few dots for small shapes: the suit is one, two or three square dots instead
        var d = g.kind === "pixel" ? 4 : 3, pitch = d * 2;
        for (var q = 0; q <= suit; q++) g.rect(g.snap(cx - (suit * pitch + d) / 2 + q * pitch), g.snap(sy - d / 2), d, d, sci, { solid: true });
        return;
      }
      if (suit === 0) g.circle(cx, sy, sr, sci, so);
      else if (suit === 1) g.poly([[cx, sy - sr * 1.2], [cx + sr, sy], [cx, sy + sr * 1.2], [cx - sr, sy]], sci, so);
      else g.poly([[cx, sy - sr * 1.1], [cx + sr * 1.15, sy + sr * 0.9], [cx - sr * 1.15, sy + sr * 0.9]], sci, so);
    }

    function draw(g, info) {
      var lcd = g.kind === "lcd", i, tw = G.tw, th = G.th, sh = G.shift;
      g.hud(LABELS[s.mode] || "", Logic.clock(Logic.effective(s)));
      g.text(Logic.left(s) + " LEFT", 120, 24, { size: 9, align: "center", a: 0.85 });
      var hintSet = s.hint ? [s.hint[0], s.hint[1]] : [];
      var playing = info.state === "running" || info.state === "paused";
      for (i = 0; i < P.length; i++) {
        if (!s.alive[i]) continue;
        var b = box(i), x = g.snap(b.x), y = g.snap(b.y), isFree = Logic.free(s, i), sel = s.sel === i;
        // the tile's side (depth), then its face
        if (lcd) g.rect(x + sh, y + sh, tw - 1, th - 1, 0, { solid: true });
        else g.rect(x + sh * 0.8, y + sh * 0.8, tw - 1, th - 1, 8, { r: 3, solid: true, edge: false, a: 1 });
        var face = sel ? 3 : 9, neon = g.kind === "neon";
        if (neon) {       // Neon: a dark face with a glowing edge (a light face would glow white over the symbol)
          g.rect(x, y, tw - 1, th - 1, 8, { r: 3, solid: true, a: 1 });
          g.rect(x, y, tw - 1, th - 1, sel ? 3 : 7, { r: 3, a: sel ? 1 : 0.7 });
        } else if (lcd) { if (sel) g.rect(x, y, tw - 1, th - 1, 0, { solid: true }); else { g.ctx.fillStyle = "#FFFFFF"; g.ctx.fillRect(x, y, tw - 1, th - 1); g.rect(x, y, tw - 1, th - 1, 9, {}); } }
        else g.rect(x, y, tw - 1, th - 1, face, { r: 3, solid: true, stroke: true, a: 1 });
        symbol(g, s.kinds[i], x + (tw - 1) / 2, y + (th - 1) / 2, th, lcd && sel ? 9 : 0);
        if (!isFree && !lcd) {            // not free: a little darker (High contrast: a little lighter on its black faces)
          if (g.kind === "contrast") g.rect(x, y, tw - 1, th - 1, 0, { r: 3, solid: true, a: 0.2, edge: false });
          else { g.ctx.fillStyle = neon || g.dark ? "rgba(0,0,0,0.42)" : "rgba(0,0,0,0.16)"; g.ctx.fillRect(x, y, tw - 1, th - 1); }
        }
        if (hintSet.indexOf(i) >= 0) frame(g, x - 1, y - 1, tw + 1, th + 1, lcd ? 0 : 1, 2.2);
        if (playing && s.cursor === i && !s.won) frame(g, x + 1.5, y + 1.5, tw - 4, th - 4, lcd ? (sel ? 9 : 0) : 7, 1.6);
      }
      var msg = s.message || (s.stuck ? "No pairs left: Shuffle or Undo" : s.pairs === 0 ? "Tap two free tiles that match" : "");
      if (msg) {
        var lines = wrap(msg, g.kind === "pixel" ? 28 : lcd ? 38 : 50);
        for (i = 0; i < Math.min(2, lines.length); i++) g.text(lines[i], 120, MSG_Y + i * 11, { size: 9, align: "center", a: 0.9 });
      }
      if (s.won) {
        var dim = g.kind === "pixel" || g.kind === "neon";
        g.rect(40, 128, 160, 44, lcd ? 0 : dim ? 8 : 9, { r: 6, solid: true, stroke: !lcd, a: 0.95 });
        g.text("ALL CLEAR!", 120, 147, { size: 15, align: "center", ci: lcd ? 9 : 0 });
        g.text(Logic.clock(Logic.effective(s)), 120, 164, { size: 10, align: "center", ci: lcd ? 9 : 0 });
      }
    }
    function frame(g, x, y, w, h, ci, wd) {
      g.line(x, y, x + w, y, ci, wd, { cap: "butt" }); g.line(x, y + h, x + w, y + h, ci, wd, { cap: "butt" });
      g.line(x, y, x, y + h, ci, wd, { cap: "butt" }); g.line(x + w, y, x + w, y + h, ci, wd, { cap: "butt" });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  function wrap(text, width) {
    var words = String(text).split(" "), out = [], cur = "";
    for (var i = 0; i < words.length; i++) {
      if (!words[i]) continue;
      if ((cur + " " + words[i]).trim().length > width && cur) { out.push(cur); cur = words[i]; } else cur = (cur + " " + words[i]).trim();
    }
    if (cur) out.push(cur);
    return out;
  }

  root.ArcadeGames.register({
    id: "tiles",
    name: "Tile Match",
    modes: MODES,
    defaultMode: "classic",
    controls: "touch",
    buttons: [{ action: "hint", label: "💡 Hint", aria: "Hint: show a pair" }, { action: "shuffle", label: "🔀 Shuffle", aria: "Shuffle the tiles left" },
      { action: "undo", label: "↶ Undo", aria: "Undo" }],
    typed: true,
    help: "Clear the heap two tiles at a time. Tap two free tiles with the same symbol (the same number and suit shape) to take them away. A tile is free when nothing lies on it and its left or right side is open; tiles that aren't free are a little dimmer. Hint shows a pair (+15 s), Shuffle deals the tiles left again (+30 s), Undo puts the last pair back. Every deal can be cleared. Keyboard: arrows and Space, H hint, S shuffle, U undo. Little is the gentle one.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
