"use strict";
/* Family Tree — front end. Plain JS, no build step, no libraries.
   Every fetch path is RELATIVE (no leading slash): Home Assistant's Ingress
   serves this app under a per-session sub-path. User text is always inserted
   with textContent (via h()), never innerHTML. */

const state = {
  me: null,          // GET /api/whoami
  user: null,        // GET /api/me
  tree: { up: 4, down: 3, siblings: true, mode: null },
  lastTreeFocus: null,
  homeFocus: null,
};

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const EVENT_LABELS = {
  birth: "Birth", death: "Death", burial: "Burial", baptism: "Baptism / naming", education: "Education",
  occupation: "Occupation", residence: "Residence", immigration: "Immigration", emigration: "Emigration",
  military: "Military service", religion: "Religious event", retirement: "Retirement", custom: "Other event",
  marriage: "Marriage", engagement: "Engagement", divorce: "Divorce",
};
/* Indian ceremonies (§13.14, the Indian ceremonies switch), labelled in the relationship-name language. */
const CEREMONIES = {"namakaranam": {"en": "Barasala / Namakaranam (naming)", "te": "బారసాల / నామకరణం", "hi": "नामकरण"}, "annaprasana": {"en": "Annaprasana (first rice)", "te": "అన్నప్రాశన", "hi": "अन्नप्राशन"}, "aksharabhyasam": {"en": "Aksharabhyasam (first letters)", "te": "అక్షరాభ్యాసం", "hi": "अक्षरारंभ / विद्यारंभ"}, "upanayanam": {"en": "Upanayanam (sacred thread ceremony)", "te": "ఉపనయనం", "hi": "उपनयन"}, "seemantham": {"en": "Seemantham (baby shower)", "te": "సీమంతం", "hi": "सीमंतोन्नयन / गोदभराई"}, "shashtipoorthi": {"en": "Shashtipoorthi (60th birthday)", "te": "షష్టిపూర్తి", "hi": "षष्टिपूर्ति"}, "sahasra_chandra": {"en": "Sahasra Chandra Darshanam (1000 full moons)", "te": "సహస్ర చంద్ర దర్శనం", "hi": "सहस्र चंद्र दर्शन"}, "ceremony": {"en": "Other ceremony", "te": "ఇతర వేడుక", "hi": "अन्य संस्कार"}, "nischitartham": {"en": "Nischitartham (engagement ceremony)", "te": "నిశ్చితార్థం", "hi": "सगाई"}, "gruhapravesham": {"en": "Gruhapravesham (housewarming)", "te": "గృహప్రవేశం", "hi": "गृह प्रवेश"}};
const PERSON_EVENT_TYPES = ["birth", "death", "burial", "baptism", "education", "occupation", "residence", "immigration", "emigration", "military", "religion", "retirement", "custom", "namakaranam", "annaprasana", "aksharabhyasam", "upanayanam", "seemantham", "shashtipoorthi", "sahasra_chandra", "ceremony"];
const FAMILY_EVENT_TYPES = ["marriage", "engagement", "divorce", "residence", "custom", "nischitartham", "gruhapravesham", "ceremony"];
/* Feature switches (Admin → App settings → Features): a switched-off module is hidden, not deleted. */
function feat(k) { return !!(state.user && state.user.features && state.user.features[k]); }
function eventTypes(forPerson) {
  const all = forPerson ? PERSON_EVENT_TYPES : FAMILY_EVENT_TYPES;
  return feat("ceremonies") ? all : all.filter((t) => !CEREMONIES[t]);
}
function evLabel(t) {
  const c = CEREMONIES[t];
  if (!c) return EVENT_LABELS[t] || t;
  const lang = (state.user && state.user.kinLangEffective) || "en";
  return lang !== "en" && c[lang] ? `${c[lang]} — ${c.en.split(" (")[0]}` : c.en;
}
const RELATION_LABELS = { birth: "Birth", adopted: "Adopted", step: "Step", foster: "Foster", unknown: "Unknown" };
const KIND_LABELS = { married: "Married", partners: "Partners", unknown: "Unknown" };
const ENDED_LABELS = { "": "Still together / n.a.", divorced: "Divorced", separated: "Separated", widowed: "Widowed" };
const NAME_TYPES = { aka: "Also known as", married: "Married name", birth: "Birth name", religious: "Religious name", nickname: "Nickname", other: "Other" };

// ---------- DOM helpers ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  let value;
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k === "style") el.style.cssText = v;
      else if (k === "value") value = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
  }
  const add = (kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) kid.forEach(add);
    else if (kid instanceof Node) el.appendChild(kid);
    else el.appendChild(document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  if (value !== undefined) el.value = value;
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function mount(el, ...kids) { clear(el); kids.flat().forEach((k) => k && el.appendChild(k)); return el; }
function spinner() { return h("div", { class: "spinner" }, "Loading…"); }
function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function plural(n, w) { return w === "person" ? `${n} ${n === 1 ? "person" : "people"}` : `${n} ${w}${n === 1 ? "" : "s"}`; }
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
/* "05:42" → the viewer's own clock style ("5:42 AM" or "05:42"). */
function fmtTime(hhmm) {
  const [hh, mm] = String(hhmm || "").split(":").map(Number);
  if (Number.isNaN(hh) || Number.isNaN(mm)) return hhmm || "";
  return new Date(2000, 0, 1, hh, mm).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

// ---------- API ----------
function errorMessage(detail, status) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) return detail.map((d) => (d && d.msg) || String(d)).join("; ");
  return `Something went wrong (HTTP ${status}).`;
}
function deviceId() {
  let d = lsGet("deviceId");
  if (!d || !/^[A-Za-z0-9_-]{16,64}$/.test(d)) {
    const a = new Uint8Array(18);
    (window.crypto || window.msCrypto).getRandomValues(a);
    d = "d-" + Array.from(a, (x) => x.toString(16).padStart(2, "0")).join("");
    lsSet("deviceId", d);
  }
  return d;
}
async function api(path, opts = {}) {
  const { method = "GET", body, formData } = opts;
  const init = { method, headers: { "X-Device-Id": deviceId() } };
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  else if (formData) init.body = formData;
  let res;
  try { res = await fetch(path.replace(/^\//, ""), init); }
  catch (e) { throw new Error("Can't reach the app. Check your connection and try again."); }
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch (e) { /* not JSON */ }
    const err = new Error(errorMessage(detail, res.status));
    err.status = res.status;
    if (res.status === 423 && !state.kids) setTimeout(() => location.reload(), 50);   // kids mode started elsewhere in this browser
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------- toasts & modals ----------
function toast(msg, opts = {}) {
  const el = h("div", { class: "toast" + (opts.error ? " error" : "") }, h("span", null, msg));
  if (opts.undo) {
    el.appendChild(h("button", { class: "btn-secondary btn-small", type: "button", onclick: async () => {
      el.remove();
      try { await api(`api/history/${opts.undo}/undo`, { method: "POST" }); toast("Undone"); if (opts.onUndone) opts.onUndone(); rerender(); }
      catch (e) { fail(e); }
    } }, "Undo"));
  }
  $("#toastRoot").appendChild(el);
  setTimeout(() => el.remove(), opts.error ? 7000 : opts.undo ? 7000 : 3000);
}
function fail(e) { toast(e && e.message ? e.message : String(e), { error: true }); }
function saved(res, msg = "Saved") { toast(msg, { undo: res && res.batchId }); }
/* How to get something back after a delete. The trash is admin-only (Admin → Trash). */
function restoreHint(pronoun) {
  return state.me && state.me.isAdmin
    ? `You can restore ${pronoun} from Admin → Trash.`
    : `You can undo this from History, or ask an admin to restore ${pronoun} from the trash.`;
}

function openModal(title, content, opts = {}) {
  const closeBtn = h("button", { class: "icon-btn", "aria-label": "Close", type: "button" }, "✕");
  const modal = h("div", { class: "modal" + (opts.wide ? " wide" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
    h("h3", null, h("span", null, title), closeBtn), content);
  const backdrop = h("div", { class: "modal-backdrop" }, modal);
  let downOnBackdrop = false;
  backdrop.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === backdrop; });
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop && downOnBackdrop) close(); });
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  function close() { backdrop.remove(); document.removeEventListener("keydown", onKey); if (opts.onClose) opts.onClose(); }
  closeBtn.addEventListener("click", close);
  $("#modalRoot").appendChild(backdrop);
  const first = modal.querySelector("input:not([type=hidden]):not([disabled]), select, textarea");
  if (first && !opts.noFocus) setTimeout(() => first.focus(), 30);
  return { close, el: modal };
}

function confirmDialog(title, message, okLabel = "OK", danger = false) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; m.close(); resolve(v); } };
    const body = h("div", null, h("p", null, message),
      h("div", { class: "actions" },
        h("button", { class: "btn-ghost", type: "button", onclick: () => finish(false) }, "Cancel"),
        h("button", { class: danger ? "btn-danger" : "btn-primary", type: "button", onclick: () => finish(true) }, okLabel)));
    const m = openModal(title, body, { onClose: () => { if (!done) { done = true; resolve(false); } } });
  });
}

// ---------- people bits ----------
function initials(p) {
  const a = ((p.given || p.givenNames || p.name || "?").trim()[0] || "?");
  const b = ((p.surname || "").trim()[0] || "");
  return (a + b).toUpperCase();
}
function avatar(p, size = "") {
  const el = h("span", { class: `avatar ${size} sex-${p.gender || "unknown"}`, "aria-hidden": "true" });
  if (p.photo) {
    const img = h("img", { src: photoSrc(p), alt: "", loading: "lazy" });
    img.addEventListener("error", () => { img.remove(); el.textContent = initials(p); });
    el.appendChild(img);
  } else el.textContent = initials(p);
  return el;
}
/* A profile photo, cropped to its tag box when it has one (§13.2). */
function photoSrc(p, size = 256) {
  return `api/media/${encodeURIComponent(p.photo)}/file?size=${size}${p.photoRegion ? "&region=" + encodeURIComponent(p.photoRegion) : ""}`;
}
/* Names in Telugu / Hindi script (§13.7): the user's choice of English, script or both. */
function shownName(p) {
  const nd = state.user && state.user.nameDisplay;
  return nd === "script" && p.nameLocal ? p.nameLocal : p.name;
}
function localLine(p) {
  const nd = state.user && state.user.nameDisplay;
  return nd === "both" && p.nameLocal ? h("div", { class: "name-local" }, p.nameLocal) : null;
}
function relText(rel) {
  if (!rel || rel === "you" || rel === "not related") return null;
  return "your " + rel;
}
function personCard(p, opts = {}) {
  const sub = [p.years, opts.extra].filter(Boolean).join(" · ");
  return h("button", { type: "button", class: "pcard", onclick: opts.onclick || (() => go(`person/${p.id}`)), title: p.name },
    avatar(p, "sm"),
    h("div", { class: "grow" },
      h("div", { class: "nm" }, shownName(p), p.living === false ? " 🕯" : ""), localLine(p),
      sub ? h("div", { class: "sub" }, sub) : null,
      relText(p.relationship) ? h("div", { class: "rel" }, relText(p.relationship)) : null),
    opts.chip ? h("span", { class: "chip" }, opts.chip) : null);
}
function ghostCard(label, onclick) { return h("button", { type: "button", class: "pcard ghost", onclick }, label); }

// ---------- dates ----------
function dateOrder() {
  const saved = lsGet("dateOrder");
  if (saved === "dmy" || saved === "mdy") return saved;
  return (navigator.language || "en-US").toLowerCase() === "en-us" ? "mdy" : "dmy";
}
const MONTH_ABBR = MONTHS.map((m) => m.slice(0, 3).toLowerCase());
/* "12/03/1950", "12.3.1950", "1950-03-12", "12 Mar 1950", "Mar 1950", "12 March", "1950" → {d,m,y}. */
function parseTypedDate(str) {
  const s = (str || "").trim();
  if (!s) return null;
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return { y: +m[1], m: +m[2], d: +m[3] };
  const parts = s.split(/[\s/.,-]+/).filter(Boolean);
  const out = { d: null, m: null, y: null };
  const words = parts.filter((x) => /[a-z]/i.test(x));
  const nums = parts.filter((x) => /^\d+$/.test(x));
  if (words.length > 1 || words.length + nums.length !== parts.length) throw new Error(`Couldn't read "${s}" as a date.`);
  if (words.length === 1) {
    const idx = MONTH_ABBR.indexOf(words[0].slice(0, 3).toLowerCase());
    if (idx < 0) throw new Error(`"${words[0]}" isn't a month.`);
    out.m = idx + 1;
    for (const n of nums) {
      if (n.length >= 3 || +n > 31) out.y = +n; else if (out.d === null) out.d = +n; else out.y = +n;
    }
    return out;
  }
  if (nums.length === 1) {
    if (nums[0].length <= 2 && +nums[0] <= 31) throw new Error("Add a month to that day.");
    return { d: null, m: null, y: +nums[0] };
  }
  // day/month order: the browser's convention, unless the numbers only fit the other way (22/07 can't be month 22)
  const dm = (a, b) => {
    let mdy = dateOrder() === "mdy";
    if (mdy && a > 12 && b <= 12) mdy = false; else if (!mdy && b > 12 && a <= 12) mdy = true;
    return mdy ? { m: a, d: b } : { d: a, m: b };
  };
  if (nums.length === 2) {                          // month + year, or day + month
    const [a, b] = nums.map(Number);
    if (nums[1].length >= 3) return { d: null, m: a, y: b };
    return Object.assign(dm(a, b), { y: null });
  }
  if (nums.length === 3) {
    const [a, b, c] = nums.map(Number);
    if (nums[0].length === 4) return { y: a, m: b, d: c };
    return Object.assign(dm(a, b), { y: c });
  }
  throw new Error(`Couldn't read "${s}" as a date.`);
}

/* Day / Month / Year fields plus an About / Before / After / Between qualifier.
   The year is optional: day + month alone means "birthday known, year not". */
function dateInput(initial) {
  const v = initial || {};
  const qual = h("select", { class: "qual", "aria-label": "Date qualifier" },
    ...[["exact", "Exact"], ["about", "About"], ["before", "Before"], ["after", "After"], ["between", "Between"]]
      .map(([k, l]) => h("option", { value: k }, l)));
  qual.value = v.qual || "exact";
  const mk = (d, m, y, suffix) => {
    const day = h("input", { class: "day", inputmode: "numeric", placeholder: "Day", "aria-label": "Day" + suffix, value: d == null ? "" : d });
    const month = h("select", { class: "month", "aria-label": "Month" + suffix }, h("option", { value: "" }, "Month"),
      ...MONTHS.map((n, i) => h("option", { value: String(i + 1) }, n)));
    month.value = m == null ? "" : String(m);
    const year = h("input", { class: "year", inputmode: "numeric", placeholder: "Year", "aria-label": "Year" + suffix, value: y == null ? "" : y });
    // typing a whole date into the day field fills all three
    const split = (force) => {
      const t = day.value.trim();
      if (force && /^\d{3,4}$/.test(t) && !year.value.trim()) { year.value = t; day.value = ""; hint(); return; }   // a year typed in the day box
      const complete = /^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}$/.test(t) || /^\d{4}-\d{1,2}-\d{1,2}$/.test(t) ||
        /^(\d{1,2}\s+)?[a-z]{3,}\.?\s+\d{4}$/i.test(t) || /^[a-z]{3,}\.?\s+\d{1,2},?\s+\d{4}$/i.test(t);
      if (!complete && !(force && (/[/.\-\s]/.test(t) || /[a-z]/i.test(t)))) return;
      try {
        const p = parseTypedDate(t);
        if (p) { day.value = p.d == null ? "" : p.d; month.value = p.m == null ? "" : String(p.m); year.value = p.y == null ? "" : p.y; hint(); }
      } catch (e) { if (force) { hintEl.className = "date-hint err"; hintEl.textContent = e.message; } }
    };
    day.addEventListener("input", () => split(false));
    day.addEventListener("blur", () => split(true));
    [day, month, year].forEach((el) => el.addEventListener("input", () => hint()));
    month.addEventListener("change", () => hint());
    return { day, month, year, split };
  };
  const a = mk(v.d, v.m, v.y, "");
  const b = mk(v.d2, v.m2, v.y2, " (second date)");
  const andLabel = h("span", { class: "and" }, "and");
  const hintEl = h("div", { class: "date-hint" });
  const row = h("div", { class: "date-input" }, qual, a.day, a.month, a.year, andLabel, b.day, b.month, b.year);
  const wrap = h("div", null, row, hintEl);
  const toggle = () => { const on = qual.value === "between"; [andLabel, b.day, b.month, b.year].forEach((e) => { e.hidden = !on; e.style.display = on ? "" : "none"; }); };
  qual.addEventListener("change", () => { toggle(); hint(); });
  toggle();
  const num = (el) => { const t = el.value.trim(); return t === "" ? null : Number(t); };
  function read() {
    const out = { qual: qual.value, d: num(a.day), m: a.month.value ? +a.month.value : null, y: num(a.year) };
    if (qual.value === "between") Object.assign(out, { d2: num(b.day), m2: b.month.value ? +b.month.value : null, y2: num(b.year) });
    return out;
  }
  function hint() {
    const r = read();
    hintEl.className = "date-hint";
    if ([r.d, r.m, r.y].some((x) => x !== null && !Number.isFinite(x))) { hintEl.className = "date-hint err"; hintEl.textContent = "Use numbers for the day and year."; return; }
    if (r.d !== null && r.m === null) { hintEl.className = "date-hint err"; hintEl.textContent = "A day needs a month too."; return; }
    if (r.m !== null && r.d === null && r.y === null) { hintEl.className = "date-hint err"; hintEl.textContent = "Add a day or a year to the month."; return; }
    if (r.d !== null && r.m !== null && r.y === null && r.qual === "exact") { hintEl.textContent = `Year unknown — shows as “${r.d} ${MONTHS[r.m - 1]}”, and the age shows as unknown. Reminders still arrive, without the age.`; return; }
    hintEl.textContent = "";
  }
  hint();
  return {
    el: wrap,
    value() {
      a.split(true); b.split(true);
      const r = read();
      if ([r.d, r.y, r.d2, r.y2].some((x) => x !== null && x !== undefined && !Number.isInteger(x))) throw new Error("Check the date: the day and year must be whole numbers.");
      return (r.d === null && r.m === null && r.y === null) ? null : r;
    },
  };
}

// ---------- person picker (search existing people) ----------
function personPicker(opts = {}) {
  let chosen = opts.initial || null;
  const input = h("input", { type: "search", placeholder: opts.placeholder || "Search people…", "aria-label": "Search people", autocomplete: "off" });
  const results = h("div", { class: "picker-results", hidden: true });
  const pickedBox = h("div");
  const wrap = h("div", { class: "picker" }, pickedBox, input, results);
  const show = () => {
    clear(pickedBox);
    if (chosen) {
      input.hidden = true;
      pickedBox.appendChild(h("div", { class: "picked" }, avatar(chosen, "sm"), h("div", { class: "grow", style: "flex:1" },
        h("div", null, h("strong", null, chosen.name)), chosen.years ? h("div", { class: "hint" }, chosen.years) : null),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { chosen = null; show(); input.focus(); if (opts.onChange) opts.onChange(null); } }, "Change")));
    } else input.hidden = false;
  };
  const search = debounce(async () => {
    const q = input.value.trim();
    if (!q) { results.hidden = true; return; }
    try {
      const r = await api(`api/people?q=${encodeURIComponent(q)}&page_size=8`);
      clear(results);
      const items = r.items.filter((p) => !(opts.exclude || []).includes(p.id));
      if (!items.length) results.appendChild(h("div", { class: "hint", style: "padding:8px 10px" }, "Nobody found."));
      for (const p of items) {
        results.appendChild(h("button", { type: "button", onclick: () => { chosen = p; results.hidden = true; input.value = ""; show(); if (opts.onChange) opts.onChange(p); } },
          avatar(p, "sm"), h("span", null, h("strong", null, p.name), p.years ? h("span", { class: "hint" }, "  " + p.years) : null)));
      }
      results.hidden = false;
    } catch (e) { fail(e); }
  }, 250);
  input.addEventListener("input", search);
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") results.hidden = true; });
  input.addEventListener("blur", () => setTimeout(() => { results.hidden = true; }, 200));
  show();
  return { el: wrap, get: () => chosen, focus: () => input.focus(), reset: () => { chosen = null; input.value = ""; show(); },
    set: (p) => { chosen = p; input.value = ""; show(); } };
}

// ---------- person form ----------
function segmented(options, value, onChange) {
  const wrap = h("div", { class: "segmented", role: "radiogroup" });
  let current = value;
  const btns = options.map(([k, label]) => {
    const b = h("button", { type: "button", role: "radio", "aria-checked": String(k === current), class: k === current ? "active" : "" }, label);
    b.addEventListener("click", () => {
      current = k;
      btns.forEach((x, i) => { const on = options[i][0] === k; x.className = on ? "active" : ""; x.setAttribute("aria-checked", String(on)); });
      if (onChange) onChange(k);
    });
    return b;
  });
  btns.forEach((b) => wrap.appendChild(b));
  return { el: wrap, get: () => current, set: (k) => { const i = options.findIndex((o) => o[0] === k); if (i >= 0) btns[i].click(); } };
}

function field(label, control, cls = "") { return h("label", { class: "field " + cls }, label, control); }

/* The full person editor, or a compact one (names, gender, birth) for quick adds. */
function personForm(initial = {}, opts = {}) {
  const p = initial || {};
  const given = h("input", { value: p.givenNames || "", maxlength: 100, autocomplete: "off" });
  const surname = h("input", { value: p.surname || opts.surnameHint || "", maxlength: 100, autocomplete: "off" });
  const birthSurname = h("input", { value: p.birthSurname || "", maxlength: 100 });
  const nickname = h("input", { value: p.nickname || "", maxlength: 100 });
  const givenLocal = h("input", { value: p.givenLocal || "", maxlength: 100, lang: "te", "aria-label": "First names in Telugu or Hindi script" });
  const surnameLocal = h("input", { value: p.surnameLocal || "", maxlength: 100, lang: "te", "aria-label": "Surname in Telugu or Hindi script" });
  const scriptSel = h("select", { "aria-label": "Script" }, h("option", { value: "telugu" }, "తెలుగు Telugu"), h("option", { value: "devanagari" }, "देवनागरी Hindi"));
  scriptSel.value = (state.user && state.user.kinLangEffective === "hi") || p.localScript === "hi" ? "devanagari" : "telugu";
  const suggest = h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
    try {
      const S = await loadSanscript();
      const tr = (t) => {
        if (!t.trim()) return "";
        let out = S.t(t.trim().toLowerCase(), "optitrans", scriptSel.value);
        if (scriptSel.value === "devanagari") out = out.split(" ").map((w) => w.replace(/्$/, "")).join(" ");   // Hindi drops the final vowel
        return out;
      };
      if (!givenLocal.value.trim()) givenLocal.value = tr(given.value);
      if (!surnameLocal.value.trim()) surnameLocal.value = tr(surname.value);
      if (!givenLocal.value && !surnameLocal.value) toast("Type the English name first.", { error: true });
      else toast("A guess — check the spelling and fix it if needed.");
    } catch (e) { fail(e); }
  } }, "Suggest from English");
  const order = h("select", { "aria-label": "Name order" }, h("option", { value: "" }, "Household default"),
    h("option", { value: "given_first" }, "First name first (Asha Sharma)"), h("option", { value: "surname_first" }, "Surname first (Sharma Asha)"));
  order.value = p.nameOrder || "";
  const gender = segmented([["male", "Male"], ["female", "Female"], ["other", "Other"], ["unknown", "Unknown"]], p.gender || opts.genderHint || "unknown");
  const deceased = h("input", { type: "checkbox", checked: !!(p.deceased || p.death) });
  const neverExport = h("input", { type: "checkbox", checked: !!p.neverExport });
  const editing = !!p.id;                     // new people always start with reminders off (§9)
  const remind = h("input", { type: "checkbox", checked: !!p.remind });
  const birthDate = dateInput(p.birth && p.birth.date);
  const birthPlace = h("input", { value: (p.birth && p.birth.place) || "", maxlength: 200, placeholder: "City, State, Country" });
  const birthTime = h("input", { type: "time", value: (p.birth && p.birth.time) || "", "aria-label": "Time of birth" });
  const deathTime = h("input", { type: "time", value: (p.death && p.death.time) || "", "aria-label": "Time of death" });
  const deathDate = dateInput(p.death && p.death.date);
  const deathPlace = h("input", { value: (p.death && p.death.place) || "", maxlength: 200, placeholder: "City, State, Country" });
  const bio = h("textarea", { maxlength: 20000, rows: 5, placeholder: "Life story, memories, what they were like…" }, p.biography || "");
  const otherNames = (p.otherNames || []).map((n) => ({ ...n }));
  const otherBox = h("div");
  const drawOther = () => {
    clear(otherBox);
    otherNames.forEach((n, i) => {
      const t = h("select", { "aria-label": "Name type" }, ...Object.entries(NAME_TYPES).map(([k, l]) => h("option", { value: k }, l)));
      t.value = n.type || "aka";
      t.addEventListener("change", () => { n.type = t.value; });
      const v = h("input", { value: n.name || "", maxlength: 100, "aria-label": "Other name" });
      v.addEventListener("input", () => { n.name = v.value; });
      otherBox.appendChild(h("div", { class: "form-row" }, t, v,
        h("button", { type: "button", class: "icon-btn", "aria-label": "Remove name", onclick: () => { otherNames.splice(i, 1); drawOther(); } }, "✕")));
    });
    if (otherNames.length < 10) otherBox.appendChild(h("button", { type: "button", class: "link-btn", onclick: () => { otherNames.push({ type: "aka", name: "" }); drawOther(); } }, "+ Add another name"));
  };
  drawOther();

  const deathSet = h("fieldset", null, h("legend", null, "Death"),
    h("div", { class: "form-row" }, field("Date", deathDate.el, "wide"), field("Time (optional)", deathTime, "narrow")),
    h("div", { class: "form-row" }, field("Place", deathPlace, "wide")));
  const syncDeath = () => { deathSet.hidden = !deceased.checked; deathSet.style.display = deceased.checked ? "" : "none"; };
  deceased.addEventListener("change", syncDeath);
  syncDeath();

  const dupBox = h("div");
  if (opts.onUseExisting) {
    const check = debounce(async () => {
      const q = [given.value.trim(), surname.value.trim()].filter(Boolean).join(" ");
      clear(dupBox);
      if (q.length < 3) return;
      try {
        const r = await api(`api/people?q=${encodeURIComponent(q)}&page_size=3`);
        const items = r.items.filter((x) => !(opts.exclude || []).includes(x.id));
        if (items.length) {
          dupBox.appendChild(h("div", { class: "dup-hint" }, "Already in the tree?",
            ...items.map((x) => h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => opts.onUseExisting(x) },
              `Use ${x.name}${x.years ? " (" + x.years + ")" : ""}`))));
        }
      } catch (e) { /* ignore */ }
    }, 450);
    given.addEventListener("input", check);
    surname.addEventListener("input", check);
  }

  const more = h("div", null,
    h("div", { class: "form-row" }, field("Birth surname (maiden name)", birthSurname), field("Nickname", nickname)),
    h("div", { class: "field wide", style: "margin-bottom:10px" }, "Other names", otherBox),
    feat("script_names") ? h("fieldset", null, h("legend", null, "Name in Telugu or Hindi script"),
      h("div", { class: "form-row" }, field("First and middle names", givenLocal), field("Surname", surnameLocal)),
      h("div", { class: "form-row notify-add" }, scriptSel, suggest,
        h("span", { class: "hint" }, "Or type it with your phone's Telugu or Hindi keyboard."))) : null,
    h("div", { class: "form-row" }, field("Name order", order)),
    opts.compact ? null : field("Biography", bio, "wide"),
    opts.compact ? null : h("label", { class: "check-row" }, neverExport, h("span", null, "Keep out of all exports",
      h("span", { class: "hint" }, " — never in a website or file, even when everyone is exported"))),
    opts.compact || !editing || !feat("reminders") ? null : h("label", { class: "check-row" }, remind, h("span", null, "Send reminders for this person",
      h("span", { class: "hint" }, " — 🔔 their birthday and other days go to everyone who has reminders on"))));
  let moreShown = !opts.compact || !!(p.birthSurname || p.nickname || otherNames.length || p.givenLocal || p.surnameLocal);
  const moreBtn = h("button", { type: "button", class: "link-btn", style: "margin-bottom:10px" }, "More details…");
  const syncMore = () => { more.style.display = moreShown ? "" : "none"; moreBtn.style.display = moreShown ? "none" : ""; };
  moreBtn.addEventListener("click", () => { moreShown = true; syncMore(); });
  syncMore();

  const el = h("div", null,
    h("div", { class: "form-row" }, field("First and middle names", given), field("Surname", surname)),
    dupBox,
    h("div", { class: "form-row" }, h("div", { class: "field" }, "Gender", gender.el)),
    h("fieldset", null, h("legend", null, "Birth"),
      h("div", { class: "form-row" }, field("Date", birthDate.el, "wide"), field("Time (optional)", birthTime, "narrow")),
      h("div", { class: "form-row" }, field("Place", birthPlace, "wide")),
      h("div", { class: "hint" }, "Time: the local time where they were born, if known — it needs the full date." +
        (feat("tithi") ? " It's used for the janma tithi and the 1000th full moon." : ""))),
    h("label", { class: "check-row" }, deceased, "Deceased"),
    deathSet, moreBtn, more);
  return {
    el,
    focus: () => given.focus(),
    isBlank: () => !given.value.trim() && !surname.value.trim() && !nickname.value.trim(),
    value() {
      const body = {
        given_names: given.value.trim() || null, surname: surname.value.trim() || null,
        birth_surname: birthSurname.value.trim() || null, nickname: nickname.value.trim() || null,
        name_order: order.value || null,
        other_names: otherNames.filter((n) => (n.name || "").trim()).map((n) => ({ type: n.type || "aka", name: n.name.trim() })),
        gender: gender.get(), deceased: deceased.checked,
        birth: { date: birthDate.value(), time: birthTime.value || null, place: birthPlace.value.trim() || null },
        death: deceased.checked ? { date: deathDate.value(), time: deathTime.value || null, place: deathPlace.value.trim() || null } : null,
      };
      if (feat("script_names")) Object.assign(body, { given_local: givenLocal.value.trim() || null, surname_local: surnameLocal.value.trim() || null });
      if (!opts.compact) { body.biography = bio.value; body.never_export = neverExport.checked; }
      if (!opts.compact && editing && feat("reminders")) body.remind = remind.checked;
      if (!body.given_names && !body.surname && !body.nickname) throw new Error("Give the person at least a first name, surname or nickname.");
      return body;
    },
  };
}

/* New person or someone already in the tree. */
function refChooser(opts = {}) {
  let mode = "new";
  const box = h("div");
  let picker = personPicker({ exclude: opts.exclude, placeholder: "Search for the person…" });
  const form = personForm({}, { compact: true, genderHint: opts.genderHint, surnameHint: opts.surnameHint, exclude: opts.exclude,
    onUseExisting: (p) => { picker = personPicker({ exclude: opts.exclude, initial: p }); seg.set("existing"); } });
  const draw = () => mount(box, mode === "new" ? form.el : picker.el);
  const seg = segmented([["new", "New person"], ["existing", "Already in the tree"]], "new", (k) => { mode = k; draw(); });
  draw();
  return {
    el: h("div", null, h("div", { style: "margin-bottom:10px" }, seg.el), box),
    isBlank: () => (mode === "existing" ? !picker.get() : form.isBlank()),
    value() {
      if (mode === "existing") {
        const p = picker.get();
        if (!p) throw new Error("Choose a person from the search results.");
        return { existingId: p.id };
      }
      return { person: form.value() };
    },
  };
}

function relationSelect(value = "birth") {
  const s = h("select", null, ...Object.entries(RELATION_LABELS).map(([k, l]) => h("option", { value: k }, l)));
  s.value = value;
  return s;
}

// ---------- quick adds: parent / partner / child ----------
async function addRelativeModal(kind, anchor, opts = {}) {
  const who = anchor.name || "this person";
  const titles = { father: `Add father of ${who}`, mother: `Add mother of ${who}`, parent: `Add parent of ${who}`,
    partner: `Add partner of ${who}`, child: `Add child of ${who}` };
  const genderHint = kind === "father" ? "male" : kind === "mother" ? "female" : null;
  const surnameHint = kind === "father" || kind === "child" ? (anchor.surname || "") : "";
  const chooser = refChooser({ genderHint, surnameHint, exclude: [anchor.id] });
  const extras = h("div");
  let relation = null, famKind = null, marriageDate = null, marriagePlace = null, famSelect = null;
  if (kind === "father" || kind === "mother" || kind === "parent" || kind === "child") {
    relation = relationSelect("birth");
    extras.appendChild(h("div", { class: "form-row" }, field(kind === "child" ? "This child is a…" : "Relationship", relation)));
  }
  if (kind === "child" && !opts.familyId) {
    const detail = opts.detail || await api(`api/people/${anchor.id}`);
    if (detail.families.length > 1) {
      famSelect = h("select", null, ...detail.families.map((f) => h("option", { value: f.id }, f.partner ? `with ${f.partner.name}` : "with an unknown partner")));
      extras.appendChild(h("div", { class: "form-row" }, field("Which family", famSelect)));
    } else if (detail.families.length === 1) opts.familyId = detail.families[0].id;
  }
  if (kind === "partner") {
    famKind = h("select", null, ...Object.entries(KIND_LABELS).map(([k, l]) => h("option", { value: k }, l)));
    marriageDate = dateInput(null);
    marriagePlace = h("input", { maxlength: 200, placeholder: "Place of marriage" });
    extras.appendChild(h("div", null, h("div", { class: "form-row" }, field("Relationship", famKind)),
      h("fieldset", null, h("legend", null, "Marriage (optional)"), h("div", { class: "form-row" }, field("Date", marriageDate.el, "wide")),
        h("div", { class: "form-row" }, field("Place", marriagePlace, "wide")))));
  }
  const err = h("div", { class: "error-text" });
  const saveBtn = h("button", { type: "submit", class: "btn-primary" }, "Add");
  const form = h("form", null, chooser.el, extras, err, h("div", { class: "actions" },
    h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), saveBtn));
  onFormSubmit(form, chooser.value, err, saveBtn, async (body) => {
    let path;
    if (kind === "partner") {
      path = "add-partner";
      body.kind = famKind.value;
      const md = marriageDate.value(), mp = marriagePlace.value.trim();
      if (md || mp) body.marriage = { date: md, place: mp || null };
      if (opts.familyId) body.familyId = opts.familyId;
    } else if (kind === "child") {
      path = "add-child";
      body.relation = relation.value;
      const fid = famSelect ? famSelect.value : opts.familyId;
      if (fid) body.familyId = fid;
    } else {
      path = "add-parent";
      body.relation = relation.value;
      if (opts.familyId) body.familyId = opts.familyId;
    }
    const res = await api(`api/people/${anchor.id}/${path}`, { method: "POST", body });
    m.close();
    saved(res, "Added");
    rerender();
  });
  const m = openModal(titles[kind], form);
}

