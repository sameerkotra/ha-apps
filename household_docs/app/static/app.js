"use strict";
/* Household Docs — the shell: state, the API, the router, the sidebar and phone bar, the ➕ New menu, the spaces
   (Home, My docs, Shared with me, Everyone, Shared folders, Favourites, Recent, Trash), folders (list or grid,
   multi-select, drag and drop) and Settings. docs.js adds the editors and the item dialogs (share, move,
   history …), files.js uploads, previews, .zip downloads and the selection bar, search.js the Search page,
   admin.js the Admin pages; start.js starts the app once every script has registered what it adds.

   Plain JS, no build step, no libraries. Every fetch path is RELATIVE (Ingress serves the app under a
   sub-path). Everything from the server or a person goes in with textContent (UI.h), never parsed as HTML.

   Extension points (window.Docs):
     Docs.route(name, render)                    a page: "#/<name>/<arg…>" → render(args)
     Docs.addNew({id, icon, label, order, show?(ctx), run(ctx)})   an entry of ➕ New (ctx: {folderId, canEdit})
     Docs.kind(id, {icon, label, open(item)})    how an item of a kind looks and opens (folders, notes, …)
     Docs.addAction({id, label, order, show(item, ctx), run(item, ctx)})   an entry of an item's ⋯ menu
     Docs.addSpace({id, icon, label, order, hash, show?(), children?()})   a sidebar entry (children: sub-links)
     Docs.addBulk({id, label, icon, order, show(items), run(items)})      an action of the selection bar

   Places: a folder is named by its node id; "root:<id>" is the top of an admin shared folder; null is My docs.
*/

const Docs = (window.Docs = {
  state: { me: null, route: null, args: [], people: null },
  routes: {}, newItems: [], kinds: {}, actions: [], spaces: [], bulk: [],
  settingsRows: [],     // fn(me, save, row) → nodes for Settings → You (later steps: follows, quiet hours, AI, scans)
  rootActions: [],      // {id, icon, label, show?(f), run(f)} on an admin shared folder's top (⋯ in its head)
  route(name, render) { this.routes[name] = render; },
  addNew(item) { this.newItems.push(item); this.newItems.sort((a, b) => (a.order || 0) - (b.order || 0)); },
  kind(id, def) { this.kinds[id] = Object.assign({}, this.kinds[id] || {}, def); },
  addAction(a) { this.actions.push(a); this.actions.sort((x, y) => (x.order || 0) - (y.order || 0)); },
  addSpace(s) { this.spaces.push(s); this.spaces.sort((a, b) => (a.order || 0) - (b.order || 0)); },
  addBulk(b) { this.bulk.push(b); this.bulk.sort((x, y) => (x.order || 0) - (y.order || 0)); },
});

// ---------- helpers (common/ui.js) ----------
const { $, $$, clear, mount, debounce, lsGet, lsSet } = UI;
const h = UI.makeH({ booleanProps: ["checked", "disabled", "hidden", "selected"] });

class ApiError extends Error {
  constructor(msg, status, detail) { super(msg); this.status = status; this.detail = detail; }
}
const api = UI.makeApi({
  networkError: "Can't reach Household Docs. Check your connection and try again.",
  message: (body, res) => {
    const d = body ? body.detail : null;
    if (typeof d === "string") return d;
    if (d && typeof d.message === "string") return d.message;
    return UI.errorMessage(d, res.status);
  },
  makeError: (msg, status, body) => new ApiError(msg, status, body ? body.detail : null),
  onError: (err) => { if ((err.status === 503 && /Documents folder/.test(err.message)) || err.status === 423) refreshMe().catch(() => {}); },
});
Docs.api = api;
Docs.h = h;

function toast(msg, isError = false, opts = {}) {
  UI.toast(msg, Object.assign({ error: isError, role: "status", ms: isError ? 6000 : 3000 }, opts));
}
function fail(e) { if (e) toast(e.message || String(e), true); }
function openModal(title, content, opts = {}) {
  return UI.openModal(title, content, Object.assign({ modalClass: opts.wide ? "wide" : "sheet", escape: "stack",
    focus: opts.focus === undefined ? "first" : opts.focus, focusIf: matchMedia("(pointer: fine)").matches }, opts));
}
function confirmDialog(title, text, okLabel = "OK", danger = false) {
  return UI.confirmDialog(title, text, { okLabel, okClass: danger ? "btn-danger" : "btn-primary", cancelClass: "btn-ghost",
    focusOk: true, modal: { modalClass: "sheet", escape: "stack", focus: false } });
}
function spinner() { return h("div", { class: "spinner" }, "Loading…"); }
function errorCard(e, retry) {
  return h("div", { class: "card fatal" }, h("div", null, e.message || String(e)),
    retry ? h("button", { class: "btn-secondary", type: "button", onclick: retry }, "Try again") : null);
}
function pageHead(title, ...right) {
  return h("div", { class: "page-head" }, typeof title === "string" ? h("h2", null, title) : title,
    right.length ? h("div", { class: "head-actions" }, right) : null);
}
function fmtSize(n) { return DocsText.fmtBytes(n); }
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const now = new Date();
  const same = d.toDateString() === now.toDateString();
  if (same) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const days = (now - d) / 86400000;
  if (days < 6 && days > 0) return d.toLocaleDateString([], { weekday: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}
function fmtFull(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function titleOf(item) {
  // documents show their title (the file name without the extension); other files their full name
  if (item.document && item.ext && item.name.toLowerCase().endsWith("." + item.ext)) return item.name.slice(0, -(item.ext.length + 1));
  return item.name;
}
function go(hash) { if (window.BackNav) BackNav.go(hash); else location.hash = hash; }
function folderHash(ref) { return ref ? "#/folder/" + encodeURIComponent(ref) : "#/mine"; }
function searchHash(params) { return "#/search/" + encodeURIComponent(DocsText.searchArg(params)); }
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Couldn't copy", true));
}
const isChild = () => !!(Docs.state.me && Docs.state.me.isChild);      // Kids' space (§17.20): a simpler app
Object.assign(Docs, { toast, fail, openModal, confirmDialog, spinner, errorCard, pageHead, fmtSize, fmtWhen, fmtFull, titleOf, go,
  folderHash, searchHash, isChild });

// ---------- me ----------
async function refreshMe() {
  Docs.state.me = await api("api/me");
  syncBanners();
  return Docs.state.me;
}
Docs.refreshMe = refreshMe;
Docs.syncBanners = () => syncBanners();
async function people() {
  if (!Docs.state.people) Docs.state.people = (await api("api/people")).people;
  return Docs.state.people;
}
Docs.people = people;

function syncBanners() {
  const me = Docs.state.me;
  HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), me && me.noAdmin, me && (me.nameSent ? me.username : me.id),
    { onOpen: () => go("#/settings/whoami"), linkId: "noAdminWhoami" });
  const fb = $("#folderBanner");
  const df = me && me.docsFolder;
  fb.hidden = !df || df.ok;
  if (df && !df.ok) mount(fb, h("span", null, h("strong", null, "⚠ "), df.message || "Documents folder not found — is the storage connected?",
    me.isAdmin ? [" ", h("a", { href: "#/admin/folder" }, "Admin → Documents folder")] : null));
  const mb = $("#moveBanner");
  const ro = me && me.readOnly;
  mb.hidden = !ro;
  document.body.classList.toggle("read-only-mode", !!ro);
  if (ro) mount(mb, h("span", null, h("strong", null, "📦 "), `Documents are being moved — you can read but not change anything until ${ro.byName || "an admin"} finishes.`,
    me.isAdmin ? [" ", h("a", { href: "#/admin/folder" }, "Admin → Documents folder")] : null));
  const hb = $("#httpBanner");
  const warn = window.HttpWarning && HttpWarning.banner("You're using Home Assistant over plain HTTP: your documents cross your network unencrypted.", { className: "http-banner" });
  hb.hidden = !warn;
  if (warn) mount(hb, warn);
}

