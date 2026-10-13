"use strict";
// Drawing only when something changed (kit.js drawKey): a board game or a puzzle left alone isn't drawn 60 times a
// second, yet input, a move's animation, the clock's seconds and a new size still show at once; a game without a
// drawKey is drawn every frame as before. The renderer builds its layers again only when the canvas's size changed.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox } = require("./helpers");

function drawsIn(sb, frames) {
  const before = sb.counts.fillText || 0;
  sb.frames(frames);
  return (sb.counts.fillText || 0) - before;
}

test("a board game between moves is drawn only when something changes", () => {
  const sb = makeSandbox();
  const def = sb.win.ArcadeGames.get("chess");
  const inst = def.create(sb.canvas(), { mode: "two", seed: 3, onEnd() {}, onScore() {}, onEvent() {} });
  inst.start();
  assert.ok(drawsIn(sb, 30) > 0, "drawn while it has just started");
  sb.frames(300);                                              // past the moment after a change
  assert.equal(drawsIn(sb, 120), 0, "two idle seconds: not drawn at all");
  inst.input("right", true);                                   // the cursor moves
  assert.ok(drawsIn(sb, 2) > 0, "input shows at once");
  sb.frames(300);
  assert.equal(drawsIn(sb, 60), 0);
  inst.resize();
  assert.ok(drawsIn(sb, 1) > 0, "a new size is drawn");
  inst.setLook("neon");
  sb.frames(300);
  assert.equal(drawsIn(sb, 60), 0);
  inst.destroy();
});

test("a puzzle's clock still ticks: drawn about once a second when left alone", () => {
  const sb = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js"] });
  const def = sb.win.ArcadeGames.get("sudoku");
  const inst = def.create(sb.canvas(), { mode: def.defaultMode, seed: 5, onEnd() {}, onScore() {}, onEvent() {} });
  inst.start();
  sb.frames(10);
  const counts = [];
  for (let i = 0; i < 4; i++) {
    const before = sb.counts.fillText || 0;
    let frames = 0;
    for (let f = 0; f < 60; f++) { sb.frames(1); if ((sb.counts.fillText || 0) > before && !frames) frames = f + 1; }
    counts.push((sb.counts.fillText || 0) - before > 0);
  }
  assert.deepEqual(counts, [true, true, true, true], "the clock's second changes once a second");
  const before = sb.counts.fillText || 0;
  sb.frames(60);
  const perSecond = (sb.counts.fillText || 0) - before;
  const one = (() => { const b = sb.counts.fillText || 0; inst.input("right", true); sb.frames(1); return (sb.counts.fillText || 0) - b; })();
  assert.ok(perSecond <= one * 2, `one second alone draws about one picture (${perSecond} texts vs ${one} in one picture)`);
  inst.destroy();
});

test("a game without a drawKey is drawn every frame, as before", () => {
  const sb = makeSandbox();
  const def = sb.win.ArcadeGames.get("snake");
  const inst = def.create(sb.canvas(), { mode: "walls-normal", seed: 3, onEnd() {}, onScore() {}, onEvent() {} });
  inst.start();
  sb.frames(10);
  let drawn = 0;
  for (let i = 0; i < 30; i++) { const b = sb.counts.fillText || 0; sb.frames(1); if ((sb.counts.fillText || 0) > b) drawn++; }
  assert.equal(drawn, 30);
  inst.destroy();
});

test("the renderer builds its layers again only when the size changed", () => {
  const sb = makeSandbox();
  const canvas = sb.canvas(390, 487);
  const r = sb.win.ArcadeKit.createRenderer(canvas, "lcd", {});          // (the LCD look draws through layers)
  let created = 0;
  const realCreate = sb.doc.createElement;
  sb.doc.createElement = function (tag) { created++; return realCreate.call(sb.doc, tag); };
  const paint = () => r.frame((g) => { g.text("hi", 120, 150, { size: 10 }); });
  paint();
  const first = created;
  r.resize(); r.resize();
  paint();
  assert.equal(created, first, "same size: the layers are kept");
  canvas.clientWidth = 300; canvas.clientHeight = 375;
  r.resize();
  paint();
  assert.ok(created > first, "a new size builds them again");
  r.destroy();
});
