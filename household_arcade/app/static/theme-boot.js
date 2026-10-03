/* Applies the saved theme before first paint (an external file: the CSP forbids inline scripts).
   The "theme" key in localStorage is shared with Home Assistant's other app panels (one origin), so an
   unknown value falls back to Ink. */
(function () {
  var THEMES = ["ink", "slate", "daylight"];
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("theme");
    if (saved && THEMES.indexOf(saved) !== -1) root.setAttribute("data-theme", saved);
    if (localStorage.getItem("sidebarCollapsed") === "1") root.setAttribute("data-sidebar", "collapsed");
  } catch (e) { /* storage unavailable (private mode, some embedded views) */ }
})();
