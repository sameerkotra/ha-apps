"use strict";
// Code Breaker (wave 6): codes from the seed, the black/white feedback, entering and checking a row, the score,
// saving, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const C = loadLogic("codebreak-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

function types(evs) { return evs.map((e) => e.type); }
function enter(s, guess) { for (const g of guess) C.place(s, g); return C.check(s); }

test("codes from the seed fit their mode (length, symbols, repeats)", () => {
  for (const [mode, m] of Object.entries(C.MODES)) {
    for (let seed = 1; seed <= 50; seed++) {
      const s = C.create({ mode, seed });
      assert.equal(s.code.length, m.pegs);
      assert.ok(s.code.every((c) => c >= 1 && c <= m.colours));
      if (!m.repeats) assert.equal(new Set(s.code).size, m.pegs, `${mode} ${seed}: no repeats`);
      assert.equal(s.maxRows, m.rows);
    }
  }
  assert.equal(C.create({}).mode, "classic");
  const seen = new Set();
  for (let seed = 1; seed <= 40; seed++) seen.add(C.create({ mode: "classic", seed }).code.join(""));
  assert.ok(seen.size > 30, "codes vary with the seed");
});

test("feedback: black for right place, white for right symbol elsewhere, each code symbol counted once", () => {
  assert.deepEqual(C.feedback([1, 2, 3, 4], [1, 2, 3, 4]), { black: 4, white: 0 });
  assert.deepEqual(C.feedback([1, 2, 3, 4], [4, 3, 2, 1]), { black: 0, white: 4 });
  assert.deepEqual(C.feedback([1, 1, 2, 2], [1, 2, 1, 1]), { black: 1, white: 2 });
  assert.deepEqual(C.feedback([1, 2, 3, 4], [5, 5, 5, 5]), { black: 0, white: 0 });
  assert.deepEqual(C.feedback([1, 1, 1, 2], [1, 1, 2, 2]), { black: 3, white: 0 });
  assert.deepEqual(C.feedback([6, 2, 2, 5], [2, 6, 6, 6]), { black: 0, white: 2 });
});

test("entering a row: symbols fill the next empty place, a tap empties one, a full row is checked", () => {
  const s = C.create({ mode: "classic", seed: 3 });
  C.place(s, 1); C.place(s, 2);
  assert.deepEqual(s.cur, [1, 2, 0, 0]);
  assert.deepEqual(types(C.check(s)), ["refuse"], "not full yet");
  C.clear(s, 0);
  assert.deepEqual(s.cur, [0, 2, 0, 0]);
  assert.equal(s.slot, 0);
  C.place(s, 5);
  assert.deepEqual(s.cur, [5, 2, 0, 0]);
  C.clear(s);
  assert.deepEqual(s.cur, [5, 0, 0, 0], "Backspace takes the last one out");
  C.place(s, 9);
  assert.deepEqual(s.cur, [5, 0, 0, 0], "no symbol 9 in Classic");
  C.place(s, 3); C.place(s, 3); C.place(s, 3);
  const evs = C.check(s);
  assert.ok(types(evs).includes("guess"));
  assert.equal(s.rows.length, 1);
  assert.deepEqual(s.cur, [0, 0, 0, 0]);
  assert.deepEqual(C.feedback(s.code, [5, 3, 3, 3]), { black: s.rows[0].black, white: s.rows[0].white });
});

test("no-repeats modes refuse a row with a symbol twice", () => {
  const s = C.create({ mode: "norepeat", seed: 4 });
  assert.deepEqual(types(enter(s, [1, 1, 2, 3])), ["refuse"]);
  assert.match(s.message, /No repeats/);
  assert.equal(s.rows.length, 0);
});

test("cracking the code wins: 1,000 × (rows left + 1) + up to 999 for speed; running out of rows loses (0)", () => {
  const s = C.create({ mode: "little", seed: 5 });
  const wrong = s.code.join() === "1,2,3" ? [2, 1, 3] : [1, 2, 3];
  enter(s, wrong);
  for (let i = 0; i < 600; i++) C.step(s);
  const evs = enter(s, s.code);
  assert.ok(types(evs).includes("win"));
  assert.equal(s.rows.length, 2);
  assert.equal(C.result(s).score, 1000 * (8 - 2 + 1) + 999 - 10);
  assert.match(C.result(s).stats.summary[0], /^Cracked in \d rows? of 8$/);
  const t = C.create({ mode: "little", seed: 5 });
  const bad = t.code.slice().reverse();
  const guess = bad.join() === t.code.join() ? [t.code[1], t.code[2], t.code[0]] : bad;
  for (let r = 0; r < 8; r++) enter(t, guess);
  assert.ok(t.over && !t.won);
  assert.equal(C.result(t).score, 0);
  assert.equal(C.result(t).stats.cause, "rows");
  assert.deepEqual(C.result(t).stats.code, t.code, "the code is shown at the end");
});

