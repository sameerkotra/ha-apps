/* QR codes for Household Vault — written for this app (no third-party code).
   QR.encode(text, ecl) → { size, modules: [[bool]] }  (byte mode, UTF-8; ecl "L"|"M"|"Q"|"H")
   QR.decode(imageData, {budgetMs}) → string | null  (imageData: {data: RGBA bytes, width, height})
   QR.decodeGrid(bits, size) → string | null           (a sampled module grid)
   Follows ISO/IEC 18004. The tables are the standard ones (versions 1–40). */
"use strict";
var QR = (function () {
  // ---------- tables ----------
  // index: [L, M, Q, H][version]
  const ECC_PER_BLOCK = [
    [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  ];
  const NUM_BLOCKS = [
    [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
  ];
  const ECL_INDEX = { L: 0, M: 1, Q: 2, H: 3 };
  const ECL_FORMAT = [1, 0, 3, 2];              // format-info bits for L, M, Q, H
  const ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";

  // ---------- GF(256), polynomial 0x11D ----------
  const EXP = new Uint8Array(512), LOG = new Uint8Array(256);
  (function () { let x = 1; for (let i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11D; } for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]; })();
  const mul = (a, b) => (a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]]);
  const div = (a, b) => { if (b === 0) throw new Error("div0"); return a === 0 ? 0 : EXP[(LOG[a] + 255 - LOG[b]) % 255]; };

  function rsDivisor(degree) {
    const r = new Array(degree).fill(0); r[degree - 1] = 1;
    let root = 1;
    for (let i = 0; i < degree; i++) {
      for (let j = 0; j < degree; j++) { r[j] = mul(r[j], root); if (j + 1 < degree) r[j] ^= r[j + 1]; }
      root = mul(root, 2);
    }
    return r;
  }
  function rsRemainder(data, divisor) {
    const r = new Array(divisor.length).fill(0);
    for (const b of data) {
      const f = b ^ r.shift(); r.push(0);
      for (let i = 0; i < divisor.length; i++) r[i] ^= mul(divisor[i], f);
    }
    return r;
  }
  // Correct a block in place (codewords, most significant first; nsym = number of ECC bytes).
  // Returns false when there are too many errors.
  function rsCorrect(block, nsym) {
    const n = block.length;
    const synd = new Array(nsym);
    let bad = false;
    for (let i = 0; i < nsym; i++) {
      let s = 0; const a = EXP[i];
      for (let j = 0; j < n; j++) s = mul(s, a) ^ block[j];
      synd[i] = s; if (s) bad = true;
    }
    if (!bad) return true;
    // Berlekamp–Massey (polynomials lowest degree first)
    let C = [1], B = [1], L = 0, m = 1, b = 1;
    for (let k = 0; k < nsym; k++) {
      let d = synd[k];
      for (let i = 1; i <= L; i++) d ^= mul(C[i] || 0, synd[k - i]);
      if (d === 0) { m++; continue; }
      const coef = div(d, b);
      const T = C.slice();
      const need = B.length + m;
      while (C.length < need) C.push(0);
      for (let i = 0; i < B.length; i++) C[i + m] ^= mul(coef, B[i]);
      if (2 * L <= k) { L = k + 1 - L; B = T; b = d; m = 1; } else m++;
    }
    while (C.length > 1 && C[C.length - 1] === 0) C.pop();
    if (C.length - 1 !== L || 2 * L > nsym) return false;
    // Chien search: position p (0 = last codeword) is an error when C(α^-p) = 0
    const pos = [];
    for (let p = 0; p < n; p++) {
      const xinv = EXP[(255 - p) % 255];
      let v = 0;
      for (let i = C.length - 1; i >= 0; i--) v = mul(v, xinv) ^ C[i];
      if (v === 0) pos.push(p);
    }
    if (pos.length !== L) return false;
    // Ω(x) = S(x)·C(x) mod x^nsym
    const omega = new Array(nsym).fill(0);
    for (let i = 0; i < nsym; i++) for (let j = 0; j < C.length && i + j < nsym; j++) omega[i + j] ^= mul(synd[i], C[j]);
    for (const p of pos) {
      const X = EXP[p % 255], Xinv = EXP[(255 - p) % 255];
      let om = 0;
      for (let i = omega.length - 1; i >= 0; i--) om = mul(om, Xinv) ^ omega[i];
      let dv = 0;                                 // formal derivative: odd terms
      for (let i = 1; i < C.length; i += 2) { let t = C[i]; for (let k = 0; k < i - 1; k++) t = mul(t, Xinv); dv ^= t; }
      if (dv === 0) return false;
      const mag = mul(X, div(om, dv));
      block[n - 1 - p] ^= mag;
    }
    for (let i = 0; i < nsym; i++) {               // verify
      let s = 0; const a = EXP[i];
      for (let j = 0; j < n; j++) s = mul(s, a) ^ block[j];
      if (s) return false;
    }
    return true;
  }

  // ---------- layout ----------
  const sizeOf = (ver) => ver * 4 + 17;
  function alignmentPositions(ver) {
    if (ver === 1) return [];
    const num = Math.floor(ver / 7) + 2;
    const step = ver === 32 ? 26 : Math.ceil((ver * 4 + 4) / (num * 2 - 2)) * 2;
    const r = [6];
    for (let pos = sizeOf(ver) - 7; r.length < num; pos -= step) r.splice(1, 0, pos);
    return r;
  }
  function rawDataModules(ver) {
    let r = (16 * ver + 128) * ver + 64;
    if (ver >= 2) { const n = Math.floor(ver / 7) + 2; r -= (25 * n - 10) * n - 55; if (ver >= 7) r -= 36; }
    return r;
  }
  function dataCodewords(ver, e) { return Math.floor(rawDataModules(ver) / 8) - ECC_PER_BLOCK[e][ver] * NUM_BLOCKS[e][ver]; }
  function formatBits(e, mask) {
    const data = (ECL_FORMAT[e] << 3) | mask;
    let rem = data;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    return ((data << 10) | rem) ^ 0x5412;
  }
  function versionBits(ver) {
    let rem = ver;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
    return (ver << 12) | rem;
  }
  const bit = (x, i) => ((x >>> i) & 1) !== 0;
  const MASKS = [
    (x, y) => (x + y) % 2 === 0, (x, y) => y % 2 === 0, (x, y) => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => (x * y) % 2 + (x * y) % 3 === 0,
    (x, y) => ((x * y) % 2 + (x * y) % 3) % 2 === 0, (x, y) => ((x + y) % 2 + (x * y) % 3) % 2 === 0,
  ];

  // Function patterns (finders, timing, alignment, format/version areas); modules[y][x]
  function functionGrid(ver) {
    const size = sizeOf(ver);
    const mods = Array.from({ length: size }, () => new Array(size).fill(false));
    const fn = Array.from({ length: size }, () => new Array(size).fill(false));
    const set = (x, y, v) => { mods[y][x] = v; fn[y][x] = true; };
    for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
    const finder = (cx, cy) => {
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
        const d = Math.max(Math.abs(dx), Math.abs(dy)), x = cx + dx, y = cy + dy;
        if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, d !== 2 && d !== 4);
      }
    };
    finder(3, 3); finder(size - 4, 3); finder(3, size - 4);
    const al = alignmentPositions(ver);
    for (let i = 0; i < al.length; i++) for (let j = 0; j < al.length; j++) {
      if ((i === 0 && j === 0) || (i === 0 && j === al.length - 1) || (i === al.length - 1 && j === 0)) continue;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(al[i] + dx, al[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
    // reserve format areas (values drawn later) and the dark module
    for (let i = 0; i < 9; i++) { fn[8][i] = fn[i][8] = true; }
    for (let i = 0; i < 8; i++) { fn[8][size - 1 - i] = true; fn[size - 1 - i][8] = true; }
    set(8, size - 8, true);
    if (ver >= 7) {
      const vb = versionBits(ver);
      for (let i = 0; i < 18; i++) {
        const a = size - 11 + (i % 3), b = Math.floor(i / 3), v = bit(vb, i);
        set(a, b, v); set(b, a, v);
      }
    }
    return { size, mods, fn };
  }
  function drawFormat(mods, size, bits) {
    for (let i = 0; i <= 5; i++) mods[i][8] = bit(bits, i);
    mods[7][8] = bit(bits, 6); mods[8][8] = bit(bits, 7); mods[8][7] = bit(bits, 8);
    for (let i = 9; i < 15; i++) mods[8][14 - i] = bit(bits, i);
    for (let i = 0; i < 8; i++) mods[8][size - 1 - i] = bit(bits, i);
    for (let i = 8; i < 15; i++) mods[size - 15 + i][8] = bit(bits, i);
    mods[size - 8][8] = true;
  }
  function readFormat(get, size) {
    let a = 0, b = 0;
    for (let i = 0; i <= 5; i++) if (get(8, i)) a |= 1 << i;
    if (get(8, 7)) a |= 1 << 6; if (get(8, 8)) a |= 1 << 7; if (get(7, 8)) a |= 1 << 8;
    for (let i = 9; i < 15; i++) if (get(14 - i, 8)) a |= 1 << i;
    for (let i = 0; i < 8; i++) if (get(size - 1 - i, 8)) b |= 1 << i;
    for (let i = 8; i < 15; i++) if (get(8, size - 15 + i)) b |= 1 << i;
    return [a, b];
  }
  // zigzag order of data modules
  function dataPositions(fn, size) {
    const out = [];
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) for (let j = 0; j < 2; j++) {
        const x = right - j, upward = ((right + 1) & 2) === 0, y = upward ? size - 1 - vert : vert;
        if (!fn[y][x]) out.push([x, y]);
      }
    }
    return out;
  }

  // ---------- encoding ----------
  function utf8(text) { return Array.from(new TextEncoder().encode(text)); }
  function penalty(m, size) {
    let p = 0;
    for (let pass = 0; pass < 2; pass++) for (let a = 0; a < size; a++) {
      let run = 1;
      for (let b = 1; b < size; b++) {
        const cur = pass ? m[b][a] : m[a][b], prev = pass ? m[b - 1][a] : m[a][b - 1];
        if (cur === prev) { run++; if (run === 5) p += 3; else if (run > 5) p++; } else run = 1;
      }
    }
    for (let y = 0; y < size - 1; y++) for (let x = 0; x < size - 1; x++) {
      const c = m[y][x];
      if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) p += 3;
    }
    const pat = [true, false, true, true, true, false, true];
    for (let pass = 0; pass < 2; pass++) for (let a = 0; a < size; a++) for (let b = 0; b + 7 <= size; b++) {
      let ok = true;
      for (let k = 0; k < 7 && ok; k++) ok = (pass ? m[b + k][a] : m[a][b + k]) === pat[k];
      if (!ok) continue;
      const lightAt = (i) => i < 0 || i >= size || !(pass ? m[i][a] : m[a][i]);
      if ([1, 2, 3, 4].every((k) => lightAt(b - k)) || [1, 2, 3, 4].every((k) => lightAt(b + 6 + k))) p += 40;
    }
    let dark = 0;
    for (const row of m) for (const v of row) if (v) dark++;
    const total = size * size;
    p += Math.ceil(Math.abs(dark * 20 - total * 10) / total - 1) * 10;
    return p;
  }
  function encode(text, eclName, minVersion) {
    const e = ECL_INDEX[eclName || "M"];
    const bytes = utf8(text);
    let ver = 0;
    for (let v = Math.max(1, minVersion || 1); v <= 40; v++) {
      const ccBits = v <= 9 ? 8 : 16;
      if (4 + ccBits + bytes.length * 8 <= dataCodewords(v, e) * 8) { ver = v; break; }
    }
    if (!ver) throw new Error("Too much text for a QR code.");
    const bits = [];
    const put = (val, n) => { for (let i = n - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
    put(4, 4); put(bytes.length, ver <= 9 ? 8 : 16);
    for (const b of bytes) put(b, 8);
    const cap = dataCodewords(ver, e) * 8;
    put(0, Math.min(4, cap - bits.length));
    put(0, (8 - (bits.length % 8)) % 8);
    const data = [];
    for (let i = 0; i < bits.length; i += 8) { let v = 0; for (let k = 0; k < 8; k++) v = (v << 1) | bits[i + k]; data.push(v); }
    for (let pad = 0xEC; data.length < cap / 8; pad ^= 0xEC ^ 0x11) data.push(pad);
    // blocks + ECC, interleaved
    const nb = NUM_BLOCKS[e][ver], ecc = ECC_PER_BLOCK[e][ver];
    const raw = Math.floor(rawDataModules(ver) / 8);
    const numShort = nb - (raw % nb), shortLen = Math.floor(raw / nb);
    const divisor = rsDivisor(ecc);
    const blocks = [];
    for (let i = 0, k = 0; i < nb; i++) {
      const len = shortLen - ecc + (i < numShort ? 0 : 1);
      const dat = data.slice(k, k + len); k += len;
      blocks.push({ dat, ecc: rsRemainder(dat, divisor) });
    }
    const out = [];
    for (let i = 0; i <= shortLen - ecc; i++) for (const b of blocks) if (i < b.dat.length) out.push(b.dat[i]);
    for (let i = 0; i < ecc; i++) for (const b of blocks) out.push(b.ecc[i]);
    // place
    const g = functionGrid(ver);
    const pos = dataPositions(g.fn, g.size);
    pos.forEach(([x, y], i) => { g.mods[y][x] = i < out.length * 8 ? bit(out[i >>> 3], 7 - (i & 7)) : false; });
    let best = null, bestP = Infinity;
    for (let mask = 0; mask < 8; mask++) {
      const m = g.mods.map((r) => r.slice());
      for (const [x, y] of pos) if (MASKS[mask](x, y)) m[y][x] = !m[y][x];
      drawFormat(m, g.size, formatBits(e, mask));
      const p = penalty(m, g.size);
      if (p < bestP) { bestP = p; best = m; }
    }
    return { size: g.size, version: ver, modules: best };
  }

  // ---------- decoding a module grid ----------
  const FORMATS = [];
  for (let e = 0; e < 4; e++) for (let m = 0; m < 8; m++) FORMATS.push({ e, m, bits: formatBits(e, m) });
  function popcount(x) { let c = 0; while (x) { c += x & 1; x >>>= 1; } return c; }

  function decodeGrid(get, size) {
    // get(x, y) → dark?
    const ver = (size - 17) / 4;
    if (ver < 1 || ver > 40 || ver !== Math.floor(ver)) return null;
    const [fa, fb] = readFormat(get, size);
    let best = null, bestD = 99;
    for (const f of FORMATS) {
      const d = Math.min(popcount(f.bits ^ fa), popcount(f.bits ^ fb));
      if (d < bestD) { bestD = d; best = f; }
    }
    if (!best || bestD > 3) return null;
    const { e, m: mask } = best;
    const g = functionGrid(ver);
    const pos = dataPositions(g.fn, size);
    const raw = Math.floor(rawDataModules(ver) / 8);
    const words = new Array(raw).fill(0);
    for (let i = 0; i < raw * 8; i++) {
      const [x, y] = pos[i];
      let v = get(x, y);
      if (MASKS[mask](x, y)) v = !v;
      if (v) words[i >>> 3] |= 0x80 >>> (i & 7);
    }
    const nb = NUM_BLOCKS[e][ver], ecc = ECC_PER_BLOCK[e][ver];
    const numShort = nb - (raw % nb), shortLen = Math.floor(raw / nb);
    const blocks = [];
    for (let i = 0; i < nb; i++) blocks.push(new Array(shortLen + (i < numShort ? 0 : 1)));
    let k = 0;
    for (let i = 0; i <= shortLen - ecc; i++) for (let b = 0; b < nb; b++) {
      if (i === shortLen - ecc && b < numShort) continue;
      blocks[b][i] = words[k++];
    }
    for (let i = 0; i < ecc; i++) for (let b = 0; b < nb; b++) blocks[b][blocks[b].length - ecc + i] = words[k++];
    const data = [];
    for (const blk of blocks) {
      if (!rsCorrect(blk, ecc)) return null;
      for (let i = 0; i < blk.length - ecc; i++) data.push(blk[i]);
    }
    return parseSegments(data, ver);
  }

  function parseSegments(data, ver) {
    let p = 0;
    const total = data.length * 8;
    const read = (n) => {
      if (p + n > total) throw new Error("eof");
      let v = 0;
      for (let i = 0; i < n; i++, p++) v = (v << 1) | ((data[p >>> 3] >>> (7 - (p & 7))) & 1);
      return v;
    };
    const grp = ver <= 9 ? 0 : ver <= 26 ? 1 : 2;
    const bytes = [];
    let text = "";
    const flush = () => {
      if (!bytes.length) return;
      const arr = new Uint8Array(bytes);
      try { text += new TextDecoder("utf-8", { fatal: true }).decode(arr); }
      catch (x) { text += Array.from(arr, (c) => String.fromCharCode(c)).join(""); }
      bytes.length = 0;
    };
    try {
      while (p + 4 <= total) {
        const mode = read(4);
        if (mode === 0) break;
        if (mode === 1) {                          // numeric
          flush();
          let n = read([10, 12, 14][grp]);
          while (n >= 3) { const v = read(10); if (v > 999) return null; text += String(v).padStart(3, "0"); n -= 3; }
          if (n === 2) { const v = read(7); if (v > 99) return null; text += String(v).padStart(2, "0"); }
          else if (n === 1) { const v = read(4); if (v > 9) return null; text += String(v); }
        } else if (mode === 2) {                   // alphanumeric
          flush();
          let n = read([9, 11, 13][grp]);
          while (n >= 2) { const v = read(11); if (v >= 45 * 45) return null; text += ALNUM[Math.floor(v / 45)] + ALNUM[v % 45]; n -= 2; }
          if (n === 1) { const v = read(6); if (v >= 45) return null; text += ALNUM[v]; }
        } else if (mode === 4) {                   // bytes
          const n = read([8, 16, 16][grp]);
          for (let i = 0; i < n; i++) bytes.push(read(8));
        } else if (mode === 8) {                   // kanji (Shift JIS)
          flush();
          const n = read([8, 10, 12][grp]);
          const sj = [];
          for (let i = 0; i < n; i++) {
            const v = read(13);
            let c = ((v / 0xC0) << 8) | (v % 0xC0);
            c += c < 0x1F00 ? 0x8140 : 0xC140;
            sj.push(c >> 8, c & 0xFF);
          }
          try { text += new TextDecoder("shift_jis").decode(new Uint8Array(sj)); } catch (x) { return null; }
        } else if (mode === 7) {                   // ECI — assume UTF-8 either way
          const first = read(8);
          if ((first & 0x80) === 0x80) read((first & 0xC0) === 0x80 ? 8 : 16);
        } else if (mode === 3) { read(16); }        // structured append header
        else if (mode === 5) { /* FNC1 first position */ }
        else if (mode === 9) { read(8); }
        else return null;
      }
    } catch (x) { if (!bytes.length && !text) return null; }
    flush();
    return text;
  }

  // ---------- image → grid ----------
  function toGray(img) {
    const { width: w, height: h, data } = img;
    const g = new Uint8Array(w * h);
    for (let i = 0, j = 0; i < g.length; i++, j += 4) {
      const a = data[j + 3];
      // transparent pixels count as white (screenshots with alpha)
      const lum = (data[j] * 77 + data[j + 1] * 150 + data[j + 2] * 29) >> 8;
      g[i] = a === 255 ? lum : 255 - (((255 - lum) * a) >> 8);
    }
    return g;
  }
  function binarize(gray, w, h) {
    const B = 8;
    const bw = Math.ceil(w / B), bh = Math.ceil(h / B);
    const avg = new Float32Array(bw * bh);
    for (let by = 0; by < bh; by++) for (let bx = 0; bx < bw; bx++) {
      let sum = 0, mn = 255, mx = 0, n = 0;
      for (let y = by * B; y < Math.min(h, by * B + B); y++) for (let x = bx * B; x < Math.min(w, bx * B + B); x++) {
        const v = gray[y * w + x]; sum += v; n++; if (v < mn) mn = v; if (v > mx) mx = v;
      }
      let a = sum / n;
      if (mx - mn <= 24) {                          // flat block: assume background unless neighbours say otherwise
        a = mn / 2;
        if (by > 0 && bx > 0) {
          const nb = (avg[(by - 1) * bw + bx] + 2 * avg[by * bw + bx - 1] + avg[(by - 1) * bw + bx - 1]) / 4;
          if (mn < nb) a = nb;
        }
      }
      avg[by * bw + bx] = a;
    }
    const out = new Uint8Array(w * h);
    for (let by = 0; by < bh; by++) for (let bx = 0; bx < bw; bx++) {
      let sum = 0, n = 0;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) {
        const yy = Math.min(bh - 1, Math.max(0, by + dy)), xx = Math.min(bw - 1, Math.max(0, bx + dx));
        sum += avg[yy * bw + xx]; n++;
      }
      const t = sum / n;
      for (let y = by * B; y < Math.min(h, by * B + B); y++) for (let x = bx * B; x < Math.min(w, bx * B + B); x++)
        out[y * w + x] = gray[y * w + x] <= t ? 1 : 0;
    }
    return out;
  }

  // Finder patterns: runs of dark:light:dark:light:dark in the ratio 1:1:3:1:1
  function ratioOk(c) {
    const total = c[0] + c[1] + c[2] + c[3] + c[4];
    if (total < 7) return false;
    const m = total / 7, v = m / 1.6;
    return Math.abs(m - c[0]) < v && Math.abs(m - c[1]) < v && Math.abs(3 * m - c[2]) < 3 * v && Math.abs(m - c[3]) < v && Math.abs(m - c[4]) < v;
  }
  function crossCheck(bin, w, h, cx, cy, dx, dy, maxCount) {
    // walk from the centre in both directions along (dx, dy); returns [centreOffset, total] or null
    const c = [0, 0, 0, 0, 0];
    const at = (k) => { const x = Math.round(cx + dx * k), y = Math.round(cy + dy * k); return x >= 0 && y >= 0 && x < w && y < h ? bin[y * w + x] : -1; };
    let k = 0;
    while (at(k) === 1) { c[2]++; k--; }
    if (at(k) === -1) return null;
    while (at(k) === 0 && c[1] <= maxCount) { c[1]++; k--; }
    if (at(k) === -1 || c[1] > maxCount) return null;
    while (at(k) === 1 && c[0] <= maxCount) { c[0]++; k--; }
    if (c[0] > maxCount) return null;
    k = 1;
    while (at(k) === 1) { c[2]++; k++; }
    if (at(k) === -1) return null;
    while (at(k) === 0 && c[3] <= maxCount) { c[3]++; k++; }
    if (at(k) === -1 || c[3] > maxCount) return null;
    while (at(k) === 1 && c[4] <= maxCount) { c[4]++; k++; }
    if (c[4] > maxCount) return null;
    if (!ratioOk(c)) return null;
    // centre of the middle run relative to the start point
    return { offset: (k - c[4] - c[3]) - c[2] / 2, total: c.reduce((a, b) => a + b, 0) };
  }
  function findFinders(bin, w, h) {
    const cands = [];
    const add = (x, y, size) => {
      for (const c of cands) {
        if (Math.abs(c.x - x) <= c.size && Math.abs(c.y - y) <= c.size && Math.abs(size - c.size) <= Math.max(1, c.size * 0.5)) {
          const n = c.n + 1;
          c.x = (c.x * c.n + x) / n; c.y = (c.y * c.n + y) / n; c.size = (c.size * c.n + size) / n; c.n = n;
          return;
        }
      }
      cands.push({ x, y, size, n: 1 });
    };
    for (let y = 0; y < h; y++) {
      const c = [0, 0, 0, 0, 0];
      let s = 0;
      const row = y * w;
      for (let x = 0; x <= w; x++) {
        const v = x < w ? bin[row + x] : 0;
        if (v === 1) {
          if (s === 1 || s === 3) s++;
          c[s]++;
        } else {
          if (s === 0 || s === 2) { if (c[s] > 0) { s++; c[s]++; } else continue; }
          else if (s === 4) {
            if (ratioOk(c)) {
              const cx = x - c[4] - c[3] - c[2] / 2;
              const max = c[2];
              const v1 = crossCheck(bin, w, h, cx, y, 0, 1, max);
              if (v1) {
                const cy = y + v1.offset;
                const h1 = crossCheck(bin, w, h, cx, cy, 1, 0, max);
                if (h1) {
                  const fx = cx + h1.offset;
                  const d1 = crossCheck(bin, w, h, fx, cy, 1, 1, max);
                  if (d1) add(fx, cy, (h1.total + v1.total) / 14);
                }
              }
            }
            c[0] = c[2]; c[1] = c[3]; c[2] = c[4]; c[3] = 1; c[4] = 0; s = 3;
          } else c[s]++;
        }
      }
    }
    return cands;
  }
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  function triples(cands) {
    let pool = cands.filter((c) => c.n >= 2);
    if (pool.length < 3) pool = cands.slice();
    pool.sort((a, b) => b.n - a.n);
    pool = pool.slice(0, 12);
    const out = [];
    for (let i = 0; i < pool.length; i++) for (let j = i + 1; j < pool.length; j++) for (let k = j + 1; k < pool.length; k++) {
      const t = [pool[i], pool[j], pool[k]];
      const sizes = t.map((c) => c.size);
      if (Math.max(...sizes) / Math.min(...sizes) > 1.6) continue;
      const d = [[dist(t[1], t[2]), 0], [dist(t[0], t[2]), 1], [dist(t[0], t[1]), 2]].sort((a, b) => a[0] - b[0]);
      const [a, b, c] = d;
      const mod = (sizes[0] + sizes[1] + sizes[2]) / 3;
      if (a[0] < mod * 8) continue;                // too close: finders are ≥ 14 modules apart (×√2 slack for rotation)
      const err = Math.abs(a[0] - b[0]) / b[0] + Math.abs(c[0] - Math.SQRT2 * b[0]) / c[0];
      if (err > 0.5) continue;
      const tl = t[c[1]], others = t.filter((_, idx) => idx !== c[1]);
      let [p, q] = others;
      const cross = (p.x - tl.x) * (q.y - tl.y) - (p.y - tl.y) * (q.x - tl.x);
      if (cross < 0) [p, q] = [q, p];
      out.push({ tl, tr: p, bl: q, err, mod });
    }
    out.sort((a, b) => a.err - b.err);
    return out.slice(0, 6);
  }
  function solveHomography(src, dst) {
    // src/dst: 4 points [x, y]; returns f(u, v) → [x, y]
    const A = [], bv = [];
    for (let i = 0; i < 4; i++) {
      const [u, v] = src[i], [x, y] = dst[i];
      A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]); bv.push(x);
      A.push([0, 0, 0, u, v, 1, -u * y, -v * y]); bv.push(y);
    }
    for (let c = 0; c < 8; c++) {                  // Gaussian elimination with pivoting
      let piv = c;
      for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[piv][c])) piv = r;
      if (Math.abs(A[piv][c]) < 1e-12) return null;
      [A[c], A[piv]] = [A[piv], A[c]]; [bv[c], bv[piv]] = [bv[piv], bv[c]];
      for (let r = 0; r < 8; r++) {
        if (r === c) continue;
        const f = A[r][c] / A[c][c];
        if (!f) continue;
        for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k];
        bv[r] -= f * bv[c];
      }
    }
    const H = bv.map((v, i) => v / A[i][i]);
    return (u, v) => { const d = H[6] * u + H[7] * v + 1; return [(H[0] * u + H[1] * v + H[2]) / d, (H[3] * u + H[4] * v + H[5]) / d]; };
  }
  function findAlignment(bin, w, h, ex, ey, mod) {
    // candidates for a dark module inside a light ring inside a dark ring, nearest (ex, ey) first
    const r = Math.max(4, Math.round(mod * 10));
    const found = [];
    const x0 = Math.max(0, Math.round(ex - r)), x1 = Math.min(w - 1, Math.round(ex + r));
    const y0 = Math.max(0, Math.round(ey - r)), y1 = Math.min(h - 1, Math.round(ey + r));
    const okRun = (n) => n > mod * 0.4 && n < mod * 1.8;
    const check = (cx, cy, dx, dy) => {
      const at = (k) => { const x = Math.round(cx + dx * k), y = Math.round(cy + dy * k); return x >= 0 && y >= 0 && x < w && y < h ? bin[y * w + x] : -1; };
      let a = 0, k = 0; while (at(k) === 1 && a <= 2 * mod) { a++; k--; }
      let l1 = 0; while (at(k) === 0 && l1 <= 2 * mod) { l1++; k--; }
      if (at(k) !== 1) return null;
      k = 1; while (at(k) === 1 && a <= 2 * mod) { a++; k++; }
      let l2 = 0; while (at(k) === 0 && l2 <= 2 * mod) { l2++; k++; }
      if (at(k) !== 1) return null;
      if (!okRun(a) || !okRun(l1) || !okRun(l2) || Math.max(l1, l2) > 2.2 * Math.min(l1, l2)) return null;
      return (k - l2) - a / 2 - 0.5;
    };
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      if (bin[y * w + x] !== 1) continue;
      const ox = check(x, y, 1, 0);
      if (ox === null) continue;
      const cx = x + ox;
      const oy = check(cx, y, 0, 1);
      if (oy === null) continue;
      const cy = y + oy;
      if (check(cx, cy, 1, 0) === null) continue;
      if (found.some((f) => Math.abs(f.x - cx) < mod && Math.abs(f.y - cy) < mod)) continue;
      found.push({ x: cx, y: cy, d: Math.hypot(cx - ex, cy - ey) });
    }
    return found.sort((a, b) => a.d - b.d).slice(0, 3);
  }
  function sampleAndDecode(bin, w, h, map, size) {
    const get = (mx, my) => {
      const [x, y] = map(mx + 0.5, my + 0.5);
      const xi = Math.round(x - 0.5), yi = Math.round(y - 0.5);
      if (xi < 0 || yi < 0 || xi >= w || yi >= h) return false;
      return bin[yi * w + xi] === 1;
    };
    const grid = [];
    for (let y = 0; y < size; y++) { const r = []; for (let x = 0; x < size; x++) r.push(get(x, y)); grid.push(r); }
    const t = decodeGrid((x, y) => grid[y][x], size);
    if (t !== null) return t;
    return decodeGrid((x, y) => grid[x][y], size);  // mirrored
  }
  function moduleAlong(bin, w, h, p, q) {
    // module size measured across finder p in the direction of q (handles rotated codes)
    const d = dist(p, q), ux = (q.x - p.x) / d, uy = (q.y - p.y) / d;
    const r = crossCheck(bin, w, h, p.x, p.y, ux, uy, p.size * 6);
    return r ? r.total / 7 : null;
  }
  let deadline = Infinity;
  function tryTriple(bin, w, h, t) {
    const { tl, tr, bl } = t;
    const mod = (tl.size + tr.size + bl.size) / 3;
    const mu = [moduleAlong(bin, w, h, tl, tr), moduleAlong(bin, w, h, tr, tl)].filter(Boolean);
    const mv = [moduleAlong(bin, w, h, tl, bl), moduleAlong(bin, w, h, bl, tl)].filter(Boolean);
    const modU = mu.length ? mu.reduce((a, b) => a + b) / mu.length : mod;
    const modV = mv.length ? mv.reduce((a, b) => a + b) / mv.length : mod;
    const est = Math.round((dist(tl, tr) / modU + dist(tl, bl) / modV) / 2) + 7;
    const base = Math.max(21, Math.min(177, 4 * Math.round((est - 17) / 4) + 17));
    const tried = [];
    for (const size of [base, base + 4, base - 4, base + 8, base - 8]) {
      if (size < 21 || size > 177) continue;
      const ver = (size - 17) / 4;
      const pts = [[3.5, 3.5], [size - 3.5, 3.5], [3.5, size - 3.5]];
      const img = [[tl.x, tl.y], [tr.x, tr.y], [bl.x, bl.y]];
      const par = [tr.x + bl.x - tl.x, tr.y + bl.y - tl.y];
      const attempts = [];
      if (ver >= 2) {
        const f = (size - 10) / (size - 7);        // alignment centre, measured from the top-left finder
        const ex = tl.x + f * (par[0] - tl.x), ey = tl.y + f * (par[1] - tl.y);
        for (const ap of findAlignment(bin, w, h, ex, ey, mod)) attempts.push([[size - 6.5, size - 6.5], [ap.x, ap.y]]);
      }
      attempts.push([[size - 3.5, size - 3.5], par]);
      for (const [src4, dst4] of attempts) {
        const map = solveHomography(pts.concat([src4]), img.concat([dst4]));
        if (!map) continue;
        const text = sampleAndDecode(bin, w, h, map, size);
        if (text !== null) return text;
      }
      tried.push({ size, pts, img, par });
    }
    // Strong perspective: search around the parallelogram's fourth corner (the error correction says when it's right)
    const step = (modU + modV) / 2;
    for (const { size, pts, img, par } of tried.slice(0, 2)) {
      const offs = [];
      for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) if (dx || dy) offs.push([dx, dy]);
      offs.sort((a, b) => Math.hypot(a[0], a[1]) - Math.hypot(b[0], b[1]));
      for (const [dx, dy] of offs) {
        if (Date.now() > deadline) return null;
        const map = solveHomography(pts.concat([[size - 3.5, size - 3.5]]), img.concat([[par[0] + dx * step * 0.75, par[1] + dy * step * 0.75]]));
        if (!map) continue;
        const text = sampleAndDecode(bin, w, h, map, size);
        if (text !== null) return text;
      }
    }
    return null;
  }
  function decodeBinary(bin, w, h) {
    const cands = findFinders(bin, w, h);
    for (const t of triples(cands)) {
      if (Date.now() > deadline) return null;
      const text = tryTriple(bin, w, h, t);
      if (text !== null) return text;
    }
    return null;
  }
  function otsu(gray, w, h) {
    const hist = new Array(256).fill(0);
    for (let i = 0; i < gray.length; i++) hist[gray[i]]++;
    const total = gray.length;
    let sum = 0; for (let i = 0; i < 256; i++) sum += i * hist[i];
    let sumB = 0, wB = 0, best = 0, t = 127;
    for (let i = 0; i < 256; i++) {
      wB += hist[i]; if (!wB) continue;
      const wF = total - wB; if (!wF) break;
      sumB += i * hist[i];
      const mB = sumB / wB, mF = (sum - sumB) / wF, between = wB * wF * (mB - mF) * (mB - mF);
      if (between > best) { best = between; t = i; }
    }
    const out = new Uint8Array(w * h);
    for (let i = 0; i < out.length; i++) out[i] = gray[i] <= t ? 1 : 0;
    return out;
  }
  function decode(img, opts) {
    const { width: w, height: h } = img;
    deadline = Date.now() + ((opts && opts.budgetMs) || 2000);
    const gray = toGray(img);
    for (const bin of [binarize(gray, w, h), otsu(gray, w, h)]) {
      let t = decodeBinary(bin, w, h);
      if (t !== null) return t;
      for (let i = 0; i < bin.length; i++) bin[i] ^= 1;    // light-on-dark codes
      t = decodeBinary(bin, w, h);
      if (t !== null) return t;
    }
    return null;
  }

  // For tests: the block error-corrector and table helpers
  return { encode, decode, decodeGrid, _rs: { rsDivisor, rsRemainder, rsCorrect }, _t: { dataCodewords, alignmentPositions } };
})();
if (typeof module !== "undefined" && module.exports) module.exports = QR;
