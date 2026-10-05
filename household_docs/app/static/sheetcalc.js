"use strict";
/* Household Docs — the sheet formula engine (SPEC §8.2, §17.10). No DOM; tested with Node
   (tests/js/sheetcalc.test.js). Formulas are tokenised and parsed into a tree, then compiled into closures —
   never eval (the page's CSP forbids it anyway).

     SheetCalc.workbook({today})          a workbook: tabs, cells, the dependency graph, recalculation
       wb.setTabs(names)                  the tab names in order (formulas are recompiled)
       wb.set(tab, ref, raw)              one cell (raw: number | string | boolean | null; "=…" is a formula)
       wb.recalc()                        everything (topological order, cycles → #CYCLE!)
       wb.update([[tab, ref, raw], …])    set cells and recalculate only what depends on them → changed keys
       wb.value(tab, ref) / wb.raw(tab, ref) / wb.isFormula(tab, ref)
     SheetCalc.parse(text)                → the tree of a formula (without the "="), or throws CalcSyntax
     SheetCalc.shiftFormula(f, dc, dr)    fill / paste: relative references move, $ parts stay (#REF! off the edge)
     SheetCalc.adjustFormula(f, opt)      insert / delete rows or columns ({tab, home, axis, at, count})
     SheetCalc.renameTab(f, old, new)     rewrites 'Old'!A1 → 'New'!A1
     SheetCalc.format(value, fmt, opts)   what a cell shows ({text, negative})
     SheetCalc.parseInput(text, style?)   what typing means: {v, f?, d?} (numbers, 12%, dates, TRUE, 'text); style:
                                          the person's number style (en 1,234.56 · de 1.234,56 · in 12,34,567.89)
     SheetCalc.FUNCTIONS                  name → {args, desc, example} (the ƒ list and autocomplete)

   Values: number, string, boolean, null (an empty cell), or an error (SheetCalc.ERR[code]). Dates are serial
   numbers as in Excel (1 = 1900-01-01, with Excel's 29 Feb 1900). */
