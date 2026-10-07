"use strict";
// Tile Match (wave 6): free tiles, solvable deals from the seed, matching, hint, shuffle, undo, the score, saving, a
// fuzz run and every look.
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadLogic, makeSandbox } = require("./helpers");
const T = loadLogic("tiles-logic.js");
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

function types(evs) { return evs.map((e) => e.type); }
/** Clear a heap: search for an order (depth first over free pairs), the way a careful player would. */
function clearHeap(s) {
  const P = T.places(s.mode);
  function rec(depth) {
    if (T.left(s) === 0) return true;
    const pairs = T.pairsFree(s);
    for (const [a, b] of pairs) {
      s.alive[a] = false; s.alive[b] = false;
      if (rec(depth + 1)) { s.alive[a] = true; s.alive[b] = true; s._order.unshift([a, b]); return true; }
      s.alive[a] = true; s.alive[b] = true;
    }
    return false;
  }
  s._order = [];
  assert.ok(P.length);
  const ok = rec(0);
  const order = s._order; delete s._order;
  if (!ok) return false;
  for (const [a, b] of order) { T.pick(s, a); T.pick(s, b); }
  return s.won;
}

test("layouts: an even number of tiles, four of each symbol, three sizes", () => {
  for (const mode of Object.keys(T.LAYOUTS)) {
    const n = T.places(mode).length;
    assert.equal(n % 2, 0);
    assert.equal(n, T.KINDS[mode] * 4, mode);
  }
  assert.deepEqual(Object.keys(T.LAYOUTS).map((m) => T.places(m).length), [20, 72, 104]);
});

test("free tiles: nothing on top, and the left or right side open", () => {
  // three in a row on one layer: only the ends are free; a tile on top of the middle frees nothing more
  const pl = [{ x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }, { x: 4, y: 0, z: 0 }, { x: 2, y: 0, z: 1 }];
  const alive = [true, true, true, true];
  assert.deepEqual([0, 1, 2, 3].map((i) => T.isFree(pl, alive, i)), [true, false, true, true]);
  alive[3] = false;
  assert.equal(T.isFree(pl, alive, 1), false, "still both sides closed");
  alive[0] = false;
  assert.equal(T.isFree(pl, alive, 1), true, "the left side opened");
  // a tile half-way on top covers both below it
  const pl2 = [{ x: 0, y: 0, z: 0 }, { x: 2, y: 0, z: 0 }, { x: 1, y: 0, z: 1 }];
  assert.deepEqual([0, 1].map((i) => T.isFree(pl2, [true, true, true], i)), [false, false]);
});

test("every deal from the seed can be cleared (the order it was made in clears it), four of each symbol, none stacked on its twin", () => {
  for (const mode of Object.keys(T.LAYOUTS)) {
    const P = T.places(mode);
    for (let seed = 1; seed <= 60; seed++) {
      const st = { rng: seed }, pairs = [];
      for (let k = 0; k < P.length / 2; k++) pairs.push(k % T.KINDS[mode]);
      let d = null;
      for (let t = 0; t < 200 && !d; t++) d = T.dealBackwards(P, P.map(() => true), pairs, st);
      assert.ok(d, `${mode} ${seed}`);
      const al = P.map(() => true);
      for (const [a, b] of d.order) {
        assert.ok(T.isFree(P, al, a) && T.isFree(P, al, b), "both free at that moment");
        assert.equal(d.kinds[a], d.kinds[b]);
        al[a] = false; al[b] = false;
      }
    }
    for (let seed = 1; seed <= 10; seed++) {
      const s = T.create({ mode, seed }), count = {};
      s.kinds.forEach((k) => { count[k] = (count[k] || 0) + 1; });
      assert.ok(Object.values(count).every((c) => c === 4), "four of each");
      assert.ok(T.pairsFree(s).length > 0, "a first move");
      const P2 = T.places(mode);
      for (let a = 0; a < P2.length; a++) for (let b = a + 1; b < P2.length; b++) {
        assert.ok(!(s.kinds[a] === s.kinds[b] && T.stacked(P2, a, b)), "two tiles of one symbol never lie on each other");
      }
    }
  }
});

