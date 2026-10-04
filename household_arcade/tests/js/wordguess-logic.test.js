"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const W = loadLogic("wordguess-words.js");
const G = loadLogic("wordguess-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const word = (s) => (w) => { for (const ch of w) G.type(s, ch); return G.enter(s); };
const other = (s) => W.ANSWERS.find((w) => w !== s.answer);

test("the word lists: five letters, lower case, no repeats, answers are all valid guesses, nothing blocked", () => {
  assert.ok(W.ANSWERS.length >= 700, "enough answers: " + W.ANSWERS.length);
  assert.ok(W.EXTRA.length >= 500);
  const seen = new Set();
  for (const w of W.ANSWERS.concat(W.EXTRA)) {
    assert.match(w, /^[a-z]{5}$/);
    assert.ok(!seen.has(w), "no repeats: " + w); seen.add(w);
    assert.ok(W.isWord(w));
  }
  for (const w of W.ANSWERS) assert.ok(!W.EXTRA.includes(w));
  for (const b of W.BLOCKED) { assert.ok(!W.isWord(b), b); assert.ok(!seen.has(b)); }
  assert.ok(W.isWord("APPLE") && !W.isWord("zzzzz") && !W.isWord("app"));
  assert.ok(W.BLOCKED.length >= 10);
});

test("marks: right place, elsewhere, and repeated letters only as often as the word has them", () => {
  const m = (g, a) => G.mark(g, a).join("");
  assert.equal(m("crane", "crane"), "22222");
  assert.equal(m("crane", "tulip"), "00000");
  assert.equal(m("arena", "crane"), "12120");
  // two Ls in the guess, one in the answer: the one in the right place wins the mark
  assert.deepEqual(G.mark("hello", "world"), [0, 0, 0, 2, 1]);
  assert.deepEqual(G.mark("lolly", "world"), [0, 2, 0, 2, 0]);
  assert.deepEqual(G.mark("speed", "abide"), [0, 0, 1, 0, 1]);
  assert.deepEqual(G.mark("erase", "speed"), [1, 0, 0, 1, 1]);
});

test("each seed makes the same answer; every mode picks answers from the answer list", () => {
  for (const mode of G.MODE_IDS) {
    for (let seed = 1; seed < 30; seed++) {
      const a = G.create({ mode, seed }), b = G.create({ mode, seed });
      assert.equal(a.answer, b.answer);
      assert.ok(W.ANSWERS.includes(a.answer));
    }
  }
  const seen = new Set(); for (let seed = 1; seed < 60; seed++) seen.add(G.create({ seed }).answer);
  assert.ok(seen.size > 30, "the answers vary");
  assert.equal(G.create({ mode: "classic" }).tries, 6);
  assert.equal(G.create({ mode: "easy" }).tries, 8);
  assert.equal(G.create({ mode: "strict" }).tries, 6);
  assert.equal(G.create({ mode: "nonsense" }).mode, "classic");
});

test("typing: letters only, five at most, Backspace removes; short or unknown words are refused", () => {
  const s = G.create({ seed: 4 });
  G.type(s, "1"); G.type(s, " "); G.type(s, "ab");
  assert.equal(s.cur, "");
  for (const ch of "abcdefg") G.type(s, ch);
  assert.equal(s.cur, "abcde");
  G.back(s); assert.equal(s.cur, "abcd");
  let evs = G.enter(s);
  assert.equal(evs[0].type, "reject"); assert.match(s.message, /Not enough/); assert.equal(s.guesses.length, 0);
  G.type(s, "x"); evs = G.enter(s);
  assert.equal(evs[0].type, "reject"); assert.match(s.message, /list/); assert.equal(s.cur, "abcdx", "the guess stays so it can be fixed");
  assert.ok(s.shake > 0);
  for (let i = 0; i < 200; i++) G.step(s);
  assert.equal(s.message, ""); assert.equal(s.shake, 0);
  assert.deepEqual(G.press(s, "key:Q", false), []);
  assert.deepEqual(G.press(s, "left", true), []);
  G.press(s, "key:BACKSPACE", true); G.press(s, "key:DELETE", true); assert.equal(s.cur, "abc");
  G.press(s, "key:Z", true); assert.equal(s.cur, "abcz");
});

test("a win scores by tries left, then the time, and ends the game", () => {
  const s = G.create({ mode: "classic", seed: 9 });
  for (let i = 0; i < 60 * 10; i++) G.step(s);
  const evs = word(s)(s.answer);
  assert.deepEqual(evs.map((e) => e.type), ["guess", "win"]);
  assert.ok(s.won && s.over);
  assert.equal(s.score, 6000 + 989);
  assert.equal(G.status(s).score, s.score); assert.equal(G.status(s).done, true);
  const r = G.result(s);
  assert.equal(r.score, s.score); assert.equal(r.stats.won, true); assert.equal(r.stats.answer, s.answer);
  assert.match(r.stats.summary[0], /in 1 try of 6/);
  assert.deepEqual(G.press(s, "key:A", true), [], "nothing after the end");
  assert.equal(G.step(s).length, 0);
  // the third try, slowly, in the eight-try mode
  const t = G.create({ mode: "easy", seed: 9 });
  word(t)(other(t)); word(t)(other(t));
  t.updates = 60 * 5000;
  word(t)(t.answer);
  assert.equal(t.score, 1000 * (8 - 3 + 1));
  assert.ok(t.score <= 9999);
  const best = G.create({ mode: "easy", seed: 9 }); word(best)(best.answer);
  assert.ok(best.score <= 9999 && best.score === 8999);
});

test("running out of tries scores 0 and shows the answer", () => {
  const s = G.create({ mode: "classic", seed: 11 });
  let last;
  for (let i = 0; i < 6; i++) last = word(s)(other(s));
  assert.ok(s.over && !s.won && s.score === 0);
  assert.equal(last[last.length - 1].type, "lose");
  assert.match(s.message, new RegExp(s.answer.toUpperCase()));
  const r = G.result(s);
  assert.equal(r.score, 0); assert.equal(r.stats.cause, "lost"); assert.match(r.stats.summary[0], new RegExp(s.answer.toUpperCase()));
  assert.equal(G.status(s).done, false);
});

test("keyboard colours keep the best mark a letter has had", () => {
  const s = G.create({ seed: 3 });
  s.answer = "crane";
  word(s)("arena");           // r and n are in place; a and e are elsewhere
  assert.equal(s.keys.A, G.ELSEWHERE); assert.equal(s.keys.R, G.CORRECT); assert.equal(s.keys.E, G.ELSEWHERE);
  assert.equal(s.keys.N, G.CORRECT);
  word(s)("crane");
  assert.equal(s.keys.A, G.CORRECT); assert.equal(s.keys.C, G.CORRECT);
  const t = G.create({ seed: 3 }); t.answer = "crane"; word(t)("pious");
  assert.equal(t.keys.P, G.ABSENT);
  word(t)("drain"); assert.equal(t.keys.D, G.ABSENT); assert.equal(t.keys.R, G.CORRECT);
});

test("strict mode: correct letters stay put and found letters must be used", () => {
  const s = G.create({ mode: "strict", seed: 5 });
  s.answer = "crane";
  word(s)("arena");           // r and n are in place, a and e are elsewhere
  assert.deepEqual(s.marks[0], [1, 2, 1, 2, 0]);
  let evs = word(s)("pious");
  assert.equal(evs[0].type, "reject"); assert.match(s.message, /must stay in the 2nd place/);
  assert.equal(s.guesses.length, 1); s.cur = "";
  evs = word(s)("brine");     // keeps r (2nd) and n (4th), has e, but no a
  assert.equal(evs[0].type, "reject"); assert.match(s.message, /Use every clue: the word has a A/);
  s.cur = "";
  evs = word(s)("crane");
  assert.equal(evs[evs.length - 1].type, "win");
  // the classic mode takes any word
  const c = G.create({ mode: "classic", seed: 5 }); c.answer = "crane"; word(c)("arena");
  assert.equal(word(c)("pious")[0].type, "guess");
});

test("strict mode asks for a repeated letter when a guess found two", () => {
  const s = G.create({ mode: "strict", seed: 6 });
  s.answer = "geese";
  word(s)("elect");
  assert.deepEqual(s.marks[0], [1, 0, 2, 0, 0]);
  const evs = word(s)("grown");
  assert.equal(evs[0].type, "reject");
});

test("save and restore carry on the same game; bad data is refused", () => {
  const s = G.create({ mode: "strict", seed: 21 });
  s.answer = "crane"; word(s)("arena");
  for (let i = 0; i < 300; i++) G.step(s);
  G.type(s, "c"); G.type(s, "r");
  const data = JSON.parse(JSON.stringify(G.save(s)));
  const c = G.restore(data);
  assert.deepEqual(c.guesses, s.guesses); assert.deepEqual(c.marks, s.marks); assert.deepEqual(c.keys, s.keys);
  assert.equal(c.cur, "cr"); assert.equal(c.updates, s.updates); assert.equal(c.answer, "crane");
  assert.equal(c.strict, true);
  assert.equal(word(c)("ane")[0].type, "guess");
  assert.throws(() => G.restore(null), /can't be continued/);
  assert.throws(() => G.restore({ ...data, answer: "zzzzz" }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, mode: "nope" }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, guesses: ["qqqqq"] }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, guesses: Array(7).fill("arena") }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, guesses: ["crane", "arena"] }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, cur: "toolongword" }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, updates: "x" }), /can't be continued/);
  assert.throws(() => G.restore({ ...data, mode: "strict", guesses: ["arena", "pious"] }), /can't be continued/);
});

