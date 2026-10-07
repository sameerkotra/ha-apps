"use strict";
// Wave 5 in the shell: the six games register with their modes and controls, and each is raced from two phones
// (spec §13.3) — two phones with the same seed (and the same level list) and the same inputs end in exactly the
// same game, a different seed gives a different one, and status() reports the race bar's numbers.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox } = require("./helpers");

const plain = (v) => JSON.parse(JSON.stringify(v));
const FILES = ["bubbles-logic.js", "bubbles.js", "gems-logic.js", "gems.js", "stack-logic.js", "stack.js", "runner-logic.js", "runner.js",
  "lander-logic.js", "lander.js", "defense-logic.js", "defense.js"];
const GAMES = {
  bubbles: ["Bubble Pop", ["classic", "relaxed", "puzzle", "endless"], "classic", "paddle", "puzzle"],
  gems: ["Gem Swap", ["timed", "moves", "zen"], "timed", "touch", "moves"],
  stack: ["Tower Stack", ["classic", "fast", "easy", "towers"], "classic", "buttons", "towers"],
  runner: ["Runner", ["classic", "easy", "courses"], "classic", "buttons", "courses"],
  lander: ["Lander", ["classic", "easy", "levels"], "classic", "buttons", "levels"],
  defense: ["City Defense", ["classic", "easy", "waves"], "classic", "touch", "waves"],
};

function script(seed) {
  let x = seed >>> 0;
  return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; };
}
const ACTIONS = ["up", "down", "left", "right", "fire", "alt"];

/** One phone: the real game files and kit in a sandbox, played with a fixed stream of keys, taps and drags. */
function phone(gameId, mode, seed, frames, levels) {
  const sb = makeSandbox({ extra: FILES });
  const ends = [];
  const inst = sb.win.ArcadeGames.get(gameId).create(sb.canvas(390, 487), { mode, seed, levels, look: "modern", onEnd: (r) => ends.push(r) });
  inst.start();
  const rnd = script(99);
  for (let i = 0; i < frames; i++) {
    if (i % 4 === 0) {
      const a = ACTIONS[Math.floor(rnd() * ACTIONS.length)];
      inst.input(a, true);
      if (rnd() < 0.6) inst.input(a, false);
    }
    if (i % 9 === 0) {
      const px = rnd() * 390, py = rnd() * 487;
      inst.pointer("down", px, py); inst.pointer("move", px + 20 * (rnd() - 0.5), py + 20 * (rnd() - 0.5)); inst.pointer("up", px, py);
    }
    sb.frames(1);
  }
  const out = { logic: JSON.stringify(inst.logic), score: inst.score, level: inst.level, status: plain(inst.status()), ends: plain(ends) };
  inst.destroy();
  return out;
}

test("the six wave 5 games register with their modes, controls, saving and a race", () => {
  const { win } = makeSandbox({ extra: FILES });
  const ids = plain(win.ArcadeGames.list().map((g) => g.id));
  const at = ids.indexOf("bubbles");
  assert.deepEqual(ids.slice(at, at + 6), Object.keys(GAMES));
  for (const [id, [name, modes, def, controls]] of Object.entries(GAMES)) {
    const g = win.ArcadeGames.get(id);
    assert.equal(g.name, name);
    assert.deepEqual(plain(g.modes.map((m) => m.id)), modes);
    assert.equal(g.defaultMode, def);
    assert.equal(g.controls, controls);
    assert.equal(g.stateVersion, 1);
    assert.equal(g.race, true, id);
    assert.equal(g.players, 1);
    assert.equal(g.lockstep, false);
    assert.ok(g.help.length > 60, id);
    if (controls === "buttons") assert.ok(g.buttons.length >= 1);
  }
});

for (const [id, [, , def, , listMode]] of Object.entries(GAMES)) {
  test(`${id}: two phones with the same seed play the same game (${def} and ${listMode})`, () => {
    for (const mode of [def, listMode]) {
      const a = phone(id, mode, 4242, 1500), b = phone(id, mode, 4242, 1500);
      assert.equal(a.logic, b.logic, `${mode}: the whole state is identical`);
      assert.equal(a.score, b.score);
      assert.equal(a.level, b.level);
      assert.deepEqual(a.ends, b.ends);
      assert.deepEqual(a.status, b.status);
    }
  });
}

