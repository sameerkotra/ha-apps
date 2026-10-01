/* Applies the saved theme before first paint (an external file: the CSP forbids inline scripts).
   The "theme" key in localStorage is shared with Home Assistant's other app panels (one origin),
   so an unknown value falls back to Vault. "auto" follows the device: Daylight when light, Vault when dark. */
(function () {
  var THEMES = ["vault", "slate", "daylight", "auto"];
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function resolve(choice) { return choice === "auto" ? (mq && mq.matches ? "daylight" : "vault") : choice; }
  var choice = "vault";
  try {
    var saved = localStorage.getItem("theme");
    if (saved && THEMES.indexOf(saved) !== -1) choice = saved;
    if (localStorage.getItem("sidebarCollapsed") === "1") root.setAttribute("data-sidebar", "collapsed");
  } catch (e) { /* storage unavailable */ }
  window.__themeChoice = choice;
  window.__resolveTheme = resolve;
  root.setAttribute("data-theme", resolve(choice));
  if (mq) {
    var onChange = function () { if (window.__themeChoice === "auto") root.setAttribute("data-theme", resolve("auto")); };
    if (mq.addEventListener) mq.addEventListener("change", onChange); else if (mq.addListener) mq.addListener(onChange);
  }
})();
