"use strict";
// The score chases and computer opponents carry on too (SPEC §14, 1.12.1): Brick Breaker, Falling Blocks challenges,
// Paddle Duel, Tank Battle, Sky Defenders waves, Rocks waves, City Defense waves and Snake Duel vs computer start at
// opts.startLevel, a start past the end plays the last level, and a run from there plays on without dropping back.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");

const CARRY = [
  ["brick-logic.js", "powerups"], ["brick-logic.js", "classic"], ["blocks-logic.js", "challenge"],
  ["duel-logic.js", "easy"], ["duel-logic.js", "normal"], ["duel-logic.js", "hard"],
  ["tanks-logic.js", "classic"], ["tanks-logic.js", "easy"], ["invaders-logic.js", "waves"],
  ["rocks-logic.js", "waves"], ["defense-logic.js", "waves"], ["snakeduel-logic.js", "cpu"],
];
const ACTIONS = ["up", "down", "left", "right", "fire", "alt"];
const levelOf = (L, s) => (L.result ? L.result(s).level : s.level);

for (const [file, mode] of CARRY) {
  test(`${file} · ${mode}: starts at the level asked for`, () => {
    const L = loadLogic(file);
    assert.equal(L.create({ mode, seed: 7 }).level, 1, "no startLevel: level 1");
    assert.equal(L.create({ mode, seed: 7, startLevel: 0 }).level, 1);
    const last = L.create({ mode, seed: 7, startLevel: 999 }).level;
    assert.ok(last >= 3, `${file} has at least 3 levels (${last})`);
    const s = L.create({ mode, seed: 7, startLevel: 3 });
    assert.equal(s.level, 3);
    assert.equal(levelOf(L, s), 3);
    let seed = 99;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    for (let i = 0; i < 1500 && !s.over; i++) {
      if (L.press && rnd() < 0.05) L.press(s, ACTIONS[Math.floor(rnd() * ACTIONS.length)], rnd() < 0.7);
      L.step(s);
      assert.ok(s.level >= 3, `${file}: level ${s.level}`);
    }
  });
}
