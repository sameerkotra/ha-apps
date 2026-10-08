/* Household Arcade — Tank Battle rules (pure: no DOM, deterministic for a given seed).

   Seen from above: a 13 × 13 arena of 18 px squares. Your tank starts at the bottom next to the home flag;
   enemy tanks drive in from three entries at the top, one at a time (at most one every 1.5 s, at most four on
   the arena at once). Tanks drive on a half-square grid in four directions and each has one shell in the air at
   a time. Brick walls break a half-square strip at a time, steel walls don't break, water stops tanks but not
   shells, bushes hide tanks. A hit costs a life; the game ends with no lives left or when a shell hits the flag.
   Destroying every enemy of an arena clears it and the next arena of the list starts; clearing the last one wins.
   Every mode plays the arena list (the arenas are the levels). One call to step() is one update (60 a second).

   Two tanks (SPEC §13.4, two phones, live): mode `together` — both players defend one flag against the enemies
   (one and a half times as many), each with their own lives and score (a tank 100 to whoever hit it, an arena
   500 to both); friendly shells stop harmlessly on the other tank. Mode `against` — the two tanks in an arena
   of their own (the same turned half-way round, VERSUS_LEVELS), a flag each and no enemies: hitting the other
   tank 100, a round (their flag, or all their lives) 300, the match (first to 2 rounds) 500; a round lasts at
   most 2 minutes (then it is drawn) and a match at most 5 rounds. Player 1 is seat 1 (its tank starts at the
   bottom), player 2 seat 2. Single-player Tank Battle is unchanged (the same random numbers in the same order). */
