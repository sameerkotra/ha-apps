"use strict";
// Bubble Pop (wave 5): aiming, bouncing, sticking, popping and dropping, the next bubbles, the ceiling and Endless
// rows, the puzzle list, saving, the honest-score limits, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const B = loadLogic("bubbles-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const EXTRA = ["bubbles-logic.js", "bubbles.js"];
const LIMIT = { perSecond: 1400, base: 2000, max: 25000000 };

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...B.step(s)); } return all; }
function puzzle(extra) { return Object.assign({ name: "Test", rows: ["11112222", "33334444", "11112222"], colours: 4, drop: 8 }, extra || {}); }
function grid(s, rows) {
  for (let r = 0; r < B.MAX_ROWS; r++) for (let c = 0; c < 8; c++) s.grid[r][c] = r < rows.length && rows[r][c] !== "." ? Number(rows[r][c]) : 0;
}
function shoot(s, aim, k) {
  s.aim = aim; if (k) s.cur = k;
  B.press(s, "fire", true);
  const evs = [];
  for (let i = 0; i < 200 && !s.over; i++) { evs.push(...B.step(s)); if (!s.shot && types(evs).includes("shoot")) break; }
  return evs;
}
// A good shooter: tries angles across the range and takes the one whose bubble joins the biggest group.
function plan(s) {
  let best = 0, bv = -1;
  const saved = s.aim;
  for (let a = -75; a <= 75; a += 2.5) {
    s.aim = a;
    const pts = B.guide(s, 600), end = pts[pts.length - 1], at = B.snapCell(s, end[0], end[1]);
    if (!at) continue;
    s.grid[at[0]][at[1]] = s.cur;
    const n = B.flood(s, at[0], at[1], s.cur).length;
    s.grid[at[0]][at[1]] = 0;
    const v = (n >= 3 ? n * 10 : n) - at[0] * 0.1;
    if (v > bv) { bv = v; best = a; }
  }
  s.aim = saved;
  return best;
}
function bot(s) { if (s.phase === "aim" && !s.shot && s.reload === 0) { s.aim = plan(s); B.press(s, "fire", true); } }

test("the start: a board of hanging bubbles, the next two are colours on it", () => {
  const s = B.create({ seed: 1 });
  assert.equal(s.mode, "classic");
  assert.ok(B.count(s) >= 24);
  const have = B.coloursOnBoard(s);
  assert.ok(have.includes(s.cur) && have.includes(s.next));
  assert.equal(s.aim, 0);
  const p = B.create({ seed: 1, mode: "puzzle" });
  assert.equal(B.count(p), 24);
  assert.equal(p.dropEvery, B.LEVELS[0].drop);
});

test("aiming turns and stops at the side; a finger above the launcher aims at it, below it its x sets the angle", () => {
  const s = B.create({ seed: 2 });
  B.press(s, "left", true); run(s, 10); B.press(s, "left", false);
  assert.ok(s.aim < -10);
  B.press(s, "left", true); run(s, 200); B.press(s, "left", false);
  assert.equal(s.aim, -B.MAX_AIM);
  B.aimAt(s, B.LX, 100);
  assert.equal(s.aim, 0);
  B.aimAt(s, B.LX + 100, B.LY - 100);
  assert.equal(s.aim, 45);
  B.aimAt(s, 240, 300);
  assert.equal(s.aim, B.MAX_AIM);
});

test("a shot flies straight, bounces off the walls and sticks to the ceiling or a bubble", () => {
  const s = B.create({ seed: 3 });
  grid(s, ["........", "........", "........"]);
  grid(s, ["1......."]);
  let evs = shoot(s, 0, 2);
  assert.ok(types(evs).includes("stick"));
  assert.equal(B.count(s), 2);
  assert.ok(s.grid[0].some((k, c) => k === 2 && c >= 3 && c <= 4), "straight up to the ceiling");
  evs = shoot(s, 60, 3);
  assert.ok(types(evs).includes("bounce"), "off the right wall");
  assert.equal(B.count(s), 3);
});

test("three of a colour pop (10 each) and whatever hangs from them drops (20 each)", () => {
  const s = B.create({ seed: 4 });
  // a pair of 1s at the top with a 2 hanging below them (row 1 is set half a bubble to the right)
  grid(s, ["...11...", "...2...."]);
  s.cur = 1;
  // aim at the gap beside the pair on row 0
  const target = [B.cellX(s, 0, 5), B.cellY(s, 0)];
  B.aimAt(s, target[0], target[1]);
  const evs = shoot(s, s.aim, 1);
  assert.ok(types(evs).includes("pop"), JSON.stringify(s.grid.slice(0, 2)));
  assert.equal(s.stats.popped, 3);
  assert.equal(s.stats.dropped, 1);
  assert.equal(s.score, 3 * B.POP_POINTS + B.DROP_POINTS + B.CLEAR_POINTS, "and the board is clear");
  assert.ok(types(evs).includes("clear"));
  const more = run(s, B.CLEAR_T + 1);
  assert.ok(types(more).includes("level"));
  assert.equal(s.level, 2);
  assert.ok(B.count(s) > 0, "the next board");
});

