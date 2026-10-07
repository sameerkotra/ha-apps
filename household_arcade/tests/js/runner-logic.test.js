"use strict";
// Runner (wave 5): the rules, the course list (passable by construction), saving, the honest-score limits, a fuzz
// run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const R = loadLogic("runner-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const EXTRA = ["runner-logic.js", "runner.js"];
const LIMIT = { perSecond: 500, base: 500, max: 5000000 };

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...R.step(s)); } return all; }
function course(extra) { return Object.assign({ name: "Test", speed: 3.5, ramp: 0, pattern: "..b....b....b...." }, extra || {}); }
/** A careful runner: jumps boxes and pits in time, ducks bars and fliers. */
function bot(s) {
  const v = R.speed(s), px = s.dist + R.PX;
  let nx = null;
  for (const o of s.obs) if (o.k !== "coin" && o.x + o.w > px - 8 && (!nx || o.x < nx.x)) nx = o;
  if (!nx) { R.press(s, "up", false); R.press(s, "down", false); return; }
  const dx = nx.x - (px + 6);
  if (nx.k === "h" || nx.k === "f") { R.press(s, "up", false); R.press(s, "down", dx < v * 3 + 6); return; }
  R.press(s, "down", false);
  const lead = nx.k === "p" ? v * 3 : nx.k === "B" ? v * 7 : v * 6;
  if (s.ground && dx < lead + 4 && dx > -4) R.press(s, "up", true); else if (s.ground) R.press(s, "up", false);
  if (!s.ground && s.vy > 0) R.press(s, "up", false);
}
function place(s, k, ahead) {
  s.obs = s.obs.filter((o) => o.x > s.dist + 400);
  const x = s.dist + R.PX + ahead;
  const shapes = { b: { w: 18, top: R.GY - 22, bot: R.GY }, p: { w: 40, top: R.GY, bot: R.GY + 60 }, h: { w: 22, top: R.TOP, bot: R.GY - 20 },
    f: { w: 16, top: R.GY - 32, bot: R.GY - 20 }, coin: { w: 8, top: R.GY - 14, bot: R.GY - 6 } };
  s.obs.push(Object.assign({ k, x }, shapes[k]));
}

test("the start: on the ground, lives by mode, a lead-in with nothing in the way", () => {
  const s = R.create({ seed: 1 });
  assert.equal(s.y, R.GY);
  assert.equal(s.lives, 1);
  assert.equal(R.create({ mode: "easy" }).lives, 3);
  assert.equal(R.create({ mode: "courses" }).lives, 3);
  for (const o of s.obs) assert.ok(o.k === "coin" || o.x >= R.LEAD * R.SEG, "nothing in the first segments");
  assert.equal(R.speed(s), R.MODES.classic.v0);
});

test("jumping: a tap jumps, holding jumps higher, ducking in the air drops faster; one point a 10 px", () => {
  const s = R.create({ seed: 2 });
  s.obs = []; s.nextSeg = 1e9;
  R.press(s, "up", true); R.press(s, "up", false);
  let evs = R.step(s), top = R.GY;
  assert.ok(types(evs).includes("jump"));
  evs = run(s, 80, (st) => { top = Math.min(top, st.y); });
  assert.ok(types(evs).includes("land"));
  const tap = R.GY - top;
  const h = R.create({ seed: 2 }); h.obs = []; h.nextSeg = 1e9;
  R.press(h, "up", true);
  let topH = R.GY;
  run(h, 80, (st) => { topH = Math.min(topH, st.y); });
  assert.ok(R.GY - topH > tap + 15, `held ${R.GY - topH} vs tap ${tap}`);
  assert.ok(tap > 40, "a tap clears a tall box");
  const d = R.create({ seed: 2 }); d.obs = []; d.nextSeg = 1e9;
  R.press(d, "up", true); R.press(d, "up", false); R.step(d);
  run(d, 5); R.press(d, "down", true);
  let n = 0; while (!d.ground && n < 80) { R.step(d); n++; }
  assert.ok(n < 20, "drops faster");
  assert.equal(d.duck, true);
  const p = R.create({ seed: 3 }); p.obs = []; p.nextSeg = 1e9;
  run(p, 100);
  assert.ok(Math.abs(p.score - R.MODES.classic.v0 * 100 / 10) <= 1, `${p.score}`);
});

