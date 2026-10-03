"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const S = loadLogic("snake-logic.js");

// Run updates until the snake has made `moves` more moves (or the game ends).
function moves(s, n) {
  const all = [];
  const target = s.moves + n;
  let guard = 0;
  while (s.moves < target && !s.over && guard++ < 100000) all.push(...S.step(s));
  return all;
}
function types(evs) { return evs.map((e) => e.type); }
function noFood(s) { s.food = { x: s.cols - 1, y: s.rows - 1 }; } // park the food in a corner

test("starts in the middle, heading right, 4 long", () => {
  const s = S.create({ mode: "walls-normal", seed: 1 });
  assert.equal(s.snake.length, 4);
  assert.deepEqual(s.snake[0], { x: 9, y: 10 });
  assert.equal(s.dir, "right");
  assert.ok(s.food && !S.isSnake(s, s.food.x, s.food.y));
});

test("moves one cell per tick at the mode's speed (fixed 60 updates a second)", () => {
  for (const [mode, cps] of [["wrap-slow", 6], ["wrap-normal", 8], ["wrap-fast", 11]]) {
    const s = S.create({ mode, seed: 2 }); noFood(s);
    for (let i = 0; i < 60; i++) S.step(s);
    assert.equal(s.moves, cps, mode);
    assert.deepEqual(s.snake[0], { x: (9 + cps) % 20, y: 10 });
  }
});

test("a turn pressed past half-way into a cell happens at once", () => {
  const s = S.create({ mode: "walls-normal", seed: 3 });
  noFood(s);
  moves(s, 1);
  while (s.pu < S.EARLY_TURN * 600) S.step(s);   // more than half-way to the next cell
  const before = s.moves, head = { ...s.snake[0] };
  assert.equal(S.turn(s, "up"), true);
  assert.equal(s.moves, before + 1);               // moved now, upwards
  assert.deepEqual(s.snake[0], { x: head.x, y: head.y - 1 });
  assert.equal(s.queue.length, 0);
  assert.equal(s.pu, 0);
  const t = S.create({ mode: "walls-normal", seed: 3 });
  noFood(t);
  moves(t, 1);
  assert.ok(t.pu < S.EARLY_TURN * 600);
  S.turn(t, "up");                                 // early in the cell: waits for the next move
  assert.equal(t.queue.length, 1);
});

test("a turn into the opposite direction is ignored", () => {
  const s = S.create({ seed: 3 }); noFood(s);
  assert.equal(S.turn(s, "left"), false);
  moves(s, 1);
  assert.equal(s.dir, "right");
  assert.deepEqual(s.snake[0], { x: 10, y: 10 });
});

test("two quick turns within one tick are both kept (queued)", () => {
  const s = S.create({ seed: 4 }); noFood(s);
  assert.equal(S.turn(s, "up"), true);
  assert.equal(S.turn(s, "left"), true);     // tight U-turn: up, then left
  assert.equal(S.turn(s, "right"), false);   // opposite of the last queued turn
  assert.equal(S.turn(s, "left"), false);    // same as the last queued turn
  moves(s, 1);
  assert.deepEqual(s.snake[0], { x: 9, y: 9 });
  moves(s, 1);
  assert.deepEqual(s.snake[0], { x: 8, y: 9 });
  assert.equal(s.dir, "left");
});

test("the turn queue holds at most three turns", () => {
  const s = S.create({ seed: 4 });
  assert.ok(S.turn(s, "up")); assert.ok(S.turn(s, "left")); assert.ok(S.turn(s, "down"));
  assert.equal(S.turn(s, "right"), false);
  assert.equal(s.queue.length, S.MAX_QUEUE);
});

test("eating food grows the snake by one and scores 10 × speed multiplier", () => {
  for (const [mode, pts] of [["walls-slow", 10], ["walls-normal", 15], ["wrap-fast", 20]]) {
    const s = S.create({ mode, seed: 5 });
    s.food = { x: 10, y: 10 };
    const evs = moves(s, 1);
    assert.ok(types(evs).includes("eat"), mode);
    assert.equal(s.score, pts, mode);
    assert.equal(s.snake.length, 5, mode);
    assert.ok(s.food && !S.isSnake(s, s.food.x, s.food.y), "new food on a free cell");
  }
});

test("walls mode: hitting the edge ends the game", () => {
  const s = S.create({ mode: "walls-fast", seed: 6 }); noFood(s); s.food = { x: 0, y: 0 };
  const evs = moves(s, 20);
  assert.equal(s.over, true);
  assert.equal(s.cause, "wall");
  assert.ok(types(evs).includes("die"));
  assert.equal(s.snake[0].x, 19);
});

test("wrap mode: out one side, in the other", () => {
  const s = S.create({ mode: "wrap-normal", seed: 7 }); s.food = { x: 0, y: 0 };
  moves(s, 10); // head at x = 19
  assert.equal(s.snake[0].x, 19);
  moves(s, 1);
  assert.equal(s.over, false);
  assert.deepEqual(s.snake[0], { x: 0, y: 10 });
  S.turn(s, "up"); moves(s, 11);
  assert.deepEqual(s.snake[0], { x: 0, y: 19 }); // wrapped through the top
});

