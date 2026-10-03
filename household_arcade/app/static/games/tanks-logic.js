/* Household Arcade — Tank Battle rules (pure: no DOM, deterministic for a given seed).

   Seen from above: a 13 × 13 arena of 18 px squares. Your tank starts at the bottom next to the home flag;
   enemy tanks drive in from three entries at the top, one at a time (at most one every 1.5 s, at most four on
   the arena at once). Tanks drive on a half-square grid in four directions and each has one shell in the air at
   a time. Brick walls break a half-square strip at a time, steel walls don't break, water stops tanks but not
   shells, bushes hide tanks. A hit costs a life; the game ends with no lives left or when a shell hits the flag.
   Destroying every enemy of an arena clears it and the next arena of the list starts; clearing the last one wins.
   Every mode plays the arena list (the arenas are the levels). One call to step() is one update (60 a second). */
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
  };

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
    var a = arena(s), m = [];
    for (var cy = 0; cy < N; cy++) for (var cx = 0; cx < N; cx++) m.push(CHARS[a.map[cy >> 1][cx >> 1]]);
    s.map = m; s.mapVer++;
    s.arenaSpeed = a.speed; s.arenaFire = a.fire; s.name = a.name;
    s.total = a.enemies; s.spawned = 0; s.enemies = []; s.shells = []; s.booms = [];
    s.arenaT = 0; s.intro = INTRO; s.clear = 0; s.entryNext = 1;
    placePlayer(s);
  }
  function placePlayer(s) {
    s.player = { x: PLAYER_START.x, y: PLAYER_START.y, dir: 0, acc: 0, dead: 0, shield: SHIELD };
  }

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
    loadArena(s);
    return s;
  }

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
    return out.concat(s.enemies);
  }
  /** Can `tank` move to (nx, ny)? Tanks that already overlap (a respawn) may drive apart. */
  function canPlace(s, tank, nx, ny) {
    if (nx < 0 || ny < 0 || nx > MAXP || ny > MAXP) return false;
    if (blocked(s, nx, ny) || overlaps(nx, ny, FLAG.x, FLAG.y)) return false;
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
    if (s.intro > 0) { s.intro--; s.fireReq = 0; s.tapDir = -1; return evs; }
    s.arenaT++;
    spawn(s, evs);
    movePlayer(s, evs);
    moveEnemies(s);
    moveShells(s, evs);
    if (!s.over && s.spawned >= s.total && !s.enemies.length) {
      s.score += CLEAR_POINTS; s.stats.arenas++;
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

  function movePlayer(s, evs) {
    var p = s.player;
    if (p.dead > 0) {
      s.fireReq = 0; s.tapDir = -1;
      if (--p.dead === 0) placePlayer(s);
      return;
    }
    if (p.shield > 0) p.shield--;
    // the direction held now, else a direction tapped since the last update (a quick tap still turns and moves)
    var dir = s.held.length ? s.held[s.held.length - 1] : s.touchDir >= 0 ? s.touchDir : s.tapDir;
    if (dir >= 0) { face(p, dir); drive(s, p, PLAYER_SPEED, true); }
    s.tapDir = -1;
    if ((s.fireReq > 0 || s.fireHeld) && !hasShell(s, 0)) {
      fire(s, p, 0, PLAYER_SHELL); s.stats.shots++;
      evs.push({ type: "fire" });
      s.fireReq = 0;
    } else if (s.fireReq > 0) s.fireReq--;
  }

  function chooseDir(s, e) {
    var hunt = s.arenaT > HUNT_AFTER, p = s.player;
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
      if (!a.dead && !b.dead && (a.owner === 0) !== (b.owner === 0) && Math.abs(a.x - b.x) < 5 && Math.abs(a.y - b.y) < 5) {
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
      else if (sh.owner === 0) evs.push({ type: "steel" });
      return true;
    }
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
  function cellOr(s, cx, cy) { return cx < 0 || cy < 0 || cx >= N || cy >= N ? EMPTY : s.map[cy * N + cx]; }

  /** An enemy is destroyed (by your shell). */
  function killEnemy(s, i, evs) {
    var e = s.enemies[i];
    s.enemies.splice(i, 1);
    for (var k = 0; k < s.shells.length; k++) if (s.shells[k].owner === e.id) s.shells[k].dead = true;
    s.booms.push({ x: e.x + 9, y: e.y + 9, t: 0, big: false });
    s.score += KILL_POINTS; s.stats.kills++;
    evs.push({ type: "hit", score: s.score, left: s.total - s.stats.kills });
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

  function end(s, evs, cause) {
    if (s.over) return;
    s.over = true; s.stats.cause = cause;
    evs.push({ type: "gameover", score: s.score, cause: cause });
  }

  // ---------- input ----------
  var DIRS = { up: 0, right: 1, down: 2, left: 3 };
  function press(s, action, down) {
    if (s.over) return [];
    if (action in DIRS) {
      var d = DIRS[action], at = s.held.indexOf(d);
      if (at >= 0) s.held.splice(at, 1);
      if (down) { s.held.push(d); s.tapDir = d; }
    } else if (action === "fire") {
      s.fireHeld = !!down;
      if (down) s.fireReq = FIRE_BUFFER;
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

  function result(s) {
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
    rand: rand, canPlace: canPlace, blocked: blocked, killEnemy: killEnemy, cellAt: cellAt, enemySpeed: enemySpeed,
  };
  if (typeof module === "object" && module.exports) module.exports = TanksLogic; else self.TanksLogic = TanksLogic;
})();
