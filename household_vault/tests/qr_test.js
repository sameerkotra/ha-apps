/* Checks for app/static/qr.js — run by test_qr.py (needs Node). Exits non-zero on failure. */
"use strict";
const path = require("path");
const QR = require(path.join(__dirname, "..", "app", "static", "qr.js"));
let failed = 0;
const check = (ok, msg) => { if (!ok) { failed++; console.log("FAIL:", msg); } };

// Reed–Solomon: random blocks with up to ⌊ecc/2⌋ errors are corrected
const { rsDivisor, rsRemainder, rsCorrect } = QR._rs;
let seed = 12345;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
for (let t = 0; t < 1500; t++) {
  const ecc = [7, 10, 15, 18, 22, 26, 28, 30][t % 8];
  const data = Array.from({ length: 1 + rnd(120) }, () => rnd(256));
  const blk = data.concat(rsRemainder(data, rsDivisor(ecc)));
  const bad = blk.slice(); const used = new Set(); const n = rnd(Math.floor(ecc / 2) + 1);
  while (used.size < n) { const p = rnd(bad.length); if (!used.has(p)) { used.add(p); bad[p] ^= 1 + rnd(255); } }
  check(rsCorrect(bad, ecc) && bad.join() === blk.join(), `rs ecc=${ecc} errors=${n}`);
}

// Round trips at every version and level, through the grid decoder (with a few flipped modules)
const texts = (v) => "otpauth://totp/Ex:ä€?secret=JBSWY3DPEHPK3PXP&n=".padEnd(v * 6, "x");
for (const e of ["L", "M", "Q", "H"]) for (let v = 1; v <= 40; v += (v < 10 ? 1 : 3)) {
  let q;
  try { q = QR.encode(texts(v), e, v); } catch (x) { continue; }
  const m = q.modules.map((r) => r.slice());
  for (let k = 0; k < 3; k++) { const x = 9 + rnd(q.size - 18), y = 9 + rnd(q.size - 18); m[y][x] = !m[y][x]; }
  check(QR.decodeGrid((x, y) => m[y][x], q.size) === texts(v), `grid ${e} v${q.version}`);
}

// Images: scaled, quiet zone, rotated 90°, inverted, on a noisy page
function image(q, px, opts = {}) {
  const pad = 4, n = (q.size + pad * 2) * px, W = n + (opts.margin || 0) * 2, H = W;
  const data = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let v = opts.noise ? 200 + rnd(56) : 255;
    const mx = Math.floor((x - (opts.margin || 0)) / px) - pad, my = Math.floor((y - (opts.margin || 0)) / px) - pad;
    if (x >= (opts.margin || 0) && y >= (opts.margin || 0) && x < (opts.margin || 0) + n && y < (opts.margin || 0) + n) {
      v = 255;
      let a = mx, b = my;
      if (opts.rot90) [a, b] = [my, q.size - 1 - mx];
      if (a >= 0 && b >= 0 && a < q.size && b < q.size && q.modules[b][a]) v = 20 + rnd(30);
    }
    if (opts.invert) v = 255 - v;
    const i = (y * W + x) * 4; data[i] = data[i + 1] = data[i + 2] = v; data[i + 3] = 255;
  }
  return { data, width: W, height: H };
}
const wifi = "WIFI:T:WPA;S:Sharma 5G;P:correct\;horse;;";
for (const [text, e] of [[wifi, "M"], ["otpauth://totp/GitHub:kiran?secret=JBSWY3DPEHPK3PXP&issuer=GitHub", "L"], ["x".repeat(300), "Q"]]) {
  const q = QR.encode(text, e);
  for (const [name, img] of [["plain3", image(q, 3)], ["plain6", image(q, 6)], ["rot90", image(q, 4, { rot90: true })],
    ["inverted", image(q, 4, { invert: true })], ["page", image(q, 4, { margin: 120, noise: true })]]) {
    check(QR.decode(img, { budgetMs: 5000 }) === text, `image ${name} v${q.version}`);
  }
}
// No code: returns null, quickly
const blank = { data: new Uint8ClampedArray(400 * 300 * 4).fill(255), width: 400, height: 300 };
check(QR.decode(blank) === null, "blank image");

console.log(failed ? `${failed} failed` : "qr ok");
process.exit(failed ? 1 : 0);
