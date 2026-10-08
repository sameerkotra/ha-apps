"use strict";
// node --test tests/js — the formula engine (app/static/sheetcalc.js): operators, every function against Excel's
// results, references (relative / $ / whole columns / other tabs), insert & delete, fill, tab rename, errors,
// cycles, recalculation, formats, and the 50 000-cell benchmark (SPEC §8.2, §17.10).
const test = require("node:test");
const assert = require("node:assert");
const S = require("../../app/static/sheetcalc.js");

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
function calc(formula, cells = {}) {
  const wb = book(Object.assign({}, cells, { Z99: "=" + formula }));
  return wb.value("S", "Z99");
}
const err = (code) => (v) => S.isErr(v) && v.code === code;
function near(a, b, eps = 1e-9) { assert.ok(typeof a === "number" && Math.abs(a - b) < eps, `${a} ≈ ${b}`); }

// ---------------------------------------------------------------- parsing and operators
test("operators and precedence follow Excel", () => {
  assert.strictEqual(calc("1+2*3"), 7);
  assert.strictEqual(calc("(1+2)*3"), 9);
  assert.strictEqual(calc("2^3^2"), 64);            // left-associative, as in Excel
  assert.strictEqual(calc("-2^2"), 4);              // unary minus binds tighter than ^
  assert.strictEqual(calc("10-2-3"), 5);
  assert.strictEqual(calc("12/4/3"), 1);
  near(calc("50%"), 0.5);
  near(calc("200*10%"), 20);
  assert.strictEqual(calc("--3"), 3);
  assert.strictEqual(calc("+4"), 4);
  assert.strictEqual(calc('"a"&"b"&1&TRUE'), "ab1TRUE");
  assert.strictEqual(calc("1&2"), "12");
  assert.strictEqual(calc("0.1+0.2&\"\""), "0.3");   // 15 significant digits, as Excel shows text
  assert.strictEqual(calc("1+2&3"), "33");          // & is below + -
  assert.strictEqual(calc("1<2"), true);
  assert.strictEqual(calc("2<=2"), true);
  assert.strictEqual(calc("3>=4"), false);
  assert.strictEqual(calc("1<>1"), false);
  assert.strictEqual(calc('"a"="A"'), true);        // text compares without case
  assert.strictEqual(calc('"b">"a"'), true);
  assert.strictEqual(calc('"1"=1'), false);         // text is never equal to a number
  assert.strictEqual(calc('"a">1'), true);          // numbers < text < booleans
  assert.strictEqual(calc('TRUE>"z"'), true);
  assert.strictEqual(calc("A1=0", {}), true);       // an empty cell is 0 …
  assert.strictEqual(calc('A1=""', {}), true);      // … and ""
  assert.strictEqual(calc("1+A1", {}), 1);
  assert.strictEqual(calc('"3"+4'), 7);             // text that is a number
  assert.ok(err("#VALUE!")(calc('"x"+1')));
  assert.strictEqual(calc("TRUE+TRUE"), 2);
  assert.ok(err("#DIV/0!")(calc("1/0")));
  assert.ok(err("#DIV/0!")(calc("0^-1")));
  assert.strictEqual(calc("1E3+1"), 1001);
  assert.strictEqual(calc("SUM( 1 , 2 )"), 3);
  assert.strictEqual(calc("1;2") instanceof Object, true);   // ; separator is read as , (a value either way)
});

test("syntax errors are reported, not evaluated", () => {
  for (const bad of ["1+", "SUM(1,2", "(1", '"open', "1 2", "A1:", "'Car'", "@"]) {
    assert.throws(() => S.parse(bad), S.CalcSyntax, bad);
  }
  const wb = book({ A1: "=1+", A2: "=A1+1" });
  assert.ok(err("#NAME?")(wb.value("S", "A1")));
  assert.ok(err("#NAME?")(wb.value("S", "A2")));      // errors flow on
});

test("unknown functions and names are #NAME?", () => {
  assert.ok(err("#NAME?")(calc("FOO(1)")));
  assert.ok(err("#NAME?")(calc("Total*2")));
  assert.ok(err("#VALUE!")(calc("ABS()")));          // wrong number of arguments
  assert.ok(err("#VALUE!")(calc("ROUND(1,2,3)")));
  assert.strictEqual(calc("_xlfn.CONCAT(\"a\",\"b\")"), "ab");   // Excel's prefix is understood
});

test("error literals and propagation", () => {
  assert.ok(err("#N/A")(calc("#N/A")));
  assert.ok(err("#N/A")(calc("#N/A+1")));
  assert.ok(err("#REF!")(calc("#REF!")));
  assert.ok(err("#DIV/0!")(calc("SUM(A1:A2)", { A1: 1, A2: "=1/0" })));
  assert.ok(err("#DIV/0!")(calc('"x"&(1/0)')));
  assert.strictEqual(S.WHY["#CYCLE!"].length > 10, true);
});

