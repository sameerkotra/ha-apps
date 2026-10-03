"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const D = loadLogic("duel-logic.js");

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...D.step(s)); } return all; }
// a good player: meets the ball where it will land, a little off centre for an angle
function player(s) { if (s.ball.vy > 0) D.setTarget(s, D.predictX(s, D.PY - D.BALL_R) + Math.sin(s.updates / 37) * 14); }

test("the ball is served after a short wait, or at once with Space", () => {
  const s = D.create({ seed: 1 });
  assert.equal(s.serve, D.SERVE_DELAY);
  let evs = run(s, D.SERVE_DELAY - 1);
  assert.equal(s.ball.vy, 0);
  evs = run(s, 1);
  assert.ok(types(evs).includes("launch"));
  assert.ok(s.ball.vy > 0, "the first serve comes to you");
  const t = D.create({ seed: 1 });
  D.press(t, "fire", true);
  assert.ok(types(run(t, 1)).includes("launch"));
});

test("your paddle follows the pointer and the keys, inside the walls", () => {
  const s = D.create({ seed: 2 });
  D.setTarget(s, 20);
  run(s, 10);
  assert.ok(Math.abs(s.paddle.x - (D.WALL_L + D.PW / 2)) < 0.01);
  D.press(s, "right", true);
  run(s, 30);
  assert.ok(s.paddle.x > 100);
  D.press(s, "right", false);
});

test("returning the ball scores 10 and sends it back up; the angle comes from where it hits", () => {
  const s = D.create({ seed: 3 });
  D.press(s, "fire", true);
  let evs = [];
  for (let i = 0; i < 400 && !types(evs).includes("paddle"); i++) { player(s); evs = D.step(s); }
  assert.ok(types(evs).includes("paddle"));
  assert.equal(s.score, D.RETURN_POINTS);
  assert.ok(s.ball.vy < 0);
  // hitting the edge sends it off at a steep angle
  const t = D.create({ seed: 3 });
  t.serve = 0; t.ball = { x: 120, y: D.PY - 10, vx: 0, vy: 3, speed: 3 };
  t.paddle.x = 120 - D.PW / 2 + 2;
  D.step(t); D.step(t); D.step(t);
  assert.ok(t.ball.vy < 0 && Math.abs(t.ball.vx) > Math.abs(t.ball.vy) * 0.9, "steep");
});

test("a missed ball is the computer's point; past the computer is yours (100 × match)", () => {
  const s = D.create({ seed: 4 });
  s.serve = 0; s.ball = { x: 10, y: 280, vx: 0, vy: 4, speed: 4 }; s.paddle.x = 200;
  let evs = run(s, 20);
  assert.ok(types(evs).includes("miss"));
  assert.equal(s.cpu, 1);
  s.serve = 0; s.ball = { x: 120, y: 60, vx: 0, vy: -4, speed: 4 }; s.cpuX = 200; s.cpuAim = 200;
  evs = run(s, 20);
  assert.ok(types(evs).includes("point"));
  assert.equal(s.me, 1);
  assert.equal(s.score, D.POINT_POINTS);
});

test("first to 5 wins the match: a quicker opponent; losing a match ends the game", () => {
  const s = D.create({ seed: 5 });
  s.me = D.TO_WIN - 1;
  s.serve = 0; s.ball = { x: 120, y: 60, vx: 0, vy: -4, speed: 4 }; s.cpuX = 200; s.cpuAim = 200;
  const evs = run(s, 20);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.level, 2);
  assert.equal(s.me, 0);
  assert.equal(s.score, D.POINT_POINTS + D.MATCH_POINTS);
  assert.ok(D.cpuSpeed(s) > D.cpuSpeed(D.create({ seed: 5 })));
  const t = D.create({ seed: 5 });
  t.cpu = D.TO_WIN - 1;
  t.serve = 0; t.ball = { x: 10, y: 280, vx: 0, vy: 4, speed: 4 }; t.paddle.x = 200;
  assert.ok(types(run(t, 20)).includes("gameover"));
  assert.ok(t.over);
  assert.equal(D.result(t).stats.won, false);
});

test("beating the tenth opponent wins the game", () => {
  const s = D.create({ seed: 6 });
  s.level = D.OPPONENTS; s.me = D.TO_WIN - 1;
  s.serve = 0; s.ball = { x: 120, y: 60, vx: 0, vy: -4, speed: 4 }; s.cpuX = 200; s.cpuAim = 200;
  assert.ok(types(run(s, 20)).includes("win"));
  assert.ok(s.over && D.result(s).stats.won);
});

test("the ball never passes through a paddle at top speed", () => {
  const s = D.create({ seed: 7 });
  s.serve = 0; s.paddle.x = 120;
  s.ball = { x: 120, y: 200, vx: 0, vy: D.MAX_SPEED, speed: D.MAX_SPEED };
  const evs = run(s, 30);
  assert.ok(types(evs).includes("paddle"));
});

