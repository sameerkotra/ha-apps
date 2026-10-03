"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const N = loadLogic("numbers-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const PER_SECOND = 900, BASE = 200, MAX_SCORE = 5_000_000;
function types(evs) { return evs.map((e) => e.type); }
function rightDir(s) { return N.DIRS[s.q.choices.indexOf(s.q.answer)]; }
function wrongDir(s) { return N.DIRS[s.q.choices.findIndex((c) => c !== s.q.answer)]; }
function evalText(t) {
  const m = t.replace(/−/g, "-").replace(/×/g, "*").replace(/÷/g, "/").match(/^(\d+) ([-+*/]) (\d+)$/);
  assert.ok(m, `question text "${t}"`);
  const a = +m[1], b = +m[3];
  return m[2] === "+" ? a + b : m[2] === "-" ? a - b : m[2] === "*" ? a * b : a / b;
}
function until(s, pred, max = 1000) { for (let i = 0; i < max && !pred(s); i++) N.step(s); }

test("every question: the text adds up, four different non-negative answers, exactly one right", () => {
  for (const mode of ["add", "times", "mixed"]) {
    const s = N.create({ mode, seed: 1 });
    for (let level = 1; level <= N.MAX_LEVEL; level++) {
      s.level = level;
      for (let k = 0; k < 150; k++) {
        const q = N.makeQuestion(s);
        assert.equal(evalText(q.text), q.answer, q.text);
        assert.ok(Number.isInteger(q.answer) && q.answer >= 0, `whole answer: ${q.text}`);
        assert.equal(q.choices.length, 4);
        assert.equal(new Set(q.choices).size, 4, `different: ${q.choices}`);
        assert.equal(q.choices.filter((c) => c === q.answer).length, 1);
        for (const c of q.choices) assert.ok(Number.isInteger(c) && c >= 0, `${c}`);
        // near misses: within 100 of the answer, or its digits swapped, or a neighbouring table entry
        for (const c of q.choices) assert.ok(Math.abs(c - q.answer) <= Math.max(100, q.answer), `${q.text}: ${c}`);
      }
    }
  }
});

test("the numbers grow with the level", () => {
  const s = N.create({ mode: "add", seed: 2 });
  const sample = (level, n = 300) => { s.level = level; const out = []; for (let i = 0; i < n; i++) out.push(N.addPair(s, level)); return out; };
  for (const [a, b] of sample(1)) assert.ok(a <= 5 && b <= 5);
  for (const [a, b] of sample(2)) assert.ok(a <= 9 && b <= 9);
  for (const [a, b] of sample(3)) { assert.ok(a >= 10 && a <= 99 && b <= 9); assert.ok((a % 10) + b < 10, "no carrying"); }
  for (const [a, b] of sample(4)) { assert.ok(b >= 10); assert.ok((a % 10) + (b % 10) < 10, "no carrying"); }
  for (const [a, b] of sample(5)) { assert.ok((a % 10) + (b % 10) >= 10, "carrying"); assert.ok(a + b <= 99); }
  for (const [a, b] of sample(13)) assert.ok(a >= 100 && b >= 100);
  const t = N.create({ mode: "times", seed: 3 });
  for (let i = 0; i < 300; i++) { const p = N.timesPair(t, 1); assert.ok([2, 5, 10].includes(p[0]) || [2, 5, 10].includes(p[1])); assert.ok(p[0] <= 10 && p[1] <= 10); }
  let max = 0;
  for (let i = 0; i < 500; i++) { const p = N.timesPair(t, 8); max = Math.max(max, p[0], p[1]); }
  assert.equal(max, 12);
  const seen = new Set();
  t.level = 3;
  for (let i = 0; i < 300; i++) seen.add(N.makeQuestion(t).text.replace(/\d+/g, "").trim());
  assert.ok(seen.has("×") && seen.has("÷"));
  const m = N.create({ mode: "mixed", seed: 4 });
  const ops = new Set();
  for (let i = 0; i < 300; i++) ops.add(N.makeQuestion(m).text.replace(/\d+/g, "").trim());
  assert.equal(ops.size, 4);
});

