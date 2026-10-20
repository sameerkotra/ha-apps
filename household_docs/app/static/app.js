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
  settingsRows: [],     // fn(me, save, row) → nodes for a Settings card; fn.group: "notify", "new" or "display" (the default)
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
function spinner(text) { return UI.loading(null, text); }
function errorCard(e, retry) { return UI.errorBox(e, retry); }
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
// Unsaved text kept on this device when a save fails or the page goes away first (review 2026-10 DFI1, DFI2);
// offered again the next time the document is opened here. Never throws (private mode, a full storage).
Docs.drafts = {
  MAX: 2000000,
  get(id) { try { const d = JSON.parse(localStorage.getItem("docs.draft." + id) || "null"); return d && typeof d.text === "string" ? d : null; } catch (e) { return null; } },
  set(id, text, etag) { if (text.length > this.MAX) return; try { localStorage.setItem("docs.draft." + id, JSON.stringify({ text, etag, at: Date.now() })); } catch (e) { /* no room */ } },
  drop(id) { try { localStorage.removeItem("docs.draft." + id); } catch (e) { /* ignore */ } },
};
function go(hash) { if (window.BackNav) BackNav.go(hash); else location.hash = hash; }
// a filter, sort, search or in-page tab change: the address changes, Back doesn't step through it
function replaceHash(hash) { if (window.BackNav) BackNav.replace(hash); else location.hash = hash; }
// one tab strip for every page with sub-pages (Recent & activity, Admin): an underline strip, aria-current on the
// tab that's showing, and switching tabs replaces the address (Back leaves the page instead of retracing tabs)
function tabStrip(items, active, label, dataKey = "tab") {
  return h("nav", { class: "ui-tabs", "aria-label": label || "Sections" }, items.map(([id, text, hash]) => h("a", {
    class: "ui-tab" + (id === active ? " active" : ""), href: hash, "aria-current": id === active ? "page" : null, dataset: { [dataKey]: id },
    onclick: (e) => { if (e.button || e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); replaceHash(hash); } }, text)));
}
const isPhone = () => matchMedia("(max-width: 760px)").matches;
function folderHash(ref) { return ref ? "#/folder/" + encodeURIComponent(ref) : "#/mine"; }
function searchHash(params) { return "#/search/" + encodeURIComponent(DocsText.searchArg(params)); }
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Couldn't copy", true));
}
const isChild = () => !!(Docs.state.me && Docs.state.me.isChild);      // Kids' space (§17.20): a simpler app
Object.assign(Docs, { toast, fail, openModal, confirmDialog, spinner, errorCard, pageHead, fmtSize, fmtWhen, fmtFull, titleOf, go,
  replace: replaceHash, tabStrip, isPhone, folderHash, searchHash, isChild });

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
// may this person do `role`'s things with the item? (one place for every page's buttons — review 2026-10 DFI9)
const ROLE_RANK = { viewer: 1, editor: 2, manager: 3, owner: 4 };
function can(it, role) { return (ROLE_RANK[it && it.role] || 0) >= (ROLE_RANK[role] || 9); }
Object.assign(Docs, { kindOf, openItem, download, iconOf, iconBox, IMAGE_EXTS, can });

// ---------- ⋯ menus ----------
let openMenuEl = null;
let menuOpener = null;            // where the focus goes back to when a menu closes with Escape or a choice
function closeMenu(refocus) {
  if (!openMenuEl) return;
  openMenuEl.remove(); openMenuEl = null;
  if (refocus && menuOpener && document.body.contains(menuOpener)) menuOpener.focus();
  menuOpener = null;
}
function menu(anchor, entries) {
  closeMenu();
  const list = entries.filter(Boolean);
  if (!list.length) return null;
  // entries: {id, icon, label, run, danger}, "-" (a line), {heading} (a small group title) or {id, icon, label, sub: [entries]}
  // (a submenu: it replaces this menu at the same place, with ‹ Back to return)
  const m = h("div", { class: "menu", role: "menu" }, list.map((e) => {
    if (e === "-") return h("hr");
    if (e.heading) return h("div", { class: "menu-head", role: "presentation" }, e.heading);
    const sub = Array.isArray(e.sub);
    return h("button", { type: "button", role: "menuitem", class: (e.danger ? "danger" : "") + (sub ? " has-sub" : ""), dataset: { action: e.id || "" },
      "aria-haspopup": sub ? "menu" : null,
      onclick: () => {
        if (sub) { menu(anchor, [{ id: "menu-back", icon: "‹", label: e.label, run: () => menu(anchor, entries) }, "-"].concat(e.sub)); return; }
        closeMenu(true); e.run();
      } }, e.icon ? h("span", { class: "menu-icon", "aria-hidden": "true" }, e.icon) : null, h("span", { class: "menu-label" }, e.label),
      sub ? h("span", { class: "menu-go", "aria-hidden": "true" }, "›") : null);
  }));
  // a menu as menus go: ↑ ↓ Home End move between its items (review 2026-10 DFI7)
  m.addEventListener("keydown", (ev) => {
    const items = Array.from(m.querySelectorAll('[role="menuitem"]'));
    const at = items.indexOf(document.activeElement);
    const go = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: items.length - 1 }[ev.key];
    if (go === undefined || !items.length) return;
    ev.preventDefault();
    items[(go + items.length) % items.length].focus();
  });
  menuOpener = anchor;
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
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openMenuEl) { closeMenu(true); e.stopPropagation(); } }, true);
Docs.menu = menu;
Docs.closeMenu = closeMenu;
Docs.openMenu = () => openMenuEl;