// ---------- quick family entry (§13.18) ----------
function quickFamilyModal(opts = {}) {
  const p1 = refChooser({ genderHint: "male" });
  const p2 = refChooser({ genderHint: "female" });
  const kind = h("select", null, ...Object.entries(KIND_LABELS).map(([k, l]) => h("option", { value: k }, l)));
  const mDate = dateInput(null);
  const mPlace = h("input", { maxlength: 200, placeholder: "Place of marriage" });
  const rows = [];
  const list = h("div");
  if (opts.child) rows.push({ existing: opts.child });
  const surnameGuess = () => {
    try { const v = p1.value(); return (v.person && v.person.surname) || ""; } catch (e) { return ""; }
  };
  const draw = (focusLast) => {
    clear(list);
    rows.forEach((r, i) => {
      const move = (d) => { const j = i + d; if (j < 0 || j >= rows.length) return; [rows[i], rows[j]] = [rows[j], rows[i]]; draw(); };
      const ctl = [
        h("button", { type: "button", class: "icon-btn", "aria-label": "Move up", onclick: () => move(-1) }, "↑"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Move down", onclick: () => move(1) }, "↓"),
      ];
      if (r.existing) {
        list.appendChild(h("div", { class: "qf-child" }, h("span", { class: "num" }, `${i + 1}.`), avatar(r.existing, "sm"),
          h("strong", { style: "flex:1" }, r.existing.name + " (already in the tree)"), ...ctl));
        return;
      }
      r.given = r.given || h("input", { class: "given", placeholder: "First name", "aria-label": "Child's first name", maxlength: 100 });
      r.sur = r.sur || h("input", { class: "sur", placeholder: "Surname", "aria-label": "Child's surname", maxlength: 100, value: surnameGuess() });
      r.gender = r.gender || h("select", { "aria-label": "Gender" }, h("option", { value: "unknown" }, "–"), h("option", { value: "male" }, "Boy"), h("option", { value: "female" }, "Girl"), h("option", { value: "other" }, "Other"));
      r.born = r.born || h("input", { class: "yr", placeholder: "Born", "aria-label": "Birth date or year", title: "A year, or a full date like 12/03/1990" });
      r.rel = r.rel || relationSelect("birth");
      r.given.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); if (i === rows.length - 1) { rows.push({}); draw(true); } } };
      list.appendChild(h("div", { class: "qf-child" }, h("span", { class: "num" }, `${i + 1}.`), r.given, r.sur, r.gender, r.born, r.rel, ...ctl,
        h("button", { type: "button", class: "icon-btn", "aria-label": "Remove child", onclick: () => { rows.splice(i, 1); draw(); } }, "✕")));
    });
    if (focusLast) { const last = rows[rows.length - 1]; if (last && last.given) last.given.focus(); }
  };
  draw();
  const err = h("div", { class: "error-text" });
  const saveBtn = h("button", { type: "submit", class: "btn-primary" }, "Save family");
  const form = h("form", null,
    h("div", { class: "form-row top", style: "align-items:flex-start" },
      h("fieldset", { style: "flex:1;min-width:260px" }, h("legend", null, "Partner 1"), p1.el),
      h("fieldset", { style: "flex:1;min-width:260px" }, h("legend", null, "Partner 2 (optional)"), p2.el)),
    h("fieldset", null, h("legend", null, "Marriage"),
      h("div", { class: "form-row" }, field("Relationship", kind, "narrow"), field("Date", mDate.el), field("Place", mPlace))),
    h("fieldset", null, h("legend", null, "Children, oldest first"), list,
      h("button", { type: "button", class: "link-btn", onclick: () => { rows.push({}); draw(true); } }, "+ Add child"),
      h("div", { class: "hint", style: "margin-top:6px" }, "Tip: press Enter in a first-name box to add the next child.")),
    err,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), saveBtn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const body = { partners: [], kind: kind.value, children: [] };
    try {
      body.partners.push(p1.value());
      // an untouched partner 2 is left out; anything typed there must be valid
      if (!p2.isBlank()) body.partners.push(p2.value());
      const md = mDate.value(), mp = mPlace.value.trim();
      if (md || mp) body.marriage = { date: md, place: mp || null };
      for (const r of rows) {
        if (r.existing) { body.children.push({ existingId: r.existing.id, relation: "birth" }); continue; }
        const g = r.given.value.trim(), s = r.sur.value.trim();
        if (!g && !s) continue;
        const bd = parseTypedDate(r.born.value);
        body.children.push({ relation: r.rel.value, person: { given_names: g || null, surname: s || null, gender: r.gender.value,
          birth: bd ? { date: Object.assign({ qual: "exact" }, bd) } : null } });
      }
    } catch (x) { err.textContent = x.message; return; }
    saveBtn.disabled = true;
    try {
      const res = await api("api/families/quick", { method: "POST", body });
      m.close();
      saved(res, "Family added");
      if (opts.onDone) opts.onDone(res); else rerender();
    } catch (x) { err.textContent = x.message; saveBtn.disabled = false; }
  });
  const m = openModal(opts.title || "Add a family", form, { wide: true });
}

// ---------- events ----------
function eventModal(owner, ev) {
  const isPerson = !!owner.personId;
  const types = eventTypes(isPerson);
  const locked = ev && ["birth", "death", "marriage"].includes(ev.type);
  const type = h("select", { disabled: !!locked }, ...types.map((t) => h("option", { value: t }, evLabel(t))));
  type.value = ev ? ev.type : (isPerson ? "residence" : "marriage");
  const title = h("input", { maxlength: 200, value: (ev && ev.title) || "", placeholder: "e.g. school, employer, what happened" });
  const date = dateInput(ev && ev.date);
  const hasTime = ev && ["birth", "death"].includes(ev.type);
  const time = h("input", { type: "time", value: (ev && ev.time) || "", "aria-label": "Time" });
  const place = h("input", { maxlength: 200, value: (ev && ev.place) || "", placeholder: "City, State, Country" });
  const desc = h("textarea", { maxlength: 2000, rows: 3 }, (ev && ev.description) || "");
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "submit", class: "btn-primary" }, ev ? "Save" : "Add");
  const del = ev ? h("button", { type: "button", class: "btn-danger", onclick: async () => {
    if (!await confirmDialog("Delete event", `Delete this ${evLabel(ev.type).toLowerCase()}?`, "Delete", true)) return;
    try { const res = await api(`api/events/${ev.id}`, { method: "DELETE" }); m.close(); saved(res, "Deleted"); rerender(); } catch (x) { fail(x); }
  } }, "Delete") : null;
  const form = h("form", null,
    h("div", { class: "form-row" }, field("Type", type), field("Title / details", title)),
    h("div", { class: "form-row" }, field("Date", date.el, "wide"), hasTime ? field("Time (optional)", time, "narrow") : null),
    h("div", { class: "form-row" }, field("Place", place, "wide")),
    field("Description", desc, "wide"), err,
    h("div", { class: "actions" }, del, h("span", { class: "spacer" }), h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), save));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const body = { title: title.value.trim() || null, date: date.value(), place: place.value.trim() || null, description: desc.value.trim() || null };
    if (hasTime) body.time = time.value || null;
    save.disabled = true;
    try {
      let res;
      if (ev) {
        if (!locked) body.type = type.value;
        res = await api(`api/events/${ev.id}`, { method: "PATCH", body });
      } else {
        body.type = type.value;
        if (isPerson) body.personId = owner.personId; else body.familyId = owner.familyId;
        res = await api("api/events", { method: "POST", body });
      }
      m.close();
      saved(res);
      rerender();
    } catch (x) { err.textContent = x.message; save.disabled = false; }
  });
  const m = openModal(ev ? `Edit ${evLabel(ev.type).toLowerCase()}` : "Add event", form);
}

// ---------- family editor ----------
async function familyModal(fid) {
  let fam;
  try { fam = await api(`api/families/${fid}`); } catch (e) { fail(e); return; }
  const body = h("div");
  const m = openModal("Edit family", body, { wide: true, onClose: () => rerender() });
  const draw = () => {
    const kind = h("select", null, ...Object.entries(KIND_LABELS).map(([k, l]) => h("option", { value: k }, l)));
    kind.value = fam.kind;
    const ended = h("select", null, ...Object.entries(ENDED_LABELS).map(([k, l]) => h("option", { value: k }, l)));
    ended.value = fam.ended || "";
    const mDate = dateInput(fam.marriage && fam.marriage.date);
    const mPlace = h("input", { maxlength: 200, value: (fam.marriage && fam.marriage.place) || "" });
    const err = h("div", { class: "error-text" });
    const partners = h("div", { class: "person-grid" }, ...fam.partners.map((p, i) => h("div", null, personCard(p, { onclick: () => { m.close(); go(`person/${p.id}`); } }),
      fam.partners.length > 1 ? h("button", { type: "button", class: "link-btn", style: "font-size:0.8rem;margin-top:4px", onclick: async () => {
        if (!await confirmDialog("Unlink partner", `Remove ${p.name} from this family? They stay in the tree.`, "Unlink")) return;
        try { const res = await api(`api/families/${fid}/partners/${i + 1}`, { method: "PUT", body: { personId: null } }); fam = res.family; saved(res); draw(); } catch (x) { fail(x); }
      } }, "Unlink from family") : null)));
    const kids = h("div");
    fam.children.forEach((c, i) => {
      const rel = relationSelect(c.relation);
      rel.addEventListener("change", async () => {
        try { const res = await api(`api/families/${fid}/children/${c.id}`, { method: "PATCH", body: { relation: rel.value } }); fam = res.family; saved(res); } catch (x) { fail(x); }
      });
      const move = async (pos) => {
        try { const res = await api(`api/families/${fid}/children/${c.id}`, { method: "PATCH", body: { position: pos } }); fam = res.family; draw(); } catch (x) { fail(x); }
      };
      kids.appendChild(h("div", { class: "qf-child" }, h("span", { class: "num" }, `${i + 1}.`), avatar(c, "sm"),
        h("span", { style: "flex:1;min-width:120px" }, h("strong", null, c.name), c.years ? h("span", { class: "hint" }, "  " + c.years) : null),
        rel,
        h("button", { type: "button", class: "icon-btn", "aria-label": "Move up", disabled: i === 0, onclick: () => move(i - 1) }, "↑"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Move down", disabled: i === fam.children.length - 1, onclick: () => move(i + 1) }, "↓"),
        h("button", { type: "button", class: "icon-btn", "aria-label": "Remove from family", title: "Remove from this family", onclick: async () => {
          if (!await confirmDialog("Remove child", `Remove ${c.name} from this family? They stay in the tree.`, "Remove")) return;
          try { const res = await api(`api/families/${fid}/children/${c.id}`, { method: "DELETE" }); fam = res.family; saved(res); draw(); } catch (x) { fail(x); }
        } }, "✕")));
    });
    if (!fam.children.length) kids.appendChild(h("div", { class: "hint" }, "No children recorded."));
    const save = h("button", { type: "button", class: "btn-primary", onclick: async () => {
      err.textContent = "";
      try {
        const md = mDate.value(), mp = mPlace.value.trim();
        const res = await api(`api/families/${fid}`, { method: "PATCH", body: { kind: kind.value, ended: ended.value || null, marriage: { date: md, place: mp || null } } });
        fam = res.family; saved(res); m.close();
      } catch (x) { err.textContent = x.message; }
    } }, "Save");
    const del = h("button", { type: "button", class: "btn-danger", onclick: async () => {
      if (!await confirmDialog("Delete family", "Delete this family? The people stay in the tree; only the link between them goes to the trash.", "Delete", true)) return;
      try { const res = await api(`api/families/${fid}`, { method: "DELETE" }); saved(res, "Family deleted"); m.close(); } catch (x) { fail(x); }
    } }, "Delete family");
    mount(body,
      h("h4", { class: "field-label" }, "Partners"), partners,
      h("div", { class: "form-row", style: "margin-top:12px" }, field("Relationship", kind), field("Ended", ended)),
      h("fieldset", null, h("legend", null, "Marriage"), h("div", { class: "form-row" }, field("Date", mDate.el, "wide")), h("div", { class: "form-row" }, field("Place", mPlace, "wide"))),
      h("h4", { class: "field-label" }, "Children (birth order)"), kids,
      (fam.custom || []).length ? h("div", null, h("h4", { class: "field-label" }, "Details"),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => customModal(`api/families/${fid}/custom`, fam.custom, "Family details", (c) => { fam.custom = c; draw(); }) },
          "✎ " + fam.custom.filter((c) => c.value).map((c) => `${c.label}: ${c.display}`).join(" · ") || "✎ Add details")) : null,
      err,
      h("div", { class: "actions" }, del, h("span", { class: "spacer" }), h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Close"), save));
  };
  draw();
}

// ---------- custom fields (§13.8) ----------
function customModal(url, custom, title, after) {
  const inputs = {};
  const rows = custom.map((c) => {
    let input;
    if (c.kind === "choice") input = h("select", null, h("option", { value: "" }, "—"), ...c.choices.map((x) => h("option", { value: x }, x)));
    else input = h("input", { maxlength: 500, placeholder: c.kind === "date" ? "e.g. 12 Mar 1985" : c.kind === "place" ? "City, State, Country" : "" });
    input.value = c.value || "";
    inputs[c.fieldId] = input;
    return field(c.label, input, "wide");
  });
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "button", class: "btn-primary", onclick: async () => {
    err.textContent = "";
    const body = {};
    for (const c of custom) { const v = inputs[c.fieldId].value.trim(); if ((c.value || "") !== v) body[c.fieldId] = v || null; }
    if (!Object.keys(body).length) { m.close(); return; }
    try { const res = await api(url, { method: "PUT", body }); m.close(); saved(res); if (after) after(res.custom); else rerender(); }
    catch (x) { err.textContent = x.message; }
  } }, "Save");
  const m = openModal(title, h("div", null, ...rows.map((r) => h("div", { class: "form-row" }, r)), err,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), save)));
}

// ---------- sources & citations (§13.9) ----------
const SOURCE_KINDS = { certificate: "Certificate", document: "Document", book: "Book", website: "Website", photo: "Photo", interview: "Interview", other: "Other" };
const QUALITY = ["Unreliable", "Questionable", "Secondary evidence", "Direct evidence"];
function citeButton(p, target, label) {
  if (!feat("sources")) return null;
  const mine = (p.citations || []).filter((c) => target.eventId ? c.eventId === target.eventId
    : target.familyId ? c.familyId === target.familyId && !c.eventId : c.personId === target.personId && !c.eventId);
  return h("button", { type: "button", class: "cite-btn" + (mine.length ? " has" : ""), title: mine.length ? `${plural(mine.length, "source")} for ${label}` : `Add a source for ${label}`,
    "aria-label": `Sources for ${label}`, onclick: (e) => { e.stopPropagation(); citationsModal(target, mine, label); } }, `📎${mine.length ? " " + mine.length : ""}`);
}
function citationsModal(target, cites, label) {
  const list = h("div");
  const draw = () => mount(list, cites.length ? cites.map((c) => h("div", { class: "row" },
    h("div", null, h("button", { type: "button", class: "link-btn", onclick: () => { m.close(); go(`sources/${c.sourceId}`); } }, c.sourceTitle),
      h("div", { class: "hint" }, [c.fact ? `for: ${c.fact}` : null, c.page, c.quality !== null && c.quality !== undefined ? QUALITY[c.quality] : null, c.note].filter(Boolean).join(" · "))),
    h("button", { type: "button", class: "icon-btn", "aria-label": "Remove citation", onclick: async () => {
      try { const res = await api(`api/citations/${c.id}`, { method: "DELETE" }); saved(res, "Removed"); cites = cites.filter((x) => x !== c); draw(); } catch (x) { fail(x); }
    } }, "✕"))) : h("div", { class: "hint" }, "No sources yet."));
  draw();
  const srcSel = h("select", { "aria-label": "Source" }, h("option", { value: "" }, "Choose a source…"));
  api("api/sources").then((r) => r.items.forEach((x) => srcSel.appendChild(h("option", { value: x.id }, `${x.title} (${SOURCE_KINDS[x.kind]})`)))).catch(() => {});
  const page = h("input", { maxlength: 200, placeholder: "Page, entry or detail" });
  const quality = h("select", { "aria-label": "How reliable" }, h("option", { value: "" }, "Reliability —"), ...QUALITY.map((q, i) => h("option", { value: String(i) }, q)));
  const fact = target.personId && !target.eventId ? h("select", { "aria-label": "Which fact" }, h("option", { value: "" }, "The whole record"),
    ...[["name", "Name"], ["gender", "Gender"], ["parents", "Parents"], ["biography", "Biography"]].map(([k, l]) => h("option", { value: k }, l))) : null;
  const add = h("button", { type: "button", class: "btn-primary btn-small", onclick: async () => {
    if (!srcSel.value) { toast("Choose a source, or make a new one.", { error: true }); return; }
    try {
      const body = { sourceId: srcSel.value, ...target, page: page.value || null, quality: quality.value === "" ? null : +quality.value };
      if (fact && fact.value) body.fact = fact.value;
      const res = await api("api/citations", { method: "POST", body });
      saved(res, "Source added"); cites = cites.concat([res.citation]); draw(); page.value = "";
    } catch (x) { fail(x); }
  } }, "Add");
  const m = openModal(`Sources for ${label}`, h("div", null, list, h("hr", { class: "sep" }),
    h("div", { class: "form-row notify-add" }, srcSel, h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => sourceModal(null, (src) => {
      srcSel.appendChild(h("option", { value: src.id }, src.title)); srcSel.value = src.id; }) }, "+ New source")),
    h("div", { class: "form-row notify-add" }, fact, page, quality, add)), { onClose: () => rerender() });
}
function sourceModal(src, after) {
  const title = h("input", { maxlength: 200, value: (src && src.title) || "" });
  const kind = h("select", null, ...Object.entries(SOURCE_KINDS).map(([k, l]) => h("option", { value: k }, l)));
  kind.value = (src && src.kind) || "document";
  const author = h("input", { maxlength: 200, value: (src && src.author) || "" });
  const date = h("input", { maxlength: 60, value: (src && src.date) || "", placeholder: "e.g. 1985, or 2026 for an interview" });
  const repo = h("input", { maxlength: 200, value: (src && src.repository) || "", placeholder: "Where it's kept" });
  const url = h("input", { type: "url", maxlength: 500, value: (src && src.url) || "", placeholder: "https://…" });
  const note = h("textarea", { maxlength: 5000, rows: 3 }, (src && src.note) || "");
  const told = personPicker({ placeholder: "Who told it? (interviews)" });
  if (src && src.toldBy) told.set({ id: src.toldBy, name: src.toldByName || "someone" });
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "button", class: "btn-primary", onclick: async () => {
    err.textContent = "";
    const body = { title: title.value, kind: kind.value, author: author.value || null, date: date.value || null,
      repository: repo.value || null, url: url.value || null, note: note.value || null, toldBy: told.get() ? told.get().id : null };
    try {
      const res = src ? await api(`api/sources/${src.id}`, { method: "PATCH", body }) : await api("api/sources", { method: "POST", body });
      m.close(); saved(res, "Saved"); if (after) after(res.source); else rerender();
    } catch (x) { err.textContent = x.message; }
  } }, "Save");
  const m = openModal(src ? "Edit source" : "New source", h("div", null,
    h("div", { class: "form-row" }, field("Title", title, "wide"), field("Kind", kind)),
    h("div", { class: "form-row" }, field("Author / issued by", author), field("Date", date)),
    h("div", { class: "form-row" }, field("Kept at", repo), field("Web address", url)),
    h("div", { class: "form-row" }, h("div", { class: "field wide" }, "Told by", told.el)),
    field("Notes", note, "wide"), err,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), save)), { wide: true });
}
async function viewSources(id) {
  if (id) {
    const s = await api(`api/sources/${id}`);
    return h("div", null,
      h("div", { class: "page-head" }, h("h2", null, `📎 ${s.title}`), h("div", { class: "person-actions" },
        h("button", { type: "button", class: "btn-ghost", onclick: () => go("sources") }, "← All sources"),
        h("button", { type: "button", class: "btn-secondary", onclick: () => sourceModal(s) }, "✎ Edit"),
        h("button", { type: "button", class: "btn-ghost", onclick: async () => {
          if (!await confirmDialog("Delete source", `Delete “${s.title}”? Its ${plural(s.citationsList.length, "citation")} stop showing. You can undo it from History.`, "Delete", true)) return;
          try { const res = await api(`api/sources/${s.id}`, { method: "DELETE" }); saved(res, "Deleted"); go("sources"); } catch (x) { fail(x); }
        } }, "🗑 Delete"))),
      h("div", { class: "card" }, h("div", { class: "kv" },
        ...[["Kind", SOURCE_KINDS[s.kind]], ["Author / issued by", s.author], ["Date", s.date], ["Kept at", s.repository],
          ["Told by", s.toldByName ? h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${s.toldBy}`) }, s.toldByName) : null],
          ["Web address", s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.url) : null]]
          .filter(([, v]) => v).map(([k, v]) => h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, k), h("div", { class: "kv-value" }, v)))),
        s.note ? h("p", { style: "white-space:pre-wrap" }, s.note) : null),
      h("div", { class: "card" }, h("h3", null, "What it supports"),
        s.citationsList.length ? s.citationsList.map((c) => h("div", { class: "row" },
          h("div", null, c.personId ? h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${c.personId}`) }, c.personName || "someone") : "a family",
            h("span", { class: "hint" }, " — " + [evLabel(c.what) || c.what, c.page, c.quality !== null ? QUALITY[c.quality] : null].filter(Boolean).join(" · "))))) : h("div", { class: "hint" }, "Nothing cites it yet.")));
  }
  const r = await api("api/sources");
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Sources ", h("span", { class: "hint" }, plural(r.items.length, "source"))),
      h("button", { type: "button", class: "btn-primary", onclick: () => sourceModal(null) }, "+ Source")),
    h("p", { class: "hint" }, "Where the information came from: certificates, documents, books, websites, or what someone told you. Add them to facts with the 📎 buttons on a person's page."),
    h("div", { class: "card" }, r.items.length ? r.items.map((x) => h("div", { class: "person-row", role: "button", tabindex: "0", onclick: () => go(`sources/${x.id}`) },
      h("div", { class: "grow" }, h("div", { class: "nm" }, x.title), h("div", { class: "sub" }, [SOURCE_KINDS[x.kind], x.author, x.date, plural(x.citations, "citation")].filter(Boolean).join(" · ")))))
      : h("div", { class: "empty" }, "No sources yet.")));
}

// ---------- contacts & address book (§13.11) ----------
const CONTACT_KINDS = { phone: "📞 Phone", whatsapp: "💬 WhatsApp", email: "✉️ Email", address: "🏠 Address", other: "Other" };
function contactsCard(p) {
  const list = h("div");
  const draw = (contacts) => mount(list, contacts.length ? contacts.map((c) => h("div", { class: "row" },
    h("div", null, h("span", { class: "hint" }, CONTACT_KINDS[c.kind] + (c.label ? ` · ${c.label}` : "")), h("div", { style: "white-space:pre-wrap" },
      c.link ? h("a", { href: c.link, target: c.kind === "address" || c.kind === "whatsapp" ? "_blank" : null, rel: "noopener noreferrer" }, c.value) : c.value)),
    h("button", { type: "button", class: "icon-btn", "aria-label": "Remove", onclick: async () => {
      try { const res = await api(`api/contacts/${c.id}`, { method: "DELETE" }); saved(res, "Removed"); draw(res.contacts); } catch (x) { fail(x); }
    } }, "✕"))) : h("div", { class: "hint" }, "No contact details."));
  draw(p.contacts || []);
  const kind = h("select", { "aria-label": "Kind" }, ...Object.entries(CONTACT_KINDS).map(([k, l]) => h("option", { value: k }, l)));
  const label = h("input", { maxlength: 60, placeholder: "label, e.g. work mobile", "aria-label": "Label" });
  const value = h("input", { maxlength: 500, placeholder: "number, address…", "aria-label": "Contact" });
  const preview = h("span", { class: "hint" });
  value.addEventListener("input", debounce(async () => {
    if (!["phone", "whatsapp"].includes(kind.value) || !value.value.trim()) { preview.textContent = ""; return; }
    try { preview.textContent = "Saved as " + (await api(`api/phone-preview?value=${encodeURIComponent(value.value)}`)).value; } catch (e) { preview.textContent = e.message; }
  }, 350));
  const add = h("button", { type: "button", class: "btn-secondary btn-small", onclick: async () => {
    try { const res = await api(`api/people/${p.id}/contacts`, { method: "POST", body: { kind: kind.value, label: label.value || null, value: value.value } });
      saved(res, "Added"); draw(res.contacts); value.value = ""; label.value = ""; preview.textContent = ""; } catch (x) { fail(x); }
  } }, "Add");
  return h("div", { class: "card" }, h("h3", null, "Contact"), list,
    h("div", { class: "form-row notify-add", style: "margin-top:8px" }, kind, label, value, add), preview,
    h("div", { class: "hint" }, "Seen by everyone in the household. Never exported unless someone ticks Contact details in Export."));
}
async function viewContacts() {
  const scope = lsGet("abScope") === "close" ? "close" : "all";
  const r = await api(`api/contacts?scope=${scope}`);
  const chosen = new Set(r.items.map((p) => p.id));
  const dl = h("a", { class: "btn-primary", download: "family-contacts.vcf" }, "⬇ Download vCard (.vcf)");
  const syncDl = () => { dl.href = `api/contacts.vcf?ids=${[...chosen].join(",")}`; dl.classList.toggle("disabled", !chosen.size); };
  syncDl();
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Address book ", h("span", { class: "hint" }, plural(r.total, "person"))),
      h("div", { class: "toolbar", style: "margin:0" }, state.user.mePersonId ? segmented([["all", "Everyone"], ["close", "Close family"]], scope, (k) => { lsSet("abScope", k); rerender(); }).el : null, dl)),
    h("p", { class: "hint" }, "Living relatives with contact details. Untick anyone you don't want in the download; the .vcf file imports into your phone's contacts."),
    h("div", { class: "card" }, r.items.length ? r.items.map((p) => {
      const box = h("input", { type: "checkbox", checked: true, "aria-label": `Include ${p.name}` });
      box.addEventListener("change", () => { if (box.checked) chosen.add(p.id); else chosen.delete(p.id); syncDl(); });
      return h("div", { class: "person-row" }, box, avatar(p),
        h("div", { class: "grow" }, h("div", { class: "nm" }, h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${p.id}`) }, shownName(p))),
          p.relationship ? h("div", { class: "sub" }, relText(p.relationship)) : null,
          h("div", { class: "contact-chips" }, ...p.contacts.map((c) => c.link ? h("a", { class: "chip", href: c.link, target: c.kind === "address" || c.kind === "whatsapp" ? "_blank" : null, rel: "noopener noreferrer" }, CONTACT_KINDS[c.kind].split(" ")[0] + " " + c.value.split("\n")[0]) : h("span", { class: "chip" }, c.value)))));
    }) : h("div", { class: "empty" }, "Nobody has contact details yet. Add them on a living relative's page.")));
}

// ---------- duplicates & merge (§13.4) ----------
async function viewDuplicates(args) {
  if (args[0] === "merge") return viewMerge(args[1], args[2]);
  const r = await api("api/duplicates");
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Possible duplicates ", h("span", { class: "hint" }, plural(r.total, "pair")))),
    h("p", { class: "hint" }, "People who may have been entered twice — similar names (Lakshmi / Laxmi, Venkat / Venkata), close birth years, the same parents. Compare them, then merge or say they're different people."),
    r.items.length ? h("div", { class: "card" }, ...r.items.map((it) => h("div", { class: "dup-row" },
      h("div", { class: "dup-pair" }, ...it.people.map((p) => personCard(p))),
      h("div", { class: "hint" }, `${Math.round(it.score * 100)}% · ${it.reasons.join(", ")}`),
      h("div", { class: "person-actions" },
        h("button", { type: "button", class: "btn-primary btn-small", onclick: () => go(`duplicates/merge/${it.a}/${it.b}`) }, "Compare & merge"),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
          try { await api("api/duplicates/dismiss", { method: "POST", body: { a: it.a, b: it.b } }); toast("Remembered — they won't be suggested again"); rerender(); } catch (x) { fail(x); }
        } }, "Not the same person")))))
      : h("div", { class: "card" }, h("div", { class: "empty" }, "No likely duplicates. 🎉")));
}
async function viewMerge(aId, bId) {
  const c = await api(`api/duplicates/compare?a=${encodeURIComponent(aId)}&b=${encodeURIComponent(bId)}`);
  let keepLeft = true;
  const box = h("div");
  const draw = () => {
    const K = keepLeft ? c.a : c.b, O = keepLeft ? c.b : c.a;
    const choices = {};
    const facts = [["given_names", "First names", (p) => p.givenNames, true], ["surname", "Surname", (p) => p.surname, true],
      ["birth_surname", "Birth surname", (p) => p.birthSurname, true], ["nickname", "Nickname", (p) => p.nickname, true],
      ["given_local", "Name in script", (p) => p.givenLocal, true], ["gender", "Gender", (p) => p.gender, false],
      ["birth", "Birth", (p) => p.birth && [p.birth.dateDisplay, p.birth.place].filter(Boolean).join(", "), "event"],
      ["death", "Death", (p) => p.death && [p.death.dateDisplay, p.death.place].filter(Boolean).join(", "), "event"],
      ["biography", "Biography", (p) => p.biography && p.biography.slice(0, 120) + (p.biography.length > 120 ? "…" : ""), false]];
    const rows = facts.map(([key, label, get, both]) => {
      const kv = get(K), ov = get(O);
      if (!ov || kv === ov) return h("tr", null, h("th", null, label), h("td", null, kv || "—"), h("td", { class: "hint" }, ov || "—"), h("td"));
      const opts = [["keep", kv ? "Keep left" : "—"], ["other", "Take right"]];
      if (both === true && kv) opts.push(["both", "Keep both (right as also known as)"]);
      if (both === "event" && kv) opts.push(["both", "Keep both (right as an extra event)"]);
      const sel = h("select", { "aria-label": `${label}: which to keep` }, ...opts.map(([v, l]) => h("option", { value: v }, l)));
      sel.value = kv ? "keep" : "other";
      choices[key] = sel;
      return h("tr", null, h("th", null, label), h("td", null, kv || "—"), h("td", null, ov), h("td", null, sel));
    });
    const counts = (p) => `${plural(p.events.length, "event")}, ${plural(p.photoCount + p.documentCount, "photo")}, ${plural(p.storyCount, "story")}, ${plural(p.families.length, "family")}`;
    mount(box,
      h("div", { class: "card" }, h("table", { class: "merge-table" },
        h("thead", null, h("tr", null, h("th"), h("th", null, "Keep: ", h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${K.id}`) }, K.name)),
          h("th", null, "Merge in: ", h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${O.id}`) }, O.name)), h("th"))),
        h("tbody", null, ...rows,
          h("tr", null, h("th", null, "Everything else"), h("td", { class: "hint" }, counts(K)), h("td", { class: "hint" }, counts(O)), h("td", { class: "hint" }, "moves to the kept person"))))),
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn-ghost", onclick: () => { keepLeft = !keepLeft; draw(); } }, "⇄ Swap which one to keep"),
        h("span", { class: "spacer" }),
        h("button", { type: "button", class: "btn-primary", onclick: async () => {
          const choose = {};
          for (const [k, sel] of Object.entries(choices)) choose[k] = sel.value;
          if (!await confirmDialog("Merge", `Merge ${O.name} into ${K.name}? Their families, events, photos, stories, sources and contacts move to ${K.name}, and ${O.name} goes away. You can undo it in one go.`, "Merge")) return;
          try { const res = await api(`api/people/${K.id}/merge`, { method: "POST", body: { otherId: O.id, choose } }); saved(res, "Merged"); go(`person/${K.id}`); }
          catch (x) { fail(x); }
        } }, "Merge")));
  };
  draw();
  return h("div", null, h("div", { class: "page-head" }, h("h2", null, "Compare & merge"),
    h("button", { type: "button", class: "btn-ghost", onclick: () => go("duplicates") }, "← Duplicates")), box);
}

// ---------- the "More" page: tools that don't need a sidebar slot ----------
function viewMore() {
  const tile = (icon, title, text, route) => h("button", { type: "button", class: "more-tile", onclick: () => go(route) }, h("span", { class: "more-icon" }, icon), h("strong", null, title), h("span", { class: "hint" }, text));
  return h("div", null, h("h2", { class: "page-title" }, "More"),
    h("div", { class: "more-grid" },
      tile("🔗", "How are they related?", "Pick any two people to see what they are to each other", "relate"),
      tile("🧭", "Find relatives", feat("kin_names") ? "First cousins, all my Babais, descendants of…" : "First cousins, all my uncles, descendants of…", "people/relatives"),
      feat("sources") ? tile("📎", "Sources", "Certificates, documents and interviews behind the facts", "sources") : null,
      feat("contacts") ? tile("📇", "Address book", "Phone numbers and addresses, and a vCard for your phone", "contacts") : null,
      feat("tithi") ? tile("🪔", "Tithi dates", "This year's shraddha and janma tithi dates, to print for the priest", "tithis") : null,
      feat("duplicates") ? tile("👯", "Duplicates", "People who may have been entered twice — merge them", "duplicates") : null,
      feat("quiz") ? tile("🧩", "Who is this?", "A photo quiz for kids and everyone", "quiz") : null,
      feat("printing") ? tile("🖼️", "Wall chart", "Print a big chart, or tile it across A4 sheets", "print/chart") : null,
      feat("printing") ? tile("📖", "Family book", "A printable book with a page for each person", "print/book") : null,
      feat("inbox") ? tile("📥", "Unsorted photos", "Photos dropped into the inbox folder, waiting to be sorted", "photos/unsorted") : null,
      feat("map") ? tile("🗺️", "Map", "Where the family was born, married and lived", "map") : null));
}


// ---------- "Who is this?" quiz and kids mode (§13.13) ----------
const QUIZ_MODES = { who: "Who is this?", call: "What do you call them?", find: "Find them" };
function quizPhoto(ph, size = 1024) {
  return `api/media/${encodeURIComponent(ph.media)}/file?size=${size}${ph.region ? "&region=" + encodeURIComponent(ph.region) : ""}`;
}
/* The game itself: `opts` = {playerId, mode, scope, deceased, difficulty, modes (switchable), kids}. */
function quizGame(opts) {
  let score = { right: 0, total: 0 };
  let mode = opts.mode;
  const modeBar = opts.modes && opts.modes.length > 1 ? h("div", { class: "quiz-modes" }) : null;
  const drawModes = () => { if (modeBar) mount(modeBar, ...opts.modes.map((m) => h("button", { type: "button", class: "quiz-mode" + (m === mode ? " on" : ""), onclick: () => { mode = m; drawModes(); next(); } }, QUIZ_MODES[m]))); };
  const stage = h("div", { class: "quiz-stage" });
  const scoreEl = h("div", { class: "quiz-score" });
  const drawScore = () => { scoreEl.textContent = score.total ? `⭐ ${score.right} / ${score.total}` : ""; };
  async function next() {
    mount(stage, spinner());
    let q;
    try { q = await api("api/quiz/next", { method: "POST", body: { playerId: opts.playerId, mode, scope: opts.scope, deceased: opts.deceased, difficulty: opts.difficulty || "normal" } }); }
    catch (e) {
      if (e.status === 423 && opts.kids) { mount(stage, h("div", { class: "quiz-timeup" }, h("div", { class: "big" }, "⏰"), h("h2", null, "Time's up!"), h("p", null, "Ask a grown-up.")) ); return; }
      mount(stage, h("div", { class: "empty" }, e.message)); return;
    }
    let answered = false;
    const buttons = [];
    const onPick = async (choice, btn) => {
      if (answered) return;
      answered = true;
      let a;
      try { a = await api("api/quiz/answer", { method: "POST", body: { token: q.token, choice } }); }
      catch (e) { toast(e.message, { error: true }); next(); return; }
      score.total += 1; if (a.correct) score.right += 1; drawScore();
      for (const b of buttons) {
        b.disabled = true;
        if (b.dataset.id === a.answer) b.classList.add("right");
        else if (b === btn) b.classList.add("wrong");
      }
      stage.appendChild(h("div", { class: "quiz-result " + (a.correct ? "ok" : "no") },
        h("div", { class: "big" }, a.correct ? "🎉 Yes!" : "Not quite"),
        a.explanation ? h("div", { class: "quiz-explain" }, a.explanation) : null,
        h("button", { type: "button", class: "btn-primary quiz-next", onclick: next }, "Next →")));
      setTimeout(() => { const n = stage.querySelector(".quiz-next"); if (n) n.focus(); }, 30);
    };
    if (q.mode === "find") {
      mount(stage, h("div", { class: "quiz-prompt" }, "Find ", h("strong", null, q.name), q.nameLocal ? h("span", { class: "name-local" }, " " + q.nameLocal) : null),
        h("div", { class: "quiz-photos" }, ...q.choices.map((c) => {
          const b = h("button", { type: "button", class: "quiz-photo-choice", "data-id": c.id, "aria-label": "This one" }, h("img", { src: quizPhoto(c.photo, 256), alt: "" }));
          b.addEventListener("click", () => onPick(c.id, b)); buttons.push(b); return b;
        })));
    } else {
      mount(stage, h("div", { class: "quiz-prompt" }, q.mode === "who" ? "Who is this?" : `What does ${q.playerName.split(" ")[0]} call them?`),
        h("div", { class: "quiz-face" }, h("img", { src: quizPhoto(q.photo), alt: "Who is this?" })),
        h("div", { class: "quiz-choices" }, ...q.choices.map((c) => {
          const b = h("button", { type: "button", class: "quiz-choice", "data-id": c.id }, q.mode === "who" ? shownName({ name: c.label, nameLocal: c.nameLocal }) : c.label);
          b.addEventListener("click", () => onPick(c.id, b)); buttons.push(b); return b;
        })));
    }
  }
  drawModes(); drawScore(); next();
  return h("div", { class: "quiz" }, modeBar, scoreEl, stage);
}

async function viewQuiz() {
  const saved0 = (() => { try { return JSON.parse(lsGet("quizSetup") || "{}"); } catch (e) { return {}; } })();
  let player = null;
  const meId = state.user.mePersonId;
  const playerLine = h("div", { class: "hint" });
  const setPlayer = (p) => { player = p; playerLine.textContent = p ? `Relationship names are worked out from ${p.name.split(" ")[0]}'s point of view.` : "Choose who is playing."; };
  if (saved0.player && saved0.player.id) setPlayer(saved0.player);
  else if (meId) setPlayer({ id: meId, name: state.user.mePersonName || "you" });
  else setPlayer(null);
  const picker = personPicker({ placeholder: "Who is playing?", initial: player, onChange: setPlayer });
  const mode = segmented(Object.entries(QUIZ_MODES), saved0.mode || "who");
  const scope = segmented([["close", "Close family"], ["all", "Everyone"]], saved0.scope || "close");
  const deceased = h("input", { type: "checkbox", checked: !!saved0.deceased });
  const diff = segmented([["easy", "Easy (3 choices)"], ["normal", "Normal (4)"]], saved0.difficulty || "normal");
  const game = h("div", null);
  const opts = () => {
    const o = { playerId: player && player.id, mode: mode.get(), scope: scope.get(), deceased: deceased.checked, difficulty: diff.get() };
    lsSet("quizSetup", JSON.stringify({ player, mode: o.mode, scope: o.scope, deceased: o.deceased, difficulty: o.difficulty }));
    return o;
  };
  const play = h("button", { type: "button", class: "btn-primary", onclick: () => {
    const o = opts();
    if (!o.playerId) { toast("Choose who is playing first.", { error: true }); return; }
    mount(game, h("div", { class: "card" }, quizGame(o)));
  } }, "▶ Play");
  // kids mode
  const kmodes = Object.keys(QUIZ_MODES).map((k) => [k, h("input", { type: "checkbox", checked: true })]);
  const limit = h("select", { "aria-label": "Time limit" }, ...[[0, "No time limit"], [10, "10 minutes"], [15, "15 minutes"], [30, "30 minutes"], [60, "1 hour"]].map(([v, l]) => h("option", { value: String(v) }, l)));
  const startKids = h("button", { type: "button", class: "btn-secondary", onclick: async () => {
    const o = opts();
    if (!o.playerId) { toast("Choose who is playing first.", { error: true }); return; }
    const modes = kmodes.filter(([, cb]) => cb.checked).map(([k]) => k);
    if (!modes.length) { toast("Tick at least one game.", { error: true }); return; }
    const ok = await confirmDialog("Start kids mode",
      `This device will show only the quiz, playing as ${player.name}, until a grown-up unlocks it: hold the 🔒 button for 3 seconds, then ` +
      (state.user.kidPinSet ? "enter your kids-mode PIN." : "answer a grown-up question (set a PIN in Settings to use that instead).") +
      " For a full lock of the phone or tablet, also use iOS Guided Access or Android screen pinning.", "Start");
    if (!ok) return;
    try {
      const k = await api("api/kidmode/start", { method: "POST", body: { playerId: o.playerId, modes, scope: o.scope, deceased: o.deceased, minutes: +limit.value } });
      lsSet("kidsHintShown", "1");
      kidsScreen(k);
    } catch (e) { fail(e); }
  } }, "🔒 Start kids mode");
  return h("div", null, h("h2", { class: "page-title" }, "🧩 Who is this?"),
    h("div", { class: "card" }, h("h3", null, "Set up"),
      h("div", { class: "field wide" }, "Play as", picker.el, playerLine),
      h("div", { class: "field wide" }, "Game", mode.el),
      h("div", { class: "form-row" }, h("div", { class: "field" }, "Who's in it", scope.el), h("div", { class: "field" }, "Difficulty", diff.el)),
      h("label", { class: "check-row" }, deceased, "Include people who have died"),
      h("p", { class: "hint" }, "Uses profile photos and faces tagged in photos; people without one are left out. People who are often missed come up more often."),
      h("div", { class: "actions" }, play)),
    game,
    h("div", { class: "card" }, h("h3", null, "Kids mode"),
      h("p", { class: "hint" }, "Locks this device to the quiz (the server refuses everything else) so a child can play on your login without changing the tree."),
      h("div", { class: "field wide" }, "Games they can switch between", h("div", { class: "chips" }, ...kmodes.map(([k, cb]) => h("label", { class: "check-row" }, cb, QUIZ_MODES[k])))),
      h("div", { class: "form-row" }, field("Time limit", limit)),
      h("div", { class: "actions" }, startKids)));
}

/* The locked screen: only the quiz, and a 🔒 to hold for 3 seconds. */
function kidsScreen(k) {
  state.kids = k;
  document.documentElement.classList.add("kids-locked");
  const root = $("#app");
  const lock = h("button", { type: "button", class: "kids-lock", "aria-label": "Grown-ups: hold for 3 seconds to unlock", title: "Grown-ups: hold for 3 seconds" }, "🔒");
  let timer = null;
  const startHold = (e) => { e.preventDefault(); lock.classList.add("holding"); timer = setTimeout(() => { lock.classList.remove("holding"); unlockModal(); }, 3000); };
  const endHold = () => { clearTimeout(timer); lock.classList.remove("holding"); };
  lock.addEventListener("pointerdown", startHold);
  ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => lock.addEventListener(ev, endHold));
  lock.addEventListener("contextmenu", (e) => e.preventDefault());
  const who = k.playerGiven || k.playerName || "";
  const screen = h("div", { class: "kids-screen" }, lock,
    h("h1", { class: "kids-title" }, `🧩 ${who ? who + "'s" : "The"} family quiz`),
    k.timeUp ? h("div", { class: "quiz-timeup" }, h("div", { class: "big" }, "⏰"), h("h2", null, "Time's up!"), h("p", null, "Ask a grown-up."))
      : quizGame({ playerId: k.playerId, mode: k.modes[0], modes: k.modes, scope: k.scope, deceased: k.deceased, kids: true, difficulty: "easy" }));
  mount(root, screen);
  if (k.endsAt && !k.timeUp) {
    const left = new Date(k.endsAt).getTime() - Date.now();
    if (left > 0) setTimeout(() => location.reload(), left + 500);
  }
  const fs = document.documentElement.requestFullscreen;
  if (fs) { try { const pr = document.documentElement.requestFullscreen(); if (pr && pr.catch) pr.catch(() => {}); } catch (e) { /* not allowed */ } }
}

async function unlockModal() {
  let ch;
  try { ch = await api("api/kidmode/challenge", { method: "POST" }); } catch (e) { fail(e); return; }
  if (!ch.locked) { location.reload(); return; }
  if (ch.blockedUntil) {
    openModal("Unlock", h("p", null, `Too many wrong tries. Try again after ${new Date(ch.blockedUntil).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.`));
    return;
  }
  const input = h("input", { type: ch.kind === "pin" ? "password" : "text", inputmode: "numeric", autocomplete: "off", maxlength: "6", "aria-label": ch.kind === "pin" ? "PIN" : "Answer" });
  const err = h("div", { class: "error-text", role: "alert" });
  const m = openModal("Grown-ups only", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    try {
      await api("api/kidmode/unlock", { method: "POST", body: { answer: input.value.trim() } });
      if (document.fullscreenElement && document.exitFullscreen) { try { await document.exitFullscreen(); } catch (x) { /* ignore */ } }
      location.reload();
    } catch (x) {
      err.textContent = x.message;
      input.value = "";
      if (ch.kind === "question" && x.status === 403) { m.close(); setTimeout(unlockModal, 600); }
    }
  } }, h("p", null, ch.kind === "pin" ? "Enter your kids-mode PIN." : ch.question), input, err,
    h("div", { class: "actions" }, h("button", { type: "submit", class: "btn-primary" }, "Unlock"))));
}

