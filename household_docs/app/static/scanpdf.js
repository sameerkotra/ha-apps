"use strict";
/* Household Docs — the camera scan's arithmetic (SPEC §17.7), no DOM, so Node tests run it:
   - jpegInfo(bytes)                      → {width, height, components} from a JPEG's SOF marker
   - buildPdf(pages)                      → Uint8Array: one PDF, a page per JPEG (pages: [{jpeg: Uint8Array}]) —
                                            a small JPEG-in-PDF writer (DCTDecode), written for the app
   - homography(from, to)                 → the 3×3 matrix taking four points onto four others
   - warp(src, quad, outW, outH)          → the part of an image inside `quad` (four corners, clockwise from the
                                            top left), straightened into an outW × outH image (bilinear)
   - blackAndWhite(img, opts)             → in place: grey, then each pixel against the mean of its
                                            neighbourhood (an adaptive threshold — text stays readable in shade)
   - suggestQuad(w, h)                    → the starting corners (a little inside the photo's edges)
   - outputSize(quad, maxSide)            → the straightened page's size from the corners' distances
   - autoName(pattern, when, folder, n)   → "Scan 2026-10-03 20-41" from "Scan {date} {time}" ({folder}, {n})
   Images are {width, height, data} with RGBA bytes, as ImageData. */