// ---------------------------------------------------------------- references
test("references: cells, ranges, $, whole columns", () => {
  const cells = { A1: 1, A2: 2, A3: 3, B1: 10, B2: 20, B3: "text", C5: 5 };
  assert.strictEqual(calc("A1+B2", cells), 21);
  assert.strictEqual(calc("$A$1+$B2+B$1", cells), 31);
  assert.strictEqual(calc("SUM(A1:B3)", cells), 36);
  assert.strictEqual(calc("SUM(B3:A1)", cells), 36);           // corners in any order
  assert.strictEqual(calc("SUM(A:A)", cells), 6);
  assert.strictEqual(calc("SUM(A:B)", cells), 36);
  assert.strictEqual(calc("COUNTA(B:B)", cells), 3);
  assert.strictEqual(calc("SUM(5:5)", cells), 5);               // whole rows
  assert.strictEqual(calc("A1:A1*3", cells), 3);                // a one-cell range is a value
  assert.ok(err("#VALUE!")(calc("A1:A3+1", cells)));            // a range where one value is needed
  assert.strictEqual(calc("a1+b1", cells), 11);                 // lower case
});

test("other tabs: 'Car'!B4, Car!B4, ranges, missing tabs", () => {
  const wb = book({ "Budget!A1": "='Car'!B4*2", "Budget!A2": "=Car!B4+SUM('Car'!B1:B4)", "Car!B4": 10, "Car!B1": 1,
    "Budget!A3": "=Nope!A1", "Budget!A4": "='My tab'!A1", "My tab!A1": 7 }, ["Budget", "Car", "My tab"]);
  assert.strictEqual(wb.value("Budget", "A1"), 20);
  assert.strictEqual(wb.value("Budget", "A2"), 21);
  assert.ok(err("#REF!")(wb.value("Budget", "A3")));
  assert.strictEqual(wb.value("Budget", "A4"), 7);
  assert.strictEqual(wb.value("Budget", "A4"), 7);
  const ch = wb.update([["Car", "B4", 100]]);
  assert.strictEqual(wb.value("Budget", "A1"), 200);
  assert.deepStrictEqual(ch.sort(), ["Budget!A1", "Budget!A2"]);
});

test("renaming a tab rewrites formulas that name it", () => {
  const wb = book({ "Budget!A1": "='Car'!B4+Car!B4+'car'!A1:A2+SUM(Car!A1:A2)", "Car!B4": 1, "Car!A1": 2 }, ["Budget", "Car"]);
  const changed = wb.renameTab("Car", "Car costs");
  assert.deepStrictEqual(changed, [["Budget", "A1", "='Car costs'!B4+'Car costs'!B4+'Car costs'!A1:A2+SUM('Car costs'!A1:A2)"]]);
  wb.recalc();
  assert.ok(S.isErr(wb.value("Budget", "A1")));   // A1:A2 + number is #VALUE! in Excel too
  assert.strictEqual(S.renameTab("=Car!A1+Cart!A1", "Car", "Van"), "=Van!A1+Cart!A1");
  assert.strictEqual(S.renameTab('="Car!A1"&Car!A1', "Car", "It's"), "=\"Car!A1\"&'It''s'!A1");   // text inside quotes untouched
  assert.strictEqual(S.tabPrefix("Plain_1"), "Plain_1!");
  assert.strictEqual(S.tabPrefix("A1"), "'A1'!");                 // looks like a cell: quoted
  assert.strictEqual(S.tabPrefix("Two words"), "'Two words'!");
});

test("fill and paste: relative references move, $ stays, off the edge is #REF!", () => {
  assert.strictEqual(S.shiftFormula("=A1+$B$1+C$1+$D1", 1, 2), "=B3+$B$1+D$1+$D3");
  assert.strictEqual(S.shiftFormula("=SUM(A1:A3)", 0, 1), "=SUM(A2:A4)");
  assert.strictEqual(S.shiftFormula("=SUM($A$1:A3)", 0, 1), "=SUM($A$1:A4)");
  assert.strictEqual(S.shiftFormula("=A1", 0, -1), "=#REF!");
  assert.strictEqual(S.shiftFormula("=Car!A1", 0, -1), "=Car!#REF!");
  assert.strictEqual(S.shiftFormula("=SUM(B:B)", 1, 5), "=SUM(C:C)");
  assert.strictEqual(S.shiftFormula('="A1"&A1', 0, 1), '="A1"&A2');      // text isn't touched
  assert.strictEqual(S.shiftFormula("=Car!A1+A1", 0, 1, { sameTabOnly: true }), "=Car!A1+A2");   // sorting
});

