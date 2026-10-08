"use strict";
// md.js (SPEC §17.2): parsing, rendering with a recording fake document, and a fuzz test — whatever the input,
// rendering makes only the allowed elements, sets text only as text, and never links anything but http(s) and
// in-app document addresses.
const test = require("node:test");
const assert = require("node:assert");
const Md = require("../../app/static/md.js");

const ALLOWED = new Set(["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "blockquote", "pre", "code", "hr",
  "table", "thead", "tbody", "tr", "th", "td", "strong", "em", "a", "input", "span", "br", "div", "img"]);
const ALLOWED_ATTRS = new Set(["href", "target", "rel", "type", "start", "aria-label", "src", "alt", "loading"]);

// A fake document that records everything and refuses anything that could parse HTML.
function fakeDoc(log) {
  function node(tag) {
    const n = {
      tag, children: [], attrs: {}, className: "", title: "", checked: false, disabled: false, _text: "",
      appendChild(c) { this.children.push(c); return c; },
      setAttribute(k, v) { log.attrs.push([tag, k, String(v)]); this.attrs[k] = String(v); },
      addEventListener() {},
      set textContent(v) { this._text = String(v); this.children = []; },
      get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
      set innerHTML(_v) { throw new Error("innerHTML used"); },
      set outerHTML(_v) { throw new Error("outerHTML used"); },
      insertAdjacentHTML() { throw new Error("insertAdjacentHTML used"); },
    };
    return n;
  }
  return {
    createElement(tag) { log.tags.push(tag); return node(tag); },
    createTextNode(v) { return { tag: "#text", textContent: String(v), children: [] }; },
    createDocumentFragment() { return node("#fragment"); },
    write() { throw new Error("document.write used"); },
  };
}
function renderText(src, opts) {
  const log = { tags: [], attrs: [] };
  const frag = Md.render(Md.parse(src), fakeDoc(log), opts || {});
  return { frag, log, text: frag.textContent };
}
function find(n, tag, out = []) {
  if (n.tag === tag) out.push(n);
  (n.children || []).forEach((c) => find(c, tag, out));
  return out;
}

test("blocks: headings, lists, tasks, quotes, code, rules, tables", () => {
  const b = Md.parse("# Title\n\nSome *em* and **strong**\nnext line\n\n- [ ] one\n- [x] two\n  - nested\n1. first\n2. second\n\n> quote\n> - [ ] in quote\n\n```js\n<b>code</b>\n```\n\n---\n\n| a | b:|\n|:-|--:|\n| 1 | 2 |\n");
  assert.deepStrictEqual(b.map((x) => x.t), ["h", "p", "list", "list", "quote", "code", "hr", "table"]);
  assert.strictEqual(b[0].level, 1);
  assert.deepStrictEqual(b[2].items.map((i) => [i.text, i.checked, i.line]), [["one", false, 5], ["two", true, 6]]);
  assert.strictEqual(b[2].items[1].sub[0].items[0].text, "nested");
  assert.strictEqual(b[3].ordered, true);
  assert.strictEqual(b[4].c[1].items[0].line, 12);
  assert.strictEqual(b[5].text, "<b>code</b>");
  assert.deepStrictEqual(b[7].align, ["left", "right"]);
});

test("inline: emphasis, code, links only http(s), [[doc]] links, images not loaded", () => {
  const n = Md.parseInline("a **b** *c* `d` [e](https://example.org/x) [f](javascript:alert(1)) [[Budget]] ![pic](https://x/y.png) https://h.example/p.");
  const kinds = n.map((x) => x.t);
  assert.ok(kinds.includes("strong") && kinds.includes("em") && kinds.includes("code") && kinds.includes("doc") && kinds.includes("img"));
  const links = n.filter((x) => x.t === "link").map((x) => x.href);
  assert.deepStrictEqual(links, ["https://example.org/x", "https://h.example/p"]);
  assert.ok(n.some((x) => x.t === "text" && x.v.includes("javascript:alert(1)")));     // shown as typed, not a link
  assert.strictEqual(Md.safeHref("JavaScript:alert(1)"), null);
  assert.strictEqual(Md.safeHref("data:text/html,<b>"), null);
  assert.strictEqual(Md.safeHref(" https://ok.example/ "), "https://ok.example/");
  assert.strictEqual(Md.safeHref("https://a b"), null);
  assert.strictEqual(Md.safeHref("//evil.example"), null);
  assert.strictEqual(Md.parseInline("snake_case_name")[0].v, "snake_case_name");
  assert.deepStrictEqual(Md.parseInline("\\*not em\\*"), [{ t: "text", v: "*not em*" }]);
});

test("raw HTML is just text", () => {
  const r = renderText("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n<a href=\"javascript:x\">a</a>");
  assert.ok(r.text.includes("<script>alert(1)</script>"));
  assert.ok(r.text.includes("<img src=x onerror=alert(1)>"));
  assert.ok(!r.log.tags.includes("script") && !r.log.tags.includes("img"));
  assert.strictEqual(find(r.frag, "a").length, 0);
});

