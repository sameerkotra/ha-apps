/* Household Vault — the page. Plain JS, DOM building only (the CSP forbids inline scripts and styles;
   user text always goes in through textContent). The unlocked session token lives only in this
   page's memory: a reload locks. */
"use strict";

// ---------- DOM helpers ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else if (k === "checked") el.checked = !!v;
      else if (k === "disabled") el.disabled = !!v;
      else if (k === "hidden") el.hidden = !!v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function mount(el, ...kids) { clear(el); for (const k of kids.flat(Infinity)) if (k) el.appendChild(k); return el; }
const $ = (s) => document.querySelector(s);
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
function initials(t) { const w = String(t || "?").trim().split(/\s+/); return ((w[0] || "?")[0] + ((w[1] || "")[0] || "")).toUpperCase(); }
function colorOf(t) { let n = 0; for (const c of String(t || "")) n = (n * 31 + c.charCodeAt(0)) >>> 0; return "av" + (n % 8); }
function avatar(title) { return h("span", { class: "avatar " + colorOf(title), "aria-hidden": "true" }, initials(title)); }
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return d.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function kdbxTime(b64) {
  // KeePass times: base64 of seconds since 0001-01-01
  try {
    const bin = atob(b64); let n = 0n;
    for (let i = 7; i >= 0; i--) n = (n << 8n) + BigInt(bin.charCodeAt(i));
    const ms = Number(n - 62135596800n) * 1000;
    return new Date(ms).toISOString();
  } catch (e) { return ""; }
}
const MULTI_VIEWS = ["all", "fav", "recent", "type", "search", "expiring", "tag"];     // views across every open vault
const ROLE_LABEL = { owner: "Owner", manager: "Manager", editor: "Can edit", viewer: "Can view" };
const TYPE_ICON = { login: "🔑", note: "📝", card: "💳" };
// Item templates: a base type, preset field names (true = hidden like a password) and what the expiry means
const TEMPLATES = {
  login: { label: "Login", icon: "🔑", type: "login", fields: [] },
  card: { label: "Payment card", icon: "💳", type: "card", fields: [], expires: "Card expires" },
  note: { label: "Secure note", icon: "📝", type: "note", fields: [] },
  wifi: { label: "Wi-Fi", icon: "📶", type: "login", fields: [], user: "Network name (SSID)", title: "Home Wi-Fi" },
  bank: { label: "Bank account", icon: "🏦", type: "login", fields: [["Account number", true], ["IFSC / sort code", false], ["Branch", false], ["Customer ID", false]], title: "e.g. HDFC savings" },
  insurance: { label: "Insurance policy", icon: "☂️", type: "note", fields: [["Insurer", false], ["Policy number", false], ["Helpline", false], ["Premium", false]], expires: "Renews on", title: "e.g. Health insurance" },
  vehicle: { label: "Vehicle", icon: "🚗", type: "note", fields: [["Registration number", false], ["Make and model", false], ["Chassis / VIN", false], ["Insurance policy", false]], expires: "Insurance / tax due", title: "e.g. Family car" },
  identity: { label: "Passport / ID", icon: "🛂", type: "note", fields: [["Full name", false], ["Document number", true], ["Issued on", false], ["Place of issue", false]], expires: "Expires on", title: "e.g. Passport — Alex" },
  licence: { label: "Driving licence", icon: "🪪", type: "note", fields: [["Full name", false], ["Licence number", true], ["Vehicle classes", false]], expires: "Valid until", title: "e.g. Driving licence — Sam" },
  software: { label: "Software licence", icon: "💿", type: "note", fields: [["Licence key", true], ["Licensed to", false], ["Email", false]], expires: "Subscription ends", title: "e.g. Office 365" },
  utility: { label: "Utility account", icon: "💡", type: "login", fields: [["Consumer / account number", false], ["Provider phone", false]], title: "e.g. Electricity" },
  membership: { label: "Membership", icon: "🎫", type: "login", fields: [["Member number", false]], expires: "Renews on", title: "e.g. Gym" },
};
function iconOf(it) { return (it.template && TEMPLATES[it.template] ? TEMPLATES[it.template].icon : TYPE_ICON[it.type]) || ""; }
function daysLeftOf(iso) { if (!iso) return null; const d = new Date(iso + "T00:00:00"); const t = new Date(); t.setHours(0, 0, 0, 0); return Math.round((d - t) / 86400000); }
function fmtDay(iso) { return iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; }
function expiryChip(iso, within = 60) {
  const n = daysLeftOf(iso);
  if (n === null || n > within) return null;
  const txt = n < 0 ? "expired" : n === 0 ? "expires today" : `expires in ${n} day${n === 1 ? "" : "s"}`;
  return h("span", { class: "chip issue " + (n <= 0 ? "breached" : n <= 30 ? "weak" : "old"), title: `Expires ${fmtDay(iso)}` }, txt);
}

// ---------- state ----------
const state = {
  me: null, token: null, vaults: [], folders: {}, expanded: {}, view: { kind: "all" }, items: [], selected: null,
  detail: null, picked: new Set(), q: "", page: "vault", lastActivity: Date.now(), autoLock: 5, navOpen: false,
  totpTimer: null, idleTimer: null, hiddenAt: null, clipboardTimer: null,
  health: null, healthMap: new Map(), dragging: null, reminderDismissed: false, lastPicked: null, healthPoll: null,
};
const FINE_POINTER = window.matchMedia && window.matchMedia("(pointer: fine)").matches;

// ---------- API ----------
class ApiError extends Error { constructor(msg, status, detail) { super(msg); this.status = status; this.detail = detail; } }
async function api(path, opts = {}) {
  const { method = "GET", body, form, raw } = opts;
  const init = { method, headers: {} };
  if (state.token) init.headers["X-Vault-Session"] = state.token;
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  else if (form) init.body = form;
  let res;
  try { res = await fetch(path.replace(/^\//, ""), init); }
  catch (e) { throw new ApiError("Can't reach the app. Check your connection and try again.", 0); }
  if (res.ok) state.lastActivity = Date.now();
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch (e) { /* not JSON */ }
    const msg = typeof detail === "string" ? detail : (detail && detail.message) || `Something went wrong (HTTP ${res.status}).`;
    if (res.status === 401 && state.token && !opts.noLock) { lockLocal("Locked."); }
    throw new ApiError(msg, res.status, detail);
  }
  if (raw) return res;
  if (res.status === 204) return null;
  return res.json();
}

// ---------- toasts, modals, menus ----------
function toast(msg, opts = {}) {
  const t = h("div", { class: "toast" + (opts.error ? " error" : ""), role: "status" }, msg);
  $("#toastRoot").appendChild(t);
  setTimeout(() => t.remove(), opts.ms || (opts.error ? 5000 : 2600));
}
function fail(e) { toast(e && e.message ? e.message : String(e), { error: true }); }
function openModal(title, content, opts = {}) {
  const closeBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Close" }, "✕");
  const modal = h("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
    h("h3", null, h("span", null, title), closeBtn), content);
  const back = h("div", { class: "modal-backdrop" }, modal);
  let downOnBack = false;
  back.addEventListener("mousedown", (e) => { downOnBack = e.target === back; });
  back.addEventListener("click", (e) => { if (e.target === back && downOnBack && !opts.sticky) close(); });
  const onKey = (e) => { if (e.key === "Escape" && !opts.sticky) close(); };
  document.addEventListener("keydown", onKey);
  function close() { back.remove(); document.removeEventListener("keydown", onKey); if (opts.onClose) opts.onClose(); }
  closeBtn.addEventListener("click", close);
  if (opts.sticky) closeBtn.hidden = true;
  $("#modalRoot").appendChild(back);
  closeNav();
  const first = modal.querySelector("input:not([type=hidden]):not([disabled]):not([type=checkbox]), select, textarea");
  if (first && !opts.noFocus) setTimeout(() => first.focus(), 30);
  return { close, el: modal };
}
function closeTopModal() { const b = document.querySelectorAll("#modalRoot .modal-backdrop"); if (b.length) b[b.length - 1].querySelector(".modal h3 .icon-btn").click(); }
function confirmDialog(title, message, okLabel = "OK", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    const m = openModal(title, h("div", null, h("p", null, message), h("div", { class: "actions" },
      h("button", { class: "btn", type: "button", onclick: () => { done = true; m.close(); resolve(false); } }, "Cancel"),
      h("button", { class: "btn " + (danger ? "danger" : "primary"), type: "button", onclick: () => { done = true; m.close(); resolve(true); } }, okLabel))),
      { onClose: () => { if (!done) resolve(false); } });
  });
}
function closeMenus() { document.querySelectorAll(".menu").forEach((m) => m.remove()); }
function menu(anchor, entries) {
  closeMenus();
  const m = h("div", { class: "menu", role: "menu" },
    ...entries.filter(Boolean).map((e) => e === "-" ? h("hr") :
      h("button", { type: "button", role: "menuitem", class: e.danger ? "danger" : "", onclick: () => { closeMenus(); e.run(); } }, e.label)));
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  const w = m.offsetWidth, hh = m.offsetHeight;
  m.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.right - w)) + "px";
  m.style.top = (r.bottom + hh + 6 > window.innerHeight ? Math.max(6, r.top - hh - 4) : r.bottom + 4) + "px";
  setTimeout(() => document.addEventListener("click", function once(ev) { if (!m.contains(ev.target)) { m.remove(); document.removeEventListener("click", once); } }), 0);
  const first = m.querySelector("button");
  if (first) first.focus();
}
function field(label, control, hint) {
  return h("label", { class: "field" }, h("span", { class: "lbl" }, label), control, hint ? h("span", { class: "hint" }, hint) : null);
}
// A table that becomes a list of cards on phones (each cell labelled from its column)
function dataTable(headers, rows, cls) {
  return h("table", { class: "table stack" + (cls ? " " + cls : "") },
    h("thead", null, h("tr", null, headers.map((x) => h("th", null, x)))),
    h("tbody", null, rows.map((cells) => h("tr", null, cells.map((c, i) => h("td", { "data-label": headers[i] || null, class: headers[i] ? null : "cell-actions" }, c))))));
}
function strengthMeter(input) {
  const bar = h("div", { class: "meter" }, h("div"));
  const label = h("span", { class: "hint" }, "");
  const check = debounce(async () => {
    if (!input.value) { bar.className = "meter"; label.textContent = ""; return; }
    try {
      const r = await api("api/strength", { method: "POST", body: { password: input.value } });
      bar.className = "meter s" + r.score; label.textContent = `Strength: ${r.label}`;
    } catch (e) { /* ignore */ }
  }, 250);
  input.addEventListener("input", check);
  return h("div", null, bar, label);
}
function pwInput(opts = {}) {
  const input = h("input", { type: "password", autocomplete: opts.autocomplete || "current-password", placeholder: opts.placeholder || "", "aria-label": opts.label || "Password", spellcheck: "false" });
  const eye = h("button", { class: "icon-btn", type: "button", "aria-label": "Show password", title: "Show / hide" }, "👁");
  eye.addEventListener("click", () => { input.type = input.type === "password" ? "text" : "password"; });
  return { el: h("div", { class: "input-with" }, input, eye), input };
}

// ---------- clipboard (overwritten after N seconds; we can't check it still holds our value) ----------
async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = h("textarea", { class: "sr" }); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (x) { /* ignore */ }
    ta.remove();
  }
  const secs = state.me ? state.me.clipboardClearSeconds : 30;
  clearTimeout(state.clipboardTimer);
  if (secs > 0 && what !== "plain") {
    state.clipboardTimer = setTimeout(() => { try { navigator.clipboard.writeText(""); } catch (e) { /* ignore */ } }, secs * 1000);
    toast(`Copied ${what || ""} — the clipboard will be cleared in ${secs} s`.replace("  ", " "));
  } else toast("Copied");
}