async function kidPinCard() {
  const card = h("div", { class: "card" });
  const draw = () => {
    const pin = h("input", { type: "password", inputmode: "numeric", maxlength: "6", autocomplete: "new-password", placeholder: "4–6 digits", "aria-label": "New PIN" });
    const save = async (value) => {
      try { const r = await api("api/me/kid-pin", { method: "PUT", body: { pin: value } }); state.user.kidPinSet = r.pinSet; toast(r.pinSet ? "PIN saved" : "PIN removed"); draw(); }
      catch (e) { fail(e); }
    };
    mount(card, h("h3", null, "Kids-mode PIN"),
      h("p", { class: "hint" }, state.user.kidPinSet ? "Set. Kids mode started by you unlocks with it." : "Not set: unlocking kids mode asks a grown-up question (like 47 + 38) instead."),
      h("div", { class: "form-row" }, field(state.user.kidPinSet ? "New PIN" : "PIN", pin, "narrow"),
        h("button", { type: "button", class: "btn-secondary", onclick: () => { if (!/^\d{4,6}$/.test(pin.value)) { toast("The PIN must be 4 to 6 digits.", { error: true }); return; } save(pin.value); } }, "Save PIN"),
        state.user.kidPinSet ? h("button", { type: "button", class: "btn-ghost", onclick: () => save(null) }, "Remove") : null),
      h("button", { type: "button", class: "link-btn", onclick: () => go("quiz") }, "Open the quiz"));
  };
  draw();
  return card;
}

async function kidSessionsCard() {
  const card = h("div", { class: "card" });
  const draw = async () => {
    let r;
    try { r = await api("api/admin/kidmode"); } catch (e) { mount(card, h("div", { class: "error-text" }, e.message)); return; }
    mount(card, h("h3", null, "Devices in kids mode"),
      r.items.length ? h("div", null, ...r.items.map((it) => h("div", { class: "row" },
        h("div", null, h("span", { class: "name" }, it.playerName || "?"), h("span", { class: "hint" }, ` · started by ${it.userName || it.userId} ${fmtWhen(it.startedAt)}${it.endsAt ? " · until " + new Date(it.endsAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}`)),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
          try { await api(`api/kidmode/${encodeURIComponent(it.deviceId)}`, { method: "DELETE" }); toast("Kids mode ended on that device"); draw(); } catch (e) { fail(e); } } }, "End kids mode"))))
        : h("div", { class: "hint" }, "None."));
  };
  await draw();
  return card;
}

// ---------- profile photo ----------
function photoModal(person) {
  const file = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp,image/gif" });
  const err = h("div", { class: "error-text" });
  const preview = person.photo ? h("img", { src: `api/media/${encodeURIComponent(person.photo)}/file?size=1024`, alt: `Photo of ${person.name}`, style: "max-width:100%;max-height:50vh;border-radius:10px;display:block;margin:0 auto 12px" }) : h("div", { class: "hint", style: "margin-bottom:10px" }, "No photo yet.");
  const upload = h("button", { type: "button", class: "btn-primary", onclick: async () => {
    err.textContent = "";
    if (!file.files.length) { err.textContent = "Choose a photo first."; return; }
    const big = tooBig(file.files[0], await uploadLimitMb());
    if (big) { err.textContent = big + "."; return; }
    const fd = new FormData();
    fd.append("file", file.files[0]);
    upload.disabled = true; upload.textContent = "Uploading…";
    try { const res = await api(`api/people/${person.id}/photo`, { method: "POST", formData: fd }); m.close(); saved(res, "Photo saved"); rerender(); }
    catch (x) { err.textContent = x.message; upload.disabled = false; upload.textContent = "Upload"; }
  } }, "Upload");
  const remove = person.photo ? h("button", { type: "button", class: "btn-danger", onclick: async () => {
    try { const res = await api(`api/people/${person.id}/photo`, { method: "DELETE" }); m.close(); saved(res, "Photo removed"); rerender(); } catch (x) { fail(x); }
  } }, "Remove photo") : null;
  const existing = h("div", { class: "media-grid small" });
  api(`api/media?personId=${encodeURIComponent(person.id)}&kind=photo&page_size=60`).then((r) => {
    const others = r.items.filter((x) => x.id !== person.photo);
    if (!others.length) return;
    existing.before(h("h4", { class: "field-label" }, "Or choose one of their photos"));
    others.forEach((x) => existing.appendChild(mediaThumb(x, { onclick: async () => {
      try { const res = await api(`api/people/${person.id}/photo`, { method: "PUT", body: { mediaId: x.id } }); m.close(); saved(res, "Profile photo set"); rerender(); } catch (e) { fail(e); }
    } })));
  }).catch(() => {});
  const m = openModal(`Photo of ${person.name}`, h("div", null, preview, h("div", null, existing),
    field("New photo (JPEG, PNG, WebP or GIF)", file, "wide"),
    h("div", { class: "hint", style: "margin-top:6px" }, "Location and camera details are removed when the photo is saved."), err,
    h("div", { class: "actions" }, remove, h("span", { class: "spacer" }), h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), upload)));
}

// ---------- routing ----------
// In-app navigation replaces the address instead of adding history entries (backnav.js): the back
// gesture in the Home Assistant app closes a dialog, then returns to the start page, then leaves.
function go(route) { BackNav.go("#/" + route); }
function currentRoute() {
  const parts = (location.hash || "").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  return { name: parts[0] || "tree", args: parts.slice(1) };
}
let renderSeq = 0;
function rerender() { route(); }
function setNav(name) {
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => {
    const r = b.dataset.route;
    b.classList.toggle("active", r === name || (name === "person" && r === "people") || (name === "kin" && r === "settings") ||
      (["more", "relate", "sources", "contacts", "duplicates", "quiz", "print"].includes(name) && r === "more") ||
      (name === "admin" && r.startsWith("admin/")));
  });
}
/* Pages that belong to a module with a switch (Features). */
const ROUTE_FEATURES = { map: "map", sources: "sources", contacts: "contacts", tithis: "tithi", quiz: "quiz", print: "printing",
  duplicates: "duplicates", kin: "kin_names", export: "export" };
const FEATURE_NAMES = { map: "The places map", sources: "Sources", contacts: "Contact details and the address book",
  tithi: "Tithi dates", quiz: "The photo quiz", printing: "The wall chart and family book", duplicates: "The duplicate finder",
  kin_names: "Indian relationship names", export: "The website export", inbox: "The inbox folder" };
function featureOffView(key) {
  return h("div", null, h("div", { class: "card" }, h("h3", null, `${FEATURE_NAMES[key] || "This part of the app"} is turned off`),
    h("p", { class: "hint" }, "Nothing has been deleted — it's only hidden. ",
      state.me && state.me.isAdmin ? h("button", { type: "button", class: "link-btn", onclick: () => go("admin/settings") }, "Turn it on in Admin → App settings → Features.")
        : "An admin can turn it on in Admin → App settings.")));
}
async function route() {
  if (state.kids) return;              // kids mode: only the quiz screen
  const seq = ++renderSeq;
  const { name, args } = currentRoute();
  const view = $("#view");
  const main = $("#main");
  main.classList.toggle("wide", name === "tree");
  // the Trash page moved into Admin: old links land on its tab
  if (name === "trash") { BackNav.go("#/admin/trash"); return; }
  setNav(name);
  if (name !== "tree") mount(view, spinner());
  try {
    let content;
    const needs = ROUTE_FEATURES[name] || (name === "photos" && args[0] === "unsorted" ? "inbox" : null);
    if (needs && !feat(needs)) { if (seq === renderSeq) mount(view, featureOffView(needs)); return; }
    if (name === "tree") return await renderTree(args[0] || null, seq);
    if (name === "person") content = await viewPerson(args[0], args[1] || "overview");
    else if (name === "people") content = args[0] === "relatives" ? await viewRelatives() : await viewPeople();
    else if (name === "history") content = await viewHistory();
    else if (name === "photos") content = args[0] === "unsorted" ? await viewUnsorted() : await viewPhotos();
    else if (name === "upcoming") content = await viewUpcoming();
    else if (name === "export") content = await viewExport();
    else if (name === "settings") content = await viewSettings();
    else if (name === "kin") content = await viewKinTerms(args[0] || state.user.kinLangEffective);
    else if (name === "map") content = await viewMap(args[0] || null);
    else if (name === "sources") content = await viewSources(args[0] || null);
    else if (name === "contacts") content = await viewContacts();
    else if (name === "more") content = viewMore();
    else if (name === "relate") content = await viewRelate(args[0] || null, args[1] || null);
    else if (name === "tithis") content = await viewTithis(args[0]);
    else if (name === "quiz") content = await viewQuiz();
    else if (name === "print") content = args[0] === "book" ? await viewPrintBook() : await viewPrintChart();
    else if (name === "duplicates") content = await viewDuplicates(args);
    else if (name === "whoami") content = h("div", null, h("h2", { class: "page-title" }, "How the app sees you"), await whoamiCard());
    else if (name === "admin") content = await viewAdmin(args[0] || "settings");
    else content = h("div", { class: "empty" }, "Page not found.");
    if (seq === renderSeq) mount(view, content);
  } catch (e) {
    if (seq === renderSeq) mount(view, h("div", { class: "card fatal" }, h("h3", null, "Couldn't load this page"), h("div", null, e.message)));
  }
}

// ---------- tree ----------
async function renderTree(focusId, seq) {
  const view = $("#view");
  const t = state.tree;
  const focus = focusId || state.lastTreeFocus || "";
  const narrow = window.innerWidth < 600;
  const mode = t.mode || (narrow ? "cards" : "chart");
  if (mode === "all") return renderWholeTree(seq, focusId);
  // the cards view always shows parents and children (its "+ Add" cards rely on them)
  const up = mode === "cards" ? Math.max(1, t.up) : t.up, down = mode === "cards" ? Math.max(1, t.down) : t.down;
  const wantSides = feat("sides") && (t.sideColour || t.sideShow !== "both");
  const qs = `focus=${encodeURIComponent(focus)}&up=${up}&down=${down}&siblings=${t.siblings ? "true" : "false"}${wantSides ? "&sides=true" : ""}`;
  let data;
  try { data = await api(`api/tree?${qs}`); }
  catch (e) {
    if (e.status === 404 && focus) { state.lastTreeFocus = null; go("tree"); return; }
    throw e;
  }
  if (seq !== renderSeq) return;
  if (data.empty) { mount(view, welcomeView()); return; }
  state.lastTreeFocus = data.focus;
  if (!focus) state.homeFocus = data.focus;   // who the start page (no address) centres on
  const focusNode = data.descendants.p;
  const sel = (label, value, max, on) => h("label", null, label, (() => {
    const s = h("select", { "aria-label": label }, ...Array.from({ length: max + 1 }, (_, i) => h("option", { value: String(i) }, String(i))));
    s.value = String(value);
    s.addEventListener("change", () => on(+s.value));
    return s;
  })());
  const jump = personPicker({ placeholder: "Find someone…", onChange: (p) => p && go(`tree/${p.id}`) });
  jump.el.classList.add("tree-search");
  const canvas = h("div", { class: "tree-canvas" });
  let chart = null;
  const toolbar = h("div", { class: "tree-toolbar" },
    h("span", { class: "title", title: focusNode.name }, focusNode.name),
    h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => go(`person/${data.focus}`) }, "Open"),
    data.meId && data.meId !== data.focus ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => go(`tree/${data.meId}`) }, "Me") : null,
    jump.el,
    h("span", { class: "spacer" }),
    mode === "chart" ? sel("Up", t.up, 10, (v) => { t.up = v; lsSet("treeUp", v); rerender(); }) : null,
    mode === "chart" ? sel("Down", t.down, 10, (v) => { t.down = v; lsSet("treeDown", v); rerender(); }) : null,
    h("label", null, (() => { const c = h("input", { type: "checkbox", checked: t.siblings }); c.addEventListener("change", () => { t.siblings = c.checked; lsSet("treeSiblings", c.checked ? "1" : "0"); rerender(); }); return c; })(), "Siblings"),
    mode === "chart" && feat("sides") ? sideControls(data.meId ? null : focusNode.name) : null,
    modeSwitch(mode),
    h("button", { type: "button", class: "btn-ghost btn-small", title: "Add a couple and their children at once", onclick: () => quickFamilyModal() }, "+ Family"),
    mode === "chart" ? h("button", { type: "button", class: "btn-ghost btn-small", title: "Print or save as PDF", onclick: () => window.print() }, "🖨") : null);
  mount(view, h("div", { class: "tree-wrap" }, toolbar, canvas));
  const anchorFor = (id) => ({ id, name: id === data.focus ? focusNode.name : "this person", surname: id === data.focus ? focusNode.surname : "" });
  const onGhost = (g) => {
    const anchor = anchorFor(g.anchor || data.focus);
    if (g.ghost === "partner") addRelativeModal("partner", anchor, { familyId: g.familyId || null });
    else if (g.ghost === "child") addRelativeModal("child", anchor, { familyId: g.familyId && g.familyId !== "new" ? g.familyId : null });
    else addRelativeModal(g.ghost, anchor, { familyId: g.familyId || null });
  };
  if (mode === "cards") { mount(canvas, familyCards(data, onGhost)); canvas.style.touchAction = "auto"; canvas.style.overflowY = "auto"; return; }
  chart = window.FamilyChart.render(canvas, data, {
    editable: true,
    cardClass: sideClass,
    onOpen: (id) => go(`person/${id}`),
    onRecenter: (id) => go(`tree/${id}`),
    onGhost,
  });
  canvas.appendChild(h("div", { class: "tree-zoom" },
    h("button", { type: "button", "aria-label": "Zoom in", onclick: () => chart.zoomIn() }, "+"),
    h("button", { type: "button", "aria-label": "Zoom out", onclick: () => chart.zoomOut() }, "−"),
    h("button", { type: "button", "aria-label": "Fit the whole chart", title: "Fit", onclick: () => chart.fit() }, "⤢"),
    h("button", { type: "button", "aria-label": "Centre on the focus person", title: "Centre", onclick: () => chart.center() }, "◎")));
  canvas.appendChild(h("div", { class: "hint", style: "position:absolute;left:14px;right:64px;bottom:12px;pointer-events:none" }, "Tap a card to open · double-tap to centre · drag and pinch to move"));
}

function modeSwitch(mode) {
  return segmented([["chart", "Chart"], ["all", "Everyone"], ["cards", "Cards"]], mode, (k) => {
    state.tree.mode = k;
    lsSet("treeMode", k);
    if (k !== "all" && currentRoute().args[0] === "all") go("tree"); else rerender();
  }).el;
}

/* Father's side / mother's side (§13.16): colour cards by side, and/or fade the other side. */
function sideClass(p) {
  const t = state.tree;
  if (!("side" in p)) return "";
  const cls = [];
  if (t.sideColour && p.side) cls.push("side-" + p.side);
  // the other side fades; your own line (no side) stays
  if (t.sideShow !== "both" && p.side && p.side !== t.sideShow && p.side !== "both") cls.push("side-off");
  return cls.join(" ");
}
function sideControls(anchorName) {
  const t = state.tree;
  const colour = h("input", { type: "checkbox", checked: t.sideColour });
  colour.addEventListener("change", () => { t.sideColour = colour.checked; lsSet("treeSides", colour.checked ? "1" : "0"); rerender(); });
  const show = h("select", { "aria-label": "Show sides" }, h("option", { value: "both" }, "Both sides"),
    h("option", { value: "paternal" }, "Father's side"), h("option", { value: "maternal" }, "Mother's side"));
  show.value = t.sideShow;
  show.addEventListener("change", () => { t.sideShow = show.value; lsSet("treeSideShow", show.value); rerender(); });
  return h("span", { class: "side-controls", title: anchorName ? `Sides are worked out from ${anchorName}` : "Sides are worked out from you" },
    h("label", null, colour, "Colour by side"), show,
    t.sideColour ? h("span", { class: "side-legend" },
      h("span", null, h("span", { class: "side-dot paternal" }), "father's"),
      h("span", null, h("span", { class: "side-dot maternal" }), "mother's"),
      h("span", null, h("span", { class: "side-dot both" }), "both")) : null);
}

/* The whole tree: everyone at once, one row per generation (tree.js layoutAll). */
async function renderWholeTree(seq, focusId) {
  const view = $("#view");
  const t = state.tree;
  const data = await api(`api/tree/all${feat("sides") && (t.sideColour || t.sideShow !== "both") ? "?sides=true" : ""}`);
  if (seq !== renderSeq) return;
  if (data.empty) { mount(view, welcomeView()); return; }
  // "🌳 Tree" on a person page lands here as #/tree/<id>: start on them, not on me
  const asked = focusId && data.people.some((p) => p.id === focusId) ? focusId : null;
  data.focus = asked || data.meId;
  const canvas = h("div", { class: "tree-canvas" });
  let chart = null;
  const jump = personPicker({ placeholder: "Find someone…", onChange: (p) => {
    if (!p) return;
    jump.reset();                    // keep the search box ready for the next name
    if (!chart.centerOnId(p.id, 1)) { toast("That person isn't in this view.", { error: true }); return; }
    canvas.querySelectorAll(".card-g.focus").forEach((el) => el.classList.remove("focus"));
    const card = canvas.querySelector(`.card-g[data-id="${CSS.escape(p.id)}"]`);
    if (card) card.classList.add("focus");
  } });
  jump.el.classList.add("tree-search");
  const groups = new Set(data.people.map((p) => p.comp)).size;
  const toolbar = h("div", { class: "tree-toolbar" },
    h("span", { class: "title" }, "Whole family"),
    h("span", { class: "hint" }, plural(data.people.length, "person") + (groups > 1 ? ` · ${groups} separate groups` : "")),
    data.meId ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => chart.centerOnId(data.meId, 1) }, "Me") : null,
    jump.el,
    h("span", { class: "spacer" }),
    data.meId && feat("sides") ? sideControls(null) : null,
    modeSwitch("all"),
    h("button", { type: "button", class: "btn-ghost btn-small", title: "Add a couple and their children at once", onclick: () => quickFamilyModal() }, "+ Family"),
    h("button", { type: "button", class: "btn-ghost btn-small", title: "Print or save as PDF", onclick: () => window.print() }, "🖨"));
  mount(view, h("div", { class: "tree-wrap" }, toolbar, canvas));
  if (data.truncated) toast(`Showing ${data.people.length} of ${data.total} people — use Chart to see the rest around someone.`);
  chart = window.FamilyChart.render(canvas, data, {
    whole: true,
    cardClass: sideClass,
    fitFirst: !asked && (data.people.length <= 40 || !data.meId),
    onOpen: (id) => go(`person/${id}`),
    onRecenter: (id) => { state.tree.mode = "chart"; lsSet("treeMode", "chart"); go(`tree/${id}`); },
  });
  canvas.appendChild(h("div", { class: "tree-zoom" },
    h("button", { type: "button", "aria-label": "Zoom in", onclick: () => chart.zoomIn() }, "+"),
    h("button", { type: "button", "aria-label": "Zoom out", onclick: () => chart.zoomOut() }, "−"),
    h("button", { type: "button", "aria-label": "Fit everyone", title: "Fit", onclick: () => chart.fit() }, "⤢"),
    data.meId ? h("button", { type: "button", "aria-label": "Centre on me", title: "Me", onclick: () => chart.centerOnId(data.meId, 1) }, "◎") : null));
  canvas.appendChild(h("div", { class: "hint", style: "position:absolute;left:14px;right:64px;bottom:12px;pointer-events:none" }, "Tap a card to open · double-tap for their close-up chart · drag and pinch to move"));
}

/* Phone layout: parents, the person, then partners and children. Tap to move. */
function familyCards(data, onGhost) {
  const d = data.descendants, a = data.ancestors;
  const move = (id) => go(`tree/${id}`);
  const card = (p, extra) => personCard(p, { onclick: () => move(p.id), extra });
  const parents = [];
  if (a.parents) {
    for (const side of [a.parents.a, a.parents.b]) {
      if (side && side.p) parents.push(card(side.p));
      else parents.push(ghostCard("+ Add parent", () => onGhost({ ghost: "parent", familyId: a.parents.familyId, anchor: data.focus })));
    }
  } else {
    parents.push(ghostCard("+ Add father", () => onGhost({ ghost: "father", anchor: data.focus })));
    parents.push(ghostCard("+ Add mother", () => onGhost({ ghost: "mother", anchor: data.focus })));
  }
  const fp = d.p;
  const focus = h("div", { class: "focus-card" }, avatar(fp, "big"),
    h("div", { style: "flex:1;min-width:0" }, h("div", { class: "nm" }, fp.name), h("div", { class: "hint" }, fp.years || ""),
      relText(fp.relationship) ? h("div", { class: "rel", style: "color:var(--accent)" }, relText(fp.relationship)) : null,
      h("button", { type: "button", class: "btn-secondary btn-small", style: "margin-top:6px", onclick: () => go(`person/${fp.id}`) }, "Open")));
  const fams = d.families.map((f) => h("div", { class: "fam-block" },
    f.partner ? card(f.partner, f.marriageYear ? `married ${f.marriageYear}` : null) : h("div", { class: "hint" }, "Partner unknown"),
    h("div", { class: "person-grid" }, ...f.children.map((c) => card(c.p, c.relation && c.relation !== "birth" ? RELATION_LABELS[c.relation] : null)),
      ghostCard("+ Add child", () => onGhost({ ghost: "child", familyId: f.id, anchor: data.focus })))));
  return h("div", { class: "fam-cards" },
    h("h4", null, "Parents"), h("div", { class: "person-grid" }, ...parents),
    focus,
    data.siblings && data.siblings.length ? h("div", null, h("h4", null, "Siblings"),
      h("div", { class: "person-grid" }, ...data.siblings.map((s) => card(s, s.full === false ? "half" : null)))) : null,
    h("h4", null, "Partners & children"), ...fams,
    h("div", { class: "person-grid" }, ghostCard("+ Add partner", () => onGhost({ ghost: "partner", anchor: data.focus,
      familyId: (d.families.find((f) => !f.partner) || {}).id })),
      !d.families.length ? ghostCard("+ Add child", () => onGhost({ ghost: "child", anchor: data.focus })) : null));
}

// Submit handler for the person/relative forms: read the form (a mistake shows in
// `err`), then send it with the button disabled until it fails or finishes.
function onFormSubmit(formEl, read, err, button, send) {
  formEl.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    let body;
    try { body = read(); } catch (x) { err.textContent = x.message; return; }
    button.disabled = true;
    try { await send(body); } catch (x) { err.textContent = x.message; button.disabled = false; }
  });
}

function welcomeView() {
  const form = personForm({}, { compact: true });
  const isMe = h("input", { type: "checkbox", checked: !state.user.mePersonId });
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "submit", class: "btn-primary" }, "Start the tree");
  const f = h("form", null, form.el, h("label", { class: "check-row" }, isMe, "This is me"), err,
    h("div", { class: "actions" }, save));
  onFormSubmit(f, form.value, err, save, async (body) => {
    const res = await api("api/people", { method: "POST", body });
    if (isMe.checked) { await api("api/me/person", { method: "PUT", body: { personId: res.person.id } }); await loadMe(); }
    go(`tree/${res.person.id}`);
  });
  return h("div", { class: "welcome" },
    h("div", { class: "card" }, h("h3", null, "🌳 Start your family tree"),
      h("p", { class: "hint" }, "Add the first person — usually yourself. Then add parents, partners and children from the tree, a relative at a time."), f),
    h("div", { class: "card" }, h("h3", null, "Or add a whole family at once"),
      h("p", { class: "hint" }, "A couple, their marriage and all their children in one form."),
      h("button", { type: "button", class: "btn-secondary", onclick: () => quickFamilyModal() }, "Add a family")));
}

// ---------- people list ----------
async function viewPeople() {
  const q = h("input", { type: "search", placeholder: "Search names, maiden names, nicknames…", "aria-label": "Search" });
  const living = h("select", { "aria-label": "Living or deceased" }, h("option", { value: "" }, "Everyone"), h("option", { value: "living" }, "Living"), h("option", { value: "deceased" }, "Deceased"));
  const surname = h("select", { "aria-label": "Surname" }, h("option", { value: "" }, "All surnames"));
  const photo = h("select", { "aria-label": "Photo" }, h("option", { value: "" }, "Any photo"), h("option", { value: "true" }, "Has photo"), h("option", { value: "false" }, "No photo"));
  const sideSel = state.user.mePersonId && feat("sides") ? h("select", { "aria-label": "Side" }, h("option", { value: "" }, "Both sides"),
    h("option", { value: "paternal" }, "Father's side"), h("option", { value: "maternal" }, "Mother's side")) : null;
  const cfSel = h("select", { "aria-label": "Detail" }, h("option", { value: "" }, "Any details"));
  const cfVal = h("select", { "aria-label": "Value", hidden: true });
  if (feat("custom_fields")) api("api/custom-fields").then((r) => r.fields.filter((f) => f.appliesTo === "person").forEach((f) => cfSel.appendChild(h("option", { value: f.id }, f.label)))).catch(() => {});
  cfSel.hidden = !feat("custom_fields");
  cfSel.addEventListener("change", async () => {
    clear(cfVal); cfVal.hidden = !cfSel.value;
    if (!cfSel.value) return;
    cfVal.appendChild(h("option", { value: "" }, "has any value"));
    try { (await api(`api/custom-fields/${cfSel.value}/values`)).forEach((v) => cfVal.appendChild(h("option", { value: v.value }, `${v.value} (${v.count})`))); } catch (e) { /* ignore */ }
  });
  const bell = h("select", { "aria-label": "Reminders", hidden: !feat("reminders") }, h("option", { value: "" }, "Reminders: any"), h("option", { value: "on" }, "🔔 Reminders on"), h("option", { value: "off" }, "🔕 Reminders off"));
  const sort = h("select", { "aria-label": "Sort" }, h("option", { value: "name" }, "Sort: surname"), h("option", { value: "birth" }, "Sort: birth date"), h("option", { value: "changed" }, "Sort: recently changed"));
  const list = h("div", { class: "card", style: "padding:6px 14px" });
  const countEl = h("span", { class: "hint" });
  let page = 1;
  try {
    const names = await api("api/surnames");
    names.forEach((s) => surname.appendChild(h("option", { value: s.surname }, `${s.surname} (${s.count})`)));
  } catch (e) { /* optional */ }
  const load = async (append) => {
    const params = new URLSearchParams({ page: String(page), page_size: "60", sort: sort.value });
    if (q.value.trim()) params.set("q", q.value.trim());
    if (living.value) params.set("living", living.value);
    if (surname.value) params.set("surname", surname.value);
    if (photo.value) params.set("has_photo", photo.value);
    if (bell.value) params.set("remind", bell.value);
    if (sideSel && sideSel.value) params.set("side", sideSel.value);
    if (cfSel.value) { params.set("field", cfSel.value); if (cfVal.value) params.set("value", cfVal.value); }
    const r = await api(`api/people?${params}`);
    if (!append) clear(list);
    const old = list.querySelector(".more-btn");
    if (old) old.remove();
    if (!r.items.length && !append) list.appendChild(h("div", { class: "empty" }, "Nobody matches."));
    for (const p of r.items) {
      const aka = [p.birthSurname ? `née ${p.birthSurname}` : null, p.nickname ? `“${p.nickname}”` : null].filter(Boolean).join(" · ");
      list.appendChild(h("div", { class: "person-row", role: "button", tabindex: "0", onclick: () => go(`person/${p.id}`),
        onkeydown: (e) => { if (e.key === "Enter") go(`person/${p.id}`); } },
        avatar(p), h("div", { class: "grow" }, h("div", { class: "nm" }, shownName(p), p.living ? "" : " 🕯",
          p.remind ? h("span", { class: "bell-mark", title: "Reminders on", "aria-label": "reminders on" }, " 🔔") : null), localLine(p),
          h("div", { class: "sub" }, [p.birthDisplay ? `b. ${p.birthDisplay}` : null, p.deathDisplay ? `d. ${p.deathDisplay}` : null, aka].filter(Boolean).join(" · ") || "No dates")),
        h("button", { type: "button", class: "icon-btn", title: "Show in tree", "aria-label": `Show ${p.name} in the tree`, onclick: (e) => { e.stopPropagation(); go(`tree/${p.id}`); } }, "🌳")));
    }
    countEl.textContent = plural(r.total, "person");
    if (r.page * r.pageSize < r.total) {
      list.appendChild(h("div", { class: "actions more-btn", style: "justify-content:center" },
        h("button", { type: "button", class: "btn-ghost", onclick: () => { page++; load(true).catch(fail); } }, "Show more")));
    }
  };
  const reload = () => { page = 1; load(false).catch(fail); };
  q.addEventListener("input", debounce(reload, 250));
  [living, surname, photo, bell, sideSel, cfSel, cfVal, sort].filter(Boolean).forEach((s) => s.addEventListener("change", reload));
  await load(false);
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "People ", countEl),
      h("div", { class: "person-actions" }, peopleModeSwitch("names"),
        h("button", { type: "button", class: "btn-ghost", onclick: () => quickFamilyModal() }, "+ Family"),
        h("button", { type: "button", class: "btn-primary", onclick: () => newPersonModal() }, "+ Person"))),
    h("div", { class: "toolbar" }, q, living, surname, photo, bell, sideSel, cfSel, cfVal, sort),
    list);
}

