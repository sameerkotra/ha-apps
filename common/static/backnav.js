/* backnav.js — the back gesture inside Home Assistant (shared: common/static/backnav.js).
 *
 * Android's back gesture in the Home Assistant app (and a browser's Back) should never leave this app
 * while something is open in it. With this file:
 *   1. Back closes the top dialog / menu / sheet, if one is open;
 *   2. otherwise it returns to the previous in-app page (the "trail", below);
 *   3. with no previous page: to the page's parent (options.parentOf), else to the start page;
 *   4. only Back on the start page, with nothing open and nothing in the trail, leaves for Home Assistant.
 *
 * How: while anything is open, you're away from the start page or the trail has entries, the app keeps
 * exactly one extra history entry (the "guard"). Ingress pages are same-origin iframes, whose entries are
 * part of the HA app's history, so its back gesture pops the guard and fires `popstate` here instead of
 * leaving. In-app navigation must not add history entries of its own: use BackNav.go("#/x") instead of
 * setting location.hash, and links like <a href="#/x"> are handled here (replaceState + a hashchange event).
 *
 * The trail: BackNav.go remembers the address it leaves (consecutive duplicates collapsed, at most 20, kept in
 * sessionStorage per app folder so a reload or another page of the same app keeps it). Entries are "#…"
 * addresses (on the current page) or page URLs (path + search + hash, from BackNav.goPage).
 *
 * The app describes itself once:
 *   BackNav.init({
 *     atHome: () => true when the start page is showing,
 *     goHome: () => show the start page (without adding history),
 *     openLayers: () => the open dialogs/menus, bottom-most first (elements or anything truthy),
 *     closeLayer: (layer) => close that one,
 *     isTab: (hash) => true for a top-level tab address (optional): going to one starts a new trail, so
 *                      Back retraces that tab and then goes home,
 *     parentOf: (hash) => the address Back goes to when the trail is empty (optional; e.g. a deep link to
 *                      a person's page → "#/people"); null/undefined → the start page,
 *   });
 * API:
 *   BackNav.go(hash)                  navigate, remembering the current address
 *   BackNav.go(hash, {replace: true}) / BackNav.replace(hash)
 *                                     navigate WITHOUT remembering (filter, sort or in-page tab changes)
 *   BackNav.goPage(url)               another HTML page of the app (location.replace), remembering this one
 *   BackNav.back()                    an in-app ← button: previous page → parentOf → start page (dialogs
 *                                     are not closed first); → true when it went somewhere
 *   BackNav.sync()                    after a change the DOM observer can't see
 * Changes are noticed by themselves (a DOM observer), so dialogs and pages need no extra calls.
 */