test("the same level list from the match gives both phones the same levels", () => {
  const lists = {
    stack: [{ name: "Short", floors: 8, width: 90, speed: 1.4, ramp: 0, grow: true }],
    defense: [{ name: "Few", missiles: 5, speed: 0.5, splits: 1, fliers: 1, ammo: 8 }],
    runner: [{ name: "Short run", speed: 3.4, ramp: 0, pattern: "..b....p....f...." }],
  };
  for (const [id, levels] of Object.entries(lists)) {
    const mode = GAMES[id][4];
    const a = phone(id, mode, 77, 900, levels), b = phone(id, mode, 77, 900, levels);
    assert.equal(a.logic, b.logic, id);
    assert.ok(a.logic.includes(levels[0].name), `${id} plays the match's list`);
  }
});

test("a different seed gives a different game (so the seed is what the race shares)", () => {
  for (const [id, [, , def]] of Object.entries(GAMES)) {
    assert.notEqual(phone(id, def, 4242, 200).logic, phone(id, def, 777, 200).logic, id);
  }
});

test("status(): score, level, over and paused for each of them", () => {
  for (const [id, [, , def]] of Object.entries(GAMES)) {
    const sb = makeSandbox({ extra: FILES });
    const inst = sb.win.ArcadeGames.get(id).create(sb.canvas(), { mode: def, seed: 3 });
    assert.deepEqual(Object.keys(plain(inst.status())), ["score", "level", "over", "paused"], id);
    inst.start(); sb.frames(10); inst.pause();
    assert.deepEqual(plain(inst.status()), { score: inst.score, level: inst.level, over: false, paused: true }, id);
    inst.destroy();
  }
});

// The rules use only + − × ÷, floor/round/sqrt/abs/min/max and imul — never Math.sin/cos/atan2/pow/random or Date —
// so two phones on different browsers get exactly the same numbers (Bubble Pop's aim and Lander's thrust use sines
// worked out by their series). Each rules file is loaded and played with those replaced by functions that throw.
test("the rules never use engine-dependent maths (two browsers play exactly the same game)", () => {
  const path = require("node:path");
  const FORBIDDEN = ["sin", "cos", "tan", "atan", "atan2", "exp", "log", "pow", "hypot", "random", "cbrt", "sinh", "cosh"];
  const MODES = { bubbles: ["classic", "relaxed", "puzzle", "endless"], gems: ["timed", "moves", "zen"], stack: ["classic", "fast", "easy", "towers"],
    runner: ["classic", "easy", "courses"], lander: ["classic", "easy", "levels"], defense: ["classic", "easy", "waves"] };
  const ACTS = ["up", "down", "left", "right", "fire", "alt"];
  const saved = {}, now = Date.now;
  for (const k of FORBIDDEN) { saved[k] = Math[k]; Math[k] = () => { throw new Error(`Math.${k} in the rules`); }; }
  Date.now = () => { throw new Error("Date.now in the rules"); };
  try {
    for (const [id, modes] of Object.entries(MODES)) {
      const file = path.join(__dirname, "..", "..", "app", "static", "games", `${id}-logic.js`);
      delete require.cache[require.resolve(file)];
      const L = require(file);
      for (const mode of modes) {
        const r = script(id.length * 31 + mode.length);
        const s = L.create({ mode, seed: 99 });
        for (let i = 0; i < 4000 && !s.over; i++) {
          if (r() < 0.08) { const a = ACTS[Math.floor(r() * ACTS.length)]; L.press(s, a, true); if (r() < 0.6) L.press(s, a, false); }
          if (id === "gems" && r() < 0.05 && s.phase === "idle") { const m = L.findMove(s.board); if (m) { L.tap(s, m[0]); L.tap(s, m[1]); } }
          if (id === "defense" && r() < 0.05) L.aim(s, Math.floor(r() * 240), Math.floor(r() * 240));
          L.step(s);
        }
      }
      delete require.cache[require.resolve(file)];
    }
  } finally { Object.assign(Math, saved); Date.now = now; }
});
