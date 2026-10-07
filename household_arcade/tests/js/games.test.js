"use strict";
// The browser side of the games, in a fake DOM: the registry and the contract the
// app's shell relies on, every look rendering without throwing, the game loop,
// pause, end results and sound being off by default.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox } = require("./helpers");

// Values made inside the sandbox have the sandbox's Array prototype; compare plain copies.
const plain = (v) => JSON.parse(JSON.stringify(v));
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

test("the kit has the six looks, each with a label", () => {
  const { win } = makeSandbox();
  assert.deepEqual(Object.keys(win.ArcadeKit.LOOKS), LOOKS);
  for (const id of LOOKS) assert.ok(win.ArcadeKit.LOOKS[id].label, id);
  assert.equal(win.ArcadeKit.LOOKS.lcd.label, "Retro LCD");
  assert.equal(win.ArcadeKit.LOOKS.contrast.label, "High contrast");
  assert.equal(typeof win.ArcadeKit.createRenderer, "function");
});

test("registry: the games with the agreed modes and controls", () => {
  const { win } = makeSandbox();
  const G = win.ArcadeGames;
  assert.deepEqual(plain(G.list().map((g) => g.id)), ["snake", "brick", "blocks", "duel", "racer", "flap", "mines", "merge", "colours", "cards", "mole", "numbers", "tanks", "invaders", "rocks", "hop", "snakeduel",
    "bubbles", "gems", "stack", "runner", "lander", "defense", "slide", "lights", "nonogram", "tiles", "codebreak", "typerain",
    "fourrow", "tictactoe", "checkers", "reversi", "dots", "seabattle", "ludo", "snakes", "carrom", "chess"]);
  const want = {
    blocks: ["Falling Blocks", ["classic", "fast", "rising", "challenge"], "classic", "buttons"],
    duel: ["Paddle Duel", ["easy", "normal", "hard", "phones"], "normal", "paddle"],
    racer: ["Lane Racer", ["three", "four", "rush", "stages"], "three", "buttons"],
    flap: ["Flap", ["easy", "normal", "moving", "course"], "normal", "buttons"],
  };
  for (const [id, [name, modes, def, controls]] of Object.entries(want)) {
    const g = G.get(id);
    assert.equal(g.name, name);
    assert.deepEqual(plain(g.modes.map((m) => m.id)), modes);
    assert.equal(g.defaultMode, def);
    assert.equal(g.controls, controls);
    assert.equal(g.stateVersion, 1);
    assert.ok(g.help.length > 10);
    if (controls === "buttons") {
      assert.ok(g.buttons.length >= 1);
      for (const b of g.buttons) assert.ok(["up", "down", "left", "right", "fire", "alt"].includes(b.action) && b.label && b.aria);
    }
  }
  assert.equal(G.get("duel").padLabel, "Serve");
  assert.equal(G.get("brick").padLabel, "Launch");
  const snake = G.get("snake"), brick = G.get("brick");
  assert.equal(snake.name, "Snake");
  assert.deepEqual(plain(snake.modes.map((m) => m.id)), ["walls-slow", "walls-normal", "walls-fast", "wrap-slow", "wrap-normal", "wrap-fast", "maze"]);
  assert.equal(snake.defaultMode, "walls-normal");
  assert.equal(snake.controls, "dpad");
  assert.equal(brick.name, "Brick Breaker");
  assert.deepEqual(plain(brick.modes.map((m) => m.id)), ["powerups", "classic"]);
  assert.equal(brick.defaultMode, "powerups");
  assert.equal(brick.controls, "paddle");
  for (const g of [snake, brick]) {
    assert.ok(g.help.length > 10);
    for (const m of g.modes) assert.ok(m.label);
  }
  assert.equal(G.get("nope"), null);
});

test("registry: register validates and replaces by id", () => {
  const { win } = makeSandbox();
  const G = win.ArcadeGames;
  assert.throws(() => G.register({ id: "x" }));
  assert.throws(() => G.register({ id: "Bad Id", name: "x", modes: [{ id: "a", label: "A" }], create() {} }));
  G.register({ id: "demo", name: "Demo", modes: [{ id: "a", label: "A" }], defaultMode: "zzz", controls: "joystick", create() {} });
  const d = G.get("demo");
  assert.equal(d.defaultMode, "a");
  assert.equal(d.controls, "dpad");
  G.register({ id: "demo", name: "Demo 2", modes: [{ id: "a", label: "A" }], create() {} });
  assert.equal(G.list().filter((g) => g.id === "demo").length, 1);
  assert.equal(G.get("demo").name, "Demo 2");
  assert.throws(() => G.register({ id: "b", name: "B", modes: [{ id: "a", label: "A" }], controls: "buttons", create() {} }));
  assert.throws(() => G.register({ id: "b", name: "B", modes: [{ id: "a", label: "A" }], controls: "buttons",
    buttons: [{ action: "jump", label: "J" }], create() {} }));
});

