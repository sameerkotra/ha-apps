"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const L = loadLogic("snakeduel-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
// The limits proposed for app/games.py.
const PER_SECOND = 800, BASE = 500, MAX_SCORE = 5_000_000;

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...L.step(s)); } return all; }
function untilPlay(s) { const all = []; while (s.phase !== "play" && !s.over) all.push(...L.step(s)); return all; }
const OPEN = Array(20).fill(".".repeat(24));
const arena = (o) => Object.assign({ name: "Test", walls: OPEN, speed: 6, foods: 1 }, o);
// The hardest arenas the field ranges allow: top speed, the most food.
const HARDEST = Array.from({ length: 12 }, (_, i) => arena({ name: "Hard " + i, speed: 12, foods: 4 }));
// …and with a wall just beside player 2's head, so it can crash on its very first move.
const QUICK_WALLS = OPEN.slice(); QUICK_WALLS[8] = ".....#" + ".".repeat(18); QUICK_WALLS[11] = ".".repeat(18) + "#.....";
const QUICK = HARDEST.map((a) => Object.assign({}, a, { walls: QUICK_WALLS }));

// A food-greedy bot for player 1 (the computer's own brain, at full skill, used for player 1).
function greedy(s) {
  if (s.phase !== "play" && s.phase !== "ready") return;
  const sn = s.snakes[0];
  if (sn.queue.length) return;
  const lv = s.level; s.level = 10; sn.cpu = true;
  const d = L.cpuDecide(s, 0);
  sn.cpu = false; s.level = lv;
  if (d !== sn.dir) L.turn(s, 0, d);
}
// Take the computer's snake over (so a test can steer it).
function takeOver(s) { if (s.snakes[1].cpu) { s.snakes[1].cpu = false; s.snakes[1].queue = []; } }
// Player 2 crashes as soon as it can: down, left, up into its own body (or a wall on the way).
function suicide(s) {
  if (s.phase !== "play") return;
  const sn = s.snakes[1];
  if (sn.queue.length) return;
  const want = s.walls[8 * 24 + 5] && sn.dir === "right" ? "up" : { right: "down", down: "left", left: "up" }[sn.dir];
  if (want) L.turn(s, 1, want);
}

test("the board, the starts and a first round", () => {
  const s = L.create({ seed: 1 });
  assert.equal(s.mode, "cpu");
  assert.equal(s.level, 1);
  assert.equal(s.arenaName, L.LEVELS[0].name);
  assert.deepEqual(s.snakes[0].body.map((c) => [c.x, c.y]), [[18, 10], [19, 10], [20, 10], [21, 10]]);
  assert.deepEqual(s.snakes[1].body.map((c) => [c.x, c.y]), [[5, 9], [4, 9], [3, 9], [2, 9]]);
  assert.equal(s.snakes[0].dir, "left");
  assert.equal(s.snakes[1].dir, "right");
  assert.ok(s.snakes[1].cpu && !s.snakes[0].cpu);
  assert.equal(s.food.length, L.LEVELS[0].foods);
  assert.equal(s.phase, "ready");
  const evs = untilPlay(s);
  assert.deepEqual(types(evs), ["go"]);
  assert.equal(s.updates, L.READY_FIRST);
  assert.equal(L.create({ mode: "nope" }).mode, "cpu");
  assert.ok(!L.create({ mode: "two" }).snakes[1].cpu);
});

test("snakes move at the arena's speed, exactly", () => {
  const s = L.create({ seed: 2, mode: "two", levels: [arena({ speed: 8 }), arena({ speed: 8 })] });
  untilPlay(s);
  const x0 = s.snakes[0].body[0].x, x1 = s.snakes[1].body[0].x;
  run(s, 60);
  assert.equal(s.snakes[0].body[0].x, x0 - 8);
  assert.equal(s.snakes[1].body[0].x, x1 + 8);
});

