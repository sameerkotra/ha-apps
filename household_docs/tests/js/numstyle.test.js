"use strict";
// The personal "number style for sheets" (SPEC §4): how numbers show and how typed numbers are read.
const test = require("node:test");
const assert = require("node:assert");
const S = require("../../app/static/sheetcalc.js");

test("typing numbers in each style", () => {
  const v = (t, s) => S.parseInput(t, s).v;
  // 1,234.56 (and the device's own, as before)
  assert.strictEqual(v("1,234.56", "en"), 1234.56);
  assert.strictEqual(v("1,234.56"), 1234.56);
  assert.strictEqual(v("12,34,567.89", "en"), "12,34,567.89");          // not that style: text
  // 1.234,56
  assert.strictEqual(v("1.234,56", "de"), 1234.56);
  assert.strictEqual(v("1234,5", "de"), 1234.5);
  assert.strictEqual(v("1.234", "de"), 1234);
  assert.strictEqual(v("1.5", "de"), 1.5);                              // can't be thousands: a decimal point
  assert.strictEqual(v("-0,25", "de"), -0.25);
  assert.deepStrictEqual(S.parseInput("12,5%", "de"), { v: 0.125, f: "percent", d: 1 });
  assert.strictEqual(v("1,234.56", "de"), "1,234.56");
  // 12,34,567.89
  assert.strictEqual(v("12,34,567.89", "in"), 1234567.89);
  assert.strictEqual(v("1,234.5", "in"), 1234.5);
  assert.strictEqual(v("99,999", "in"), 99999);
  assert.deepStrictEqual(S.parseInput("1,00,000%", "in"), { v: 1000, f: "percent", d: 0 });
  // dates and the rest are the same in every style
  for (const s of ["en", "de", "in", "auto"]) {
    assert.deepStrictEqual(S.parseInput("2026-03-09", s), S.parseInput("2026-03-09"));
    assert.strictEqual(v("TRUE", s), true);
    assert.strictEqual(v("=SUM(A1:A3)", s), "=SUM(A1:A3)");
  }
});

test("showing numbers in each style", () => {
  const f = (n, fmt, s) => S.format(n, fmt, { numStyle: s, currency: "EUR" }).text;
  assert.strictEqual(f(1234567.891, { f: "number" }, "en"), "1,234,567.89");
  assert.strictEqual(f(1234567.891, { f: "number" }, "de"), "1.234.567,89");
  assert.strictEqual(f(1234567.891, { f: "number" }, "in"), "12,34,567.89");
  assert.strictEqual(f(1.5, null, "de"), "1,5");
  assert.strictEqual(f(1.5, null, "in"), "1.5");
  assert.ok(f(0.125, { f: "percent", d: 1 }, "de").startsWith("12,5"));
  assert.ok(f(1234.5, { f: "currency" }, "de").includes("1.234,50"));
});
