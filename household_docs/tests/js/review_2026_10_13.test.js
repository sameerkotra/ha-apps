"use strict";
// node --test tests/js — the browser code's fixes from the 13 October 2026 review: formulas nested without end
// (DF1), SUMIF … COUNTIFS over a huge explicit range (DF2), wildcard criteria that used to backtrack (DF3),
// MIN / MAX over 150 000 numbers (DFI3), and Markdown that used to take quadratic or cubic time (DF4). Each
// rewritten piece is also checked against what the old code gave.
const test = require("node:test");
const assert = require("node:assert");
const S = require("../../app/static/sheetcalc.js");
const Md = require("../../app/static/md.js");

const TODAY = S.dateSerial(2026, 10, 5);
function book(cells = {}, tabs = ["S"]) {
  const wb = S.workbook({ today: () => TODAY });
  wb.setTabs(tabs);
  for (const [k, v] of Object.entries(cells)) {
    const [tab, ref] = k.includes("!") ? k.split("!") : [tabs[0], k];
    wb.set(tab, ref, v);
  }
  wb.recalc();
  return wb;
}
const isErr = (code) => (v) => S.isErr(v) && v.code === code;
function quick(ms, fn) { const t = Date.now(); const out = fn(); assert.ok(Date.now() - t < ms, `took ${Date.now() - t} ms`); return out; }

// ---------------------------------------------------------------- DF1: nesting
test("a formula nested past 64 levels is a syntax error, not a stack overflow", () => {
  assert.throws(() => S.parse("(".repeat(65) + "1" + ")".repeat(65)), S.CalcSyntax);
  assert.strictEqual(S.parse("(".repeat(60) + "1" + ")".repeat(60)).k, "paren");
  assert.throws(() => S.parse("ABS(".repeat(100) + "1" + ")".repeat(100)), S.CalcSyntax);
  assert.throws(() => S.parse("-".repeat(5000) + "1"), S.CalcSyntax);
  assert.throws(() => S.parse(Array(3000).fill("1").join("+")), S.CalcSyntax);      // a deep tree without brackets
  assert.doesNotThrow(() => S.parse(Array(200).fill("A1").join("+")));
});

test("a sheet with a hostile formula still opens; that cell shows an error", () => {
  const wb = quick(5000, () => book({ A1: 5, B1: "=" + "(".repeat(20000) + "1" + ")".repeat(20000), C1: "=A1*2" }));
  assert.ok(isErr("#NAME?")(wb.value("S", "B1")));
  assert.strictEqual(wb.value("S", "C1"), 10);
});

// ---------------------------------------------------------------- DF2: big explicit ranges
test("COUNTIF over A1:XFD1048576 answers at once and counts the empties arithmetically", () => {
  const wb = quick(3000, () => book({ "S!A1": "x", "S!B2": "x", "S!C3": "y", "S!D4": 7,
    "T!E1": '=COUNTIF(S!A1:XFD1048576, "x")', "T!E2": '=COUNTIF(S!A1:XFD1048576, "")', "T!E3": '=COUNTIF(S!A1:XFD1048576, "<>x")',
    "T!E4": '=SUMIF(S!A1:XFD1048576, ">5")', "T!E5": '=COUNTIFS(S!A1:XFD1048576, "", S!A1:XFD1048576, "")' }, ["S", "T"]));
  const v = (ref) => wb.value("T", ref);
  assert.strictEqual(v("E1"), 2);
  const all = 16384 * 1048576;
  assert.strictEqual(v("E2"), all - 4);
  assert.strictEqual(v("E3"), all - 2);
  assert.strictEqual(v("E4"), 7);
  assert.strictEqual(v("E5"), all - 4);
});

