"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const T = loadLogic("tanks-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...T.step(s)); } return all; }
function skipIntro(s) { while (s.intro > 0) T.step(s); }
// no enemies coming in (for tests of one thing at a time)
function quiet(s) { skipIntro(s); s.sinceSpawn = -1e9; }
const OPEN = Array(13).fill(".............");
function arena(extra) { return Object.assign({ name: "Test", enemies: 4, speed: 0.6, fire: 10, map: OPEN.slice() }, extra || {}); }
function setCell(s, cx, cy, v) { s.map[cy * T.N + cx] = v; }

// A bot that plays with the inputs: lines up with the nearest enemy and fires, never at the flag.
function bot(s) {
  const p = s.player, set = (dir) => { for (const a of ["up", "down", "left", "right"]) T.press(s, a, a === dir); };
  if (p.dead) { set(null); T.press(s, "fire", false); return; }
  let best = null, bd = 1e9;
  for (const e of s.enemies) { if (e.arrive) continue; const d = Math.abs(e.x - p.x) + Math.abs(e.y - p.y); if (d < bd) { bd = d; best = e; } }
  if (!best) { set(p.y > 150 ? "up" : null); T.press(s, "fire", false); return; }
  const flagBelow = Math.abs(p.x - T.FLAG.x) < 18;
  let fire;
  if (Math.abs(best.x - p.x) < 6) { set(best.y < p.y ? "up" : (flagBelow ? "left" : "down")); fire = true; }
  else if (Math.abs(best.y - p.y) < 6) { set(best.x < p.x ? "left" : "right"); fire = true; }
  else { set(best.x < p.x ? "left" : "right"); fire = (s.updates % 20) < 10; }
  const d = p.dir;
  if (d === 2 && flagBelow) fire = false;
  if ((d === 1 || d === 3) && p.y > 190 && ((d === 1) === (T.FLAG.x > p.x))) fire = false;
  T.press(s, "fire", fire);
}

test("the start: your tank, the flag, the arena from the list, lives by mode", () => {
  const s = T.create({ seed: 1 });
  assert.equal(s.mode, "classic");
  assert.equal(s.lives, 3);
  assert.equal(T.create({ mode: "easy" }).lives, 5);
  assert.deepEqual([s.player.x, s.player.y], [72, 216]);
  assert.equal(s.level, 1);
  assert.equal(s.arenas, T.LEVELS);
  assert.equal(s.total, T.LEVELS[0].enemies);
  assert.equal(s.map.length, 26 * 26);
  // the first arena's brick row next to the flag: row 11, columns 5–7 → half-squares 10–15 of rows 22–23
  for (let cx = 10; cx < 16; cx++) assert.equal(T.cellAt(s, cx, 22), T.BRICK);
  assert.ok(s.intro > 0, "the arena's name shows first");
  const x = s.player.x;
  T.press(s, "right", true);
  run(s, 10);
  assert.equal(s.player.x, x, "nothing moves during the intro");
});

test("driving: held arrows move on the half-square grid; walls and water stop tanks, bushes don't", () => {
  const s = T.create({ seed: 2, levels: [arena()] });
  skipIntro(s);
  T.press(s, "up", true);
  run(s, 8);
  assert.equal(s.player.x, 72);
  assert.ok(s.player.y < 216 && s.player.y >= 216 - 8 * 1.5);
  // turning onto the other axis lines up with the half-square grid
  s.player.y = 200;
  T.press(s, "up", false); T.press(s, "left", true);
  T.step(s);
  assert.equal(s.player.dir, 3);
  assert.equal(s.player.y % 9, 0);
  T.press(s, "left", false);
  // a steel wall to the left stops the tank; water too; bushes don't
  for (const [kind, stops] of [[T.STEEL, true], [T.WATER, true], [T.BRICK, true], [T.BUSH, false]]) {
    const t = T.create({ seed: 2, levels: [arena()] });
    skipIntro(t);
    t.player.x = 72; t.player.y = 108;
    setCell(t, 6, 12, kind); setCell(t, 6, 13, kind);
    T.press(t, "left", true);
    run(t, 30);
    if (stops) assert.equal(t.player.x, 63, `cell kind ${kind}`); else assert.ok(t.player.x < 60, `cell kind ${kind}`);
  }
  // the arena's edge and the flag stop it too
  const e = T.create({ seed: 2, levels: [arena()] });
  skipIntro(e);
  T.press(e, "right", true);
  run(e, 60);
  assert.equal(e.player.x, T.FLAG.x - 18, "stops beside the flag");
});

