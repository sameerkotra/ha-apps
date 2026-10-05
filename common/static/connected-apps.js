/* connected-apps.js — the household apps' messages to each other, in the browser (shared:
 * common/static/connected-apps.js; APP_MESSAGES_SPEC.md §5, §6.5).
 *
 * One global, window.ConnectedApps:
 *
 *   ConnectedApps.label(kind)               what a message kind lets an app do, in words
 *   ConnectedApps.rows(data, now?)          GET /api/admin/connected-apps → [{slug, name, version, lastSeen
 *                                           (Date|null), active, does: [labels]}], newest first
 *   ConnectedApps.status(data)              → null, or a one-line notice (off outside Home Assistant,
 *                                           not connected, nobody has said hello)
 *   ConnectedApps.card(data, opts)          the read-only "Connected apps" card for an Admin page.
 *     opts.h                                the app's element builder (UI.h); required
 *     opts.cardClass ("card"), opts.title ("Connected apps"), opts.when(date) → text (toLocaleString)
 *
 *   ConnectedApps.openAppPage(path, opts)   go to another app's page inside Home Assistant (§6.5): ask Home
 *                                           Assistant to navigate (postMessage "home-assistant/navigate");
 *                                           if the top page hasn't moved after opts.wait ms (300), do what Home
 *                                           Assistant's navigate() does (pushState + "location-changed" on the
 *                                           top window); if that page can't be reached, load the path in the
 *                                           top window. Returns a Promise of how it went: "ha", "history",
 *                                           "load". opts.win (window) for tests.
 *   ConnectedApps.safePath(panel, target)   "/<panel>/<target>" when both are well formed (as Chat checks them),
 *                                           else null
 *
 * Text only (never HTML); no app-specific branches.
 */
(function () {
  "use strict";

  const LABELS = {
    "chat.card": "Post shared documents in chats",
    "chat.chats.list": "List the chats a person can post in",
    "todo.items.add": "Add checklist items as tasks",
    "todo.lists.list": "List the lists a person can add to",
    "poll.create": "Run polls in chats",
    "poll.cancel": "Cancel those polls",
    "message.post": "Post short notes in chats",
  };

  function label(kind) {
    return LABELS[kind] || String(kind || "");
  }

  function toDate(s) {
    const d = s ? new Date(s) : null;
    return d && !isNaN(d.getTime()) ? d : null;
  }

  function rows(data) {
    const apps = (data && Array.isArray(data.apps)) ? data.apps : [];
    return apps.map((a) => ({
      slug: a.slug, name: a.name || a.slug, version: a.version || "", lastSeen: toDate(a.last_seen),
      active: !!a.active, does: (Array.isArray(a.can) ? a.can : []).map(label),
    })).sort((x, y) => (y.active - x.active) || ((y.lastSeen ? y.lastSeen.getTime() : 0) - (x.lastSeen ? x.lastSeen.getTime() : 0)));
  }

  function status(data) {
    if (!data || !data.on) return "App messages are off: they work only when the app runs inside Home Assistant.";
    if (!data.connected) return "Not connected to Home Assistant's events right now — the app keeps trying.";
    if (!rows(data).length) return "No other household app has said hello yet.";
    return null;
  }

  function card(data, opts) {
    const o = opts || {};
    const h = o.h;
    const when = o.when || ((d) => d.toLocaleString());
    const note = status(data);
    const list = rows(data);
    return h("div", { class: (o.cardClass || "card") + " connected-apps" },
      h("h3", null, o.title || "Connected apps"),
      h("p", { class: "hint" }, "Other household apps this app exchanges messages with, through Home Assistant. Read only."),
      note ? h("p", { class: "hint connected-apps-note" }, note) : null,
      list.length ? h("ul", { class: "connected-apps-list" }, list.map((a) =>
        h("li", { class: "connected-app" + (a.active ? "" : " stale") },
          h("div", { class: "connected-app-name" }, a.name, a.version ? h("span", { class: "hint" }, " " + a.version) : null),
          h("div", { class: "hint" }, a.lastSeen ? "Last seen " + when(a.lastSeen) : "Never seen", a.active ? null : " · not seen lately"),
          a.does.length ? h("div", { class: "connected-app-does" }, "Can: " + a.does.join(" · ")) : null))) : null);
  }

  const PANEL_RE = /^\/[a-z0-9_]{1,80}$/;
  const TARGET_RE = /^(\/[A-Za-z0-9_.~-]+)+$/;

  function safePath(panel, target) {
    if (typeof panel !== "string" || !PANEL_RE.test(panel)) return null;
    if (target == null || target === "" || target === "/") return panel;
    if (typeof target !== "string" || target.length > 200 || !TARGET_RE.test(target)) return null;
    if (target.split("/").some((s) => s === "." || s === "..")) return null;
    return panel + target;
  }

  function topPath(win) {
    try { return win.top.location.pathname + win.top.location.search; } catch (e) { return null; }
  }

  function openAppPage(path, opts) {
    const o = opts || {};
    const win = o.win || window;
    const wait = o.wait == null ? 300 : o.wait;
    const load = () => { try { win.open(path, "_top"); } catch (e) { win.location.href = path; } return "load"; };
    if (!win.top || win.top === win) return Promise.resolve(load());
    const before = topPath(win);
    try { win.parent.postMessage({ type: "home-assistant/navigate", path: path }, win.location.origin); } catch (e) { /* older frames */ }
    return new Promise((resolve) => {
      setTimeout(() => {
        const now = topPath(win);
        if (now !== null && now !== before) { resolve("ha"); return; }
        if (now === null) { resolve(load()); return; }
        try {
          win.top.history.pushState(null, "", path);
          win.top.dispatchEvent(new win.top.CustomEvent("location-changed", { detail: { replace: false } }));
          resolve("history");
        } catch (e) { resolve(load()); }
      }, wait);
    });
  }

  window.ConnectedApps = { label, rows, status, card, safePath, openAppPage };
})();
