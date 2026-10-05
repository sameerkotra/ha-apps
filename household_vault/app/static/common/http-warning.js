// Shared file: edit common/static/http-warning.js and run tools/sync_common.py; don't edit this copy. sha256=191f2c6e35a7c71c4f27d9a0f63602e105ed66a910db9e4a8546039a87bfe470
/* HTTP warning — a banner when the app is opened over plain http:// (not on this computer).
 *
 *   HttpWarning.isPlainHttp([loc])     true on http:// except localhost / 127.0.0.1
 *   HttpWarning.banner(text[, opts])   a <div class="banner"> with `text` when isPlainHttp(), else null;
 *                                      opts: { className: "banner", role: null, loc }
 *
 * The wording is the app's own (what crosses the network unencrypted is different in each app).
 * Load it before the app's script; it needs nothing else.
 */
(function () {
  "use strict";
  const LOCAL = ["localhost", "127.0.0.1"];

  function isPlainHttp(loc) {
    loc = loc || window.location;
    return loc.protocol === "http:" && !LOCAL.includes(loc.hostname);
  }

  function banner(text, opts) {
    opts = opts || {};
    if (!isPlainHttp(opts.loc)) return null;
    const el = document.createElement("div");
    el.className = opts.className || "banner";
    if (opts.role) el.setAttribute("role", opts.role);
    el.appendChild(document.createTextNode(text));
    return el;
  }

  window.HttpWarning = { isPlainHttp, banner };
})();
