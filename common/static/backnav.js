/* backnav.js — the back gesture inside Home Assistant (shared: common/static/backnav.js).
 *
 * Android's back gesture in the Home Assistant app (and a browser's Back) should never leave this app
 * while something is open in it. With this file:
 *   1. Back closes the top dialog / menu / sheet, if one is open;
 *   2. otherwise, if you aren't on the page the app opened on (its start page), it goes back there;
 *   3. only Back on the start page, with nothing open, leaves for Home Assistant.
 *
 * How: while anything is open or you're away from the start page, the app keeps exactly one extra
 * history entry (the "guard"). Ingress pages are same-origin iframes, whose entries are part of the HA
 * app's history, so its back gesture pops the guard and fires `popstate` here instead of leaving.
 * In-app navigation must not add history entries of its own: use BackNav.go("#/x") instead of setting
 * location.hash, and links like <a href="#/x"> are handled here (replaceState + a hashchange event).
 *
 * The app describes itself once:
 *   BackNav.init({
 *     atHome: () => true when the start page is showing,
 *     goHome: () => show the start page (without adding history),
 *     openLayers: () => the open dialogs/menus, bottom-most first (elements or anything truthy),
 *     closeLayer: (layer) => close that one,
 *   });
 * Changes are noticed by themselves (a DOM observer), so dialogs and pages need no extra calls;
 * BackNav.sync() can be called after a change the observer can't see.
 */
(function () {
  "use strict";
  let cfg = null;
  let guard = false;          // our extra history entry is in place
  let ignorePops = 0;         // pops we caused ourselves (removing the guard)
  let dropTimer = null;
  let lastHref = location.href;
  let scheduled = false;

  const layers = () => { try { return (cfg && cfg.openLayers && cfg.openLayers()) || []; } catch (e) { return []; } };
  const atHome = () => { try { return !cfg || cfg.atHome(); } catch (e) { return true; } };
  const needGuard = () => layers().length > 0 || !atHome();

  function arm() {
    clearTimeout(dropTimer);
    dropTimer = null;
    if (guard) return;
    try { history.pushState({ backnav: true }, "", location.href); guard = true; } catch (e) { /* sandboxed frame */ }
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

  window.addEventListener("popstate", () => {
    if (ignorePops) {
      // our own removal of the guard: the entry under it may hold an older address — keep the current
      // one, so the app's hashchange listener stays on the page that's showing
      ignorePops--;
      if (location.href !== lastHref) { try { history.replaceState(history.state, "", lastHref); } catch (e) { /* ignore */ } }
      return;
    }
    guard = false;
    // the entry under the guard may hold an older address (the app replaced it since): keep the current one
    if (location.href !== lastHref) { try { history.replaceState(history.state, "", lastHref); } catch (e) { /* ignore */ } }
    if (!cfg) return;
    const open = layers();
    try {
      if (open.length) cfg.closeLayer(open[open.length - 1]);
      else if (!atHome()) cfg.goHome();
    } catch (e) { /* never break Back */ }
    setTimeout(sync, 0);
  });

  // In-app navigation without history entries of its own
  function go(hash) {
    const target = hash.startsWith("#") ? hash : "#" + hash;
    if (location.hash === target) { window.dispatchEvent(new HashChangeEvent("hashchange")); return; }
    const old = location.href;
    try { history.replaceState(history.state, "", target); } catch (e) { location.hash = target; return; }
    window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: old, newURL: location.href }));
    soon();
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
    sync();
  }

  window.BackNav = { init, go, sync: soon };
})();
