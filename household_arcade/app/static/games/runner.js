/* Household Arcade — Runner: input and drawing around the rules in runner-logic.js.
   Registers itself as "runner" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.RunnerLogic;

  var MODES = [{ id: "classic", label: "Endless" }, { id: "easy", label: "Easy (3 lives)" }, { id: "courses", label: "Courses" }];
  var GY = Logic.GY, PX = Logic.PX, TOP = Logic.TOP, W = Kit.W;
  var CAUSES = { box: "BUMP!", pit: "DOWN THE PIT!", bar: "BONK!", flier: "OOF!" };

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prevDist = 0, prevY = GY, ptr = null;

    function snap() { prevDist = s.dist; prevY = s.y; }
    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels, startLevel: opts.startLevel }); pending = []; snap(); },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; snap(); },
      step: function () {
        snap();
        var evs = pending.concat(Logic.step(s));
        pending = [];
        if (s.dist < prevDist) prevDist = s.dist;          // a new course starts at 0
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { jump: "turn", coin: "eat", hit: "lose", course: "row", level: "level", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      // a finger on the game: down jumps (held: higher), dragging down ducks
      pointer: function (kind, lx, ly) {
        if (kind === "down") { ptr = { y: ly, duck: false }; impl.input("up", true); }
        else if (kind === "move" && ptr) {
          var duck = ly - ptr.y > 18;
          if (duck !== ptr.duck) { ptr.duck = duck; if (duck) impl.input("up", false); impl.input("down", duck); }
        } else if (kind === "up") {
          impl.input("up", false); if (ptr && ptr.duck) impl.input("down", false);
          ptr = null;
        }
      },
      draw: function (g, info) {
        var fmt = Kit.fmt, lcd = g.kind === "lcd", a = info.state === "running" ? info.alpha : 1, i;
        var dist = prevDist + (s.dist - prevDist) * a, y = prevY + (s.y - prevY) * a;
        var c = Logic.course(s);
        g.hud("SCORE " + fmt(s.score), s.courses ? "COURSE " + s.level + "/" + s.courses.length : "LEVEL " + s.level);

        var ctx = g.ctx;
        ctx.save();
        ctx.beginPath(); ctx.rect(0, 36, W, Kit.H - 36); ctx.clip();
        // far hills, slowly scrolling (a cached strip drawn twice)
        if (!lcd) {
          var off = -((dist * 0.25) % W);
          ctx.save(); ctx.beginPath(); ctx.rect(0, 36, W, GY - 36); ctx.clip();
          for (var k = 0; k < 2; k++) {
            ctx.save(); ctx.translate(g.snap(off + k * W), 0);
            g.layer("runner-hills", 1, function (lg) {
              for (var hx = -20; hx < W + 40; hx += 60) lg.circle(hx + 30, GY + 18, 44, 8, { a: 0.5, edge: false });
              lg.circle(190, 78, 12, 3, { a: 0.6, edge: false });
            });
            ctx.restore();
          }
          ctx.restore();
        }
        // the ground, with gaps for pits
        var cuts = [];
        for (i = 0; i < s.obs.length; i++) if (s.obs[i].k === "p") cuts.push([s.obs[i].x - dist, s.obs[i].x - dist + s.obs[i].w]);
        cuts.sort(function (p, q) { return p[0] - q[0]; });
        var gx = 0;
        for (i = 0; i <= cuts.length; i++) {
          var end = i < cuts.length ? cuts[i][0] : W;
          if (end > gx) {
            g.rect(g.snap(gx), GY, g.snap(end) - g.snap(gx), Kit.H - GY, 4, { r: 0, a: lcd ? 1 : 0.55, solid: g.kind === "modern" || g.kind === "pixel" || g.kind === "contrast", edge: false });
            g.line(gx, GY, end, GY, 0, 2);
          }
          if (i < cuts.length) gx = Math.max(gx, cuts[i][1]);
        }
        for (var dx = -(dist % 24); dx < W; dx += 24) {
          var inPit = false;
          for (i = 0; i < cuts.length; i++) if (dx + 8 > cuts[i][0] && dx < cuts[i][1]) inPit = true;
          if (!inPit) g.line(g.snap(dx), GY + 8, g.snap(dx + 8), GY + 8, lcd ? 9 : 0, 2, { a: lcd ? 1 : 0.3 });
        }

        // things on the way
        for (i = 0; i < s.obs.length; i++) {
          var o = s.obs[i], x = g.snap(o.x - dist);
          if (x > W + 4 || x + o.w < -4) continue;
          if (o.k === "coin") { g.circle(x + 4, (o.top + o.bot) / 2, 4, 3, { solid: true, stroke: true }); if (!g.lowres) g.circle(x + 3, (o.top + o.bot) / 2 - 1, 1.3, 9, { a: 0.8, edge: false }); }
          else if (o.k === "b" || o.k === "B") drawCrates(g, x, o);
          else if (o.k === "h") drawBar(g, x, o);
          else if (o.k === "f") drawFlier(g, x, o, info.t, info.reduce);
        }

        // the runner (knocked over while a life is lost; blinking while safe, a ring with reduce motion)
        if (s.phase === "dead") drawRunner(g, PX, g.snap(Math.min(y, GY + 20)), s, dist, true, true);
        else {
          var blink = s.safe > 0 && !info.reduce && Math.floor(s.safe / 10) % 2 === 1;
          if (!blink) drawRunner(g, PX, g.snap(y), s, dist, info.reduce);
          if (s.safe > 0 && info.reduce) g.ring(PX, y - 13, 16, 7, 1.5, { a: 0.8 });
        }
        ctx.restore();

        // lives and the course's progress
        if (s.mode !== "classic") for (i = 0; i < s.lives; i++) g.circle(14 + i * 12, 46, 4, 1, { solid: true });
        if (c) {
          var total = (c.pattern.length + Logic.LEAD + 1) * Logic.SEG, frac = Math.max(0, Math.min(1, (s.dist + PX) / total));
          g.rect(60, 43, 170, 6, 8, { r: 2, a: lcd ? 1 : 0.6 });
          if (frac > 0) g.rect(60, 43, Math.max(2, 170 * frac), 6, 4, { r: 2, solid: true });
        }
        if (info.state !== "over") {
          if (s.phase === "dead") banner(g, CAUSES[s.stats.cause] || "OOPS!", s.lives > 0 ? s.lives + (s.lives === 1 ? " LIFE LEFT" : " LIVES LEFT") : "");
          else if (s.phase === "clear") banner(g, "COURSE DONE!", "COURSE " + (s.level + 1) + " NEXT");
          else if (c && s.dist < 160) banner(g, c.name.toUpperCase(), "COURSE " + s.level + "/" + s.courses.length);
          else if (!c && s.dist < 100) g.text("JUMP! DUCK! RUN!", W / 2, 100, { size: 12, align: "center", a: 0.85 });
        }
      },
    };

    function drawCrates(g, x, o) {
      var n = o.k === "B" ? 2 : 1, hgt = (o.bot - o.top) / n;
      for (var k = 0; k < n; k++) {
        var yy = o.top + k * hgt;
        g.rect(x, yy, o.w, hgt - 0.5, 2, { r: 1, solid: g.kind !== "lcd", stroke: true });
        if (!g.lowres) { g.line(x + 2, yy + 2, x + o.w - 2, yy + hgt - 2.5, 0, 1, { a: 0.5 }); g.line(x + o.w - 2, yy + 2, x + 2, yy + hgt - 2.5, 0, 1, { a: 0.5 }); }
      }
    }
    function drawBar(g, x, o) {
      g.line(x + 3, TOP, x + 3, o.bot - 12, 0, 1.5, { a: 0.6 });
      g.line(x + o.w - 3, TOP, x + o.w - 3, o.bot - 12, 0, 1.5, { a: 0.6 });
      g.rect(x, o.bot - 13, o.w, 13, 1, { r: 1, solid: g.kind !== "lcd", stroke: true });
      if (!g.lowres) for (var k = 0; k < 3; k++) g.line(x + 3 + k * 7, o.bot - 1, x + 8 + k * 7, o.bot - 12, 9, 2, { a: 0.8 });
    }
    function drawFlier(g, x, o, t, still) {
      var cy = (o.top + o.bot) / 2, flap = still ? 0 : Math.floor(t * 6) % 2;
      if (g.lowres) g.rect(x + 2, cy - 3, o.w - 4, 6, 6, { solid: true });
      else g.ellipse(x + o.w / 2, cy, o.w / 2 - 1, 4, 6, { solid: true, stroke: true });
      g.line(x + 5, cy - 1, x + 9, flap ? cy - 9 : cy + 6, 6, 2);
      g.line(x + 11, cy - 1, x + 13, flap ? cy - 8 : cy + 5, 6, 2);
      g.circle(x + 2, cy - 1, 1.6, g.kind === "lcd" ? 9 : 0, { solid: true, white: true });
    }
    function drawRunner(g, x, y, st, dist, still, down) {
      var duck = st.duck && !down, ground = st.ground;
      if (down) { g.rect(x - 10, y - 8, 20, 8, 5, { r: 3, solid: true }); g.circle(x + 12, y - 5, 5, 5, { solid: true }); return; }
      var hgt = duck ? Logic.DUCK_H : Logic.STAND_H;
      var head = y - hgt + 5;
      g.circle(x + (duck ? 4 : 0), head, 5, 5, { solid: true, stroke: true });
      if (g.kind === "lcd") g.circle(x + (duck ? 6 : 2), head - 1, 1.4, 9, { solid: true, white: true });
      else g.circle(x + (duck ? 6 : 2), head - 1, 1.2, 9, { edge: false });
      var hip = y - (duck ? 4 : 9);
      g.line(x + (duck ? 2 : 0), head + 4, x - 1, hip, 5, 3);
      var ph = still || !ground ? 0 : Math.floor(dist / 9) % 2;
      if (!ground) { g.line(x - 1, hip, x + 5, y - 3, 5, 2.5); g.line(x - 1, hip, x - 6, y - 1, 5, 2.5); }
      else if (duck) { g.line(x - 1, hip, x + 5, y, 5, 2.5); g.line(x - 1, hip, x - 6, y, 5, 2.5); }
      else { g.line(x - 1, hip, ph ? x + 5 : x - 4, y, 5, 2.5); g.line(x - 1, hip, ph ? x - 5 : x + 4, y, 5, 2.5); }
      if (!duck) g.line(x, head + 7, ph ? x + 6 : x - 5, head + 12, 5, 2);
    }
    function banner(g, a, b) {
      var y = 140, dim = g.kind === "pixel" || g.kind === "neon", ci = dim ? 0 : 9;
      g.rect(30, y - 18, 180, b ? 40 : 26, dim ? 8 : 0, { solid: true, r: 6, a: 0.92 });
      g.text(a, W / 2, y + (g.kind === "lcd" ? 2 : 0), { size: 13, align: "center", ci: ci });
      if (b) g.text(b, W / 2, y + 15, { size: 10, align: "center", ci: ci });
    }

    return Kit.createSession(canvas, opts, impl);
  }

  root.ArcadeGames.register({
    id: "runner",
    name: "Runner",
    modes: MODES,
    defaultMode: "classic",
    controls: "buttons",
    buttons: [
      { action: "down", label: "▼ Duck", aria: "Duck", wide: true },
      { action: "up", label: "▲ Jump", aria: "Jump", wide: true },
    ],
    help: "Space or ↑ jumps (hold for a higher jump), ↓ ducks (in the air: drop faster). On a phone tap the game to jump and drag down to duck, or use the buttons. Jump boxes and pits, duck under bars, jump or duck the fliers, and grab the coins.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