const RENDER_MODE = { snake: "wrap-fast", brick: "powerups", blocks: "rising", duel: "hard", racer: "four", flap: "moving" };
for (const gameId of ["snake", "brick", "blocks", "duel", "racer", "flap"]) {
  for (const look of LOOKS) {
    test(`${gameId} renders and plays in the ${look} look`, () => {
      const sb = makeSandbox();
      const canvas = sb.canvas(390, 487);
      const ends = [], scores = [];
      const inst = sb.win.ArcadeGames.get(gameId).create(canvas, {
        mode: RENDER_MODE[gameId], look, seed: 5,
        onScore: (s, l) => scores.push([s, l]), onEnd: (r) => ends.push(r),
      });
      const drawn = (sb.counts.fillRect || 0) + (sb.counts.drawImage || 0);
      assert.ok(drawn > 0, "draws a first frame before start");
      inst.start();
      assert.deepEqual(scores[0], [0, 1]);
      for (let i = 0; i < 240; i++) {
        if (gameId === "snake" && i % 20 === 0) inst.input(["up", "left", "down", "right"][(i / 20) % 4], true);
        if (gameId === "brick") { inst.input("fire", true); inst.pointer("move", 100 + (i % 50), 400); }
        if (gameId === "duel") { inst.input("fire", true); inst.pointer("move", 100 + (i % 80), 400); }
        if (gameId === "blocks") {
          const k = ["left", "up", "right", "down", "fire"][i % 5];
          if (i % 12 === 0) inst.input(k, true);
          if (i % 12 === 6) inst.input(k, false);
          if (i % 40 === 0) { inst.pointer("down", 150, 200); inst.pointer("move", 190, 210); inst.pointer("up", 190, 210); }
        }
        if (gameId === "racer" && i % 30 === 0) { inst.input(i % 60 ? "left" : "right", true); inst.input(i % 60 ? "left" : "right", false); inst.pointer("down", 300, 200); }
        if (gameId === "flap" && i % 18 === 0) { if (i % 36) inst.input("fire", true); else inst.pointer("down", 100, 100); }
        sb.frames(1);
      }
      // switch looks, sizes and reduce motion mid-game
      for (const other of LOOKS) { inst.setLook(other); sb.frames(2); }
      inst.setReduceMotion(true); sb.frames(3);
      canvas.clientWidth = 1000; canvas.clientHeight = 500; inst.resize(); sb.frames(3);
      assert.equal(sb.counts.nonFinite || 0, 0, "no NaN or Infinity reaches the canvas");
      assert.ok(canvas.width > 0 && canvas.height > 0);
      inst.destroy();
      assert.equal(sb.pending(), 0, "no animation frame left after destroy");
    });
  }
}

test("modern follows the app's dark theme", () => {
  const sb = makeSandbox({ colorScheme: "dark" });
  const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), { look: "modern", seed: 1 });
  assert.equal(inst.renderer.dark, true);
  const sb2 = makeSandbox({ colorScheme: "light" });
  const inst2 = sb2.win.ArcadeGames.get("snake").create(sb2.canvas(), { look: "modern", seed: 1 });
  assert.equal(inst2.renderer.dark, false);
});

test("pointer coordinates map to the 240 × 300 logical area (letterboxed)", () => {
  const sb = makeSandbox({ dpr: 2 });
  const inst = sb.win.ArcadeGames.get("brick").create(sb.canvas(480, 800), { look: "modern", seed: 1 });
  const r = inst.renderer;
  const tl = r.toLogical(0, 100), br = r.toLogical(480, 700);
  assert.ok(Math.abs(tl.x) < 0.5 && Math.abs(tl.y) < 0.5);
  assert.ok(Math.abs(br.x - 240) < 0.5 && Math.abs(br.y - 300) < 0.5);
});

test("snake: a game in Walls mode ends with a result in active seconds", () => {
  const sb = makeSandbox();
  const ends = [], events = [];
  const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), {
    mode: "walls-normal", seed: 3, onEnd: (r) => ends.push(r), onEvent: (t) => events.push(t),
  });
  inst.start();
  inst.pause();
  assert.equal(inst.paused, true);
  sb.frames(600); // paused: no time passes in the game
  assert.equal(inst.seconds, 0);
  inst.resume();
  sb.frames(600);
  assert.equal(ends.length, 1);
  const r = ends[0];
  assert.equal(r.level, 1);
  assert.equal(typeof r.score, "number");
  assert.ok(r.seconds >= 1 && r.seconds <= 3, `seconds ${r.seconds}`);
  assert.equal(r.stats.cause, "wall");
  assert.equal(inst.state, "over");
  assert.equal(sb.pending(), 0, "the loop stops when the game ends");
  assert.ok(events.includes("start") && events.includes("die") && events.includes("pause"));
  // start() again plays a new game
  inst.start();
  assert.equal(inst.state, "running");
  assert.equal(inst.score, 0);
  inst.destroy();
});