// ---------- auto-lock ----------
function startIdleWatch() {
  stopIdleWatch();
  state.lastActivity = Date.now();
  state.idleTimer = setInterval(() => {
    const left = state.autoLock * 60 - Math.floor((Date.now() - state.lastActivity) / 1000);
    const cd = $("#countdown");
    if (cd) cd.textContent = left > 0 ? `🔒 ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "";
    if (left <= 0) lock();
  }, 1000);
}
function stopIdleWatch() { clearInterval(state.idleTimer); state.idleTimer = null; }
["mousedown", "keydown", "touchstart", "scroll"].forEach((ev) => document.addEventListener(ev, () => { state.lastActivity = Date.now(); }, { passive: true }));
let pingAt = 0;
document.addEventListener("mousemove", () => {
  state.lastActivity = Date.now();
  if (state.token && Date.now() - pingAt > 60000) { pingAt = Date.now(); api("api/session").catch(() => {}); }  // keeps the server session in step
}, { passive: true });
function hideLockMs() {
  const s = state.me ? state.me.hideLockSeconds : -1;
  return s === undefined || s === null || s < 0 ? state.autoLock * 60000 : s * 1000;
}
document.addEventListener("visibilitychange", () => {
  if (!state.token) return;
  clearTimeout(state.hideTimer);
  if (document.hidden) {
    state.hiddenAt = Date.now();
    // "lock at once / after N s when I switch away" — a timer, since a hidden tab may never come back
    if (hideLockMs() < state.autoLock * 60000) state.hideTimer = setTimeout(() => { if (document.hidden) lock(); }, Math.max(hideLockMs(), 200));
  } else if (state.hiddenAt && Date.now() - state.hiddenAt > hideLockMs()) lock();
  else if (state.token) api("api/session").catch(() => {});
});
function lockLocal(reason) {
  state.token = null; state.vaults = []; state.folders = {}; state.items = []; state.detail = null; state.selected = null;
  state.picked = new Set(); state.q = ""; state.health = null; state.healthMap = new Map(); state.reminderDismissed = false;
  stopIdleWatch(); clearInterval(state.totpTimer); clearInterval(state.healthPoll);
  clear($("#modalRoot")); closeMenus();
  showUnlock(reason);
}
async function lock() {
  const t = state.token;
  if (t) { try { await api("api/lock", { method: "POST", noLock: true }); } catch (e) { /* already locked */ } }
  lockLocal();
}

// ---------- boot ----------
async function boot() {
  try { state.me = await api("api/me"); }
  catch (e) { mount($("#app"), h("div", { class: "center" }, h("div", { class: "card" }, h("h1", null, "Can't open Household Vault"), h("p", null, e.message)))); return; }
  if (state.me.disabled) return showNoAccess();
  if (state.me.status === "none") return showNotSetUp();
  showUnlock();
}
function httpBanner() {
  if (location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(location.hostname))
    return h("div", { class: "banner" }, "⚠ You're using Home Assistant over plain HTTP: your master password crosses your network unencrypted. Use HTTPS (e.g. Nabu Casa or a certificate) if you can.");
  return null;
}
// First run: with admin_users empty nobody can open Admin, so every page says how to fix it (never auto-promote).
function noAdminBanner() {
  if (!state.me || !state.me.noAdmin) return null;
  const whoami = () => (state.token && $("#shell") ? showPage("whoami") : showStandalone("whoami"));
  return h("div", { class: "banner no-admin", role: "status" },
    "No admin yet — add your Home Assistant user name (", h("strong", null, state.me.username || "see “How the app sees you”"),
    ") to ", h("code", null, "admin_users"), " on the app's Configuration tab, save, and restart the app. ",
    h("button", { class: "link-btn", type: "button", onclick: whoami }, "How the app sees you"));
}
function topBanners() { return [noAdminBanner(), httpBanner()]; }
// A password manager without an independent security review: said calmly, where people unlock and in Admin.
function experimentalNote() {
  return h("p", { class: "hint experimental" }, h("span", { class: "chip" }, "Experimental"),
    " Not independently security-reviewed yet. Keep your own KeePass copy (Settings → Download all my passwords) — don't make this your only copy.");
}
function adminLinks() {
  if (!state.me || !state.me.isAdmin) return null;
  return h("p", { class: "hint" }, "You're an admin: ", h("button", { class: "btn small", type: "button", onclick: () => showStandalone("admin") }, "🛡 Manage people"));
}
function showNoAccess() {
  mount($("#app"), topBanners(), h("div", { class: "center" }, h("div", { class: "card" },
    h("div", { class: "brand-big" }, "🔐"), h("h1", null, "Household Vault"),
    h("p", null, "Ask an admin to give you access. They'll set you up and hand you a one-time password."),
    adminLinks(),
    h("button", { class: "btn small", type: "button", onclick: () => showStandalone("whoami") }, "How the app sees you"),
    experimentalNote())));
}
function showNotSetUp() {
  mount($("#app"), topBanners(), h("div", { class: "center" }, h("div", { class: "card" },
    h("div", { class: "brand-big" }, "🔐"), h("h1", null, "Almost there"),
    h("p", null, "An admin needs to set up your vault. They'll give you a one-time password to open it the first time."),
    adminLinks(),
    h("button", { class: "btn small", type: "button", onclick: () => showStandalone("whoami") }, "How the app sees you"),
    experimentalNote())));
}
async function showStandalone(page) {
  const back = h("button", { class: "btn small", type: "button", onclick: () => boot() }, "← Back");
  const box = h("div", { class: "page standalone" }, back);
  mount($("#app"), topBanners(), box);
  try {
    if (page === "admin") box.appendChild(await adminPage());
    else if (page === "guest") box.appendChild(await guestPage());
    else box.appendChild(await whoamiPage());
  } catch (e) { box.appendChild(h("div", { class: "notice danger" }, e.message)); }
}

function showUnlock(reason) {
  const pw = pwInput({ placeholder: "Master password", label: "Master password" });
  const err = h("div", { class: "error", role: "alert" }, reason && reason !== "Locked." ? reason : "");
  const btn = h("button", { class: "btn primary", type: "submit" }, "Unlock");
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    if (!pw.input.value) return;
    btn.disabled = true; err.textContent = "";
    btn.textContent = "Unlocking…";
    try {
      const r = await api("api/unlock", { method: "POST", body: { password: pw.input.value } });
      pw.input.value = "";
      await afterUnlock(r);
    } catch (x) { err.textContent = x.message; btn.disabled = false; btn.textContent = "Unlock"; pw.input.select(); }
  } }, field("Master password", pw.el), err, h("div", { class: "actions" }, btn));
  const quickBox = h("div");
  if (quickSupported()) api("api/quick-unlock/options").then((o) => {
    if (o.available) mount(quickBox, h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: async (e) => {
      const b = e.currentTarget; b.disabled = true; err.textContent = "";
      try {
        const q = await quickUnlock(o);
        const r = await api("api/unlock", { method: "POST", body: { password: q.password, quickUnlockId: q.id } });
        await afterUnlock(r);
      } catch (x) { err.textContent = x.name === "NotAllowedError" ? "Cancelled — type your master password instead." : x.message; b.disabled = false; }
    } }, "🔓 Unlock with fingerprint / face")));
    else if (o.reason) mount(quickBox, h("p", { class: "hint" }, o.reason));
  }).catch(() => {});
  mount($("#app"), topBanners(), h("div", { class: "center" }, h("div", { class: "card" },
    h("div", { class: "brand-big" }, "🔐"), h("h1", null, "Household Vault"),
    h("p", { class: "hint" }, `Hi ${state.me.name}. Unlock to open your vaults.`),
    reason === "Locked." ? h("div", { class: "notice info" }, "Locked — unlock again to continue.") : null,
    form, quickBox, adminLinks(),
    state.me.guestWifi ? h("p", null, h("button", { class: "btn small", type: "button", onclick: () => showStandalone("guest") }, "📶 Guest Wi-Fi")) : null,
    h("p", { class: "hint" }, "Forgot your master password? It can't be recovered — an admin can reset you (your Personal vault is wiped; shared vaults are kept)."),
    experimentalNote())));
  setTimeout(() => pw.input.focus(), 30);
}

async function afterUnlock(r) {
  state.token = r.session;
  state.autoLock = r.autoLockMinutes || 5;
  if (r.mustChangePassword) return showChoosePassword();
  state.vaults = r.vaults;
  try { state.me = Object.assign(state.me, await api("api/me")); } catch (e) { /* keep what we have */ }
  startIdleWatch();
  await showMain(r);
}

// ---------- quick unlock with a passkey (SPEC §12.5): the PRF output never leaves this page ----------
const b64uEnc = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64uDec = (t) => { let x = t.replace(/-/g, "+").replace(/_/g, "/"); while (x.length % 4) x += "="; return Uint8Array.from(atob(x), (c) => c.charCodeAt(0)); };
function quickSupported() { return !!(window.isSecureContext && window.PublicKeyCredential && navigator.credentials && window.crypto && crypto.subtle); }
async function prfKey(prfOut, userId) {
  const base = await crypto.subtle.importKey("raw", prfOut, "HKDF", false, ["deriveKey"]);
  const enc = new TextEncoder();
  return crypto.subtle.deriveKey({ name: "HKDF", hash: "SHA-256", salt: enc.encode("household-vault"), info: enc.encode("quick-unlock-v1|" + userId) },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}
function prfFirst(cred) { const x = cred.getClientExtensionResults(); return x && x.prf && x.prf.results ? x.prf.results.first : null; }
async function setupQuickUnlock(master, label) {
  const b = await api("api/me/quick-unlock/begin", { method: "POST" });
  const salt = b64uDec(b.prfSalt);
  const cred = await navigator.credentials.create({ publicKey: {
    rp: { name: "Household Vault", id: location.hostname }, user: { id: b64uDec(b.userHandle), name: b.name, displayName: b.name },
    challenge: b64uDec(b.challenge), pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
    authenticatorSelection: { userVerification: "required", residentKey: "preferred" }, timeout: 120000,
    extensions: { prf: { eval: { first: salt } } } } });
  const ext = cred.getClientExtensionResults();
  if (!ext.prf || ext.prf.enabled === false) throw new Error("This device's passkeys can't do quick unlock (they need the PRF extension). Try Chrome or Edge, or a newer phone.");
  let out = prfFirst(cred);
  if (!out) {                                     // most authenticators only answer PRF when signing in
    const a = await navigator.credentials.get({ publicKey: { challenge: crypto.getRandomValues(new Uint8Array(32)), rpId: location.hostname,
      allowCredentials: [{ type: "public-key", id: cred.rawId }], userVerification: "required", timeout: 120000, extensions: { prf: { eval: { first: salt } } } } });
    out = prfFirst(a);
  }
  if (!out) throw new Error("This device's passkeys can't do quick unlock (no PRF result).");
  const key = await prfKey(out, state.me.id);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(state.me.id) }, key, new TextEncoder().encode(master));
  return api("api/me/quick-unlock", { method: "POST", body: { credentialId: b64uEnc(cred.rawId), label, wrapped: { iv: b64uEnc(iv), ct: b64uEnc(ct) }, password: master } });
}
async function quickUnlock(o) {
  const byId = {}, evalBy = {};
  for (const c of o.credentials) { byId[c.credentialId] = c; evalBy[c.credentialId] = { first: b64uDec(c.prfSalt) }; }
  const a = await navigator.credentials.get({ publicKey: { challenge: b64uDec(o.challenge), rpId: location.hostname,
    allowCredentials: o.credentials.map((c) => ({ type: "public-key", id: b64uDec(c.credentialId) })), userVerification: "required", timeout: 120000,
    extensions: { prf: { evalByCredential: evalBy } } } });
  const c = byId[b64uEnc(a.rawId)];
  const out = prfFirst(a);
  if (!c || !out) throw new Error("That passkey can't unlock here — type your master password.");
  const key = await prfKey(out, state.me.id);
  let pt;
  try { pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64uDec(c.wrapped.iv), additionalData: new TextEncoder().encode(state.me.id) }, key, b64uDec(c.wrapped.ct)); }
  catch (e) { throw new Error("Quick unlock didn't work on this device — type your master password (then set it up again)."); }
  return { password: new TextDecoder().decode(pt), id: c.id };
}
async function drawQuickUnlock(card) {
  let list = [];
  try { list = (await api("api/me/quick-unlock")).devices; } catch (e) { /* locked meanwhile */ }
  const guess = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) ? "My phone" : "My computer";
  const paint = () => mount(card, h("h3", null, "Quick unlock"),
    h("p", { class: "hint" }, "Unlock with your fingerprint, face or device PIN instead of typing your master password. Your master password is still needed every 14 days, and after it changes."),
    list.length ? dataTable(["Device", "Set up", "Last used", ""], list.map((d) => [d.label, fmtWhen(d.createdAt), d.lastUsed ? fmtWhen(d.lastUsed) : "—",
      h("button", { class: "btn small ghost", type: "button", onclick: async () => { try { list = (await api(`api/me/quick-unlock/${d.id}`, { method: "DELETE" })).devices; paint(); toast("Removed"); } catch (e) { fail(e); } } }, "Remove")]), "compact") : null,
    quickSupported() ? h("button", { class: "btn", type: "button", onclick: () => {
      const label = h("input", { type: "text", value: guess, maxlength: 60, "aria-label": "Name for this device" });
      const pw = pwInput({ label: "Master password" });
      const err = h("div", { class: "error" });
      const m = openModal("Quick unlock on this device", h("form", { onsubmit: async (e) => {
        e.preventDefault(); err.textContent = "";
        try { list = (await setupQuickUnlock(pw.input.value, label.value.trim() || guess)).devices; pw.input.value = ""; m.close(); paint(); toast("Quick unlock is set up on this device"); }
        catch (x) { err.textContent = x.name === "NotAllowedError" ? "Cancelled." : x.message; }
      } }, h("p", { class: "hint" }, "Your browser will ask for your fingerprint, face or PIN. Your master password is encrypted on this device with a key only the passkey can produce; the app stores just the encrypted copy."),
        field("Name for this device", label), field("Your master password", pw.el), err,
        h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Set up"))));
    } }, "🔓 Set up on this device") : h("p", { class: "hint" }, location.protocol !== "https:" ? "Needs Home Assistant over HTTPS." : "This browser doesn't support passkeys."));
  paint();
}
function showChoosePassword() {
  const min = state.me.minPasswordLength || 12;
  const a = pwInput({ autocomplete: "new-password", label: "New master password" });
  const b = pwInput({ autocomplete: "new-password", label: "Type it again" });
  const err = h("div", { class: "error", role: "alert" });
  const suggest = h("button", { class: "btn small", type: "button", onclick: async () => {
    const r = await api("api/generate", { method: "POST", body: { kind: "passphrase", words: 6 } });
    a.input.value = r.password; b.input.value = ""; a.input.type = "text"; a.input.dispatchEvent(new Event("input"));
  } }, "Suggest a passphrase");
  const btn = h("button", { class: "btn primary", type: "submit" }, "Save and continue");
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    if (a.input.value !== b.input.value) { err.textContent = "The two passwords don't match."; return; }
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const r = await api("api/me/activate", { method: "POST", body: { newPassword: a.input.value } });
      state.vaults = r.vaults;
      state.me = await api("api/me");
      showKit(true);
    } catch (x) { err.textContent = x.message; btn.disabled = false; btn.textContent = "Save and continue"; }
  } },
  h("p", null, "Choose your own master password. It opens everything in Household Vault — and ", h("strong", null, "nobody can recover it"), ", not even an admin. The admin will not know it."),
  h("p", { class: "hint" }, `At least ${min} characters. A passphrase of 5–6 random words is strong and easy to remember.`),
  field("New master password", a.el), strengthMeter(a.input), suggest, field("Type it again", b.el), err, h("div", { class: "actions" }, btn));
  mount($("#app"), topBanners(), h("div", { class: "center" }, h("div", { class: "card" }, h("h1", null, "Choose your master password"), form)));
}

function showKit(first) {
  const personal = state.vaults.find((v) => v.kind === "personal" && v.mine);
  const content = h("div", null,
    h("p", null, "Print this Emergency Kit and keep it somewhere safe. Write your master password on it by hand if you like — it's never stored anywhere."),
    h("div", { class: "row wrap" },
      h("button", { class: "btn", type: "button", onclick: () => printKit() }, "🖨 Print Emergency Kit"),
      personal ? h("button", { class: "btn", type: "button", onclick: () => downloadVault(personal) }, "⬇ Download Personal.kdbx") : null),
    h("p", { class: "hint" }, "The download is still locked with your master password; it opens in KeePassXC, KeePassDX or Strongbox."),
    first ? h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => { startIdleWatch(); showMain({}); } }, "Open my vault")) : null);
  if (first) mount($("#app"), h("div", { class: "center" }, h("div", { class: "card" }, h("h1", null, "✅ You're set up"), content)));
  else openModal("Emergency Kit", content);
}
function printKit() {
  const root = $("#printRoot");
  mount(root, h("div", { class: "kit" },
    h("h1", null, "Household Vault — Emergency Kit"),
    h("p", null, `For: ${state.me.name}`),
    h("p", null, `Made: ${new Date().toLocaleDateString()}`),
    h("div", { class: "box" }, h("strong", null, "Master password"), h("div", { class: "line" }), h("div", { class: "line" })),
    h("h3", null, "If you need your passwords outside Home Assistant"),
    h("ol", null, h("li", null, "Open Household Vault in Home Assistant → Settings → Download all my passwords (or the ⬇ on your Personal vault)."),
      h("li", null, "Open the .kdbx file in KeePassXC (computer), KeePassDX (Android) or Strongbox (iPhone)."),
      h("li", null, "Enter your master password.")),
    h("p", null, "Nobody can recover a forgotten master password — keep this sheet safe.")));
  printNow();
}
function printNow() {
  document.documentElement.classList.add("printing");
  const done = () => { document.documentElement.classList.remove("printing"); clear($("#printRoot")); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  setTimeout(() => { window.print(); setTimeout(done, 1000); }, 100);
}
async function downloadVault(v) {
  try {
    const res = await api(`api/vaults/${v.id}/download`, { raw: true });
    saveBlob(await res.blob(), filenameFrom(res) || "vault.kdbx");
  } catch (e) { fail(e); }
}
function filenameFrom(res) {
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename="([^"]+)"/.exec(cd);
  return m ? m[1] : null;
}
function saveBlob(blob, name) {
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

// ---------- main shell ----------
async function refreshVaults() {
  const r = await api("api/vaults");
  state.vaults = r.vaults;
  await Promise.all(state.vaults.filter((v) => v.open).map(async (v) => {
    try { state.folders[v.id] = await api(`api/vaults/${v.id}/folders`); } catch (e) { /* locked meanwhile */ }
  }));
  for (const id of Object.keys(state.folders)) if (!state.vaults.some((v) => v.id === id && v.open)) delete state.folders[id];
  try { state.tags = (await api("api/tags")).tags; } catch (e) { state.tags = []; }
}
function vaultById(id) { return state.vaults.find((v) => v.id === id); }
function vaultIcon(v) { return v.kind === "personal" ? (v.mine ? "👤" : "👥") : v.kind === "household" ? "🏠" : v.kind === "emergency" ? "🆘" : "🗄"; }
function canEdit(v) { return v && ["owner", "manager", "editor"].includes(v.role); }
function canManage(v) { return v && ["owner", "manager"].includes(v.role); }

async function showMain(report) {
  state.page = "vault";
  await refreshVaults();
  const shell = h("div", { class: "shell", id: "shell" },
    h("aside", { class: "sidebar", id: "sidebar", "aria-label": "Vaults and folders" }),
    h("div", { class: "main" },
      topBanners(),
      h("div", { class: "topbar" },
        h("button", { class: "icon-btn menu-btn", type: "button", "aria-label": "Menu", onclick: toggleNav }, "☰"),
        searchBox(),
        h("span", { class: "countdown", id: "countdown", title: "Locks when you're idle" }),
        h("button", { class: "btn small lock-btn", type: "button", onclick: lock, title: "Lock now", "aria-label": "Lock" }, "🔒", h("span", { class: "lbl-text" }, " Lock"))),
      h("div", { id: "notices" }),
      h("div", { id: "content", class: "panes" })));
  mount($("#app"), shell);
  renderSidebar();
  if (report && report.notices && report.notices.length) {
    const box = h("div", { class: "notice info" }, h("strong", null, "Since you last unlocked: "),
      h("ul", null, report.notices.map((n) => h("li", null, n.text))),
      h("button", { class: "btn small", type: "button", onclick: () => clear($("#notices")) }, "OK"));
    mount($("#notices"), box);
    if (report.notices.some((n) => n.action === "backup_restored")) restoreNotes(box);
  }
  if (report && report.couldNotOpen && report.couldNotOpen.length)
    toast(`Couldn't open ${report.couldNotOpen.join(", ")} — its password changed. Open it with the new one.`, { ms: 7000 });
  const pending = state.vaults.filter((v) => v.passwordChangeSuggested && v.role === "owner");
  if (pending.length) $("#notices").appendChild(h("div", { class: "notice" }, `Someone who knew the password of ${pending.map((v) => v.name).join(", ")} lost access — change it (vault ⋯ → Change vault password).`));
  const rem = reminderNotice();
  if (rem) $("#notices").appendChild(rem);
  emergencyNotice();
  await setView(state.view.kind ? state.view : { kind: "all" });
  refreshHealth();
}
function undoneList(undone) {
  return h("ul", null, undone.map((x) => h("li", null, `${x.action.replace("_", " ")} — ${x.person || ""} ${x.vault ? "(" + x.vault + ")" : ""}`)));
}
async function restoreNotes(box) {
  // after an admin restored a backup: what it undid, so it can be redone (SPEC §4.2 #11)
  try {
    const r = await api("api/restore-notes");
    const undone = (r.notes && r.notes.undone) || [];
    if (undone.length) box.insertBefore(h("div", null, h("p", null, "The restore undid these — redo them if they still apply:"), undoneList(undone)), box.lastChild);
  } catch (e) { /* not important enough to interrupt */ }
}
function reminderNotice() {
  const me = state.me;
  if (!me || !me.downloadReminderDays || state.reminderDismissed) return null;
  const last = me.lastDownloadAll ? new Date(me.lastDownloadAll) : null;
  const days = last ? Math.floor((Date.now() - last) / 86400000) : null;
  const stale = me.lastMasterChange && last && new Date(me.lastMasterChange) > last;
  let text = null;
  if (!last) text = "You haven't downloaded a copy of your passwords yet.";
  else if (stale) text = "Your downloaded copy still opens with your old master password — download a new one and delete the old copies.";
  else if (days >= me.downloadReminderDays) text = `Your downloaded copy of your passwords is ${days} days old.`;
  if (!text) return null;
  const box = h("div", { class: "notice info row wrap" }, h("span", { class: "grow" }, "⬇ " + text),
    h("button", { class: "btn small primary", type: "button", onclick: () => showPage("settings", "download") }, "Download now"),
    h("button", { class: "btn small ghost", type: "button", onclick: () => { state.reminderDismissed = true; box.remove(); } }, "Later"));
  return box;
}
async function emergencyNotice() {
  let r;
  try { r = await api("api/emergency"); } catch (e) { return; }
  const box = $("#notices");
  if (!box) return;
  for (const c of r.mine.contacts.filter((x) => x.status === "requested")) {
    const n = h("div", { class: "notice danger row wrap" },
      h("span", { class: "grow" }, `🆘 ${c.name} asked for your emergency items. Unless you deny it, they get them on ${fmtWhen(c.releaseAt)}.`),
      h("button", { class: "btn small danger", type: "button", onclick: async () => { try { await api(`api/emergency/contacts/${c.id}/deny`, { method: "POST" }); n.remove(); toast("Denied"); } catch (e) { fail(e); } } }, "Deny"),
      h("button", { class: "btn small", type: "button", onclick: async () => {
        if (!await confirmDialog("Give access now", `Give ${c.name} your emergency items now, without waiting?`, "Give access")) return;
        try { await api(`api/emergency/contacts/${c.id}/approve`, { method: "POST" }); n.remove(); toast("Access given"); } catch (e) { fail(e); } } }, "Approve now"));
    box.appendChild(n);
  }
}
function toggleNav() { state.navOpen = !state.navOpen; $("#shell").classList.toggle("nav-open", state.navOpen); }
function closeNav() { if (state.navOpen) toggleNav(); }

function navItem(icon, label, active, onclick, count, extra) {
  return h("button", { class: "nav-item" + (active ? " active" : ""), type: "button", onclick },
    h("span", { class: "ico", "aria-hidden": "true" }, icon), h("span", { class: "nm" }, label),
    count !== undefined && count !== null ? h("span", { class: "count" }, String(count)) : null, extra || null);
}
function isView(kind, vaultId, folderId, type) {
  if (state.page !== "vault") return false;
  const v = state.view;
  return v.kind === kind && (vaultId === undefined || v.vaultId === vaultId) && (folderId === undefined || (v.folderId || null) === (folderId || null)) && (type === undefined || v.type === type);
}
function renderSidebar() {
  const sb = $("#sidebar");
  if (!sb) return;
  const theme = h("select", { "aria-label": "Theme" }, ...[["vault", "🔐 Vault"], ["slate", "🌆 Slate"], ["daylight", "☀️ Daylight"], ["auto", "🌓 Auto"]].map(([k, l]) => h("option", { value: k }, l)));
  theme.value = window.__themeChoice || "vault";
  theme.addEventListener("change", () => { window.__themeChoice = theme.value; lsSet("theme", theme.value); document.documentElement.setAttribute("data-theme", window.__resolveTheme(theme.value)); });
  const vaultBlocks = state.vaults.map((v) => {
    const f = state.folders[v.id];
    const expanded = state.expanded[v.id] !== false;
    const head = h("div", { class: "vault-head" },
      v.open ? h("button", { class: "twisty", type: "button", "aria-label": expanded ? "Collapse" : "Expand", onclick: () => { state.expanded[v.id] = !expanded; renderSidebar(); } }, expanded ? "▾" : "▸") : h("span", { class: "twisty" }),
      navItem(v.open ? vaultIcon(v) : "🔒", v.name, isView("folder", v.id, null), () => v.open ? setView({ kind: "folder", vaultId: v.id, folderId: null }) : openLockedVault(v),
        v.open && f ? f.rootCount + f.folders.reduce((a, x) => a + x.count, 0) : null),
      h("button", { class: "icon-btn", type: "button", "aria-label": `${v.name} options`, onclick: (e) => vaultMenu(e.currentTarget, v) }, "⋯"));
    if (v.open) dropTarget(head, { vaultId: v.id, folderId: null, name: v.name });
    if (!v.open || !f || !expanded) return h("div", null, head);
    const tree = h("div", { class: "folder-tree" });
    const kids = (pid) => f.folders.filter((x) => x.parentId === pid);
    const collapsed = (fid) => state.expanded[v.id + ":" + fid] === false;
    const walk = (pid, depth) => {
      for (const fo of kids(pid)) {
        const hasKids = kids(fo.id).length > 0;
        const row = h("div", { class: "vault-head" },
          h("span", { class: "twisty" }, ""),
          navItem(hasKids ? (collapsed(fo.id) ? "📁" : "📂") : "📁", fo.name, isView("folder", v.id, fo.id), () => setView({ kind: "folder", vaultId: v.id, folderId: fo.id }), fo.count || null),
          canEdit(v) ? h("button", { class: "icon-btn", type: "button", "aria-label": `${fo.name} options`, onclick: (e) => folderMenu(e.currentTarget, v, fo) }, "⋯") : null);
        row.firstChild.textContent = hasKids ? (collapsed(fo.id) ? "▸" : "▾") : "";
        if (hasKids) { row.firstChild.classList.add("clickable"); row.firstChild.addEventListener("click", () => { state.expanded[v.id + ":" + fo.id] = collapsed(fo.id); renderSidebar(); }); }
        row.classList.add("indent-" + Math.min(depth, 6));
        dropTarget(row, { vaultId: v.id, folderId: fo.id, name: fo.name });
        if (FINE_POINTER && canEdit(v)) {
          row.setAttribute("draggable", "true");
          row.addEventListener("dragstart", (e) => startDrag(e, { kind: "folder", vaultId: v.id, folder: fo, folders: f.folders }, fo.name));
          row.addEventListener("dragend", dragEnd);
        }
        row.firstChild.setAttribute("data-depth", depth);
        tree.appendChild(row);
        pad(row, depth);
        if (!collapsed(fo.id)) walk(fo.id, depth + 1);
      }
    };
    walk(null, 1);
    if (f.trashCount || canEdit(v)) {
      const t = h("div", { class: "vault-head trash-row" }, h("span", { class: "twisty" }), navItem("🗑", "Trash", isView("trash", v.id), () => setView({ kind: "trash", vaultId: v.id }), f.trashCount || null));
      pad(t, 1);
      if (canEdit(v)) dropTarget(t, { vaultId: v.id, trash: true, name: "Trash" });
      tree.appendChild(t);
    }
    return h("div", null, head, tree);
  });
  mount(sb,
    h("div", { class: "side-top" }, h("span", { "aria-hidden": "true" }, "🔐"), h("span", { class: "brand" }, "Household Vault")),
    h("div", { class: "side-scroll" },
      navItem("🗂", "All items", isView("all"), () => setView({ kind: "all" })),
      navItem("⭐", "Favourites", isView("fav"), () => setView({ kind: "fav" })),
      navItem("🕘", "Recently used", isView("recent"), () => setView({ kind: "recent" })),
      navItem("🗓", "Expiring", isView("expiring"), () => setView({ kind: "expiring" }), state.expiringCount || null),
      navItem("🩺", "Password health", state.page === "health", () => showPage("health"), state.health && state.health.items.length ? state.health.items.length : null),
      state.me.guestWifi ? navItem("📶", "Guest Wi-Fi", state.page === "guest", () => showPage("guest")) : null,
      h("div", { class: "nav-sec" }, h("span", { class: "grow" }, "Vaults"),
        h("button", { class: "icon-btn", type: "button", title: "New shared vault", "aria-label": "New shared vault", onclick: newVaultDialog }, "＋")),
      vaultBlocks,
      state.tags && state.tags.length ? [
        h("div", { class: "nav-sec" }, h("span", { class: "grow" }, "Tags"),
          h("button", { class: "icon-btn", type: "button", "aria-label": state.expanded.__tags === false ? "Show tags" : "Hide tags", onclick: () => { state.expanded.__tags = state.expanded.__tags === false; renderSidebar(); } }, state.expanded.__tags === false ? "▸" : "▾")),
        state.expanded.__tags === false ? null : state.tags.slice(0, 30).map((t) => navItem("🏷", t.tag, state.page === "vault" && state.view.kind === "tag" && state.view.tag === t.tag, () => setView({ kind: "tag", tag: t.tag }), t.count))] : null,
      h("div", { class: "nav-sec" }, "Types"),
      navItem("🔑", "Logins", isView("type", undefined, undefined, "login"), () => setView({ kind: "type", type: "login" })),
      navItem("⏱", "2FA codes", isView("type", undefined, undefined, "2fa"), () => setView({ kind: "type", type: "2fa" })),
      navItem("📝", "Secure notes", isView("type", undefined, undefined, "note"), () => setView({ kind: "type", type: "note" })),
      navItem("💳", "Cards", isView("type", undefined, undefined, "card"), () => setView({ kind: "type", type: "card" }))),
    h("div", { class: "side-foot" },
      navItem("⚙️", "Settings", state.page === "settings", () => showPage("settings")),
      state.me.isAdmin ? navItem("🛡", "Admin", state.page === "admin", () => showPage("admin")) : null,
      navItem("👤", state.me.name, state.page === "whoami", () => showPage("whoami")),
      theme));
}
function pad(row, depth) { row.style.paddingLeft = (depth * 12) + "px"; }

// ---------- views ----------
function viewTitle() {
  const v = state.view;
  if (v.kind === "all") return "All items";
  if (v.kind === "fav") return "Favourites";
  if (v.kind === "recent") return "Recently used";
  if (v.kind === "expiring") return "Expired or expiring in 90 days";
  if (v.kind === "tag") return `🏷 ${v.tag}`;
  if (v.kind === "type") return { login: "Logins", "2fa": "2FA codes", note: "Secure notes", card: "Cards" }[v.type];
  if (v.kind === "search") return "Search";
  const vault = vaultById(v.vaultId);
  if (v.kind === "trash") return `Trash — ${vault ? vault.name : ""}`;
  if (v.folderId) { const f = (state.folders[v.vaultId] || { folders: [] }).folders.find((x) => x.id === v.folderId); return f ? f.name : "Folder"; }
  return vault ? vault.name : "";
}
async function setView(view) {
  state.page = "vault";
  state.view = view;
  state.picked = new Set();
  closeNav();
  if (view.kind !== "search") { state.q = ""; const sb = $("#search"); if (sb) sb.value = ""; }
  renderSidebar();
  await loadItems();
}
async function loadItems() {
  const v = state.view;
  let path;
  if (v.kind === "all") path = "api/items";
  else if (v.kind === "fav") path = "api/items?favourites=true";
  else if (v.kind === "recent") path = "api/items?recent=true";
  else if (v.kind === "expiring") path = "api/expiring?within=90";
  else if (v.kind === "tag") path = "api/items?tag=" + encodeURIComponent(v.tag);
  else if (v.kind === "type") path = v.type === "2fa" ? "api/search?q=has:2fa" : `api/items?type=${v.type}`;
  else if (v.kind === "trash") path = `api/vaults/${v.vaultId}/items?trash=true`;
  else if (v.kind === "folder") path = `api/vaults/${v.vaultId}/items${v.folderId ? "?folder=" + v.folderId : ""}`;
  else if (v.kind === "search") {
    const p = new URLSearchParams({ q: state.q });
    if (v.scope === "vault" || v.scope === "folder") p.set("vault", v.vaultId);
    if (v.scope === "folder" && v.folderId) p.set("folder", v.folderId);
    path = "api/search?" + p.toString();
  }
  try {
    const r = await api(path);
    state.items = r.items;
    state.lockedInSearch = r.lockedVaults || [];
    state.trashFolders = r.folders || [];
  } catch (e) {
    if (e.status === 423) { state.items = []; }
    else if (e.status !== 401) fail(e);
  }
  renderPanes();
}

function renderPanes() {
  const c = $("#content");
  if (!c) return;
  c.className = "panes" + (state.selected ? " show-detail" : "");
  mount(c, listPane(), h("section", { class: "detail-pane", id: "detail", "aria-label": "Item" }, state.selected ? spinnerBox() : emptyDetail()));
  if (state.selected) showDetail(state.selected.vaultId, state.selected.id);
}
function isSelected(vaultId, id) { return !!(state.selected && state.selected.vaultId === vaultId && state.selected.id === id); }
function closeDetail() {
  // also empties the pane, so nothing shown in it (a revealed password, a 2FA code) stays in the page
  state.selected = null; state.detail = null;
  clearInterval(state.totpTimer);
  document.querySelectorAll(".item-row.active").forEach((r) => r.classList.remove("active"));
  const c = $("#content");
  if (c) c.classList.remove("show-detail");
  const pane = $("#detail");
  if (pane) mount(pane, emptyDetail());
}
function spinnerBox() { return h("div", { class: "empty" }, "…"); }
function emptyDetail() {
  return h("div", { class: "empty" }, h("div", { class: "brand-big" }, "🔐"), h("p", null, "Choose an item, or search with / or Ctrl+K."));
}
function currentVault() { return state.view.vaultId ? vaultById(state.view.vaultId) : null; }

function listPane() {
  const v = state.view;
  const vault = currentVault();
  const crumbs = h("div", { class: "crumbs" });
  if ((v.kind === "folder") && vault) {
    const f = state.folders[vault.id] || { folders: [] };
    const chain = [];
    let cur = v.folderId ? f.folders.find((x) => x.id === v.folderId) : null;
    while (cur) { chain.unshift(cur); cur = cur.parentId ? f.folders.find((x) => x.id === cur.parentId) : null; }
    crumbs.appendChild(h("button", { type: "button", onclick: () => setView({ kind: "folder", vaultId: vault.id, folderId: null }) }, vault.name));
    for (const x of chain) { crumbs.appendChild(document.createTextNode("›")); crumbs.appendChild(h("button", { type: "button", onclick: () => setView({ kind: "folder", vaultId: vault.id, folderId: x.id }) }, x.name)); }
  }
  const editable = v.kind === "folder" ? canEdit(vault) : MULTI_VIEWS.includes(v.kind);
  const newBtn = editable ? h("button", { class: "btn small primary", type: "button", onclick: (e) => newItemMenu(e.currentTarget) }, "+ New") : null;
  const folderBtn = v.kind === "folder" && canEdit(vault) ? h("button", { class: "btn small", type: "button", onclick: () => newFolderDialog(vault, v.folderId) }, "+ Folder") : null;
  const trashBtns = v.kind === "trash" && canEdit(vault) && state.items.length ? h("button", { class: "btn small danger", type: "button", onclick: async () => {
    if (!await confirmDialog("Empty the Trash", "Delete everything in this vault's Trash for good? This can't be undone (older versions are kept for a while).", "Empty Trash", true)) return;
    try { await api(`api/vaults/${vault.id}/trash/empty`, { method: "POST" }); await afterChange(); } catch (e) { fail(e); }
  } }, "Empty Trash") : null;
  let scope = null;
  if (v.kind === "search" && v.vaultId) {
    const chips = [["all", "All vaults"], ["vault", "This vault"]].concat(v.folderId ? [["folder", "This folder"]] : []);
    scope = h("div", { class: "row wrap" }, ...chips.map(([k, l]) => h("button", { class: "chip" + ((v.scope || "all") === k ? " on" : ""), type: "button", onclick: () => { state.view.scope = k; loadItems(); } }, l)));
  }
  const bulk = state.picked.size ? h("div", { class: "bulk" }, h("strong", null, `${state.picked.size} selected`),
    state.picked.size < state.items.length ? h("button", { class: "btn small ghost", type: "button", onclick: () => { state.items.forEach((x) => state.picked.add(x.vaultId + ":" + x.id)); renderPanes(); } }, `Select all ${state.items.length}`) : null,
    h("span", { class: "spacer" }),
    h("button", { class: "btn small", type: "button", onclick: () => bulkMove(false) }, "Move to…"),
    h("button", { class: "btn small", type: "button", onclick: () => bulkMove(true) }, "Copy to…"),
    h("button", { class: "btn small danger", type: "button", onclick: bulkDelete }, "Delete"),
    h("button", { class: "btn small ghost", type: "button", onclick: () => { state.picked = new Set(); renderPanes(); } }, "✕")) : null;
  const rows = state.items.map((it) => itemRow(it));
  const trashFolders = v.kind === "trash" ? (state.trashFolders || []).map((f) => h("div", { class: "item-row" },
    h("span", { class: "avatar av7" }, "📁"), h("div", { class: "grow" }, h("div", { class: "t" }, f.name), h("div", { class: "s" }, "Folder")),
    canEdit(vault) ? h("button", { class: "btn small", type: "button", onclick: async () => { try { await api(`api/vaults/${vault.id}/restore/${f.id}`, { method: "POST" }); toast("Restored"); await afterChange(); } catch (e) { fail(e); } } }, "Restore") : null)) : [];
  let emptyText = "Nothing here yet.";
  if (v.kind === "search") emptyText = state.q ? "No matches." : "Type to search.";
  if (v.kind === "fav") emptyText = "Mark items with ⭐ to see them here.";
  if (v.kind === "recent") emptyText = "Items you open appear here.";
  if (v.kind === "expiring") emptyText = "Nothing expires in the next 90 days. Give items an expiry date (cards, passports, insurance…) to be reminded.";
  if (v.kind === "trash") emptyText = "The Trash is empty.";
  const locked = state.lockedInSearch && state.lockedInSearch.length && v.kind === "search" ?
    h("div", { class: "notice" }, `${state.lockedInSearch.join(", ")} ${state.lockedInSearch.length === 1 ? "is" : "are"} locked — open to search there.`) : null;
  return h("section", { class: "list-pane", "aria-label": "Items" },
    h("div", { class: "list-head" },
      crumbs.childNodes.length > 1 ? crumbs : null,
      h("div", { class: "row" }, h("div", { class: "list-title grow" }, viewTitle()), folderBtn, newBtn, trashBtns,
        v.kind === "folder" && v.folderId && canEdit(vault) ? h("button", { class: "icon-btn", type: "button", "aria-label": "Folder options", onclick: (e) => folderMenu(e.currentTarget, vault, (state.folders[vault.id] || { folders: [] }).folders.find((x) => x.id === v.folderId)) }, "⋯") : null),
      scope),
    bulk, locked,
    h("div", { class: "list", role: "list" }, trashFolders, rows.length || trashFolders.length ? rows : h("div", { class: "empty" }, emptyText)));
}
function newItemMenu(anchor) {
  const v = state.view;
  const first = v.kind === "type" && v.type !== "2fa" ? v.type : null;
  const keys = Object.keys(TEMPLATES);
  menu(anchor, (first ? [first, ...keys.filter((k) => k !== first)] : keys).map((k) => ({ label: `${TEMPLATES[k].icon} ${TEMPLATES[k].label}`, run: () => itemEditor(null, k) })));
}
function itemRow(it) {
  const active = state.selected && state.selected.id === it.id && state.selected.vaultId === it.vaultId;
  const key = it.vaultId + ":" + it.id;
  const box = h("input", { type: "checkbox", class: "sel", "aria-label": `Select ${it.title}`, checked: state.picked.has(key) });
  box.addEventListener("click", (e) => e.stopPropagation());
  box.addEventListener("click", (e) => {
    const idx = state.items.indexOf(it);
    if (e.shiftKey && state.lastPicked !== null && state.lastPicked < state.items.length) {
      const [a, b] = [Math.min(idx, state.lastPicked), Math.max(idx, state.lastPicked)];
      for (let i = a; i <= b; i++) { const x = state.items[i]; if (box.checked) state.picked.add(x.vaultId + ":" + x.id); else state.picked.delete(x.vaultId + ":" + x.id); }
    }
    state.lastPicked = idx;
  });
  box.addEventListener("change", () => { if (box.checked) state.picked.add(key); else state.picked.delete(key); renderPanes(); });
  const where = MULTI_VIEWS.includes(state.view.kind) ? [it.vaultName, ...it.folderPath].join(" › ") : null;
  const sub = [it.type === "card" && it.cardLast4 ? "•••• " + it.cardLast4 : it.username || it.host, where].filter(Boolean).join(" · ");
  const issues = state.healthMap.get(key) || [];
  const row = h("div", { class: "item-row" + (active ? " active" : ""), role: "listitem", tabindex: "0", "data-key": key,
    onclick: () => selectItem(it), onkeydown: (e) => { if (e.key === "Enter") selectItem(it); } },
    box, avatar(it.title),
    h("div", { class: "grow" }, h("div", { class: "t" }, it.title || "(no name)", it.favourite ? " ⭐" : "", it.hasTotp ? " ⏱" : ""), sub ? h("div", { class: "s" }, sub) : null),
    issues.length || expiryChip(it.expires) ? h("span", { class: "badges" }, expiryChip(it.expires), issues.slice(0, 2).map((k) => issueChip(k))) : null,
    h("span", { class: "hint", "aria-hidden": "true" }, iconOf(it)));
  if (FINE_POINTER && !it.inTrash && state.view.kind !== "trash") {
    row.setAttribute("draggable", "true");
    row.addEventListener("dragstart", (e) => {
      const list = state.picked.has(key) ? pickedItems() : [it];
      startDrag(e, { kind: "items", list }, list.length === 1 ? list[0].title : `${list.length} items`);
    });
    row.addEventListener("dragend", dragEnd);
  }
  return row;
}
const ISSUE_LABEL = { breached: "breached", reused: "reused", weak: "weak", old: "old", no2fa: "no 2FA" };
function issueChip(k) { return h("span", { class: "chip issue " + k, title: { breached: "Found in known data breaches", reused: "Used on more than one item", weak: "Easy to guess", old: "Not changed for over a year", no2fa: "This site offers 2FA codes — turn them on" }[k] }, ISSUE_LABEL[k]); }
function selectItem(it) {
  state.selected = { vaultId: it.vaultId, id: it.id, inTrash: it.inTrash || state.view.kind === "trash" };
  document.querySelectorAll(".item-row.active").forEach((r) => r.classList.remove("active"));
  const row = document.querySelector(`.item-row[data-key="${it.vaultId}:${it.id}"]`);
  if (row) row.classList.add("active");
  $("#content").classList.add("show-detail");
  showDetail(it.vaultId, it.id);
}
async function afterChange(keepSelection = true) {
  await refreshVaults();
  renderSidebar();
  if (!keepSelection) state.selected = null;
  if (state.page === "vault") await loadItems();      // on Settings / Health the page stays as it is
  refreshHealth();
}

// ---------- search ----------
function searchBox() {
  const narrow = window.matchMedia && window.matchMedia("(max-width: 820px)").matches;
  const input = h("input", { type: "search", id: "search", placeholder: narrow ? "Search" : "Search all vaults   ( / )", "aria-label": "Search", autocomplete: "off", spellcheck: "false" });
  input.value = state.q;
  const run = debounce(() => {
    state.q = input.value.trim();
    if (!state.q) { if (state.view.kind === "search") setView(state.view.from || { kind: "all" }); return; }
    if (state.view.kind !== "search") state.view = { kind: "search", from: state.view, vaultId: state.view.vaultId, folderId: state.view.folderId, scope: "all" };
    state.selected = null;
    loadItems();
  }, 150);
  input.addEventListener("input", run);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { input.value = ""; run(); input.blur(); }
    if (e.key === "ArrowDown") { e.preventDefault(); const r = document.querySelector(".item-row"); if (r) r.focus(); }
    if (e.key === "Enter") { const first = state.items[0]; if (first) selectItem(first); }
  });
  return h("div", { class: "search-wrap" }, h("span", { class: "mag", "aria-hidden": "true" }, "🔍"), input);
}
document.addEventListener("keydown", (e) => {
  const tag = (e.target && e.target.tagName) || "";
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(tag);
  if (!state.token) return;
  if ((e.key === "/" && !typing) || (e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey))) {
    const s = $("#search"); if (s) { e.preventDefault(); s.focus(); s.select(); }
  }
  if (!typing && (e.key === "ArrowDown" || e.key === "ArrowUp") && e.target.classList && e.target.classList.contains("item-row")) {
    e.preventDefault();
    const sib = e.key === "ArrowDown" ? e.target.nextElementSibling : e.target.previousElementSibling;
    if (sib && sib.classList.contains("item-row")) sib.focus();
  }
});

