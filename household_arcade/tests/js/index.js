// Entry point for `node --test tests/js` (Node treats the folder as a module and loads
// this file): runs every *.test.js here in one process. `node --test tests/js/*.test.js`
// works too.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
for (const f of fs.readdirSync(__dirname).sort()) {
  if (f.endsWith(".test.js")) require(path.join(__dirname, f));
}
