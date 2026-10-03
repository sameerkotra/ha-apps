"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const I = loadLogic("invaders-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const PER_SECOND = 150, BASE = 400, MAX_SCORE = 1_300_000;
function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...I.step(s)); } return all; }
function play(s) { while (s.phase !== "play" && !s.over) I.step(s); }
function critters(s) { const out = []; s.grid.forEach((k, i) => { if (k) out.push({ k, c: i % I.COLS, r: Math.floor(i / I.COLS), i }); }); return out; }
// A steady gunner: lines up under the nearest critter, keeps the fire button held, steps away from bombs.
function gunner(s) {
  let best = null, bd = 1e9;
  for (const t of critters(s)) { const x = I.critterX(s, t.c), d = Math.abs(x - s.x); if (d < bd) { bd = d; best = x; } }
  if (s.ship) best = s.ship.x + s.ship.dir * 24;
  if (s.bombs.some((b) => Math.abs(b.x - s.x) < 14 && b.y > 160)) I.aim(s, s.x + (s.x < 120 ? 30 : -30));
  else if (best != null) I.aim(s, best);
  I.press(s, "fire", true);
}
// A frame-perfect cheat for the honest-score test: puts the cannon right under the lowest critter (or the
// ship) every update, fires the moment it may, and never runs out of lives.
function perfect(s) {
  s.lives = 3;
  let target = null, low = -1;
  for (const t of critters(s)) if (I.critterY(s, t.r) > low) { low = I.critterY(s, t.r); target = I.critterX(s, t.c); }
  if (s.ship && s.ship.x > 20 && s.ship.x < 220) target = s.ship.x + s.ship.dir * 10;
  if (target != null) s.x = Math.max(12, Math.min(228, target));
  const e = I.extent(s);
  if (e) s.fy = I.LAND_Y - 40 - e.maxR * I.CELL;   // as low as it can be without landing: the shortest shots
  s.shields = [];
  I.press(s, "fire", true);
}

test("a wave: the formation from the level, 3 lives, shields, a pause before play", () => {
  const s = I.create({ seed: 1 });
  assert.equal(s.mode, "classic");
  assert.equal(s.lives, 3);
  assert.equal(s.rows, 3);
  assert.equal(critters(s).length, 9 + 11 + 11);
  assert.equal(s.shields.length, 4);
  assert.deepEqual(s.shields.map((sh) => sh.x + 12), [30, 90, 150, 210]);
  assert.equal(s.phase, "start");
  I.press(s, "fire", true);
  run(s, 30);
  assert.equal(s.shot, null, "no shots before the wave starts");
  play(s);
  assert.equal(s.phase, "play");
  assert.equal(I.create({ mode: "nope" }).mode, "classic");
});

test("the cannon: keys move it 2 px an update; a finger is followed at most 3 px an update; it stays on screen", () => {
  const s = I.create({ seed: 2 });
  play(s);
  const x0 = s.x;
  I.press(s, "left", true); run(s, 10); I.press(s, "left", false);
  assert.equal(s.x, x0 - 20);
  I.aim(s, 200); I.step(s);
  assert.equal(s.x, x0 - 17);
  run(s, 200);
  assert.equal(s.x, 200);
  I.press(s, "right", true); run(s, 200);
  assert.equal(s.x, 228);
  I.aim(s, -50); I.press(s, "right", false); run(s, 200);
  assert.equal(s.x, 12);
});

test("one shot at a time, at least 15 updates apart; a hit scores by row type and removes the critter", () => {
  const s = I.create({ seed: 3 });
  play(s);
  const t = critters(s).find((c) => c.r === s.rows - 1);
  s.x = I.critterX(s, t.c);
  s.bombT = 1e9;
  I.press(s, "fire", true);
  const evs = I.step(s);
  assert.ok(types(evs).includes("fire"));
  assert.ok(s.shot);
  const shots = s.stats.shots;
  I.step(s); I.step(s);
  assert.equal(s.stats.shots, shots, "held fire doesn't fire again while a shot flies");
  const all = run(s, 60);
  const hit = all.find((e) => e.type === "hit");
  assert.ok(hit, "the shot hits the critter above");
  assert.equal(hit.points, 10, "bottom row c = 10");
  assert.equal(s.grid[t.i], "");
  assert.equal(I.POINTS.a, 30); assert.equal(I.POINTS.b, 20);
  // shots are at least FIRE_GAP apart
  let last = -100;
  const u = I.create({ seed: 3 }); play(u);
  for (let i = 0; i < 600 && !u.over; i++) {
    u.x = 120; u.lives = 3; I.press(u, "fire", true);
    if (types(I.step(u)).includes("fire")) { assert.ok(u.updates - last >= I.FIRE_GAP); last = u.updates; }
  }
});

