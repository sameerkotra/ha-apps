/* Household Arcade — Lane Racer rules (pure: no DOM, deterministic for a given seed).

   Your car drives up a road of 3 or 4 lanes; traffic comes in rows and you change lanes to get
   past. Every row leaves a way through that a lane change can reach, so a crash is always
   avoidable. The road gets faster as you go. One call to step() is one update (60 a second).
   Distances are logical pixels of road. */
(function () {
  "use strict";

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  var W = 240, H = 300;
  var ROAD_L = 30, ROAD_R = 210;
  var CAR_Y = 250;                 // the middle of your car on screen
  var CAR_H = 38, TRUCK_H = 62;
  var CHANGE = 10;                 // updates to move one lane
  var LEVEL_DIST = 2500;           // road between levels
  var MAX_LEVEL = 40;
  var SPEED_GAIN = 0.25, MAX_SPEED = 7.5;
  var COIN_POINTS = 25, COIN_R = 6;
  var SAFE_UPDATES = 90;           // after a crash, you can't crash again for 1.5 s
  var SPAWN_AHEAD = 340;           // rows appear this far ahead of your car (above the screen)
  var MODES = {
    three: { lanes: 3, speed: 3, lives: 3 },
    four: { lanes: 4, speed: 3, lives: 3 },
    rush: { lanes: 3, speed: 5, lives: 1 },
    stages: { lanes: 3, speed: 3, lives: 3 },   // Stages: the numbers come from the level list
  };
  var TRAFFIC = 0.5, TRUCKS = 0.2, COINS = 0.45;  // the endless modes' traffic

  // Stages (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. A stage is
  // { name, lanes (3 or 4), speed (px an update), rows (rows of traffic to the finish), traffic (how full a
  // row is beyond its one car), trucks (the share of trucks), coins (the chance of a coin after a row) }.
  // The rows still come from the seeded generator with its always-a-way-through rules.
  var LEVELS = [
    { name: "Sunday drive", lanes: 3, speed: 3, rows: 15, traffic: 0.2, trucks: 0.1, coins: 0.5 },
    { name: "Country road", lanes: 3, speed: 3.5, rows: 20, traffic: 0.3, trucks: 0.15, coins: 0.5 },
    { name: "Market day", lanes: 4, speed: 3.5, rows: 25, traffic: 0.4, trucks: 0.2, coins: 0.45 },
    { name: "Coast road", lanes: 3, speed: 4.25, rows: 30, traffic: 0.4, trucks: 0.2, coins: 0.5 },
    { name: "Rush hour", lanes: 4, speed: 4.5, rows: 35, traffic: 0.6, trucks: 0.25, coins: 0.4 },
    { name: "Truck stop", lanes: 3, speed: 5, rows: 40, traffic: 0.5, trucks: 0.45, coins: 0.4 },
    { name: "Night run", lanes: 4, speed: 5.75, rows: 45, traffic: 0.6, trucks: 0.3, coins: 0.45 },
    { name: "Highway home", lanes: 4, speed: 6.5, rows: 50, traffic: 0.7, trucks: 0.3, coins: 0.5 },
  ];
  var FIELDS = { speed: [2.5, 7.5], rows: [15, 80], traffic: [0.2, 0.9], trucks: [0, 0.5], coins: [0, 0.8] };
  var STAGE_BANNER = 120;          // updates "STAGE CLEAR" and the next stage's name show
  var STAGE_GAP = 90;              // updates of empty road before a new stage's first row

  /** The stages to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (c) {
      if (!c || typeof c.name !== "string" || (c.lanes !== 3 && c.lanes !== 4) || c.rows !== Math.floor(c.rows)) return false;
      for (var k in FIELDS) if (typeof c[k] !== "number" || !(c[k] >= FIELDS[k][0] && c[k] <= FIELDS[k][1])) return false;
      return true;
    });
    return out.length ? out : LEVELS;
  }
  function stage(s) { return s.stages ? s.stages[Math.min(s.level, s.stages.length) - 1] : null; }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function laneW(s) { return (ROAD_R - ROAD_L) / s.lanes; }
  function laneX(s, lane) { return ROAD_L + laneW(s) * (lane + 0.5); }
  function carW(s) { return s.lanes === 3 ? 28 : 26; }
  function speed(s) {
    var st = stage(s);
    if (st) return st.speed;
    return Math.min(MAX_SPEED, MODES[s.mode].speed + SPEED_GAIN * (s.level - 1));
  }
  /** Road between rows of traffic at a speed: room for a lane change and a car length or two. */
  function rowGap(v) { return TRUCK_H + 30 + CHANGE * v * 1.6; }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "three", m = MODES[mode];
    var s = {
      mode: mode, lanes: m.lanes, rng: (o.seed >>> 0) || 1,
      lane: Math.floor(m.lanes / 2), x: 0, dist: 0, bonus: 0, score: 0, level: 1,
      lives: m.lives, safe: 0, cars: [], coins: [], freeLanes: null, nextRow: SPAWN_AHEAD * 0.6, safeLane: Math.floor(m.lanes / 2),
      over: false, updates: 0, crashAt: null,
      stats: { rows: 0, coins: 0, crashes: 0, distance: 0, stages: 0 },
      stages: null, stageRows: 0, stagePassed: 0, finishD: null, banner: 0, won: false,
    };
    if (mode === "stages") {
      s.stages = usableLevels(o.levels);
      s.level = startAt(o.startLevel, s.stages.length);
      s.lanes = stage(s).lanes; s.lane = Math.floor(s.lanes / 2); s.safeLane = s.lane;
      s.banner = STAGE_BANNER;
    }
    s.x = laneX(s, s.lane);
    return s;
  }

  // A row of traffic `at` px up the road. One lane next to (or the same as) the last row's open lane
  // stays open, and every lane that was open in the last row keeps an open lane within one change,
  // so whichever way through you took, there's a way on. One to (lanes − 1) lanes get a car or a truck.
  function spawnRow(s, at) {
    var move = Math.floor(rand(s) * 3) - 1;
    var open = clamp(s.safeLane + move, 0, s.lanes - 1);
    var prevFree = s.freeLanes || [];
    var others = [];
    for (var l = 0; l < s.lanes; l++) if (l !== open) others.push(l);
    for (var i = others.length - 1; i > 0; i--) { var j = Math.floor(rand(s) * (i + 1)), t = others[i]; others[i] = others[j]; others[j] = t; }
    var st = stage(s), n, blocked = [];
    if (st) { n = 1; for (i = 1; i < others.length; i++) if (rand(s) < st.traffic) n++; }
    else n = 1 + Math.floor(rand(s) * others.length);
    function reachable(b) {
      for (var p = 0; p < prevFree.length; p++) {
        var ok = false;
        for (var d = -1; d <= 1; d++) { var q = prevFree[p] + d; if (q >= 0 && q < s.lanes && b.indexOf(q) < 0) ok = true; }
        if (!ok) return false;
      }
      return true;
    }
    for (i = 0; i < others.length && blocked.length < n; i++) {
      if (reachable(blocked.concat([others[i]]))) blocked.push(others[i]);
    }
    if (!blocked.length) blocked.push(others[0]);   // (can't happen with 3+ lanes; a row always has traffic)
    for (i = 0; i < blocked.length; i++) {
      var truck = rand(s) < (st ? st.trucks : TRUCKS);
      var car = { lane: blocked[i], d: at, h: truck ? TRUCK_H : CAR_H, truck: truck, ci: 1 + Math.floor(rand(s) * 6), passed: false };
      if (st) car.row = s.stageRows + 1;
      s.cars.push(car);
    }
    s.freeLanes = [];
    for (l = 0; l < s.lanes; l++) if (blocked.indexOf(l) < 0) s.freeLanes.push(l);
    // sometimes a coin in the gap before the next row
    if (rand(s) < (st ? st.coins : COINS)) s.coins.push({ lane: Math.floor(rand(s) * s.lanes), d: at + rowGap(speed(s)) / 2 });
    s.safeLane = open;
    if (st && ++s.stageRows >= st.rows) s.finishD = at + rowGap(speed(s));   // the last row: then the finish line
  }

  /** Where something `d` px along the road is on screen (y of its middle). */
  function screenY(s, d) { return CAR_Y - (d - s.dist); }

  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    var v = speed(s);
    s.dist += v;
    s.stats.distance = Math.floor(s.dist);

    // steering toward the chosen lane
    var tx = laneX(s, s.lane), dx = laneW(s) / CHANGE;
    s.x = Math.abs(tx - s.x) <= dx ? tx : s.x + (tx > s.x ? dx : -dx);

    // new rows ahead (a stage has a set number, then its finish line)
    while (s.finishD == null && s.nextRow <= s.dist + SPAWN_AHEAD) { spawnRow(s, s.nextRow); s.nextRow += rowGap(v); }

    if (s.stages) {
      if (s.banner > 0) s.banner--;
      if (s.finishD != null && s.dist >= s.finishD) {        // over the finish line
        s.stats.stages++;
        if (s.level >= s.stages.length) {
          s.won = true; s.over = true; s.stats.cause = "won";
          s.score = Math.floor(s.dist / 10) + s.bonus;
          evs.push({ type: "win", score: s.score });
          return evs;
        }
        nextStage(s, evs);
      }
    } else {
      var lv = Math.min(MAX_LEVEL, 1 + Math.floor(s.dist / LEVEL_DIST));
      if (lv > s.level) { s.level = lv; evs.push({ type: "level", level: lv }); }
    }

    if (s.safe > 0) s.safe--;
    var cw = carW(s), hit = null, i;
    for (i = s.cars.length - 1; i >= 0; i--) {
      var c = s.cars[i], cy = screenY(s, c.d);
      if (cy - c.h / 2 > H + 10) { s.cars.splice(i, 1); continue; }
      // the boxes are a little smaller than the drawings, so a near miss is a miss
      if (!hit && s.safe === 0 && Math.abs(cy - CAR_Y) < (c.h + CAR_H) / 2 - 4 && Math.abs(laneX(s, c.lane) - s.x) < cw - 6) hit = c;
      if (!c.passed && cy - c.h / 2 > CAR_Y + CAR_H / 2) {
        c.passed = true; s.stats.rows++;
        if (c.row > s.stagePassed) s.stagePassed = c.row;
      }
    }
    for (i = s.coins.length - 1; i >= 0; i--) {
      var co = s.coins[i], ky = screenY(s, co.d);
      if (ky > H + 10) { s.coins.splice(i, 1); continue; }
      if (Math.abs(ky - CAR_Y) < CAR_H / 2 + COIN_R - 2 && Math.abs(laneX(s, co.lane) - s.x) < cw / 2 + COIN_R - 2) {
        s.coins.splice(i, 1); s.bonus += COIN_POINTS; s.stats.coins++;
        evs.push({ type: "coin" });
      }
    }
    if (hit) {
      s.lives--; s.stats.crashes++; s.crashAt = s.updates;
      evs.push({ type: "crash", lives: s.lives });
      if (s.lives <= 0) { s.over = true; s.stats.cause = "crash"; evs.push({ type: "gameover", score: s.score }); }
      else {
        s.safe = SAFE_UPDATES;
        // clear the traffic around the crash so the car can carry on
        s.cars = s.cars.filter(function (c) { return Math.abs(screenY(s, c.d) - CAR_Y) > 90; });
      }
    }
    s.score = Math.floor(s.dist / 10) + s.bonus;
    return evs;
  }

  /** The next stage: its lanes (the car glides to the nearest one), speed and traffic after a stretch of
      empty road. Everything of the last stage is already behind, off the screen. */
  function nextStage(s, evs) {
    s.level++;
    var st = stage(s);
    s.cars = []; s.coins = [];
    if (st.lanes !== s.lanes) {
      s.lanes = st.lanes;
      s.lane = clamp(Math.round((s.x - ROAD_L) / laneW(s) - 0.5), 0, s.lanes - 1);
    }
    s.safeLane = s.lane; s.freeLanes = null;
    s.stageRows = 0; s.stagePassed = 0; s.finishD = null;
    s.nextRow = s.dist + SPAWN_AHEAD + STAGE_GAP * st.speed;
    s.banner = STAGE_BANNER;
    evs.push({ type: "level", level: s.level });
  }

  function press(s, action, down) {
    var evs = [];
    if (!down || s.over) return evs;
    if (action === "left" && s.lane > 0) { s.lane--; evs.push({ type: "turn" }); }
    if (action === "right" && s.lane < s.lanes - 1) { s.lane++; evs.push({ type: "turn" }); }
    return evs;
  }

  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "stages") out[k] = s[k];
    return JSON.parse(JSON.stringify(out));
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.cars) && Array.isArray(data.coins) &&
      typeof data.dist === "number" && typeof data.score === "number" && data.level >= 1 && data.lives >= 1 &&
      typeof data.rng === "number" && data.stats;
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    if (s.stats.stages === undefined) s.stats.stages = 0;      // saves from before Stages
    if (s.stageRows === undefined) { s.stageRows = 0; s.stagePassed = 0; s.finishD = null; s.banner = 0; }
    s.won = !!s.won;
    s.stages = s.mode === "stages" ? usableLevels(levels) : null;
    if (s.stages && (s.level > s.stages.length || stage(s).lanes !== s.lanes)) throw new Error("That saved game can't be continued.");
    return s;
  }

  function result(s) {
    return {
      score: s.score, level: s.level,
      stats: { distance: s.stats.distance, rows: s.stats.rows, coins: s.stats.coins, crashes: s.stats.crashes,
        livesLeft: s.lives, stages: s.stats.stages || 0, won: !!s.won, cause: s.stats.cause || null, mode: s.mode },
    };
  }

  var RacerLogic = {
    W: W, H: H, ROAD_L: ROAD_L, ROAD_R: ROAD_R, CAR_Y: CAR_Y, CAR_H: CAR_H, TRUCK_H: TRUCK_H, CHANGE: CHANGE,
    LEVEL_DIST: LEVEL_DIST, MAX_LEVEL: MAX_LEVEL, MAX_SPEED: MAX_SPEED, COIN_POINTS: COIN_POINTS, COIN_R: COIN_R, SAFE_UPDATES: SAFE_UPDATES,
    MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels, stage: stage,
    STAGE_BANNER: STAGE_BANNER, SPAWN_AHEAD: SPAWN_AHEAD,
    create: create, step: step, press: press, spawnRow: spawnRow, laneX: laneX, laneW: laneW, carW: carW, speed: speed, rowGap: rowGap,
    screenY: screenY, save: save, restore: restore, result: result, rand: rand,
  };
  if (typeof module === "object" && module.exports) module.exports = RacerLogic; else self.RacerLogic = RacerLogic;
})();