// ---------- item detail ----------
async function showDetail(vaultId, id) {
  const pane = $("#detail");
  clearInterval(state.totpTimer);
  let d;
  try { d = await api(`api/vaults/${vaultId}/items/${id}`); }
  catch (e) { if (e.status === 404) { state.selected = null; mount(pane, emptyDetail()); } else fail(e); return; }
  if (!isSelected(vaultId, id)) return;      // closed (or another item chosen) while loading
  state.detail = d;
  const inTrash = d.inTrash;
  const secretRow = (label, fieldName, set, mono = true) => {
    const val = h("span", { class: "v" + (mono ? " mono" : "") }, set ? "••••••••••" : h("span", { class: "hint" }, "—"));
    let shown = false;
    const eye = h("button", { class: "icon-btn", type: "button", title: "Show", "aria-label": `Show ${label}`, onclick: async () => {
      if (shown) { val.textContent = "••••••••••"; shown = false; return; }
      try { const r = await api(`api/vaults/${vaultId}/items/${id}/secret?field=${encodeURIComponent(fieldName)}`); val.textContent = r.value; shown = true; }
      catch (e) { fail(e); }
    } }, "👁");
    const copy = h("button", { class: "icon-btn", type: "button", title: "Copy", "aria-label": `Copy ${label}`, onclick: async () => {
      try { const r = await api(`api/vaults/${vaultId}/items/${id}/secret?field=${encodeURIComponent(fieldName)}`); copyText(r.value, label.toLowerCase()); }
      catch (e) { fail(e); }
    } }, "📋");
    return h("div", { class: "frow" }, h("span", { class: "k" }, label), val, set ? h("span", { class: "acts" }, eye, copy) : h("span"));
  };
  const plainRow = (label, value, opts = {}) => value ? h("div", { class: "frow" }, h("span", { class: "k" }, label),
    opts.link ? h("a", { class: "v", href: safeUrl(value), target: "_blank", rel: "noopener noreferrer" }, value) : h("span", { class: "v" }, value),
    h("span", { class: "acts" }, opts.link ? h("a", { class: "icon-btn", href: safeUrl(value), target: "_blank", rel: "noopener noreferrer", title: "Open site", "aria-label": "Open site" }, "↗") : null,
      h("button", { class: "icon-btn", type: "button", title: "Copy", "aria-label": `Copy ${label}`, onclick: () => copyText(value, "plain") }, "📋"))) : null;
  const rows = [];
  if (d.type === "login") {
    rows.push(plainRow("Username", d.username));
    rows.push(secretRow("Password", "Password", d.password.set));
    rows.push(plainRow("Website", d.url, { link: true }));
  } else if (d.type === "card") {
    rows.push(plainRow("Name on card", d.cardholder));
    rows.push(secretRow("Number", "Number", d.card.number.set));
    rows.push(plainRow("Expiry", d.expiry));
    rows.push(secretRow("CVV", "CVV", d.card.cvv.set));
    rows.push(secretRow("PIN", "PIN", d.card.pin.set));
    rows.push(plainRow("Website", d.url, { link: true }));
  } else {
    rows.push(plainRow("Website", d.url, { link: true }));
  }
  if (d.template === "wifi" && rows[0]) rows[0].querySelector(".k").textContent = "Network";
  if (d.expires) {
    const t = TEMPLATES[d.template] || TEMPLATES[d.type] || {};
    rows.push(h("div", { class: "frow" }, h("span", { class: "k" }, t.expires || "Expires"),
      h("span", { class: "v row wrap" }, fmtDay(d.expires), expiryChip(d.expires, 3650)),
      h("span", { class: "acts hint" }, d.remindDays ? `🔔 ${d.remindDays} d before` : "🔕")));
  }
  let totpBox = null;
  if (d.totp) {
    const code = h("span", { class: "totp-code mono" }, "······");
    const ringFg = svgEl("circle", { cx: 11, cy: 11, r: 9, fill: "none", stroke: "var(--accent)", "stroke-width": 3, "stroke-dasharray": "56.5", "stroke-dashoffset": "0", transform: "rotate(-90 11 11)" });
    const ring = svgEl("svg", { class: "ring", viewBox: "0 0 22 22", "aria-hidden": "true" }, svgEl("circle", { cx: 11, cy: 11, r: 9, fill: "none", stroke: "var(--panel-hover)", "stroke-width": 3 }), ringFg);
    let cur = null, remaining = 0, period = d.totp.period;
    const refresh = async () => {
      try { const r = await api(`api/vaults/${vaultId}/items/${id}/totp`); cur = r.code; remaining = r.remaining; period = r.period;
        code.textContent = cur.replace(/(\d{3})(\d+)/, "$1 $2"); } catch (e) { clearInterval(state.totpTimer); }
    };
    await refresh();
    if (!isSelected(vaultId, id)) return;
    state.totpTimer = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) refresh();
      ringFg.setAttribute("stroke-dashoffset", String(56.5 * (1 - Math.max(remaining, 0) / period)));
    }, 1000);
    totpBox = h("div", { class: "frow" }, h("span", { class: "k" }, "2FA code"), h("span", { class: "v row" }, code, ring),
      h("span", { class: "acts" }, h("button", { class: "icon-btn", type: "button", title: "Copy code", "aria-label": "Copy 2FA code", onclick: () => cur && copyText(cur, "code") }, "📋")));
  }
  const custom = d.fields.map((f) => f.protected ? secretRow(f.name, f.name, true) : plainRow(f.name, f.value));
  const vault = vaultById(vaultId);
  const editable = canEdit(vault) && d.canEdit;
  const actions = h("div", { class: "row wrap" },
    h("button", { class: "icon-btn back-btn", type: "button", "aria-label": "Back to list", onclick: closeDetail }, "←"),
    h("span", { class: "spacer" }),
    inTrash ? (editable ? h("button", { class: "btn small", type: "button", onclick: async () => { try { await api(`api/vaults/${vaultId}/restore/${id}`, { method: "POST" }); toast("Restored"); state.selected = null; await afterChange(); } catch (e) { fail(e); } } }, "Restore") : null)
      : (editable ? h("button", { class: "btn small primary", type: "button", onclick: () => itemEditor(d) }, "✎ Edit") : null),
    h("button", { class: "icon-btn", type: "button", "aria-label": "More actions", onclick: (e) => itemMenu(e.currentTarget, d, vault) }, "⋯"));
  mount($("#detail"),
    actions,
    h("div", { class: "detail-head" }, avatar(d.title), h("div", { class: "grow" }, h("h2", null, d.title, d.favourite ? " ⭐" : ""),
      h("div", { class: "hint" }, [d.template && TEMPLATES[d.template] ? `${TEMPLATES[d.template].icon} ${TEMPLATES[d.template].label}` : null, [d.vaultName, ...d.folderPath].join(" › ")].filter(Boolean).join(" · ")))),
    inTrash ? h("div", { class: "notice" }, "This item is in the Trash.") : null,
    (state.healthMap.get(vaultId + ":" + id) || []).length ? h("div", { class: "row wrap meta" }, h("span", { class: "hint" }, "Password health:"),
      (state.healthMap.get(vaultId + ":" + id) || []).map((k) => issueChip(k))) : null,
    h("div", { class: "fields" }, rows, totpBox, custom),
    (d.sheet && d.sheet.include) || d.emergency || d.guestWifi ? h("div", { class: "row wrap meta" },
      d.sheet && d.sheet.include ? h("span", { class: "chip on" }, "🖨 On the household sheet") : null, d.sheet && d.sheet.wifi ? h("span", { class: "chip" }, "Wi-Fi QR code") : null,
      d.emergency ? h("span", { class: "chip on" }, "🆘 In emergency access") : null,
      d.guestWifi ? h("button", { class: "chip on", type: "button", onclick: () => showPage("guest") }, "📶 Guest Wi-Fi on the dashboard") : null) : null,
    d.tags.length ? h("div", { class: "row wrap meta" }, d.tags.map((t) => h("span", { class: "chip" }, t))) : null,
    d.notes ? h("div", null, h("div", { class: "hint meta" }, "Notes"), h("div", { class: "notes-box" }, d.notes)) : null,
    h("div", { class: "meta" }, `Changed ${fmtWhen(kdbxTime(d.modified))}`, d.historyCount ? ` · ${d.historyCount} earlier version${d.historyCount > 1 ? "s" : ""}` : ""));
}
function svgEl(tag, attrs, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  kids.forEach((k) => el.appendChild(k));
  return el;
}
function safeUrl(u) {
  const s = String(u || "").trim();
  if (/^https?:\/\//i.test(s)) return s;
  if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return "#";         // javascript:, data: … never
  return "https://" + s;
}
function itemMenu(anchor, d, vault) {
  const editable = canEdit(vault) && d.canEdit;
  menu(anchor, [
    editable && !d.inTrash ? { label: d.favourite ? "☆ Remove from favourites" : "⭐ Add to favourites", run: async () => {
      try { await api(`api/vaults/${d.vaultId}/items/${d.id}`, { method: "PATCH", body: { favourite: !d.favourite } }); await afterChange(); } catch (e) { fail(e); } } } : null,
    editable && !d.inTrash ? { label: "📁 Move to…", run: () => moveDialog([d], false) } : null,
    !d.inTrash ? { label: "📄 Copy to…", run: () => moveDialog([d], true) } : null,
    { label: "🕘 History", run: () => historyDialog(d) },
    editable && !d.inTrash && sheetAllowed(vault) ? { label: "🖨 Household sheet…", run: () => sheetDialog(d) } : null,
    editable && !d.inTrash && vault && vault.kind === "household" && d.template === "wifi" ? { label: d.guestWifi ? "📶 Guest Wi-Fi on the dashboard…" : "📶 Show as guest Wi-Fi on the dashboard…", run: () => guestWifiDialog(d) } : null,
    editable && !d.inTrash && vault && vault.kind === "household" && d.type === "login" && !d.template ? { label: "📶 This is a Wi-Fi network", run: async () => {
      try { await api(`api/vaults/${d.vaultId}/items/${d.id}`, { method: "PATCH", body: { template: "wifi" } }); toast("Marked as a Wi-Fi network — it can now go on the dashboard"); await afterChange(); } catch (e) { fail(e); } } } : null,
    !d.inTrash && vault && vault.kind === "personal" && vault.mine ? { label: d.emergency ? "🆘 Take out of emergency access" : "🆘 Include in emergency access", run: async () => {
      try {
        await api(`api/vaults/${d.vaultId}/items/${d.id}/emergency`, { method: "PUT", body: { include: !d.emergency } });
        toast(d.emergency ? "Taken out of emergency access" : "Included — your emergency contacts can ask for it (Settings → Emergency access)", { ms: 4500 });
        await afterChange();
      } catch (e) { fail(e); } } } : null,
    "-",
    editable ? { label: d.inTrash ? "Delete for good" : "🗑 Delete", danger: true, run: async () => {
      if (d.inTrash && !await confirmDialog("Delete for good", `Delete “${d.title}” permanently?`, "Delete", true)) return;
      try { const r = await api(`api/vaults/${d.vaultId}/items/${d.id}`, { method: "DELETE" }); toast(r.result === "trashed" ? "Moved to the Trash" : "Deleted"); state.selected = null; await afterChange(); } catch (e) { fail(e); } } } : null,
  ]);
}
async function historyDialog(d) {
  try {
    const r = await api(`api/vaults/${d.vaultId}/items/${d.id}/history`);
    openModal(`History — ${d.title}`, r.history.length ? h("div", null, h("p", { class: "hint" }, "Earlier versions, newest first. Passwords aren't shown."),
      dataTable(["Changed", "Name", "Username", "Password"], r.history.map((x) => [fmtWhen(kdbxTime(x.modified)), x.title, x.username || "—",
        x.passwordChanged ? "Changed after this" : "Same as next"]))) : h("p", { class: "hint" }, "No earlier versions."), { wide: true });
  } catch (e) { fail(e); }
}

// ---------- item editor ----------
function editableVaults() { return state.vaults.filter((v) => v.open && canEdit(v)); }
function folderOptions(vaultId, selected) {
  const f = state.folders[vaultId] || { folders: [] };
  const sel = h("select", { "aria-label": "Folder" }, h("option", { value: "" }, "(top level)"));
  const walk = (pid, depth) => f.folders.filter((x) => x.parentId === pid).forEach((x) => {
    sel.appendChild(h("option", { value: x.id }, "  ".repeat(depth) + x.name)); walk(x.id, depth + 1); });
  walk(null, 0);
  sel.value = selected || "";
  return sel;
}
function itemEditor(d, tplKey) {
  const creating = !d;
  const v = state.view;
  let vaultId = d ? d.vaultId : (v.vaultId && canEdit(vaultById(v.vaultId)) ? v.vaultId : (editableVaults().find((x) => x.kind === "personal" && x.mine) || editableVaults()[0] || {}).id);
  if (!vaultId) { toast("Open a vault you can edit first.", { error: true }); return; }
  const kindOf = d ? (d.template && TEMPLATES[d.template] ? d.template : d.type) : (tplKey || (v.type && v.type !== "2fa" ? v.type : "login"));
  const kind = h("select", { "aria-label": "Kind", disabled: !creating }, Object.entries(TEMPLATES).map(([k, t]) => h("option", { value: k }, `${t.icon} ${t.label}`)));
  kind.value = kindOf;
  const type = { get value() { return TEMPLATES[kind.value].type; } };
  const vaultSel = h("select", { "aria-label": "Vault", disabled: !creating }, ...editableVaults().map((x) => h("option", { value: x.id }, x.name)));
  vaultSel.value = vaultId;
  let folderSel = folderOptions(vaultId, d ? d.folderId : (v.kind === "folder" ? v.folderId : null));
  const folderBox = h("div", null, field("Folder", folderSel));
  vaultSel.addEventListener("change", () => { vaultId = vaultSel.value; folderSel = folderOptions(vaultId, null); mount(folderBox, field("Folder", folderSel)); });
  const title = h("input", { type: "text", value: d ? d.title : "", maxlength: 500, "aria-label": "Name" });
  const username = h("input", { type: "text", value: d ? d.username : "", autocomplete: "off", "aria-label": "Username" });
  const userLbl = h("span", { class: "lbl" }, "Username");
  const expires = h("input", { type: "date", value: d && d.expires ? d.expires : "", "aria-label": "Expires on", min: "1900-01-01", max: "2200-12-31" });
  const remind = h("select", { "aria-label": "Remind me" }, [[0, "Don't remind me"], [1, "1 day before"], [7, "1 week before"], [14, "2 weeks before"], [30, "30 days before"], [60, "60 days before"], [90, "90 days before"]].map(([n, l]) => h("option", { value: n }, l)));
  remind.value = String(d ? d.remindDays : 30);
  const expLbl = h("span", { class: "lbl" }, "Expires on");
  const expiryBox = h("div", { class: "form-row" }, h("label", { class: "field" }, expLbl, expires), field("Remind me", remind));
  const pw = pwInput({ autocomplete: "new-password", label: "Password", placeholder: d && d.password.set ? "(unchanged)" : "" });
  const gen = h("button", { class: "btn small", type: "button", onclick: () => generatorDialog((p) => { pw.input.value = p; pw.input.type = "text"; pw.input.dispatchEvent(new Event("input")); }) }, "🎲 Generate");
  const url = h("input", { type: "text", value: d ? d.url : "", placeholder: "https://…", "aria-label": "Website" });
  const totpIn = h("input", { type: "text", placeholder: d && d.totp ? "(set — paste a new one to replace)" : "otpauth://… or the setup key", autocomplete: "off", spellcheck: "false", "aria-label": "2FA setup key" });
  let removeTotp = false;
  const totpRemove = d && d.totp ? h("button", { class: "btn small", type: "button", onclick: () => { removeTotp = true; totpIn.value = ""; totpIn.placeholder = "(will be removed)"; } }, "Remove 2FA") : null;
  const holder = h("input", { type: "text", value: d ? d.cardholder : "", "aria-label": "Name on card" });
  const number = h("input", { type: "text", inputmode: "numeric", placeholder: d && d.card.number.set ? "(unchanged)" : "", autocomplete: "off", "aria-label": "Card number" });
  const expiry = h("input", { type: "text", value: d ? d.expiry : "", placeholder: "MM/YY", "aria-label": "Expiry" });
  expiry.addEventListener("change", () => {
    const m = /^\s*(\d{1,2})\s*[\/\-.]\s*(\d{2}|\d{4})\s*$/.exec(expiry.value);
    if (!m || expires.value) return;
    const mon = +m[1], yr = +m[2] < 100 ? 2000 + +m[2] : +m[2];
    if (mon < 1 || mon > 12) return;
    const last = new Date(Date.UTC(yr, mon, 0));
    expires.value = last.toISOString().slice(0, 10);
  });
  const cvv = h("input", { type: "password", placeholder: d && d.card.cvv.set ? "(unchanged)" : "", autocomplete: "off", "aria-label": "CVV" });
  const pin = h("input", { type: "password", placeholder: d && d.card.pin.set ? "(unchanged)" : "", autocomplete: "off", "aria-label": "PIN" });
  const notes = h("textarea", { "aria-label": "Notes" }); notes.value = d ? d.notes : "";
  const tags = h("input", { type: "text", value: d ? d.tags.join(", ") : "", placeholder: "bank, family", "aria-label": "Tags" });
  const fav = h("input", { type: "checkbox", checked: d ? d.favourite : false });
  let extra = (d ? d.fields : []).map((f) => ({ name: f.name, value: f.value, protected: f.protected, existing: true }));
  const extraBox = h("div");
  const drawExtra = () => mount(extraBox, extra.map((f, i) => {
    const n = h("input", { type: "text", value: f.name, placeholder: "Field name", "aria-label": "Field name" });
    n.addEventListener("input", () => { f.name = n.value; });
    const val = h("input", { type: f.protected ? "password" : "text", value: f.value || "", placeholder: f.protected && f.existing ? "(unchanged)" : "Value", "aria-label": "Field value" });
    val.addEventListener("input", () => { f.value = val.value; f.changed = true; });
    const prot = h("input", { type: "checkbox", checked: f.protected, title: "Hidden (like a password)" });
    prot.addEventListener("change", () => { f.protected = prot.checked; val.type = prot.checked ? "password" : "text"; });
    return h("div", { class: "cf-row" }, n, val, h("label", { class: "check", title: "Hidden" }, prot, "🔒"),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Remove field", onclick: () => { extra.splice(i, 1); drawExtra(); } }, "✕"));
  }));
  drawExtra();
  const addField = h("button", { class: "btn small", type: "button", onclick: () => { extra.push({ name: "", value: "", protected: false }); drawExtra(); } }, "+ Add field");
  const scanBtn = h("button", { class: "btn small", type: "button", onclick: () => scanDialog((uri, info) => {
    totpIn.value = uri; removeTotp = false;
    if (!title.value.trim() && info && (info.issuer || info.account)) title.value = info.issuer || info.account;
    if (!username.value.trim() && info && info.account) username.value = info.account;
    toast("2FA code added — Save to keep it");
  }) }, "📷 Scan QR code");
  const loginBox = h("div", null, h("label", { class: "field" }, userLbl, username), field("Password", pw.el), h("div", { class: "row" }, gen), strengthMeter(pw.input), breachHint(pw.input),
    field("Website", url), field("2FA (time-based code)", h("div", { class: "input-with totp-row" }, totpIn, scanBtn), "Scan the QR code, or paste the otpauth:// link or the setup key your site shows."), totpRemove);
  const cardBox = h("div", null, field("Name on card", holder), field("Card number", number), h("div", { class: "form-row" }, field("Expiry", expiry), field("CVV", cvv), field("PIN", pin)), field("Website", url));
  const sync = () => {
    const t = TEMPLATES[kind.value];
    loginBox.hidden = t.type !== "login"; cardBox.hidden = t.type !== "card";
    userLbl.textContent = t.user || "Username";
    expLbl.textContent = t.expires || "Expires on (optional)";
    title.placeholder = t.title || "";
    if (creating) {                                   // swap the preset fields the person hasn't filled in
      extra = extra.filter((f) => !f.preset || f.value);
      for (const [name, prot] of t.fields) if (!extra.some((f) => f.name === name)) extra.push({ name, value: "", protected: prot, preset: true });
      drawExtra();
    }
  };
  kind.addEventListener("change", sync); sync();
  const err = h("div", { class: "error", role: "alert" });
  const save = h("button", { class: "btn primary", type: "submit" }, creating ? "Add" : "Save");
  const form = h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const body = { title: title.value.trim(), notes: notes.value, url: url.value.trim(),
      tags: tags.value.split(",").map((x) => x.trim()).filter(Boolean), favourite: fav.checked, folderId: folderSel.value,
      expires: expires.value || "", remindDays: +remind.value };
    if (creating) { body.type = type.value; if (!["login", "card", "note"].includes(kind.value)) body.template = kind.value; }
    if (type.value === "login") {
      body.username = username.value;
      if (pw.input.value || creating) body.password = pw.input.value;
      if (totpIn.value.trim()) body.totp = totpIn.value.trim(); else if (removeTotp) body.totp = "";
    } else if (type.value === "card") {
      body.cardholder = holder.value; body.expiry = expiry.value;
      body.card = {};
      if (number.value) body.card.number = number.value;
      if (cvv.value) body.card.cvv = cvv.value;
      if (pin.value) body.card.pin = pin.value;
    }
    body.fields = extra.filter((f) => f.name.trim() && !(f.preset && !f.value)).map((f) => ({ name: f.name.trim(), protected: f.protected,
      value: f.protected && f.existing && !f.changed ? null : (f.value || "") }));
    if (!creating) body.ifModified = d.modified;
    save.disabled = true;
    try {
      const r = creating ? await api(`api/vaults/${vaultId}/items`, { method: "POST", body })
        : await api(`api/vaults/${d.vaultId}/items/${d.id}`, { method: "PATCH", body });
      m.close();
      toast(creating ? "Added" : "Saved");
      state.selected = { vaultId: r.vaultId, id: r.id };
      await afterChange();
    } catch (x) {
      save.disabled = false;
      if (x.status === 409 && x.detail && x.detail.current) {
        err.textContent = x.message + " Close this to see their version, then edit again.";
      } else err.textContent = x.message;
    }
  } },
  h("div", { class: "form-row" }, field("Kind", kind), field("Vault", vaultSel)),
  field("Name", title), folderBox, loginBox, cardBox, expiryBox,
  field("Notes", notes), field("Tags", tags, "Separate with commas."),
  h("label", { class: "check" }, fav, "⭐ Favourite"),
  h("div", { class: "nav-sec" }, "More fields"), extraBox, addField,
  err, h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: () => m.close() }, "Cancel"), save));
  const m = openModal(creating ? `New ${TEMPLATES[kindOf].label}` : `Edit — ${d.title}`, form, { wide: true });
  setTimeout(() => title.focus(), 40);
}
function generatorDialog(use) {
  const kind = h("select", { "aria-label": "Kind" }, h("option", { value: "random" }, "Random characters"), h("option", { value: "passphrase" }, "Words (passphrase)"));
  const len = h("input", { type: "number", min: 8, max: 64, value: 20, "aria-label": "Length" });
  const words = h("input", { type: "number", min: 3, max: 10, value: 6, "aria-label": "Words" });
  const sep = h("input", { type: "text", value: "-", maxlength: 3, "aria-label": "Separator" });
  const opts = { upper: h("input", { type: "checkbox", checked: true }), digits: h("input", { type: "checkbox", checked: true }),
    symbols: h("input", { type: "checkbox", checked: true }), avoidAmbiguous: h("input", { type: "checkbox" }),
    capitalize: h("input", { type: "checkbox" }), number: h("input", { type: "checkbox" }) };
  const out = h("div", { class: "pw-big mono" }, "…");
  const info = h("div", { class: "hint" });
  let current = "";
  const randomBox = h("div", null, field("Length", len), h("div", { class: "row wrap" },
    h("label", { class: "check" }, opts.upper, "A–Z"), h("label", { class: "check" }, opts.digits, "0–9"),
    h("label", { class: "check" }, opts.symbols, "!@#"), h("label", { class: "check" }, opts.avoidAmbiguous, "Avoid look-alikes (l 1 O 0)")));
  const wordBox = h("div", null, h("div", { class: "form-row" }, field("Words", words), field("Separator", sep)), h("div", { class: "row wrap" },
    h("label", { class: "check" }, opts.capitalize, "Capitalise"), h("label", { class: "check" }, opts.number, "Add a number")));
  const make = async () => {
    randomBox.hidden = kind.value !== "random"; wordBox.hidden = kind.value !== "passphrase";
    try {
      const r = await api("api/generate", { method: "POST", body: { kind: kind.value, length: +len.value || 20, lower: true, upper: opts.upper.checked,
        digits: opts.digits.checked, symbols: opts.symbols.checked, avoidAmbiguous: opts.avoidAmbiguous.checked, words: +words.value || 6,
        separator: sep.value, capitalize: opts.capitalize.checked, number: opts.number.checked } });
      current = r.password; out.textContent = r.password; info.textContent = `Strength: ${r.strength.label}`;
    } catch (e) { fail(e); }
  };
  [kind, len, words, sep, ...Object.values(opts)].forEach((el) => el.addEventListener("change", make));
  const m = openModal("Password generator", h("div", null, field("Kind", kind), randomBox, wordBox, out, info,
    h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: make }, "🎲 Another"),
      h("button", { class: "btn", type: "button", onclick: () => copyText(current, "password") }, "📋 Copy"),
      use ? h("button", { class: "btn primary", type: "button", onclick: () => { use(current); m.close(); } }, "Use it") : null)));
  make();
}

