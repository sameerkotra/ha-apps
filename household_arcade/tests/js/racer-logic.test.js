"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const R = loadLogic("racer-logic.js");

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...R.step(s)); } return all; }
// A careful driver: heads for the open lane of the next row that hasn't been passed yet.
function driver(s) {
  const ahead = s.cars.filter((c) => R.screenY(s, c.d) < R.CAR_Y + R.CAR_H && !c.passed);
  if (!ahead.length) return;
  const nextD = Math.min(...ahead.map((c) => c.d));
  const blocked = new Set(ahead.filter((c) => c.d === nextD).map((c) => c.lane));
  let target = s.lane, bestDist = 99;
  for (let l = 0; l < s.lanes; l++) if (!blocked.has(l) && Math.abs(l - s.lane) < bestDist) { bestDist = Math.abs(l - s.lane); target = l; }
  if (target < s.lane) R.press(s, "left", true);
  else if (target > s.lane) R.press(s, "right", true);
}

test("modes: lanes, lives and starting speed", () => {
  const three = R.create({ mode: "three", seed: 1 }), four = R.create({ mode: "four", seed: 1 }), rush = R.create({ mode: "rush", seed: 1 });
  assert.equal(three.lanes, 3); assert.equal(four.lanes, 4); assert.equal(rush.lanes, 3);
  assert.equal(three.lives, 3); assert.equal(rush.lives, 1);
  assert.ok(R.speed(rush) > R.speed(three));
  assert.equal(three.lane, 1);
});

test("changing lanes moves the car over smoothly, not past the road's edge", () => {
  const s = R.create({ seed: 2 });
  const x0 = s.x;
  assert.deepEqual(types(R.press(s, "left", true)), ["turn"]);
  R.step(s);
  assert.ok(s.x < x0 && s.x > R.laneX(s, 0));
  run(s, R.CHANGE);
  assert.equal(s.x, R.laneX(s, 0));
  assert.deepEqual(R.press(s, "left", true), []);
  assert.equal(s.lane, 0);
});

test("score: 1 for each 10 px of road, 25 a coin; faster every level", () => {
  const s = R.create({ seed: 3 });
  s.cars = []; s.nextRow = 1e9;
  run(s, 100);
  assert.equal(s.score, Math.floor(s.dist / 10));
  s.coins.push({ lane: s.lane, d: s.dist + 3 });
  const evs = run(s, 2);
  assert.ok(types(evs).includes("coin"));
  assert.equal(s.score, Math.floor(s.dist / 10) + R.COIN_POINTS);
  const v1 = R.speed(s);
  s.dist = R.LEVEL_DIST - 1;
  assert.ok(types(run(s, 2)).includes("level"));
  assert.ok(R.speed(s) > v1);
});

test("every row leaves an open lane next to the last one, with room to reach it", () => {
  for (const mode of ["three", "four", "rush"]) {
    const s = R.create({ seed: 4, mode });
    let lastOpen = s.safeLane, prevFree = null;
    for (let i = 0; i < 300; i++) {
      s.cars = [];
      R.spawnRow(s, 1000 + i * 200);
      const row = s.cars;
      const lanes = new Set(row.map((c) => c.lane));
      assert.ok(lanes.size <= s.lanes - 1, "never a full row");
      assert.ok(!lanes.has(s.safeLane) && Math.abs(s.safeLane - lastOpen) <= 1);
      for (const f of prevFree || []) {
        assert.ok([f - 1, f, f + 1].some((q) => q >= 0 && q < s.lanes && !lanes.has(q)), "every open lane has a way on");
      }
      prevFree = s.freeLanes.slice();
      lastOpen = s.safeLane;
    }
    assert.ok(R.rowGap(R.speed(s)) > R.TRUCK_H + R.CAR_H + R.CHANGE * R.speed(s));
  }
});

test("a crash costs a life and clears nearby traffic; the last life ends the game", () => {
  const s = R.create({ seed: 5 });
  s.cars = [{ lane: s.lane, d: s.dist + 2, h: R.CAR_H, ci: 1, passed: false }]; s.nextRow = 1e9;
  let evs = run(s, 1);
  assert.ok(types(evs).includes("crash"));
  assert.equal(s.lives, 2);
  assert.equal(s.cars.length, 0);
  assert.equal(s.safe, R.SAFE_UPDATES);
  s.cars = [{ lane: s.lane, d: s.dist + 2, h: R.CAR_H, ci: 1, passed: false }];
  assert.ok(!types(run(s, 1)).includes("crash"), "no second crash straight away");
  const r = R.create({ mode: "rush", seed: 5 });
  r.cars = [{ lane: r.lane, d: r.dist + 2, h: R.CAR_H, ci: 1, passed: false }]; r.nextRow = 1e9;
  evs = run(r, 1);
  assert.ok(r.over && types(evs).includes("gameover"));
});

