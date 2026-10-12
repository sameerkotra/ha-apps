/* Household Proxy — the script put first into every proxied HTML page (spec/SPEC.md §3.3, §3.4).
 *
 * A page written for the top of its own address builds URLs while it runs ("/api/x", "wss://host/ws"). Inside Home
 * Assistant those would reach Home Assistant itself, so this script rewrites them, by one rule:
 *   - root-relative ("/x"), or absolute on the site's own origin, or on Home Assistant's origin but outside the
 *     ingress path                                   → P + path   (P: the site's prefix, e.g. /api/hassio_ingress/…/s/nas)
 *   - ws:// / wss:// on the site's host or Home Assistant's host → ws(s)://<Home Assistant's host> + P + path
 *   - anything else (relative URLs, other hosts, data:, blob:) is left alone.
 * Applied to fetch, XMLHttpRequest, WebSocket, EventSource, sendBeacon, window.open, Worker / SharedWorker,
 * service-worker registration, history.pushState / replaceState, and the src / href / action / srcset / poster /
 * data property setters and setAttribute of the elements that load things — before the browser starts a request.
 * A page navigation to Home Assistant's own address outside the ingress path is sent through P as well (where the
 * browser has the Navigation API).
 *
 * Single-page app mode: before the app starts, the frame's address is changed (history.replaceState) to the site's
 * own path ("/stacks"), and kept that way when the app navigates, so its router sees what it would at its own address.
 * The server adds <base href="P/…"> to such pages so relative URLs still load through the proxy.
 *
 * Its settings come from the data-hp attribute of its own <script> tag: {p: P, i: ingress path, o: the site's origin,
 * spa: bool, v: the site's own path for single-page app mode}.
 */