// ---------- folders ----------
function newFolderDialog(vault, parentId) {
  const name = h("input", { type: "text", maxlength: 100, "aria-label": "Folder name" });
  const parent = folderOptions(vault.id, parentId);
  const err = h("div", { class: "error" });
  const m = openModal("New folder", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api(`api/vaults/${vault.id}/folders`, { method: "POST", body: { name: name.value, parentId: parent.value || null } }); m.close(); toast("Folder added"); await afterChange(); }
    catch (x) { err.textContent = x.message; }
  } }, field("Name", name), field("Inside", parent), err, h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Add"))));
}
function folderMenu(anchor, vault, fo) {
  if (!fo) return;
  menu(anchor, [
    { label: "+ New subfolder", run: () => newFolderDialog(vault, fo.id) },
    { label: "✎ Rename", run: () => {
      const name = h("input", { type: "text", value: fo.name, maxlength: 100, "aria-label": "Folder name" });
      const m = openModal("Rename folder", h("form", { onsubmit: async (e) => { e.preventDefault();
        try { await api(`api/vaults/${vault.id}/folders/${fo.id}`, { method: "PATCH", body: { name: name.value } }); m.close(); await afterChange(); } catch (x) { fail(x); } } },
        field("Name", name), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Save")))); } },
    { label: "📁 Move into…", run: () => {
      const parent = folderOptions(vault.id, fo.parentId);
      const m = openModal(`Move “${fo.name}”`, h("form", { onsubmit: async (e) => { e.preventDefault();
        const body = parent.value ? { parentId: parent.value } : { toRoot: true };
        try { await api(`api/vaults/${vault.id}/folders/${fo.id}`, { method: "PATCH", body }); m.close(); await afterChange(); } catch (x) { fail(x); } } },
        field("Move into", parent), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Move")))); } },
    "-",
    { label: "🗑 Delete", danger: true, run: async () => {
      if (fo.count && !await confirmDialog("Delete folder", `“${fo.name}” and everything in it go to the Trash. You can restore them from there.`, "Delete", true)) return;
      try { const r = await api(`api/vaults/${vault.id}/folders/${fo.id}`, { method: "DELETE" }); toast(r.result === "deleted" ? "Folder deleted" : "Moved to the Trash");
        if (state.view.folderId === fo.id) state.view = { kind: "folder", vaultId: vault.id, folderId: null }; await afterChange(); } catch (e) { fail(e); } } },
  ]);
}
function moveDialog(list, copy) {
  const targets = editableVaults();
  if (!targets.length) { toast("No vault you can edit is open.", { error: true }); return; }
  const src = list[0].vaultId;
  const vaultSel = h("select", { "aria-label": "Vault" }, ...targets.map((x) => h("option", { value: x.id }, x.name)));
  vaultSel.value = targets.some((x) => x.id === src) ? src : targets[0].id;
  let folderSel = folderOptions(vaultSel.value, null);
  const fb = h("div", null, field("Folder", folderSel));
  vaultSel.addEventListener("change", () => { folderSel = folderOptions(vaultSel.value, null); mount(fb, field("Folder", folderSel)); });
  const err = h("div", { class: "error" });
  const m = openModal(`${copy ? "Copy" : "Move"} ${list.length === 1 ? "“" + list[0].title + "”" : list.length + " items"}`, h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const bySrc = {};
    list.forEach((it) => { (bySrc[it.vaultId] = bySrc[it.vaultId] || []).push(it.id); });
    try {
      for (const [vid, ids] of Object.entries(bySrc))
        await api(`api/vaults/${vid}/items/move`, { method: "POST", body: { items: ids, toVaultId: vaultSel.value, toFolderId: folderSel.value || null, duplicate: copy } });
      m.close(); toast(copy ? "Copied" : "Moved"); state.picked = new Set(); state.selected = null; await afterChange();
    } catch (x) { err.textContent = x.message; }
  } }, field("To vault", vaultSel), fb,
  !copy && list.some((x) => x.vaultId !== vaultSel.value) ? h("p", { class: "hint" }, "Moving to another vault copies the item there and puts the original in its vault's Trash.") : null,
  err, h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, copy ? "Copy" : "Move"))));
}
function pickedItems() { return state.items.filter((it) => state.picked.has(it.vaultId + ":" + it.id)); }
function bulkMove(copy) { const l = pickedItems(); if (l.length) moveDialog(l, copy); }
async function bulkDelete() {
  const l = pickedItems();
  if (!l.length || !await confirmDialog("Delete", `Move ${l.length} item${l.length > 1 ? "s" : ""} to the Trash?`, "Delete", true)) return;
  try { for (const it of l) await api(`api/vaults/${it.vaultId}/items/${it.id}`, { method: "DELETE" }); state.picked = new Set(); state.selected = null; toast("Moved to the Trash"); await afterChange(); }
  catch (e) { fail(e); }
}

// ---------- vaults ----------
function openLockedVault(v) {
  const pw = pwInput({ label: `${v.name} password` });
  const remember = h("input", { type: "checkbox", checked: true });
  const err = h("div", { class: "error", role: "alert" });
  const m = openModal(`Open ${v.name}`, h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    try {
      await api(`api/vaults/${v.id}/open`, { method: "POST", body: { password: pw.input.value, remember: remember.checked } });
      m.close(); toast(`${v.name} is open`);
      await afterChange(false);
      setView({ kind: "folder", vaultId: v.id, folderId: null });
    } catch (x) { err.textContent = x.message; }
  } },
  h("p", null, v.kind === "personal" ? `${v.owner} shared their Personal vault with you. Enter its password (${v.owner}'s master password) — ask them for it.`
    : `This vault has its own password${v.owner ? " — ask " + v.owner : ""}.`),
  field("Vault password", pw.el), h("label", { class: "check" }, remember, "Remember it — open with my master password from now on"), err,
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Open"))));
}
function vaultMenu(anchor, v) {
  const shared = v.kind === "shared";
  const entries = [
    v.open ? { label: "📁 New folder", run: () => canEdit(v) ? newFolderDialog(v, null) : toast("You can only view this vault.", { error: true }) } : null,
    v.kind !== "household" && canManage(v) && v.open ? { label: "👥 Share…", run: () => shareDialog(v) } : null,
    { label: `👀 People (${v.members.length})`, run: () => membersDialog(v) },
    "-",
    v.open ? { label: "⬇ Download .kdbx", run: () => downloadVault(v) } : null,
    v.open && sheetAllowed(v) ? { label: "🖨 Print household sheet", run: () => printSheet(v) } : null,
    v.open && v.passwordMode === "random" ? { label: "🔑 Show vault password (for KeePassXC)", run: () => showVaultPassword(v) } : null,
    v.open && canManage(v) || (v.open && v.kind === "household") ? { label: "🕘 Versions", run: () => versionsDialog(v) } : null,
    v.open && v.passwordMode === "random" && (canManage(v) || v.kind === "household") ? { label: "♻ Re-key (new random password)", run: async () => {
      if (!await confirmDialog("Re-key", `Give ${v.name} a new random password? Everyone in it keeps access automatically; copies downloaded earlier stay locked with the old one.`, "Re-key")) return;
      try { await api(`api/vaults/${v.id}/rekey`, { method: "POST" }); toast("Re-keyed"); await afterChange(); } catch (e) { fail(e); } } } : null,
    shared && v.role === "owner" && v.passwordMode === "chosen" && v.open ? { label: "🔑 Change vault password", run: () => changeVaultPassword(v) } : null,
    shared && v.passwordMode === "chosen" && v.remembered && v.role !== "owner" ? { label: "Forget remembered password", run: async () => {
      try { await api(`api/vaults/${v.id}/forget`, { method: "POST" }); toast("Forgotten — you'll type it next time"); await afterChange(); } catch (e) { fail(e); } } } : null,
    shared && canManage(v) && v.open ? { label: "✎ Rename", run: () => renameVault(v) } : null,
    shared && v.role === "owner" ? { label: "👑 Transfer ownership", run: () => transferDialog(v) } : null,
    v.role !== "owner" && v.kind !== "household" && v.kind !== "emergency" ? { label: "🚪 Leave", danger: true, run: async () => {
      if (!await confirmDialog("Leave vault", `Leave ${v.name}? You'll need to be added again to see it.`, "Leave", true)) return;
      try { await api(`api/vaults/${v.id}/leave`, { method: "POST" }); await afterChange(false); setView({ kind: "all" }); } catch (e) { fail(e); } } } : null,
    shared && v.role === "owner" ? { label: "🗑 Delete vault", danger: true, run: () => deleteVaultDialog(v) } : null,
  ];
  menu(anchor, entries);
}
function newVaultDialog() {
  const name = h("input", { type: "text", maxlength: 60, placeholder: "e.g. Parents, Finance", "aria-label": "Vault name" });
  const mode = h("select", { "aria-label": "Password" }, h("option", { value: "random" }, "Random password (opens by itself)"),
    h("option", { value: "chosen" }, "Same password (we all type it)"));
  const modeHint = h("span", { class: "hint" });
  const syncHint = () => { modeHint.textContent = mode.value === "random" ? "Opens automatically for everyone you add, with their own master password." : "Everyone types a password you share (they can tick Remember)."; };
  mode.addEventListener("change", syncHint); syncHint();
  const pw = pwInput({ autocomplete: "new-password", label: "Vault password" });
  const pw2 = pwInput({ autocomplete: "new-password", label: "Type it again" });
  const chosenBox = h("div", null, field("Vault password", pw.el), strengthMeter(pw.input), field("Type it again", pw2.el),
    h("p", { class: "hint" }, "Everyone you add types this password once (they can tick Remember). Tell them in person."));
  const sync = () => { chosenBox.hidden = mode.value !== "chosen"; };
  mode.addEventListener("change", sync); sync();
  const err = h("div", { class: "error", role: "alert" });
  const m = openModal("New shared vault", h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    if (mode.value === "chosen" && pw.input.value !== pw2.input.value) { err.textContent = "The passwords don't match."; return; }
    try {
      const v = await api("api/vaults", { method: "POST", body: { name: name.value, passwordMode: mode.value, password: mode.value === "chosen" ? pw.input.value : null } });
      m.close(); toast("Vault created");
      await afterChange(false);
      await setView({ kind: "folder", vaultId: v.id, folderId: null });
      shareDialog(vaultById(v.id));
    } catch (x) { err.textContent = x.message; }
  } }, field("Name", name), field("Password", mode), modeHint, chosenBox, err, h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Create"))));
}
async function shareDialog(v) {
  if (!v) return;
  let users = [];
  try { users = (await api("api/users")).users.filter((u) => !v.members.some((m) => m.id === u.id)); } catch (e) { fail(e); return; }
  if (!users.length) { toast("Everyone who's set up already has access (or nobody else is set up yet).", { ms: 4000 }); return; }
  const who = h("select", { "aria-label": "Person" }, ...users.map((u) => h("option", { value: u.id }, u.name)));
  const role = h("select", { "aria-label": "Role" }, h("option", { value: "editor" }, "Can edit"), h("option", { value: "viewer" }, "Can view"),
    v.kind !== "personal" ? h("option", { value: "manager" }, "Manager (can add people)") : null);
  const err = h("div", { class: "error" });
  const note = v.passwordMode === "chosen" ? h("div", { class: "notice" }, v.kind === "personal"
    ? "They'll open your Personal vault with your master password — so they'll know it. They still can't open your other vaults (they'd have to sign in to Home Assistant as you). If you'd rather keep your master password to yourself, create a shared vault (e.g. “You & them”) instead."
    : "They'll need this vault's password the first time — tell them in person.")
    : h("p", { class: "hint" }, "It opens for them automatically, with their own master password.");
  const m = openModal(`Share ${v.name}`, h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api(`api/vaults/${v.id}/members`, { method: "POST", body: { userId: who.value, role: role.value } }); m.close(); toast("Shared"); await afterChange(); }
    catch (x) { err.textContent = x.message; }
  } }, field("Person", who), field("Can", role), note,
  v.kind === "personal" ? h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: () => { m.close(); newVaultDialog(); } }, "Create a shared vault instead")) : null,
  err, h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Share"))));
}
function membersDialog(v) {
  const list = h("div", { class: "members" }, v.members.map((mm) => h("div", { class: "m" },
    avatar(mm.name), h("span", { class: "grow m-name" }, mm.name, mm.id === state.me.id ? " (you)" : ""),
    h("span", { class: "m-acts" }, canManage(v) && mm.role !== "owner" && v.kind !== "household" && mm.id !== state.me.id ? (() => {
      const sel = h("select", { "aria-label": `Role of ${mm.name}` }, h("option", { value: "editor" }, "Can edit"), h("option", { value: "viewer" }, "Can view"),
        v.kind !== "personal" ? h("option", { value: "manager" }, "Manager") : null);
      sel.value = mm.role;
      sel.addEventListener("change", async () => { try { await api(`api/vaults/${v.id}/members`, { method: "POST", body: { userId: mm.id, role: sel.value } }); toast("Saved"); await afterChange(); } catch (e) { fail(e); } });
      return sel;
    })() : h("span", { class: "chip" }, ROLE_LABEL[mm.role]),
    canManage(v) && mm.role !== "owner" && v.kind !== "household" && mm.id !== state.me.id ? h("button", { class: "btn small danger", type: "button", onclick: async () => {
      const txt = v.passwordMode === "random" ? `Remove ${mm.name}? The vault gets a new password automatically; they keep anything they already saw or downloaded, so change important passwords.`
        : `Remove ${mm.name}? They know this vault's password — change it afterwards (you'll be reminded).`;
      if (!await confirmDialog("Remove access", txt, "Remove", true)) return;
      try { await api(`api/vaults/${v.id}/members/${mm.id}`, { method: "DELETE" }); m.close(); toast("Removed"); await afterChange(); } catch (e) { fail(e); } } }, "Remove") : null))));
  const m = openModal(`People with access — ${v.name}`, h("div", null,
    h("p", { class: "hint" }, v.kind === "household" ? "Everyone who's set up is in Household. An admin can turn someone's access off." :
      v.passwordMode === "chosen" ? "Same-password vault: everyone opens it with its password." : "Random password: opens automatically for everyone here."),
    list, canManage(v) && v.kind !== "household" && v.open ? h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => { m.close(); shareDialog(v); } }, "+ Share with someone")) : null));
}
function changeVaultPassword(v) {
  const a = pwInput({ autocomplete: "new-password", label: "New vault password" });
  const b = pwInput({ autocomplete: "new-password", label: "Type it again" });
  const err = h("div", { class: "error" });
  const m = openModal(`Change password — ${v.name}`, h("form", { onsubmit: async (e) => {
    e.preventDefault();
    if (a.input.value !== b.input.value) { err.textContent = "The passwords don't match."; return; }
    try { await api(`api/vaults/${v.id}/password`, { method: "POST", body: { new: a.input.value, remember: true } }); m.close(); toast("Password changed — tell the others"); await afterChange(); }
    catch (x) { err.textContent = x.message; }
  } }, h("p", { class: "hint" }, "Everyone else in this vault will need to enter the new password once. Old versions of the vault are removed."),
  field("New password", a.el), strengthMeter(a.input), field("Type it again", b.el), err, h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Change"))));
}
async function showVaultPassword(v) {
  try {
    const r = await api(`api/vaults/${v.id}/password`);
    openModal(`${v.name} — vault password`, h("div", null, h("p", { class: "hint" }, "Use this to open the downloaded file in KeePassXC or another KeePass app. It changes when someone is removed."),
      h("div", { class: "pw-big mono" }, r.password), h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: () => copyText(r.password, "vault password") }, "📋 Copy"))));
  } catch (e) { fail(e); }
}
async function versionsDialog(v) {
  try {
    const r = await api(`api/vaults/${v.id}/versions`);
    const m = openModal(`Versions — ${v.name}`, h("div", null, h("p", { class: "hint" }, "The last 20 saves since the vault's password last changed. Restoring saves the old version as a new one."),
      dataTable(["Version", "Saved", "By", ""], r.versions.map((x) => [String(x.version), fmtWhen(x.createdAt), x.by || "—",
        x.version === r.current ? h("span", { class: "chip on" }, "current") : h("button", { class: "btn small", type: "button", onclick: async () => {
          if (!await confirmDialog("Restore", `Restore version ${x.version} of ${v.name}? Changes made after it are undone (they stay in the version list).`, "Restore")) return;
          try { await api(`api/vaults/${v.id}/versions/${x.version}/restore`, { method: "POST" }); m.close(); toast("Restored"); await afterChange(false); } catch (e) { fail(e); } } }, "Restore")]), "compact")), { wide: true });
  } catch (e) { fail(e); }
}
function renameVault(v) {
  const name = h("input", { type: "text", value: v.name, maxlength: 60, "aria-label": "Name" });
  const m = openModal("Rename vault", h("form", { onsubmit: async (e) => { e.preventDefault();
    try { await api(`api/vaults/${v.id}`, { method: "PATCH", body: { name: name.value } }); m.close(); await afterChange(); } catch (x) { fail(x); } } },
    field("Name", name), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Save"))));
}
function transferDialog(v) {
  const others = v.members.filter((mm) => mm.id !== state.me.id);
  if (!others.length) { toast("Share the vault with someone first.", { error: true }); return; }
  const who = h("select", { "aria-label": "New owner" }, ...others.map((mm) => h("option", { value: mm.id }, mm.name)));
  const m = openModal("Transfer ownership", h("form", { onsubmit: async (e) => { e.preventDefault();
    try { await api(`api/vaults/${v.id}/transfer`, { method: "POST", body: { userId: who.value } }); m.close(); toast("Transferred — you're now a manager"); await afterChange(); } catch (x) { fail(x); } } },
    field("New owner", who), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Transfer"))));
}
function deleteVaultDialog(v) {
  const name = h("input", { type: "text", "aria-label": "Vault name" });
  const err = h("div", { class: "error" });
  const m = openModal("Delete vault", h("form", { onsubmit: async (e) => { e.preventDefault();
    try { await api(`api/vaults/${v.id}/delete`, { method: "POST", body: { confirmName: name.value } }); m.close(); toast("Vault deleted"); await afterChange(false); setView({ kind: "all" }); }
    catch (x) { err.textContent = x.message; } } },
    h("p", null, `This deletes ${v.name} and everything in it for everyone, including its saved versions. Download a copy first if you might need it.`),
    field(`Type “${v.name}” to confirm`, name), err, h("div", { class: "actions" }, h("button", { class: "btn danger", type: "submit" }, "Delete"))));
}


// ---------- drag and drop (desktop; phones use Move to…) ----------
function startDrag(e, what, label) {
  state.dragging = what;
  e.dataTransfer.effectAllowed = "copyMove";
  try { e.dataTransfer.setData("text/plain", label || ""); } catch (x) { /* ignore */ }
  document.body.classList.add("dragging");
}
function dragEnd() {
  state.dragging = null;
  document.body.classList.remove("dragging");
  document.querySelectorAll(".drop-ok").forEach((el) => el.classList.remove("drop-ok"));
}
function isInside(folders, id, ancestorId) {
  // is folder `id` the folder `ancestorId` or inside it?
  for (let cur = id; cur; cur = (folders.find((x) => x.id === cur) || {}).parentId) if (cur === ancestorId) return true;
  return false;
}
function canDrop(d, t) {
  if (!d) return false;
  const v = vaultById(t.vaultId);
  if (!v || !v.open || !canEdit(v)) return false;
  if (d.kind === "items") {
    if (t.trash) return d.list.every((x) => x.vaultId === t.vaultId);
    return !d.list.every((x) => x.vaultId === t.vaultId && (x.folderId || null) === (t.folderId || null));
  }
  if (d.kind === "folder") {
    if (d.vaultId !== t.vaultId) return false;                 // folders move within their vault
    if (t.trash) return true;
    if (t.folderId && isInside(d.folders, t.folderId, d.folder.id)) return false;
    return (d.folder.parentId || null) !== (t.folderId || null);
  }
  return false;
}
function wantsCopy(e, d, t) {
  if (d.kind !== "items" || t.trash) return false;
  if (e.ctrlKey || e.altKey || e.metaKey) return true;
  return d.list.some((x) => !canEdit(vaultById(x.vaultId)));  // can't take items out of a view-only vault
}
function dropTarget(el, t) {
  el.addEventListener("dragover", (e) => {
    if (!canDrop(state.dragging, t)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = wantsCopy(e, state.dragging, t) ? "copy" : "move";
    el.classList.add("drop-ok");
  });
  el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop-ok"); });
  el.addEventListener("drop", (e) => {
    const d = state.dragging;
    if (!canDrop(d, t)) return;
    e.preventDefault();
    const copy = wantsCopy(e, d, t);
    dragEnd();
    doDrop(d, t, copy);
  });
}
async function doDrop(d, t, copy) {
  try {
    if (d.kind === "items" && t.trash) {
      for (const it of d.list) await api(`api/vaults/${it.vaultId}/items/${it.id}`, { method: "DELETE" });
      toast(d.list.length === 1 ? `“${d.list[0].title}” moved to the Trash` : `${d.list.length} items moved to the Trash`);
    } else if (d.kind === "items") {
      const bySrc = {};
      d.list.forEach((it) => { (bySrc[it.vaultId] = bySrc[it.vaultId] || []).push(it.id); });
      for (const [vid, ids] of Object.entries(bySrc))
        await api(`api/vaults/${vid}/items/move`, { method: "POST", body: { items: ids, toVaultId: t.vaultId, toFolderId: t.folderId || null, duplicate: copy } });
      const what = d.list.length === 1 ? `“${d.list[0].title}”` : `${d.list.length} items`;
      toast(`${copy ? "Copied" : "Moved"} ${what} to ${t.name}`);
    } else if (d.kind === "folder" && t.trash) {
      if (d.folder.count && !await confirmDialog("Delete folder", `“${d.folder.name}” and everything in it go to the Trash. You can restore them from there.`, "Delete", true)) return;
      await api(`api/vaults/${d.vaultId}/folders/${d.folder.id}`, { method: "DELETE" });
      if (state.view.folderId === d.folder.id) state.view = { kind: "folder", vaultId: d.vaultId, folderId: null };
      toast("Folder moved to the Trash");
    } else if (d.kind === "folder") {
      await api(`api/vaults/${d.vaultId}/folders/${d.folder.id}`, { method: "PATCH", body: t.folderId ? { parentId: t.folderId } : { toRoot: true } });
      toast(`Moved “${d.folder.name}” into ${t.name}`);
    }
    state.picked = new Set();
    if (d.kind === "items" && !copy && state.selected && d.list.some((x) => x.id === state.selected.id)) state.selected = null;
    await afterChange();
  } catch (e) { fail(e); await afterChange(); }
}

// ---------- password health (SPEC §12.1) ----------
async function refreshHealth() {
  if (!state.token) return;
  try { state.expiringCount = (await api("api/expiring?within=30")).items.length; } catch (e) { /* locked meanwhile */ }
  try {
    const r = await api("api/password-health");
    state.health = r;
    state.healthMap = new Map(r.items.map((x) => [x.vaultId + ":" + x.id, x.issues]));
    renderSidebar();
    if (state.page === "vault") document.querySelectorAll(".item-row[data-key]").forEach((row) => {
      const k = row.getAttribute("data-key");
      const old = row.querySelector(".badges");
      const issues = state.healthMap.get(k) || [];
      if (old) old.remove();
      const it = state.items.find((x) => x.vaultId + ":" + x.id === k);
      const ex = it ? expiryChip(it.expires) : null;
      if (issues.length || ex) row.insertBefore(h("span", { class: "badges" }, ex, issues.slice(0, 2).map((x) => issueChip(x))), row.lastChild);
    });
  } catch (e) { /* locked meanwhile */ }
}
function openItemFrom(x) {
  state.page = "vault";
  const view = { kind: "folder", vaultId: x.vaultId, folderId: x.folderId || null };
  state.selected = { vaultId: x.vaultId, id: x.id };
  setView(view).then(() => { const c = $("#content"); if (c) c.classList.add("show-detail"); });
}
async function healthPage() {
  const r = await api("api/password-health");
  state.health = r;
  state.healthMap = new Map(r.items.map((x) => [x.vaultId + ":" + x.id, x.issues]));
  const box = h("div");
  let filter = null;
  const DESC = {
    breached: ["🚨", "Breached", "Found in known data breaches — attackers try these first. Change them now."],
    reused: ["♻", "Reused", "The same password on more than one item — one leak opens them all."],
    weak: ["🧩", "Weak", "Short or easy to guess."],
    old: ["⏳", "Old", `Not changed for over ${Math.round(r.oldDays / 365)} year.`],
    no2fa: ["🔐", "No 2FA", "These sites offer codes from an authenticator app. Turn 2FA on at the site, then add the code here (Edit → Scan QR code)."],
  };
  const tiles = h("div", { class: "tiles" }, Object.entries(DESC).map(([k, [ico, label]]) => {
    const n = r.counts[k];
    const unchecked = k === "breached" && (!r.breach.enabled || r.breach.state === "idle");
    const t = h("button", { class: "tile " + k + (n ? " bad" : ""), type: "button", "aria-pressed": "false", onclick: () => { filter = filter === k ? null : k; draw(); } },
      h("span", { class: "tile-n" }, unchecked ? "–" : String(n)), h("span", { class: "tile-l" }, ico + " " + label));
    return t;
  }));
  const breachCard = h("div", { class: "card" });
  const drawBreach = () => {
    const b = r.breach;
    if (!b.allowed) {
      mount(breachCard, h("h3", null, "Breach check"), h("p", { class: "hint" }, "Off for this household. An admin can allow it in Admin → App settings (it needs internet)."));
      return;
    }
    if (!b.enabled) {
      mount(breachCard, h("h3", null, "Breach check"),
        h("p", { class: "hint" }, "Check your passwords against Have I Been Pwned's list of passwords from known data breaches. Only the first 5 characters of each password's SHA-1 hash leave Home Assistant — never the password."),
        h("button", { class: "btn", type: "button", onclick: async () => {
          try { state.me = Object.assign(state.me, await api("api/me/settings", { method: "PUT", body: { breachCheck: true } })); r.breach.enabled = true; drawBreach(); } catch (e) { fail(e); }
        } }, "Turn on the breach check"));
      return;
    }
    const running = b.state === "running";
    mount(breachCard, h("h3", null, "Breach check"),
      h("p", { class: "hint" }, running ? `Checking… ${b.done} of ${b.total}` :
        b.state === "done" ? `Checked ${r.breachChecked} of ${r.total} passwords${b.finished ? " at " + fmtWhen(b.finished) : ""}.` +
          (b.errors ? ` Couldn't check ${b.errors} — the app may be offline.` : "") + " Results are forgotten when you lock." :
          "Only the first 5 characters of each password's SHA-1 hash are sent — never the password."),
      running ? h("div", { class: "meter s3" }, (() => { const bar = h("div"); bar.style.width = Math.round(100 * b.done / Math.max(1, b.total)) + "%"; return bar; })()) : null,
      h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", disabled: running, onclick: async () => {
        try { r.breach = Object.assign(r.breach, await api("api/password-health/breach-check", { method: "POST" })); drawBreach(); poll(); } catch (e) { fail(e); }
      } }, running ? "Checking…" : b.state === "done" ? "Check again" : "Check now")));
  };
  const poll = () => {
    clearInterval(state.healthPoll);
    state.healthPoll = setInterval(async () => {
      if (state.page !== "health") { clearInterval(state.healthPoll); return; }
      try {
        const nr = await api("api/password-health");
        Object.assign(r, nr);
        if (nr.breach.state !== "running") { clearInterval(state.healthPoll); state.healthMap = new Map(nr.items.map((x) => [x.vaultId + ":" + x.id, x.issues])); state.health = nr; renderSidebar(); mount(box, content()); return; }
        drawBreach();
      } catch (e) { clearInterval(state.healthPoll); }
    }, 1000);
  };
  const row = (x) => h("div", { class: "item-row multi", role: "listitem", tabindex: "0", onclick: () => openItemFrom(x), onkeydown: (e) => { if (e.key === "Enter") openItemFrom(x); } },
    avatar(x.title),
    h("div", { class: "grow" }, h("div", { class: "t" }, x.title || "(no name)"),
      h("div", { class: "s" }, [[x.vaultName, ...x.folderPath].join(" › "), x.username].filter(Boolean).join(" · ")),
      h("div", { class: "s" }, [
        x.breachCount ? `Found in ${x.breachCount.toLocaleString()} breaches` : null,
        x.issues.includes("reused") ? `Same password as ${(r.reuseGroups[x.reuseGroup - 1] || []).filter((y) => !(y.id === x.id && y.vaultId === x.vaultId)).map((y) => y.title).slice(0, 3).join(", ")}` : null,
        x.issues.includes("weak") ? `Strength: ${x.strength}` : null,
        x.issues.includes("old") && x.ageDays ? `Changed ${Math.floor(x.ageDays / 365)} yr ${Math.floor((x.ageDays % 365) / 30)} mo ago` : null,
      ].filter(Boolean).join(" · "))),
    h("span", { class: "badges" }, x.issues.map((k) => issueChip(k))));
  const content = () => {
    const list = filter ? r.items.filter((x) => x.issues.includes(filter)) : r.items;
    tiles.querySelectorAll(".tile").forEach((t) => { const on = t.classList.contains(filter); t.classList.toggle("on", !!on); t.setAttribute("aria-pressed", on ? "true" : "false"); });
    return h("div", null,
      r.lockedVaults.length ? h("div", { class: "notice" }, `${r.lockedVaults.join(", ")} ${r.lockedVaults.length === 1 ? "is" : "are"} locked — open to include ${r.lockedVaults.length === 1 ? "it" : "them"}.`) : null,
      h("div", { class: "card flush" }, h("div", { class: "list-title pad" }, filter ? `${DESC[filter][1]} (${list.length})` : `Needs attention (${list.length})`),
        list.length ? h("div", { class: "list", role: "list" }, list.map(row)) : h("div", { class: "empty" }, r.total ? "🎉 Nothing to fix here." : "No logins with passwords yet.")),
      filter ? h("p", { class: "hint" }, DESC[filter][2]) : null,
      h("p", { class: "hint" }, "Tip: search with is:weak, is:reused, is:old, is:breached or is:no2fa."));
  };
  const draw = () => mount(box, content());
  drawBreach();
  draw();
  if (r.breach.state === "running") poll();
  return h("div", null, h("h2", null, "Password health"),
    h("p", { class: "hint" }, `${r.total} login${r.total === 1 ? "" : "s"} with a password in the vaults you have open. Worked out in Home Assistant; nothing is sent anywhere unless you run the breach check.`),
    tiles, breachCard, box);
}
function breachHint(input) {
  const out = h("div", { class: "hint" });
  if (!state.me || !state.me.breachCheck) return out;
  const run = debounce(async () => {
    const v = input.value;
    if (!v || v.length < 4) { out.textContent = ""; out.className = "hint"; return; }
    try {
      const r = await api("api/breach/check", { method: "POST", body: { password: v } });
      if (input.value !== v) return;
      if (r.count === null) { out.textContent = "Couldn't check for breaches (offline?)."; out.className = "hint"; }
      else if (r.count > 0) { out.textContent = `⚠ Found in ${r.count.toLocaleString()} data breaches — choose another.`; out.className = "error"; }
      else { out.textContent = "✓ Not found in known breaches."; out.className = "hint"; }
    } catch (e) { out.textContent = ""; }
  }, 900);
  input.addEventListener("input", run);
  return out;
}

// ---------- QR codes: drawing (Wi-Fi on the household sheet) ----------
function qrSvg(text, px) {
  const q = QR.encode(text, "M");
  const n = q.size + 8;
  let d = "";
  q.modules.forEach((r, y) => r.forEach((on, x) => { if (on) d += `M${x + 4} ${y + 4}h1v1h-1z`; }));
  return svgEl("svg", { class: "qr", viewBox: `0 0 ${n} ${n}`, width: px, height: px, "shape-rendering": "crispEdges", role: "img", "aria-label": "QR code" },
    svgEl("rect", { width: n, height: n, fill: "#fff" }), svgEl("path", { d, fill: "#000" }));
}
function wifiText(w) {
  const esc = (t) => String(t || "").replace(/([\\;,:"])/g, "\\$1");
  return w.security === "nopass" ? `WIFI:T:nopass;S:${esc(w.ssid)};;` : `WIFI:T:${w.security};S:${esc(w.ssid)};P:${esc(w.password)};;`;
}

// ---------- guest Wi-Fi on the Home Assistant dashboard ----------
function guestWifiDialog(d) {
  const sec = h("select", { "aria-label": "Security" }, h("option", { value: "WPA" }, "WPA / WPA2 / WPA3"), h("option", { value: "WEP" }, "WEP"), h("option", { value: "nopass" }, "Open network (no password)"));
  const showPw = h("input", { type: "checkbox" });
  const err = h("div", { class: "error" });
  const m = openModal("Guest Wi-Fi on the dashboard", h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    try {
      await api(`api/vaults/${d.vaultId}/items/${d.id}/guest-wifi`, { method: "PUT", body: { security: sec.value, showPassword: showPw.checked } });
      m.close(); toast("On the dashboard — add a Picture entity card for sensor.household_vault_guest_wifi", { ms: 7000 });
      await afterChange(); state.me.guestWifi = true;
    } catch (x) { err.textContent = x.message; }
  } },
  h("p", null, `Guests scan a QR code to join “${d.username || d.title}”. It's shown on the app's Guest Wi-Fi page (no unlock needed) and on your Home Assistant dashboard, as the picture of sensor.household_vault_guest_wifi.`),
  h("div", { class: "notice" }, "This network's password leaves the encrypted vault: the QR code contains it, it's kept readable by the app and Home Assistant (and in Home Assistant's history and backups), and anyone who can see your dashboard can join. Use it for a guest network, not your main one. The other Household members are told."),
  field("Security", sec), h("label", { class: "check" }, showPw, "Also show the password as text (for devices that can't scan)"),
  h("p", { class: "hint" }, "It follows this item: change the password here and the dashboard updates; delete the item to remove it."), err,
  h("div", { class: "actions" },
    d.guestWifi ? h("button", { class: "btn danger", type: "button", onclick: async () => { try { await api("api/guest-wifi", { method: "DELETE" }); m.close(); toast("Taken off the dashboard"); state.me.guestWifi = false; await afterChange(); } catch (x) { fail(x); } } }, "Stop showing") : null,
    h("button", { class: "btn primary", type: "submit" }, d.guestWifi ? "Update" : "Show it"))));
}
async function guestPage() {
  const g = await api("api/guest-wifi");
  if (!g.published) return h("div", null, h("h2", null, "📶 Guest Wi-Fi"), h("div", { class: "card" }, h("p", null, "No guest network is shown yet."),
    h("p", { class: "hint" }, "Someone who can edit Household opens the Wi-Fi item → ⋯ → Show as guest Wi-Fi on the dashboard.")));
  const pw = h("span", { class: "mono" }, g.security === "nopass" ? "(none — open network)" : "••••••••");
  let shown = false;
  return h("div", null, h("h2", null, "📶 Guest Wi-Fi"),
    h("div", { class: "card guest-card" },
      h("img", { src: g.picture, alt: `QR code to join ${g.ssid}`, class: "guest-qr" }),
      h("div", { class: "kv" }, h("span", { class: "k" }, "Network"), h("strong", null, g.ssid),
        h("span", { class: "k" }, "Password"), h("span", null, pw, " ", g.security === "nopass" ? null : h("button", { class: "icon-btn", type: "button", "aria-label": "Show password", onclick: () => { shown = !shown; pw.textContent = shown ? g.password : "••••••••"; } }, "👁"),
          g.security === "nopass" ? null : h("button", { class: "icon-btn", type: "button", "aria-label": "Copy password", onclick: () => copyText(g.password, "plain") }, "📋"))),
      h("p", { class: "hint" }, "Point a phone's camera at the code to join.")),
    h("div", { class: "card" }, h("h3", null, "On your dashboard"),
      h("p", { class: "hint" }, "Edit a dashboard → Add card → Picture entity → entity ", h("code", null, g.entity), ", then turn on “Show name” / “Show state”. It updates by itself when the Wi-Fi item changes.")));
}

// ---------- household sheet (SPEC §12.3) ----------
const FIELD_LABEL = { UserName: "Username", Password: "Password", URL: "Website", Notes: "Notes", Cardholder: "Name on card", Expiry: "Expiry" };
function sheetAllowed(v) { return v && (v.kind === "household" || (v.kind === "personal" && v.mine)); }
function sheetDialog(d) {
  const st = d.sheet || { include: false, fields: [], wifi: null };
  const inc = h("input", { type: "checkbox", checked: st.include });
  const boxes = d.sheetFields.map((f) => ({ f, el: h("input", { type: "checkbox", checked: st.fields.includes(f) || (!st.include && ["UserName", "Password"].includes(f)) }) }));
  const wifi = h("select", { "aria-label": "Wi-Fi QR code" }, h("option", { value: "" }, "No Wi-Fi QR code"), h("option", { value: "WPA" }, "Wi-Fi QR code — WPA / WPA2 / WPA3"),
    h("option", { value: "WEP" }, "Wi-Fi QR code — WEP"), h("option", { value: "nopass" }, "Wi-Fi QR code — open network"));
  wifi.value = st.wifi || "";
  const opts = h("div", null,
    h("div", { class: "nav-sec" }, "Print these fields"),
    boxes.length ? boxes.map(({ f, el }) => h("label", { class: "check" }, el, FIELD_LABEL[f] || f)) : h("p", { class: "hint" }, "This item has no printable fields — only its name will be printed."),
    d.type === "login" ? field("Wi-Fi", wifi, "Guests scan it to join. The network name is the Username (or the item's name); the password is the Password.") : null,
    h("p", { class: "hint" }, "Card numbers, CVVs, PINs and 2FA secrets are never printed."));
  const sync = () => { opts.hidden = !inc.checked; };
  inc.addEventListener("change", sync); sync();
  const err = h("div", { class: "error" });
  const m = openModal(`Household sheet — ${d.title}`, h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    try {
      await api(`api/vaults/${d.vaultId}/items/${d.id}/sheet`, { method: "PUT", body: { include: inc.checked, fields: boxes.filter((b) => b.el.checked).map((b) => b.f), wifi: wifi.value || null } });
      m.close(); toast(inc.checked ? "On the household sheet" : "Taken off the sheet"); await afterChange();
    } catch (x) { err.textContent = x.message; }
  } }, h("p", { class: "hint" }, "A printed page for the fridge or a drawer — the Wi-Fi password, the alarm code, the plumber's number."),
  h("label", { class: "check" }, inc, "Include on the household sheet"), opts, err,
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Save"))));
}
async function printSheet(v) {
  if (!await confirmDialog("Print household sheet", "This prints secrets on paper. Anyone who sees the sheet can read them — keep it somewhere safe and shred old copies.", "Print")) return;
  let r;
  try { r = await api(`api/vaults/${v.id}/sheet`); } catch (e) { fail(e); return; }
  if (!r.items.length) { toast("Nothing is on the sheet yet — use an item's ⋯ → Household sheet….", { ms: 5000 }); return; }
  mount($("#printRoot"), h("div", { class: "kit sheet" },
    h("h1", null, `${r.vault === "Household" ? "Household" : state.me.name + "'s"} sheet`),
    h("p", null, `Printed ${new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })} · Keep this somewhere safe. Shred old copies.`),
    r.items.map((it) => h("div", { class: "sheet-item" },
      h("div", { class: "sheet-text" }, h("h2", null, it.title),
        it.fields.map((f) => h("div", { class: "sheet-field" }, h("span", { class: "k" }, FIELD_LABEL[f.name] || f.name), h("span", { class: "v" }, f.value)))),
      it.wifi ? h("div", { class: "sheet-qr" }, qrSvg(wifiText(it.wifi), 150), h("div", { class: "cap" }, "Scan to join the Wi-Fi")) : null))));
  printNow();
}