test("deterministic for a seed; a careful driver lasts; scores stay inside the honest-score limits", () => {
  const play = (seed, mode) => {
    const s = R.create({ seed, mode });
    let u = 0;
    while (!s.over && u++ < 60 * 600) {
      driver(s);
      R.step(s);
      assert.ok(s.score <= (s.updates / 60) * 150 + 300, `score ${s.score} after ${s.updates / 60}s`);
    }
    return { key: JSON.stringify(R.result(s)) + s.updates, s };
  };
  const a = play(41, "four");
  assert.equal(a.key, play(41, "four").key);
  assert.ok(a.s.updates >= 60 * 600 && a.s.stats.crashes === 0, `a driver who takes any open lane never has to crash (${a.s.stats.crashes})`);
  assert.equal(a.s.level, R.MAX_LEVEL, "the level stops at the top");
  play(42, "rush");
});

test("save and restore carry on the same game", () => {
  const s = R.create({ seed: 9 });
  for (let i = 0; i < 500; i++) { driver(s); R.step(s); }
  const copy = R.restore(JSON.parse(JSON.stringify(R.save(s))));
  for (let i = 0; i < 500 && !s.over; i++) { driver(s); driver(copy); R.step(s); R.step(copy); }
  assert.equal(copy.score, s.score);
  assert.deepEqual(copy.cars, s.cars);
  assert.throws(() => R.restore({ cars: 1 }));
});

// ---------------------------------------------------------------------------
// Stages: the level list
// ---------------------------------------------------------------------------
const { makeSandbox } = require("./helpers");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const TWO = [
  { name: "Short hop", lanes: 3, speed: 4, rows: 15, traffic: 0.3, trucks: 0.1, coins: 0.5 },
  { name: "Wide road", lanes: 4, speed: 5, rows: 16, traffic: 0.6, trucks: 0.3, coins: 0.6 },
];

test("Stages: built-in or the session's list; bad stages are skipped", () => {
  assert.equal(R.usableLevels(undefined), R.LEVELS);
  for (const bad of [{ ...TWO[0], lanes: 5 }, { ...TWO[0], speed: 8 }, { ...TWO[0], rows: 10 }, { ...TWO[0], rows: 20.5 },
    { ...TWO[0], traffic: 1 }, { ...TWO[0], trucks: 0.6 }, { ...TWO[0], coins: 0.9 }, { ...TWO[0], name: null }]) {
    assert.equal(R.usableLevels([bad]), R.LEVELS, JSON.stringify(bad));
  }
  assert.equal(R.usableLevels([TWO[0], { name: "x" }]).length, 1);
  const s = R.create({ mode: "stages", seed: 1, levels: TWO });
  assert.equal(s.stages.length, 2);
  assert.equal(s.lanes, 3);
  assert.equal(R.speed(s), 4);
  assert.equal(s.lives, 3);
  assert.equal(R.create({ mode: "stages", seed: 1, levels: [TWO[1]] }).lanes, 4);
});

test("Stages: the traffic numbers shape the rows, which always leave a way through", () => {
  const full = { name: "Full", lanes: 4, speed: 7.5, rows: 80, traffic: 0.9, trucks: 0.5, coins: 0.8 };
  const light = { name: "Light", lanes: 4, speed: 3, rows: 80, traffic: 0.2, trucks: 0, coins: 0 };
  const count = (lv) => {
    const s = R.create({ mode: "stages", seed: 4, levels: [lv] });
    let cars = 0, trucks = 0, coins = 0, prevFree = null;
    for (let i = 0; i < 70; i++) {
      s.cars = []; s.coins = [];
      R.spawnRow(s, 1000 + i * 300);
      const lanes = new Set(s.cars.map((c) => c.lane));
      assert.ok(lanes.size <= s.lanes - 1, "never a full row");
      for (const f of prevFree || []) assert.ok([f - 1, f, f + 1].some((q) => q >= 0 && q < s.lanes && !lanes.has(q)), "a way on");
      prevFree = s.freeLanes.slice();
      cars += s.cars.length; trucks += s.cars.filter((c) => c.truck).length; coins += s.coins.length;
    }
    return { cars, trucks, coins };
  };
  const a = count(full), b = count(light);
  assert.ok(a.cars > b.cars * 1.4, `fuller rows (${a.cars} vs ${b.cars})`);
  assert.ok(a.trucks > 0 && b.trucks === 0);
  assert.ok(a.coins > 30 && b.coins === 0);
});