test("insert and delete rows / columns adjust references", () => {
  const ins = (f, axis, at, count, tab = "S", home = "S") => S.adjustFormula(f, { tab, home, axis, at, count });
  assert.strictEqual(ins("=A1+A5", "row", 2, 1), "=A1+A6");                 // row inserted above row 3
  assert.strictEqual(ins("=SUM(A1:A5)", "row", 2, 2), "=SUM(A1:A7)");       // range grows
  assert.strictEqual(ins("=SUM(A3:A5)", "row", 2, 1), "=SUM(A4:A6)");
  assert.strictEqual(ins("=$A$5", "row", 0, 1), "=$A$6");                   // $ refs move too (Excel)
  assert.strictEqual(ins("=A1+C1", "col", 1, 1), "=A1+D1");
  assert.strictEqual(ins("=SUM(B:B)", "row", 0, 1), "=SUM(B:B)");           // whole columns don't move for rows
  assert.strictEqual(ins("=A5", "row", 4, -1), "=#REF!");                   // the cell itself deleted
  assert.strictEqual(ins("=A5+A7", "row", 4, -1), "=#REF!+A6");
  assert.strictEqual(ins("=SUM(A1:A5)", "row", 2, -2), "=SUM(A1:A3)");      // range shrinks
  assert.strictEqual(ins("=SUM(A3:A4)", "row", 2, -2), "=SUM(#REF!)");     // all of it deleted
  assert.strictEqual(ins("=SUM(A3:A9)", "row", 0, -4), "=SUM(A1:A5)");      // its top deleted
  assert.strictEqual(ins("=B1", "col", 0, -1), "=A1");
  assert.strictEqual(ins("=Car!A5", "row", 0, 1, "Car", "S"), "=Car!A6");   // into another tab
  assert.strictEqual(ins("=A5", "row", 0, 1, "Car", "S"), "=A5");           // a different tab changed
  assert.strictEqual(ins("=A5", "row", 0, 1, "car", "Car"), "=A6");         // case doesn't matter
});

// ---------------------------------------------------------------- functions
test("SUM AVERAGE MIN MAX MEDIAN COUNT COUNTA", () => {
  const c = { A1: 1, A2: 2, A3: "3", A4: true, A5: null, A6: 4, B1: "x" };
  assert.strictEqual(calc("SUM(A1:A6)", c), 7);               // text and TRUE in a range are skipped
  assert.strictEqual(calc('SUM(1,"2",TRUE)'), 4);             // typed directly they count
  assert.ok(err("#VALUE!")(calc('SUM(1,"x")')));
  assert.strictEqual(calc("SUM(A3)", c), 0);                  // a reference to text: skipped
  assert.strictEqual(calc("SUM()"), 0);
  near(calc("AVERAGE(A1:A6)", c), 7 / 3);
  assert.ok(err("#DIV/0!")(calc("AVERAGE(B1)", c)));
  assert.strictEqual(calc("MIN(A1:A6)", c), 1);
  assert.strictEqual(calc("MAX(A1:A6,10)", c), 10);
  assert.strictEqual(calc("MAX(B1)", c), 0);
  assert.strictEqual(calc("MEDIAN(1,3,2)"), 2);
  assert.strictEqual(calc("MEDIAN(1,2,3,4)"), 2.5);
  assert.ok(err("#NUM!")(calc("MEDIAN(B1)", c)));
  assert.strictEqual(calc("COUNT(A1:A6)", c), 3);
  assert.strictEqual(calc('COUNT(1,"2","x",TRUE)'), 3);
  assert.strictEqual(calc("COUNTA(A1:A6,B1)", c), 6);
  assert.strictEqual(calc('COUNTA("",1)'), 2);
});

test("ROUND ROUNDUP ROUNDDOWN ABS", () => {
  assert.strictEqual(calc("ROUND(2.5,0)"), 3);
  assert.strictEqual(calc("ROUND(-2.5,0)"), -3);              // half away from zero
  assert.strictEqual(calc("ROUND(1.005,2)"), 1.01);           // Excel gives 1.01 (no binary surprise)
  assert.strictEqual(calc("ROUND(1234.567,-2)"), 1200);
  assert.strictEqual(calc("ROUND(3.14159)"), 3);
  assert.strictEqual(calc("ROUNDUP(3.2,0)"), 4);
  assert.strictEqual(calc("ROUNDUP(-3.2,0)"), -4);
  assert.strictEqual(calc("ROUNDDOWN(3.9,0)"), 3);
  assert.strictEqual(calc("ROUNDDOWN(-3.9,0)"), -3);
  assert.strictEqual(calc("ROUNDUP(0.1+0.2,1)"), 0.3);
  assert.strictEqual(calc("ABS(-4)"), 4);
  assert.ok(err("#VALUE!")(calc('ABS("x")')));
});

test("IF AND OR NOT IFERROR", () => {
  assert.strictEqual(calc('IF(1>0,"yes","no")'), "yes");
  assert.strictEqual(calc('IF(0,"yes","no")'), "no");
  assert.strictEqual(calc("IF(FALSE,1)"), false);
  assert.strictEqual(calc("IF(TRUE,,1)"), 0);
  assert.strictEqual(calc('IF("TRUE",1,2)'), 1);
  assert.ok(err("#VALUE!")(calc('IF("maybe",1,2)')));
  assert.strictEqual(calc("IF(A1=0,0,1/A1)", { A1: 0 }), 0);   // the other branch isn't an error
  assert.strictEqual(calc("AND(1,TRUE,2>1)"), true);
  assert.strictEqual(calc("AND(A1:A2)", { A1: true, A2: 0 }), false);
  assert.strictEqual(calc("OR(0,FALSE)"), false);
  assert.strictEqual(calc("OR(A1:A3)", { A1: "x", A2: 1 }), true);   // text in ranges ignored
  assert.ok(err("#VALUE!")(calc("AND(A1)", { A1: "x" })));
  assert.strictEqual(calc("NOT(0)"), true);
  assert.strictEqual(calc("IFERROR(1/0,\"none\")"), "none");
  assert.strictEqual(calc("IFERROR(5,0)"), 5);
  assert.strictEqual(calc("IFERROR(#N/A,)"), 0);
});

