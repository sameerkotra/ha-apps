/* Household Proxy — the page (spec/SPEC.md §5).
 *
 * Views (the address's #fragment, so a reload stays where it was):
 *   #/             the launcher: a tile per site this person may open
 *   #/site/<slug>  the viewer: the site in a frame (s/<slug>/<start path>), with a slim bar or a floating ←
 *   #/admin/<tab>  admins: Sites, App settings, People, Storage
 * Everything is built with DOM nodes (UI.h): names and addresses are never parsed as HTML.
 */
(function () {
  "use strict";
  const { h, $, $$, clear, mount } = UI;

  const api = UI.makeApi({
    networkError: "Couldn't reach the app. Check the connection.",
    message: (body, res) => UI.errorMessage(body && body.detail, res.status),
  });
  const toast = (msg, error) => UI.toast(msg, { error: !!error });
  const openModal = (title, content, opts) => UI.openModal(title, content, Object.assign({ escape: "stack" }, opts || {}));

  const state = { me: null, sites: [], view: "sites", site: null, adminTab: "sites", admin: null };

  HouseholdTheme.bindSelect($("#theme-select"));

  // ---------- views ----------
  function show(view) {
    state.view = view;
    $("#sitesView").hidden = view !== "sites";
    $("#monitorView").hidden = view !== "monitor";
    $("#bookmarksView").hidden = view !== "bookmarks";
    if (view !== "monitor") stopMonitor();
    $("#viewerView").hidden = view !== "viewer";
    $("#adminView").hidden = view !== "admin";
    $("#topbar").hidden = view === "viewer";
    $$("#topnav .tab").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    if (view !== "viewer") { const f = $("#frame"); if (f.getAttribute("src")) f.removeAttribute("src"); }
  }

  function go(hash) {
    if (window.BackNav) BackNav.go(hash); else location.hash = hash;
  }

  function route() {
    const r = location.hash.replace(/^#/, "") || "/";
    let m;
    if ((m = /^\/site\/([a-z0-9-]{1,40})(\/.*)?$/.exec(r))) return openSite(m[1], m[2] || null);
    if ((m = /^\/monitor(?:\/([a-z0-9-]{1,40}))?$/.exec(r))) return openMonitor(m[1] || null);
    if (r === "/bookmarks") return openBookmarks();
    if ((m = /^\/admin(?:\/([a-z]+))?$/.exec(r)) && state.me && state.me.user.isAdmin) return openAdmin(m[1] || state.adminTab);
    show("sites");
    renderTiles();
  }

  // ---------- launcher ----------
  async function loadSites() {
    try { state.sites = (await api("api/sites")).sites; } catch (e) { toast(e.message, true); state.sites = []; }
  }

  function renderTiles() {
    const box = clear($("#tiles"));
    $("#noSites").hidden = !!state.sites.length || !state.me || !state.me.user.enabled;
    for (const s of state.sites) {
      box.append(h("a", { class: "tile", href: `#/site/${s.slug}`, title: s.name + (s.state ? ` — ${STATE_LABEL[s.state] || s.state}` : "") },
        s.state ? h("span", { class: `status-dot ${s.state}`, "aria-hidden": "true" }) : null,
        h("span", { class: "tile-icon", "aria-hidden": "true" }, s.favicon ? h("img", { src: s.favicon, alt: "" }) : (s.icon || "🌐")),
        h("span", { class: "tile-name" }, s.name)));
    }
  }

  // ---------- bookmarks: the household's links, opened in the browser (never through the proxy) ----------
  // A phone opens them in Chrome: on Android with an intent: link (Chrome, or the default browser when Chrome isn't
  // there), on an iPhone with Chrome's googlechromes:// scheme. A computer opens them in a new tab.
  const UA = navigator.userAgent || "";
  const IS_ANDROID = /Android/i.test(UA);
  const IS_IOS = /iPhone|iPad|iPod/i.test(UA) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const IS_PHONE = IS_ANDROID || IS_IOS;
  function chromeOn() { try { return localStorage.getItem("bookmarkChrome") !== "off"; } catch (e) { return true; } }

  function bookmarkLink(url) {
    let u;
    try { u = new URL(url); } catch (e) { return { href: url, target: "_blank" }; }
    if (!/^https?:$/.test(u.protocol)) return { href: "#/bookmarks" };          // never anything but the web
    if (IS_PHONE && chromeOn()) {
      const rest = u.host + u.pathname + u.search;
      if (IS_ANDROID) {
        return { href: `intent://${rest}#Intent;scheme=${u.protocol.slice(0, -1)};package=com.android.chrome;` +
          `S.browser_fallback_url=${encodeURIComponent(url)};end` };
      }
      return { href: (u.protocol === "https:" ? "googlechromes://" : "googlechrome://") + rest + u.hash };
    }
    return { href: url, target: "_blank" };
  }

  async function openBookmarks() {
    show("bookmarks");
    $("#bmChromeRow").hidden = !IS_PHONE;
    $("#bmChrome").checked = chromeOn();
    try { state.bookmarks = (await api("api/bookmarks")).bookmarks; }
    catch (e) { mount(clear($("#bookmarkList")), h("p", { class: "error" }, e.message)); return; }
    renderBookmarks();
  }

  function hostOf(url) { try { return new URL(url).host; } catch (e) { return url; } }

  function renderBookmarks() {
    const list = state.bookmarks || [];
    $("#noBookmarks").hidden = !!list.length;
    const box = clear($("#bookmarkList"));
    list.forEach((b, i) => {
      const link = bookmarkLink(b.url);
      const move = async (d) => {
        const ids = list.map((x) => x.id);
        const [x] = ids.splice(i, 1);
        ids.splice(i + d, 0, x);
        try { state.bookmarks = (await api("api/bookmarks/reorder", { method: "POST", body: { ids } })).bookmarks; renderBookmarks(); }
        catch (e) { toast(e.message, true); openBookmarks(); }
      };
      const admin = state.me && state.me.user.isAdmin;           // the order is everyone's: admins set it
      const actions = h("div", { class: "bm-actions" },
        admin ? h("button", { type: "button", class: "icon-btn", title: "Move up", "aria-label": `Move ${b.name} up`, disabled: i === 0, onclick: () => move(-1) }, "▲") : null,
        admin ? h("button", { type: "button", class: "icon-btn", title: "Move down", "aria-label": `Move ${b.name} down`, disabled: i === list.length - 1, onclick: () => move(1) }, "▼") : null,
        b.canEdit ? h("button", { type: "button", class: "icon-btn", title: "Edit", "aria-label": `Edit ${b.name}`, onclick: () => editBookmark(b) }, "✎") : null);
      box.append(h("div", { class: "bookmark" },
        h("a", { class: "bm-link", href: link.href, target: link.target || null, rel: "noopener noreferrer", title: b.url },
          h("span", { class: "bm-icon", "aria-hidden": "true" }, b.icon || "🔖"),
          h("span", { class: "bm-text" }, h("span", { class: "bm-name" }, b.name),
            h("span", { class: "bm-host dim small" }, hostOf(b.url) + (b.addedBy ? ` · added by ${b.addedBy}` : "")))),
        actions));
    });
  }

  function editBookmark(b) {
    const f = {
      name: h("input", { class: "input", value: b ? b.name : "", maxlength: 80, placeholder: "Router" }),
      url: h("input", { class: "input", value: b ? b.url : "", maxlength: 2000, placeholder: "https://example.com", inputmode: "url", autocapitalize: "off", spellcheck: "false" }),
      icon: h("input", { class: "input icon-input", value: b && b.icon ? b.icon : "", maxlength: 8, placeholder: "🔖" }),
    };
    const err = h("p", { class: "error" });
    const save = h("button", { type: "submit", class: "btn-primary" }, b ? "Save" : "Add");
    const del = b ? h("button", { type: "button", class: "btn-ghost danger" }, "Delete") : h("span");
    const form = h("form", { class: "site-form" },
      field("Address", f.url, "A web address — http:// or https://. Everyone here sees it; it opens in each person's browser, not through this app."),
      field("Name", f.name, "Leave empty to use the site's address."), field("Icon (an emoji, optional)", f.icon), err,
      h("div", { class: "row-between" }, del, save));
    const m = openModal(b ? "Edit bookmark" : "Add a bookmark", form);
    setTimeout(() => f.url.focus(), 0);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      save.disabled = true;
      const body = { name: f.name.value, url: f.url.value, icon: f.icon.value };
      try {
        const r = b ? await api(`api/bookmarks/${b.id}`, { method: "PUT", body }) : await api("api/bookmarks", { method: "POST", body });
        state.bookmarks = r.bookmarks; renderBookmarks(); m.close(); toast(b ? "Saved" : "Bookmark added");
      } catch (x) { err.textContent = x.message; save.disabled = false; }
    });
    if (del) del.addEventListener("click", async () => {
      if (!await UI.confirmDialog("Delete bookmark?", `Delete “${b.name}”?`, { okLabel: "Delete", okClass: "btn-primary danger" })) return;
      try { state.bookmarks = (await api(`api/bookmarks/${b.id}`, { method: "DELETE" })).bookmarks; renderBookmarks(); m.close(); toast("Deleted"); }
      catch (x) { err.textContent = x.message; }
    });
  }
  $("#addBookmarkBtn").addEventListener("click", () => editBookmark(null));
  $("#bmChrome").addEventListener("change", (e) => {
    try { localStorage.setItem("bookmarkChrome", e.target.checked ? "on" : "off"); } catch (x) { /* this browser keeps nothing */ }
    renderBookmarks();
  });

  // ---------- viewer ----------
  function siteSrc(site, path) {
    const p = path && path.startsWith("/") ? path : site.startPath || "/";
    return `s/${site.slug}${p}`;
  }

  function openSite(slug, path) {
    const site = state.sites.find((s) => s.slug === slug);
    if (!site) { toast("That site isn't on your list.", true); go("#/"); return; }
    state.site = site;
    show("viewer");
    $("#viewerTitle").textContent = `${site.icon || "🌐"} ${site.name}`;
    $("#viewerBar").hidden = !site.showBar;
    $("#floatBack").hidden = !!site.showBar;
    const f = $("#frame");
    const src = siteSrc(site, path);
    if (f.getAttribute("src") !== src) f.setAttribute("src", src);
    document.title = `${site.name} · Household Proxy`;
  }

  function deepLink(site) {
    const page = state.me && state.me.page;
    try {
      if (page) return `${window.top.location.origin}${page}/site/${site.slug}`;
    } catch (e) { /* the top page isn't readable */ }
    return location.href.split("#")[0] + `#/site/${site.slug}`;
  }

  $("#backBtn").addEventListener("click", () => go("#/"));
  $("#floatBack").addEventListener("click", () => go("#/"));
  $("#reloadBtn").addEventListener("click", () => {
    const f = $("#frame");
    // single-page app mode: the frame's own address is the site's path, not a proxied one — start again through it
    if (state.site && state.site.spaMode) { f.setAttribute("src", siteSrc(state.site, null) + (siteSrc(state.site, null).includes("?") ? "&" : "?") + "_hp=" + Date.now()); return; }
    try { f.contentWindow.location.reload(); } catch (e) { f.setAttribute("src", f.getAttribute("src")); }
  });
  $("#copyBtn").addEventListener("click", async () => {
    if (!state.site) return;
    const link = deepLink(state.site);
    try { await navigator.clipboard.writeText(link); toast("Link copied"); }
    catch (e) { openModal("Link to this site", h("input", { class: "input", value: link, readonly: true })); }
  });
  $("#newTabBtn").addEventListener("click", () => {
    if (state.site) window.open(location.href.split("#")[0] + `#/site/${state.site.slug}`, "_blank", "noopener");
  });

  // ---------- the Monitor page (§13.6) ----------
  const STATE_LABEL = { up: "Up", down: "Down", restarting: "Restarting", waiting: "Waiting after restart",
    needs_attention: "Needs attention", paused: "Paused", not_checked: "Not checked" };
  let monitorTimer = null;
  function stopMonitor() { if (monitorTimer) { clearInterval(monitorTimer); monitorTimer = null; } }

  function when(isoText) {
    if (!isoText) return "";
    const d = new Date(isoText);
    if (isNaN(d)) return "";
    const today = new Date().toDateString() === d.toDateString();
    return today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  async function openMonitor(focus) {
    show("monitor");
    stopMonitor();
    await renderMonitor(focus);
    monitorTimer = setInterval(() => { if (!document.hidden && !menuOpen()) renderMonitor(null); }, 15000);
  }

  // The page refreshes itself every 15 seconds; not while an admin has a Pause menu (or another control) open in it,
  // which a refresh would close under their finger.
  function menuOpen() {
    const el = document.activeElement;
    return !!(el && el !== document.body && $("#monitorList").contains(el) && el.tagName === "SELECT");
  }

  async function renderMonitor(focus) {
    let r;
    try { r = await api("api/monitor"); } catch (e) { toast(e.message, true); return; }
    const box = clear($("#monitorList"));
    $("#noChecks").hidden = r.sites.length > 0;
    for (const s of r.sites) box.append(monitorCard(s, r.isAdmin));
    if (focus) {
      const el = box.querySelector(`[data-slug="${focus}"]`);
      if (el) { el.classList.add("focus"); el.scrollIntoView({ block: "center" }); }
      else toast("That site isn't on your Monitor page.", true);
      history.replaceState(history.state, "", "#/monitor");
    }
  }

  function monitorCard(s, isAdmin) {
    const strip = h("div", { class: "strip", title: "The last 24 hours" },
      s.strip.map((b) => h("span", { class: `seg ${b || "none"}` })));
    const mute = h("button", { type: "button", class: "icon-btn", title: s.muted ? "Alerts muted for you — select to hear them again" : "Mute this site's alerts for you",
      onclick: async () => {
        try { await api(`api/monitor/${s.slug}/mute`, { method: "POST", body: { muted: !s.muted } }); renderMonitor(null); }
        catch (e) { toast(e.message, true); }
      } }, s.muted ? "🔕" : "🔔");
    const facts = [];
    if (s.since) facts.push(`since ${when(s.since)}`);
    if (s.ms != null && s.state === "up") facts.push(`${s.ms} ms`);
    if (s.error && s.state !== "up") facts.push(s.error);
    if (s.restarts) facts.push(`${s.restarts} restart${s.restarts > 1 ? "s" : ""}`);
    if (s.state === "paused") facts.push(s.pausedUntil ? `until ${when(s.pausedUntil)}` : "until resumed");
    if (s.blocked && s.state !== "needs_attention") facts.push(`not restarted: ${s.blocked}`);
    const actions = isAdmin ? adminActions(s) : null;
    return h("div", { class: "card monitor-card", dataset: { slug: s.slug } },
      h("div", { class: "monitor-head" },
        h("span", { class: "site-icon", "aria-hidden": "true" }, s.favicon ? h("img", { src: s.favicon, alt: "", class: "fav" }) : s.icon),
        h("div", { class: "site-main" },
          h("div", { class: "site-name" }, s.name, s.use === "monitor" ? h("span", { class: "dim small" }, "  monitor only") : null),
          h("div", { class: "small" }, h("span", { class: `badge ${s.state}` }, STATE_LABEL[s.state] || s.state),
            facts.length ? h("span", { class: "dim" }, "  " + facts.join(" · ")) : null)),
        s.alerted ? mute : null),
      strip, actions);
  }

  function adminActions(s) {
    const act = async (path, body, msg) => {
      try { const r = await api(`api/admin/sites/${s.id}/${path}`, { method: "POST", body: body || {} });
        if (msg) toast(typeof msg === "function" ? msg(r) : msg); renderMonitor(null); }
      catch (e) { toast(e.message, true); }
    };
    const pause = h("select", { class: "input small-select", title: "Pause checks and restarts" },
      h("option", { value: "" }, "Pause…"), h("option", { value: "1" }, "for 1 hour"), h("option", { value: "8" }, "for 8 hours"),
      h("option", { value: "forever" }, "until resumed"));
    pause.addEventListener("change", () => {
      if (!pause.value) return;
      act("pause", { hours: pause.value === "forever" ? null : Number(pause.value) }, "Paused");
    });
    const restart = async () => {
      if (!await UI.confirmDialog(`Restart ${s.name}?`, "Its switch is turned off for a few seconds, then on again. " +
        "Anything else on that plug loses power too.", { okLabel: "Restart", okClass: "btn-primary danger" })) return;
      act("restart", null, "Restarting…");
    };
    return h("div", { class: "monitor-actions" },
      h("button", { type: "button", class: "btn-secondary", onclick: () => act("check-now", null, (r) =>
        (r.ok ? `Answered in ${r.ms} ms` : `No: ${r.error}`) + (r.recorded === false ? " (not counted: it is restarting or starting up)" : "")) }, "Check now"),
      s.canRestart ? h("button", { type: "button", class: "btn-secondary", disabled: s.state === "restarting", onclick: restart }, "🔌 Restart now") : null,
      s.state === "needs_attention" && s.canRestart ? h("button", { type: "button", class: "btn-secondary", onclick: () => act("retry", null, "Trying again") }, "Try again") : null,
      s.state === "paused" ? h("button", { type: "button", class: "btn-secondary", onclick: () => act("resume", null, "Resumed") }, "Resume") : pause,
      h("button", { type: "button", class: "btn-ghost", onclick: () => showHistory(s) }, "History"));
  }

  async function showHistory(s) {
    const body = h("div", null, "Loading…");
    openModal(`${s.name} — history`, body, { modalClass: "wide" });
    try {
      const r = await api(`api/admin/sites/${s.id}/history`);
      const pct = (v) => (v == null ? "—" : `${v}%`);
      const OUT = { up: "back up", up_after_restart: "back up after restart", gave_up: "gave up", deleted: "deleted" };
      mount(clear(body),
        h("div", { class: "uptime" }, h("span", null, `24 h: ${pct(r.uptime.day)}`), h("span", null, `7 days: ${pct(r.uptime.week)}`),
          h("span", null, `30 days: ${pct(r.uptime.month)}`)),
        r.incidents.length ? h("table", { class: "people" }, h("tbody", null, r.incidents.map((i) => h("tr", null,
          h("td", null, when(i.started_at)), h("td", null, i.ended_at ? `→ ${when(i.ended_at)}` : "ongoing"),
          h("td", null, i.cause || ""), h("td", null, i.restarts ? `${i.restarts} restart${i.restarts > 1 ? "s" : ""}` : ""),
          h("td", null, OUT[i.outcome] || ""))))) : h("p", { class: "dim" }, "No incidents in the last 90 days."));
    } catch (e) { mount(clear(body), h("p", { class: "error" }, e.message)); }
  }

  // ---------- who am I ----------
  async function openWhoami() {
    const body = h("div", null, "Loading…");
    openModal("How the app sees you", body);
    try {
      const w = await api("api/whoami");
      mount(clear(body), HouseholdWhoami.panel(w, { appName: "Household Proxy", classes: { copy: "btn-ghost" } }));
    } catch (e) { mount(clear(body), h("p", { class: "error" }, e.message)); }
  }
  $("#userBtn").addEventListener("click", openWhoami);

  // ---------- admin ----------
  $("#adminBtn").addEventListener("click", () => go("#/admin/" + state.adminTab));
  $("#homeBtn").addEventListener("click", () => go("#/"));
  $$("#topnav .tab").forEach((b) => b.addEventListener("click", () => go(b.dataset.view === "sites" ? "#/" : `#/${b.dataset.view}`)));
  $("#homeBtn").addEventListener("click", stopMonitor);
  $$("#adminTabs .tab").forEach((b) => b.addEventListener("click", () => go(`#/admin/${b.dataset.tab}`)));

  const ADMIN_TABS = { sites: loadAdminSites, settings: loadSettings, people: loadPeople, storage: () => {} };

  function openAdmin(tab) {
    if (!ADMIN_TABS[tab]) tab = "sites";
    state.adminTab = tab;
    show("admin");
    $$("#adminTabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    for (const t of Object.keys(ADMIN_TABS)) $(`#tab-${t}`).hidden = t !== tab;
    ADMIN_TABS[tab]();
  }

  // Admin → Sites
  async function loadAdminSites() {
    const box = $("#tab-sites");
    try { state.admin = await api("api/admin/sites"); }
    catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); return; }
    const list = h("div", { class: "site-list" });
    state.admin.sites.forEach((s, i) => list.append(siteRow(s, i)));
    mount(clear(box),
      h("div", { class: "card" },
        h("div", { class: "row-between" }, h("h3", null, "Sites"),
          h("button", { type: "button", class: "btn-primary", onclick: () => editSite(null) }, "＋ Add a site")),
        h("p", { class: "hint" }, "Each site is a web page on your home network, opened inside Home Assistant. Only " +
          "the sites listed here can be reached, and only by the people allowed on each."),
        state.admin.sites.length ? list : h("p", { class: "dim" }, "No sites yet.")));
  }

  const ACCESS_LABEL = { admins: "Admins only", everyone: "Everyone", chosen: "Chosen people" };

  function siteRow(s, i) {
    const n = state.admin.sites.length;
    const move = async (d) => {
      const ids = state.admin.sites.map((x) => x.id);
      const [x] = ids.splice(i, 1);
      ids.splice(i + d, 0, x);
      try { await api("api/admin/sites/reorder", { method: "POST", body: { ids } }); loadAdminSites(); loadSites(); }
      catch (e) { toast(e.message, true); }
    };
    return h("div", { class: "site-row" },
      h("span", { class: "site-icon", "aria-hidden": "true" }, s.icon),
      h("div", { class: "site-main" },
        h("div", { class: "site-name" }, s.name, h("span", { class: "dim" }, `  /${s.slug}`)),
        h("div", { class: "dim small" }, `${s.target}${s.start_path !== "/" ? s.start_path : ""} · ${ACCESS_LABEL[s.access]}`)),
      h("div", { class: "site-actions" },
        h("button", { type: "button", class: "icon-btn", title: "Move up", disabled: i === 0, onclick: () => move(-1) }, "▲"),
        h("button", { type: "button", class: "icon-btn", title: "Move down", disabled: i === n - 1, onclick: () => move(1) }, "▼"),
        h("button", { type: "button", class: "btn-secondary", onclick: () => editSite(s) }, "Edit")));
  }

  function field(label, control, hint) {
    return h("label", { class: "field" }, h("span", { class: "field-label" }, label), control,
      hint ? h("span", { class: "hint small" }, hint) : null);
  }

  function check(label, checked, hint) {
    const box = h("input", { type: "checkbox", checked: !!checked });
    const row = h("label", { class: "check" }, box, h("span", null, label));
    return { box, el: h("div", { class: "check-row" }, row, hint ? h("div", { class: "hint small" }, hint) : null) };
  }

  function editSite(site) {
    const s = site || { name: "", slug: "", icon: "🌐", target: "", start_path: "/", access: "admins", people: [],
      trusted_ok: false, spa_mode: false, show_bar: true, keep_host: false, strip_csp: false, pass_user: false,
      allow_public: false, access_log: false, timeout_s: 0, headers: [], rules: [], use_favicon: false, use: "proxy",
      check: { kind: null, path: "", every_s: 60, timeout_s: 5, down_after: 3, ok_codes: "below500", sensor: true },
      alerts: { people: null, service: "", events: ["down", "back_up", "gave_up", "couldnt_restart"] },
      restart: { entity: null, confirmed: false, off_for_s: 5, wait_after_s: 120, max_restarts: 3, window_start: "", window_end: "" } };
    const f = {
      name: h("input", { class: "input", value: s.name, maxlength: 60, required: true, placeholder: "Komodo" }),
      slug: h("input", { class: "input", value: s.slug, maxlength: 40, placeholder: "komodo" }),
      icon: h("input", { class: "input icon-input", value: s.icon, maxlength: 16 }),
      target: h("input", { class: "input", value: s.target, maxlength: 300, placeholder: "http://192.168.1.10:9120" }),
      start: h("input", { class: "input", value: s.start_path, maxlength: 500 }),
      access: h("select", { class: "input" }, Object.entries(ACCESS_LABEL).map(([v, l]) =>
        h("option", { value: v, selected: s.access === v }, l))),
      timeout: h("input", { class: "input", type: "number", min: 0, max: 600, value: s.timeout_s }),
    };
    const people = h("div", { class: "people-picks" }, (state.admin.people || []).map((p) =>
      h("label", { class: "check" }, h("input", { type: "checkbox", value: p.id, checked: s.people.includes(p.id) }), p.name)));
    const peopleRow = field("People who may open it", people, "Everyone who has opened this app is listed.");
    const syncAccess = () => { peopleRow.hidden = f.access.value !== "chosen"; };
    f.access.addEventListener("change", syncAccess);
    syncAccess();

    const trusted = check("This is software I run and keep updated", s.trusted_ok,
      "A site opened here runs on Home Assistant's own address: a page there can act as the person viewing it in " +
      "Home Assistant. Add only software you trust and keep updated (Komodo, your NAS) — not a router, printer or " +
      "camera page with old firmware.");
    const spa = check("Single-page app mode", s.spa_mode, "For apps with their own router (React, Vue — Komodo is one) " +
      "that show “not found” under the proxy. Reloading inside the frame then goes through ↻.");
    const bar = check("Show the bar", s.show_bar, "Off suits apps with their own navigation; a small ← stays.");
    const pub = check("Allow an internet address", s.allow_public, "Only for a site that isn't on your home network.");
    const keepHost = check("Send Home Assistant's host name", s.keep_host, "Normally the site gets its own address as Host.");
    const stripCsp = check("Remove the site's Content-Security-Policy", s.strip_csp, "Only if the site's own policy blocks the proxy's script.");
    const passUser = check("Pass the viewer's name (Remote-User)", s.pass_user, "Only for sites with header sign-in that accept this header from this app alone.");
    const accessLog = check("Keep an access log", s.access_log, "Time, person, method, path and status — no contents.");

    const headerRows = h("div", { class: "header-rows" });
    const addHeader = (hd) => {
      const name = h("input", { class: "input", value: hd ? hd.name : "", placeholder: "Authorization", maxlength: 64 });
      const value = h("input", { class: "input", type: "password", autocomplete: "new-password",
        placeholder: hd && hd.saved ? "saved — type to replace" : "value", maxlength: 4096 });
      const row = h("div", { class: "header-row" }, name, value,
        h("button", { type: "button", class: "icon-btn", title: "Remove", onclick: () => row.remove() }, "✕"));
      row._get = () => ({ name: name.value.trim(), value: value.value ? value.value : (hd && hd.saved && name.value.trim() === hd.name ? null : "") });
      headerRows.append(row);
    };
    (s.headers || []).forEach(addHeader);

    const favicon = check("Use the site's own icon", s.use_favicon, "Fetched from the site when you save (a small PNG, ICO, GIF, JPEG or WebP).");

    const ruleRows = h("div", { class: "rule-rows" });
    const addRule = (r) => {
      r = r || { where: "body", types: "application/javascript", match: "", replace: "" };
      const where = h("select", { class: "input" }, h("option", { value: "body", selected: r.where === "body" }, "Body"),
        h("option", { value: "header", selected: r.where === "header" }, "Header values"));
      const types = h("input", { class: "input", value: r.types, placeholder: "content types (empty = all)", maxlength: 300 });
      const match = h("input", { class: "input mono", value: r.match, placeholder: "regular expression", maxlength: 500 });
      const replace = h("input", { class: "input mono", value: r.replace, placeholder: "replacement ($prefix = the site's prefix)", maxlength: 500 });
      const row = h("div", { class: "rule-row" }, where, types, match, replace,
        h("button", { type: "button", class: "icon-btn", title: "Remove", onclick: () => row.remove() }, "✕"));
      row._get = () => ({ where: where.value, types: types.value.trim(), match: match.value, replace: replace.value });
      ruleRows.append(row);
    };
    (s.rules || []).forEach(addRule);

    const report = h("div", { class: "test-report", hidden: true });
    const testBtn = h("button", { type: "button", class: "btn-secondary", onclick: () => runTest() }, "🔍 Test");

    async function runTest() {
      testBtn.disabled = true;
      report.hidden = false;
      mount(clear(report), h("p", { class: "dim" }, "Testing…"));
      try {
        const r = await api("api/admin/sites/test" + (site ? `?id=${site.id}` : ""), { method: "POST", body: body() });
        mount(clear(report), testReport(r));
      } catch (e) { mount(clear(report), h("p", { class: "error" }, e.message)); }
      testBtn.disabled = false;
    }

    function testReport(r) {
      const lines = [];
      const li = (t, cls) => lines.push(h("li", { class: cls || null }, t));
      if (!r.reachable) li(r.problem || "Couldn't reach the site.", "error");
      else {
        li(`Answered ${r.status} in ${r.ms} ms.`);
        if (r.redirects.length) li(`Redirected: ${r.redirects.join(" → ")}`);
        if (r.frameHeaders.length) li(`Frame-blocking headers found (${r.frameHeaders.join(", ")}) — the proxy removes them.`);
        li(`${r.rewritten} root paths in the page are rewritten.`);
        if (r.scripts.checked) {
          const bits = [];
          if (r.scripts.router) bits.push("a client-side router");
          if (r.scripts.basename) bits.push('a fixed base path ("/")');
          if (r.scripts.rootPaths) bits.push("root-path addresses (handled by the in-page script)");
          li(`Checked ${r.scripts.checked} script${r.scripts.checked > 1 ? "s" : ""}: ${bits.length ? bits.join(", ") : "nothing unusual"}.`);
        }
      }
      const out = [h("div", { class: `verdict v-${r.verdict}` }, r.verdictText), h("ul", null, lines)];
      if (r.certificate) {
        const c = r.certificate;
        out.push(h("div", { class: "cert" },
          h("div", null, h("strong", null, "Certificate: "), c.subject || "(no name)", c.notAfter ? `, until ${c.notAfter}` : ""),
          h("div", { class: "mono small" }, c.fingerprint),
          site ? h("button", { type: "button", class: "btn-secondary", onclick: async (e) => {
            e.target.disabled = true;
            try { await api(`api/admin/sites/${site.id}/certificate`, { method: "POST", body: { fingerprint: c.fingerprint } });
              toast("Certificate trusted"); runTest(); }
            catch (err2) { toast(err2.message, true); e.target.disabled = false; }
          } }, "Trust this certificate") : h("p", { class: "hint" }, "Save the site first, then trust its certificate here."),
          h("p", { class: "hint small" }, "Only this exact certificate will be accepted for this site, whatever its name and dates.")));
      }
      if (r.suggestedRule) {
        out.push(h("div", null, h("p", { class: "hint" }, "Suggested rule:"),
          h("code", null, `${r.suggestedRule.match} → ${r.suggestedRule.replace}`), " ",
          h("button", { type: "button", class: "link-btn", onclick: () => { addRule(r.suggestedRule); advanced.open = true; toast("Rule added — save to keep it"); } }, "Add it")));
      }
      if (r.verdict === "try_spa" && !spa.box.checked) {
        out.push(h("button", { type: "button", class: "link-btn", onclick: () => { spa.box.checked = true; runTest(); } }, "Turn on single-page app mode and test again"));
      }
      return out;
    }

    // Use, Check and Alerts (§13)
    const use = h("select", { class: "input" }, h("option", { value: "proxy", selected: s.use === "proxy" }, "Open in Proxy"),
      h("option", { value: "monitor", selected: s.use === "monitor" }, "Monitor only (never opened through the proxy)"));
    const ck = s.check;
    const cf = {
      kind: h("select", { class: "input" }, [["", "Not checked"], ["http", "Web page"], ["tcp", "Port"], ["ping", "Ping"]].map(([v, l]) =>
        h("option", { value: v, selected: (ck.kind || "") === v }, l))),
      path: h("input", { class: "input", value: ck.path, placeholder: "the start path", maxlength: 500 }),
      every: h("input", { class: "input", type: "number", min: 15, max: 86400, value: ck.every_s }),
      timeout: h("input", { class: "input", type: "number", min: 1, max: 60, value: ck.timeout_s }),
      downAfter: h("input", { class: "input", type: "number", min: 1, max: 10, value: ck.down_after }),
      codes: h("select", { class: "input" }, h("option", { value: "below500", selected: ck.ok_codes === "below500" }, "Any answer below 500"),
        h("option", { value: "2xx3xx", selected: ck.ok_codes === "2xx3xx" }, "Only 2xx and 3xx")),
    };
    const sensor = check("Home Assistant sensor", ck.sensor, "binary_sensor.household_proxy_<short name>: on while up.");
    const httpOnly = h("div", { class: "grid2 wide-first" }, field("Path to check", cf.path), field("Up when", cf.codes));
    const checkBody = h("div", { class: "sub-fields" },
      httpOnly, h("div", { class: "grid3" }, field("Every (seconds)", cf.every), field("Timeout (s)", cf.timeout),
        field("Down after (failed checks)", cf.downAfter)), sensor.el);
    const checkSection = h("fieldset", { class: "section" }, h("legend", null, "🩺 Check"),
      field("Kind", cf.kind, "Web page: a GET, redirects not followed. Port: a TCP connection. Ping: 3 echoes."), checkBody);

    const al = s.alerts;
    const alertPeople = h("div", { class: "people-picks" }, (state.admin.people || []).map((p) =>
      h("label", { class: "check" }, h("input", { type: "checkbox", value: p.id,
        checked: al.people === null ? !!p.isAdmin : al.people.includes(p.id) }), p.name)));
    const EVENTS = [["down", "Down"], ["restarting", "Restarting (each restart)"], ["back_up", "Back up"], ["gave_up", "Gave up"],
      ["couldnt_restart", "Couldn't restart"]];
    const events = h("div", { class: "people-picks" }, EVENTS.map(([v, l]) =>
      h("label", { class: "check" }, h("input", { type: "checkbox", value: v, checked: al.events.includes(v) }), l)));
    const service = h("input", { class: "input", value: al.service, placeholder: "notify.living_room_speaker", maxlength: 110 });
    const alertResult = h("p", { class: "hint small", role: "status" });
    const alertTest = site ? h("button", { type: "button", class: "btn-secondary", onclick: async () => {
      alertTest.disabled = true;
      alertResult.textContent = "Sending…";
      try {
        const r = await api(`api/admin/sites/${site.id}/alert-test`, { method: "POST", body: {} });
        const { list, text } = PeoplePage.resultsText(r.results);
        alertResult.textContent = text;
        const bad = list.filter((y) => !y.ok).map((y) => y.service);
        toast(bad.length ? `Sent, but Home Assistant refused ${bad.join(", ")}` : "Test alert sent", !!bad.length);
      } catch (e) { alertResult.textContent = e.message; toast(e.message, true); }
      setTimeout(() => { alertTest.disabled = false; }, 1500);
    } }, "🔔 Send a test alert") : null;
    const alertSection = h("fieldset", { class: "section" }, h("legend", null, "🔔 Alerts"),
      field("Tell", alertPeople, "Their phones from Home Assistant, plus any extra services set under Admin → People → 🔔. Any people — being told gives no access to the site."),
      field("Also send to", service, "An extra notify service (optional)."),
      field("About", events),
      site ? h("div", { class: "field" }, alertTest, h("span", { class: "hint small" }, " To everyone above, as saved."), alertResult) : null);

    const rs = s.restart;
    const rf = {
      entity: h("select", { class: "input" }, h("option", { value: "" }, "No auto-restart"),
        rs.entity ? h("option", { value: rs.entity, selected: true }, rs.entity) : null),
      offFor: h("input", { class: "input", type: "number", min: 1, max: 300, value: rs.off_for_s }),
      waitAfter: h("input", { class: "input", type: "number", min: 10, max: 3600, value: rs.wait_after_s }),
      max: h("input", { class: "input", type: "number", min: 0, max: 10, value: rs.max_restarts }),
      from: h("input", { class: "input", type: "time", value: rs.window_start || "" }),
      to: h("input", { class: "input", type: "time", value: rs.window_end || "" }),
    };
    const plugOk = check("This switch doesn't power Home Assistant, my router or my network switch", rs.confirmed,
      "Turning one of those off would cut Home Assistant off from the device — and from turning it back on.");
    api("api/admin/switches").then((r) => {
      const keep = rf.entity.value;
      mount(clear(rf.entity), h("option", { value: "" }, "No auto-restart"),
        r.switches.map((x) => h("option", { value: x.entity_id, selected: x.entity_id === keep }, `${x.name} (${x.entity_id}) — ${x.state}`)));
      if (keep && !r.switches.some((x) => x.entity_id === keep)) rf.entity.append(h("option", { value: keep, selected: true }, `${keep} — not found`));
    }).catch(() => { /* Home Assistant not reachable: the saved switch stays as it is */ });
    const restartBody = h("div", { class: "sub-fields" }, plugOk.el,
      h("div", { class: "grid3" }, field("Off for (seconds)", rf.offFor), field("Wait after restart (s)", rf.waitAfter),
        field("Restarts before giving up", rf.max, "0 = never restart, only report.")),
      h("div", { class: "grid2 even" }, field("Only restart from", rf.from), field("until", rf.to)),
      h("p", { class: "hint small" }, "Off, wait, on — then no checks until “Wait after restart” has passed. Never when the " +
        "network looks down or the switch is unavailable; sites sharing a switch are restarted once, together. Leave the " +
        "times empty to restart at any time."));
    const restartSection = h("fieldset", { class: "section" }, h("legend", null, "🔌 Auto-restart"),
      field("Switch that powers the device", rf.entity, "A smart plug in Home Assistant. Only switch.turn_off and switch.turn_on are used."),
      restartBody);
    rf.entity.addEventListener("change", () => { restartBody.hidden = !rf.entity.value; });

    const syncUse = () => {
      const mon = use.value === "monitor";
      for (const el of proxyOnly) el.hidden = mon;
      const kind = cf.kind.value;
      checkBody.hidden = !kind;
      httpOnly.hidden = kind !== "http";
      alertSection.hidden = !kind;
      restartSection.hidden = !kind;
      restartBody.hidden = !rf.entity.value;
      if (mon && !kind) cf.kind.value = "ping", syncUse();
    };
    use.addEventListener("change", syncUse);
    cf.kind.addEventListener("change", syncUse);
    let proxyOnly = [];

    const err = h("p", { class: "error" });
    const advanced = h("details", { class: "advanced" }, h("summary", null, "Advanced"),
      pub.el, keepHost.el, stripCsp.el, passUser.el, accessLog.el,
      field("Timeout (seconds, 0 = App setting)", f.timeout),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Extra headers"),
        h("span", { class: "hint small" }, "Added to every request to the site (e.g. a sign-in). Never sent to the browser. " +
          "Everyone allowed on this site acts as that account."),
        headerRows, h("button", { type: "button", class: "link-btn", onclick: () => addHeader(null) }, "＋ Add a header")),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Rules"),
        h("span", { class: "hint small" }, "For a site that hard-codes something the proxy misses: a regular expression and its " +
          "replacement, applied to the answers of the listed content types. $prefix is the site's prefix here. The Test suggests one when it sees a common case."),
        ruleRows, h("button", { type: "button", class: "link-btn", onclick: () => addRule(null) }, "＋ Add a rule")));

    const startRow = field("Start path", f.start);
    proxyOnly = [startRow, trusted.el, spa.el, bar.el, favicon.el, advanced];
    const form = h("form", { class: "site-form" },
      h("div", { class: "grid2" }, field("Name", f.name), field("Icon", f.icon)),
      field("Use", use),
      field("Address", f.target, "The site's address on your network: http(s)://host:port, no path. Monitor only: also a host name or address (ping), or host:port."),
      startRow,
      field("Short name", f.slug, "Used in its address: …/s/<short name>/. Lower-case letters, digits and -."),
      field("Who can open it", f.access, "Also who sees its status on the Monitor page."), peopleRow,
      trusted.el, spa.el, bar.el, favicon.el, checkSection, restartSection, alertSection, advanced, h("div", null, testBtn), report, err,
      h("div", { class: "row-between" },
        site ? h("button", { type: "button", class: "btn-ghost danger", onclick: () => remove() }, "Delete") : h("span"),
        h("div", null, h("button", { type: "button", class: "btn-ghost", onclick: () => modal.close() }, "Cancel"), " ",
          h("button", { type: "submit", class: "btn-primary" }, "Save"))));

    const body = () => ({
      name: f.name.value.trim(), slug: f.slug.value.trim(), icon: f.icon.value.trim() || "🌐",
      target: f.target.value.trim(), start_path: f.start.value.trim() || "/", access: f.access.value,
      people: $$("input:checked", people).map((x) => x.value), trusted_ok: trusted.box.checked,
      spa_mode: spa.box.checked, show_bar: bar.box.checked, keep_host: keepHost.box.checked,
      strip_csp: stripCsp.box.checked, pass_user: passUser.box.checked, allow_public: pub.box.checked,
      access_log: accessLog.box.checked, timeout_s: parseInt(f.timeout.value, 10) || 0,
      headers: $$(".header-row", headerRows).map((r) => r._get()).filter((x) => x.name),
      rules: $$(".rule-row", ruleRows).map((r) => r._get()).filter((x) => x.match),
      use_favicon: favicon.box.checked, use: use.value,
      check: { kind: cf.kind.value || null, path: cf.path.value.trim(), every_s: parseInt(cf.every.value, 10) || 60,
        timeout_s: parseInt(cf.timeout.value, 10) || 5, down_after: parseInt(cf.downAfter.value, 10) || 3,
        ok_codes: cf.codes.value, sensor: sensor.box.checked },
      alerts: { people: $$("input:checked", alertPeople).map((x) => x.value), service: service.value.trim(),
        events: $$("input:checked", events).map((x) => x.value) },
      restart: { entity: (cf.kind.value && rf.entity.value) || null, confirmed: plugOk.box.checked,
        off_for_s: parseInt(rf.offFor.value, 10) || 5, wait_after_s: parseInt(rf.waitAfter.value, 10) || 120,
        max_restarts: Number.isNaN(parseInt(rf.max.value, 10)) ? 3 : parseInt(rf.max.value, 10),
        window_start: rf.from.value || null, window_end: rf.to.value || null },
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      try {
        const saved = await api(site ? `api/admin/sites/${site.id}` : "api/admin/sites", { method: site ? "PUT" : "POST", body: body() });
        modal.close();
        toast("Saved");
        for (const w of saved.warnings || []) UI.toast("⚠️ " + w, { ms: 12000, wrap: true });   // saved, but worth knowing
        if (saved.use_favicon && !saved.has_favicon) {
          try { await api(`api/admin/sites/${saved.id}/favicon`, { method: "POST" }); }
          catch (e3) { toast(e3.message, true); }
        }
        await Promise.all([loadAdminSites(), loadSites()]);
      } catch (e2) { err.textContent = e2.message; }
    });

    async function remove() {
      if (!await UI.confirmDialog("Delete this site?", `${site.name} disappears for everyone. Nothing on the site itself changes.`,
        { okLabel: "Delete", okClass: "btn-primary danger" })) return;
      try { await api(`api/admin/sites/${site.id}`, { method: "DELETE" }); modal.close(); loadAdminSites(); loadSites(); }
      catch (e) { err.textContent = e.message; }
    }

    const modal = openModal(site ? `Edit ${site.name}` : "Add a site", form, { modalClass: "wide" });
    syncUse();
  }

  // Admin → App settings
  async function loadSettings() {
    const box = $("#tab-settings");
    try {
      await SettingsPage.render(box, {
        load: () => api("api/admin/settings"),
        save: (body) => api("api/admin/settings", { method: "PUT", body }),
        classes: { card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" },
        footer: "Changes apply right away. Who is an admin is set on the app's Configuration tab (admin_users).",
        toast: (m, e) => toast(m, e),
      });
    } catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); }
  }

  // Admin → People
  // the shared people page (common/people.js): access switch, then 🔔 phones, extra services and Send a test
  async function loadPeople(refresh) {
    const box = $("#tab-people");
    let r;
    try { r = await api("api/admin/people" + (refresh ? "?refresh=1" : "")); } catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); return; }
    PeoplePage.render(box, {
      people: r.people,
      intro: ["Everyone who has opened this app. Someone turned off opens no site, whatever each site allows.",
        "📱 Site alerts go to each person's phone from Home Assistant: Settings → People → (the person) → Track device, with the Companion app on the phone. 🔔 shows their phones, lets you add an extra notify service and send a test."],
      checkAgain: async () => { await loadPeople(true); toast("Read from Home Assistant"); },
      person: (p) => ({
        badges: [p.you ? ["you"] : null, p.isAdmin ? ["admin", "accent"] : null],
        sub: p.username || null,
        controls: h("label", { class: "pp-toggle" }, "May open sites ", PeoplePage.accessSwitch(p.enabled, async (on, t) => {
          try { await api(`api/admin/people/${encodeURIComponent(p.id)}`, { method: "PUT", body: { enabled: on } }); p.enabled = on; }
          catch (e) { t.checked = !on; toast(e.message, true); }
        }, { label: `May ${p.name} open sites`, disabled: p.you })),
      }),
      notify: {
        mode: "dialog", buttonClass: "btn-secondary", ghostClass: "btn-ghost", openModal, api,
        toast: (m, err) => toast(m, err), fail: (e) => toast(e.message, true),
        loadServices: () => api("api/admin/notify-services"),
        path: (p) => `api/admin/people/${encodeURIComponent(p.id)}/notify`,
        testPath: (p) => `api/admin/people/${encodeURIComponent(p.id)}/notify/test`,
        texts: { intro: (p) => `Where ${p.name}'s site alerts go (for the sites that tell them).` },
        onClose: () => loadPeople(),
      },
      empty: "Nobody has opened the app yet.",
    });
  }

  // Admin → Storage
  $("#importDb").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (!await UI.confirmDialog("Restore this backup?", "Every site and setting is replaced by the backup's.", { okLabel: "Restore" })) return;
    const fd = new FormData();
    fd.append("file", file);
    try { await api("api/admin-storage-import-db", { method: "POST", formData: fd }); toast("Restored"); await init(); }
    catch (err) { toast(err.message, true); }
  });

  // ---------- start ----------
  async function loadMe() {
    state.me = await api("api/me");
    $("#userBtn").textContent = state.me.user.name;
    $("#adminBtn").hidden = !state.me.user.isAdmin;
    $("#offNote").hidden = state.me.user.enabled;
    $("#monitorTab").hidden = !(state.me.user.isAdmin || state.me.monitor);
    HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), state.me.noAdmin, state.me.user.name, { onOpen: openWhoami });
  }

  async function init() {
    try { await loadMe(); } catch (e) { toast(e.message, true); return; }
    await loadSites();
    route();
  }

  window.addEventListener("hashchange", route);
  if (window.BackNav) {
    BackNav.init({
      atHome: () => state.view === "sites",
      goHome: () => { history.replaceState(history.state, "", "#/"); route(); },
      openLayers: () => UI.dialogs(),
      closeLayer: (m) => m.close(),
    });
  }
  init().then(() => {
    if (window.HouseholdDeepLink) {
      HouseholdDeepLink.start(state.me && state.me.page, (r) => (/^\/(site|monitor)\/[a-z0-9-]{1,40}$/.test(r) ? r : null),
        (r) => go("#" + r));
    }
  });
})();