(function () {
  "use strict";
  const TRAIL_MAX = 20;
  let cfg = null;
  let guard = false;          // our extra history entry is in place
  let ignorePops = 0;         // pops we caused ourselves (removing the guard)
  let dropTimer = null;
  let lastHref = location.href;
  let guardBase = null;       // the address under the guard (where Back lands when it pops the guard)
  let scheduled = false;

  // ---------- the trail ----------
  const pathname = () => { const p = location.pathname; if (typeof p === "string") return p; const m = /^[a-z]+:\/\/[^/]*([^?#]*)/i.exec(location.href); return m ? m[1] : ""; };
  const search = () => { const s = location.search; if (typeof s === "string") return s; const m = /\?[^#]*/.exec(location.href); return m ? m[0] : ""; };
  const pagePart = () => pathname() + search();
  const here = () => pagePart() + (location.hash || "");                  // this page's full relative URL
  const current = () => location.hash || pagePart();                      // what go() remembers
  const KEY = "backnav.trail:" + pathname().replace(/[^/]*$/, "");         // one trail per app folder (ingress)
  function load() {
    try {
      const v = JSON.parse(window.sessionStorage.getItem(KEY) || "[]");
      return Array.isArray(v) ? v.filter((s) => typeof s === "string" && s).slice(-TRAIL_MAX) : [];
    } catch (e) { return []; }
  }
  let trail = load();
  function save() { try { window.sessionStorage.setItem(KEY, JSON.stringify(trail)); } catch (e) { /* private mode */ } }
  function isCurrent(entry) { return entry.startsWith("#") ? entry === location.hash : entry === here(); }
  function record(entry) {
    if (!entry || trail[trail.length - 1] === entry) return;
    trail.push(entry);
    if (trail.length > TRAIL_MAX) trail.splice(0, trail.length - TRAIL_MAX);
    save();
  }
  function reset() { if (trail.length) { trail = []; save(); } }
  function popTrail() {
    while (trail.length) { const e = trail.pop(); if (!isCurrent(e)) { save(); return e; } }
    save();
    return null;
  }
  const isTab = (target) => { try { return !!(cfg && cfg.isTab && cfg.isTab(target)); } catch (e) { return false; } };
  const parentOf = () => { try { const p = cfg && cfg.parentOf && cfg.parentOf(location.hash || ""); return p && !isCurrent(p) ? p : null; } catch (e) { return null; } };

  const layers = () => { try { return (cfg && cfg.openLayers && cfg.openLayers()) || []; } catch (e) { return []; } };
  const atHome = () => { try { return !cfg || cfg.atHome(); } catch (e) { return true; } };
  const needGuard = () => layers().length > 0 || !atHome() || trail.length > 0;

  function arm() {
    clearTimeout(dropTimer);
    dropTimer = null;
    if (guard) return;
    try { guardBase = location.href; history.pushState({ backnav: true }, "", location.href); guard = true; } catch (e) { /* sandboxed frame */ }
  }

  function sync() {
    scheduled = false;
    if (!cfg) return;
    lastHref = location.href;
    if (needGuard()) { arm(); return; }
    if (!guard) return;
    // nothing open any more: remove the guard a moment later, unless something opens meanwhile
    // (closing a dialog and opening a page in one go). A timer already waiting isn't restarted, so
    // frequent page updates (a countdown) can't keep postponing it.
    if (dropTimer) return;
    dropTimer = setTimeout(() => {
      dropTimer = null;
      if (guard && !needGuard()) { guard = false; ignorePops++; lastHref = location.href; history.back(); }
    }, 300);
  }
  function soon() { if (!scheduled) { scheduled = true; requestAnimationFrame(sync); } }

  // Show an address without adding history or remembering anything: "#…" on this page, or a page URL.
  function show(entry) {
    let hashPart = entry;
    if (!entry.startsWith("#")) {
      const i = entry.indexOf("#");
      const page = i < 0 ? entry : entry.slice(0, i);
      if (page !== pagePart()) { try { location.replace(entry); } catch (e) { /* ignore */ } return; }
      hashPart = i < 0 ? "" : entry.slice(i);
    }
    const old = location.href;
    if (hashPart && hashPart === location.hash) { window.dispatchEvent(new HashChangeEvent("hashchange")); return; }
    try { history.replaceState(history.state, "", hashPart || pagePart()); } catch (e) { location.hash = hashPart; return; }
    window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: old, newURL: location.href }));
    soon();
  }

  // Back without a layer to close: previous address → parent → start page. → true when it went somewhere.
  function stepBack() {
    const prev = popTrail();
    if (prev) { show(prev); return true; }
    if (!atHome()) {
      const p = parentOf();
      if (p) { show(p); return true; }
      cfg.goHome();
      return true;
    }
    return false;
  }

  window.addEventListener("popstate", () => {
    if (ignorePops) {
      // our own removal of the guard: the entry under it may hold an older address — keep the current
      // one, so the app's hashchange listener stays on the page that's showing
      ignorePops--;
      if (location.href !== lastHref) { try { history.replaceState(history.state, "", lastHref); } catch (e) { /* ignore */ } }
      return;
    }
    // A link or a notification that changed the address (setting location.hash adds a history entry and fires
    // popstate too) isn't the back gesture: leave the new address for the app's hashchange listener to show. Back
    // lands exactly on the address under the guard; anything else is a navigation (with no guard, any change is).
    if (location.href !== lastHref && (!guard || location.href !== guardBase)) {
      lastHref = location.href;
      setTimeout(sync, 0);
      return;
    }
    guard = false;
    // the entry under the guard may hold an older address (the app replaced it since): keep the current one
    if (location.href !== lastHref) { try { history.replaceState(history.state, "", lastHref); } catch (e) { /* ignore */ } }
    if (!cfg) return;
    const open = layers();
    try {
      if (open.length) cfg.closeLayer(open[open.length - 1]);
      else stepBack();
    } catch (e) { /* never break Back */ }
    setTimeout(sync, 0);
  });

  // In-app navigation without history entries of its own
  function go(hash, opts) {
    const target = hash.startsWith("#") ? hash : "#" + hash;
    if (location.hash === target) { window.dispatchEvent(new HashChangeEvent("hashchange")); return; }
    if (cfg && isTab(target)) reset();
    else if (!(opts && opts.replace)) record(current());
    const old = location.href;
    try { history.replaceState(history.state, "", target); } catch (e) { location.hash = target; return; }
    window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: old, newURL: location.href }));
    soon();
  }
  function replace(hash) { go(hash, { replace: true }); }
  function goPage(url) {
    if (isTab(url)) reset(); else record(here());
    location.replace(url);
  }
  function back() {
    if (!cfg) return false;
    let went = false;
    try { went = stepBack(); } catch (e) { /* ignore */ }
    soon();
    return went;
  }

  document.addEventListener("click", (e) => {
    if (!cfg || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    const href = a.getAttribute("href");
    if (href === "#" || href.length < 2) return;
    e.preventDefault();
    go(href);
  });

  function init(options) {
    cfg = options;
    new MutationObserver(soon).observe(document.body, { childList: true, subtree: true, attributes: true,
      attributeFilter: ["hidden", "class", "open", "style", "aria-hidden"] });
    window.addEventListener("hashchange", soon);
    sync();                                 // a non-empty trail (a reload, another page) arms the guard too
  }

  window.BackNav = {
    init, go, replace, goPage, back, sync: soon,
    _trail: { get: () => trail.slice(), clear: reset, key: KEY },   // for tests
  };
})();
