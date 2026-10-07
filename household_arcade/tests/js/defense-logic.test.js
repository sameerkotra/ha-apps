"use strict";
// City Defense (wave 5): the rules (interceptors, clouds, splitting missiles, fliers, cities and bases, the wave's
// bonus), the wave list, saving, the honest-score limits, a fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const D = loadLogic("defense-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];
const EXTRA = ["defense-logic.js", "defense.js"];
const LIMIT = { perSecond: 300, base: 2500, max: 5000000 };

function types(evs) { return evs.map((e) => e.type); }
function run(s, n, each) { const all = []; for (let i = 0; i < n && !s.over; i++) { if (each) each(s); all.push(...D.step(s)); } return all; }
function wave(extra) { return Object.assign({ name: "Test", missiles: 6, speed: 0.5, splits: 0, fliers: 0, ammo: 10 }, extra || {}); }
function missile(s, x, y, tx, ty, kind, idx, split) {
  const d = Math.hypot(tx - x, ty - y);
  s.enemies.push({ x0: x, y0: y, x, y, vx: (tx - x) / d * 0.5, vy: (ty - y) / d * 0.5, tx, ty, tk: kind, ti: idx, split: split || 0 });
}
// A defender: aims a little ahead of the lowest missile no cloud or interceptor covers yet.
function bot(s) {
  if (s.phase !== "wave" || s.updates % 6) return;
  let best = null;
  for (const m of s.enemies) {
    let covered = false;
    for (const b of s.booms) if (!b.hurt && Math.hypot(b.x - m.x, b.y - m.y) < 26) covered = true;
    for (const sh of s.shots) if (Math.hypot(sh.tx - (m.x + m.vx * sh.left), sh.ty - (m.y + m.vy * sh.left)) < 16) covered = true;
    if (!covered && (!best || m.y > best.y)) best = m;
  }
  if (!best && s.fliers.length) { const f = s.fliers[0]; D.aim(s, f.x + f.vx * 30, f.y); return; }
  if (best) D.aim(s, best.x + best.vx * 30, Math.min(best.y + best.vy * 30, D.GROUND - 30));
}
/** Stops everything the moment it appears (a cheat): the most points as early as the rules allow. */
function perfect(s) {
  for (const m of s.enemies.splice(0)) { s.score += D.MISSILE_POINTS; s.stats.missiles++; }
  for (const f of s.fliers.splice(0)) { s.score += D.FLIER_POINTS; s.stats.fliers++; }
}

test("the start: six cities, three bases with the wave's ammo, a schedule of missiles", () => {
  const s = D.create({ seed: 1 });
  assert.equal(s.cities.filter(Boolean).length, 6);
  assert.deepEqual(s.bases.map((b) => b.ammo), [10, 10, 10]);
  assert.equal(s.schedule.filter((x) => x.k !== "flier").length, D.waveSpec(s, 1).missiles);
  assert.ok(s.schedule.every((x) => x.at >= D.START_T));
  const last = s.schedule[s.schedule.length - 1].at;
  assert.ok(last >= D.START_T + 240, "a wave lasts its whole spread");
  assert.ok(D.waveSpec(D.create({ mode: "easy" }), 5).speed < D.waveSpec(s, 5).speed);
});

test("a tap sends the nearest base's interceptor; it bursts into a cloud that stops a missile", () => {
  const s = D.create({ seed: 2 });
  s.schedule = [{ at: 1e9, k: "m" }];
  let evs = D.aim(s, 30, 150);
  assert.ok(types(evs).includes("fire"));
  assert.equal(s.bases[0].ammo, 9, "the left base");
  D.aim(s, 200, 150);
  assert.equal(s.bases[2].ammo, 9, "the right base");
  D.aim(s, 120, 150);
  assert.equal(s.bases[1].ammo, 9, "the middle base");
  missile(s, 30, 120, 44, D.GROUND - 4, "city", 0);
  const m = s.enemies[0];
  D.aim(s, m.x + m.vx * 30, m.y + m.vy * 30);          // where it will be when the interceptor gets there
  evs = run(s, 60);
  assert.ok(types(evs).includes("burst"));
  assert.ok(types(evs).includes("hit"));
  assert.equal(s.score, D.MISSILE_POINTS);
  // keys and a controller: the crosshair and fire
  D.press(s, "left", true); run(s, 10); D.press(s, "left", false);
  assert.ok(s.cross.x < 120);
  D.press(s, "fire", true);
  assert.ok(types(D.step(s)).includes("fire"));
});