test("a right answer: 10 + 2 × level, +1 s (at most 60), the next sum 12 updates later; input ignored meanwhile", () => {
  const s = N.create({ seed: 5 });
  const q0 = s.q;
  N.step(s);
  const evs = N.press(s, rightDir(s), true);
  assert.deepEqual(types(evs), ["right"]);
  assert.equal(s.score, 12);
  assert.equal(s.clock, N.CLOCK, "capped at 60 s");
  assert.equal(s.last.ok, true);
  assert.deepEqual(N.press(s, "up", true), []);
  assert.deepEqual(N.press(s, "down", true), []);
  for (let i = 0; i < 11; i++) assert.deepEqual(types(N.step(s)), []);
  assert.equal(s.q, q0);
  assert.deepEqual(types(N.step(s)), ["question"]);
  assert.notEqual(s.q, q0);
  assert.equal(s.last, null);
  assert.deepEqual(N.press(s, "fire", true), []);
  assert.deepEqual(N.press(s, rightDir(s), false), []);
  s.clock = 1000;
  N.press(s, rightDir(s), true);
  assert.equal(s.clock, 1060);
});

test("a wrong answer: −3 s, the streak resets, the right tile is shown", () => {
  const s = N.create({ seed: 6 });
  N.press(s, rightDir(s), true);
  until(s, (t) => t.wait === 0);
  const right = s.q.choices.indexOf(s.q.answer);
  const c = s.clock;
  assert.deepEqual(types(N.press(s, wrongDir(s), true)), ["wrong"]);
  assert.equal(s.clock, c - N.WRONG_COST);
  assert.equal(s.streak, 0);
  assert.equal(s.last.right, right);
  assert.equal(s.last.ok, false);
  assert.equal(s.wait, N.NEXT_WRONG);
  assert.equal(s.score, 12);
  assert.equal(N.result(s).stats.wrong, 1);
});

test("streaks: double from 5 in a row, triple from 10; a level every 8 right answers", () => {
  const s = N.create({ seed: 7 });
  const got = [];
  for (let n = 0; n < 16; n++) {
    until(s, (t) => t.wait === 0);
    const evs = N.press(s, rightDir(s), true);
    got.push(evs.find((e) => e.type === "right").points);
    if (n === 4) assert.ok(types(evs).includes("streak"));
    if (n === 7) assert.ok(types(evs).includes("level"));
  }
  assert.deepEqual(got.slice(0, 4), [12, 12, 12, 12]);
  assert.deepEqual(got.slice(4, 8), [24, 24, 24, 24]);
  assert.deepEqual(got.slice(8, 9), [28]);       // level 2, ×2
  assert.deepEqual(got.slice(9, 12), [42, 42, 42]); // ×3 from the 10th
  assert.equal(s.level, 3);
  assert.equal(N.result(s).stats.bestStreak, 16);
  assert.equal(N.points(1, 1), 12);
  assert.equal(N.points(N.MAX_LEVEL, 99), 180);
});