// The item ⋯ menu keeps the everyday actions at the top level; the rest sit in four submenus (an action can name its
// own with `group`). A group with a single entry shows that entry directly.
const ACTION_GROUPS = [
  { id: "organise", icon: "🏷", label: "Organise", ids: ["favourite", "pin", "follow", "tags"] },
  { id: "export", icon: "⬇", label: "Export", ids: ["download", "export-xlsx", "print", "pdf", "pdf-here"] },
  { id: "ai", icon: "✨", label: "AI", prefix: "ai-" },
  { id: "advanced", icon: "⚙️", label: "Advanced", ids: ["rules", "ha-sensor", "where", "save-template", "convert-note", "transfer"] },
];
function groupOf(a) {
  if (a.group) return ACTION_GROUPS.find((g) => g.id === a.group) || null;
  return ACTION_GROUPS.find((g) => (g.ids && g.ids.includes(a.id)) || (g.prefix && String(a.id).startsWith(g.prefix))) || null;
}
Docs.actionGroups = ACTION_GROUPS;
function itemMenu(anchor, item, ctx = {}) {
  const c = Object.assign({}, ctx, { anchor });
  const extra = ctx.inEditor ? (Docs.state.editorExtras || []) : [];
  const hide = ctx.inEditor ? (Docs.state.editorHide || []) : [];
  const actions = Docs.actions.concat(extra).filter((a) => !hide.includes(a.id)).sort((x, y) => (x.order || 0) - (y.order || 0));
  const entries = [];
  const groups = new Map();
  for (const a of actions) {
    if (a.show && !a.show(item, c)) continue;
    if (a.sep) { entries.push("-"); continue; }
    const e = { id: a.id, label: typeof a.label === "function" ? a.label(item, c) : a.label, icon: a.icon, danger: a.danger, run: () => a.run(item, c) };
    const g = groupOf(a);
    if (!g) { entries.push(e); continue; }
    if (!groups.has(g.id)) { const holder = { id: "group-" + g.id, icon: g.icon, label: g.label, sub: [] }; groups.set(g.id, holder); entries.push(holder); }
    groups.get(g.id).sub.push(e);
  }
  const flat = [];
  for (const e of entries) {
    if (e !== "-" && e.sub && e.sub.length === 1) flat.push(e.sub[0]);
    else if (e === "-" && (!flat.length || flat[flat.length - 1] === "-")) continue;
    else flat.push(e);
  }
  while (flat.length && flat[flat.length - 1] === "-") flat.pop();
  return menu(anchor, (ctx.prepend || []).concat(ctx.prepend && ctx.prepend.length && flat.length ? ["-"] : [], flat));
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
// ➕ New in three groups: Create, From…, Bring in (an entry can name its own `group`)
const NEW_GROUPS = [
  { id: "create", heading: "Create", ids: ["note", "note-other", "checklist", "sheet", "folder"] },
  { id: "from", heading: "From…", ids: ["template", "ai-checklist", "ai-sheet"] },
  { id: "bring", heading: "Bring in", ids: ["upload", "scan", "import-keep", "import-zip", "import-sheet"] },
];
function openNewMenu(anchor) {
  const ctx = newContext();
  if (!ctx.canEdit) { toast("You can only view this folder — new items go into My docs.", false); ctx.folderId = null; ctx.label = "My docs"; ctx.names = null; }
  const shown = Docs.newItems.filter((n) => !n.show || n.show(ctx));
  const groupIdx = (n) => { const i = NEW_GROUPS.findIndex((g) => g.id === n.group || g.ids.includes(n.id)); return i < 0 ? NEW_GROUPS.length : i; };
  const entries = [];
  let last = null;
  shown.map((n, i) => [groupIdx(n), i, n]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).forEach(([g, _i, n]) => {
    if (g !== last) {
      if (entries.length) entries.push("-");
      if (NEW_GROUPS[g]) entries.push({ heading: NEW_GROUPS[g].heading });
      last = g;
    }
    entries.push({ id: "new-" + n.id, icon: n.icon, label: typeof n.label === "function" ? n.label(ctx) : n.label, run: () => n.run(ctx) });
  });
  menu(anchor, entries);
}
Docs.openNewMenu = openNewMenu;
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
Docs.addSpace({ id: "trash", icon: "🗑", label: "Trash", order: 58, hash: "#/trash" });
Docs.addSpace({ id: "settings", icon: "⚙️", label: "Settings", order: 90, hash: "#/settings", sep: true });
Docs.addSpace({ id: "admin", icon: "🛡️", label: "Admin", order: 95, hash: "#/admin", show: () => Docs.state.me && Docs.state.me.isAdmin });

function activeSpace() {
  const r = Docs.state.route;
  if (r === "folder") return Docs.state.currentSpace || "mine";
  if (r === "doc") return Docs.state.currentSpace || null;
  if (["recent", "storage"].includes(r)) return "activity";     // tabs of the Recent & activity page
  return r === "" ? "home" : r;
}
const NAV_CHILDREN = 5;           // sub-links under a sidebar entry (tags, saved searches); the rest behind "More…"
function drawNav() {
  const nav = $("#sideNav");
  const act = activeSpace();
  const sub = (c) => h("a", { class: "tab-btn sub-link" + (c.more ? " more-link" : ""), href: c.hash, title: c.label, dataset: { space: c.id } },
    h("span", { class: "nav-icon", "aria-hidden": "true" }, c.more ? "…" : "·"), h("span", { class: "nav-label" }, c.label));
  mount(nav, Docs.spaces.filter((s) => !s.show || s.show()).map((s) => {
    const kids = s.children ? s.children() : [];
    return [s.sep ? h("div", { class: "nav-sep" }) : null,
      h("a", { class: "tab-btn" + (act === s.id ? " active" : ""), href: s.hash, title: s.label, dataset: { space: s.id },
        "aria-current": act === s.id ? "page" : null },
      h("span", { class: "nav-icon", "aria-hidden": "true" }, s.icon), h("span", { class: "nav-label" }, s.label)),
      kids.slice(0, NAV_CHILDREN).map(sub),
      kids.length > NAV_CHILDREN ? sub({ id: s.id + "-more", label: `More… (${kids.length - NAV_CHILDREN})`, hash: s.hash, more: true }) : null];
  }));
  const bar = $("#bottomBar");
  const btn = (id, icon, label, onclick, active) => h("button", { type: "button", class: "bb-btn" + (active ? " active" : ""), dataset: { bb: id }, onclick, "aria-label": label,
    "aria-current": active ? "page" : null },
    h("span", { class: "bb-icon", "aria-hidden": "true" }, icon), h("span", { class: "bb-label" }, label));
  // everything the More page lists (the spaces other than Home, My docs and Shared with me) lights up More
  const moreIds = Docs.spaces.filter((s) => !["home", "mine", "shared"].includes(s.id)).map((s) => s.id).concat(["more"]);
  mount(bar,
    btn("docs", "📄", "Docs", () => go("#/"), act === "home" || act === "mine"),
    btn("search", "🔎", "Search", () => go("#/search"), act === "search"),
    btn("new", "➕", "New", (e) => openNewMenu(e.currentTarget), false),
    btn("shared", "👥", "Shared", () => go("#/shared"), act === "shared"),
    btn("more", "☰", "More", () => go("#/more"), moreIds.includes(act)));
}

// ---------- router ----------
function parseHash(hash) {
  const raw = String(hash === undefined ? location.hash || "" : hash).replace(/^#\/?/, "");
  const parts = raw.split("/").map((p) => { try { return decodeURIComponent(p); } catch (e) { return p; } });
  return { name: parts[0] || "", args: parts.slice(1).filter((x) => x !== "") };
}
let renderSeq = 0;
async function render() {
  const { name, args } = parseHash();
  const fn = Docs.routes[name] || Docs.routes[""];
  if (Docs.state.leaving) {
    // the open document saves before the page changes; when it can't (offline, a conflict, an error) the
    // person decides — nothing is dropped without asking (review 2026-10 DFI1)
    let left = true;
    try { left = (await Docs.state.leaving()) !== false; } catch (e) { left = false; }
    if (!left) {
      const leave = await confirmDialog("Leave without saving?", "Your latest changes couldn't be saved yet. If you leave, a copy stays on " +
        "this device and is offered again when you open the document here.", "Leave", true);
      if (!leave) {
        if (Docs.state.hash !== undefined && location.hash !== Docs.state.hash) history.replaceState(history.state, "", Docs.state.hash || "#/");
        return;
      }
      try { await Docs.state.leaving(true); } catch (e) { /* leaving anyway */ }
    }
  }
  Docs.state.leaving = null;
  Docs.state.hash = location.hash;
  Docs.state.route = Docs.routes[name] ? name : "";
  Docs.state.args = args;
  if (!["folder", "doc"].includes(Docs.state.route)) Docs.state.currentSpace = null;
  if (Docs.state.route !== "folder" && Docs.state.route !== "mine") Docs.state.currentFolder = null;
  Docs.state.docParent = null;          // where ← and Back go from a deep link (set by the page)
  Docs.state.folderParent = null;
  Docs.state.editorExtras = null;       // an editor's own entries in its ⋯ menu (a sheet's exports)
  Docs.state.editorHide = null;
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
  return [it.changed ? h("span", { class: "new-dot", title: "Changed since you last looked", "aria-label": "Changed since you last looked" })
    : it.newShare ? h("span", { class: "new-dot", title: "New: you haven't opened it yet", "aria-label": "New" }) : null,
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
  if (!items.length) return typeof empty === "string" ? UI.empty(null, empty) : empty;
  return h("div", { class: ctx.grid ? "item-grid" : "item-list" }, items.map((it) => itemRow(it, ctx)));
}
Object.assign(Docs, { itemRow, itemList, roleLabel, itemMeta });

// ---------- Home ----------
// Home: 📌 Pinned, Recent (5), new shares, Favourites (6), Shared folders, My docs (the 8 changed last)
const NEW_SHARE_DAYS = 30;
const tsOf = (iso) => { const t = iso ? new Date(iso).getTime() : 0; return isNaN(t) ? 0 : t; };
// shared with me and not opened yet (a "new" dot), from the last 30 days
function isNewShare(it) { return !!it.sharedAt && !it.openedAt && !it.hidden && Date.now() - tsOf(it.sharedAt) < NEW_SHARE_DAYS * 86400000; }
Docs.isNewShare = isNewShare;
Docs.route("", async (page, _args, current) => {
  mount(page, pageHead("Home"), spinner());
  const none = () => ({ items: [] });
  const [me, favs, mine, recent, shared] = await Promise.all([refreshMe(), api("api/space/favourites").catch(none),
    api("api/space/mine").catch(none), api("api/space/recent").catch(none), api("api/space/shared").catch(none)]);
  if (!current()) return;
  const parts = [pageHead(`Hello, ${me.name}`, newButton())];
  if (me.disabled) { mount(page, pageHead("Household Docs"), h("div", { class: "card banner-card danger" }, me.disabledMessage)); return; }
  const card = (id, title, link, linkLabel, body) => h("section", { class: "card home-card", id },
    h("div", { class: "card-head" }, typeof title === "string" ? h("h3", null, title) : title, link ? h("a", { class: "link-btn", href: link }, linkLabel) : null), body);
  if (me.isAdmin && me.docsFolder && !me.docsFolder.confirmed) parts.push(Docs.firstRunCard ? Docs.firstRunCard() : null);
  if (Docs.pinsSection) parts.push(Docs.pinsSection());
  if (recent.items.length) parts.push(card("homeRecent", "Recent", "#/recent", "All", itemList(recent.items.slice(0, 5), { showOwner: true })));
  const fresh = shared.items.filter(isNewShare).sort((a, b) => tsOf(b.sharedAt) - tsOf(a.sharedAt));
  if (fresh.length) parts.push(card("homeShared", h("h3", null, "Shared with me ", h("span", { class: "chip on" }, `${fresh.length} new`)), "#/shared", "All",
    itemList(fresh.slice(0, 5).map((it) => Object.assign({}, it, { newShare: true })), { showOwner: true, space: "shared", extraMeta: (it) => [it.sharedByName ? `from ${it.sharedByName}` : null].filter(Boolean) })));
  if (favs.items.length) parts.push(card("homeFavourites", "Favourites", "#/favourites", favs.items.length > 6 ? `All ${favs.items.length}` : "All",
    itemList(favs.items.slice(0, 6), { showOwner: true })));
  const sf = (me.sharedFolders || []).filter((x) => x.exists);
  if (sf.length) parts.push(card("homeFolders", "Shared folders", "#/folders", "All",
    h("div", { class: "chip-links" }, sf.map((x) => h("a", { class: "folder-chip", href: folderHash("root:" + x.rootId) }, x.mode === "rw" ? "📂 " : "🗂️ ", x.label,
      h("span", { class: "hint" }, x.mode === "rw" ? " · read and write" : " · read only"))))));
  const latest = mine.items.slice().sort((a, b) => tsOf(b.modified) - tsOf(a.modified)).slice(0, 8);
  parts.push(card("homeMine", "My docs", "#/mine", "Open", itemList(latest, {},
    UI.empty("Nothing here yet", "Make a note, a checklist or a sheet — or bring files in.", { label: "➕ New", onclick: (e) => openNewMenu(e.currentTarget) }))));
  parts.push(h("p", { class: "hint footnote" }, "Docs are plain files in Home Assistant's /share folder — keep passwords and card numbers in Household Vault."));
  mount(page, parts);
});
function newButton() {
  return h("button", { class: "btn-primary new-inline", type: "button", onclick: (e) => openNewMenu(e.currentTarget) }, "➕ New");
}
Docs.newButton = newButton;

// rev: the other way round (Z–A, oldest first, smallest first) — this device's choice (↑/↓ in a folder's head)
function sortItems(items, sort, rev) {
  const by = sort || (Docs.state.me && Docs.state.me.prefs.sort) || "name";
  const k = rev ? -1 : 1;
  const ts = (x) => (x.modified ? new Date(x.modified).getTime() : 0);
  return items.slice().sort((a, b) => {
    if ((a.kind === "folder") !== (b.kind === "folder")) return a.kind === "folder" ? -1 : 1;
    if (by === "modified") return k * (ts(b) - ts(a));
    if (by === "size") return k * ((b.size || 0) - (a.size || 0));
    return k * a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true });
  });
}
const sortReversed = () => lsGet("docs.sortRev") === "1";
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
  // Back from a deep link: the folder above (the crumb before the last), or the space a top folder is in
  const up = data.crumbs.length > 1 ? data.crumbs[data.crumbs.length - 2] : null;
  Docs.state.folderParent = up ? (up.id ? folderHash(up.id) : "#/" + (up.space || "mine"))
    : (data.rootKind === "shared" ? "#/folders" : (ref ? "#/" + (Docs.state.currentSpace === "kids" ? "kids" : "mine") : null));
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Folder path" }, data.crumbs.map((c, i) => {
    if (i === data.crumbs.length - 1) return [i ? h("span", { class: "crumb-sep" }, "›") : null, h("span", { class: "crumb current" }, c.name)];
    const a = h("a", { class: "crumb", href: c.id ? folderHash(c.id) : "#/" + (c.space || "mine") }, c.name);
    if (data.canEdit && Docs.dropTarget && (c.id || (c.space === "mine" && data.role === "owner"))) Docs.dropTarget(a, { id: c.id || null, name: c.name, names: null });
    return [i ? h("span", { class: "crumb-sep" }, "›") : null, a];
  }));
  const setSort = async (v) => {
    try { const r = await api("api/me/settings", { method: "PUT", body: { sort: v } }); Docs.state.me.prefs = r.prefs; render(); } catch (e) { fail(e); }
  };
  const sortSel = h("select", { "aria-label": "Sort by", id: "sortSel", value: sort, class: "desk-only" },
    h("option", { value: "name" }, "Name"), h("option", { value: "modified" }, "Modified"), h("option", { value: "size" }, "Size"));
  sortSel.addEventListener("change", () => setSort(sortSel.value));
  const rev = sortReversed();
  const flipSort = () => { lsSet("docs.sortRev", rev ? "0" : "1"); render(); };
  const dirLabel = { name: rev ? "Z to A — tap for A to Z" : "A to Z — tap for Z to A", modified: rev ? "Oldest first — tap for newest first" : "Newest first — tap for oldest first",
    size: rev ? "Smallest first — tap for largest first" : "Largest first — tap for smallest first" }[sort] || "Reverse the order";
  const dirBtn = h("button", { class: "icon-btn sort-dir desk-only", type: "button", id: "sortDir", title: dirLabel, "aria-label": dirLabel, "aria-pressed": rev ? "true" : "false",
    onclick: flipSort }, rev ? "↑" : "↓");
  const grid = viewMode() === "grid";
  const flipView = () => { lsSet("docs.view", grid ? "list" : "grid"); render(); };
  const viewBtn = h("button", { class: "icon-btn view-toggle desk-only", type: "button", id: "viewToggle", title: grid ? "Show as a list" : "Show as a grid",
    "aria-label": grid ? "Show as a list" : "Show as a grid", onclick: flipView }, grid ? "☰" : "▦");
  const changed = data.items.some((x) => x.changed);           // §17.21: changed since you last looked
  const markSeen = async () => {
    try { const r = await api(`api/nodes/${encodeURIComponent(data.ref || "mine")}/seen`, { method: "POST" }); toast(`Marked ${r.items} item${r.items === 1 ? "" : "s"} as seen`); render(); } catch (e) { fail(e); }
  };
  // on a phone, sort, view and "mark all as seen" move into the folder's ⋯ (the head stays one line)
  const viewEntries = () => (isPhone() ? [{ heading: "Sort by" },
    ...[["name", "Name"], ["modified", "Modified"], ["size", "Size"]].map(([v, l]) => ({ id: "sort-" + v, icon: v === sort ? "✓" : " ", label: l, run: () => setSort(v) })),
    { id: "sort-rev", icon: rev ? "↑" : "↓", label: rev ? "Back to the usual order" : "Reverse the order", run: flipSort },
    "-", { id: "view-mode", icon: grid ? "☰" : "▦", label: grid ? "Show as a list" : "Show as a grid", run: flipView },
    changed ? { id: "mark-seen", icon: "✓", label: "Mark all as seen", run: markSeen } : null].filter(Boolean) : []);
  const right = [];
  if (data.canEdit) {
    right.push(h("button", { class: "btn-secondary upload-btn", type: "button", id: "uploadBtn", onclick: () => Docs.pickUpload && Docs.pickUpload(Docs.state.currentFolder) }, "⬆ Upload"));
    right.push(newButton());
  }
  if (changed) {
    right.push(h("button", { class: "btn-ghost btn-small desk-only", type: "button", id: "markSeen", title: "Clear the dots: you've seen everything here", onclick: markSeen }, "✓ Mark all as seen"));
  }
  if (f && f.canShare) right.push(h("button", { class: "btn-secondary", type: "button", onclick: () => Docs.shareDialog(f) }, "Share"));
  if (f && f.isRoot) right.push(h("button", { class: "icon-btn head-more", type: "button", "aria-label": "More for this folder", "aria-haspopup": "menu", onclick: (e) => {
    const v = viewEntries();
    menu(e.currentTarget, v.concat(v.length ? ["-"] : [], [
      { id: "zip", icon: "⬇", label: "Download (.zip)", run: () => download({ id: f.id, kind: "folder", name: f.name }) },
      { id: "search-here", icon: "🔎", label: "Search in this folder", run: () => go(searchHash({ scope: [f.id] })) }]
      .concat((f.kidsRoot ? [] : Docs.rootActions).filter((a) => !a.show || a.show(f)).map((a) => ({ id: a.id, icon: a.icon, label: typeof a.label === "function" ? a.label(f) : a.label, run: () => a.run(f) })))));
  } }, "⋯"));
  else if (f) right.push(h("button", { class: "icon-btn head-more", type: "button", "aria-label": "More for this folder", "aria-haspopup": "menu",
    onclick: (e) => itemMenu(e.currentTarget, f, { inFolderHead: true, prepend: viewEntries() }) }, "⋯"));
  else right.push(h("button", { class: "icon-btn head-more phone-only", type: "button", "aria-label": "Sort and view", "aria-haspopup": "menu", id: "mineMore",
    onclick: (e) => menu(e.currentTarget, viewEntries()) }, "⋯"));
  const role = data.mode ? h("span", { class: "chip mode-chip role-" + data.role }, data.mode === "rw" ? "✏️ Read and write" : "🔒 Read only")
    : (data.role !== "owner" ? h("span", { class: "chip role-" + data.role }, roleLabel(data.role)) : null);
  const items = sortItems(data.items, sort, rev);
  const nFolders = items.filter((x) => x.kind === "folder").length;
  const bytes = items.reduce((t, x) => t + (x.kind === "folder" ? 0 : (x.size || 0)), 0);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const summary = items.length ? h("p", { class: "folder-summary hint", id: "folderSummary" },
    [plural(items.length, "item"), nFolders ? plural(nFolders, "folder") : null, bytes ? fmtSize(bytes) : null].filter(Boolean).join(" · ")) : null;
  const sel = Docs.makeSelection ? Docs.makeSelection(items, () => drawList()) : null;
  const listBox = h("div", { class: "list-box" });
  const bar = h("div", { class: "select-bar-slot" });
  const ctx = { inFolder: true, grid, select: sel, folderRole: data.role, dnd: { canDrag: data.canEdit, canDrop: data.canEdit } };
  const emptyState = () => {
    if (!data.canEdit) return UI.empty("This folder is empty", null);
    const nc = () => newContext();
    return UI.empty("This folder is empty", "Make something, or drop files here (they can also come in over Samba or the File editor).",
      h("div", { class: "empty-actions" },
        h("button", { class: "btn-primary", type: "button", id: "emptyNote", onclick: () => createDoc(defaultNote(), "", nc()) }, "📝 New note"),
        h("button", { class: "btn-secondary", type: "button", id: "emptyUpload", onclick: () => Docs.pickUpload && Docs.pickUpload(Docs.state.currentFolder) }, "⬆ Upload"),
        Docs.scanDialog ? h("button", { class: "btn-secondary", type: "button", id: "emptyScan", onclick: () => Docs.scanDialog(nc()) }, "📷 Scan") : null));
  };
  function drawList() {
    mount(listBox, itemList(items, ctx, emptyState()));
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
    h("div", { class: "head-actions" }, sortSel, dirBtn, viewBtn, right)), summary, kidsNote, f ? (Docs.headExtras || []).map((fn) => fn(f, { folder: true })) : null, card);
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
  const ctx = { showOwner: !opts.bySharer, space, extraMeta: (it) => [it.hidden ? "hidden" : null,
    !opts.bySharer && it.sharedByName && it.sharedByName !== it.ownerName ? `shared by ${it.sharedByName}` : null,
    opts.bySharer && it.sharedAt ? `shared ${fmtWhen(it.sharedAt)}` : null].filter(Boolean) };
  let body;
  if (!data.items.length) body = itemList([], ctx, empty);
  else if (opts.bySharer) {
    // grouped by who shared it ("From Priya"), the newest shares first; a dot on what you haven't opened yet
    const groups = new Map();
    data.items.slice().sort((a, b) => tsOf(b.sharedAt) - tsOf(a.sharedAt)).forEach((it) => {
      const who = it.sharedByName || it.ownerName || "Someone";
      if (!groups.has(who)) groups.set(who, []);
      groups.get(who).push(it);
    });
    body = Array.from(groups, ([who, list]) => [h("h3", { class: "ui-group-head" }, `From ${who}`,
      h("span", { class: "ui-group-total" }, list.filter(isNewShare).length ? `${list.filter(isNewShare).length} new` : `${list.length}`)),
      itemList(list.map((it) => (isNewShare(it) ? Object.assign({}, it, { newShare: true }) : it)), ctx)]);
  } else if (opts.byDay) {
    // Today / Yesterday / Earlier, by when you opened it
    const label = (it) => { const d = Docs.dayLabel ? Docs.dayLabel(it.openedAt || it.modified) : ""; return d === "Today" || d === "Yesterday" ? d : "Earlier"; };
    const groups = new Map();
    data.items.forEach((it) => { const l = label(it); if (!groups.has(l)) groups.set(l, []); groups.get(l).push(it); });
    body = Array.from(groups, ([l, list]) => [h("h3", { class: "ui-group-head" }, l), itemList(list, ctx)]);
  } else body = itemList(opts.sort === false ? data.items : sortItems(data.items), ctx, empty);
  mount(page, pageHead(title, right), h("div", { class: "card list-card" + (opts.bySharer || opts.byDay ? " grouped" : "") }, body));
}
Docs.route("shared", (page, _a, cur) => renderSpace(page, "shared", "Shared with me", "Nothing is shared with you yet.", cur, { hideable: true, bySharer: true }));
Docs.route("everyone", (page, _a, cur) => renderSpace(page, "everyone", "Everyone", "Nothing is shared with Everyone yet.", cur, { hideable: true, bySharer: true }));
Docs.route("favourites", (page, _a, cur) => renderSpace(page, "favourites", "Favourites", "Tap ⋯ → Organise → Favourite on anything to keep it here.", cur));
Docs.route("recent", (page, _a, cur) => renderSpace(page, "recent", "Recent", "What you open shows up here.", cur, { sort: false, byDay: true }));

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
  openItem(it);             // a PDF opens in the viewer, anything else in the file panel
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

