"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const B = loadLogic("brick-logic.js");

function types(evs) { return evs.map((e) => e.type); }
function run(s, n) { const all = []; for (let i = 0; i < n && !s.over; i++) all.push(...B.step(s)); return all; }
function launched(seed = 1, mode = "powerups") {
  const s = B.create({ seed, mode });
  B.press(s, "fire", true); B.step(s);
  return s;
}
// Put the ball somewhere with a direction, away from everything else.
function place(s, x, y, dx, dy) {
  const n = Math.hypot(dx, dy);
  s.serving = false;
  s.balls = [{ x, y, dx: dx / n, dy: dy / n }];
}
// Keep just one tough brick in the top-left corner so the level doesn't end.
function keepOne(s) {
  s.bricks.forEach((b) => { b.alive = false; });
  Object.assign(s.bricks[0], { alive: true, hp: 3, max: 3, x: 8, y: 46 });
  s.bricksLeft = 1;
}
function clearBricks(s, keep) {
  s.bricks.forEach((b) => { if (!keep || !keep(b)) b.alive = false; });
  s.bricksLeft = s.bricks.filter((b) => b.alive).length;
}

test("ten original layouts, 8 bricks wide, all with bricks of 1–3 hits", () => {
  assert.equal(B.LAYOUTS.length, 10);
  const names = new Set();
  for (const L of B.LAYOUTS) {
    names.add(L.name);
    assert.ok(L.rows.length >= 5 && L.rows.length <= 9, L.name);
    for (const r of L.rows) assert.match(r, /^[.123]{8}$/, L.name);
    assert.ok(L.rows.join("").replace(/\./g, "").length >= 20, L.name);
  }
  assert.equal(names.size, 10);
  assert.ok(B.LAYOUTS.some((L) => L.rows.join("").includes("3")));
});

test("starts serving: three lives, ball sitting on the paddle until launched", () => {
  const s = B.create({ seed: 1 });
  assert.equal(s.lives, 3); assert.equal(s.level, 1); assert.equal(s.serving, true);
  run(s, 30);
  assert.equal(s.serving, true);
  assert.equal(s.balls[0].x, s.paddle.x);
  B.press(s, "fire", true);
  const evs = B.step(s);
  assert.ok(types(evs).includes("launch"));
  assert.equal(s.serving, false);
  assert.ok(s.balls[0].dy < 0);
});

test("the ball follows the paddle while serving", () => {
  const s = B.create({ seed: 1 });
  B.setTarget(s, 40); run(s, 60);
  assert.ok(Math.abs(s.paddle.x - 40) < 0.5);
  assert.equal(s.balls[0].x, s.paddle.x);
});

test("paddle follows the pointer smoothly and stops at the walls", () => {
  const s = B.create({ seed: 1 });
  B.setTarget(s, 200);
  B.step(s);
  const first = s.paddle.x;
  assert.ok(first > 120 && first < 200, "moves part of the way each update");
  assert.ok(first - 120 <= B.FOLLOW_MAX + 1e-9, "never jumps more than the follow cap");
  B.step(s); B.step(s);
  assert.ok(Math.abs(s.paddle.x - 200) < 12, "close to the finger within three updates");
  run(s, 60);
  assert.ok(Math.abs(s.paddle.x - 200) < 0.5);
  B.setTarget(s, 500); run(s, 60);
  assert.equal(s.paddle.x, B.WALL_R - s.paddle.w / 2);
});

test("keyboard moves the paddle; a key press overrides the pointer", () => {
  const s = B.create({ seed: 1 });
  B.setTarget(s, 200);
  B.press(s, "left", true); run(s, 20); B.press(s, "left", false);
  assert.ok(s.paddle.x < 120);
  assert.equal(s.input.target, null);
});

