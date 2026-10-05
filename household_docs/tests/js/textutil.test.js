"use strict";
// node --test tests/js — the browser's text helpers (app/static/textutil.js).
const test = require("node:test");
const assert = require("node:assert");
const T = require("../../app/static/textutil.js");

test("secret hint: passwords, authenticator codes, card numbers", () => {
  assert.strictEqual(T.looksSecret("wifi password: hunter2"), "a password");
  assert.strictEqual(T.looksSecret("PIN=1234"), "a password");
  assert.strictEqual(T.looksSecret("otpauth://totp/Example?secret=ABC"), "an authenticator code");
  assert.strictEqual(T.looksSecret("card 4111 1111 1111 1111 exp 12/30"), "a card number");
  assert.strictEqual(T.looksSecret("4111-1111-1111-1111"), "a card number");
  assert.strictEqual(T.looksSecret("order 4111 1111 1111 1112"), null);        // fails the Luhn check
  assert.strictEqual(T.looksSecret("Buy milk\nPassport renewal in May"), null);
  assert.strictEqual(T.looksSecret(""), null);
  assert.strictEqual(T.looksSecret(null), null);
});

test("luhn", () => {
  assert.ok(T.luhn("79927398713"));
  assert.ok(!T.luhn("79927398710"));
});

test("line compare", () => {
  const d = T.lineDiff("a\nb\nc", "a\nx\nc");
  assert.deepStrictEqual(d, [[" ", "a"], ["-", "b"], ["+", "x"], [" ", "c"]]);
  assert.deepStrictEqual(T.lineDiff("", "new"), [["-", ""], ["+", "new"]]);
  assert.deepStrictEqual(T.lineDiff("same", "same"), [[" ", "same"]]);
  assert.strictEqual(T.lineDiff("a\nb", "c\nd", 1), null);
});

test("search snippet marks are split, never parsed", () => {
  assert.deepStrictEqual(T.splitMarks("hello ⁅world⁆ <b>x</b>"),
    [[false, "hello "], [true, "world"], [false, " <b>x</b>"]]);
  assert.deepStrictEqual(T.splitMarks(""), []);
  assert.deepStrictEqual(T.splitMarks("⁅unclosed"), [[false, "⁅unclosed"]]);
});

test("search chips: removing one filter's text from the box", () => {
  assert.strictEqual(T.removeToken("bill type:pdf modified:7d", "type:pdf"), "bill modified:7d");
  assert.strictEqual(T.removeToken('in:"House papers" tax', 'in:"House papers"'), "tax");
  assert.strictEqual(T.removeToken("name:/^a b/ x", "name:/^a b/"), "x");
  assert.strictEqual(T.removeToken("bill", "nothere"), "bill");
  assert.strictEqual(T.removeToken("  a  ", ""), "a");
});

test("search address: params in, params out", () => {
  const arg = T.searchArg({ q: "bill 2026", type: ["pdf", "image"], trash: true, sub: false, size: "", page: 2 });
  assert.strictEqual(arg, "?q=bill+2026&type=pdf%2Cimage&trash=1&page=2");
  assert.deepStrictEqual(T.parseSearchArg(arg), { q: "bill 2026", type: ["pdf", "image"], trash: true, page: 2 });
  assert.deepStrictEqual(T.parseSearchArg("plain words"), { q: "plain words" });
  assert.deepStrictEqual(T.parseSearchArg(""), {});
  assert.strictEqual(T.searchArg({}), "");
  assert.deepStrictEqual(T.parseSearchArg("?page=x&scope=mine,root:ab"), { page: 1, scope: ["mine", "root:ab"] });
});

test("upload clashes ignore case, like Windows", () => {
  assert.deepStrictEqual(T.clashes(["Scan.PDF", "a.txt"], ["scan.pdf", "b.txt", "A.TXT"]), ["scan.pdf", "A.TXT"]);
  assert.deepStrictEqual(T.clashes([], ["x"]), []);
});

test("sizes", () => {
  assert.strictEqual(T.fmtBytes(0), "0 B");
  assert.strictEqual(T.fmtBytes(1536), "1.5 KB");
  assert.strictEqual(T.fmtBytes(5 * 1024 * 1024), "5.0 MB");
  assert.strictEqual(T.fmtBytes(null), "");
});

test("Open in Docs: where a link points (APP_MESSAGES_SPEC §6.5)", () => {
  const panel = "/a1b2c3d4_household_docs";
  assert.strictEqual(T.appLinkHash({ panel, parentPath: panel + "/doc/abc_1-2" }), "#/doc/abc_1-2");
  assert.strictEqual(T.appLinkHash({ panel, parentPath: panel + "/folder/f1" }), "#/folder/f1");
  assert.strictEqual(T.appLinkHash({ panel, parentPath: panel + "/file/x" }), "#/file/x");
  assert.strictEqual(T.appLinkHash({ panel, parentPath: panel + "/quick-note" }), "#quick-note");
  assert.strictEqual(T.appLinkHash({ panel, parentPath: panel }), null);
  assert.strictEqual(T.appLinkHash({ panel, parentPath: "/other_app/doc/abc" }), null);       // not our page
  assert.strictEqual(T.appLinkHash({ panel: null, parentPath: panel + "/doc/abc" }), null);  // page unknown
  for (const bad of ["/doc/..", "/doc/a/b", "/doc/a%2Fb", "/doc/" + "x".repeat(65), "/note/abc", "/doc/a b"]) {
    assert.strictEqual(T.appLinkHash({ panel, parentPath: panel + bad }), null, bad);
  }
  // Home Assistant's own message: route.path, with or without the panel
  assert.strictEqual(T.appLinkHash({ panel, routePath: "/doc/abc" }), "#/doc/abc");
  assert.strictEqual(T.appLinkHash({ panel, routePath: panel + "/doc/abc" }), "#/doc/abc");
  assert.strictEqual(T.appLinkHash({ panel, routePath: "/" }), null);
  // the app's own address (a dashboard button's …/quick-note)
  assert.strictEqual(T.appLinkHash({ ownPath: "/api/hassio_ingress/t0k3n/quick-note" }), "#quick-note");
  assert.strictEqual(T.appLinkHash({ ownPath: "/api/hassio_ingress/t0k3n/" }), null);
  assert.strictEqual(T.appLinkHash(null), null);
});