function peopleModeSwitch(mode) {
  return segmented([["names", "By name"], ["relatives", "Relatives"]], mode, (k) => go(k === "relatives" ? "people/relatives" : "people")).el;
}

const REL_CHOICES = [["relatives", "Everyone related"], ["blood", "Blood relatives"], ["parents", "Parents"], ["grandparents", "Grandparents"],
  ["ancestors", "Ancestors"], ["siblings", "Brothers and sisters"], ["uncles_aunts", "Uncles and aunts"], ["first_cousins", "First cousins"],
  ["cousins", "All cousins"], ["nieces_nephews", "Nieces and nephews"], ["children", "Children"], ["grandchildren", "Grandchildren"],
  ["descendants", "Descendants"], ["partners", "Partners"], ["in_laws", "In-laws"]];

/* Relationship search (§13.17): [Relationship] of [Person] with filters; typing a phrase fills the picker. */
async function viewRelatives() {
  const saved = (() => { try { return JSON.parse(sessionStorageGet("relSearch") || "null"); } catch (e) { return null; } })() || {};
  const phrase = h("input", { type: "search", placeholder: feat("kin_names") ? "Try: first cousins of me · all my Babais · descendants of Venkat · everyone on Amma's side"
    : "Try: first cousins of me · all my uncles · descendants of Grandpa · everyone on my mother's side",
    "aria-label": "Describe who to find", value: saved.phrase || "" });
  const understood = h("div", { class: "hint", role: "status" });
  const rel = h("select", { "aria-label": "Relationship" }, ...REL_CHOICES.map(([k, l]) => h("option", { value: k }, l)));
  rel.value = saved.rel || "first_cousins";
  const termBox = h("span");
  let term = saved.term || null;
  const of = personPicker({ placeholder: "Whose? (you if empty)", onChange: () => run() });
  if (saved.anchor) of.set(saved.anchor);
  const side = h("select", { "aria-label": "Side", hidden: !feat("sides") }, h("option", { value: "" }, "Both sides"), h("option", { value: "paternal" }, "Father's side"), h("option", { value: "maternal" }, "Mother's side"));
  side.value = feat("sides") ? saved.side || "" : "";
  const living = h("select", { "aria-label": "Living" }, h("option", { value: "" }, "Living and deceased"), h("option", { value: "living" }, "Living"), h("option", { value: "deceased" }, "Deceased"));
  living.value = saved.living || "";
  const gender = h("select", { "aria-label": "Gender" }, h("option", { value: "" }, "Any gender"), h("option", { value: "male" }, "Men"), h("option", { value: "female" }, "Women"));
  gender.value = saved.gender || "";
  const gen = h("select", { "aria-label": "Generation" }, h("option", { value: "" }, "Any generation"),
    ...[[-2, "Grandparents' generation"], [-1, "Parents' generation"], [0, "Same generation"], [1, "Children's generation"], [2, "Grandchildren's generation"]]
      .map(([v, l]) => h("option", { value: String(v) }, l)));
  gen.value = saved.gen ?? "";
  const list = h("div", { class: "card", style: "padding:6px 14px" });
  const countEl = h("span", { class: "hint" });
  let lastIds = [];
  const exportBtn = h("button", { type: "button", class: "btn-secondary", disabled: true, onclick: () => {
    let draft = null;
    try { draft = JSON.parse(sessionStorageGet("exportDraft") || "null"); } catch (e) { draft = null; }
    draft = draft || {};
    draft.scope = { type: "selected", ids: lastIds.slice() };
    sessionStorageSet("exportDraft", JSON.stringify(draft));
    go("export");
  } }, "📤 Export these people");
  const drawTerm = () => mount(termBox, term ? h("span", { class: "chip big" }, `“${term}”`,
    h("button", { type: "button", class: "icon-btn", "aria-label": "Clear word", onclick: () => { term = null; drawTerm(); run(); } }, "✕")) : null);
  async function run() {
    const anchor = of.get();
    sessionStorageSet("relSearch", JSON.stringify({ phrase: phrase.value, rel: rel.value, term, side: side.value, living: living.value,
      gender: gender.value, gen: gen.value, anchor }));
    const params = new URLSearchParams({ rel: rel.value });
    if (anchor) params.set("anchor", anchor.id);
    if (term) params.set("term", term);
    for (const [k, el] of [["side", side], ["living", living], ["gender", gender], ["generation", gen]]) if (el.value !== "") params.set(k, el.value);
    if (!anchor && !state.user.mePersonId) {
      mount(list, h("div", { class: "empty" }, "Choose whose relatives to find — or set “This is me” in Settings."));
      countEl.textContent = ""; exportBtn.disabled = true; return;
    }
    try {
      const r = await api(`api/people/related?${params}`);
      clear(list);
      lastIds = r.items.map((p) => p.id);
      exportBtn.disabled = !lastIds.length;
      countEl.textContent = plural(r.total, "person");
      if (!r.items.length) list.appendChild(h("div", { class: "empty" }, "Nobody matches."));
      const whose = r.anchor.id === state.user.mePersonId ? "your" : `${r.anchor.name}'s`;
      for (const p of r.items) {
        list.appendChild(h("div", { class: "person-row", role: "button", tabindex: "0", onclick: () => go(`person/${p.id}`),
          onkeydown: (e) => { if (e.key === "Enter") go(`person/${p.id}`); } },
          avatar(p), h("div", { class: "grow" },
            h("div", { class: "nm" }, p.name, p.living ? "" : " 🕯", p.side && feat("sides") ? h("span", { class: `side-chip ${p.side}`, title: `${p.side === "both" ? "Both sides" : p.side === "paternal" ? "Father's side" : "Mother's side"}` }) : null),
            h("div", { class: "sub" }, [`${whose} ${p.relationship}`, p.english && p.english !== p.relationship ? p.english : null, p.years].filter(Boolean).join(" · "))),
          h("button", { type: "button", class: "icon-btn", title: "Show in tree", "aria-label": `Show ${p.name} in the tree`, onclick: (e) => { e.stopPropagation(); go(`tree/${p.id}`); } }, "🌳")));
      }
    } catch (e) { mount(list, h("div", { class: "error-text" }, e.message)); exportBtn.disabled = true; }
  }
  const understand = async () => {
    const q = phrase.value.trim();
    if (!q) return;
    try {
      const r = await api(`api/people/related/parse?q=${encodeURIComponent(q)}`);
      rel.value = r.rel; gender.value = r.gender || ""; side.value = feat("sides") ? r.side || "" : ""; term = r.term; drawTerm();
      if (r.anchor === state.user.mePersonId) of.reset(); else of.set({ id: r.anchor, name: r.anchorName });
      understood.textContent = `Showing ${r.understood}.`;
      run();
    } catch (e) { understood.textContent = e.message; }
  };
  phrase.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); understand(); } });
  [rel, side, living, gender, gen].forEach((el) => el.addEventListener("change", () => { understood.textContent = ""; run(); }));
  drawTerm();
  await run();
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Relatives ", countEl), h("div", { class: "person-actions" }, peopleModeSwitch("relatives"))),
    h("div", { class: "card" },
      h("div", { class: "form-row" }, h("div", { class: "field wide" }, "Find", h("div", { class: "notify-add form-row" }, phrase,
        h("button", { type: "button", class: "btn-primary btn-small", onclick: understand }, "Find")))),
      understood,
      h("div", { class: "rel-picker" }, rel, termBox, h("span", { class: "hint" }, "of"), of.el),
      h("div", { class: "toolbar", style: "margin:8px 0 0" }, side, living, gender, gen)),
    feat("export") ? h("div", { class: "actions", style: "justify-content:flex-start;margin:0 0 8px" }, exportBtn,
      h("span", { class: "hint" }, "Opens Export with exactly these people picked.")) : null,
    list);
}

function newPersonModal() {
  const form = personForm({}, { onUseExisting: (p) => { m.close(); go(`person/${p.id}`); } });
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "submit", class: "btn-primary" }, "Add person");
  const f = h("form", null, form.el, h("p", { class: "hint" }, "Tip: it's usually quicker to add relatives from someone's page or the tree, so they're linked straight away."), err,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), save));
  onFormSubmit(f, form.value, err, save, async (body) => {
    const res = await api("api/people", { method: "POST", body });
    m.close(); saved(res, "Added"); go(`person/${res.person.id}`);
  });
  const m = openModal("Add a person", f);
}

// ---------- person page ----------
async function viewPerson(id, tab) {
  const p = await api(`api/people/${id}`);
  const rel = p.relationshipToMe;
  const dates = [];
  const at = (ev) => ev.time ? ` at ${fmtTime(ev.time)}` : "";
  if (p.birth) dates.push(`Born ${p.birth.dateDisplay || "—"}${at(p.birth)}${p.birth.place ? " in " + p.birth.place : ""}`);
  if (p.death || p.deceased) dates.push(p.death ? `Died ${p.death.dateDisplay || "(date unknown)"}${at(p.death)}${p.death.place ? " in " + p.death.place : ""}` : "Deceased");
  let ageText = null;
  if (p.age !== null && p.age !== undefined) ageText = p.living ? `age ${p.age}` : `aged ${p.age}`;
  else if (p.birth && p.birth.date && p.birth.date.y === null) ageText = "age unknown";
  const aka = [p.birthSurname ? `née ${p.birthSurname}` : null, p.nickname ? `“${p.nickname}”` : null,
    ...(p.otherNames || []).map((n) => `${NAME_TYPES[n.type] || "Also"}: ${n.name}`)].filter(Boolean).join(" · ");
  const mine = state.user.mePersonId === p.id;
  const actions = h("div", { class: "person-actions" },
    h("button", { type: "button", class: "btn-primary btn-small", onclick: () => editPersonModal(p) }, "✎ Edit"),
    h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => go(`tree/${p.id}`) }, "🌳 Tree"),
    feat("reminders") ? bellButton(p) : null,
    !p.claimedBy && !state.user.mePersonId ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => claimMe(p.id) }, "This is me") : null,
    mine ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => claimMe(null) }, "This isn't me") : null,
    h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
      if (!await confirmDialog("Delete person", `Move ${p.name} to the trash? Their relationships are kept. ${restoreHint("them")}`, "Delete", true)) return;
      try { const res = await api(`api/people/${p.id}`, { method: "DELETE" }); saved(res, "Moved to trash"); go("people"); } catch (x) { fail(x); }
    } }, "🗑 Delete"));
  const head = h("div", { class: "card" },
    h("div", { class: "person-head" },
      h("button", { type: "button", class: "avatar-btn", title: "Change photo", "aria-label": "Change photo", onclick: () => photoModal(p) }, avatar(p, "big")),
      h("div", { class: "info" },
        h("h2", null, shownName(p), p.living ? "" : " 🕯"),
        state.user.nameDisplay === "both" && p.nameLocal ? h("div", { class: "name-local big" }, p.nameLocal)
          : state.user.nameDisplay === "script" && p.nameLocal ? h("div", { class: "name-local big" }, p.name) : null,
        aka ? h("div", { class: "aka" }, aka) : null,
        h("div", { class: "dates" }, [...dates, ageText].filter(Boolean).join(" · ") || h("span", { class: "hint" }, "No dates yet")),
        h("div", { class: "relbox" },
          mine ? h("span", { class: "chip accent" }, "This is you") : null,
          p.neverExport ? h("span", { class: "chip warn", title: "Never included in exports" }, "🚫 Kept out of exports") : null,
          p.claimedBy && !mine ? h("span", { class: "chip" }, `${p.claimedBy.name} in Home Assistant`) : null,
          citeButton(p, { personId: p.id }, p.name),
          rel && rel.kind !== "self" ? h("span", { class: "rel-label" }, rel.kind === "none" ? "Not related to you" : `Your ${rel.label}`) : null,
          rel && rel.term ? h("span", { class: "rel-meaning", title: rel.note || "" }, `— your ${rel.meaning}`) : null,
          rel && rel.ageUnknown && rel.kinKey && state.user.kinLangEffective !== "en"
            ? h("span", { class: "hint" }, "Add a birth date or the birth order to tell elder from younger.") : null,
          rel && rel.term && /(^|\.)[PXCE]/.test(rel.kinKey || "")
            ? h("span", { class: "hint" }, "Someone on the way has no gender set — add it (Edit) to show one word instead of two.") : null,
          rel && rel.path && rel.path.length > 1 ? h("button", { type: "button", class: "link-btn", onclick: () => pathModal(p, rel) }, "How are we related?") : null,
          h("button", { type: "button", class: "link-btn", onclick: () => go(`relate/${p.id}`) }, "🔗 Related to someone else?"),
          !state.user.mePersonId ? h("span", { class: "hint" }, "Set “This is me” to see how everyone is related to you.") : null)),
      actions),
    p.warnings.length ? h("div", { class: "warnings" }, ...p.warnings.map((w) => h("div", null, "⚠ " + w))) : null);
  const nMedia = (p.photoCount || 0) + (p.documentCount || 0);
  const tabs = h("div", { class: "tabs", role: "tablist" }, ...[["overview", "Overview"], ["timeline", "Timeline"],
    ["photos", "Photos & documents" + (nMedia ? ` (${nMedia})` : "")], ["stories", "Stories" + (p.storyCount ? ` (${p.storyCount})` : "")], ["history", "History"]].map(([k, l]) =>
    h("button", { type: "button", role: "tab", class: k === tab ? "active" : "", "aria-selected": String(k === tab), onclick: () => go(`person/${p.id}/${k}`) }, l)));
  let body;
  if (tab === "timeline") body = await personTimeline(p);
  else if (tab === "photos") body = await personPhotos(p);
  else if (tab === "stories") body = await personStories(p);
  else if (tab === "history") body = await historyList({ personId: p.id });
  else body = personOverview(p);
  return h("div", null, head, tabs, body);
}

/* The person's 🔔 switch (§9): shared by the household, saved at once, undoable. */
function bellButton(p) {
  const on = !!p.remind;
  return h("button", { type: "button", class: "btn-ghost btn-small bell-btn" + (on ? " on" : ""), "aria-pressed": String(on),
    title: on ? `Birthdays and other days for ${p.name} go into the reminders of everyone who has reminders on. Tap to turn off.`
      : `Nobody is reminded about ${p.name}. Tap to include them in reminders.`,
    onclick: async () => {
      try {
        const res = await api(`api/people/${p.id}`, { method: "PATCH", body: { remind: !on } });
        saved(res, !on ? `Reminders on for ${p.name}` : `Reminders off for ${p.name}`);
        rerender();
      } catch (x) { fail(x); }
    } }, on ? "🔔 Reminders on" : "🔕 Reminders off");
}

async function claimMe(pid) {
  try {
    await api("api/me/person", { method: "PUT", body: { personId: pid } });
    await loadMe();
    toast(pid ? "Linked to you" : "Unlinked");
    rerender();
  } catch (e) { fail(e); }
}

function pathModal(p, rel) {
  const steps = [];
  rel.path.forEach((s, i) => {
    if (i > 0) steps.push(h("div", { class: "arrow" }, `↓ ${s.rel}`));
    steps.push(h("div", { class: "step" }, h("button", { type: "button", class: "link-btn", onclick: () => { m.close(); go(`person/${s.id}`); } }, i === 0 ? `${s.name} (you)` : s.name)));
  });
  const m = openModal(`How ${p.name} is related to you`, h("div", null,
    h("p", null, h("strong", null, `${p.name} is your ${rel.label}.`)), h("div", { class: "path" }, ...steps),
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-primary", onclick: () => m.close() }, "Close"))));
}

// ---------- how two people are related (any two, not just "me") ----------
async function viewRelate(aId, bId) {
  const load = async (id) => { if (!id) return null; try { return await api(`api/people/${id}`); } catch (e) { return null; } };
  let a = await load(aId), b = await load(bId || (aId !== state.user.mePersonId ? state.user.mePersonId : null));
  const result = h("div");
  const show = async () => {
    const path = `#/relate/${a ? a.id : ""}${b ? "/" + b.id : ""}`;
    if (location.hash !== path) history.replaceState(history.state, "", path);
    if (!a || !b) { mount(result, h("p", { class: "hint" }, "Choose two people.")); return; }
    if (a.id === b.id) { mount(result, h("div", { class: "card" }, h("p", null, "That's the same person twice."))); return; }
    mount(result, spinner());
    try {
      const [ab, ba] = await Promise.all([
        api(`api/relationship?from=${encodeURIComponent(a.id)}&to=${encodeURIComponent(b.id)}`),
        api(`api/relationship?from=${encodeURIComponent(b.id)}&to=${encodeURIComponent(a.id)}`)]);
      mount(result, relateCard(a, b, ab, ba));
    } catch (e) { mount(result, h("div", { class: "card fatal" }, e.message)); }
  };
  const pa = personPicker({ initial: a, placeholder: "First person…", onChange: (p) => { a = p; show(); } });
  const pb = personPicker({ initial: b, placeholder: "Second person…", onChange: (p) => { b = p; show(); } });
  const swap = h("button", { type: "button", class: "btn-ghost btn-small", title: "Swap", "aria-label": "Swap the two people",
    onclick: () => { [a, b] = [b, a]; pa.set(a); pb.set(b); show(); } }, "⇅ Swap");
  show();
  return h("div", null, h("h2", { class: "page-title" }, "🔗 How are they related?"),
    h("div", { class: "card relate-pick" },
      h("div", { class: "field" }, h("label", null, "Person"), pa.el),
      h("div", { class: "relate-swap" }, swap),
      h("div", { class: "field" }, h("label", null, "and"), pb.el)),
    result);
}
function relateCard(a, b, ab, ba) {
  const nameA = a.name, nameB = b.name;
  if (ab.kind === "none") {
    return h("div", { class: "card" }, h("p", null, h("strong", null, `${nameB} and ${nameA} aren't related in the tree.`)),
      h("p", { class: "hint" }, "No chain of parents, children or marriages links them yet."));
  }
  const line = (who, whose, r) => h("p", { class: "relate-line" }, h("strong", null, `${who} is ${whose}'s ${r.label}`),
    r.term && r.meaning ? h("span", { class: "rel-meaning", title: r.note || "" }, ` — ${whose}'s ${r.meaning}`) : null);
  const steps = [];
  (ab.path || []).forEach((s, i) => {
    if (i > 0) steps.push(h("div", { class: "arrow" }, `↓ ${s.rel}`));
    steps.push(h("div", { class: "step" }, h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${s.id}`) }, s.name)));
  });
  const ageHint = (ab.ageUnknown || ba.ageUnknown) && ab.kinKey && state.user.kinLangEffective !== "en"
    ? h("p", { class: "hint" }, "Add a birth date or the birth order to tell elder from younger.") : null;
  return h("div", { class: "card" }, line(nameB, nameA, ab), line(nameA, nameB, ba), ageHint,
    steps.length > 1 ? h("div", null, h("h4", null, "The link"), h("div", { class: "path" }, ...steps)) : null);
}

function editPersonModal(p) {
  const form = personForm(p, {});
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "submit", class: "btn-primary" }, "Save");
  const f = h("form", null, form.el, err, h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), save));
  onFormSubmit(f, form.value, err, save, async (body) => {
    const res = await api(`api/people/${p.id}`, { method: "PATCH", body });
    m.close(); saved(res); rerender();
  });
  const m = openModal(`Edit ${p.name}`, f, { wide: true });
}

function eventRow(ev, onEdit, extra) {
  const label = evLabel(ev.type);
  return h("div", { class: "event-row" },
    h("div", { class: "when" }, ev.dateDisplay || "—", ev.time ? h("div", { class: "hint" }, fmtTime(ev.time)) : null),
    h("div", { class: "what" }, h("b", null, ev.type === "custom" && ev.title ? ev.title : label),
      ev.type !== "custom" && ev.title ? ` — ${ev.title}` : "",
      ev.place ? h("div", { class: "where" }, "📍 " + ev.place) : null,
      ev.description ? h("div", { class: "hint", style: "white-space:pre-wrap" }, ev.description) : null),
    extra || null,
    onEdit ? h("button", { type: "button", class: "icon-btn", "aria-label": `Edit ${label}`, onclick: onEdit }, "✎") : null);
}

function personOverview(p) {
  const anchor = { id: p.id, name: p.name, surname: p.surname };
  const events = h("div", { class: "card" }, h("h3", null, "Life events",
    h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => eventModal({ personId: p.id }) }, "+ Add event")),
    p.events.length ? h("div", null, ...p.events.map((ev) => eventRow(ev, () => eventModal({ personId: p.id }, ev), citeButton(p, { eventId: ev.id }, evLabel(ev.type).toLowerCase()))))
      : h("div", { class: "hint" }, "No events yet. Births, deaths, schooling, jobs, homes, moves…"));

  // parents
  const parentBlocks = [];
  for (const pf of p.parentFamilies) {
    const cards = pf.partners.map((x) => personCard(x, { chip: pf.relation !== "birth" ? RELATION_LABELS[pf.relation] : null }));
    if (pf.openSlot) cards.push(ghostCard("+ Add parent", () => addRelativeModal("parent", anchor, { familyId: pf.id })));
    parentBlocks.push(h("div", { class: "person-grid", style: "margin-bottom:8px" }, ...cards));
  }
  if (!p.parentFamilies.length) {
    parentBlocks.push(h("div", { class: "person-grid" },
      ghostCard("+ Add father", () => addRelativeModal("father", anchor)),
      ghostCard("+ Add mother", () => addRelativeModal("mother", anchor))));
    parentBlocks.push(h("div", { style: "margin-top:8px" }, h("button", { type: "button", class: "link-btn", onclick: () => quickFamilyModal({ child: p, title: `Add ${p.name}'s parents and siblings` }) }, "Add parents and siblings at once…")));
  } else {
    parentBlocks.push(h("button", { type: "button", class: "link-btn", style: "font-size:0.84rem", onclick: () => addRelativeModal("parent", anchor) }, "+ Add an adoptive, step or other parent"));
  }
  const parents = h("div", { class: "card" }, h("h3", null, "Parents"), ...parentBlocks);

  // partners & children
  const famBlocks = p.families.map((f) => {
    const md = f.marriage && f.marriage.dateDisplay;
    const meta = [md ? `${f.kind === "partners" ? "Together since" : "Married"} ${md}` : KIND_LABELS[f.kind],
      f.marriage && f.marriage.place ? f.marriage.place : null, f.ended ? ENDED_LABELS[f.ended] : null].filter(Boolean).join(" · ");
    return h("div", { class: "family-block" },
      h("div", { class: "fhead" },
        f.partner ? h("div", { style: "min-width:220px;flex:1" }, personCard(f.partner))
          : h("div", { style: "flex:1" }, h("span", { class: "hint" }, "Partner unknown "), h("button", { type: "button", class: "link-btn", onclick: () => addRelativeModal("partner", anchor, { familyId: f.id }) }, "+ Add")),
        h("div", { class: "fmeta" }, meta),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => familyModal(f.id) }, "Edit family")),
      h("div", { class: "person-grid" },
        ...f.children.map((c) => personCard(c, { chip: c.relation !== "birth" ? RELATION_LABELS[c.relation] : null })),
        ghostCard("+ Add child", () => addRelativeModal("child", anchor, { familyId: f.id }))));
  });
  const families = h("div", { class: "card" }, h("h3", null, "Partners & children",
    h("span", { class: "person-actions" },
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => addRelativeModal("partner", anchor) }, "+ Add partner"),
      !p.families.length ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => addRelativeModal("child", anchor) }, "+ Add child") : null)),
    famBlocks.length ? famBlocks : h("div", { class: "hint" }, "No partners or children recorded."));

  const siblings = p.siblings.length ? h("div", { class: "card" }, h("h3", null, "Siblings"),
    h("div", { class: "person-grid" }, ...p.siblings.map((s) => personCard(s, { chip: s.full ? null : "half" })))) : null;
  const bio = h("div", { class: "card" }, h("h3", null, "Biography", h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => editPersonModal(p) }, "✎ Edit")),
    p.biography ? h("div", { class: "bio" }, p.biography) : h("div", { class: "hint" }, "No biography yet."));
  const custom = (p.custom || []);
  const details = custom.length ? h("div", { class: "card" }, h("h3", null, "Details",
    h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => customModal(`api/people/${p.id}/custom`, custom, `Details of ${p.name}`) }, "✎ Edit")),
    custom.some((c) => c.value) ? h("div", { class: "kv" }, ...custom.filter((c) => c.value).map((c) => h("div", { class: "kv-row" },
      h("div", { class: "kv-label" }, c.label), h("div", { class: "kv-value" }, c.display,
        citeButton(p, { personId: p.id }, c.label))))) : h("div", { class: "hint" }, "Nothing filled in yet.")) : null;
  const contact = p.living && feat("contacts") ? contactsCard(p) : null;
  return h("div", null, details, events, feat("tithi") ? tithiCard(p) : null, contact, parents, families, siblings, bio);
}

// ---------- tithi (§13.12) ----------
const MASAS = ["Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada", "Ashwayuja", "Kartika", "Margashira", "Pushya", "Magha", "Phalguna"];
const TITHI_NAMES = ["Padyami", "Vidiya", "Tadiya", "Chavithi", "Panchami", "Shashti", "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi", "Dwadashi", "Trayodashi", "Chaturdashi"];
function tithiName(paksha, t) { return t === 15 ? (paksha === "shukla" ? "Pournami" : "Amavasya") : TITHI_NAMES[t - 1]; }
function fmtDay(iso) { return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" }); }
function tithiCard(p) {
  const evs = { birth: p.events.find((e) => e.type === "birth"), death: p.events.find((e) => e.type === "death") };
  const t = p.tithi || {};
  const rows = [];
  for (const kind of ["death", "birth"]) {
    const ev = evs[kind];
    if (!ev || (kind === "birth" && !p.living)) continue;
    const what = kind === "death" ? "🪔 Tithi (shraddha)" : "🪔 Janma tithi";
    const cur = t[kind];
    rows.push(h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, what),
      h("div", { class: "kv-value" },
        cur ? h("span", null, h("strong", null, cur.label), cur.labelEn !== cur.label ? h("span", { class: "hint" }, ` (${cur.labelEn})`) : null, cur.next ? h("span", { class: "hint" }, ` · next ${fmtDay(cur.next)}${cur.nextOverridden ? " (corrected)" : ""}`) : null)
          : h("span", { class: "hint" }, "Not set"),
        " ", h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => tithiModal(p, ev, cur, kind) }, cur ? "✎ Edit" : "+ Set"))));
  }
  if (!rows.length) return null;
  return h("div", { class: "card" }, h("h3", null, "Tithi"), h("div", { class: "kv" }, ...rows),
    h("div", { class: "hint" }, "Dates follow the Telugu (amanta) calendar for Home Assistant's home location. ",
      h("button", { type: "button", class: "link-btn", onclick: () => go("tithis") }, "All tithi dates this year")));
}
function tithiModal(p, ev, cur, kind) {
  const masa = h("select", { "aria-label": "Month" }, ...MASAS.map((m, i) => h("option", { value: String(i + 1) }, m)));
  const paksha = h("select", { "aria-label": "Fortnight" }, h("option", { value: "shukla" }, "Shukla (waxing)"), h("option", { value: "krishna" }, "Bahula / Krishna (waning)"));
  const tithi = h("select", { "aria-label": "Tithi" });
  const fillTithi = () => { const v = tithi.value; mount(tithi, ...Array.from({ length: 15 }, (_, i) => h("option", { value: String(i + 1) }, `${i + 1}. ${tithiName(paksha.value, i + 1)}`))); if (v) tithi.value = v; };
  paksha.addEventListener("change", fillTithi);
  if (cur) { masa.value = String(cur.masa); paksha.value = cur.paksha; }
  fillTithi();
  if (cur) tithi.value = String(cur.tithi);
  let source = "entered";
  const err = h("div", { class: "error-text", role: "alert" });
  const optsBox = h("div", null);
  const hasDate = ev.date && ev.date.d && ev.date.m && ev.date.y;
  const workOut = h("button", { type: "button", class: "btn-secondary", disabled: !hasDate, onclick: async () => {
    err.textContent = "";
    try {
      const r = await api(`api/events/${ev.id}/tithi/options`);
      const pick = (o) => { masa.value = String(o.masa); paksha.value = o.paksha; fillTithi(); tithi.value = String(o.tithi); source = "computed"; };
      pick(r.options[0]);
      mount(optsBox, r.options.length > 1
        ? h("div", { class: "notice" }, "The tithi changed that day. Which one?",
          ...r.options.map((o, i) => { const rb = h("input", { type: "radio", name: "topt", checked: i === 0 }); rb.addEventListener("change", () => pick(o));
            return h("label", { class: "check-row" }, rb, `${o.label}${o.adhika ? " (adhika month)" : ""} — until ${o.until}${o.untilDate !== ev.date.y + "-" + String(ev.date.m).padStart(2, "0") + "-" + String(ev.date.d).padStart(2, "0") ? " next day" : ""}`); }),
          h("div", { class: "hint" }, "Before sunrise counts as the day before; if they died late at night, ask the family which one they keep."))
        : h("div", { class: "hint" }, `${r.atTime ? `At ${fmtTime(r.atTime)} on ${ev.dateDisplay}` : `On ${ev.dateDisplay || "that day"}`}: ${r.options[0].label}${r.options[0].adhika ? " (adhika month)" : ""}.`),
        !r.locationKnown ? h("div", { class: "hint" }, "Home Assistant's location isn't known; using Hyderabad.") : null);
    } catch (x) { err.textContent = x.message; }
  } }, "Work it out from the date");
  for (const el of [masa, paksha, tithi]) el.addEventListener("change", () => { source = "entered"; });
  const overrides = h("div", null);
  const drawOverrides = (c) => {
    if (!c) { clear(overrides); return; }
    const year = c.next ? +c.next.slice(0, 4) : new Date().getFullYear();
    const d = h("input", { type: "date", value: c.next || "", "aria-label": "Corrected date" });
    mount(overrides, h("hr", { class: "sep" }), h("div", { class: "field wide" }, `Next: ${c.next ? fmtDay(c.next) : "—"}${c.nextOverridden ? " (corrected by hand)" : " (worked out)"}`),
      h("div", { class: "form-row" }, field(`Correct the ${year} date (e.g. what your priest says)`, d),
        h("button", { type: "button", class: "btn-secondary", onclick: async () => {
          try { const r = await api(`api/tithi/${ev.id}/${year}`, { method: "PUT", body: { date: d.value } }); saved(r, "Date corrected"); drawOverrides(r.tithi); }
          catch (x) { fail(x); } } }, "Save date")),
      ...c.overrides.map((o) => h("div", { class: "row" }, h("span", null, `${o.year}: ${fmtDay(o.date)}`),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
          try { const r = await api(`api/tithi/${ev.id}/${o.year}`, { method: "DELETE" }); saved(r, "Correction removed"); drawOverrides(r.tithi); }
          catch (x) { fail(x); } } }, "Use the worked-out date"))));
  };
  drawOverrides(cur);
  const m = openModal(`${kind === "death" ? "Tithi" : "Janma tithi"} of ${p.name}`, h("div", null,
    h("p", { class: "hint" }, kind === "death" ? "The lunar day the shraddha is kept on each year." : "The lunar birthday."),
    h("div", { class: "actions", style: "justify-content:flex-start" }, workOut, !hasDate ? h("span", { class: "hint" }, "Needs the full date.") : null),
    optsBox,
    h("div", { class: "form-row" }, field("Month (masa)", masa), field("Paksha", paksha), field("Tithi", tithi)),
    err,
    h("div", { class: "actions" },
      cur ? h("button", { type: "button", class: "btn-ghost danger", onclick: async () => {
        try { const r = await api(`api/events/${ev.id}/tithi`, { method: "DELETE" }); saved(r, "Tithi removed"); m.close(); rerender(); } catch (x) { fail(x); } } }, "Remove") : null,
      h("button", { type: "button", class: "btn-primary", onclick: async () => {
        err.textContent = "";
        try {
          const r = await api(`api/events/${ev.id}/tithi`, { method: "PUT", body: { masa: +masa.value, paksha: paksha.value, tithi: +tithi.value, source } });
          saved(r, "Tithi saved"); drawOverrides(r.tithi); cur = r.tithi; rerender();
        } catch (x) { err.textContent = x.message; }
      } }, "Save tithi")),
    overrides));
}
async function viewTithis(yearArg) {
  const year = +yearArg || new Date().getFullYear();
  const r = await api(`api/tithi/dates?year=${year}`);
  const nav = h("div", { class: "toolbar", style: "margin:0" },
    h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => go(`tithis/${year - 1}`) }, `← ${year - 1}`),
    h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => go(`tithis/${year + 1}`) }, `${year + 1} →`),
    h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => window.print() }, "🖨 Print"));
  return h("div", null, h("div", { class: "page-head" }, h("h2", null, `Tithi dates ${year}`), nav),
    h("p", { class: "hint" }, `Worked out for ${r.location.known ? "Home Assistant's home location" : "Hyderabad (Home Assistant's location isn't known)"}, ${r.timezone}, by the ${r.rule === "sunrise" ? "sunrise" : "aparahna (afternoon)"} rule. Corrected dates are marked ✎.`),
    h("div", { class: "card" }, r.items.length ? h("table", { class: "table" },
      h("thead", null, h("tr", null, h("th", null, "Date"), h("th", null, "Person"), h("th", null, "Tithi"))),
      h("tbody", null, ...r.items.map((it) => h("tr", null,
        h("td", null, it.date ? fmtDay(it.date) : "—", it.overridden ? " ✎" : ""),
        h("td", null, h("a", { href: `#/person/${it.person.id}` }, it.person.name), it.type === "birth" ? h("span", { class: "hint" }, " · janma tithi") : null),
        h("td", null, it.label)))))
      : h("div", { class: "empty" }, "No tithis kept yet. Open a person who has died and set their tithi under Tithi.")));
}