test("picking: two free matching tiles go; a blocked tile is refused; the same tile again lets go", () => {
  const s = T.create({ mode: "classic", seed: 3 });
  const [a, b] = T.pairsFree(s)[0];
  assert.deepEqual(types(T.pick(s, a)), ["select"]);
  assert.deepEqual(types(T.pick(s, a)), ["unselect"]);
  T.pick(s, a);
  const evs = T.pick(s, b);
  assert.deepEqual(types(evs).slice(0, 1), ["match"]);
  assert.equal(s.alive[a], false);
  assert.equal(s.alive[b], false);
  assert.equal(T.left(s), 70);
  const blocked = s.alive.findIndex((v, i) => v && !T.free(s, i));
  assert.deepEqual(types(T.pick(s, blocked)), ["blocked"]);
  // two free tiles that don't match: the second becomes the selection
  const f = T.freeList(T.places(s.mode), s.alive);
  const x = f.find((i) => f.some((j) => j !== i && s.kinds[j] !== s.kinds[i]));
  const y = f.find((j) => j !== x && s.kinds[j] !== s.kinds[x]);
  T.pick(s, x);
  assert.deepEqual(types(T.pick(s, y)), ["nomatch", "select"]);
  assert.equal(s.sel, y);
});

test("clearing the heap wins; the score is TOP − the counted seconds; unfinished scores 0", () => {
  const s = T.create({ mode: "little", seed: 4 });
  assert.equal(T.result(s).score, 0);
  for (let i = 0; i < 1200; i++) T.step(s);
  assert.ok(clearHeap(s));
  assert.ok(s.over);
  assert.equal(T.result(s).score, T.TOP - 20);
  assert.deepEqual(T.result(s).stats.summary, ["Time 0:20", "Counted time 0:20"]);
});

test("hint rings a free pair (+15 s); undo puts a pair back; shuffle deals the rest so it can still be cleared (+30 s)", () => {
  const s = T.create({ mode: "classic", seed: 5 });
  const evs = T.hint(s);
  assert.deepEqual(types(evs), ["hint"]);
  const [a, b] = s.hint;
  assert.ok(T.free(s, a) && T.free(s, b) && s.kinds[a] === s.kinds[b]);
  for (let i = 0; i < T.HINT_T; i++) T.step(s);
  assert.equal(s.hint, null, "the ring goes after a while");
  T.pick(s, a); T.pick(s, b);
  T.undo(s);
  assert.ok(s.alive[a] && s.alive[b]);
  assert.equal(T.left(s), 72);
  const before = s.alive.slice();
  assert.deepEqual(types(T.shuffle(s)).slice(0, 1), ["shuffle"]);
  assert.deepEqual(s.alive, before, "the same places");
  const count = {};
  s.kinds.forEach((k) => { count[k] = (count[k] || 0) + 1; });
  assert.ok(Object.values(count).every((c) => c === 4));
  assert.ok(clearHeap(s), "still clearable");
  assert.equal(T.effective(s), T.HINT_PENALTY + T.SHUFFLE_PENALTY + T.seconds(s));
  assert.match(T.result(s).stats.summary.join(" "), /1 hint .* 1 shuffle/);
});

test("stuck: no free pair says so; Undo still works after a shuffle", () => {
  const s = T.create({ mode: "little", seed: 6 });
  // take pairs greedily until none is free (or it's cleared)
  let guard = 0;
  while (!s.over && T.pairsFree(s).length && guard++ < 50) { const [a, b] = T.pairsFree(s)[0]; T.pick(s, a); T.pick(s, b); }
  if (!s.over) {
    assert.equal(s.stuck, true);
    assert.match(s.message, /No pairs left/);
    T.shuffle(s);
    const n = T.left(s);
    assert.deepEqual(types(T.undo(s)), ["undo"]);
    assert.equal(T.left(s), n + 2);
  }
});