test("a missile that gets through destroys its city or base; no ammo left: nothing fires", () => {
  const s = D.create({ seed: 3 });
  s.schedule = [{ at: 1e9, k: "m" }];
  missile(s, 44, D.GROUND - 20, 44, D.GROUND - 4, "city", 0);
  missile(s, 14, D.LAUNCH_Y - 10, 14, D.LAUNCH_Y, "base", 0);
  const evs = run(s, 60);
  assert.ok(types(evs).includes("cityLost"));
  assert.ok(types(evs).includes("baseLost"));
  assert.equal(s.cities[0], false);
  assert.equal(s.bases[0].alive, false);
  for (const b of s.bases) b.ammo = 0;
  assert.ok(types(D.aim(s, 100, 100)).includes("empty"));
});

test("splitting missiles become three; fliers drop one and can be shot for 100", () => {
  const s = D.create({ seed: 4 });
  s.schedule = [{ at: 1e9, k: "m" }];
  missile(s, 100, 100, 148, D.GROUND - 4, "city", 3, 105);
  const evs = run(s, 20);
  assert.ok(types(evs).includes("split"));
  assert.equal(s.enemies.length, 3);
  const f = D.create({ seed: 4 });
  f.schedule = [{ at: 1e9, k: "m" }];
  f.fliers.push({ x: 10, y: 90, vx: 0.8, drop: 30, dropped: false });
  run(f, 40);
  assert.equal(f.fliers[0].dropped, true);
  assert.equal(f.enemies.length, 1);
  f.booms.push({ x: f.fliers[0].x, y: 90, t: 0, r: 0, small: false });
  const e2 = run(f, 10);
  assert.ok(types(e2).includes("flierHit"));
  assert.equal(f.score, D.FLIER_POINTS);
});

test("the end of a wave: ammo left × 5 + cities × 100, then the next wave with fresh bases; every third wave a city comes back", () => {
  const s = D.create({ seed: 5 });
  s.schedule = []; s.cities[2] = false; s.bases[1].ammo = 4;
  let evs = run(s, 2);
  assert.ok(types(evs).includes("clear"));
  assert.equal(s.bonus.points, (10 + 4 + 10) * D.AMMO_POINTS + 5 * D.CITY_POINTS);
  evs = run(s, D.BONUS_T + 1);
  assert.ok(types(evs).includes("level"));
  assert.equal(s.wave, 2);
  assert.deepEqual(s.bases.map((b) => b.ammo), [D.waveSpec(s, 2).ammo, D.waveSpec(s, 2).ammo, D.waveSpec(s, 2).ammo]);
  assert.equal(s.cities[2], false);
  for (let w = 2; w <= 3; w++) { s.schedule = []; s.enemies = []; s.fliers = []; run(s, D.BONUS_T + 3); }
  assert.equal(s.wave, 4);
  assert.equal(s.cities.filter(Boolean).length, 6, "rebuilt after the third wave");
});

test("losing every city ends the game", () => {
  const s = D.create({ seed: 6 });
  s.schedule = [{ at: 1e9, k: "m" }];
  for (let i = 0; i < 5; i++) s.cities[i] = false;
  missile(s, 196, D.GROUND - 10, 196, D.GROUND - 4, "city", 5);
  const evs = run(s, 200);
  assert.ok(s.over);
  assert.ok(types(evs).includes("gameover"));
  assert.equal(D.result(s).stats.cause, "cities");
  assert.equal(D.result(s).stats.citiesLeft, 0);
});

test("Waves: built-in or the session's list; bad waves are skipped; the last wave wins", () => {
  assert.equal(D.usableLevels(undefined), D.LEVELS);
  const bad = [wave({ missiles: 3 }), wave({ missiles: 31 }), wave({ speed: 0.2 }), wave({ speed: 1.5 }), wave({ splits: 9 }),
    wave({ fliers: 5 }), wave({ ammo: 5 }), wave({ ammo: 16 }), wave({ missiles: 21, speed: 1.1 }), wave({ missiles: 30, splits: 8, fliers: 4, ammo: 6 }),
    wave({ missiles: 4, splits: 5 }), wave({ name: "" }), null, 1];
  assert.equal(D.usableLevels(bad), D.LEVELS);
  assert.deepEqual(D.usableLevels(bad.concat([wave({ name: "Fine" })])).map((w) => w.name), ["Fine"]);
  for (const w of D.LEVELS) { assert.equal(D.waveProblem(w), null, w.name); assert.equal(D.usableLevels([w]).length, 1, w.name); }
  const two = [wave({ name: "A" }), wave({ name: "B", missiles: 8 })];
  const s = D.create({ mode: "waves", seed: 3, levels: two });
  assert.equal(D.create({ mode: "classic", levels: two }).waves, null);
  const all = run(s, 60 * 120, bot);
  assert.ok(s.over);
  assert.ok(types(all).includes("level"));
  if (s.won) { assert.ok(types(all).includes("win")); assert.equal(D.result(s).stats.waves, 2); }
  const data = JSON.parse(JSON.stringify(D.save(D.create({ mode: "waves", seed: 3, levels: two }))));
  assert.ok(!("waves" in data));
  assert.equal(D.restore(data, two).waves.length, 2);
  // the built-in waves can be won
  let won = 0;
  for (let seed = 1; seed <= 4; seed++) { const b = D.create({ mode: "waves", seed }); run(b, 60 * 600, bot); if (b.won) won++; }
  assert.ok(won >= 3, `won ${won} of 4`);
});