test("deterministic for a seed; rallies end; scores stay inside the honest-score limits", () => {
  const play = (seed, mode) => {
    const s = D.create({ seed, mode });
    let u = 0;
    while (!s.over && u++ < 60 * 1800) {
      player(s);
      if (s.serve > 0) D.press(s, "fire", true);
      D.step(s);
      assert.ok(s.score <= (s.updates / 60) * 3500 + 2000, `score ${s.score} after ${s.updates / 60}s`);
    }
    assert.ok(s.over, "the game ends");
    return { key: JSON.stringify(D.result(s)) + s.updates, s };
  };
  const a = play(31, "hard");
  assert.equal(a.key, play(31, "hard").key);
  assert.ok(a.s.level >= 3, `a good player gets past a few opponents (${a.s.level})`);
  play(32, "easy");
});

test("save and restore carry on the same game", () => {
  const s = D.create({ seed: 8 });
  for (let i = 0; i < 400; i++) { player(s); D.step(s); }
  const copy = D.restore(JSON.parse(JSON.stringify(D.save(s))));
  for (let i = 0; i < 400 && !s.over; i++) { player(s); player(copy); D.step(s); D.step(copy); }
  assert.equal(copy.score, s.score);
  assert.deepEqual(copy.ball, s.ball);
  assert.throws(() => D.restore({ ball: {} }));
});

// ---------------------------------------------------------------------------
// Opponents: the level list (every mode plays it)
// ---------------------------------------------------------------------------
const { makeSandbox } = require("./helpers");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const TWO = [
  { name: "Pip", speed: 1.2, miss: 0.4, aim: 0.5, ball: 2.2, toWin: 3 },
  { name: "Max", speed: 4.4, miss: 0.05, aim: 0.9, ball: 3.6, toWin: 4 },
];
function winPoint(s) { s.serve = 0; s.ball = { x: 120, y: 60, vx: 0, vy: -4, speed: 4 }; s.cpuX = 200; s.cpuAim = 200; return run(s, 20); }

test("Opponents: the built-in ten are today's numbers; a session list replaces them; bad ones are skipped", () => {
  assert.equal(D.usableLevels(undefined), D.LEVELS);
  assert.equal(D.LEVELS.length, D.OPPONENTS);
  assert.deepEqual([...D.NAMES], ["Rookie", "Rally", "Swift", "Wall", "Spin", "Echo", "Blaze", "Ace", "Storm", "Champion"]);
  for (let lv = 1; lv <= 10; lv++) {
    const s = D.create({ seed: 1 }); s.level = lv;
    assert.ok(Math.abs(D.cpuSpeed(s) - Math.min(4.4, 1.7 + 0.24 * (lv - 1))) < 1e-9);
    assert.ok(Math.abs(D.missChance(s) - (0.3 - 0.024 * (lv - 1))) < 1e-9);
    assert.ok(Math.abs(D.opponent(s).aim - Math.min(0.9, 0.5 + 0.04 * lv)) < 1e-9);
    assert.ok(Math.abs(D.opponent(s).ball - (2.4 + 0.12 * (lv - 1))) < 1e-9);
  }
  for (const bad of [{ ...TWO[0], speed: 5 }, { ...TWO[0], miss: 0.6 }, { ...TWO[0], aim: 0.2 }, { ...TWO[0], ball: 4 },
    { ...TWO[0], toWin: 2 }, { ...TWO[0], toWin: 3.5 }, { ...TWO[0], name: 7 }, null]) {
    assert.equal(D.usableLevels([bad]), D.LEVELS, JSON.stringify(bad));
  }
  assert.equal(D.usableLevels([TWO[0], { name: "x" }]).length, 1);
  for (const mode of ["easy", "normal", "hard"]) assert.deepEqual(D.create({ mode, seed: 1, levels: TWO }).opponents, TWO, "every mode plays the list");
});