test("the keyboard cursor moves to the nearest tile that way; Space picks; H / S / U", () => {
  const s = T.create({ mode: "classic", seed: 7 });
  const P = T.places("classic");
  const start = s.cursor;
  T.press(s, "right", true);
  assert.ok(P[s.cursor].x > P[start].x);
  T.press(s, "left", true); T.press(s, "left", true);
  assert.ok(P[s.cursor].x < P[start].x);
  T.press(s, "up", true);
  T.press(s, "fire", true);
  assert.ok(s.sel === s.cursor || s.message);
  T.press(s, "key:H", true); assert.equal(s.hintsUsed, 1);
  T.press(s, "key:S", true); assert.equal(s.shuffles, 1);
  T.press(s, "alt", true); assert.equal(s.hintsUsed, 2);
});

test("deterministic for a seed; save and restore plays on the same; bad saves are refused", () => {
  assert.equal(JSON.stringify(T.create({ mode: "big", seed: 9 })), JSON.stringify(T.create({ mode: "big", seed: 9 })));
  assert.notEqual(JSON.stringify(T.create({ mode: "big", seed: 9 }).kinds), JSON.stringify(T.create({ mode: "big", seed: 10 }).kinds));
  const s = T.create({ mode: "classic", seed: 12 });
  for (let i = 0; i < 5; i++) { const p = T.pairsFree(s)[0]; T.pick(s, p[0]); T.pick(s, p[1]); T.step(s); }
  const copy = T.restore(JSON.parse(JSON.stringify(T.save(s))));
  T.shuffle(s); T.shuffle(copy);
  assert.equal(JSON.stringify(T.save(copy)), JSON.stringify(T.save(s)));
  const bad = T.save(T.create({ mode: "little", seed: 1 }));
  bad.alive[0] = false;                                   // an odd number of one symbol left
  assert.throws(() => T.restore(bad), /can't be continued/);
  assert.throws(() => T.restore({ mode: "little", kinds: [1] }), /can't be continued/);
  assert.throws(() => T.restore(null));
});

test("honest score: never above TOP however quickly it is cleared", () => {
  const s = T.create({ mode: "little", seed: 2 });
  assert.ok(clearHeap(s));
  assert.ok(T.result(s).score <= 10000 && T.result(s).score >= T.MIN_SCORE);
});

test("fuzz: random picks and keys never throw; symbols stay in pairs", () => {
  for (const mode of Object.keys(T.LAYOUTS)) {
    for (let seed = 1; seed <= 6; seed++) {
      const s = T.create({ mode, seed });
      let x = seed * 7919;
      for (let i = 0; i < 2500 && !s.over; i++) {
        x = (x * 1103515245 + 12345) & 0x7fffffff;
        if (x % 3 === 0) T.press(s, ["up", "down", "left", "right", "fire", "alt", "shuffle", "undo", "key:H"][x % 9], true);
        if (x % 4 === 0) T.pick(s, x % (s.alive.length + 2) - 1);
        if (x % 11 === 0) { const p = T.pairsFree(s)[0]; if (p) { T.pick(s, p[0]); T.pick(s, p[1]); } }
        T.step(s);
        const count = {};
        s.alive.forEach((v, k) => { if (v) count[s.kinds[k]] = (count[s.kinds[k]] || 0) + 1; });
        assert.ok(Object.values(count).every((c) => c % 2 === 0));
        assert.ok(s.sel === -1 || s.alive[s.sel]);
      }
    }
  }
});

test("tiles renders and plays in every look, with taps and keys", () => {
  for (const look of LOOKS) {
    for (const mode of ["little", "classic", "big"]) {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const def = sb.win.ArcadeGames.get("tiles");
      assert.equal(def.name, "Tile Match");
      assert.deepEqual(JSON.parse(JSON.stringify(def.buttons.map((b) => b.action))), ["hint", "shuffle", "undo"]);
      const inst = def.create(canvas, { mode, look, seed: 5 });
      inst.start();
      for (let i = 0; i < 200; i++) {
        if (i % 9 === 0) inst.input(["up", "left", "fire", "right", "down", "hint", "shuffle", "undo"][(i / 9) % 8], true);
        if (i % 7 === 0) inst.pointer("down", 40 + (i * 13) % 320, 90 + (i * 7) % 330);
        sb.frames(1);
      }
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, `${look}/${mode}: no NaN or Infinity reaches the canvas`);
      if (inst.state !== "over") assert.ok(inst.save().state);
      inst.destroy();
      assert.equal(sb.pending(), 0);
    }
  }
});