test("the ball's angle depends on where it hits the paddle", () => {
  const angleAt = (offset) => {
    const s = launched(2);
    keepOne(s);
    s.paddle.x = 120;
    place(s, 120 + offset, B.PADDLE_Y - 20, 0, 1);
    const evs = run(s, 20);
    assert.ok(types(evs).includes("paddle"));
    return Math.atan2(s.balls[0].dx, -s.balls[0].dy) * 180 / Math.PI;
  };
  const centre = angleAt(0), right = angleAt(20), edge = angleAt(28), left = angleAt(-20);
  assert.ok(Math.abs(centre) < 1, "centre sends it straight up");
  assert.ok(right > 30 && right < 60, "right side sends it right");
  assert.ok(left < -30 && left > -60, "left side sends it left");
  assert.ok(edge > right && edge <= 60.0001, "the edge sends it wider, up to 60°");
});

test("walls and ceiling bounce the ball", () => {
  const s = launched(3);
  keepOne(s);
  place(s, 20, 200, -1, -0.2);
  const evs = run(s, 20);
  assert.ok(types(evs).includes("wall"));
  assert.ok(s.balls[0].dx > 0);
  place(s, 200, 60, 0.1, -1);
  run(s, 20);
  assert.ok(s.balls[0].dy > 0);
});

test("brick hits: toughness 1/2/3, 10/20/30 points when broken", () => {
  for (const hp of [1, 2, 3]) {
    const s = launched(4);
    clearBricks(s);
    const br = s.bricks[0];
    Object.assign(br, { alive: true, hp, max: hp, x: 107, y: 100, w: 26, h: 11 });
    s.bricksLeft = 2; // a second (unreachable) brick keeps the level going
    Object.assign(s.bricks[1], { alive: true, hp: 1, max: 1, x: 8, y: 46 });
    for (let i = 0; i < hp; i++) {
      place(s, 120, 130, 0, -1);
      const evs = run(s, 15);
      assert.ok(types(evs).includes(i < hp - 1 ? "hit" : "break"), `hp ${hp} hit ${i + 1}`);
    }
    assert.equal(br.alive, false);
    assert.equal(s.score, 10 * hp);
    assert.equal(s.stats.bricks, 1);
  }
});

test("a brick hit from below sends the ball back down", () => {
  const s = launched(5);
  clearBricks(s);
  Object.assign(s.bricks[0], { alive: true, hp: 2, max: 2, x: 107, y: 100 });
  Object.assign(s.bricks[1], { alive: true, hp: 1, max: 1, x: 8, y: 46 });
  s.bricksLeft = 2;
  place(s, 120, 130, 0, -1);
  run(s, 15);
  assert.ok(s.balls[0].dy > 0);
  assert.ok(s.balls[0].y > 111);
});

test("losing the ball costs a life and serves again; no lives left ends the game", () => {
  const s = launched(6);
  s.paddle.x = 30;
  place(s, 200, 250, 0, 1);
  let evs = run(s, 60);
  assert.ok(types(evs).includes("lifeLost"));
  assert.equal(s.lives, 2);
  assert.equal(s.serving, true);
  s.lives = 1;
  B.press(s, "fire", true); B.step(s);
  s.paddle.x = 30; place(s, 200, 250, 0, 1);
  evs = run(s, 60);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(s.over, true);
  assert.equal(B.result(s).stats.livesLeft, 0);
});

test("clearing every brick finishes the level: 100 × level bonus and the next layout", () => {
  const s = launched(7);
  clearBricks(s, (b) => b === s.bricks[0]);
  const br = s.bricks[0];
  place(s, br.x + br.w / 2, br.y + 30, 0, -1);
  const evs = run(s, 30);
  const lv = evs.find((e) => e.type === "level");
  assert.ok(lv);
  assert.equal(s.score, 10 * br.max + 100);
  assert.equal(s.level, 2);
  assert.equal(s.layout, B.LAYOUTS[1].name);
  assert.equal(s.serving, true);
  assert.ok(s.bricksLeft > 0);
});

