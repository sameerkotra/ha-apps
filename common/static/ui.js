/* ui.js — small browser helpers every household app uses (shared: common/static/ui.js).
 *
 * One global, window.UI. Apps bind the names they use locally (const { h, $, clear } = UI; …) and wrap
 * the configurable parts (api, toast, modal, confirm) in their own one-liners with their own settings,
 * so the app's code keeps calling api(…) / toast(…) / openModal(…) as before. No app-specific branches
 * here: every difference between apps is an option.
 *
 * DOM
 *   h(tag, attrs, ...kids)   build an element. attrs: class, dataset {…}, style "css text", value (set after
 *                            the children, so a <select>'s options exist), onxxx: fn (addEventListener,
 *                            event name lower-cased), true → empty attribute, null/undefined/false → skipped,
 *                            anything else → setAttribute. kids: nodes, text (always a text node, never
 *                            HTML), nested arrays; null/undefined/false skipped.
 *   $(sel, root)  $$(sel, root) (an array)  clear(el)  mount(el, ...kids)  debounce(fn, ms)
 *   lsGet(key) / lsSet(key, value)          localStorage that never throws (private mode, blocked storage)
 *   escapeHtml(s)                           & < > " ' escaped; null/undefined → "" (for apps that still
 *                                           build HTML strings: never put user text into innerHTML unescaped)
 *
 * API
 *   errorMessage(detail, status)            FastAPI's detail (a string, or validation errors) → one line
 *   makeApi(options) → api(path, opts)      a fetch wrapper. Paths are relative to the page (a leading "/" is
 *                                           dropped): Home Assistant's ingress serves every app under its own
 *                                           sub-path. opts: method, body (sent as JSON), formData / form /
 *                                           rawBody (sent as is), headers, keepalive, raw (resolve with the
 *                                           Response). Resolves with the JSON, null for 204. Errors reject with
 *                                           an Error whose message is the server's detail and .status the HTTP
 *                                           status (no .status when the app couldn't be reached).
 *     options.url(path, opts)               → the URL to fetch (default: path without a leading "/")
 *     options.init(opts)                    → the fetch() init (default: built from opts as above)
 *     options.headers(opts)                 → extra headers for every request
 *     options.networkError                  message when fetch() fails; null = let fetch's own error through
 *     options.message(body, res)            → the error message (body: the parsed JSON, or null)
 *     options.makeError(message, status, body, res) → the Error to throw
 *     options.onResponse(res, opts)         called with every response (e.g. note activity)
 *     options.onError(err, res, opts)       called before an error is thrown (e.g. reload, lock)
 *
 * Toasts and dialogs
 *   toast(msg, opts)                        a toast in opts.root (default #toastRoot). opts: error, kind
 *                                           (extra class), ms (default 3000, error 6000), role, wrap (msg in a
 *                                           <span>), extra (a node appended), onclick. Returns the element.
 *   openModal(title, content, opts)         a dialog in opts.root (default #modalRoot); returns { close, el }.
 *     opts.modalClass / backdropClass       extra classes ("wide", "sheet", "dark", …)
 *     opts.sticky                           no closing by Escape or a click outside; no ✕
 *     opts.escape                           "self" (default: Escape closes this dialog), "top" (only if it is
 *                                           the top dialog, and the event goes no further), "stack" (one
 *                                           shared Escape handler closes the top dialog of the stack), "none"
 *     opts.focus                            "first" (default: the first field, opts.focusSelector, after
 *                                           opts.focusDelay ms, default 30), "close" (the ✕ at once), false
 *     opts.focusIf                          only focus when this is true
 *     opts.onOpen(close) / onClosed(close)  hooks (e.g. the app's back-button layers); onClose() as before
 *   dialogs()                               the open escape: "stack" dialogs ({ close, el }), oldest first
 *   confirmDialog(title, message, opts)     → Promise<boolean>; opts: okLabel ("OK"), cancelLabel ("Cancel"),
 *                                           okClass, cancelClass, focusOk, okFirst, modal (openModal options)
 */
