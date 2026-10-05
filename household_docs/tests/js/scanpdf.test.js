"use strict";
// node --test tests/js — the camera scan's arithmetic (app/static/scanpdf.js): JPEG sizes, the JPEG-in-PDF writer,
// straightening with a homography, black and white, and file names. tests/test_bring.py also reads a PDF made here
// with pypdf and uploads it.
const test = require("node:test");
const assert = require("node:assert");
const S = require("../../app/static/scanpdf.js");

// two tiny JPEGs (an invented red 4×3 picture in colour and a grey 3×5 one), made with Pillow
const RGB = Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAADAAQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDgKKKK+eP2E//Z", "base64");
const GREY = Buffer.from("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAAFAAMBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/ACv/2Q==", "base64");

test("jpegInfo reads the frame header", () => {
  assert.deepStrictEqual(S.jpegInfo(RGB), { width: 4, height: 3, components: 3 });
  assert.deepStrictEqual(S.jpegInfo(GREY), { width: 3, height: 5, components: 1 });
  assert.throws(() => S.jpegInfo(Buffer.from("%PDF-1.4")), /isn't a JPEG/);
  assert.throws(() => S.jpegInfo(Buffer.from([0xff, 0xd8, 0xff, 0xda, 0, 2])), /size/);
});

test("buildPdf: a well-formed PDF with a page per picture and a correct cross-reference table", () => {
  const pdf = S.buildPdf([{ jpeg: RGB }, { jpeg: GREY }]);
  const text = Buffer.from(pdf).toString("latin1");
  assert.ok(text.startsWith("%PDF-1.4\n"));
  assert.ok(text.endsWith("%%EOF\n"));
  assert.match(text, /\/Count 2 \/Kids \[3 0 R 6 0 R\]/);
  assert.match(text, /\/ColorSpace \/DeviceRGB/);
  assert.match(text, /\/ColorSpace \/DeviceGray/);
  assert.match(text, /\/MediaBox \[0 0 595\.28 446\.46\]/);            // as wide as A4, the picture's shape
  // every offset in the xref points at "<n> 0 obj"
  const xrefAt = Number(text.match(/startxref\n(\d+)\n/)[1]);
  assert.strictEqual(text.slice(xrefAt, xrefAt + 4), "xref");
  const rows = text.slice(xrefAt).split("\n").slice(2, 2 + 9);
  rows.slice(1).forEach((row, i) => {
    const off = Number(row.slice(0, 10));
    assert.strictEqual(text.slice(off, off + String(i + 1).length + 6), `${i + 1} 0 obj`);
  });
  // the JPEG is in there byte for byte
  assert.ok(Buffer.from(pdf).includes(RGB));
  assert.throws(() => S.buildPdf([]), /Nothing/);
});

test("homography maps the four corners, and warp straightens", () => {
  const quad = [[10, 5], [90, 12], [95, 70], [3, 60]];
  const H = S.homography([[0, 0], [49, 0], [49, 29], [0, 29]], quad);
  const back = [[0, 0], [49, 0], [49, 29], [0, 29]].map(([x, y]) => S.apply(H, x, y));
  back.forEach((p, i) => { assert.ok(Math.abs(p[0] - quad[i][0]) < 1e-6 && Math.abs(p[1] - quad[i][1]) < 1e-6); });
  assert.strictEqual(S.homography([[0, 0], [0, 0], [0, 0], [0, 0]], quad), null);
  // a 20×20 picture: left half black, right half white; the whole of it, warped to 10×10, keeps that
  const w = 20, h = 20, data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const o = (y * w + x) * 4; const v = x < 10 ? 0 : 255; data[o] = data[o + 1] = data[o + 2] = v; data[o + 3] = 255; }
  const out = S.warp({ width: w, height: h, data }, [[0, 0], [19, 0], [19, 19], [0, 19]], 10, 10);
  assert.strictEqual(out.data[(5 * 10 + 1) * 4], 0);
  assert.strictEqual(out.data[(5 * 10 + 8) * 4], 255);
  const size = S.outputSize([[0, 0], [3000, 0], [3000, 4000], [0, 4000]], 2000);
  assert.deepStrictEqual(size, { width: 1500, height: 2000 });
  assert.deepStrictEqual(S.suggestQuad(100, 50)[0], [4, 2]);
});

test("black and white keeps dark text dark in uneven light", () => {
  const w = 60, h = 20, data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const o = (y * w + x) * 4; const paper = 120 + x * 2;                // a shadow from left to right
    const ink = (x % 15 === 7) ? paper - 70 : paper;
    data[o] = data[o + 1] = data[o + 2] = ink; data[o + 3] = 255;
  }
  S.blackAndWhite({ width: w, height: h, data });
  assert.strictEqual(data[(10 * w + 7) * 4], 0);                         // ink in the shade
  assert.strictEqual(data[(10 * w + 52) * 4], 0);                        // ink in the light
  assert.strictEqual(data[(10 * w + 2) * 4], 255);                       // shaded paper is white
  assert.strictEqual(data[(10 * w + 40) * 4], 255);
});

test("autoName fills the pattern and keeps names Windows can hold", () => {
  const when = new Date(2026, 9, 3, 20, 41);
  assert.strictEqual(S.autoName("Scan {date} {time}", when), "Scan 2026-10-03 20-41");
  assert.strictEqual(S.autoName("{date} {folder}", when, "Taxes"), "2026-10-03 Taxes");
  assert.strictEqual(S.autoName("Bill {date} p{n}", when, "", 2), "Bill 2026-10-03 p2");
  assert.strictEqual(S.autoName("a/b:c? ", when), "a_b_c_");
  assert.strictEqual(S.autoName("   ", when), "Scan 2026-10-03 20-41");
});