test("Opponents: each one's numbers, adjusted by Easy / Hard; its points to win; beating the last one wins", () => {
  const s = D.create({ mode: "normal", seed: 2, levels: TWO });
  assert.equal(s.opponents.length, 2);
  assert.equal(D.cpuSpeed(s), 1.2);
  assert.equal(D.missChance(s), 0.4);
  assert.equal(D.toWin(s), 3);
  assert.equal(D.cpuSpeed(D.create({ mode: "easy", seed: 2, levels: TWO })), 1, "Easy is slower (never under 1)");
  assert.ok(Math.abs(D.missChance(D.create({ mode: "hard", seed: 2, levels: TWO })) - 0.34) < 1e-9);
  run(s, D.SERVE_DELAY);
  assert.ok(Math.abs(Math.hypot(s.ball.vx, s.ball.vy) - 2.2) < 1e-9, "serves at the opponent's speed");
  s.me = 2;
  let evs = winPoint(s);
  assert.ok(types(evs).includes("level"), "3 points win against Pip");
  assert.equal(s.level, 2);
  assert.equal(D.cpuSpeed(s), 4.4);
  assert.equal(D.missChance(D.create({ mode: "hard", seed: 2, levels: [TWO[1]] })), 0.05, "never under 0.05");
  s.me = 2;
  evs = winPoint(s);
  assert.ok(!types(evs).includes("win"), "Max needs 4");
  s.me = 3;
  evs = winPoint(s);
  assert.ok(types(evs).includes("win"));
  assert.ok(s.over && s.won);
  const r = D.result(s);
  assert.equal(r.level, 2);
  assert.equal(r.stats.won, true);
  assert.equal(r.stats.cause, "won");
  // losing to an opponent who needs 3
  const t = D.create({ seed: 2, levels: TWO });
  t.cpu = 2; t.serve = 0; t.ball = { x: 10, y: 280, vx: 0, vy: 4, speed: 4 }; t.paddle.x = 200;
  assert.ok(types(run(t, 20)).includes("gameover"));
  assert.equal(D.result(t).stats.cause, "lost");
});

test("Opponents: points are × the match number up to 10, however long the list", () => {
  const many = Array.from({ length: 30 }, (_, i) => ({ ...TWO[0], name: "Pal" }));
  const s = D.create({ seed: 3, levels: many });
  s.level = 25;
  winPoint(s);
  assert.equal(s.score, D.POINT_POINTS * 10);
  s.me = 2;
  winPoint(s);
  assert.equal(s.score, D.POINT_POINTS * 10 * 2 + D.MATCH_POINTS * 10);
  assert.equal(s.level, 26);
  s.level = 4; s.score = 0;
  winPoint(s);
  assert.equal(s.score, D.POINT_POINTS * 4);
});

test("Opponents: save and restore keep going with the list (kept out of the save)", () => {
  const s = D.create({ seed: 8, levels: TWO });
  for (let i = 0; i < 400; i++) { player(s); D.step(s); }
  const data = JSON.parse(JSON.stringify(D.save(s)));
  assert.ok(!("opponents" in data));
  const copy = D.restore(data, TWO);
  assert.equal(copy.opponents.length, 2);
  for (let i = 0; i < 2000 && !s.over; i++) { player(s); player(copy); D.step(s); D.step(copy); }
  assert.equal(copy.score, s.score);
  assert.equal(copy.level, s.level);
  assert.deepEqual(copy.ball, s.ball);
  // a save past the end of a shorter list can't be continued
  const far = D.create({ seed: 8 }); far.level = 5;
  assert.throws(() => D.restore(JSON.parse(JSON.stringify(D.save(far))), TWO));
  assert.equal(D.restore(JSON.parse(JSON.stringify(D.save(far)))).opponents, D.LEVELS);
});

test("Opponents: the most generous opponents the fields allow stay inside the honest-score limits", () => {
  // the quickest points: a 3.6 serve, 3 points a match, a computer that never touches the ball, a long list
  const easiest = Array.from({ length: 60 }, () => ({ name: "Snooze", speed: 1, miss: 0.45, aim: 0.9, ball: 3.6, toWin: 3 }));
  const straight = (s) => { if (s.ball.vy > 0) D.setTarget(s, D.predictX(s, D.PY - D.BALL_R)); };
  for (const [mode, absent] of [["easy", true], ["hard", true], ["normal", false]]) {
    const s = D.create({ mode, seed: 5, levels: easiest });
    let u = 0;
    while (!s.over && u++ < 60 * 1800) {
      (absent ? straight : player)(s);
      if (s.serve > 0) D.press(s, "fire", true);
      if (absent && s.ball.vy < 0) { s.cpuX = s.ball.x < 120 ? 214 : 26; s.cpuAim = s.cpuX; }
      D.step(s);
      assert.ok(s.score <= (s.updates / 60) * 3500 + 2000, `score ${s.score} after ${s.updates / 60}s`);
    }
    assert.ok(s.score <= 1000000, "max score");
    assert.ok(s.level <= easiest.length);
    if (absent) assert.ok(s.won, "wins every match");
  }
});

test("Opponents: draws in every look with a list", () => {
  const long = [{ ...TWO[0], name: "A very long opponent name here" }, TWO[1]];
  for (const look of LOOKS) {
    const sb = makeSandbox();
    const canvas = sb.canvas(390, 487);
    const inst = sb.win.ArcadeGames.get("duel").create(canvas, { mode: "normal", look, seed: 5, levels: long });
    inst.start();
    for (let i = 0; i < 300; i++) {
      inst.input("fire", true);
      inst.pointer("move", 100 + (i % 80), 400);
      if (i % 50 === 0) inst.input("left", true);
      if (i % 50 === 25) inst.input("left", false);
      sb.frames(1);
    }
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
  }
});
