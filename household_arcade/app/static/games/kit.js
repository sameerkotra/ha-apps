/* Household Arcade — the shared game kit (window.ArcadeKit).

   - LOOKS: the six looks (colours, fonts, effects, sound set). A look changes only
     how things are drawn, never the rules.
   - createRenderer(canvas, lookId): draws one frame of a game at the 240 × 300
     logical size, scaled to the canvas's CSS size and the device pixel ratio.
     The game draws through `g`, a small drawing kit that renders the same scene
     in any look:
       * Modern, Paper, High contrast: straight onto the visible canvas.
       * Neon: onto a transparent layer, then a cheap glow made by halving that
         layer a few times and adding the blurred copies back (no shadowBlur).
       * Pixel: onto a 120 × 150 canvas, scaled up without smoothing.
       * Retro LCD: onto a 160 × 200 canvas, turned into dark "pixels" on a
         grey-green screen once per frame with ImageData (ink where it's dark,
         a faint ghost of unlit pixels everywhere else).
     Static parts (background, a level's bricks) are cached with g.layer().
   - createLoop(): the fixed-step loop, 60 updates a second whatever the screen's
     refresh rate; it never runs while the page is hidden.
   - createSession(canvas, opts, impl): the part every game shares — start, pause,
     resume, looks, sound, reduce motion, active seconds, scores and the end
     result — so a game file only has its rules, input and drawing. With
     opts.live (a live duel on two phones, SPEC §13.4) the session plays in
     lockstep: an update runs only when both players' inputs for it are known.

   No DOM access outside the canvas it is given (and the page's theme for the
   Modern look). Nothing is loaded from the network. */