// ---------- scanning 2FA QR codes (SPEC §12.2) ----------
async function decodeImageSource(src, w, h0, budgetMs) {
  // src: ImageBitmap / video / canvas
  const max = 1600, k = Math.min(1, max / Math.max(w, h0));
  const cw = Math.max(1, Math.round(w * k)), ch = Math.max(1, Math.round(h0 * k));
  const c = document.createElement("canvas"); c.width = cw; c.height = ch;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, cw, ch);
  ctx.drawImage(src, 0, 0, cw, ch);
  if ("BarcodeDetector" in window) {
    try {
      const det = new window.BarcodeDetector({ formats: ["qr_code"] });
      const found = await det.detect(c);
      if (found.length) return found[0].rawValue;
    } catch (e) { /* fall back to our decoder */ }
  }
  return QR.decode(ctx.getImageData(0, 0, cw, ch), { budgetMs });
}
function scanDialog(onTotp, opts = {}) {
  // onTotp(uri): one TOTP link for the editor; a Google Authenticator export opens the import preview
  const canCamera = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
  const status = h("div", { class: "hint", role: "status" });
  const body = h("div");
  let stream = null, timer = null, busy = false;
  const stop = () => { clearInterval(timer); timer = null; if (stream) stream.getTracks().forEach((t) => t.stop()); stream = null; };
  const handle = async (text) => {
    if (!text) return false;
    try {
      const r = await api("api/totp/parse", { method: "POST", body: { text } });
      stop();
      if (r.kind === "totp" && !onTotp) { m.close(); migrationDialog({ entries: [{ issuer: r.issuer, account: r.account, uri: r.uri, ok: true }], batch: [1, 1] }); return true; }
      if (r.kind === "totp") { m.close(); onTotp(r.uri, r); return true; }
      if (r.kind === "migration") {
        const ok = r.entries.filter((x) => x.ok);
        if (onTotp && !opts.importMode && ok.length === 1 && r.entries.length === 1) { m.close(); onTotp(ok[0].uri, ok[0]); return true; }
        m.close(); migrationDialog(r); return true;
      }
    } catch (e) { status.textContent = e.message; status.className = "error"; }
    return false;
  };
  const tabs = h("div", { class: "tabs" });
  const show = (which) => {
    stop();
    status.textContent = ""; status.className = "hint";
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === which));
    if (which === "camera") {
      const video = h("video", { class: "scan-video", playsinline: "true", muted: "true", autoplay: "true" });
      video.muted = true;
      mount(body, h("div", { class: "scan-frame" }, video), h("p", { class: "hint" }, "Point the camera at the QR code your site shows."));
      navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }).then((st) => {
        stream = st; video.srcObject = st; video.play().catch(() => {});
        timer = setInterval(async () => {
          if (busy || !video.videoWidth) return;
          busy = true;
          try { const t = await decodeImageSource(video, video.videoWidth, video.videoHeight, 250); if (t) await handle(t); }
          finally { busy = false; }
        }, 300);
      }).catch((e) => {
        show("image");
        status.textContent = "Couldn't use the camera (" + (e.message || e.name) + ") — use a screenshot or paste the key.";
        status.className = "error";
      });
    } else if (which === "image") {
      const file = h("input", { type: "file", accept: "image/*", "aria-label": "Screenshot of the QR code" });
      const run = async (blob) => {
        status.textContent = "Reading…"; status.className = "hint";
        try {
          const bmp = await createImageBitmap(blob);
          const t = await decodeImageSource(bmp, bmp.width, bmp.height, 3000);
          if (!t) { status.textContent = "No QR code found in that image. Crop it closer, or paste the setup key instead."; status.className = "error"; return; }
          await handle(t);
        } catch (e) { status.textContent = "That image couldn't be read."; status.className = "error"; }
      };
      file.addEventListener("change", () => { if (file.files[0]) run(file.files[0]); });
      const zone = h("div", { class: "drop-zone", tabindex: "0" }, "Paste a screenshot here (Ctrl+V), drop it here, or choose a file.");
      zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drop-ok"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("drop-ok"));
      zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("drop-ok"); const f = e.dataTransfer.files[0]; if (f) run(f); });
      body.onpaste = (e) => {
        const it = Array.from(e.clipboardData.items || []).find((x) => x.type.startsWith("image/"));
        if (it) { e.preventDefault(); run(it.getAsFile()); }
      };
      mount(body, zone, file);
      setTimeout(() => zone.focus(), 30);
    } else {
      const ta = h("textarea", { "aria-label": "Setup link or key", placeholder: "otpauth://totp/…  or  otpauth-migration://…  or the setup key", spellcheck: "false" });
      mount(body, field("Setup link or key", ta, "Sites often show the key under “Can't scan the code?”."),
        h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => handle(ta.value.trim()) }, "Use it")));
      setTimeout(() => ta.focus(), 30);
    }
  };
  const tab = (k, label) => { const b = h("button", { class: "chip", type: "button", onclick: () => show(k) }, label); b.dataset.k = k; return b; };
  if (canCamera) tabs.appendChild(tab("camera", "📷 Camera"));
  tabs.appendChild(tab("image", "🖼 Screenshot"));
  tabs.appendChild(tab("text", "⌨ Paste the key"));
  const m = openModal(opts.importMode ? "Import from an authenticator app" : "Scan a 2FA QR code", h("div", null,
    opts.importMode ? h("p", { class: "hint" }, "In Google Authenticator: ⋮ → Transfer accounts → Export accounts. Scan each QR code it shows.") : null,
    tabs, body, status,
    canCamera ? null : h("p", { class: "hint" }, location.protocol === "http:" ? "The camera needs HTTPS — use a screenshot, or paste the key." : "No camera available here — use a screenshot, or paste the key."),
    h("p", { class: "hint" }, "The picture is read in your browser; only the resulting text is sent to Home Assistant.")), { wide: true, onClose: stop });
  show(canCamera ? "camera" : "image");
}
function migrationDialog(r) {
  const targets = editableVaults();
  if (!targets.length) { toast("Open a vault you can edit first.", { error: true }); return; }
  const rows = r.entries.map((x) => ({ x, el: h("input", { type: "checkbox", checked: x.ok && !x.existing, disabled: !x.ok }) }));
  const vaultSel = h("select", { "aria-label": "Vault" }, ...targets.map((v) => h("option", { value: v.id }, v.name)));
  const mine = targets.find((v) => v.kind === "personal" && v.mine);
  vaultSel.value = mine ? mine.id : targets[0].id;
  let folderSel = folderOptions(vaultSel.value, null);
  const fb = h("div", null, field("Folder", folderSel));
  vaultSel.addEventListener("change", () => { folderSel = folderOptions(vaultSel.value, null); mount(fb, field("Folder", folderSel)); });
  const err = h("div", { class: "error" });
  const go = h("button", { class: "btn primary", type: "submit" }, "Add");
  const m = openModal("Accounts from your authenticator", h("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    const chosen = rows.filter((row) => row.el.checked);
    if (!chosen.length) { err.textContent = "Choose at least one."; return; }
    go.disabled = true;
    let n = 0;
    try {
      for (const { x } of chosen) {
        await api(`api/vaults/${vaultSel.value}/items`, { method: "POST", body: { type: "login", title: x.issuer || x.account || "2FA code",
          username: x.account, totp: x.uri, folderId: folderSel.value || null } });
        n++;
      }
      m.close(); toast(`Added ${n} item${n === 1 ? "" : "s"} with 2FA codes` + (r.batch[1] > 1 ? ` — scan the other ${r.batch[1] - 1} export code${r.batch[1] > 2 ? "s" : ""} too` : ""), { ms: 6000 });
      await afterChange();
    } catch (x) { err.textContent = `Added ${n}; then: ${x.message}`; go.disabled = false; }
  } },
  r.batch[1] > 1 ? h("div", { class: "notice info" }, `This is export code ${r.batch[0]} of ${r.batch[1]}.`) : null,
  h("p", { class: "hint" }, "Each becomes a login with its 2FA code. If you already have a login for the site, you can move the code into it later (Edit → 2FA)."),
  h("div", { class: "list" }, rows.map(({ x, el }) => h("label", { class: "check" }, el, h("span", { class: "grow" }, h("strong", null, x.issuer || "(no issuer)"), " ", x.account,
    x.ok ? null : h("span", { class: "hint" }, " — " + x.reason), x.existing ? h("span", { class: "hint" }, ` — you already have this code in ${x.existing}`) : null)))),
  field("Add to vault", vaultSel), fb, err, h("div", { class: "actions" }, go)), { wide: true });
}

