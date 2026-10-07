"use strict";
// Type Rain (wave 6): the word list, falling words from the seed (never from the typing), typing and slips, runs,
// lives, levels, the stage list, saving, the honest-score limits, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const R = loadLogic("typerain-logic.js");
const Words = loadLogic("typerain-words.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const LIMIT = { perSecond: 600, base: 1000, max: 5000000 };

function types(evs) { return evs.map((e) => e.type); }
function stage(extra) { return Object.assign({ name: "Test", words: 6, speed: 0.3, gap: 60, shortest: 3, longest: 4 }, extra || {}); }
/** A perfect typist: types every word as soon as it appears. */
function typeAll(s) { for (const w of s.words.slice()) for (const ch of w.text.slice(w.typed)) R.type(s, ch); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...R.step(s)); } return all; }

test("the word list: family words of 3–10 letters a–z, no repeats, enough of each length", () => {
  assert.ok(Words.WORDS.length > 500);
  assert.equal(new Set(Words.WORDS).size, Words.WORDS.length);
  for (const w of Words.WORDS) assert.match(w, /^[a-z]{3,10}$/);
  for (let n = 3; n <= 8; n++) assert.ok(R.BY_LEN[n].length >= 30, `${n} letters`);
  for (const bad of ["kill", "dead", "gun", "hate", "stupid"]) assert.ok(!Words.WORDS.includes(bad), bad);
});

test("words fall from the seed at a steady pace; Little ones drops single letters", () => {
  const s = R.create({ mode: "classic", seed: 3 });
  const evs = run(s, 600);
  const spawns = evs.filter((e) => e.type === "spawn");
  assert.ok(spawns.length >= 4);
  for (const w of s.words) {
    assert.ok(w.x >= R.LEFT && w.x + w.text.length * R.CHAR_W + R.PAD <= R.RIGHT + 0.01, "inside the sky");
    assert.ok(w.text.length >= 3 && w.text.length <= 4, "short words at level 1");
  }
  const k = R.create({ mode: "letters", seed: 3 });
  run(k, 600);
  assert.ok(k.words.length > 0 && k.words.every((w) => /^[a-z]$/.test(w.text)));
  assert.equal(k.lives, 5);
});

test("the same words in the same order whatever the typing (what a race shares)", () => {
  const a = R.create({ mode: "classic", seed: 77 }), b = R.create({ mode: "classic", seed: 77 });
  const wa = [], wb = [];
  for (let i = 0; i < 60 * 90; i++) {
    if (i % 3 === 0) typeAll(a);            // a types everything, b nothing (it loses lives)
    if (b.over) break;
    for (const e of R.step(a)) if (e.type === "spawn") wa.push(e.word);
    for (const e of R.step(b)) if (e.type === "spawn") wb.push(e.word);
  }
  assert.ok(wb.length > 5);
  assert.deepEqual(wa.slice(0, wb.length), wb);
  const st = [stage({ name: "A" }), stage({ name: "B", shortest: 5, longest: 6 })];
  const c = R.create({ mode: "stages", seed: 5, levels: st }), d = R.create({ mode: "stages", seed: 5, levels: st });
  const wc = [], wd = [];
  for (let i = 0; i < 60 * 120 && !(c.over && d.over); i++) {
    typeAll(c); if (i % 2) typeAll(d);      // different speeds of typing: stage 2 starts at different times
    for (const e of R.step(c)) if (e.type === "spawn") wc.push(e.word);
    for (const e of R.step(d)) if (e.type === "spawn") wd.push(e.word);
  }
  assert.deepEqual(wc, wd, "each stage's words come from the seed, not the clock");
});

test("typing: the first letter picks the lowest word starting with it; a wrong letter is a slip; Backspace lets go", () => {
  const s = R.create({ mode: "classic", seed: 1 });
  s.words = [{ id: 1, text: "cat", x: 10, y: 100, v: 0.3, typed: 0 }, { id: 2, text: "cow", x: 100, y: 150, v: 0.3, typed: 0 },
    { id: 3, text: "dog", x: 150, y: 170, v: 0.3, typed: 0 }];
  s.nextId = 4;
  R.type(s, "c");
  assert.equal(s.target, 2, "the lower of cat and cow");
  assert.deepEqual(types(R.type(s, "a")), ["slip"]);
  assert.equal(s.target, 2, "still picked");
  assert.equal(s.stats.slips, 1);
  R.press(s, "key:BACKSPACE", true);
  assert.equal(s.target, -1);
  R.press(s, "key:C", true); R.press(s, "key:O", true);
  const evs = R.press(s, "key:W", true);
  assert.ok(types(evs).includes("word"));
  assert.equal(s.score, 30);
  assert.equal(s.words.length, 2);
  assert.deepEqual(types(R.type(s, "z")), ["slip"], "no word starts with z");
  assert.deepEqual(types(R.type(s, "?")), [], "not a letter");
});