(function (root) {
  // ---------------------------------------------------------------- JPEG
  function jpegInfo(b) {
    if (!b || b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) throw new Error("This isn't a JPEG picture.");
    let i = 2;
    while (i + 9 < b.length) {
      if (b[i] !== 0xff) { i += 1; continue; }
      const m = b[i + 1];
      if (m === 0xd8 || m === 0x01 || (m >= 0xd0 && m <= 0xd7) || m === 0xff) { i += 2; continue; }
      const len = (b[i + 2] << 8) | b[i + 3];
      // SOF0..SOF15 except DHT (C4), JPG (C8) and DAC (CC)
      if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
        return { height: (b[i + 5] << 8) | b[i + 6], width: (b[i + 7] << 8) | b[i + 8], components: b[i + 9] };
      }
      if (m === 0xda) break;          // start of scan before any frame header: broken
      i += 2 + len;
    }
    throw new Error("Couldn't read the picture's size.");
  }

  // ---------------------------------------------------------------- PDF
  const A4_WIDTH = 595.28;            // points; each page is as wide as A4, as tall as the picture's shape needs

  function ascii(s) { const out = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff; return out; }
  function fmt(n) { return (Math.round(n * 100) / 100).toString(); }

  function buildPdf(pages, opts = {}) {
    if (!pages || !pages.length) throw new Error("Nothing to save.");
    const chunks = [];
    let length = 0;
    const offsets = [];
    const push = (u8) => { chunks.push(u8); length += u8.length; };
    const obj = (n, parts) => {
      offsets[n] = length;
      push(ascii(`${n} 0 obj\n`));
      parts.forEach((p) => push(typeof p === "string" ? ascii(p) : p));
      push(ascii("\nendobj\n"));
    };
    push(ascii("%PDF-1.4\n"));
    push(new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a]));        // binary marker line
    const n = pages.length;
    // 1 catalog, 2 pages, then per page: page, content, image
    const kids = [];
    for (let p = 0; p < n; p++) kids.push(`${3 + p * 3} 0 R`);
    obj(1, ["<< /Type /Catalog /Pages 2 0 R >>"]);
    obj(2, [`<< /Type /Pages /Count ${n} /Kids [${kids.join(" ")}] >>`]);
    pages.forEach((pg, p) => {
      const info = jpegInfo(pg.jpeg);
      const w = opts.pageWidth || A4_WIDTH;
      const h = w * info.height / info.width;
      const pageN = 3 + p * 3, contentN = pageN + 1, imageN = pageN + 2;
      obj(pageN, [`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${fmt(w)} ${fmt(h)}] /Resources << /XObject << /Im${p + 1} ${imageN} 0 R >> >> /Contents ${contentN} 0 R >>`]);
      const content = `q ${fmt(w)} 0 0 ${fmt(h)} 0 0 cm /Im${p + 1} Do Q`;
      obj(contentN, [`<< /Length ${content.length} >>\nstream\n`, content, "\nendstream"]);
      const cs = info.components === 1 ? "/DeviceGray" : info.components === 4 ? "/DeviceCMYK" : "/DeviceRGB";
      obj(imageN, [`<< /Type /XObject /Subtype /Image /Width ${info.width} /Height ${info.height} /ColorSpace ${cs} /BitsPerComponent 8 /Filter /DCTDecode /Length ${pg.jpeg.length} >>\nstream\n`,
        pg.jpeg, "\nendstream"]);
    });
    const count = 3 + n * 3;
    const xref = length;
    let table = `xref\n0 ${count}\n0000000000 65535 f \n`;
    for (let k = 1; k < count; k++) table += String(offsets[k]).padStart(10, "0") + " 00000 n \n";
    push(ascii(table));
    push(ascii(`trailer\n<< /Size ${count} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`));
    const out = new Uint8Array(length);
    let at = 0;
    chunks.forEach((c) => { out.set(c, at); at += c.length; });
    return out;
  }

  // ---------------------------------------------------------------- straightening
  // Solve the 8×8 system for the homography h (h33 = 1) with from[i] → to[i].
  function homography(from, to) {
    const A = [], bvec = [];
    for (let i = 0; i < 4; i++) {
      const [x, y] = from[i], [u, v] = to[i];
      A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); bvec.push(u);
      A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); bvec.push(v);
    }
    const h = solve(A, bvec);
    if (!h) return null;
    return [h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1];
  }
  function solve(A, b) {
    const n = b.length;
    const M = A.map((row, i) => row.concat([b[i]]));
    for (let c = 0; c < n; c++) {
      let piv = c;
      for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
      if (Math.abs(M[piv][c]) < 1e-12) return null;
      [M[c], M[piv]] = [M[piv], M[c]];
      for (let r = 0; r < n; r++) {
        if (r === c) continue;
        const f = M[r][c] / M[c][c];
        if (!f) continue;
        for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
      }
    }
    return M.map((row, i) => row[n] / row[i]);
  }
  function apply(H, x, y) {
    const d = H[6] * x + H[7] * y + H[8];
    return [(H[0] * x + H[1] * y + H[2]) / d, (H[3] * x + H[4] * y + H[5]) / d];
  }

  function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
  function outputSize(quad, maxSide = 2000) {
    const w = Math.max(dist(quad[0], quad[1]), dist(quad[3], quad[2]));
    const h = Math.max(dist(quad[0], quad[3]), dist(quad[1], quad[2]));
    const s = Math.min(1, maxSide / Math.max(w, h, 1));
    return { width: Math.max(1, Math.round(w * s)), height: Math.max(1, Math.round(h * s)) };
  }

  function warp(src, quad, outW, outH, make) {
    // map each output pixel back into the photo: H takes the output rectangle onto the quad
    const H = homography([[0, 0], [outW - 1, 0], [outW - 1, outH - 1], [0, outH - 1]], quad);
    const out = make ? make(outW, outH) : { width: outW, height: outH, data: new Uint8ClampedArray(outW * outH * 4) };
    if (!H) return out;
    const sw = src.width, sh = src.height, s = src.data, d = out.data;
    for (let y = 0; y < outH; y++) {
      for (let x = 0; x < outW; x++) {
        const [fx, fy] = apply(H, x, y);
        const o = (y * outW + x) * 4;
        if (fx < 0 || fy < 0 || fx > sw - 1 || fy > sh - 1) { d[o] = d[o + 1] = d[o + 2] = 255; d[o + 3] = 255; continue; }
        const x0 = Math.floor(fx), y0 = Math.floor(fy), x1 = Math.min(x0 + 1, sw - 1), y1 = Math.min(y0 + 1, sh - 1);
        const ax = fx - x0, ay = fy - y0;
        const i00 = (y0 * sw + x0) * 4, i10 = (y0 * sw + x1) * 4, i01 = (y1 * sw + x0) * 4, i11 = (y1 * sw + x1) * 4;
        for (let c = 0; c < 3; c++) {
          const top = s[i00 + c] * (1 - ax) + s[i10 + c] * ax;
          const bot = s[i01 + c] * (1 - ax) + s[i11 + c] * ax;
          d[o + c] = top * (1 - ay) + bot * ay;
        }
        d[o + 3] = 255;
      }
    }
    return out;
  }

  function suggestQuad(w, h) {
    const mx = Math.round(w * 0.04), my = Math.round(h * 0.04);
    return [[mx, my], [w - 1 - mx, my], [w - 1 - mx, h - 1 - my], [mx, h - 1 - my]];
  }

  // ---------------------------------------------------------------- black and white
  function blackAndWhite(img, opts = {}) {
    const w = img.width, h = img.height, d = img.data;
    const grey = new Float64Array(w * h);
    for (let i = 0, p = 0; p < w * h; p++, i += 4) grey[p] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    // integral image for the mean of a window around each pixel
    const W = w + 1, I = new Float64Array(W * (h + 1));
    for (let y = 1; y <= h; y++) {
      let row = 0;
      for (let x = 1; x <= w; x++) { row += grey[(y - 1) * w + (x - 1)]; I[y * W + x] = I[(y - 1) * W + x] + row; }
    }
    const r = Math.max(4, Math.round((opts.window || Math.max(w, h) / 24) / 2));
    const k = opts.k === undefined ? 0.88 : opts.k;           // a pixel is ink when darker than 88 % of its neighbourhood
    for (let y = 0; y < h; y++) {
      const y0 = Math.max(0, y - r), y1 = Math.min(h, y + r + 1);
      for (let x = 0; x < w; x++) {
        const x0 = Math.max(0, x - r), x1 = Math.min(w, x + r + 1);
        const sum = I[y1 * W + x1] - I[y0 * W + x1] - I[y1 * W + x0] + I[y0 * W + x0];
        const mean = sum / ((x1 - x0) * (y1 - y0));
        const v = grey[y * w + x] < mean * k ? 0 : 255;
        const o = (y * w + x) * 4;
        d[o] = d[o + 1] = d[o + 2] = v;
        d[o + 3] = 255;
      }
    }
    return img;
  }

  // ---------------------------------------------------------------- names
  function pad(n) { return String(n).padStart(2, "0"); }
  function autoName(pattern, when, folder, n) {
    const d = when || new Date();
    const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const time = `${pad(d.getHours())}-${pad(d.getMinutes())}`;
    let s = String(pattern || "Scan {date} {time}")
      .replace(/\{date\}/g, date).replace(/\{time\}/g, time)
      .replace(/\{folder\}/g, folder || "").replace(/\{n\}/g, n === undefined ? "" : String(n));
    s = s.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").replace(/\s+/g, " ").trim().replace(/^\.+/, "").replace(/[ .]+$/, "");
    return s || `Scan ${date} ${time}`;
  }

  const api = { jpegInfo, buildPdf, homography, apply, warp, outputSize, suggestQuad, blackAndWhite, autoName };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ScanPdf = api;
})(typeof window !== "undefined" ? window : globalThis);