test("SUMIF … COUNTIFS give what visiting every cell gives", () => {
  // a small sheet with blanks, text, numbers and an error; criteria that do and don't match empty cells
  const cells = { A1: "a", A2: "b", A3: "", A4: "a", A6: 3, B1: 1, B2: 2, B4: "4", B5: 5, B6: 6, C2: "a", C6: "=1/0" };
  const crits = ['"a"', '""', '"<>a"', '">2"', '"<>"', "3", '"?"', '"*"'];
  for (const cr of crits) {
    const f = { X1: `=COUNTIF(A1:C8, ${cr})`, X2: `=SUMIF(A1:A8, ${cr}, B1:B8)`, X3: `=COUNTIFS(A1:A8, ${cr}, B1:B8, "<>")`,
      X4: `=AVERAGEIF(A1:A8, ${cr}, B1:B8)`, X5: `=SUMIFS(C1:C8, A1:A8, ${cr})` };
    const wb = book(Object.assign({}, cells, f));
    // the expected values, by brute force over every cell
    const get = (ref) => wb.value("S", ref);
    const crit = (v) => { const t = book({ A1: v === null ? null : v, B1: `=COUNTIF(A1, ${cr})` }); return t.value("S", "B1") === 1; };
    let count = 0, sum = 0, n = 0, cnt3 = 0, err = null;
    for (let r = 1; r <= 8; r++) {
      for (const c of "ABC") if (crit(get(c + r))) count++;
      if (crit(get("A" + r))) {
        const b = get("B" + r);
        if (typeof b === "number") { sum += b; n++; }
        if (b !== null && b !== "") cnt3++;
        const cv = get("C" + r);
        if (S.isErr(cv) && !err) err = cv;
      }
    }
    assert.strictEqual(get("X1"), count, "COUNTIF " + cr);
    assert.strictEqual(get("X2"), sum, "SUMIF " + cr);
    assert.strictEqual(get("X3"), cnt3, "COUNTIFS " + cr);
    if (n) assert.strictEqual(get("X4"), sum / n, "AVERAGEIF " + cr); else assert.ok(isErr("#DIV/0!")(get("X4")), "AVERAGEIF " + cr);
    if (err) assert.ok(isErr(err.code)(get("X5")), "SUMIFS " + cr);
  }
});

test("range work past the budget is #NUM!, not a frozen page", () => {
  const cells = {};
  for (let r = 1; r <= 3000; r++) { cells["A" + r] = "x" + r; cells["CV" + r] = "x"; }
  for (let k = 0; k < 80; k++) cells["T!C" + (k + 1)] = '=COUNTIFS(S!A1:CV3000, "x*", S!A1:CV3000, "<>", S!A1:CV3000, "*")';
  const wb = quick(20000, () => book(cells, ["S", "T"]));
  const vals = Array.from({ length: 80 }, (_x, k) => wb.value("T", "C" + (k + 1)));
  assert.ok(vals.some(isErr("#NUM!")), "some went over the budget");
  assert.ok(vals.filter((v) => v === 6000).length >= 1, "the first ones were worked out");
});

test("NETWORKDAYS counts as walking day by day did", () => {
  const walk = (a, b, hol) => {
    const sign = b >= a ? 1 : -1; const [lo, hi] = sign > 0 ? [a, b] : [b, a]; let n = 0;
    for (let d = lo; d <= hi; d++) { const x = new Date(Date.UTC(1899, 11, 30) + d * 864e5).getUTCDay(); if (x !== 0 && x !== 6 && !hol.has(d)) n++; }
    return sign * n;
  };
  const start = S.dateSerial(2026, 1, 1);
  for (const [a, len, hol] of [[0, 0, []], [1, 6, []], [3, 13, [5, 6, 7]], [0, 400, [10, 11, 12, 13, 300]], [9, -30, [2, 4]], [2, 999, [0, 999, 500, 500]]]) {
    const s = start + a, e = start + a + len;
    const h = hol.map((x) => start + x);
    const cells = { A1: s, A2: e, C1: "=NETWORKDAYS(A1, A2, B1:B5)" };
    h.forEach((x, k) => { cells["B" + (k + 1)] = x; });
    assert.strictEqual(book(cells).value("S", "C1"), walk(s, e, new Set(h)), `${a} ${len}`);
  }
  assert.ok(quick(2000, () => { const cells = { A1: 1, A2: 900000 }; for (let k = 1; k <= 300; k++) cells["C" + k] = "=NETWORKDAYS(A1, A2)"; return book(cells); }));
});