test("doc links use the resolver; states for no access, deleted and missing", () => {
  const states = { A: { href: "#/doc/abc123", title: "A.txt" }, B: { state: "noaccess" }, C: { state: "deleted" }, D: { state: "missing" },
    E: { href: "javascript:alert(1)" } };
  const r = renderText("[[A]] [[B]] [[C]] [[D]] [[E]]", { docLink: (t) => states[t] });
  const a = find(r.frag, "a");
  assert.strictEqual(a.length, 1);
  assert.strictEqual(a[0].attrs.href, "#/doc/abc123");
  assert.ok(r.text.includes("🔒 No access") && r.text.includes("Deleted") && r.text.includes("Not found"));
  assert.deepStrictEqual(Md.docLinks("x [[A]] [[B]] [[A]] [[no]]"), ["A", "B", "no"]);
});

test("tick boxes: rendered, and toggled in the source by line", () => {
  const src = "# List\n- [ ] milk\n  - [x] cold\n> - [ ] quoted";
  const r = renderText(src, { onTick: () => {} });
  const boxes = find(r.frag, "input");
  assert.deepStrictEqual(boxes.map((b) => b.checked), [false, true, false]);
  assert.strictEqual(Md.toggleTask(src, 1), "# List\n- [x] milk\n  - [x] cold\n> - [ ] quoted");
  assert.strictEqual(Md.toggleTask(src, 2), "# List\n- [ ] milk\n  - [ ] cold\n> - [ ] quoted");
  assert.strictEqual(Md.toggleTask(src, 3), "# List\n- [ ] milk\n  - [x] cold\n> - [x] quoted");
  assert.strictEqual(Md.toggleTask(src, 0), src);
  const lines = Md.parse(src);
  assert.strictEqual(lines[1].items[0].line, 1);
  assert.strictEqual(lines[1].items[0].sub[0].items[0].line, 2);
  assert.strictEqual(lines[2].c[0].items[0].line, 3);
});

test("fuzz: never anything but the allowed elements, safe links, text as text", () => {
  const pieces = ["<script>", "</script>", "<img src=x onerror=alert(1)>", "javascript:", "[", "]", "(", ")", "[[", "]]",
    "**", "*", "_", "__", "`", "```", "\n", "\n\n", "# ", "- ", "- [ ] ", "- [x] ", "1. ", "> ", "|", "|---|", "---",
    "http://", "https://a.example/", "data:text/html,", "vbscript:", "<a href=\"javascript:x\">", "&lt;", "&#106;avascript:",
    "![x](javascript:alert(1))", "[x](  javascript:alert(1)  )", "<https://ok.example>", "<javascript:alert(1)>", "\\", "  ",
    "\t", "word", "ünï", "😀", "on", "click=", "style=", "\u0000", " "];
  let seed = 12345;
  const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
  for (let k = 0; k < 3000; k++) {
    let src = "";
    const len = 1 + rnd(40);
    for (let j = 0; j < len; j++) src += pieces[rnd(pieces.length)];
    const log = { tags: [], attrs: [] };
    let frag;
    assert.doesNotThrow(() => { frag = Md.render(Md.parse(src), fakeDoc(log), { docLink: () => ({ href: "#/doc/x1" }), onTick: () => {}, images: k % 2 === 0 }); }, src);
    for (const t of log.tags) assert.ok(ALLOWED.has(t), `element ${t} from ${JSON.stringify(src)}`);
    for (const [tag, k2, v] of log.attrs) {
      assert.ok(ALLOWED_ATTRS.has(k2), `attribute ${k2} from ${JSON.stringify(src)}`);
      assert.ok(!/^on/i.test(k2));
      if (k2 === "href") assert.ok(/^https?:\/\//.test(v) || /^#\/(doc|folder)\/[A-Za-z0-9:_-]+$/.test(v), `href ${v} from ${JSON.stringify(src)}`);
      if (k2 === "type") assert.strictEqual(v, "checkbox");
      if (k2 === "src") assert.ok(/^api\/nodes\/[A-Za-z0-9_-]+\/preview$/.test(v), `src ${v} from ${JSON.stringify(src)}`);
      void tag;
    }
    for (const a of find(frag, "a")) assert.ok(!/^\s*(javascript|data|vbscript):/i.test(a.attrs.href));
  }
});

test("pictures: only ones kept in the app, only when asked for", () => {
  const src = "![Receipt](doc:abc123) ![x](https://tracker.example/p.png) ![y](doc:../x)";
  const on = renderText(src, { images: true });
  const imgs = find(on.frag, "img");
  assert.strictEqual(imgs.length, 1);
  assert.deepStrictEqual([imgs[0].attrs.src, imgs[0].attrs.alt], ["api/nodes/abc123/preview", "Receipt"]);
  assert.ok(on.text.includes("🖼 x") && on.text.includes("🖼 y"));
  assert.strictEqual(find(renderText(src).frag, "img").length, 0);          // not without opts.images
});

test("deep nesting and long input stay bounded", () => {
  const deep = "> ".repeat(200) + "x\n" + "  ".repeat(300) + "- y\n" + "*".repeat(5000) + "\n" + "[".repeat(3000);
  const t0 = Date.now();
  const r = renderText(deep);
  assert.ok(Date.now() - t0 < 3000);
  assert.ok(r.text.length > 0);
});