test("shells: one at a time; bricks break a strip at a time, steel stops shells, water lets them over", () => {
  const s = T.create({ seed: 3, levels: [arena()] });
  quiet(s);
  s.player.x = 54; s.player.y = 126; s.player.dir = 0;
  setCell(s, 6, 8, T.BRICK); setCell(s, 7, 8, T.BRICK); setCell(s, 6, 7, T.BRICK); setCell(s, 7, 7, T.BRICK);
  let evs = T.press(s, "fire", true);
  assert.deepEqual(evs, []);
  evs = T.step(s);
  assert.ok(types(evs).includes("fire"));
  T.step(s);
  assert.equal(s.shells.filter((x) => x.owner === 0).length, 1, "held fire doesn't make a second shell");
  T.press(s, "fire", false);
  evs = run(s, 30);
  assert.ok(types(evs).includes("brick"));
  assert.equal(T.cellAt(s, 6, 8), T.EMPTY);
  assert.equal(T.cellAt(s, 7, 8), T.EMPTY);
  assert.equal(T.cellAt(s, 6, 7), T.BRICK, "one strip at a time");
  assert.equal(s.stats.bricks, 2);
  // steel
  setCell(s, 6, 7, T.STEEL); setCell(s, 7, 7, T.STEEL);
  T.press(s, "fire", true); T.press(s, "fire", false);
  evs = run(s, 30);
  assert.ok(types(evs).includes("steel"));
  assert.equal(T.cellAt(s, 6, 7), T.STEEL);
  // water: the shell flies over and leaves the arena
  setCell(s, 6, 7, T.WATER); setCell(s, 7, 7, T.WATER);
  T.press(s, "fire", true); T.press(s, "fire", false);
  run(s, 20);
  assert.equal(s.shells.length, 1);
  assert.ok(s.shells[0].y < 7 * 9, "past the water");
  run(s, 20);
  assert.equal(s.shells.length, 0);
  assert.equal(T.cellAt(s, 6, 7), T.WATER);
});

test("enemies come in at most one every 1.5 s, four at once at most, from the three entries", () => {
  const s = T.create({ seed: 4, levels: [arena({ enemies: 20 })] });
  for (const [cx, cy] of [[10, 22], [11, 22], [12, 22], [13, 22], [14, 22], [15, 22], [10, 23], [11, 23], [14, 23], [15, 23],
    [10, 24], [11, 24], [14, 24], [15, 24], [10, 25], [11, 25], [14, 25], [15, 25]]) setCell(s, cx, cy, T.STEEL);  // a safe flag
  let last = -1e9, maxOn = 0;
  const seen = new Set();
  while (s.updates < 60 * 30) {
    assert.ok(!s.over);
    const evs = T.step(s);
    if (types(evs).includes("spawn")) {
      assert.ok(s.updates - last >= T.SPAWN_GAP);
      last = s.updates;
      const e = s.enemies[s.enemies.length - 1];
      seen.add(e.x);
      assert.equal(e.y, 0);
    }
    maxOn = Math.max(maxOn, s.enemies.length);
    s.player.shield = 999;
  }
  assert.equal(maxOn, T.MAX_ON_FIELD);
  assert.deepEqual([...seen].sort((a, b) => a - b), [0, 108, 216]);
});