// ---------- kinds ----------
Docs.kind("folder", { icon: "📁", label: "Folder", open: (it) => go("#/folder/" + it.id) });
Docs.kind("note", { icon: "📝", label: "Note", open: (it) => go("#/doc/" + it.id) });
Docs.kind("markdown", { icon: "📝", label: "Markdown note", open: (it) => go("#/doc/" + it.id) });
Docs.kind("checklist", { icon: "☑️", label: "Checklist", open: (it) => go("#/doc/" + it.id) });
Docs.kind("sheet", { icon: "📊", label: "Sheet", open: (it) => download(it) });
Docs.kind("file", { icon: "📎", label: "File", open: (it) => (Docs.filePanel ? Docs.filePanel(it) : download(it)) });
function kindOf(it) { return Docs.kinds[it.kind] || Docs.kinds.file; }
function openItem(it) { kindOf(it).open(it); }
function download(it) {
  const url = it.kind === "folder" ? `api/nodes/${encodeURIComponent(it.id)}/zip` : `api/nodes/${encodeURIComponent(it.id)}/file`;
  const a = h("a", { href: url, download: it.kind === "folder" ? it.name + ".zip" : it.name });
  document.body.appendChild(a); a.click(); a.remove();
}
const IMAGE_EXTS = ["png", "jpg", "jpeg", "gif", "webp"];
function iconBox(it, cls) {
  // the item's icon, with its colour (§17.1) as a dot and a tint
  return h("span", { class: cls + (it.color ? " tint tint-" + it.color : ""), "aria-hidden": "true", title: it.color ? `Colour: ${it.color}` : null }, iconOf(it),
    it.color ? h("span", { class: "color-dot dot-" + it.color }) : null);
}
function iconOf(it) {
  if (it.kind === "file" && (it.preview || IMAGE_EXTS.includes(it.ext))) return "🖼️";
  if (it.kind === "file" && it.ext === "pdf") return "📕";
  if (it.isRoot) return it.role === "editor" ? "📂" : "🗂️";
  return kindOf(it).icon;
}
Object.assign(Docs, { kindOf, openItem, download, iconOf, iconBox, IMAGE_EXTS });

// ---------- ⋯ menus ----------
let openMenuEl = null;
function closeMenu() { if (openMenuEl) { openMenuEl.remove(); openMenuEl = null; } }
function menu(anchor, entries) {
  closeMenu();
  const list = entries.filter(Boolean);
  if (!list.length) return null;
  const m = h("div", { class: "menu", role: "menu" }, list.map((e) => e === "-" ? h("hr") :
    h("button", { type: "button", role: "menuitem", class: e.danger ? "danger" : "", dataset: { action: e.id || "" },
      onclick: () => { closeMenu(); e.run(); } }, e.icon ? h("span", { class: "menu-icon", "aria-hidden": "true" }, e.icon) : null, e.label)));
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  const w = m.offsetWidth, mh = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + "px";
  m.style.top = (r.bottom + mh + 6 > window.innerHeight ? Math.max(6, r.top - mh - 4) : r.bottom + 4) + "px";
  openMenuEl = m;
  setTimeout(() => document.addEventListener("click", function once(ev) {
    if (openMenuEl === m && !m.contains(ev.target)) closeMenu();
    document.removeEventListener("click", once);
  }), 0);
  const first = m.querySelector("button");
  if (first && matchMedia("(pointer: fine)").matches) first.focus();
  return m;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openMenuEl) { closeMenu(); e.stopPropagation(); } }, true);
Docs.menu = menu;
Docs.closeMenu = closeMenu;
Docs.openMenu = () => openMenuEl;

function itemMenu(anchor, item, ctx = {}) {
  const entries = Docs.actions.filter((a) => !a.show || a.show(item, ctx)).map((a) => (a.sep ? "-" : {
    id: a.id, label: typeof a.label === "function" ? a.label(item, ctx) : a.label, icon: a.icon, danger: a.danger,
    run: () => a.run(item, ctx),
  }));
  while (entries.length && entries[entries.length - 1] === "-") entries.pop();
  return menu(anchor, entries);
}
Docs.itemMenu = itemMenu;

// ---------- ➕ New ----------
function newContext() {
  const r = Docs.state.route;
  const folder = Docs.state.currentFolder;
  if ((r === "folder" || r === "mine") && folder) return { folderId: folder.id, canEdit: folder.canEdit, label: folder.name, names: folder.names || [] };
  return { folderId: null, canEdit: true, label: "My docs", names: null };
}
Docs.newContext = newContext;
function openNewMenu(anchor) {
  const ctx = newContext();
  if (!ctx.canEdit) { toast("You can only view this folder — new items go into My docs.", false); ctx.folderId = null; ctx.label = "My docs"; ctx.names = null; }
  menu(anchor, Docs.newItems.filter((n) => !n.show || n.show(ctx)).map((n) => ({ id: "new-" + n.id, icon: n.icon,
    label: typeof n.label === "function" ? n.label(ctx) : n.label, run: () => n.run(ctx) })));
}
async function askName(title, label, initial = "", okLabel = "Create") {
  return new Promise((resolve) => {
    let done = false;
    const input = h("input", { type: "text", value: initial, maxlength: "200", "aria-label": label, id: "nameInput" });
    const err = h("div", { class: "error-text", role: "alert" });
    const ok = h("button", { class: "btn-primary", type: "submit" }, okLabel);
    const form = h("form", { class: "name-form" }, h("label", { class: "field" }, label, input), err,
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), ok));
    const m = openModal(title, form, { onClose: () => { if (!done) resolve(null); } });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = input.value.trim();
      if (!v) { err.textContent = "Type a name."; return; }
      done = true; m.close(); resolve(v);
    });
    setTimeout(() => { input.focus(); input.select(); }, 40);
  });
}
Docs.askName = askName;
async function createDoc(kind, defaultName, ctx) {
  const name = await askName(`New ${Docs.kinds[kind].label.toLowerCase().replace("markdown", "Markdown")}`, "Name", defaultName);
  if (!name) return;
  try {
    const it = await api("api/docs", { method: "POST", body: { kind, name, parentId: ctx.folderId } });
    toast(`${Docs.kinds[kind].label} made in ${ctx.label}`);
    openItem(it);
  } catch (e) { fail(e); }
}
Docs.createDoc = createDoc;
// "Note" makes the person's default type (Settings → You: plain text or Markdown); the other type is listed too
const defaultNote = () => ((Docs.state.me && Docs.state.me.prefs && Docs.state.me.prefs.defaultNote) === "markdown" ? "markdown" : "note");
Docs.addNew({ id: "note", icon: "📝", label: "Note", order: 10, run: (ctx) => createDoc(defaultNote(), "", ctx) });
Docs.addNew({ id: "note-other", icon: "📝", order: 11, label: () => (defaultNote() === "markdown" ? "Plain text note" : "Markdown note"),
  run: (ctx) => createDoc(defaultNote() === "markdown" ? "note" : "markdown", "", ctx) });
