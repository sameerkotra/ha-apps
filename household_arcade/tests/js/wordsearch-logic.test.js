"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const Words = loadLogic("wordsearch-words.js");
const S = loadLogic("wordsearch-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const idx = (s, r, c) => r * s.n + c;
const endOf = (s, w) => idx(s, w.r + S.DIRS[w.d][0] * (w.w.length - 1), w.c + S.DIRS[w.d][1] * (w.w.length - 1));
const find = (s, i) => { const w = s.words[i]; return S.submit(s, idx(s, w.r, w.c), endOf(s, w)); };

test("the themes: ten of them, plain lower-case words, enough of each length for every age group", () => {
  assert.equal(Words.THEMES.length, 10);
  for (const t of Words.THEMES) {
    assert.match(t.id, /^[a-z]+$/);
    const seen = new Set();
    for (const w of t.words) { assert.match(w, /^[a-z]{3,11}$/); assert.ok(!seen.has(w), t.id + " repeats " + w); seen.add(w); }
    for (const id of S.MODE_IDS) {
      const m = S.MODES[id], n = t.words.filter((w) => w.length >= m.lens[0] && w.length <= m.lens[1]).length;
      assert.ok(n >= m.words + 4, `${t.id} has ${n} words for ${id}`);
    }
  }
});

test("each mode makes a grid with the right size and words, each placed once, in allowed directions, deterministic", () => {
  for (const id of S.MODE_IDS) {
    const m = S.MODES[id];
    for (let seed = 1; seed <= 25; seed++) {
      const a = S.generate(id, seed * 31337), b = S.generate(id, seed * 31337);
      assert.deepEqual(a, b);
      assert.equal(a.letters.length, m.n * m.n); assert.match(a.letters, /^[a-z]+$/);
      assert.equal(a.words.length, m.words);
      const texts = a.words.map((p) => p.w);
      assert.equal(new Set(texts).size, texts.length);
      for (const p of a.words) {
        assert.ok(p.w.length >= m.lens[0] && p.w.length <= m.lens[1]);
        assert.ok(m.dirs.includes(p.d), "direction allowed");
        assert.equal(S.occurrences(a.letters, m.n, p.w), 1, p.w + " appears once");
        for (let k = 0; k < p.w.length; k++) assert.equal(a.letters[(p.r + S.DIRS[p.d][0] * k) * m.n + p.c + S.DIRS[p.d][1] * k], p.w[k]);
      }
      for (const x of texts) for (const y of texts) if (x !== y) assert.ok(!x.includes(y) && !y.includes(x));
    }
  }
  const themes = new Set(); for (let i = 1; i < 40; i++) themes.add(S.generate("kids", i * 7).theme);
  assert.ok(themes.size >= 6, "the themes vary");
  const little = S.generate("little", 3), puz = S.generate("puzzler", 3);
  assert.ok(little.words.every((p) => p.d <= 1)); assert.equal(puz.n, 13);
});

test("the harder modes use more directions across many puzzles", () => {
  const dirs = (id) => { const seen = new Set(); for (let i = 1; i < 60; i++) S.generate(id, i * 13).words.forEach((p) => seen.add(p.d)); return seen; };
  assert.deepEqual([...dirs("little")].sort(), [0, 1]);
  assert.deepEqual([...dirs("kids")].sort(), [0, 1, 2]);
  assert.equal(dirs("family").size, 6); assert.equal(dirs("puzzler").size, 8);
});

test("lines and snapping", () => {
  const s = S.create({ mode: "kids", seed: 2 });
  assert.deepEqual(S.line(s, idx(s, 1, 1), idx(s, 1, 4)), [10, 11, 12, 13]);
  assert.deepEqual(S.line(s, idx(s, 3, 3), idx(s, 1, 1)), [30, 20, 10]);
  assert.equal(S.line(s, idx(s, 0, 0), idx(s, 1, 2)), null);
  assert.deepEqual(S.line(s, 5, 5), [5]);
  assert.equal(S.snap(s, idx(s, 4, 4), idx(s, 4, 4)), idx(s, 4, 4));
  assert.equal(S.snap(s, idx(s, 4, 4), idx(s, 4, 8)), idx(s, 4, 8));
  assert.equal(S.snap(s, idx(s, 4, 4), idx(s, 5, 8)), idx(s, 4, 8), "a slight slope is pulled flat");
  assert.equal(S.snap(s, idx(s, 0, 0), idx(s, 7, 6)), idx(s, 7, 7), "near a diagonal pulls onto it");
  assert.equal(S.snap(s, idx(s, 0, 0), idx(s, 8, 1)), idx(s, 8, 0), "near vertical pulls onto it");
  assert.equal(S.snap(s, idx(s, 0, 8), idx(s, 3, 8)), idx(s, 3, 8));
  assert.equal(S.snap(s, idx(s, 8, 8), idx(s, 0, 8)), idx(s, 0, 8), "straight up the edge");
  assert.equal(S.snap(s, idx(s, 8, 0), idx(s, 8, 0)), idx(s, 8, 0));
});

test("marking a word (either way round) finds it once; a wrong line is a miss", () => {
  const s = S.create({ mode: "kids", seed: 5 });
  const w = s.words[2];
  const wrong = S.submit(s, 0, 1);
  assert.deepEqual(wrong.map((e) => e.type), ["miss"]); assert.equal(s.stats.misses, 1);
  const evs = S.submit(s, endOf(s, w), idx(s, w.r, w.c));       // backwards
  assert.deepEqual(evs.map((e) => e.type), ["found"]);
  assert.ok(s.words[2].found); assert.equal(s.foundCount, 1);
  assert.equal(S.submit(s, idx(s, w.r, w.c), endOf(s, w))[0].type, "miss", "found words don't count twice");
  assert.deepEqual(S.submit(s, 3, 3), [], "a single cell is not a word");
  assert.equal(S.submit(s, 0, idx(s, 1, 2)).length, 0, "not in a line");
  assert.equal(s.score, 100);
});

test("tap-tap and drag both mark words; a drag that ends off the line is pulled onto it", () => {
  const s = S.create({ mode: "family", seed: 6 });
  const w = s.words[0], a = idx(s, w.r, w.c), b = endOf(s, w);
  assert.deepEqual(S.tap(s, a).map((e) => e.type), ["select"]); assert.equal(s.anchor, a);
  assert.deepEqual(S.tap(s, a).map((e) => e.type), ["select"]); assert.equal(s.anchor, -1, "tapping it again lets go");
  S.tap(s, a); assert.deepEqual(S.tap(s, b).map((e) => e.type), ["found"]); assert.equal(s.anchor, -1);
  const w2 = s.words[1], a2 = idx(s, w2.r, w2.c), b2 = endOf(s, w2);
  S.dragStart(s, a2); S.dragMove(s, b2); assert.equal(s.drag.to, b2);
  assert.deepEqual(S.dragEnd(s, b2).map((e) => e.type), ["found"]); assert.equal(s.drag, null);
  const w3 = s.words[2];
  S.dragStart(s, idx(s, w3.r, w3.c)); S.dragMove(s, -1);
  assert.deepEqual(S.dragEnd(s, endOf(s, w3)).map((e) => e.type), ["found"]);
  S.dragStart(s, 4); assert.deepEqual(S.dragEnd(s, 4).map((e) => e.type), ["select"], "a drag that goes nowhere is a tap");
  assert.deepEqual(S.dragEnd(s, 4), []);
  S.dragStart(s, -1); assert.equal(s.drag, null);
  S.dragStart(s, 9999); assert.equal(s.drag, null);
});

test("hints point at an unfound word, cost 25 once, and fade", () => {
  const s = S.create({ mode: "kids", seed: 7 });
  let evs = S.hint(s);
  assert.equal(evs[0].type, "hint"); assert.equal(s.hints, 1); assert.equal(s.hint.cell, s.words[0].r * s.n + s.words[0].c);
  assert.match(s.message, /Find /);
  S.hint(s); assert.equal(s.hints, 1, "the hint is still showing");
  for (let i = 0; i < 400; i++) S.step(s);
  assert.equal(s.hint, null); assert.equal(s.message, "");
  find(s, 0);
  assert.equal(s.hint, null);
  assert.equal(S.scoreOf(s), 100 - 25);
  S.hint(s); assert.equal(s.hints, 2); assert.equal(s.hint.word, 1, "the next word");
  find(s, 1); S.hint(s); assert.equal(s.hints, 3);
  const t = S.create({ mode: "little", seed: 7 });
  for (let i = 0; i < 5; i++) { S.hint(t); find(t, i); }
  assert.ok(t.won); assert.deepEqual(S.hint(t), []);
  assert.ok(S.scoreOf(t) >= 0);
});

test("finishing scores 100 a word, less 25 a hint, plus a bonus that shrinks with the time", () => {
  for (const id of S.MODE_IDS) {
    const s = S.create({ mode: id, seed: 8 });
    for (let i = 0; i < 60 * 20; i++) S.step(s);
    s.words.forEach((_w, i) => find(s, i));
    assert.ok(s.won && s.over);
    const m = S.MODES[id];
    assert.equal(s.score, 100 * m.words + Math.max(0, 900 - m.rate * 20));
    assert.ok(s.score <= 2_500 && s.score <= 1_500 + 150 * 20);
    assert.equal(S.status(s).done, true);
    const r = S.result(s);
    assert.equal(r.score, s.score); assert.equal(r.stats.found, m.words);
    assert.match(r.stats.summary[0], new RegExp("Found " + m.words + " of " + m.words));
    assert.ok(r.stats.summary.some((l) => /bonus/.test(l)));
    assert.deepEqual(S.step(s), []); assert.deepEqual(S.press(s, "hint", true), []);
    assert.deepEqual(S.tap(s, 3), []);
  }
  const slow = S.create({ mode: "puzzler", seed: 8 }); slow.updates = 60 * 5000;
  slow.words.forEach((_w, i) => find(slow, i));
  assert.equal(slow.score, 1200);
});

test("keyboard: arrows move a cursor, fire marks the first and last letter", () => {
  const s = S.create({ mode: "little", seed: 9 });
  assert.equal(s.cursor, -1);
  S.press(s, "right", true); assert.equal(s.cursor, 24);
  S.press(s, "up", true); assert.equal(s.cursor, 17);
  S.press(s, "left", true); S.press(s, "down", true); assert.equal(s.cursor, 23);
  for (let i = 0; i < 7; i++) S.press(s, "right", true);
  assert.equal(s.cursor, 23, "the cursor wraps around");
  S.press(s, "fire", true); assert.equal(s.anchor, 23);
  assert.deepEqual(S.press(s, "fire", false), []);
  assert.deepEqual(S.press(s, "nonsense", true), []);
  const w = s.words[0];
  s.anchor = -1; s.cursor = idx(s, w.r, w.c);
  S.press(s, "fire", true);
  s.cursor = endOf(s, w); const evs = S.press(s, "fire", true);
  assert.equal(evs[0].type, "found");
});

test("save and restore keep the game; bad data is refused", () => {
  const s = S.create({ mode: "family", seed: 10 });
  find(s, 0); find(s, 3); S.hint(s);
  for (let i = 0; i < 300; i++) S.step(s);
  const data = JSON.parse(JSON.stringify(S.save(s)));
  const c = S.restore(data);
  assert.deepEqual(c.words, s.words); assert.equal(c.letters, s.letters); assert.equal(c.hints, 1);
  assert.equal(c.foundCount, 2); assert.equal(c.updates, s.updates); assert.equal(c.hint, null);
  find(c, 1); assert.equal(c.foundCount, 3);
  assert.throws(() => S.restore(null), /can't be continued/);
  assert.throws(() => S.restore({ ...data, mode: "nope" }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, letters: data.letters.slice(1) }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, letters: "a".repeat(121) }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, words: data.words.slice(1) }), /can't be continued/);
  const bad = JSON.parse(JSON.stringify(data)); bad.words[0].r = 50;
  assert.throws(() => S.restore(bad), /can't be continued/);
  const moved = JSON.parse(JSON.stringify(data)); moved.words[0].c = (moved.words[0].c + 1) % 11;
  assert.throws(() => S.restore(moved), /can't be continued/);
  const all = JSON.parse(JSON.stringify(data)); all.words.forEach((w) => { w.found = true; });
  assert.throws(() => S.restore(all), /can't be continued/);
  assert.throws(() => S.restore({ ...data, updates: -1 }), /can't be continued/);
  assert.throws(() => S.restore({ ...data, hints: 1.5 }), /can't be continued/);
});

test("registry contract", () => {
  const sb = makeSandbox({ extra: ["wordsearch-words.js", "wordsearch-logic.js", "wordsearch.js"] });
  const def = sb.win.ArcadeGames.get("wordsearch");
  assert.equal(def.controls, "touch");
  assert.deepEqual(Array.from(def.modes, (m) => m.id), ["little", "kids", "family", "puzzler"]);
  assert.deepEqual(Array.from(def.buttons, (b) => b.action), ["hint"]);
});

for (const look of LOOKS) {
  test(`word search renders and plays in the ${look} look with the pointer`, () => {
    const sb = makeSandbox({ extra: ["wordsearch-words.js", "wordsearch-logic.js", "wordsearch.js"] });
    const def = sb.win.ArcadeGames.get("wordsearch");
    const canvas = sb.canvas(390, 487);
    const ends = [], events = [];
    const inst = def.create(canvas, { mode: "puzzler", look, seed: 4, onEnd: (r) => ends.push(r), onEvent: (t) => events.push(t) });
    inst.start();
    const L = inst.logic, k = 390 / 240, cell = Math.floor(208 / L.n), gx = 120 - L.n * cell / 2;
    const at = (i) => [(gx + (i % L.n) * cell + cell / 2) * k, (38 + Math.floor(i / L.n) * cell + cell / 2) * k];
    const drag = (a, b) => { inst.pointer("down", ...at(a)); inst.pointer("move", ...at(b)); sb.frames(2); inst.pointer("up", ...at(b)); sb.frames(2); };
    drag(0, 1); assert.ok(events.includes("miss"));
    inst.pointer("down", 1, 1); inst.pointer("up", 1, 1);
    const w0 = L.words[0];
    inst.pointer("down", ...at(w0.r * L.n + w0.c)); inst.pointer("move", ...at(w0.r * L.n + w0.c)); sb.frames(2);
    inst.pointer("up", ...at(w0.r * L.n + w0.c)); sb.frames(2);
    assert.equal(L.anchor, w0.r * L.n + w0.c);
    inst.pointer("down", ...at(endOf(L, w0))); inst.pointer("up", ...at(endOf(L, w0))); sb.frames(3);
    assert.equal(L.foundCount, 1); assert.ok(events.includes("found"));
    inst.input("hint", true); inst.input("left", true); inst.input("fire", true); sb.frames(5);
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    canvas.clientWidth = 390; canvas.clientHeight = 487; inst.resize(); sb.frames(2);
    L.anchor = -1;
    for (let i = 1; i < L.words.length; i++) {
      const w = L.words[i];
      drag(w.r * L.n + w.c, endOf(L, w));
    }
    sb.frames(5);
    assert.equal(ends.length, 1);
    assert.ok(ends[0].stats.won); assert.equal(ends[0].stats.found, 12);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
    assert.equal(sb.pending(), 0);
  });
}