test("SUMIF COUNTIF AVERAGEIF and criteria", () => {
  const c = { A1: "Food", A2: "Car", A3: "food", A4: "Fuel", A5: null, A6: "Car hire",
    B1: 10, B2: 200, B3: 30, B4: 40, B5: 50, B6: 60 };
  assert.strictEqual(calc('SUMIF(A1:A6,"Food",B1:B6)', c), 40);      // without case
  assert.strictEqual(calc('SUMIF(A1:A6,"F*",B1:B6)', c), 80);        // wildcards
  assert.strictEqual(calc('SUMIF(A1:A6,"Car*",B1:B6)', c), 260);
  assert.strictEqual(calc('SUMIF(A1:A6,"?ar",B1:B6)', c), 200);
  assert.strictEqual(calc('SUMIF(A1:A6,"<>Car",B1:B6)', c), 190);    // includes the empty A5
  assert.strictEqual(calc('SUMIF(A1:A6,"",B1:B6)', c), 50);          // empty cells
  assert.strictEqual(calc('SUMIF(B1:B6,">=50")', c), 310);
  assert.strictEqual(calc("SUMIF(B1:B6,30)", c), 30);
  assert.strictEqual(calc('SUMIF(A1:A6,"Food",B1)', c), 40);          // the sum range grows to the range's size
  assert.strictEqual(calc('COUNTIF(A1:A6,"car*")', c), 2);
  assert.strictEqual(calc('COUNTIF(B1:B6,">40")', c), 3);
  assert.strictEqual(calc('COUNTIF(B1:B6,"<>40")', c), 5);
  assert.strictEqual(calc('COUNTIF(A1:A6,"~*")', { A1: "*", A2: "x" }), 1);   // ~ escapes
  assert.strictEqual(calc("COUNTIF(A1:A3,\">=b\")", { A1: "a", A2: "b", A3: "c" }), 2);   // text compare
  assert.strictEqual(calc('COUNTIF(A1:A3,"5")', { A1: 5, A2: "5", A3: 6 }), 2);
  assert.strictEqual(calc('AVERAGEIF(A1:A6,"Car*",B1:B6)', c), 130);
  assert.ok(err("#DIV/0!")(calc('AVERAGEIF(A1:A6,"none",B1:B6)', c)));
  assert.strictEqual(calc('SUMIF(A:A,"Car",B:B)', c), 200);           // whole columns
});

test("SUMIFS COUNTIFS AVERAGEIFS", () => {
  const c = { A1: "Car", A2: "Car", A3: "Food", A4: "Car", B1: S.dateSerial(2026, 1, 5), B2: S.dateSerial(2026, 2, 1),
    B3: S.dateSerial(2026, 2, 3), B4: S.dateSerial(2025, 12, 31), C1: 10, C2: 20, C3: 30, C4: 40 };
  assert.strictEqual(calc('SUMIFS(C1:C4,A1:A4,"Car",B1:B4,">=2026-01-01")', c), 30);
  assert.strictEqual(calc('SUMIFS(C1:C4,A1:A4,"Car")', c), 70);
  assert.strictEqual(calc('COUNTIFS(A1:A4,"Car",C1:C4,">15")', c), 2);
  assert.strictEqual(calc('AVERAGEIFS(C1:C4,A1:A4,"Car",C1:C4,"<40")', c), 15);
  assert.ok(err("#VALUE!")(calc('SUMIFS(C1:C3,A1:A4,"Car")', c)));   // ranges must be the same size
  assert.ok(err("#VALUE!")(calc('COUNTIFS(A1:A4,"Car",C1:C4)', c)));
  assert.ok(err("#DIV/0!")(calc('AVERAGEIFS(C1:C4,A1:A4,"Bus")', c)));
});

