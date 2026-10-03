"use strict";
// Saved games (SPEC §12): each game saves its rules state as plain data and carries on from it,
// with the time already played; the kit refuses to save a game that isn't on.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox, loadLogic } = require("./helpers");
const S = loadLogic("snake-logic.js");
const B = loadLogic("brick-logic.js");
const plain = (v) => JSON.parse(JSON.stringify(v));

test("snake: a saved game comes back the same, maze walls included", () => {
  const s = S.create({ mode: "maze", seed: 7 });
  for (let i = 0; i < 40; i++) S.step(s);
  assert.equal(s.over, false);
  s.score = 77;
  const data = plain(S.save(s));
  assert.equal(data.mazes, undefined);              // the maze list travels separately
  const r = S.restore(data, S.MAZES);
  assert.deepEqual(r.snake, s.snake);
  assert.equal(r.score, 77);
  assert.equal(r.mazeName, s.mazeName);
  assert.ok(S.isWall(r, 4, 4));
  // and it plays on exactly as the original would
  const a = [], b = [];
  for (let i = 0; i < 40; i++) { a.push(...S.step(s).map((e) => e.type)); b.push(...S.step(r).map((e) => e.type)); }
  assert.deepEqual(r.snake, s.snake);
  assert.deepEqual(a, b);
  assert.throws(() => S.restore({ snake: [] }));
  assert.throws(() => S.restore({ ...data, snake: [{ x: 99, y: 0 }] }));
});

test("brick: a saved game comes back the same", () => {
  const s = B.create({ seed: 3, mode: "classic" });
  B.press(s, "fire", true);
  for (let i = 0; i < 300 && !s.over; i++) B.step(s);
  const data = plain(B.save(s));
  assert.equal(data.layouts, undefined);
  const r = B.restore(data, null);
  assert.equal(r.layouts, B.LAYOUTS);
  for (let i = 0; i < 300; i++) { B.step(s); B.step(r); }
  assert.equal(r.score, s.score);
  assert.equal(r.bricksLeft, s.bricksLeft);
  assert.throws(() => B.restore({ bricks: [] }));
});

test("kit: save() while playing, restore with the time already played", () => {
  const sb = makeSandbox();
  const G = sb.win.ArcadeGames;
  assert.equal(G.get("snake").stateVersion, S.STATE_VERSION);
  assert.equal(G.get("brick").stateVersion, B.STATE_VERSION);
  const inst = G.get("snake").create(sb.canvas(), { mode: "wrap-slow", seed: 1 });
  assert.equal(inst.save(), null);                  // not started
  assert.equal(inst.canSave, true);
  inst.start();
  sb.frames(120);
  inst.pause();
  const snap = plain(inst.save());
  assert.equal(snap.seconds, 2);
  assert.ok(snap.state.snake.length >= 4);
  inst.destroy();
  const again = G.get("snake").create(sb.canvas(), { mode: "wrap-slow", seed: 5, restore: { state: snap.state, seconds: snap.seconds } });
  assert.deepEqual(plain(again.logic.snake), snap.state.snake);
  again.start();
  assert.equal(again.seconds, 2);                   // carries on from the saved time
  sb.frames(60);
  assert.equal(again.seconds, 3);
});
