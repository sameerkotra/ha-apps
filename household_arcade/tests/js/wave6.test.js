"use strict";
// Wave 6 in the shell: the six games register with their modes and controls, and each is raced from two phones
// (spec §13.3) — two phones with the same seed (and, for Type Rain's Stages, the same stage list) and the same inputs
// end in exactly the same game, a different seed gives a different board, status() reports the race bar's numbers,
// and the rules never use engine-dependent maths.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { makeSandbox } = require("./helpers");

const plain = (v) => JSON.parse(JSON.stringify(v));
const GAMES = {
  slide: ["Slide Puzzle", ["three", "four", "five", "picture", "picture4"], "four", "touch", "picture"],
  lights: ["Lights Out", ["little", "classic", "big", "climb"], "classic", "touch", "climb"],
  nonogram: ["Picture Logic", ["five", "eight", "ten", "fifteen"], "ten", "touch", "five"],
  tiles: ["Tile Match", ["little", "classic", "big"], "classic", "touch", "little"],
  codebreak: ["Code Breaker", ["little", "classic", "norepeat", "master"], "classic", "touch", "master"],
  typerain: ["Type Rain", ["letters", "easy", "classic", "stages"], "classic", "touch", "stages"],
};

function script(seed) {
  let x = seed >>> 0;
  return () => { x = (Math.imul(x, 1664525) + 1013904223) >>> 0; return x / 4294967296; };
}
const ACTIONS = ["up", "down", "left", "right", "fire", "alt", "hint", "undo", "notes", "shuffle"];
const KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ12345678";

/** One phone: the real game files and kit in a sandbox, played with a fixed stream of keys, typed keys and taps. */
function phone(gameId, mode, seed, frames, levels) {
  const sb = makeSandbox();
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
    if (i % 3 === 0) inst.input("key:" + KEYS[Math.floor(rnd() * KEYS.length)], true);
    if (i % 7 === 0) {
      const px = rnd() * 390, py = rnd() * 487;
      inst.pointer("down", px, py); inst.pointer("move", px + 20 * (rnd() - 0.5), py + 20 * (rnd() - 0.5)); inst.pointer("up", px, py);
    }
    sb.frames(1);
  }
  const out = { logic: JSON.stringify(inst.logic), score: inst.score, level: inst.level, status: plain(inst.status()), ends: plain(ends) };
  inst.destroy();
  return out;
}

test("the six wave 6 games register after wave 5, with their modes, controls, saving and a race", () => {
  const { win } = makeSandbox();
  const ids = plain(win.ArcadeGames.list().map((g) => g.id));
  assert.deepEqual(ids.slice(ids.indexOf("slide"), ids.indexOf("slide") + 6), Object.keys(GAMES));      // (waves 7 and 8 come after)
  for (const [id, [name, modes, def, controls]] of Object.entries(GAMES)) {
    const g = win.ArcadeGames.get(id);
    assert.equal(g.name, name);
    assert.deepEqual(plain(g.modes.map((m) => m.id)), modes);
    assert.equal(g.defaultMode, def);
    assert.equal(g.controls, controls);
    assert.equal(g.stateVersion, 1);
    assert.equal(g.race, true, id);
    assert.equal(g.players, 1);
    assert.ok(g.help.length > 120, id);
    assert.ok(/gentle|little/i.test(g.help) || plain(g.modes).some((m) => /gentle|little/i.test(m.label)), `${id} has something gentle`);
  }
  for (const id of ["nonogram", "tiles", "codebreak", "typerain"]) assert.equal(win.ArcadeGames.get(id).typed, true, id);
  const lightsOpt = win.ArcadeGames.get("lights").options.find((o) => o.id === "hints");
  assert.equal(lightsOpt.offInRaces, true);
  assert.equal(lightsOpt.default, "off", "races play without hints");
});

for (const [id, [, , def, , other]] of Object.entries(GAMES)) {
  test(`${id}: two phones with the same seed play the same game (${def} and ${other})`, () => {
    for (const mode of [def, other]) {
      const a = phone(id, mode, 4242, 900), b = phone(id, mode, 4242, 900);
      assert.equal(a.logic, b.logic, `${mode}: the whole state is identical`);
      assert.equal(a.score, b.score);
      assert.equal(a.level, b.level);
      assert.deepEqual(a.ends, b.ends);
      assert.deepEqual(a.status, b.status);
    }
  });
}

