"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const C = loadLogic("colours-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...C.step(s)); } return all; }
function toInput(s) { let u = 0; while (s.phase !== "input" && !s.over && u++ < 100000) C.step(s); }
// The fastest possible player: on its turn it presses the whole sequence at once.
function bot(s) {
  const evs = [];
  if (s.phase === "input") while (s.phase === "input" && !s.over) evs.push(...C.tap(s, C.expectedPad(s)));
  return evs;
}

test("a round plays the sequence (lit for a while, then a gap), then it's your turn", () => {
  const s = C.create({ seed: 1 });
  assert.equal(s.seq.length, 1); assert.equal(s.level, 1);
  assert.deepEqual(C.press(s, "up", true), [], "input is ignored while it plays");
  const evs = run(s, C.LEAD);
  assert.equal(s.phase, "show");
  assert.equal(s.lit, s.seq[0]);
  assert.ok(types(evs)[0].startsWith("tone"));
  run(s, C.lightTime("classic", 1));
  assert.equal(s.lit, -1, "a gap after each light");
  const rest = run(s, C.gapTime("classic", 1));
  assert.ok(types(rest).includes("yourTurn"));
  assert.equal(s.phase, "input");
});

test("repeat it right: the round scores 10 × its length and the next round adds one pad", () => {
  const s = C.create({ seed: 2 });
  toInput(s);
  const evs = C.press(s, C.PADS[s.seq[0]], true);
  assert.deepEqual(types(evs), ["tone" + C.PADS[s.seq[0]][0].toUpperCase() + C.PADS[s.seq[0]].slice(1), "round"]);
  assert.equal(s.score, 10);
  const first = s.seq[0];
  const more = run(s, C.DONE);
  assert.ok(types(more).includes("level"));
  assert.equal(s.seq.length, 2); assert.equal(s.seq[0], first); assert.equal(s.level, 2);
  toInput(s);
  C.tap(s, s.seq[0]); assert.equal(s.score, 10);
  C.tap(s, s.seq[1]); assert.equal(s.score, 30);
});

test("a wrong pad ends the game and shows the right one", () => {
  const s = C.create({ seed: 3 });
  toInput(s);
  const right = s.seq[0], wrong = (right + 1) % 4;
  const evs = C.tap(s, wrong);
  assert.ok(s.over);
  const end = evs.find((e) => e.type === "gameover");
  assert.equal(end.cause, "wrong"); assert.equal(end.expected, right);
  assert.equal(s.expected, right); assert.equal(s.wrongPad, wrong);
  assert.equal(C.result(s).stats.cause, "wrong");
});

test("5 seconds without a pad on your turn ends the game", () => {
  const s = C.create({ seed: 4 });
  toInput(s);
  run(s, C.IDLE_LIMIT - 1);
  assert.ok(!s.over);
  C.tap(s, s.seq[0]);
  run(s, C.DONE); toInput(s);
  C.tap(s, s.seq[0]);
  run(s, C.IDLE_LIMIT - 1); assert.ok(!s.over, "each press restarts the 5 seconds");
  const evs = run(s, 1);
  assert.ok(s.over && types(evs).includes("gameover"));
  assert.equal(C.result(s).stats.cause, "time");
  assert.equal(s.expected, s.seq[1]);
});

test("Backwards mode wants the sequence from last to first", () => {
  const s = C.create({ seed: 5, mode: "reverse" });
  for (let r = 0; r < 3; r++) { toInput(s); bot(s); run(s, C.DONE); }
  toInput(s);
  assert.equal(s.seq.length, 4);
  const seq = s.seq.slice();
  if (seq[3] !== seq[0]) {
    const t = C.create({ seed: 5, mode: "reverse" }); Object.assign(t, JSON.parse(JSON.stringify(s)));
    C.tap(t, seq[0]); assert.ok(t.over, "the first pad isn't wanted first");
  }
  for (let i = 3; i >= 0; i--) C.tap(s, seq[i]);
  assert.ok(!s.over); assert.equal(s.phase, "done");
});