Docs.addNew({ id: "checklist", icon: "☑️", label: "Checklist", order: 20, run: (ctx) => createDoc("checklist", "", ctx) });
Docs.addNew({ id: "folder", icon: "📁", label: "Folder", order: 90, run: async (ctx) => {
  const name = await askName("New folder", "Name");
  if (!name) return;
  try {
    const it = await api("api/docs", { method: "POST", body: { kind: "folder", name, parentId: ctx.folderId } });
    if (Docs.state.route === "folder" || Docs.state.route === "mine") render(); else openItem(it);
  } catch (e) { fail(e); }
} });

// ---------- the sidebar and the phone bar ----------
Docs.addSpace({ id: "home", icon: "🏠", label: "Home", order: 0, hash: "#/" });
Docs.addSpace({ id: "mine", icon: "📄", label: "My docs", order: 10, hash: "#/mine" });
Docs.addSpace({ id: "shared", icon: "👥", label: "Shared with me", order: 20, hash: "#/shared" });
Docs.addSpace({ id: "everyone", icon: "👪", label: "Everyone", order: 30, hash: "#/everyone", show: () => !isChild() });
// a parent an admin let view a child's My docs (§17.20)
Docs.addSpace({ id: "kids", icon: "🧒", label: "Kids' docs", order: 35, hash: "#/kids",
  show: () => !!(Docs.state.me && Docs.state.me.kidsView && Docs.state.me.kidsView.length),
  children: () => ((Docs.state.me && Docs.state.me.kidsView) || []).map((k) => ({ id: "kid-" + k.childId, label: k.name, hash: folderHash("root:" + k.rootId) })) });
Docs.addSpace({ id: "folders", icon: "📁", label: "Shared folders", order: 40, hash: "#/folders",
  show: () => Docs.state.me && Docs.state.me.sharedFolders && Docs.state.me.sharedFolders.length > 0 });
Docs.addSpace({ id: "favourites", icon: "⭐", label: "Favourites", order: 50, hash: "#/favourites" });
Docs.addSpace({ id: "searches", icon: "🔎", label: "Saved searches", order: 65, hash: "#/searches",
  children: () => ((Docs.state.me && Docs.state.me.pinnedSearches) || []).map((s) => ({ id: "saved-" + s.id, label: s.name,
    hash: searchHash(Object.assign({ q: s.q }, s.filters)) })) });
Docs.addSpace({ id: "settings", icon: "⚙️", label: "Settings", order: 90, hash: "#/settings", sep: true });
Docs.addSpace({ id: "admin", icon: "🛡️", label: "Admin", order: 95, hash: "#/admin", show: () => Docs.state.me && Docs.state.me.isAdmin });

function activeSpace() {
  const r = Docs.state.route;
  if (r === "folder") return Docs.state.currentSpace || "mine";
  if (r === "doc") return Docs.state.currentSpace || null;
  if (["recent", "trash", "storage"].includes(r)) return "activity";     // tabs of the Activity page
  return r === "" ? "home" : r;
}
function drawNav() {
  const nav = $("#sideNav");
  const act = activeSpace();
  mount(nav, Docs.spaces.filter((s) => !s.show || s.show()).map((s) => [s.sep ? h("div", { class: "nav-sep" }) : null,
    h("a", { class: "tab-btn" + (act === s.id ? " active" : ""), href: s.hash, title: s.label, dataset: { space: s.id },
      "aria-current": act === s.id ? "page" : null },
    h("span", { class: "nav-icon", "aria-hidden": "true" }, s.icon), h("span", { class: "nav-label" }, s.label)),
    s.children ? s.children().map((c) => h("a", { class: "tab-btn sub-link", href: c.hash, title: c.label, dataset: { space: c.id } },
      h("span", { class: "nav-icon", "aria-hidden": "true" }, "·"), h("span", { class: "nav-label" }, c.label))) : null]));
  const bar = $("#bottomBar");
  const btn = (id, icon, label, onclick, active) => h("button", { type: "button", class: "bb-btn" + (active ? " active" : ""), dataset: { bb: id }, onclick, "aria-label": label },
    h("span", { class: "bb-icon", "aria-hidden": "true" }, icon), h("span", { class: "bb-label" }, label));
  const moreIds = ["everyone", "folders", "favourites", "recent", "searches", "trash", "settings", "admin", "more"];
  mount(bar,
    btn("docs", "📄", "Docs", () => go("#/"), act === "home" || act === "mine"),
    btn("search", "🔎", "Search", () => go("#/search"), act === "search"),
    btn("new", "➕", "New", (e) => openNewMenu(e.currentTarget), false),
    btn("shared", "👥", "Shared", () => go("#/shared"), act === "shared"),
    btn("more", "☰", "More", () => go("#/more"), moreIds.includes(act)));
}