test("the seconds count only the updates the game ran", () => {
  const s = G.create({ seed: 2 });
  assert.equal(G.seconds(s), 0);
  for (let i = 0; i < 125; i++) G.step(s);
  assert.equal(G.seconds(s), 2); assert.equal(G.clock(75), "1:15");
});

test("registry contract", () => {
  const sb = makeSandbox({ extra: ["wordguess-words.js", "wordguess-logic.js", "wordguess.js"] });
  const def = sb.win.ArcadeGames.get("wordguess");
  assert.equal(def.controls, "touch"); assert.equal(def.typed, true);
  assert.deepEqual(Array.from(def.modes, (m) => m.id), ["classic", "easy", "strict"]);
  assert.equal(def.stateVersion, 1);
});

for (const look of LOOKS) {
  test(`word guess renders and plays in the ${look} look, with the on-screen keyboard`, () => {
    const sb = makeSandbox({ extra: ["wordguess-words.js", "wordguess-logic.js", "wordguess.js"] });
    const def = sb.win.ArcadeGames.get("wordguess");
    const canvas = sb.canvas(390, 487);
    const ends = [], events = [];
    const inst = def.create(canvas, { mode: "easy", look, seed: 5, onEnd: (r) => ends.push(r), onEvent: (t) => events.push(t) });
    inst.start();
    const L = inst.logic, k = 390 / 240;
    const pt = (x, y) => [x * k, y * k];
    // press the on-screen keys by their positions: find them by drawing order is not possible, so type then tap
    const tapKey = (label) => {
      // keys sit on three rows; row y centres 233, 258, 283
      const rows = { 0: "QWERTYUIOP", 1: "ASDFGHJKL", 2: "ZXCVBNM" };
      for (const r of [0, 1, 2]) {
        const i = rows[r].indexOf(label);
        if (i >= 0) {
          const letters = rows[r], units = r === 2 ? 10.2 : 10, total = r === 2 ? 10.2 : letters.length;
          const x0 = 6 + (10.2 - total) * 22.8 / 2 + (r === 2 ? 1.6 * 22.8 : 0);
          return [x0 + (i + 0.5) * 22.8, 233 + r * 25];
        }
      }
      if (label === "ENTER") return [6 + 0.8 * 22.8, 283];
      if (label === "BACK") return [6 + (1.6 + 7 + 0.8) * 22.8, 283];
    };
    for (const ch of "ZZ") { const [x, y] = tapKey(ch); inst.pointer("down", ...pt(x, y)); inst.pointer("up", ...pt(x, y)); }
    sb.frames(3);
    assert.equal(L.cur, "zz");
    { const [x, y] = tapKey("BACK"); inst.pointer("down", ...pt(x, y)); }
    sb.frames(2);
    assert.equal(L.cur, "z");
    { const [x, y] = tapKey("ENTER"); inst.pointer("down", ...pt(x, y)); }
    sb.frames(60 * 3 > 100 ? 100 : 3);
    assert.ok(events.includes("reject"));
    inst.input("key:BACKSPACE", true);
    inst.pointer("down", 1, 1); inst.pointer("down", 500, 500); inst.pointer("move", 100, 100);
    for (let i = 0; i < 3; i++) { for (const ch of other(L)) inst.input("key:" + ch.toUpperCase(), true); inst.input("key:ENTER", true); sb.frames(50); }
    for (const other2 of LOOKS) { inst.setLook(other2); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    for (const ch of L.answer) inst.input("key:" + ch.toUpperCase(), true);
    inst.input("key:ENTER", true); sb.frames(20);
    assert.equal(ends.length, 1);
    assert.ok(ends[0].stats.won); assert.ok(ends[0].score >= 5000 + 900 && ends[0].score <= 5999, "tries then time: " + ends[0].score);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
    assert.equal(sb.pending(), 0);
  });
}

test("a lost game draws the answer and still ends cleanly", () => {
  const sb = makeSandbox({ extra: ["wordguess-words.js", "wordguess-logic.js", "wordguess.js"] });
  const def = sb.win.ArcadeGames.get("wordguess");
  const ends = [];
  const inst = def.create(sb.canvas(390, 487), { mode: "strict", look: "lcd", seed: 8, onEnd: (r) => ends.push(r) });
  inst.start();
  const L = inst.logic;
  for (let i = 0; i < 6; i++) {
    // a strict game needs each guess to use the clues; the answer itself always does, so fake the guesses directly
    L.cur = other(L); L.strict = false;
    inst.input("key:ENTER", true); sb.frames(2);
  }
  sb.frames(5);
  assert.equal(ends.length, 1); assert.equal(ends[0].score, 0); assert.equal(ends[0].stats.cause, "lost");
  inst.destroy();
});
