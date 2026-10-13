// Builds app/static/games/manifest.js: for each game, the files it needs in the order they load, and its help line
// (shown on the Games page before the game's own files are loaded). The page loads only kit.js, sound.js,
// registry.js and manifest.js up front; ArcadeGames.load(id) loads a game's files when it is opened.
//
//     node tests/js/manifest-tool.js            write the manifest
//     node tests/js/manifest-tool.js --check    exit 1 when it is out of date (manifest.test.js does the same)
//
// A game's files are its own (the file that registers it) and every file whose globals they use, found by name
// (root.SnakeLogic, BoardKit …), in ORDER below.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { makeSandbox, GAMES_DIR } = require("./helpers");

// Every game file in a working load order (each file after the ones it uses): the order index.html listed them in
// when it loaded them all. A new game's files go here too (and the manifest is made again).
const ORDER = [
  "kit.js", "sound.js", "registry.js", "snake-logic.js", "snake.js", "brick-logic.js", "brick.js", "blocks-logic.js",
  "blocks.js", "duel-logic.js", "duel.js", "racer-logic.js", "racer.js", "flap-logic.js", "flap.js",
  "mines-logic.js", "mines.js", "merge-logic.js", "merge.js", "colours-logic.js", "colours.js", "cards-logic.js",
  "cards.js", "mole-logic.js", "mole.js", "numbers-logic.js", "numbers.js", "tanks-logic.js", "tanks.js",
  "invaders-logic.js", "invaders.js", "rocks-logic.js", "rocks.js", "hop-logic.js", "hop.js", "snakeduel-logic.js",
  "snakeduel.js", "sudoku-logic.js", "sudoku.js", "wordguess-words.js", "wordguess-logic.js", "wordguess.js",
  "wordsearch-words.js", "wordsearch-logic.js", "wordsearch.js", "bubbles-logic.js", "bubbles.js", "gems-logic.js",
  "gems.js", "stack-logic.js", "stack.js", "runner-logic.js", "runner.js", "lander-logic.js", "lander.js",
  "defense-logic.js", "defense.js", "slide-logic.js", "slide.js", "lights-logic.js", "lights.js",
  "nonogram-logic.js", "nonogram.js", "tiles-logic.js", "tiles.js", "codebreak-logic.js", "codebreak.js",
  "typerain-words.js", "typerain-logic.js", "typerain.js", "boardkit.js", "fourrow-logic.js", "fourrow.js",
  "tictactoe-logic.js", "tictactoe.js", "checkers-logic.js", "checkers.js", "reversi-logic.js", "reversi.js",
  "dots-logic.js", "dots.js", "seabattle-logic.js", "seabattle.js", "dicekit.js", "ludo-logic.js", "ludo.js",
  "snakes-logic.js", "snakes.js", "carrom-logic.js", "carrom.js", "chess-logic.js", "chess.js", "arrows-logic.js",
  "arrows.js", "parking-levels.js", "parking-logic.js", "parking.js", "watersort-logic.js", "watersort.js", "bolts-logic.js", "bolts.js",
  "connect-logic.js", "connect.js", "untangle-logic.js", "untangle.js"];

const BASE = ["kit.js", "sound.js", "registry.js"];        // always on the page
const OUT = path.join(GAMES_DIR, "manifest.js");

function build() {
  // Load the base files, then each other file on its own, noting the globals it adds and the games it registers.
  const sb = makeSandbox({ only: BASE });
  const win = sb.win;
  const context = vm.createContext(win);
  const defines = {};            // file -> [global names]
  const registers = {};          // game id -> file
  const realRegister = win.ArcadeGames.register;
  let current = null;
  win.ArcadeGames.register = function (def) { registers[def.id] = current; return realRegister(def); };
  for (const f of ORDER) {
    if (BASE.includes(f)) continue;
    const before = new Set(Object.keys(win));
    current = f;
    vm.runInContext(fs.readFileSync(path.join(GAMES_DIR, f), "utf8"), context, { filename: f });
    defines[f] = Object.keys(win).filter((k) => !before.has(k));
  }
  const owner = {};              // global -> file
  for (const [f, names] of Object.entries(defines)) for (const n of names) owner[n] = f;
  const text = (f) => fs.readFileSync(path.join(GAMES_DIR, f), "utf8");
  function needs(f) {
    const src = text(f);
    return Object.keys(owner).filter((n) => owner[n] !== f && new RegExp("\\b" + n + "\\b").test(src)).map((n) => owner[n]);
  }
  const games = {};
  for (const def of win.ArcadeGames.list()) {
    const seen = new Set();
    const walk = (f) => { if (seen.has(f)) return; seen.add(f); for (const d of needs(f)) walk(d); };
    walk(registers[def.id]);
    const files = [...seen].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
    games[def.id] = { files, help: def.help };
  }
  return games;
}

function render(games) {
  const lines = Object.entries(games).map(([id, g]) =>
    `    ${JSON.stringify(id)}: { files: ${JSON.stringify(g.files)}, help: ${JSON.stringify(g.help)} },`);
  return `/* Household Arcade — the files each game needs and its help line. Made by tests/js/manifest-tool.js from the game
   files (node tests/js/manifest-tool.js); a test checks it is up to date. Don't edit it by hand.
   The page loads kit.js, sound.js, registry.js and this file; ArcadeGames.load(id) loads a game's files when it is
   opened. */
(function (root) {
  "use strict";
  root.ArcadeManifest = { games: {
${lines.join("\n")}
  } };
})(typeof window !== "undefined" ? window : this);
`;
}

module.exports = { build, render, OUT, ORDER };

if (require.main === module) {
  const want = render(build());
  if (process.argv.includes("--check")) {
    const have = fs.existsSync(OUT) ? fs.readFileSync(OUT, "utf8") : "";
    if (have !== want) { console.error("games/manifest.js is out of date: run node tests/js/manifest-tool.js"); process.exit(1); }
  } else {
    fs.writeFileSync(OUT, want);
    console.log("wrote " + path.relative(process.cwd(), OUT));
  }
}