// ---------- router ----------
function parseHash() {
  const raw = String(location.hash || "").replace(/^#\/?/, "");
  const parts = raw.split("/").map((p) => { try { return decodeURIComponent(p); } catch (e) { return p; } });
  return { name: parts[0] || "", args: parts.slice(1).filter((x) => x !== "") };
}
let renderSeq = 0;
async function render() {
  const { name, args } = parseHash();
  const fn = Docs.routes[name] || Docs.routes[""];
  if (Docs.state.leaving) { try { await Docs.state.leaving(); } catch (e) { /* ignore */ } }
  Docs.state.leaving = null;
  Docs.state.route = Docs.routes[name] ? name : "";
  Docs.state.args = args;
  if (!["folder", "doc"].includes(Docs.state.route)) Docs.state.currentSpace = null;
  if (Docs.state.route !== "folder" && Docs.state.route !== "mine") Docs.state.currentFolder = null;
  document.body.dataset.route = Docs.state.route || "home";
  if (Docs.state.route !== "doc" && Docs.isFull && Docs.isFull()) Docs.setFull(false);    // full screen is for a document
  closeMenu();
  const seq = ++renderSeq;
  const page = $("#page");
  drawNav();
  try {
    await fn(page, args, () => seq === renderSeq);
  } catch (e) {
    if (seq === renderSeq) mount(page, errorCard(e, render));
  }
  if (seq === renderSeq) drawNav();
}
Docs.render = render;
Docs.drawNav = drawNav;
Docs.isHome = () => Docs.state.route === "";

// ---------- lists ----------
/* ctx: showOwner, showLocation, snippet(it), extraMeta(it), noMenu, space, inFolder,
        select: {ids: Set, toggle(it, on)} — a tick box per item; grid: tiles instead of rows;
        dnd: {canDrop: bool} — drag items onto folders (and files from the computer into them) */
function itemMeta(it, ctx) {
  const meta = [];
  if (ctx.showOwner && it.ownerName && it.ownerId !== Docs.state.me.id) meta.push(it.ownerName);
  if (ctx.showOwner && it.rootKind === "shared" && it.rootLabel) meta.push(it.rootLabel);
  if (ctx.showLocation && it.location) meta.push(it.location);
  if (it.kind !== "folder") {
    if (it.modified) meta.push(fmtWhen(it.modified));
    if (it.outside && !it.updatedByName) meta.push("added outside the app");
    else if (it.updatedByName && it.updatedBy !== Docs.state.me.id) meta.push(it.updatedByName);
    if (it.size !== null && it.size !== undefined) meta.push(fmtSize(it.size));
  } else if (it.outside) meta.push("added outside the app");
  if (ctx.extraMeta) meta.push(...ctx.extraMeta(it));
  return meta;
}
function nameNode(it, ctx) {
  if (ctx.markName && it.nameMarked) return h("span", { class: "nm" }, highlight(it.document && it.ext && it.nameMarked.toLowerCase().endsWith("." + it.ext) ? it.nameMarked.slice(0, -(it.ext.length + 1)) : it.nameMarked));
  return h("span", { class: "nm" }, titleOf(it));
}
function badges(it, ctx = {}) {
  const roleChip = it.role && it.role !== "owner" && it.role !== ctx.folderRole;      // the folder's own role is in its head
  const tags = it.tags || [];
  return [it.changed ? h("span", { class: "new-dot", title: "Changed since you last looked", "aria-label": "Changed since you last looked" }) : null,
    it.pinned ? h("span", { class: "row-badge", title: "Pinned to your home page" }, "📌") : null,
    it.favourite ? h("span", { class: "row-badge", title: "Favourite" }, "⭐") : null,
    it.shared ? h("span", { class: "row-badge", title: "Shared" }, "👥") : null,
    roleChip ? h("span", { class: "chip role-" + it.role }, roleLabel(it.role, it.rootKind)) : null,
    tags.slice(0, 3).map((t) => h("a", { class: "tag-chip", href: searchHash({ tag: [t] }), title: `Everything tagged ${t}`, onclick: (e) => e.stopPropagation() }, "#" + t)),
    tags.length > 3 ? h("span", { class: "tag-chip more" }, `+${tags.length - 3}`) : null];
}
function wireDnd(el, it, ctx) {
  if (!ctx.dnd || !matchMedia("(pointer: fine)").matches) return;
  if (ctx.dnd.canDrag && it.role && it.role !== "viewer") {
    el.draggable = true;
    el.addEventListener("dragstart", (e) => {
      const ids = ctx.select && ctx.select.ids.has(it.id) ? Array.from(ctx.select.ids) : [it.id];
      e.dataTransfer.setData("application/x-docs-ids", JSON.stringify(ids));
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
    });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
  }
  if (it.kind === "folder" && ctx.dnd.canDrop && Docs.dropTarget) Docs.dropTarget(el, { id: it.id, name: it.name, names: null });
}
function itemRow(it, ctx = {}) {
  const more = h("button", { class: "icon-btn row-more", type: "button", "aria-label": `More for ${it.name}`, title: "More",
    onclick: (e) => { e.stopPropagation(); itemMenu(e.currentTarget, it, ctx); } }, "⋯");
  const tick = ctx.select ? h("label", { class: "row-tick", title: "Select" }, h("input", { type: "checkbox", checked: ctx.select.ids.has(it.id),
    "aria-label": `Select ${it.name}`, onchange: (e) => ctx.select.toggle(it, e.target.checked) })) : null;
  const meta = itemMeta(it, ctx);
  if (ctx.grid) {
    const thumb = it.kind === "file" && it.preview && (it.size || 0) <= 3 * 1024 * 1024
      ? h("img", { class: "tile-thumb", src: `api/nodes/${encodeURIComponent(it.id)}/preview`, alt: "", loading: "lazy", onerror: (e) => e.target.replaceWith(h("span", { class: "tile-icon", "aria-hidden": "true" }, iconOf(it))) })
      : iconBox(it, "tile-icon");
    const tile = h("div", { class: "item-tile" + (ctx.select && ctx.select.ids.has(it.id) ? " selected" : ""), dataset: { id: it.id, kind: it.kind } },
      h("button", { class: "tile-main", type: "button", onclick: () => openItem(it), title: it.name }, thumb,
        h("span", { class: "tile-name" }, nameNode(it, ctx)), h("span", { class: "row-meta" }, meta.slice(0, 2).join(" · "))),
      h("div", { class: "tile-foot" }, tick, h("span", { class: "tile-badges" }, badges(it, ctx)), ctx.noMenu ? null : more));
    wireDnd(tile, it, ctx);
    return tile;
  }
  const main = h("button", { class: "row-main", type: "button", onclick: () => openItem(it), title: it.name },
    iconBox(it, "row-icon"),
    h("span", { class: "row-text" },
      h("span", { class: "row-name" }, nameNode(it, ctx), badges(it, ctx)),
      ctx.snippet ? ctx.snippet(it) : null,
      h("span", { class: "row-meta" }, meta.join(" · "))));
  const row = h("div", { class: "item-row" + (ctx.select && ctx.select.ids.has(it.id) ? " selected" : ""), dataset: { id: it.id, kind: it.kind } }, tick, main,
    ctx.rowExtra ? ctx.rowExtra(it) : null, ctx.noMenu ? null : more);
  wireDnd(row, it, ctx);
  return row;
}
function roleLabel(r, rootKind) {
  if (rootKind === "shared") return { viewer: "Read only", editor: "Read and write" }[r] || r;
  return { viewer: "Can view", editor: "Can edit", manager: "Manager", owner: "Owner" }[r] || r;
}
function itemList(items, ctx = {}, empty = "Nothing here yet.") {
  if (!items.length) return h("div", { class: "empty" }, empty);
  return h("div", { class: ctx.grid ? "item-grid" : "item-list" }, items.map((it) => itemRow(it, ctx)));
}
Object.assign(Docs, { itemRow, itemList, roleLabel, itemMeta });

// ---------- Home ----------
Docs.route("", async (page, _args, current) => {
  mount(page, pageHead("Home"), spinner());
  const [me, favs, mine] = await Promise.all([refreshMe(), api("api/space/favourites"),
    api("api/space/mine").catch(() => ({ items: [] }))]);
  if (!current()) return;
  const parts = [pageHead(`Hello, ${me.name}`, newButton())];
  if (me.disabled) { mount(page, pageHead("Household Docs"), h("div", { class: "card banner-card danger" }, me.disabledMessage)); return; }
  if (me.isAdmin && me.docsFolder && !me.docsFolder.confirmed) parts.push(Docs.firstRunCard ? Docs.firstRunCard() : null);
  if (Docs.pinsSection) parts.push(Docs.pinsSection());
  if (favs.items.length) parts.push(h("section", { class: "card" }, h("h3", null, "Favourites"), itemList(favs.items, { showOwner: true })));
  const sf = (me.sharedFolders || []).filter((x) => x.exists);
  if (sf.length) parts.push(h("section", { class: "card", id: "homeFolders" }, h("div", { class: "card-head" }, h("h3", null, "Shared folders"), h("a", { class: "link-btn", href: "#/folders" }, "All")),
    h("div", { class: "chip-links" }, sf.map((x) => h("a", { class: "folder-chip", href: folderHash("root:" + x.rootId) }, x.mode === "rw" ? "📂 " : "🗂️ ", x.label,
      h("span", { class: "hint" }, x.mode === "rw" ? " · read and write" : " · read only"))))));
  parts.push(h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "My docs"), h("a", { class: "link-btn", href: "#/mine" }, "Open")),
    itemList(sortItems(mine.items), {}, "Nothing here yet — use ➕ New to make a note or a checklist.")));
  parts.push(h("p", { class: "hint footnote" }, "Docs are plain files in Home Assistant's /share folder — keep passwords and card numbers in Household Vault."));
  mount(page, parts);
});
function newButton() {
  return h("button", { class: "btn-primary new-inline", type: "button", onclick: (e) => openNewMenu(e.currentTarget) }, "➕ New");
}
Docs.newButton = newButton;