test("playback speeds up but never lights for less than 10 updates or gaps for less than 10; Fast starts quicker", () => {
  for (const mode of Object.keys(C.MODES)) {
    for (let n = 1; n <= 100; n++) {
      assert.ok(C.lightTime(mode, n) >= 10 && C.gapTime(mode, n) >= 10);
      if (n > 1) assert.ok(C.lightTime(mode, n) <= C.lightTime(mode, n - 1));
    }
  }
  assert.ok(C.lightTime("fast", 1) < C.lightTime("classic", 1));
  assert.ok(C.lightTime("classic", 30) < C.lightTime("classic", 1));
  // measured: from one light coming on to the next, at least 20 updates (3 a second)
  const s = C.create({ seed: 6, mode: "fast" });
  let last = -1, minGap = 1e9;
  while (!s.over && s.level < 40) {
    const evs = C.step(s);
    if (evs.some((e) => e.type.startsWith("tone"))) { if (last >= 0) minGap = Math.min(minGap, s.updates - last); last = s.updates; }
    if (s.phase === "input") { bot(s); last = -1; }
  }
  assert.ok(minGap >= 20, `min ${minGap}`);
});

test("deterministic for a seed; a perfect player wins at round 100; scores stay inside the honest-score limits", () => {
  const PER_SECOND = 40, BASE = 50, MAX = 50500;
  const play = (seed, mode) => {
    const s = C.create({ seed, mode });
    let best = 0;
    while (!s.over) {
      bot(s);
      C.step(s);
      assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates / 60}s`);
      assert.ok(s.score <= MAX && s.level <= 100);
      if (s.updates > 600) best = Math.max(best, s.score / (s.updates / 60));
    }
    return { key: JSON.stringify(C.result(s)) + s.updates + s.seq.join(""), s, best };
  };
  const a = play(61, "fast");
  assert.equal(a.key, play(61, "fast").key);
  assert.ok(a.s.won && a.s.level === 100 && a.s.score === MAX, `score ${a.s.score}`);
  for (const m of ["classic", "reverse"]) assert.ok(play(62, m).s.won);
  assert.notEqual(play(63, "classic").s.seq.join(""), play(64, "classic").s.seq.join(""));
  console.log(`# colours: fastest rate ${a.best.toFixed(1)} points/s`);
});

test("save and restore: carries on the same game; on your turn the sequence plays again first", () => {
  const s = C.create({ seed: 7 });
  for (let r = 0; r < 4; r++) { toInput(s); bot(s); }
  run(s, 5);
  assert.equal(s.phase, "done");
  const copy = C.restore(JSON.parse(JSON.stringify(C.save(s))));
  for (let i = 0; i < 3000 && !s.over; i++) { bot(s); bot(copy); C.step(s); C.step(copy); }
  assert.equal(copy.score, s.score);
  assert.deepEqual(copy.seq, s.seq);
  assert.equal(copy.updates, s.updates);
  toInput(s);
  C.tap(s, C.expectedPad(s));
  const again = C.restore(JSON.parse(JSON.stringify(C.save(s))));
  assert.equal(again.phase, "lead"); assert.equal(again.input, 0);
  toInput(again); bot(again); assert.ok(!again.over);
  assert.throws(() => C.restore({ seq: 3 }), /can't be continued/);
  assert.throws(() => C.restore(null));
});

for (const look of LOOKS) {
  test(`colours renders and plays in the ${look} look`, () => {
    const sb = makeSandbox({ extra: ["colours-logic.js", "colours.js"] });
    const def = sb.win.ArcadeGames.get("colours");
    assert.equal(def.name, "Colour Memory"); assert.equal(def.controls, "touch"); assert.equal(def.stateVersion, 1);
    const canvas = sb.canvas(390, 487);
    const ends = [];
    const inst = def.create(canvas, { mode: "fast", look, seed: 5, onEnd: (r) => ends.push(r) });
    inst.start();
    const L = 390 / 240;   // CSS px per logical px
    const padXY = [[120, 96], [48, 168], [192, 168], [120, 240]];
    for (let i = 0; i < 400; i++) {
      const s = inst.logic;
      if (s.phase === "input" && i % 7 === 0) {
        const p = C.expectedPad(s);
        if (i % 14) inst.input(C.PADS[p], true);
        else { inst.pointer("down", padXY[p][0] * L, padXY[p][1] * L); inst.pointer("up", padXY[p][0] * L, padXY[p][1] * L); }
      }
      sb.frames(1);
    }
    assert.ok(inst.logic.level >= 3, `level ${inst.logic.level}`);
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    // a wrong pad ends it and draws the game-over picture
    let u = 0; while (inst.logic.phase !== "input" && u++ < 2000) sb.frames(1);
    inst.input(C.PADS[(C.expectedPad(inst.logic) + 1) % 4], true); sb.frames(2);
    assert.equal(ends.length, 1);
    inst.setLook(look);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
    assert.equal(sb.pending(), 0);
  });
}

// ---------------------------------------------------------------------------
// Challenges: the level list (SPEC §11.9)
// ---------------------------------------------------------------------------
const ch = (o) => ({ name: "T", length: 4, light: 30, gap: 20, reverse: false, start: 1, ...o });
function clearRound(s) { toInput(s); return bot(s); }

test("Challenges: built-in or the session's list; bad levels are skipped, none usable falls back", () => {
  assert.equal(C.usableLevels(undefined), C.LEVELS);
  assert.equal(C.usableLevels("x"), C.LEVELS);
  assert.ok(C.LEVELS.length >= 5 && C.LEVELS.length <= 10);
  for (const bad of [ch({ length: 3 }), ch({ length: 31 }), ch({ light: 9 }), ch({ gap: 31 }), ch({ start: 0 }),
    ch({ start: 5, length: 4 }), ch({ reverse: "no" }), ch({ light: 12.5 }), ch({ name: 3 }), null]) {
    assert.equal(C.usableLevels([bad]), C.LEVELS, JSON.stringify(bad));
  }
  const good = [ch({ name: "A" }), ch({ length: 31 }), ch({ name: "B", start: 4, length: 4 })];
  assert.deepEqual(C.usableLevels(good).map((c) => c.name), ["A", "B"]);
  const s = C.create({ mode: "challenge", seed: 1 });
  assert.equal(s.challenges, C.LEVELS);
  assert.equal(s.seq.length, C.LEVELS[0].start);
  assert.equal(C.create({ mode: "challenge", seed: 1, levels: [ch({ length: 2 })] }).challenges, C.LEVELS);
  assert.equal(C.create({ seed: 1, levels: good }).challenges, null, "the classic modes don't use the list");
});

test("Challenges: the level's speed, start and direction; reaching its length clears it, a fresh sequence starts the next; the last wins", () => {
  const list = [ch({ name: "A", length: 4, light: 22, gap: 13, start: 2 }), ch({ name: "B", length: 5, light: 11, gap: 10, start: 3, reverse: true })];
  const s = C.create({ mode: "challenge", seed: 4, levels: list });
  assert.equal(s.seq.length, 2); assert.equal(s.level, 1);
  run(s, C.LEAD);
  assert.equal(s.phase, "show"); assert.equal(s.t, 22);
  run(s, 22); assert.equal(s.t, 13, "the gap");
  assert.ok(!C.isReverse(s));
  clearRound(s);
  assert.equal(s.score, 20); assert.ok(!s.cleared);
  run(s, C.DONE);
  assert.equal(s.seq.length, 3); assert.equal(s.level, 1);
  clearRound(s); run(s, C.DONE);
  assert.equal(s.seq.length, 4);
  toInput(s);
  const evs = bot(s);
  const round = evs.find((e) => e.type === "round");
  assert.ok(round.cleared); assert.equal(s.score, 20 + 30 + 40);
  assert.equal(s.phase, "done"); assert.equal(C.result(s).stats.challenges, 1);
  const old = s.seq.slice();
  const next = run(s, C.DONE);
  const lv = next.find((e) => e.type === "level");
  assert.equal(lv.level, 2); assert.equal(lv.name, "B");
  assert.equal(s.level, 2); assert.equal(s.seq.length, 3, "a fresh sequence, start pads long");
  assert.ok(C.isReverse(s));
  run(s, C.LEAD); assert.equal(s.t, 11);
  // backwards: the first pad wanted is the last shown
  toInput(s);
  assert.equal(C.expectedPad(s), s.seq[s.seq.length - 1]);
  bot(s); run(s, C.DONE); clearRound(s); run(s, C.DONE); toInput(s);
  assert.equal(s.seq.length, 5);
  const end = bot(s);
  assert.ok(types(end).includes("win"));
  assert.ok(s.over && s.won);
  const r = C.result(s);
  assert.equal(r.stats.won, true); assert.equal(r.stats.cause, "won"); assert.equal(r.stats.challenges, 2);
  assert.equal(r.level, 2); assert.equal(r.stats.mode, "challenge");
  assert.equal(s.score, 90 + 30 + 40 + 50);
  assert.ok(old.length === 4);
});

test("Challenges: a wrong pad still ends the game; save/restore keeps going with the list (kept out of the save)", () => {
  const s = C.create({ mode: "challenge", seed: 9 });
  for (let i = 0; i < 6; i++) { clearRound(s); run(s, C.DONE); }
  assert.ok(s.level >= 2);
  const data = JSON.parse(JSON.stringify(C.save(s)));
  assert.ok(!("challenges" in data));
  const copy = C.restore(data);
  assert.equal(copy.challenges, C.LEVELS);
  for (let i = 0; i < 20000 && !s.over; i++) { bot(s); bot(copy); C.step(s); C.step(copy); }
  assert.ok(s.won);
  assert.equal(copy.score, s.score); assert.equal(copy.level, s.level); assert.ok(copy.won);
  // with the session's list
  const list = [ch({ name: "A" }), ch({ name: "B", start: 2 })];
  const t = C.create({ mode: "challenge", seed: 2, levels: list });
  for (let i = 0; i < 4; i++) { clearRound(t); run(t, C.DONE); }
  assert.equal(t.level, 2);
  const back = C.restore(JSON.parse(JSON.stringify(C.save(t))), list);
  assert.equal(back.challenges.length, 2); assert.equal(back.level, 2);
  // an older save (no challenge fields) still restores
  const o = C.create({ seed: 3 }); clearRound(o);
  const plain = JSON.parse(JSON.stringify(C.save(o)));
  delete plain.cleared; delete plain.challenges; delete plain.stats.challenges;
  const ob = C.restore(plain);
  assert.equal(ob.challenges, null); assert.equal(ob.cleared, false);
  clearRound(ob); assert.ok(!ob.over);
  const w = C.create({ mode: "challenge", seed: 5 }); toInput(w);
  C.tap(w, (C.expectedPad(w) + 1) % 4);
  assert.ok(w.over && !w.won); assert.equal(C.result(w).stats.cause, "wrong");
});

test("Challenges: honest score with the hardest levels the ranges allow (bot as fast as the rules let it)", () => {
  const PER_SECOND = 40, BASE = 50, PER_CHALLENGE = 10 * (30 * 31) / 2;
  const hard = [];
  for (let i = 0; i < 24; i++) hard.push(ch({ name: "H", length: i % 3 ? 30 : 4, light: 10, gap: 10, start: i % 2 ? (i % 3 ? 5 : 4) : 1, reverse: i % 4 === 0 }));
  let best = 0;
  const s = C.create({ mode: "challenge", seed: 77, levels: hard });
  while (!s.over) {
    bot(s);
    C.step(s);
    assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates / 60}s`);
    assert.ok(s.score <= PER_CHALLENGE * s.level && s.level <= hard.length);
    if (s.updates > 600) best = Math.max(best, s.score / (s.updates / 60));
  }
  assert.ok(s.won && s.level === hard.length, `level ${s.level} ${s.stats.cause}`);
  console.log(`# colours challenge: fastest rate ${best.toFixed(1)} points/s, final ${s.score} for ${hard.length} challenges`);
});

test("Challenges render in every look (name, progress, backwards), with no NaN", () => {
  for (const look of LOOKS) {
    const sb = makeSandbox({ extra: ["colours-logic.js", "colours.js"] });
    const def = sb.win.ArcadeGames.get("colours");
    const canvas = sb.canvas(390, 487);
    const ends = [];
    const levels = [ch({ name: "A very long challenge name for a small screen", length: 4, start: 3, light: 10, gap: 10 }), ch({ name: "Back", reverse: true, start: 2 })];
    const inst = def.create(canvas, { mode: "challenge", look, seed: 5, levels, onEnd: (r) => ends.push(r) });
    inst.start();
    for (let i = 0; i < 900 && !inst.logic.over; i++) {
      const s = inst.logic;
      if (s.phase === "input" && i % 5 === 0) inst.input(C.PADS[C.expectedPad(s)], true);
      if (i === 300) { inst.setLook(LOOKS[(LOOKS.indexOf(look) + 1) % 6]); inst.setReduceMotion(true); }
      if (i === 400) { canvas.clientWidth = 700; canvas.clientHeight = 500; inst.resize(); inst.setLook(look); }
      sb.frames(1);
    }
    assert.ok(inst.logic.level === 2, `${look}: level ${inst.logic.level}`);
    let u = 0; while (inst.logic.phase !== "input" && u++ < 2000) sb.frames(1);
    inst.input(C.PADS[(C.expectedPad(inst.logic) + 1) % 4], true); sb.frames(2);
    assert.equal(ends.length, 1);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
  }
});