test("a shell destroys an enemy (100); the arena's last enemy clears it (500); then the next arena; the last one wins", () => {
  const two = [arena({ name: "One" }), arena({ name: "Two", enemies: 5 })];
  const s = T.create({ seed: 5, levels: two });
  assert.equal(s.arenas.length, 2);
  skipIntro(s);
  run(s, 2);
  const e = s.enemies[0];
  assert.ok(e);
  e.arrive = 0;
  s.player.x = e.x; s.player.y = e.y + 60; s.player.dir = 0;
  T.press(s, "fire", true); T.press(s, "fire", false);
  const evs = run(s, 20);
  assert.ok(types(evs).includes("hit"));
  assert.equal(s.score, T.KILL_POINTS);
  // the rest of the arena by hand
  const all = [];
  while (!s.over && s.level === 1) {
    for (let i = s.enemies.length - 1; i >= 0; i--) T.killEnemy(s, i, all);
    all.push(...T.step(s));
  }
  assert.ok(types(all).includes("clear"));
  assert.ok(types(all).includes("level"));
  assert.equal(s.level, 2);
  assert.equal(s.score, 4 * T.KILL_POINTS + T.CLEAR_POINTS);
  assert.equal(s.total, 5);
  assert.equal(s.name, "Two");
  assert.deepEqual([s.player.x, s.player.y], [72, 216], "back at the start");
  while (!s.over) {
    for (let i = s.enemies.length - 1; i >= 0; i--) T.killEnemy(s, i, all);
    all.push(...T.step(s));
  }
  assert.ok(s.won);
  assert.ok(types(all).includes("win"));
  const r = T.result(s);
  assert.equal(r.stats.won, true);
  assert.equal(r.stats.cause, "won");
  assert.equal(r.stats.arenas, 2);
  assert.equal(r.stats.kills, 9);
  assert.equal(r.score, 9 * 100 + 2 * 500);
});

test("an enemy shell costs a life (not while shielded); no lives left ends the game; a shell on the flag ends it", () => {
  const s = T.create({ seed: 6, levels: [arena()] });
  quiet(s);
  s.player.x = 108; s.player.y = 108; s.player.shield = 0;
  s.shells.push({ x: 117, y: 60, dir: 2, v: 2.5, owner: 99 });
  let evs = run(s, 30);
  assert.ok(types(evs).includes("lifeLost"));
  assert.equal(s.lives, 2);
  assert.ok(s.player.dead > 0);
  run(s, T.RESPAWN + 1);
  assert.deepEqual([s.player.x, s.player.y], [72, 216]);
  assert.ok(s.player.shield > 0, "a shield after coming back");
  s.shells.push({ x: 81, y: 150, dir: 2, v: 2.5, owner: 99 });
  evs = run(s, 40);
  assert.ok(!types(evs).includes("lifeLost"), "the shield takes it");
  s.lives = 1; s.player.shield = 0;
  s.shells.push({ x: 81, y: 150, dir: 2, v: 2.5, owner: 99 });
  evs = run(s, 40);
  assert.ok(s.over);
  assert.equal(T.result(s).stats.cause, "lives");
  assert.ok(types(evs).includes("gameover"));
  // the flag
  const f = T.create({ seed: 6, levels: [arena()] });
  quiet(f);
  f.shells.push({ x: 117, y: 180, dir: 2, v: 2.5, owner: 99 });
  evs = run(f, 40);
  assert.ok(f.over && f.flagDown);
  assert.equal(T.result(f).stats.cause, "flag");
  assert.equal(T.result(f).stats.flagBy, "enemy");
});

test("Easy: slower enemies that shoot less, more lives; enemies hunt the flag after 30 s", () => {
  const c = T.create({ mode: "classic" }), e = T.create({ mode: "easy" });
  assert.ok(T.enemySpeed(e) < T.enemySpeed(c));
  assert.ok(T.MODES.easy.fire < T.MODES.classic.fire);
  assert.equal(T.HUNT_AFTER, 1800);
  // with nobody shooting back, the enemies end the game in the end
  const s = T.create({ seed: 8, levels: [arena({ enemies: 20 })] });
  run(s, 60 * 60 * 10, (st) => { st.player.shield = 999; });
  assert.ok(s.over, "the flag falls without a defender");
  assert.equal(T.result(s).stats.cause, "flag");
});