test("VLOOKUP HLOOKUP", () => {
  const c = { A1: "Apple", B1: 1, C1: "red", A2: "Banana", B2: 2, C2: "yellow", A3: "Cherry", B3: 3, C3: "dark", D1: 10, D2: 20, D3: 30 };
  assert.strictEqual(calc('VLOOKUP("banana",A1:C3,3,FALSE)', c), "yellow");
  assert.strictEqual(calc('VLOOKUP("B*",A1:C3,2,FALSE)', c), 2);          // wildcards in exact mode
  assert.ok(err("#N/A")(calc('VLOOKUP("Kiwi",A1:C3,2,FALSE)', c)));
  assert.ok(err("#REF!")(calc('VLOOKUP("Apple",A1:C3,4,FALSE)', c)));
  assert.ok(err("#VALUE!")(calc('VLOOKUP("Apple",A1:C3,0,FALSE)', c)));
  assert.strictEqual(calc("VLOOKUP(25,D1:D3,1)", c), 20);                 // approximate: largest ≤
  assert.strictEqual(calc("VLOOKUP(30,D1:D3,1,TRUE)", c), 30);
  assert.ok(err("#N/A")(calc("VLOOKUP(5,D1:D3,1)", c)));
  assert.strictEqual(calc("VLOOKUP(99,D1:D3,1)", c), 30);
  const h = { A1: "Jan", B1: "Feb", C1: "Mar", A2: 1, B2: 2, C2: 3 };
  assert.strictEqual(calc('HLOOKUP("Feb",A1:C2,2,FALSE)', h), 2);
  assert.ok(err("#REF!")(calc('HLOOKUP("Feb",A1:C2,3,FALSE)', h)));
});

test("XLOOKUP INDEX MATCH", () => {
  const c = { A1: "Car", A2: "Food", A3: "Rent", B1: 100, B2: 50, B3: 900, D1: 10, D2: 20, D3: 30 };
  assert.strictEqual(calc('XLOOKUP("food",A1:A3,B1:B3)', c), 50);
  assert.strictEqual(calc('XLOOKUP("Bus",A1:A3,B1:B3,"none")', c), "none");
  assert.ok(err("#N/A")(calc('XLOOKUP("Bus",A1:A3,B1:B3)', c)));
  assert.strictEqual(calc("XLOOKUP(25,D1:D3,A1:A3,,-1)", c), "Food");   // next smaller
  assert.strictEqual(calc("XLOOKUP(25,D1:D3,A1:A3,,1)", c), "Rent");    // next larger
  assert.strictEqual(calc('XLOOKUP("R*",A1:A3,B1:B3,,2)', c), 900);     // wildcards
  assert.strictEqual(calc('XLOOKUP("x",A1:A3,B1:B3,,0,-1)', { A1: "x", A2: "x", B1: 1, B2: 2 }), 2);   // from the end
  assert.ok(err("#VALUE!")(calc('XLOOKUP("Car",A1:A3,B1:B2)', c)));
  assert.strictEqual(calc("INDEX(A1:B3,3,2)", c), 900);
  assert.strictEqual(calc("INDEX(B1:B3,2)", c), 50);
  assert.strictEqual(calc("INDEX(A1:C1,2)", { A1: 1, B1: 2, C1: 3 }), 2);
  assert.ok(err("#REF!")(calc("INDEX(A1:B3,4,1)", c)));
  assert.strictEqual(calc('MATCH("Rent",A1:A3,0)', c), 3);
  assert.strictEqual(calc("MATCH(25,D1:D3)", c), 2);
  assert.strictEqual(calc("MATCH(25,D1:D3,-1)", { D1: 30, D2: 20, D3: 10 }), 1);   // smallest ≥, sorted down
  assert.ok(err("#N/A")(calc('MATCH("Bus",A1:A3,0)', c)));
  assert.strictEqual(calc('INDEX(B1:B3,MATCH("Rent",A1:A3,0))', c), 900);
});

test("LEN UPPER LOWER CONCAT CONCATENATE TEXT", () => {
  assert.strictEqual(calc('LEN("hello")'), 5);
  assert.strictEqual(calc("LEN(123.5)"), 5);
  assert.strictEqual(calc('UPPER("abc")'), "ABC");
  assert.strictEqual(calc('LOWER("ÀBC")'), "àbc");
  assert.strictEqual(calc('CONCAT("a",1,TRUE)'), "a1TRUE");
  assert.strictEqual(calc("CONCAT(A1:B2)", { A1: "a", B1: "b", A2: "c", B2: "d" }), "abcd");
  assert.strictEqual(calc('CONCATENATE("x","-",2)'), "x-2");
  assert.strictEqual(calc('TEXT(1234.5,"#,##0.00")'), "1,234.50");
  assert.strictEqual(calc('TEXT(0.256,"0.0%")'), "25.6%");
  assert.strictEqual(calc('TEXT(3,"000")'), "003");
  assert.strictEqual(calc('TEXT(-5,"0;(0)")'), "(5)");
  assert.strictEqual(calc('TEXT(DATE(2026,3,9),"yyyy-mm-dd")'), "2026-03-09");
  assert.strictEqual(calc('TEXT(DATE(2026,3,9),"dddd d mmmm yy")'), "Monday 9 March 26");
  assert.strictEqual(calc('TEXT(12,"""€""0.00")'), "€12.00");
  assert.strictEqual(calc('TEXT("abc","0")'), "abc");
});