// ---------- pages: settings, admin, whoami ----------
async function showPage(page, anchor) {
  state.page = page;
  state.selected = null;
  closeNav();
  clearInterval(state.healthPoll);
  renderSidebar();
  const c = $("#content");
  c.className = "page-host";
  let content;
  try {
    content = page === "settings" ? await settingsPage() : page === "admin" ? await adminPage() : page === "health" ? await healthPage() : page === "guest" ? await guestPage() : await whoamiPage();
  } catch (e) { content = h("div", { class: "notice danger" }, e.message); }
  mount(c, h("div", { class: "page" }, content));
  if (anchor) { const el = document.getElementById("sec-" + anchor); if (el) el.scrollIntoView(); }
}
async function settingsPage() {
  const me = await api("api/me");
  state.me = Object.assign(state.me, me);
  const lockSel = h("select", { "aria-label": "Lock after" }, ...[1, 2, 5, 10, 15, 30, 60].map((n) => h("option", { value: n }, `${n} minute${n > 1 ? "s" : ""}`)));
  lockSel.value = String(me.autoLockMinutes);
  if (!lockSel.value) lockSel.appendChild(h("option", { value: me.autoLockMinutes }, `${me.autoLockMinutes} minutes`)), lockSel.value = String(me.autoLockMinutes);
  lockSel.addEventListener("change", async () => { try { const r = await api("api/me/settings", { method: "PUT", body: { autoLockMinutes: +lockSel.value } }); state.autoLock = r.autoLockMinutes; toast("Saved"); } catch (e) { fail(e); } });
  const hideSel = h("select", { "aria-label": "When I switch away" }, h("option", { value: -1 }, "Lock after the time above"),
    h("option", { value: 60 }, "Lock after 1 minute"), h("option", { value: 10 }, "Lock after 10 seconds"), h("option", { value: 0 }, "Lock at once"));
  hideSel.value = String(me.hideLockSeconds === undefined ? -1 : me.hideLockSeconds);
  hideSel.addEventListener("change", async () => { try { state.me = Object.assign(state.me, await api("api/me/settings", { method: "PUT", body: { hideLockSeconds: +hideSel.value } })); toast("Saved"); } catch (e) { fail(e); } });
  const prefToggle = (key, label) => {
    const box = h("input", { type: "checkbox", checked: me[key] });
    box.addEventListener("change", async () => { try { state.me = Object.assign(state.me, await api("api/me/settings", { method: "PUT", body: { [key]: box.checked } })); toast("Saved"); } catch (e) { fail(e); box.checked = !box.checked; } });
    return h("label", { class: "check" }, box, label);
  };
  const notifyCard = h("div", { class: "card", id: "sec-notify" }, h("h3", null, "Notifications"),
    h("p", { class: "hint" }, me.notifyLinked ? "Sent to your phone through Home Assistant. They never contain a password — just what happened."
      : "No phone is linked to you yet, so nothing is sent. In Home Assistant: Settings → People → you → Track device, and pick your phone with the Home Assistant Companion app — it's picked up here within 5 minutes. An admin can also add another notify service under Admin → People → 🔔." + (me.isAdmin ? " (That's you.)" : "")),
    prefToggle("securityAlerts", "Security alerts — wrong master passwords, your master password changed, someone downloaded a shared vault or printed the household sheet"),
    prefToggle("expiryAlerts", "Expiry reminders — cards, passports, insurance and anything else with an expiry date"),
    h("p", { class: "hint" }, "Emergency-access requests are always sent."));
  const notes = h("input", { type: "checkbox", checked: me.searchNotes });
  notes.addEventListener("change", async () => { try { await api("api/me/settings", { method: "PUT", body: { searchNotes: notes.checked } }); toast("Saved"); } catch (e) { fail(e); } });
  const breach = h("input", { type: "checkbox", checked: me.breachCheck, disabled: !me.breachAllowed });
  breach.addEventListener("change", async () => {
    try { state.me = Object.assign(state.me, await api("api/me/settings", { method: "PUT", body: { breachCheck: breach.checked } })); toast("Saved"); }
    catch (e) { fail(e); breach.checked = !breach.checked; }
  });
  const remind = h("select", { "aria-label": "Remind me to download" }, h("option", { value: 0 }, "Never"), h("option", { value: 30 }, "After 30 days"), h("option", { value: 60 }, "After 60 days"), h("option", { value: 90 }, "After 90 days"));
  remind.value = String(me.downloadReminderDays || 0);
  remind.addEventListener("change", async () => {
    try { state.me = Object.assign(state.me, await api("api/me/settings", { method: "PUT", body: { downloadReminderDays: +remind.value } })); state.reminderDismissed = false; toast("Saved"); }
    catch (e) { fail(e); }
  });
  const lastDl = h("p", { class: "hint" }, me.lastDownloadAll ? `Last downloaded ${fmtWhen(me.lastDownloadAll)}.` +
    (me.lastMasterChange && me.lastMasterChange > me.lastDownloadAll ? " Your master password changed since — that copy opens with the old one." : "") : "Not downloaded yet.");
  // change master password
  const cur = pwInput({ label: "Current master password" });
  const n1 = pwInput({ autocomplete: "new-password", label: "New master password" });
  const n2 = pwInput({ autocomplete: "new-password", label: "Type it again" });
  const cErr = h("div", { class: "error" });
  const changeForm = h("form", { onsubmit: async (e) => {
    e.preventDefault(); cErr.textContent = "";
    if (n1.input.value !== n2.input.value) { cErr.textContent = "The new passwords don't match."; return; }
    try {
      await api("api/me/password", { method: "POST", body: { current: cur.input.value, new: n1.input.value } });
      cur.input.value = n1.input.value = n2.input.value = "";
      toast("Master password changed");
      openModal("Master password changed", h("div", null, h("p", null, "Your other devices were locked. Please:"),
        h("ul", null, h("li", null, "print a new Emergency Kit;"), h("li", null, "download a new copy of your passwords, and delete old downloaded copies — they still open with the old password.")),
        h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: printKit }, "🖨 Emergency Kit"))));
    } catch (x) { cErr.textContent = x.message; }
  } }, field("Current master password", cur.el), field("New master password", n1.el), strengthMeter(n1.input), field("Type it again", n2.el), cErr,
    h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Change master password")));
  // download all
  const dl = pwInput({ label: "Master password" });
  const inclDel = h("input", { type: "checkbox" });
  const dlErr = h("div", { class: "error" });
  const dlForm = h("form", { onsubmit: async (e) => {
    e.preventDefault(); dlErr.textContent = "";
    const ok = await confirmDialog("Download all my passwords", "This file holds every password you can open, including Household and shared vaults. Keep it on your own devices; anyone with it and your master password can read everything. It's a copy — changes made in it don't come back here.", "Download");
    if (!ok) return;
    try {
      const res = await api("api/me/download-all", { method: "POST", body: { password: dl.input.value, includeDeleted: inclDel.checked }, raw: true });
      saveBlob(await res.blob(), filenameFrom(res) || "household-vault.kdbx");
      dl.input.value = "";
      try { state.me = Object.assign(state.me, await api("api/me")); lastDl.textContent = `Last downloaded ${fmtWhen(state.me.lastDownloadAll)}.`; } catch (x) { /* ignore */ }
      state.reminderDismissed = true;
      const locked = res.headers.get("X-Locked-Vaults");
      toast(locked ? `Downloaded. Not included (locked): ${locked.replace(/;/g, ", ")}` : "Downloaded", { ms: 5000 });
    } catch (x) { dlErr.textContent = x.message; }
  } }, h("p", { class: "hint" }, "One .kdbx file with everything you have open, locked with your master password. Opens in KeePassXC, KeePassDX (Android) and Strongbox (iPhone) — which can autofill from it."),
  field("Master password", dl.el), h("label", { class: "check" }, inclDel, "Include deleted items (Trash)"), dlErr,
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "⬇ Download all my passwords")),
  lastDl, field("Remind me to download a fresh copy", remind, "A banner after unlocking when your copy is older than this, or after you change your master password."),
  personalCopyNote(me.personalCopy));
  // import
  const file = h("input", { type: "file", accept: ".kdbx,.csv", "aria-label": "File to import" });
  const skipDup = h("input", { type: "checkbox", checked: true });
  const ipw = pwInput({ label: "File password" });
  const kf = h("input", { type: "file", "aria-label": "Key file (optional)" });
  const target = h("select", { "aria-label": "Import into" }, h("option", { value: "new" }, "A new shared vault"), ...editableVaults().map((v) => h("option", { value: v.id }, `A folder in ${v.name}`)));
  const iErr = h("div", { class: "error" });
  const importForm = h("form", { onsubmit: async (e) => {
    e.preventDefault(); iErr.textContent = "";
    if (!file.files[0]) { iErr.textContent = "Choose a .kdbx file."; return; }
    const fd = new FormData();
    fd.append("file", file.files[0]); fd.append("password", ipw.input.value); fd.append("target", target.value);
    fd.append("skipDuplicates", skipDup.checked ? "true" : "false");
    if (kf.files[0]) fd.append("keyfile", kf.files[0]);
    try {
      const r = await api("api/import", { method: "POST", form: fd });
      toast(`Imported ${r.items} item${r.items === 1 ? "" : "s"}` + (r.skipped ? ` — ${r.skipped} you already had were skipped` : "") + (r.keyFileDropped ? " — the key file isn't needed any more" : ""), { ms: 6000 });
      ipw.input.value = ""; file.value = "";
      if (r.format === "csv") openModal("Delete the export file", h("div", null, h("p", null, "The CSV file you imported holds your passwords unencrypted. Delete it now (and empty the recycle bin / Downloads), so it doesn't linger on this device."),
        h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => closeTopModal() }, "OK"))));
      await refreshVaults(); renderSidebar();
    }
    catch (x) { iErr.textContent = x.message; }
  } }, h("p", { class: "hint" }, "A KeePass file (.kdbx), or a CSV export from Chrome, Edge, Firefox, Bitwarden, LastPass, 1Password, Dashlane or Proton Pass."),
    field("File", file), field("Its password (KeePass files)", ipw.el), field("Key file (optional)", kf), field("Import into", target),
    h("label", { class: "check" }, skipDup, "Skip items I already have (same name, username, website and password)"), iErr,
    h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Import")));
  const emergencyCard = h("div", { class: "card", id: "sec-emergency" }, h("h3", null, "Emergency access"), h("p", { class: "hint" }, "…"));
  drawEmergency(emergencyCard);
  return h("div", null, h("h2", null, "Settings"),
    (() => { const c = h("div", { class: "card", id: "sec-quick" }, h("h3", null, "Quick unlock")); drawQuickUnlock(c); return c; })(),
    h("div", { class: "card" }, h("h3", null, "Locking"),
      field("Lock after", lockSel, "Used on all your devices. The vault also locks when you reload the page."),
      field("When I switch to another tab or app", hideSel, "On a shared computer, “Lock at once” is safest. On a phone you may want a little time to paste a password."),
      h("button", { class: "btn", type: "button", onclick: async () => { try { await api("api/lock-everywhere", { method: "POST", noLock: true }); } catch (e) { /* ok */ } lockLocal(); } }, "Lock everywhere")),
    notifyCard,
    h("div", { class: "card" }, h("h3", null, "Search"), h("label", { class: "check" }, notes, "Also search in notes")),
    h("div", { class: "card" }, h("h3", null, "Breach check"),
      h("label", { class: "check" }, breach, "Check my passwords against known data breaches (Have I Been Pwned)"),
      h("p", { class: "hint" }, me.breachAllowed ? "Only the first 5 characters of a password's SHA-1 hash are sent — never the password. Run it from Password health; new passwords are checked as you type them."
        : "Off for this household — an admin can allow it in Admin → App settings (it needs internet).")),
    h("div", { class: "card" }, h("h3", null, "Master password"), changeForm),
    emergencyCard,
    h("div", { class: "card", id: "sec-download" }, h("h3", null, "Download all my passwords"), dlForm),
    h("div", { class: "card" }, h("h3", null, "Import passwords"), importForm),
    h("div", { class: "card" }, h("h3", null, "Import 2FA codes from an authenticator app"),
      h("p", { class: "hint" }, "Move your codes out of Google Authenticator (or any app that exports a QR code): each account becomes a login with its code."),
      h("button", { class: "btn", type: "button", onclick: () => scanDialog(null, { importMode: true }) }, "📷 Scan an export QR code")),
    h("div", { class: "card" }, h("h3", null, "Emergency Kit"), h("p", { class: "hint" }, "A printable sheet with how to reach your passwords if Home Assistant is down."),
      h("button", { class: "btn", type: "button", onclick: () => showKit(false) }, "Emergency Kit…")));
}
const EM_STATUS = { ready: "Can ask", requested: "Asked", denied: "Denied", granted: "Has access" };
async function drawEmergency(card) {
  let r, people = [];
  try { [r, people] = await Promise.all([api("api/emergency"), api("api/users").then((x) => x.users)]); }
  catch (e) { mount(card, h("h3", null, "Emergency access"), h("p", { class: "error" }, e.message)); return; }
  const redraw = (nr) => { if (nr) r = nr; paint(); };
  const act = (path, method = "POST") => async () => { try { redraw(await api(path, { method })); toast("Saved"); } catch (e) { fail(e); } };
  const paint = () => {
    const m = r.mine;
    const rows = m.contacts.map((c) => [
      c.name, `${c.waitDays} day${c.waitDays === 1 ? "" : "s"}`,
      h("span", null, EM_STATUS[c.status], c.status === "requested" ? h("div", { class: "hint" }, `Gets access ${fmtWhen(c.releaseAt)}`) : null),
      h("span", { class: "row wrap" },
        c.status === "requested" ? h("button", { class: "btn small danger", type: "button", onclick: act(`api/emergency/contacts/${c.id}/deny`) }, "Deny") : null,
        c.status === "requested" ? h("button", { class: "btn small", type: "button", onclick: act(`api/emergency/contacts/${c.id}/approve`) }, "Approve now") : null,
        h("button", { class: "btn small ghost", type: "button", onclick: async () => {
          if (!await confirmDialog("Remove emergency contact", `Remove ${c.name}?` + (c.status === "granted" ? " They lose your emergency items at once (the items get a new password). They keep anything they already saw." : ""), "Remove", true)) return;
          act(`api/emergency/contacts/${c.id}`, "DELETE")();
        } }, "Remove"))]);
    const free = people.filter((p) => !m.contacts.some((c) => c.id === p.id));
    const who = h("select", { "aria-label": "Person" }, free.map((p) => h("option", { value: p.id }, p.name)));
    const wait = h("select", { "aria-label": "Waiting period" }, [1, 2, 3, 7, 14, 30].map((n) => h("option", { value: n }, `${n} day${n > 1 ? "s" : ""}`)));
    wait.value = "7";
    const trusted = r.trustedBy.map((t) => h("div", { class: "notice info row wrap" },
      h("span", { class: "grow" }, t.status === "granted" ? `✅ You have ${t.name}'s emergency items — see “${t.name}'s emergency items” in the sidebar.`
        : t.status === "requested" && t.onHold ? `⏸ You asked for ${t.name}'s emergency items, but an admin has turned ${t.name}'s access off, so nothing is released until it's back on.`
        : t.status === "requested" ? `⏳ You asked for ${t.name}'s emergency items. You get them ${fmtWhen(t.releaseAt)} unless ${t.name} denies it.`
        : t.status === "denied" ? `${t.name} denied your last request. You can ask again after a day.`
        : `${t.name} trusts you with their emergency items. If something happens to them, ask — you get them after ${t.waitDays} day${t.waitDays === 1 ? "" : "s"} unless they deny it.`),
      t.status === "ready" || t.status === "denied" ? h("button", { class: "btn small", type: "button", onclick: async () => {
        if (!await confirmDialog("Ask for emergency access", `Ask for ${t.name}'s emergency items? ${t.name} is told at once and can deny it; otherwise you get them after ${t.waitDays} day${t.waitDays === 1 ? "" : "s"}.`, "Ask")) return;
        act(`api/emergency/${t.ownerId}/request`)();
      } }, "Ask for access") : null));
    mount(card, h("h3", null, "Emergency access"),
      h("p", { class: "hint" }, "If something happens to you, people you trust can get the items you choose — after a waiting period in which you can say no. Mark items in your Personal vault with ⋯ → 🆘 Include in emergency access."),
      h("p", null, m.itemCount === null ? "" : m.itemCount ? `${m.itemCount} item${m.itemCount === 1 ? "" : "s"} included.` : "No items included yet."),
      m.contacts.length ? dataTable(["Contact", "Waiting period", "Status", ""], rows, "compact") : h("p", { class: "hint" }, "No emergency contacts yet."),
      free.length ? h("div", { class: "row wrap" }, field("Add a contact", who), field("Waiting period", wait),
        h("button", { class: "btn primary", type: "button", onclick: async () => {
          try { redraw(await api("api/emergency/contacts", { method: "POST", body: { userId: who.value, waitDays: +wait.value } })); toast("Added"); } catch (e) { fail(e); }
        } }, "Add")) : h("p", { class: "hint" }, m.contacts.length ? "" : "Only people who are set up can be emergency contacts."),
      trusted.length ? h("div", null, h("div", { class: "nav-sec" }, "People who trust you"), trusted) : null);
  };
  paint();
}
// Phones come from Home Assistant (Settings → People → Track device); they count once the person is active here.
const haPhones = (x) => (x.ha && x.ha.phones ? x.ha.phones : []).filter((p) => p.service);
const phonesCount = (x) => (!x.disabled && x.status === "active" ? haPhones(x).length : 0);
async function adminPage() {
  const [u, s] = await Promise.all([api("api/admin/users"), api("api/admin/settings")]);
  const statusText = { none: "Not set up", temporary: "Waiting for first unlock", active: "Active" };
  const rows = u.users.map((x) => [
    h("div", { class: "person" }, h("strong", null, x.name), x.you ? " (you)" : "", h("div", { class: "hint" }, [x.username, x.person].filter(Boolean).join(" · "))),
    h("span", null, x.disabled && x.status !== "active" ? "No access" : statusText[x.status], x.blockedUntil ? h("div", { class: "hint" }, "Too many wrong passwords") : null),
    h("span", null, x.status === "none" ? h("button", { class: "btn small primary", type: "button", onclick: () => setupDialog(x) }, "Enable and set up")
      : (() => {
        const t = h("input", { type: "checkbox", checked: !x.disabled, "aria-label": `Access for ${x.name}` });
        t.addEventListener("change", async () => {
          if (!t.checked && !await confirmDialog("Turn off access", `Turn off Household Vault for ${x.name}? They're signed out, removed from Household and shared vaults (which get new passwords). Their Personal vault is kept.`, "Turn off", true)) { t.checked = true; return; }
          try { await api(`api/users/${x.id}`, { method: "PATCH", body: { disabled: !t.checked } }); toast("Saved"); } catch (e) { fail(e); t.checked = !t.checked; }
        });
        return h("label", { class: "check" }, t, "Access");
      })()),
    h("span", { class: "row wrap" },
      h("button", { class: "btn small", type: "button", title: "Notifications", onclick: () => notifyDialog(x) }, "🔔 ", phonesCount(x) + x.notify.length ? String(phonesCount(x) + x.notify.length) : "–"),
      x.status !== "none" ? h("button", { class: "btn small danger", type: "button", onclick: () => resetDialog(x) }, "Reset…") : null,
      x.blockedUntil ? h("button", { class: "btn small", type: "button", onclick: async () => { await api(`api/users/${x.id}/unblock`, { method: "POST" }); toast("Unblocked"); } }, "Unblock") : null)]);
  const inputs = {};
  const settingsForm = h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const body = {};
    for (const [k, el] of Object.entries(inputs)) body[k] = el.type === "checkbox" ? el.checked : +el.value;
    try { await api("api/admin/settings", { method: "PUT", body }); toast("Saved"); } catch (x) { fail(x); }
  } }, Object.entries(s.values).map(([k, v]) => {
    const el = typeof v === "boolean" ? h("input", { type: "checkbox", checked: v }) : h("input", { type: "number", value: v });
    inputs[k] = el;
    return typeof v === "boolean" ? h("label", { class: "check" }, el, s.labels[k]) : field(s.labels[k], el);
  }), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Save")));
  const restoreFile = h("input", { type: "file", accept: ".zip", "aria-label": "Backup file" });
  return h("div", null, h("h2", null, "Admin"),
    h("p", { class: "hint" }, "Admins manage who can use Household Vault. They can never open anyone's vaults."),
    h("div", { class: "notice" }, h("strong", null, "Experimental. "), "Household Vault hasn't had an independent security review yet. Ask everyone to keep their own KeePass copy (Settings → Download all my passwords), and keep Home Assistant backups of this app."),
    h("div", { class: "card" }, h("h3", null, "People"),
      h("p", { class: "hint" }, "Everyone with a Home Assistant login is listed, with no access until you enable and set them up. Setting up gives a one-time password; they choose their own on first unlock."),
      h("p", { class: "hint" }, "📱 Alerts go to each person's phone from Home Assistant: Settings → People → (the person) → Track device, picking their phone with the Home Assistant Companion app. Set it up there once and every household app uses it. 🔔 shows their phones and lets you add an extra notify service (a speaker, a second service). ",
        h("button", { class: "link-btn", type: "button", onclick: async () => { try { await api("api/admin/users?refresh=1"); showPage("admin"); toast("Read from Home Assistant"); } catch (e) { fail(e); } } }, "Check Home Assistant again")),
      dataTable(["Person", "Status", "Access", ""], rows, "people")),
    h("div", { class: "card" }, h("h3", null, "App settings"), settingsForm,
      h("p", { class: "hint" }, `Key derivation: Argon2id ${s.kdf.memoryMiB} MiB × ${s.kdf.iterations || "?"} iterations (tuned for this machine). ${s.sessions} unlocked session(s), ${s.openVaults} open vault(s). Version ${s.version}.`)),
    personalCopiesCard(s.personalCopies),
    h("div", { class: "card" }, h("h3", null, "Backup and restore"),
      h("p", { class: "hint" }, "The backup holds only encrypted vault files and settings — nobody can read passwords from it without the vaults' passwords."),
      h("button", { class: "btn", type: "button", onclick: async () => { try { const res = await api("api/admin-storage-download-db", { raw: true }); saveBlob(await res.blob(), filenameFrom(res) || "household-vault-backup.zip"); } catch (e) { fail(e); } } }, "⬇ Download backup"),
      h("div", { class: "row wrap" }, restoreFile, h("button", { class: "btn danger", type: "button", onclick: async () => {
        if (!restoreFile.files[0]) { toast("Choose a backup .zip first.", { error: true }); return; }
        if (!await confirmDialog("Restore backup", "Replace everything with this backup? Everyone is locked, and changes made after the backup are lost — including people removed and passwords changed since.", "Restore", true)) return;
        const fd = new FormData(); fd.append("file", restoreFile.files[0]);
        try { const r = await api("api/admin-storage-import-db", { method: "POST", form: fd }); openModal("Backup restored", h("div", null,
          h("p", null, "Everyone must unlock again."), r.undone.length ? h("div", null, h("p", null, "These changes were undone — redo them:"), undoneList(r.undone)) : null,
          h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: () => location.reload() }, "OK")))); }
        catch (e) { fail(e); }
      } }, "Restore…"))));
}
const NOTIFY_RE = /^notify\.[a-z0-9_]+$/;
async function notifyDialog(x) {
  let avail = { available: false, services: [], entities: [], error: null };
  try { avail = await api("api/admin/notify-services"); } catch (e) { /* manual entry still works */ }
  const body = h("div");
  const draw = () => {
    const have = new Set(x.notify);
    const opts = [...avail.services, ...avail.entities].filter((n) => !have.has(n) && n !== "notify.persistent_notification");
    const sel = opts.length ? h("select", { "aria-label": "Notify service" }, h("option", { value: "" }, "Choose…"), opts.map((n) => h("option", { value: n }, n))) : null;
    const manual = h("input", { type: "text", placeholder: "notify.mobile_app_phone", "aria-label": "Notify service", autocomplete: "off", spellcheck: "false", maxlength: 120 });
    const result = h("div", { class: "hint" });
    const add = h("button", { class: "btn small primary", type: "button", onclick: async () => {
      let v = (manual.value.trim() || (sel && sel.value) || "");
      if (!v) { toast("Choose or type a notify service first.", { error: true }); return; }
      if (!v.includes(".")) v = "notify." + v;
      if (!NOTIFY_RE.test(v)) { toast("A notify service looks like notify.mobile_app_phone (lower-case letters, digits and _).", { error: true }); return; }
      try { x.notify = (await api(`api/admin/users/${x.id}/notify`, { method: "POST", body: { service: v } })).notify; draw(); } catch (e) { fail(e); }
    } }, "Add");
    // phones from Home Assistant (Settings → People) — read-only here
    const ha = x.ha || { known: false, phones: [] };
    const active = !x.disabled && x.status === "active";
    const phoneChips = !ha.known
      ? h("p", { class: "hint" }, "Home Assistant's people couldn't be read yet.")
      : !ha.person
        ? h("p", { class: "hint" }, "No Home Assistant person is linked to this login — in Home Assistant: Settings → People → (the person) → Allow person to login.")
        : ha.phones.length
          ? h("div", { class: "row wrap" }, ha.phones.map((p) => h("span", { class: p.service ? "chip on" : "chip warn", title: p.service ? `${p.tracker} → ${p.service}` : `${p.tracker}: Home Assistant has no notify action for this phone` },
              "📱 " + p.label, h("span", { class: "hint" }, p.service ? " " + p.service : " — Companion app action not found"))))
          : h("p", { class: "hint" }, `${ha.personName} has no phone in Home Assistant — Settings → People → ${ha.personName} → Track device.`);
    const reachable = (active ? haPhones(x).length : 0) + x.notify.length;
    const again = h("button", { class: "link-btn", type: "button", onclick: async () => {
      try {
        const r = await api("api/admin/users?refresh=1");
        const me2 = r.users.find((y) => y.id === x.id);
        if (me2) { x.ha = me2.ha; x.notify = me2.notify; }
        draw(); toast("Read from Home Assistant");
      } catch (e) { fail(e); }
    } }, "Check Home Assistant again");
    const test = h("button", { class: "btn small", type: "button", disabled: !reachable,
      title: reachable ? "Send a short test notification to their phones and every service listed" : "Link a phone in Home Assistant or add a notify service first", onclick: async () => {
      try {
        const r = await api(`api/admin/users/${x.id}/notify/test`, { method: "POST" });
        result.textContent = r.results.map((y) => `${y.service}: ${y.ok ? "sent ✓" : "failed" + (y.hint ? " — " + y.hint : "")}`).join(" · ");
      } catch (e) { fail(e); }
    } }, "Send a test");
    mount(body,
      h("p", { class: "hint" }, `Alerts for ${x.name}: security alerts, emergency access and expiry reminders. They never contain a password.`),
      h("div", { class: "nav-sec" }, "Phones — from Home Assistant"), phoneChips,
      haPhones(x).length && !active ? h("p", { class: "hint" }, `Used once ${x.name} is enabled and set up here.`) : null,
      h("p", { class: "hint" }, "Set in Home Assistant: Settings → People → (the person) → Track device, with the Home Assistant Companion app on the phone. ", again),
      h("div", { class: "nav-sec" }, "Also — extra notify services"),
      avail.available ? null : h("div", { class: "notice" }, (avail.error || "Home Assistant's notify services couldn't be listed.") + " You can still type one."),
      x.notify.length ? h("div", { class: "row wrap" }, x.notify.map((n) => h("span", { class: "chip on" }, n, " ",
        h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${n}`, onclick: async () => {
          try { x.notify = (await api(`api/admin/users/${x.id}/notify/${encodeURIComponent(n)}`, { method: "DELETE" })).notify; draw(); } catch (e) { fail(e); }
        } }, "✕")))) : h("p", { class: "hint" }, reachable || haPhones(x).length ? "None." : "None — nothing is sent to them until a phone is linked in Home Assistant or a service is added here."),
      h("div", { class: "row wrap" }, sel, manual, add, test), result);
  };
  draw();
  openModal(`Notifications — ${x.name}`, body, { onClose: () => { if (state.page === "admin") showPage("admin"); } });
}
function setupDialog(x) {
  const go = h("button", { class: "btn primary", type: "button" }, "Enable and set up");
  const box = h("div", null, h("p", null, `This gives ${x.name} access and makes their Personal vault with a one-time password. You'll see it once — hand it over in person. They choose their own master password the first time they unlock, and you won't know it.`),
    h("div", { class: "actions" }, go));
  const m = openModal(`Set up ${x.name}`, box, { sticky: false });
  go.addEventListener("click", async () => {
    try {
      const r = await api(`api/users/${x.id}/setup`, { method: "POST" });
      mount(box, h("p", null, `One-time password for ${x.name}:`), h("div", { class: "pw-big mono" }, r.oneTimePassword),
        h("p", { class: "hint" }, "It's shown only now. If it's lost, use Reset and set them up again."),
        h("div", { class: "actions" },
          h("button", { class: "btn", type: "button", onclick: () => copyText(r.oneTimePassword, "plain") }, "📋 Copy"),
          h("button", { class: "btn", type: "button", onclick: () => { mount($("#printRoot"), h("div", { class: "kit" }, h("h1", null, "Household Vault"),
            h("p", null, `For ${x.name}: your one-time password`), h("div", { class: "box mono" }, r.oneTimePassword),
            h("p", null, "Open Household Vault in Home Assistant and unlock with this. You'll then choose your own master password."))); printNow(); } }, "🖨 Print a slip"),
          h("button", { class: "btn primary", type: "button", onclick: () => { m.close(); showPage("admin"); } }, "Done")));
    } catch (e) { fail(e); }
  });
}
function resetDialog(x) {
  const name = h("input", { type: "text", "aria-label": "Name" });
  const err = h("div", { class: "error" });
  const m = openModal(`Reset ${x.name}`, h("form", { onsubmit: async (e) => { e.preventDefault();
    if (name.value.trim() !== x.name) { err.textContent = "Type their name exactly."; return; }
    try { await api(`api/users/${x.id}/reset`, { method: "POST" }); m.close(); toast("Reset — set them up again"); showPage("admin"); } catch (y) { err.textContent = y.message; } } },
    h("div", { class: "notice danger" }, `Only for a forgotten master password. ${x.name}'s Personal vault is wiped — every password in it is gone for good. They're removed from shared vaults (which get new passwords). Make sure they agree.`),
    field(`Type “${x.name}” to confirm`, name), err, h("div", { class: "actions" }, h("button", { class: "btn danger", type: "submit" }, "Reset"))));
}
async function whoamiPage() {
  const w = await api("api/whoami");
  const status = { none: "Not set up", temporary: "Waiting for first unlock", active: "Active" }[w.status];
  const copyBtn = (t) => h("button", { class: "icon-btn", type: "button", "aria-label": "Copy", onclick: () => copyText(t || "", "plain") }, "📋");
  let advice = null;
  if (w.noAdmin) advice = h("div", { class: "notice" }, "No admin yet: add your user name above (", h("strong", null, w.haUsername || w.haUserId), ") to admin_users on the app's Configuration tab, save, and restart the app.");
  if (w.displayNameOnly) advice = h("div", { class: "notice" }, "Your display name is in admin_users, but display names don't count — add your user name or user id instead, then restart the app.");
  return h("div", null, h("h2", null, "How the app sees you"), h("div", { class: "card" }, h("div", { class: "kv" },
    h("span", { class: "k" }, "User name"), h("span", null, w.haUsername || "—", " ", copyBtn(w.haUsername)),
    h("span", { class: "k" }, "User id"), h("span", { class: "mono" }, w.haUserId, " ", copyBtn(w.haUserId)),
    h("span", { class: "k" }, "Display name"), h("span", null, w.haDisplayName, h("span", { class: "hint" }, " (not used for matching)")),
    h("span", { class: "k" }, "Administrator in this app"), h("span", null, w.isAdmin ? "Yes" : "No"),
    h("span", { class: "k" }, "Names in admin_users"), h("span", null, String(w.adminEntries)),
    h("span", { class: "k" }, "Set up"), h("span", null, status),
    h("span", { class: "k" }, "Vaults you can open"), h("span", null, String(w.vaultCount)),
    h("span", { class: "k" }, "Account status"), h("span", null, w.disabled ? "No access (ask an admin)" : "Enabled"),
    h("span", { class: "k" }, "Phone linked for alerts"), h("span", null, w.notifyLinked ? "Yes" : w.disabled || w.status !== "active" ? "No — only once you're enabled and set up here" : "No — in Home Assistant: Settings → People → you → Track device (your phone with the Companion app)")), advice,
    h("p", { class: "hint" }, "Admins are listed in the admin_users option on the app's Configuration tab, by user name or user id. After editing it, restart the app.")));
}

// ---------- Back gesture in the Home Assistant app (backnav.js) ----------
// The start page is "All items" (what showMain opens on). Before unlocking — the unlock screen, choosing a
// master password, "no access" — the page on show counts as the start page, so Back never reveals anything.
// Nothing goes into the address: backnav.js only adds a copy of the current (unchanged) URL.
function atHome() {
  if (!state.token) return !document.querySelector("#app > .page.standalone");
  if (!$("#shell")) return true;                       // first unlock: choosing a password, the Emergency Kit
  return state.page === "vault" && state.view.kind === "all";
}
function goHome() {
  if (!state.token) {
    if (!state.me) return boot();
    if (state.me.disabled) return showNoAccess();
    if (state.me.status === "none") return showNotSetUp();
    return showUnlock();
  }
  state.selected = null;
  setView({ kind: "all" });
}
function openLayers() {
  const out = [];
  if (state.token && state.selected && $("#content.panes")) out.push("detail");
  const shell = $("#shell.nav-open");
  if (shell && window.matchMedia("(max-width: 820px)").matches) out.push("nav");
  out.push(...document.querySelectorAll("#modalRoot .modal-backdrop"), ...document.querySelectorAll("body > .menu"));
  return out;
}
function closeLayer(layer) {
  if (layer === "detail") closeDetail();
  else if (layer === "nav") closeNav();
  else if (layer.classList.contains("menu")) layer.remove();                   // as closeMenus()
  else { const b = layer.querySelector(".modal h3 .icon-btn"); if (b) b.click(); }   // the dialog's own ✕ (runs its onClose)
}

boot().finally(() => { if (window.BackNav) BackNav.init({ atHome, goHome, openLayers, closeLayer }); });

// ---------- one file per person in /data/copies (App setting "personal_copies", SPEC §12.10) ----------
function personalCopyNote(pc) {
  if (!pc) return null;
  const when = pc.builtAt ? `updated ${fmtWhen(pc.builtAt)}` : "not written yet";
  return h("div", { class: "notice" },
    h("strong", null, "Kept for you: "), pc.file ? h("code", null, pc.file) : "a file in /data/copies", ` — ${when}. `,
    "It holds everything you can open, locked with your master password, and is rewritten after every change. It's in Home Assistant's backups of this app and in the admin's backup.",
    pc.stale ? h("div", { class: "hint" }, "Changes are added the next time you unlock.") : null,
    pc.missing && pc.missing.length ? h("div", { class: "hint" }, `Not in it right now (not open): ${pc.missing.join(", ")}.`) : null);
}
function personalCopiesCard(pc) {
  if (!pc) return null;
  const rows = pc.people.map((p) => h("li", null, h("code", null, p.file), ` — ${p.name}, ` + (p.builtAt ? `updated ${fmtWhen(p.builtAt)}` : "not written yet") + (p.stale ? " (updated at their next unlock)" : "")));
  return h("div", { class: "card" }, h("h3", null, "Personal copies"),
    h("p", { class: "hint" }, pc.enabled
      ? `One file per person in ${pc.folder}, named after their Home Assistant user name: all their passwords, locked with their master password, rewritten a few seconds after any change they can see (while they're unlocked; otherwise at their next unlock). They're in Home Assistant's backups and in "Download backup" below. Only that person can open their file.`
      : "Off. Turn on “Keep one file per person in /data/copies” in App settings to keep, for each person, one file with all their passwords locked with their master password."),
    rows.length ? h("ul", { class: "plain" }, rows) : null);
}