async function personTimeline(p) {
  const r = await api(`api/people/${p.id}/timeline`);
  const mini = feat("map") ? personMiniMap(p) : null;
  if (!r.items.length) return h("div", null, mini, h("div", { class: "card" }, h("div", { class: "empty" }, "Nothing dated yet. Add dates to events to build a timeline.")));
  const words = { "relative-birth": "Birth of", "relative-death": "Death of" };
  return h("div", null, mini, h("div", { class: "card" }, ...r.items.map((it) => it.own ? eventRow(it, null)
    : h("div", { class: "event-row rel" }, h("div", { class: "when" }, it.dateDisplay),
      h("div", { class: "what" }, `${words[it.type]} ${it.relation ? "their " + it.relation + " " : ""}`,
        h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${it.personId}`) }, it.name),
        it.place ? h("div", { class: "where" }, "📍 " + it.place) : null)))));
}

// ---------- places map (§13.3) ----------
const MAP_KINDS = { birth: ["Births", "#3fa34d", "🎂 Born"], marriage: ["Marriages", "#d9467a", "💍 Married"],
  residence: ["Where they lived", "#3b82f6", "🏠 Lived"], death: ["Deaths", "#8f8f8f", "🕯 Died"], other: ["Other events", "#c9a227", "📍"] };
let leafletReady = null, sanscriptReady = null;
function loadSanscript() {
  if (window.Sanscript) return Promise.resolve(window.Sanscript);
  if (!sanscriptReady) sanscriptReady = new Promise((resolve, reject) => {
    document.head.appendChild(h("script", { src: "vendor/sanscript/sanscript.js?v=1.3.3",
      onload: () => resolve(window.Sanscript), onerror: () => { sanscriptReady = null; reject(new Error("Couldn't load the transliteration helper.")); } }));
  });
  return sanscriptReady;
}
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (!leafletReady) {
    leafletReady = new Promise((resolve, reject) => {
      document.head.appendChild(h("link", { rel: "stylesheet", href: "vendor/leaflet/leaflet.css?v=1.9.4" }));
      document.head.appendChild(h("script", { src: "vendor/leaflet/leaflet.js?v=1.9.4",
        onload: () => resolve(window.L), onerror: () => { leafletReady = null; reject(new Error("Couldn't load the map.")); } }));
    });
  }
  return leafletReady;
}
function eventLine(e) {
  const who = e.people.map((x) => x.name).join(" & ");
  const what = e.kind === "other" ? (e.title || evLabel(e.type)) : MAP_KINDS[e.kind][2];
  return `${what}${e.kind === "other" ? " — " : " "}${who}${e.dateDisplay ? ", " + e.dateDisplay : ""}`;
}
function hueOf(id) { let n = 0; for (const c of id) n = (n * 31 + c.charCodeAt(0)) % 360; return n; }

/* Draws places on a Leaflet map. Returns { update(points, opts) }. */
function placesLayer(L, map, handlers) {
  if (!map.getPane("famLines")) map.createPane("famLines").style.zIndex = 390;   // lines under the pins
  const markers = L.layerGroup().addTo(map);
  const lines = L.layerGroup().addTo(map);
  return {
    update(points, opts = {}) {
      markers.clearLayers(); lines.clearLayers();
      const byPlace = new Map();
      for (const e of points) {
        if (!byPlace.has(e.placeKey)) byPlace.set(e.placeKey, []);
        byPlace.get(e.placeKey).push(e);
      }
      for (const [, evs] of byPlace) {
        const kinds = new Set(evs.map((e) => e.kind));
        const colour = kinds.size === 1 ? MAP_KINDS[evs[0].kind][1] : "#b07cd8";
        const mk = L.circleMarker([evs[0].lat, evs[0].lon], { radius: Math.min(6 + evs.length, 14), color: "#222", weight: 1, fillColor: colour, fillOpacity: 0.9 });
        const box = h("div", { class: "map-pop" }, h("strong", null, evs[0].place),
          h("ul", null, ...evs.slice(0, 30).map((e) => h("li", null, h("button", { type: "button", class: "link-btn", onclick: () => go(`person/${e.people[0].id}`) }, eventLine(e))))),
          evs.length > 30 ? h("div", { class: "hint" }, `…and ${evs.length - 30} more`) : null,
          handlers.onMove ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { map.closePopup(); handlers.onMove(evs[0].place); } },
            evs[0].source === "manual" ? "Move pin again" : "Pin is in the wrong place? Move it") : null);
        mk.bindPopup(box, { maxWidth: 320 });
        mk.bindTooltip(`${evs[0].place} (${evs.length})`);
        mk.addTo(markers);
      }
      if (opts.lines) {
        const byPerson = new Map();
        for (const e of points) for (const p of e.people) {
          if (!byPerson.has(p.id)) byPerson.set(p.id, []);
          byPerson.get(p.id).push(e);
        }
        for (const [pid, evs] of byPerson) {
          const pts = [];
          for (const e of evs.filter((x) => x.sortKey).sort((a, b) => a.sortKey.localeCompare(b.sortKey))) {
            const last = pts[pts.length - 1];
            if (!last || last[0] !== e.lat || last[1] !== e.lon) pts.push([e.lat, e.lon]);
          }
          if (pts.length > 1) L.polyline(pts, { pane: "famLines", color: `hsl(${hueOf(pid)} 70% 55%)`, weight: 2.5, opacity: 0.8, dashArray: "6 5" })
            .bindTooltip(evs[0].people.find((x) => x.id === pid).name).addTo(lines);
        }
      }
      if (opts.fit && points.length) {
        const b = L.latLngBounds(points.map((e) => [e.lat, e.lon]));
        map.fitBounds(b.pad, { maxZoom: 9 });
      }
    },
  };
}
function tileLayer(L, st) {
  return L.tileLayer(st.tilesUrl, { maxZoom: 18, attribution: st.attribution });
}

async function viewMap(focusPerson) {
  const st = await api("api/map/status");
  if (!st.enabled) {
    return h("div", null, h("h2", { class: "page-title" }, "Map"), h("div", { class: "card" }, h("h3", null, "The places map is off"),
      h("p", { class: "hint" }, "It shows where the family was born, married, lived and died. It needs internet: place names are looked up on OpenStreetMap and map pictures are downloaded. ",
        state.me.isAdmin ? h("button", { type: "button", class: "link-btn", onclick: () => go("admin/settings") }, "Turn it on in Admin → App settings.") : "An admin can turn it on.")));
  }
  const L = await loadLeaflet();
  const saved = (() => { try { return JSON.parse(lsGet("mapView") || "null"); } catch (e) { return null; } })() || {};
  let filter = focusPerson ? "person" : (saved.filter || "all");
  const who = personPicker({ placeholder: "Whose? (you if empty)", onChange: () => load() });
  if (focusPerson) { try { who.set(await api(`api/people/${focusPerson}`)); } catch (e) { /* gone */ } }
  const kinds = {};
  const kindBoxes = Object.entries(MAP_KINDS).map(([k, [label, colour]]) => {
    kinds[k] = h("input", { type: "checkbox", checked: !(saved.hidden || []).includes(k) });
    kinds[k].addEventListener("change", () => { remember(); draw(false); });
    return h("label", { class: "check-row" }, kinds[k], h("span", { class: "map-dot", style: `background:${colour}` }), label);
  });
  const linesBox = h("input", { type: "checkbox", checked: saved.lines !== false });
  linesBox.addEventListener("change", () => { remember(); draw(false); });
  const slider = h("input", { type: "range", min: 0, max: 0, value: 0, "aria-label": "Show events up to year" });
  const yearLabel = h("strong", { class: "map-year" });
  const play = h("button", { type: "button", class: "btn-secondary btn-small" }, "▶ Play");
  const mapEl = h("div", { class: "map-big" });
  const info = h("div", { class: "hint", role: "status" });
  const unloc = h("div");
  const filterSeg = segmented([["all", "Everyone"], ["ancestors", "Ancestors"], ["descendants", "Descendants"], ["person", "One person"]], filter,
    (k) => { filter = k; remember(); load(); });
  const remember = () => lsSet("mapView", JSON.stringify({ filter, lines: linesBox.checked, hidden: Object.keys(kinds).filter((k) => !kinds[k].checked) }));
  let data = { points: [], unlocated: [] }, map = null, layer = null, pinFor = null, timer = null;
  const years = () => data.points.map((e) => e.year).filter((y) => y);
  function draw(fit) {
    const upTo = Number(slider.value), max = Number(slider.max);
    const shown = data.points.filter((e) => kinds[e.kind].checked && (e.year ? e.year <= upTo : upTo >= max));
    yearLabel.textContent = years().length ? (upTo >= max ? `up to ${max}` : `up to ${upTo}`) : "";
    layer.update(shown, { lines: linesBox.checked, fit });
    info.textContent = `${plural(shown.length, "event")} at ${new Set(shown.map((e) => e.placeKey)).size} places` +
      (st.pending ? ` · still looking up ${plural(st.pending, "place")}…` : "");
  }
  function drawUnlocated() {
    if (!data.unlocated.length) { clear(unloc); return; }
    mount(unloc, h("details", { class: "card map-unloc" }, h("summary", null, `${plural(data.unlocated.length, "place")} not on the map yet`),
      h("p", { class: "hint" }, "Places still being looked up, or that couldn't be found. Choose one, then click where it is on the map."),
      ...data.unlocated.map((u) => h("div", { class: "row" }, h("div", null, h("span", { class: "name" }, u.place),
        h("span", { class: "hint" }, ` · ${plural(u.events, "event")} · ${u.status === "failed" ? "not found" : "looking it up"}`)),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => startPin(u.place) }, "Place on map")))));
  }
  function startPin(place) {
    pinFor = place;
    mapEl.classList.add("pinning");
    toast(`Click where “${place}” is on the map.`);
    mapEl.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  async function load() {
    const params = new URLSearchParams({ filter });
    const p = who.get();
    if (filter !== "all" && p) params.set("personId", p.id);
    try {
      data = await api(`api/map/points?${params}`);
    } catch (e) { info.textContent = e.message; data = { points: [], unlocated: [] }; }
    const ys = years();
    const lo = ys.length ? Math.min(...ys) : 0, hi = ys.length ? Math.max(...ys) : 0;
    slider.min = String(lo); slider.max = String(hi); slider.value = String(hi);
    slider.disabled = !ys.length; play.disabled = !ys.length;
    draw(true);
    drawUnlocated();
  }
  slider.addEventListener("input", () => draw(false));
  play.addEventListener("click", () => {
    if (timer) { clearInterval(timer); timer = null; play.textContent = "▶ Play"; return; }
    const hi = Number(slider.max), lo = Number(slider.min);
    let y = Number(slider.value) >= hi ? lo : Number(slider.value);
    const step = Math.max(1, Math.round((hi - lo) / 60));
    play.textContent = "⏸ Pause";
    timer = setInterval(() => {
      y = Math.min(hi, y + step);
      slider.value = String(y); draw(false);
      if (y >= hi || !document.body.contains(mapEl)) { clearInterval(timer); timer = null; play.textContent = "▶ Play"; }
    }, 250);
  });
  const page = h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Map"), h("div", { class: "toolbar", style: "margin:0" }, filterSeg.el)),
    h("div", { class: "card map-controls" },
      h("div", { class: "rel-picker" }, h("span", { class: "hint" }, "Whose:"), who.el),
      h("div", { class: "chips" }, ...kindBoxes, h("label", { class: "check-row" }, linesBox, "Moves (lines)")),
      h("div", { class: "map-slider" }, h("span", { class: "hint" }, "Events"), yearLabel, slider, play)),
    mapEl, info, unloc);
  setTimeout(async () => {
    map = L.map(mapEl, { worldCopyJump: true }).setView([20, 78], 3);
    tileLayer(L, st).addTo(map);
    layer = placesLayer(L, map, { onMove: startPin });
    map.on("click", async (e) => {
      if (!pinFor) return;
      const place = pinFor;
      pinFor = null; mapEl.classList.remove("pinning");
      try { await api("api/map/places", { method: "PUT", body: { place, lat: e.latlng.lat, lon: e.latlng.lng } }); toast(`Pinned ${place}`); load(); }
      catch (x) { fail(x); }
    });
    await load();
  }, 0);
  return page;
}

/* A small map of one person's places on their Timeline tab. */
function personMiniMap(p) {
  const el = h("div", { class: "map-mini" });
  const box = h("div", { class: "card" }, h("div", { class: "page-head", style: "margin:0 0 8px" }, h("h3", { style: "margin:0" }, "Places"),
    h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => go(`map/${p.id}`) }, "Open the map")), el);
  (async () => {
    try {
      const [st, L, data] = await Promise.all([api("api/map/status"), loadLeaflet(), api(`api/map/points?filter=person&personId=${encodeURIComponent(p.id)}`)]);
      if (!data.points.length) { box.remove(); return; }
      const map = L.map(el, { scrollWheelZoom: false }).setView([data.points[0].lat, data.points[0].lon], 5);
      tileLayer(L, st).addTo(map);
      placesLayer(L, map, {}).update(data.points, { lines: true, fit: true });
    } catch (e) { box.remove(); }
  })();
  return box;
}

// ---------- history ----------
async function historyList(filters = {}) {
  const box = h("div", { class: "card" });
  let page = 1;
  const load = async () => {
    const params = new URLSearchParams({ page: String(page), page_size: "40" });
    if (filters.personId) params.set("personId", filters.personId);
    if (filters.userId) params.set("userId", filters.userId);
    const r = await api(`api/history?${params}`);
    if (page === 1) clear(box);
    const old = box.querySelector(".more-btn");
    if (old) old.remove();
    if (!r.items.length && page === 1) box.appendChild(h("div", { class: "empty" }, "No changes yet."));
    for (const it of r.items) box.appendChild(historyRow(it));
    if (r.page * r.pageSize < r.total) box.appendChild(h("div", { class: "actions more-btn", style: "justify-content:center" },
      h("button", { type: "button", class: "btn-ghost", onclick: () => { page++; load().catch(fail); } }, "Show older")));
    return r;
  };
  const r = await load();
  box._users = r.users;
  return box;
}

function historyRow(it) {
  const detail = h("div", { class: "hist-detail", hidden: true });
  let loaded = false;
  const toggle = async () => {
    detail.hidden = !detail.hidden;
    if (!detail.hidden && !loaded) {
      loaded = true;
      try {
        const d = await api(`api/history/${it.id}`);
        mount(detail, ...d.changes.map((c) => h("div", { class: "chg" }, `${c.op} ${c.entity}`,
          c.fields.length ? ": " : "",
          ...c.fields.slice(0, 8).flatMap((f, i) => [i ? "; " : "", `${f.field.replace(/_/g, " ")} `,
            f.before !== null && f.before !== undefined ? h("span", { class: "old" }, String(f.before).slice(0, 60)) : null,
            f.before !== null && f.before !== undefined && f.after !== null && f.after !== undefined ? " → " : "",
            f.after !== null && f.after !== undefined ? h("span", { class: "new" }, String(f.after).slice(0, 60)) : (f.before !== null && f.before !== undefined ? " (cleared)" : "")]))));
      } catch (e) { detail.textContent = e.message; }
    }
  };
  const undoBtn = !it.changes ? null : !it.undoneBy ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
    try { await api(`api/history/${it.id}/undo`, { method: "POST" }); toast("Undone"); rerender(); } catch (e) { fail(e); }
  } }, "Undo") : h("span", { class: "chip" }, "undone");
  return h("div", { class: "hist-row" + (it.undoneBy ? " undone" : "") },
    h("div", { class: "top" }, h("span", { class: "label" }, it.label),
      h("button", { type: "button", class: "link-btn", style: "font-size:0.8rem", onclick: toggle }, "Details"), undoBtn),
    h("div", { class: "meta" }, `${it.userName} · ${fmtWhen(it.at)}`,
      it.people.length ? " · " : "", ...it.people.slice(0, 5).flatMap((p, i) => [i ? ", " : "", p.id && p.name !== "a deleted person"
        ? h("button", { type: "button", class: "link-btn", style: "font-weight:500", onclick: () => go(`person/${p.id}`) }, p.name) : p.name])),
    detail);
}

async function viewHistory() {
  const filter = { userId: sessionStorageGet("histUser") || "" };
  const box = await historyList(filter.userId ? { userId: filter.userId } : {});
  const who = h("select", { "aria-label": "Changed by" }, h("option", { value: "" }, "Everyone"), ...(box._users || []).map((u) => h("option", { value: u.id }, u.name)));
  who.value = filter.userId;
  who.addEventListener("change", () => { sessionStorageSet("histUser", who.value); rerender(); });
  return h("div", null, h("div", { class: "page-head" }, h("h2", null, "History"), who),
    h("p", { class: "hint" }, "Every change, who made it and when. Undo works as long as nothing it touched was changed again afterwards."), box);
}
function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* ignore */ } }

// ---------- trash (Admin → Trash) ----------
async function viewTrash() {
  const r = await api("api/trash");
  const words = { people: "Person", families: "Family", media: "Photo" };
  const list = h("div", { class: "card" });
  if (!r.items.length) list.appendChild(h("div", { class: "empty" }, "The trash is empty."));
  for (const it of r.items) {
    list.appendChild(h("div", { class: "row" },
      h("div", null, h("div", { class: "name" }, it.name), h("div", { class: "hint" }, `${it.kind === "document" ? "Document" : words[it.entity]} · deleted ${fmtWhen(it.deletedAt)}`)),
      h("button", { type: "button", class: "btn-secondary btn-small", onclick: async () => {
        try { const res = await api(`api/trash/${it.entity}/${it.id}/restore`, { method: "POST" }); saved(res, "Restored"); rerender(); } catch (e) { fail(e); }
      } }, "Restore")));
  }
  const empty = r.items.length ? h("button", { type: "button", class: "btn-danger btn-small", onclick: async () => {
    if (!await confirmDialog("Empty the trash", "Permanently delete everything in the trash? This can't be undone.", "Empty trash", true)) return;
    try { const res = await api("api/trash", { method: "DELETE" }); toast(`Deleted ${res.purged.people} people, ${res.purged.families} families, ${res.purged.media} photos`); rerender(); } catch (e) { fail(e); }
  } }, "Empty trash") : null;
  return h("div", null, h("div", { class: "page-head" },
    h("p", { class: "hint", style: "margin:0;flex:1;min-width:200px" }, `Deleted people, families and photos stay here for ${plural(r.trashDays, "day")} (see App settings), then they're removed for good. Only admins see the trash; everyone can still undo a delete from History.`), empty),
    list);
}

// ---------- settings ----------
async function viewSettings() {
  const meCard = h("div", { class: "card" }, h("h3", null, "This is me"));
  const drawMe = () => {
    const current = state.user.mePersonId;
    const picker = personPicker({ placeholder: "Find yourself in the tree…" });
    mount(meCard, h("h3", null, "This is me"),
      h("p", { class: "hint" }, "Link yourself to your person in the tree: it opens centred on you and names everyone's relationship to you."),
      current ? h("div", { class: "row" }, h("div", null, h("span", { class: "name" }, state.user.mePersonName || "(deleted person)")),
        h("div", { class: "person-actions" },
          h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => go(`person/${current}`) }, "Open"),
          h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => claimMe(null) }, "Unlink")))
        : h("div", null, picker.el, h("div", { class: "actions" }, h("button", { type: "button", class: "btn-primary", onclick: () => {
          const p = picker.get();
          if (!p) { toast("Search for your name and pick yourself first.", { error: true }); return; }
          claimMe(p.id);
        } }, "Save"))));
  };
  drawMe();
  const theme = h("select", { "aria-label": "Theme" }, ...[["heritage", "📜 Heritage"], ["slate", "🌆 Slate"], ["daylight", "☀️ Daylight"], ["parchment", "🕯️ Parchment"], ["auto", "🌓 Auto (follows your device)"]].map(([k, l]) => h("option", { value: k }, l)));
  theme.value = window.__themeChoice || "heritage";
  theme.addEventListener("change", () => applyTheme(theme.value));
  const order = h("select", { "aria-label": "Typed date order" }, h("option", { value: "dmy" }, "Day / Month / Year (12/03/1950 = 12 March)"), h("option", { value: "mdy" }, "Month / Day / Year (03/12/1950 = 12 March)"));
  order.value = dateOrder();
  order.addEventListener("change", () => { lsSet("dateOrder", order.value); toast("Saved"); });
  return h("div", null, h("h2", { class: "page-title" }, "Settings"), meCard,
    feat("kin_names") || feat("script_names") ? kinLangCard() : null,
    feat("reminders") ? await remindersCard() : null,
    state.me.isAdmin && feat("custom_fields") ? await customFieldsCard() : null,
    feat("milestones") ? await milestonesCard() : null,
    feat("quiz") ? await kidPinCard() : null,
    h("div", { class: "card" }, h("h3", null, "Display"),
      h("div", { class: "form-row" }, field("Theme", theme), field("When typing dates like 12/03/1950", order)),
      h("div", { class: "hint" }, "Both are remembered in this browser.")),
    await whoamiCard());
}

const KIN_LANGS = { en: "English", te: "Telugu (తెలుగు)", hi: "Hindi (हिन्दी)" };

/* Settings → Relationship names (§13.1): your language, and the family's words. */
function kinLangCard() {
  const u = state.user;
  const sel = h("select", { "aria-label": "Relationship names" },
    h("option", { value: "" }, `App default — ${KIN_LANGS[u.kinLangApp] || "English"}`),
    ...Object.entries(KIN_LANGS).map(([k, l]) => h("option", { value: k }, l)));
  sel.value = u.kinLang || "";
  sel.addEventListener("change", async () => {
    try {
      await api("api/me/kin-lang", { method: "PUT", body: { kinLang: sel.value || null } });
      await loadMe();
      toast("Saved");
      rerender();
    } catch (e) { fail(e); }
  });
  const lang = u.kinLangEffective;
  const kinOn = feat("kin_names");
  return h("div", { class: "card" }, h("h3", null, "Names and relationships"),
    kinOn ? h("p", { class: "hint" }, "How relatives are named for you — “your uncle”, or in Telugu or Hindi: “Babai — your father's younger brother”. " +
      "Everyone can choose their own; the household default is set by an admin.") : null,
    kinOn && u.kinLang && u.kinLang !== u.kinLangApp
      ? h("p", { class: "hint" }, `Your own choice (${KIN_LANGS[u.kinLang] || u.kinLang}) is used instead of the household's ` +
          `${KIN_LANGS[u.kinLangApp] || "English"}. Pick “App default” to follow the household.`) : null,
    h("div", { class: "form-row" }, kinOn ? field("Show relationships in", sel) : null, !feat("script_names") ? null : field("Show names in", (() => {
      const nd = h("select", { "aria-label": "Show names in" }, h("option", { value: "en" }, "English"),
        h("option", { value: "script" }, "Telugu / Hindi script (where there is one)"), h("option", { value: "both" }, "Both"));
      nd.value = u.nameDisplay || "en";
      nd.addEventListener("change", async () => {
        try { await api("api/me/name-display", { method: "PUT", body: { nameDisplay: nd.value } }); await loadMe(); toast("Saved"); rerender(); }
        catch (e) { fail(e); }
      });
      return nd;
    })())),
    kinOn && lang !== "en" ? h("p", { class: "hint" }, "Does your family say it differently? ",
      h("button", { type: "button", class: "link-btn", onclick: () => go(`kin/${lang}`) }, `Edit the ${KIN_LANGS[lang].split(" ")[0]} words`),
      " — changes are shared with everyone and kept in History.") : null);
}

/* Settings → Relationship names → the family's own words (§13.1). */
async function viewKinTerms(lang) {
  if (!["te", "hi"].includes(lang)) lang = "te";
  const r = await api(`api/kin-terms?lang=${lang}`);
  const filter = h("input", { type: "search", placeholder: "Find a relationship or word…", "aria-label": "Find" });
  const list = h("div", { class: "card kin-list" });
  const draw = () => {
    const q = filter.value.trim().toLowerCase();
    clear(list);
    const rows = r.items.filter((it) => !q || it.meaning.toLowerCase().includes(q) || (it.term || "").toLowerCase().includes(q) || it.key.toLowerCase() === q);
    if (!rows.length) list.appendChild(h("div", { class: "empty" }, "Nothing matches."));
    for (const it of rows) {
      const term = h("input", { value: it.term || "", maxlength: 60, "aria-label": `${r.language} word for ${it.meaning}` });
      const note = h("input", { value: it.note || "", maxlength: 120, placeholder: "note (optional)", "aria-label": `Note for ${it.meaning}` });
      const save = h("button", { type: "button", class: "btn-secondary btn-small", onclick: async () => {
        try {
          const res = await api(`api/kin-terms/${lang}/${encodeURIComponent(it.key)}`, { method: "PUT", body: { term: term.value, note: note.value || null } });
          saved(res, res.batchId ? "Saved" : "No change"); rerender();
        } catch (e) { fail(e); }
      } }, "Save");
      const reset = it.source === "custom" ? h("button", { type: "button", class: "btn-ghost btn-small", title: it.seedTerm ? `Back to ${it.seedTerm}` : "Remove",
        onclick: async () => {
          try { const res = await api(`api/kin-terms/${lang}/${encodeURIComponent(it.key)}`, { method: "DELETE" }); saved(res, "Reset to default"); rerender(); }
          catch (e) { fail(e); }
        } }, it.seedTerm ? "Reset" : "Remove") : null;
      list.appendChild(h("div", { class: "kin-row" },
        h("div", { class: "kin-meaning" }, h("div", null, it.meaning), h("code", { class: "hint" }, it.key),
          it.source === "custom" ? h("span", { class: "chip accent" }, it.seedTerm ? `changed · default ${it.seedTerm}` : "added") : null),
        term, note, h("div", { class: "person-actions" }, save, reset)));
    }
  };
  filter.addEventListener("input", debounce(draw, 150));
  draw();
  // add a relationship that has no word yet
  const keyIn = h("input", { placeholder: "e.g. F.F.B", maxlength: 40, spellcheck: "false", autocapitalize: "characters", "aria-label": "Relationship key" });
  const keyMeaning = h("span", { class: "hint" });
  const LET = { F: "father", M: "mother", B: "brother", Z: "sister", S: "son", D: "daughter", H: "husband", W: "wife" };
  keyIn.addEventListener("input", () => {
    const parts = keyIn.value.trim().toUpperCase().split(".");
    keyMeaning.textContent = parts.every((x) => /^[FMBZSDHW][+-]?$/.test(x))
      ? "= " + parts.map((x) => (x[1] === "+" ? "elder " : x[1] === "-" ? "younger " : "") + LET[x[0]]).join("'s ") : "";
  });
  const newTerm = h("input", { placeholder: "your word", maxlength: 60, "aria-label": "Word" });
  const add = h("button", { type: "button", class: "btn-primary btn-small", onclick: async () => {
    const key = keyIn.value.trim().toUpperCase();
    try { const res = await api(`api/kin-terms/${lang}/${encodeURIComponent(key)}`, { method: "PUT", body: { term: newTerm.value } }); saved(res, "Added"); rerender(); }
    catch (e) { fail(e); }
  } }, "Add");
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, `${r.language} relationship names`),
      h("div", { class: "toolbar", style: "margin:0" },
        segmented([["te", "Telugu"], ["hi", "Hindi"]], lang, (k) => go(`kin/${k}`)).el,
        h("button", { type: "button", class: "btn-ghost", onclick: () => go("settings") }, "← Settings"))),
    h("p", { class: "hint" }, "The words shown for each relationship. Change any word to the one your family uses — it changes for everyone, and it's in History with an Undo. ",
      "“Reset” goes back to the default. Elder and younger come from birth dates, or the order of children in a family."),
    h("div", { class: "toolbar" }, filter), list,
    h("div", { class: "card" }, h("h3", null, "Add a relationship"),
      h("p", { class: "hint" }, "Write the relationship as letters joined by dots: F father, M mother, B brother, Z sister, S son, D daughter, H husband, W wife; add + for elder or - for younger. ",
        "F.F.B is your father's father's brother; M.B.S+ an elder son of your mother's brother."),
      h("div", { class: "form-row notify-add" }, keyIn, newTerm, add), keyMeaning));
}

/* Settings → Custom fields (admins, §13.8). */
const MILESTONE_KINDS = { age: "Birthday (age)", anniversary: "Wedding anniversary (years)", full_moons: "Full moons since birth" };
async function milestonesCard() {
  const card = h("div", { class: "card", id: "milestonesCard" });
  const admin = state.me.isAdmin;
  const draw = (items) => {
    const call = async (path, method, body) => { try { draw((await api(path, { method, body })).items); toast("Saved"); } catch (x) { fail(x); } };
    const rows = items.filter((m) => m.kind !== "full_moons" || feat("tithi")).map((m) => {
      const on = h("input", { type: "checkbox", checked: m.enabled, disabled: !admin, "aria-label": "On" });
      on.addEventListener("change", () => call(`api/milestones/${m.id}`, "PATCH", { enabled: on.checked }));
      const leads = m.leadDays.map((d) => d === 0 ? "on the day" : d % 30 === 0 ? `${d / 30} mo` : d % 7 === 0 ? `${d / 7} wk` : `${d} d`).join(", ");
      return h("div", { class: "row" + (m.enabled ? "" : " archived") },
        h("label", { class: "check-row" }, on, h("span", null, h("span", { class: "name" }, `🎉 ${m.labelEn}`), m.labelTe && feat("kin_names") ? h("span", { class: "hint" }, ` · ${m.labelTe}`) : null,
          h("span", { class: "hint" }, ` · ${MILESTONE_KINDS[m.kind].split(" (")[0]} ${m.value} · ${leads}`))),
        admin ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
          if (await confirmDialog("Remove milestone", `Remove “${m.labelEn}”?`, "Remove", true)) call(`api/milestones/${m.id}`, "DELETE"); } }, "Remove") : null);
    });
    let add = null;
    if (admin) {
      const kind = h("select", { "aria-label": "Kind" }, ...Object.entries(MILESTONE_KINDS).filter(([k]) => k !== "full_moons" || feat("tithi")).map(([k, l]) => h("option", { value: k }, l)));
      const value = h("input", { type: "number", min: "1", max: "2000", value: "75", "aria-label": "Number" });
      const en = h("input", { type: "text", maxlength: "80", placeholder: "e.g. 75th birthday" });
      const te = h("input", { type: "text", maxlength: "80", placeholder: "తెలుగు (optional)" });
      add = h("details", null, h("summary", null, "+ Add a milestone"),
        h("div", { class: "form-row" }, field("Kind", kind), field("Number", value, "narrow"), field("Name", en), feat("kin_names") ? field("Telugu name", te) : null),
        h("div", { class: "actions" }, h("button", { type: "button", class: "btn-primary", onclick: () =>
          call("api/milestones", "POST", { kind: kind.value, value: +value.value, labelEn: en.value.trim(), labelTe: (feat("kin_names") && te.value.trim()) || null }) }, "Add")));
    }
    mount(card, h("h3", null, "Milestones"),
      h("p", { class: "hint" }, "Special birthdays and anniversaries get 🎉 in Upcoming and extra reminders ahead of time. Only for the living, and only when the year is known." +
        (admin ? "" : " Admins can change the list.")),
      h("div", null, ...rows), add);
  };
  try { draw((await api("api/milestones")).items); } catch (e) { mount(card, h("h3", null, "Milestones"), h("div", { class: "error-text" }, e.message)); }
  return card;
}

async function customFieldsCard() {
  const card = h("div", { class: "card", id: "customFieldsCard" });
  const KIND = { text: "Text", choice: "Choice", place: "Place", date: "Date" };
  const draw = async () => {
    const r = await api("api/custom-fields?archived=true");
    const have = new Set(r.fields.map((f) => f.label.toLowerCase()));
    const patch = async (f, body) => { try { await api(`api/custom-fields/${f.id}`, { method: "PATCH", body }); toast("Saved"); draw(); } catch (x) { fail(x); } };
    const rows = r.fields.map((f) => {
      const onCard = h("input", { type: "checkbox", checked: f.onCard });
      onCard.addEventListener("change", () => patch(f, { onCard: onCard.checked }));
      const exp = h("input", { type: "checkbox", checked: f.exportDefault });
      exp.addEventListener("change", () => patch(f, { exportDefault: exp.checked }));
      return h("div", { class: "row" + (f.archived ? " archived" : "") },
        h("div", null, h("span", { class: "name" }, f.label), h("span", { class: "hint" }, ` · ${KIND[f.kind]}${f.appliesTo === "family" ? " · families" : ""}${f.kind === "choice" ? ` · ${f.choices.length} choices` : ""}${f.archived ? " · archived" : ""}`)),
        h("div", { class: "person-actions" },
          h("label", { class: "check-row small" }, onCard, "on cards"), h("label", { class: "check-row small" }, exp, "in exports"),
          h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => patch(f, { archived: !f.archived }) }, f.archived ? "Restore" : "Remove")));
    });
    const label = h("input", { maxlength: 60, placeholder: "e.g. Native village", "aria-label": "New field" });
    const kind = h("select", { "aria-label": "Kind" }, ...Object.entries(KIND).map(([k, l]) => h("option", { value: k }, l)));
    const choices = h("input", { placeholder: "choices, separated by commas", hidden: true, "aria-label": "Choices" });
    kind.addEventListener("change", () => { choices.hidden = kind.value !== "choice"; });
    const applies = h("select", { "aria-label": "For" }, h("option", { value: "person" }, "people"), h("option", { value: "family" }, "families"));
    const add = h("button", { type: "button", class: "btn-secondary btn-small", onclick: async () => {
      try {
        await api("api/custom-fields", { method: "POST", body: { label: label.value, kind: kind.value, appliesTo: applies.value,
          choices: kind.value === "choice" ? choices.value.split(",").map((x) => x.trim()).filter(Boolean) : null } });
        toast("Field added"); draw();
      } catch (x) { fail(x); }
    } }, "Add field");
    mount(card, h("h3", null, "Custom fields"),
      h("p", { class: "hint" }, "Extra details everyone can fill in on a person's page — native village, gotram, nakshatram… Removing a field hides it but keeps what was entered."),
      rows.length ? rows : h("div", { class: "hint" }, "No custom fields yet."),
      h("div", { class: "chips", style: "margin-top:10px" }, h("span", { class: "hint" }, "Quick add:"),
        ...r.templates.filter((t) => !have.has(t.label.toLowerCase())).map((t) => h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
          try { await api(`api/custom-fields/templates/${t.key}`, { method: "POST" }); toast(`${t.label} added`); draw(); } catch (x) { fail(x); }
        } }, "+ " + t.label))),
      h("div", { class: "form-row notify-add" }, label, kind, applies, add), choices);
  };
  try { await draw(); } catch (e) { mount(card, h("h3", null, "Custom fields"), h("div", { class: "error-text" }, e.message)); }
  return card;
}

/* Settings → Reminders (§9): your own digest. Nothing is sent about anyone
   until their 🔔 is on, and nothing is sent to you until you turn this on. */