(function (root) {
  // ===================================================================== errors
  class CalcError { constructor(code, why) { this.code = code; this.why = why; } toString() { return this.code; } }
  const ERR = {};
  const WHY = {
    "#DIV/0!": "Divided by zero (or an average of nothing).",
    "#REF!": "Refers to a cell, range or tab that isn't there (deleted, or off the edge of the sheet).",
    "#NAME?": "A function or name the app doesn't know — check the spelling.",
    "#VALUE!": "The wrong kind of value: text where a number is needed, or a range where one cell is needed.",
    "#CYCLE!": "The formula depends on itself (directly or through other cells).",
    "#N/A": "Not found — a lookup didn't find the value.",
    "#NUM!": "A number out of range (too big, or no answer).",
  };
  for (const k of Object.keys(WHY)) ERR[k] = new CalcError(k, WHY[k]);
  const isErr = (v) => v instanceof CalcError;
  class CalcSyntax extends Error {}

  // ===================================================================== addresses
  const MAXC = 16384, MAXR = 1048576;
  function colName(c) { let s = ""; c += 1; while (c > 0) { const m = (c - 1) % 26; s = String.fromCharCode(65 + m) + s; c = Math.floor((c - 1) / 26); } return s; }
  function colIndex(name) { let n = 0; for (const ch of name.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64); return n - 1; }
  function addr(c, r) { return colName(c) + (r + 1); }
  const CELL_RE = /^(\$?)([A-Za-z]{1,3})(\$?)(\d{1,7})$/;
  function parseRef(s) {
    const m = CELL_RE.exec(s);
    if (!m) return null;
    const c = colIndex(m[2]), r = parseInt(m[4], 10) - 1;
    if (c >= MAXC || r < 0 || r >= MAXR) return null;
    return { c, r, ac: !!m[1], ar: !!m[3] };
  }
  function parseRange(s) {        // "A1:C9" | "A1" | "B:B" (no tab) → {c1, r1, c2, r2}
    const parts = String(s).trim().split(":");
    if (parts.length === 1) { const a = parseRef(parts[0]); return a ? { c1: a.c, r1: a.r, c2: a.c, r2: a.r } : null; }
    if (parts.length !== 2) return null;
    const a = parseRef(parts[0]), b = parseRef(parts[1]);
    if (a && b) return { c1: Math.min(a.c, b.c), r1: Math.min(a.r, b.r), c2: Math.max(a.c, b.c), r2: Math.max(a.r, b.r) };
    const ca = /^\$?([A-Za-z]{1,3})$/.exec(parts[0]), cb = /^\$?([A-Za-z]{1,3})$/.exec(parts[1]);
    if (ca && cb) { const x = colIndex(ca[1]), y = colIndex(cb[1]); return { c1: Math.min(x, y), r1: 0, c2: Math.max(x, y), r2: MAXR - 1 }; }
    return null;
  }
  const PLAIN_TAB = /^[A-Za-z_À-￿][A-Za-z0-9_.À-￿]*$/;
  function tabPrefix(name) {
    if (name == null) return "";
    const plain = PLAIN_TAB.test(name) && !/^[A-Za-z]{1,3}\d+$/.test(name) && !/^(TRUE|FALSE)$/i.test(name) && !/^R\d*C\d*$/i.test(name);
    return (plain ? name : "'" + name.replace(/'/g, "''") + "'") + "!";
  }

  // ===================================================================== tokens
  // {t, s (source text), …}: num v · str v · bool v · err v · ref {sheet, a:{c,r,ac,ar}, b?, cols?, rows?} ·
  // func name · name v · op v · ( ) , · ws
  const ERR_LIT = ["#DIV/0!", "#REF!", "#NAME?", "#VALUE!", "#CYCLE!", "#N/A", "#NUM!", "#NULL!", "#GETTING_DATA"];
  function tokenize(src) {
    const out = [];
    let i = 0;
    const n = src.length;
    while (i < n) {
      const ch = src[i];
      const start = i;
      if (/\s/.test(ch)) { while (i < n && /\s/.test(src[i])) i++; out.push({ t: "ws", s: src.slice(start, i) }); continue; }
      if (ch === '"') {
        let v = ""; i++;
        for (;;) {
          if (i >= n) throw new CalcSyntax("A text in quotes isn't closed.");
          if (src[i] === '"') { if (src[i + 1] === '"') { v += '"'; i += 2; continue; } i++; break; }
          v += src[i++];
        }
        out.push({ t: "str", v, s: src.slice(start, i) });
        continue;
      }
      if (ch === "#") {
        const lit = ERR_LIT.find((e) => src.slice(i, i + e.length).toUpperCase() === e);
        if (!lit) throw new CalcSyntax("Unknown error value.");
        i += lit.length;
        out.push({ t: "err", v: lit === "#NULL!" || lit === "#GETTING_DATA" ? "#VALUE!" : lit, s: src.slice(start, i) });
        continue;
      }
      // a sheet prefix: 'Name'! or Name!
      let sheet = null;
      if (ch === "'") {
        let j = i + 1, v = "";
        for (;;) {
          if (j >= n) throw new CalcSyntax("A tab name in quotes isn't closed.");
          if (src[j] === "'") { if (src[j + 1] === "'") { v += "'"; j += 2; continue; } j++; break; }
          v += src[j++];
        }
        if (src[j] !== "!") throw new CalcSyntax("A tab name must be followed by ! and a cell.");
        sheet = v; i = j + 1;
      } else {
        const m = /^[A-Za-z_À-￿][A-Za-z0-9_.À-￿]*!/.exec(src.slice(i));
        if (m) { sheet = m[0].slice(0, -1); i += m[0].length; }
      }
      const rest = src.slice(i);
      let m = /^(\$?[A-Za-z]{1,3}\$?\d{1,7})(?::(\$?[A-Za-z]{1,3}\$?\d{1,7}))?(?![A-Za-z0-9_.(!])/.exec(rest);
      if (m) {
        const a = parseRef(m[1]), b = m[2] ? parseRef(m[2]) : null;
        if (a && (!m[2] || b)) {
          i += m[0].length;
          out.push({ t: "ref", sheet, a, b, s: src.slice(start, i) });
          continue;
        }
      }
      m = /^(\$?)([A-Za-z]{1,3}):(\$?)([A-Za-z]{1,3})(?![A-Za-z0-9_.(!])/.exec(rest);
      if (m) {
        i += m[0].length;
        out.push({ t: "ref", sheet, cols: true, a: { c: colIndex(m[2]), r: 0, ac: !!m[1], ar: true }, b: { c: colIndex(m[4]), r: MAXR - 1, ac: !!m[3], ar: true }, s: src.slice(start, i) });
        continue;
      }
      m = /^(\$?)(\d{1,7}):(\$?)(\d{1,7})(?![A-Za-z0-9_.(!])/.exec(rest);
      if (m && sheet !== null) {
        i += m[0].length;
        out.push({ t: "ref", sheet, rows: true, a: { c: 0, r: parseInt(m[2], 10) - 1, ac: true, ar: !!m[1] }, b: { c: MAXC - 1, r: parseInt(m[4], 10) - 1, ac: true, ar: !!m[3] }, s: src.slice(start, i) });
        continue;
      }
      if (sheet !== null) throw new CalcSyntax("A tab name must be followed by a cell or range.");
      m = /^(\d+:\d+)(?![A-Za-z0-9_.(])/.exec(rest);
      if (m) {
        const [x, y] = m[1].split(":").map((v) => parseInt(v, 10) - 1);
        i += m[0].length;
        out.push({ t: "ref", sheet: null, rows: true, a: { c: 0, r: x, ac: true, ar: false }, b: { c: MAXC - 1, r: y, ac: true, ar: false }, s: src.slice(start, i) });
        continue;
      }
      m = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(rest);
      if (m) { i += m[0].length; out.push({ t: "num", v: parseFloat(m[0]), s: m[0] }); continue; }
      m = /^[A-Za-z_À-￿][A-Za-z0-9_.À-￿]*/.exec(rest);
      if (m) {
        i += m[0].length;
        let k = i; while (k < n && src[k] === " ") k++;
        const word = m[0];
        if (src[k] === "(") out.push({ t: "func", name: word.toUpperCase().replace(/^_XLFN\.|^_XLWS\./, ""), s: word });
        else if (/^(TRUE|FALSE)$/i.test(word)) out.push({ t: "bool", v: word.toUpperCase() === "TRUE", s: word });
        else out.push({ t: "name", v: word, s: word });
        continue;
      }
      const two = src.slice(i, i + 2);
      if (two === "<>" || two === "<=" || two === ">=") { i += 2; out.push({ t: "op", v: two, s: two }); continue; }
      if ("+-*/^&=<>%".includes(ch)) { i++; out.push({ t: "op", v: ch, s: ch }); continue; }
      if (ch === "(" || ch === ")" || ch === ",") { i++; out.push({ t: ch, s: ch }); continue; }
      if (ch === ";") { i++; out.push({ t: ",", s: ch }); continue; }
      throw new CalcSyntax(`Unexpected “${ch}”.`);
    }
    return out;
  }

  // ===================================================================== parser
  // precedence (low → high): comparisons · & · + - · * / · ^ · % · unary - +
  function parse(text) {
    const toks = tokenize(String(text)).filter((t) => t.t !== "ws");
    let p = 0;
    const peek = () => toks[p];
    const isOp = (v) => peek() && peek().t === "op" && v.includes(peek().v);
    function bin(next, ops) {
      return function () {
        let a = next();
        while (isOp(ops)) { const op = toks[p++].v; a = { k: "bin", op, a, b: next() }; }
        return a;
      };
    }
    function primary() {
      const t = toks[p++];
      if (!t) throw new CalcSyntax("The formula ends too soon.");
      switch (t.t) {
        case "num": return { k: "num", v: t.v };
        case "str": return { k: "str", v: t.v };
        case "bool": return { k: "bool", v: t.v };
        case "err": return { k: "err", v: t.v };
        case "name": return { k: "name", v: t.v };
        case "ref": {
          if (!t.b) return { k: "ref", sheet: t.sheet, c: t.a.c, r: t.a.r };
          return { k: "range", sheet: t.sheet, c1: Math.min(t.a.c, t.b.c), r1: Math.min(t.a.r, t.b.r), c2: Math.max(t.a.c, t.b.c), r2: Math.max(t.a.r, t.b.r), whole: !!(t.cols || t.rows) };
        }
        case "(": {
          const e = expr();
          if (!peek() || peek().t !== ")") throw new CalcSyntax("A bracket isn't closed.");
          p++;
          return { k: "paren", a: e };
        }
        case "func": {
          p++;    // the "("
          const args = [];
          if (peek() && peek().t === ")") { p++; return { k: "fn", name: t.name, args }; }
          for (;;) {
            if (peek() && (peek().t === "," || peek().t === ")")) args.push({ k: "missing" });
            else args.push(expr());
            const s = toks[p++];
            if (!s) throw new CalcSyntax(`${t.name}( isn't closed.`);
            if (s.t === ")") break;
            if (s.t !== ",") throw new CalcSyntax("Expected , or ) in " + t.name + "(.");
          }
          if (args.length > 255) throw new CalcSyntax("Too many arguments.");
          return { k: "fn", name: t.name, args };
        }
        case "op":
          if (t.v === "-" || t.v === "+") return { k: "un", op: t.v, a: unary() };
          throw new CalcSyntax(`Unexpected “${t.v}”.`);
        default:
          throw new CalcSyntax(`Unexpected “${t.s}”.`);
      }
    }
    function unary() {
      if (isOp("-+")) { const op = toks[p++].v; return { k: "un", op, a: unary() }; }
      return primary();
    }
    function percent() { let a = unary(); while (isOp("%")) { p++; a = { k: "pct", a }; } return a; }
    const power = bin(percent, ["^"]);
    const mul = bin(power, ["*", "/"]);
    const add = bin(mul, ["+", "-"]);
    const cat = bin(add, ["&"]);
    const expr = bin(cat, ["=", "<>", "<", ">", "<=", ">="]);
    if (!toks.length) throw new CalcSyntax("The formula is empty.");
    const tree = expr();
    if (p < toks.length) throw new CalcSyntax(`Unexpected “${toks[p].s}”.`);
    return tree;
  }

  // ===================================================================== text rewriting (fill, insert/delete, rename)
  function refText(tok, a, b) {
    const one = (x) => (x.ac ? "$" : "") + colName(x.c) + (x.ar ? "$" : "") + (x.r + 1);
    const pre = tok.sheet !== null ? tabPrefix(tok.sheet) : "";
    if (tok.cols) return pre + (a.ac ? "$" : "") + colName(a.c) + ":" + (b.ac ? "$" : "") + colName(b.c);
    if (tok.rows) return pre + (a.ar ? "$" : "") + (a.r + 1) + ":" + (b.ar ? "$" : "") + (b.r + 1);
    return pre + one(a) + (b ? ":" + one(b) : "");
  }
  function rewrite(formula, fn) {
    const lead = formula.startsWith("=") ? "=" : "";
    let toks;
    try { toks = tokenize(lead ? formula.slice(1) : formula); } catch (e) { return formula; }
    return lead + toks.map((t) => (t.t === "ref" ? fn(t) : t.s)).join("");
  }
  /** Fill / copy-paste: relative parts move by (dc, dr); $ parts stay. Off the sheet → #REF!. With
   *  {sameTabOnly: true} (sorting) references to other tabs don't move. */
  function shiftFormula(formula, dc, dr, opts) {
    const sameTabOnly = !!(opts && opts.sameTabOnly);       // sorting: references to other tabs stay put
    return rewrite(formula, (t) => {
      if (sameTabOnly && t.sheet !== null) return t.s;
      const mv = (x) => ({ c: x.ac || t.rows ? x.c : x.c + dc, r: x.ar || t.cols ? x.r : x.r + dr, ac: x.ac, ar: x.ar });
      const a = mv(t.a), b = t.b ? mv(t.b) : null;
      const bad = (x) => x.c < 0 || x.r < 0 || x.c >= MAXC || x.r >= MAXR;
      if (bad(a) || (b && bad(b))) return (t.sheet !== null ? tabPrefix(t.sheet) : "") + "#REF!";
      return refText(t, a, b);
    });
  }
  /** Insert (count > 0) or delete (count < 0) rows or columns at `at` (0-based) of tab `tab`; the formula lives
   *  on tab `home`. References into that tab move; a deleted cell becomes #REF!, a range shrinks. */
  function adjustFormula(formula, opt) {
    const { tab, home, axis, at, count } = opt;
    const same = (s) => (s === null ? home : s).toLowerCase() === String(tab).toLowerCase();
    const key = axis === "row" ? "r" : "c";
    return rewrite(formula, (t) => {
      if (!same(t.sheet)) return t.s;
      if ((axis === "row" && t.cols) || (axis === "col" && t.rows)) return t.s;
      const a = Object.assign({}, t.a), b = t.b ? Object.assign({}, t.b) : null;
      const pre = t.sheet !== null ? tabPrefix(t.sheet) : "";
      if (count > 0) {
        if (a[key] >= at) a[key] += count;
        if (b && b[key] >= at) b[key] += count;
      } else {
        const del = -count, end = at + del;           // deleted: [at, end)
        if (!b) {
          if (a[key] >= at && a[key] < end) return pre + "#REF!";
          if (a[key] >= end) a[key] -= del;
        } else {
          let lo = a[key], hi = b[key];
          if (lo >= at && hi < end) return pre + "#REF!";
          const nlo = lo >= end ? lo - del : (lo >= at ? at : lo);
          const nhi = hi >= end ? hi - del : (hi >= at ? at - 1 : hi);
          lo = nlo; hi = nhi;
          a[key] = lo; b[key] = hi;
        }
      }
      const lim = key === "r" ? MAXR : MAXC;
      if (a[key] >= lim || (b && b[key] >= lim)) return pre + "#REF!";
      return refText(t, a, b);
    });
  }
  /** A tab was renamed: 'Old'!A1 → 'New'!A1 (case doesn't matter, as in Excel). */
  function renameTab(formula, oldName, newName) {
    const o = String(oldName).toLowerCase();
    return rewrite(formula, (t) => (t.sheet !== null && t.sheet.toLowerCase() === o ? refText(Object.assign({}, t, { sheet: newName }), t.a, t.b) : t.s));
  }
  /** Every function name used in a formula (to tell unknown ones apart). */
  function functionsIn(formula) {
    try { return tokenize(formula.replace(/^=/, "")).filter((t) => t.t === "func").map((t) => t.name); } catch (e) { return []; }
  }

  // ===================================================================== values
  function numText(n) {
    if (!isFinite(n)) return "#NUM!";
    if (n === 0) return "0";
    const a = Math.abs(n);
    if (a >= 1e21 || a < 1e-9) return String(Number(n.toPrecision(15))).replace("e+", "E+").replace("e-", "E-");
    let s = String(Number(n.toPrecision(15)));
    if (/e/.test(s)) s = Number(s).toFixed(20).replace(/\.?0+$/, "");
    return s;
  }
  function toText(v) {
    if (v === null || v === undefined) return "";
    if (typeof v === "number") return numText(v);
    if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
    return String(v);
  }
  const NUM_RE = /^[+-]?(\d{1,3}(,\d{3})+|\d*)(\.\d*)?([eE][+-]?\d+)?$/;
  function textToNumber(s) {
    let t = String(s).trim();
    if (!t) return null;
    let pct = false;
    if (t.endsWith("%")) { pct = true; t = t.slice(0, -1).trim(); }
    if (NUM_RE.test(t) && /\d/.test(t)) {
      const n = parseFloat(t.replace(/,/g, ""));
      return pct ? n / 100 : n;
    }
    const d = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t);
    if (d && !pct) { const v = dateSerial(+d[1], +d[2], +d[3]); return isErr(v) ? null : v; }
    return null;
  }
  function toNum(v) {
    if (typeof v === "number") return v;
    if (v === null || v === undefined) return 0;
    if (typeof v === "boolean") return v ? 1 : 0;
    if (isErr(v)) return v;
    const n = textToNumber(v);
    return n === null ? ERR["#VALUE!"] : n;
  }
  function toBool(v) {
    if (typeof v === "boolean") return v;
    if (typeof v === "number") return v !== 0;
    if (v === null || v === undefined) return false;
    if (isErr(v)) return v;
    const u = String(v).toUpperCase();
    if (u === "TRUE") return true;
    if (u === "FALSE") return false;
    return ERR["#VALUE!"];
  }
  function typeRank(v) { return typeof v === "number" ? 0 : typeof v === "string" ? 1 : 2; }
  /** Excel's ordering: numbers < text < booleans; text without case; empty = 0 / "" / FALSE. */
  function compare(a, b) {
    if (a === null || a === undefined) a = typeof b === "string" ? "" : typeof b === "boolean" ? false : 0;
    if (b === null || b === undefined) b = typeof a === "string" ? "" : typeof a === "boolean" ? false : 0;
    const ra = typeRank(a), rb = typeRank(b);
    if (ra !== rb) return ra < rb ? -1 : 1;
    if (ra === 1) { const x = a.toLowerCase(), y = b.toLowerCase(); return x < y ? -1 : x > y ? 1 : 0; }
    const x = +a, y = +b;
    return x < y ? -1 : x > y ? 1 : 0;
  }

  // ===================================================================== dates (Excel serials)
  const DAY = 86400000;
  const EPOCH = Date.UTC(1899, 11, 30);
  function dateSerial(y, m, d) {
    y = Math.trunc(y); m = Math.trunc(m); d = Math.trunc(d);
    if (y < 0 || y > 9999) return ERR["#NUM!"];
    if (y < 1900) y += 1900;
    const t = Date.UTC(y, m - 1, 1) + (d - 1) * DAY;
    let s = Math.round((t - EPOCH) / DAY);
    if (s < 61) s -= 1;                      // Excel counts a 29 Feb 1900 that never was
    if (s < 0) return ERR["#NUM!"];
    return s;
  }
  function serialParts(s) {
    s = Math.floor(s);
    if (s === 60) return { y: 1900, m: 2, d: 29, wd: 3 };
    const dt = new Date(EPOCH + (s < 60 ? s + 1 : s) * DAY);
    return { y: dt.getUTCFullYear(), m: dt.getUTCMonth() + 1, d: dt.getUTCDate(), wd: dt.getUTCDay() };
  }
  function todaySerial(now) {
    const d = now || new Date();
    return dateSerial(d.getFullYear(), d.getMonth() + 1, d.getDate());
  }

  // ===================================================================== ranges
  class Range {
    constructor(ctx, tab, c1, r1, c2, r2) { this.ctx = ctx; this.tab = tab; this.c1 = c1; this.r1 = r1; this.c2 = c2; this.r2 = r2; }
    get rows() { return this.r2 - this.r1 + 1; }
    get cols() { return this.c2 - this.c1 + 1; }
    at(i, j) { return this.ctx.cell(this.tab, this.c1 + j, this.r1 + i); }
    /** Filled cells only, row by row: fn(value, i, j). */
    each(fn) {
      const t = this.tab;
      if (this.rows * this.cols <= 4096 || !t) {
        for (let i = 0; i < this.rows; i++) for (let j = 0; j < this.cols; j++) { const v = this.at(i, j); if (v !== null) fn(v, i, j); }
        return;
      }
      const hits = [];
      for (const [key, cell] of t.cells) {
        const r = Math.floor(key / MAXC), c = key % MAXC;
        if (r >= this.r1 && r <= this.r2 && c >= this.c1 && c <= this.c2) hits.push([r, c, cell]);
      }
      hits.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
      for (const [r, c] of hits) { const v = this.ctx.cell(t, c, r); if (v !== null) fn(v, r - this.r1, c - this.c1); }
    }
    /** Every cell (empties too), row by row — capped to the used part of the tab. */
    list() {
      const out = [];
      for (let i = 0; i < this.rows; i++) for (let j = 0; j < this.cols; j++) out.push(this.at(i, j));
      return out;
    }
  }
  const isRange = (v) => v instanceof Range;

  // ===================================================================== criteria (SUMIF, COUNTIFS …)
  function wildRe(s) {
    let re = "";
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (ch === "~" && i + 1 < s.length && "*?~".includes(s[i + 1])) { re += "\\" + s[++i]; continue; }
      if (ch === "*") re += "[\\s\\S]*"; else if (ch === "?") re += "[\\s\\S]"; else re += ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
    return new RegExp("^" + re + "$", "i");
  }
  function criterion(c) {
    if (isErr(c)) return () => false;
    if (typeof c === "number" || typeof c === "boolean") {
      return (v) => (typeof v === typeof c && v === c) || (typeof c === "number" && typeof v === "string" && textToNumber(v) === c);
    }
    if (c === null) c = "";
    let s = String(c), op = "=";
    const m = /^(<=|>=|<>|<|>|=)/.exec(s);
    if (m) { op = m[1]; s = s.slice(m[1].length); }
    const n = s.trim() !== "" ? textToNumber(s) : null;
    const lower = s.toUpperCase();
    if (n !== null) {
      const cmp = { "=": (x) => x === n, "<>": (x) => x !== n, "<": (x) => x < n, ">": (x) => x > n, "<=": (x) => x <= n, ">=": (x) => x >= n }[op];
      return (v) => {
        if (typeof v === "number") return cmp(v);
        if (op === "=") return typeof v === "string" && textToNumber(v) === n;
        return op === "<>";
      };
    }
    if (lower === "TRUE" || lower === "FALSE") {
      const b = lower === "TRUE";
      return (v) => (op === "<>" ? v !== b : op === "=" ? v === b : false);
    }
    if (op === "=" || op === "<>") {
      if (s === "") return op === "=" ? (v) => v === null || v === "" : (v) => v !== null && v !== "";
      const re = /[*?~]/.test(s) ? wildRe(s) : null;
      const eq = (v) => typeof v === "string" && (re ? re.test(v) : v.toLowerCase() === s.toLowerCase());
      return op === "=" ? eq : (v) => !eq(v);
    }
    const cmpT = { "<": (x) => x < 0, ">": (x) => x > 0, "<=": (x) => x <= 0, ">=": (x) => x >= 0 }[op];
    return (v) => typeof v === "string" && cmpT(compare(v, s));
  }

  // ===================================================================== functions
  const FN = {};
  const FUNCTIONS = {};
  function def(name, args, desc, example, impl, opts = {}) {
    FN[name] = Object.assign({ impl }, opts);
    FUNCTIONS[name] = { args, desc, example };
  }
  // helpers given to implementations: ev(i) scalar value, arg(i) raw (range or value), has(i)
  function numbersOf(args, ctx, { countText = false } = {}) {
    const out = [];
    for (const a of args) {
      if (a.k === "missing") continue;
      const v = ctx.evalRaw(a);
      if (isRange(v)) {
        let e = null;
        v.each((x) => { if (e) return; if (isErr(x)) e = x; else if (typeof x === "number") out.push(x); });
        if (e) return e;
      } else if (isErr(v)) return v;
      else if (a.k === "ref") { if (typeof v === "number") out.push(v); }
      else if (v === null) continue;
      else {
        const n = toNum(v);
        if (isErr(n)) { if (countText) continue; return n; }
        out.push(n);
      }
    }
    return out;
  }
  const NUMS = (fn) => (args, ctx) => { const xs = numbersOf(args, ctx); return isErr(xs) ? xs : fn(xs); };
  def("SUM", "number1, [number2], …", "Adds the numbers (text in ranges is skipped).", "=SUM(B2:B10)", NUMS((xs) => xs.reduce((a, b) => a + b, 0)));
  def("AVERAGE", "number1, [number2], …", "The average of the numbers.", "=AVERAGE(B2:B10)", NUMS((xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : ERR["#DIV/0!"])));
  def("MIN", "number1, [number2], …", "The smallest number (0 if none).", "=MIN(B2:B10)", NUMS((xs) => (xs.length ? Math.min(...xs) : 0)));
  def("MAX", "number1, [number2], …", "The largest number (0 if none).", "=MAX(B2:B10)", NUMS((xs) => (xs.length ? Math.max(...xs) : 0)));
  def("MEDIAN", "number1, [number2], …", "The middle number.", "=MEDIAN(B2:B10)", NUMS((xs) => {
    if (!xs.length) return ERR["#NUM!"];
    const s = xs.slice().sort((a, b) => a - b), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }));
  def("COUNT", "value1, [value2], …", "How many numbers.", "=COUNT(B2:B10)", (args, ctx) => {
    let n = 0;
    for (const a of args) {
      if (a.k === "missing") continue;
      const v = ctx.evalRaw(a);
      if (isRange(v)) v.each((x) => { if (typeof x === "number") n++; });
      else if (a.k === "ref") { if (typeof v === "number") n++; }
      else if (typeof v === "number" || typeof v === "boolean" || (typeof v === "string" && textToNumber(v) !== null)) n++;
    }
    return n;
  });
  def("COUNTA", "value1, [value2], …", "How many cells aren't empty.", "=COUNTA(A2:A50)", (args, ctx) => {
    let n = 0;
    for (const a of args) {
      if (a.k === "missing") continue;
      const v = ctx.evalRaw(a);
      if (isRange(v)) v.each(() => { n++; });
      else if (v !== null || a.k !== "ref") n++;
    }
    return n;
  });
  function roundTo(x, d, mode) {
    const m = Math.pow(10, d);
    const v = Number((Math.abs(x) * m).toPrecision(15));
    const r = mode === "up" ? Math.ceil(v) : mode === "down" ? Math.floor(v) : Math.round(v);
    const out = (Math.sign(x) || 1) * r / m;
    return Number(out.toPrecision(15));
  }
  const num2 = (fn) => (args, ctx) => {
    if (args.length < 1) return ERR["#VALUE!"];
    const x = ctx.num(args[0]); if (isErr(x)) return x;
    const d = args.length > 1 ? ctx.num(args[1]) : 0; if (isErr(d)) return d;
    return fn(x, Math.trunc(d));
  };
  def("ROUND", "number, digits", "Rounds half away from zero.", "=ROUND(B2, 2)", num2((x, d) => roundTo(x, d, "near")), { min: 1, max: 2 });
  def("ROUNDUP", "number, digits", "Rounds away from zero.", "=ROUNDUP(B2, 0)", num2((x, d) => roundTo(x, d, "up")), { min: 1, max: 2 });
  def("ROUNDDOWN", "number, digits", "Rounds towards zero.", "=ROUNDDOWN(B2, 0)", num2((x, d) => roundTo(x, d, "down")), { min: 1, max: 2 });
  def("ABS", "number", "The number without its sign.", "=ABS(B2)", (args, ctx) => { const x = ctx.num(args[0]); return isErr(x) ? x : Math.abs(x); }, { min: 1, max: 1 });
  def("IF", "test, value if true, [value if false]", "One value or another.", '=IF(B2>100, "Over", "OK")', (args, ctx) => {
    const t = ctx.bool(args[0]);
    if (isErr(t)) return t;
    const a = t ? args[1] : args[2];
    if (!a) return t ? true : false;
    if (a.k === "missing") return 0;
    return ctx.ev(a);
  }, { min: 1, max: 3 });
  function logical(args, ctx, init, step) {
    let acc = init, any = false;
    for (const a of args) {
      if (a.k === "missing") continue;
      const v = ctx.evalRaw(a);
      let e = null;
      const one = (x, fromRef) => {
        if (e) return;
        if (isErr(x)) { e = x; return; }
        if (typeof x === "boolean" || typeof x === "number") { acc = step(acc, typeof x === "boolean" ? x : x !== 0); any = true; return; }
        if (!fromRef && typeof x === "string") { const b = toBool(x); if (isErr(b)) { e = b; return; } acc = step(acc, b); any = true; }
      };
      if (isRange(v)) v.each((x) => one(x, true)); else one(v, a.k === "ref");
      if (e) return e;
    }
    return any ? acc : ERR["#VALUE!"];
  }
  def("AND", "test1, [test2], …", "TRUE when every test is true.", "=AND(B2>0, C2>0)", (args, ctx) => logical(args, ctx, true, (a, b) => a && b), { min: 1 });
  def("OR", "test1, [test2], …", "TRUE when any test is true.", '=OR(A2="Car", A2="Bus")', (args, ctx) => logical(args, ctx, false, (a, b) => a || b), { min: 1 });
  def("NOT", "test", "The opposite.", "=NOT(B2>100)", (args, ctx) => { const b = ctx.bool(args[0]); return isErr(b) ? b : !b; }, { min: 1, max: 1 });
  def("IFERROR", "value, value if error", "The value, or another when it's an error.", "=IFERROR(B2/C2, 0)", (args, ctx) => {
    const v = ctx.ev(args[0]);
    if (isErr(v)) return args[1].k === "missing" ? 0 : ctx.ev(args[1]);
    return v;
  }, { min: 2, max: 2 });

  // --- conditional sums and counts
  function rangeArg(a, ctx) {
    if (a.k === "ref" && a.rtab) return new Range(ctx, a.rtab, a.c, a.r, a.c, a.r);
    if (a.k === "paren") return rangeArg(a.a, ctx);
    const v = ctx.evalRaw(a);
    if (isRange(v) || isErr(v)) return v;
    return { scalar: v };
  }
  function sameShape(base, other, ctx) {      // SUMIF's sum range: same size as the range, from its top-left
    return new Range(ctx, other.tab, other.c1, other.r1, other.c1 + base.cols - 1, other.r1 + base.rows - 1);
  }
  function ifsCore(ctx, pairs, valueRange, need) {
    const tests = [];
    let shape = null;
    for (const [ra, ca] of pairs) {
      const r = rangeArg(ra, ctx);
      if (isErr(r)) return r;
      if (!isRange(r)) return ERR["#VALUE!"];
      if (shape && (r.rows !== shape.rows || r.cols !== shape.cols)) return ERR["#VALUE!"];
      shape = shape || r;
      tests.push([r, criterion(ctx.ev(ca))]);
    }
    let vr = null;
    if (valueRange) {
      vr = rangeArg(valueRange.arg, ctx);
      if (isErr(vr)) return vr;
      if (!isRange(vr)) return ERR["#VALUE!"];
      if (valueRange.resize) vr = sameShape(shape, vr, ctx);
      else if (vr.rows !== shape.rows || vr.cols !== shape.cols) return ERR["#VALUE!"];
    }
    const hits = [];
    // walk the cells of the first range (only its filled cells can match a non-empty criterion)
    const rows = Math.min(shape.rows, Math.max(0, (ctx.lastRow(shape.tab) - shape.r1) + 1));
    const cols = shape.cols;
    const matchesEmpty = tests.every(([, f]) => f(null));
    const lim = matchesEmpty ? shape.rows : rows;
    for (let i = 0; i < lim; i++) {
      for (let j = 0; j < cols; j++) {
        let ok = true;
        for (const [r, f] of tests) { if (!f(r.at(i, j))) { ok = false; break; } }
        if (ok) hits.push(vr ? vr.at(i, j) : null);
      }
    }
    return hits;
  }
  function sumOf(hits) { let s = 0; for (const v of hits) { if (isErr(v)) return v; if (typeof v === "number") s += v; } return s; }
  function avgOf(hits) { let s = 0, n = 0; for (const v of hits) { if (isErr(v)) return v; if (typeof v === "number") { s += v; n++; } } return n ? s / n : ERR["#DIV/0!"]; }
  def("SUMIF", "range, criteria, [sum range]", "Adds the cells whose row matches.", '=SUMIF(A2:A20, "Food", B2:B20)', (args, ctx) => {
    const h = ifsCore(ctx, [[args[0], args[1]]], { arg: args[2] && args[2].k !== "missing" ? args[2] : args[0], resize: true });
    return isErr(h) ? h : sumOf(h);
  }, { min: 2, max: 3 });
  def("COUNTIF", "range, criteria", "How many cells match.", '=COUNTIF(B2:B20, ">100")', (args, ctx) => {
    const h = ifsCore(ctx, [[args[0], args[1]]], null, "count");
    return isErr(h) ? h : h.length;
  }, { min: 2, max: 2 });
  def("AVERAGEIF", "range, criteria, [average range]", "The average of the matching cells.", '=AVERAGEIF(A2:A20, "Food", B2:B20)', (args, ctx) => {
    const h = ifsCore(ctx, [[args[0], args[1]]], { arg: args[2] && args[2].k !== "missing" ? args[2] : args[0], resize: true });
    return isErr(h) ? h : avgOf(h);
  }, { min: 2, max: 3 });
  const pairsFrom = (args, from) => { const ps = []; for (let i = from; i + 1 < args.length; i += 2) ps.push([args[i], args[i + 1]]); return ps; };
  def("SUMIFS", "sum range, range1, criteria1, …", "Adds the cells whose rows match every test.", '=SUMIFS(C2:C50, A2:A50, "Car", B2:B50, ">=2026-01-01")', (args, ctx) => {
    if (args.length < 3 || args.length % 2 === 0) return ERR["#VALUE!"];
    const h = ifsCore(ctx, pairsFrom(args, 1), { arg: args[0] });
    return isErr(h) ? h : sumOf(h);
  }, { min: 3 });
  def("COUNTIFS", "range1, criteria1, …", "How many rows match every test.", '=COUNTIFS(A2:A50, "Car", C2:C50, ">100")', (args, ctx) => {
    if (args.length < 2 || args.length % 2) return ERR["#VALUE!"];
    const h = ifsCore(ctx, pairsFrom(args, 0), null, "count");
    return isErr(h) ? h : h.length;
  }, { min: 2 });
  def("AVERAGEIFS", "average range, range1, criteria1, …", "The average of the cells whose rows match every test.", '=AVERAGEIFS(C2:C50, A2:A50, "Car")', (args, ctx) => {
    if (args.length < 3 || args.length % 2 === 0) return ERR["#VALUE!"];
    const h = ifsCore(ctx, pairsFrom(args, 1), { arg: args[0] });
    return isErr(h) ? h : avgOf(h);
  }, { min: 3 });

  // --- lookups
  function lookupEq(v, x, wild) {
    if (v === null || x === null) return false;
    if (typeof v === "string" && typeof x === "string") return wild && /[*?~]/.test(v) ? wildRe(v).test(x) : v.toLowerCase() === x.toLowerCase();
    return typeof v === typeof x && v === x;
  }
  function vectorOf(r) {         // a one-row or one-column range as a list (else null)
    if (r.rows === 1) return { n: r.cols, at: (k) => r.at(0, k) };
    if (r.cols === 1) return { n: r.rows, at: (k) => r.at(k, 0) };
    return null;
  }
  function usedLen(ctx, r, byRow) {   // don't walk a whole column past the used part
    if (byRow) return r.cols;
    return Math.min(r.rows, Math.max(0, ctx.lastRow(r.tab) - r.r1 + 1));
  }
  function findExact(v, n, at, rev, wild) {
    for (let k = 0; k < n; k++) { const i = rev ? n - 1 - k : k; if (lookupEq(v, at(i), wild)) return i; }
    return -1;
  }
  function findSorted(v, n, at, desc) {   // largest ≤ v (ascending) / smallest ≥ v (descending): binary search as Excel
    let lo = 0, hi = n - 1, best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const x = at(mid);
      if (x === null) { hi = mid - 1; continue; }
      const c = typeRank(x) === typeRank(v) ? compare(x, v) : (desc ? -1 : 1);
      if (!desc ? c <= 0 : c >= 0) { best = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return best;
  }
  function tableLookup(args, ctx, vertical) {
    const v = ctx.ev(args[0]); if (isErr(v)) return v;
    const t = rangeArg(args[1], ctx); if (isErr(t)) return t; if (!isRange(t)) return ERR["#VALUE!"];
    const idx = ctx.num(args[2]); if (isErr(idx)) return idx;
    const k = Math.trunc(idx);
    let approx = true;
    if (args[3] && args[3].k !== "missing") { const b = ctx.bool(args[3]); if (isErr(b)) return b; approx = b; }
    if (k < 1) return ERR["#VALUE!"];
    if (k > (vertical ? t.cols : t.rows)) return ERR["#REF!"];
    const n = vertical ? usedLen(ctx, t, false) : t.cols;
    const at = vertical ? (i) => t.at(i, 0) : (j) => t.at(0, j);
    const hit = approx ? findSorted(v, n, at, false) : findExact(v, n, at, false, true);
    if (hit < 0) return ERR["#N/A"];
    const out = vertical ? t.at(hit, k - 1) : t.at(k - 1, hit);
    return out === null ? 0 : out;
  }
  def("VLOOKUP", "value, table, column number, [approximate]", "Finds the value in the table's first column and gives the cell from another column. FALSE = exact match.", '=VLOOKUP("Car", A2:C20, 3, FALSE)', (args, ctx) => tableLookup(args, ctx, true), { min: 3, max: 4 });
  def("HLOOKUP", "value, table, row number, [approximate]", "As VLOOKUP, across the table's first row.", '=HLOOKUP("Mar", B1:M5, 3, FALSE)', (args, ctx) => tableLookup(args, ctx, false), { min: 3, max: 4 });
  def("XLOOKUP", "value, look in, return from, [if not found], [match mode], [search mode]", "Finds the value and gives the matching cell of another range. Match mode 0 exact, -1 next smaller, 1 next larger, 2 wildcards; search mode -1 from the end.", '=XLOOKUP("Car", A2:A20, C2:C20, "none")', (args, ctx) => {
    const v = ctx.ev(args[0]); if (isErr(v)) return v;
    const la = rangeArg(args[1], ctx); if (isErr(la)) return la;
    const ra = rangeArg(args[2], ctx); if (isErr(ra)) return ra;
    if (!isRange(la) || !isRange(ra)) return ERR["#VALUE!"];
    const lv = vectorOf(la); if (!lv) return ERR["#VALUE!"];
    const mm = args[4] && args[4].k !== "missing" ? ctx.num(args[4]) : 0; if (isErr(mm)) return mm;
    const sm = args[5] && args[5].k !== "missing" ? ctx.num(args[5]) : 1; if (isErr(sm)) return sm;
    if (![0, -1, 1, 2].includes(mm) || ![1, -1, 2, -2].includes(sm)) return ERR["#VALUE!"];
    const n = la.cols === 1 ? usedLen(ctx, la, false) : lv.n;
    const rev = sm < 0;
    let hit = findExact(v, n, lv.at, rev, mm === 2);
    if (hit < 0 && (mm === -1 || mm === 1)) {
      let best = -1, bv = null;
      for (let i = 0; i < n; i++) {
        const x = lv.at(i);
        if (x === null || typeRank(x) !== typeRank(v)) continue;
        const c = compare(x, v);
        if (mm === -1 && c < 0 && (best < 0 || compare(x, bv) > 0)) { best = i; bv = x; }
        if (mm === 1 && c > 0 && (best < 0 || compare(x, bv) < 0)) { best = i; bv = x; }
      }
      hit = best;
    }
    if (hit < 0) return args[3] && args[3].k !== "missing" ? ctx.ev(args[3]) : ERR["#N/A"];
    let out;
    if (la.cols === 1 && ra.rows === la.rows) { if (ra.cols !== 1) return ERR["#VALUE!"]; out = ra.at(hit, 0); }
    else if (la.rows === 1 && ra.cols === la.cols) { if (ra.rows !== 1) return ERR["#VALUE!"]; out = ra.at(0, hit); }
    else return ERR["#VALUE!"];
    return out === null ? 0 : out;
  }, { min: 3, max: 6 });
  def("INDEX", "range, row, [column]", "The cell at that row and column of the range.", "=INDEX(B2:D20, 3, 2)", (args, ctx) => {
    const t = rangeArg(args[0], ctx); if (isErr(t)) return t;
    if (!isRange(t)) return ERR["#VALUE!"];
    let r = ctx.num(args[1]); if (isErr(r)) return r;
    let c = args[2] && args[2].k !== "missing" ? ctx.num(args[2]) : null; if (isErr(c)) return c;
    r = Math.trunc(r);
    if (c === null) { if (t.rows === 1) { c = r; r = 1; } else c = 1; }
    c = Math.trunc(c);
    if (r < 0 || c < 0 || r > t.rows || c > t.cols) return ERR["#REF!"];
    if (r === 0 || c === 0) {
      if ((r === 0 && t.rows === 1) || (c === 0 && t.cols === 1)) { r = r || 1; c = c || 1; } else return ERR["#VALUE!"];
    }
    const out = t.at(r - 1, c - 1);
    return out === null ? 0 : out;
  }, { min: 2, max: 3 });
  def("MATCH", "value, range, [match type]", "The position of the value in a row or column (1 = first). 0 exact, 1 largest ≤ (sorted up), -1 smallest ≥ (sorted down).", '=MATCH("Car", A2:A20, 0)', (args, ctx) => {
    const v = ctx.ev(args[0]); if (isErr(v)) return v;
    const t = rangeArg(args[1], ctx); if (isErr(t)) return t;
    if (!isRange(t)) return ERR["#N/A"];
    const vec = vectorOf(t); if (!vec) return ERR["#N/A"];
    const mt = args[2] && args[2].k !== "missing" ? ctx.num(args[2]) : 1; if (isErr(mt)) return mt;
    const n = t.cols === 1 ? usedLen(ctx, t, false) : vec.n;
    const hit = mt === 0 ? findExact(v, n, vec.at, false, true) : findSorted(v, n, vec.at, mt < 0);
    return hit < 0 ? ERR["#N/A"] : hit + 1;
  }, { min: 2, max: 3 });

  // --- text
  const str1 = (fn) => (args, ctx) => { const v = ctx.ev(args[0]); return isErr(v) ? v : fn(toText(v)); };
  def("LEN", "text", "How many characters.", "=LEN(A2)", str1((s) => s.length), { min: 1, max: 1 });
  def("UPPER", "text", "In capitals.", "=UPPER(A2)", str1((s) => s.toUpperCase()), { min: 1, max: 1 });
  def("LOWER", "text", "In small letters.", "=LOWER(A2)", str1((s) => s.toLowerCase()), { min: 1, max: 1 });
  def("CONCAT", "text1, [text2], …", "Joins texts (and every cell of a range).", '=CONCAT(A2, " ", B2)', (args, ctx) => {
    let s = "";
    for (const a of args) {
      if (a.k === "missing") continue;
      const v = ctx.evalRaw(a);
      if (isRange(v)) { let e = null; v.each((x) => { if (!e) { if (isErr(x)) e = x; else s += toText(x); } }); if (e) return e; }
      else if (isErr(v)) return v;
      else s += toText(v);
    }
    return s.length > 32767 ? ERR["#VALUE!"] : s;
  }, { min: 1 });
  def("CONCATENATE", "text1, [text2], …", "Joins texts (the older name of CONCAT, without ranges).", '=CONCATENATE(A2, " ", B2)', (args, ctx) => {
    let s = "";
    for (const a of args) { if (a.k === "missing") continue; const v = ctx.ev(a); if (isErr(v)) return v; s += toText(v); }
    return s;
  }, { min: 1 });
  def("TEXT", "value, format", "A number as text in a format.", '=TEXT(B2, "#,##0.00")', (args, ctx) => {
    const v = ctx.ev(args[0]); if (isErr(v)) return v;
    const f = ctx.ev(args[1]); if (isErr(f)) return f;
    const n = typeof v === "number" ? v : (typeof v === "string" ? textToNumber(v) : (v === null ? 0 : null));
    if (n === null) return toText(v);
    return formatCode(n, toText(f));
  }, { min: 2, max: 2 });

  // --- dates
  const dnum = (ctx, a) => { const v = ctx.ev(a); if (isErr(v)) return v; const n = toNum(v); return isErr(n) ? n : (n < 0 ? ERR["#NUM!"] : n); };
  def("TODAY", "", "Today's date (Home Assistant's time zone).", "=TODAY()", (args, ctx) => ctx.today(), { min: 0, max: 0, volatile: true });
  def("DAYS", "end date, start date", "Days from the start date to the end date.", "=DAYS(C2, B2)", (args, ctx) => {
    const e = dnum(ctx, args[0]); if (isErr(e)) return e;
    const s = dnum(ctx, args[1]); if (isErr(s)) return s;
    return Math.floor(e) - Math.floor(s);
  }, { min: 2, max: 2 });
  def("DATE", "year, month, day", "A date from its parts (months and days past the end carry over).", "=DATE(2026, 12, 31)", (args, ctx) => {
    const p = args.map((a) => ctx.num(a));
    for (const x of p) if (isErr(x)) return x;
    return dateSerial(p[0], p[1], p[2]);
  }, { min: 3, max: 3 });
  const part = (k) => (args, ctx) => { const s = dnum(ctx, args[0]); return isErr(s) ? s : serialParts(s)[k]; };
  def("YEAR", "date", "The year of a date.", "=YEAR(B2)", part("y"), { min: 1, max: 1 });
  def("MONTH", "date", "The month of a date (1–12).", "=MONTH(B2)", part("m"), { min: 1, max: 1 });
  def("DAY", "date", "The day of the month.", "=DAY(B2)", part("d"), { min: 1, max: 1 });
  def("EOMONTH", "start date, months", "The last day of the month, months before or after.", "=EOMONTH(B2, 0)", (args, ctx) => {
    const s = dnum(ctx, args[0]); if (isErr(s)) return s;
    const m = ctx.num(args[1]); if (isErr(m)) return m;
    const p = serialParts(s);
    return dateSerial(p.y, p.m + Math.trunc(m) + 1, 1) - 1;
  }, { min: 2, max: 2 });
  def("NETWORKDAYS", "start date, end date, [holidays]", "Working days (Monday–Friday) from start to end, both counted, less holidays.", "=NETWORKDAYS(B2, C2)", (args, ctx) => {
    let s = dnum(ctx, args[0]); if (isErr(s)) return s;
    let e = dnum(ctx, args[1]); if (isErr(e)) return e;
    s = Math.floor(s); e = Math.floor(e);
    const hol = new Set();
    if (args[2] && args[2].k !== "missing") {
      const hv = ctx.evalRaw(args[2]);
      if (isErr(hv)) return hv;
      const add = (x) => { if (typeof x === "number") hol.add(Math.floor(x)); };
      if (isRange(hv)) hv.each(add); else add(toNum(hv));
    }
    const sign = e >= s ? 1 : -1;
    const [a, b] = sign > 0 ? [s, e] : [e, s];
    if (b - a > 1e6) return ERR["#NUM!"];
    let n = 0;
    for (let d = a; d <= b; d++) { const wd = serialParts(d).wd; if (wd !== 0 && wd !== 6 && !hol.has(d)) n++; }
    return sign * n;
  }, { min: 2, max: 3 });
  def("PMT", "rate, periods, present value, [future value], [type]", "The payment per period of a loan (negative = you pay). Type 1 = paid at the start of each period.", "=PMT(5%/12, 60, 20000)", (args, ctx) => {
    const v = [0, 1, 2, 3, 4].map((i) => (args[i] && args[i].k !== "missing" ? ctx.num(args[i]) : 0));
    for (const x of v) if (isErr(x)) return x;
    const [rate, n, pv, fv, type] = v;
    if (n === 0) return ERR["#NUM!"];
    if (rate === 0) return -(pv + fv) / n;
    const q = Math.pow(1 + rate, n);
    const out = -(rate * (fv + pv * q)) / ((1 + rate * (type ? 1 : 0)) * (q - 1));
    return isFinite(out) ? out : ERR["#NUM!"];
  }, { min: 3, max: 5 });

  // ===================================================================== compile
  function compile(tree, wb, home) {
    // returns fn(ctx) → value (ranges are returned as Range for functions to use)
    function c(n) {
      switch (n.k) {
        case "num": { const v = n.v; return () => v; }
        case "str": { const v = n.v; return () => v; }
        case "bool": { const v = n.v; return () => v; }
        case "err": { const v = ERR[n.v] || ERR["#VALUE!"]; return () => v; }
        case "missing": return () => null;
        case "name": return () => ERR["#NAME?"];
        case "paren": return c(n.a);
        case "ref": {
          const tab = n.sheet === null ? home : wb.tabNamed(n.sheet);
          if (!tab) return () => ERR["#REF!"];
          const col = n.c, row = n.r;
          n.rtab = tab;                       // functions that want a range accept one cell too (SUMIF's B1)
          return (ctx) => ctx.cell(tab, col, row);
        }
        case "range": {
          const tab = n.sheet === null ? home : wb.tabNamed(n.sheet);
          if (!tab) return () => ERR["#REF!"];
          const { c1, r1, c2, r2 } = n;
          if (!n.whole) return (ctx) => new Range(ctx, tab, c1, r1, c2, r2);
          // whole columns stop at the last used row (B:B → B1:B<last>)
          return (ctx) => new Range(ctx, tab, c1, r1, c2, Math.min(r2, Math.max(r1, ctx.lastRow(tab))));
        }
        case "un": {
          const a = c(n.a), neg = n.op === "-";
          return (ctx) => { const v = toNum(scalar(a(ctx))); return isErr(v) ? v : (neg ? -v : v); };
        }
        case "pct": { const a = c(n.a); return (ctx) => { const v = toNum(scalar(a(ctx))); return isErr(v) ? v : v / 100; }; }
        case "bin": {
          const a = c(n.a), b = c(n.b), op = n.op;
          if (op === "&") return (ctx) => { const x = scalar(a(ctx)); if (isErr(x)) return x; const y = scalar(b(ctx)); if (isErr(y)) return y; return toText(x) + toText(y); };
          if (["=", "<>", "<", ">", "<=", ">="].includes(op)) {
            return (ctx) => {
              const x = scalar(a(ctx)); if (isErr(x)) return x;
              const y = scalar(b(ctx)); if (isErr(y)) return y;
              const r = compare(x, y);
              return op === "=" ? r === 0 : op === "<>" ? r !== 0 : op === "<" ? r < 0 : op === ">" ? r > 0 : op === "<=" ? r <= 0 : r >= 0;
            };
          }
          return (ctx) => {
            const x = toNum(scalar(a(ctx))); if (isErr(x)) return x;
            const y = toNum(scalar(b(ctx))); if (isErr(y)) return y;
            let r;
            switch (op) {
              case "+": r = x + y; break;
              case "-": r = x - y; break;
              case "*": r = x * y; break;
              case "/": if (y === 0) return ERR["#DIV/0!"]; r = x / y; break;
              case "^": if (x === 0 && y < 0) return ERR["#DIV/0!"]; r = Math.pow(x, y); break;
              default: return ERR["#VALUE!"];
            }
            return isFinite(r) ? r : ERR["#NUM!"];
          };
        }
        case "fn": {
          const f = FN[n.name];
          if (!f) return () => ERR["#NAME?"];
          if ((f.min !== undefined && n.args.length < f.min) || (f.max !== undefined && n.args.length > f.max)) return () => ERR["#VALUE!"];
          const args = n.args;
          for (const a of args) a.fn = c(a);
          return (ctx) => f.impl(args, ctx);
        }
        default: return () => ERR["#VALUE!"];
      }
    }
    return c(tree);
  }
  function scalar(v) {
    if (v instanceof Range) {
      if (v.rows === 1 && v.cols === 1) { const x = v.at(0, 0); return x; }
      return ERR["#VALUE!"];
    }
    return v;
  }
  /** The cells and ranges a formula reads: [{tab, c, r}] and [{tab, c1, r1, c2, r2}] (tab objects; null = missing). */
  function refsOf(tree, wb, home, cells, ranges) {
    (function walk(n) {
      if (!n) return;
      if (n.k === "ref") cells.push({ tab: n.sheet === null ? home : wb.tabNamed(n.sheet), c: n.c, r: n.r });
      else if (n.k === "range") ranges.push({ tab: n.sheet === null ? home : wb.tabNamed(n.sheet), c1: n.c1, r1: n.r1, c2: n.c2, r2: n.r2 });
      if (n.a) walk(n.a);
      if (n.b) walk(n.b);
      if (n.args) n.args.forEach(walk);
    })(tree);
  }
  function usesToday(tree) {
    let v = false;
    (function walk(n) { if (!n || v) return; if (n.k === "fn" && FN[n.name] && FN[n.name].volatile) v = true; if (n.a) walk(n.a); if (n.b) walk(n.b); if (n.args) n.args.forEach(walk); })(tree);
    return v;
  }

  // ===================================================================== the workbook
  function workbook(opts = {}) {
    const tabs = [];
    const byName = new Map();
    let todayFn = opts.today || (() => todaySerial());
    let dirtyAll = true;

    function makeTab(name) { return { name, cells: new Map(), maxRow: -1 }; }
    const key = (c, r) => r * MAXC + c;
    const ctx = {
      cell(tab, c, r) {
        const cell = tab.cells.get(key(c, r));
        if (!cell) return null;
        return cell.f ? (cell.value === undefined ? null : cell.value) : cell.v;
      },
      lastRow(tab) { return tab ? tab.maxRow : -1; },
      evalRaw(a) { return a.fn(ctx); },
      ev(a) { return scalar(a.fn(ctx)); },
      num(a) { if (!a || a.k === "missing") return 0; const v = scalar(a.fn(ctx)); return toNum(v); },
      bool(a) { const v = scalar(a.fn(ctx)); return toBool(v); },
      today() { return todayFn(); },
    };
    const wb = {
      tabs,
      tabNamed(name) { return byName.get(String(name).toLowerCase()) || null; },
      setTabs(names) {
        const old = new Map(tabs.map((t) => [t.name.toLowerCase(), t]));
        tabs.length = 0; byName.clear();
        for (const n of names) {
          const t = old.get(String(n).toLowerCase()) || makeTab(n);
          t.name = n;
          tabs.push(t); byName.set(n.toLowerCase(), t);
        }
        dirtyAll = true;
        for (const t of tabs) for (const cell of t.cells.values()) if (cell.f) compileCell(t, cell);
      },
      /** Rename a tab and rewrite every formula that names it → [[tab, ref, newRaw], …]. */
      renameTab(oldName, newName) {
        const t = wb.tabNamed(oldName);
        if (!t) return [];
        const changed = [];
        for (const tt of tabs) for (const [k, cell] of tt.cells) {
          if (!cell.f) continue;
          const nf = renameTab(cell.raw, oldName, newName);
          if (nf !== cell.raw) { cell.raw = nf; changed.push([tt.name === t.name ? newName : tt.name, addr(k % MAXC, Math.floor(k / MAXC)), nf]); }
        }
        wb.setTabs(tabs.map((x) => (x === t ? newName : x.name)));
        return changed;
      },
      set(tabName, ref, raw, isText) {
        const t = typeof tabName === "string" ? wb.tabNamed(tabName) : tabName;
        if (!t) throw new Error("No tab " + tabName);
        const a = typeof ref === "string" ? parseRef(ref) : ref;
        const k = key(a.c, a.r);
        const prev = t.cells.get(k);
        if (prev && prev.f) unlink(t, k, prev);
        if (raw === null || raw === undefined || raw === "") { t.cells.delete(k); if (a.r === t.maxRow) fixMax(t); return; }
        const cell = { raw, v: raw, f: false, c: a.c, r: a.r };
        if (!isText && typeof raw === "string" && raw.length > 1 && raw[0] === "=") { cell.f = true; compileCell(t, cell); link(t, k, cell); }
        t.cells.set(k, cell);
        if (a.r > t.maxRow) t.maxRow = a.r;
      },
      raw(tab, ref) { const t = wb.tabNamed(tab); const a = parseRef(ref); const cell = t && a && t.cells.get(key(a.c, a.r)); return cell ? cell.raw : null; },
      isFormula(tab, ref) { const t = wb.tabNamed(tab); const a = parseRef(ref); const cell = t && a && t.cells.get(key(a.c, a.r)); return !!(cell && cell.f); },
      value(tab, ref) { const t = wb.tabNamed(tab); const a = typeof ref === "string" ? parseRef(ref) : ref; if (!t || !a) return ERR["#REF!"]; const v = ctx.cell(t, a.c, a.r); return v; },
      /** Everything (topological order; cycles → #CYCLE!). Returns the number of formulas computed. */
      recalc() { dirtyAll = false; rebuildIndex(); const all = []; for (const t of tabs) for (const cell of t.cells.values()) if (cell.f) all.push(cell); return evaluate(all); },
      /** Set cells, then recalculate what depends on them. → keys "Tab!A1" of formula cells whose value changed (and the cells set). */
      update(changes) {
        for (const [tab, ref, raw, isText] of changes) wb.set(tab, ref, raw, isText);
        if (dirtyAll) { wb.recalc(); return null; }
        rebuildIndex();
        const start = changes.map(([tab, ref]) => { const t = wb.tabNamed(tab); const a = typeof ref === "string" ? parseRef(ref) : ref; return [t, a.c, a.r]; });
        const dirty = dependents(start);
        const before = new Map(dirty.map((cell) => [cell, cell.value]));
        evaluate(dirty);
        const out = [];
        for (const [cell, v] of before) if (!same(v, cell.value)) out.push(cell.tab.name + "!" + addr(cell.c, cell.r));
        return out;
      },
      /** Volatile formulas (TODAY) again — e.g. after midnight. */
      refreshToday() { const vol = []; for (const t of tabs) for (const cell of t.cells.values()) if (cell.f && cell.volatile) vol.push([t, cell.c, cell.r]); if (vol.length) evaluate(dependents(vol, true)); },
      setToday(fn) { todayFn = fn; },
      formulaCount() { let n = 0; for (const t of tabs) for (const cell of t.cells.values()) if (cell.f) n++; return n; },
    };
    function same(a, b) { return a === b || (isErr(a) && isErr(b) && a.code === b.code); }
    function fixMax(t) { let m = -1; for (const k of t.cells.keys()) { const r = Math.floor(k / MAXC); if (r > m) m = r; } t.maxRow = m; }
    function compileCell(t, cell) {
      cell.tab = t;
      cell.deps = []; cell.rdeps = [];
      try {
        const tree = parse(cell.raw.slice(1));
        cell.fn = compile(tree, wb, t);
        refsOf(tree, wb, t, cell.deps, cell.rdeps);
        cell.volatile = usesToday(tree);
        cell.syntax = null;
      } catch (e) {
        if (!(e instanceof CalcSyntax)) throw e;
        cell.fn = () => ERR["#NAME?"];
        cell.syntax = e.message;
      }
    }
    // reverse index: who reads a cell (single refs) — ranges are matched by scanning (rangeReaders)
    let readers = new Map();       // tab → Map(key → [cell]) — who reads a single cell
    let rangeReaders = new Map();  // tab → {cols: Map(col → [reader]), wide: [reader]}; reader {c1, r1, c2, r2, cell}
    let indexDirty = true;
    function link(t, k, cell) { indexDirty = true; }
    function unlink(t, k, cell) { indexDirty = true; }
    // formula cells by tab and column, rows sorted: finds the formulas inside a range quickly
    let colIndexByTab = new Map();
    function rebuildIndex() {
      if (!indexDirty) return;
      indexDirty = false;
      indexGen++;
      readers = new Map(); rangeReaders = new Map(); colIndexByTab = new Map();
      for (const t of tabs) {
        const cols = new Map();
        for (const cell of t.cells.values()) {
          if (!cell.f) continue;
          let list = cols.get(cell.c); if (!list) cols.set(cell.c, (list = [])); list.push(cell);
          for (const d of cell.deps) {
            if (!d.tab) continue;
            let m = readers.get(d.tab); if (!m) readers.set(d.tab, (m = new Map()));
            const k = key(d.c, d.r);
            const l = m.get(k); if (!l) m.set(k, [cell]); else l.push(cell);
          }
          for (const d of cell.rdeps) {
            if (!d.tab) continue;
            let b = rangeReaders.get(d.tab); if (!b) rangeReaders.set(d.tab, (b = { cols: new Map(), wide: [] }));
            const rr = { c1: d.c1, r1: d.r1, c2: d.c2, r2: d.r2, cell };
            if (d.c2 - d.c1 >= 32) b.wide.push(rr);
            else for (let c = d.c1; c <= d.c2; c++) { let l = b.cols.get(c); if (!l) b.cols.set(c, (l = [])); l.push(rr); }
          }
        }
        for (const list of cols.values()) list.sort((a, b) => a.r - b.r);
        colIndexByTab.set(t, cols);
      }
    }
    function formulasIn(t, c1, r1, c2, r2) {
      const cols = colIndexByTab.get(t);
      const out = [];
      if (!cols) return out;
      const scan = (list) => {
        let lo = 0, hi = list.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (list[m].r < r1) lo = m + 1; else hi = m; }
        for (let i = lo; i < list.length && list[i].r <= r2; i++) out.push(list[i]);
      };
      if (c2 - c1 + 1 > cols.size) { for (const [c, list] of cols) if (c >= c1 && c <= c2) scan(list); }
      else for (let c = c1; c <= c2; c++) { const list = cols.get(c); if (list) scan(list); }
      return out;
    }
    let indexGen = 0;
    function precedents(cell) {     // formula cells this one reads (cached until a formula is added or removed)
      if (cell._pg === indexGen) return cell._pre;
      const out = [];
      for (const d of cell.deps) {
        if (!d.tab) continue;
        const x = d.tab.cells.get(key(d.c, d.r));
        if (x && x.f) out.push(x);
      }
      for (const d of cell.rdeps) if (d.tab) for (const x of formulasIn(d.tab, d.c1, d.r1, d.c2, d.r2)) out.push(x);
      cell._pre = out; cell._pg = indexGen;
      return out;
    }
    function dependents(start, includeStart) {
      const seen = new Set();
      const queue = [];
      const push = (cell) => { if (!seen.has(cell)) { seen.add(cell); queue.push(cell); } };
      const readersOf = (t, c, r) => {
        const m = readers.get(t);
        const l = m && m.get(key(c, r));
        if (l) for (const x of l) push(x);
        const b = rangeReaders.get(t);
        if (!b) return;
        const hit = (rr) => { if (c >= rr.c1 && c <= rr.c2 && r >= rr.r1 && r <= rr.r2) push(rr.cell); };
        const cl = b.cols.get(c); if (cl) cl.forEach(hit);
        b.wide.forEach(hit);
      };
      for (const [t, c, r] of start) {
        if (!t) continue;
        const own = t.cells.get(key(c, r));
        if (own && own.f) push(own);
        else if (includeStart) continue;
        readersOf(t, c, r);
      }
      for (let i = 0; i < queue.length; i++) { const x = queue[i]; readersOf(x.tab, x.c, x.r); }
      return queue;
    }
    /** Evaluate these formula cells in dependency order (Tarjan's strongly connected components; the state
     *  lives on the cells, stamped with this run's number). */
    let run = 0;
    function evaluate(list) {
      run++;
      const R = run;
      for (const cell of list) { cell._r = R; cell._i = -1; }
      let idx = 0, n = 0;
      const stack = [];
      const work = [];
      const one = (cell) => {
        let v;
        try { v = scalar(cell.fn(ctx)); } catch (e) { v = ERR["#VALUE!"]; }
        if (v === null || v === undefined) v = 0;
        else if (typeof v === "number" && !isFinite(v)) v = ERR["#NUM!"];
        cell.value = v;
        n++;
      };
      for (const root of list) {
        if (root._i >= 0) continue;
        work.push(root); root._i = root._lo = idx++; root._k = 0; root._on = true; stack.push(root);
        while (work.length) {
          const v = work[work.length - 1];
          const pre = precedents(v);
          let pushed = false;
          while (v._k < pre.length) {
            const w = pre[v._k++];
            if (w._r !== R) continue;                  // not being recalculated: its value stands
            if (w._i < 0) { w._i = w._lo = idx++; w._k = 0; w._on = true; stack.push(w); work.push(w); pushed = true; break; }
            if (w._on && w._i < v._lo) v._lo = w._i;
          }
          if (pushed) continue;
          work.pop();
          if (work.length) { const p = work[work.length - 1]; if (v._lo < p._lo) p._lo = v._lo; }
          if (v._lo === v._i) {
            let w = stack.pop(); w._on = false;
            if (w === v) {                             // a component of one: cyclic only if it reads itself
              if (pre.includes(v)) v.value = ERR["#CYCLE!"]; else one(v);
            } else {
              const comp = [w];
              do { w = stack.pop(); w._on = false; comp.push(w); } while (w !== v);
              for (const c of comp) c.value = ERR["#CYCLE!"];
            }
          }
        }
      }
      return n;
    }
    return wb;
  }

  // ===================================================================== formats (what a cell shows)
  // fmt: {f: "general"|"number"|"currency"|"percent"|"date"|"text", d: decimals, red: bool}; opts: {currency, locale}
  const nfCache = new Map();
  function nf(locale, o) {
    const k = locale + JSON.stringify(o);
    let f = nfCache.get(k);
    if (!f) { try { f = new Intl.NumberFormat(locale || undefined, o); } catch (e) { f = new Intl.NumberFormat(undefined, Object.assign({}, o, { currency: undefined, style: o.style === "currency" ? "decimal" : o.style })); } nfCache.set(k, f); }
    return f;
  }
  function fmtDate(serial, locale) {
    const p = serialParts(serial);
    try { return new Date(Date.UTC(p.y, p.m - 1, p.d)).toLocaleDateString(locale || undefined, { timeZone: "UTC", year: "numeric", month: "short", day: "numeric" }); }
    catch (e) { return `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`; }
  }
  // number styles (a personal setting, SPEC §4): how numbers look and how typed numbers are read
  const NUM_LOCALES = { en: "en-US", de: "de-DE", in: "en-IN" };
  function numLocale(opts) { return NUM_LOCALES[opts.numStyle] || opts.numLocale || opts.locale; }
  function format(v, fmt, opts = {}) {
    fmt = fmt || {};
    if (v === null || v === undefined) return { text: "", negative: false };
    if (isErr(v)) return { text: v.code, negative: false, error: true };
    if (typeof v === "boolean") return { text: v ? "TRUE" : "FALSE", negative: false };
    if (typeof v === "string") return { text: v, negative: false };
    const f = fmt.f || "general";
    const d = fmt.d === undefined || fmt.d === null ? null : Math.max(0, Math.min(10, fmt.d));
    const neg = v < 0;
    const nl = numLocale(opts);
    let text;
    switch (f) {
      case "number": text = nf(nl, { minimumFractionDigits: d ?? 2, maximumFractionDigits: d ?? 2, useGrouping: true }).format(v); break;
      case "currency": text = nf(nl, { style: "currency", currency: opts.currency || "EUR", minimumFractionDigits: d ?? 2, maximumFractionDigits: d ?? 2 }).format(v); break;
      case "percent": text = nf(nl, { style: "percent", minimumFractionDigits: d ?? 0, maximumFractionDigits: d ?? 0 }).format(v); break;
      case "date": text = v >= 0 && v < 2958466 ? fmtDate(v, opts.locale) : numText(v); break;
      default: text = numText(v); if (opts.numStyle === "de") text = text.replace(".", ",");
    }
    return { text, negative: neg && !!fmt.red };
  }
  /** TEXT(): a subset of Excel's format codes — 0 0.00 #,##0.00 0% $#,##0 "text" and dates (yyyy mm dd mmm mmmm ddd dddd). */
  function formatCode(n, code) {
    const section = String(code).split(";");
    let c = section[0];
    if (n < 0 && section.length > 1) { c = section[1]; n = -n; }
    else if (n === 0 && section.length > 2) c = section[2];
    const lits = [];
    c = c.replace(/"([^"]*)"|\\(.)/g, (m, a, b) => { lits.push(a !== undefined ? a : b); return String.fromCharCode(0xE000 + lits.length - 1); });
    const isDate = /[yd]|m{3,}/i.test(c) || (/m/i.test(c) && !/[0#]/.test(c));
    let out;
    if (isDate) {
      const p = serialParts(n);
      const M = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
      const W = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
      out = c.replace(/yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d/gi, (t) => {
        switch (t.toLowerCase()) {
          case "yyyy": return String(p.y);
          case "yy": return String(p.y).slice(-2);
          case "mmmm": return M[p.m - 1];
          case "mmm": return M[p.m - 1].slice(0, 3);
          case "mm": return String(p.m).padStart(2, "0");
          case "m": return String(p.m);
          case "dddd": return W[p.wd];
          case "ddd": return W[p.wd].slice(0, 3);
          case "dd": return String(p.d).padStart(2, "0");
          default: return String(p.d);
        }
      });
    } else {
      const m = /[0#,.]+/.exec(c);
      if (!m) out = c;
      else {
        let x = n;
        const pct = (c.match(/%/g) || []).length;
        x *= Math.pow(100, pct);
        const pat = m[0];
        const dot = pat.indexOf(".");
        const dec = dot < 0 ? 0 : pat.slice(dot + 1).replace(/[^0#]/g, "").length;
        const minDec = dot < 0 ? 0 : (pat.slice(dot + 1).match(/0/g) || []).length;
        const intPat = dot < 0 ? pat : pat.slice(0, dot);
        const group = intPat.includes(",");
        const minInt = (intPat.match(/0/g) || []).length;
        let s = roundTo(Math.abs(x), dec, "near").toFixed(dec);
        let [ip, fp] = s.split(".");
        if (fp !== undefined) { fp = fp.replace(/0+$/, ""); while (fp.length < minDec) fp += "0"; }
        ip = ip.replace(/^0+/, "");
        while (ip.length < minInt) ip = "0" + ip;
        if (group) ip = ip.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        const num = (x < 0 && section.length === 1 ? "-" : "") + ip + (fp ? "." + fp : "");
        out = c.slice(0, m.index) + num + c.slice(m.index + pat.length);
      }
    }
    return out.replace(/[\uE000-\uE0FF]/g, (m) => lits[m.charCodeAt(0) - 0xE000]);
  }
  /** What typing into a cell means. → {v, f?, d?} ("'" keeps text as it is; formulas stay text "=…"). */
  /** A typed number in a number style ("auto"/"en": 1,234.56 · "de": 1.234,56 · "in": 12,34,567.89) → a number,
      or null. A plain 1.5 is read in every style (in "de" only when it can't be thousands, like 1.234). */
  function styledNumber(t, style) {
    if (style === "de") {
      if (/^[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?([eE][+-]?\d+)?$/.test(t)) return parseFloat(t.replace(/\./g, "").replace(",", "."));
      if (/^[+-]?\d*\.\d+$/.test(t) && !/^[+-]?\d{1,3}\.\d{3}$/.test(t)) return parseFloat(t);
      return null;
    }
    if (style === "in" && /^[+-]?\d{1,2}(,\d{2})*,\d{3}(\.\d+)?$/.test(t)) return parseFloat(t.replace(/,/g, ""));
    if (/^[+-]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?([eE][+-]?\d+)?$/.test(t) && /\d/.test(t)) {
      const n = parseFloat(t.replace(/,/g, ""));
      return isFinite(n) ? n : null;
    }
    return null;
  }
  function parseInput(text, style) {
    if (text === null || text === undefined) return { v: null };
    const s = String(text);
    if (s === "") return { v: null };
    if (s[0] === "'") return { v: s.slice(1), text: true };
    if (s[0] === "=" && s.length > 1) return { v: s };
    const t = s.trim();
    if (/^(TRUE|FALSE)$/i.test(t)) return { v: t.toUpperCase() === "TRUE" };
    let m;
    if (style === "de" || style === "in") {
      m = /^([+-]?[\d.,]*\d)\s*%$/.exec(t);
      if (m) { const n = styledNumber(m[1], style); if (n !== null) { const frac = style === "de" ? m[1].split(",")[1] : m[1].split(".")[1]; return { v: n / 100, f: "percent", d: (frac || "").length }; } }
    }
    m = /^([+-]?)(\d[\d,]*\.?\d*|\.\d+)\s*%$/.exec(t);
    if (m && style !== "de") { const n = textToNumber(m[1] + m[2]); if (n !== null) { const dec = (m[2].split(".")[1] || "").length; return { v: n / 100, f: "percent", d: dec }; } }
    m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t) || null;
    if (m) { const v = dateSerial(+m[1], +m[2], +m[3]); if (!isErr(v) && +m[2] >= 1 && +m[2] <= 12 && +m[3] >= 1 && +m[3] <= 31) return { v, f: "date" }; }
    m = /^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$/.exec(t);
    if (m) { const v = dateSerial(+m[3], +m[2], +m[1]); if (!isErr(v) && +m[2] >= 1 && +m[2] <= 12 && +m[1] >= 1 && +m[1] <= 31) return { v, f: "date" }; }
    const n = styledNumber(t, style);
    if (n !== null) return { v: n };
    return { v: s };
  }

  const api = {
    workbook, parse, tokenize, shiftFormula, adjustFormula, renameTab, functionsIn, format, formatCode, parseInput,
    colName, colIndex, addr, parseRef, parseRange, tabPrefix, dateSerial, serialParts, todaySerial, toText, numText,
    compare, ERR, WHY, isErr, CalcError, CalcSyntax, FUNCTIONS, MAXR, MAXC, styledNumber, NUM_LOCALES,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SheetCalc = api;
})(typeof window !== "undefined" ? window : globalThis);