test("a box, a bar, a flier and a pit: what hits and what passes", () => {
  const hitBy = (k, act) => {
    const s = R.create({ seed: 4, mode: "classic" });
    s.nextSeg = 1e9; place(s, k, 30);
    return types(run(s, 60, act)).includes("hit") ? R.result(s).stats.cause : null;
  };
  assert.equal(hitBy("b"), "box");
  assert.equal(hitBy("h"), "bar");
  assert.equal(hitBy("f"), "flier");
  assert.equal(hitBy("p"), "pit");
  assert.equal(hitBy("h", (s) => R.press(s, "down", true)), null, "ducking passes under a bar");
  assert.equal(hitBy("f", (s) => R.press(s, "down", true)), null, "and under a flier");
  assert.equal(hitBy("b", bot), null, "jumping clears a box");
  assert.equal(hitBy("p", bot), null, "and a pit");
  // a coin
  const c = R.create({ seed: 4 }); c.nextSeg = 1e9; place(c, "coin", 20);
  const evs = run(c, 30);
  assert.ok(types(evs).includes("coin"));
  assert.equal(c.stats.coins, 1);
});

test("Easy has three lives with a safe moment after a hit; Endless ends at the first", () => {
  const s = R.create({ seed: 5, mode: "easy" });
  s.nextSeg = 1e9; place(s, "b", 20);
  let evs = run(s, 30);
  assert.ok(types(evs).includes("hit"));
  assert.equal(s.lives, 2);
  evs = run(s, R.DEAD_T + 2);
  assert.ok(!s.over && s.safe > 0, "safe after coming back");
  place(s, "b", 10);
  evs = run(s, 20);
  assert.ok(!types(evs).includes("hit"), "the safe moment passes through");
  const e = R.create({ seed: 5 }); e.nextSeg = 1e9; place(e, "b", 20);
  run(e, R.DEAD_T + 40);
  assert.ok(e.over);
  assert.equal(R.result(e).stats.cause, "box");
});

test("speed rises with distance and never passes the top; hazards are always far enough apart", () => {
  const s = R.create({ seed: 6 });
  s.dist = 50 * R.LEVEL_PX;
  assert.equal(R.speed(s), R.MODES.classic.vmax);
  assert.equal(R.speed(Object.assign(R.create({ mode: "easy" }), { dist: 1e6 })), R.MODES.easy.vmax);
  for (const mode of ["classic", "easy"]) {
    const t = R.create({ seed: 7, mode });
    let lastHazard = null;
    const seen = new Set();
    for (let i = 0; i < 60 * 300; i++) {
      bot(t); R.step(t);
      for (const o of t.obs) if ("bBphf".includes(o.k) && !seen.has(o.x)) {
        seen.add(o.x);
        if (lastHazard !== null) assert.ok((o.x - lastHazard) / R.SEG >= R.gapSegs(Math.min(R.VMAX, R.speed(t))) - 1, `${mode}: gap at ${o.x}`);
        lastHazard = o.x;
      }
    }
    assert.equal(t.stats.hits, 0, `${mode}: the careful runner never gets hit`);
  }
});