async function remindersCard() {
  const card = h("div", { class: "card", id: "remindersCard" });
  const draw = (r) => {
    const enabled = h("input", { type: "checkbox", checked: r.enabled, disabled: !r.canEnable && !r.enabled });
    const time = h("input", { type: "time", value: r.time, required: true, "aria-label": "Time" });
    const kinds = {
      birthdays: h("input", { type: "checkbox", checked: r.birthdays }),
      anniversaries: h("input", { type: "checkbox", checked: r.anniversaries }),
      remembrance: h("input", { type: "checkbox", checked: r.remembrance }),
      tithi: h("input", { type: "checkbox", checked: r.tithi }),
      milestones: h("input", { type: "checkbox", checked: r.milestones }),
    };
    const tlead = h("select", { "aria-label": "Tithi notice" }, ...[0, 1, 2, 3, 5, 7, 10, 14, 21, 30].map((n) => h("option", { value: String(n) }, n === 0 ? "On the day only" : `On the day and ${n} day${n === 1 ? "" : "s"} before`)));
    tlead.value = String(r.tithiLeadDays);
    const lead = h("select", { "aria-label": "Days ahead" }, ...Array.from({ length: 15 }, (_, n) =>
      h("option", { value: String(n) }, n === 0 ? "Only today" : n === 1 ? "Today + tomorrow" : `Today + ${n} days`)));
    lead.value = String(r.leadDays);
    const scope = segmented([["all", "Everyone with 🔔 on"], ["close", "Only my close family"]], r.scope);
    const err = h("div", { class: "error-text", role: "alert" });
    const save = h("button", { type: "button", class: "btn-primary", onclick: async () => {
      err.textContent = "";
      const body = { enabled: enabled.checked, time: time.value, leadDays: Number(lead.value), scope: scope.get(),
        birthdays: kinds.birthdays.checked, anniversaries: kinds.anniversaries.checked, remembrance: kinds.remembrance.checked,
        tithi: kinds.tithi.checked, tithiLeadDays: Number(tlead.value), milestones: kinds.milestones.checked };
      if (!/^\d{2}:\d{2}$/.test(body.time || "")) { err.textContent = "Choose a time, like 08:00."; return; }
      if (body.scope === "close" && !r.meSet) { err.textContent = "“Only my close family” needs “This is me” — set it above first."; return; }
      save.disabled = true;
      try { draw(await api("api/reminders/prefs", { method: "PUT", body })); toast("Reminder settings saved"); }
      catch (e) { err.textContent = e.message; save.disabled = false; }
    } }, "Save");
    const test = h("button", { type: "button", class: "btn-secondary", disabled: !r.services.length, onclick: async () => {
      test.disabled = true;
      try { await api("api/reminders/test", { method: "POST" }); toast("Test sent — check your phone"); }
      catch (e) { fail(e); }
      finally { setTimeout(() => { test.disabled = false; }, 3000); }
    } }, "Send me a test");
    const where = r.services.length
      ? h("p", { class: "hint" }, "Sent to ", ...r.services.flatMap((sv, i) => [i ? ", " : null, h("code", null, sv)]),
        " (your phones from Home Assistant, plus anything an admin added). One message a day, only when there's something to say.")
      : h("div", { class: "notice" }, "No phone is linked to you yet. In Home Assistant: Settings → People → you → Track device, and pick your phone (the Home Assistant Companion app) — it's picked up here within 5 minutes. Or ask an admin to add a notify service for you in Admin → Users. Until then reminders can't be turned on.");
    const cf = r.closeFamily;
    let shortcut;
    if (!r.meSet) shortcut = h("p", { class: "hint" }, "Set “This is me” above to turn reminders on for your close family in one go.");
    else if (cf.off === 0) shortcut = h("p", { class: "hint" }, `🔔 is on for all ${plural(cf.total, "person")} in your close family.`);
    else shortcut = h("button", { type: "button", class: "btn-secondary", onclick: async () => {
      const ok = await confirmDialog("Reminders for your close family",
        `Turn on 🔔 for ${plural(cf.off, "person")} in your close family? That's everyone within three steps of you — parents, children, ` +
        `siblings and partners, and theirs — living and deceased (${cf.total} in all, ${cf.total - cf.off} already on). ` +
        "The switch is shared, so they'll be in the reminders of everyone who has reminders on. You can undo this in one go.", "Turn on");
      if (!ok) return;
      try { const res = await api("api/reminders/close-family", { method: "POST" }); saved(res, `Reminders on for ${plural(res.count, "person")}`); draw(await api("api/reminders/prefs")); }
      catch (e) { fail(e); }
    } }, `🔔 Turn on reminders for my close family (${plural(cf.off, "person")})`);
    mount(card, h("h3", null, "Reminders"),
      h("p", { class: "hint" }, "A daily message to your phone about birthdays, anniversaries and, if you like, remembrance days. " +
        "Only people whose 🔔 is on are included — ", h("strong", null, String(r.peopleOn)), ` ${r.peopleOn === 1 ? "person has" : "people have"} it on now. ` +
        "Each person's 🔔 is on their page and in Upcoming."),
      where,
      r.warning ? h("div", { class: "notice warn" }, "⚠ " + r.warning) : null,
      h("label", { class: "check-row" }, enabled, h("strong", null, "Send me reminders")),
      h("div", { class: "form-row" }, field(`Time (${r.timezone})`, time, "narrow"), field("Covering", lead)),
      h("div", { class: "field wide" }, "Which days",
        h("div", { class: "chips" },
          h("label", { class: "check-row" }, kinds.birthdays, "🎂 Birthdays"),
          h("label", { class: "check-row" }, kinds.anniversaries, "💍 Anniversaries"),
          h("label", { class: "check-row" }, kinds.remembrance, "🕯 Remembrance days"),
          feat("tithi") ? h("label", { class: "check-row" }, kinds.tithi, "🪔 Tithi days") : null,
          feat("milestones") ? h("label", { class: "check-row" }, kinds.milestones, "🎉 Milestones") : null)),
      feat("tithi") ? h("div", { class: "form-row" }, field("🪔 Tithi days", tlead)) : null,
      feat("milestones") ? h("p", { class: "hint" }, "Milestones (60th birthday, golden anniversary…) come 3 months, 1 month and 1 week ahead, and on the day.") : null,
      h("div", { class: "field wide" }, "About whom", scope.el,
        !r.meSet ? h("span", { class: "hint" }, "Close family needs “This is me”.") : null),
      err, h("div", { class: "actions" }, test, save),
      h("hr", { class: "sep" }), shortcut);
  };
  try { draw(await api("api/reminders/prefs")); }
  catch (e) { mount(card, h("h3", null, "Reminders"), h("div", { class: "error-text" }, e.message)); }
  return card;
}

function applyTheme(choice) {
  window.__themeChoice = choice;
  lsSet("theme", choice);
  document.documentElement.setAttribute("data-theme", window.__resolveTheme ? window.__resolveTheme(choice) : choice);
  const sel = $("#themeSelect");
  if (sel) sel.value = choice;
}

async function whoamiCard() {
  const w = await api("api/whoami");
  const name = w.nameSent ? w.haUsername : null;
  const row = (label, value, copy) => h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, label),
    h("div", { class: "kv-value" }, value, copy ? h("button", { class: "icon-btn", type: "button", title: "Copy", "aria-label": `Copy ${label}`, onclick: () => copyText(copy) }, "⧉") : null));
  const yesNo = (v) => h("strong", null, v ? "Yes" : "No");
  let advice;
  if (w.isAdmin) advice = "You are an administrator.";
  else if (w.displayNameOnly) advice = h("span", null, "Your ", h("strong", null, "display name"), " is in ", h("code", null, "admin_users"),
    ", but display names are never used for matching. Replace it with ", h("strong", null, name || w.haUserId), " and restart the app.");
  else if (w.adminEntries === 0) advice = h("span", null, "The ", h("code", null, "admin_users"), " list is empty in the running app. If you've filled it in, restart the app — options are only read when it starts.");
  else advice = h("span", null, `Neither your user name nor your user id matches any of the ${plural(w.adminEntries, "entry")} in `, h("code", null, "admin_users"),
    ". Add ", h("strong", null, name || w.haUserId), " exactly as shown, save, and restart the app. Upper and lower case don't matter.");
  return h("div", { class: "card", id: "whoamiCard" }, h("h3", null, "How the app sees you"),
    h("div", { class: "kv" },
      row("User name (sent by Home Assistant)", name || "not sent", name),
      row("User id (sent by Home Assistant)", h("code", null, w.haUserId), w.haUserId),
      row("Display name (not used for matching)", w.haDisplayName),
      row("Administrator in this app", yesNo(w.isAdmin)),
      row("Names in admin_users", String(w.adminEntries)),
      row("This is me", w.mePersonName ? w.mePersonName : h("button", { type: "button", class: "link-btn", onclick: () => go("settings") }, "not set — set it")),
      state.user && !feat("reminders") ? null : row("Reminders", w.notifyLinked
        ? `${w.remindersOn ? "on" : "off"} · ${w.notifyLinked === 1 ? "1 phone or service" : `${w.notifyLinked} phones or services`} · ${plural(w.remindPeople, "person")} with 🔔 on`
        : "No phone linked yet — in Home Assistant: Settings → People → you → Track device (your phone with the Companion app). It's picked up within 5 minutes; or ask an admin."),
      row("Account status", w.disabled ? h("strong", { style: "color:var(--danger)" }, "Turned off by an admin") : "Enabled")),
    h("div", { class: "hint", style: "margin-top:8px" }, advice));
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast("Copied"); } catch (e) { toast("Couldn't copy — select and copy it by hand.", { error: true }); }
}

// ---------- admin ----------
const ADMIN_TABS = [["settings", "App settings"], ["users", "Users"], ["storage", "Storage"], ["trash", "Trash"]];

async function viewAdmin(tab) {
  if (!state.me.isAdmin) {
    return h("div", null, h("h2", { class: "page-title" }, "Admin"),
      h("div", { class: "card" }, h("h3", null, "Only admins can open this page"),
        h("p", { class: "hint" }, "App settings, users, storage and the trash are for Family Tree's admins, who are listed in the ",
          h("code", null, "admin_users"), " option on the app's Configuration tab in Home Assistant. Deleted something by mistake? Undo it from ",
          h("button", { type: "button", class: "link-btn", onclick: () => go("history") }, "History"), ".")));
  }
  if (!ADMIN_TABS.some(([k]) => k === tab)) return h("div", { class: "empty" }, "Page not found.");
  const tabs = h("div", { class: "tabs", role: "tablist" }, ...ADMIN_TABS.map(([k, l]) =>
    h("button", { type: "button", role: "tab", class: k === tab ? "active" : "", "aria-selected": String(k === tab), onclick: () => go(`admin/${k}`) }, l)));
  let body;
  if (tab === "users") body = await viewAdminUsers();
  else if (tab === "storage") body = await viewAdminStorage();
  else if (tab === "trash") body = await viewTrash();
  else body = await viewAdminSettings();
  return h("div", null, h("h2", { class: "page-title" }, "Admin"), tabs, body);
}

const APP_SETTING_FIELDS = [
  { key: "trash_days", label: "Days in trash", min: 7, max: 3650, unit: "days",
    help: "How long deleted people, families and photos can be restored before they're removed for good." },
  { key: "max_upload_mb", label: "Largest upload (MB)", min: 1, max: 100, unit: "MB",
    help: "The biggest photo or document anyone can upload." },
];

async function viewAdminSettings() {
  const s = await api("api/admin/settings");
  const err = h("div", { class: "error-text", role: "alert" });
  const inputs = {};
  const rows = APP_SETTING_FIELDS.map((f) => {
    const input = h("input", { type: "number", min: f.min, max: f.max, step: 1, inputmode: "numeric", value: String(s.values[f.key]), "aria-describedby": `help-${f.key}` });
    inputs[f.key] = input;
    return h("div", { class: "form-row" }, field(f.label, input, "narrow setting-field"),
      h("div", { class: "hint setting-help", id: `help-${f.key}` }, f.help, h("br"), `${f.min}–${f.max} ${f.unit}. Default ${s.defaults[f.key]}.`));
  });

  // ---- photo folder: checked before saving; changing it never moves files ----
  const pathInput = h("input", { type: "text", value: s.values.media_path, spellcheck: "false", autocomplete: "off", autocapitalize: "off", "aria-describedby": "help-media_path" });
  const pathResult = h("div", { class: "folder-check", role: "status", "aria-live": "polite" });
  let lastCheck = null;          // { path, result } for the value in the box
  const markerWords = { this: "this tree's marker", other: "another tree's marker", none: "no .family_tree_store marker" };
  const drawCheck = (r) => {
    const kind = r.refused ? "bad" : r.ok ? "good" : "warn";
    const facts = [r.exists ? (r.writable ? "folder exists, writable" : "folder exists, read-only") : "folder doesn't exist yet",
      markerWords[r.marker], plural(r.files, "photo file"), r.networkMount ? "network storage" : null].filter(Boolean).join(" · ");
    mount(pathResult, h("div", { class: `folder-check-box ${kind}` },
      h("div", null, { good: "✅ ", warn: "⚠️ ", bad: "⛔ " }[kind], r.message), h("div", { class: "hint" }, facts)));
  };
  const checkFolder = async () => {
    const p = pathInput.value.trim();
    mount(pathResult, h("div", { class: "hint" }, "Checking the folder…"));
    try {
      const r = await api("api/admin/settings/check-media-path", { method: "POST", body: { path: p } });
      if (pathInput.value.trim() !== p) return null;           // typed on meanwhile
      lastCheck = { path: p, result: r };
      drawCheck(r);
      return r;
    } catch (e) { lastCheck = null; mount(pathResult, h("div", { class: "error-text" }, e.message)); return null; }
  };
  pathInput.addEventListener("input", () => { lastCheck = null; clear(pathResult); err.textContent = ""; });
  pathInput.addEventListener("change", () => { if (pathInput.value.trim() !== s.values.media_path) checkFolder(); });
  const nowLine = h("div", { class: "hint", id: "help-media_path" }, "In use now: ", h("code", null, s.media.path), " — ",
    s.media.online ? h("span", null, h("span", { class: "status-dot ok" }), "reachable") : h("span", { style: "color:var(--warn)" }, `offline: ${s.media.reason || "not reachable"}`));
  const folderRow = h("div", null,
    h("div", { class: "form-row folder-row" }, field("Photo folder", pathInput, "wide-ish"),
      h("button", { type: "button", class: "btn-secondary", onclick: checkFolder }, "Check folder")),
    nowLine,
    h("p", { class: "hint" }, "Where photos and documents are kept, inside /share — for example ", h("code", null, "/share/nas/family_tree"),
      " on network storage. Changing it doesn't move any files: copy the whole old folder, including ", h("code", null, ".family_tree_store"),
      ", to the new place first. Default ", h("code", null, s.defaults.media_path), "."),
    pathResult);

  // ---- module settings, shown while their module is switched on ----
  const on = (k) => !!s.values["feature_" + k];
  const kinSel = h("select", { "aria-label": "Relationship names", "aria-describedby": "help-relationship_language" },
    ...Object.entries(KIN_LANGS).map(([k, l]) => h("option", { value: k }, l)));
  kinSel.value = s.values.relationship_language || "en";
  const orderSel = h("select", { "aria-label": "Name order" }, h("option", { value: "given_first" }, "First name first (Asha Sharma)"),
    h("option", { value: "surname_first" }, "Surname first (Sharma Asha)"));
  orderSel.value = s.values.name_order || "given_first";
  const phoneIn = h("input", { value: s.values.default_phone_code, maxlength: 5, "aria-label": "Default phone code", style: "max-width:90px" });
  const tithiSel = h("select", { "aria-label": "Tithi day" }, h("option", { value: "aparahna" }, "Aparahna — the tithi covers the afternoon (usual for shraddha)"),
    h("option", { value: "sunrise" }, "Sunrise — the tithi at sunrise"));
  tithiSel.value = s.values.tithi_rule;
  const orderRow = h("div", { class: "form-row" }, field("Name order", orderSel, "narrow setting-field"),
    h("div", { class: "hint setting-help" }, "How full names are written for everyone. Any person can have their own order (Edit → More details). Default: first name first."));
  const moreRows = h("div", null,
    on("contacts") ? h("div", { class: "form-row" }, field("Default phone code", phoneIn, "narrow setting-field"),
      h("div", { class: "hint setting-help" }, "Contact details: used when a phone number is typed without a country code, like +1 or +44. Default +1.")) : null,
    on("tithi") ? h("div", { class: "form-row" }, field("Tithi day", tithiSel, "setting-field"),
      h("div", { class: "hint setting-help" }, "Tithi: which day a death-anniversary tithi is kept on, when a tithi spans two days. Default aparahna.")) : null,
    on("kin_names") ? h("div", { class: "form-row" }, field("Relationship names", kinSel, "narrow setting-field"),
      h("div", { class: "hint setting-help", id: "help-relationship_language" },
        "The household's language for relationship names: “uncle”, or “Babai — your father's younger brother”. Each person can choose their own in Settings. Default English.")) : null);
  // ---- places map addresses (only while the map is on) ----
  const tilesIn = h("input", { type: "url", value: s.values.map_tiles_url, spellcheck: "false", autocomplete: "off", "aria-label": "Map tiles address" });
  const nomIn = h("input", { type: "url", value: s.values.nominatim_url, spellcheck: "false", autocomplete: "off", "aria-label": "Place search address" });
  const mapStatus = h("div", { class: "hint" });
  if (on("map")) {
    api("api/map/status").then((m) => mount(mapStatus, `${m.located} of ${plural(m.places, "place")} found on the map` +
      (m.pending ? `, ${m.pending} still to look up` : "") + (m.failed ? `, ${m.failed} not found. ` : ". "),
      m.failed ? h("button", { type: "button", class: "link-btn", onclick: async () => {
        try { const r = await api("api/map/retry", { method: "POST" }); toast(`Looking up ${plural(r.retrying, "place")} again`); } catch (e) { fail(e); }
      } }, "Try the missing ones again") : null)).catch(() => {});
  }
  const mapRow = on("map") ? h("div", { class: "map-settings" },
    h("h4", { class: "field-label" }, "Places map"),
    h("div", { class: "form-row" }, field("Map tiles address", tilesIn, "wide"), field("Place search address", nomIn, "wide")),
    h("div", { class: "hint" }, "Defaults: OpenStreetMap's tile server and Nominatim. Point them at your own servers if you have them."),
    mapStatus) : null;
  // ---- Features: every optional module has a switch ----
  const featBoxes = {};
  const GROUPS = [["general", "General"], ["regional", "Region- and culture-specific (off for a new install)"],
    ["internet", "Uses the internet (off for a new install)"]];
  const featuresCard = h("div", { class: "card", id: "featuresCard" }, h("h3", null, "Features"),
    h("p", { class: "hint" }, "Turn parts of the app on or off for everyone. Turning something off only hides it — nothing is deleted, " +
      "and turning it back on shows everything again. Background work for a module that's off (reminders, map look-ups, the inbox) stops."),
    ...GROUPS.map(([kind, title]) => {
      const items = (s.features || []).filter((f) => f.kind === kind);
      if (!items.length) return null;
      return h("div", { class: "feature-group" }, h("h4", { class: "field-label" }, title),
        ...items.map((f) => {
          const box = h("input", { type: "checkbox", checked: !!s.values[f.key], "aria-describedby": `help-${f.key}` });
          featBoxes[f.key] = box;
          return h("label", { class: "check-row feature-row" }, box, h("span", null, h("strong", null, f.label),
            h("span", { class: "hint", id: `help-${f.key}` }, ` — ${f.help} Default: ${f.default ? "on" : "off"}.`)));
        }));
    }));
  const save = h("button", { type: "button", class: "btn-primary", onclick: async () => {
    err.textContent = "";
    const body = {};
    for (const [k, box] of Object.entries(featBoxes)) if (box.checked !== s.values[k]) body[k] = box.checked;
    if (on("kin_names") && kinSel.value !== s.values.relationship_language) body.relationship_language = kinSel.value;
    if (orderSel.value !== s.values.name_order) body.name_order = orderSel.value;
    if (on("contacts") && phoneIn.value.trim() !== s.values.default_phone_code) body.default_phone_code = phoneIn.value.trim();
    if (on("tithi") && tithiSel.value !== s.values.tithi_rule) body.tithi_rule = tithiSel.value;
    if (on("map") && tilesIn.value.trim() !== s.values.map_tiles_url) body.map_tiles_url = tilesIn.value.trim();
    if (on("map") && nomIn.value.trim() !== s.values.nominatim_url) body.nominatim_url = nomIn.value.trim();
    for (const f of APP_SETTING_FIELDS) {
      const raw = inputs[f.key].value.trim();
      const n = Number(raw);
      if (raw === "" || !Number.isInteger(n) || n < f.min || n > f.max) {
        err.textContent = `${f.label} must be a whole number from ${f.min} to ${f.max}.`;
        inputs[f.key].focus();
        return;
      }
      if (n !== s.values[f.key]) body[f.key] = n;
    }
    const newPath = pathInput.value.trim();
    if (newPath !== s.values.media_path) {
      const r = lastCheck && lastCheck.path === newPath ? lastCheck.result : await checkFolder();
      if (!r) { err.textContent = "Check the photo folder first."; return; }
      if (r.verdict !== "current") {
        if (r.refused) { err.textContent = r.message; return; }
        if (!r.ok) {
          if (!await confirmDialog("Change the photo folder", `${r.message} Change the photo folder anyway?`, "Change folder", true)) return;
          body.confirm = true;
        }
        body.media_path = newPath;
      }
    }
    if (body.feature_map === true && !await confirmDialog("Turn on the places map",
      "The map uses the internet: each place name in the tree (only the place — no names or dates) is sent to OpenStreetMap's place search, one a second, and everyone's browser downloads map pictures from the tile server. Turn it on?", "Turn on")) return;
    if (!Object.keys(body).filter((k) => k !== "confirm").length) { toast("Nothing to save"); return; }
    save.disabled = true;
    try {
      const r = await api("api/admin/settings", { method: "PUT", body });
      await loadMe().catch(() => {});                 // the photo-storage banner, default language and switches follow
      if ("media_path" in body && !r.media.online) toast(`Saved. Photo storage is offline: ${r.media.reason}`, { error: true });
      else toast("Settings saved");
      rerender();
    } catch (e) { err.textContent = e.message; save.disabled = false; }
  } }, "Save");
  const saveBar = (id) => h("div", { class: "actions", id }, save);
  return h("div", null,
    h("div", { class: "card" }, h("h3", null, "App settings"), ...rows, orderRow, moreRows, folderRow,
      mapRow ? h("hr", { class: "sep" }) : null, mapRow),
    featuresCard, err, saveBar("settingsSave"),
    h("p", { class: "hint", style: "margin-bottom:0" }, "Changes apply straight away, no restart needed. Only who is an admin (",
      h("code", null, "admin_users"), ") stays in the app's Configuration tab in Home Assistant."));
}

async function viewAdminUsers() {
  const users = await api("api/admin/users");
  const checkAgain = h("button", { type: "button", class: "link-btn", onclick: async () => {
    checkAgain.disabled = true;
    try { await api("api/admin/users?refresh=1"); toast("Read from Home Assistant"); rerender(); }
    catch (e) { fail(e); checkAgain.disabled = false; }
  } }, "Check Home Assistant again");
  const avail = feat("reminders") ? await api("api/admin/notify-services").catch(() => ({ available: false, services: [], entities: [] }))
    : { available: true, services: [], entities: [] };
  const known = [...avail.services, ...avail.entities];
  const listId = "notifyServiceList";
  const datalist = h("datalist", { id: listId }, ...known.map((sv) => h("option", { value: sv })));
  const list = h("div", { class: "card" });
  for (const u of users) {
    const toggle = h("input", { type: "checkbox", checked: !u.disabled, "aria-label": `Access for ${u.name}`, disabled: u.id === state.me.haUserId });
    toggle.addEventListener("change", async () => {
      try { await api(`api/users/${u.id}`, { method: "PATCH", body: { disabled: !toggle.checked } }); toast(toggle.checked ? "Access turned on" : "Access turned off"); }
      catch (e) { fail(e); toggle.checked = !toggle.checked; }
    });
    list.appendChild(h("div", { class: "row" },
      h("div", null, h("div", null, h("span", { class: "name" }, u.name), u.isAdmin ? h("span", { class: "badge-you" }, "admin") : null, u.id === state.me.haUserId ? h("span", { class: "badge-you" }, "you") : null),
        h("div", { class: "hint" }, [u.username, u.haPerson, u.lastSeen ? `last seen ${fmtWhen(u.lastSeen)}` : "never opened Family Tree"].filter(Boolean).join(" · ")),
        h("div", { class: "hint" }, "This is me: ", u.mePersonName ? h("strong", null, u.mePersonName) : "not set", " ",
          h("button", { type: "button", class: "link-btn", onclick: () => setUserMe(u) }, "change"))),
      h("label", { class: "check-row" }, toggle, "Can use Family Tree"),
      feat("reminders") ? notifyEditor(u, listId) : null));
  }
  return h("div", null,
    h("p", { class: "hint" }, "Everyone with a Home Assistant login is listed and can use Family Tree unless you turn their access off. Turned-off users see nothing and get no reminders."),
    !feat("reminders") ? null : h("p", { class: "hint" }, "📱 Phones come from Home Assistant: Settings → People → (the person) → Track device, picking their phone with the Home Assistant Companion app. " +
      "Set a phone up there once and every household app uses it. Add an extra notify service below (“Also”) only for something else — a speaker, a second service. " +
      "Each person still turns their own reminders on in Settings. ", checkAgain,
      avail.available ? null : h("span", { style: "color:var(--warn)" }, ` ${avail.error || "Home Assistant's notify services couldn't be listed"} — type the name instead.`)),
    datalist, list, feat("quiz") ? await kidSessionsCard() : null);
}

/* Admin → Users: the person's phones from Home Assistant (read-only), their extra
   notify services as removable chips ("Also"), + Add, Send test. */
function notifyEditor(u, listId) {
  const box = h("div", { class: "notify-edit" });
  let services = [...(u.notify || [])];
  const ha = u.ha || { known: false, person: null, phones: [] };
  const haPhones = ha.phones.filter((p) => p.service);
  const phoneChips = !ha.known
    ? [h("span", { class: "hint" }, "Home Assistant's people couldn't be read yet.")]
    : !ha.person
      ? [h("span", { class: "hint" }, "No Home Assistant person is linked to this login (Settings → People → the person → Allow person to login).")]
      : ha.phones.length
        ? ha.phones.map((p) => p.service
          ? h("span", { class: "chip big phone-chip", title: `${p.tracker || ""} → ${p.service}` }, "📱 " + p.label,
              h("span", { class: "chip-hint" }, p.service))
          : h("span", { class: "chip warn phone-chip", title: `${p.tracker || ""}: Home Assistant has no notify action for this phone` },
              `⚠ ${p.label} — Companion app action not found`))
        : [h("span", { class: "hint" }, `${ha.personName} has no phone in Home Assistant — Settings → People → ${ha.personName} → Track device.`)];
  const draw = () => {
    const input = h("input", { type: "text", list: listId, placeholder: "notify.mobile_app_phone", spellcheck: "false",
      autocomplete: "off", autocapitalize: "off", "aria-label": `Add a notify service for ${u.name}` });
    const add = async () => {
      const v = input.value.trim();
      if (!v) { input.focus(); return; }
      try { services = (await api(`api/admin/users/${u.id}/notify`, { method: "POST", body: { service: v } })).notify; toast("Notify service added"); draw(); }
      catch (e) { fail(e); }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    mount(box,
      h("div", { class: "chips" }, h("span", { class: "hint notify-label" }, "Phones:"), ...phoneChips,
        u.remindersOn ? h("span", { class: "chip accent" }, "reminders on") : null),
      h("div", { class: "chips" }, h("span", { class: "hint notify-label" }, "Also:"),
        services.length ? services.map((sv) => h("span", { class: "chip big" }, sv,
          h("button", { type: "button", class: "icon-btn", "aria-label": `Remove ${sv}`, title: "Remove", onclick: async () => {
            try { services = (await api(`api/admin/users/${u.id}/notify/${encodeURIComponent(sv)}`, { method: "DELETE" })).notify; toast("Removed"); draw(); }
            catch (e) { fail(e); }
          } }, "✕")))
          : h("span", { class: "hint" }, haPhones.length ? "nothing else" : "nothing — no reminders until a phone is linked in Home Assistant or a service is added here")),
      h("div", { class: "form-row notify-add" }, input,
        h("button", { type: "button", class: "btn-secondary btn-small", onclick: add }, "Add"),
        services.length || haPhones.length ? h("button", { type: "button", class: "btn-ghost btn-small", title: "Send a short test notification to their phones and every service listed", onclick: async () => {
          try { await api(`api/admin/users/${u.id}/notify-test`, { method: "POST" }); toast(`Test sent to ${u.name}`); } catch (e) { fail(e); }
        } }, "Send test") : null));
  };
  draw();
  return box;
}

function setUserMe(u) {
  const picker = personPicker({ placeholder: "Find their person in the tree…" });
  const m = openModal(`“This is me” for ${u.name}`, h("div", null, picker.el,
    h("div", { class: "actions" },
      u.mePersonId ? h("button", { type: "button", class: "btn-ghost", onclick: () => save(null) }, "Unlink") : null,
      h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn-primary", onclick: () => { const p = picker.get(); if (p) save(p.id); } }, "Save"))));
  async function save(pid) {
    try { await api(`api/users/${u.id}`, { method: "PATCH", body: { mePersonId: pid } }); m.close(); toast("Saved"); rerender(); } catch (e) { fail(e); }
  }
}

async function viewAdminStorage() {
  const s = await api("api/admin/storage");
  const checkBox = h("div");
  const mediaCard = h("div", { class: "card" }, h("h3", null, "Photo storage"),
    h("div", { class: "kv" },
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Photo folder"), h("div", { class: "kv-value" }, h("code", null, s.path), " ",
        h("button", { type: "button", class: "link-btn", onclick: () => go("admin/settings") }, "change"))),
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Status"), h("div", { class: "kv-value" }, h("span", { class: "status-dot" + (s.online ? " ok" : "") }), s.online ? "Reachable" : "Not reachable")),
      !s.online && s.reason ? h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Why"), h("div", { class: "kv-value", style: "font-weight:500" }, s.reason)) : null,
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Network storage (NAS)"), h("div", { class: "kv-value" }, s.networkMount ? "Yes" : "No — a local folder")),
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Photos"), h("div", { class: "kv-value" }, `${s.files} · ${fmtBytes(s.bytes)}`)),
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Free space"), h("div", { class: "kv-value" }, fmtBytes(s.freeBytes))),
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Database (in HA backups)"), h("div", { class: "kv-value" }, `${fmtBytes(s.dbBytes)} · ${plural(s.people, "person")}`))),
    h("p", { class: "hint" }, s.networkMount
      ? "Photos are on network storage, which Home Assistant leaves out of its backups — make sure the NAS backs them up."
      : "Photos are in Home Assistant's Share folder. Full backups include it unless “Share folder” is unticked; photos then need their own backup (see the app's Documentation tab)."),
    h("div", { class: "actions", style: "justify-content:flex-start" },
      s.foreignMarker ? h("button", { type: "button", class: "btn-primary", onclick: async () => {
        if (!await confirmDialog("Use this folder", "This folder holds photos from a Family Tree install. Use it for this tree (for example after restoring a backup)?", "Use this folder")) return;
        try { await api("api/admin/media/adopt", { method: "POST" }); toast("Done"); loadMe().then(rerender); } catch (e) { fail(e); }
      } }, "Use this folder") : null,
      s.online ? h("button", { type: "button", class: "btn-secondary", onclick: async () => {
        try {
          const r = await api("api/admin/media/check", { method: "POST" });
          mount(checkBox, h("div", { class: "hint", style: "margin-top:10px" },
            `${plural(r.missing.length, "photo")} missing from the folder · ${plural(r.orphans.length, "file")} not in the database.`),
            r.orphans.length ? h("button", { type: "button", class: "btn-ghost btn-small", style: "margin-top:6px", onclick: async () => {
              try { const x = await api("api/admin/media/orphans", { method: "POST", body: { ids: r.orphans } }); toast(`Moved ${x.moved} to the orphans folder`); } catch (e) { fail(e); }
            } }, "Move unknown files to orphans/") : null);
        } catch (e) { fail(e); }
      } }, "Check photos") : null),
    checkBox);
  const backup = h("div", { class: "card" }, h("h3", null, "Backup"),
    h("p", { class: "hint" }, "Database only is small: every person, family, event, history, trash and the App settings — but no photo files. Add the photos for a complete copy or a move to another Home Assistant."),
    h("div", { class: "actions", style: "justify-content:flex-start" },
      h("a", { class: "btn-primary", href: "api/admin-storage-download-db", download: "" }, "Download database"),
      s.online ? h("a", { class: "btn-secondary", href: "api/admin-storage-download-db?media=true", download: "" }, "Download database + photos") : null),
    h("p", { class: "hint" }, "A website export is not a backup: it holds only the details you chose, and no history."));
  const file = h("input", { type: "file", accept: ".zip,application/zip" });
  const result = h("div", { class: "hint" });
  const restore = h("div", { class: "card" }, h("h3", null, "Restore"),
    h("p", { class: "hint" }, "Replaces the whole tree with a backup file. Everything is checked before anything changes. Photos in the backup are written to the photo folder."),
    field("Backup file (.zip)", file, "wide"),
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-danger", onclick: async () => {
      if (!file.files.length) { toast("Choose a backup file first.", { error: true }); return; }
      if (!await confirmDialog("Restore backup", "Replace the entire tree with this backup? Changes made since the backup will be lost.", "Restore", true)) return;
      const fd = new FormData();
      fd.append("file", file.files[0]);
      result.textContent = "Restoring…";
      try {
        const r = await api("api/admin-storage-import-db", { method: "POST", formData: fd });
        result.textContent = `Restored. ${plural(r.mediaRestored, "photo file")} written.` + (r.mediaMissing ? ` ${plural(r.mediaMissing, "photo")} referenced by the database ${r.mediaMissing === 1 ? "is" : "are"} not in the folder and will show as initials.` : "");
        await loadMe();
      } catch (e) { result.textContent = ""; fail(e); }
    } }, "Restore")), result);
  return h("div", null, mediaCard, backup, restore);
}

// ---------- photos & documents ----------
const UPLOAD_ACCEPT = "image/jpeg,image/png,image/webp,image/gif,application/pdf";

function mediaThumb(m, opts = {}) {
  let inner;
  if (m.kind === "photo") {
    inner = h("img", { src: `api/media/${encodeURIComponent(m.id)}/file?size=256`, alt: m.title || "Photo", loading: "lazy" });
    inner.addEventListener("error", () => inner.replaceWith(h("div", { class: "doc-tile" }, h("span", { class: "doc-icon" }, "📷"), h("span", null, "Not available"))));
  } else {
    inner = h("div", { class: "doc-tile" }, h("span", { class: "doc-icon" }, "📄"), h("span", { class: "doc-name" }, m.title || "Document"));
  }
  const cap = [m.kind === "photo" ? m.title : null, m.dateDisplay].filter(Boolean).join(" · ");
  return h("button", { type: "button", class: "media-tile", title: m.title || (m.kind === "photo" ? "Photo" : "Document"), onclick: opts.onclick },
    inner,
    opts.profile ? h("span", { class: "profile-star", title: "Profile photo" }, "★") : null,
    cap ? h("span", { class: "media-cap" }, cap) : null);
}

/* Upload several files one after another, with progress. `target` links each to a person/family/event. */
/* The upload limit (Admin → App settings), re-read before each upload so a change applies at once. */
async function uploadLimitMb() {
  try { await loadMe(); } catch (e) { /* use the last known value */ }
  return (state.user && state.user.maxUploadMb) || null;
}
function tooBig(file, mb) { return mb && file.size > mb * 1024 * 1024 ? `${file.name}: larger than ${mb} MB, the upload limit` : null; }