test("keys and a controller: 1–8, Enter, Backspace; ← → pick, fire places, ↑ checks, ↓ deletes", () => {
  const s = C.create({ mode: "master", seed: 6 });
  C.press(s, "key:8", true);
  assert.equal(s.cur[0], 8);
  C.press(s, "key:BACKSPACE", true);
  assert.equal(s.cur[0], 0);
  s.pal = 0;
  C.press(s, "left", true); assert.equal(s.pal, 7);
  C.press(s, "fire", true); assert.equal(s.cur[0], 8);
  C.press(s, "down", true); assert.equal(s.cur[0], 0);
  for (let i = 0; i < 5; i++) C.press(s, "key:" + (i + 1), true);
  C.press(s, "up", true);
  assert.equal(s.rows.length, 1);
  for (let i = 0; i < 5; i++) C.press(s, "key:2", true);
  C.press(s, "key:ENTER", true);
  assert.equal(s.rows.length, 2);
});

test("deterministic for a seed; save and restore plays on the same; bad saves are refused", () => {
  assert.equal(JSON.stringify(C.create({ mode: "master", seed: 9 })), JSON.stringify(C.create({ mode: "master", seed: 9 })));
  const s = C.create({ mode: "classic", seed: 12 });
  enter(s, [1, 2, 3, 4]); C.place(s, 6);
  const copy = C.restore(JSON.parse(JSON.stringify(C.save(s))));
  for (const x of [s, copy]) { enter(x, [6, 5, 4]); enter(x, x.code); }
  assert.equal(JSON.stringify(C.save(copy)), JSON.stringify(C.save(s)));
  const bad = C.save(C.create({ mode: "classic", seed: 1 }));
  bad.rows = [{ guess: [1, 1, 1, 1], black: 4, white: 0 }];
  bad.code = [2, 2, 2, 2];
  assert.throws(() => C.restore(bad), /can't be continued/, "feedback that doesn't fit the code");
  assert.throws(() => C.restore({ mode: "classic" }), /can't be continued/);
  assert.throws(() => C.restore(null));
});

test("honest score: at most 12,999 (Master cracked in the first row at once)", () => {
  for (const mode of Object.keys(C.MODES)) {
    const s = C.create({ mode, seed: 2 });
    enter(s, s.code);
    assert.ok(s.won);
    assert.ok(C.result(s).score <= 12999);
    assert.equal(C.result(s).score, 1000 * C.MODES[mode].rows + 999);
  }
});

test("fuzz: random keys and taps never throw and keep the rows true to the code", () => {
  for (const mode of Object.keys(C.MODES)) {
    for (let seed = 1; seed <= 10; seed++) {
      const s = C.create({ mode, seed });
      let x = seed * 7919;
      for (let i = 0; i < 3000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 3 === 0) C.press(s, ["up", "down", "left", "right", "fire", "alt", "key:ENTER", "key:BACKSPACE", "key:" + (1 + x % 9)][x % 9], true);
        if (x % 7 === 0) C.clear(s, x % 7);
        C.step(s);
        assert.ok(s.cur.every((c) => c >= 0 && c <= s.colours));
        for (const r of s.rows) assert.deepEqual(C.feedback(s.code, r.guess), { black: r.black, white: r.white });
        assert.ok(s.rows.length <= s.maxRows);
      }
    }
  }
});

test("codebreak renders and plays in every look, with taps on the keys and the rows", () => {
  for (const look of LOOKS) {
    for (const mode of ["little", "master"]) {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("codebreak");
      assert.equal(def.name, "Code Breaker");
      assert.equal(def.typed, true);
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      const k = Math.min(390 / 240, 487 / 300);
      for (let i = 0; i < 400; i++) {
        if (i % 6 === 0) inst.pointer("down", (20 + (i * 11) % 200) * k, 240 * k);      // the symbol keys
        if (i % 30 === 0) inst.pointer("down", 170 * k, 276 * k);                        // Check
        if (i % 45 === 0) inst.pointer("down", 50 * k, 276 * k);                         // Delete
        if (i % 50 === 0) inst.input("key:" + (1 + (i % 4)), true);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      if (inst.state !== "over") {
        assert.ok(inst.save().state);
        // type the code with the keyboard: it is cracked
        for (let q = 0; q < 6; q++) inst.input("key:BACKSPACE", true);
        for (const c of inst.logic.code) inst.input("key:" + c, true);
        inst.input("key:ENTER", true);
        sb.frames(2);
      }
      assert.equal(inst.state, "over");
      assert.equal(ends.length, 1);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