test("pause: from input('pause'), when the page is hidden, and never running in the background", () => {
  const sb = makeSandbox();
  const pauses = [];
  const inst = sb.win.ArcadeGames.get("brick").create(sb.canvas(), {
    seed: 1, onEvent: (t, d) => { if (t === "pause") pauses.push(d.paused); },
  });
  inst.start(); sb.frames(5);
  inst.input("pause", true);
  assert.equal(inst.paused, true);
  assert.equal(sb.pending(), 0);
  inst.input("pause", true);
  assert.equal(inst.paused, false);
  sb.doc.hidden = true; sb.fire("doc", "visibilitychange");
  assert.equal(inst.paused, true);
  assert.equal(sb.pending(), 0);
  sb.doc.hidden = false;
  inst.resume(); sb.frames(2);
  sb.fire("win", "pagehide");
  assert.equal(inst.paused, true);
  assert.deepEqual(pauses, [true, false, true, false, true]);
  // input while paused is ignored
  const x = inst.logic.paddle.x;
  inst.input("left", true); inst.resume(); inst.pause();
  assert.equal(inst.logic.paddle.x, x);
  inst.destroy();
});

test("the loop runs 60 updates a second whatever the refresh rate", () => {
  for (const hz of [30, 60, 120, 144]) {
    const sb = makeSandbox();
    const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), { mode: "wrap-slow", seed: 1 });
    inst.start();
    sb.frames(hz * 2, 1000 / hz);
    const u = inst.logic.updates;
    assert.ok(Math.abs(u - 120) <= 2, `${hz} Hz → ${u} updates in 2 s`);
    inst.destroy();
  }
});

test("a long stall doesn't fast-forward the game", () => {
  const sb = makeSandbox();
  const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), { mode: "wrap-slow", seed: 1 });
  inst.start(); sb.frames(1);
  sb.frames(1, 5000);
  assert.ok(inst.logic.updates <= 3);
  inst.destroy();
});

test("brick: a full game ends with score, level, seconds and stats", () => {
  const sb = makeSandbox();
  const ends = [];
  const inst = sb.win.ArcadeGames.get("brick").create(sb.canvas(), { mode: "classic", seed: 9, onEnd: (r) => ends.push(r) });
  inst.start();
  // Don't move: the ball is launched and lost three times.
  for (let i = 0; i < 60 * 120 && !ends.length; i++) { inst.input("fire", true); sb.frames(1); }
  assert.equal(ends.length, 1);
  const r = ends[0];
  assert.ok(r.seconds > 0);
  assert.equal(r.stats.livesLeft, 0);
  assert.equal(r.stats.mode, "classic");
  assert.ok(r.score >= 0 && r.level >= 1);
});

test("sound is off by default; on, it plays through Web Audio", () => {
  const sb = makeSandbox();
  const S = sb.win.ArcadeSound;
  assert.equal(S.isEnabled(), false);
  assert.equal(S.play("eat"), false);
  assert.equal(sb.audio.created, 0, "no audio created while off");
  const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), { seed: 2, sound: false });
  inst.start(); sb.frames(120);
  assert.equal(sb.audio.oscillators, 0);
  inst.setSound(true);
  assert.equal(S.isEnabled(), true);
  assert.equal(S.play("eat", "lcd"), true);
  assert.ok(sb.audio.oscillators > 0);
  for (const name of ["eat", "bounce", "break", "row", "gameover"]) assert.ok(S.SOUNDS[name], name);
  for (const look of LOOKS) assert.ok(S.SETS[look], look);
  inst.setSound(false);
  assert.equal(S.isEnabled(), false);
  assert.equal(S.play("eat"), false);
  inst.destroy();
});

test("a broken callback doesn't stop the game", () => {
  const sb = makeSandbox();
  const quiet = sb.win.console;
  sb.win.console = { error() {} };
  const inst = sb.win.ArcadeGames.get("snake").create(sb.canvas(), {
    mode: "wrap-normal", seed: 2, onScore() { throw new Error("boom"); }, onEvent() { throw new Error("boom"); },
  });
  inst.start(); sb.frames(60);
  assert.equal(inst.state, "running");
  assert.ok(inst.logic.updates >= 59);
  sb.win.console = quiet;
  inst.destroy();
});
