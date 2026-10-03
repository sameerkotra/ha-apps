"use strict";
// Level lists (SPEC §11): Brick Breaker and Snake's Maze play the levels the server gives them,
// check them again, and fall back to their built-in ones.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");
const B = loadLogic("brick-logic.js");
const S = loadLogic("snake-logic.js");

const WALLS_OPEN = Array(20).fill("....................");
function maze(name, foods, walls) { return { name, foods, walls: walls || WALLS_OPEN.slice() }; }

// ---------------------------------------------------------------- Brick Breaker
test("brick: plays the levels it is given, in order, then repeats them faster", () => {
  const levels = [{ name: "One", rows: ["11111111", "11111111", "11111111"] }, { name: "Two", rows: ["2.2.2.2.", ".3.3.3.3", "11111111"] }];
  const s = B.create({ seed: 4, levels });
  assert.equal(s.layouts.length, 2);
  assert.equal(s.layout, "One");
  assert.equal(s.bricksLeft, 24);
  s.serving = false; s.bricksLeft = 0;          // clear the level
  let ev = B.step(s);
  assert.ok(ev.some((e) => e.type === "level" && e.next === 2));
  assert.equal(s.layout, "Two");
  s.serving = false; s.bricksLeft = 0;
  B.step(s);
  assert.equal(s.level, 3);
  assert.equal(s.layout, "One");                 // round two
  assert.ok(B.levelSpeed(3, 2) > B.levelSpeed(1, 2));
});

test("brick: levels that don't fit are skipped; none usable means the built-in ones", () => {
  const bad = [null, { name: "x" }, { name: "short", rows: ["111"] }, { name: "letters", rows: ["abcdefgh"] },
    { name: "empty", rows: ["........"] }, { name: "tall", rows: Array(11).fill("11111111") }];
  assert.equal(B.usableLevels(bad), B.LAYOUTS);
  assert.equal(B.usableLevels(undefined), B.LAYOUTS);
  const mixed = B.usableLevels(bad.concat([{ name: "Good", rows: ["11111111"] }]));
  assert.equal(mixed.length, 1);
  assert.equal(mixed[0].name, "Good");
  assert.equal(B.create({ seed: 1 }).layouts, B.LAYOUTS);
});

test("brick: a long list speeds up gently after its tenth level", () => {
  assert.equal(B.levelSpeed(1), B.BASE_SPEED);
  const at11 = B.levelSpeed(11, 40), at21 = B.levelSpeed(21, 40);
  assert.ok(at21 > at11 && at21 - at11 < at11 * 0.12);
  assert.ok(B.levelSpeed(400, 40) <= B.MAX_SPEED);
});

// ---------------------------------------------------------------- Snake · Maze
test("maze: ten built-in mazes, each with a free start and every free cell reachable", () => {
  assert.equal(S.MAZES.length, 10);
  S.MAZES.forEach((m, i) => {
    assert.equal(m.walls.length, 20, m.name);
    m.walls.forEach((r) => assert.match(r, /^[.#]{20}$/));
    for (let k = -3; k <= 3; k++) assert.equal(m.walls[10][9 + k], ".", m.name);
    assert.equal(m.foods, 5 + i);
    const free = new Set(), seen = new Set(["9,10"]), todo = [[9, 10]];
    m.walls.forEach((r, y) => [...r].forEach((c, x) => { if (c === ".") free.add(`${x},${y}`); }));
    while (todo.length) {
      const [x, y] = todo.pop();
      for (const [nx, ny] of [[x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]]) {
        const k = `${nx},${ny}`;
        if (free.has(k) && !seen.has(k)) { seen.add(k); todo.push([nx, ny]); }
      }
    }
    assert.equal(seen.size, free.size, m.name);
  });
});

test("maze: walls stop the snake, food never lands on one", () => {
  const walls = WALLS_OPEN.slice();
  walls[10] = ".............#......";               // four cells ahead of the head
  const s = S.create({ mode: "maze", seed: 2, levels: [maze("Wall ahead", 5, walls)] });
  assert.equal(s.mode, "maze");
  assert.equal(s.cols, 20);
  assert.equal(s.mazeName, "Wall ahead");
  assert.deepEqual(S.nextCell(s, "right"), { x: 10, y: 10 });
  s.food = { x: 0, y: 0 };
  for (let seed = 1; seed < 40; seed++) {
    const t = S.create({ mode: "maze", seed, levels: [maze("Wall ahead", 5, walls)] });
    assert.ok(!S.isWall(t, t.food.x, t.food.y));
  }
  let ev = [];
  for (let i = 0; i < 60 * 3 && !s.over; i++) ev = ev.concat(S.step(s));
  assert.ok(s.over);
  assert.equal(s.cause, "wall");
});

function eatNext(s) {
  const h = s.snake[0], d = S.DIRS[s.queue[0] || s.dir];
  s.food = { x: h.x + d[0], y: h.y + d[1] };
  const ev = [];
  const target = s.moves + 1;
  while (s.moves < target && !s.over) ev.push(...S.step(s));
  return ev;
}

test("maze: eating the goal clears the maze, scores a bonus and opens the next one", () => {
  const s = S.create({ mode: "maze", seed: 5, levels: [maze("First", 2), maze("Second", 3)] });
  assert.equal(s.level, 1);
  assert.equal(s.mazeName, "First");
  eatNext(s);
  assert.equal(s.mazeFoods, 1);
  const ev = eatNext(s);
  assert.ok(ev.some((e) => e.type === "level" && e.cleared === 1 && e.next === 2));
  assert.equal(s.level, 2);
  assert.equal(s.mazeName, "Second");
  assert.equal(s.mazeFoods, 0);
  assert.equal(s.snake.length, 4);                // back at the start
  assert.equal(s.snake[0].x, 9);
  assert.equal(s.score, 2 * 15 + S.MAZE_CLEAR_POINTS);
  assert.ok(s.cps > s.baseCps);                   // each maze a little faster
});

test("maze: clearing the last maze wins the game", () => {
  const s = S.create({ mode: "maze", seed: 6, levels: [maze("Only", 1)] });
  const ev = eatNext(s);
  assert.ok(ev.some((e) => e.type === "win"));
  assert.ok(s.over && s.won);
  assert.equal(S.result(s).stats.mazesCleared, 1);
  assert.equal(S.result(s).level, 1);
});

test("maze: bad mazes are skipped; none usable means the built-in ones", () => {
  const blocked = WALLS_OPEN.slice();
  blocked[10] = "........#...........";            // on the snake's body
  const bad = [maze("Blocked start", 5, blocked), maze("Short", 5, WALLS_OPEN.slice(1)), maze("No foods", 0), { name: "x" }];
  assert.equal(S.usableMazes(bad), S.MAZES);
  assert.equal(S.usableMazes(bad.concat([maze("Fine", 5)])).length, 1);
  assert.equal(S.create({ mode: "maze", seed: 1 }).mazes, S.MAZES);
  assert.equal(S.create({ mode: "walls-normal", seed: 1, levels: [maze("Ignored", 5)] }).walls, null);
});