// ---------------------------------------------------------------- DF3: wildcards
test("wildcard criteria match as Excel's do, without backtracking", () => {
  const wb = book({ A1: "apple", A2: "Apricot", A3: "a*b", A4: "banana", A5: "a?c", A6: "abc",
    B1: '=COUNTIF(A1:A6, "a*")', B2: '=COUNTIF(A1:A6, "a~*b")', B3: '=COUNTIF(A1:A6, "?????")', B4: '=COUNTIF(A1:A6, "*an*")',
    B5: '=COUNTIF(A1:A6, "a~?c")', B6: '=MATCH("ap*", A1:A6, 0)', B7: '=VLOOKUP("*nan*", A1:A6, 1, FALSE)', B8: '=COUNTIF(A1:A6, "A?C")' });
  assert.strictEqual(wb.value("S", "B1"), 5);
  assert.strictEqual(wb.value("S", "B2"), 1);
  assert.strictEqual(wb.value("S", "B3"), 1);
  assert.strictEqual(wb.value("S", "B4"), 1);
  assert.strictEqual(wb.value("S", "B5"), 1);
  assert.strictEqual(wb.value("S", "B6"), 1);
  assert.strictEqual(wb.value("S", "B7"), "banana");
  assert.strictEqual(wb.value("S", "B8"), 2);
  const long = "a".repeat(30000);
  const cells = { B1: '=COUNTIF(A1:A50, "*a*a*a*a*a*a*a*a*a*a*a*a*b")', B2: '=MATCH("*a*a*a*a*a*a*a*a*a*b", A1:A50, 0)' };
  for (let r = 1; r <= 50; r++) cells["A" + r] = long;
  const big = quick(3000, () => book(cells));
  assert.strictEqual(big.value("S", "B1"), 0);
  assert.ok(isErr("#N/A")(big.value("S", "B2")));
});

// ---------------------------------------------------------------- DFI3: MIN / MAX over many numbers
test("MIN and MAX over 150 000 numbers", () => {
  const cells = {};
  for (let r = 1; r <= 150000; r++) cells["A" + r] = r % 1000 - 500;
  cells.B1 = "=MAX(A1:A150000)"; cells.B2 = "=MIN(A:A)";
  const wb = book(cells);
  assert.strictEqual(wb.value("S", "B1"), 499);
  assert.strictEqual(wb.value("S", "B2"), -500);
  assert.strictEqual(S.maxOf([]), -Infinity);
});