test("deterministic for a seed; save and restore plays on the same", () => {
  const play = (seed) => { const s = D.create({ seed }); run(s, 60 * 90, bot); return s; };
  assert.equal(JSON.stringify(play(9)), JSON.stringify(play(9)));
  assert.notEqual(JSON.stringify(play(9).schedule), JSON.stringify(play(10).schedule));
  const s = D.create({ seed: 12, mode: "easy" });
  run(s, 900, bot);
  const copy = D.restore(JSON.parse(JSON.stringify(D.save(s))));
  for (let i = 0; i < 3000 && !s.over; i++) { bot(s); bot(copy); D.step(s); D.step(copy); }
  assert.equal(JSON.stringify(D.save(copy)), JSON.stringify(D.save(s)));
  assert.throws(() => D.restore({ cities: 6 }), /can't be continued/);
  assert.throws(() => D.restore(null));
});

test("honest score: stopping every missile the moment it appears stays inside the limits, also on the fullest waves", () => {
  const fullest = [];
  for (let i = 0; i < 6; i++) fullest.push(wave({ name: "Full " + i, missiles: 30, speed: 1, splits: 8, fliers: 4, ammo: 15 }));
  let top = 0;
  for (const [mode, levels] of [["classic"], ["easy"], ["waves"], ["waves", fullest]]) {
    const s = D.create({ seed: 1, mode, levels });
    while (!s.over && s.updates < 60 * 60 * 10) {
      perfect(s); D.step(s);
      const sec = s.updates / 60;
      assert.ok(s.score <= sec * LIMIT.perSecond + LIMIT.base, `${mode}: ${s.score} after ${sec.toFixed(1)} s`);
      assert.ok(s.score <= LIMIT.max && s.level <= 100);
      if (sec > 10) top = Math.max(top, s.score / sec);
    }
  }
  console.log(`# defense: fastest possible rate ${top.toFixed(1)} points a second`);
});

test("fuzz: random taps and keys never throw and keep the game sound", () => {
  for (const mode of ["classic", "easy", "waves"]) {
    for (let seed = 1; seed <= 10; seed++) {
      const s = D.create({ seed, mode });
      let x = seed * 2654435761 >>> 0, last = 0;
      for (let i = 0; i < 5000 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 11 === 0) D.aim(s, (x >> 3) % 260 - 10, (x >> 5) % 320 - 10);
        if (x % 13 === 0) D.press(s, ["up", "down", "left", "right", "fire"][x % 5], x % 2 === 0);
        D.step(s);
        assert.ok(Number.isFinite(s.score) && s.score >= last);
        last = s.score;
        assert.ok(s.bases.every((b) => b.ammo >= 0 && b.ammo <= 15));
        assert.ok(s.shots.length <= D.MAX_SHOTS);
        assert.ok(s.enemies.every((m) => Number.isFinite(m.x) && Number.isFinite(m.y) && m.y <= D.GROUND + 1));
        assert.ok(s.level >= 1 && s.level <= 100);
      }
    }
  }
});

test("defense renders and plays in every look, with taps and keys", () => {
  for (const look of LOOKS) {
    for (const mode of ["classic", "waves"]) {
      const sb = makeSandbox({ extra: EXTRA });
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("defense");
      assert.equal(def.name, "City Defense");
      assert.equal(def.controls, "touch");
      const ends = [];
      const inst = def.create(canvas, { mode, look, seed: 5, onEnd: (r) => ends.push(r) });
      inst.start();
      for (let i = 0; i < 900; i++) {
        bot(inst.logic);
        if (i % 37 === 0) inst.pointer("down", (i * 13) % 390, (i * 7) % 300);
        if (i % 50 === 0) { inst.input("up", true); inst.input("fire", true); inst.input("up", false); }
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