test("the clock running out ends the game; so can wrong answers", () => {
  const s = N.create({ mode: "times", seed: 8 });
  const evs = [];
  for (let i = 0; i < 4000 && !s.over; i++) evs.push(...N.step(s));
  assert.ok(s.over);
  assert.equal(s.updates, N.CLOCK);
  assert.deepEqual(types(evs).slice(-1), ["gameover"]);
  assert.deepEqual(N.step(s), []);
  assert.deepEqual(N.press(s, "up", true), []);
  const w = N.create({ seed: 9 });
  let n = 0;
  while (!w.over && n < 100) { until(w, (t) => t.wait === 0 || t.over); if (w.over) break; N.press(w, wrongDir(w), true); n++; }
  assert.ok(w.over);
  assert.ok(n <= 20, `${n} wrong answers`);
  assert.equal(N.result(w).stats.mode, "add");
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const bot = (s) => { if (s.wait === 0 && s.updates % 40 === 0) return N.press(s, s.updates % 120 ? rightDir(s) : wrongDir(s), true); return []; };
  const play = (seed, mode) => { const s = N.create({ seed, mode }); while (!s.over && s.updates < 60 * 200) { bot(s); N.step(s); } return JSON.stringify(N.result(s)) + s.updates + s.q.text + s.q.choices; };
  assert.equal(play(11, "mixed"), play(11, "mixed"));
  assert.notEqual(play(11, "mixed"), play(12, "mixed"));
  for (const mode of ["add", "times", "mixed"]) {
    const s = N.create({ seed: 13, mode });
    for (let i = 0; i < 700; i++) { bot(s); N.step(s); }
    const copy = N.restore(JSON.parse(JSON.stringify(N.save(s))));
    for (let i = 0; i < 2000 && !s.over; i++) {
      assert.deepEqual(bot(copy), bot(s));
      assert.deepEqual(N.step(copy), N.step(s));
    }
    assert.equal(JSON.stringify(copy), JSON.stringify(s));
  }
  assert.throws(() => N.restore({ q: 1 }), /can't be continued/);
  assert.throws(() => N.restore("x"), /can't be continued/);
});

test("honest score: a frame-perfect bot stays within seconds × 900 + 200", () => {
  let best = 0;
  for (const mode of ["add", "times", "mixed"]) {
    const s = N.create({ mode, seed: 21 });
    while (!s.over && s.updates < 60 * 600) {
      N.press(s, rightDir(s), true);   // answers the very update a sum appears
      N.step(s);
      assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates} updates`);
      assert.ok(s.score <= MAX_SCORE && s.level <= 100);
    }
    best = Math.max(best, s.score / (s.updates / 60));
    assert.ok(s.level === N.MAX_LEVEL);
  }
  assert.ok(best > 400, `fastest ${best.toFixed(0)} a second`);
  console.log(`# numbers: perfect bot's fastest rate ${best.toFixed(0)} points a second`);
});

test("numbers renders and plays in every look, with taps and keys, switching looks mid-game", () => {
  // tile centres (logical), in the order up, left, right, down
  const centres = [[120, 129], [66, 177], [174, 177], [120, 225]];
  for (const look of LOOKS) {
    for (const mode of ["add", "times", "mixed", "challenge"]) {
      const sb = makeSandbox({ extra: ["numbers-logic.js", "numbers.js"] });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("numbers");
      assert.equal(def.controls, "touch");
      assert.equal(def.name, "Number Dash");
      assert.deepEqual(JSON.parse(JSON.stringify(def.modes.map((m) => m.id))), ["add", "times", "mixed", "challenge"]);
      const inst = def.create(canvas, { mode, look, seed: 5, levels: mode === "challenge" ? HARD.concat(N.LEVELS) : undefined });
      assert.ok((sb.counts.fillRect || 0) + (sb.counts.drawImage || 0) > 0);
      inst.start();
      const s = inst.logic;
      let taps = 0;
      for (let i = 0; i < 400; i++) {
        if (i % 20 === 5 && s.wait === 0) {
          const k = s.q.choices.indexOf(s.q.answer);
          inst.pointer("down", centres[k][0] * 1.625, centres[k][1] * 1.625); inst.pointer("up", 0, 0); taps++;
        }
        if (i % 20 === 15) inst.input(["up", "left", "right", "down"][(i / 20 | 0) % 4], true);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      assert.ok(s.stats.right >= taps - 1 && taps > 5, `taps answered right (${s.stats.right}/${taps})`);
      assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});

// ---- Challenges (the level list) ----
// The hardest challenges the field ranges allow (app/level_kinds/numbers.py): every sum, top 999, the
// fewest right answers (each challenge cleared as fast as possible) and the shortest clock allowed.
const HARD = [{ name: "Hardest", ops: "+-x/", top: 999, right: 5, time: 20 }];

test("Challenges: built-in or the session's list; bad ones skipped; nothing usable means the built-ins", () => {
  assert.equal(N.usableLevels(undefined), N.LEVELS);
  assert.equal(N.usableLevels([]), N.LEVELS);
  assert.ok(N.LEVELS.length >= 5 && N.LEVELS.length <= 10);
  const bad = [
    null, { name: "No ops", ops: "", top: 10, right: 5, time: 30 }, { name: "Odd", ops: "+%", top: 10, right: 5, time: 30 },
    { name: "Small", ops: "+", top: 4, right: 5, time: 30 }, { name: "Many", ops: "+", top: 10, right: 26, time: 90 },
    { name: "Short", ops: "+", top: 10, right: 5, time: 9 }, { name: "Rushed", ops: "+", top: 200, right: 10, time: 25 },
    { name: "Half", ops: "+", top: 10.5, right: 5, time: 30 }, { name: 3, ops: "+", top: 10, right: 5, time: 30 },
  ];
  assert.equal(N.usableLevels(bad), N.LEVELS);
  const ok = { name: "Fine", ops: "x", top: 7, right: 5, time: 20 };
  assert.deepEqual(N.usableLevels(bad.concat([ok])), [ok]);
  for (const c of N.LEVELS) assert.ok(c.time >= N.minTime(c), c.name);
});

test("Challenges: the sums keep to the challenge's signs and top (tables up to min(top, 12))", () => {
  const lists = [
    [{ name: "A", ops: "+", top: 5, right: 5, time: 30 }, ["+"], (q) => q.answer <= 5],
    [{ name: "B", ops: "-", top: 999, right: 5, time: 30 }, ["−"], (q) => q.a <= 999],
    [{ name: "C", ops: "x", top: 5, right: 5, time: 30 }, ["×"], (q) => Math.min(q.a, q.b) <= 5 && Math.max(q.a, q.b) <= 10],
    [{ name: "D", ops: "/", top: 400, right: 5, time: 30 }, ["÷"], (q) => q.b <= 12 && q.answer <= 12],
    [{ name: "E", ops: "+-x/", top: 999, right: 5, time: 30 }, ["+", "−", "×", "÷"], (q) => q.answer <= 999],
  ];
  for (const [c, signs, fits] of lists) {
    const s = N.create({ mode: "challenge", seed: 3, levels: [c] });
    const seen = new Set();
    for (let k = 0; k < 400; k++) {
      const q = N.makeQuestion(s);
      assert.equal(evalText(q.text), q.answer, q.text);
      assert.ok(fits(q), `${c.name}: ${q.text}`);
      assert.equal(new Set(q.choices).size, 4);
      assert.equal(q.choices.filter((x) => x === q.answer).length, 1);
      for (const x of q.choices) assert.ok(Number.isInteger(x) && x >= 0);
      seen.add(q.text.replace(/\d+/g, "").trim());
    }
    assert.deepEqual([...seen].sort(), [...signs].sort(), c.name);
  }
});

test("Challenges: the right answers clear one → the next with a fresh clock; the last wins; time ends it", () => {
  const two = [{ name: "One", ops: "+", top: 10, right: 5, time: 30 }, { name: "Two", ops: "x", top: 6, right: 6, time: 40 }];
  const s = N.create({ mode: "challenge", seed: 4, levels: two });
  assert.equal(s.clock, 30 * 60);
  assert.equal(s.challenges.length, 2);
  const evs = [];
  while (!s.over && s.updates < 60 * 60) {
    if (s.wait === 0) evs.push(...N.press(s, rightDir(s), true));
    evs.push(...N.step(s));
    if (s.level === 1) assert.ok(s.clock <= 30 * 60, "never above the start");
  }
  const lv = evs.filter((e) => e.type === "level");
  assert.equal(lv.length, 1);
  assert.equal(lv[0].name, "Two");
  assert.ok(s.won && s.over);
  assert.deepEqual(types(evs).slice(-1), ["win"]);
  assert.equal(s.stats.right, 11);
  const r = N.result(s);
  assert.equal(r.level, 2);
  assert.equal(r.stats.won, true);
  assert.equal(r.stats.cause, "won");
  assert.equal(r.stats.challenges, 2);
  assert.equal(r.stats.mode, "challenge");
  // the clock is fresh after a clear
  const t = N.create({ mode: "challenge", seed: 5, levels: two });
  for (let n = 0; n < 5; n++) { until(t, (x) => x.wait === 0); for (let i = 0; i < 100; i++) N.step(t); N.press(t, rightDir(t), true); }
  assert.equal(t.level, 2);
  assert.equal(t.clock, 40 * 60);
  // doing nothing: the clock ends the game, not won
  const u = N.create({ mode: "challenge", seed: 6, levels: two });
  for (let i = 0; i < 5000 && !u.over; i++) N.step(u);
  assert.equal(u.updates, 30 * 60);
  assert.equal(N.result(u).stats.won, false);
  assert.equal(N.result(u).stats.cause, "time");
  // the multiplier's level is capped, however long the list
  const long = Array.from({ length: 60 }, (_, i) => ({ name: "Lap", ops: "+", top: 10 + i, right: 5, time: 20 }));
  const v = N.create({ mode: "challenge", seed: 7, levels: long });
  v.level = 60; v.streak = 20;
  until(v, (x) => x.wait === 0);
  assert.equal(N.press(v, rightDir(v), true)[0].points, 180);
});

test("Challenges: save and restore keeps going with the list; the list isn't saved; old saves continue", () => {
  const list = N.LEVELS.slice(0, 3);
  const bot = (s) => (s.wait === 0 && s.updates % 30 === 0 ? N.press(s, s.updates % 150 ? rightDir(s) : wrongDir(s), true) : []);
  const s = N.create({ mode: "challenge", seed: 8, levels: list });
  for (let i = 0; i < 900; i++) { bot(s); N.step(s); }
  assert.ok(s.level >= 2, `level ${s.level}`);
  const data = JSON.parse(JSON.stringify(N.save(s)));
  assert.ok(!("challenges" in data));
  const copy = N.restore(data, list);
  assert.equal(copy.challenges.length, 3);
  for (let i = 0; i < 3000 && !s.over; i++) {
    assert.deepEqual(bot(copy), bot(s));
    assert.deepEqual(N.step(copy), N.step(s));
  }
  assert.equal(copy.score, s.score);
  assert.equal(copy.won, s.won);
  // without a list, the built-ins; an old save (before Challenges) still continues
  assert.equal(N.restore(data).challenges, N.LEVELS);
  const old = N.create({ mode: "add", seed: 9 });
  const o = JSON.parse(JSON.stringify(N.save(old)));
  delete o.won; delete o.cright; delete o.challenges; delete o.stats.challenges; delete o.stats.cause;
  const back = N.restore(o);
  assert.equal(back.challenges, null);
  N.press(back, rightDir(back), true);
  assert.equal(back.score, 12);
  assert.equal(N.result(back).stats.challenges, 0);
});

test("honest score in Challenges: a frame-perfect bot on the hardest lists stays within seconds × 900 + 200", () => {
  let best = 0;
  const longest = Array.from({ length: 100 }, (_, i) => Object.assign({}, HARD[0], { name: "Hard " + i }));
  for (const levels of [HARD, longest, N.LEVELS]) {
    for (const seed of [31, 32]) {
      const s = N.create({ mode: "challenge", seed, levels });
      while (!s.over && s.updates < 60 * 600) {
        N.press(s, rightDir(s), true);
        N.step(s);
        assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates} updates`);
        assert.ok(s.score <= MAX_SCORE && s.level <= 100 && s.level <= levels.length);
      }
      assert.ok(s.won, "the list is played to the end");
      best = Math.max(best, s.score / (s.updates / 60));
    }
  }
  console.log(`# numbers challenges: perfect bot's fastest rate ${best.toFixed(0)} points a second`);
});