test("dates: DATE YEAR MONTH DAY TODAY DAYS EOMONTH NETWORKDAYS", () => {
  assert.strictEqual(S.dateSerial(1900, 1, 1), 1);
  assert.strictEqual(S.dateSerial(1900, 2, 28), 59);
  assert.strictEqual(S.dateSerial(1900, 3, 1), 61);          // Excel's 29 Feb 1900 is serial 60
  assert.strictEqual(S.dateSerial(2026, 1, 1), 46023);
  assert.deepStrictEqual(S.serialParts(60), { y: 1900, m: 2, d: 29, wd: 3 });
  assert.strictEqual(calc("DATE(2026,13,1)"), S.dateSerial(2027, 1, 1));     // months carry over
  assert.strictEqual(calc("DATE(2026,3,0)"), S.dateSerial(2026, 2, 28));
  assert.strictEqual(calc("DATE(26,1,1)"), S.dateSerial(1926, 1, 1));         // years below 1900 add 1900
  assert.strictEqual(calc("YEAR(46023)"), 2026);
  assert.strictEqual(calc("MONTH(46023.75)"), 1);
  assert.strictEqual(calc('DAY("2026-02-14")'), 14);                          // a date typed as text
  assert.ok(err("#NUM!")(calc("YEAR(-1)")));
  assert.strictEqual(calc("TODAY()"), TODAY);
  assert.strictEqual(calc("DAYS(DATE(2026,3,1),DATE(2026,2,1))"), 28);
  assert.strictEqual(calc("EOMONTH(DATE(2026,1,15),1)"), S.dateSerial(2026, 2, 28));
  assert.strictEqual(calc("EOMONTH(DATE(2024,1,31),1)"), S.dateSerial(2024, 2, 29));
  assert.strictEqual(calc("EOMONTH(DATE(2026,3,15),-1)"), S.dateSerial(2026, 2, 28));
  assert.strictEqual(calc("NETWORKDAYS(DATE(2026,10,5),DATE(2026,10,16))"), 10);   // Mon to Fri a week later
  assert.strictEqual(calc("NETWORKDAYS(DATE(2026,10,16),DATE(2026,10,5))"), -10);
  assert.strictEqual(calc("NETWORKDAYS(DATE(2026,10,5),DATE(2026,10,16),A1:A2)", { A1: S.dateSerial(2026, 10, 7), A2: S.dateSerial(2026, 10, 10) }), 9);
  assert.strictEqual(calc("NETWORKDAYS(DATE(2026,10,10),DATE(2026,10,11))"), 0);   // a weekend
});

test("PMT", () => {
  near(calc("PMT(5%/12,60,20000)"), -377.4246729, 1e-6);
  near(calc("PMT(0,10,1000)"), -100);
  near(calc("PMT(0.01,12,0,1000)"), -78.8487887, 1e-6);
  near(calc("PMT(0.01,12,1000,0,1)"), -87.9690977, 1e-6);    // paid at the start of each period
  assert.ok(err("#NUM!")(calc("PMT(0.1,0,100)")));
});

// ---------------------------------------------------------------- the workbook
test("cycles are #CYCLE!, and what reads them gets the error", () => {
  const wb = book({ A1: "=B1", B1: "=A1", C1: "=A1+1", D1: "=D1", E1: 5, F1: "=E1*2" });
  assert.ok(err("#CYCLE!")(wb.value("S", "A1")));
  assert.ok(err("#CYCLE!")(wb.value("S", "B1")));
  assert.ok(err("#CYCLE!")(wb.value("S", "C1")));
  assert.ok(err("#CYCLE!")(wb.value("S", "D1")));
  assert.strictEqual(wb.value("S", "F1"), 10);
  wb.update([["S", "B1", 3]]);                                 // the loop broken
  assert.strictEqual(wb.value("S", "A1"), 3);
  assert.strictEqual(wb.value("S", "C1"), 4);
  const rng = book({ A1: "=SUM(A1:A3)", A2: 1 });                // a range that includes itself
  assert.ok(err("#CYCLE!")(rng.value("S", "A1")));
});

test("recalculation follows dependencies (cells, ranges, whole columns, other tabs)", () => {
  const wb = book({ "S!A1": 1, "S!A2": "=A1*2", "S!A3": "=SUM(A1:A2)", "S!B1": "=SUM(A:A)", "T!A1": "=S!A3+1", "S!C1": 7 }, ["S", "T"]);
  assert.strictEqual(wb.value("T", "A1"), 4);
  let changed = wb.update([["S", "A1", 10]]);
  assert.deepStrictEqual(changed.sort(), ["S!A2", "S!A3", "S!B1", "T!A1"].sort());
  assert.strictEqual(wb.value("S", "B1"), 60);
  assert.strictEqual(wb.value("T", "A1"), 31);
  changed = wb.update([["S", "C1", 8]]);                        // nobody reads C1
  assert.deepStrictEqual(changed, []);
  changed = wb.update([["S", "A9", 5]]);                        // a new cell inside A:A
  assert.deepStrictEqual(changed, ["S!B1"]);
  assert.strictEqual(wb.value("S", "B1"), 65);
  wb.update([["S", "A2", null]]);                               // a formula removed
  assert.strictEqual(wb.value("S", "A3"), 10);
  wb.update([["S", "A2", "=1/0"]]);                             // and an error added
  assert.ok(err("#DIV/0!")(wb.value("T", "A1")));
});