// 🗑 Trash: its own space. Each row: Restore, and ⋯ → Restore / Delete now; "goes in N days"; a filter box when long.
Docs.route("trash", async (page, _a, current) => {
  mount(page, pageHead("Trash"), spinner());
  let data;
  try { data = await api("api/space/trash"); }
  catch (e) { if (current()) mount(page, pageHead("Trash"), errorCard(e, render)); return; }
  if (!current()) return;
  const total = data.items.length + (data.folders || []).reduce((n, f) => n + f.items.length, 0);
  const empty = h("button", { class: "btn-danger", type: "button", id: "emptyTrash", disabled: !data.items.length, onclick: async () => {
    if (!(await confirmDialog("Empty Trash", `Delete ${data.items.length} item${data.items.length === 1 ? "" : "s"} for good? This can't be undone.`, "Empty Trash", true))) return;
    try { await api("api/trash/empty", { method: "POST" }); toast("Trash emptied"); render(); } catch (e) { fail(e); }
  } }, "Empty Trash");
  const days = data.days;
  const goesIn = (t) => {
    if (!days) return null;
    const left = Math.ceil(days - (Date.now() - tsOf(t.deletedAt)) / 86400000);
    return left <= 1 ? "goes for good within a day" : `goes for good in ${left} days`;
  };
  const restore = async (t) => { try { await api(`api/trash/${encodeURIComponent(t.id)}/restore`, { method: "POST" }); toast(`${t.name} restored`); render(); } catch (e) { fail(e); } };
  const purge = async (t) => {
    if (!(await confirmDialog("Delete now", `Delete “${t.name}” for good? This can't be undone.`, "Delete now", true))) return;
    try { await api(`api/trash/${encodeURIComponent(t.id)}`, { method: "DELETE" }); toast(`${t.name} deleted for good`); render(); } catch (e) { fail(e); }
  };
  const trashRow = (t, top) => h("div", { class: "item-row", dataset: { trash: t.id, name: t.name.toLowerCase() } },
    h("div", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, kindOf(t).icon),
      h("span", { class: "row-text" }, h("span", { class: "row-name" }, t.name),
        h("span", { class: "row-meta" }, [`was in ${t.originalPath.includes("/") ? t.originalPath.slice(0, t.originalPath.lastIndexOf("/")) : top}`,
          `deleted ${fmtWhen(t.deletedAt)}`, t.deletedByName ? `by ${t.deletedByName}` : null, goesIn(t)].filter(Boolean).join(" · ")))),
    h("button", { class: "btn-secondary btn-small", type: "button", onclick: () => restore(t) }, "Restore"),
    h("button", { class: "icon-btn row-more", type: "button", "aria-label": `More for ${t.name}`, "aria-haspopup": "menu", onclick: (e) => menu(e.currentTarget, [
      { id: "trash-restore", icon: "♻️", label: "Restore", run: () => restore(t) }, "-",
      { id: "trash-delete", icon: "🗑", label: "Delete now", danger: true, run: () => purge(t) }]) }, "⋯"));
  const rows = data.items.map((t) => trashRow(t, "My docs"));
  const folders = (data.folders || []).map((f) => h("section", { class: "card list-card", dataset: { trashFolder: f.rootId } },
    h("h3", { class: "list-card-title" }, `🗂️ ${f.label}`), h("div", { class: "item-list" }, f.items.map((t) => trashRow(t, f.label)))));
  const filter = total > 20 ? h("input", { type: "search", id: "trashFilter", placeholder: "Find in Trash", "aria-label": "Find in Trash", maxlength: "100" }) : null;
  if (filter) filter.addEventListener("input", () => {
    const q = filter.value.trim().toLowerCase();
    page.querySelectorAll("[data-trash]").forEach((r) => { r.hidden = !!q && !r.dataset.name.includes(q); });
  });
  mount(page, pageHead("Trash", empty),
    h("p", { class: "hint" }, `Deleted things stay here${days ? ` for ${days} days` : ""}, then go for good.`),
    h("details", { class: "about" }, h("summary", null, "More about Trash"),
      h("p", null, "Things deleted from your folder — by you or by people you share with — stay here until the Trash is emptied (automatically after the number of days an admin set). What's deleted from a shared folder you can change is kept in that folder's own hidden .trash.")),
    filter ? h("div", { class: "filter-row" }, filter) : null,
    rows.length ? h("div", { class: "card list-card" }, h("div", { class: "item-list" }, rows))
      : UI.empty("Trash is empty", folders.length ? null : "Deleted things land here, and can be restored."),
    folders.length ? h("p", { class: "hint" }, "Deleted from shared folders you can change:") : null, folders);
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
  const me = Docs.state.me || {};
  mount(page, pageHead("More"),
    // you: who the app thinks you are (Settings → How the app sees you)
    h("a", { class: "card me-card item-row more-row", href: "#/settings/whoami", id: "moreMe" },
      h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, "👤"),
        h("span", { class: "row-text" }, h("span", { class: "row-name" }, me.name || "You", me.isAdmin ? h("span", { class: "chip" }, "admin") : null),
          h("span", { class: "row-meta" }, "How the app sees you")))),
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
  // rows other parts of the app add (Docs.settingsRows): fn.group = "notify" | "new" | "display" (default "display")
  const extra = (group) => Docs.settingsRows.filter((fn) => (fn.group || "display") === group).map((fn) => fn(me, save, row));
  const card = (id, title, ...rows) => h("div", { class: "card", id }, h("h3", null, title), rows);
  const cards = me.disabled ? [] : [
    card("prefsCard", "Notifications",
      row("Tell me when something is shared with me", me.notifyLinked ? "A notification on your phone (from Home Assistant)." :
        "No phone is linked to you in Home Assistant yet (Settings → People → you → Track device).",
      toggleSwitch(me.prefs.notifyShares, (v) => save({ notifyShares: v }), "Tell me when something is shared with me")),
      extra("notify")),
    card("prefsNewCard", "New documents",
      row("New notes are", "➕ New → Note makes this kind; the other is in the menu too. Markdown adds headings, lists and tick boxes with a preview.", defaultNote),
      row("New sheets as", "An .xlsx keeps formats, tabs and charts; a .csv holds values and formulas only (any app opens it).", newSheets),
      row("Number style for sheets", "How numbers look in sheets, and how the numbers you type are read (1.234,56 reads a decimal comma). Formulas keep commas between values.", numberStyle),
      extra("new")),
    card("prefsDisplayCard", "Display",
      row("Sort folders by", "Folders always come first.", sort),
      row("Theme", "The same choice in every household app on this device.", HouseholdTheme.bindSelect(h("select", { "aria-label": "Theme" }))),
      extra("display")),
    me.app.assistant ? card("prefsAssistantCard", "Household Assistant",
      row("Let the Household Assistant answer for me", "When you ask the Household Assistant app, it can find and read the documents you can open here, and make a note in your Inbox when you tap to confirm.",
        toggleSwitch(me.prefs.assistantOk !== false, (v) => save({ assistantOk: v }), "Let the Household Assistant answer for me"))) : null,
  ];
  cards.push(
    h("div", { class: "card" }, h("h3", null, "Where your documents are"),
      h("p", { class: "hint" }, "Every document is an ordinary file in Home Assistant's /share folder, in a folder of your own."),
      h("details", { class: "about" }, h("summary", null, "Who else can reach them"),
        h("p", null, "Anyone who can reach /share — over Samba, the File editor, another app, or a Home Assistant backup that includes Share — can read and change them there; the sharing you set here only applies inside the app.")),
      me.folder ? h("p", null, "Your folder: ", h("strong", null, me.folder)) : null),
    // the diagnostic card goes last, folded
    h("details", { class: "card whoami-card", id: "whoamiCard", open: args[0] === "whoami" }, h("summary", null, h("h3", null, "How the app sees you")),
      HouseholdWhoami.panel(who, { appName: "Household Docs", adviceTag: "div", adviceStyle: "margin-top:8px", onCopy: (t) => copyText(t) })));
  mount(page, pageHead("Settings"), cards);
  if (args[0] === "whoami") setTimeout(() => { const c = $("#whoamiCard"); if (c) c.scrollIntoView({ block: "start" }); }, 50);
});

