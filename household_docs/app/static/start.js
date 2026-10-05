"use strict";
/* Household Docs — starts the app once every script before this one has registered its routes, kinds, ➕ New
   entries and ⋯ actions on window.Docs. Keep it the last script on the page. */
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => window.Docs.start());
else window.Docs.start();