(function () {
  "use strict";

  var SIZE = 13, TILE = 18, CELL = 9, N = 26, ARENA = SIZE * TILE, MAXP = ARENA - TILE;
  var OX = 3, OY = 42;                         // where the arena is drawn (logical px)
  var EMPTY = 0, BRICK = 1, STEEL = 2, WATER = 3, BUSH = 4;
  var CHARS = { ".": EMPTY, b: BRICK, s: STEEL, w: WATER, g: BUSH };
  var DX = [0, 1, 0, -1], DY = [-1, 0, 1, 0];   // up, right, down, left
  var PLAYER_START = { x: 4 * TILE, y: 12 * TILE };
  var FLAG = { x: 6 * TILE, y: 12 * TILE };
  var ENTRIES = [{ x: 0, y: 0 }, { x: 6 * TILE, y: 0 }, { x: 12 * TILE, y: 0 }];
  var FLAG_AROUND = [[11, 5], [11, 6], [11, 7], [12, 5], [12, 7]];

  var PLAYER_SPEED = 1.5, PLAYER_SHELL = 4.5, ENEMY_SHELL = 2.5;
  var FIRE_BUFFER = 12;        // a Fire press while your shell is still flying fires as soon as it's gone (≤ 12 updates)
  var CORNER_SLIDE = 5;        // turning into a gap you're up to 5 px off lines you up with it instead of stopping
  var SPAWN_GAP = 90;          // updates between two enemies coming in (the honest-score limit rests on this)
  var MAX_ON_FIELD = 4, ARRIVE = 40, INTRO = 90, CLEAR_PAUSE = 120, RESPAWN = 60, SHIELD = 150;
  var HUNT_AFTER = 1800;       // after 30 s in an arena the enemies head for the flag
  var KILL_POINTS = 100, CLEAR_POINTS = 500;
  var MODES = {
    classic: { lives: 3, speed: 1, fire: 1, shell: 1 },
    easy: { lives: 5, speed: 0.75, fire: 0.6, shell: 0.8 },
    together: { lives: 3, speed: 1, fire: 1, shell: 1, two: "together" },
    against: { lives: 3, speed: 1, fire: 1, shell: 1, two: "against" },
  };
  // Two tanks: where player 2 starts (together: right of the flag; against: the top, the other way round)
  var P2_START = { x: 8 * TILE, y: 12 * TILE }, P2_TOP = { x: 8 * TILE, y: 0 };
  var FLAG2 = { x: 6 * TILE, y: 0 };
  var V_HIT = 100, V_ROUND = 300, V_MATCH = 500, V_WIN_ROUNDS = 2, V_MAX_ROUNDS = 5, V_ROUND_LIMIT = 120 * 60;
  var TOGETHER_ENEMIES = 1.5;

  // The arenas (SPEC §11.9): the built-in list; the session's list (opts.levels) replaces it. An arena is
  // { name, map: [13 strings of 13 of ". b s w g"], enemies, speed, fire }.
  var LEVELS = [
    { name: "Open field", enemies: 5, speed: 0.5, fire: 10, map: [
      ".............", ".............", "..b..b.b..b..", "..b..b.b..b..", "..b.......b..", ".............",
      "....bb.bb....", ".............", "..b..s.s..b..", "..b.......b..", ".............", ".....bbb.....",
      ".....b.b....."] },
    { name: "Brick town", enemies: 6, speed: 0.55, fire: 12, map: [
      ".............", ".bb.bb.bb.bb.", ".bb.bb.bb.bb.", ".............", "bb.bbb.bbb.bb", ".............",
      ".b.b.b.b.b.b.", ".b.b.b.b.b.b.", ".............", "bbb.bb.bb.bbb", ".............", ".b...bbb...b.",
      ".b...b.b...b."] },
    { name: "River crossing", enemies: 8, speed: 0.6, fire: 14, map: [
      ".............", "..b.......b..", "..b..bbb..b..", ".............", "ww.wwwwwww.ww", "ww.wwwwwww.ww",
      ".............", "..s..bbb..s..", "..b.......b..", "..b..g.g..b..", ".....g.g.....", ".....bbb.....",
      "..g..b.b..g.."] },
    { name: "Leafy hideout", enemies: 9, speed: 0.65, fire: 16, map: [
      ".............", ".ggg.....ggg.", ".ggg.bbb.ggg.", ".....bsb.....", "bb.........bb", "..ggg...ggg..",
      "..ggg.s.ggg..", "..ggg...ggg..", "bb....b....bb", "...bb...bb...", ".gg.......gg.", ".gg..bbb..gg.",
      ".....b.b....."] },
    { name: "Steel garden", enemies: 10, speed: 0.7, fire: 18, map: [
      ".............", ".s.s.....s.s.", ".............", "..bbsbbbsbb..", ".............", "s.b.s...s.b.s",
      "..b.......b..", "..b.ss.ss.b..", ".............", ".sbb.....bbs.", ".............", "..s..bbb..s..",
      ".....b.b....."] },
    { name: "Lake fort", enemies: 12, speed: 0.75, fire: 20, map: [
      ".............", ".....bbb.....", ".ww.......ww.", ".ww.bb.bb.ww.", ".....b.b.....", "b.ss.....ss.b",
      "b.....g.....b", "..ww.ggg.ww..", "..ww..g..ww..", ".............", ".bb.s...s.bb.", ".....bbb.....",
      "..b..b.b..b.."] },
    { name: "Winding road", enemies: 14, speed: 0.8, fire: 22, map: [
      ".............", "bbbbb.b.bbbbb", ".............", ".bbbbbbbbbbb.", ".s.........s.", ".s.bbbbbbb.s.",
      "...b.....b...", "ggg.b.s.b.ggg", "ggg.......ggg", ".bbbbb.bbbbb.", ".............", "..s..bbb..s..",
      ".....b.b....."] },
    { name: "Castle gates", enemies: 16, speed: 0.85, fire: 24, map: [
      ".............", ".sss.....sss.", ".s.........s.", ".s.bbbbbbb.s.", "...b.....b...", "ww.b.ggg.b.ww",
      "ww...ggg...ww", "...b.ggg.b...", ".s.bbb.bbb.s.", ".s.........s.", ".sss.....sss.", "....bbbbb....",
      "..g..b.b..g.."] },
    { name: "Last stand", enemies: 20, speed: 0.95, fire: 28, map: [
      ".............", ".b.b.b.b.b.b.", ".b.b.bsb.b.b.", ".............", "ss.bbb.bbb.ss", "...g.....g...",
      ".w.ggg.ggg.w.", ".w...s.s...w.", ".....s.s.....", "bb.b.....b.bb", ".....bbb.....", ".s..bbbbb..s.",
      "..b..b.b..b.."] },
  ];

  /** An arena for two tanks against each other: its top seven rows; the bottom six are the top ones turned
      half-way round (so both sides are the same) and the middle row reads the same both ways. */
  function mirrored(name, top) {
    var rows = top.slice();
    for (var r = 5; r >= 0; r--) rows.push(top[r].split("").reverse().join(""));
    return { name: name, map: rows };
  }
  var VERSUS_LEVELS = [
    mirrored("Face off", [".....b.b.....", ".....bbb.....", "..s.......s..", ".bb..b.b..bb.", "......w......",
      "bb..s...s..bb", "..g.bb.bb.g.."]),
    mirrored("Bush maze", [".....b.b.....", "..g..bbb..g..", ".ggg.....ggg.", "...b.s.s.b...", "w.b.......b.w",
      "..b..bbb..b..", ".s....g....s."]),
    mirrored("The river", [".....b.b.....", ".s...bbb...s.", ".............", "bb.bb...bb.bb", ".............",
      "...s.....s...", "ww.wwwwwww.ww"]),
    mirrored("Brick yard", [".....b.b.....", ".b.b.bbb.b.b.", ".b.b.....b.b.", ".....s.s.....", "bbb.bbbbb.bbb",
      ".............", ".b.bb.s.bb.b."]),
  ];

  function isInt(v, lo, hi) { return typeof v === "number" && v === Math.floor(v) && v >= lo && v <= hi; }
  function isNum(v, lo, hi) { return typeof v === "number" && v >= lo && v <= hi; }

  /** Is this arena's map usable: the right shape, the fixed squares free, every entry reachable from the start? */
  function mapOk(m) {
    if (!Array.isArray(m) || m.length !== SIZE) return false;
    for (var r = 0; r < SIZE; r++) if (typeof m[r] !== "string" || !/^[.bswg]{13}$/.test(m[r])) return false;
    var fixed = [[12, 4], [12, 6], [0, 0], [0, 6], [0, 12]];
    for (var i = 0; i < fixed.length; i++) if (m[fixed[i][0]][fixed[i][1]] !== ".") return false;
    for (i = 0; i < FLAG_AROUND.length; i++) if (".b".indexOf(m[FLAG_AROUND[i][0]][FLAG_AROUND[i][1]]) < 0) return false;
    var seen = {}, todo = [[12, 4]];
    seen["12,4"] = true;
    while (todo.length) {
      var p = todo.pop();
      for (var d = 0; d < 4; d++) {
        var nr = p[0] + DY[d], nc = p[1] + DX[d];
        if (nr < 0 || nc < 0 || nr >= SIZE || nc >= SIZE || seen[nr + "," + nc] || (nr === 12 && nc === 6)) continue;
        if ("sw".indexOf(m[nr][nc]) >= 0) continue;
        seen[nr + "," + nc] = true; todo.push([nr, nc]);
      }
    }
    return !!(seen["0,0"] && seen["0,6"] && seen["0,12"]);
  }
  /** The arenas to play: the usable ones from `levels`, else the built-in list. */
  function usableLevels(levels) {
    if (!Array.isArray(levels)) return LEVELS;
    var out = levels.filter(function (a) {
      return a && typeof a === "object" && typeof a.name === "string" && a.name.length > 0 && isInt(a.enemies, 4, 20) &&
        isNum(a.speed, 0.4, 1.2) && isInt(a.fire, 6, 40) && mapOk(a.map);
    });
    return out.length ? out : LEVELS;
  }

  function rand(s) { // mulberry32
    s.rng = (s.rng + 0x6D2B79F5) | 0;
    var t = Math.imul(s.rng ^ (s.rng >>> 15), 1 | s.rng);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  function arena(s) { return s.arenas[Math.min(s.level, s.arenas.length) - 1]; }
  function enemySpeed(s) { return s.arenaSpeed * MODES[s.mode].speed; }
  function fireChance(s) { return s.arenaFire * MODES[s.mode].fire / 3600; }

  function loadArena(s) {
    if (s.two === "against") { loadVersus(s); return; }
    var a = arena(s), m = [];
    for (var cy = 0; cy < N; cy++) for (var cx = 0; cx < N; cx++) m.push(CHARS[a.map[cy >> 1][cx >> 1]]);
    s.map = m; s.mapVer++;
    s.arenaSpeed = a.speed; s.arenaFire = a.fire; s.name = a.name;
    s.total = a.enemies; s.spawned = 0; s.enemies = []; s.shells = []; s.booms = [];
    s.arenaT = 0; s.intro = INTRO; s.clear = 0; s.entryNext = 1;
    placePlayer(s);
    if (s.two === "together") {
      clearTile(s, 12, 8);                       // player 2's start
      s.total = Math.ceil(a.enemies * TOGETHER_ENEMIES);
      if (s.lives2 > 0 || !s.p2) placeP2(s); else s.p2.dead = 1e9;
      if (s.lives <= 0) s.player.dead = 1e9;     // a player out of lives stays out in the next arena
    }
  }
  function clearTile(s, row, col) {
    for (var cy = row * 2; cy < row * 2 + 2; cy++) for (var cx = col * 2; cx < col * 2 + 2; cx++) s.map[cy * N + cx] = EMPTY;
  }
  /** A round of two tanks against each other: the round's arena, both tanks at their starts, full lives. */
  function loadVersus(s) {
    var a = VERSUS_LEVELS[(s.vStart + s.level - 1) % VERSUS_LEVELS.length], m = [];
    for (var cy = 0; cy < N; cy++) for (var cx = 0; cx < N; cx++) m.push(CHARS[a.map[cy >> 1][cx >> 1]]);
    s.map = m; s.mapVer++;
    s.arenaSpeed = 0.5; s.arenaFire = 10; s.name = a.name;
    s.total = 0; s.spawned = 0; s.enemies = []; s.shells = []; s.booms = [];
    s.arenaT = 0; s.intro = INTRO; s.clear = 0; s.entryNext = 1;
    s.lives = MODES[s.mode].lives; s.lives2 = MODES[s.mode].lives;
    s.flagDown = false; s.flag2Down = false; s.roundWinner = null;
    placePlayer(s); placeP2(s);
  }
  function placePlayer(s) {
    s.player = { x: PLAYER_START.x, y: PLAYER_START.y, dir: 0, acc: 0, dead: 0, shield: SHIELD };
  }
  function placeP2(s) {
    var at = s.two === "against" ? P2_TOP : P2_START;
    s.p2 = { x: at.x, y: at.y, dir: s.two === "against" ? 2 : 0, acc: 0, dead: 0, shield: SHIELD };
  }

  // SPEC §14: a game that carries on from the next level starts at opts.startLevel (1 to the list's length).
  function startAt(n, count) { n = Math.floor(Number(n) || 1); return n < 1 ? 1 : n > count ? Math.max(1, count) : n; }

  function create(o) {
    o = o || {};
    var mode = MODES[o.mode] ? o.mode : "classic";
    var s = {
      mode: mode, rng: (o.seed >>> 0) || 1, level: 1, score: 0, lives: MODES[mode].lives, over: false, won: false,
      updates: 0, arenaT: 0, intro: 0, clear: 0, sinceSpawn: SPAWN_GAP, nextId: 1, mapVer: 0, flagDown: false,
      map: null, player: null, enemies: [], shells: [], booms: [], total: 0, spawned: 0, entryNext: 1,
      arenaSpeed: 0.5, arenaFire: 10, name: "",
      held: [], fireHeld: false, fireReq: 0, touchDir: -1, tapDir: -1,
      arenas: usableLevels(o.levels),
      stats: { kills: 0, shots: 0, arenas: 0, livesLost: 0, bricks: 0, cause: null },
    };
    if (MODES[mode].two) {
      s.two = MODES[mode].two; s.p2 = null; s.lives2 = MODES[mode].lives; s.score2 = 0;
      s.ctl2 = { held: [], fireHeld: false, fireReq: 0, touchDir: -1, tapDir: -1 };
      s.stats2 = { kills: 0, shots: 0, livesLost: 0 };
      if (s.two === "against") {
        s.arenas = VERSUS_LEVELS;
        s.vStart = Math.floor(rand(s) * VERSUS_LEVELS.length);    // the first arena comes from the seed
        s.wins = [0, 0]; s.matchWinner = null; s.draws = 0; s.flag2Down = false; s.roundWinner = null;
      }
    }
    if (!s.two) s.level = startAt(o.startLevel, s.arenas.length);
    loadArena(s);
    return s;
  }

  // ---------- the players: one tank, or two ----------
  /** The players' tanks: [player 1] or [player 1, player 2]. */
  function humans(s) { return s.two ? [s.player, s.p2] : [s.player]; }
  /** Where player i's controls are kept (player 1's on the state itself, as always). */
  function ctlOf(s, i) { return i === 1 ? s.ctl2 : s; }
  /** The owner of player i's shells (enemies are 1, 2, 3 …). */
  function ownerOf(i) { return i === 1 ? -1 : 0; }
  function humanOf(owner) { return owner === 0 ? 0 : owner === -1 ? 1 : -1; }
  /** Which side a shell is on, for shells meeting head on. */
  function side(s, owner) { return owner > 0 ? "e" : s.two === "against" ? "p" + owner : "h"; }

  // ---------- the map and tanks ----------
  function cellAt(s, cx, cy) { return s.map[cy * N + cx]; }
  /** Does a tank at (x, y) sit on a wall or water? */
  function blocked(s, x, y) {
    var x0 = Math.floor(x / CELL), x1 = Math.floor((x + TILE - 1) / CELL);
    var y0 = Math.floor(y / CELL), y1 = Math.floor((y + TILE - 1) / CELL);
    for (var cy = y0; cy <= y1; cy++) for (var cx = x0; cx <= x1; cx++) {
      var c = cellAt(s, cx, cy);
      if (c === BRICK || c === STEEL || c === WATER) return true;
    }
    return false;
  }
  function overlaps(ax, ay, bx, by) { return Math.abs(ax - bx) < TILE && Math.abs(ay - by) < TILE; }
  function tanks(s) {
    var out = s.player && !s.player.dead ? [s.player] : [];
    if (s.two && s.p2 && !s.p2.dead) out.push(s.p2);
    return out.concat(s.enemies);
  }
  /** Can `tank` move to (nx, ny)? Tanks that already overlap (a respawn) may drive apart. */
  function canPlace(s, tank, nx, ny) {
    if (nx < 0 || ny < 0 || nx > MAXP || ny > MAXP) return false;
    if (blocked(s, nx, ny) || overlaps(nx, ny, FLAG.x, FLAG.y)) return false;
    if (s.two === "against" && overlaps(nx, ny, FLAG2.x, FLAG2.y)) return false;
    var all = tanks(s);
    for (var i = 0; i < all.length; i++) {
      var t = all[i];
      if (t === tank) continue;
      if (overlaps(nx, ny, t.x, t.y) && !overlaps(tank.x, tank.y, t.x, t.y)) return false;
    }
    return true;
  }
  /** Face `dir`; turning onto the other axis lines the tank up with the half-square grid. */
  function face(tank, dir) {
    if ((dir & 1) !== (tank.dir & 1)) {
      if (dir & 1) tank.y = Math.round(tank.y / CELL) * CELL; else tank.x = Math.round(tank.x / CELL) * CELL;
      tank.acc = 0;
    }
    tank.dir = dir;
  }
  /** Drive forward; false when something was in the way. With `slide`, a tank blocked by a corner it is only a
      few px off slides sideways toward the gap (the player's tank: turning into corridors feels instant). */
  function drive(s, tank, speed, slide) {
    tank.acc += speed;
    var n = Math.floor(tank.acc);
    tank.acc -= n;
    for (var i = 0; i < n; i++) {
      var nx = tank.x + DX[tank.dir], ny = tank.y + DY[tank.dir];
      if (!canPlace(s, tank, nx, ny)) {
        if (slide && nudge(s, tank)) continue;
        tank.acc = 0; return false;
      }
      tank.x = nx; tank.y = ny;
    }
    return true;
  }
  /** One px sideways toward the nearest position (within CORNER_SLIDE) from which the tank can go forward. */
  function nudge(s, tank) {
    var side = tank.dir & 1 ? [0, 1] : [1, 0];      // moving across → slide up/down; moving up/down → slide sideways
    for (var d = 1; d <= CORNER_SLIDE; d++) {
      for (var sgn = -1; sgn <= 1; sgn += 2) {
        var ox = side[0] * d * sgn, oy = side[1] * d * sgn;
        if (canPlace(s, tank, tank.x + ox, tank.y + oy) &&
            canPlace(s, tank, tank.x + ox + DX[tank.dir], tank.y + oy + DY[tank.dir])) {
          var sx = side[0] * sgn, sy = side[1] * sgn;
          if (!canPlace(s, tank, tank.x + sx, tank.y + sy)) continue;
          tank.x += sx; tank.y += sy;
          return true;
        }
      }
    }
    return false;
  }
  function fire(s, tank, owner, speed) {
    s.shells.push({ x: tank.x + 9 + DX[tank.dir] * 9, y: tank.y + 9 + DY[tank.dir] * 9, dir: tank.dir, v: speed, owner: owner });
  }
  function hasShell(s, owner) {
    for (var i = 0; i < s.shells.length; i++) if (s.shells[i].owner === owner && !s.shells[i].dead) return true;
    return false;
  }

  // ---------- one update ----------
  function step(s) {
    var evs = [];
    if (s.over) return evs;
    s.updates++;
    s.sinceSpawn++;
    for (var b = s.booms.length - 1; b >= 0; b--) if (++s.booms[b].t > 30) s.booms.splice(b, 1);
    if (s.clear > 0) {                       // between arenas
      if (--s.clear === 0) { s.level++; loadArena(s); evs.push({ type: "level", level: s.level }); }
      return evs;
    }
    if (s.intro > 0) {
      s.intro--; s.fireReq = 0; s.tapDir = -1;
      if (s.two) { s.ctl2.fireReq = 0; s.ctl2.tapDir = -1; }
      return evs;
    }
    s.arenaT++;
    if (s.two === "against") return stepVersus(s, evs);
    spawn(s, evs);
    movePlayer(s, evs);
    if (s.two) movePlayer2(s, evs);
    moveEnemies(s);
    moveShells(s, evs);
    if (!s.over && s.spawned >= s.total && !s.enemies.length) {
      s.score += CLEAR_POINTS; s.stats.arenas++;
      if (s.two) s.score2 += CLEAR_POINTS;
      evs.push({ type: "clear", level: s.level, score: s.score });
      if (s.level >= s.arenas.length) {
        s.won = true; s.over = true; s.stats.cause = "won";
        evs.push({ type: "win", score: s.score });
      } else s.clear = CLEAR_PAUSE;
    }
    return evs;
  }

  function spawn(s, evs) {
    if (s.spawned >= s.total || s.enemies.length >= MAX_ON_FIELD || s.sinceSpawn < SPAWN_GAP) return;
    var all = tanks(s);
    for (var k = 0; k < 3; k++) {
      var e = ENTRIES[(s.entryNext + k) % 3], free = true;
      for (var i = 0; i < all.length; i++) if (overlaps(e.x, e.y, all[i].x, all[i].y)) free = false;
      if (!free) continue;
      s.enemies.push({ id: s.nextId++, x: e.x, y: e.y, dir: 2, acc: 0, arrive: ARRIVE, turn: 20, stuck: false });
      s.spawned++; s.sinceSpawn = 0; s.entryNext = (s.entryNext + k + 1) % 3;
      evs.push({ type: "spawn", left: s.total - s.spawned });
      return;
    }
  }

  function movePlayer(s, evs) { moveHuman(s, 0, evs); }
  function movePlayer2(s, evs) { moveHuman(s, 1, evs); }
  function moveHuman(s, i, evs) {
    var p = i === 1 ? s.p2 : s.player, c = ctlOf(s, i), owner = ownerOf(i);
    if (p.dead > 0) {
      c.fireReq = 0; c.tapDir = -1;
      if (--p.dead === 0) { if (i === 1) placeP2(s); else placePlayer(s); }
      return;
    }
    if (p.shield > 0) p.shield--;
    // the direction held now, else a direction tapped since the last update (a quick tap still turns and moves)
    var dir = c.held.length ? c.held[c.held.length - 1] : c.touchDir >= 0 ? c.touchDir : c.tapDir;
    if (dir >= 0) { face(p, dir); drive(s, p, PLAYER_SPEED, true); }
    c.tapDir = -1;
    if ((c.fireReq > 0 || c.fireHeld) && !hasShell(s, owner)) {
      fire(s, p, owner, PLAYER_SHELL);
      if (i === 1) s.stats2.shots++; else s.stats.shots++;
      evs.push({ type: "fire", player: i + 1 });
      c.fireReq = 0;
    } else if (c.fireReq > 0) c.fireReq--;
  }
  /** The enemies' target: the nearer player still playing (player 1 when only one tank plays). */
  function prey(s, e) {
    if (!s.two) return s.player;
    var a = s.player, b = s.p2;
    if (a.dead && !b.dead) return b;
    if (b.dead || a.dead) return a;
    return Math.abs(b.x - e.x) + Math.abs(b.y - e.y) < Math.abs(a.x - e.x) + Math.abs(a.y - e.y) ? b : a;
  }

  function chooseDir(s, e) {
    var hunt = s.arenaT > HUNT_AFTER, p = prey(s, e);
    var toward = rand(s) < (hunt ? 0.8 : 0.5);
    if (toward) {
      var t = (hunt || p.dead || rand(s) < 0.25) ? FLAG : p;
      var dx = t.x - e.x, dy = t.y - e.y;
      var horiz = Math.abs(dx) > Math.abs(dy);
      if (rand(s) < 0.3) horiz = !horiz;
      if (horiz && dx === 0) horiz = false;
      if (!horiz && dy === 0) horiz = true;
      face(e, horiz ? (dx > 0 ? 1 : 3) : (dy > 0 ? 2 : 0));
    } else face(e, Math.floor(rand(s) * 4));
    e.turn = 30 + Math.floor(rand(s) * 90);
  }
  /** Is the player (or, once they hunt, the flag) straight ahead of this enemy? */
  function inLine(s, e) {
    var targets = s.arenaT > HUNT_AFTER ? [FLAG] : [];
    if (!s.player.dead) targets.push(s.player);
    if (s.two && s.p2 && !s.p2.dead) targets.push(s.p2);
    for (var i = 0; i < targets.length; i++) {
      var t = targets[i];
      if (e.dir & 1) { if (Math.abs(t.y - e.y) < 9 && (t.x - e.x) * DX[e.dir] > 0) return true; }
      else if (Math.abs(t.x - e.x) < 9 && (t.y - e.y) * DY[e.dir] > 0) return true;
    }
    return false;
  }
  function moveEnemies(s) {
    var speed = enemySpeed(s), chance = fireChance(s), shell = ENEMY_SHELL * MODES[s.mode].shell;
    for (var i = 0; i < s.enemies.length; i++) {
      var e = s.enemies[i];
      if (e.arrive > 0) { e.arrive--; continue; }
      if (--e.turn <= 0 || e.stuck) chooseDir(s, e);
      e.stuck = !drive(s, e, speed);
      if (!hasShell(s, e.id)) {
        var c = chance * (inLine(s, e) ? 3 : 1) + (e.stuck ? 0.02 : 0);
        if (rand(s) < c) fire(s, e, e.id, shell);
      }
    }
  }

  function boxHitsTank(x, y, t) { return x + 2 > t.x && x - 2 < t.x + TILE && y + 2 > t.y && y - 2 < t.y + TILE; }

  function moveShells(s, evs) {
    var i, list = s.shells.slice();
    for (i = 0; i < list.length && !s.over; i++) {
      var sh = list[i];
      var n = Math.ceil(sh.v), stepLen = sh.v / n;
      for (var k = 0; k < n && !sh.dead && !s.over; k++) {
        sh.x += DX[sh.dir] * stepLen; sh.y += DY[sh.dir] * stepLen;
        if (shellHits(s, sh, evs)) sh.dead = true;
      }
    }
    // shells meeting head on cancel each other
    for (i = 0; i < s.shells.length; i++) for (var j = i + 1; j < s.shells.length; j++) {
      var a = s.shells[i], b = s.shells[j];
      if (!a.dead && !b.dead && side(s, a.owner) !== side(s, b.owner) && Math.abs(a.x - b.x) < 5 && Math.abs(a.y - b.y) < 5) {
        a.dead = true; b.dead = true;
      }
    }
    s.shells = s.shells.filter(function (sh) { return !sh.dead; });
  }

  /** One small step of a shell: true when it's used up. */
  function shellHits(s, sh, evs) {
    var x = sh.x, y = sh.y;
    if (x < 0 || y < 0 || x >= ARENA || y >= ARENA) return true;
    // walls: the leading edge of the shell
    var lead = sh.dir & 1 ? Math.floor((sh.dir === 1 ? x + 1.99 : x - 2) / CELL) : Math.floor((sh.dir === 2 ? y + 1.99 : y - 2) / CELL);
    var across0 = Math.floor(((sh.dir & 1 ? y : x) - 2) / CELL), across1 = Math.floor(((sh.dir & 1 ? y : x) + 1.99) / CELL);
    var hit = false, c, a;
    for (a = across0; a <= across1; a++) {
      c = sh.dir & 1 ? cellOr(s, lead, a) : cellOr(s, a, lead);
      if (c === BRICK || c === STEEL) hit = true;
    }
    if (hit) {
      // the blast: a strip a square wide, half a square deep
      var mid = sh.dir & 1 ? y : x, broke = 0;
      for (a = Math.floor((mid - 8) / CELL); a <= Math.floor((mid + 7.99) / CELL); a++) {
        var cx = sh.dir & 1 ? lead : a, cy = sh.dir & 1 ? a : lead;
        if (cx >= 0 && cy >= 0 && cx < N && cy < N && s.map[cy * N + cx] === BRICK) { s.map[cy * N + cx] = EMPTY; broke++; }
      }
      if (broke) { s.mapVer++; s.stats.bricks += broke; evs.push({ type: "brick" }); }
      else if (sh.owner <= 0) evs.push({ type: "steel" });
      return true;
    }
    if (s.two) return shellHits2(s, sh, evs, x, y);
    // the flag
    if (boxHitsTank(x, y, FLAG)) {
      s.flagDown = true; s.booms.push({ x: FLAG.x + 9, y: FLAG.y + 9, t: 0, big: true });
      s.stats.flagBy = sh.owner === 0 ? "you" : "enemy";
      end(s, evs, "flag");
      return true;
    }
    // tanks
    if (sh.owner === 0) {
      for (var i = 0; i < s.enemies.length; i++) {
        var e = s.enemies[i];
        if (boxHitsTank(x, y, e)) { killEnemy(s, i, evs); return true; }
      }
    } else {
      var p = s.player;
      if (!p.dead && boxHitsTank(x, y, p)) {
        if (p.shield > 0) return true;
        loseLife(s, evs);
        return true;
      }
    }
    return false;
  }
  /** Two tanks: flags, enemies and the players' tanks. */
  function shellHits2(s, sh, evs, x, y) {
    var shooter = humanOf(sh.owner), i;
    if (boxHitsTank(x, y, FLAG)) {
      s.flagDown = true; s.booms.push({ x: FLAG.x + 9, y: FLAG.y + 9, t: 0, big: true });
      s.stats.flagBy = sh.owner > 0 ? "enemy" : shooter === 0 ? "p1" : "p2";
      if (s.two === "against") roundOver(s, 0, "flag", evs); else end(s, evs, "flag");        // player 1's flag
      return true;
    }
    if (s.two === "against" && boxHitsTank(x, y, FLAG2)) {
      s.flag2Down = true; s.booms.push({ x: FLAG2.x + 9, y: FLAG2.y + 9, t: 0, big: true });
      roundOver(s, 1, "flag", evs);                                                            // player 2's flag
      return true;
    }
    if (shooter >= 0) {
      for (i = 0; i < s.enemies.length; i++) {
        if (boxHitsTank(x, y, s.enemies[i])) { killEnemy(s, i, evs, shooter); return true; }
      }
    }
    var hs = humans(s);
    for (i = 0; i < hs.length; i++) {
      var p = hs[i];
      if (i === shooter || !p || p.dead || !boxHitsTank(x, y, p)) continue;
      if (shooter >= 0 && s.two !== "against") return true;          // a friendly shell stops on the other tank
      if (p.shield > 0) return true;
      if (shooter >= 0) {                                             // against each other: a hit
        if (shooter === 0) s.score += V_HIT; else s.score2 += V_HIT;
        evs.push({ type: "hit", player: shooter + 1, score: s.score });
      }
      loseLifeOf(s, i, evs);
      return true;
    }
    return false;
  }
  function cellOr(s, cx, cy) { return cx < 0 || cy < 0 || cx >= N || cy >= N ? EMPTY : s.map[cy * N + cx]; }

  /** An enemy is destroyed (by your shell; with two tanks, by player `by`'s). */
  function killEnemy(s, i, evs, by) {
    var e = s.enemies[i];
    s.enemies.splice(i, 1);
    for (var k = 0; k < s.shells.length; k++) if (s.shells[k].owner === e.id) s.shells[k].dead = true;
    s.booms.push({ x: e.x + 9, y: e.y + 9, t: 0, big: false });
    if (by === 1) { s.score2 += KILL_POINTS; s.stats2.kills++; } else { s.score += KILL_POINTS; s.stats.kills++; }
    evs.push({ type: "hit", score: s.score, player: (by || 0) + 1, left: s.total - s.stats.kills - (s.two ? s.stats2.kills : 0) });
  }

  function loseLife(s, evs) {
    var p = s.player;
    s.booms.push({ x: p.x + 9, y: p.y + 9, t: 0, big: true });
    s.lives--; s.stats.livesLost++;
    for (var k = 0; k < s.shells.length; k++) if (s.shells[k].owner === 0) s.shells[k].dead = true;
    evs.push({ type: "lifeLost", lives: s.lives });
    if (s.lives <= 0) { p.dead = 1e9; end(s, evs, "lives"); return; }
    p.dead = RESPAWN;
  }

  /** Player i's tank is hit (two tanks): a life, then back at the start with a shield — or out. */
  function loseLifeOf(s, i, evs) {
    if (i === 0 && !s.two) { loseLife(s, evs); return; }
    var p = i === 1 ? s.p2 : s.player;
    s.booms.push({ x: p.x + 9, y: p.y + 9, t: 0, big: true });
    var left;
    if (i === 1) { s.lives2--; s.stats2.livesLost++; left = s.lives2; } else { s.lives--; s.stats.livesLost++; left = s.lives; }
    var owner = ownerOf(i);
    for (var k = 0; k < s.shells.length; k++) if (s.shells[k].owner === owner) s.shells[k].dead = true;
    evs.push({ type: "lifeLost", player: i + 1, lives: left });
    if (left > 0) { p.dead = RESPAWN; return; }
    p.dead = 1e9;
    if (s.two === "against") roundOver(s, i, "lives", evs);
    else if (s.player.dead && s.p2.dead) end(s, evs, "lives");          // together: over when both are out
  }

  // ---------- two tanks against each other ----------
  function stepVersus(s, evs) {
    movePlayer(s, evs);
    movePlayer2(s, evs);
    moveShells(s, evs);
    if (!s.over && s.clear === 0 && s.arenaT >= V_ROUND_LIMIT) roundOver(s, -1, "time", evs);
    return evs;
  }
  /** The round is lost by player `loser` (0 or 1; -1: nobody, time ran out). */
  function roundOver(s, loser, cause, evs) {
    if (s.clear > 0 || s.over) return;
    var w = loser < 0 ? -1 : 1 - loser;
    s.roundWinner = w + 1;
    if (w === 0) s.score += V_ROUND; else if (w === 1) s.score2 += V_ROUND;
    if (w >= 0) s.wins[w]++; else s.draws++;
    evs.push({ type: "round", winner: w + 1, cause: cause, wins: s.wins.slice() });
    if (w >= 0 && s.wins[w] >= V_WIN_ROUNDS) { matchOver(s, w, evs); return; }
    if (s.level >= V_MAX_ROUNDS) { matchOver(s, s.wins[0] > s.wins[1] ? 0 : s.wins[1] > s.wins[0] ? 1 : -1, evs); return; }
    s.clear = CLEAR_PAUSE;
  }
  function matchOver(s, w, evs) {
    s.matchWinner = w + 1;
    if (w === 0) s.score += V_MATCH; else if (w === 1) s.score2 += V_MATCH;
    s.won = w === 0;
    end(s, evs, w < 0 ? "draw" : "match");
  }

  function end(s, evs, cause) {
    if (s.over) return;
    s.over = true; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause });
  }

  // ---------- input ----------
  var DIRS = { up: 0, right: 1, down: 2, left: 3 };
  /** A key or button for player 1 — or, with `player` (0 or 1: two tanks), for that player. `steer` (two phones)
      is a finger on the game: its value is 1 + the direction toward the finger, or 0 when it lifts. */
  function press(s, action, down, player) {
    if (s.over) return [];
    var c = player === 1 && s.two ? s.ctl2 : s;
    if (action in DIRS) {
      var d = DIRS[action], at = c.held.indexOf(d);
      if (at >= 0) c.held.splice(at, 1);
      if (down) { c.held.push(d); c.tapDir = d; }
    } else if (action === "fire") {
      c.fireHeld = !!down;
      if (down) c.fireReq = FIRE_BUFFER;
    } else if (action === "steer") {
      var v = Math.round(Number(down) || 0);
      c.touchDir = v >= 1 && v <= 4 ? v - 1 : -1;
    }
    return [];
  }
  /** A finger on the game (x, y in logical px, or null when it lifts): a touch on your tank fires, elsewhere
      the tank drives toward the finger. */
  function touch(s, x, y, isDown) {
    if (x == null) { s.touchDir = -1; return; }
    var p = s.player, dx = x - OX - (p.x + 9), dy = y - OY - (p.y + 9);
    if (Math.abs(dx) < 14 && Math.abs(dy) < 14) { if (isDown) s.fireReq = FIRE_BUFFER; s.touchDir = -1; return; }
    s.touchDir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 1 : 3) : (dy > 0 ? 2 : 0);
  }
  /** Where a finger at arena point (ax, ay) sends player i's tank: "fire" on the tank, else 1 + a direction
      (the value of a `steer` input; two phones work it out on the phone and send it). */
  function steerFor(s, i, ax, ay) {
    var p = i === 1 ? s.p2 : s.player;
    if (!p) return 0;
    var dx = ax - (p.x + 9), dy = ay - (p.y + 9);
    if (Math.abs(dx) < 14 && Math.abs(dy) < 14) return "fire";
    return 1 + (Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 1 : 3) : (dy > 0 ? 2 : 0));
  }

  // ---------- two phones ----------
  function hash(str) {          // FNV-1a, unsigned 32-bit (the same as ArcadeLockstep.hash)
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  function tankParts(t) { return t ? [t.x, t.y, t.dir, t.acc, t.dead, t.shield] : null; }
  /** What both phones must agree on (SPEC §13.4): the map, every tank and shell, scores, lives and the dice. */
  function checksum(s) {
    var c2 = s.ctl2 || {};
    return hash(JSON.stringify([s.updates, s.rng, s.level, s.score, s.score2, s.lives, s.lives2, s.over, s.intro, s.clear,
      s.arenaT, s.sinceSpawn, s.spawned, s.total, s.nextId, s.entryNext, s.flagDown, s.flag2Down || false, s.wins || null,
      tankParts(s.player), tankParts(s.p2), s.held, s.fireHeld, s.fireReq, s.touchDir, c2.held || null, c2.fireHeld || false,
      c2.fireReq || 0, c2.touchDir == null ? -1 : c2.touchDir,
      s.enemies.map(function (e) { return [e.id, e.x, e.y, e.dir, e.acc, e.arrive, e.turn, e.stuck]; }),
      s.shells.map(function (sh) { return [sh.x, sh.y, sh.dir, sh.v, sh.owner]; }), s.map.join("")]));
  }
  /** Both players' results for the match: each score, and who won (1 or 2; 0 a draw). Together: the higher
      score; against each other: the match. */
  function report(s) {
    var w = s.two === "against" ? (s.matchWinner || 0) : s.score > s.score2 ? 1 : s.score2 > s.score ? 2 : 0;
    return { scores: [s.score, s.score2 || 0], levels: [s.level, s.level], winner: w };
  }

  // ---------- saving ----------
  var STATE_VERSION = 1;
  function save(s) {
    var out = {};
    for (var k in s) if (Object.prototype.hasOwnProperty.call(s, k) && k !== "arenas") out[k] = s[k];
    out = JSON.parse(JSON.stringify(out));
    out.held = []; out.fireHeld = false; out.fireReq = 0; out.touchDir = -1; out.tapDir = -1;
    return out;
  }
  function restore(data, levels) {
    var ok = data && typeof data === "object" && MODES[data.mode] && Array.isArray(data.map) && data.map.length === N * N &&
      data.player && typeof data.player.x === "number" && Array.isArray(data.enemies) && Array.isArray(data.shells) &&
      typeof data.score === "number" && data.level >= 1 && typeof data.lives === "number" && typeof data.rng === "number" &&
      data.stats && typeof data.stats === "object";
    if (!ok) throw new Error("That saved game can't be continued.");
    var s = JSON.parse(JSON.stringify(data));
    s.arenas = usableLevels(levels);
    if (!Array.isArray(s.booms)) s.booms = [];
    s.held = []; s.fireHeld = false; s.fireReq = 0; s.touchDir = -1; s.tapDir = -1;
    return s;
  }

  function result(s, player) {
    if (s.two && player === 1) {
      return { score: s.score2, level: s.level, stats: { kills: s.stats2.kills, shots: s.stats2.shots, arenas: s.stats.arenas,
        bricks: s.stats.bricks, livesLeft: Math.max(0, s.lives2), livesLost: s.stats2.livesLost,
        won: s.two === "against" ? s.matchWinner === 2 : !!s.won, cause: s.stats.cause, flagBy: s.stats.flagBy || null, mode: s.mode } };
    }
    if (s.two === "against") {
      var r = { score: s.score, level: s.level, stats: { kills: 0, shots: s.stats.shots, arenas: 0, bricks: s.stats.bricks,
        livesLeft: Math.max(0, s.lives), livesLost: s.stats.livesLost, won: s.matchWinner === 1, cause: s.stats.cause,
        flagBy: null, mode: s.mode } };
      return r;
    }
    return { score: s.score, level: s.level, stats: { kills: s.stats.kills, shots: s.stats.shots, arenas: s.stats.arenas,
      bricks: s.stats.bricks, livesLeft: Math.max(0, s.lives), livesLost: s.stats.livesLost, won: !!s.won,
      cause: s.stats.cause, flagBy: s.stats.flagBy || null, mode: s.mode } };
  }

  var TanksLogic = {
    SIZE: SIZE, TILE: TILE, CELL: CELL, N: N, ARENA: ARENA, OX: OX, OY: OY, EMPTY: EMPTY, BRICK: BRICK, STEEL: STEEL,
    WATER: WATER, BUSH: BUSH, DX: DX, DY: DY, PLAYER_START: PLAYER_START, FLAG: FLAG, ENTRIES: ENTRIES,
    PLAYER_SPEED: PLAYER_SPEED, SPAWN_GAP: SPAWN_GAP, MAX_ON_FIELD: MAX_ON_FIELD, ARRIVE: ARRIVE, INTRO: INTRO,
    CLEAR_PAUSE: CLEAR_PAUSE, RESPAWN: RESPAWN, SHIELD: SHIELD, HUNT_AFTER: HUNT_AFTER, KILL_POINTS: KILL_POINTS,
    CLEAR_POINTS: CLEAR_POINTS, MODES: MODES, STATE_VERSION: STATE_VERSION, LEVELS: LEVELS, usableLevels: usableLevels,
    mapOk: mapOk, create: create, step: step, press: press, touch: touch, save: save, restore: restore, result: result,
    checksum: checksum, report: report, steerFor: steerFor, humans: humans, VERSUS_LEVELS: VERSUS_LEVELS, FLAG2: FLAG2,
    P2_START: P2_START, P2_TOP: P2_TOP, V_HIT: V_HIT, V_ROUND: V_ROUND, V_MATCH: V_MATCH, V_WIN_ROUNDS: V_WIN_ROUNDS,
    V_MAX_ROUNDS: V_MAX_ROUNDS, V_ROUND_LIMIT: V_ROUND_LIMIT,
    rand: rand, canPlace: canPlace, blocked: blocked, killEnemy: killEnemy, cellAt: cellAt, enemySpeed: enemySpeed,
  };
  if (typeof module === "object" && module.exports) module.exports = TanksLogic; else self.TanksLogic = TanksLogic;
})();