test("turn queue: two quick turns both happen; opposite turns are ignored; early turns never add speed", () => {
  const s = L.create({ seed: 3, mode: "two", levels: [arena({ speed: 6 })] });
  untilPlay(s);
  assert.equal(L.turn(s, 0, "right"), false, "no reversing");
  assert.equal(L.turn(s, 0, "left"), false, "same direction");
  assert.ok(L.turn(s, 0, "up"));
  assert.ok(L.turn(s, 0, "right"));
  assert.equal(L.turn(s, 0, "left"), false);
  run(s, 20);
  assert.equal(s.snakes[0].dir, "right");
  // early turn: past half-way, the move happens at once
  const t = L.create({ seed: 3, mode: "two", levels: [arena({ speed: 6 })] });
  untilPlay(t);
  run(t, 6);       // 0.6 of a cell
  const hx = t.snakes[0].body[0].x;
  L.turn(t, 0, "up");
  assert.equal(t.snakes[0].body[0].y, 9, "moved up at once");
  assert.equal(t.snakes[0].body[0].x, hx);
  // a frame-perfect zigzag doesn't go faster than the speed (at most half a cell ahead)
  const z = L.create({ seed: 4, mode: "two", levels: [arena({ speed: 12 })] });
  untilPlay(z);
  let moves = 0;
  for (let i = 0; i < 120; i++) {
    const before = z.stats.moves;
    L.turn(z, 0, z.snakes[0].dir === "left" ? "up" : z.snakes[0].dir === "up" ? "left" : "up");
    L.step(z);
    moves += z.stats.moves - before;
    if (z.phase !== "play") break;
    assert.ok(z.stats.moves <= (i + 1) * 12 / 60 + 1, `moves ${z.stats.moves} after ${i + 1} updates`);
  }
  assert.ok(moves > 0);
  // keys: arrows steer player 1, W A S D player 2 (or player 1 against the computer)
  const k = L.create({ seed: 5, mode: "two" });
  L.press(k, "up2", true);
  assert.deepEqual(k.snakes[1].queue, ["up"]);
  L.press(k, "down", true);
  assert.deepEqual(k.snakes[0].queue, ["down"]);
  const c = L.create({ seed: 5 });
  L.press(c, "up2", true);
  assert.deepEqual(c.snakes[0].queue, ["up"]);
  assert.equal(L.turn(c, 1, "up"), false, "the computer's snake can't be steered");
});

test("eating: +10 for player 1, the snake grows, new food appears", () => {
  const s = L.create({ seed: 6, mode: "two", levels: [arena({ foods: 2 })] });
  untilPlay(s);
  s.food = [{ x: 17, y: 10 }, { x: 0, y: 0 }];
  const evs = run(s, 10);
  assert.ok(types(evs).includes("eat"));
  assert.equal(s.score, 10);
  assert.equal(s.stats.foods, 1);
  assert.equal(s.food.length, 2);
  run(s, 10);
  assert.equal(s.snakes[0].body.length, 5);
});