function sortItems(items, sort) {
  const by = sort || (Docs.state.me && Docs.state.me.prefs.sort) || "name";
  const ts = (x) => (x.modified ? new Date(x.modified).getTime() : 0);
  return items.slice().sort((a, b) => {
    if ((a.kind === "folder") !== (b.kind === "folder")) return a.kind === "folder" ? -1 : 1;
    if (by === "modified") return ts(b) - ts(a);
    if (by === "size") return (b.size || 0) - (a.size || 0);
    return a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true });
  });
}
Docs.sortItems = sortItems;

// ---------- folders (My docs, any folder, an admin shared folder's top) ----------
function viewMode() { return lsGet("docs.view") === "grid" ? "grid" : "list"; }
async function renderFolder(page, ref, current, selectId) {
  mount(page, pageHead(ref ? "Folder" : "My docs"), spinner());
  const sort = (Docs.state.me && Docs.state.me.prefs.sort) || "name";
  let data;
  try { data = await api("api/list?" + (ref ? `node=${encodeURIComponent(ref)}&` : "") + `sort=${sort}`); }
  catch (e) {       // a link to a folder you can't open (§17.15): Docs' own page, naming nothing
    if (e.status === 404 && ref && !ref.startsWith("root:") && Docs.noAccessPage && current()) { Docs.noAccessPage(page, "folder"); return; }
    throw e;
  }
  if (!current()) return;
  const f = data.folder;
  const label = f ? f.name : "My docs";
  Docs.state.currentFolder = { id: data.ref, name: label, canEdit: data.canEdit, names: data.items.map((i) => i.name), rootKind: data.rootKind };
  Docs.state.currentSpace = data.rootKind === "shared" ? "folders" : (data.crumbs[0] && ["shared", "kids"].includes(data.crumbs[0].space) ? data.crumbs[0].space : "mine");
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Folder path" }, data.crumbs.map((c, i) => {
    if (i === data.crumbs.length - 1) return [i ? h("span", { class: "crumb-sep" }, "›") : null, h("span", { class: "crumb current" }, c.name)];
    const a = h("a", { class: "crumb", href: c.id ? folderHash(c.id) : "#/" + (c.space || "mine") }, c.name);
    if (data.canEdit && Docs.dropTarget && (c.id || (c.space === "mine" && data.role === "owner"))) Docs.dropTarget(a, { id: c.id || null, name: c.name, names: null });
    return [i ? h("span", { class: "crumb-sep" }, "›") : null, a];
  }));
  const sortSel = h("select", { "aria-label": "Sort by", id: "sortSel", value: sort },
    h("option", { value: "name" }, "Name"), h("option", { value: "modified" }, "Modified"), h("option", { value: "size" }, "Size"));
  sortSel.addEventListener("change", async () => {
    try { const r = await api("api/me/settings", { method: "PUT", body: { sort: sortSel.value } }); Docs.state.me.prefs = r.prefs; render(); } catch (e) { fail(e); }
  });
  const grid = viewMode() === "grid";
  const viewBtn = h("button", { class: "icon-btn view-toggle", type: "button", id: "viewToggle", title: grid ? "Show as a list" : "Show as a grid",
    "aria-label": grid ? "Show as a list" : "Show as a grid", onclick: () => { lsSet("docs.view", grid ? "list" : "grid"); render(); } }, grid ? "☰" : "▦");
  const right = [];
  if (data.canEdit) {
    right.push(h("button", { class: "btn-secondary upload-btn", type: "button", id: "uploadBtn", onclick: () => Docs.pickUpload && Docs.pickUpload(Docs.state.currentFolder) }, "⬆ Upload"));
    right.push(newButton());
  }
  if (data.items.some((x) => x.changed)) {         // §17.21: changed since you last looked
    right.push(h("button", { class: "btn-ghost btn-small", type: "button", id: "markSeen", title: "Clear the dots: you've seen everything here", onclick: async () => {
      try { const r = await api(`api/nodes/${encodeURIComponent(data.ref || "mine")}/seen`, { method: "POST" }); toast(`Marked ${r.items} item${r.items === 1 ? "" : "s"} as seen`); render(); } catch (e) { fail(e); }
    } }, "✓ Mark all as seen"));
  }
  if (f && f.canShare) right.push(h("button", { class: "btn-secondary", type: "button", onclick: () => Docs.shareDialog(f) }, "Share"));
  if (f && f.isRoot) right.push(h("button", { class: "icon-btn head-more", type: "button", "aria-label": "More for this folder", onclick: (e) => menu(e.currentTarget, [
    { id: "zip", icon: "⬇", label: "Download (.zip)", run: () => download({ id: f.id, kind: "folder", name: f.name }) },
    { id: "search-here", icon: "🔎", label: "Search in this folder", run: () => go(searchHash({ scope: [f.id] })) }]
    .concat((f.kidsRoot ? [] : Docs.rootActions).filter((a) => !a.show || a.show(f)).map((a) => ({ id: a.id, icon: a.icon, label: typeof a.label === "function" ? a.label(f) : a.label, run: () => a.run(f) })))) }, "⋯"));
  else if (f) right.push(h("button", { class: "icon-btn head-more", type: "button", "aria-label": "More for this folder", onclick: (e) => itemMenu(e.currentTarget, f, { inFolderHead: true }) }, "⋯"));
  const role = data.mode ? h("span", { class: "chip mode-chip role-" + data.role }, data.mode === "rw" ? "✏️ Read and write" : "🔒 Read only")
    : (data.role !== "owner" ? h("span", { class: "chip role-" + data.role }, roleLabel(data.role)) : null);
  const items = sortItems(data.items, sort);
  const sel = Docs.makeSelection ? Docs.makeSelection(items, () => drawList()) : null;
  const listBox = h("div", { class: "list-box" });
  const bar = h("div", { class: "select-bar-slot" });
  const ctx = { inFolder: true, grid, select: sel, folderRole: data.role, dnd: { canDrag: data.canEdit, canDrop: data.canEdit } };
  function drawList() {
    mount(listBox, itemList(items, ctx, data.canEdit ? "This folder is empty — use ➕ New or ⬆ Upload, drop files here, or add them over Samba or the File editor." : "This folder is empty."));
    if (sel && Docs.selectionBar) mount(bar, Docs.selectionBar(sel, { folderRef: data.ref, rootKind: data.rootKind }));
  }
  drawList();
  const card = h("div", { class: "card list-card" + (grid ? " grid-card" : "") }, bar, listBox);
  if (data.canEdit && Docs.dropZone) Docs.dropZone(card, Docs.state.currentFolder);
  // §17.20: a child is told who may view what they make in My docs (an admin set it)
  const ks = !ref && Docs.state.me && Docs.state.me.kidsStatus;
  const kidsNote = ks ? h("p", { class: "hint kids-note", id: "kidsNote" }, ks.parents.length
    ? `You're in Kids' space. ${ks.parents.join(", ")} can view what you make here from ${fmtFull(ks.since)} on (not what you had before).`
    : "You're in Kids' space. Nobody else can view your My docs.") : null;
  mount(page, h("div", { class: "page-head folder-head" }, h("div", { class: "folder-title" }, crumbs, role),
    h("div", { class: "head-actions" }, sortSel, viewBtn, right)), kidsNote, f ? (Docs.headExtras || []).map((fn) => fn(f, { folder: true })) : null, card);
  if (selectId) {
    const el = page.querySelector(`[data-id="${CSS.escape(selectId)}"]`);
    if (el) { el.classList.add("flash"); el.scrollIntoView({ block: "center" }); setTimeout(() => el.classList.remove("flash"), 2400); }
  }
}
Docs.route("mine", (page, _a, cur) => renderFolder(page, null, cur));
Docs.route("folder", (page, args, cur) => renderFolder(page, args[0] === "mine" ? null : args[0], cur, args[1] === "sel" ? args[2] : null));