test("text that starts with = can be kept as text", () => {
  const wb = S.workbook();
  wb.setTabs(["S"]);
  wb.set("S", "A1", "=1+1", true);
  wb.set("S", "A2", "=A1&\"!\"");
  wb.recalc();
  assert.strictEqual(wb.value("S", "A1"), "=1+1");
  assert.strictEqual(wb.value("S", "A2"), "=1+1!");
});

test("TODAY is recalculated when the day changes", () => {
  let today = 46000;
  const wb = S.workbook({ today: () => today });
  wb.setTabs(["S"]);
  wb.set("S", "A1", "=TODAY()"); wb.set("S", "A2", "=A1+1"); wb.recalc();
  assert.strictEqual(wb.value("S", "A2"), 46001);
  today = 46001;
  wb.refreshToday();
  assert.strictEqual(wb.value("S", "A2"), 46002);
});

// ---------------------------------------------------------------- typing and formats
test("what typing means", () => {
  assert.deepStrictEqual(S.parseInput("12"), { v: 12 });
  assert.deepStrictEqual(S.parseInput("-1,234.5"), { v: -1234.5 });
  assert.deepStrictEqual(S.parseInput("12%"), { v: 0.12, f: "percent", d: 0 });
  assert.deepStrictEqual(S.parseInput("12.5%"), { v: 0.125, f: "percent", d: 1 });
  assert.deepStrictEqual(S.parseInput("2026-03-09"), { v: S.dateSerial(2026, 3, 9), f: "date" });
  assert.deepStrictEqual(S.parseInput("9/3/2026"), { v: S.dateSerial(2026, 3, 9), f: "date" });   // day first
  assert.deepStrictEqual(S.parseInput("true"), { v: true });
  assert.deepStrictEqual(S.parseInput("'007"), { v: "007", text: true });
  assert.deepStrictEqual(S.parseInput("=SUM(A1)"), { v: "=SUM(A1)" });
  assert.deepStrictEqual(S.parseInput("hello"), { v: "hello" });
  assert.deepStrictEqual(S.parseInput("1.2.3"), { v: "1.2.3" });
  assert.deepStrictEqual(S.parseInput(""), { v: null });
});

test("formats: number, currency, percent, date, red negatives, errors", () => {
  const o = { currency: "EUR", locale: "en-GB" };
  assert.strictEqual(S.format(1234.5, { f: "number", d: 2 }, o).text, "1,234.50");
  assert.strictEqual(S.format(1234.5, { f: "currency" }, o).text, "€1,234.50");
  assert.strictEqual(S.format(1234.5, { f: "currency", d: 0 }, { currency: "USD", locale: "en-US" }).text, "$1,235");
  assert.strictEqual(S.format(0.256, { f: "percent", d: 1 }, o).text, "25.6%");
  assert.strictEqual(S.format(S.dateSerial(2026, 3, 9), { f: "date" }, o).text, "9 Mar 2026");
  assert.deepStrictEqual(S.format(-5, { f: "number", d: 0, red: 1 }, o), { text: "-5", negative: true });
  assert.strictEqual(S.format(-5, { f: "number", d: 0 }, o).negative, false);
  assert.strictEqual(S.format(0.1 + 0.2, null, o).text, "0.3");
  assert.strictEqual(S.format(1e21, null, o).text, "1E+21");
  assert.strictEqual(S.format(S.ERR["#N/A"], null, o).text, "#N/A");
  assert.strictEqual(S.format(true, null, o).text, "TRUE");
  assert.strictEqual(S.format("x", { f: "number" }, o).text, "x");
});

test("formats: accounting, scientific, time, date and time; typed times", () => {
  const o = { currency: "USD", locale: "en-US" };
  assert.strictEqual(S.format(-1234.5, { f: "accounting" }, o).text, "($1,234.50)");
  assert.strictEqual(S.format(1234.5, { f: "accounting" }, o).text, "$1,234.50");
  assert.strictEqual(S.format(-1234.5, { f: "accounting" }, o).negative, false);
  assert.strictEqual(S.format(123456, { f: "scientific" }, o).text, "1.23E+05");
  assert.strictEqual(S.format(0.00042, { f: "scientific", d: 1 }, o).text, "4.2E-04");
  assert.strictEqual(S.format(0.00042, { f: "scientific", d: 1 }, { numStyle: "de" }).text, "4,2E-04");
  assert.strictEqual(S.format(17.5 / 24, { f: "time" }, o).text, "5:30 PM");
  assert.strictEqual(S.format(17.5 / 24, { f: "time" }, { locale: "en-GB" }).text, "17:30");
  assert.strictEqual(S.format(S.dateSerial(2026, 10, 8) + 0.25, { f: "datetime" }, { locale: "en-GB" }).text, "8 Oct 2026 06:00");
  assert.deepStrictEqual(S.parseInput("17:30"), { v: 17.5 / 24, f: "time" });
  assert.deepStrictEqual(S.parseInput("5:30 pm"), { v: 17.5 / 24, f: "time" });
  assert.deepStrictEqual(S.parseInput("12am"), { v: 0, f: "time" });
  assert.deepStrictEqual(S.parseInput("2026-10-08 06:00"), { v: S.dateSerial(2026, 10, 8) + 0.25, f: "datetime" });
  assert.deepStrictEqual(S.parseInput("25:00"), { v: "25:00" });
  assert.deepStrictEqual(S.parseInput("13pm"), { v: "13pm" });
  assert.strictEqual(S.timeOfDay("9"), null);
});