test("crashes: the wall, yourself, the other snake; head-on is a draw", () => {
  // player 2 into a wall
  const w = L.create({ seed: 7, mode: "two", levels: [arena()] });
  untilPlay(w);
  L.turn(w, 1, "up");
  let evs = run(w, 60 * 3);
  assert.ok(types(evs).includes("crash"));
  assert.equal(evs.find((e) => e.type === "crash").cause, "wall");
  const r = evs.find((e) => e.type === "round");
  assert.equal(r.winner, 1);
  assert.equal(w.score, L.ROUND_POINTS);
  // player 2 into itself
  const me = L.create({ seed: 7, mode: "two", levels: [arena()] });
  untilPlay(me);
  me.snakes[1].grow = 1;       // one longer, so its tail doesn't move out of the way
  L.turn(me, 1, "down"); run(me, 10); L.turn(me, 1, "left"); run(me, 10); L.turn(me, 1, "up");
  evs = run(me, 20);
  assert.equal(evs.find((e) => e.type === "crash").cause, "self");
  // player 1 into player 2's body
  const o = L.create({ seed: 8, mode: "two", levels: [arena()] });
  untilPlay(o);
  o.snakes[1].body = Array.from({ length: 8 }, (_, k) => ({ x: 15, y: 11 - k })); o.snakes[1].dir = "down";
  evs = run(o, 40);
  const crash = evs.find((e) => e.type === "crash");
  assert.equal(crash.player, 1);
  assert.equal(crash.cause, "snake");
  assert.equal(evs.find((e) => e.type === "round").winner, 2);
  // head-on: both crash, nobody wins the round
  const h = L.create({ seed: 9, mode: "two", levels: [arena()] });
  untilPlay(h);
  h.snakes[1].body = [{ x: 10, y: 10 }, { x: 9, y: 10 }, { x: 8, y: 10 }, { x: 7, y: 10 }];
  evs = run(h, 60 * 2);
  assert.equal(types(evs).filter((t) => t === "crash").length, 2);
  const hr = evs.find((e) => e.type === "round");
  assert.equal(hr.winner, 0);
  assert.equal(hr.cause, "head");
  assert.equal(h.score, 0);
  assert.equal(h.stats.draws, 1);
  // moving into the cell the other's tail is leaving is fine
  const t = L.create({ seed: 10, mode: "two", levels: [arena()] });
  untilPlay(t);
  t.snakes[1].body = [{ x: 16, y: 12 }, { x: 16, y: 11 }, { x: 17, y: 11 }, { x: 17, y: 10 }]; t.snakes[1].dir = "down";
  t.snakes[0].pu = 599; t.snakes[1].pu = 599;
  evs = L.step(t);
  assert.ok(!types(evs).includes("crash"), JSON.stringify(evs));
  assert.deepEqual(t.snakes[0].body[0], { x: 17, y: 10 });
});

test("a round lasts at most a minute: then the longer snake wins it", () => {
  const s = L.create({ seed: 11, mode: "two", levels: [arena()] });
  untilPlay(s);
  s.snakes[0].body.push({ x: 22, y: 10 });
  s.roundTime = L.ROUND_LIMIT - 1;
  const evs = L.step(s);
  const r = evs.find((e) => e.type === "round");
  assert.equal(r.cause, "time");
  assert.equal(r.winner, 1);
});

test("first to 3 rounds wins the match (500 × arena); then the next arena; the last one wins the game", () => {
  const two = [arena({ name: "One" }), arena({ name: "Two", speed: 7, foods: 2 })];
  const s = L.create({ seed: 12, levels: two });
  assert.equal(s.arenas.length, 2);
  const all = [];
  for (let i = 0; i < 60 * 600 && !s.over; i++) { takeOver(s); suicide(s); all.push(...L.step(s)); }   // the computer crashes at once
  assert.ok(s.over && s.won, `won (${s.stats.cause})`);
  assert.equal(s.stats.cause, "won");
  const ty = types(all);
  assert.equal(ty.filter((x) => x === "match").length, 2);
  assert.equal(ty.filter((x) => x === "level").length, 1);
  assert.equal(all.find((e) => e.type === "level").level, 2);
  assert.equal(ty[ty.length - 1], "win");
  assert.equal(s.level, 2);
  assert.equal(s.stats.matches, 2);
  assert.equal(s.stats.arenas, 2);
  assert.equal(s.score, s.stats.foods * 10 + 6 * L.ROUND_POINTS + L.MATCH_POINTS * (1 + 2));
  assert.equal(L.result(s).stats.won, true);
});

test("losing a match ends the game; Two players ends after one match either way", () => {
  const s = L.create({ seed: 13, levels: [arena(), arena()] });
  for (let i = 0; i < 60 * 600 && !s.over; i++) {
    if (s.phase === "play" && !s.snakes[0].queue.length && s.snakes[0].dir === "left") L.turn(s, 0, "up");   // into the top wall
    L.step(s);
  }
  assert.ok(s.over && !s.won);
  assert.equal(s.stats.cause, "lost");
  assert.equal(s.stats.matchesLost, 1);
  assert.equal(s.wins[1], 3);
  const t = L.create({ seed: 14, mode: "two", levels: [arena(), arena()] });
  for (let i = 0; i < 60 * 600 && !t.over; i++) { suicide(t); L.step(t); }
  assert.ok(t.over);
  assert.equal(t.stats.cause, "match");
  assert.equal(L.result(t).stats.winner, 1);
  assert.equal(t.stats.matches, 1);
  // two players play one arena of the list, picked by the seed
  const seen = new Set();
  for (let seed = 1; seed < 40; seed++) seen.add(L.create({ mode: "two", seed }).level);
  assert.ok(seen.size >= 5);
  for (const n of seen) assert.ok(n >= 1 && n <= L.LEVELS.length);
});