test("a run of words without a slip doubles the points from 10 and triples them from 25; a landing breaks it", () => {
  const s = R.create({ mode: "classic", seed: 2 });
  let points = [];
  for (let n = 1; n <= 26; n++) {
    s.words = [{ id: 100 + n, text: "sun", x: 10, y: 100, v: 0.3, typed: 0 }];
    for (const ch of "sun") for (const e of R.type(s, ch)) if (e.type === "word") points.push(e.points);
  }
  assert.deepEqual(points.slice(0, 9), Array(9).fill(30));
  assert.equal(points[9], 60);
  assert.equal(points[24], 90);
  assert.equal(s.stats.bestRun, 26);
  s.words = [{ id: 999, text: "sea", x: 10, y: R.GROUND - 0.1, v: 0.3, typed: 0 }];
  const evs = R.step(s);
  assert.ok(types(evs).includes("land"));
  assert.equal(s.run, 0);
  assert.equal(s.lives, 2);
});

test("lives: every landed word costs one; none left ends the game", () => {
  const s = R.create({ mode: "easy", seed: 4 });
  const evs = run(s, 60 * 120);
  assert.ok(s.over);
  assert.equal(evs.filter((e) => e.type === "land").length, 3);
  assert.equal(R.result(s).stats.cause, "landed");
  assert.equal(R.result(s).stats.landed, 3);
});

test("levels come with time (faster, longer words), capped", () => {
  const s = R.create({ mode: "classic", seed: 6 });
  assert.deepEqual(R.settings(s), { speed: 0.3, gap: 130, shortest: 3, longest: 4 });
  run(s, R.LEVEL_T * 3 + 1, typeAll);
  assert.equal(s.level, 4);
  const set = R.settings(s);
  assert.ok(set.speed > 0.3 && set.gap < 130 && set.longest === 5);
  s.level = 99; s.updates = R.LEVEL_T * 200;
  R.step(s);
  assert.equal(s.level, R.MAX_LEVEL);
  assert.ok(R.settings(s).speed <= R.MODES.classic.vmax && R.settings(s).gap >= R.GAP_MIN && R.settings(s).longest <= 10);
});

test("Stages: built-in or the session's list; bad stages are skipped; clearing one moves on, the last wins", () => {
  assert.equal(R.usableLevels(undefined), R.LEVELS);
  for (const lv of R.LEVELS) assert.ok(R.stageOk(lv), lv.name);
  const bad = [stage({ words: 4 }), stage({ words: 41 }), stage({ speed: 0.1 }), stage({ gap: 39 }), stage({ shortest: 2, longest: 3 }),
    stage({ shortest: 5, longest: 4 }), stage({ shortest: 1, longest: 4 }), stage({ longest: 10, speed: 0.6 }), stage({ name: "" }),
    stage({ words: 5.5 }), null, 7];
  assert.equal(R.usableLevels(bad), R.LEVELS);
  assert.deepEqual(R.usableLevels(bad.concat([stage({ name: "Fine" })])).map((t) => t.name), ["Fine"]);
  const two = [stage({ name: "A" }), stage({ name: "B", shortest: 1, longest: 1 })];
  const s = R.create({ mode: "stages", seed: 3, levels: two });
  assert.equal(R.create({ mode: "classic", levels: two }).stages, null, "only Stages plays the list");
  const all = run(s, 60 * 120, typeAll);
  assert.deepEqual(types(all).filter((t) => t === "stage" || t === "level" || t === "win"), ["stage", "level", "stage", "win"]);
  assert.ok(s.won);
  assert.equal(R.result(s).stats.stages, 2);
  assert.equal(all.filter((e) => e.type === "spawn").length, 12);
  assert.ok(all.filter((e) => e.type === "spawn").slice(6).every((e) => e.word.length === 1), "stage B is letters");
  const data = JSON.parse(JSON.stringify(R.save(s)));
  assert.ok(!("stages" in data));
  assert.equal(R.restore(data, two).stages.length, 2);
});

