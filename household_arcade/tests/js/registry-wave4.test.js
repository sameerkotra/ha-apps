"use strict";
// The shell features wave 4 added to the registry: start-screen options, typed keys, the pad actions for numbers.
const test = require("node:test");
const assert = require("node:assert/strict");
const { makeSandbox } = require("./helpers");

const plain = (v) => JSON.parse(JSON.stringify(v));
const base = (extra) => Object.assign({ id: "t" + Math.floor(Math.random() * 1e6), name: "T", modes: [{ id: "a", label: "A" }], defaultMode: "a", controls: "touch", help: "x", stateVersion: 1, create() {} }, extra);

test("options are normalised: ids, labels, choices and a default that is one of them", () => {
  const { win } = makeSandbox();
  const G = win.ArcadeGames;
  const def = base({ options: [{ id: "level_of", label: "L", default: "zzz", choices: [{ id: 1, label: "One" }, { id: "two", label: "Two" }] }] });
  G.register(def);
  const g = G.get(def.id);
  assert.deepEqual(plain(g.options), [{ id: "level_of", label: "L", choices: [{ id: "1", label: "One" }, { id: "two", label: "Two" }], default: "1" }]);
  const d2 = base({ options: [{ id: "x", label: "X", default: "b", choices: [{ id: "a", label: "A" }, { id: "b", label: "B" }] }] });
  G.register(d2);
  assert.equal(G.get(d2.id).options[0].default, "b");
  const d3 = base({});
  G.register(d3);
  assert.deepEqual(plain(G.get(d3.id).options), []);
  assert.equal(G.get(d3.id).typed, false);
  const d4 = base({ typed: true });
  G.register(d4);
  assert.equal(G.get(d4.id).typed, true);
  const d5 = base({ typed: "yes" });
  G.register(d5);
  assert.equal(G.get(d5.id).typed, false, "only true counts");
});

test("a bad option is refused when the game registers", () => {
  const { win } = makeSandbox();
  const G = win.ArcadeGames;
  for (const bad of [{ id: "Bad Id", label: "x", choices: [{ id: "a", label: "A" }] }, { id: "ok", label: 5, choices: [{ id: "a", label: "A" }] },
    { id: "ok", label: "x", choices: [] }, { id: "ok", label: "x" }, null]) {
    assert.throws(() => G.register(base({ options: [bad] })), undefined, JSON.stringify(bad));
  }
});

test("the pad can carry number, notes, fill, hint, undo, erase and auto buttons", () => {
  const { win } = makeSandbox();
  const G = win.ArcadeGames;
  const actions = ["n1", "n2", "n3", "n4", "n5", "n6", "n7", "n8", "n9", "notes", "fill", "hint", "undo", "erase", "auto"];
  const def = base({ controls: "buttons", buttons: actions.map((a, i) => ({ action: a, label: a, place: [1 + (i % 5), 1 + Math.floor(i / 5), 1, 1] })) });
  G.register(def);
  assert.deepEqual(plain(G.get(def.id).buttons.map((b) => b.action)), actions);
  assert.throws(() => G.register(base({ controls: "buttons", buttons: [{ action: "n10", label: "x" }] })));
  assert.throws(() => G.register(base({ controls: "buttons", buttons: [{ action: "n0", label: "x" }] })));
  const t = base({ controls: "touch", buttons: [{ action: "hint", label: "Hint" }] });
  G.register(t);
  assert.equal(G.get(t.id).buttons.length, 1);
  const none = base({ controls: "touch" });
  G.register(none);
  assert.equal(G.get(none.id).buttons.length, 0, "a touch game's buttons are optional");
});

test("the four wave 4 games register with the right shell settings", () => {
  const { win } = makeSandbox({ extra: ["sudoku-logic.js", "sudoku.js", "solitaire-logic.js", "solitaire.js", "wordguess-words.js", "wordguess-logic.js", "wordguess.js",
    "wordsearch-words.js", "wordsearch-logic.js", "wordsearch.js"] });
  const G = win.ArcadeGames;
  assert.deepEqual(plain(["sudoku", "solitaire", "wordguess", "wordsearch"].map((id) => [id, G.get(id).controls, G.get(id).typed, G.get(id).stateVersion])),
    [["sudoku", "buttons", true, 1], ["solitaire", "touch", false, 1], ["wordguess", "touch", true, 1], ["wordsearch", "touch", false, 1]]);
  assert.deepEqual(plain(G.get("sudoku").options.map((o) => o.id)), ["mistakes", "lines"]);
  for (const id of ["sudoku", "solitaire", "wordguess", "wordsearch"]) assert.ok(G.get(id).help.length > 40, id);
});