test("Stages: the finish line after the rows; the next stage (its lanes and speed); the last one wins", () => {
  const s = R.create({ mode: "stages", seed: 6, levels: TWO });
  let evs = run(s, 60 * 120, driver);
  assert.ok(types(evs).includes("level"), "the next stage");
  assert.ok(s.over && s.won, `won (crashes ${s.stats.crashes})`);
  assert.ok(types(evs).includes("win"));
  assert.equal(s.level, 2);
  assert.equal(s.lanes, 4);
  assert.equal(s.stagePassed, 16);
  const r = R.result(s);
  assert.equal(r.stats.won, true);
  assert.equal(r.stats.cause, "won");
  assert.equal(r.stats.stages, 2);
  assert.equal(r.stats.mode, "stages");
  // lives last for the whole run
  const t = R.create({ mode: "stages", seed: 6, levels: TWO });
  t.lives = 1;
  run(t, 300, driver);
  assert.ok(!t.over);
  t.cars = [{ lane: t.lane, d: t.dist + 2, h: R.CAR_H, ci: 1, passed: false }];
  t.safe = 0; t.x = R.laneX(t, t.lane);
  evs = run(t, 1);
  assert.ok(t.over && !t.won && types(evs).includes("gameover"));
  assert.equal(R.result(t).stats.cause, "crash");
});

test("Stages: a stage's rows are counted down to the finish", () => {
  const s = R.create({ mode: "stages", seed: 7, levels: TWO });
  run(s, 600, driver);
  assert.ok(s.stagePassed > 0 && s.stagePassed < 15, `${s.stagePassed}`);
  assert.equal(s.stageRows, 15);
  assert.ok(s.finishD > s.dist);
  assert.ok(s.cars.every((c) => c.d < s.finishD));
});

test("Stages: save and restore keep going with the list (kept out of the save)", () => {
  const s = R.create({ mode: "stages", seed: 9, levels: TWO });
  for (let i = 0; i < 500; i++) { driver(s); R.step(s); }
  const data = JSON.parse(JSON.stringify(R.save(s)));
  assert.ok(!("stages" in data));
  const copy = R.restore(data, TWO);
  assert.equal(copy.stages.length, 2);
  for (let i = 0; i < 4000 && !s.over; i++) { driver(s); driver(copy); R.step(s); R.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.level, s.level);
  assert.equal(copy.won, s.won);
  assert.deepEqual(copy.cars, s.cars);
  // a save from before Stages still continues
  const old = R.save(R.create({ seed: 2 }));
  delete old.stageRows; delete old.stagePassed; delete old.finishD; delete old.banner; delete old.won; delete old.stages; delete old.stats.stages;
  const back = R.restore(old);
  assert.equal(back.stages, null);
  for (let i = 0; i < 200; i++) { driver(back); R.step(back); }
  assert.equal(R.result(back).stats.stages, 0);
});

test("Stages: the hardest stages the fields allow stay inside the honest-score limits", () => {
  const fast = Array.from({ length: 30 }, (_, i) => ({ name: "Zoom", lanes: i % 2 ? 4 : 3, speed: 7.5, rows: 15 + (i % 3) * 30, traffic: 0.9, trucks: 0.5, coins: 0.8 }));
  for (const [seed, levels] of [[41, fast], [42, R.LEVELS]]) {
    const s = R.create({ mode: "stages", seed, levels });
    let u = 0;
    while (!s.over && u++ < 60 * 1200) {
      driver(s);
      R.step(s);
      assert.ok(s.score <= (s.updates / 60) * 150 + 300, `score ${s.score} after ${s.updates / 60}s`);
      assert.ok(s.score <= 3000000);
    }
    assert.ok(s.level <= levels.length);
    assert.ok(s.won, `a careful driver finishes every stage (stage ${s.level}, crashes ${s.stats.crashes})`);
  }
  // all coins, no traffic in the way: the fastest score there is
  const s = R.create({ mode: "stages", seed: 43, levels: fast });
  while (!s.over) {
    s.cars = [];
    if (s.coins.length) { const c = s.coins.reduce((a, b) => (a.d < b.d ? a : b)); s.lane = c.lane; s.x = R.laneX(s, c.lane); }
    R.step(s);
    assert.ok(s.score <= (s.updates / 60) * 150 + 300, `score ${s.score} after ${s.updates / 60}s`);
  }
  assert.ok(s.won && s.stats.coins > 500, `${s.stats.coins} coins, ${Math.round(s.score / (s.updates / 60))} a second`);
});

test("Stages: draws in every look with a list, the finish line and the banners", () => {
  for (const look of LOOKS) {
    const sb = makeSandbox();
    const canvas = sb.canvas(390, 487);
    const inst = sb.win.ArcadeGames.get("racer").create(canvas, { mode: "stages", look, seed: 5, levels: [{ ...TWO[0], name: "A very long stage name here" }, TWO[1]] });
    inst.start();
    for (let i = 0; i < 700; i++) {
      if (i % 30 === 0) { inst.input(i % 60 ? "left" : "right", true); inst.input(i % 60 ? "left" : "right", false); inst.pointer("down", 300, 200); }
      sb.frames(1);
    }
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
  }
});
