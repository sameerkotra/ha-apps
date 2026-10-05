/* theme-boot.js — the page theme and the collapsed sidebar (shared: common/static/theme-boot.js).
 *
 * Load it in <head>, before the stylesheets, as an external file (the apps' Content-Security-Policy
 * forbids inline scripts): it applies the saved theme before the first paint, so there is no flash of
 * the wrong colours. The colours themselves are in common/static/themes.css; each app's style.css
 * adds its own accent colour on top.
 *
 * Every app on Home Assistant's origin shares the localStorage keys:
 *   "theme"             midnight | slate | daylight | auto  (auto follows the device: Daylight when it is
 *                       set to light, Midnight when dark). Names from before the themes were unified are
 *                       still understood: heritage, ink, vault, paper → midnight; parchment, sandstone →
 *                       daylight. Anything else → midnight.
 *   "sidebarCollapsed"  "1" = collapsed (sets <html data-sidebar="collapsed">)
 * <html data-theme> always holds the resolved theme (never "auto").
 *
 *   HouseholdTheme.choice()             the saved choice (one of THEMES)
 *   HouseholdTheme.set(choice)          save it, apply it, update every bound <select>
 *   HouseholdTheme.bindSelect(select)   fill a <select> with OPTIONS, show the choice, save on change
 *   HouseholdTheme.onChange(fn)         fn(choice, resolved) after set()
 *   HouseholdTheme.sidebarCollapsed() / setSidebarCollapsed(bool)
 */
(function () {
  "use strict";
  var THEMES = ["midnight", "slate", "daylight", "auto"];
  var OPTIONS = [["midnight", "🌙 Midnight"], ["slate", "🌆 Slate"], ["daylight", "☀️ Daylight"], ["auto", "🌓 Auto"]];
  var OLD_NAMES = { heritage: "midnight", ink: "midnight", vault: "midnight", paper: "midnight",
    parchment: "daylight", sandstone: "daylight" };
  var root = document.documentElement;
  var mq = null;
  try { mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null; } catch (e) { mq = null; }

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } }

  function normalize(name) {
    if (THEMES.indexOf(name) !== -1) return name;
    return Object.prototype.hasOwnProperty.call(OLD_NAMES, name) ? OLD_NAMES[name] : "midnight";
  }
  function resolve(choice) {
    choice = normalize(choice);
    if (choice === "auto") return mq && mq.matches ? "daylight" : "midnight";
    return choice;
  }

  var current = normalize(lsGet("theme"));
  var selects = [];
  var listeners = [];

  function apply() { root.setAttribute("data-theme", resolve(current)); }

  function set(choice) {
    current = normalize(choice);
    lsSet("theme", current);
    apply();
    selects = selects.filter(function (s) { return s.isConnected; });   // forget pages that were redrawn
    selects.forEach(function (s) { s.value = current; });
    listeners.forEach(function (fn) { try { fn(current, resolve(current)); } catch (e) { /* a listener's own problem */ } });
  }

  function bindSelect(select) {
    if (!select) return select;
    while (select.firstChild) select.removeChild(select.firstChild);
    OPTIONS.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o[0];
      opt.textContent = o[1];
      select.appendChild(opt);
    });
    select.value = current;
    select.addEventListener("change", function () { set(select.value); });
    selects.push(select);
    return select;
  }

  function sidebarCollapsed() { return root.getAttribute("data-sidebar") === "collapsed"; }
  function setSidebarCollapsed(collapsed) {
    if (collapsed) root.setAttribute("data-sidebar", "collapsed");
    else root.removeAttribute("data-sidebar");
    lsSet("sidebarCollapsed", collapsed ? "1" : "0");
  }

  apply();
  if (lsGet("sidebarCollapsed") === "1") root.setAttribute("data-sidebar", "collapsed");
  if (mq) {
    var onDevice = function () { if (current === "auto") apply(); };
    if (mq.addEventListener) mq.addEventListener("change", onDevice); else if (mq.addListener) mq.addListener(onDevice);
  }

  window.HouseholdTheme = {
    THEMES: THEMES.slice(),
    OPTIONS: OPTIONS.map(function (o) { return o.slice(); }),
    normalize: normalize,
    resolve: resolve,
    choice: function () { return current; },
    set: set,
    bindSelect: bindSelect,
    onChange: function (fn) { listeners.push(fn); },
    sidebarCollapsed: sidebarCollapsed,
    setSidebarCollapsed: setSidebarCollapsed,
  };
})();