test("the formation marches, steps down at the edge and turns, and speeds up as critters go", () => {
  const s = I.create({ seed: 4 });
  play(s);
  s.bombT = 1e9;
  const full = I.interval(s), fx = s.fx, fy = s.fy;
  run(s, full + 1);
  assert.equal(s.fx, fx + 3);
  let turned = false;
  for (let i = 0; i < 4000 && !turned; i++) { I.step(s); if (s.fy > fy) turned = true; }
  assert.ok(turned, "stepped down");
  assert.equal(s.dir, -1);
  for (let i = 0; i < s.grid.length - 2; i++) if (s.grid[i]) { s.grid[i] = ""; s.alive--; }
  assert.ok(I.interval(s) < full / 3);
  assert.ok(I.interval(s) >= 2);
});

test("the formation reaching the bottom ends the game; it wears away shields on the way", () => {
  const s = I.create({ seed: 5 });
  play(s);
  s.bombT = 1e9;
  s.fy = I.SHIELD_Y - 30;
  const before = s.shields.reduce((n, sh) => n + sh.blocks.filter(Boolean).length, 0);
  const evs = run(s, 20000);
  assert.ok(s.over);
  assert.equal(I.result(s).stats.cause, "landed");
  assert.ok(types(evs).includes("gameover"));
  const after = s.shields.reduce((n, sh) => n + sh.blocks.filter(Boolean).length, 0);
  assert.ok(after < before);
});

test("bombs: they wear the shields block by block, a bomb on the cannon costs a life, no lives ends the game", () => {
  const s = I.create({ seed: 6 });
  play(s);
  s.bombT = 1e9;
  const sh = s.shields[1], x = sh.x + 10;
  s.bombs.push({ x, y: I.SHIELD_Y - 20, k: 0 });
  const blocks = sh.blocks.filter(Boolean).length;
  run(s, 30);
  assert.equal(s.bombs.length, 0);
  const left = sh.blocks.filter(Boolean).length;
  assert.ok(left < blocks && left >= blocks - 2, "one or two blocks gone");
  // a shot wears them from below too
  s.x = x; s.lastShot = -100; I.press(s, "fire", true); I.press(s, "fire", false);
  run(s, 20);
  assert.ok(sh.blocks.filter(Boolean).length < left);
  for (let life = 2; life >= 0; life--) {
    play(s);
    s.bombT = 1e9;
    s.bombs.push({ x: s.x, y: I.CANNON_Y - 20, k: 1 });
    const evs = run(s, 20);
    assert.ok(types(evs).includes("lifeLost"));
    assert.equal(s.lives, life);
  }
  assert.ok(s.over);
  assert.equal(I.result(s).stats.cause, "bombed");
  assert.equal(I.result(s).stats.livesLeft, 0);
});

test("a shot meets a bomb: both go", () => {
  const s = I.create({ seed: 7 });
  play(s);
  s.bombT = 1e9;
  s.bombs.push({ x: 120, y: 150, k: 0 });
  s.shot = { x: 120, y: 200 };
  const evs = run(s, 20);
  assert.ok(types(evs).includes("zap"));
  assert.equal(s.bombs.length, 0);
  assert.equal(s.shot, null);
});