test("deterministic for a seed; save and restore plays on the same; bad saves are refused", () => {
  const play = (seed) => { const s = R.create({ seed }); run(s, 60 * 40, (st) => { if (st.updates % 7 === 0) typeAll(st); }); return s; };
  assert.equal(JSON.stringify(play(9)), JSON.stringify(play(9)));
  assert.notEqual(JSON.stringify(play(9).recent), JSON.stringify(play(10).recent));
  const s = R.create({ seed: 12, mode: "stages" });
  run(s, 900, typeAll);
  const copy = R.restore(JSON.parse(JSON.stringify(R.save(s))));
  for (let i = 0; i < 2000 && !s.over; i++) { if (i % 5 === 0) { typeAll(s); typeAll(copy); } R.step(s); R.step(copy); }
  assert.equal(JSON.stringify(R.save(copy)), JSON.stringify(R.save(s)));
  assert.throws(() => R.restore({ mode: "classic", words: [{ text: "NO!" }] }), /can't be continued/);
  assert.throws(() => R.restore(null));
});

test("honest score: a perfect typist stays inside the limits in every mode, also on the fastest stages allowed", () => {
  const fastest = [];
  for (let i = 0; i < 6; i++) fastest.push(stage({ name: "Fast " + i, words: 5, gap: 40, speed: 0.55, shortest: 8, longest: 10 }));
  assert.ok(fastest.every(R.stageOk));
  let top = 0;
  for (const [mode, levels] of [["letters"], ["easy"], ["classic"], ["stages"], ["stages", fastest]]) {
    const s = R.create({ seed: 1, mode, levels });
    while (!s.over && s.updates < 60 * 60 * 15) {
      typeAll(s); R.step(s);
      const sec = s.updates / 60;
      assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
      assert.ok(s.score <= LIMIT.max && s.level <= 100);
      if (sec > 5) top = Math.max(top, s.score / sec);
    }
  }
  console.log(`# typerain: fastest rate ${top.toFixed(1)} points a second`);
});

test("fuzz: random keys never throw and keep the sky sound", () => {
  for (const mode of Object.keys(R.MODES)) {
    for (let seed = 1; seed <= 8; seed++) {
      const s = R.create({ seed, mode });
      let x = seed * 7919, last = 0;
      for (let i = 0; i < 5000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 3 === 0) R.press(s, "key:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[x % 26], true);
        if (x % 41 === 0) R.press(s, "key:BACKSPACE", true);
        if (x % 13 === 0) R.press(s, ["up", "fire", "alt", "key:1", "key:ENTER"][x % 5], true);
        R.step(s);
        assert.ok(s.score >= last); last = s.score;
        assert.ok(s.lives >= 0 && s.level >= 1);
        for (const w of s.words) assert.ok(w.typed >= 0 && w.typed < w.text.length && w.y < R.GROUND);
        assert.ok(s.target === -1 || R.find(s, s.target) >= 0);
      }
    }
  }
});

test("typerain renders and plays in every look, with typed keys and taps on the keyboard", () => {
  for (const look of LOOKS) {
    for (const mode of ["letters", "classic", "stages"]) {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("typerain");
      assert.equal(def.name, "Type Rain");
      assert.equal(def.typed, true);
      assert.equal(def.stateVersion, 1);
      const inst = def.create(canvas, { mode, look, seed: 5 });
      inst.start();
      const k = Math.min(390 / 240, 487 / 300);
      for (let i = 0; i < 700; i++) {
        const s = inst.logic;
        if (i % 4 === 0 && s.words.length) {
          const w = s.words[0];
          inst.input("key:" + w.text[w.typed].toUpperCase(), true);
        }
        if (i % 23 === 0) inst.pointer("down", (10 + (i * 7) % 220) * k, (210 + (i % 3) * 31) * k);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(inst.logic.stats.typed > 0, "words were typed");
      if (inst.state !== "over") assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});

test("a controller types with a ring on the on-screen keyboard (A types the key, X lets go)", () => {
  const sb = makeSandbox();
  const inst = sb.win.ArcadeGames.get("typerain").create(sb.canvas(390, 487), { mode: "letters", seed: 9 });
  inst.start();
  sb.frames(R.FIRST_T + 2);
  const s = inst.logic;
  s.words = [{ id: 500, text: "w", x: 50, y: 120, v: 0.1, typed: 0 }];
  inst.input("right", true);             // the ring appears on Q
  inst.input("right", true);             // … and moves to W
  inst.input("fire", true);
  sb.frames(1);
  assert.equal(s.stats.typed, 1, "W was typed");
  s.words = [{ id: 501, text: "cat", x: 50, y: 120, v: 0.1, typed: 0 }];
  inst.input("key:C", true);
  assert.equal(s.target, 501);
  inst.input("alt", true);
  assert.equal(s.target, -1, "X lets go");
  inst.destroy();
});
