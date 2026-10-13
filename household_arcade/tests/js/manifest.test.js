"use strict";
// Loading a game's files when it is opened (games/manifest.js, ArcadeGames.load): the manifest is up to date with
// the game files, every game file belongs to a game, each game loads and draws from its own files alone (with only
// the kit, sound and registry on the page), and the registry's loader adds each file once, in order, with the
// page's ?v=.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { makeSandbox, GAMES_DIR } = require("./helpers");
const tool = require("./manifest-tool");

const BASE = ["kit.js", "sound.js", "registry.js"];
const LOOKS = ["modern", "lcd", "neon", "pixel", "paper", "contrast"];

function manifestIn(win) {
  vm.runInContext(fs.readFileSync(tool.OUT, "utf8"), vm.createContext(win), { filename: "manifest.js" });
  return win.ArcadeManifest.games;
}

test("manifest.js is up to date (node tests/js/manifest-tool.js makes it)", () => {
  assert.equal(fs.readFileSync(tool.OUT, "utf8"), tool.render(tool.build()));
});

test("every game file belongs to a game, and the page lists only the base files", () => {
  const { win } = makeSandbox({ only: BASE });
  const games = manifestIn(win);
  const used = new Set(BASE.concat(["manifest.js", "chess-worker.js", "parking-worker.js", "board-worker.js"]));          // (workers: loaded by their game)
  for (const g of Object.values(games)) for (const f of g.files) used.add(f);
  const all = fs.readdirSync(GAMES_DIR).filter((f) => f.endsWith(".js"));
  assert.deepEqual(all.filter((f) => !used.has(f)), []);
  for (const f of tool.ORDER) assert.ok(all.includes(f), f);
  const index = fs.readFileSync(path.join(GAMES_DIR, "..", "index.html"), "utf8");
  const listed = [...index.matchAll(/src="games\/([a-z-]+\.js)\?v=/g)].map((m) => m[1]);
  assert.deepEqual(listed, BASE.concat(["manifest.js"]));
});

test("each game loads and draws from its own files alone", () => {
  const { win: w0 } = makeSandbox({ only: BASE });
  const games = manifestIn(w0);
  assert.ok(Object.keys(games).length >= 45);
  for (const [id, g] of Object.entries(games)) {
    const sb = makeSandbox({ only: BASE.concat(g.files) });
    const def = sb.win.ArcadeGames.get(id);
    assert.ok(def, id + " registers from " + g.files.join(", "));
    assert.equal(def.help, g.help, id + ": the help line on the Games page is the game's own");
    for (const look of LOOKS) {
      const inst = def.create(sb.canvas(), { mode: def.defaultMode, look, seed: 7, preview: true });
      inst.resize();
      sb.frames(2);
      inst.destroy();
    }
    const inst = def.create(sb.canvas(), { mode: def.defaultMode, seed: 7, onEnd() {}, onScore() {}, onEvent() {} });
    inst.start();
    sb.frames(20);
    inst.destroy();
    assert.equal(sb.counts.nonFinite || 0, 0, id + " drew with a number that isn't finite");
  }
});

test("ArcadeGames.load adds each file once, in order, with the page's ?v=, and says when one fails", async () => {
  const sb = makeSandbox({ only: ["kit.js", "sound.js"] });
  const win = sb.win, doc = sb.doc;
  const added = [];
  const context = vm.createContext(win);
  doc.head = {
    appendChild(el) {
      added.push(el.src);
      setTimeout(() => {
        const file = el.src.replace(/^games\//, "").replace(/\?.*$/, "");
        if (file === "missing.js") { el.onerror(); return; }
        vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, file), "utf8"), context, { filename: file });
        el.onload();
      }, 0);
    },
  };
  const make = doc.createElement;
  doc.createElement = (tag) => (tag === "script" ? { tag } : make.call(doc, tag));
  doc.currentScript = { src: "http://x/games/registry.js?v=9.9.9" };
  vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, "registry.js"), "utf8"), context, { filename: "registry.js" });
  delete doc.currentScript;
  manifestIn(win);
  const G = win.ArcadeGames;
  assert.equal(G.get("checkers"), null);
  assert.ok(G.info("checkers").help.length > 10);                 // known before it loads
  const [a, b] = await Promise.all([G.load("checkers"), G.load("checkers")]);
  assert.equal(a, b);
  assert.equal(a.id, "checkers");
  assert.deepEqual(added, ["games/boardkit.js?v=9.9.9", "games/checkers-logic.js?v=9.9.9", "games/checkers.js?v=9.9.9"]);
  await G.load("reversi");                                        // boardkit.js is on the page already
  assert.deepEqual(added.slice(3), ["games/reversi-logic.js?v=9.9.9", "games/reversi.js?v=9.9.9"]);
  assert.equal(await G.load("checkers"), a);
  win.ArcadeManifest.games.broken = { files: ["missing.js"], help: "" };
  await assert.rejects(G.load("broken"), /couldn't be loaded/);
  await assert.rejects(G.load("nope"), /isn't known/);
});