test("Courses: built-in or the session's list; bad courses are skipped; the end of a course moves on, the last wins", () => {
  assert.equal(R.usableLevels(undefined), R.LEVELS);
  const bad = [course({ pattern: "..b.b.....b..." }), course({ pattern: "..b..." }), course({ pattern: "....c....c...." }),
    course({ speed: 2.9 }), course({ speed: 7.1 }), course({ ramp: 1.1 }), course({ pattern: "..x....b....b...." }),
    course({ name: "" }), null, "x"];
  assert.equal(R.usableLevels(bad), R.LEVELS);
  assert.deepEqual(R.usableLevels(bad.concat([course({ name: "Fine" })])).map((c) => c.name), ["Fine"]);
  for (const c of R.LEVELS) { assert.equal(R.courseProblem(c), null, c.name); assert.equal(R.usableLevels([c]).length, 1, c.name); }
  const two = [course({ name: "A" }), course({ name: "B", pattern: "..p....p....p...." })];
  const s = R.create({ mode: "courses", seed: 3, levels: two });
  assert.equal(s.courses.length, 2);
  assert.equal(R.create({ mode: "classic", levels: two }).courses, null);
  const all = run(s, 60 * 60, bot);
  assert.ok(types(all).includes("course"));
  assert.ok(types(all).includes("level"));
  assert.ok(s.won, "both courses run");
  assert.ok(types(all).includes("win"));
  assert.equal(R.result(s).stats.courses, 2);
  assert.ok(s.obs.length >= 0);
  // the built-in courses can all be run without a hit
  const b = R.create({ mode: "courses", seed: 1 });
  run(b, 60 * 600, bot);
  assert.ok(b.won);
  assert.equal(b.stats.hits, 0);
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed) => { const s = R.create({ seed }); run(s, 60 * 60, bot); return s; };
  assert.equal(JSON.stringify(play(9)), JSON.stringify(play(9)));
  assert.notEqual(JSON.stringify(play(9).obs), JSON.stringify(play(10).obs));
  const s = R.create({ seed: 12, mode: "courses" });
  run(s, 600, bot);
  const copy = R.restore(JSON.parse(JSON.stringify(R.save(s))));
  for (let i = 0; i < 1800 && !s.over; i++) { bot(s); bot(copy); R.step(s); R.step(copy); }
  assert.equal(JSON.stringify(R.save(copy)), JSON.stringify(R.save(s)));
  assert.throws(() => R.restore({ dist: "x" }), /can't be continued/);
  assert.throws(() => R.restore(null));
});

test("honest score: running flat out with every coin there could be stays inside the limits", () => {
  const coins = [];
  for (let i = 0; i < 6; i++) coins.push(course({ name: "Coins " + i, speed: 7, ramp: 1, pattern: ("oooooooo" + "b").repeat(10).slice(0, 120) }));
  let top = 0;
  for (const [mode, levels] of [["classic"], ["easy"], ["courses"], ["courses", R.usableLevels(coins) === R.LEVELS ? undefined : coins]]) {
    const s = R.create({ seed: 1, mode, levels });
    while (!s.over && s.updates < 60 * 60 * 10) {
      bot(s);
      if (s.safe < 2) s.safe = 2;              // never hit: it runs as long as it can
      R.step(s);
      const sec = s.updates / 60;
      assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
      assert.ok(s.score <= LIMIT.max && s.level <= 100);
      if (sec > 5) top = Math.max(top, s.score / sec);
    }
  }
  // the most a stretch of road can pay: every segment an arc of 5 coins at the top speed
  const perSecond = 60 * R.VMAX / 10 + 60 * R.VMAX / R.SEG * 5 * R.COIN_POINTS;
  assert.ok(perSecond <= LIMIT.perSecond, `${perSecond}`);
  console.log(`# runner: quick bot's fastest rate ${top.toFixed(1)} points a second (the rules allow ${perSecond})`);
});

test("fuzz: random keys never throw and keep the runner sound", () => {
  for (const mode of ["classic", "easy", "courses"]) {
    for (let seed = 1; seed <= 12; seed++) {
      const s = R.create({ seed, mode });
      let x = seed * 104729, last = 0;
      for (let i = 0; i < 5000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 7 === 0) R.press(s, ["up", "down", "fire", "left", "alt"][x % 5], x % 3 !== 0);
        R.step(s);
        assert.ok(Number.isFinite(s.score) && s.score >= last && Number.isFinite(s.y) && Number.isFinite(s.dist));
        last = s.score;
        assert.ok(s.y <= R.GY + 60 && s.y > 0, `y ${s.y}`);
        assert.ok(s.level >= 1 && s.level <= 100 && s.lives >= 0);
      }
    }
  }
});

test("runner renders and plays in every look, with keys, taps and drags", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "courses"]) {
      const sb = makeSandbox({ extra: EXTRA });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("runner");
      assert.equal(def.name, "Runner");
      assert.equal(def.controls, "buttons");
      assert.deepEqual(JSON.parse(JSON.stringify(def.buttons.map((b) => b.action))), ["down", "up"]);
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      for (let i = 0; i < 700; i++) {
        bot(inst.logic);
        if (i % 50 === 0) { inst.pointer("down", 100, 200); inst.pointer("move", 100, 240); inst.pointer("up", 100, 240); }
        if (i % 40 === 0) { inst.input("up", true); inst.input("up", false); }
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
