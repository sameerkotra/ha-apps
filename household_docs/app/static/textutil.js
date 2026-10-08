"use strict";
/* Household Docs — text helpers with no DOM (tested with Node: tests/js/textutil.test.js).
     DocsText.looksSecret(text)  → "a password" | "an authenticator code" | "a card number" | null — the secret hint
                                   (SPEC §3.3): a light check in the browser; nothing is sent anywhere
     DocsText.lineDiff(a, b)     → [[" " | "-" | "+", line], …] (a = theirs, b = mine), or null when too long
     DocsText.splitMarks(text)   → [[marked, part], …] for search snippets, whose matches the server wraps in ⁅ ⁆
     DocsText.removeToken(q, t)  → the search box without one chip's text (tapping a chip removes its filter)
     DocsText.searchArg(params)  → "?q=…&type=pdf" for the Search page's address (empty values left out)
     DocsText.parseSearchArg(a)  → the Search page's options from its address ("?…", or just the words)
     DocsText.clashes(have, add) → the names in `add` already taken in `have` (case doesn't matter, as on Windows)
     DocsText.fmtBytes(n)        → "1.5 MB"
     DocsText.appLinkHash(o)     → "#/doc/<id>" | "#/folder/<id>" | "#/file/<id>" | "#/view/<token>" | "#quick-note" | null: where an
                                   "Open in Docs" link (APP_MESSAGES_SPEC §6.5) or a dashboard button points, from
                                   {panel, parentPath, ownPath, routePath} — Home Assistant's route.path, the top page's
                                   path (after the panel), or the app's own sub-path */
(function (root) {
  function luhn(digits) {
    let sum = 0, alt = false;
    for (let i = digits.length - 1; i >= 0; i--) {
      let n = digits.charCodeAt(i) - 48;
      if (alt) { n *= 2; if (n > 9) n -= 9; }
      sum += n; alt = !alt;
    }
    return sum % 10 === 0;
  }

  function looksSecret(text) {
    const t = String(text || "");
    if (/\b(pass(word|wd|code)?|pwd|pin)\s*[:=]/i.test(t)) return "a password";
    if (/otpauth:\/\//i.test(t)) return "an authenticator code";
    const cards = t.match(/\b(?:\d[ -]?){12,18}\d\b/g) || [];
    if (cards.some((c) => { const d = c.replace(/\D/g, ""); return d.length >= 13 && d.length <= 19 && luhn(d); })) return "a card number";
    return null;
  }

  function lineDiff(a, b, maxCells = 4e6) {
    const x = String(a).split("\n"), y = String(b).split("\n");
    const n = x.length, m = y.length;
    if (n * m > maxCells) return null;
    const t = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) t[i][j] = x[i] === y[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1]);
    const out = [];
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (x[i] === y[j]) { out.push([" ", x[i]]); i++; j++; }
      else if (t[i + 1][j] >= t[i][j + 1]) { out.push(["-", x[i]]); i++; }
      else { out.push(["+", y[j]]); j++; }
    }
    while (i < n) out.push(["-", x[i++]]);
    while (j < m) out.push(["+", y[j++]]);
    return out;
  }

  function splitMarks(text) {
    const out = [];
    String(text || "").split(/(⁅[^⁆]*⁆)/).forEach((part) => {
      if (part.startsWith("⁅") && part.endsWith("⁆")) out.push([true, part.slice(1, -1)]);
      else if (part) out.push([false, part]);
    });
    return out;
  }

  function removeToken(q, token) {
    const s = String(q || "");
    if (!token) return s.trim();
    const i = s.indexOf(token);
    if (i < 0) return s.trim();
    return (s.slice(0, i) + " " + s.slice(i + token.length)).replace(/\s+/g, " ").trim();
  }

  function searchArg(params) {
    const u = new URLSearchParams();
    Object.keys(params || {}).forEach((k) => {
      const v = params[k];
      if (v === undefined || v === null || v === "" || v === false) return;
      if (Array.isArray(v)) { if (v.length) u.set(k, v.join(",")); return; }
      u.set(k, v === true ? "1" : String(v));
    });
    const s = u.toString();
    return s ? "?" + s : "";
  }

  const LIST_KEYS = ["scope", "type", "ext", "is", "tag"];
  function parseSearchArg(arg) {
    const a = String(arg || "");
    if (!a.startsWith("?")) return a ? { q: a } : {};
    const out = {};
    new URLSearchParams(a.slice(1)).forEach((v, k) => {
      if (LIST_KEYS.includes(k)) out[k] = v.split(",").filter(Boolean);
      else if (k === "trash" || k === "sub") out[k] = v === "1" || v === "true";
      else if (k === "page") out[k] = Math.max(1, parseInt(v, 10) || 1);
      else out[k] = v;
    });
    return out;
  }

  function clashes(have, add) {
    const taken = new Set((have || []).map((n) => String(n).toLowerCase()));
    return (add || []).filter((n) => taken.has(String(n).toLowerCase()));
  }

  function fmtBytes(n) {
    if (n === null || n === undefined) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
    if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + " MB";
    return (n / 1024 ** 3).toFixed(2) + " GB";
  }

  const LINK_ID = /^[A-Za-z0-9_-]{1,64}$/;
  function hashOf(sub) {
    // "/doc/<id>" (and "/folder/…", "/file/…") or "/quick-note" → the app's own route; anything else → null
    const parts = String(sub || "").split("?")[0].split("#")[0].split("/").filter(Boolean);
    if (parts.length === 1 && parts[0] === "quick-note") return "#quick-note";
    if (parts.length === 2 && ["doc", "folder", "file", "view"].includes(parts[0]) && LINK_ID.test(parts[1])) return `#/${parts[0]}/${parts[1]}`;
    return null;
  }
  function appLinkHash(o) {
    const opts = o || {};
    const panel = typeof opts.panel === "string" && /^\/[a-z0-9_]{1,80}$/.test(opts.panel) ? opts.panel : null;
    if (typeof opts.routePath === "string") {                  // home-assistant/properties: route.path
      const rp = opts.routePath;
      const sub = panel && (rp === panel || rp.startsWith(panel + "/")) ? rp.slice(panel.length) : rp;
      const hash = hashOf(sub);
      if (hash) return hash;
    }
    if (panel && typeof opts.parentPath === "string" && opts.parentPath.startsWith(panel + "/")) {
      const hash = hashOf(opts.parentPath.slice(panel.length));
      if (hash) return hash;
    }
    if (typeof opts.ownPath === "string") {                     // the app's own address: …/quick-note
      const m = opts.ownPath.match(/\/(quick-note)\/?$/);
      if (m) return "#quick-note";
    }
    return null;
  }

  const api = { luhn, looksSecret, lineDiff, splitMarks, removeToken, searchArg, parseSearchArg, clashes, fmtBytes,
    appLinkHash };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.DocsText = api;
})(typeof window !== "undefined" ? window : globalThis);
