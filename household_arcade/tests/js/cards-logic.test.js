"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const K = loadLogic("cards-logic.js");

const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) all.push(...each(s)); all.push(...K.step(s)); } return all; }
function partner(s, i) { return s.cards.findIndex((c, j) => j !== i && !c.done && c.sym === s.cards[i].sym); }
function other(s, i) { return s.cards.findIndex((c, j) => j !== i && !c.done && c.sym !== s.cards[i].sym); }
// A perfect player that knows every card and taps as fast as the rules take taps.
// Wait until the card just turned has finished turning (taps sooner than that wait for it).
function wait(s) { while (s.cool > 0 || s.queued >= 0) K.step(s); }
function bot(s) {
  if (s.phase !== "play" || s.cool > 0 || s.queued >= 0) return [];
  if (s.first < 0) return K.tap(s, s.cards.findIndex((c) => !c.done));
  return K.tap(s, partner(s, s.first));
}

test("modes deal 8, 10 and 15 pairs, two of each symbol, face down; cards fit the playfield", () => {
  for (const [mode, pairs] of [["small", 8], ["medium", 10], ["large", 15]]) {
    const s = K.create({ mode, seed: 1 });
    assert.equal(s.cards.length, pairs * 2);
    const count = {};
    for (const c of s.cards) { count[c.sym] = (count[c.sym] || 0) + 1; assert.ok(!c.up && !c.done); }
    assert.equal(Object.keys(count).length, pairs);
    assert.ok(Object.values(count).every((n) => n === 2));
    const L = K.layout(mode);
    assert.ok(L.ox >= 0 && L.ox + L.cols * L.px <= 240 && L.oy >= 46 && L.oy + L.rows * L.py <= 296, mode);
    assert.ok(L.px - L.gap >= 24 && L.py - L.gap >= 24, "big enough to tap");
    assert.ok(L.px % 6 === 0 && L.py % 6 === 0 && L.ox % 6 === 0 && L.oy % 6 === 0);
    assert.equal(K.cardAt(s, L.ox + 1, L.oy + 1), 0);
    assert.equal(K.cardAt(s, L.ox + L.cols * L.px - 1, L.oy + L.rows * L.py - 1), s.cards.length - 1);
    assert.equal(K.cardAt(s, 1, 1), -1);
  }
  assert.equal(K.timeFor("small", 1), 60 * 60); assert.equal(K.timeFor("medium", 1), 75 * 60); assert.equal(K.timeFor("large", 1), 110 * 60);
  assert.equal(K.timeFor("small", 99), 30 * 60);
});

test("a pair stays face up and scores 20 × the streak; a miss turns both back after 45 updates and resets the streak", () => {
  const s = K.create({ seed: 2 });
  let evs = K.tap(s, 0);
  assert.deepEqual(types(evs), ["turn"]); assert.ok(s.cards[0].up);
  assert.deepEqual(K.tap(s, partner(s, 0)), [], "a tap within the turning time waits");
  evs = run(s, K.TURN);
  assert.ok(types(evs).includes("pair"), "and happens when the first card has turned");
  assert.equal(s.score, 20); assert.equal(s.streak, 1);
  wait(s);
  const a = s.cards.findIndex((c) => !c.done);
  K.tap(s, a); wait(s);
  K.tap(s, partner(s, a)); wait(s);
  assert.equal(s.score, 20 + 40); assert.equal(s.streak, 2);
  const b = s.cards.findIndex((c) => !c.done), wrong = other(s, b);
  K.tap(s, b); wait(s);
  evs = K.tap(s, wrong);
  assert.ok(types(evs).includes("miss"));
  assert.equal(s.streak, 0); assert.equal(s.misses, 1);
  run(s, K.BACK_DELAY - 1);
  assert.ok(s.cards[b].up && s.cards[wrong].up);
  evs = run(s, 1);
  assert.ok(types(evs).includes("back"));
  assert.ok(!s.cards[b].up && !s.cards[wrong].up);
  K.tap(s, b); wait(s); K.tap(s, partner(s, b));
  assert.equal(s.score, 80, "the streak starts again at 1");
});