// ---------- spaces ----------
async function renderSpace(page, space, title, empty, current, opts = {}) {
  mount(page, pageHead(title), spinner());
  const showHidden = !!opts.showHidden;
  const data = await api(`api/space/${space}` + (showHidden ? "?hidden=1" : ""));
  if (!current()) return;
  const right = [];
  if (opts.hideable) {
    right.push(h("label", { class: "mini-toggle" }, h("input", { type: "checkbox", checked: showHidden, onchange: (e) => renderSpace(page, space, title, empty, current, { ...opts, showHidden: e.target.checked }) }), "Show hidden"));
  }
  const ctx = { showOwner: true, space, extraMeta: (it) => [it.hidden ? "hidden" : null, it.sharedByName && it.sharedByName !== it.ownerName ? `shared by ${it.sharedByName}` : null].filter(Boolean) };
  const items = opts.sort === false ? data.items : sortItems(data.items);
  mount(page, pageHead(title, right), h("div", { class: "card list-card" }, itemList(items, ctx, empty)));
}
Docs.route("shared", (page, _a, cur) => renderSpace(page, "shared", "Shared with me", "Nothing is shared with you yet.", cur, { hideable: true }));
Docs.route("everyone", (page, _a, cur) => renderSpace(page, "everyone", "Everyone", "Nothing is shared with Everyone yet.", cur, { hideable: true }));
Docs.route("favourites", (page, _a, cur) => renderSpace(page, "favourites", "Favourites", "Tap ⋯ → Favourite on anything to keep it here.", cur));
Docs.route("recent", (page, _a, cur) => renderSpace(page, "recent", "Recent", "What you open shows up here.", cur, { sort: false }));

Docs.route("kids", (page) => {
  const list = (Docs.state.me && Docs.state.me.kidsView) || [];
  mount(page, pageHead("Kids' docs"), h("p", { class: "hint" }, "An admin let you view these children's My docs (you can open, search and download, not change)."),
    h("div", { class: "card list-card" }, list.length ? h("div", { class: "item-list" }, list.map((k) => h("a", { class: "item-row more-row", href: folderHash("root:" + k.rootId), dataset: { kid: k.childId } },
      h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, "🧒"), h("span", { class: "row-text" }, h("span", { class: "row-name" }, `${k.name}'s docs`))))))
      : h("div", { class: "empty" }, "No children's docs are open to you.")));
});
// a file from an "Open in Docs" link (§17.15): its folder, with the file panel open
Docs.route("file", async (page, args, current) => {
  mount(page, spinner());
  let it;
  try { it = await api(`api/nodes/${encodeURIComponent(args[0] || "")}`); }
  catch (e) { if (e.status === 404 && Docs.noAccessPage) { Docs.noAccessPage(page, "file"); return; } throw e; }
  if (!current()) return;
  const where = it.parentRef ? "#/folder/" + encodeURIComponent(it.parentRef) : (it.ownerId === Docs.state.me.id ? "#/folder/mine" : null);
  history.replaceState(history.state, "", where ? where + "/sel/" + encodeURIComponent(it.id) : "#/shared");
  await render();
  if (Docs.filePanel) Docs.filePanel(it);
});

// A view link (§6.6): Can view for whoever opens it, then the item itself
Docs.route("view", async (page, args, current) => {
  mount(page, spinner());
  let it;
  try { it = await api(`api/view-links/${encodeURIComponent(args[0] || "")}`); }
  catch (e) {
    if (e.status !== 404) throw e;
    mount(page, pageHead("Link"), h("div", { class: "card banner-card", id: "viewLinkGone" }, e.message), h("p", null, h("a", { href: "#/" }, "Home")));
    return;
  }
  if (!current()) return;
  const place = it.kind === "folder" ? "folder" : it.kind === "file" ? "file" : "doc";
  history.replaceState(history.state, "", `#/${place}/${encodeURIComponent(it.id)}`);
  await render();
});

