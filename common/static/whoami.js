/* whoami.js — "How the app sees you" and the "No admin yet" banner (shared: common/static/whoami.js).
 *
 * Draws the whoami page from the JSON every app's whoami route returns (common/python/whoami.py; the
 * contract is in WHOAMI_PAGE_SPEC.md), with the same rows and wording in every app, and the
 * first-run "No admin yet" banner. Everything is built with DOM nodes and textContent: names come
 * from request headers and are never parsed as HTML.
 *
 *   const panel = HouseholdWhoami.panel(w, {
 *     appName: "Household Todo",             // for "Settings → Apps → <appName> → … → Restart"
 *     classes: { kv: "kv", row: "kv-row", label: "kv-label", value: "kv-value", copy: "icon-btn",
 *                advice: "hint", hint: "hint" },   // the app's own CSS classes (these are the defaults);
 *                                                  // row: null = label and value straight in kv (a grid)
 *     adviceTag: "p", adviceStyle: "margin-top:8px",
 *     onCopy: (text, button) => …,           // default: clipboard, then ✓ on the button for a moment
 *     onAction: (target) => …,               // an extras row's action button was pressed
 *   });
 *
 *   HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), me.noAdmin, me.username || me.id,
 *                                     { onOpen: openWhoami });          // or { href: "whoami.html" }
 */
(function () {
  "use strict";

  const DEFAULT_CLASSES = { kv: "kv", row: "kv-row", label: "kv-label", value: "kv-value", copy: "icon-btn",
    advice: "hint", hint: "hint" };

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "onclick") node.addEventListener("click", v);
      else if (k === "style") node.style.cssText = v;              // CSSOM: allowed under a strict style-src CSP
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.appendChild(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  const yesNo = (v) => el("strong", null, v ? "Yes" : "No");
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  async function defaultCopy(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      const was = btn.textContent;
      btn.textContent = "✓";
      setTimeout(() => { btn.textContent = was; }, 1200);
    } catch (e) { /* clipboard unavailable (insecure context): the value is on screen to copy by hand */ }
  }

  /* The name to put in admin_users: the login name when Home Assistant sent one, else the user id. */
  function nameToAdd(w) { return (w.nameSent && w.haUsername) || w.haUserId || ""; }

  function extraValue(r, opts) {
    const action = r.action && typeof opts.onAction === "function"
      ? el("button", { type: "button", class: "link-btn", onclick: () => opts.onAction(r.action.target) }, r.action.label)
      : null;
    let value;
    if (typeof r.value === "boolean") value = yesNo(r.value);
    else if (r.value === null || r.value === undefined) value = action ? null : "—";
    else value = String(r.value);
    if (r.tone === "danger") value = el("strong", { style: "color:var(--danger)" }, value);
    return action && value !== null ? [value, " ", action] : (action || value);
  }

  /* The admin advice: four cases, one sentence each. */
  function advice(w, opts) {
    const name = nameToAdd(w);
    const restart = `(Settings → Apps → ${(opts && opts.appName) || "this app"} → Information → Restart)`;
    const orId = w.nameSent && w.haUsername && w.haUserId ? [" (or ", el("strong", null, w.haUserId), ")"] : [];
    const code = () => el("code", null, "admin_users");
    if (w.isAdmin) return el("span", null, "You are an administrator.");
    if (w.displayNameOnly) {
      return el("span", null, "Your ", el("strong", null, "display name"), " is in the ", code(),
        " list, but display names aren't accepted there (anyone could share or take a name). Replace it with ",
        el("strong", null, name), orId, ", save, and ", el("strong", null, "restart"), " the app.");
    }
    if (!w.adminEntries) {
      return el("span", null, "The ", code(), " list is ", el("strong", null, "empty"),
        " in the running app, so nobody is an administrator yet. Add ", el("strong", null, name), " to ", code(),
        " on the app's Configuration tab, save, and ", el("strong", null, "restart"), ` the app ${restart} — the list is only read when the app starts, so if you have already filled it in, a restart is all it needs.`);
    }
    return el("span", null, `Neither your user name nor your user id above matches any of the ${plural(w.adminEntries, "name")} in the `,
      code(), " list. Add ", el("strong", null, name), orId, " exactly as shown, save, and ", el("strong", null, "restart"),
      " the app — the list is only read when the app starts. Upper and lower case don't matter.");
  }

  /* The page body: the table, the advice, then one hint per extras row that has one. */
  function panel(w, opts) {
    opts = opts || {};
    const c = Object.assign({}, DEFAULT_CLASSES, opts.classes || {});
    const onCopy = typeof opts.onCopy === "function" ? opts.onCopy : defaultCopy;
    const copyBtn = (label, text) => {
      if (!text) return null;
      const b = el("button", { type: "button", class: c.copy, title: "Copy", "aria-label": `Copy ${label}` }, opts.copyGlyph || "⧉");
      b.addEventListener("click", () => onCopy(text, b));
      return b;
    };
    const kv = el("div", { class: c.kv });
    const add = (label, value, copy) => {
      const lab = el(c.row ? "div" : "span", { class: c.label }, label);
      const val = el(c.row ? "div" : "span", { class: c.value }, value, copy ? [" ", copyBtn(label, copy)] : null);
      if (c.row) kv.appendChild(el("div", { class: c.row }, lab, val));
      else { kv.appendChild(lab); kv.appendChild(val); }
    };
    const name = w.nameSent ? w.haUsername : null;
    add("User name (sent by Home Assistant)", name || "not sent", name);
    add("User id (sent by Home Assistant)", el("code", null, w.haUserId || ""), w.haUserId);
    add("Display name (not used for matching)", w.haDisplayName || "not sent");
    add("Administrator in this app", yesNo(w.isAdmin));
    add("Names in the app's admin_users", String(w.adminEntries));
    const extras = Array.isArray(w.extras) ? w.extras : [];
    extras.forEach((r) => add(r.label, extraValue(r, opts)));
    const tag = opts.adviceTag || "p";
    const para = (cls, kid) => el(tag, { class: cls, style: opts.adviceStyle || null }, kid);
    return el("div", { class: "whoami-panel" }, kv, para(c.advice, advice(w, opts)),
      extras.filter((r) => r.hint).map((r) => para(c.hint, r.hint)));
  }

  /* "No admin yet — add your Home Assistant user name (<name>) to admin_users …" plus a link to the
     whoami page: { onOpen } for a button, { href } for a link; linkClass / linkId optional. */
  function noAdminBanner(name, opts) {
    opts = opts || {};
    const link = opts.href
      ? el("a", { href: opts.href, class: opts.linkClass || null, id: opts.linkId || null }, "How the app sees you")
      : el("button", { type: "button", class: opts.linkClass || "link-btn", id: opts.linkId || null,
        onclick: () => { if (typeof opts.onOpen === "function") opts.onOpen(); } }, "How the app sees you");
    const frag = document.createDocumentFragment();
    frag.appendChild(el("span", null, el("strong", null, "No admin yet"), " — add your Home Assistant user name (",
      el("strong", null, name || "see “How the app sees you”"), ") to ", el("code", null, "admin_users"),
      " on the app's Configuration tab, save, and restart the app. "));
    frag.appendChild(link);
    return frag;
  }

  /* Show (filled) or hide a banner container. */
  function fillNoAdminBanner(container, show, name, opts) {
    if (!container) return;
    if (!show) { container.hidden = true; container.replaceChildren(); return; }
    container.replaceChildren(noAdminBanner(name, opts));
    container.hidden = false;
  }

  window.HouseholdWhoami = { panel, advice, noAdminBanner, fillNoAdminBanner, nameToAdd };
})();
