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
      box.append(h("a", { class: "tile", href: `#/site/${s.slug}`, title: s.name },
        h("span", { class: "tile-icon", "aria-hidden": "true" }, s.icon || "🌐"),
        h("span", { class: "tile-name" }, s.name)));
    }
  }

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
      allow_public: false, access_log: false, timeout_s: 0, headers: [] };
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

    const err = h("p", { class: "error" });
    const advanced = h("details", { class: "advanced" }, h("summary", null, "Advanced"),
      pub.el, keepHost.el, stripCsp.el, passUser.el, accessLog.el,
      field("Timeout (seconds, 0 = App setting)", f.timeout),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Extra headers"),
        h("span", { class: "hint small" }, "Added to every request to the site (e.g. a sign-in). Never sent to the browser. " +
          "Everyone allowed on this site acts as that account."),
        headerRows, h("button", { type: "button", class: "link-btn", onclick: () => addHeader(null) }, "＋ Add a header")));

    const form = h("form", { class: "site-form" },
      h("div", { class: "grid2" }, field("Name", f.name), field("Icon", f.icon)),
      field("Address", f.target, "The site's address on your network: http(s)://host:port, no path."),
      field("Start path", f.start),
      field("Short name", f.slug, "Used in its address: …/s/<short name>/. Lower-case letters, digits and -."),
      field("Who can open it", f.access), peopleRow,
      trusted.el, spa.el, bar.el, advanced, err,
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
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.textContent = "";
      try {
        await api(site ? `api/admin/sites/${site.id}` : "api/admin/sites", { method: site ? "PUT" : "POST", body: body() });
        modal.close();
        toast("Saved");
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
  async function loadPeople() {
    const box = $("#tab-people");
    let r;
    try { r = await api("api/admin/people"); } catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); return; }
    const row = (p) => {
      const on = h("input", { type: "checkbox", checked: p.enabled });
      on.addEventListener("change", async () => {
        try { await api(`api/admin/people/${encodeURIComponent(p.id)}`, { method: "PUT", body: { enabled: on.checked } }); }
        catch (e) { on.checked = !on.checked; toast(e.message, true); }
      });
      return h("tr", null, h("td", null, p.name, p.username ? h("span", { class: "dim" }, `  ${p.username}`) : null),
        h("td", null, h("label", null, on, " May open sites")));
    };
    mount(clear(box), h("div", { class: "card" },
      h("p", { class: "hint" }, "Everyone who has opened this app. Someone turned off opens no site, whatever each site allows."),
      h("table", { class: "people" }, h("tbody", null, r.people.map(row)))));
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
      HouseholdDeepLink.start(state.me && state.me.page, (r) => (/^\/site\/[a-z0-9-]{1,40}$/.test(r) ? r : null),
        (r) => go("#" + r));
    }
  });
})();