test("the bonus ship: not before 15 s, at most 2 a wave, 1,200 updates apart; hitting it scores 50–300", () => {
  const s = I.create({ seed: 8 });
  const seen = [];
  for (let i = 0; i < 60 * 120; i++) {
    s.lives = 3; s.fy = 54; s.bombs = []; s.bombT = 1e9;
    if (types(I.step(s)).includes("ship")) seen.push(s.updates);
  }
  assert.ok(seen.length >= 1 && seen.length <= 2, `ships ${seen}`);
  assert.ok(seen[0] >= I.SHIP_FIRST);
  for (let i = 1; i < seen.length; i++) assert.ok(seen[i] - seen[i - 1] >= I.SHIP_GAP);
  const t = I.create({ seed: 9 });
  play(t);
  t.ship = { x: 30, dir: 1, points: 150 };
  t.shot = { x: 31, y: 60 };
  const evs = run(t, 10);
  const b = evs.find((e) => e.type === "bonus");
  assert.ok(b);
  assert.equal(b.points, 150);
  assert.equal(t.score, 150);
  assert.ok(I.SHIP_POINTS.every((p) => p >= 50 && p <= 300));
});

test("clearing the formation brings the next wave (a level event); classic waves repeat quicker", () => {
  const s = I.create({ seed: 10 });
  play(s);
  for (const t of critters(s).slice(1)) { s.grid[t.i] = ""; s.alive--; }
  const last = critters(s)[0];
  s.x = I.critterX(s, last.c); s.bombT = 1e9;
  I.press(s, "fire", true);
  const evs = run(s, 60);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.level, 2);
  assert.equal(s.stats.waves, 1);
  play(s);
  assert.equal(critters(s).length, 44, "Busy bees");
  assert.equal(I.wave(s, 9).name, I.LEVELS[0].name);
  assert.ok(I.wave(s, 9).speed > I.wave(s, 1).speed);
  assert.ok(I.wave(I.create({ mode: "easy" }), 1).speed < I.wave(s, 1).speed);
});

test("Waves: built-in or the session's list; a bad list falls back; the last wave wins", () => {
  assert.equal(I.usableLevels(undefined), I.LEVELS);
  assert.equal(I.usableLevels([{ name: "Bad", formation: ["aaa"], speed: 1, bombs: 2, shields: 4 }]), I.LEVELS);
  assert.equal(I.usableLevels([{ name: "Few", formation: ["a..........", "...........", "....b......"], speed: 1, bombs: 2, shields: 4 }]), I.LEVELS);
  const two = [
    { name: "A", formation: ["aaaaaa.....", "...........", "..........."], speed: 1, bombs: 1, shields: 4 },
    { name: "B", formation: ["...........", "...........", "bbbbbbcccc."], speed: 2, bombs: 10, shields: 0 },
  ];
  assert.equal(I.usableLevels([two[0], { name: "Fast", formation: two[0].formation, speed: 3, bombs: 1, shields: 4 }]).length, 1);
  const s = I.create({ mode: "waves", seed: 3, levels: two });
  assert.equal(s.waves.length, 2);
  assert.equal(critters(s).length, 6);
  let evs = [];
  for (let i = 0; i < 60 * 300 && !s.over; i++) { perfect(s); evs.push(...I.step(s)); }
  assert.ok(types(evs).includes("level"));
  assert.ok(s.won && s.over, `won (cause ${s.stats.cause})`);
  assert.equal(s.level, 2);
  assert.equal(s.shields.length, 0);
  assert.equal(I.result(s).stats.won, true);
  assert.equal(I.result(s).stats.cause, "won");
  assert.equal(I.result(s).stats.waves, 2);
  assert.ok(types(evs).includes("win"));
  // the built-in list in Waves mode
  assert.equal(I.create({ mode: "waves" }).waves, I.LEVELS);
  // saved mid-wave, continued with the same list
  const t = I.create({ mode: "waves", seed: 3, levels: two });
  for (let i = 0; i < 400; i++) { perfect(t); I.step(t); }
  assert.ok(!("waves" in I.save(t)));
  const back = I.restore(JSON.parse(JSON.stringify(I.save(t))), two);
  assert.equal(back.waves.length, 2);
  for (let i = 0; i < 60 * 300 && !back.over; i++) { perfect(back); I.step(back); }
  assert.ok(back.won);
  // classic ends won after wave 100
  const c = I.create({ seed: 4 });
  c.level = I.MAX_WAVES;
  play(c);
  for (const t2 of critters(c)) { c.grid[t2.i] = ""; c.alive--; }
  c.grid[0] = "a"; c.alive = 1;
  for (let i = 0; i < 60 * 300 && !c.over; i++) { perfect(c); I.step(c); }
  assert.ok(c.won);
  assert.equal(c.level, 100);
});