(function () {
  "use strict";
  if (window.__householdProxy) return;
  var me = document.currentScript;
  var C;
  try { C = JSON.parse(me.getAttribute("data-hp")); } catch (e) { return; }
  var P = C.p, ING = C.i || "", SITE = C.o, SPA = !!C.spa;
  var HERE = location;
  var siteHost = "";
  try { siteHost = new URL(SITE).host; } catch (e) { /* no origin: only root-relative URLs are rewritten */ }
  window.__householdProxy = { prefix: P, fix: fix };

  function inside(path) {
    return path === P || path.indexOf(P + "/") === 0 || (ING && (path === ING || path.indexOf(ING + "/") === 0));
  }

  function fix(u) {
    if (u === null || u === undefined) return u;
    var s;
    if (typeof u === "string") s = u;
    else if (typeof URL !== "undefined" && u instanceof URL) s = u.href;
    else return u;
    var t = s.trim();
    if (!t) return u;
    if (t.charAt(0) === "/" && t.charAt(1) !== "/" && t.charAt(1) !== "\\") {
      return inside(t.split(/[?#]/)[0]) ? s : P + t;
    }
    if (!/^([a-z][a-z0-9+.-]*:)?\/\//i.test(t)) return s;          // relative ("x", "./x", "?q"), data:, blob:, …
    var a;
    try { a = new URL(t, HERE.href); } catch (e) { return s; }
    var rest = a.pathname + a.search + a.hash;
    if (a.protocol === "http:" || a.protocol === "https:") {
      if (a.host === siteHost && siteHost) return HERE.origin + P + rest;      // the site (http or https)
      if (a.origin === HERE.origin && !inside(a.pathname)) return HERE.origin + P + rest;
      return s;
    }
    if (a.protocol === "ws:" || a.protocol === "wss:") {
      if (a.host === siteHost || a.host === HERE.host) {
        var path = inside(a.pathname) ? a.pathname : P + a.pathname;
        return (HERE.protocol === "https:" ? "wss://" : "ws://") + HERE.host + path + a.search;
      }
    }
    return s;
  }

  function wrapMethod(obj, name, argIndex) {
    if (!obj || typeof obj[name] !== "function") return;
    var orig = obj[name];
    obj[name] = function () {
      var args = Array.prototype.slice.call(arguments);
      if (args.length > argIndex) args[argIndex] = fix(args[argIndex]);
      return orig.apply(this, args);
    };
  }

  // fetch (a Request object is rebuilt with the rewritten URL)
  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      if (typeof Request !== "undefined" && input instanceof Request) {
        var f = fix(input.url);
        if (f !== input.url) input = new Request(f, input);
      } else {
        input = fix(input);
      }
      return origFetch.call(this, input, init);
    };
  }

  // XMLHttpRequest.open(method, url, …)
  if (window.XMLHttpRequest) wrapMethod(XMLHttpRequest.prototype, "open", 1);
  if (navigator.sendBeacon) wrapMethod(navigator, "sendBeacon", 0);
  wrapMethod(window, "open", 0);
  if (navigator.serviceWorker) wrapMethod(navigator.serviceWorker, "register", 0);

  // constructors: WebSocket, EventSource, Worker, SharedWorker
  function wrapCtor(name) {
    var Orig = window[name];
    if (typeof Orig !== "function") return;
    var Wrapped = function (url, opt) {
      return arguments.length > 1 ? new Orig(fix(url), opt) : new Orig(fix(url));
    };
    Wrapped.prototype = Orig.prototype;
    Object.getOwnPropertyNames(Orig).forEach(function (k) {
      if (!(k in Wrapped)) { try { Object.defineProperty(Wrapped, k, Object.getOwnPropertyDescriptor(Orig, k)); } catch (e) { /* skip */ } }
    });
    window[name] = Wrapped;
  }
  ["WebSocket", "EventSource", "Worker", "SharedWorker"].forEach(wrapCtor);

  // element properties and setAttribute
  var ATTRS = { src: 1, href: 1, action: 1, formaction: 1, poster: 1, data: 1, srcset: 2, imagesrcset: 2 };
  function fixSrcset(v) {
    return String(v).split(",").map(function (part) {
      var bits = part.trim().split(/\s+/);
      if (bits[0]) bits[0] = fix(bits[0]);
      return bits.join(" ");
    }).join(", ");
  }
  function fixAttr(name, v) {
    var k = ATTRS[String(name).toLowerCase()];
    if (!k || typeof v !== "string") return v;
    return k === 2 ? fixSrcset(v) : fix(v);
  }
  function wrapProp(ctor, prop, how) {
    if (!ctor || !ctor.prototype) return;
    var d = Object.getOwnPropertyDescriptor(ctor.prototype, prop);
    if (!d || !d.set) return;
    Object.defineProperty(ctor.prototype, prop, {
      configurable: true, enumerable: d.enumerable, get: d.get,
      set: function (v) { d.set.call(this, how === 2 ? fixSrcset(v) : fix(v)); },
    });
  }
  [["HTMLScriptElement", "src"], ["HTMLLinkElement", "href"], ["HTMLImageElement", "src"], ["HTMLImageElement", "srcset", 2],
    ["HTMLIFrameElement", "src"], ["HTMLSourceElement", "src"], ["HTMLSourceElement", "srcset", 2], ["HTMLMediaElement", "src"],
    ["HTMLTrackElement", "src"], ["HTMLEmbedElement", "src"], ["HTMLObjectElement", "data"], ["HTMLInputElement", "src"],
    ["HTMLAnchorElement", "href"], ["HTMLAreaElement", "href"], ["HTMLFormElement", "action"], ["HTMLButtonElement", "formAction"],
    ["HTMLInputElement", "formAction"], ["HTMLVideoElement", "poster"], ["HTMLBaseElement", "href"]
  ].forEach(function (x) { wrapProp(window[x[0]], x[1], x[2]); });
  if (window.Element) {
    var origSet = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      return origSet.call(this, name, fixAttr(name, value));
    };
    var origSetNS = Element.prototype.setAttributeNS;
    Element.prototype.setAttributeNS = function (ns, name, value) {
      var local = String(name).split(":").pop();
      return origSetNS.call(this, ns, name, local === "href" ? fix(value) : value);
    };
  }

  // history: in single-page app mode the frame keeps the site's own paths; otherwise addresses stay under P
  if (SPA) {
    try { history.replaceState(history.state, "", C.v || "/"); } catch (e) { /* keep the proxied address */ }
  } else {
    wrapMethod(history, "pushState", 2);
    wrapMethod(history, "replaceState", 2);
  }

  // a full navigation to Home Assistant's own address (location.href = "/login") goes through the proxy instead
  if (window.navigation && typeof window.navigation.addEventListener === "function") {
    window.navigation.addEventListener("navigate", function (e) {
      try {
        if (e.hashChange || !e.destination || (e.destination.sameDocument)) return;
        var d = new URL(e.destination.url);
        if (d.origin !== HERE.origin || inside(d.pathname) || e.formData) return;
        e.preventDefault();
        HERE.assign(HERE.origin + P + d.pathname + d.search + d.hash);
      } catch (err) { /* leave it to the browser */ }
    });
  }
})();