test("deterministic for a seed; the bot plays well; save and restore plays on the same", () => {
  const play = (seed, mode) => { const s = T.create({ seed, mode }); run(s, 60 * 60 * 5, bot); return s; };
  const a = play(11, "classic"), b = play(11, "classic");
  assert.equal(JSON.stringify(T.result(a)) + a.updates, JSON.stringify(T.result(b)) + b.updates);
  assert.ok(a.stats.kills >= 5, `kills ${a.stats.kills}`);
  const s = T.create({ seed: 12 });
  run(s, 60 * 20, bot);
  const copy = T.restore(JSON.parse(JSON.stringify(T.save(s))));
  for (let i = 0; i < 60 * 30 && !s.over; i++) { bot(s); bot(copy); T.step(s); T.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.updates, s.updates);
  assert.deepEqual(copy.map, s.map);
  assert.equal(JSON.stringify(T.save(copy)), JSON.stringify(T.save(s)));
  assert.throws(() => T.restore({ map: 3 }), /can't be continued/);
  assert.throws(() => T.restore(null));
});

test("the arena list: built-in or the session's; bad arenas are skipped; a bad list falls back; save keeps going with the list", () => {
  assert.equal(T.usableLevels(undefined), T.LEVELS);
  assert.equal(T.usableLevels("nope"), T.LEVELS);
  for (const a of T.LEVELS) assert.ok(T.mapOk(a.map), a.name);
  const blockedEntry = OPEN.slice(); blockedEntry[0] = "s............";
  const bad = [
    arena({ enemies: 3 }), arena({ enemies: 21 }), arena({ speed: 1.3 }), arena({ fire: 41 }), arena({ fire: 10.5 }),
    arena({ map: blockedEntry }), arena({ map: OPEN.slice(0, 12) }), arena({ map: OPEN.map((r) => r + ".") }),
    arena({ map: ["............x"].concat(OPEN.slice(1)) }), arena({ name: 5 }), null,
  ];
  // the start walled in by steel and water: the entries can't be reached
  const shut = OPEN.slice(); shut[11] = "sswsss......."; shut[12] = "...s.b.b.....";
  bad.push(arena({ map: shut }));
  // steel beside the flag
  const steelFlag = OPEN.slice(); steelFlag[12] = ".....s.b.....";
  bad.push(arena({ map: steelFlag }));
  assert.equal(T.usableLevels(bad), T.LEVELS);
  const good = arena({ name: "Good" });
  assert.deepEqual(T.usableLevels(bad.concat([good])).map((a) => a.name), ["Good"]);
  // saved mid-arena, continued with the same list; the list isn't in the save
  const list = [arena({ name: "A" }), arena({ name: "B" })];
  const s = T.create({ seed: 4, levels: list });
  run(s, 600, bot);
  const data = JSON.parse(JSON.stringify(T.save(s)));
  assert.ok(!("arenas" in data));
  const back = T.restore(data, list);
  assert.equal(back.arenas.length, 2);
  assert.equal(T.restore(data).arenas, T.LEVELS);
});

test("honest score: a frame-perfect bot (every enemy gone the moment it can be) and the playing bot stay inside the limits", () => {
  const PER_SECOND = 150, BASE = 225, MAX = 1500000;
  const hard = [];
  for (let i = 0; i < 12; i++) hard.push(arena({ name: "Hard " + "ABCDEFGHIJKL"[i], enemies: 20, speed: 1.2, fire: 40 }));
  let fastest = 0;
  for (const [mode, levels] of [["classic", undefined], ["easy", undefined], ["classic", hard], ["easy", hard]]) {
    // perfect: an enemy is destroyed in the very update it comes in
    const s = T.create({ seed: 21, mode, levels });
    while (!s.over) {
      for (let i = s.enemies.length - 1; i >= 0; i--) T.killEnemy(s, i, []);
      T.step(s);
      const sec = s.updates / 60;
      assert.ok(s.score <= sec * PER_SECOND + BASE, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
      assert.ok(s.score <= MAX && s.level <= 100);
      if (sec > 10) fastest = Math.max(fastest, s.score / sec);
    }
    assert.ok(s.won);
    // and a bot playing with the keys
    for (const seed of [1, 2, 3]) {
      const p = T.create({ seed, mode, levels });
      while (!p.over && p.updates < 60 * 60 * 15) {
        bot(p); T.step(p);
        assert.ok(p.score <= (p.updates / 60) * PER_SECOND + BASE);
      }
    }
  }
  console.log(`# tanks: fastest possible rate ${fastest.toFixed(1)} points a second`);
});

test("tanks renders and plays in every look, with keys and touches, switching looks mid-game", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "easy"]) {
      const sb = makeSandbox({ extra: ["tanks-logic.js", "tanks.js"] });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("tanks");
      assert.equal(def.name, "Tank Battle");
      assert.equal(def.controls, "buttons");
      assert.equal(def.stateVersion, 1);
      assert.equal(def.buttons.length, 5);
      assert.ok(def.buttons.every((b) => b.place));
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      const s = inst.logic;
      for (let i = 0; i < 500; i++) {
        if (i % 60 === 0) inst.input(["up", "left", "right", "down"][(i / 60) % 4], true);
        if (i % 60 === 40) inst.input(["up", "left", "right", "down"][((i - 40) / 60) % 4], false);
        if (i % 9 === 0) inst.input("fire", i % 18 === 0);
        if (i % 50 === 25) { inst.pointer("down", 200, 300); inst.pointer("move", 210, 120); }
        if (i % 50 === 35) inst.pointer("up", 0, 0);
        if (i === 300) { s.booms.push({ x: 50, y: 50, t: 0, big: true }); s.clear = 50; }
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      if (inst.state === "over") assert.equal(ends.length, 1);
      else { const saved = inst.save(); assert.ok(saved && saved.state); }
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
  // a saved game continues with the session's list, and a game that ends reports its result
  const sb = makeSandbox({ extra: ["tanks-logic.js", "tanks.js"] });
  const def = sb.win.ArcadeGames.get("tanks");
  const list = [arena({ name: "Only" })];
  const ends = [];
  const first = def.create(sb.canvas(390, 487), { seed: 3, levels: list });
  first.start(); sb.frames(200);
  const saved = first.save();
  first.destroy();
  const inst = def.create(sb.canvas(390, 487), { seed: 3, levels: list, restore: saved, onEnd: (r) => ends.push(r) });
  inst.start();
  assert.equal(inst.logic.arenas.length, 1);
  inst.logic.flagDown = false;
  inst.logic.shells.push({ x: 117, y: 200, dir: 2, v: 2.5, owner: 99 });
  sb.frames(60);
  assert.equal(ends.length, 1);
  assert.equal(ends[0].stats.cause, "flag");
  assert.equal(sb.counts.nonFinite || 0, 0);
});

test("responsive controls: a quick tap still turns and moves, Fire is kept until the shell is back, corners slide", () => {
  const s = T.create({ seed: 4, levels: [arena()] });
  quiet(s);
  const y0 = s.player.y;
  T.press(s, "left", true); T.press(s, "left", false);       // pressed and let go between two updates
  T.step(s);
  assert.equal(s.player.dir, 3, "the tap turned the tank");
  T.press(s, "up", true); T.press(s, "up", false);
  T.step(s);
  assert.ok(s.player.y < y0, "and a tap moves it a little");
  // Fire while the shell is still flying: it fires as soon as the shell is gone
  T.press(s, "fire", true); T.press(s, "fire", false);
  T.step(s);
  assert.equal(s.shells.filter((x) => x.owner === 0).length, 1);
  const shots = s.stats.shots;
  for (let i = 0; i < 8; i++) T.step(s);
  T.press(s, "fire", true); T.press(s, "fire", false);
  for (let i = 0; i < 80 && s.stats.shots === shots; i++) T.step(s);
  // (the buffer lasts 12 updates; a shell takes longer to land, so without one in flight it fires at once)
  const t = T.create({ seed: 4, levels: [arena()] });
  quiet(t);
  T.press(t, "fire", true); T.press(t, "fire", false); T.step(t);
  const first = t.shells.find((x) => x.owner === 0);
  t.shells.forEach((x) => { x.v = 0; });                       // keep it in the air
  T.press(t, "fire", true); T.press(t, "fire", false);
  T.step(t); T.step(t);
  t.shells = t.shells.filter((x) => x !== first);              // it lands
  T.step(t);
  assert.equal(t.stats.shots, 2, "the buffered press fired");
  // a gap between two bricks: 4 px off still goes through (slides in), instead of stopping at the corner
  const c = T.create({ seed: 4, levels: [arena()] });
  quiet(c);
  for (let cx = 0; cx < T.N; cx++) if (cx < 8 || cx > 9) setCell(c, cx, 18, T.STEEL);  // a wall with a 2-cell gap at cells 8–9
  c.player.x = 72 + 4; c.player.y = 171;
  T.press(c, "up", true);
  for (let i = 0; i < 40; i++) T.step(c);
  assert.ok(c.player.y < 150, `went through the gap (y ${c.player.y})`);
  assert.equal(c.player.x, 72);
});