// ---------------------------------------------------------------- DF4: Markdown
const OLD_HEAD = /^\s{0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$/;
const OLD_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
function oldFindClose(s, at, open, close) {
  let depth = 0;
  for (let i = at; i < s.length && i < at + 2000; i++) {
    if (s[i] === "\\") { i += 1; continue; }
    if (s[i] === open) depth += 1;
    else if (s[i] === close) { depth -= 1; if (depth === 0) return i; }
    else if (s[i] === "\n" && s[i + 1] === "\n") return -1;
  }
  return -1;
}

test("headings and table rules are read as before", () => {
  for (const line of ["# Title", "## Title ##", "###   Spaced   #  ", "# C#", "# #", "#", "#  ", "  # x", "####### no", "#no",
    "# a # b ##", "# \t tab\t#\t", "######", "    # four"]) {
    const m = OLD_HEAD.exec(line);
    const old = m && (m[2] !== undefined || /^\s{0,3}#{1,6}\s*$/.test(line)) ? { level: m[1].length, text: m[2] || "" } : null;
    assert.deepStrictEqual(Md.headOf(line), old, line);
  }
  for (const line of ["---", "|---|", "| --- | :-: |", ":--|--:", "||---", "|", "", "a|b", "--- x", " | - | ", "|-|-|-|", "-|", "|:", "::"]) {
    assert.strictEqual(Md.isTableSep(line), OLD_SEP.test(line), line);
  }
});

test("brackets pair up as scanning from each opener did", () => {
  const samples = ["[a](b)", "[[x]]", "[a [b] c](d (e) f)", "\\[a](b)", "[a\\]b](c)", "[a\n\nb](c)", "](([[", "[" + "x".repeat(2100) + "]",
    "[" + "x".repeat(1990) + "]", "a]b[c]d", "[\\\\](x)"];
  let rnd = 7;
  const next = () => { rnd = (rnd * 1103515245 + 12345) % 2147483648; return rnd; };
  for (let k = 0; k < 300; k++) {
    let s = "";
    const n = next() % 60;
    for (let j = 0; j < n; j++) s += "[]()\\\na"[next() % 7];
    samples.push(s);
  }
  for (const s of samples) {
    for (const [open, close] of [["[", "]"], ["(", ")"]]) {
      const pairs = Md.bracketPairs(s, open, close);
      // only openers the inline parser can reach (not escaped) are asked about
      for (let i = 0; i < s.length; i++) {
        if (s[i] === "\\") { i += 1; continue; }
        if (s[i] === open) assert.strictEqual(pairs.get(i) ?? -1, oldFindClose(s, i, open, close), JSON.stringify(s) + " @" + i);
      }
    }
  }
});

test("hostile Markdown renders quickly", () => {
  const lines = ["# " + " #".repeat(20000) + "x", "|" + " - |".repeat(20000) + "x", "*a ".repeat(6000), "[a".repeat(9000),
    "**a ".repeat(4000), "_a ".repeat(6000), "![a".repeat(6000), "`".repeat(30) + " " + "``a ".repeat(3000)];
  quick(4000, () => { for (const ln of lines) Md.parse(ln + "\n" + ln + "\n\n" + ln); });
  // a list item with thousands of continuation lines: its inline parts are worked out once
  const list = "- item\n" + Array(500).fill("  more *text* here").join("\n");
  const blocks = quick(2000, () => Md.parse(list));
  assert.strictEqual(blocks[0].items.length, 1);
  assert.ok(blocks[0].items[0].c.some((n) => n.t === "em"));
  // a paragraph past 20 000 characters is plain text (line breaks kept)
  const huge = Md.parse("**bold** " + "word ".repeat(5000) + "\nnext");
  assert.ok(huge[0].c.every((n) => n.t === "text" || n.t === "br"));
  assert.ok(huge[0].c.some((n) => n.t === "br"));
});

test("emphasis, code and links still work", () => {
  const c = Md.parseInline("a **b** *c* `d` [e](https://x.y) _f_ *g");
  assert.deepStrictEqual(c.map((n) => n.t), ["text", "strong", "text", "em", "text", "code", "text", "link", "text", "em", "text"]);
  assert.strictEqual(c[c.length - 1].v, " *g");
  assert.deepStrictEqual(Md.parseInline("`` ` `` and `x").map((n) => n.t), ["code", "text"]);
  assert.deepStrictEqual(Md.parseInline("*a *b* c*").map((n) => n.t), ["em", "text"]);
});

// ---------------------------------------------------------------- DFP6: the scan's straightening, without an array per pixel
test("warp gives what mapping each pixel through apply() gave", () => {
  const Scan = require("../../app/static/scanpdf.js");
  const w = 37, hgt = 29, data = new Uint8ClampedArray(w * hgt * 4);
  for (let i = 0; i < data.length; i++) data[i] = (i * 7919) % 256;
  const src = { width: w, height: hgt, data };
  const quad = [[2.5, 1], [33, 3.2], [35, 27], [1, 25.5]];
  const out = Scan.warp(src, quad, 20, 15);
  const H = Scan.homography([[0, 0], [19, 0], [19, 14], [0, 14]], quad);
  for (let y = 0; y < 15; y++) for (let x = 0; x < 20; x++) {
    const [fx, fy] = Scan.apply(H, x, y);
    const o = (y * 20 + x) * 4;
    const x0 = Math.floor(fx), y0 = Math.floor(fy), x1 = Math.min(x0 + 1, w - 1), y1 = Math.min(y0 + 1, hgt - 1);
    const ax = fx - x0, ay = fy - y0;
    for (let c = 0; c < 3; c++) {
      const top = data[(y0 * w + x0) * 4 + c] * (1 - ax) + data[(y0 * w + x1) * 4 + c] * ax;
      const bot = data[(y1 * w + x0) * 4 + c] * (1 - ax) + data[(y1 * w + x1) * 4 + c] * ax;
      const want = new Uint8ClampedArray(1);
      want[0] = top * (1 - ay) + bot * ay;
      assert.strictEqual(out.data[o + c], want[0], `${x},${y},${c}`);
    }
  }
});

// ---------------------------------------------------------------- DF5: an AI's answer never has live links
test("linksAsText shows a link's whole address as text", () => {
  const made = [];
  const doc = {
    createDocumentFragment() { return node("#frag"); },
    createElement(tag) { return node(tag); },
    createTextNode(t) { return { tag: "#text", textContent: String(t), children: [] }; },
  };
  function node(tag) {
    const n = { tag, children: [], attrs: {}, className: "",
      appendChild(c) { this.children.push(c); return c; }, setAttribute(k, v) { this.attrs[k] = v; },
      addEventListener() {}, get textContent() { return this.children.map((c) => c.textContent).join(""); } };
    made.push(n);
    return n;
  }
  const frag = Md.render(Md.parse("See [the shop](https://evil.example/login) now"), doc, { linksAsText: true });
  assert.ok(!made.some((n) => n.tag === "a"));
  assert.match(frag.textContent, /the shop \(https:\/\/evil\.example\/login\)/);
  made.length = 0;
  Md.render(Md.parse("See [the shop](https://evil.example/login)"), doc, {});
  assert.ok(made.some((n) => n.tag === "a"));
});
