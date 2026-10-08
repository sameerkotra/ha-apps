"use strict";
/* Household Docs — Markdown for notes (SPEC §17.2), written for the app, no third-party code.

     DocsMd.parse(text)                  → blocks (plain objects, no DOM): headings, paragraphs, lists (with tick
                                           boxes, nested by indent), quotes, code blocks, rules, simple tables
     DocsMd.parseInline(text)            → inline nodes: text, strong, em, code, link (http/https only), doc ([[…]]),
                                           br, img (shown as "🖼 alt"; only a picture kept in the app itself,
                                           ![alt](doc:<id>), is shown, and only when opts.images is on)
     DocsMd.render(blocks, doc, opts)    → a DocumentFragment built with doc.createElement / createTextNode only
     DocsMd.safeHref(url)                → the URL when it is http: or https:, else null
     DocsMd.toggleTask(text, line)       → the text with that line's [ ] ↔ [x] switched (ticking in the preview)
     DocsMd.docLinks(text)               → the [[link]] texts in order

   Nothing is ever parsed as HTML: raw HTML in a note is just text (it shows as typed). Links are made only for
   http: and https: addresses and for [[document links]], which opts.docLink(text) turns into an in-app address
   (or a "🔒 No access" / "Deleted" / "Not found" note). Pictures from elsewhere are never fetched: only one
   kept in Household Docs (![alt](doc:<id>), what pasting a picture into a note writes) is shown, from the app's
   own image preview (api/nodes/<id>/preview). Tested with Node
   (tests/js/md.test.js), including a fuzz test that renders random input with a recording fake document. */
