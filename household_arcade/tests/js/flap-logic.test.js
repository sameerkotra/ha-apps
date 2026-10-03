"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const F = loadLogic("flap-logic.js");

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...F.step(s)); } return all; }
// A steady pilot: flaps when it's below the middle of the next gap and falling.
function pilot(s) {
  const next = s.gates.find((g) => g.x + F.GATE_W > F.X - F.R);
  const aim = next ? F.gateMid(s, next) + 10 : (F.TOP + F.GROUND) / 2;
  if (s.y > aim && s.vy > 0) F.flap(s);
}

test("it waits for the first flap", () => {
  const s = F.create({ seed: 1 });
  const y = s.y, x = s.gates[0].x;
  run(s, 300);
  assert.equal(s.y, y);
  assert.equal(s.gates[0].x, x);
  assert.ok(!s.over);
  assert.deepEqual(types(F.press(s, "fire", true)), ["flap"]);
  assert.ok(s.started);
});

test("gravity pulls it down; a flap sends it up; the top stops it", () => {
  const s = F.create({ seed: 2 });
  F.flap(s);
  const y0 = s.y;
  F.step(s);
  assert.ok(s.y < y0);
  run(s, 40);
  assert.ok(s.vy > 0, "falling again");
  for (let i = 0; i < 60; i++) { F.flap(s); F.step(s); }
  assert.ok(s.y >= F.TOP + F.R);
});

test("touching the ground ends the game", () => {
  const s = F.create({ seed: 3 });
  F.flap(s);
  const evs = run(s, 400);
  assert.ok(s.over);
  const end = evs.find((e) => e.type === "gameover");
  assert.ok(end);
  assert.equal(F.result(s).stats.cause, s.y + F.R >= F.GROUND ? "ground" : "gate");
});

test("passing a gate scores 1; hitting one ends the game", () => {
  const s = F.create({ seed: 4 });
  F.flap(s);
  const evs = run(s, 60 * 20, pilot);
  assert.ok(types(evs).includes("gate"));
  assert.ok(s.score >= 5, `score ${s.score}`);
  const t = F.create({ seed: 4 });
  F.flap(t);
  t.gates[0].x = F.X - 4; t.gates[0].mid = F.GROUND - 40; t.y = F.TOP + 30; t.vy = 0;
  run(t, 2);
  assert.ok(t.over);
  assert.equal(F.result(t).stats.cause, "gate");
});

test("every 10 gates: a new level with narrower gaps and quicker gates; Easy is roomier", () => {
  const s = F.create({ seed: 5 });
  const g1 = F.gap(s), v1 = F.speed(s);
  s.level = 3;
  assert.ok(F.gap(s) < g1 && F.speed(s) > v1);
  assert.ok(F.gap(F.create({ mode: "easy" })) > g1);
  s.level = 99;
  assert.ok(F.gap(s) >= F.MODES.normal.minGap && F.speed(s) <= 2.6);
});

test("Moving gates sway but their gaps stay inside the screen", () => {
  const s = F.create({ seed: 6, mode: "moving" });
  F.flap(s);
  const g = s.gates[0], seen = new Set();
  for (let i = 0; i < 200; i++) { s.t = i * 7; const m = F.gateMid(s, g); seen.add(Math.round(m)); assert.ok(m - g.gap / 2 >= F.TOP && m + g.gap / 2 <= F.GROUND); }
  assert.ok(seen.size > 5);
});

test("deterministic for a seed; a steady pilot goes far; scores stay inside the honest-score limits", () => {
  const play = (seed, mode) => {
    const s = F.create({ seed, mode });
    F.flap(s);
    let u = 0;
    while (!s.over && u++ < 60 * 600) {
      pilot(s);
      F.step(s);
      assert.ok(s.score <= (s.updates / 60) * 2 + 5, `score ${s.score} after ${s.updates / 60}s`);
    }
    return { key: JSON.stringify(F.result(s)) + s.updates, s };
  };
  const a = play(51, "normal");
  assert.equal(a.key, play(51, "normal").key);
  assert.ok(a.s.score >= 30, `score ${a.s.score}`);
  play(52, "moving");
});

test("save and restore: the continued game waits for a flap, then plays the same", () => {
  const s = F.create({ seed: 7 });
  F.flap(s);
  run(s, 300, pilot);
  const copy = F.restore(JSON.parse(JSON.stringify(F.save(s))));
  assert.equal(copy.started, false);
  const y = copy.y; F.step(copy); assert.equal(copy.y, y);
  F.flap(s); F.flap(copy);
  for (let i = 0; i < 300 && !s.over; i++) { pilot(s); pilot(copy); F.step(s); F.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.y, s.y);
  assert.throws(() => F.restore({ gates: 3 }));
});

test("Courses: built-in or the session's list; the end of a course is the next level; the last one wins", () => {
  assert.equal(F.usableLevels(undefined), F.LEVELS);
  assert.equal(F.usableLevels([{ name: "Bad", gates: 2, gap: 90, speed: 1.5, sway: 0, spacing: 120, heights: "11" }]), F.LEVELS);
  const two = [
    { name: "A", gates: 8, gap: 100, speed: 1.4, sway: 0, spacing: 140, heights: "4444" },
    { name: "B", gates: 8, gap: 100, speed: 1.6, sway: 0, spacing: 140, heights: "5555" },
  ];
  assert.equal(F.usableLevels([two[0], { name: "Steep", gates: 8, gap: 90, speed: 1.5, sway: 0, spacing: 120, heights: "0909" }]).length, 1);
  const s = F.create({ mode: "course", seed: 3, levels: two });
  assert.equal(s.courses.length, 2);
  F.flap(s);
  const evs = run(s, 60 * 120, pilot);
  assert.ok(types(evs).includes("level"));
  assert.ok(s.won && s.over, `won (score ${s.score}, cause ${s.stats.cause})`);
  assert.equal(s.score, 16);
  assert.equal(s.level, 2);
  assert.equal(F.result(s).stats.courses, 2);
  // saved mid-course, continued with the same list
  const t = F.create({ mode: "course", seed: 3, levels: two });
  F.flap(t); run(t, 400, pilot);
  const back = F.restore(JSON.parse(JSON.stringify(F.save(t))), two);
  assert.equal(back.courses.length, 2);
  assert.ok(!("courses" in F.save(t)));
});