test("nine rounds at most: then the one with more round wins takes the match", () => {
  const s = L.create({ seed: 15, mode: "two", levels: [arena()] });
  s.round = L.MAX_ROUNDS; s.wins = [2, 1];
  untilPlay(s);
  L.turn(s, 0, "down"); L.turn(s, 1, "up");   // into the bottom and top walls at the same moment: a draw round
  const evs = run(s, 60 * 3);
  const r = evs.find((e) => e.type === "round");
  assert.equal(r.winner, 0);
  assert.equal(r.cause, "both");
  assert.equal(evs.find((e) => e.type === "match").winner, 1);
  assert.ok(s.over);
});

test("the computer avoids walls, grabs food, and gets better on later arenas", () => {
  const survive = (level, seed) => {
    const s = L.create({ seed, levels: Array.from({ length: 10 }, () => arena({ speed: 8, foods: 3 })) });
    if (level > 1) L.loadArena(s, level);
    s.snakes[0].cpu = true;     // player 1 parked: never moves (cpu snakes can't be steered), only the computer plays
    untilPlay(s);
    let n = 0;
    while (s.phase === "play" && n < 60 * 20) {
      s.snakes[0].pu = 0;          // player 1 stands still
      L.step(s); n++;
    }
    return { n, foods: s.stats.foodsOther };
  };
  let lowT = 0, highT = 0, foods = 0;
  for (let seed = 1; seed <= 6; seed++) {
    const a = survive(1, seed), b = survive(10, seed);
    lowT += a.n; highT += b.n; foods += b.foods;
  }
  assert.ok(highT >= 6 * 60 * 15, `the arena 10 computer survives (${highT / 6 / 60}s each)`);
  assert.ok(highT >= lowT, "later arenas are at least as good");
  assert.ok(foods >= 12, `it eats (${foods})`);
});

test("level lists: built-in or the session's; bad arenas are skipped", () => {
  assert.equal(L.usableLevels(undefined), L.LEVELS);
  assert.equal(L.LEVELS.length, 10);
  assert.equal(L.usableLevels(L.LEVELS).length, 10, "every built-in arena is usable");
  for (const a of L.LEVELS) {     // turned half-way round, the same
    for (let y = 0; y < 20; y++) for (let x = 0; x < 24; x++) assert.equal(a.walls[y][x], a.walls[19 - y][23 - x], a.name);
  }
  const blockedStart = OPEN.slice(); blockedStart[9] = "....#" + ".".repeat(19);
  const pocket = OPEN.slice(); pocket[0] = ".#" + ".".repeat(22); pocket[1] = "#" + ".".repeat(23);
  const crowded = OPEN.map((r, y) => (y === 9 || y === 10 ? r : "#".repeat(12) + ".".repeat(12)));
  const bad = [null, arena({ speed: 13 }), arena({ speed: 5 }), arena({ foods: 0 }), arena({ foods: 5 }), arena({ speed: 7.5 }),
    arena({ walls: OPEN.slice(1) }), arena({ walls: OPEN.map((r) => r + ".") }), arena({ walls: blockedStart }),
    arena({ walls: pocket }), arena({ walls: crowded }), arena({ walls: OPEN.map((r) => r.replace(".", "x")) }), arena({ name: 3 })];
  for (const b of bad) assert.equal(L.usableLevels([b]), L.LEVELS, JSON.stringify(b && b.walls ? b.walls[0] : b));
  const mine = L.usableLevels([bad[1], arena({ name: "Mine" })]);
  assert.equal(mine.length, 1);
  assert.equal(mine[0].name, "Mine");
  const s = L.create({ seed: 1, levels: [arena({ name: "Mine", speed: 9, foods: 4 })] });
  assert.equal(s.arenas.length, 1);
  assert.equal(s.cps, 9);
  assert.equal(s.food.length, 4);
  assert.equal(L.create({ seed: 1, levels: [bad[1]] }).arenas, L.LEVELS);
  // walls block and food never lands on them
  const wl = OPEN.slice(); wl[0] = "#".repeat(24);
  const w = L.create({ seed: 2, levels: [arena({ walls: wl, foods: 4 })] });
  assert.ok(L.isWall(w, 3, 0) && !L.isWall(w, 3, 1));
  for (let i = 0; i < 200; i++) { w.food = []; for (let k = 0; k < 4; k++) L.placeFood(w); for (const f of w.food) assert.ok(f.y > 0); }
});

