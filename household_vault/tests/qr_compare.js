/* Prints QR.encode(text).modules as 0/1 rows for each line of stdin (JSON array of strings) — test_qrcode.py compares. */
"use strict";
const path = require("path");
const QR = require(path.join(__dirname, "..", "app", "static", "qr.js"));
const texts = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(texts.map((t) => QR.encode(t, "M").modules.map((r) => r.map((b) => (b ? 1 : 0)).join("")))));
