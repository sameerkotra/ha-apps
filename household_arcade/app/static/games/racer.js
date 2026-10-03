/* Household Arcade — Lane Racer: input and drawing around the rules in racer-logic.js.
   Registers itself as "racer" with window.ArcadeGames. */
(function (root) {
  "use strict";
  var Kit = root.ArcadeKit, Logic = root.RacerLogic;

  var MODES = [{ id: "three", label: "3 lanes" }, { id: "four", label: "4 lanes" }, { id: "rush", label: "Rush (one life)" },
    { id: "stages", label: "Stages" }];
  var DASH = 30;

  function create(canvas, opts) {
    opts = opts || {};
    var s = null, pending = [], prevDist = 0, prevX = 0, shake = 0, reduceFlag = !!opts.reduceMotion;

    var impl = {
      init: function (seed) { s = Logic.create({ mode: opts.mode, seed: seed, levels: opts.levels }); pending = []; prevDist = s.dist; prevX = s.x; shake = 0; },
      save: function () { return Logic.save(s); },
      restore: function (data) { s = Logic.restore(data, opts.levels); pending = []; prevDist = s.dist; prevX = s.x; shake = 0; },
      step: function () {
        prevDist = s.dist; prevX = s.x;
        var evs = pending.concat(Logic.step(s));
        pending = [];
        for (var i = 0; i < evs.length; i++) if (evs[i].type === "crash" && !(Kit.prefersReducedMotion() || reduceFlag)) shake = 18;
        if (shake > 0) shake--;
        return evs;
      },
      logic: function () { return s; },
      score: function () { return s.score; },
      level: function () { return s.level; },
      isOver: function () { return s.over; },
      result: function () { return Logic.result(s); },
      sounds: { turn: "turn", coin: "eat", crash: "lose", level: "speed", win: "win", gameover: "gameover" },
      input: function (action, down) {
        var evs = Logic.press(s, action, down);
        for (var i = 0; i < evs.length; i++) pending.push(evs[i]);
      },
      pointer: function (kind, lx) {
        // a tap on the left half of the game moves a lane left; on the right half, right
        if (kind === "down") impl.input(lx < Kit.W / 2 ? "left" : "right", true);
      },
      draw: function (g, info) {
        var a = info.alpha, fmt = Kit.fmt, reduce = info.reduce, i;
        var dist = prevDist + (s.dist - prevDist) * a, x = prevX + (s.x - prevX) * a;
        var top = 36;
        if (shake > 0 && !reduce) {
          var m = shake / 18 * 2.5;
          g.ctx.translate(g.snap((Math.random() - 0.5) * 2 * m), g.snap((Math.random() - 0.5) * 2 * m));
        }
        // the roadside and road
        g.rect(0, top, Logic.ROAD_L - 2, Kit.H - top, 4, { a: g.kind === "lcd" ? 0.3 : 0.35, r: 0, edge: false });
        g.rect(Logic.ROAD_R + 2, top, Kit.W - Logic.ROAD_R - 2, Kit.H - top, 4, { a: g.kind === "lcd" ? 0.3 : 0.35, r: 0, edge: false });
        g.line(Logic.ROAD_L, top, Logic.ROAD_L, Kit.H, 0, 2);
        g.line(Logic.ROAD_R, top, Logic.ROAD_R, Kit.H, 0, 2);
        var off = dist % DASH, lw = Logic.laneW(s);
        for (var l = 1; l < s.lanes; l++) {
          var lx = g.snap(Logic.ROAD_L + lw * l);
          for (var y = top - DASH + off; y < Kit.H; y += DASH) {
            var y0 = Math.max(top, y), y1 = Math.min(Kit.H, y + DASH / 2);
            if (y1 > y0) g.line(lx, g.snap(y0), lx, g.snap(y1), 8, 2);
          }
        }
        // roadside posts, moving with the road
        var poff = dist % 60;
        for (var py = top - 60 + poff; py < Kit.H; py += 60) {
          if (py < top) continue;
          g.rect(12, g.snap(py), 6, 6, 8, { r: 1 });
          g.rect(222, g.snap(py + 30 < Kit.H ? py + 30 : py), 6, 6, 8, { r: 1 });
        }

        // the stage's finish line: a band of checks across the road
        if (s.finishD != null) {
          var fy = g.snap(Logic.CAR_Y - (s.finishD - dist));
          if (fy > top + 12 && fy < Kit.H) {
            for (var fx = 0; fx < (Logic.ROAD_R - Logic.ROAD_L) / 6; fx++) {
              g.rect(Logic.ROAD_L + fx * 6, fy - 12 + (fx % 2) * 6, 6, 6, 0, { r: 0, solid: true, edge: false });
            }
            g.line(Logic.ROAD_L, fy - 12, Logic.ROAD_R, fy - 12, 0, 1);
            g.line(Logic.ROAD_L, fy, Logic.ROAD_R, fy, 0, 1);
          }
        }

        // coins
        for (i = 0; i < s.coins.length; i++) {
          var co = s.coins[i], cy = Logic.CAR_Y - (co.d - dist);
          if (cy < top + Logic.COIN_R) continue;
          g.circle(g.snap(Logic.laneX(s, co.lane)), g.snap(cy), Logic.COIN_R, 3, { solid: true });
        }
        // traffic
        var cw = Logic.carW(s);
        for (i = 0; i < s.cars.length; i++) {
          var c = s.cars[i], ty = Logic.CAR_Y - (c.d - dist);
          if (ty + c.h / 2 < top) continue;
          drawCar(g, Logic.laneX(s, c.lane), ty, cw, c.h, c.ci, true, c.truck, top);
        }
        // your car (blinks slowly while it can't crash again, or shows a ring with reduce motion)
        var safe = s.safe > 0 && info.state !== "over";
        var hide = safe && !reduce && Math.floor(s.safe / 12) % 2 === 1;
        if (!hide) drawCar(g, x, Logic.CAR_Y, cw, Logic.CAR_H, s.over ? 1 : 0, false, false, top);
        if (safe && reduce && !g.lowres) g.ring(x, Logic.CAR_Y, Logic.CAR_H * 0.7, 7, 1.5, { a: s.safe / Logic.SAFE_UPDATES });

        var st = Logic.stage(s);
        g.hud("SCORE " + fmt(s.score), st ? "STAGE " + s.level + "/" + s.stages.length : "LEVEL " + s.level);
        if (st) {
          g.text(Math.max(0, st.rows - s.stagePassed) + " TO GO", Kit.W / 2, 293, { size: 9, align: "center", a: 0.7 });
          // the stage's name as it starts (and "STAGE CLEAR" after the last one's finish)
          if (s.banner > 0 && info.state !== "over") {
            g.rect(36, 92, 168, 50, 9, { r: 6, stroke: true, a: 0.95 });
            g.text(s.level > 1 ? "STAGE CLEAR" : "STAGE 1", Kit.W / 2, 112, { size: 12, align: "center" });
            g.text((s.level > 1 ? "NEXT: " : "") + fitName(st.name, g.lowres ? 13 : 20), Kit.W / 2, 131, { size: 9, align: "center", a: 0.85 });
          }
        }
        for (i = 0; i < s.lives; i++) g.rect(Logic.ROAD_L + 6 + i * 14, 288, 10, 5, 0, { r: 2, a: 0.7 });
        g.text(Math.round(Logic.speed(s) * 36) + " KM/H", Logic.ROAD_R - 6, 293, { size: 9, align: "right", a: 0.7 });
      },
    };

    // a stage name, shortened to fit
    function fitName(n, max) { n = String(n).toUpperCase(); return n.length > max ? n.slice(0, max - 1).trim() + "." : n; }

    function drawCar(g, cx, cy, w, h, ci, oncoming, truck, top) {
      var x0 = g.snap(cx - w / 2), y0 = g.snap(cy - h / 2);
      if (y0 < top) { h -= top - y0; y0 = top; if (h <= 2) return; }
      g.rect(x0, y0, w, h, ci, { r: 5, bevel: true });
      if (g.lowres && h < 10) return;
      // windows: the front window at the top for your car, at the bottom for traffic coming toward you
      var win = truck ? 10 : 8, wy = oncoming ? y0 + h - win - 5 : y0 + 6;
      if (wy >= top) g.rect(x0 + 4, wy, w - 8, win, 9, { r: 2, a: 0.8, edge: false });
      if (truck) g.line(x0 + 3, y0 + h - 22, x0 + w - 3, y0 + h - 22, 9, 1.5, { a: 0.6 });
    }

    var inst = Kit.createSession(canvas, opts, impl);
    var setRM = inst.setReduceMotion;
    inst.setReduceMotion = function (on) { reduceFlag = !!on; if (reduceFlag) shake = 0; setRM(on); };
    return inst;
  }

  root.ArcadeGames.register({
    id: "racer",
    name: "Lane Racer",
    modes: MODES,
    defaultMode: "three",
    controls: "buttons",
    buttons: [{ action: "left", label: "◀", aria: "Left lane", wide: true }, { action: "right", label: "▶", aria: "Right lane", wide: true }],
    help: "← → (or A D) change lanes; on a phone, tap the left or right half of the game or use the buttons. Every row of traffic has a way through. Coins score extra; the road gets faster. In Stages, reach each stage's finish line; 3 lives for the whole run.",
    stateVersion: Logic.STATE_VERSION,
    create: create,
  });
})(typeof window !== "undefined" ? window : this);