test("tapping a third card turns the two back at once; face-up cards ignore taps", () => {
  const s = K.create({ seed: 3 });
  const b = 0, wrong = other(s, b);
  K.tap(s, b); run(s, K.TURN); K.tap(s, wrong); run(s, K.TURN);
  const third = s.cards.findIndex((c, j) => j !== b && j !== wrong);
  K.tap(s, third);
  assert.ok(!s.cards[b].up && !s.cards[wrong].up && s.cards[third].up);
  assert.equal(s.first, third);
  run(s, K.TURN);
  assert.deepEqual(K.tap(s, third), []);
});

test("keyboard: arrows move the cursor (wrapping), Space turns the card under it", () => {
  const s = K.create({ seed: 4, mode: "medium" });
  assert.equal(s.cursor, 0);
  K.press(s, "right", true); K.press(s, "down", true);
  assert.equal(s.cursor, 5);
  K.press(s, "left", true); K.press(s, "left", true);
  assert.equal(s.cursor, 4 + 3);
  K.press(s, "up", true); K.press(s, "up", true);
  assert.equal(s.cursor, 4 * 4 + 3);
  const evs = K.press(s, "fire", true);
  assert.deepEqual(types(evs), ["turn"]); assert.ok(s.cards[19].up);
});

test("clearing a board: a bonus for time left and few misses, then the next board with less time", () => {
  const s = K.create({ seed: 5 });
  // one miss first
  const b = 0, wrong = other(s, b);
  K.tap(s, b); run(s, K.TURN); K.tap(s, wrong); run(s, K.BACK_DELAY);
  let evs = [];
  while (s.phase === "play") { evs.push(...bot(s)); evs.push(...K.step(s)); }
  const clear = evs.find((e) => e.type === "clear");
  assert.ok(clear);
  const secs = clear.seconds;
  assert.equal(clear.bonus, 5 * secs + 10 * (8 - 1));
  assert.equal(s.score, 20 * (8 * 9 / 2) + clear.bonus);
  evs = run(s, K.CLEAR_PAUSE);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.level, 2); assert.equal(s.phase, "play");
  assert.ok(s.cards.every((c) => !c.up && !c.done));
  assert.equal(s.timeLimit, (60 - 2) * 60);
  assert.equal(K.result(s).stats.boards, 1);
});

test("the time running out ends the game", () => {
  const s = K.create({ seed: 6, mode: "large" });
  const evs = run(s, 110 * 60);
  assert.ok(s.over && types(evs).includes("gameover"));
  assert.equal(K.result(s).stats.cause, "time");
  assert.equal(s.updates, 110 * 60);
});

test("deterministic for a seed; a perfect player wins after board 100; scores stay inside the honest-score limits", () => {
  const PER_SECOND = 800, BASE = 2000, MAX = 288250;   // MAX: 100 boards × (pairs 2,400 + 150) + 5 × (sum of the large time limits)
  const play = (seed, mode) => {
    const s = K.create({ seed, mode });
    let best = 0;
    while (!s.over) {
      bot(s);
      K.step(s);
      assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates / 60}s`);
      assert.ok(s.score <= MAX && s.level <= 100);
      if (s.updates > 600) best = Math.max(best, s.score / (s.updates / 60));
    }
    return { key: JSON.stringify(K.result(s)) + s.updates, s, best };
  };
  const a = play(71, "large");
  assert.equal(a.key, play(71, "large").key);
  assert.ok(a.s.won && a.s.level === 100, `level ${a.s.level}`);
  for (const m of ["small", "medium"]) {
    const r = play(72, m);
    assert.ok(r.s.won);
    console.log(`# cards ${m}: fastest rate ${r.best.toFixed(0)} points/s, final ${r.s.score}`);
  }
  console.log(`# cards large: fastest rate ${a.best.toFixed(0)} points/s, final ${a.s.score}`);
});