test("deterministic for a seed; save and restore play on identically", () => {
  const go = (seed) => { const s = I.create({ seed }); run(s, 60 * 90, gunner); return JSON.stringify(I.result(s)) + s.updates + s.score; };
  assert.equal(go(21), go(21));
  const s = I.create({ seed: 22 });
  run(s, 60 * 20, gunner);
  assert.ok(!s.over);
  const copy = I.restore(JSON.parse(JSON.stringify(I.save(s))));
  for (let i = 0; i < 60 * 30 && !s.over; i++) { gunner(s); gunner(copy); I.step(s); I.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.x, s.x);
  assert.deepEqual(copy.grid, s.grid);
  assert.equal(copy.over, s.over);
  assert.throws(() => I.restore({ grid: 3 }), /can't be continued/);
  assert.throws(() => I.restore(null), /can't be continued/);
  // an older save without some fields still continues
  const old = JSON.parse(JSON.stringify(I.save(s)));
  delete old.fireBuf;
  assert.doesNotThrow(() => I.step(I.restore(old)));
});

test("a steady gunner scores; honest-score limits hold for a frame-perfect cheat in every mode and the hardest waves", () => {
  const g = I.create({ seed: 31 });
  run(g, 60 * 120, gunner);
  assert.ok(g.score >= 300, `gunner ${g.score}`);
  const hardest = [
    { name: "Packed", formation: Array(6).fill("aaaaaaaaaaa"), speed: 2, bombs: 10, shields: 0 },
    { name: "Few", formation: ["aaaaaa.....", "...........", "..........."], speed: 2, bombs: 10, shields: 0 },
  ];
  let fastest = 0;
  for (const [mode, levels] of [["classic"], ["easy"], ["waves", hardest], ["waves"]]) {
    const s = I.create({ mode, seed: 32, levels });
    while (!s.over && s.updates < 60 * 60 * 40) {
      perfect(s);
      I.step(s);
      assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `${mode}: ${s.score} after ${s.updates / 60}s`);
      assert.ok(s.score <= MAX_SCORE);
      assert.ok(s.level <= Math.max(100, s.waves ? s.waves.length : 0));
      if (s.updates > 600) fastest = Math.max(fastest, s.score / (s.updates / 60));
    }
    if (mode === "waves") assert.ok(s.won, `${mode}: the list ends the game (${s.level})`);
    assert.ok(s.score > 1000);
  }
  if (process.env.SHOW_RATE) console.log("fastest", fastest);
  assert.ok(fastest > 60, `fastest ${fastest.toFixed(1)} points a second`);
});

test("renders in every look while it plays (buttons, drag, tap), switching looks, reduce motion and size", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "waves"]) {
      const sb = makeSandbox({ extra: ["invaders-logic.js", "invaders.js"] });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("invaders");
      assert.equal(def.controls, "buttons");
      assert.equal(def.name, "Sky Defenders");
      assert.equal(def.stateVersion, 1);
      assert.equal(def.buttons.map((b) => b.action).join(), "left,right,fire");
      assert.ok(def.buttons.every((b) => b.place));
      const inst = def.create(canvas, { mode, look, seed: 5, levels: mode === "waves" ? I.LEVELS.slice(2) : undefined });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      const s = inst.logic;
      for (let i = 0; i < 500; i++) {
        if (i === 150) { s.ship = { x: 100, dir: 1, points: 100 }; s.bombs.push({ x: 60, y: 120, k: 1 }); }
        if (i === 200) s.bombs.push({ x: s.x, y: 240, k: 0 });
        if (i % 9 === 0) inst.input(i % 18 ? "left" : "right", true);
        if (i % 9 === 4) { inst.input("left", false); inst.input("right", false); }
        if (i % 13 === 0) inst.input("fire", true);
        if (i % 13 === 6) inst.input("fire", false);
        if (i % 40 === 0) { inst.pointer("down", 100, 300); inst.pointer("move", 160, 300); inst.pointer("move", 260, 300); inst.pointer("up", 260, 300); }
        if (i % 40 === 20) { inst.pointer("down", 200, 200); inst.pointer("up", 200, 200); }
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(s.stats.shots > 0, "fired");
      if (!s.over) assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