test("Type Rain: the same stage list from the match gives both phones the same stages", () => {
  const levels = [{ name: "Short shower", words: 6, speed: 0.4, gap: 50, shortest: 3, longest: 4 }];
  const a = phone("typerain", "stages", 77, 900, levels), b = phone("typerain", "stages", 77, 900, levels);
  assert.equal(a.logic, b.logic);
  assert.ok(a.logic.includes("Short shower"), "plays the match's list");
});

test("a different seed gives a different board, code or rain (so the seed is what the race shares)", () => {
  for (const [id, [, , def]] of Object.entries(GAMES)) {
    assert.notEqual(phone(id, def, 4242, 60).logic, phone(id, def, 777, 60).logic, id);
  }
});

test("status(): score, level, over and paused for each of them", () => {
  for (const [id, [, , def]] of Object.entries(GAMES)) {
    const sb = makeSandbox();
    const inst = sb.win.ArcadeGames.get(id).create(sb.canvas(), { mode: def, seed: 3 });
    assert.deepEqual(Object.keys(plain(inst.status())), ["score", "level", "over", "paused"], id);
    inst.start(); sb.frames(10); inst.pause();
    assert.deepEqual(plain(inst.status()), { score: inst.score, level: inst.level, over: false, paused: true }, id);
    inst.destroy();
  }
});

// The rules use only + − × ÷, floor/round/sqrt/abs/min/max and imul — never Math.sin/cos/atan2/pow/random or Date —
// so two phones on different browsers get exactly the same boards. Each rules file is loaded and played (every mode,
// with its own taps) with those replaced by functions that throw.
test("the rules never use engine-dependent maths (two browsers make and play exactly the same game)", () => {
  const FORBIDDEN = ["sin", "cos", "tan", "atan", "atan2", "exp", "log", "pow", "hypot", "random", "cbrt", "sinh", "cosh"];
  const ACTS = ["up", "down", "left", "right", "fire", "alt", "hint", "undo", "notes", "shuffle", "key:A", "key:3", "key:ENTER", "key:BACKSPACE"];
  const saved = {}, now = Date.now;
  for (const k of FORBIDDEN) { saved[k] = Math[k]; Math[k] = () => { throw new Error(`Math.${k} in the rules`); }; }
  Date.now = () => { throw new Error("Date.now in the rules"); };
  const dir = path.join(__dirname, "..", "..", "app", "static", "games");
  try {
    for (const [id, [, modes]] of Object.entries(GAMES)) {
      const file = path.join(dir, `${id}-logic.js`);
      delete require.cache[require.resolve(file)];
      const L = require(file);
      for (const mode of modes) {
        const r = script(id.length * 31 + mode.length);
        const s = L.create({ mode, seed: 99, hints: "on" });
        for (let i = 0; i < 3000 && !s.over; i++) {
          if (r() < 0.1) L.press(s, ACTS[Math.floor(r() * ACTS.length)], true);
          if (id === "slide" && r() < 0.05) L.move(s, Math.floor(r() * s.n * s.n));
          if (id === "lights" && r() < 0.05) L.pressCell(s, Math.floor(r() * s.n * s.n));
          if (id === "nonogram" && r() < 0.05) { L.tap(s, Math.floor(r() * s.n * s.n)); L.drag(s, Math.floor(r() * s.n * s.n)); L.endDrag(s); }
          if (id === "tiles" && r() < 0.05) { const p = L.pairsFree(s)[0]; if (p) { L.pick(s, p[0]); L.pick(s, p[1]); } else L.shuffle(s); }
          if (id === "codebreak" && r() < 0.05) L.place(s, 1 + Math.floor(r() * s.colours));
          if (id === "typerain" && r() < 0.2) for (const w of s.words) L.type(s, w.text[w.typed]);
          L.step(s);
        }
        L.result(s);
        if (!s.over) L.restore(JSON.parse(JSON.stringify(L.save(s))));
      }
      delete require.cache[require.resolve(file)];
    }
  } finally { Object.assign(Math, saved); Date.now = now; }
});