(function (root) {
  "use strict";

  var W = 240, H = 300, STEP_MS = 1000 / 60;

  // Fonts: the looks name their preferred faces first, then fallbacks that are
  // installed on phones and computers. Nothing is downloaded (strict CSP, offline);
  // Retro LCD and Pixel draw their text with the built-in pixel font below.
  var SANS = "'Atkinson Hyperlegible', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
  var DISPLAY = "'Bungee', 'Arial Black', 'Segoe UI Black', system-ui, -apple-system, sans-serif";
  var HAND = "'Patrick Hand', 'Comic Neue', 'Comic Sans MS', 'Chalkboard SE', 'Segoe Print', 'Bradley Hand', cursive";
  var MONO = "ui-monospace, 'SFMono-Regular', Menlo, Consolas, 'Courier New', monospace";

  // Palette index: 0 ink, 1 red, 2 orange, 3 yellow, 4 green, 5 blue, 6 purple, 7 cyan, 8 muted, 9 light
  var LOOKS = {
    modern: { id: "modern", label: "Modern", bg: "#F6F7F3", font: SANS, sound: "modern",
      c: ["#22302A", "#E4572E", "#F29E4C", "#F1C453", "#4CAF7A", "#3D7DD8", "#8E6CCF", "#2BB3C0", "#D9DED6", "#FFFFFF"],
      // Modern follows the app's light/dark theme.
      dark: { bg: "#161B19",
        c: ["#E6ECE8", "#F0714D", "#F5AD62", "#F3CF6A", "#5CC28C", "#5B95E6", "#A28AE0", "#3FC3CF", "#2C3531", "#1D2421"] } },
    lcd: { id: "lcd", label: "Retro LCD", bg: "#FFFFFF", font: MONO, sound: "lcd", lowres: 2 / 3,
      screen: "#9EAD86", ink: "#1F2A1A", ghost: "#93A17C",
      c: ["#000", "#000", "#000", "#000", "#000", "#000", "#000", "#000", "#C8C8C8", "#FFFFFF"] },
    neon: { id: "neon", label: "Neon", bg: "#08070F", font: DISPLAY, sound: "neon", glow: true,
      c: ["#ECEBFF", "#FF3D7F", "#FF9F1C", "#FFE45E", "#3CFF8F", "#4D9DFF", "#B26BFF", "#2DE2E6", "#2C2944", "#FFFFFF"] },
    pixel: { id: "pixel", label: "Pixel", bg: "#1D1B33", font: MONO, sound: "pixel", lowres: 0.5,
      c: ["#F4F1DE", "#E5383B", "#F48C06", "#FFD60A", "#52B788", "#4895EF", "#9D4EDD", "#4CC9F0", "#3B3863", "#FFFFFF"] },
    paper: { id: "paper", label: "Paper", bg: "#F3EEE2", font: HAND, sound: "paper",
      c: ["#2E2A26", "#C8553D", "#E09F3E", "#E3C567", "#5B8E55", "#3F6EA8", "#7E5BA6", "#4A9BA8", "#D2C9B6", "#FFFDF7"] },
    contrast: { id: "contrast", label: "High contrast", bg: "#000000", font: SANS, sound: "contrast",
      c: ["#FFFFFF", "#FF453A", "#FF9F0A", "#FFD60A", "#32D74B", "#2F8CFF", "#C46BFF", "#64D2FF", "#48484A", "#000000"] },
  };
  var LOOK_IDS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

  // ---------- small helpers ----------
  var rgbaCache = {};
  function hexA(hex, a) {
    if (a >= 1) return hex;
    if (a < 0) a = 0;
    a = Math.round(a * 100) / 100;
    var key = hex + "|" + a, v = rgbaCache[key];
    if (v) return v;
    var h = hex.replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    v = "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
    rgbaCache[key] = v;
    return v;
  }
  function now() { return (root.performance && root.performance.now) ? root.performance.now() : Date.now(); }
  function fmt(n) { return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function prefersReducedMotion() {
    try { return !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches); } catch (e) { return false; }
  }
  function themeIsDark(doc) {
    try {
      var cs = root.getComputedStyle ? String(root.getComputedStyle(doc.documentElement).colorScheme || "") : "";
      var d = /dark/.test(cs), l = /light/.test(cs);
      if (d && !l) return true;
      if (l && !d) return false;
    } catch (e) { /* no computed style (tests) */ }
    try { return !!(root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches); } catch (e) { return false; }
  }

  // ---------- built-in pixel font (5 rows high, proportional) ----------
  // Used by Retro LCD and Pixel so their text is crisp on the coarse grid and
  // doesn't depend on any downloaded font. Original glyphs.
  var GLYPHS = {
    A: [".#.", "#.#", "###", "#.#", "#.#"], B: ["##.", "#.#", "##.", "#.#", "##."],
    C: [".##", "#..", "#..", "#..", ".##"], D: ["##.", "#.#", "#.#", "#.#", "##."],
    E: ["###", "#..", "##.", "#..", "###"], F: ["###", "#..", "##.", "#..", "#.."],
    G: [".##", "#..", "#.#", "#.#", ".##"], H: ["#.#", "#.#", "###", "#.#", "#.#"],
    I: ["###", ".#.", ".#.", ".#.", "###"], J: ["..#", "..#", "..#", "#.#", ".#."],
    K: ["#.#", "#.#", "##.", "#.#", "#.#"], L: ["#..", "#..", "#..", "#..", "###"],
    M: ["#...#", "##.##", "#.#.#", "#...#", "#...#"], N: ["#..#", "##.#", "#.##", "#..#", "#..#"],
    O: [".#.", "#.#", "#.#", "#.#", ".#."], P: ["##.", "#.#", "##.", "#..", "#.."],
    Q: [".##.", "#..#", "#..#", "#.#.", ".#.#"], R: ["##.", "#.#", "##.", "#.#", "#.#"],
    S: [".##", "#..", ".#.", "..#", "##."], T: ["###", ".#.", ".#.", ".#.", ".#."],
    U: ["#.#", "#.#", "#.#", "#.#", "###"], V: ["#.#", "#.#", "#.#", "#.#", ".#."],
    W: ["#...#", "#...#", "#.#.#", "##.##", "#...#"], X: ["#.#", "#.#", ".#.", "#.#", "#.#"],
    Y: ["#.#", "#.#", ".#.", ".#.", ".#."], Z: ["###", "..#", ".#.", "#..", "###"],
    0: ["###", "#.#", "#.#", "#.#", "###"], 1: [".#", "##", ".#", ".#", ".#"],
    2: ["##.", "..#", ".#.", "#..", "###"], 3: ["##.", "..#", ".#.", "..#", "##."],
    4: ["#.#", "#.#", "###", "..#", "..#"], 5: ["###", "#..", "##.", "..#", "##."],
    6: [".##", "#..", "###", "#.#", "###"], 7: ["###", "..#", ".#.", ".#.", ".#."],
    8: ["###", "#.#", "###", "#.#", "###"], 9: ["###", "#.#", "###", "..#", "##."],
    " ": ["..", "..", "..", "..", ".."], ".": [".", ".", ".", ".", "#"], ",": [".", ".", ".", "#", "#"],
    ":": [".", "#", ".", "#", "."], "·": [".", ".", "#", ".", "."], "-": ["...", "...", "###", "...", "..."],
    "+": ["...", ".#.", "###", ".#.", "..."], "!": ["#", "#", "#", ".", "#"], "?": ["##.", "..#", ".#.", "...", ".#."],
    "/": ["..#", "..#", ".#.", "#..", "#.."], "'": ["#", "#", ".", ".", "."], "%": ["#.#", "..#", ".#.", "#..", "#.#"],
    "×": ["...", "#.#", ".#.", "#.#", "..."], "(": [".#", "#.", "#.", "#.", ".#"], ")": ["#.", ".#", ".#", ".#", "#."],
    "=": ["...", "###", "...", "###", "..."], "÷": [".#.", "...", "###", "...", ".#."], "−": ["...", "...", "###", "...", "..."], "—": ["....", "....", "####", "....", "...."], "–": ["...", "...", "###", "...", "..."],
    "♥": [".#.#.", "#####", "#####", ".###.", "..#.."], "→": ["....", "..#.", "####", "..#.", "...."],
    "…": [".....", ".....", ".....", ".....", "#.#.#"], "&": [".#..", "#.#.", ".#..", "#.#.", ".#.#"],
  };
  function glyph(ch) { return GLYPHS[ch] || GLYPHS[String(ch).toUpperCase()] || GLYPHS["?"]; }
  function bitmapWidth(s, scale) {
    var w = 0;
    for (var i = 0; i < s.length; i++) w += (glyph(s[i])[0].length + 1) * scale;
    return Math.max(0, w - scale);
  }

  // ---------- the drawing kit ----------
  // env: { look, kind, pal (colour array), bg, k (low-resolution factor, 1 if none), reduce, layer }
  function makeG(ctx, env) {
    var kind = env.kind, pal = env.pal, L = env.look, k = env.k, ik = 1 / k;
    var lowres = kind === "lcd" || kind === "pixel";
    var col = function (ci, a) { return a == null || a >= 1 ? pal[ci] : hexA(pal[ci], a); };

    // Low-resolution helpers (Retro LCD, Pixel): integer pixels of the small canvas.
    function px(x0, y0, x1, y1, color) {
      if (x1 <= x0 || y1 <= y0) return;
      ctx.fillStyle = color;
      ctx.fillRect(x0 * ik, y0 * ik, (x1 - x0) * ik, (y1 - y0) * ik);
    }
    function snapBox(x, y, w, h) {
      return [Math.round(x * k), Math.round(y * k), Math.round((x + w) * k), Math.round((y + h) * k)];
    }
    function ringPx(b, color) {
      px(b[0], b[1], b[2], b[1] + 1, color); px(b[0], b[3] - 1, b[2], b[3], color);
      px(b[0], b[1] + 1, b[0] + 1, b[3] - 1, color); px(b[2] - 1, b[1] + 1, b[2], b[3] - 1, color);
    }
    function lcdInk(a) { return (a == null ? 1 : a) >= 0.5; }

    function paint(build, ci, o) {
      var a = o.a == null ? 1 : o.a;
      ctx.save();
      build();
      if (kind === "lcd") {
        if ((ci === 8 || ci === 9) && !o.solid) {
          ctx.fillStyle = "#FFFFFF"; ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = ik; ctx.stroke();
        } else { ctx.fillStyle = a < 0.5 ? "#C8C8C8" : "#000"; ctx.fill(); }
      } else if (kind === "neon") {
        ctx.fillStyle = col(ci, Math.min(1, 0.16 * a + (o.solid ? 0.5 : 0))); ctx.fill();
        ctx.strokeStyle = col(ci, Math.min(1, a + 0.2)); ctx.lineWidth = 1.6; ctx.stroke();
      } else if (kind === "paper") {
        ctx.fillStyle = col(ci, 0.55 * a); ctx.fill();
        ctx.strokeStyle = col(0, 0.85 * Math.min(1, a + 0.3)); ctx.lineWidth = 1; ctx.stroke();
        ctx.translate(0.8, -0.6); build(); ctx.strokeStyle = col(0, 0.35 * a); ctx.stroke();
      } else if (kind === "contrast") {
        ctx.fillStyle = col(ci, a); ctx.fill();
        if (o.edge !== false || ci === 9) {
          ctx.strokeStyle = ci === 9 || ci === 8 ? "#FFFFFF" : "#000000"; ctx.lineWidth = 2; ctx.stroke();
        }
      } else {
        ctx.fillStyle = col(ci, a); ctx.fill();
        if (o.stroke) { ctx.strokeStyle = col(0, 0.7); ctx.lineWidth = 1.2; ctx.stroke(); }
      }
      ctx.restore();
    }
    function rr(x, y, w, h, r) {
      return function () {
        ctx.beginPath();
        if (r > 0 && ctx.roundRect) ctx.roundRect(x, y, w, h, Math.min(r, w / 2, h / 2)); else ctx.rect(x, y, w, h);
      };
    }
    function bitmapText(s, x, y, o, color) {
      var scale = Math.max(1, Math.floor((o.size || 12) * k / 6 + 0.25));
      var tw = bitmapWidth(s, scale), th = 5 * scale;
      var px0 = Math.round(x * k), py0 = Math.round(y * k);
      var align = o.align || "left", base = o.base || "alphabetic";
      if (align === "center") px0 -= Math.round(tw / 2); else if (align === "right") px0 -= tw;
      if (base === "middle") py0 -= Math.round(th / 2); else if (base === "top") py0 += 0; else py0 -= th;
      ctx.fillStyle = color;
      var cx = px0;
      for (var i = 0; i < s.length; i++) {
        var gl = glyph(s[i]);
        for (var r = 0; r < 5; r++) {
          var row = gl[r], run = -1;
          for (var c = 0; c <= row.length; c++) {
            var on = c < row.length && row[c] === "#";
            if (on && run < 0) run = c;
            if (!on && run >= 0) {
              ctx.fillRect((cx + run * scale) * ik, (py0 + r * scale) * ik, (c - run) * scale * ik, scale * ik);
              run = -1;
            }
          }
        }
        cx += (gl[0].length + 1) * scale;
      }
    }

    var lastFont = "";
    var g = {
      W: W, H: H, kind: kind, look: L, lowres: lowres, k: k, ctx: ctx, col: col, dark: !!env.dark,
      get reduce() { return !!env.reduce; },
      /** Round a logical coordinate to the low-resolution grid (identity in smooth looks). */
      snap: function (v) { return lowres ? Math.round(v * k) / k : v; },
      layer: function (key, version, fn) { env.layer(key, version, fn); },
      rect: function (x, y, w, h, ci, o) {
        o = o || {};
        if (lowres) {
          var b = snapBox(x, y, w, h), a = o.a == null ? 1 : o.a;
          if (kind === "lcd") {
            if (!lcdInk(a)) return;
            if ((ci === 8 || ci === 9) && !o.solid) { ringPx(b, "#000"); return; }
            if (o.solid || b[2] - b[0] < 5 || b[3] - b[1] < 5) { px(b[0], b[1], b[2], b[3], "#000"); return; }
            ringPx(b, "#000"); px(b[0] + 2, b[1] + 2, b[2] - 2, b[3] - 2, "#000");
          } else {
            px(b[0], b[1], b[2], b[3], col(ci, a));
            if (o.bevel && b[3] - b[1] >= 3) {
              px(b[0], b[1], b[2], b[1] + 1, "rgba(255,255,255,.35)");
              px(b[0], b[3] - 1, b[2], b[3], "rgba(0,0,0,.3)");
            }
          }
          return;
        }
        var r = o.r != null ? o.r : (kind === "modern" ? 3 : kind === "paper" ? 2 : 0);
        if (!o.stroke && !(kind === "neon" || kind === "paper" || kind === "contrast") && !(r > 0)) {
          ctx.fillStyle = col(ci, o.a == null ? 1 : o.a); ctx.fillRect(x, y, w, h); return;
        }
        paint(rr(x, y, w, h, r), ci, o);
      },
      circle: function (x, y, r, ci, o) {
        o = o || {};
        if (lowres) {
          var a = o.a == null ? 1 : o.a, color;
          if (kind === "lcd") { if (!lcdInk(a)) return; color = o.white ? "#FFFFFF" : "#000"; } else color = col(ci, a);
          var cx = Math.round(x * k), cy = Math.round(y * k), R = r * k;
          if (R < 1) { px(cx, cy, cx + 1, cy + 1, color); return; }
          var span = Math.ceil(R);
          for (var dy = -span; dy < span; dy++) {
            var yc = dy + 0.5, h2 = R * R - yc * yc;
            if (h2 <= 0) continue;
            var hw = Math.round(Math.sqrt(h2));
            if (hw > 0) px(cx - hw, cy + dy, cx + hw, cy + dy + 1, color);
          }
          if (kind === "lcd" && (ci === 8 || ci === 9) && !o.solid && R >= 2) g.circle(x, y, r - ik, 9, { solid: true, white: true });
          return;
        }
        if (kind === "modern" && !o.stroke) {
          ctx.fillStyle = col(ci, o.a == null ? 1 : o.a); ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); return;
        }
        paint(function () { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); }, ci, o);
      },
      ellipse: function (x, y, rx, ry, ci, o) { paint(function () { ctx.beginPath(); ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2); }, ci, o || {}); },
      poly: function (pts, ci, o) {
        paint(function () {
          ctx.beginPath();
          for (var i = 0; i < pts.length; i++) { if (i) ctx.lineTo(pts[i][0], pts[i][1]); else ctx.moveTo(pts[i][0], pts[i][1]); }
          ctx.closePath();
        }, ci, o || {});
      },
      path: function (fn, ci, o) { paint(function () { ctx.beginPath(); fn(ctx); }, ci, o || {}); },
      line: function (x1, y1, x2, y2, ci, w, o) {
        o = o || {}; w = w == null ? 2 : w;
        var a = o.a == null ? 1 : o.a;
        if (lowres) {
          var color;
          if (kind === "lcd") { if (!lcdInk(a)) return; color = "#000"; } else color = col(ci, a);
          var t = Math.max(1, Math.round(w * k));
          var p0x = Math.round(x1 * k), p0y = Math.round(y1 * k), p1x = Math.round(x2 * k), p1y = Math.round(y2 * k);
          var half = Math.floor(t / 2);
          if (p0y === p1y) { px(Math.min(p0x, p1x), p0y - half, Math.max(p0x, p1x) + 1, p0y - half + t, color); return; }
          if (p0x === p1x) { px(p0x - half, Math.min(p0y, p1y), p0x - half + t, Math.max(p0y, p1y) + 1, color); return; }
          var dash = o.dash ? [Math.max(1, Math.round(o.dash[0] * k)), Math.max(1, Math.round(o.dash[1] * k))] : null;
          var dx = Math.abs(p1x - p0x), sx = p0x < p1x ? 1 : -1, dy = -Math.abs(p1y - p0y), sy = p0y < p1y ? 1 : -1;
          var err = dx + dy, n = 0, xx = p0x, yy = p0y;
          for (;;) {
            if (!dash || (n % (dash[0] + dash[1])) < dash[0]) px(xx - half, yy - half, xx - half + t, yy - half + t, color);
            n++;
            if (xx === p1x && yy === p1y) break;
            var e2 = 2 * err;
            if (e2 >= dy) { err += dy; xx += sx; }
            if (e2 <= dx) { err += dx; yy += sy; }
          }
          return;
        }
        ctx.save();
        ctx.lineCap = o.cap || "round"; ctx.lineWidth = kind === "contrast" ? Math.max(w, 2) : w;
        ctx.strokeStyle = col(ci, a);
        if (o.dash) ctx.setLineDash(o.dash);
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        if (kind === "paper") {
          ctx.translate(0.7, 0.6); ctx.globalAlpha = 0.4; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
        }
        ctx.restore();
      },
      curve: function (fn, ci, w, o) {
        o = o || {};
        ctx.save(); ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = w == null ? 2 : w;
        ctx.strokeStyle = kind === "lcd" ? "#000" : col(ci, o.a == null ? 1 : o.a);
        ctx.beginPath(); fn(ctx); ctx.stroke(); ctx.restore();
      },
      ring: function (x, y, r, ci, w, o) { g.curve(function (c) { c.arc(x, y, r, 0, Math.PI * 2); }, ci, w, o); },
      text: function (s, x, y, o) {
        o = o || {}; s = String(s);
        var ci = o.ci == null ? 0 : o.ci, a = o.a == null ? 1 : o.a;
        if (lowres) {
          var color;
          if (kind === "lcd") color = ci === 9 ? "#FFFFFF" : a < 0.5 ? "#C8C8C8" : "#000";
          else color = col(ci, a);
          bitmapText(s.toUpperCase(), x, y, o, color);
          return;
        }
        var size = o.size || 12, wt = o.bold === false ? "400" : "700";
        var font = (kind === "neon" ? "400" : wt) + " " + size + "px " + (o.font || L.font);
        if (font !== lastFont || ctx.font !== font) { ctx.font = font; lastFont = font; }
        ctx.textAlign = o.align || "left"; ctx.textBaseline = o.base || "alphabetic";
        ctx.fillStyle = o.abs && kind === "contrast" ? o.abs : col(ci, a);
        ctx.fillText(s, x, y);
        if (kind === "paper" && size >= 18) { ctx.fillStyle = col(ci, 0.25 * a); ctx.fillText(s, x + 0.7, y - 0.5); }
      },
      cell: function (x, y, s, ci, o) {
        o = o || {};
        if (lowres) {
          var b = snapBox(x, y, s, s), a = o.a == null ? 1 : o.a;
          b[2] -= 1; b[3] -= 1; // one pixel gap between cells
          if (kind === "lcd") {
            if (!lcdInk(a)) return;
            if (b[2] - b[0] >= 5) { ringPx(b, "#000"); px(b[0] + 2, b[1] + 2, b[2] - 2, b[3] - 2, "#000"); }
            else px(b[0], b[1], b[2], b[3], "#000");
          } else {
            px(b[0], b[1], b[2], b[3], col(ci, a));
            if (b[3] - b[1] >= 3) {
              px(b[0], b[1], b[2], b[1] + 1, "rgba(255,255,255,.35)");
              px(b[0], b[3] - 1, b[2], b[3], "rgba(0,0,0,.3)");
            }
          }
          return;
        }
        var oo = { r: kind === "modern" ? 2.5 : 0, a: o.a, solid: o.solid, edge: o.edge, stroke: o.stroke };
        g.rect(x + 0.6, y + 0.6, s - 1.2, s - 1.2, ci, oo);
      },
      board: function (x, y, w, h) {
        if (kind === "lcd") { ringPx(snapBox(x - 3, y - 3, w + 6, h + 6), "#000"); return; }
        if (kind === "pixel") {
          var b = snapBox(x - 2, y - 2, w + 4, h + 4);
          px(b[0], b[1], b[2], b[3], "rgba(255,255,255,.05)"); ringPx(b, col(8)); return;
        }
        if (kind === "modern") { g.rect(x - 3, y - 3, w + 6, h + 6, 8, { a: env.dark ? 0.7 : 0.55, r: 6 }); return; }
        ctx.save();
        ctx.strokeStyle = kind === "contrast" ? "#FFFFFF" : kind === "paper" ? col(0, 0.7) : col(6, 0.7);
        ctx.lineWidth = kind === "contrast" ? 2 : 1.4;
        ctx.strokeRect(x - 3, y - 3, w + 6, h + 6);
        ctx.restore();
      },
      hud: function (left, right) {
        g.text(left, 10, 24, { size: 12 });
        if (right) g.text(right, 230, 24, { size: 12, align: "right" });
        g.line(10, 33, 230, 33, 8, 1, { a: kind === "lcd" ? 1 : 0.9 });
      },
      star: function (x, y, r, ci, o) {
        var p = [];
        for (var i = 0; i < 10; i++) {
          var an = -Math.PI / 2 + i * Math.PI / 5, rad = i % 2 ? r * 0.45 : r;
          p.push([x + Math.cos(an) * rad, y + Math.sin(an) * rad]);
        }
        if (lowres) { // a chunky diamond on the coarse grid
          o = o || {};
          var a = o.a == null ? 1 : o.a, color;
          if (kind === "lcd") { if (!lcdInk(a)) return; color = "#000"; } else color = col(ci, a);
          var cx = Math.round(x * k), cy = Math.round(y * k), R = Math.max(2, Math.round(r * k));
          for (var dy = -R; dy <= R; dy++) { var hw = R - Math.abs(dy); px(cx - hw, cy + dy, cx + hw + 1, cy + dy + 1, color); }
          if (kind === "lcd" && R >= 3) px(cx, cy, cx + 1, cy + 1, "#FFFFFF");
          return;
        }
        g.poly(p, ci, o);
      },
      fmt: fmt,
    };
    return g;
  }

  // ---------- the renderer ----------
  function createRenderer(canvas, lookId, hooks) {
    hooks = hooks || {};
    var doc = canvas.ownerDocument || root.document;
    var mctx = canvas.getContext("2d", { alpha: false }) || canvas.getContext("2d");
    var look = null, kind = "", dark = false, reduce = false, destroyed = false;
    var cssW = W, cssH = H, cw = W, ch = H, dpr = 1;
    var fit = { x: 0, y: 0, w: W, h: H };
    var tctx = null, g = null, k = 1, bw = W, bh = H;
    var buf = null, inkCanvas = null, inkCtx = null, inkImg = null, ink32 = null, grid = null, P = 2;
    var chain = [], bgLayer = null;
    var layers = {}, generation = 0;
    var INK32 = 0, GHOST32 = 0;
    var api;
    var env = { layer: layer };

    function mk(w, h) {
      var c = doc.createElement("canvas");
      c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h));
      return c;
    }
    function free(c) { if (c) { c.width = 1; c.height = 1; } }
    function measure() {
      var w = canvas.clientWidth, h = canvas.clientHeight;
      if (w > 0 && h > 0) { cssW = w; cssH = h; }
      // Neon is soft by nature: a slightly smaller backing store keeps its glow cheap.
      dpr = Math.min(kind === "neon" ? 1.5 : 2, Math.max(1, root.devicePixelRatio || 1));
      // A game filling a big screen would otherwise draw millions of pixels every frame:
      // keep the backing store under about 2.4 million pixels (still sharp at that size).
      var MAX_PIXELS = 2400000, area = Math.max(1, cssW * cssH);
      if (area * dpr * dpr > MAX_PIXELS) dpr = Math.max(1, Math.sqrt(MAX_PIXELS / area));
      if (kind === "lcd") {
        // Size the backing store so every LCD dot is exactly P × P device pixels;
        // the browser scales the canvas to its CSS size (free, on the compositor).
        var lbw = Math.round(W * look.lowres), lbh = Math.round(H * look.lowres);
        var fitCss = Math.max(1, Math.min(cssW, cssH * W / H));
        P = Math.max(2, Math.round(fitCss * dpr / lbw));
        var f = P * lbw / fitCss;
        cw = Math.max(P * lbw, Math.round(cssW * f)); ch = Math.max(P * lbh, Math.round(cssH * f));
        if (canvas.width !== cw) canvas.width = cw;
        if (canvas.height !== ch) canvas.height = ch;
        fit.w = P * lbw; fit.h = P * lbh;
        fit.x = Math.floor((cw - fit.w) / 2); fit.y = Math.floor((ch - fit.h) / 2);
        return;
      }
      cw = Math.max(1, Math.round(cssW * dpr)); ch = Math.max(1, Math.round(cssH * dpr));
      if (canvas.width !== cw) canvas.width = cw;
      if (canvas.height !== ch) canvas.height = ch;
      var s = Math.min(cw / W, ch / H);
      fit.w = Math.max(1, Math.round(W * s)); fit.h = Math.max(1, Math.round(H * s));
      fit.x = Math.floor((cw - fit.w) / 2); fit.y = Math.floor((ch - fit.h) / 2);
    }
    function palette() { return kind === "modern" && dark ? look.dark.c : look.c; }
    function bgColor() { return kind === "modern" && dark ? look.dark.bg : kind === "lcd" ? look.screen : look.bg; }
    function packRGB(hex) {
      var n = parseInt(hex.replace("#", ""), 16);
      return ((255 << 24) | ((n & 255) << 16) | (((n >> 8) & 255) << 8) | ((n >> 16) & 255)) >>> 0;
    }

    function build() {
      generation++;
      for (var key in layers) free(layers[key].canvas);
      layers = {};
      [buf, inkCanvas, grid, bgLayer].forEach(free);
      chain.forEach(function (c) { free(c.c); });
      buf = inkCanvas = grid = bgLayer = null; chain = []; inkImg = ink32 = null;
      k = look.lowres || 1;
      dark = kind === "modern" ? themeIsDark(doc) : false;
      env.look = look; env.kind = kind; env.pal = palette(); env.bg = bgColor(); env.k = k; env.dark = dark; env.reduce = reduce;
      if (look.lowres) {
        bw = Math.round(W * k); bh = Math.round(H * k);
        buf = mk(bw, bh);
        tctx = buf.getContext("2d", kind === "lcd" ? { willReadFrequently: true } : undefined) || buf.getContext("2d");
        if (kind === "lcd") {
          inkCanvas = mk(bw, bh); inkCtx = inkCanvas.getContext("2d");
          inkImg = inkCtx.createImageData(bw, bh);
          ink32 = new Uint32Array(inkImg.data.buffer);
          INK32 = packRGB(look.ink); GHOST32 = packRGB(look.ghost);
          // The gaps between dots, drawn over the scaled-up ink (P was set by measure()).
          grid = mk(bw * P, bh * P);
          var gc = grid.getContext("2d");
          var dot = P <= 2 ? 1 : Math.max(1, Math.min(P - 1, Math.round(P * 0.8)));
          gc.fillStyle = look.screen;
          for (var x = 0; x < bw; x++) gc.fillRect(x * P + dot, 0, P - dot, bh * P);
          for (var y = 0; y < bh; y++) gc.fillRect(0, y * P + dot, bw * P, P - dot);
        }
      } else if (kind === "neon") {
        tctx = mctx;
        // Halve until a quarter of the logical size; the last two copies make the glow.
        // (Neon draws straight onto the visible canvas; the glow is added afterwards.)
        var w = fit.w, h = fit.h;
        do {
          w = Math.max(1, Math.ceil(w / 2)); h = Math.max(1, Math.ceil(h / 2));
          var c = mk(w, h); chain.push({ c: c, ctx: c.getContext("2d") });
        } while (w > W / 4 && chain.length < 8);
        bgLayer = mk(fit.w, fit.h);
        var bc = bgLayer.getContext("2d");
        bc.fillStyle = look.bg; bc.fillRect(0, 0, fit.w, fit.h);
        bc.setTransform(fit.w / W, 0, 0, fit.h / H, 0, 0);
        bc.strokeStyle = hexA("#B26BFF", 0.08); bc.lineWidth = 1;
        for (var gx = 0; gx < W; gx += 20) { bc.beginPath(); bc.moveTo(gx, 0); bc.lineTo(gx, H); bc.stroke(); }
      } else {
        tctx = mctx;
        if (kind === "paper") {
          bgLayer = mk(fit.w, fit.h);
          var pc = bgLayer.getContext("2d");
          pc.fillStyle = look.bg; pc.fillRect(0, 0, fit.w, fit.h);
          pc.setTransform(fit.w / W, 0, 0, fit.h / H, 0, 0);
          pc.strokeStyle = hexA("#7FA6C9", 0.25); pc.lineWidth = 1;
          for (var py = 20; py < H; py += 16) { pc.beginPath(); pc.moveTo(0, py); pc.lineTo(W, py); pc.stroke(); }
          pc.strokeStyle = hexA("#C8553D", 0.18);
          pc.beginPath(); pc.moveTo(4, 0); pc.lineTo(4, H); pc.stroke();
        }
      }
      g = makeG(tctx, env);
    }

    function targetSize() {
      if (look.lowres) return [bw, bh];
      return [fit.w, fit.h];
    }
    function setTargetTransform(c) {
      if (look.lowres) c.setTransform(k, 0, 0, k, 0, 0);
      else c.setTransform(fit.w / W, 0, 0, fit.h / H, 0, 0);
    }
    // A cached sub-layer (a level's bricks, a board): redrawn only when `version` changes.
    function layer(key, version, fn) {
      var sz = targetSize(), Ly = layers[key];
      if (!Ly) { Ly = layers[key] = { canvas: mk(sz[0], sz[1]), version: null, gen: -1, g: null }; }
      if (Ly.gen !== generation || Ly.version !== version) {
        if (Ly.canvas.width !== sz[0] || Ly.canvas.height !== sz[1]) { Ly.canvas.width = sz[0]; Ly.canvas.height = sz[1]; }
        var lc = Ly.canvas.getContext("2d");
        lc.setTransform(1, 0, 0, 1, 0, 0); lc.clearRect(0, 0, sz[0], sz[1]);
        setTargetTransform(lc);
        if (!Ly.g || Ly.gen !== generation) Ly.g = makeG(lc, env);
        lc.save(); fn(Ly.g); lc.restore();
        Ly.version = version; Ly.gen = generation;
      }
      tctx.drawImage(Ly.canvas, 0, 0, W, H);
    }

    // Fill only the strips around the game area (the area itself is always fully drawn).
    function letterbox(color) {
      if (fit.x === 0 && fit.y === 0 && fit.w === cw && fit.h === ch) return;
      mctx.fillStyle = color;
      if (fit.x > 0) { mctx.fillRect(0, 0, fit.x, ch); mctx.fillRect(fit.x + fit.w, 0, cw - fit.x - fit.w, ch); }
      if (fit.y > 0) { mctx.fillRect(0, 0, cw, fit.y); mctx.fillRect(0, fit.y + fit.h, cw, ch - fit.y - fit.h); }
    }
    function drawDirect(draw) {
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.globalAlpha = 1; mctx.globalCompositeOperation = "source-over";
      letterbox(bgColor());
      if (bgLayer) mctx.drawImage(bgLayer, fit.x, fit.y);
      else { mctx.fillStyle = bgColor(); mctx.fillRect(fit.x, fit.y, fit.w, fit.h); }
      mctx.save();
      mctx.beginPath(); mctx.rect(fit.x, fit.y, fit.w, fit.h); mctx.clip();
      mctx.setTransform(fit.w / W, 0, 0, fit.h / H, fit.x, fit.y);
      draw(g);
      mctx.restore();
    }

    function frame(draw) {
      if (destroyed || !look) return 0;
      var t0 = now();
      env.reduce = reduce;
      if (look.lowres) {
        tctx.setTransform(1, 0, 0, 1, 0, 0);
        tctx.globalAlpha = 1; tctx.globalCompositeOperation = "source-over";
        tctx.fillStyle = kind === "lcd" ? "#FFFFFF" : look.bg; tctx.fillRect(0, 0, bw, bh);
        setTargetTransform(tctx);
        tctx.save(); draw(g); tctx.restore();
        mctx.setTransform(1, 0, 0, 1, 0, 0);
        mctx.globalAlpha = 1; mctx.globalCompositeOperation = "source-over";
        if (kind === "lcd") {
          // dark → ink, everything else → a faint ghost of an unlit pixel (opaque, so no extra fill)
          var d = tctx.getImageData(0, 0, bw, bh).data, n = bw * bh;
          for (var i = 0, j = 0; j < n; j++, i += 4) {
            ink32[j] = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) < 140250 ? INK32 : GHOST32;
          }
          inkCtx.putImageData(inkImg, 0, 0);
          letterbox(look.screen);
          mctx.imageSmoothingEnabled = false;
          mctx.drawImage(inkCanvas, fit.x, fit.y, fit.w, fit.h);
          mctx.imageSmoothingEnabled = true;
          mctx.drawImage(grid, fit.x, fit.y);
        } else {
          letterbox(look.bg);
          mctx.imageSmoothingEnabled = false;
          mctx.drawImage(buf, fit.x, fit.y, fit.w, fit.h);
          mctx.imageSmoothingEnabled = true;
        }
      } else if (kind === "neon") {
        drawDirect(draw);
        // Glow without shadowBlur: shrink the finished picture a few times by half,
        // remove the background colour, and add the blurred copy back once.
        var first = chain[0];
        first.ctx.globalCompositeOperation = "copy";
        first.ctx.drawImage(canvas, fit.x, fit.y, fit.w, fit.h, 0, 0, first.c.width, first.c.height);
        first.ctx.globalCompositeOperation = "difference";
        first.ctx.fillStyle = look.bg; first.ctx.fillRect(0, 0, first.c.width, first.c.height);
        for (var c = 1; c < chain.length; c++) {
          var lv = chain[c];
          lv.ctx.globalCompositeOperation = "copy";
          lv.ctx.drawImage(chain[c - 1].c, 0, 0, lv.c.width, lv.c.height);
        }
        var a1 = chain[Math.max(0, chain.length - 2)], a2 = chain[chain.length - 1];
        if (a2 !== a1) {
          a1.ctx.globalCompositeOperation = "lighter"; a1.ctx.globalAlpha = 0.9;
          a1.ctx.drawImage(a2.c, 0, 0, a1.c.width, a1.c.height);
          a1.ctx.globalAlpha = 1;
        }
        var pulse = reduce ? 1 : 1 + 0.12 * Math.sin(now() / 1000 * 2.4);
        mctx.setTransform(1, 0, 0, 1, 0, 0);
        mctx.globalCompositeOperation = "lighter"; mctx.globalAlpha = Math.min(1, 0.85 * pulse);
        mctx.drawImage(a1.c, fit.x, fit.y, fit.w, fit.h);
        mctx.globalAlpha = 1; mctx.globalCompositeOperation = "source-over";
      } else {
        drawDirect(draw);
      }
      api.lastFrameMs = now() - t0;
      return api.lastFrameMs;
    }

    function setLook(id) {
      look = LOOKS[id] || LOOKS.modern; kind = look.id;
      measure(); build();
    }

    // Keep up with the canvas's size and (for Modern) the app's light/dark theme.
    var ro = null, mo = null, mq = null;
    function invalidate() { if (!destroyed && hooks.onInvalidate) hooks.onInvalidate(); }
    function onResized() {
      if (destroyed) return;
      var w = canvas.clientWidth, h = canvas.clientHeight;
      if (w === cw && h === ch && (Math.round(cssW) !== cw || Math.round(cssH) !== ch)) {
        // The canvas has no CSS size of its own, so its CSS size follows the backing store
        // we just set: pin it, or every resize would grow it again.
        if (canvas.style) { canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px"; }
        return;
      }
      var ow = cw, oh = ch; measure();
      if (cw !== ow || ch !== oh) { build(); invalidate(); }
    }
    function onTheme() {
      if (destroyed || kind !== "modern") return;
      if (themeIsDark(doc) !== dark) { build(); invalidate(); }
    }
    try { if (root.ResizeObserver) { ro = new root.ResizeObserver(onResized); ro.observe(canvas); } } catch (e) { ro = null; }
    try {
      if (root.MutationObserver && doc.documentElement) {
        mo = new root.MutationObserver(onTheme);
        mo.observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
      }
    } catch (e) { mo = null; }
    try {
      if (root.matchMedia) {
        mq = root.matchMedia("(prefers-color-scheme: dark)");
        if (mq && mq.addEventListener) mq.addEventListener("change", onTheme); else mq = null;
      }
    } catch (e) { mq = null; }

    api = {
      get lookId() { return kind; },
      get look() { return look; },
      get dark() { return dark; },
      get g() { return g; },
      lastFrameMs: 0,
      setLook: setLook,
      setReduceMotion: function (on) { reduce = !!on; env.reduce = reduce; },
      resize: function () { measure(); build(); },
      frame: frame,
      /** CSS pixels relative to the canvas → logical 240 × 300 coordinates. */
      toLogical: function (x, y) {
        var dx = x * (cw / cssW), dy = y * (ch / cssH);
        return { x: (dx - fit.x) * W / fit.w, y: (dy - fit.y) * H / fit.h };
      },
      destroy: function () {
        if (destroyed) return;
        destroyed = true;
        if (ro) ro.disconnect(); if (mo) mo.disconnect(); if (mq) mq.removeEventListener("change", onTheme);
        for (var key in layers) free(layers[key].canvas);
        [buf, inkCanvas, grid, bgLayer].forEach(free); chain.forEach(function (c) { free(c.c); });
        layers = {}; chain = [];
      },
    };
    // A canvas without a CSS size of its own takes its size from the backing store, which
    // this renderer changes; give such a canvas a fixed CSS size once (the shell normally
    // sizes the canvas itself, and then this does nothing).
    (function pinIntrinsicSize() {
      if (!canvas.style || canvas.style.width || canvas.style.height) return;
      var w0 = canvas.clientWidth;
      if (!w0) return;
      var old = canvas.width;
      canvas.width = old + 8;
      var w1 = canvas.clientWidth;
      canvas.width = old;
      if (w1 !== w0) { canvas.style.width = w0 + "px"; canvas.style.height = Math.round(w0 * H / W) + "px"; }
    })();
    setLook(lookId);
    return api;
  }

  // ---------- the fixed-step loop ----------
  // 60 updates a second whatever the refresh rate. A small tolerance keeps a 60 Hz
  // screen at exactly one update per frame (no 0/2 judder from timestamp jitter);
  // after a stall the game doesn't fast-forward. Stops itself when the page is hidden.
  // Lockstep (o.step() returns false: the other phone's inputs aren't here yet): the
  // update waits and at most 4 updates of time are kept for when they arrive; o.extra()
  // (> 0 when this phone is behind the other) adds one update a frame to catch up.
  function createLoop(o) {
    var doc = o.doc || root.document;
    var raf = root.requestAnimationFrame ? root.requestAnimationFrame.bind(root) : function (cb) { return setTimeout(function () { cb(now()); }, 16); };
    var caf = root.cancelAnimationFrame ? root.cancelAnimationFrame.bind(root) : clearTimeout;
    var running = false, id = 0, last = -1, acc = 0;
    function frame(ts) {
      id = 0;
      if (!running) return;
      if (doc && doc.hidden) { if (o.onHidden) o.onHidden(); if (!running) return; }
      if (last < 0) last = ts;
      var dt = ts - last; last = ts;
      if (dt > 250) dt = STEP_MS;
      if (dt < 0) dt = 0;
      acc += dt;
      if (o.extra && o.extra() > 0) acc += STEP_MS;
      var n = 0;
      while (acc >= STEP_MS * 0.9 && running) {
        if (o.step() === false) { if (acc > STEP_MS * 4) acc = STEP_MS * 4; break; }
        acc -= STEP_MS;
        if (++n >= 8) { acc = 0; break; }
      }
      if (!running) return;
      o.draw(clamp(acc / STEP_MS, 0, 1));
      id = raf(frame);
    }
    function onVis() { if (running && doc && doc.hidden && o.onHidden) o.onHidden(); }
    function onPageHide() { if (running && o.onHidden) o.onHidden(); }
    if (doc && doc.addEventListener) doc.addEventListener("visibilitychange", onVis);
    if (root.addEventListener) root.addEventListener("pagehide", onPageHide);
    return {
      get running() { return running; },
      start: function () { if (running) return; running = true; last = -1; acc = 0; id = raf(frame); },
      stop: function () { running = false; if (id) caf(id); id = 0; },
      destroy: function () {
        this.stop();
        if (doc && doc.removeEventListener) doc.removeEventListener("visibilitychange", onVis);
        if (root.removeEventListener) root.removeEventListener("pagehide", onPageHide);
      },
    };
  }

  // ---------- what every game shares ----------
  // impl: { init(seed), step() → events, input(action, isDown), pointer(kind, lx, ly, cssX, cssY),
  //         draw(g, info), score(), level(), isOver(), result() → { score, level, stats },
  //         sounds: { eventType: soundName } }
  // Taking turns live (opts.live.lockstep from ArcadeLockstep.createTurns, Carrom): impl also has turn() (whose move,
  // −1 while one is moving), shots(), settledShots() (shots that have stopped), nextSeat() (1 / 2, 0 at the end),
  // apply(), checksum() and report(); each shot from the server is applied when it is a player's turn, and each one
  // that has stopped is reported with the checksum.
  // A live duel (opts.live = { seat, lockstep } — lockstep from ArcadeLockstep.create): impl also has
  //   apply(player, action, value)  an input of player 0 or 1, played on both phones on the same update,
  //   checksum()                    the rules' checksum (compared between the phones),
  //   report()                      { scores: [p1, p2], levels, winner } at the end,
  // Turn by turn (opts.turns = { seat, picture, send(move) }, SPEC §13.5): impl.turnSync(picture) takes the match as
  // the server has it after every move (inst.turnSync); the game sends its own moves with opts.turns.send.
  // and its input()/pointer() get a third/sixth argument act(action, value) that sends this phone's input
  // (instead of touching the rules: an input only reaches the rules through apply(), on both phones).
  function createSession(canvas, opts, impl) {
    opts = opts || {};
    function cb(name) { return typeof opts[name] === "function" ? opts[name] : null; }
    var onScore = cb("onScore"), onEnd = cb("onEnd"), onEvent = cb("onEvent");
    var lookId = LOOKS[opts.look] ? opts.look : "modern";
    var userReduce = !!opts.reduceMotion, soundOn = !!opts.sound;
    var seed = (opts.seed == null || !isFinite(Number(opts.seed)))
      ? ((Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) >>> 0)
      : (Number(opts.seed) >>> 0);
    var Sound = root.ArcadeSound;
    var state = "ready", steps = 0, alpha = 0, lastScore = -1, lastLevel = -1;
    var live = opts.live && opts.live.lockstep ? opts.live : null, ls = live ? live.lockstep : null;
    function act(action, value) { if (ls && state === "running") ls.local(action, value); }

    function safe(fn) {
      if (!fn) return;
      try { fn.apply(null, Array.prototype.slice.call(arguments, 1)); }
      catch (e) { if (root.console) root.console.error(e); }
    }
    function reduce() { return userReduce || prefersReducedMotion(); }
    function draw() {
      renderer.frame(function (g) {
        impl.draw(g, { alpha: state === "running" ? alpha : 0, t: steps / 60, now: now() / 1000,
          reduce: reduce(), state: state, look: lookId });
      });
    }
    function play(name) { if (soundOn && Sound && name) Sound.play(name, lookId); }
    function emit(type, data) { safe(onEvent, type, data || { type: type }); }
    function reportScore() {
      var sc = impl.score(), lv = impl.level();
      if (sc !== lastScore || lv !== lastLevel) { lastScore = sc; lastLevel = lv; safe(onScore, sc, lv); }
    }
    var reported = 0;
    function tick() {
      var evs;
      if (ls && ls.turns) {                  // taking turns live: a shot moving runs freely; a turn waits for its shot
        steps++;
        evs = [];
        if (impl.turn() >= 0) {
          var sh = ls.nextShot();
          if (sh) evs = impl.apply(sh[0] - 1, sh[1], sh[2]) || [];
        }
        evs = evs.concat(impl.step() || []);
        var done = impl.settledShots();
        if (done > reported) { reported = done; ls.report(done, impl.checksum() >>> 0, impl.nextSeat()); }
      } else if (ls) {                       // lockstep: both players' inputs for this update, or wait
        var ins = ls.next(now());
        if (!ins) return false;
        evs = [];
        for (var k = 0; k < ins.length; k++) {
          var more = impl.apply(ins[k][0] - 1, ins[k][1], ins[k][2]);
          if (more && more.length) evs = evs.concat(more);
        }
        steps++;
        evs = evs.concat(impl.step() || []);
        ls.advance(impl.checksum);
        ls.flush(false);
      } else {
        steps++;
        evs = impl.step() || [];
      }
      for (var i = 0; i < evs.length; i++) {
        var ev = evs[i];
        play(impl.sounds && impl.sounds[ev.type]);
        emit(ev.type, ev);
      }
      reportScore();
      if (impl.isOver()) finish();
    }
    function finish() {
      state = "over";
      loop.stop();
      draw();
      var r = impl.result() || {};
      var out = { score: r.score || 0, level: r.level || 1, seconds: Math.round(steps / 60), stats: r.stats || {} };
      if (ls) {                              // what both phones must agree on (SPEC §13.4)
        ls.finish();
        var rep = impl.report ? impl.report() : {};
        // taking turns, the phones agree on the shots played (each counts its own updates while waiting for a shot)
        out.live = { tick: ls.turns ? impl.shots() : steps, sum: impl.checksum() >>> 0, scores: rep.scores, levels: rep.levels, winner: rep.winner };
      }
      safe(onEnd, out);
    }

    impl.init(seed);
    // A saved game (SPEC §12): opts.restore = { state, seconds }; the game carries on from there.
    var restoredSteps = 0;
    if (opts.restore && impl.restore) {
      impl.restore(opts.restore.state);
      restoredSteps = Math.max(0, Math.round((Number(opts.restore.seconds) || 0) * 60));
    }
    var renderer = createRenderer(canvas, lookId, { onInvalidate: function () { if (state !== "running" && state !== "destroyed") draw(); } });
    renderer.setReduceMotion(reduce());
    var loop = createLoop({ step: tick, draw: function (a) { alpha = a; draw(); }, onHidden: function () { inst.pause(); }, doc: canvas.ownerDocument,
      extra: ls && !ls.turns ? function () { return ls.behind() > 2 ? 1 : 0; } : null });
    if (Sound && opts.sound !== undefined) Sound.setEnabled(soundOn);
    draw();

    var inst = {
      start: function () {
        if (state === "destroyed" || state === "running") return;
        if (state === "paused") { inst.resume(); return; }
        if (state === "over") { seed = (seed + 1) >>> 0; impl.init(seed); }
        steps = restoredSteps; restoredSteps = 0; lastScore = -1; lastLevel = -1; alpha = 0;
        state = "running";
        reportScore();
        emit("start", { type: "start" });
        loop.start();
      },
      pause: function () {
        if (state !== "running") return;
        state = "paused"; loop.stop(); draw();
        emit("pause", { type: "pause", paused: true });
      },
      resume: function () {
        if (state !== "paused") return;
        state = "running"; loop.start();
        emit("pause", { type: "pause", paused: false });
      },
      /** Can this game be saved to finish later? */
      get canSave() { return !!impl.save; },
      /** The game as it stands, to save: { state, score, level, seconds } (null if it can't be saved). */
      save: function () {
        if (!impl.save || (state !== "running" && state !== "paused")) return null;
        return { state: impl.save(), score: impl.score(), level: impl.level(), seconds: Math.round(steps / 60) };
      },
      /** For playing together (a race, SPEC §13.3): where this game stands, to show the other player. */
      status: function () {
        return { score: impl.score(), level: impl.level(), over: state === "over", paused: state === "paused" };
      },
      get paused() { return state === "paused"; },
      /** Why this game, ended now, would not be saved (the game's own words, e.g. "Board not finished — not saved"), or "". */
      unfinishedNote: function () {
        var r = null;
        try { r = impl.result && impl.result(); } catch (e) { r = null; }
        return (r && r.stats && r.stats.notSaved) || "";
      },
      /** A live duel: the update reached, how long (ms) it has been waiting for the other phone, its seat. */
      liveStatus: function () {
        if (!ls) return null;
        var rep = impl.report ? impl.report() : null;
        return { tick: ls.tick, waitingMs: state === "running" ? ls.waiting(now()) : 0, seat: live.seat, delay: ls.delay,
          scores: rep ? rep.scores : null };
      },
      /** Turn by turn (SPEC §13.5): the match as the server now has it (a move made here or on another phone). The
          game rebuilds its board from it; the events it returns play their sounds; a match that ended ends the game. */
      turnSync: function (picture) {
        if (state === "destroyed" || !impl.turnSync) return;
        var evs = impl.turnSync(picture) || [];
        for (var i = 0; i < evs.length; i++) { play(impl.sounds && impl.sounds[evs[i].type]); emit(evs[i].type, evs[i]); }
        reportScore();
        if (state !== "running") draw();
      },
      /** A live duel the server ended (out of step, left, …): stop where it is, without a result. */
      stop: function () { if (state === "running" || state === "paused") { state = "over"; loop.stop(); draw(); } },
      get state() { return state; },
      get seconds() { return Math.round(steps / 60); },
      get score() { return impl.score(); },
      get level() { return impl.level(); },
      get renderer() { return renderer; },
      /** The game's rules state, read-only (for tests and debugging). */
      get logic() { return impl.logic ? impl.logic() : null; },
      setLook: function (id) {
        if (state === "destroyed") return;
        lookId = LOOKS[id] ? id : "modern";
        renderer.setLook(lookId);
        if (state !== "running") draw();
      },
      setSound: function (on) { soundOn = !!on; if (Sound) Sound.setEnabled(soundOn); },
      setReduceMotion: function (on) {
        userReduce = !!on; renderer.setReduceMotion(reduce());
        if (state !== "running" && state !== "destroyed") draw();
      },
      input: function (action, isDown) {
        if (action === "pause") {
          if (isDown) { if (state === "running") inst.pause(); else if (state === "paused") inst.resume(); }
          return;
        }
        if (state !== "running") return;
        if (ls) impl.input(action, isDown !== false, act);
        else impl.input(action, isDown !== false);
      },
      pointer: function (kind, x, y) {
        if (state !== "running" || !impl.pointer) return;
        var p = renderer.toLogical(x, y);
        if (ls) impl.pointer(kind, p.x, p.y, x, y, act);
        else impl.pointer(kind, p.x, p.y, x, y);
      },
      resize: function () {
        if (state === "destroyed") return;
        renderer.resize();
        if (state !== "running") draw();
      },
      destroy: function () {
        if (state === "destroyed") return;
        state = "destroyed";
        loop.destroy(); renderer.destroy();
      },
    };
    return inst;
  }

  var Kit = {
    W: W, H: H, STEP_MS: STEP_MS,
    LOOKS: LOOKS, LOOK_IDS: LOOK_IDS,
    createRenderer: createRenderer,
    createLoop: createLoop,
    createSession: createSession,
    makeG: makeG,
    fmt: fmt, hexA: hexA, clamp: clamp,
    prefersReducedMotion: prefersReducedMotion,
    bitmapWidth: bitmapWidth,
  };
  root.ArcadeKit = Kit;
})(typeof window !== "undefined" ? window : this);
