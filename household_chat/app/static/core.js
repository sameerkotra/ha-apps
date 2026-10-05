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
// ---------- the back button / back gesture ----------
// Android's back gesture (and the browser's Back) should close what's open in the app — a menu, a
// dialog, the open chat on a phone — before it leaves the app for Home Assistant. Everything open is a
// "layer" (a function that closes it). While any layer is open there is exactly one extra history entry
// (the guard); Back pops it, the top layer closes, and the guard is set again if more layers remain.
// When the app itself closes the last layer, the guard is removed a moment later — unless something new
// opened meanwhile (closing a dialog and opening a chat in one go).
const layers = [];
let backGuard = false, ignorePops = 0, dropTimer = null;
function addLayer(close) {
  layers.push(close);
  clearTimeout(dropTimer);
  if (!backGuard) { try { history.pushState({ hcBack: true }, ""); backGuard = true; } catch (e) { /* sandboxed */ } }
  return close;
}
function removeLayer(close) {
  const i = layers.lastIndexOf(close);
  if (i >= 0) layers.splice(i, 1);
  if (layers.length || !backGuard) return;
  clearTimeout(dropTimer);
  dropTimer = setTimeout(() => { if (!layers.length && backGuard) { backGuard = false; ignorePops++; history.back(); } }, 300);
}
window.addEventListener("popstate", () => {
  if (ignorePops) { ignorePops--; return; }
  backGuard = false;
  const close = layers.pop();
  if (close) close();
  if (layers.length) addLayer(layers.pop());     // re-arm the guard for what's still open
});

// Dialogs (common/ui.js): Escape closes only the top one, and each is a back-button layer.
function modalOptions(opts) {
  return { modalClass: [opts.wide ? "wide" : "", opts.full ? "full" : ""].filter(Boolean).join(" "), backdropClass: opts.dark ? "dark" : "",
    escape: "top", onOpen: addLayer, onClosed: removeLayer, onClose: opts.onClose,
    focus: opts.noFocus ? false : "first", focusIf: FINE_POINTER,
    focusSelector: "input:not([type=hidden]):not([disabled]):not([type=checkbox]):not([type=radio]), select, textarea" };
}
function openModal(title, content, opts = {}) { return UI.openModal(title, content, modalOptions(opts)); }
function confirmDialog(title, message, okLabel = "OK", danger = false) {
  return UI.confirmDialog(title, message, { okLabel, okClass: "btn " + (danger ? "danger" : "primary"), cancelClass: "btn", modal: modalOptions({}) });
}
function dropMenu(m) { m.remove(); if (m._layer) removeLayer(m._layer); }
function closeMenus() { document.querySelectorAll(".menu").forEach(dropMenu); }
function menu(anchor, entries, at) {
  closeMenus();
  const m = h("div", { class: "menu", role: "menu" },
    ...entries.filter(Boolean).map((e) => e === "-" ? h("hr") :
      e.row ? e.row :
        h("button", { type: "button", role: "menuitem", class: e.danger ? "danger" : "", onclick: () => { closeMenus(); e.run(); } }, e.label)));
  document.body.appendChild(m);
  const r = at ? { left: at.x, right: at.x, top: at.y, bottom: at.y } : anchor.getBoundingClientRect();
  const w = m.offsetWidth, hh = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + "px";
  m.style.top = (r.bottom + hh + 6 > window.innerHeight ? Math.max(6, r.top - hh - 4) : r.bottom + 4) + "px";
  m._layer = addLayer(() => m.remove());
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
  page: "chat",             // chat | starred | reminders | files | search | settings | admin | whoami
  pageArg: null,
  replyTo: {}, editing: null, pending: {}, drafts: {}, sendOriginal: {}, expiresIn: {}, announce: {},
  typing: {},               // chat id → {user id: {name, until}}
  unreadMarker: null, newBelow: 0, sse: null, sseFails: 0, polling: null,
  adminTab: "people",
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
  if (uid && isOnline(uid) && !opts.noDot) el.appendChild(h("span", { class: "dot", title: "Online" }));
  return el;
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