test("addresses", () => {
  assert.strictEqual(S.colName(0), "A");
  assert.strictEqual(S.colName(25), "Z");
  assert.strictEqual(S.colName(26), "AA");
  assert.strictEqual(S.colName(16383), "XFD");
  assert.strictEqual(S.colIndex("AA"), 26);
  assert.deepStrictEqual(S.parseRef("$B$4"), { c: 1, r: 3, ac: true, ar: true });
  assert.strictEqual(S.parseRef("A0"), null);
  assert.deepStrictEqual(S.parseRange("C3:A1"), { c1: 0, r1: 0, c2: 2, r2: 2 });
  assert.strictEqual(S.functionsIn('=SUM(A1)+IF(B1,"MAX(",ROUND(1,0))').join(","), "SUM,IF,ROUND");
});

test("the ƒ list documents every function the engine knows", () => {
  const names = Object.keys(S.FUNCTIONS);
  for (const n of ["SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "ROUND", "ROUNDUP", "ROUNDDOWN", "ABS", "IF", "AND", "OR",
    "NOT", "SUMIF", "COUNTIF", "AVERAGEIF", "TODAY", "DAYS", "LEN", "UPPER", "LOWER", "CONCAT", "SUMIFS", "COUNTIFS", "AVERAGEIFS",
    "VLOOKUP", "HLOOKUP", "XLOOKUP", "INDEX", "MATCH", "IFERROR", "MEDIAN", "TEXT", "DATE", "YEAR", "MONTH", "DAY", "EOMONTH",
    "NETWORKDAYS", "PMT"]) assert.ok(names.includes(n), n);
  for (const n of names) {
    const f = S.FUNCTIONS[n];
    assert.ok(f.desc && f.example.startsWith("="), n);
    const v = calc(f.example.slice(1), { A2: "Car", B2: 120, C2: 3, A20: "x" });
    assert.ok(!(S.isErr(v) && v.code === "#NAME?"), `${n}: its example works (${S.toText(v)})`);
  }
});

// ---------------------------------------------------------------- the benchmark (SPEC §8.2: 50 000 cells < 100 ms)
test("50 000 filled cells recalculate quickly", () => {
  const wb = S.workbook({ today: () => TODAY });
  wb.setTabs(["Data", "Sum"]);
  for (let r = 1; r <= 10000; r++) {          // 10 000 rows × 5 columns: text, 2 numbers, 2 formulas (one a running total)
    wb.set("Data", "A" + r, "Item " + (r % 50));
    wb.set("Data", "B" + r, r % 97);
    wb.set("Data", "C" + r, 1.5);
    wb.set("Data", "D" + r, `=B${r}*C${r}`);
    wb.set("Data", "E" + r, r > 1 ? `=E${r - 1}+D${r}` : `=D${r}`);
  }
  wb.set("Sum", "A1", "=SUM(Data!D:D)");
  wb.set("Sum", "A2", '=SUMIF(Data!A:A,"Item 7",Data!D:D)');
  wb.set("Sum", "A3", "=AVERAGE(Data!B1:B10000)");
  const time = (fn) => { const t = process.hrtime.bigint(); fn(); return Number(process.hrtime.bigint() - t) / 1e6; };
  const first = time(() => wb.recalc());
  const runs = [];
  for (let i = 0; i < 5; i++) runs.push(time(() => wb.recalc()));
  runs.sort((a, b) => a - b);
  const median = runs[2];
  const editTop = time(() => wb.update([["Data", "B1", 5]]));        // 10 000 running totals change
  const editOne = time(() => wb.update([["Data", "C9000", 2]]));
  console.log(`  sheetcalc benchmark: 50 000 cells (20 003 formulas) — first full recalculation ${first.toFixed(1)} ms, ` +
    `then median ${median.toFixed(1)} ms (best ${runs[0].toFixed(1)}); an edit at the top of a 10 000-long chain ${editTop.toFixed(1)} ms, ` +
    `an edit near the end ${editOne.toFixed(1)} ms`);
  assert.strictEqual(wb.formulaCount(), 20003);
  assert.strictEqual(wb.value("Data", "E10000"), wb.value("Sum", "A1"));
  assert.ok(median < 100, `full recalculation ${median} ms`);
  assert.ok(first < 1000, `first recalculation ${first} ms`);
});
