"use strict";
// Car Park boards without the wait: the 200 Levels boards are made ahead (parking-levels.js, exactly what makeBoard
// makes), every level starts within a few milliseconds, Classic and Hard boards can be made off the main thread
// (deferBoard → boardFor → useBoard, the same board as made in place), and Undo takes back a whole drag and its move.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox, loadLogic } = require("./helpers");
const tool = require("./parking-levels-tool");

const Logic = loadLogic("parking-logic.js");
const plain = (v) => JSON.parse(JSON.stringify(v));

test("the boards made ahead are the ones makeBoard makes", () => {
  assert.ok(Logic.madeAhead());
  const sample = [1, 2, 3, 7, 50, 99, 100, 150, 199, 200];
  const fresh = tool.generate(sample);
  const Levels = require(tool.OUT);
  sample.forEach((n, i) => {
    const b = Levels.board(n);
    assert.deepEqual([b.fewest, b.rng >>> 0, b.vs.map((v, k) => `${v.h ? 1 : 0}${v.len}${v.lane}${b.pos[k]}`).join(" ")], fresh[i], "level " + n);
  });
  assert.equal(Levels.COUNT, Logic.LEVEL_COUNT);
});

test("every level starts in a few milliseconds", () => {
  let worst = 0;
  for (let n = 1; n <= Logic.LEVEL_COUNT; n++) {
    const t0 = process.hrtime.bigint();
    const s = Logic.create({ mode: "levels", seed: 1, startLevel: n });
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    worst = Math.max(worst, ms);
    assert.equal(s.level, n);
    assert.ok(s.fewest >= 1 && !Logic.solved(s.pos));
    assert.equal(Logic.fewestFrom(s.vs, s.pos), s.fewest, "level " + n + ": the fewest moves are right");
  }
  assert.ok(worst < 50, `slowest level start ${worst.toFixed(1)} ms`);
});

test("a level reached by playing on is the same board as one started there", () => {
  const s = Logic.create({ mode: "levels", seed: 1, startLevel: 4 });
  const direct = Logic.create({ mode: "levels", seed: 1, startLevel: 5 });
  // solve level 4 with the hints, then let the next one start
  for (let guard = 0; guard < 200 && s.level === 4; guard++) {
    if (s.phase === "play") {
      const m = Logic.nextMove(s.vs, s.pos);
      Logic.slide(s, m.v, m.to);
    } else Logic.step(s);
  }
  assert.equal(s.level, 5);
  assert.deepEqual(plain([s.vs, s.pos, s.fewest, s.rng]), plain([direct.vs, direct.pos, direct.fewest, direct.rng]));
});

test("a Classic or Hard board made elsewhere is the board made in place", () => {
  for (const mode of ["classic", "hard"]) {
    for (const seed of [1, 77, 123456]) {
      const here = Logic.create({ mode, seed });
      const later = Logic.create({ mode, seed, deferBoard: true });
      assert.equal(later.phase, "wait");
      Logic.step(later); Logic.step(later);
      assert.equal(later.updates, 0, "the clock waits for the board");
      assert.deepEqual(Logic.press(later, "fire", true), []);
      const evs = Logic.useBoard(later, Logic.boardFor(mode, seed));
      assert.deepEqual(plain(evs), [{ type: "ready" }]);
      assert.deepEqual(plain([later.vs, later.pos, later.fewest, later.phase]), plain([here.vs, here.pos, here.fewest, "play"]));
      assert.deepEqual(Logic.useBoard(later, Logic.boardFor(mode, seed)), [], "only once");
    }
  }
});

test("undo takes back a whole drag, and the board's move with it", () => {
  const s = Logic.create({ mode: "book", seed: 1 });
  const v = s.vs.findIndex((x, i) => i > 0 && Logic.range(s.vs, s.pos, i)[1] - Logic.range(s.vs, s.pos, i)[0] >= 2);
  assert.ok(v > 0);
  const r = Logic.range(s.vs, s.pos, v), from = s.pos[v];
  const to = from === r[0] ? r[0] + 2 : r[0];
  const stepTo = from < to ? from + 1 : from - 1;
  Logic.slide(s, v, stepTo, false);               // a drag across two squares: two slides …
  Logic.slide(s, v, to, true);
  assert.equal(s.undo.length, 1, "… one undo");
  assert.equal(s.boardMoves, 1);
  Logic.undo(s);
  assert.equal(s.pos[v], from);
  assert.equal(s.boardMoves, 0, "the board's move is taken back");
  assert.equal(s.moves, 1, "the run's total still counts it");
});

test("in the page without a worker, a Classic game shows its board a moment later", async () => {
  const sb = makeSandbox();
  const def = sb.win.ArcadeGames.get("parking");
  const inst = def.create(sb.canvas(), { mode: "classic", seed: 9, onEnd() {}, onScore() {}, onEvent() {} });
  inst.start();
  sb.frames(3);
  assert.equal(inst.logic.phase, "wait");
  assert.equal(inst.save().state, null, "nothing to save yet");
  await new Promise((resolve) => setTimeout(resolve, 600));
  sb.frames(2);
  assert.equal(inst.logic.phase, "play");
  assert.deepEqual(plain(inst.logic.pos), plain(Logic.create({ mode: "classic", seed: 9 }).pos));
  inst.destroy();
});
