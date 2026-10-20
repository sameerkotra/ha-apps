/* Household Chat — shared helpers. Plain JS, DOM building only: the CSP forbids inline scripts
   and styles, and every piece of user text goes in through textContent (never innerHTML). */
"use strict";

// ---------- DOM (common/ui.js) ----------
const { clear, mount, $, debounce, lsGet, lsSet } = UI;
const h = UI.makeH({ booleanProps: ["checked", "disabled", "hidden"] });   // as properties: 0 / "" mean off
function initials(t) { const w = String(t || "?").replace(/\(.*\)/, "").trim().split(/\s+/); return ((w[0] || "?")[0] + ((w[1] || "")[0] || "")).toUpperCase(); }
function colorOf(t) { let n = 0; for (const c of String(t || "")) n = (n * 31 + c.charCodeAt(0)) >>> 0; return "av" + (n % 8); }
function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  if (n < 1024 ** 3) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 ** 3).toFixed(2) + " GB";
}
function fmtDuration(s) { s = Math.max(0, Math.round(s || 0)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
const FINE_POINTER = window.matchMedia && window.matchMedia("(pointer: fine)").matches;
const TAB_ID = "t" + Math.random().toString(36).slice(2, 12);

// ---------- time ----------
function toDate(iso) { const d = new Date(iso); return isNaN(d) ? null : d; }
function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
function fmtTime(iso) { const d = toDate(iso); return d ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : ""; }
function fmtDay(iso) {
  const d = toDate(iso); if (!d) return "";
  const now = new Date(); const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return "Today";
  if (sameDay(d, y)) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}
function fmtWhen(iso) {
  const d = toDate(iso); if (!d) return "";
  const now = new Date();
  if (sameDay(d, now)) return fmtTime(iso);
  const diff = (now - d) / 86400000;
  if (diff < 6) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
function fmtFull(iso) { const d = toDate(iso); return d ? d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
function ago(iso) {
  const d = toDate(iso); if (!d) return "";
  const s = (Date.now() - d) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return fmtWhen(iso);
}

// ---------- API ----------
class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
const api = UI.makeApi({
  networkError: "Can't reach Household Chat. Check your connection and try again.",
  message: (body, res) => {
    const detail = body ? body.detail : null;
    return typeof detail === "string" ? detail : `Something went wrong (HTTP ${res.status}).`;
  },
  makeError: (msg, status) => new ApiError(msg, status),
  onError: (err, res) => {
    if (res.status === 403 && state.me && !state.me.disabled && /hasn't given you access/.test(err.message)) { location.reload(); }
  },
});
function uploadXhr(url, file, onProgress) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("POST", url.replace(/^\//, ""));
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null; try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError((data && typeof data.detail === "string" && data.detail) || `Upload failed (HTTP ${xhr.status}).`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("The upload failed — check your connection.", 0));
    xhr.onabort = () => reject(new ApiError("Cancelled", -1));
    xhr.send(file);
  });
  return { xhr, promise };
}

// ---------- toasts, modals, menus ----------
function toast(msg, opts = {}) {
  UI.toast(msg, { error: opts.error, role: "status", onclick: opts.onclick, ms: opts.ms || (opts.error ? 5000 : 2600) });
}
function fail(e) { if (e && e.status === -1) return; toast(e && e.message ? e.message : String(e), { error: true }); }
// ---------- addresses and the back gesture (common/backnav.js) ----------
// Every view has an address: "#/c/<chat id>" (with "/m/<message id>" to open it at a message), "#/starred",
// "#/reminders", "#/calls", "#/files", "#/search/<words>", "#/settings", "#/admin/<tab>" and "#/me"; no address is
// the chat list. Moving around goes through navigate(), and the hashchange listener (route(), main.js) draws the view.
// BackNav makes Android's back gesture (and the browser's Back) close what's open first — a menu, a dialog, the
// photo viewer, selecting messages — then go back to the previous address (e.g. Starred after "Show in chat"),
// then to the chat list, and only then leave the app. A call is never ended by Back.
let routeTask = null;       // what the last address change started (openChat() awaits it)
let tabNav = false;         // the navigation under way starts a new trail (the chat list, the ⋯ and Me menus)
let routing = false;        // BackNav and the hashchange listener are set up (the full app, not the admin-only view)
function navigate(hash, opts = {}) {
  routeTask = null;
  if (!routing) { try { location.hash = hash; } catch (e) { /* ignore */ } return Promise.resolve(); }
  tabNav = !!opts.tab;
  try { if (opts.replace) BackNav.replace(hash); else BackNav.go(hash); } finally { tabNav = false; }
  const t = routeTask;
  routeTask = null;
  return t || Promise.resolve();
}
// an in-app ← button: the previous address, else the chat list
function goBack() { if (!(routing && BackNav.back())) showHome(); }
function openLayers() {
  const out = [...document.querySelectorAll("#modalRoot .modal-backdrop")];
  const sel = $("#selectBar");
  if (sel && !sel.hidden && state.selecting) out.push(sel);
  out.push(...document.querySelectorAll(".menu"));
  const call = $("#callScreen");
  if (call) out.push(call);                    // the call screen is on top of everything else
  return out;
}
function closeLayer(el) {
  if (el.id === "callScreen") { closeCallScreenByBack(); return; }
  if (el.id === "selectBar") { stopSelecting(); return; }
  if (el.classList.contains("menu")) { dropMenu(el); return; }
  if (el._close) el._close();
  else el.remove();
}
function initNav() {
  if (!window.BackNav || routing) return;
  BackNav.init({
    atHome: () => state.page === "chat" && !state.current,
    goHome: () => showHome(),
    openLayers, closeLayer,
    isTab: () => tabNav,
    parentOf: () => null,                      // every view's parent is the chat list
  });
  window.addEventListener("hashchange", route);
  routing = true;
}

// Dialogs (common/ui.js): Escape closes only the top one, and each is a back-button layer.
function modalOptions(opts) {
  return { modalClass: [opts.wide ? "wide" : "", opts.full ? "full" : ""].filter(Boolean).join(" "), backdropClass: opts.dark ? "dark" : "",
    escape: "top", onClose: opts.onClose,
    afterOpen: (handle) => { const b = handle.el.parentNode; if (b) b._close = handle.close; },      // Back closes it
    focus: opts.noFocus ? false : "first", focusIf: FINE_POINTER,
    focusSelector: "input:not([type=hidden]):not([disabled]):not([type=checkbox]):not([type=radio]), select, textarea" };
}
function openModal(title, content, opts = {}) { return UI.openModal(title, content, modalOptions(opts)); }
function confirmDialog(title, message, okLabel = "OK", danger = false) {
  return UI.confirmDialog(title, message, { okLabel, okClass: "btn " + (danger ? "danger" : "primary"), cancelClass: "btn", modal: modalOptions({}) });
}
function dropMenu(m) { m.remove(); }
function closeMenus() { document.querySelectorAll(".menu").forEach(dropMenu); }
function menu(anchor, entries, at) {
  closeMenus();
  const m = h("div", { class: "menu", role: "menu" },
    ...entries.filter(Boolean).map((e) => e === "-" ? h("hr") :
      e.row ? e.row : e.head ? h("div", { class: "menu-head", role: "presentation" }, e.head) :
        h("button", { type: "button", role: "menuitem", class: e.danger ? "danger" : "", onclick: () => { closeMenus(); e.run(); } }, e.label)));
  document.body.appendChild(m);
  const r = at ? { left: at.x, right: at.x, top: at.y, bottom: at.y } : anchor.getBoundingClientRect();
  const w = m.offsetWidth, hh = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + "px";
  m.style.top = (r.bottom + hh + 6 > window.innerHeight ? Math.max(6, r.top - hh - 4) : r.bottom + 4) + "px";
  setTimeout(() => document.addEventListener("click", function once(ev) { if (!m.contains(ev.target)) { dropMenu(m); document.removeEventListener("click", once); } }), 0);
  const onKey = (e) => { if (e.key === "Escape") { dropMenu(m); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
  const first = m.querySelector("button");
  if (first && FINE_POINTER) first.focus();
  return m;
}
function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl" }, label), control, hint ? h("span", { class: "hint" }, hint) : null);
}
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = h("textarea", { class: "sr" }); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (x) { /* ignore */ }
    ta.remove();
  }
  toast("Copied");
}

// ---------- state ----------
const state = {
  me: null, convs: [], people: [], online: {}, homeAway: {},
  current: null,            // open chat id
  detail: null,             // its details (members …)
  msgs: [], moreBefore: false, reads: [], loadingOlder: false,
  page: "chat",             // chat | starred | reminders | calls | files | search | settings | admin | whoami
  pageArg: null,
  replyTo: {}, editing: null, pending: {}, drafts: {}, sendOriginal: {}, expiresIn: {}, announce: {},
  typing: {},               // chat id → {user id: {name, until}}
  unreadMarker: null, newBelow: 0, sse: null, sseFails: 0, polling: null,
  adminTab: "people",
  unreadOnly: false,        // the chat list shows only chats with unread messages
  strip: { ann: [], pins: [], sched: [], note: false, pinAt: 0 }, stripOpen: false,
};
function convById(id) { return state.convs.find((c) => c.id === id); }
function personById(id) { return state.people.find((p) => p.id === id); }
function nameOf(uid) { if (state.me && uid === state.me.id) return "You"; const p = personById(uid); if (p) return p.name; const m = state.detail && state.detail.members.find((x) => x.id === uid); return m ? m.name : "Someone"; }

// ---------- avatars & presence ----------
function avatar(name, uid, opts = {}) {
  // the photo is a copy of their Home Assistant person picture; `avatar` is its version (null = none)
  const version = uid && ((personById(uid) || {}).avatar || opts.photo);
  const el = h("span", { class: "avatar " + colorOf(name) + (opts.small ? " small" : "") + (opts.big ? " big" : ""), "aria-hidden": "true" });
  if (version) el.appendChild(h("img", { src: `api/avatars/${encodeURIComponent(uid)}?v=${version}`, alt: "", loading: "lazy" }));
  else el.textContent = initials(name);
  if (uid && !opts.noDot) { el.dataset.uid = uid; if (isOnline(uid)) el.appendChild(h("span", { class: "dot", title: "Online" })); }
  return el;
}
// the online dots of the avatars inside `root`, brought up to date without drawing anything else again
function refreshDots(root) {
  if (!root) return;
  root.querySelectorAll(".avatar[data-uid]").forEach((el) => {
    const dot = el.querySelector(".dot");
    const on = isOnline(el.dataset.uid);
    if (on && !dot) el.appendChild(h("span", { class: "dot", title: "Online" }));
    else if (!on && dot) dot.remove();
  });
}
function convIcon(c, opts = {}) {
  if (c.kind === "personal") {
    // your own room shows your Home Assistant picture, or 📌 without one
    if ((personById(state.me.id) || {}).avatar) return avatar(state.me.name, state.me.id, Object.assign({ noDot: true }, opts));
    return h("span", { class: "avatar room" + (opts.big ? " big" : ""), "aria-hidden": "true" }, "📌");
  }
  if (c.kind === "direct") return avatar(c.name, c.otherUserId, opts);
  return h("span", { class: "avatar group " + colorOf(c.name) + (opts.big ? " big" : ""), "aria-hidden": "true" }, c.icon || initials(c.name));
}
function isOnline(uid) { const o = state.online[uid]; return !!(o && o.online); }
function presenceLine(uid) {
  const parts = [];
  const ha = state.homeAway[uid];
  if (ha) parts.push(ha.label + (ha.since && toDate(ha.since) ? " since " + (sameDay(toDate(ha.since), new Date()) ? fmtTime(ha.since) : fmtWhen(ha.since)) : ""));
  const o = state.online[uid];
  if (o) parts.push(o.online ? "online" : (o.lastSeen ? "last seen " + ago(o.lastSeen) : ""));
  return parts.filter(Boolean).join(" · ");
}

const EMOJI = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👏", "🎉", "🔥", "✅", "👀", "😊", "😍", "🤔", "😅", "🙌", "💯", "👌", "😴", "🍕",
  "☕", "🏠", "🚗", "🛒", "📅", "⏰", "💡", "⚠️", "❌", "➕"];
const QUICK_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏"];