(function (root) {
  const MAX_DEPTH = 6;
  const LINK_TEXT = /\[\[([^\[\]\n|]{1,200})\]\]/g;

  function safeHref(url) {
    const u = String(url || "").trim();
    if (!u || /[\s\u0000-\u001f\u007f<>"'`\\]/.test(u)) return null;
    if (!/^https?:\/\//i.test(u)) return null;
    try {
      const p = new URL(u);
      return p.protocol === "http:" || p.protocol === "https:" ? p.href : null;
    } catch (e) { return null; }
  }

  // ---------------------------------------------------------------- inline
  const PUNCT = "\\`*_{}[]()#+-.!|~<>\"'";
  function parseInline(src, depth = 0) {
    const s = String(src || "");
    const out = [];
    let buf = "";
    const flush = () => { if (buf) { out.push({ t: "text", v: buf }); buf = ""; } };
    let i = 0;
    while (i < s.length) {
      const c = s[i];
      if (c === "\\" && i + 1 < s.length && PUNCT.includes(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
      if (c === "\n") { flush(); out.push({ t: "br" }); i += 1; continue; }
      if (c === "`") {
        let n = 1;
        while (s[i + n] === "`") n += 1;
        const fence = "`".repeat(n);
        const end = s.indexOf(fence, i + n);
        if (end > i) { flush(); out.push({ t: "code", v: s.slice(i + n, end).replace(/^ (.*) $/, "$1") }); i = end + n; continue; }
        buf += fence; i += n; continue;
      }
      if (c === "[" && s[i + 1] === "[") {
        const end = s.indexOf("]]", i + 2);
        const inner = end > 0 ? s.slice(i + 2, end) : "";
        if (end > 0 && inner.trim() && inner.length <= 200 && !/[\[\]\n|]/.test(inner)) {
          flush(); out.push({ t: "doc", text: inner.trim() }); i = end + 2; continue;
        }
      }
      if ((c === "!" && s[i + 1] === "[") || c === "[") {
        const img = c === "!";
        const open = img ? i + 1 : i;
        const close = findClose(s, open, "[", "]");
        if (close > 0 && s[close + 1] === "(") {
          const pclose = findClose(s, close + 1, "(", ")");
          if (pclose > 0) {
            const label = s.slice(open + 1, close);
            const url = s.slice(close + 2, pclose).trim().split(/\s+/)[0] || "";
            flush();
            if (img) {
              const own = /^doc:([A-Za-z0-9_-]{1,64})$/.exec(url);
              out.push(own ? { t: "img", alt: label, id: own[1] } : { t: "img", alt: label });
            }
            else {
              const href = safeHref(url);
              const kids = depth < MAX_DEPTH ? parseInline(label, depth + 1).filter((x) => x.t !== "link") : [{ t: "text", v: label }];
              if (href) out.push({ t: "link", href, c: kids });
              else out.push({ t: "text", v: s.slice(i, pclose + 1) });     // not a web address: shown as typed
            }
            i = pclose + 1;
            continue;
          }
        }
      }
      if (c === "<") {
        const m = /^<(https?:\/\/[^\s<>]+)>/i.exec(s.slice(i));
        if (m && safeHref(m[1])) { flush(); out.push({ t: "link", href: safeHref(m[1]), c: [{ t: "text", v: m[1] }] }); i += m[0].length; continue; }
      }
      if ((c === "h" || c === "H") && /^https?:\/\//i.test(s.slice(i, i + 8)) && (i === 0 || /[\s(]/.test(s[i - 1]))) {
        const m = /^https?:\/\/[^\s<>"]+/i.exec(s.slice(i));
        const url = m ? m[0].replace(/[.,;:!?)\]]+$/, "") : "";
        const href = url ? safeHref(url) : null;
        if (href) { flush(); out.push({ t: "link", href, c: [{ t: "text", v: url }] }); i += url.length; continue; }
      }
      if ((c === "*" || c === "_") && depth < MAX_DEPTH) {
        const dbl = s[i + 1] === c;
        const mark = dbl ? c + c : c;
        const leftOk = i + mark.length < s.length && !/\s/.test(s[i + mark.length]);
        const wordInside = c === "_" && i > 0 && /\w/.test(s[i - 1]);
        if (leftOk && !wordInside) {
          let j = i + mark.length;
          let end = -1;
          while (j < s.length) {
            const k = s.indexOf(mark, j);
            if (k < 0) break;
            if (!/\s/.test(s[k - 1]) && (dbl || s[k + 1] !== c) && !(c === "_" && /\w/.test(s[k + mark.length] || ""))) { end = k; break; }
            j = k + 1;
          }
          if (end >= i + mark.length) {
            const inner = s.slice(i + mark.length, end);
            if (inner) {
              flush();
              out.push({ t: dbl ? "strong" : "em", c: parseInline(inner, depth + 1) });
              i = end + mark.length;
              continue;
            }
          }
        }
      }
      buf += c;
      i += 1;
    }
    flush();
    return out;
  }
  function findClose(s, at, open, close) {
    let depth = 0;
    for (let i = at; i < s.length && i < at + 2000; i++) {
      if (s[i] === "\\") { i += 1; continue; }
      if (s[i] === open) depth += 1;
      else if (s[i] === close) { depth -= 1; if (depth === 0) return i; }
      else if (s[i] === "\n" && s[i + 1] === "\n") return -1;
    }
    return -1;
  }

  // ---------------------------------------------------------------- blocks
  const RX = {
    fence: /^\s{0,3}(`{3,}|~{3,})\s*([\w+-]*)/,
    head: /^\s{0,3}(#{1,6})(?:\s+(.*?))?\s*#*\s*$/,
    hr: /^\s{0,3}([-*_])(\s*\1){2,}\s*$/,
    quote: /^\s{0,3}>\s?(.*)$/,
    item: /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/,
    task: /^\[([ xX])\]\s+(.*)$/,
    tableSep: /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/,
  };
  function cells(line) {
    let t = line.trim();
    if (t.startsWith("|")) t = t.slice(1);
    if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
    return t.split(/(?<!\\)\|/).map((x) => x.trim().replace(/\\\|/g, "|"));
  }
  function parse(text, depth = 0, lineOffset = 0) {
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let para = null;
    const endPara = () => { if (para) { blocks.push({ t: "p", c: parseInline(para.join("\n")) }); para = null; } };
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if ((m = RX.fence.exec(line))) {
        endPara();
        const fence = m[1];
        const code = [];
        i += 1;
        while (i < lines.length && !lines[i].trim().startsWith(fence[0].repeat(fence.length))) { code.push(lines[i]); i += 1; }
        blocks.push({ t: "code", text: code.join("\n"), lang: m[2] || "" });
        i += 1;
        continue;
      }
      if (!line.trim()) { endPara(); i += 1; continue; }
      if ((m = RX.head.exec(line)) && (m[2] !== undefined || /^\s{0,3}#{1,6}\s*$/.test(line))) {
        endPara();
        blocks.push({ t: "h", level: m[1].length, c: parseInline(m[2] || "") });
        i += 1;
        continue;
      }
      if (RX.hr.test(line) && !(para && /^\s{0,3}-+\s*$/.test(line))) {
        endPara(); blocks.push({ t: "hr" }); i += 1; continue;
      }
      if (RX.quote.test(line)) {
        endPara();
        const q = [];
        const start = i;
        while (i < lines.length && RX.quote.test(lines[i])) { q.push(RX.quote.exec(lines[i])[1]); i += 1; }
        blocks.push({ t: "quote", c: depth < MAX_DEPTH ? parse(q.join("\n"), depth + 1, lineOffset + start) : [] });
        continue;
      }
      if (line.includes("|") && i + 1 < lines.length && RX.tableSep.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        endPara();
        const head = cells(line);
        const align = cells(lines[i + 1]).map((c) => (/^:-+:$/.test(c) ? "center" : /-+:$/.test(c) ? "right" : /^:-+/.test(c) ? "left" : null));
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(cells(lines[i])); i += 1; }
        const width = Math.min(50, head.length);
        blocks.push({ t: "table", align: align.slice(0, width), head: head.slice(0, width).map((c) => parseInline(c)),
          rows: rows.slice(0, 1000).map((r) => Array.from({ length: width }, (_x, k) => parseInline(r[k] || ""))) });
        continue;
      }
      if ((m = RX.item.exec(line))) {
        endPara();
        const res = parseList(lines, i, depth, lineOffset);
        blocks.push(res.block);
        i = res.next;
        continue;
      }
      if (!para) para = [];
      para.push(line.replace(/^\s+/, ""));
      i += 1;
    }
    endPara();
    return blocks;
  }
  function indentOf(s) { return s.replace(/\t/g, "    ").length; }
  function parseList(lines, start, depth, lineOffset) {
    const first = RX.item.exec(lines[start]);
    const base = indentOf(first[1]);
    const ordered = /\d/.test(first[2]);
    const block = { t: "list", ordered, start: ordered ? parseInt(first[2], 10) : 1, items: [] };
    let i = start;
    while (i < lines.length) {
      const m = RX.item.exec(lines[i]);
      if (!m) {
        if (!lines[i].trim()) {
          const nx = i + 1 < lines.length ? RX.item.exec(lines[i + 1]) : null;
          if (nx && indentOf(nx[1]) >= base && /\d/.test(nx[2]) === ordered) { i += 1; continue; }
          break;
        }
        const last = block.items[block.items.length - 1];
        if (last && indentOf(/^\s*/.exec(lines[i])[0]) > base && !RX.fence.test(lines[i])) {   // a continuation line
          last.text += "\n" + lines[i].trim();
          last.c = parseInline(last.text);
          i += 1;
          continue;
        }
        break;
      }
      const ind = indentOf(m[1]);
      if (ind < base) break;
      if (ind >= base + 2) {
        const last = block.items[block.items.length - 1];
        if (last && depth < MAX_DEPTH) {
          const res = parseList(lines, i, depth + 1, lineOffset);
          last.sub = (last.sub || []).concat([res.block]);
          i = res.next;
          continue;
        }
      }
      if (/\d/.test(m[2]) !== ordered) break;
      const task = /[-*+]/.test(m[2]) ? RX.task.exec(m[3]) : null;
      const text = task ? task[2] : m[3];
      block.items.push({ checked: task ? task[1] !== " " : null, line: lineOffset + i, text, c: parseInline(text), sub: null });
      i += 1;
    }
    return { block, next: i };
  }

  function toggleTask(text, line) {
    const lines = String(text).split("\n");
    if (line < 0 || line >= lines.length) return text;
    const m = /^(\s*(?:>\s?)*\s*[-*+]\s+\[)([ xX])(\]\s)/.exec(lines[line]);
    if (!m) return text;
    lines[line] = m[1] + (m[2] === " " ? "x" : " ") + m[3] + lines[line].slice(m[0].length);
    return lines.join("\n");
  }

  function docLinks(text) {
    const out = [];
    String(text || "").replace(LINK_TEXT, (_m, t) => { t = t.trim(); if (t && !out.includes(t)) out.push(t); return ""; });
    return out;
  }

  // ---------------------------------------------------------------- DOM (only createElement / createTextNode)
  function render(blocks, doc, opts = {}) {
    const frag = doc.createDocumentFragment();
    const el = (tag, cls) => { const e = doc.createElement(tag); if (cls) e.className = cls; return e; };
    const text = (v) => doc.createTextNode(String(v));
    function inline(nodes, parent, depth = 0) {
      for (const n of nodes) {
        if (depth > MAX_DEPTH + 2) { parent.appendChild(text(n.v || "")); continue; }
        if (n.t === "text") parent.appendChild(text(n.v));
        else if (n.t === "br") parent.appendChild(el("br"));
        else if (n.t === "code") { const c = el("code"); c.textContent = n.v; parent.appendChild(c); }
        else if (n.t === "strong" || n.t === "em") { const e = el(n.t); inline(n.c, e, depth + 1); parent.appendChild(e); }
        else if (n.t === "img" && n.id && opts.images && /^[A-Za-z0-9_-]{1,64}$/.test(n.id)) {
          const im = el("img", "md-pic");
          im.setAttribute("src", "api/nodes/" + n.id + "/preview");
          im.setAttribute("alt", n.alt || "picture");
          im.setAttribute("loading", "lazy");
          parent.appendChild(im);
        }
        else if (n.t === "img") { const s = el("span", "md-img"); s.textContent = "🖼 " + (n.alt || "image"); s.title = "Pictures in notes aren't loaded"; parent.appendChild(s); }
        else if (n.t === "link") {
          const href = safeHref(n.href);
          if (!href) { inline(n.c, parent, depth + 1); continue; }
          const a = el("a", "md-link");
          a.setAttribute("href", href);
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener noreferrer nofollow");
          inline(n.c, a, depth + 1);
          parent.appendChild(a);
        } else if (n.t === "doc") {
          const info = opts.docLink ? opts.docLink(n.text) : null;
          if (info && info.href && /^#\/(doc|folder)\/[A-Za-z0-9:_-]+$/.test(info.href)) {
            const a = el("a", "doc-link");
            a.setAttribute("href", info.href);
            a.textContent = n.text;
            if (info.title) a.title = info.title;
            parent.appendChild(a);
          } else {
            const s = el("span", "doc-link " + ((info && info.state) || "pending"));
            s.textContent = n.text;
            const why = info && { noaccess: "🔒 No access", deleted: "Deleted", missing: "Not found" }[info.state];
            if (why) { const w = el("span", "doc-link-why"); w.textContent = " " + why; s.appendChild(w); }
            parent.appendChild(s);
          }
        }
      }
    }
    function list(b, parent, depth) {
      const l = el(b.ordered ? "ol" : "ul", b.items.some((x) => x.checked !== null) ? "md-tasks" : null);
      if (b.ordered && b.start !== 1) l.setAttribute("start", String(Math.max(0, Math.min(1e9, b.start))));
      for (const it of b.items) {
        const li = el("li");
        if (it.checked !== null) {
          li.className = "md-task" + (it.checked ? " done" : "");
          const box = el("input");
          box.setAttribute("type", "checkbox");
          box.checked = !!it.checked;
          if (!opts.onTick) box.disabled = true;
          else box.addEventListener("change", () => opts.onTick(it.line, box.checked));
          box.setAttribute("aria-label", it.text.slice(0, 80));
          li.appendChild(box);
          li.appendChild(text(" "));
        }
        inline(it.c, li);
        for (const sub of it.sub || []) if (depth < MAX_DEPTH) list(sub, li, depth + 1);
        l.appendChild(li);
      }
      parent.appendChild(l);
    }
    function blocksTo(bs, parent, depth) {
      for (const b of bs) {
        if (b.t === "h") { const e = el("h" + Math.min(6, Math.max(1, b.level))); inline(b.c, e); parent.appendChild(e); }
        else if (b.t === "p") { const e = el("p"); inline(b.c, e); parent.appendChild(e); }
        else if (b.t === "hr") parent.appendChild(el("hr"));
        else if (b.t === "code") { const pre = el("pre"); const c = el("code"); c.textContent = b.text; pre.appendChild(c); parent.appendChild(pre); }
        else if (b.t === "quote") { const q = el("blockquote"); if (depth < MAX_DEPTH) blocksTo(b.c, q, depth + 1); parent.appendChild(q); }
        else if (b.t === "list") list(b, parent, depth);
        else if (b.t === "table") {
          const wrap = el("div", "md-table-wrap");
          const t = el("table", "md-table");
          const th = el("thead"), tr = el("tr");
          b.head.forEach((c, k) => { const x = el("th"); if (b.align[k]) x.className = "al-" + b.align[k]; inline(c, x); tr.appendChild(x); });
          th.appendChild(tr); t.appendChild(th);
          const tb = el("tbody");
          for (const r of b.rows) {
            const row = el("tr");
            r.forEach((c, k) => { const x = el("td"); if (b.align[k]) x.className = "al-" + b.align[k]; inline(c, x); row.appendChild(x); });
            tb.appendChild(row);
          }
          t.appendChild(tb); wrap.appendChild(t); parent.appendChild(wrap);
        }
      }
    }
    blocksTo(blocks, frag, 0);
    return frag;
  }

  const api = { parse, parseInline, render, safeHref, toggleTask, docLinks };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DocsMd = api;
})(typeof window !== "undefined" ? window : globalThis);
