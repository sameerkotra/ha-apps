// The Games page search (SPEC §9): the match function in static/search.js.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const S = require(path.join(__dirname, "..", "..", "app", "static", "search.js"));

const G = {
  tictactoe: { name: "Tic-tac-toe", tags: ["board", "two players", "turn by turn", "gentle"],
    modes: [{ label: "Computer · Easy (gentle)" }, { label: "Two players (one screen)" }, { label: "Two phones (turn by turn)" }] },
  seabattle: { name: "Sea Battle", tags: ["board", "two players", "turn by turn", "gentle"], modes: [{ label: "Computer · Hard" }] },
  lights: { name: "Lights Out", tags: ["puzzle", "levels", "gentle"],
    modes: [{ label: "Little 3 × 3 (gentle)" }, { label: "5 × 5" }, { label: "7 × 7" }, { label: "Climb (3 × 3 to 7 × 7)" }] },
  chess: { name: "Chess", tags: ["board", "two players", "turn by turn", "gentle"], modes: [{ label: "Computer · Medium" }] },
  snake: { name: "Snake", tags: ["arcade", "levels"], modes: [{ label: "Walls · Normal" }, { label: "Maze" }] },
  carrom: { name: "Carrom", tags: ["board", "two players"], modes: [{ label: "Doubles: four, two teams (one screen)" }] },
  cafe: { name: "Café Crème", tags: ["word"], modes: [] },
};
const ok = (g, q) => S.match(G[g], q).ok;

test("a word matches the start of a word in the name, case and punctuation ignored", () => {
  assert.ok(ok("tictactoe", "tic"));
  assert.ok(ok("tictactoe", "TAC"));
  assert.ok(ok("seabattle", "sea"));
  assert.ok(ok("seabattle", "bat"));
  assert.ok(!ok("seabattle", "attle"));             // the start of a word only
  assert.ok(ok("tictactoe", "tic-tac"));
});

test("every word must match, in any order", () => {
  assert.ok(ok("seabattle", "battle sea"));
  assert.ok(!ok("chess", "puzzle chess"));
  assert.ok(!ok("lights", "puzzle chess"));
  assert.ok(ok("lights", "out puzzle"));
});

test("accents are ignored both ways", () => {
  assert.ok(ok("cafe", "cafe"));
  assert.ok(ok("cafe", "CRÈME"));
  assert.equal(S.normalise("Crème brûlée"), "creme brulee");
});

test("tags and mode labels match, and say which matched", () => {
  assert.deepEqual(S.match(G.lights, "puzzle"), { ok: true, via: "Tag: puzzle" });
  assert.deepEqual(S.match(G.lights, "7 x 7"), { ok: true, via: "Mode: 7 × 7" });
  assert.ok(ok("lights", "7×7"));
  assert.deepEqual(S.match(G.snake, "maze"), { ok: true, via: "Mode: Maze" });
  assert.deepEqual(S.match(G.snake, "snake"), { ok: true, via: null });     // the name: nothing to explain
  assert.ok(ok("tictactoe", "turn by turn"));
  assert.ok(ok("lights", "puzzles"));                // a plural
});

test("the everyday words lead to the tags", () => {
  for (const w of ["kids", "children", "easy"]) assert.ok(ok("lights", w), w);
  assert.ok(!ok("snake", "kids"));
  for (const w of ["2", "two", "multiplayer", "together"]) assert.ok(ok("carrom", w), w);
  assert.ok(!ok("snake", "multiplayer"));
  assert.deepEqual(S.match(G.chess, "strategy"), { ok: true, via: "Tag: board" });
  assert.ok(!ok("lights", "strategy"));
});

test("no match, and nothing typed shows everything", () => {
  assert.ok(!ok("snake", "xyz"));
  assert.deepEqual(S.match(G.snake, "   "), { ok: true, via: null });
  assert.deepEqual(S.match(G.snake, "--"), { ok: true, via: null });
  const all = Object.values(G);
  assert.equal(S.filter(all, "").length, all.length);
  assert.deepEqual(S.filter(all, "xyz"), []);
  assert.deepEqual(S.filter(all, "board").map((r) => r.game.name), ["Tic-tac-toe", "Sea Battle", "Chess", "Carrom"]);   // the usual order
});