test("the ceiling comes down every few shots; a bubble below the line ends the game", () => {
  const s = B.create({ seed: 5 });
  grid(s, ["12121212", "21212121", "12121212", "21212121"]);
  s.dropEvery = 2; s.sinceDrop = 0;
  const top = B.ceil(s);
  shoot(s, -70, 3); let evs = shoot(s, 70, 3);
  assert.ok(types(evs).includes("lower"));
  assert.equal(B.ceil(s), top + B.RH);
  s.dropEvery = 1;
  for (let i = 0; i < 12 && !s.over; i++) evs = evs.concat(shoot(s, i % 2 ? -60 : 60, 3), run(s, 60));
  assert.ok(s.over);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(B.result(s).stats.cause, "low");
});

test("Endless pushes a new row in from the top instead; ↓ or C swaps the next bubble in", () => {
  const s = B.create({ seed: 6, mode: "endless" });
  const n = B.count(s), par = s.parity;
  s.dropEvery = 1;
  const evs = shoot(s, -70);
  assert.ok(types(evs).includes("push"));
  assert.notEqual(s.parity, par);
  assert.ok(s.grid[0].every((k) => k > 0), "a full row came in at the top");
  assert.ok(B.count(s) > n - 3);
  assert.equal(B.ceil(s), B.TOP, "the ceiling stays");
  const cur = s.cur, next = s.next;
  assert.ok(types(B.press(s, "alt", true)).includes("swapNext"));
  assert.deepEqual([s.cur, s.next], [next, cur]);
  B.press(s, "down", true);
  assert.deepEqual([s.cur, s.next], [cur, next]);
});

test("Puzzles: built-in or the session's list; bad puzzles are skipped; the last clear wins", () => {
  assert.equal(B.usableLevels(undefined), B.LEVELS);
  const bad = [puzzle({ rows: ["11112222", "33334444"] }), puzzle({ rows: ["1111222", "33334444", "11112222"] }),
    puzzle({ rows: ["11112222", "33334444", "11112227"] }), puzzle({ colours: 2 }), puzzle({ colours: 3 }), puzzle({ drop: 3 }),
    puzzle({ drop: 13 }), puzzle({ rows: ["11112222", "........", "11112222"] }), puzzle({ rows: ["1.......", "1.......", "1......."] }),
    puzzle({ rows: Array(9).fill("11112222") }), puzzle({ name: "" }), null, 5];
  assert.equal(B.usableLevels(bad), B.LEVELS);
  assert.deepEqual(B.usableLevels(bad.concat([puzzle({ name: "Fine" })])).map((p) => p.name), ["Fine"]);
  for (const p of B.LEVELS) { assert.equal(B.levelProblem(p), null, p.name); assert.equal(B.usableLevels([p]).length, 1, p.name); }
  assert.equal(B.create({ mode: "classic", levels: [puzzle()] }).list, null);
  const two = [puzzle({ name: "A" }), puzzle({ name: "B", rows: ["11111111", "22222222", "33333333"], colours: 3 })];
  const s = B.create({ mode: "puzzle", seed: 3, levels: two });
  const all = run(s, 60 * 300, bot);
  assert.ok(s.won, JSON.stringify(B.result(s)));
  assert.ok(types(all).includes("level"));
  assert.ok(types(all).includes("win"));
  assert.equal(B.result(s).stats.boards, 2);
  const data = JSON.parse(JSON.stringify(B.save(B.create({ mode: "puzzle", seed: 1, levels: two }))));
  assert.ok(!("list" in data));
  assert.equal(B.restore(data, two).list.length, 2);
  // the good shooter solves most of the built-in puzzles
  let won = 0;
  for (let seed = 1; seed <= 4; seed++) { const p = B.create({ mode: "puzzle", seed }); run(p, 60 * 600, bot); if (p.won) won++; }
  assert.ok(won >= 2, `won ${won} of 4`);
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed) => { const s = B.create({ seed }); run(s, 60 * 40, bot); return s; };
  assert.equal(JSON.stringify(play(9)), JSON.stringify(play(9)));
  assert.notEqual(JSON.stringify(play(9).grid), JSON.stringify(play(10).grid));
  const s = B.create({ seed: 12, mode: "endless" });
  run(s, 900, bot);
  const copy = B.restore(JSON.parse(JSON.stringify(B.save(s))));
  for (let i = 0; i < 1800 && !s.over; i++) { bot(s); bot(copy); B.step(s); B.step(copy); }
  assert.equal(JSON.stringify(B.save(copy)), JSON.stringify(B.save(s)));
  assert.throws(() => B.restore({ grid: [] }), /can't be continued/);
  assert.throws(() => B.restore(null));
});

