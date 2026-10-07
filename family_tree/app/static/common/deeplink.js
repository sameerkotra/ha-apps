// Shared file: edit common/static/deeplink.js and run tools/sync_common.py; don't edit this copy. sha256=9713d5ea3ed56e2db8381a7f676b51cd87c6990b11b1d0aad758784df5a9dfb6
/* Links that open a page inside the app (APP_MESSAGES_SPEC §6.5), shared by the household apps
   (common/static/deeplink.js, copied by tools/sync_common.py).

   A phone notification or the Household Assistant links to the app's sidebar page with the app's own route as a
   sub-path: "/<full slug>/group/abc". A "#…" fragment would be lost on the way in, and the admin's Settings → Apps
   page isn't open to everyone, so the route travels in the path. Home Assistant opens the sidebar page and hands the
   rest of the path to it: current versions in a "home-assistant/properties" message (route.path, once the page asks
   with "home-assistant/subscribe-properties"); in any version the top page's own address is readable from here (the
   same origin). The route is checked by the app's own `accept` (only its own routes, never another page), opened
   with `open`, and the top page's address is put back to the bare page so a reload doesn't open it again. A route
   tapped while the app is already open arrives the same way, so the listener stays for as long as the page is open.

     HouseholdDeepLink.start(page, accept, open)
       page   "/<full slug>" (the server learns it from the Supervisor), or null: then nothing happens
       accept (route) => a value to open, or null — route is the sub-path, e.g. "/group/abc"
       open   (value) => void
*/
(function () {
  "use strict";
  const PAGE_RE = /^\/[a-z0-9]{1,16}_[a-z0-9_]{1,64}$/;

  function subPath(page, path) {
    if (typeof path !== "string") return null;
    path = path.split("?")[0].split("#")[0];
    if (!(path === page || path.startsWith(page + "/"))) return null;
    const sub = path.slice(page.length).replace(/\/+$/, "");
    return sub && sub !== "/" ? sub : null;
  }

  function parentPath() {
    try { return window.parent !== window ? window.parent.location.pathname : null; } catch (e) { return null; }
  }

  function forget(page) {
    try {
      const pp = parentPath();
      if (pp && pp !== page && pp.startsWith(page + "/")) window.parent.history.replaceState(window.parent.history.state, "", page);
    } catch (e) { /* the top page isn't reachable */ }
  }

  function start(page, accept, open) {
    if (typeof page !== "string" || !PAGE_RE.test(page)) return false;
    let last = null;
    const take = (path) => {
      const route = subPath(page, path);
      if (!route) { last = null; return false; }            // back on the bare page: the next link counts again
      if (route === last) return true;
      const value = accept(route);
      if (value === null || value === undefined) return false;
      last = route;
      forget(page);
      open(value);
      return true;
    };
    const done = take(parentPath()) || take(location.pathname);
    if (window.parent !== window) {
      window.addEventListener("message", (e) => {
        if (e.origin !== location.origin || e.source !== window.parent) return;
        const d = e.data;
        if (d && d.type === "home-assistant/properties" && d.route && typeof d.route.path === "string") take(d.route.path);
      });
      try { window.parent.postMessage({ type: "home-assistant/subscribe-properties" }, location.origin); } catch (e) { /* older frames */ }
    }
    return done;
  }

  window.HouseholdDeepLink = { start, subPath };
})();