test("biting itself ends the game; moving into the tail's old cell does not", () => {
  const s = S.create({ seed: 8, length: 5 }); s.food = { x: 0, y: 0 };
  S.turn(s, "up"); S.turn(s, "left"); S.turn(s, "down");
  moves(s, 3);
  assert.equal(s.over, true);
  assert.equal(s.cause, "self");

  // A 4-long snake circling in a 2 × 2 square chases its own tail safely.
  const t = S.create({ seed: 9, length: 4 }); t.food = { x: 0, y: 0 };
  t.snake = [{ x: 5, y: 5 }, { x: 5, y: 6 }, { x: 6, y: 6 }, { x: 6, y: 5 }]; t.dir = "up";
  S.turn(t, "right"); moves(t, 1); // into (6,5), the tail's cell, as the tail leaves it
  assert.equal(t.over, false);
  assert.deepEqual(t.snake[0], { x: 6, y: 5 });
});

test("speeds up a little every 5 foods (and the level goes up)", () => {
  const s = S.create({ mode: "walls-normal", seed: 10 });
  for (let i = 0; i < 5; i++) {
    const h = s.snake[0];
    s.food = { x: h.x + 1, y: h.y };
    const evs = moves(s, 1);
    if (i < 4) assert.ok(!types(evs).includes("speed"));
    else assert.ok(types(evs).includes("speed"));
    if (s.snake[0].x >= 17) { S.turn(s, "down"); moves(s, 1); S.turn(s, "left"); }
  }
  assert.equal(s.foods, 5);
  assert.equal(s.level, 2);
  assert.equal(s.cps, 8 + S.SPEEDUP_CPS);
});

test("speed is capped", () => {
  const s = S.create({ mode: "walls-fast", seed: 11 });
  for (let i = 0; i < 200; i++) { s.foods = i * 5 - 1; s.food = null; }
  s.cps = s.maxCps;
  assert.ok(s.maxCps <= S.MAX_CPS);
});

test("bonus food: 50 points, disappears after a few seconds", () => {
  const s = S.create({ mode: "walls-normal", seed: 12 }); noFood(s);
  S.spawnBonus(s);
  s.bonus.x = 10; s.bonus.y = 10;
  const evs = moves(s, 1);
  assert.ok(types(evs).includes("bonus"));
  assert.equal(s.score, 50);
  assert.equal(s.bonus, null);
  assert.equal(s.snake.length, 4, "the bonus doesn't grow the snake");

  const t = S.create({ mode: "wrap-normal", seed: 13 }); noFood(t);
  S.spawnBonus(t); t.bonus.x = 0; t.bonus.y = 0;
  let gone = false;
  for (let i = 0; i < S.BONUS_SECONDS * 60; i++) if (types(S.step(t)).includes("bonusGone")) gone = true;
  assert.ok(gone);
  assert.equal(t.bonus, null);
});

test("bonus food turns up now and then while playing", () => {
  let seen = 0;
  for (let seed = 1; seed <= 20; seed++) {
    const s = S.create({ mode: "wrap-normal", seed });
    for (let i = 0; i < 12; i++) {
      const h = s.snake[0]; s.food = { x: (h.x + 1) % 20, y: h.y };
      if (types(moves(s, 1)).includes("bonusSpawn")) seen++;
    }
  }
  assert.ok(seen > 0);
});

test("filling the whole board wins", () => {
  const s = S.create({ cols: 4, rows: 2, seed: 14, length: 2 });
  // Board 4 × 2. Snake at (1,1),(0,1) heading right; walk a loop that eats everything.
  const path = ["right", "right", "up", "left", "left", "left"];
  let guard = 0;
  while (!s.over && guard++ < 20) {
    const next = path.shift();
    if (next && next !== s.dir) S.turn(s, next);
    const n = S.nextCell(s, s.queue[0] || s.dir);
    if (n && !S.isSnake(s, n.x, n.y)) s.food = { x: n.x, y: n.y };
    moves(s, 1);
  }
  assert.equal(s.won, true);
  assert.equal(s.over, true);
  assert.equal(s.snake.length, 8);
});

test("deterministic for a given seed", () => {
  const run = (seed) => {
    const s = S.create({ mode: "wrap-fast", seed });
    const foods = [];
    for (let i = 0; i < 8; i++) { const h = s.snake[0]; s.food = s.food; foods.push(`${s.food.x},${s.food.y}`); s.food = { x: (h.x + 1) % 20, y: h.y }; moves(s, 1); }
    return foods.join(" ") + "|" + s.score;
  };
  assert.equal(run(42), run(42));
  assert.notEqual(run(42), run(43));
});

test("scores stay within the server's honest-score limits during long play", () => {
  // A greedy player on Fast, wrap: score ≤ seconds × 40 + 100.
  const s = S.create({ mode: "wrap-fast", seed: 15 });
  let guard = 0;
  while (!s.over && guard++ < 60 * 600) {
    if (!s.queue.length && s.food) {
      const h = s.snake[0];
      const want = s.food.x !== h.x ? (s.food.x > h.x ? "right" : "left") : (s.food.y > h.y ? "down" : "up");
      S.turn(s, want);
    }
    S.step(s);
    assert.ok(s.score <= (s.updates / 60) * 40 + 100, `score ${s.score} after ${s.updates / 60}s`);
  }
});

test("result has score, level and stats", () => {
  const s = S.create({ mode: "walls-slow", seed: 16 });
  const r = S.result(s);
  assert.equal(r.score, 0); assert.equal(r.level, 1);
  assert.equal(r.stats.length, 4); assert.equal(r.stats.mode, "walls-slow");
  assert.deepEqual(S.MODES, ["walls-slow", "walls-normal", "walls-fast", "wrap-slow", "wrap-normal", "wrap-fast", "maze"]);
  assert.equal(S.create({ mode: "nonsense" }).mode, "walls-normal");
});