test("save and restore carry on the same game", () => {
  const s = K.create({ seed: 8, mode: "medium" });
  run(s, 200, bot);
  K.tap(s, s.cards.findIndex((c) => !c.done && !c.up));
  const copy = K.restore(JSON.parse(JSON.stringify(K.save(s))));
  for (let i = 0; i < 3000 && !s.over; i++) { bot(s); bot(copy); K.step(s); K.step(copy); }
  assert.equal(copy.score, s.score);
  assert.deepEqual(copy.cards, s.cards);
  assert.equal(copy.updates, s.updates);
  assert.throws(() => K.restore({ cards: 1 }), /can't be continued/);
  assert.throws(() => K.restore({ ...K.save(s), cards: [] }));
});

for (const look of LOOKS) {
  test(`cards renders and plays in the ${look} look`, () => {
    const sb = makeSandbox({ extra: ["cards-logic.js", "cards.js"] });
    const def = sb.win.ArcadeGames.get("cards");
    assert.equal(def.name, "Memory Cards"); assert.equal(def.controls, "touch"); assert.equal(def.stateVersion, 1);
    const canvas = sb.canvas(390, 487);
    const ends = [];
    const inst = def.create(canvas, { mode: "large", look, seed: 5, onEnd: (r) => ends.push(r) });
    inst.start();
    const Lc = 390 / 240, L = K.layout("large");
    for (let i = 0; i < 400; i++) {
      if (i % 9 === 0) {
        const k = i % 27;
        if (k === 0) { const c = (i / 9) % 30; const x = (L.ox + (c % 5) * L.px + 10) * Lc, y = (L.oy + Math.floor(c / 5) * L.py + 10) * Lc; inst.pointer("down", x, y); inst.pointer("up", x, y); }
        else if (k === 9) inst.input(["left", "right", "up", "down"][(i / 9) % 4], true);
        else inst.input("fire", true);
      }
      sb.frames(1);
    }
    assert.ok(inst.logic.stats.turns > 5);
    for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
    inst.setReduceMotion(true); sb.frames(3);
    canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
    // the board-clear picture and the end
    inst.logic.cards.forEach((c) => { c.done = true; c.up = true; });
    inst.logic.phase = "clear"; inst.logic.clearT = 30; inst.logic.lastBonus = 100; sb.frames(5);
    inst.setLook(look); sb.frames(40);
    inst.logic.timeLeft = 2; sb.frames(3);
    assert.equal(ends.length, 1);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
    assert.equal(sb.pending(), 0);
  });
}

// ---------------------------------------------------------------------------
// Challenges: the level list (SPEC §11.9)
// ---------------------------------------------------------------------------
const bd = (o) => ({ name: "T", cols: 4, rows: 4, time: 60, peek: 0, symbols: 8, ...o });
function clearBoard(s) { const evs = []; while (s.phase === "play" && !s.over) { evs.push(...bot(s)); evs.push(...K.step(s)); } return evs; }

test("Challenges: built-in or the session's list; bad boards are skipped, none usable falls back; every allowed board fits", () => {
  assert.equal(K.usableLevels(undefined), K.LEVELS);
  assert.ok(K.LEVELS.length >= 5 && K.LEVELS.length <= 10);
  for (const bad of [bd({ cols: 2 }), bd({ rows: 7 }), bd({ cols: 3, rows: 3 }), bd({ cols: 6, rows: 6 }), bd({ cols: 5, rows: 5 }),
    bd({ time: 19 }), bd({ time: 151 }), bd({ peek: 6 }), bd({ peek: 1.5 }), bd({ symbols: 3 }), bd({ symbols: 9 }),
    bd({ cols: 3, rows: 4, symbols: 7 }), bd({ name: null }), null]) {
    assert.equal(K.usableLevels([bad]), K.LEVELS, JSON.stringify(bad));
  }
  assert.deepEqual(K.usableLevels([bd({ name: "A" }), bd({ cols: 9 })]).map((b) => b.name), ["A"]);
  for (let cols = 3; cols <= 6; cols++) {
    for (let rows = 3; rows <= 6; rows++) {
      if ((cols * rows) % 2 || cols * rows > 30) continue;
      for (const symbols of [4, Math.min(15, cols * rows / 2)]) {
        const s = K.create({ mode: "challenge", seed: cols * 10 + rows, levels: [bd({ cols, rows, symbols })] });
        assert.equal(s.cards.length, cols * rows);
        const count = {};
        for (const c of s.cards) count[c.sym] = (count[c.sym] || 0) + 1;
        assert.equal(Object.keys(count).length, symbols, `${cols}×${rows} uses ${symbols} kinds`);
        assert.ok(Object.values(count).every((n) => n % 2 === 0));
        const L = K.layout(s);
        assert.equal(L.cols, cols); assert.equal(L.rows, rows);
        assert.ok(L.ox >= 0 && L.ox + L.cols * L.px <= 240 && L.oy >= 46 && L.oy + L.rows * L.py <= 296, `${cols}×${rows}`);
        assert.ok(L.px - L.gap >= 24 && L.py - L.gap >= 24, "big enough to tap");
        assert.ok(L.px % 6 === 0 && L.py % 6 === 0 && L.ox % 6 === 0 && L.oy % 6 === 0);
        assert.equal(K.cardAt(s, L.ox + L.cols * L.px - 1, L.oy + L.rows * L.py - 1), s.cards.length - 1);
        // the keyboard cursor wraps on this board's size
        K.press(s, "left", true); assert.equal(s.cursor, cols - 1);
        K.press(s, "up", true); assert.equal(s.cursor, (rows - 1) * cols + cols - 1);
      }
    }
  }
});

test("Challenges: peek shows every face (taps wait, the clock waits); the board's time; clearing → next board; the last wins", () => {
  const list = [bd({ name: "A", cols: 3, rows: 4, time: 30, peek: 2, symbols: 4 }), bd({ name: "B", cols: 6, rows: 5, time: 90, peek: 0, symbols: 15 })];
  const s = K.create({ mode: "challenge", seed: 3, levels: list });
  assert.equal(s.phase, "peek"); assert.equal(s.timeLimit, 30 * 60);
  assert.deepEqual(K.tap(s, 0), [], "no turning while every face shows");
  let evs = run(s, 2 * 60 - 1);
  assert.equal(s.phase, "peek"); assert.equal(s.timeLeft, 30 * 60, "the clock waits");
  evs = run(s, 1);
  assert.ok(types(evs).includes("hide")); assert.equal(s.phase, "play");
  run(s, 10); assert.equal(s.timeLeft, 30 * 60 - 10);
  evs = clearBoard(s);
  const clear = evs.find((e) => e.type === "clear");
  assert.ok(clear); assert.equal(s.phase, "clear");
  evs = run(s, K.CLEAR_PAUSE);
  const lv = evs.find((e) => e.type === "level");
  assert.equal(lv.level, 2); assert.equal(lv.name, "B");
  assert.equal(s.cards.length, 30); assert.equal(s.phase, "play", "no peek"); assert.equal(s.timeLimit, 90 * 60);
  evs = clearBoard(s);
  assert.ok(types(evs).includes("win"));
  assert.ok(s.over && s.won);
  const r = K.result(s);
  assert.equal(r.level, 2); assert.equal(r.stats.won, true); assert.equal(r.stats.cause, "won"); assert.equal(r.stats.boards, 2);
  assert.equal(r.stats.mode, "challenge");
  // the time running out ends it
  const t = K.create({ mode: "challenge", seed: 4, levels: [bd({ time: 20 })] });
  evs = run(t, 20 * 60);
  assert.ok(t.over && !t.won && types(evs).includes("gameover")); assert.equal(K.result(t).stats.cause, "time");
});

test("Challenges: save/restore keeps going with the list (kept out of the save); older saves restore", () => {
  const s = K.create({ mode: "challenge", seed: 8 });
  for (let i = 0; i < 2; i++) { run(s, K.LEVELS[i].peek * 60); clearBoard(s); run(s, K.CLEAR_PAUSE); }
  run(s, 30); bot(s); run(s, 3);
  assert.equal(s.level, 3);
  const data = JSON.parse(JSON.stringify(K.save(s)));
  assert.ok(!("boards" in data));
  const copy = K.restore(data);
  assert.equal(copy.boards, K.LEVELS);
  for (let i = 0; i < 60000 && !s.over; i++) { bot(s); bot(copy); K.step(s); K.step(copy); }
  assert.ok(s.won && s.level === K.LEVELS.length);
  assert.equal(copy.score, s.score); assert.ok(copy.won); assert.equal(copy.updates, s.updates);
  const list = [bd({ name: "A" }), bd({ name: "B", cols: 5, rows: 6, symbols: 15 })];
  const t = K.create({ mode: "challenge", seed: 2, levels: list });
  clearBoard(t); run(t, K.CLEAR_PAUSE);
  const back = K.restore(JSON.parse(JSON.stringify(K.save(t))), list);
  assert.equal(back.boards.length, 2); assert.equal(back.cards.length, 30); assert.equal(back.cols, 5);
  assert.throws(() => K.restore({ ...K.save(t), cols: 6, rows: 6 }), /can't be continued/);
  // a save from before the challenge mode (no cols, rows, peekT)
  const o = K.create({ seed: 1, mode: "medium" });
  const plain = JSON.parse(JSON.stringify(K.save(o)));
  delete plain.cols; delete plain.rows; delete plain.peekT; delete plain.boards;
  const ob = K.restore(plain);
  assert.equal(ob.cols, 4); assert.equal(ob.rows, 5); assert.equal(ob.boards, null);
  clearBoard(ob); assert.equal(ob.phase, "clear");
});

test("Challenges: honest score with the hardest boards the ranges allow (bot as fast as the rules let it)", () => {
  const PER_SECOND = 800, BASE = 2000, PER_BOARD = 20 * (15 * 16) / 2 + 10 * 15 + 5 * 150;
  const hard = [];
  const sizes = [[3, 4], [4, 3], [5, 6], [6, 5], [4, 4], [6, 4]];
  for (let i = 0; i < 30; i++) {
    const [cols, rows] = sizes[i % sizes.length];
    hard.push(bd({ name: "H", cols, rows, time: 150, peek: 0, symbols: i % 2 ? 4 : Math.min(15, cols * rows / 2) }));
  }
  // and a list of only the biggest boards with the most time (the most points a second)
  const big = [];
  for (let i = 0; i < 40; i++) big.push(bd({ name: "B", cols: i % 2 ? 5 : 6, rows: i % 2 ? 6 : 5, time: 150, peek: 0, symbols: i % 3 ? 15 : 4 }));
  for (const list of [hard, big]) {
    const s = K.create({ mode: "challenge", seed: 91, levels: list });
    let best = 0;
    while (!s.over) {
      bot(s);
      K.step(s);
      assert.ok(s.score <= (s.updates / 60) * PER_SECOND + BASE, `score ${s.score} after ${s.updates / 60}s`);
      assert.ok(s.score <= PER_BOARD * s.level && s.level <= list.length);
      if (s.updates > 600) best = Math.max(best, s.score / (s.updates / 60));
    }
    assert.ok(s.won && s.level === list.length);
    console.log(`# cards challenge: fastest rate ${best.toFixed(0)} points/s, final ${s.score} for ${list.length} boards`);
  }
});

test("Challenges render in every look (peek, name, board sizes), with no NaN", () => {
  for (const look of LOOKS) {
    const sb = makeSandbox({ extra: ["cards-logic.js", "cards.js"] });
    const def = sb.win.ArcadeGames.get("cards");
    const canvas = sb.canvas(390, 487);
    const ends = [];
    const levels = [bd({ name: "A very long board name that will not fit", cols: 3, rows: 4, peek: 1, symbols: 4 }), bd({ name: "Wide", cols: 6, rows: 5, symbols: 15 })];
    const inst = def.create(canvas, { mode: "challenge", look, seed: 5, levels, onEnd: (r) => ends.push(r) });
    inst.start();
    const Lc = 390 / 240;
    for (let i = 0; i < 700 && !inst.logic.over; i++) {
      const s = inst.logic;
      if (i % 8 === 0 && s.phase === "play" && s.cool === 0) {
        const want = s.first < 0 ? s.cards.findIndex((c) => !c.done) : partner(s, s.first);
        if (i % 16) { const L = K.layout(s); const x = (L.ox + (want % L.cols) * L.px + 10) * Lc, y = (L.oy + Math.floor(want / L.cols) * L.py + 10) * Lc; inst.pointer("down", x, y); inst.pointer("up", x, y); }
        else { inst.input("right", true); inst.input("fire", true); }
      }
      if (i === 200) { inst.setLook(LOOKS[(LOOKS.indexOf(look) + 2) % 6]); inst.setReduceMotion(true); }
      if (i === 300) { canvas.clientWidth = 700; canvas.clientHeight = 500; inst.resize(); inst.setLook(look); }
      sb.frames(1);
    }
    assert.equal(inst.logic.level, 2, `${look}: board ${inst.logic.level}`);
    inst.logic.timeLeft = 2; sb.frames(3);
    assert.equal(ends.length, 1);
    assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
    inst.destroy();
  }
});
