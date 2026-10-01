/* Simple formatting (SPEC §16.4): *bold* _italic_ ~strike~ `code` ```block```, "- " / "1. " lists and "> " quotes.
   Text is turned into tokens and then DOM nodes — never HTML strings. Links are auto-linked (outside code),
   @mentions highlighted. The stored text stays exactly as typed. */
"use strict";

const WORD = /[\p{L}\p{N}]/u;
const URL_RE = /^(?:https?:\/\/|www\.)[^\s<>"]+/i;

function safeUrl(u) {
  let url = u;
  if (/^www\./i.test(url)) url = "https://" + url;
  try {
    const p = new URL(url);
    if (["http:", "https:", "mailto:", "tel:"].includes(p.protocol)) return p.href;
  } catch (e) { /* not a URL */ }
  return null;
}
function trimUrl(u) {
  // leave trailing punctuation out of the link; keep balanced parentheses
  let out = u;
  while (/[.,!?;:'"\]]$/.test(out) || (out.endsWith(")") && (out.match(/\(/g) || []).length < (out.match(/\)/g) || []).length)) out = out.slice(0, -1);
  return out;
}
function linkEl(url, text) {
  const href = safeUrl(url);
  if (!href) return document.createTextNode(text);
  return h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
}

function inline(s, ctx, depth = 0) {
  const out = [];
  let buf = "";
  const flush = () => { if (buf) { out.push(document.createTextNode(buf)); buf = ""; } };
  const names = ctx.names || [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    const prev = i > 0 ? s[i - 1] : "";
    // code span: nothing inside is formatted
    if (c === "`") {
      const j = s.indexOf("`", i + 1);
      if (j > i + 1) { flush(); out.push(h("code", null, s.slice(i + 1, j))); i = j + 1; continue; }
    }
    // links
    if ((c === "h" || c === "H" || c === "w" || c === "W") && !WORD.test(prev)) {
      const m = URL_RE.exec(s.slice(i));
      if (m) {
        const u = trimUrl(m[0]);
        if (u.length > 4) { flush(); out.push(linkEl(u, u)); i += u.length; continue; }
      }
    }
    // mentions
    if (c === "@" && !WORD.test(prev)) {
      const rest = s.slice(i + 1);
      const lower = rest.toLowerCase();
      const hit = ["everyone", ...names].find((n) => n && lower.startsWith(n.toLowerCase()) && !WORD.test(rest[n.length] || ""));
      if (hit) {
        flush();
        const me = ctx.myName && hit.toLowerCase() === ctx.myName.toLowerCase();
        out.push(h("span", { class: "mention" + (me || hit === "everyone" ? " me" : "") }, "@" + rest.slice(0, hit.length)));
        i += 1 + hit.length; continue;
      }
    }
    // *bold* _italic_ ~strike~
    if ((c === "*" || c === "_" || c === "~") && depth < 6 && !WORD.test(prev) && s[i + 1] && !/\s/.test(s[i + 1]) && s[i + 1] !== c) {
      let j = i + 2;
      let found = -1;
      while (j < s.length) {
        const k = s.indexOf(c, j);
        if (k < 0) break;
        if (!/\s/.test(s[k - 1]) && !WORD.test(s[k + 1] || "")) { found = k; break; }
        j = k + 1;
      }
      if (found > 0) {
        flush();
        const tag = c === "*" ? "strong" : c === "_" ? "em" : "s";
        out.push(h(tag, null, inline(s.slice(i + 1, found), ctx, depth + 1)));
        i = found + 1; continue;
      }
    }
    buf += c; i += 1;
  }
  flush();
  return out;
}

function renderText(text, ctx = {}) {
  const frag = h("div", { class: "fmt" });
  const lines = String(text || "").split("\n");
  let i = 0;
  let para = [];
  const flushPara = () => {
    if (!para.length) return;
    const p = h("div", { class: "p" });
    para.forEach((ln, k) => { if (k) p.appendChild(h("br")); inline(ln, ctx).forEach((n) => p.appendChild(n)); });
    frag.appendChild(p); para = [];
  };
  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (t.startsWith("```")) {
      flushPara();
      if (t.length > 6 && t.endsWith("```")) { frag.appendChild(h("pre", null, t.slice(3, -3))); i += 1; continue; }
      const body = [t.slice(3)];
      let j = i + 1;
      let closed = false;
      while (j < lines.length) {
        const tj = lines[j].trimEnd();
        if (tj.endsWith("```")) { body.push(tj.slice(0, -3)); closed = true; break; }
        body.push(lines[j]); j += 1;
      }
      if (closed) {
        frag.appendChild(h("pre", null, body.join("\n").replace(/^\n+|\n+$/g, "")));
        i = j + 1; continue;
      }
    }
    let m;
    if ((m = /^\s*[-•]\s+(.*)$/.exec(line))) {
      flushPara();
      const ul = h("ul");
      while (i < lines.length && (m = /^\s*[-•]\s+(.*)$/.exec(lines[i]))) { ul.appendChild(h("li", null, inline(m[1], ctx))); i += 1; }
      frag.appendChild(ul); continue;
    }
    if ((m = /^\s*(\d{1,4})\.\s+(.*)$/.exec(line))) {
      flushPara();
      const ol = h("ol", { start: m[1] !== "1" ? m[1] : null });
      while (i < lines.length && (m = /^\s*\d{1,4}\.\s+(.*)$/.exec(lines[i]))) { ol.appendChild(h("li", null, inline(m[1], ctx))); i += 1; }
      frag.appendChild(ol); continue;
    }
    if ((m = /^>\s?(.*)$/.exec(line))) {
      flushPara();
      const bq = h("blockquote");
      let first = true;
      while (i < lines.length && (m = /^>\s?(.*)$/.exec(lines[i]))) {
        if (!first) bq.appendChild(h("br"));
        inline(m[1], ctx).forEach((n) => bq.appendChild(n)); first = false; i += 1;
      }
      frag.appendChild(bq); continue;
    }
    para.push(line); i += 1;
  }
  flushPara();
  return frag;
}

// Text without formatting marks (previews, pin bar, sublines)
function plainText(t) {
  return String(t || "").replace(/```|[*_~`]/g, "").replace(/^\s*(?:[-•]|\d+\.|>)\s+/gm, "").replace(/\s+/g, " ").trim();
}

// The Aa help
function formattingHelp() {
  const row = (syntax, sample) => h("tr", null, h("td", { class: "mono" }, syntax), h("td", null, renderText(sample)));
  return h("table", { class: "table fmt-help" }, h("tbody", null,
    row("*bold*", "*bold*"), row("_italic_", "_italic_"), row("~strike~", "~strike~"), row("`code`", "`code`"),
    row("```block```", "```a block of code```"), row("- item", "- item"), row("1. item", "1. item"), row("> quote", "> quote"),
    row("@Name", "@everyone")));
}