test("after ten levels the layouts repeat, faster", () => {
  assert.equal(B.LAYOUTS[(11 - 1) % 10].name, B.LAYOUTS[0].name);
  assert.ok(B.levelSpeed(2) > B.levelSpeed(1));
  assert.ok(B.levelSpeed(11) > B.levelSpeed(1));
  assert.ok(B.levelSpeed(21) > B.levelSpeed(11));
  assert.ok(B.levelSpeed(500) <= B.MAX_SPEED);
  const s = B.create({ seed: 1, level: 11 });
  assert.equal(s.layout, B.LAYOUTS[0].name);
});

test("power-ups fall from broken bricks in Power-ups mode, never in Classic", () => {
  const drops = (mode) => {
    let n = 0;
    for (let seed = 1; seed <= 30; seed++) {
      const s = launched(seed, mode);
      for (const br of s.bricks.slice(0, 12)) { br.hp = 1; const ev = []; B.hitBrick(s, br, ev); n += types(ev).filter((t) => t === "drop").length; }
      if (mode === "classic") assert.equal(s.capsules.length, 0);
    }
    return n;
  };
  assert.ok(drops("powerups") > 10);
  assert.equal(drops("classic"), 0);
});

test("catching a capsule applies it; wide and slow last a set time", () => {
  const s = launched(8);
  keepOne(s);
  place(s, 120, 120, 0.3, -1);
  s.paddle.x = 120;
  s.capsules.push({ x: 120, y: B.PADDLE_Y - 20, type: "wide" });
  let evs = run(s, 30);
  assert.ok(evs.some((e) => e.type === "powerup" && e.power === "wide"));
  assert.equal(s.paddle.w, B.PADDLE_WIDE);
  s.timers.wide = 2;
  evs = run(s, 3);
  assert.ok(evs.some((e) => e.type === "powerEnd" && e.power === "wide"));
  assert.equal(s.paddle.w, B.PADDLE_W);

  const v0 = B.ballSpeed(s);
  B.apply(s, "slow", []);
  assert.ok(Math.abs(B.ballSpeed(s) - v0 * B.SLOW_FACTOR) < 1e-9);
  assert.equal(s.timers.slow, B.POWER_SECONDS.slow * 60);

  const balls = s.balls.length;
  B.apply(s, "multi", []);
  assert.equal(s.balls.length, balls + 1);

  s.lives = 3; B.apply(s, "life", []);
  assert.equal(s.lives, 4);
  s.lives = B.MAX_LIVES; B.apply(s, "life", []);
  assert.equal(s.lives, B.MAX_LIVES);
});

test("with an extra ball, losing one ball doesn't cost a life", () => {
  const s = launched(9);
  keepOne(s);
  s.paddle.x = 30;
  s.serving = false;
  s.balls = [{ x: 200, y: 280, dx: 0, dy: 1 }, { x: 120, y: 120, dx: 0.3, dy: -0.95 }];
  run(s, 30);
  assert.equal(s.lives, 3);
  assert.equal(s.balls.length, 1);
});

test("the ball never passes through a brick at top speed", () => {
  const s = launched(10);
  clearBricks(s);
  const br = s.bricks[0];
  Object.assign(br, { alive: true, hp: 3, max: 3, x: 107, y: 100 });
  Object.assign(s.bricks[1], { alive: true, hp: 1, max: 1, x: 8, y: 46 });
  s.bricksLeft = 2;
  s.level = 999; s.rally = 1000;
  assert.equal(B.ballSpeed(s), B.MAX_SPEED);
  place(s, 120, 150, 0, -1);
  run(s, 10);
  assert.equal(br.hp, 2);
  assert.ok(s.balls[0].y > 111);
});

test("deterministic for a given seed; scores stay within the honest-score limits", () => {
  const play = (seed) => {
    const s = B.create({ seed });
    let u = 0;
    while (!s.over && u++ < 60 * 300) {
      const b = s.balls[0];
      if (s.serving) B.press(s, "fire", true);
      if (b) B.setTarget(s, b.x + Math.sin(u / 50) * 10);
      B.step(s);
      assert.ok(s.score <= (s.updates / 60) * 400 + 500, `score ${s.score} after ${s.updates / 60}s`);
    }
    return JSON.stringify(B.result(s)) + s.updates;
  };
  assert.equal(play(77), play(77));
});
