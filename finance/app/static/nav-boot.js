// Loaded in <head>, before anything paints: re-applies the collapsed sidenav (its own "navCollapsed" key; the
// theme comes from common/theme-boot.js). An external file so the Content-Security-Policy can forbid inline scripts.
(function () {
    try {
        if (localStorage.getItem("navCollapsed") === "1") {
            document.documentElement.classList.add("nav-collapsed");
        }
    } catch (e) { /* localStorage unavailable (private mode, etc.) — just stay expanded */ }
})();