Docs.route("trash", async (page, _a, current) => {
  mount(page, pageHead("Trash"), spinner());
  const data = await api("api/space/trash");
  if (!current()) return;
  const empty = h("button", { class: "btn-danger", type: "button", disabled: !data.items.length, onclick: async () => {
    if (!(await confirmDialog("Empty Trash", `Delete ${data.items.length} item${data.items.length === 1 ? "" : "s"} for good? This can't be undone.`, "Empty Trash", true))) return;
    try { await api("api/trash/empty", { method: "POST" }); toast("Trash emptied"); render(); } catch (e) { fail(e); }
  } }, "Empty Trash");
  const trashRow = (t, top) => h("div", { class: "item-row", dataset: { trash: t.id } },
    h("div", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, kindOf(t).icon),
      h("span", { class: "row-text" }, h("span", { class: "row-name" }, t.name),
        h("span", { class: "row-meta" }, [`was in ${t.originalPath.includes("/") ? t.originalPath.slice(0, t.originalPath.lastIndexOf("/")) : top}`,
          `deleted ${fmtWhen(t.deletedAt)}`, t.deletedByName ? `by ${t.deletedByName}` : null].filter(Boolean).join(" · ")))),
    h("button", { class: "btn-secondary btn-small", type: "button", onclick: async () => {
      try { await api(`api/trash/${encodeURIComponent(t.id)}/restore`, { method: "POST" }); toast(`${t.name} restored`); render(); } catch (e) { fail(e); }
    } }, "Restore"));
  const rows = data.items.map((t) => trashRow(t, "My docs"));
  const folders = (data.folders || []).map((f) => h("section", { class: "card list-card", dataset: { trashFolder: f.rootId } },
    h("h3", { class: "list-card-title" }, `🗂️ ${f.label}`), h("div", { class: "item-list" }, f.items.map((t) => trashRow(t, f.label)))));
  mount(page, pageHead("Trash", empty), h("p", { class: "hint" }, "Things deleted from your folder — by you or by people you share with — stay here until the Trash is emptied (automatically after an admin-set number of days)."),
    h("div", { class: "card list-card" }, rows.length ? h("div", { class: "item-list" }, rows) : h("div", { class: "empty" }, "Trash is empty.")),
    folders.length ? h("p", { class: "hint" }, "Deleted from shared folders you can change (kept in each folder's own hidden .trash):") : null, folders);
});

// ---------- search ----------
function highlight(text) {
  // the server marks matches with ⁅ ⁆; the snippet stays plain text (never parsed as HTML)
  return DocsText.splitMarks(text).map(([marked, part]) => (marked ? h("mark", null, part) : part));
}
Docs.highlight = highlight;
// ---------- More (phones) ----------
Docs.route("more", (page) => {
  const items = Docs.spaces.filter((s) => !["home", "mine", "shared"].includes(s.id) && (!s.show || s.show()));
  mount(page, pageHead("More"),
    h("div", { class: "card list-card" }, h("div", { class: "item-list" }, items.map((s) => h("a", { class: "item-row more-row", href: s.hash, dataset: { space: s.id } },
      h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, s.icon), h("span", { class: "row-text" }, h("span", { class: "row-name" }, s.label))))))),
    h("div", { class: "card" }, h("h3", null, "Appearance"), h("label", { class: "field" }, "Theme", HouseholdTheme.bindSelect(h("select", { "aria-label": "Theme" })))));
});

// ---------- Settings (everyone) ----------
function toggleSwitch(checked, onChange, label) {
  const input = h("input", { type: "checkbox", role: "switch", checked: !!checked, "aria-label": label });
  input.addEventListener("change", () => onChange(input.checked, input));
  return h("label", { class: "sp-switch", title: label }, input, h("span", { class: "sp-track" }, h("span", { class: "sp-thumb" })));
}
Docs.toggleSwitch = toggleSwitch;
Docs.route("settings", async (page, args, current) => {
  mount(page, pageHead("Settings"), spinner());
  const [me, who] = await Promise.all([refreshMe(), api("api/whoami")]);
  if (!current()) return;
  const save = async (change) => {
    try { const r = await api("api/me/settings", { method: "PUT", body: change }); Docs.state.me.prefs = r.prefs; toast("Saved"); } catch (e) { fail(e); }
  };
  const row = (label, sub, control) => h("div", { class: "setting-row" }, h("div", null, h("div", null, label), sub ? h("div", { class: "sub" }, sub) : null), control);
  const sort = h("select", { "aria-label": "Sort folders by", value: me.prefs.sort },
    h("option", { value: "name" }, "Name"), h("option", { value: "modified" }, "Last modified"), h("option", { value: "size" }, "Size"));
  sort.addEventListener("change", () => save({ sort: sort.value }));
  const newSheets = h("select", { "aria-label": "New sheets as", id: "prefNewSheets", value: me.prefs.newSheets || "xlsx" },
    h("option", { value: "xlsx" }, ".xlsx (Excel)"), h("option", { value: "csv" }, ".csv"));
  newSheets.addEventListener("change", () => save({ newSheets: newSheets.value }));
  const numberStyle = h("select", { "aria-label": "Number style for sheets", id: "prefNumberStyle", value: me.prefs.numberStyle || "auto" },
    h("option", { value: "auto" }, "This device's own"), h("option", { value: "en" }, "1,234.56"), h("option", { value: "de" }, "1.234,56"),
    h("option", { value: "in" }, "12,34,567.89"));
  numberStyle.addEventListener("change", () => save({ numberStyle: numberStyle.value }));
  const defaultNote = h("select", { "aria-label": "New notes are", id: "prefDefaultNote", value: me.prefs.defaultNote || "note" },
    h("option", { value: "note" }, "Plain text (.txt)"), h("option", { value: "markdown" }, "Markdown (.md)"));
  defaultNote.addEventListener("change", () => save({ defaultNote: defaultNote.value }));
  const cards = [
    me.disabled ? null : h("div", { class: "card", id: "prefsCard" }, h("h3", null, "You"),
      row("Tell me when something is shared with me", me.notifyLinked ? "A notification on your phone (from Home Assistant)." :
        "No phone is linked to you in Home Assistant yet (Settings → People → you → Track device).",
      toggleSwitch(me.prefs.notifyShares, (v) => save({ notifyShares: v }), "Tell me when something is shared with me")),
      row("Sort folders by", "Folders always come first.", sort),
      row("New sheets as", "An .xlsx keeps formats, tabs and charts; a .csv holds values and formulas only (any app opens it).", newSheets),
      row("Number style for sheets", "How numbers look in sheets, and how the numbers you type are read (1.234,56 reads a decimal comma). Formulas keep commas between values.", numberStyle),
      row("New notes are", "➕ New → Note makes this kind; the other is in the menu too. Markdown adds headings, lists and tick boxes with a preview.", defaultNote),
      Docs.settingsRows.map((fn) => fn(me, save, row)),
      me.app.assistant ? row("Let the Household Assistant answer for me", "When you ask the Household Assistant app, it can find and read the documents you can open here, and make a note in your Inbox when you tap to confirm.",
        toggleSwitch(me.prefs.assistantOk !== false, (v) => save({ assistantOk: v }), "Let the Household Assistant answer for me")) : null,
      row("Theme", "The same choice in every household app on this device.", HouseholdTheme.bindSelect(h("select", { "aria-label": "Theme" })))),
    h("div", { class: "card", id: "whoamiCard" }, h("h3", null, "How the app sees you"),
      HouseholdWhoami.panel(who, { appName: "Household Docs", adviceTag: "div", adviceStyle: "margin-top:8px", onCopy: (t) => copyText(t) })),
    h("div", { class: "card" }, h("h3", null, "Where your documents are"),
      h("p", { class: "hint" }, "Every document is an ordinary file in Home Assistant's /share folder, in a folder of your own. Anyone who can reach /share — over Samba, the File editor, another app, or a Home Assistant backup that includes Share — can read and change them there; the sharing you set here only applies inside the app."),
      me.folder ? h("p", null, "Your folder: ", h("strong", null, me.folder)) : null),
  ];
  mount(page, pageHead("Settings"), cards);
  if (args[0] === "whoami") setTimeout(() => { const c = $("#whoamiCard"); if (c) c.scrollIntoView({ block: "start" }); }, 50);
});