test("honest score: the good shooter stays inside the limits; the most the rules can pay is inside them too", () => {
  let top = 0;
  for (const [mode] of [["classic"], ["relaxed"], ["puzzle"], ["endless"]]) {
    for (const seed of [1, 2]) {
      const s = B.create({ seed, mode });
      while (!s.over && s.updates < 60 * 60 * 5) {
        bot(s); B.step(s);
        const sec = s.updates / 60;
        assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
        assert.ok(s.score <= LIMIT.max && s.level <= 100);
        if (sec > 5) top = Math.max(top, s.score / sec);
      }
    }
  }
  // a full board dropped at once, then the next board; a shot; an Endless row — each as often as the rules allow
  const shotGap = B.RELOAD + 3;
  const most = (64 * B.DROP_POINTS + B.CLEAR_POINTS) / ((B.CLEAR_T + shotGap) / 60) + B.DROP_POINTS / (shotGap / 60) + 8 * B.DROP_POINTS / (4 * shotGap / 60);
  assert.ok(most <= LIMIT.perSecond, `${most}`);
  console.log(`# bubbles: good shooter's fastest rate ${top.toFixed(1)}; the rules' most ${most.toFixed(0)} points a second`);
});

test("fuzz: random aims, shots, swaps and taps never throw and keep the board sound", () => {
  for (const mode of ["classic", "relaxed", "puzzle", "endless"]) {
    for (let seed = 1; seed <= 10; seed++) {
      const s = B.create({ seed, mode });
      let x = seed * 69069, last = 0;
      for (let i = 0; i < 5000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 7 === 0) B.press(s, ["left", "right", "fire", "up", "down", "alt"][x % 6], x % 3 !== 0);
        if (x % 17 === 0) B.aimAt(s, (x >> 3) % 300 - 30, (x >> 6) % 340 - 20);
        B.step(s);
        assert.ok(Number.isFinite(s.score) && s.score >= last);
        last = s.score;
        assert.ok(Math.abs(s.aim) <= B.MAX_AIM);
        if (s.shot) assert.ok(s.shot.x >= B.X0 + B.R - 0.01 && s.shot.x <= B.X1 - B.R + 0.01 && Number.isFinite(s.shot.y));
        for (let r = 0; r < B.MAX_ROWS; r++) for (let c = 0; c < 8; c++) assert.ok(s.grid[r][c] >= 0 && s.grid[r][c] <= 6);
        assert.ok(s.cur >= 1 && s.cur <= 6 && s.next >= 1 && s.next <= 6);
        assert.ok(s.level >= 1 && s.level <= 100);
      }
    }
  }
});

test("bubbles renders and plays in every look, aiming with the pointer and keys", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "relaxed", "endless"]) {
      const sb = makeSandbox({ extra: EXTRA });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("bubbles");
      assert.equal(def.name, "Bubble Pop");
      assert.equal(def.controls, "paddle");
      assert.equal(def.padLabel, "Shoot");
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      for (let i = 0; i < 900; i++) {
        if (i % 45 === 0) { inst.pointer("down", 100 + (i % 200), 150); inst.pointer("move", 120, 160); inst.pointer("up", 120, 160); }
        if (i % 33 === 0) { inst.input("left", true); inst.input("fire", true); inst.input("left", false); inst.input("alt", true); }
        if (i % 70 === 0) inst.pointer("move", 300, 480);                 // the strip below: x sets the angle
        if (i % 90 === 0) { inst.pointer("down", 80, 470); inst.pointer("up", 80, 470); }   // the next bubble
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}`);
      if (inst.state === "over") assert.equal(ends.length, 1); else assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});

test("Relaxed (for little ones): at most four colours, short boards and a ceiling that comes down only every 14 shots", () => {
  for (let seed = 1; seed <= 20; seed++) {
    const s = B.create({ seed, mode: "relaxed" });
    assert.equal(s.dropEvery, 14);
    assert.ok(s.colours <= 4);
    let rows = 0;
    for (let r = 0; r < B.MAX_ROWS; r++) if (s.grid[r].some((k) => k)) rows = r + 1;
    assert.ok(rows <= 6, `${rows} rows`);
    for (let r = 0; r < B.MAX_ROWS; r++) for (const k of s.grid[r]) assert.ok(k <= 4);
  }
  // the good shooter clears relaxed boards one after another
  const s = B.create({ seed: 3, mode: "relaxed" });
  while (!s.over && s.updates < 60 * 120) { bot(s); B.step(s); }
  assert.ok(s.level >= 2 || s.stats.popped > 20, `level ${s.level}, popped ${s.stats.popped}`);
});
