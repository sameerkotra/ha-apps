"use strict";
// Carrying on from the next level (spec SPEC §14, GAMES.md "Starting at a level"): every game and mode that carries
// on starts at opts.startLevel — the level number it reports is the real one, the run goes on from there, and a level
// past the list's end starts at the last one. Without startLevel nothing changes.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic } = require("./helpers");

const CARRY = [
  ["snake-logic.js", "maze", "MAZES"], ["flap-logic.js", "course"], ["mines-logic.js", "boards"], ["merge-logic.js", "goals"],
  ["colours-logic.js", "challenge"], ["cards-logic.js", "challenge"], ["mole-logic.js", "gardens"],
  ["numbers-logic.js", "challenge"], ["racer-logic.js", "stages"], ["hop-logic.js", "levels"], ["bubbles-logic.js", "puzzle"],
  ["gems-logic.js", "moves"], ["stack-logic.js", "towers"], ["runner-logic.js", "courses"], ["lander-logic.js", "levels"],
  ["typerain-logic.js", "stages"], ["lights-logic.js", "climb", null, 5],
];
const ACTIONS = ["up", "down", "left", "right", "fire", "alt"];

function levelOf(L, s) { return L.result ? L.result(s).level : s.level; }
function countOf(L, list, fixed) { return fixed || (L[list || "LEVELS"] || []).length; }

for (const [file, mode, list, fixed] of CARRY) {
  test(`${file} · ${mode}: starts at the level asked for`, () => {
    const L = loadLogic(file);
    const count = countOf(L, list, fixed);
    assert.ok(count >= 3, `${file} has at least 3 levels`);
    const plain = L.create({ mode, seed: 7 });
    assert.equal(plain.level, 1);
    const s = L.create({ mode, seed: 7, startLevel: 3 });
    assert.equal(s.level, 3);
    assert.equal(levelOf(L, s), 3);
    assert.equal(L.create({ mode, seed: 7, startLevel: count + 50 }).level, count);
    assert.equal(L.create({ mode, seed: 7, startLevel: 0 }).level, 1);
    // a run from level 3 plays on: random input for a while never goes below level 3 and never throws
    let seed = 99;
    const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
    for (let i = 0; i < 1500 && !s.over; i++) {
      if (L.press && rnd() < 0.05) L.press(s, ACTIONS[Math.floor(rnd() * ACTIONS.length)], true);
      L.step(s);
      assert.ok(s.level >= 3, `${file}: level ${s.level}`);
    }
    if (L.save && L.restore && !s.over) {                       // a saved game keeps its level
      const back = L.restore(JSON.parse(JSON.stringify(L.save(s))), L[list || "LEVELS"]);
      assert.equal(back.level, s.level);
    }
  });
}