// ---------- the chrome ----------
function wireChrome() {
  const collapse = $("#sidebarCollapseBtn");
  const syncCollapse = () => {
    const c = document.documentElement.getAttribute("data-sidebar") === "collapsed";
    collapse.textContent = c ? "›" : "‹";
    collapse.title = c ? "Expand sidebar" : "Collapse sidebar";
    collapse.setAttribute("aria-label", collapse.title);
  };
  collapse.addEventListener("click", () => { HouseholdTheme.setSidebarCollapsed(!HouseholdTheme.sidebarCollapsed()); syncCollapse(); });
  syncCollapse();
  HouseholdTheme.bindSelect($("#theme-select"));
  $("#newBtn").addEventListener("click", (e) => openNewMenu(e.currentTarget));
  $("#quickNoteBtn").addEventListener("click", () => { if (Docs.quickNote) Docs.quickNote(); });
  const input = $("#searchInput");
  $("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); if (Docs.searchFromHeader) Docs.searchFromHeader(input.value.trim(), true); });
  input.addEventListener("input", debounce(() => { if (input.value.trim().length >= 2 && Docs.searchFromHeader) Docs.searchFromHeader(input.value.trim(), false); }, 250));
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable]");
    if (typing || UI.dialogs().length) return;
    if (e.key === "/" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")) { e.preventDefault(); input.focus(); input.select(); }
    else if ((e.key === "n" || e.key === "N") && !e.ctrlKey && !e.metaKey && !e.altKey && Docs.quickNote && Docs.state.route !== "doc") { e.preventDefault(); Docs.quickNote(); }
  });
  window.addEventListener("hashchange", () => { if (Docs.state.me && !Docs.state.me.disabled) render(); });
}

// ---------- "Open in Docs" (APP_MESSAGES_SPEC §6.5) ----------
function parentPath() {
  try { return window.parent !== window ? window.parent.location.pathname : null; } catch (e) { return null; }
}
function forgetParentPath(me) {
  // replace Home Assistant's address with the bare page path, so a reload doesn't open the item again
  const panel = me.apps && me.apps.panel;
  const pp = parentPath();
  if (!panel || !pp || pp === panel || !pp.startsWith(panel + "/")) return;
  try { window.parent.history.replaceState(window.parent.history.state, "", panel); } catch (e) { /* not reachable */ }
}
function listenForHaRoute(me, handled) {
  // current Home Assistant versions hand the rest of the path to the app in "home-assistant/properties"
  if (window.parent === window) return;
  let done = handled;
  const until = Date.now() + 5000;
  window.addEventListener("message", (e) => {
    if (done || e.origin !== location.origin || e.source !== window.parent || Date.now() > until) return;
    const d = e.data;
    if (!d || d.type !== "home-assistant/properties" || !d.route || typeof d.route.path !== "string") return;
    const link = DocsText.appLinkHash({ panel: me.apps && me.apps.panel, routePath: d.route.path });
    if (!link) return;
    done = true;
    forgetParentPath(me);
    if (location.hash !== link) location.hash = link;
  });
  try { window.parent.postMessage({ type: "home-assistant/subscribe-properties" }, location.origin); } catch (e) { /* older frames */ }
}

async function start() {
  wireChrome();
  try { await refreshMe(); }
  catch (e) {
    const box = $("#fatal");
    box.hidden = false;
    mount(box, h("h3", null, "Can't open Household Docs"), h("div", null, e.message));
    return;
  }
  const me = Docs.state.me;
  const chip = $("#sidebarUser");
  chip.textContent = me.name + (me.isAdmin ? " · admin" : "");
  chip.title = "How the app sees you";
  chip.addEventListener("click", () => go("#/settings/whoami"));
  if (me.disabled) {
    document.body.classList.add("turned-off");          // nothing to search, make or open
    $("#newBtn").hidden = true;
    mount($("#page"), pageHead("Household Docs"), h("div", { class: "card banner-card danger", id: "turnedOff" }, me.disabledMessage),
      h("p", { class: "hint" }, h("a", { href: "#/settings/whoami" }, "How the app sees you")));
    window.addEventListener("hashchange", () => { if (parseHash().name === "settings") render(); });
    drawNav();
    return;
  }
  // ⚡ Quick note from a dashboard button or a phone shortcut (…/quick-note or #quick-note, §17.13), and "Open in
  // Docs" links from Household Chat (/<panel>/doc/<id>, APP_MESSAGES_SPEC §6.5)
  const link = DocsText.appLinkHash({ panel: me.apps && me.apps.panel, parentPath: parentPath(), ownPath: location.pathname });
  if (link && !location.hash) { history.replaceState(history.state, "", link); forgetParentPath(me); }
  listenForHaRoute(me, !!link);
  await render();
  if (window.BackNav) {
    BackNav.init({
      atHome: () => Docs.isHome(),
      goHome: () => go("#/"),
      openLayers: () => UI.dialogs().concat(openMenuEl ? ["menu"] : []),
      closeLayer: (layer) => { if (layer === "menu") closeMenu(); else layer.close(); },
    });
  }
}
Docs.start = start;
