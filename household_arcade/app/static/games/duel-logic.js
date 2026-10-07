/* Household Arcade — Paddle Duel rules (pure: no DOM, deterministic for a given seed).

   You play the bottom paddle against the computer's top paddle in the 240 × 300 area. Each match is
   against the next opponent of the list (its own paddle speed, aim, serve and points to win); win it
   and the next comes on. Lose a match and the game ends; beat the last opponent and you've won.
   One call to step() is one update (60 a second).

   Mode `phones` (SPEC §13.4) is two people on two phones, one court: seat 1's paddle at the bottom, seat 2's at
   the top (seat 2's phone draws the court turned round, so each player sees their own paddle at the bottom).
   It is worked out in whole numbers only (positions in 1/256 px, angles from a table, no sin/cos), so every
   phone — whatever its JavaScript engine — computes exactly the same game; `checksum(s)` is compared every
   second. First to 7 points wins; each player's own score: a return 10, a point 100, the match 1,000. */
(function () {
  "use strict";

  var W = 240, H = 300;
  var WALL_L = 4, WALL_R = 236, TOP = 38;
  var PY = 266, PH = 8, PW = 48;            // your paddle (top edge PY)
  var CY = 46, CW = 44;                     // the computer's paddle (bottom edge CY + PH)
  var BALL_R = 4;
  var BASE_SPEED = 2.4, HIT_GAIN = 0.12, MAX_SPEED = 6;
  var MAX_ANGLE = 55 * Math.PI / 180;
  var KEY_SPEED = 5, KEY_ACCEL = 0.6, FOLLOW = 0.75, FOLLOW_MAX = 30;
  var TO_WIN = 5, OPPONENTS = 10;
  var SERVE_DELAY = 60;
  var RETURN_POINTS = 10, POINT_POINTS = 100, MATCH_POINTS = 1000;   // point and match × min(match, 10)
  var MULT_MAX = 10;
  var MODES = {
    easy: { cpu: -0.5, miss: 0.1 },
    normal: { cpu: 0, miss: 0 },
    hard: { cpu: 0.5, miss: -0.06 },
  };
  // Opponents (SPEC §11.9): every mode plays the list; the session's list (opts.levels) replaces the built-in
  // one. An opponent is { name, speed (its paddle, px an update), miss (the chance it misjudges a ball),
  // aim (how far from its paddle's middle it hits, a share of the paddle: more = steeper shots),
  // ball (serve speed, px an update), toWin (points to win the match) }. Easy / Normal / Hard adjust
  // speed and miss.
  var LEVELS = [
    { name: "Rookie", speed: 1.7, miss: 0.3, aim: 0.54, ball: 2.4, toWin: 5 },
    { name: "Rally", speed: 1.94, miss: 0.276, aim: 0.58, ball: 2.52, toWin: 5 },
    { name: "Swift", speed: 2.18, miss: 0.252, aim: 0.62, ball: 2.64, toWin: 5 },
    { name: "Wall", speed: 2.42, miss: 0.228, aim: 0.66, ball: 2.76, toWin: 5 },
    { name: "Spin", speed: 2.66, miss: 0.204, aim: 0.7, ball: 2.88, toWin: 5 },
    { name: "Echo", speed: 2.9, miss: 0.18, aim: 0.74, ball: 3, toWin: 5 },
    { name: "Blaze", speed: 3.14, miss: 0.156, aim: 0.78, ball: 3.12, toWin: 5 },
    { name: "Ace", speed: 3.38, miss: 0.132, aim: 0.82, ball: 3.24, toWin: 5 },
    { name: "Storm", speed: 3.62, miss: 0.108, aim: 0.86, ball: 3.36, toWin: 5 },
    { name: "Champion", speed: 3.86, miss: 0.084, aim: 0.9, ball: 3.48, toWin: 5 },
  ];
  var NAMES = LEVELS.map(function (o) { return o.name; });
  var FIELDS = { speed: [1, 4.4], miss: [0.05, 0.45], aim: [0.4, 0.9], ball: [2, 3.6], toWin: [3, 7] };

  /** The opponents to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (o) {
      if (!o || typeof o.name !== "string" || o.toWin !== Math.floor(o.toWin)) return false;
      for (var k in FIELDS) if (typeof o[k] !== "number" || !(o[k] >= FIELDS[k][0] && o[k] <= FIELDS[k][1])) return false;
      return true;
    });
    return out.length ? out : LEVELS;
  }
  function opponent(s) { return s.opponents[Math.min(s.level, s.opponents.length) - 1]; }
  function mult(s) { return Math.min(s.level, MULT_MAX); }
  function toWin(s) { return opponent(s).toWin; }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function cpuSpeed(s) { return clamp(opponent(s).speed + MODES[s.mode].cpu, 1, 4.4); }
  function missChance(s) { return clamp(opponent(s).miss + MODES[s.mode].miss, 0.05, 0.45); }
  function startSpeed(s) { return opponent(s).ball; }

  function create(o) {
    o = o || {};
    if (o.mode === "phones") return createPhones(o);
    var s = {
      mode: MODES[o.mode] ? o.mode : "normal", rng: (o.seed >>> 0) || 1,
      level: 1, score: 0, me: 0, cpu: 0,
      paddle: { x: W / 2, v: 0 }, cpuX: W / 2, cpuAim: W / 2,
      ball: { x: W / 2, y: H / 2, vx: 0, vy: 0, speed: BASE_SPEED },
      serve: SERVE_DELAY, serveDir: 1, rally: 0,
      over: false, won: false, updates: 0, shownUntil: SERVE_DELAY,
      input: { left: false, right: false, target: null },
      stats: { returns: 0, pointsWon: 0, pointsLost: 0, matches: 0, longestRally: 0 },
      opponents: usableLevels(o.levels),
    };
    return s;
  }

  function serveBall(s) {
    var an = (rand(s) - 0.5) * (Math.PI / 3);
    s.ball.speed = startSpeed(s);
    s.ball.vx = Math.sin(an) * s.ball.speed;
    s.ball.vy = Math.cos(an) * s.ball.speed * s.serveDir;
    s.serve = 0; s.rally = 0;
    if (s.serveDir < 0) planCpu(s, false);
  }

  /** Where the ball will cross the line y (bouncing off the side walls). */
  function predictX(s, y) {
    var b = s.ball;
    if (!b.vy) return b.x;
    var t = (y - b.y) / b.vy;
    if (t < 0) return b.x;
    var x = b.x + b.vx * t, lo = WALL_L + BALL_R, hi = WALL_R - BALL_R, span = hi - lo;
    var u = ((x - lo) % (2 * span) + 2 * span) % (2 * span);
    return lo + (u <= span ? u : 2 * span - u);
  }

  // The computer chooses where to meet the ball each time it heads its way: usually somewhere on its
  // paddle, sometimes (more often against steep shots) just off it.
  function planCpu(s, steep) {
    var land = predictX(s, CY + PH + BALL_R);
    var miss = rand(s) < missChance(s) + (steep ? 0.1 : 0);
    var off;
    if (miss) off = (rand(s) < 0.5 ? -1 : 1) * (CW / 2 + BALL_R + 4 + rand(s) * 14);
    else off = (rand(s) - 0.5) * CW * opponent(s).aim;   // better opponents hit nearer the edge: steeper shots
    s.cpuAim = land + off;
  }

  function bounceOff(s, px, w, dirUp) {
    var b = s.ball, off = clamp((b.x - px) / (w / 2), -1, 1);
    var an = off * MAX_ANGLE;
    b.speed = Math.min(MAX_SPEED, b.speed + HIT_GAIN);
    b.vx = Math.sin(an) * b.speed;
    b.vy = Math.cos(an) * b.speed * (dirUp ? -1 : 1);
    return Math.abs(off) > 0.7;
  }

  function point(s, mine, evs) {
    s.stats.longestRally = Math.max(s.stats.longestRally, s.rally);
    if (mine) {
      s.me++; s.stats.pointsWon++;
      s.score += POINT_POINTS * mult(s);
      evs.push({ type: "point", who: "me", me: s.me, cpu: s.cpu });
    } else {
      s.cpu++; s.stats.pointsLost++;
      evs.push({ type: "miss", who: "cpu", me: s.me, cpu: s.cpu });
    }
    s.ball.x = W / 2; s.ball.y = H / 2; s.ball.vx = 0; s.ball.vy = 0;
    s.serveDir = mine ? -1 : 1;     // the next serve goes toward whoever lost the point
    s.serve = SERVE_DELAY;
    if (s.me >= toWin(s)) {
      s.score += MATCH_POINTS * mult(s); s.stats.matches++;
      if (s.level >= s.opponents.length) {
        s.over = true; s.won = true;
        evs.push({ type: "win", score: s.score });
        return;
      }
      s.level++; s.me = 0; s.cpu = 0; s.serveDir = 1;
      s.shownUntil = s.updates + SERVE_DELAY * 2; s.serve = SERVE_DELAY * 2;
      evs.push({ type: "level", level: s.level });
    } else if (s.cpu >= toWin(s)) {
      s.over = true;
      evs.push({ type: "gameover", score: s.score });
    }
  }

  function movePaddle(s) {
    var p = s.paddle, inp = s.input, half = PW / 2;
    if (inp.left || inp.right) {
      inp.target = null;
      var want = (inp.right ? KEY_SPEED : 0) - (inp.left ? KEY_SPEED : 0);
      p.v += (want - p.v) * KEY_ACCEL;
      p.x += p.v;
    } else if (inp.target != null) {
      p.v = 0;
      p.x += clamp((inp.target - p.x) * FOLLOW, -FOLLOW_MAX, FOLLOW_MAX);
    } else p.v = 0;
    p.x = clamp(p.x, WALL_L + half, WALL_R - half);
  }

  function moveCpu(s) {
    var aim = s.ball.vy < 0 ? s.cpuAim : W / 2 + (s.ball.x - W / 2) * 0.3;
    var d = clamp(aim - s.cpuX, -cpuSpeed(s), cpuSpeed(s));
    s.cpuX = clamp(s.cpuX + d, WALL_L + CW / 2, WALL_R - CW / 2);
  }

  function step(s) {
    if (s.mode === "phones") return stepPhones(s);
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    movePaddle(s);
    moveCpu(s);
    if (s.serve > 0) {
      if (--s.serve === 0) { serveBall(s); evs.push({ type: "launch" }); }
      return evs;
    }
    var b = s.ball, n = Math.max(1, Math.ceil(b.speed / 2)), i;
    for (i = 0; i < n; i++) {
      var ox = b.x, oy = b.y;
      b.x += b.vx / n; b.y += b.vy / n;
      if (b.x < WALL_L + BALL_R) { b.x = 2 * (WALL_L + BALL_R) - b.x; b.vx = Math.abs(b.vx); evs.push({ type: "wall" }); }
      if (b.x > WALL_R - BALL_R) { b.x = 2 * (WALL_R - BALL_R) - b.x; b.vx = -Math.abs(b.vx); evs.push({ type: "wall" }); }
      // your paddle
      if (b.vy > 0 && oy + BALL_R <= PY + 2 && b.y + BALL_R >= PY && Math.abs(b.x - s.paddle.x) <= PW / 2 + BALL_R) {
        b.y = PY - BALL_R;
        var steep = bounceOff(s, s.paddle.x, PW, true);
        s.score += RETURN_POINTS; s.rally++; s.stats.returns++;
        evs.push({ type: "paddle", x: b.x });
        planCpu(s, steep);
        break;
      }
      // the computer's paddle
      if (b.vy < 0 && oy - BALL_R >= CY + PH - 2 && b.y - BALL_R <= CY + PH && Math.abs(b.x - s.cpuX) <= CW / 2 + BALL_R) {
        b.y = CY + PH + BALL_R;
        bounceOff(s, s.cpuX, CW, false);
        s.rally++;
        evs.push({ type: "hit", x: b.x });
        break;
      }
      if (b.y > H + BALL_R) { point(s, false, evs); break; }
      if (b.y < TOP - BALL_R) { point(s, true, evs); break; }
      if (ox === b.x && oy === b.y) break;
    }
    return evs;
  }

  function press(s, action, down, player) {
    if (s.mode === "phones") return pressPhones(s, action, down, player === 1 ? 1 : 0);
    if (action === "left" || action === "right") s.input[action] = !!down;
    else if ((action === "fire" || action === "up") && down && s.serve > 0 && s.updates >= s.shownUntil - SERVE_DELAY) s.serve = 1;
  }
  function setTarget(s, x) {
    if (s.mode === "phones") { pressPhones(s, "aim", Math.round(x), 0); return; }
    s.input.target = clamp(x, WALL_L + PW / 2, WALL_R - PW / 2);
  }

  // ---------- two phones (mode "phones"): whole numbers only ----------
  var FP = 256;                                // 1 px = 256
  var P_TOP = 46, P_BOTTOM = 266;              // the paddles' top edges: seat 2 at the top, seat 1 at the bottom
  var OUT_TOP = 34, OUT_BOTTOM = 286;          // past these the ball is out (the court is the same from either end: y ↔ 320 − y)
  var MIRROR = P_TOP + PH + P_BOTTOM;          // 320
  var PHONES_TO_WIN = 7;
  var F_BASE = 614, F_GAIN = 31, F_MAX = 1536, F_KEY = 1280, F_FOLLOW_MAX = 30 * FP;   // 2.4, 0.12, 6, 5, 30 px
  // sin and cos of 0°, 5.5°, 11° … 55°, × 4096 (index = how far off the paddle's middle the ball hit, in tenths)
  var SIN = [0, 393, 782, 1163, 1534, 1891, 2231, 2550, 2845, 3115, 3355];
  var COS = [4096, 4077, 4021, 3927, 3798, 3633, 3435, 3206, 2946, 2660, 2349];
  var PAD_LO = (WALL_L + PW / 2) * FP, PAD_HI = (WALL_R - PW / 2) * FP;
  var BALL_LO = (WALL_L + BALL_R) * FP, BALL_HI = (WALL_R - BALL_R) * FP;

  function trunc(v) { return v < 0 ? Math.ceil(v) : Math.floor(v); }
  function newPad() { return { x: (W / 2) * FP, v: 0, left: false, right: false, target: null }; }
  function createPhones(o) {
    var s = {
      mode: "phones", rng: (o.seed >>> 0) || 1, level: 1, score: 0, score2: 0, points: [0, 0],
      pads: [newPad(), newPad()], ball: { x: (W / 2) * FP, y: (MIRROR / 2) * FP, vx: 0, vy: 0, speed: F_BASE },
      serve: SERVE_DELAY * 2, serveTo: 0, rally: 0, over: false, won: false, winner: 0, updates: 0,
      shownUntil: SERVE_DELAY * 2,
      stats: [{ returns: 0, pointsWon: 0, pointsLost: 0, longestRally: 0 }, { returns: 0, pointsWon: 0, pointsLost: 0, longestRally: 0 }],
      opponents: usableLevels(o.levels),
    };
    s.serveTo = rand(s) < 0.5 ? 0 : 1;          // who gets the first ball comes from the seed
    return s;
  }
  function launchPhones(s) {
    var i = Math.floor(rand(s) * 11) - 5, a = i < 0 ? -i : i, b = s.ball;
    b.speed = F_BASE;
    b.vx = (i < 0 ? -1 : 1) * trunc(b.speed * SIN[a] / 4096);
    b.vy = (s.serveTo === 0 ? 1 : -1) * trunc(b.speed * COS[a] / 4096);
    s.serve = 0; s.rally = 0;
  }
  function movePad(p) {
    if (p.left || p.right) {
      p.target = null;
      var want = (p.right ? F_KEY : 0) - (p.left ? F_KEY : 0);
      p.v += trunc((want - p.v) * 3 / 5);
      p.x += p.v;
    } else if (p.target !== null) {
      p.v = 0;
      p.x += clamp(trunc((p.target - p.x) * 3 / 4), -F_FOLLOW_MAX, F_FOLLOW_MAX);
    } else p.v = 0;
    p.x = clamp(p.x, PAD_LO, PAD_HI);
  }
  /** The ball meets player i's paddle: off the middle sets the angle (in steps of 5.5°, at most 55°). */
  function bouncePhones(s, i) {
    var b = s.ball, p = s.pads[i];
    // rounded the same way on either side, so the court is exactly the same from either end
    var d = (b.x - p.x) * 20, q = Math.min(10, Math.round(Math.abs(d) / (PW * FP))), idx = d < 0 ? -q : q, a = q;
    b.speed = Math.min(F_MAX, b.speed + F_GAIN);
    b.vx = (idx < 0 ? -1 : 1) * trunc(b.speed * SIN[a] / 4096);
    b.vy = (i === 0 ? -1 : 1) * trunc(b.speed * COS[a] / 4096);
    s.rally++; s.stats[i].returns++;
    if (i === 0) s.score += RETURN_POINTS; else s.score2 += RETURN_POINTS;
  }
  function pointPhones(s, w, evs) {
    var l = 1 - w;
    s.points[w]++;
    s.stats[w].pointsWon++; s.stats[l].pointsLost++;
    s.stats[0].longestRally = s.stats[1].longestRally = Math.max(s.stats[0].longestRally, s.rally);
    if (w === 0) s.score += POINT_POINTS; else s.score2 += POINT_POINTS;
    evs.push({ type: "point", player: w + 1, points: s.points.slice() });
    s.ball.x = (W / 2) * FP; s.ball.y = (MIRROR / 2) * FP; s.ball.vx = 0; s.ball.vy = 0; s.ball.speed = F_BASE;
    s.serveTo = l; s.serve = SERVE_DELAY; s.shownUntil = s.updates + SERVE_DELAY;
    if (s.points[w] >= PHONES_TO_WIN) {
      if (w === 0) s.score += MATCH_POINTS; else s.score2 += MATCH_POINTS;
      s.over = true; s.winner = w + 1; s.won = w === 0;
      evs.push({ type: w === 0 ? "win" : "gameover", winner: w + 1, score: s.score, score2: s.score2 });
    }
  }
  function stepPhones(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    movePad(s.pads[0]); movePad(s.pads[1]);
    if (s.serve > 0) {
      if (--s.serve === 0) { launchPhones(s); evs.push({ type: "launch" }); }
      return evs;
    }
    var b = s.ball, n = Math.max(1, Math.ceil(b.speed / (2 * FP))), r = BALL_R * FP;
    for (var k = 0; k < n; k++) {
      var oy = b.y, dx = trunc(b.vx / n), dy = trunc(b.vy / n);
      b.x += dx; b.y += dy;
      if (b.x < BALL_LO) { b.x = 2 * BALL_LO - b.x; b.vx = Math.abs(b.vx); evs.push({ type: "wall" }); }
      if (b.x > BALL_HI) { b.x = 2 * BALL_HI - b.x; b.vx = -Math.abs(b.vx); evs.push({ type: "wall" }); }
      // seat 1's paddle (bottom)
      if (b.vy > 0 && oy + r <= (P_BOTTOM + 2) * FP && b.y + r >= P_BOTTOM * FP && Math.abs(b.x - s.pads[0].x) <= (PW / 2 + BALL_R) * FP) {
        b.y = (P_BOTTOM - BALL_R) * FP;
        bouncePhones(s, 0);
        evs.push({ type: "paddle", player: 1 });
        break;
      }
      // seat 2's paddle (top)
      if (b.vy < 0 && oy - r >= (P_TOP + PH - 2) * FP && b.y - r <= (P_TOP + PH) * FP && Math.abs(b.x - s.pads[1].x) <= (PW / 2 + BALL_R) * FP) {
        b.y = (P_TOP + PH + BALL_R) * FP;
        bouncePhones(s, 1);
        evs.push({ type: "paddle", player: 2 });
        break;
      }
      if (b.y > OUT_BOTTOM * FP) { pointPhones(s, 1, evs); break; }
      if (b.y < OUT_TOP * FP) { pointPhones(s, 0, evs); break; }
    }
    return evs;
  }
  function pressPhones(s, action, down, i) {
    var p = s.pads[i];
    if (!p || s.over) return [];
    if (action === "left" || action === "right") p[action] = !!down;
    else if (action === "aim") p.target = clamp(Math.round(Number(down) || 0) * FP, PAD_LO, PAD_HI);
    else if ((action === "fire" || action === "up") && down && s.serve > 0 && s.serveTo === i && s.updates >= s.shownUntil - SERVE_DELAY) s.serve = 1;
    return [];
  }

  function hash(str) {          // FNV-1a, unsigned 32-bit (the same as ArcadeLockstep.hash)
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  /** What both phones must agree on (SPEC §13.4). */
  function checksum(s) {
    var b = s.ball, parts = [s.updates, s.serve, s.serveTo, s.rally, s.points[0], s.points[1], s.score, s.score2, s.rng,
      b.x, b.y, b.vx, b.vy, b.speed, s.over ? 1 : 0, s.winner];
    s.pads.forEach(function (p) { parts.push(p.x, p.v, p.left ? 1 : 0, p.right ? 1 : 0, p.target === null ? "n" : p.target); });
    return hash(parts.join(","));
  }
  /** Both players' results for the match: each score, and who won (1 or 2). */
  function report(s) { return { scores: [s.score, s.score2], levels: [1, 1], winner: s.winner || 0 }; }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "input" && k !== "opponents") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var opponents = usableLevels(levels);
    var ok = data && typeof data === "object" && data.ball && data.paddle && typeof data.paddle.x === "number" &&
      typeof data.score === "number" && data.level >= 1 && data.level <= opponents.length && typeof data.rng === "number" &&
      MODES[data.mode] && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.opponents = opponents;
    s.input = { left: false, right: false, target: null };
    s.paddle.v = 0;
    return s;
  }

  function result(s, player) {
    if (s.mode === "phones") {
      var i = player === 1 ? 1 : 0, st = s.stats[i];
      return { score: i ? s.score2 : s.score, level: 1, stats: { won: s.winner === i + 1, cause: s.over ? (s.winner === i + 1 ? "won" : "lost") : null,
        matches: s.winner === i + 1 ? 1 : 0, pointsWon: st.pointsWon, pointsLost: st.pointsLost, returns: st.returns,
        longestRally: st.longestRally, mode: s.mode } };
    }
    return {
      score: s.score, level: s.level,
      stats: { won: s.won, cause: s.won ? "won" : s.over ? "lost" : null, matches: s.stats.matches, pointsWon: s.stats.pointsWon, pointsLost: s.stats.pointsLost,
        returns: s.stats.returns, longestRally: s.stats.longestRally, mode: s.mode },
    };
  }

  var DuelLogic = {
    W: W, H: H, WALL_L: WALL_L, WALL_R: WALL_R, TOP: TOP, PY: PY, PH: PH, PW: PW, CY: CY, CW: CW, BALL_R: BALL_R,
    MAX_SPEED: MAX_SPEED, TO_WIN: TO_WIN, OPPONENTS: OPPONENTS, LEVELS: LEVELS, usableLevels: usableLevels,
    opponent: opponent, toWin: toWin, mult: mult, MULT_MAX: MULT_MAX, SERVE_DELAY: SERVE_DELAY, NAMES: NAMES, MODES: MODES,
    RETURN_POINTS: RETURN_POINTS, POINT_POINTS: POINT_POINTS, MATCH_POINTS: MATCH_POINTS, STATE_VERSION: STATE_VERSION,
    create: create, step: step, press: press, setTarget: setTarget, predictX: predictX, cpuSpeed: cpuSpeed,
    missChance: missChance, save: save, restore: restore, result: result, rand: rand, checksum: checksum, report: report,
    FP: FP, P_TOP: P_TOP, P_BOTTOM: P_BOTTOM, OUT_TOP: OUT_TOP, OUT_BOTTOM: OUT_BOTTOM, MIRROR: MIRROR, PHONES_TO_WIN: PHONES_TO_WIN,
  };
  if (typeof module === "object" && module.exports) module.exports = DuelLogic; else self.DuelLogic = DuelLogic;
})();