async function uploadFiles(fileList, target = {}) {
  let files = Array.from(fileList || []);
  if (!files.length) return 0;
  const mb = await uploadLimitMb();
  const skipped = files.map((f) => tooBig(f, mb)).filter(Boolean);
  files = files.filter((f) => !tooBig(f, mb));
  if (skipped.length) toast(skipped.join(" · "), { error: true });
  if (!files.length) return 0;
  const el = h("div", { class: "toast" }, "Uploading…");
  $("#toastRoot").appendChild(el);
  let ok = 0;
  const errors = [];
  for (let i = 0; i < files.length; i++) {
    el.textContent = `Uploading ${i + 1} of ${files.length}: ${files[i].name}`;
    const fd = new FormData();
    fd.append("file", files[i]);
    for (const [k, v] of Object.entries(target)) if (v) fd.append(k, v);
    try { await api("api/media", { method: "POST", formData: fd }); ok++; }
    catch (e) { errors.push(`${files[i].name}: ${e.message}`); }
  }
  el.remove();
  if (ok) toast(`${plural(ok, "file")} added`);
  if (errors.length) toast(errors.join(" · "), { error: true });
  return ok;
}

function uploadButton(label, target, after) {
  const input = h("input", { type: "file", accept: UPLOAD_ACCEPT, multiple: true, hidden: true });
  input.addEventListener("change", async () => {
    const n = await uploadFiles(input.files, target);
    input.value = "";
    if (n && after) after();
  });
  return h("span", null, input, h("button", { type: "button", class: "btn-primary btn-small", onclick: () => input.click() }, label));
}

/* Drop files anywhere on `el` to upload them. */
function dropZone(el, target, after) {
  let depth = 0;
  el.addEventListener("dragenter", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); depth++; el.classList.add("dragging"); } });
  el.addEventListener("dragover", (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
  el.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) el.classList.remove("dragging"); });
  el.addEventListener("drop", async (e) => {
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    depth = 0;
    el.classList.remove("dragging");
    const n = await uploadFiles(e.dataTransfer.files, target);
    if (n && after) after();
  });
  return el;
}

/* A file name for a download ("Holi 2024.jpg"), not the URL's "file". */
function downloadName(m) {
  const ext = { "application/pdf": ".pdf", "image/png": ".png" }[m.contentType] || ".jpg";
  const base = (m.title || (m.kind === "document" ? "Document" : "Photo")).replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 100);
  return (base || "file") + ext;
}

/* The big view of one photo or document: details, who's in it, and prev/next. */
function mediaModal(items, index, opts = {}) {
  let i = index, changed = false, tagMode = false;       // tagMode survives the redraw after each new box
  const body = h("div");
  const onKey = (e) => {
    if (e.target && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
    if (e.key === "ArrowLeft" && i > 0) { i--; draw(); }
    if (e.key === "ArrowRight" && i < items.length - 1) { i++; draw(); }
  };
  document.addEventListener("keydown", onKey);
  const m = openModal("Photo", body, { wide: true, noFocus: true, onClose: () => { document.removeEventListener("keydown", onKey); if (changed) rerender(); } });
  const setTitle = (t) => { const s = m.el.querySelector("h3 span"); if (s) s.textContent = t; };
  // an Undo from the toast changes things behind this dialog: reload what it shows
  const refresh = async (ids) => {
    for (const id of ids) {
      try {
        const fresh = await api(`api/media/${encodeURIComponent(id)}`);
        const k = items.findIndex((x) => x.id === id);
        if (k >= 0 && !fresh.deleted) items[k] = fresh;
      } catch (e) { /* gone */ }
    }
    if (items[i]) draw();
  };
  const note = (res, msg, ids) => toast(msg, { undo: res && res.batchId, onUndone: () => refresh(ids) });
  function draw() {
    const it = items[i];
    setTitle(it.kind === "photo" ? "Photo" : "Document");
    const fileUrl = `api/media/${encodeURIComponent(it.id)}/file?size=original`;
    const view = it.kind === "photo" ? photoTagger(it, {
      tagging: tagMode, onTagMode: (v) => { tagMode = v; },
      onChange: (res, msg) => { items[i] = res.media; changed = true; note(res, msg, [it.id]); draw(); },
      onOpen: (pid) => { m.close(); go(`person/${pid}`); },
      onProfile: async (rg) => {
        try {
          const res = await api(`api/people/${rg.personId}/photo`, { method: "PUT", body: { mediaId: it.id, regionId: rg.id } });
          const fresh = await api(`api/media/${encodeURIComponent(it.id)}`);
          items[i] = fresh; changed = true;
          note(res, `Profile photo of ${rg.name} set`, [it.id]); draw();
        } catch (x) { fail(x); }
      },
    }) : h("div", { class: "doc-big" }, h("div", { class: "doc-icon" }, "📄"), h("div", null, it.title || "Document"),
        h("a", { class: "btn-secondary btn-small", href: fileUrl, download: downloadName(it) }, "Download PDF"));
    const nav = items.length > 1 ? h("div", { class: "lightbox-nav" },
      h("button", { type: "button", class: "btn-ghost btn-small", disabled: i === 0, onclick: () => { i--; draw(); } }, "‹ Previous"),
      h("span", { class: "hint" }, `${i + 1} of ${items.length}`),
      h("button", { type: "button", class: "btn-ghost btn-small", disabled: i === items.length - 1, onclick: () => { i++; draw(); } }, "Next ›")) : null;
    const title = h("input", { maxlength: 200, value: it.title || "", placeholder: "What's happening, who's there…" });
    const date = dateInput(it.date);
    const desc = h("textarea", { maxlength: 5000, rows: 3, placeholder: "Notes about this photo" }, it.description || "");
    const err = h("div", { class: "error-text" });
    const replace = (res) => { items[i] = res.media; changed = true; note(res, "Saved", [it.id]); draw(); };
    const save = h("button", { type: "button", class: "btn-primary btn-small", onclick: async () => {
      err.textContent = "";
      try { replace(await api(`api/media/${it.id}`, { method: "PATCH", body: { title: title.value.trim() || null, date: date.value(), description: desc.value.trim() || null } })); }
      catch (x) { err.textContent = x.message; }
    } }, "Save details");
    const tags = h("div", { class: "tag-list" }, ...it.links.map((l) => h("span", { class: "tag-chip" },
      l.personId && !l.eventId ? h("button", { type: "button", class: "link-btn", onclick: () => { m.close(); go(`person/${l.personId}`); } }, l.label) : h("span", null, l.label),
      it.kind === "photo" && l.personId && !l.eventId ? (it.profileOf.includes(l.personId)
        ? h("span", { class: "chip accent", title: "Their profile photo" }, "★ profile")
        : h("button", { type: "button", class: "icon-btn", title: "Use as their profile photo", "aria-label": `Use as ${l.label}'s profile photo`, onclick: async () => {
          try {
            const res = await api(`api/people/${l.personId}/photo`, { method: "PUT", body: { mediaId: it.id } });
            // it's no longer the profile photo on whichever photo was before
            const was = items.filter((x) => x !== it && x.profileOf.includes(l.personId));
            was.forEach((x) => { x.profileOf = x.profileOf.filter((id) => id !== l.personId); });
            it.profileOf.push(l.personId); changed = true;
            note(res, "Profile photo set", [it.id, ...was.map((x) => x.id)]); draw();
          } catch (x) { fail(x); }
        } }, "☆")) : null,
      h("button", { type: "button", class: "icon-btn", title: "Remove this tag", "aria-label": `Remove ${l.label}`, onclick: async () => {
        try { replace(await api(`api/media/${it.id}/links/${l.id}`, { method: "DELETE" })); } catch (x) { fail(x); }
      } }, "✕"))));
    if (!it.links.length) tags.appendChild(h("span", { class: "hint" }, "Nobody tagged yet."));
    const picker = personPicker({ placeholder: "Tag someone in this " + (it.kind === "photo" ? "photo" : "document") + "…",
      exclude: it.links.filter((l) => l.personId && !l.eventId).map((l) => l.personId),
      onChange: async (p) => { if (!p) return; try { replace(await api(`api/media/${it.id}/links`, { method: "POST", body: { personId: p.id } })); } catch (x) { fail(x); } } });
    const del = h("button", { type: "button", class: "btn-danger btn-small", onclick: async () => {
      if (!await confirmDialog("Delete", `Move this ${it.kind} to the trash? ${restoreHint("it")}`, "Delete", true)) return;
      try {
        const res = await api(`api/media/${it.id}`, { method: "DELETE" });
        saved(res, "Moved to trash");
        changed = true;
        items.splice(i, 1);
        if (!items.length) { m.close(); return; }
        i = Math.min(i, items.length - 1);
        draw();
      } catch (x) { fail(x); }
    } }, "🗑 Delete");
    mount(body,
      h("div", { class: "lightbox" }, view), nav,
      h("h4", { class: "field-label", style: "margin-top:12px" }, "People"), tags, h("div", { style: "margin-top:6px" }, picker.el),
      h("h4", { class: "field-label", style: "margin-top:14px" }, "Details"),
      h("div", { class: "form-row" }, field("Title", title, "wide")),
      h("div", { class: "form-row" }, field(it.kind === "photo" ? "Date taken" : "Date", date.el, "wide")),
      field("Notes", desc, "wide"), err,
      h("div", { class: "actions" }, del, h("span", { class: "spacer" }),
        it.kind === "photo" && feat("photo_fixes") ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => fixModal(it, (res) => { items[i] = res.media; changed = true; note(res, "Photo fixed", [it.id]); draw(); }) },
          it.edit ? "🛠 Fixed — change" : "🛠 Fix photo") : null,
        h("a", { class: "btn-ghost btn-small", href: fileUrl, download: downloadName(it) }, "⬇ Original"), save),
      h("div", { class: "hint" }, `Added ${fmtWhen(it.createdAt)} · ${fmtBytes(it.size)}${it.width ? ` · ${it.width}×${it.height}` : ""}`));
  }
  draw();
}

/* Photo fixes (§13.19): rotate, straighten with a grid, crop, auto contrast — the original is kept. */
function fixModal(it, after) {
  const e = Object.assign({ rotate: 0, angle: 0, crop: null, autocontrast: false }, it.edit || {});
  const img = h("img", { class: "fix-img", alt: "Preview", draggable: "false" });
  const grid = h("div", { class: "fix-grid" });
  const cropEl = h("div", { class: "fix-crop", hidden: true });
  const stage = h("div", { class: "fix-stage" }, img, grid, cropEl);
  let cropping = false;
  const refresh = debounce(() => {
    const shown = Object.assign({}, e, cropping ? { crop: null } : {});
    img.src = `api/media/${encodeURIComponent(it.id)}/preview?edit=${encodeURIComponent(JSON.stringify(shown))}`;
  }, 150);
  const angle = h("input", { type: "range", min: -15, max: 15, step: 0.5, value: e.angle, "aria-label": "Straighten" });
  const angleLbl = h("span", { class: "hint" });
  const syncAngle = () => { angleLbl.textContent = `${Number(e.angle).toFixed(1)}°`; };
  angle.addEventListener("input", () => { e.angle = Number(angle.value); syncAngle(); grid.hidden = false; refresh(); });
  const auto = h("input", { type: "checkbox", checked: e.autocontrast });
  auto.addEventListener("change", () => { e.autocontrast = auto.checked; refresh(); });
  const cropBtn = h("button", { type: "button", class: "btn-secondary btn-small" });
  const pct = (v) => `${(v * 100).toFixed(2)}%`;
  const drawCrop = () => {
    cropBtn.textContent = cropping ? "✓ Done cropping" : e.crop ? "Change crop" : "✂ Crop";
    cropEl.hidden = !cropping || !e.crop;
    stage.classList.toggle("cropping", cropping);
    if (e.crop) { cropEl.style.left = pct(e.crop.x); cropEl.style.top = pct(e.crop.y); cropEl.style.width = pct(e.crop.w); cropEl.style.height = pct(e.crop.h); }
  };
  cropBtn.addEventListener("click", () => { cropping = !cropping; drawCrop(); refresh(); });
  let start = null;
  const at = (ev) => { const r = img.getBoundingClientRect(); return { x: Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((ev.clientY - r.top) / r.height, 0), 1) }; };
  stage.addEventListener("pointerdown", (ev) => { if (!cropping) return; ev.preventDefault(); start = at(ev); stage.setPointerCapture(ev.pointerId); });
  stage.addEventListener("pointermove", (ev) => {
    if (!cropping || !start) return;
    const p = at(ev);
    e.crop = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    drawCrop();
  });
  stage.addEventListener("pointerup", () => { start = null; if (e.crop && (e.crop.w < 0.05 || e.crop.h < 0.05)) e.crop = null; drawCrop(); });
  const turn = (d) => { e.rotate = (e.rotate + d + 360) % 360; e.crop = null; drawCrop(); refresh(); };
  const err = h("div", { class: "error-text" });
  const m = openModal("Fix photo", h("div", null,
    h("div", { class: "lightbox" }, stage),
    h("div", { class: "fix-tools" },
      h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => turn(270) }, "⟲ Left"),
      h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => turn(90) }, "⟳ Right"),
      h("label", { class: "fix-angle" }, "Straighten", angle, angleLbl),
      cropBtn,
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { e.crop = null; cropping = false; drawCrop(); refresh(); } }, "No crop"),
      h("label", { class: "check-row" }, auto, "Auto contrast (faded scans)")),
    h("p", { class: "hint" }, "The original stays as it was — you can put it back any time. Tags on faces move with the picture."), err,
    h("div", { class: "actions" },
      it.edit ? h("button", { type: "button", class: "btn-ghost", onclick: async () => {
        try { const res = await api(`api/media/${it.id}/edit`, { method: "PUT", body: { edit: null } }); m.close(); after(res); } catch (x) { err.textContent = x.message; }
      } }, "Revert to original") : null,
      h("span", { class: "spacer" }),
      h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"),
      h("button", { type: "button", class: "btn-primary", onclick: async () => {
        try { const res = await api(`api/media/${it.id}/edit`, { method: "PUT", body: { edit: e } }); m.close(); after(res); } catch (x) { err.textContent = x.message; }
      } }, "Save"))), { wide: true, noFocus: true });
  syncAngle(); drawCrop(); refresh();
}

/* A photo with tag boxes (§13.2): hover or tap a box for the name; "Tag a face"
   to drag a new box and say who it is; a selected box can become their profile photo. */
function photoTagger(it, opts) {
  const img = h("img", { class: "lightbox-img", src: `api/media/${encodeURIComponent(it.id)}/file?size=1024`, alt: it.title || "Photo", draggable: "false" });
  const layer = h("div", { class: "tag-layer" });
  const stage = h("div", { class: "tag-stage" }, img, layer);
  const bar = h("div", { class: "tag-bar" });
  let tagging = !!opts.tagging, selected = null, drawing = null;
  if (tagging) stage.classList.add("tagging");
  const setTagging = (v) => { tagging = v; if (opts.onTagMode) opts.onTagMode(v); stage.classList.toggle("tagging", v); };
  const pct = (v) => `${(v * 100).toFixed(3)}%`;
  const place = (el, b) => { el.style.left = pct(b.x); el.style.top = pct(b.y); el.style.width = pct(b.w); el.style.height = pct(b.h); };
  const drawBoxes = () => {
    clear(layer);
    for (const rg of (it.regions || []).filter((r) => r.visible !== false)) {
      const box = h("button", { type: "button", class: "tag-box" + (selected === rg.id ? " selected" : "") + (rg.profile ? " profile" : ""),
        "aria-label": rg.name, title: rg.name, onclick: (e) => { e.stopPropagation(); if (tagging) return; selected = selected === rg.id ? null : rg.id; drawBoxes(); drawBar(); } },
        h("span", { class: "tag-name" }, rg.name + (rg.profile ? " ★" : "")));
      place(box, rg);
      layer.appendChild(box);
    }
    if (drawing) { const d = h("div", { class: "tag-box drawing" }); place(d, drawing); layer.appendChild(d); }
  };
  const drawBar = () => {
    const rg = (it.regions || []).find((r) => r.id === selected);
    if (tagging) {
      mount(bar, h("span", { class: "hint" }, "Drag a box around a face, then say who it is."),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { setTagging(false); drawing = null; drawBoxes(); drawBar(); } }, "Done"));
      return;
    }
    mount(bar,
      feat("photo_tagging") ? h("button", { type: "button", class: "btn-secondary btn-small", onclick: () => { setTagging(true); selected = null; drawBoxes(); drawBar(); } }, "▢ Tag a face") : null,
      rg ? h("span", { class: "tag-sel" }, h("strong", null, rg.name),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => opts.onOpen(rg.personId) }, "Open"),
        rg.profile ? h("span", { class: "chip accent" }, "★ their profile photo")
          : h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => opts.onProfile(rg) }, "★ Use as profile photo"),
        h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
          try { opts.onChange(await api(`api/media/${it.id}/regions/${rg.id}`, { method: "DELETE" }), "Box removed"); } catch (x) { fail(x); }
        } }, "Remove box"))
        : (it.regions || []).length ? h("span", { class: "hint" }, "Tap a box to see who it is.") : null);
  };
  const at = (e) => {
    const r = layer.getBoundingClientRect();
    return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) };
  };
  let start = null;
  layer.addEventListener("pointerdown", (e) => {
    if (!tagging) return;
    e.preventDefault();
    start = at(e);
    layer.setPointerCapture(e.pointerId);
  });
  layer.addEventListener("pointermove", (e) => {
    if (!tagging || !start) return;
    const p = at(e);
    drawing = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
    drawBoxes();
  });
  layer.addEventListener("pointerup", () => {
    if (!tagging || !start) return;
    start = null;
    const box = drawing;
    if (!box || box.w < 0.02 || box.h < 0.02) { drawing = null; drawBoxes(); return; }
    const picker = personPicker({ placeholder: "Who is this?", onChange: async (p) => {
      if (!p) return;
      try { const res = await api(`api/media/${it.id}/regions`, { method: "POST", body: { personId: p.id, ...box } }); drawing = null; opts.onChange(res, `Tagged ${p.name}`); }
      catch (x) { fail(x); }
    } });
    mount(bar, h("span", { class: "hint" }, "Who is in the box?"), picker.el,
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { drawing = null; drawBoxes(); drawBar(); } }, "Cancel"));
    setTimeout(() => picker.focus(), 30);
  });
  drawBoxes();
  drawBar();
  return h("div", { class: "tagger" }, stage, bar);
}

/* The Unsorted queue (§13.10): what came in through the inbox folder. */
async function viewUnsorted() {
  const [r, st] = await Promise.all([api("api/media?unsorted=true&page_size=200"), api("api/inbox/status")]);
  const items = r.items;
  const chosen = new Set();
  const bar = h("div", { class: "bulk-bar" });
  const grid = h("div", { class: "media-grid" });
  const drawBar = () => {
    const who = personPicker({ placeholder: "Tag someone in the selected…", onChange: async (p) => {
      if (!p) return;
      try { const res = await api("api/media/bulk-link", { method: "POST", body: { ids: [...chosen], personId: p.id } }); saved(res, `Tagged ${p.name}`); rerender(); } catch (x) { fail(x); }
    } });
    mount(bar, h("span", { class: "hint" }, chosen.size ? `${chosen.size} selected` : "Select photos to act on several at once"),
      chosen.size ? who.el : null,
      chosen.size ? h("button", { type: "button", class: "btn-primary btn-small", onclick: async () => {
        try { const res = await api("api/media/sorted", { method: "POST", body: { ids: [...chosen] } }); saved(res, "Moved out of Unsorted"); rerender(); } catch (x) { fail(x); }
      } }, "✓ Done") : null,
      chosen.size ? h("button", { type: "button", class: "btn-danger btn-small", onclick: async () => {
        if (!await confirmDialog("Delete", `Move ${plural(chosen.size, "file")} to the trash?`, "Delete", true)) return;
        for (const id of chosen) { try { await api(`api/media/${id}`, { method: "DELETE" }); } catch (x) { fail(x); } }
        toast("Moved to trash"); rerender();
      } }, "🗑 Delete") : null,
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { if (chosen.size === items.length) chosen.clear(); else items.forEach((m) => chosen.add(m.id)); draw(); } },
        chosen.size === items.length && items.length ? "Select none" : "Select all"));
  };
  const draw = () => {
    clear(grid);
    items.forEach((m, i) => {
      const box = h("input", { type: "checkbox", checked: chosen.has(m.id), "aria-label": `Select ${m.title || "photo"}` });
      box.addEventListener("change", () => { if (box.checked) chosen.add(m.id); else chosen.delete(m.id); drawBar(); });
      grid.appendChild(h("div", { class: "unsorted-tile" }, mediaThumb(m, { onclick: () => mediaModal(items, i) }), box,
        m.suggest ? h("button", { type: "button", class: "chip accent suggest", title: "Suggested from the folder name", onclick: async () => {
          try { const res = await api(`api/media/${m.id}/links`, { method: "POST", body: { personId: m.suggest.id } }); saved(res, `Tagged ${m.suggest.name}`); rerender(); } catch (x) { fail(x); }
        } }, `+ ${m.suggest.name}?`) : null));
    });
    if (!items.length) grid.appendChild(h("div", { class: "empty", style: "grid-column:1/-1" }, "Nothing waiting. 🎉"));
    drawBar();
  };
  draw();
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Unsorted ", h("span", { class: "hint" }, plural(items.length, "file"))),
      h("div", { class: "person-actions" },
        h("button", { type: "button", class: "btn-ghost", onclick: () => go("photos") }, "← All photos"),
        st.enabled ? h("button", { type: "button", class: "btn-secondary", onclick: async () => {
          try { const res = await api("api/inbox/scan", { method: "POST" });
            toast(`${plural(res.imported, "new file")}${res.waiting ? `, ${res.waiting} still copying` : ""}${res.rejected || res.duplicates ? `, ${res.rejected + res.duplicates} set aside in _rejected` : ""}`); rerender(); }
          catch (x) { fail(x); }
        } }, "Check now") : null)),
    h("p", { class: "hint" }, st.enabled ? ["Drop photos or PDFs into ", h("code", null, st.folder), " — from a PC over Samba, the NAS, or a phone's file app. They're picked up every 2 minutes. A subfolder's name becomes the title and suggests a person (",
      h("code", null, "inbox/Venkat/"), "). Files that can't be used go to ", h("code", null, "inbox/_rejected"), " with a note."]
      : "The inbox folder is turned off (Admin → App settings)."),
    bar, grid);
}

async function viewPhotos() {
  const kind = h("select", { "aria-label": "Kind" }, h("option", { value: "" }, "Photos & documents"), h("option", { value: "photo" }, "Photos"), h("option", { value: "document" }, "Documents"));
  const sort = h("select", { "aria-label": "Sort" }, h("option", { value: "added" }, "Recently added"), h("option", { value: "date" }, "By date"));
  const q = h("input", { type: "search", placeholder: "Search titles and notes…", "aria-label": "Search" });
  const unlinked = h("input", { type: "checkbox" });
  let person = null;
  const who = personPicker({ placeholder: "Anyone", onChange: (p) => { person = p; reload(); } });
  who.el.classList.add("tree-search");
  const grid = h("div", { class: "media-grid" });
  const countEl = h("span", { class: "hint" });
  const more = h("div", { class: "actions", style: "justify-content:center" });
  let page = 1, items = [];
  const load = async (append) => {
    const params = new URLSearchParams({ page: String(page), page_size: "60", sort: sort.value });
    if (kind.value) params.set("kind", kind.value);
    if (person) params.set("personId", person.id);
    if (q.value.trim()) params.set("q", q.value.trim());
    if (unlinked.checked) params.set("unlinked", "true");
    const r = await api(`api/media?${params}`);
    if (!append) { items = []; clear(grid); }
    const start = items.length;
    items.push(...r.items);
    r.items.forEach((m, k) => grid.appendChild(mediaThumb(m, { onclick: () => mediaModal(items, start + k) })));
    if (!items.length) grid.appendChild(h("div", { class: "empty", style: "grid-column:1/-1" }, "No photos or documents yet. Drop files here or use Upload."));
    countEl.textContent = plural(r.total, r.total === 1 ? "file" : "file");
    mount(more, r.page * r.pageSize < r.total ? h("button", { type: "button", class: "btn-ghost", onclick: () => { page++; load(true).catch(fail); } }, "Show more") : null);
  };
  const reload = () => { page = 1; load(false).catch(fail); };
  [kind, sort].forEach((s) => s.addEventListener("change", reload));
  unlinked.addEventListener("change", reload);
  q.addEventListener("input", debounce(reload, 300));
  await load(false);
  const unsortedBtn = feat("inbox") ? h("button", { type: "button", class: "btn-ghost", onclick: () => go("photos/unsorted") }, "📥 Unsorted") : null;
  if (unsortedBtn) api("api/inbox/status").then((st) => { unsortedBtn.textContent = `📥 Unsorted${st.unsorted ? ` (${st.unsorted})` : ""}`; }).catch(() => {});
  const wrap = h("div", { class: "dropzone" },
    h("div", { class: "page-head" }, h("h2", null, "Photos & documents ", countEl), h("div", { class: "person-actions" }, unsortedBtn, uploadButton("⬆ Upload", {}, rerender))),
    h("div", { class: "toolbar" }, q, kind, who.el, sort, h("label", { class: "check-row" }, unlinked, "Not linked to anyone")),
    h("p", { class: "hint" }, `Drop photos or PDFs anywhere on this page${state.user && state.user.maxUploadMb ? ` (up to ${state.user.maxUploadMb} MB each)` : ""}. Location and camera details are removed from photos when they're saved.`),
    grid, more);
  return dropZone(wrap, {}, rerender);
}

async function personPhotos(p) {
  const r = await api(`api/media?personId=${encodeURIComponent(p.id)}&page_size=200&sort=date`);
  const items = r.items;
  const grid = h("div", { class: "media-grid" }, ...items.map((m, k) => mediaThumb(m, { profile: m.id === p.photo, onclick: () => mediaModal(items, k) })));
  if (!items.length) grid.appendChild(h("div", { class: "empty", style: "grid-column:1/-1" }, `No photos or documents of ${p.name} yet. Drop files here or use Upload.`));
  const card = h("div", { class: "card dropzone" },
    h("h3", null, "Photos & documents", uploadButton("⬆ Upload", { personId: p.id }, rerender)),
    h("p", { class: "hint" }, "Everything uploaded here is tagged with " + p.name + ". Open a photo to tag more people or make it a profile photo."),
    grid);
  return dropZone(card, { personId: p.id }, rerender);
}

// ---------- stories ----------
function storyModal(p, story) {
  const title = h("input", { maxlength: 200, value: story ? story.title : "", placeholder: "e.g. How they met, the move to a new country" });
  const body = h("textarea", { maxlength: 50000, rows: 12, placeholder: "Write the story…" }, story ? story.body : "");
  const err = h("div", { class: "error-text" });
  const save = h("button", { type: "submit", class: "btn-primary" }, story ? "Save" : "Add story");
  const f = h("form", null, field("Title", title, "wide"), h("div", { style: "height:10px" }), field("Story", body, "wide"), err,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"), save));
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    const payload = { title: title.value.trim(), body: body.value.trim() };
    if (!payload.title || !payload.body) { err.textContent = "Give the story a title and some text."; return; }
    save.disabled = true;
    try {
      const res = story ? await api(`api/stories/${story.id}`, { method: "PATCH", body: payload })
        : await api(`api/people/${p.id}/stories`, { method: "POST", body: payload });
      m.close(); saved(res); rerender();
    } catch (x) { err.textContent = x.message; save.disabled = false; }
  });
  const m = openModal(story ? "Edit story" : `A story about ${p.name}`, f, { wide: true });
}

async function personStories(p) {
  const stories = await api(`api/people/${p.id}/stories`);
  const list = stories.map((s) => h("div", { class: "card story" },
    h("h3", null, s.title, h("span", { class: "person-actions" },
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => storyModal(p, s) }, "✎ Edit"),
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
        if (!await confirmDialog("Delete story", `Delete “${s.title}”? You can undo it from History.`, "Delete", true)) return;
        try { const res = await api(`api/stories/${s.id}`, { method: "DELETE" }); saved(res, "Deleted"); rerender(); } catch (x) { fail(x); }
      } }, "🗑"))),
    h("div", { class: "hint", style: "margin:-4px 0 8px" }, [s.createdBy ? `by ${s.createdBy}` : null, fmtWhen(s.createdAt)].filter(Boolean).join(" · ")),
    h("div", { class: "bio" }, s.body)));
  return h("div", null,
    h("div", { class: "actions", style: "justify-content:flex-start;margin:0 0 12px" },
      h("button", { type: "button", class: "btn-primary btn-small", onclick: () => storyModal(p, null) }, "+ Add a story")),
    list.length ? list : h("div", { class: "card" }, h("div", { class: "empty" }, `No stories about ${p.name} yet — memories, how they met, what they were like…`)));
}

// ---------- upcoming ----------
/* 🔔 on an Upcoming entry: that person's switch; for a couple, both partners
   in one undoable change (the anniversary is sent when either is on). */
function upcomingBell(it) {
  const on = !!it.remind;
  const names = it.people.map((p) => p.given || p.name).join(" & ");
  const label = on ? `Reminders on for ${names} — tap to turn off` : `Reminders off for ${names} — tap to turn on`;
  return h("button", { type: "button", class: "icon-btn bell-toggle" + (on ? " on" : ""), title: label, "aria-label": label, "aria-pressed": String(on),
    onclick: async (e) => {
      e.stopPropagation();
      try {
        const res = it.people.length === 1
          ? await api(`api/people/${it.people[0].id}`, { method: "PATCH", body: { remind: !on } })
          : await api("api/reminders/people", { method: "PUT", body: { ids: it.people.map((p) => p.id), remind: !on } });
        saved(res, `Reminders ${on ? "off" : "on"} for ${names}`);
        rerender();
      } catch (x) { fail(x); }
    } }, on ? "🔔" : "🔕");
}