(function () {
  "use strict";

  // ---------- DOM ----------
  // makeH({ booleanProps: ["checked", …] }): those attributes follow truthiness (0 and "" leave them off)
  // instead of the rule below (only null/undefined/false leave an attribute off).
  function makeH(options = {}) {
    const booleans = new Set(options.booleanProps || []);
    return function h(tag, attrs, ...kids) {
      const el = document.createElement(tag);
      let value;
      if (attrs) {
        for (const [k, v] of Object.entries(attrs)) {
          if (v === null || v === undefined || v === false) continue;
          if (booleans.has(k)) { if (v) el.setAttribute(k, ""); }
          else if (k === "class") el.className = v;
          else if (k === "dataset") Object.assign(el.dataset, v);
          else if (k === "style") el.style.cssText = v;
          else if (k === "value") value = v;              // applied after the children (<select> options)
          else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
          else if (v === true) el.setAttribute(k, "");
          else el.setAttribute(k, v);
        }
      }
      const add = (kid) => {
        if (kid === null || kid === undefined || kid === false) return;
        if (Array.isArray(kid)) kid.forEach(add);
        else if (kid instanceof Node) el.appendChild(kid);
        else el.appendChild(document.createTextNode(String(kid)));
      };
      kids.forEach(add);
      if (value !== undefined) el.value = value;
      return el;
    };
  }
  const h = makeH();
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function mount(el, ...kids) {
    clear(el);
    const add = (k) => { if (!k) return; if (Array.isArray(k)) k.forEach(add); else el.appendChild(k); };
    kids.forEach(add);
    return el;
  }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]); }

  // ---------- API ----------
  function errorMessage(detail, status) {
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail) && detail.length) return detail.map((d) => (d && d.msg) || String(d)).join("; ");
    return `Something went wrong (HTTP ${status}).`;
  }
  const NETWORK_ERROR = "Can't reach the app. Check your connection and try again.";

  function defaultInit(opts, extraHeaders) {
    const init = { method: opts.method || "GET", headers: Object.assign({}, extraHeaders || {}, opts.headers || {}) };
    if (opts.keepalive) init.keepalive = true;
    if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    else if (opts.formData) init.body = opts.formData;
    else if (opts.form) init.body = opts.form;
    else if (opts.rawBody !== undefined) init.body = opts.rawBody;
    return init;
  }

  function makeApi(options = {}) {
    const o = options;
    return async function api(path, opts = {}) {
      const url = o.url ? o.url(path, opts) : String(path).replace(/^\//, "");
      const init = o.init ? o.init(opts) : defaultInit(opts, o.headers ? o.headers(opts) : null);
      let res;
      try { res = await fetch(url, init); }
      catch (e) {
        if (o.networkError === null) throw e;
        const msg = o.networkError || NETWORK_ERROR;
        throw o.makeError ? o.makeError(msg, 0, null, null) : new Error(msg);
      }
      if (o.onResponse) o.onResponse(res, opts);
      if (!res.ok) {
        let body = null;
        try { body = await res.json(); } catch (e) { /* not JSON */ }
        const msg = o.message ? o.message(body, res) : errorMessage(body && body.detail, res.status);
        let err;
        if (o.makeError) err = o.makeError(msg, res.status, body, res);
        else { err = new Error(msg); err.status = res.status; }
        if (o.onError) o.onError(err, res, opts);
        throw err;
      }
      if (opts.raw) return res;
      if (res.status === 204) return null;
      return res.json();
    };
  }

  // ---------- toasts ----------
  function toast(msg, opts = {}) {
    const root = rootOf(opts.root, "#toastRoot");
    const cls = "toast" + (opts.error ? " error" : "") + (opts.kind ? " " + opts.kind : "");
    const el = h("div", { class: cls, role: opts.role || null }, opts.wrap ? h("span", null, msg) : msg);
    if (opts.extra) el.appendChild(opts.extra);
    if (opts.onclick) { el.classList.add("clickable"); el.addEventListener("click", () => { opts.onclick(); el.remove(); }); }
    root.appendChild(el);
    setTimeout(() => el.remove(), opts.ms || (opts.error ? 6000 : 3000));
    return el;
  }

  // ---------- dialogs ----------
  const FIRST_FIELD = "input:not([type=hidden]):not([disabled]), select, textarea";
  let stack = [];
  // One Escape handler for escape: "stack" dialogs, added before any app code runs.
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && stack.length) stack[stack.length - 1].close(); });
  function rootOf(r, def) { return typeof r === "string" || !r ? $(r || def) : r; }

  function openModal(title, content, opts = {}) {
    const closeBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Close" }, "✕");
    const modal = h("div", { class: "modal" + (opts.modalClass ? " " + opts.modalClass : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
      h("h3", null, h("span", null, title), closeBtn), content);
    const backdrop = h("div", { class: "modal-backdrop" + (opts.backdropClass ? " " + opts.backdropClass : "") }, modal);
    const escape = opts.escape || "self";
    const rootEl = rootOf(opts.root, "#modalRoot");
    let downOnBackdrop = false;
    backdrop.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === backdrop; });
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop && downOnBackdrop && !opts.sticky) close(); });
    const onKey = (e) => {
      if (e.key !== "Escape" || opts.sticky) return;
      if (escape === "top") {
        const all = rootEl.querySelectorAll(".modal-backdrop");
        if (backdrop !== all[all.length - 1]) return;
        e.stopPropagation();
      }
      close();
    };
    if (escape === "self" || escape === "top") document.addEventListener("keydown", onKey);
    const handle = { close, el: modal };
    let closed = false;
    function close() {
      if (closed) return;
      closed = true;
      if (opts.onClosed) opts.onClosed(close);
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      stack = stack.filter((m) => m !== handle);
      if (opts.onClose) opts.onClose();
    }
    if (escape === "stack") stack.push(handle);
    if (opts.onOpen) opts.onOpen(close);
    closeBtn.addEventListener("click", close);
    if (opts.sticky) closeBtn.hidden = true;
    rootEl.appendChild(backdrop);
    if (opts.afterOpen) opts.afterOpen(handle);
    const focus = opts.focus === undefined ? "first" : opts.focus;
    if (focus === "close") closeBtn.focus();
    else if (focus === "first" && opts.focusIf !== false) {
      const first = modal.querySelector(opts.focusSelector || FIRST_FIELD);
      if (first) {
        const delay = opts.focusDelay === undefined ? 30 : opts.focusDelay;
        if (delay === 0) first.focus(); else setTimeout(() => first.focus(), delay);
      }
    }
    return handle;
  }

  function confirmDialog(title, message, opts = {}) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; m.close(); resolve(v); } };
      const cancel = h("button", { class: opts.cancelClass || "btn-ghost", type: "button", onclick: () => finish(false) }, opts.cancelLabel ?? "Cancel");
      const ok = h("button", { class: opts.okClass || "btn-primary", type: "button", onclick: () => finish(true) }, opts.okLabel ?? "OK");
      const modalOpts = Object.assign({}, opts.modal || {});
      const userClose = modalOpts.onClose;
      modalOpts.onClose = () => { if (!done) { done = true; resolve(false); } if (userClose) userClose(); };
      const m = openModal(title, h("div", null, h("p", null, message), h("div", { class: "actions" }, cancel, ok)), modalOpts);
      if (opts.focusOk) ok.focus();
    });
  }

  window.UI = {
    h, makeH, $, $$, clear, mount, debounce, lsGet, lsSet, escapeHtml,
    errorMessage, makeApi, NETWORK_ERROR,
    toast, openModal, confirmDialog,
    dialogs: () => stack.slice(),      // the open escape: "stack" dialogs, oldest first
  };
})();
