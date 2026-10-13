// Builds app/static/games/parking-levels.js: Car Park's 200 numbered boards (mode Levels), worked out once here
// instead of on the phone each time a level starts (up to a second or more on a slow phone). Each board is exactly
// what ParkingLogic.makeBoard makes from the seed "parking-level-<n>", so nothing about a level changes.
//
//     node tests/js/parking-levels-tool.js            write the file (after changing how boards are made)
//     node tests/js/parking-levels-tool.js --check    exit 1 when it is out of date
//
// A board is [fewest moves, the random state after making it, "HLRP HLRP …"]: one group per vehicle in order —
// H 1 across / 0 along, L its length, R its lane (row or column), P its first square along the lane.
"use strict";
const fs = require("node:fs");
const path = require("node:path");

const GAMES = path.join(__dirname, "..", "..", "app", "static", "games");
const OUT = path.join(GAMES, "parking-levels.js");

function generate(levels) {
  // the boards as makeBoard makes them, without the precomputed file (so it can be made again from nothing)
  const Logic = freshLogic();
  return levels.map((n) => {
    const s = { rng: Logic.hashSeed("parking-level-" + n) };
    const b = Logic.makeBoard(s, Logic.levelSpec(n));
    return [b.fewest, s.rng >>> 0, b.vs.map((v, i) => `${v.h ? 1 : 0}${v.len}${v.lane}${b.pos[i]}`).join(" ")];
  });
}

function freshLogic() {
  const file = path.join(GAMES, "parking-logic.js");
  delete require.cache[require.resolve(file)];
  const pre = OUT;                     // (the resolved name require() caches the file under)
  const saved = require.cache[pre];
  require.cache[pre] = { id: pre, filename: pre, loaded: true, exports: null };     // as if the file weren't there
  try { return require(file); }
  finally { if (saved) require.cache[pre] = saved; else delete require.cache[pre]; delete require.cache[require.resolve(file)]; }
}

function render(boards) {
  const rows = boards.map((b, i) => `    ${JSON.stringify(b)},${(i + 1) % 10 === 0 ? " // " + (i + 1) : ""}`);
  return `/* Household Arcade — Car Park's 200 numbered boards (mode Levels), made by tests/js/parking-levels-tool.js with
   ParkingLogic.makeBoard from the seed "parking-level-<n>" (a test checks them). Don't edit by hand.
   A board: [fewest moves, the random state after making it, "HLRP …"] — per vehicle: H 1 across / 0 along, L its
   length, R its lane, P its first square. */
(function (root) {
  "use strict";
  var BOARDS = [
${rows.join("\n")}
  ];
  /** Level n's board ({ vs, pos, fewest, rng }), or null. */
  function board(n) {
    var b = BOARDS[n - 1];
    if (!b) return null;
    var vs = [], pos = [];
    b[2].split(" ").forEach(function (g) { vs.push({ h: g[0] === "1", len: +g[1], lane: +g[2] }); pos.push(+g[3]); });
    return { vs: vs, pos: pos, fewest: b[0], rng: b[1] | 0 };
  }
  var ParkingLevels = { COUNT: BOARDS.length, board: board };
  if (typeof module === "object" && module.exports) module.exports = ParkingLevels; else root.ParkingLevels = ParkingLevels;
})(typeof self !== "undefined" ? self : this);
`;
}

module.exports = { generate, render, OUT };

if (require.main === module) {
  const all = Array.from({ length: 200 }, (_, i) => i + 1);
  const want = render(generate(all));
  if (process.argv.includes("--check")) {
    if (!fs.existsSync(OUT) || fs.readFileSync(OUT, "utf8") !== want) { console.error("parking-levels.js is out of date"); process.exit(1); }
  } else {
    fs.writeFileSync(OUT, want);
    console.log("wrote " + path.relative(process.cwd(), OUT));
  }
}