async function viewUpcoming() {
  const scope = lsGet("upScope") === "all" ? "all" : "close", rem = lsGet("upRemembrance") === "1";
  const days = [30, 60, 90, 366].includes(+lsGet("upDays")) ? +lsGet("upDays") : 60;
  const tith = feat("tithi") && lsGet("upTithi") !== "0";
  const r = await api(`api/upcoming?days=${days}&scope=${scope}&remembrance=${rem}&tithi=${tith}`);
  const scopeSel = r.meSet ? segmented([["close", "Close family"], ["all", "Everyone"]], scope, (k) => { lsSet("upScope", k); rerender(); }).el : null;
  const daysSel = h("select", { "aria-label": "How far ahead" }, ...[[30, "Next 30 days"], [60, "Next 60 days"], [90, "Next 3 months"], [366, "Next 12 months"]].map(([v, l]) => h("option", { value: String(v) }, l)));
  daysSel.value = String(days);
  daysSel.addEventListener("change", () => { lsSet("upDays", daysSel.value); rerender(); });
  const remBox = h("input", { type: "checkbox", checked: rem });
  remBox.addEventListener("change", () => { lsSet("upRemembrance", remBox.checked ? "1" : "0"); rerender(); });
  const tithiBox = h("input", { type: "checkbox", checked: tith });
  tithiBox.addEventListener("change", () => { lsSet("upTithi", tithiBox.checked ? "1" : "0"); rerender(); });
  const icon = { birthday: "🎂", anniversary: "💍", remembrance: "🕯", tithi: "🪔", milestone: "🎉" };
  const groups = [];
  let current = null;
  for (const it of r.items) {
    const d = new Date(it.date + "T12:00:00");
    const key = d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    if (!current || current.key !== key) { current = { key, items: [] }; groups.push(current); }
    current.items.push(it);
  }
  const when = (n) => n === 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`;
  const rows = groups.map((g) => h("div", { class: "card" }, h("h3", null, g.key),
    ...g.items.map((it) => {
      const d = new Date(it.date + "T12:00:00");
      const rel = it.people.length === 1 ? relText(it.people[0].relationship) : null;
      return h("div", { class: "up-row" + (it.daysAway === 0 ? " today" : ""), role: "button", tabindex: "0",
        onclick: () => go(`person/${it.people[0].id}`), onkeydown: (e) => { if (e.key === "Enter" && e.target === e.currentTarget) go(`person/${it.people[0].id}`); } },
        h("div", { class: "up-date" }, h("b", null, String(d.getDate())), h("span", null, d.toLocaleDateString(undefined, { weekday: "short" }))),
        h("div", { class: "up-avatars" }, ...it.people.map((p) => avatar(p, "sm"))),
        h("div", { class: "grow" }, h("div", { class: "nm" }, `${icon[it.kind]} ${it.title}`, it.milestone ? h("span", { class: "chip milestone" }, "🎉 " + it.milestone) : null),
          h("div", { class: "sub" }, [rel, when(it.daysAway)].filter(Boolean).join(" · "))),
        feat("reminders") ? upcomingBell(it) : null);
    })));
  return h("div", null,
    h("div", { class: "page-head" }, h("h2", null, "Upcoming"), h("div", { class: "toolbar", style: "margin:0" }, scopeSel, daysSel,
      h("label", { class: "check-row" }, remBox, "Remembrance days"), feat("tithi") ? h("label", { class: "check-row" }, tithiBox, "🪔 Tithi days") : null)),
    !r.meSet ? h("p", { class: "hint" }, "Showing everyone. Set “This is me” in Settings to narrow this to your close family and see relationships.") : null,
    r.items.length && feat("reminders") ? h("p", { class: "hint" }, "🔔 = included in reminders (tap a bell to switch). Your own reminders are in ",
      h("button", { type: "button", class: "link-btn", onclick: () => go("settings") }, "Settings"), ".") : null,
    rows.length ? rows : h("div", { class: "card" }, h("div", { class: "empty" }, "Nothing coming up. Birthdays need at least a day and a month to show here.")));
}

// ---------- export (§13.6) ----------
const EXPORT_EVENT_GROUPS = [["birth_death", "Births, deaths and burials"], ["marriage", "Marriages, engagements, divorces"],
  ["education_occupation", "Education and work"], ["residence", "Where they lived"], ["migration", "Moving country"],
  ["military", "Military service"], ["religious", "Religious events & ceremonies"], ["other", "Other events"]];

function exportDefaults() {
  return {
    scope: { type: "whole", personId: null, generations: 4, partners: true, start: [], walk: "connected", partnerRule: "stop", stops: [], leaveOut: [], ids: [] },
    living: "limited", maidenNames: true, otherNames: true, dates: "full", places: true,
    events: Object.fromEntries(EXPORT_EVENT_GROUPS.map(([k]) => [k, k !== "other"])),
    photos: "all", photoSize: "web", documents: false, stories: true, notes: false, relationships: true, relativeTo: null,
    gender: true, deaths: true, familyDetails: true, customFields: {}, contacts: false, sources: false,
    site: { title: "Our family", intro: "", homePersonId: null, coverMediaId: null, theme: "auto", pages: { tree: true, people: true, surnames: true, places: true } },
  };
}
/* Saved options onto today's defaults: keys we no longer know are dropped (the server rejects them). */
function exportMerge(base, over) {
  if (!over || typeof over !== "object" || Array.isArray(over)) return base;
  const out = Array.isArray(base) ? base : { ...base };
  for (const k of Object.keys(base)) {
    if (!(k in over)) continue;
    const b = base[k], v = over[k];
    if (b && typeof b === "object" && !Array.isArray(b) && !Object.keys(b).length) out[k] = (v && typeof v === "object" && !Array.isArray(v)) ? { ...v } : b;   // open-ended maps (customFields)
    else if (b && typeof b === "object" && !Array.isArray(b)) out[k] = exportMerge(b, v);
    else if (Array.isArray(b)) out[k] = Array.isArray(v) ? v.slice() : b;
    else if (b === null || typeof v === typeof b) out[k] = v;
  }
  return out;
}

/* Every detail at once: "names" (only the name), "standard" (the defaults) or "all". */
function exportDetailLevel(o, level) {
  const d = exportDefaults();
  const on = level !== "names";
  const all = level === "all";
  Object.assign(o, {
    maidenNames: on, otherNames: on, dates: on ? "full" : "none", places: on,
    events: Object.fromEntries(EXPORT_EVENT_GROUPS.map(([k]) => [k, all || (on && d.events[k])])),
    photos: on ? "all" : "none", documents: all, stories: on, notes: all, relationships: on,
    gender: on, deaths: on, familyDetails: on,
  });
}
/* Which of the quick levels the current choices match, if any. */
function exportDetailMatch(o) {
  const keys = ["maidenNames", "otherNames", "dates", "places", "events", "photos", "documents", "stories", "notes", "relationships", "gender", "deaths", "familyDetails"];
  for (const level of ["names", "standard", "all"]) {
    const t = { ...exportDefaults() };
    exportDetailLevel(t, level);
    if (keys.every((k) => JSON.stringify(t[k]) === JSON.stringify(o[k]))) return level;
  }
  return null;
}

async function viewExport() {
  const [last, presetsIn, all, cf] = await Promise.all([api("api/export/last?format=site"), api("api/export/presets"), api("api/tree/all"),
    feat("custom_fields") ? api("api/custom-fields").catch(() => ({ fields: [] })) : { fields: [] }]);
  const personFields = cf.fields.filter((f) => f.appliesTo === "person");
  let presets = presetsIn;
  let draft = null;
  try { draft = JSON.parse(sessionStorageGet("exportDraft") || "null"); } catch (e) { draft = null; }
  let o = exportMerge(exportDefaults(), draft || last.options || {});
  let presetName = sessionStorageGet("exportPreset") || "";
  const people = {};
  (all.people || []).forEach((p) => { people[p.id] = p; });
  const meId = state.user.mePersonId || all.meId || null;
  const nameOf = (id) => (people[id] ? people[id].name : "Someone no longer in the tree");
  const pickerInitial = (id) => (id && people[id] ? people[id] : null);
  let pv = null;                 // the last preview
  let chart = null;

  // ----- live preview -----
  const statsEl = h("div", { class: "export-stats" }, "Working out who's included…");
  const warnEl = h("div", { class: "warnings" });
  const jobEl = h("div", { class: "export-job" });
  const exportBtn = h("button", { type: "button", class: "btn-primary" }, "Export website");
  let pvSeq = 0;
  const runPreview = async () => {
    const seq = ++pvSeq;
    try {
      const r = await api("api/export/preview", { method: "POST", body: { format: "site", options: o } });
      if (seq !== pvSeq) return;
      pv = r;
      pv.includedSet = new Set(r.included);
      pv.placeholderSet = new Set(r.placeholderIds);
      const bits = [plural(r.people, "person")];
      if (r.living) bits.push(`${r.living} living${r.limited ? ` (${r.limited} names only)` : ""}`);
      if (r.placeholders) bits.push(`${r.placeholders} shown as “Private”`);
      if (o.photos !== "none") bits.push(plural(r.photos, "photo"));
      if (o.documents) bits.push(plural(r.documents, "document"));
      bits.push(`about ${fmtBytes(r.bytesEstimate)}`);
      statsEl.textContent = bits.join(" · ");
      mount(warnEl, ...r.warnings.map((w) => h("div", null, "⚠ " + w)));
      exportBtn.disabled = !r.people || !!state.exportJob;
      if (chart) chart.restyle();
    } catch (e) {
      if (seq !== pvSeq) return;
      statsEl.textContent = e.message;
      exportBtn.disabled = true;
    }
  };
  const previewSoon = debounce(runPreview, 350);
  let quickEl = null;              // the Names only / Standard / Everything switch
  const changed = () => {
    if (quickEl) {
      const m = exportDetailMatch(o);
      quickEl.querySelectorAll("button").forEach((b, i) => {
        const on = ["names", "standard", "all"][i] === m;
        b.className = on ? "active" : "";
        b.setAttribute("aria-checked", String(on));
      });
    }
    sessionStorageSet("exportDraft", JSON.stringify(o));
    previewSoon();
  };

  // ----- the chart that shows (and edits) who's in -----
  const sc = () => o.scope;
  const inList = (list, id) => list.includes(id);
  const toggle = (list, id) => { const i = list.indexOf(id); if (i >= 0) list.splice(i, 1); else list.push(id); };
  const leaveOf = (id) => sc().leaveOut.find((x) => x.id === id);
  const clearCut = (id) => {
    sc().stops = sc().stops.filter((x) => x !== id);
    sc().leaveOut = sc().leaveOut.filter((x) => x.id !== id);
  };
  const cardClass = (p) => {
    const s = sc();
    if (s.type === "branch" && inList(s.start, p.id)) return "ex-start";
    if (s.type === "branch" && inList(s.stops, p.id)) return "ex-stop";
    if (s.type === "branch" && leaveOf(p.id)) return "ex-cut";
    if (!pv) return "";
    if (pv.placeholderSet.has(p.id)) return "ex-private";
    return pv.includedSet.has(p.id) ? "" : "ex-off";
  };
  const badge = (p) => {
    const s = sc();
    if (s.type === "branch" && inList(s.start, p.id)) return "▶ start";
    if (s.type === "branch" && inList(s.stops, p.id)) return "✂ stop";
    const lo = s.type === "branch" && leaveOf(p.id);
    if (lo) return lo.keepChain ? "⛔ private" : "⛔ left out";
    if (pv && pv.placeholderSet.has(p.id)) return "🔒 private";
    return null;
  };
  const canvas = h("div", { class: "tree-canvas export-canvas" });
  const chartBox = h("div", { class: "export-chart" },
    h("div", { class: "export-legend" },
      h("span", null, h("i", { class: "lg in" }), "Included"),
      h("span", null, h("i", { class: "lg off" }), "Not included"),
      h("span", null, "▶ Start"), h("span", null, "✂ Stop here"), h("span", null, "⛔ Left out"), h("span", null, "🔒 Shown as “Private”")),
    canvas);
  const cardMenu = (id) => {
    const s = sc();
    const p = people[id];
    if (!p) return;
    let m = null;
    const act = (fn) => () => { fn(); m.close(); redrawScope(); changed(); if (chart) chart.restyle(); };
    const btn = (label, fn, cls = "btn-secondary") => h("button", { type: "button", class: cls, onclick: act(fn) }, label);
    const items = [];
    if (s.type !== "branch" && s.type !== "selected") {
      items.push(btn(`Export a branch starting from ${p.name}`, () => { s.type = "branch"; s.start = [id]; clearCut(id); rebuild(); }, "btn-primary"));
    } else if (s.type === "selected") {
      items.push(btn(inList(s.ids, id) ? "Remove from the export" : "Add to the export", () => toggle(s.ids, id), "btn-primary"));
    } else {
      const isStart = inList(s.start, id), isStop = inList(s.stops, id), lo = leaveOf(id);
      if (!isStart) items.push(btn("▶ Start from here too", () => { clearCut(id); s.start.push(id); }));
      else if (s.start.length > 1) items.push(btn("Don't start from here", () => toggle(s.start, id)));
      if (!isStop && !isStart) items.push(btn("✂ Stop here — include them, but not their family beyond", () => { clearCut(id); s.stops.push(id); }, "btn-primary"));
      if (!lo && !isStart) items.push(btn("⛔ Leave them out, and everyone reached only through them", () => { clearCut(id); s.leaveOut.push({ id, keepChain: false }); }));
      if (!(lo && lo.keepChain) && !isStart) items.push(btn("🔒 Leave out, but keep the link as “Private”", () => { clearCut(id); s.leaveOut.push({ id, keepChain: true }); }));
      if (isStop || lo) items.push(btn("Clear this cut", () => clearCut(id), "btn-ghost"));
    }
    items.push(h("button", { type: "button", class: "btn-ghost", onclick: () => { m.close(); go(`person/${id}`); } }, "Open their page"));
    const cutNote = s.type !== "branch" ? "" : inList(s.start, id) ? "The export starts from here." : inList(s.stops, id) ? "Stop point — included, but their family beyond isn't."
      : leaveOf(id) ? (leaveOf(id).keepChain ? "Left out; the link through them shows as “Private”." : "Left out, with everyone reached only through them.") : "";
    const state_ = cutNote || (pv ? (pv.placeholderSet.has(id) ? "Shown as “Private” in this export." : pv.includedSet.has(id) ? "Included in this export." : "Not included in this export.") : "");
    m = openModal(p.name, h("div", null, state_ ? h("p", { class: "hint" }, state_) : null, h("div", { class: "menu-list" }, ...items)));
  };
  const drawChart = () => {
    if (!canvas.isConnected || !all.people || !all.people.length) return;
    const data = { ...all, focus: (sc().start[0] || sc().personId || meId || all.people[0].id) };
    chart = window.FamilyChart.render(canvas, data, {
      whole: true, fitFirst: true, cardClass, badge,
      relLabel: () => "",
      onOpen: cardMenu, onRecenter: cardMenu,
    });
    canvas.appendChild(h("div", { class: "tree-zoom" },
      h("button", { type: "button", "aria-label": "Zoom in", onclick: () => chart.zoomIn() }, "+"),
      h("button", { type: "button", "aria-label": "Zoom out", onclick: () => chart.zoomOut() }, "−"),
      h("button", { type: "button", "aria-label": "Fit everyone", title: "Fit", onclick: () => chart.fit() }, "⤢")));
  };

  // ----- small builders -----
  const check = (label, get, set, hint) => {
    const c = h("input", { type: "checkbox", checked: !!get() });
    c.addEventListener("change", () => { set(c.checked); changed(); });
    return h("label", { class: "check-row" }, c, h("span", null, label, hint ? h("span", { class: "hint" }, " — " + hint) : null));
  };
  const seg = (opts, get, set, after) => segmented(opts, get(), (k) => { set(k); changed(); if (after) after(k); }).el;
  const section = (title, ...kids) => h("section", { class: "card export-sec" }, h("h3", null, title), ...kids);
  const personChips = (ids, onRemove) => h("div", { class: "chips" }, ...ids.map((id) =>
    h("span", { class: "chip big" }, nameOf(id), h("button", { type: "button", class: "icon-btn", "aria-label": `Remove ${nameOf(id)}`, onclick: () => onRemove(id) }, "✕"))));

  // ----- "Who" -----
  const scopeBox = h("div");
  const redrawScope = () => {
    const s = sc();
    const kids = [];
    if (s.type === "whole") kids.push(h("p", { class: "hint" }, "Everyone in the tree. People marked “Keep out of all exports” are still left out."));
    if (["ancestors", "descendants", "both"].includes(s.type)) {
      const pk = personPicker({ initial: pickerInitial(s.personId || meId), placeholder: "Whose family?", onChange: (p) => { s.personId = p ? p.id : null; changed(); } });
      if (!s.personId && meId) s.personId = meId;
      const gen = h("select", { "aria-label": "Generations" }, ...Array.from({ length: 30 }, (_, i) => h("option", { value: String(i + 1) }, String(i + 1))));
      gen.value = String(s.generations);
      gen.addEventListener("change", () => { s.generations = +gen.value; changed(); });
      kids.push(h("div", { class: "form-row" }, field("Starting from", pk.el, "wide")),
        h("div", { class: "form-row" }, field("Generations", gen)),
        check("Include their partners", () => s.partners, (v) => { s.partners = v; }));
    }
    if (s.type === "branch") {
      const addStart = personPicker({ placeholder: "Add someone to start from…", exclude: s.start, onChange: (p) => {
        if (p && !inList(s.start, p.id) && s.start.length < 20) { clearCut(p.id); s.start.push(p.id); redrawScope(); changed(); }
      } });
      const walk = h("select", { "aria-label": "Which way to go" },
        h("option", { value: "connected" }, "Their blood relatives — ancestors, uncles, cousins, descendants"), h("option", { value: "descendants" }, "Their descendants"),
        h("option", { value: "ancestors" }, "Their ancestors"), h("option", { value: "both" }, "Their ancestors and descendants"));
      walk.value = s.walk;
      walk.addEventListener("change", () => { s.walk = walk.value; changed(); });
      const addStop = personPicker({ placeholder: "Stop at…", onChange: (p) => { if (p) { clearCut(p.id); s.stops.push(p.id); redrawScope(); changed(); } } });
      const addOut = personPicker({ placeholder: "Leave out…", onChange: (p) => { if (p) { clearCut(p.id); s.leaveOut.push({ id: p.id, keepChain: false }); redrawScope(); changed(); } } });
      const cuts = h("div", { class: "cut-list" });
      for (const id of s.stops) {
        cuts.appendChild(h("div", { class: "cut-row" }, h("span", { class: "cut-ico" }, "✂"), h("span", { class: "grow" }, h("strong", null, nameOf(id)), h("span", { class: "hint" }, " — included; their family beyond isn't")),
          h("button", { type: "button", class: "icon-btn", "aria-label": "Remove this stop", onclick: () => { clearCut(id); redrawScope(); changed(); } }, "✕")));
      }
      for (const lo of s.leaveOut) {
        const kc = h("input", { type: "checkbox", checked: lo.keepChain });
        kc.addEventListener("change", () => { lo.keepChain = kc.checked; changed(); });
        cuts.appendChild(h("div", { class: "cut-row" }, h("span", { class: "cut-ico" }, "⛔"), h("span", { class: "grow" }, h("strong", null, nameOf(lo.id)), h("span", { class: "hint" }, " — left out"),
          h("label", { class: "check-row small" }, kc, "Keep the link as “Private”")),
          h("button", { type: "button", class: "icon-btn", "aria-label": "Remove this cut", onclick: () => { clearCut(lo.id); redrawScope(); changed(); } }, "✕")));
      }
      if (!s.stops.length && !s.leaveOut.length) cuts.appendChild(h("p", { class: "hint" }, "No cuts yet. Tap someone on the chart below and choose “Stop here” to include them but not their family beyond, or “Leave out” to drop them and everyone reached only through them."));
      kids.push(
        h("div", { class: "field wide" }, "Start from", personChips(s.start, (id) => { toggle(s.start, id); redrawScope(); changed(); }), s.start.length < 20 ? addStart.el : null),
        h("div", { class: "form-row" }, field("Then include", walk, "wide")),
        h("div", { class: "field wide" }, "Partners who married in",
          seg([["stop", "Include them, not their family"], ["follow", "Include their family too"], ["exclude", "Leave them out"]], () => s.partnerRule, (k) => { s.partnerRule = k; }),
          h("p", { class: "hint" }, "“Include their family too” follows every marriage, so it can reach most of the tree — add cuts to stop it where you want.")),
        h("div", { class: "field wide" }, `Cuts (${s.stops.length + s.leaveOut.length})`, cuts,
          h("div", { class: "form-row" }, h("div", { class: "field" }, addStop.el), h("div", { class: "field" }, addOut.el))));
    }
    if (s.type === "selected") {
      const add = personPicker({ placeholder: "Add a person…", exclude: s.ids, onChange: (p) => { if (p && !inList(s.ids, p.id)) { s.ids.push(p.id); redrawScope(); changed(); } } });
      kids.push(h("p", { class: "hint" }, "Only the people you pick — tap them on the chart, or search."),
        personChips(s.ids, (id) => { toggle(s.ids, id); redrawScope(); changed(); }), add.el,
        s.ids.length ? h("button", { type: "button", class: "link-btn", onclick: () => { s.ids = []; redrawScope(); changed(); } }, "Clear all") : null);
    }
    mount(scopeBox, ...kids);
  };

  // ----- website extras -----
  const coverBox = h("div", { class: "cover-pick" });
  const drawCover = () => {
    const id = o.site.coverMediaId;
    mount(coverBox,
      id ? h("img", { src: `api/media/${encodeURIComponent(id)}/file?size=256`, alt: "Cover photo" }) : h("span", { class: "hint" }, "Uses the home person's photo"),
      h("button", { type: "button", class: "btn-ghost btn-small", onclick: chooseCover }, id ? "Change" : "Choose a photo"),
      id ? h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => { o.site.coverMediaId = null; drawCover(); changed(); } }, "Remove") : null);
  };
  async function chooseCover() {
    try {
      const r = await api("api/media?kind=photo&page_size=120");
      let m = null;
      const grid = h("div", { class: "media-grid" }, ...r.items.map((x) => mediaThumb(x, { onclick: () => { o.site.coverMediaId = x.id; m.close(); drawCover(); changed(); } })));
      m = openModal("Cover photo", r.items.length ? grid : h("p", { class: "hint" }, "No photos yet."), { wide: true });
    } catch (e) { fail(e); }
  }

  // ----- presets -----
  const presetSel = h("select", { "aria-label": "Saved choices" });
  const drawPresets = () => {
    mount(presetSel, h("option", { value: "" }, presets.length ? "Saved choices…" : "No saved choices yet"),
      ...presets.map((p) => h("option", { value: p.id }, p.name + (p.createdBy ? ` (${p.createdBy})` : ""))));
    const cur = presets.find((p) => p.name === presetName);
    presetSel.value = cur ? cur.id : "";
    delBtn.disabled = !cur;
  };
  const delBtn = h("button", { type: "button", class: "btn-ghost btn-small", onclick: async () => {
    const cur = presets.find((p) => p.id === presetSel.value);
    if (!cur || !await confirmDialog("Delete saved choices", `Delete “${cur.name}” for everyone?`, "Delete", true)) return;
    try { await api(`api/export/presets/${cur.id}`, { method: "DELETE" }); presets = await api("api/export/presets"); presetName = ""; sessionStorageSet("exportPreset", ""); drawPresets(); toast("Deleted"); }
    catch (e) { fail(e); }
  } }, "Delete");
  presetSel.addEventListener("change", () => {
    const cur = presets.find((p) => p.id === presetSel.value);
    if (!cur) return;
    o = exportMerge(exportDefaults(), cur.options);
    presetName = cur.name;
    sessionStorageSet("exportPreset", presetName);
    rebuild();
    changed();
    toast(`Loaded “${cur.name}”`);
  });
  const saveBtn = h("button", { type: "button", class: "btn-ghost btn-small", onclick: () => {
    const name = h("input", { value: presetName, maxlength: 80, placeholder: "e.g. Dad's side for the reunion" });
    let m = null;
    const save = async () => {
      if (!name.value.trim()) { name.focus(); return; }
      try {
        const r = await api("api/export/presets", { method: "POST", body: { name: name.value, format: "site", options: o } });
        presets = await api("api/export/presets");
        presetName = r.name;
        sessionStorageSet("exportPreset", presetName);
        drawPresets();
        m.close();
        toast("Saved — everyone can use these choices");
      } catch (e) { fail(e); }
    };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    m = openModal("Save these choices", h("div", null,
      field("Name", name, "wide"),
      h("p", { class: "hint" }, "Saved choices are shared with everyone in the family. Using the same name replaces them."),
      h("div", { class: "actions" }, h("button", { type: "button", class: "btn-ghost", onclick: () => m.close() }, "Cancel"),
        h("button", { type: "button", class: "btn-primary", onclick: save }, "Save"))));
  } }, "Save choices…");
  const resetBtn = h("button", { type: "button", class: "link-btn", onclick: () => {
    o = exportDefaults(); presetName = ""; sessionStorageSet("exportPreset", ""); rebuild(); changed(); drawPresets();
  } }, "Reset to defaults");

  // ----- the job -----
  const drawJob = (job) => {
    if (!job) { clear(jobEl); return; }
    if (job.status === "running") {
      mount(jobEl, h("div", { class: "progress" }, h("div", { class: "bar", style: `width:${Math.max(3, job.progress || 0)}%` })),
        h("div", { class: "hint" }, `${job.message || "Working"}…`));
    } else if (job.status === "done") {
      mount(jobEl, h("div", { class: "export-done" },
        h("a", { class: "btn-primary", href: `api/export/${encodeURIComponent(job.id)}/file`, download: job.filename || "family-tree-site.zip" }, `⬇ Download ${job.filename || "zip"}`),
        h("span", { class: "hint" }, `${fmtBytes(job.size || 0)} · unzip it and open index.html. The download is kept for an hour.`)));
    } else {
      mount(jobEl, h("div", { class: "warnings" }, h("div", null, "⚠ " + (job.error || "The export failed."))));
    }
  };
  const poll = async () => {
    const jid = state.exportJob;
    if (!jid) return;
    try {
      const job = await api(`api/export/${encodeURIComponent(jid)}`);
      if (!jobEl.isConnected) return;                 // left the page; picked up again on return
      drawJob(job);
      if (job.status === "running") { setTimeout(poll, 800); return; }
      state.exportJob = null;
      state.exportDone = job.status === "done" ? job : null;
      exportBtn.disabled = !(pv && pv.people);
      if (job.status === "done") toast("Your website is ready to download");
    } catch (e) {
      state.exportJob = null;
      exportBtn.disabled = false;
      drawJob({ status: "failed", error: e.message });
    }
  };
  exportBtn.addEventListener("click", async () => {
    if (!pv || !pv.people) return;
    if (o.living === "full" && pv.living) {
      const ok = await confirmDialog("Include living people in full?",
        `This website will include birth dates, places and photos of ${plural(pv.living, "living person")}. Anyone who gets the zip file can read it. Only share it with family.`,
        "Export anyway", true);
      if (!ok) return;
    }
    if (o.contacts && feat("contacts") && o.living === "full") {
      const ok = await confirmDialog("Include phone numbers and addresses?",
        "The website will list phone numbers, WhatsApp numbers, emails and home addresses of living people. Anyone who gets the zip file can read them — and a website is easy to forward. Are you sure?",
        "Include them", true);
      if (!ok) return;
    }
    try {
      exportBtn.disabled = true;
      const r = await api("api/export", { method: "POST", body: { format: "site", options: o } });
      state.exportJob = r.jobId;
      state.exportDone = null;
      drawJob({ status: "running", progress: 0, message: "Starting" });
      setTimeout(poll, 500);
    } catch (e) { exportBtn.disabled = false; fail(e); }
  });

  // ----- the whole form -----
  const form = h("div");
  function rebuild() {
    const s = sc();
    const whoSeg = segmented([["whole", "Everyone"], ["ancestors", "Ancestors"], ["descendants", "Descendants"], ["both", "Both"], ["branch", "A branch, with cuts"], ["selected", "Pick people"]],
      s.type, (k) => {
        s.type = k;
        if (k === "branch" && !s.start.length && (s.personId || meId)) s.start = [s.personId || meId];
        redrawScope(); changed(); if (chart) chart.restyle();
      });
    whoSeg.el.classList.add("wrap");
    const living = h("p", { class: "hint" });
    const livingHint = () => {
      living.textContent = { limited: "Living people appear with their name and place in the family only — no dates, places, photos or stories.",
        full: "Everything about living people is included. Only use this for a copy that stays within the family.",
        exclude: "Living people are left out. Where one is the only link between others, they show as “Private”." }[o.living];
    };
    livingHint();
    const relTo = personPicker({ initial: pickerInitial(o.relativeTo || meId), placeholder: "Relative to…", onChange: (p) => { o.relativeTo = p ? p.id : null; changed(); } });
    const home = personPicker({ initial: pickerInitial(o.site.homePersonId || o.relativeTo || meId), placeholder: "Who the site opens on", onChange: (p) => { o.site.homePersonId = p ? p.id : null; changed(); } });
    const title = h("input", { value: o.site.title, maxlength: 120 });
    title.addEventListener("input", () => { o.site.title = title.value; changed(); });
    const intro = h("textarea", { rows: 4, maxlength: 5000, placeholder: "A few words for the front page — where the family comes from, who put this together…" }, o.site.intro);
    intro.addEventListener("input", () => { o.site.intro = intro.value; changed(); });
    const theme = h("select", { "aria-label": "Website theme" }, ...[["auto", "🌓 Follows the reader's device"], ["heritage", "📜 Heritage"], ["slate", "🌆 Slate"], ["daylight", "☀️ Daylight"], ["parchment", "🕯️ Parchment"]].map(([k, l]) => h("option", { value: k }, l)));
    theme.value = o.site.theme;
    theme.addEventListener("change", () => { o.site.theme = theme.value; changed(); });
    drawCover();
    redrawScope();
    mount(form,
      section("Format",
        h("div", { class: "format-row" },
          h("button", { type: "button", class: "format-card active", "aria-pressed": "true" }, h("strong", null, "🌐 Website"), h("span", { class: "hint" }, "A zip of web pages anyone can open — no app needed")))),
      section("Who", whoSeg.el, scopeBox, chartBox,
        h("p", { class: "hint" }, "Tap someone on the chart to start, stop or cut there. Faded cards aren't included.")),
      section("Living people", seg([["limited", "Names only"], ["full", "Everything"], ["exclude", "Leave out"]], () => o.living, (k) => { o.living = k; }, livingHint), living),
      section("Details",
        h("div", { class: "detail-quick" },
          quickEl = segmented([["names", "Names only"], ["standard", "Standard"], ["all", "Everything"]], exportDetailMatch(o),
            (k) => { exportDetailLevel(o, k); rebuild(); changed(); }).el,
          h("span", { class: "hint" }, "Each person's name and their place in the family (parents, partners, children) are always included. Untick anything else to leave it out.")),
        h("div", { class: "export-grid" },
          h("div", null, h("h4", null, "About each person"),
            check("Gender", () => o.gender, (v) => { o.gender = v; }, "off: neutral colours, “parent” instead of “mother”"),
            check("Who has died", () => o.deaths, (v) => { o.deaths = v; }, "with death and burial details"),
            check("Family details", () => o.familyDetails, (v) => { o.familyDetails = v; }, "married or partners, divorced, adopted, step, foster"),
            h("h4", null, "Names"),
            check("Maiden (birth) surnames", () => o.maidenNames, (v) => { o.maidenNames = v; }),
            check("Other names and nicknames", () => o.otherNames, (v) => { o.otherNames = v; }),
            h("h4", null, "Dates"),
            seg([["full", "Full dates"], ["year", "Years only"], ["none", "No dates"]], () => o.dates, (k) => { o.dates = k; }),
            check("Places", () => o.places, (v) => { o.places = v; })),
          h("div", null, h("h4", null, "Life events"),
            h("div", { class: "hint" }, "Births and deaths also control the years shown on each card."),
            ...EXPORT_EVENT_GROUPS.map(([k, l]) => check(l, () => o.events[k], (v) => { o.events[k] = v; })))),
        h("div", { class: "export-grid" },
          h("div", null, h("h4", null, "Photos & documents"),
            seg([["none", "No photos"], ["profile", "Profile photos"], ["all", "All photos"]], () => o.photos, (k) => { o.photos = k; }),
            h("div", { style: "margin-top:8px" }, seg([["web", "Web size (smaller)"], ["original", "Original size"]], () => o.photoSize, (k) => { o.photoSize = k; })),
            check("Documents (PDFs)", () => o.documents, (v) => { o.documents = v; }),
            h("p", { class: "hint" }, "Photos of people shown as names only or “Private” are never included.")),
          h("div", null, h("h4", null, "Writing"),
            check("Biographies and stories", () => o.stories, (v) => { o.stories = v; }),
            check("Notes on events and photos", () => o.notes, (v) => { o.notes = v; }),
            check("Show how everyone is related", () => o.relationships, (v) => { o.relationships = v; }),
            h("div", { class: "field wide" }, "Related to", relTo.el),
            feat("sources") ? check("Sources behind the facts", () => o.sources, (v) => { o.sources = v; }, "certificates, documents, interviews") : null,
            feat("contacts") ? check("Phone numbers and addresses", () => o.contacts, (v) => { o.contacts = v; }, "of living people shown in full — off unless you choose it") : null),
          personFields.length ? h("div", null, h("h4", null, "Details (custom fields)"),
            ...personFields.map((f) => check(f.label, () => (f.id in o.customFields ? o.customFields[f.id] : f.exportDefault), (v) => { o.customFields[f.id] = v; }))) : null)),
      section("Website",
        h("div", { class: "form-row" }, field("Title", title, "wide")),
        field("Front-page text", intro, "wide"),
        h("div", { class: "form-row" }, h("div", { class: "field wide" }, "Home person", home.el)),
        h("div", { class: "form-row" }, h("div", { class: "field wide" }, "Cover photo", coverBox), field("Theme", theme)),
        h("div", { class: "field wide" }, "Pages",
          h("div", { class: "chips" },
            check("Tree", () => o.site.pages.tree, (v) => { o.site.pages.tree = v; }),
            check("Everyone (A–Z)", () => o.site.pages.people, (v) => { o.site.pages.people = v; }),
            check("Surnames", () => o.site.pages.surnames, (v) => { o.site.pages.surnames = v; }),
            check("Places", () => o.site.pages.places, (v) => { o.site.pages.places = v; })))));
  }

  rebuild();
  drawPresets();
  const bar = h("div", { class: "export-bar" },
    h("div", { class: "grow" }, statsEl, warnEl, jobEl),
    exportBtn);
  const page = h("div", { class: "export-page" },
    h("div", { class: "page-head" }, h("h2", null, "Export"),
      h("div", { class: "toolbar" }, presetSel, saveBtn, delBtn, resetBtn)),
    h("p", { class: "hint" }, "Make a copy of the tree to share with family. Nothing leaves Home Assistant until you download it."),
    all.truncated ? h("div", { class: "warnings" }, h("div", null, `⚠ The chart shows ${all.people.length} of ${all.total} people; the export itself includes everyone you choose.`)) : null,
    form, bar);
  setTimeout(() => {
    drawChart();
    runPreview();
    if (state.exportJob) { exportBtn.disabled = true; drawJob({ status: "running", progress: 0, message: "Working" }); poll(); }
    else if (state.exportDone) drawJob(state.exportDone);
  }, 0);
  return page;
}

// ---------- start-up ----------
async function loadMe() {
  state.user = await api("api/me");
  const banner = $("#mediaBanner");
  if (state.user.media && !state.user.media.online) {
    banner.hidden = false;
    banner.textContent = `📷 ${state.user.media.reason || "Photo storage isn't reachable."} Photos show initials, and uploads wait until it's back.`;
  } else banner.hidden = true;
  document.querySelectorAll("[data-feature]").forEach((el) => { el.hidden = !feat(el.dataset.feature); });
  window.__nameDisplay = state.user.nameDisplay || "en";
}

function wireChrome() {
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => b.addEventListener("click", () => {
    const r = b.dataset.route;
    if (r === "tree") go(state.lastTreeFocus ? `tree/${state.lastTreeFocus}` : "tree"); else go(r);
  }));
  const collapse = $("#sidebarCollapseBtn");
  const sync = () => {
    const c = document.documentElement.getAttribute("data-sidebar") === "collapsed";
    collapse.textContent = c ? "›" : "‹";
    collapse.title = c ? "Expand sidebar" : "Collapse sidebar";
    collapse.setAttribute("aria-label", collapse.title);
  };
  collapse.addEventListener("click", () => {
    const c = document.documentElement.getAttribute("data-sidebar") === "collapsed";
    if (c) document.documentElement.removeAttribute("data-sidebar"); else document.documentElement.setAttribute("data-sidebar", "collapsed");
    lsSet("sidebarCollapsed", c ? "0" : "1");
    sync();
  });
  sync();
  const themeSel = $("#themeSelect");
  themeSel.value = window.__themeChoice || "heritage";
  themeSel.addEventListener("change", () => applyTheme(themeSel.value));
  $("#sidebarUser").addEventListener("click", () => go("whoami"));
  const up = +lsGet("treeUp"), down = +lsGet("treeDown");
  if (lsGet("treeUp") !== null && up >= 0 && up <= 10) state.tree.up = up;
  if (lsGet("treeDown") !== null && down >= 0 && down <= 10) state.tree.down = down;
  if (lsGet("treeSiblings") === "0") state.tree.siblings = false;
  state.tree.sideColour = lsGet("treeSides") === "1";
  state.tree.sideShow = ["paternal", "maternal"].includes(lsGet("treeSideShow")) ? lsGet("treeSideShow") : "both";
  const savedMode = lsGet("treeMode");
  if (["chart", "all", "cards"].includes(savedMode)) state.tree.mode = savedMode;
}

/* First run: with the admin list empty nobody can open App settings. Everyone sees how to fix it;
   nobody is ever made admin automatically. */
function showNoAdminBanner(me) {
  const banner = $("#noAdminBanner");
  if (!me || !me.noAdmin) { banner.hidden = true; return; }
  const name = me.haUsername || me.haUserId;
  mount(banner, h("span", null, "No admin yet — add your Home Assistant user name (", h("strong", null, name), ") to ",
    h("code", null, me.adminOption || "admin_users"), " in the app's Configuration tab, save, and restart the app. "),
    h("button", { type: "button", class: "link-btn", onclick: () => go("whoami") }, "How the app sees you"));
  banner.hidden = false;
}

async function init() {
  wireChrome();
  try {
    const k = await api("api/kidmode");
    if (k.locked) { kidsScreen(k); initBackNav(); return; }
  } catch (e) { /* older server or no user yet: carry on */ }
  try {
    state.me = await api("api/whoami");
  } catch (e) {
    const box = $("#fatal");
    box.hidden = false;
    mount(box, h("h3", null, "Can't open Family Tree"), h("div", null, e.message));
    return;
  }
  const chip = $("#sidebarUser");
  chip.textContent = state.me.haDisplayName + (state.me.isAdmin ? " · admin" : "");
  showNoAdminBanner(state.me);
  if (state.me.disabled) {
    document.querySelectorAll(".side-nav .tab-btn").forEach((b) => { b.hidden = b.dataset.route !== "whoami"; });
    mount($("#view"), h("div", { class: "card fatal" }, h("h3", null, "Your access is turned off"),
      h("div", null, "An admin has turned off your access to Family Tree. Ask them to turn it back on in Family Tree → Admin → Users.")), await whoamiCard());
    return;
  }
  document.querySelectorAll(".admin-only").forEach((el) => { el.hidden = !state.me.isAdmin; });
  try { await loadMe(); } catch (e) { fail(e); return; }
  window.addEventListener("hashchange", route);
  let resizeT, wasNarrow = window.innerWidth < 600;
  window.addEventListener("resize", () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => {
      const narrow = window.innerWidth < 600;
      if (narrow !== wasNarrow && currentRoute().name === "tree" && !state.tree.mode) rerender();
      wasNarrow = narrow;
    }, 300);
  });
  route();
  initBackNav();
}

// ---------- the back gesture (backnav.js) ----------
// Start page: the tree the app opens on with no address (#/ or #/tree), i.e. centred on its default
// person (me, or the best-connected person); #/tree/<that person> — the sidebar's Tree — counts too.
function atStartPage() {
  if (state.kids || !state.me || state.me.disabled) return true;
  const { name, args } = currentRoute();
  if (name !== "tree") return false;
  const f = args[0] || state.lastTreeFocus;
  return !f || (!!state.homeFocus && f === state.homeFocus);
}
function initBackNav() {
  BackNav.init({
    atHome: atStartPage,
    goHome: () => { state.lastTreeFocus = null; BackNav.go("#/"); },
    // dialogs (the photo viewer is one too), open search results and map popups, bottom-most first
    openLayers: () => Array.from(document.querySelectorAll(".modal-backdrop, .picker-results:not([hidden]), .leaflet-popup"))
      .filter((el) => el.isConnected && el.getClientRects().length),
    closeLayer: (el) => {
      if (el.classList.contains("modal-backdrop")) {
        const btn = el.querySelector(":scope > .modal > h3 > button.icon-btn");
        if (btn) btn.click(); else el.remove();
      } else if (el.classList.contains("picker-results")) el.hidden = true;
      else { const x = el.querySelector(".leaflet-popup-close-button"); if (x) x.click(); else el.remove(); }
    },
  });
}

init();
