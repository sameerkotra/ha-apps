// Shared file: edit common/static/backnav.js and run tools/sync_common.py; don't edit this copy. sha256=dae35411b957c2e0cc2053f723cd7f56eba6c03aa88a548a8917a953aa882a06
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
 *     parentOf: (hash, url) => the address Back goes to when the trail is empty (optional; e.g. a deep link
 *                      to a person's page → "#/people"); null/undefined → the start page. hash: location.hash
 *                      ("" on a page without one); url: this page's full relative URL (path + search + hash),
 *                      for apps with several HTML pages,
 *   });
 * API:
 *   BackNav.go(hash)                  navigate, remembering the current address
 *   BackNav.go(hash, {replace: true}) / BackNav.replace(hash)
 *                                     navigate WITHOUT remembering (filter, sort or in-page tab changes)
 *   BackNav.go(hash, {keepTrail: true})  to a tab address, remembering the current one instead of starting a
 *                                     new trail (a link from one tab into another: Back returns to it).
 *                                     Links: <a href="#/x" data-keep-trail>
 *   BackNav.go(hash, {silent: true})  change the address without a hashchange event (the page already shows it)
 *   BackNav.replace("")               no address at all: the page's own URL, without a trailing "#" (the start
 *                                     page of an app whose home has no hash)
 *   BackNav.goPage(url, {replace})    another HTML page of the app, remembering this one (replace: true → not):
 *                                     the guard entry is taken away first, then location.replace(url), so a page
 *                                     change never leaves an entry in Home Assistant's history
 *   BackNav.back()                    an in-app ← button: previous page → parentOf → start page (dialogs
 *                                     are not closed first); → true when it went somewhere
 *   BackNav.peek()                    the address Back would return to from the trail (null when it is empty):
 *                                     lets a header name the real back target
 *   BackNav.clearTrail()              forget the trail (e.g. on lock / sign-out)
 *   BackNav.sync()                    after a change the DOM observer can't see
 * Links to a place on the page (<a href="#some-id">, not "#/…") just scroll there (a <details> opens), without
 * changing the address; "#x" links with no such element on the page are navigation, as before.
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
  let leaving = null;         // goPage: { url, done } while the guard is taken away before the page changes

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
  const parentOf = () => { try { const p = cfg && cfg.parentOf && cfg.parentOf(location.hash || "", here()); return p && !isCurrent(p) ? p : null; } catch (e) { return null; } };
  function peek() { for (let i = trail.length - 1; i >= 0; i--) if (!isCurrent(trail[i])) return trail[i]; return null; }

  const layers = () => { try { return (cfg && cfg.openLayers && cfg.openLayers()) || []; } catch (e) { return []; } };
  const atHome = () => { try { return !cfg || cfg.atHome(); } catch (e) { return true; } };
  const needGuard = () => layers().length > 0 || !atHome() || trail.length > 0;

  function arm() {
    clearTimeout(dropTimer);
    dropTimer = null;
    if (guard || leaving) return;
    try { guardBase = location.href; history.pushState({ backnav: true }, "", location.href); guard = true; } catch (e) { /* sandboxed frame */ }
  }

  function sync() {
    scheduled = false;
    if (!cfg || leaving) return;
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
      if (page !== pagePart()) { leave(entry); return; }
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

  // Open another page of the app in this one's place: with the guard in place (or our own Back still on its
  // way), go back over it first and replace the page once that Back arrives, so no stale entry stays behind.
  function leave(url) {
    if (leaving) return;
    clearTimeout(dropTimer);
    dropTimer = null;
    const state = { url, done: false };
    leaving = state;
    const finish = () => { if (state.done) return; state.done = true; try { location.replace(url); } catch (e) { /* ignore */ } };
    state.finish = finish;
    if (guard || ignorePops) {
      if (guard) { guard = false; try { history.back(); } catch (e) { finish(); return; } }
      setTimeout(finish, 600);             // in case that Back never reaches us
      return;
    }
    finish();
  }
  window.addEventListener("pageshow", (e) => { if (e && e.persisted) leaving = null; });   // back from the cache

  window.addEventListener("popstate", () => {
    if (leaving) { if (leaving.finish) leaving.finish(); return; }
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
    opts = opts || {};
    const target = !hash || hash === "#" ? "" : hash.startsWith("#") ? hash : "#" + hash;   // "" → no hash
    if (location.hash === target) { if (!opts.silent) window.dispatchEvent(new HashChangeEvent("hashchange")); return; }
    if (cfg && isTab(target) && !opts.keepTrail) reset();
    else if (!opts.replace) record(current());
    const old = location.href;
    try { history.replaceState(history.state, "", target || pagePart()); } catch (e) { location.hash = target; return; }
    if (!opts.silent) window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: old, newURL: location.href }));
    soon();
  }
  function replace(hash, opts) { go(hash, Object.assign({}, opts || {}, { replace: true })); }
  function goPage(url, opts) {
    opts = opts || {};
    if (leaving) return;
    if (isTab(url) && !opts.keepTrail) reset();
    else if (!opts.replace) record(here());
    leave(url);
  }
  function clearTrail() { reset(); soon(); }
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
    if (!href.startsWith("#/")) {
      // a place on this page: scroll to it without changing the address (no element → an address, as before)
      let el = null;
      try {
        const id = decodeURIComponent(href.slice(1));
        el = document.getElementById ? document.getElementById(id) : null;
        if (!el && document.getElementsByName) el = document.getElementsByName(id)[0] || null;
      } catch (err) { el = null; }
      if (el) {
        e.preventDefault();
        if (el.tagName === "DETAILS") el.open = true;
        if (el.scrollIntoView) el.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    }
    e.preventDefault();
    go(href, { keepTrail: a.hasAttribute && a.hasAttribute("data-keep-trail") });
  });

  function init(options) {
    cfg = options;
    new MutationObserver(soon).observe(document.body, { childList: true, subtree: true, attributes: true,
      attributeFilter: ["hidden", "class", "open", "style", "aria-hidden"] });
    window.addEventListener("hashchange", soon);
    sync();                                 // a non-empty trail (a reload, another page) arms the guard too
  }

  window.BackNav = {
    init, go, replace, goPage, back, peek, clearTrail, sync: soon,
    _trail: { get: () => trail.slice(), clear: reset, key: KEY },   // for tests
  };
})();