test("save and restore: the game carries on identically, with the list kept out of the save", () => {
  for (const mode of ["cpu", "two"]) {
    const levels = [arena({ name: "A", speed: 9, foods: 2 }), arena({ name: "B", speed: 10 })];
    const s = L.create({ seed: 21, mode, levels });
    run(s, 400, (x) => { greedy(x); if (mode === "two") suicide(x); });
    const data = JSON.parse(JSON.stringify(L.save(s)));
    assert.ok(!("arenas" in data));
    const c = L.restore(data, levels);
    assert.equal(c.arenas.length, 2);
    for (let i = 0; i < 1500 && !s.over; i++) {
      greedy(s); greedy(c);
      if (mode === "two") { suicide(s); suicide(c); }
      const a = L.step(s), b = L.step(c);
      assert.deepEqual(types(b), types(a));
    }
    assert.equal(c.score, s.score);
    assert.deepEqual(c.snakes[0].body, s.snakes[0].body);
    assert.deepEqual(L.result(c), L.result(s));
  }
  // an old save without walls takes them from the list
  const s = L.create({ seed: 22 });
  const d = L.save(s); delete d.walls;
  assert.equal(L.restore(d).walls.length, 24 * 20);
  assert.throws(() => L.restore({ snakes: 3 }), /can't be continued/);
  assert.throws(() => L.restore(null), /can't be continued/);
  assert.throws(() => L.restore(Object.assign(L.save(s), { mode: "solo" })), /can't be continued/);
});

test("deterministic for a seed", () => {
  const play = (seed) => {
    const s = L.create({ seed });
    for (let i = 0; i < 60 * 120 && !s.over; i++) { greedy(s); L.step(s); }
    return JSON.stringify(L.result(s)) + s.updates + JSON.stringify(s.snakes);
  };
  assert.equal(play(31), play(31));
  assert.notEqual(play(31), play(32));
});

test("honest scores: the fastest play stays inside the limits (also on the hardest arenas allowed)", () => {
  let fastest = 0;
  const check = (s) => {
    const sec = s.updates / 60;
    assert.ok(s.score <= sec * PER_SECOND + BASE, `score ${s.score} after ${sec.toFixed(2)}s`);
    assert.ok(s.score <= MAX_SCORE);
    assert.ok(s.level <= 100);
    if (sec > 5) fastest = Math.max(fastest, (s.score - BASE) / sec);
  };
  // 1. the other snake crashes as soon as it can in every round, player 1 grabs food: every match won quickly
  for (const levels of [HARDEST, QUICK, undefined]) {
    for (let seed = 1; seed <= 4; seed++) {
      const s = L.create({ seed, levels });
      while (!s.over) { takeOver(s); suicide(s); greedy(s); L.step(s); check(s); }
      assert.ok(s.won);
    }
  }
  // 2. two players on the hardest arenas (arena 10+ is worth the most)
  for (let seed = 1; seed <= 10; seed++) {
    const s = L.create({ seed, mode: "two", levels: HARDEST });
    while (!s.over) { suicide(s); greedy(s); L.step(s); check(s); }
  }
  // 3. long rounds of eating: player 1 against the computer at top speed with four foods
  for (let seed = 1; seed <= 3; seed++) {
    const s = L.create({ seed, levels: HARDEST });
    for (let i = 0; i < 60 * 600 && !s.over; i++) { greedy(s); L.step(s); check(s); }
  }
  // 4. a frame-perfect zigzag (early turns every update) never outruns the limit
  const z = L.create({ seed: 5, mode: "two", levels: HARDEST });
  for (let i = 0; i < 60 * 120 && !z.over; i++) { L.turn(z, 0, i % 2 ? "up" : "down"); L.turn(z, 0, "left"); suicide(z); L.step(z); check(z); }
  console.log(`  snakeduel: fastest bot ${fastest.toFixed(0)} points a second`);
});

for (const mode of ["cpu", "two"]) {
  for (const look of LOOKS) {
    test(`renders and plays in the ${look} look (${mode})`, () => {
      const sb = makeSandbox({ extra: ["snakeduel-logic.js", "snakeduel.js"] });
      const canvas = sb.canvas(390, 487);
      const ends = [];
      const def = sb.win.ArcadeGames.get("snakeduel");
      assert.equal(def.name, "Snake Duel");
      assert.equal(def.controls, "touch");
      assert.equal(def.players, 2);
      assert.equal(def.stateVersion, 1);
      assert.deepEqual(JSON.parse(JSON.stringify(def.modes.map((m) => m.id))), ["cpu", "two"]);
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      for (let i = 0; i < 600; i++) {
        if (i % 17 === 0) inst.input(["up", "left", "down", "right"][(i / 17) % 4], true);
        if (i % 23 === 0) inst.input(["up2", "right2", "down2", "left2"][(i / 23) % 4], true);
        if (i % 29 === 0) {      // two fingers at once, one on each half
          inst.pointer("down", 60, 200); inst.pointer("down", 330, 220);
          inst.pointer("move", 60, 240); inst.pointer("move", 300, 220);
          inst.pointer("up", 60, 240); inst.pointer("up", 300, 220);
        }
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
      if (inst.state === "running") {
        const saved = inst.save();
        assert.ok(saved && saved.state);
      }
      inst.destroy();
      assert.equal(sb.pending(), 0);
    });
  }
}

test("touch: each finger steers the player of the half it began on", () => {
  const sb = makeSandbox({ extra: ["snakeduel-logic.js", "snakeduel.js"] });
  const canvas = sb.canvas(390, 487);
  const inst = sb.win.ArcadeGames.get("snakeduel").create(canvas, { mode: "two", seed: 3 });
  inst.start();
  const s = inst.logic;
  // both fingers down, then their moves interleaved
  inst.pointer("down", 80, 200);          // left half: blue, player 2
  inst.pointer("down", 300, 200);         // right half: green, player 1
  inst.pointer("move", 80, 230);          // player 2 swipes down
  inst.pointer("move", 300, 170);         // player 1 swipes up
  assert.deepEqual(JSON.parse(JSON.stringify(s.snakes[1].queue)), ["down"]);
  assert.deepEqual(JSON.parse(JSON.stringify(s.snakes[0].queue)), ["up"]);
  inst.pointer("move", 50, 230);          // player 2 keeps swiping: a second turn queued
  assert.deepEqual(JSON.parse(JSON.stringify(s.snakes[1].queue)), ["down", "left"]);
  inst.pointer("up", 50, 230);
  inst.pointer("move", 330, 170);         // player 1's finger still followed
  assert.deepEqual(JSON.parse(JSON.stringify(s.snakes[0].queue)), ["up", "right"]);
  inst.pointer("up", 330, 170);
  // against the computer, any finger steers you
  const sb2 = makeSandbox({ extra: ["snakeduel-logic.js", "snakeduel.js"] });
  const c = sb2.win.ArcadeGames.get("snakeduel").create(sb2.canvas(390, 487), { mode: "cpu", seed: 3 });
  c.start();
  c.pointer("down", 40, 200); c.pointer("move", 40, 240);
  assert.deepEqual(JSON.parse(JSON.stringify(c.logic.snakes[0].queue)), ["down"]);
  inst.destroy(); c.destroy();
});