// ---------- Back ----------
// top-level pages (a sidebar or bottom-bar entry): going to one starts a new back trail
const TAB_ROUTES = new Set(["", "mine", "shared", "everyone", "kids", "folders", "favourites", "recent", "activity", "storage", "trash", "tags",
  "searches", "search", "settings", "admin", "more"]);
// where Back goes from an address when there's no trail (a link from a chat, a reload): a document → its folder, a
// folder → the folder above, Admin's and Settings' sub-pages → their page
function parentOf(hash) {
  const { name, args } = parseHash(hash);
  if (name === "doc") return Docs.state.docParent || null;
  if (name === "folder") return Docs.state.folderParent || null;
  if (name === "admin" && args.length) return "#/admin";
  if (name === "settings" && args.length) return "#/settings";
  if (name === "storage" && args.length) return "#/storage";
  return null;
}
Docs.parentOf = parentOf;

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
  // the header's box (computers; phones use the Search tab): Enter opens the Search page. Typing only searches as you
  // go while the Search page is already showing (it fills the page's own box).
  input.addEventListener("input", debounce(() => {
    if (Docs.state.route === "search" && Docs.searchFromHeader && (input.value.trim().length >= 2 || !input.value.trim())) Docs.searchFromHeader(input.value.trim(), false);
  }, 250));
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
      isTab: (hash) => { const p = parseHash(hash); return TAB_ROUTES.has(p.name) && !p.args.length; },
      parentOf,
      openLayers: () => UI.dialogs().concat(openMenuEl ? ["menu"] : []),
      closeLayer: (layer) => { if (layer === "menu") closeMenu(); else layer.close(); },
    });
  }
}
Docs.start = start;
