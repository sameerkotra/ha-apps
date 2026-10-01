/* Applies the saved theme before first paint (an external file because the
   Content-Security-Policy forbids inline scripts). "auto" follows the device's
   light/dark setting: Parchment when light, Heritage when dark. */
(function () {
  var THEMES = ["heritage", "slate", "daylight", "parchment", "auto"];
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function resolve(choice) {
    if (choice === "auto") return mq && mq.matches ? "parchment" : "heritage";
    return choice;
  }
  var choice = "heritage";
  try {
    var saved = localStorage.getItem("theme");
    if (saved && THEMES.indexOf(saved) !== -1) choice = saved;
    if (localStorage.getItem("sidebarCollapsed") === "1") root.setAttribute("data-sidebar", "collapsed");
  } catch (e) { /* storage unavailable */ }
  window.__themeChoice = choice;
  window.__resolveTheme = resolve;
  root.setAttribute("data-theme", resolve(choice));
  if (mq) {
    var onChange = function () {
      if (window.__themeChoice === "auto") root.setAttribute("data-theme", resolve("auto"));
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange); else if (mq.addListener) mq.addListener(onChange);
  }
})();
