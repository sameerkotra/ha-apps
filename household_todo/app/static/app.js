"use strict";
/* Household Todo — front end. Plain JS, no build step, no libraries.
   Every fetch path is RELATIVE (no leading slash): Home Assistant's Ingress
   serves this app under a per-session sub-path, and an absolute "/api/..."
   would hit Home Assistant itself instead of this app. Every piece of
   user-supplied text is inserted with textContent (via h()), never innerHTML. */

const state = {
  me: null,            // GET /api/whoami — always the real caller
  users: [],
  types: [],
  places: [],          // places visible to the acting user
  actAs: null,         // id of the user an admin is acting as (null = yourself)
  today: null,         // "YYYY-MM-DD" in Home Assistant's time zone, from the server
  tab: "calendar",
  adminTab: "settings", // App settings | Users | Storage
};

const TABS = ["dashboard", "calendar", "schedule", "maintenance", "lists", "places", "settings", "admin"];
const PRIORITY_LABEL = { high: "High", medium: "Medium", low: "Low" };
const ICON_PRESETS = [
  ["mdi:trash-can-outline", "🗑️", "Trash"],
  ["mdi:recycle", "♻️", "Recycling"],
  ["mdi:leaf", "🍃", "Garden waste"],
  ["mdi:calendar-clock", "📅", "Calendar"],
  ["mdi:bell-outline", "🔔", "Bell"],
];

// ---------- tiny DOM helpers (common/ui.js) ----------
const { h, $, clear, mount, lsGet, lsSet } = UI;
function spinner() { return h("div", { class: "spinner" }, "Loading…"); }

function toggleSwitch(checked, onChange, opts = {}) {
  const input = h("input", { type: "checkbox", checked: !!checked, disabled: !!opts.disabled, "aria-label": opts.label || "toggle" });
  input.addEventListener("change", () => onChange(input.checked, input));
  return h("label", { class: "toggle-switch" }, input, h("span", { class: "track" }, h("span", { class: "thumb" })));
}

// ---------- safe storage (localStorage can throw or be empty) ----------
function ssGet(key) { try { return sessionStorage.getItem(key); } catch (e) { return null; } }
function ssSet(key, val) {
  try { if (val === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, val); } catch (e) { /* ignore */ }
}

// ---------- API ----------
function withUser(path) {
  if (!state.actAs || (state.me && state.actAs === state.me.haUserId)) return path;
  return path + (path.includes("?") ? "&" : "?") + "as_user=" + encodeURIComponent(state.actAs);
}

const errorMessage = UI.errorMessage;
// asSelf: true sends the request as the real caller even while an admin is acting as someone else.
const api = UI.makeApi({ url: (path, opts) => (opts.asSelf ? path : withUser(path)).replace(/^\//, "") });

// ---------- toasts & modals (common/ui.js) ----------
function toast(msg, isError = false) { UI.toast(msg, { error: isError, ms: isError ? 6000 : 3000 }); }
function fail(e) { toast(e && e.message ? e.message : String(e), true); }

function openModal(title, content, opts = {}) {
  return UI.openModal(title, content, { modalClass: opts.sheet ? "sheet" : "", onClose: opts.onClose, escape: "stack",
    focus: opts.focus === false ? false : "first", focusSelector: "input:not([type=hidden]), textarea, select", focusDelay: 0 });
}

// ---------- dates (all display formatting; the server owns the maths) ----------
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function isoOf(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function parseIso(iso) {          // local noon avoids DST edge cases
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d, 12, 0, 0);
}
function todayIso() { return state.today || isoOf(new Date()); }
function addDays(iso, n) { const d = parseIso(iso); d.setDate(d.getDate() + n); return isoOf(d); }
function daysBetween(aIso, bIso) { return Math.round((parseIso(bIso) - parseIso(aIso)) / 86400000); }

function fmtDate(iso, opts = {}) {
  if (!iso) return "";
  const d = parseIso(iso);
  const sameYear = d.getFullYear() === parseIso(todayIso()).getFullYear();
  const base = `${DOW[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return sameYear && !opts.year ? base : `${base} ${d.getFullYear()}`;
}
function relLabel(iso) {
  const n = daysBetween(todayIso(), iso);
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n === -1) return "yesterday";
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}
function fmtStamp(isoTs) {         // "2026-09-20T14:05:00+00:00" -> "Sun 20 Sep"
  if (!isoTs) return "";
  const d = new Date(isoTs);
  return isNaN(d) ? "" : fmtDate(isoOf(d));
}
function sundayFirst() { return lsGet("calendarWeekStart") !== "monday"; }

function copyText(text) {
  const done = () => toast("Copied");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => legacyCopy(text, done));
  } else {
    legacyCopy(text, done);
  }
}
function legacyCopy(text, done) {
  const ta = h("textarea", { style: "position:fixed;opacity:0" }, text);
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("Couldn't copy — select and copy it manually.", true); }
  ta.remove();
}

// ---------- lookups shared by the forms ----------
async function loadLookups() {
  const [users, types, places] = await Promise.all([
    api("/api/users"), api("/api/task-types"), api("/api/places"),
  ]);
  state.users = users;
  state.types = types;
  state.places = places;
}
async function refreshPlaces() { state.places = await api("/api/places"); }
async function refreshTypes() { state.types = await api("/api/task-types"); }
function userName(id) { const u = state.users.find((x) => x.id === id); return u ? u.name : null; }
function isAdmin() { return !!(state.me && state.me.isAdmin); }
function actingUserId() { return state.actAs || (state.me && state.me.haUserId); }
function isActingAsOther() { return !!state.actAs && state.me && state.actAs !== state.me.haUserId; }

// ---------- drag-to-reorder (Pointer Events; no library) ----------
// The dragged row is never re-parented mid-drag (that would drop pointer
// capture on touch screens); an insertion line shows where it will land and
// the DOM moves once, on release. Alt+↑/↓ on a focused handle is the keyboard path.
function makeSortable(container, { itemSelector, handleSelector, onReorder, grid = false }) {
  const items = () => [...container.querySelectorAll(itemSelector)].filter((el) => el.parentElement === container);
  const ids = () => items().map((el) => el.dataset.id);
  let drag = null;

  function clearMarks() { items().forEach((el) => el.classList.remove("drag-over-top", "drag-over-bottom", "drag-over-left", "drag-over-right")); }
  function target(x, y) {
    const others = items().filter((el) => el !== drag.el);
    for (const el of others) {
      const r = el.getBoundingClientRect();
      // a grid reads left-to-right, then down; a single column only cares about y
      const before = grid ? (y < r.top || (y <= r.bottom && x < r.left + r.width / 2)) : y < r.top + r.height / 2;
      if (before) return { el, before: true };
    }
    return others.length ? { el: others[others.length - 1], before: false } : null;
  }
  const markClass = (before) => grid ? (before ? "drag-over-left" : "drag-over-right") : (before ? "drag-over-top" : "drag-over-bottom");

  container.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(handleSelector);
    if (!handle || !container.contains(handle) || e.button > 0) return;
    const el = handle.closest(itemSelector);
    if (!el) return;
    e.preventDefault();
    drag = { el, before: ids(), tgt: null };
    el.classList.add("dragging");
    const move = (ev) => {
      clearMarks();
      drag.tgt = target(ev.clientX, ev.clientY);
      if (drag.tgt) drag.tgt.el.classList.add(markClass(drag.tgt.before));
    };
    const up = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      document.removeEventListener("pointercancel", up);
      clearMarks();
      el.classList.remove("dragging");
      const t = drag.tgt;
      const before = drag.before;
      drag = null;
      if (!t) return;
      if (t.before) container.insertBefore(el, t.el); else t.el.after(el);
      const after = ids();
      if (after.join() !== before.join()) onReorder(after, before);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
    document.addEventListener("pointercancel", up);
  });

  container.addEventListener("keydown", (e) => {
    const handle = e.target.closest(handleSelector);
    const back = e.key === "ArrowUp" || (grid && e.key === "ArrowLeft");
    const fwd = e.key === "ArrowDown" || (grid && e.key === "ArrowRight");
    if (!handle || !e.altKey || (!back && !fwd)) return;
    const el = handle.closest(itemSelector);
    const before = ids();
    if (back && el.previousElementSibling && el.previousElementSibling.matches(itemSelector)) {
      container.insertBefore(el, el.previousElementSibling);
    } else if (fwd && el.nextElementSibling && el.nextElementSibling.matches(itemSelector)) {
      el.nextElementSibling.after(el);
    } else return;
    e.preventDefault();
    handle.focus();
    onReorder(ids(), before);
  });
}

// =====================================================================
// Task rows, the task form, the edit modal and the quick-add row
// =====================================================================

const expandedTasks = new Set();   // task ids whose checklist is open (survives re-renders)

// The optional link on a task or schedule item. The server only
// stores http(s) URLs; the check here means nothing else ever becomes an href.
function isWebUrl(url) { return typeof url === "string" && /^https?:\/\//i.test(url); }
function linkChip(url) {
  if (!isWebUrl(url)) return null;
  return h("a", { class: "chip btnlike link-chip", href: url, target: "_blank", rel: "noopener noreferrer", title: url }, "🔗 Link");
}

function typeChip(type) {
  if (!type) return null;
  return h("span", { class: "chip type", style: type.color ? `border-left-color:${type.color}` : null },
    type.icon ? `${type.icon} ` : "", type.name);
}

function dueChip(t) {
  if (!t.dueDate) return null;
  const time = t.dueTime ? ` · ${t.dueTime}` : "";
  let cls = "chip", label = fmtDate(t.dueDate) + time, title = null;
  if (t.overdue) { cls += " overdue"; label = "Overdue · " + label; }
  else if (t.past) { cls += " past"; title = "Past its date — optional tasks never count as overdue"; }
  else if (t.dueDate === todayIso() && !t.completed) { cls += " today"; label = "Today" + time; }
  return h("span", { class: cls, title }, "📅 ", label);
}

// Estimated drive time from home (§8n) — "HH:MM" due time minus N minutes,
// wrapping around midnight for the rare task due just after it.
function leaveByTime(dueTime, driveMinutes) {
  const [hh, mm] = dueTime.split(":").map(Number);
  const total = (((hh * 60 + mm - driveMinutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function driveChip(t) {
  if (!t.place || t.place.driveMinutes == null || !t.dueTime) return null;
  const route = t.place.driveTollsAvoided ? " (route without toll roads)" : "";
  return h("span", { class: "chip", title: `Estimated drive time from home to ${t.place.name}${route}` },
    "🚗 ", `${t.place.driveMinutes} min · leave by ${leaveByTime(t.dueTime, t.place.driveMinutes)}`);
}

function taskRow(t, opts = {}) {
  const { onChange = () => {}, showList = false, grip = false } = opts;
  const row = h("div", { class: "task-row" + (t.completed ? " done" : "") + (t.overdue ? " overdue-row" : ""), dataset: { id: t.id } });

  const cb = h("input", { type: "checkbox", checked: t.completed, "aria-label": t.completed ? "Mark as not done" : "Mark as done" });
  cb.addEventListener("change", async () => {
    cb.disabled = true;
    try {
      await api(`/api/tasks/${t.id}`, { method: "PATCH", body: { completed: cb.checked } });
      onChange();
    } catch (e) {
      cb.checked = !cb.checked;
      cb.disabled = false;
      fail(e);
    }
  });

  const rerender = (nt) => { row.replaceWith(taskRow(nt, opts)); };
  const meta = h("div", { class: "task-meta" });
  meta.append(...[
    typeChip(t.type),
    dueChip(t),
    t.priority ? h("span", { class: `chip pri-${t.priority}` }, PRIORITY_LABEL[t.priority]) : null,
    t.completionRequired ? h("span", { class: "chip required", title: "Must be completed — counts as overdue when late" }, "Required") : null,
    t.place ? h("a", { class: "chip btnlike", href: mapsUrl(t.place.address), target: "_blank", rel: "noopener noreferrer", title: `Open ${t.place.address} in maps` }, "📍 ", t.place.name) : null,
    driveChip(t),
    t.assigneeName ? h("span", { class: "chip" }, "👤 ", t.assigneeName) : null,
    linkChip(t.url),
    t.source ? h("span", { class: "chip source", title: `Added from Household ${t.source}` }, "from " + t.source) : null,
    t.fileCount ? h("span", { class: "chip", title: "Files" }, "📎 ", String(t.fileCount)) : null,
    showList ? h("span", { class: "plain" }, t.listKind === "personal" ? "in My lists · " : "in ", t.listName) : null,
  ].filter(Boolean));

  if (t.itemsTotal > 0) {
    const open = expandedTasks.has(t.id);
    const pct = Math.round((t.itemsDone / t.itemsTotal) * 100);
    const chip = h("button", { class: "chip btnlike", type: "button", "aria-expanded": open ? "true" : "false", title: "Show checklist" },
      `☑ ${t.itemsDone}/${t.itemsTotal}`, h("span", { class: "progress-bar" }, h("i", { style: `width:${pct}%` })));
    chip.addEventListener("click", () => {
      if (expandedTasks.has(t.id)) expandedTasks.delete(t.id); else expandedTasks.add(t.id);
      rerender(t);
    });
    meta.appendChild(chip);
  }
  if (t.completed) {
    meta.appendChild(h("span", { class: "plain" }, `Completed ${fmtStamp(t.completedAt)}${t.completedByName ? " by " + t.completedByName : ""}`));
  }

  const title = h("div", { class: "task-title", tabindex: "0", role: "button" }, t.title);
  const open = () => openTaskModal(t, { onSaved: onChange });
  title.addEventListener("click", open);
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });

  const main = h("div", { class: "task-main" }, title);
  if (t.notes) main.appendChild(h("div", { class: "task-notes" }, t.notes));
  main.appendChild(meta);

  if (t.itemsTotal > 0 && expandedTasks.has(t.id)) {
    main.appendChild(h("div", { class: "checklist" }, t.items.map((it) => {
      const icb = h("input", { type: "checkbox", checked: it.done });
      icb.addEventListener("change", async () => {
        icb.disabled = true;
        try { rerender(await api(`/api/task-items/${it.id}`, { method: "PATCH", body: { done: icb.checked } })); }
        catch (e) { icb.checked = !icb.checked; icb.disabled = false; fail(e); }
      });
      return h("label", { class: it.done ? "done" : "" }, icb, h("span", null, it.text));
    })));
  }

  const gripEl = grip
    ? h("button", { class: "grip", type: "button", title: "Drag to reorder (or focus and press Alt+↑ / Alt+↓)", "aria-label": `Reorder ${t.title}` }, "⠿")
    : null;
  row.append(...[gripEl, h("div", { class: "check" }, cb), main,
    h("div", { class: "task-actions" }, h("button", { class: "icon-btn", type: "button", title: "Edit", "aria-label": "Edit", onclick: open }, "✎"))].filter(Boolean));
  return row;
}

// ---------------------------------------------------------------------
// The shared form: quick-add's "More options" and the edit modal
// ---------------------------------------------------------------------

function listContext(t) {
  // the list a task lives in, as much as the forms need to know
  return { id: t.listId, kind: t.listKind, ownerUserId: t.listKind === "personal" ? actingUserId() : null };
}

// The place picker shared by the task form and the schedule form: every
// place (they're shared with the household, §8h), "+ New place…" with inline
// fields, and an "Open in maps · ~N min from home" link. `owner` is the task
// or schedule item being edited (its placeId / place), or null.
function placePicker(owner) {
  const allowed = state.places;
  const select = h("select", { "aria-label": "Place" });
  // Search: typing narrows the list to places whose name or address contains every word typed
  const search = h("input", { type: "search", class: "place-search", placeholder: "Search places", "aria-label": "Search places",
    autocomplete: "off", hidden: !allowed.length });
  const found = h("span", { class: "hint place-found" });
  function fill(query) {
    const words = (query || "").toLowerCase().split(/\s+/).filter(Boolean);
    const hit = (p) => words.every((w) => (p.name + " " + (p.address || "")).toLowerCase().includes(w));
    const keep = select.value || (owner && owner.placeId) || "";
    const shown = allowed.filter(hit);
    const opts = [h("option", { value: "" }, "— none —"), shown.map((p) => h("option", { value: p.id }, p.name))];
    if (owner && owner.place && !allowed.some((p) => p.id === owner.placeId) && (!words.length || hit(owner.place))) {
      opts.push(h("option", { value: owner.placeId }, owner.place.name));
    }
    opts.push(h("option", { value: "__new__" }, "+ New place…"));
    mount(select, opts);
    const values = [...select.options].map((o) => o.value);
    // keep the choice if it's still listed; otherwise pick the first place found
    select.value = values.includes(keep) && !(words.length && keep === "") ? keep : (shown[0] ? shown[0].id : "");
    found.textContent = words.length ? (shown.length ? `${shown.length} found` : "No place matches — choose “+ New place…” to add it") : "";
  }
  fill("");
  search.addEventListener("input", () => { fill(search.value); newBox.hidden = select.value !== "__new__"; syncMaps(); });
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
  const control = h("div", { class: "place-control" }, search, select, found);
  const newName = h("input", { type: "text", maxlength: "60", placeholder: "Place name, e.g. Dentist" });
  const newAddr = h("textarea", { maxlength: "300", placeholder: "Address", style: "min-height:52px" });
  const newPhone = h("input", { type: "tel", maxlength: "30", placeholder: "Phone (optional)" });
  const newBox = h("div", { class: "form-row", hidden: true },
    h("div", { class: "field" }, "New place", newName),
    h("div", { class: "field" }, "Address", newAddr),
    h("div", { class: "field" }, "Phone", newPhone));
  const mapsLink = h("a", { class: "hint", target: "_blank", rel: "noopener noreferrer", hidden: true }, "Open in maps");
  const syncMaps = () => {
    const chosen = allowed.find((p) => p.id === select.value) || (owner && owner.place && owner.placeId === select.value ? owner.place : null);
    if (chosen && chosen.address) {
      mapsLink.href = mapsUrl(chosen.address);
      mapsLink.hidden = false;
      mapsLink.textContent = chosen.driveMinutes != null ? `Open in maps · ~${chosen.driveMinutes} min from home` : "Open in maps";
    } else {
      mapsLink.hidden = true;
    }
  };
  select.addEventListener("change", () => { newBox.hidden = select.value !== "__new__"; syncMaps(); });
  syncMaps();
  // -> the chosen place id (creating the new place first), or null
  async function resolve() {
    if (select.value !== "__new__") return select.value || null;
    const name = newName.value.trim(), address = newAddr.value.trim();
    if (!name || !address) throw new Error("Give the new place a name and an address.");
    const p = await api("/api/places", { method: "POST", body: { name, address, phone: newPhone.value.trim() || null } });
    await refreshPlaces();
    // select it, so retrying after a failed save doesn't try to create it twice (409)
    select.insertBefore(h("option", { value: p.id }, p.name), select.lastChild);
    select.value = p.id;
    newBox.hidden = true;
    syncMaps();
    return p.id;
  }
  return { select, control, newBox, mapsLink, resolve };
}

function buildTaskForm({ task = null, list, includeTitle = false }) {
  const isEdit = !!task;
  const f = {};
  f.title = h("input", { type: "text", maxlength: "200", value: task ? task.title : "", placeholder: "What needs doing?" });
  f.notes = h("textarea", { maxlength: "5000", placeholder: "Notes (optional)" }, task && task.notes ? task.notes : "");
  f.url = h("input", { type: "url", maxlength: "2000", inputmode: "url", placeholder: "https://…", value: task && task.url ? task.url : "" });
  f.date = h("input", { type: "date", value: task && task.dueDate ? task.dueDate : "" });
  f.time = h("input", { type: "time", value: task && task.dueTime ? task.dueTime : "" });
  f.priority = h("select", { value: task && task.priority ? task.priority : "" },
    h("option", { value: "" }, "None"), h("option", { value: "high" }, "High"),
    h("option", { value: "medium" }, "Medium"), h("option", { value: "low" }, "Low"));
  f.type = h("select", { value: task && task.typeId ? task.typeId : "" },
    h("option", { value: "" }, "— none —"),
    state.types.map((ty) => h("option", { value: ty.id }, (ty.icon ? ty.icon + " " : "") + ty.name)));

  const place = placePicker(task);
  f.place = place.select;

  f.assignee = h("select");
  // who can be assigned depends on the list: only the owner in a personal list (also used when a task moves list)
  function fillAssignees(lst) {
    const keep = f.assignee.value || (task && task.assignedTo) || "";
    const cands = lst.kind === "personal"
      ? state.users.filter((u) => u.id === (lst.ownerUserId || actingUserId()))
      : state.users.filter((u) => !u.disabled || (task && u.id === task.assignedTo));
    mount(f.assignee, h("option", { value: "" }, "Unassigned"), cands.map((u) => h("option", { value: u.id }, u.name + (u.disabled ? " (disabled)" : ""))));
    f.assignee.value = cands.some((u) => u.id === keep) ? keep : "";
  }
  fillAssignees(list);
  f.completion = h("select", { value: task && task.completionRequired ? "required" : "optional" },
    h("option", { value: "optional" }, "Optional"), h("option", { value: "required" }, "Required"));
  f.items = isEdit ? null : h("textarea", { placeholder: "Checklist — one item per line (optional)", "aria-label": "Checklist items" });

  const syncTime = () => {
    f.time.disabled = !f.date.value;
    if (!f.date.value) f.time.value = "";
  };
  f.date.addEventListener("input", syncTime);
  syncTime();

  const el = h("div", { class: "task-form" },
    includeTitle ? h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Title", f.title)) : null,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Notes", f.notes)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Link (optional)", f.url)),
    h("div", { class: "form-row" },
      h("label", { class: "field" }, "Due date", f.date),
      h("label", { class: "field" }, "Time", f.time),
      h("label", { class: "field" }, "Priority", f.priority)),
    h("div", { class: "form-row" },
      h("label", { class: "field" }, "Type", f.type),
      h("div", { class: "field wide" }, "Place", place.control, place.mapsLink),
      h("label", { class: "field" }, "Assign to", f.assignee),
      h("label", { class: "field" }, "Completion", f.completion)),
    place.newBox,
    f.items ? h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Checklist", f.items)) : null);

  async function collect() {
    const body = {};
    if (includeTitle) {
      const title = f.title.value.trim();
      if (!title) throw new Error("A task needs a title.");
      body.title = title;
    }
    const put = (key, val) => { if (isEdit || (val !== null && val !== "")) body[key] = val; };
    put("notes", f.notes.value.trim() || null);
    put("url", f.url.value.trim() || null);
    put("due_date", f.date.value || null);
    put("due_time", f.date.value && f.time.value ? f.time.value : null);
    put("priority", f.priority.value || null);
    put("type_id", f.type.value || null);
    put("assigned_to", f.assignee.value || null);
    body.completion_required = f.completion.value === "required";

    put("place_id", await place.resolve());
    if (f.items) {
      const lines = f.items.value.split("\n").map((s) => s.trim()).filter(Boolean);
      if (lines.length) body.items = lines;
    }
    return body;
  }
  return { el, f, collect, fillAssignees };
}

// A live checklist editor for an existing task: every change is saved as it
// happens through the item routes, so two people can tick different items at once (§8i).
function checklistEditor(task, markChanged) {
  const box = h("div", { class: "edit-checklist" });
  let addInput = null;
  // Text typed into "Add an item" but not yet confirmed with Enter is saved too
  // when the task's Save button is pressed, so it isn't silently lost.
  box.flushPending = async () => {
    const v = addInput ? addInput.value.trim() : "";
    if (!v) return;
    task.items = (await api(`/api/tasks/${task.id}/items`, { method: "POST", body: { text: v } })).items;
    addInput.value = "";
    markChanged();
  };
  function render() {
    clear(box);
    task.items.forEach((it) => {
      const cb = h("input", { type: "checkbox", checked: it.done, "aria-label": "Done" });
      const txt = h("input", { type: "text", value: it.text, maxlength: "200", "aria-label": "Item text" });
      const del = h("button", { class: "icon-btn danger", type: "button", title: "Remove item", "aria-label": "Remove item" }, "✕");
      cb.addEventListener("change", async () => {
        try { task.items = (await api(`/api/task-items/${it.id}`, { method: "PATCH", body: { done: cb.checked } })).items; markChanged(); }
        catch (e) { cb.checked = !cb.checked; fail(e); }
      });
      txt.addEventListener("change", async () => {
        const v = txt.value.trim();
        if (!v) { txt.value = it.text; return; }
        try { task.items = (await api(`/api/task-items/${it.id}`, { method: "PATCH", body: { text: v } })).items; markChanged(); }
        catch (e) { txt.value = it.text; fail(e); }
      });
      del.addEventListener("click", async () => {
        try { await api(`/api/task-items/${it.id}`, { method: "DELETE" }); task.items = task.items.filter((x) => x.id !== it.id); markChanged(); render(); }
        catch (e) { fail(e); }
      });
      box.appendChild(h("div", { class: "item" }, cb, txt, del));
    });
    const add = h("input", { type: "text", class: "add-item", maxlength: "200", placeholder: "Add an item and press Enter", "aria-label": "New checklist item" });
    add.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const v = add.value.trim();
      if (!v) return;
      try {
        task.items = (await api(`/api/tasks/${task.id}/items`, { method: "POST", body: { text: v } })).items;
        markChanged();
        render();
        $(".add-item", box).focus();
      } catch (err) { fail(err); }
    });
    addInput = add;
    box.appendChild(add);
  }
  render();
  return box;
}

function openTaskModal(task, { onSaved = () => {} } = {}) {
  const list = listContext(task);
  const form = buildTaskForm({ task, list, includeTitle: true });
  const local = JSON.parse(JSON.stringify(task));   // the checklist editor mutates its own copy
  let changed = false;
  const err = h("div", { class: "error-text" });
  const save = h("button", { class: "btn-primary", type: "button" }, "Save");
  const del = h("button", { class: "btn-danger", type: "button" }, "Delete task");
  const meta = h("div", { class: "hint" },
    `In ${task.listName}` + (task.createdByName ? ` · added by ${task.createdByName}` : "") + (task.createdAt ? ` on ${fmtStamp(task.createdAt)}` : ""));
  // Move to another list: the household's shared lists and your own personal ones
  const listSel = h("select", { "aria-label": "List" }, h("option", { value: task.listId }, task.listName));
  const listNote = h("div", { class: "hint", hidden: true, style: "margin:-4px 0 10px" });
  let lists = [];
  api("/api/lists").then((ls) => {
    lists = ls;
    const opt = (l) => h("option", { value: l.id }, l.name);
    mount(listSel,
      ls.some((l) => l.kind === "shared") ? h("optgroup", { label: "Household" }, ls.filter((l) => l.kind === "shared").map(opt)) : null,
      ls.some((l) => l.kind === "personal") ? h("optgroup", { label: "Just me" }, ls.filter((l) => l.kind === "personal").map(opt)) : null);
    listSel.value = task.listId;
  }).catch(() => { /* keep just the current list */ });
  listSel.addEventListener("change", () => {
    const target = lists.find((l) => l.id === listSel.value);
    if (!target) return;
    const before = form.f.assignee.value;
    form.fillAssignees({ id: target.id, kind: target.kind, ownerUserId: target.ownerUserId });
    const moving = target.id !== task.listId;
    listNote.hidden = !moving;
    listNote.textContent = !moving ? "" : (target.kind === "personal" ? `Moves to ${target.name} — only you will see it.` : `Moves to ${target.name}, which the household sees.`)
      + (before && !form.f.assignee.value ? " It will be unassigned." : "");
  });

  const checklist = checklistEditor(local, () => { changed = true; });
  const body = h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "List", listSel)), listNote,
    form.el,
    h("div", { class: "field" }, "Checklist", checklist),
    task.listRole === "maintenance" && maintEnabled() ? jobFilesSection(task) : null,
    meta, err, h("div", { class: "actions" }, del, save));
  const modal = openModal("Edit task", body, { onClose: () => { if (changed) onSaved(); } });

  save.addEventListener("click", async () => {
    err.textContent = "";
    save.disabled = true;
    try {
      const patch = await form.collect();
      if (listSel.value && listSel.value !== task.listId) patch.list_id = listSel.value;
      await checklist.flushPending();
      await api(`/api/tasks/${task.id}`, { method: "PATCH", body: patch });
      changed = true;
      modal.close();
      const moved = patch.list_id && lists.find((l) => l.id === patch.list_id);
      toast(moved ? `Moved to ${moved.name}` : "Saved");
    } catch (e) { err.textContent = e.message; save.disabled = false; }
  });
  del.addEventListener("click", async () => {
    if (!confirm(`Delete “${task.title}”? This can't be undone.`)) return;
    try { await api(`/api/tasks/${task.id}`, { method: "DELETE" }); changed = true; modal.close(); toast("Deleted"); }
    catch (e) { err.textContent = e.message; }
  });
}

// ---------------------------------------------------------------------
// Quick add: a title box + a Required toggle; Enter and the task exists (§8g, §8l)
// ---------------------------------------------------------------------
function quickAddCard({ list, onAdded }) {
  const title = h("input", { type: "text", maxlength: "200", placeholder: "Add a task — just a title is enough", "aria-label": "New task title" });
  const reqBox = h("input", { type: "checkbox" });
  const req = h("label", { class: "mini-toggle", title: "A required task counts as overdue when its date passes" }, reqBox, "Required");
  const addBtn = h("button", { class: "btn-primary", type: "button" }, "Add");
  const moreBtn = h("button", { class: "link-btn", type: "button" }, "More options");
  const err = h("div", { class: "error-text" });
  const panel = h("div", { class: "more-panel", hidden: true });
  let form = null;

  function ensureForm() {
    form = buildTaskForm({ list, includeTitle: false });
    form.f.completion.value = reqBox.checked ? "required" : "optional";
    form.f.completion.addEventListener("change", () => { reqBox.checked = form.f.completion.value === "required"; });
    mount(panel, form.el);
  }
  reqBox.addEventListener("change", () => { if (form) form.f.completion.value = reqBox.checked ? "required" : "optional"; });
  moreBtn.addEventListener("click", () => {
    if (!form) ensureForm();
    panel.hidden = !panel.hidden;
    moreBtn.textContent = panel.hidden ? "More options" : "Fewer options";
  });

  async function submit() {
    const t = title.value.trim();
    if (!t) { title.focus(); return; }
    err.textContent = "";
    addBtn.disabled = true;
    try {
      const body = form ? await form.collect() : {};
      body.title = t;
      body.completion_required = reqBox.checked;
      await api(`/api/lists/${list.id}/tasks`, { method: "POST", body });
      title.value = "";
      reqBox.checked = false;          // the Required toggle resets after each add (§8l)
      form = null;
      clear(panel);
      panel.hidden = true;
      moreBtn.textContent = "More options";
      await onAdded();
      title.focus();
    } catch (e) { err.textContent = e.message; }
    addBtn.disabled = false;
  }
  addBtn.addEventListener("click", submit);
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });

  return h("div", { class: "card" },
    h("div", { class: "quick-add" }, title, req, addBtn),
    h("div", { style: "margin-top:8px" }, moreBtn), err, panel);
}

// =====================================================================
// Shared bits
// =====================================================================

function errorCard(e, retry) {
  return h("div", { class: "card fatal" }, h("div", { class: "error-text" }, e.message || String(e)),
    retry ? h("div", { class: "actions" }, h("button", { class: "btn-secondary", type: "button", onclick: retry }, "Try again")) : null);
}
function pageHead(title, ...right) {
  return h("div", { class: "page-head" }, h("h2", null, title), h("div", { class: "toolbar", style: "margin:0" }, right));
}

// =====================================================================
// Dashboard
// =====================================================================

function scheduleGlyph(icon) {
  const p = ICON_PRESETS.find((x) => x[0] === icon);
  return p ? p[1] : "🔁";
}

async function renderDashboard() {
  const root = $("#tab-dashboard");
  mount(root, pageHead("Dashboard"), spinner());
  try {
    const [d, w] = await Promise.all([api("/api/dashboard"), api("/api/workload")]);
    state.today = d.today;
    const again = () => renderDashboard();

    const quick = h("input", { type: "text", maxlength: "200", placeholder: "Quick add to My Tasks…", "aria-label": "Quick add a task" });
    const quickBtn = h("button", { class: "btn-primary", type: "button" }, "Add");
    const doQuick = async () => {
      const title = quick.value.trim();
      if (!title) return;
      quickBtn.disabled = true;
      try { await api("/api/tasks", { method: "POST", body: { title } }); toast("Added to My Tasks"); again(); }
      catch (e) { fail(e); quickBtn.disabled = false; }
    };
    quickBtn.addEventListener("click", doQuick);
    quick.addEventListener("keydown", (e) => { if (e.key === "Enter") doQuick(); });

    const tiles = h("div", { class: "card-grid" },
      statTile(d.counts.overdue, "Overdue", d.counts.overdue > 0),
      statTile(d.counts.dueToday, "Due today"),
      statTile(d.counts.upcoming, "Next 7 days"),
      statTile(d.counts.open, "Open tasks"));

    const coming = d.schedule.length
      ? h("div", { class: "card" }, h("h3", null, "Coming up"),
        h("div", { class: "coming-strip" }, d.schedule.map((c) =>
          h("span", { class: "coming-pill" + (c.occursToday ? " today" : ""), title: c.name },
            scheduleGlyph(c.icon), c.private ? " 🔒" : null,
            ` ${c.name} — ${c.occursToday ? "today" : relLabel(c.nextDate)}${c.startTime ? " " + c.startTime + "–" + c.endTime : ""}`))))
      : null;

    const workload = h("div", { class: "card" }, h("h3", null, "Household workload"),
      h("div", { class: "workload-grid" }, w.people.map((p) => workloadCard(p)), unassignedCard(w.unassigned)),
      isAdmin() ? h("div", { class: "hint", style: "margin-top:8px" }, "Click a person to act as them (admins only).") : null);

    const group = (label, cls, tasks) => tasks.length
      ? h("div", null, h("div", { class: "group-head " + cls }, label, h("span", { class: "count-badge" }, tasks.length)),
        h("div", { class: "task-list" }, tasks.map((t) => taskRow(t, { onChange: again, showList: true }))))
      : null;
    const groups = [group("Overdue", "overdue", d.overdue), group("Due today", "", d.dueToday), group("Upcoming — next 7 days", "", d.upcoming)].filter(Boolean);

    mount(root, pageHead("Dashboard"),
      h("div", { class: "card" }, h("div", { class: "quick-add" }, quick, quickBtn)),
      tiles, coming, workload,
      groups.length ? h("div", null, groups) : h("div", { class: "card empty" }, "Nothing due in the next week. 🎉 Add a task above, or open a list."));
  } catch (e) { mount(root, pageHead("Dashboard"), errorCard(e, renderDashboard)); }
}

function statTile(value, label, over = false) {
  return h("div", { class: "stat-card" + (over ? " over" : "") }, h("div", { class: "value" }, value), h("div", { class: "label" }, label));
}

function workloadCard(p) {
  const clickable = isAdmin() && p.userId !== actingUserId();
  const el = h("div", { class: "workload-card" + (p.isMe ? " me" : "") + (clickable ? " clickable" : ""), title: clickable ? `Act as ${p.name}` : null },
    h("div", { class: "who" }, h("span", null, p.name), p.isMe ? h("span", { class: "badge-you" }, isActingAsOther() ? "acting" : "you") : (p.disabled ? h("span", { class: "chip" }, "disabled") : null)),
    h("div", { class: "nums" },
      h("span", null, h("b", null, p.open), "open"),
      h("span", null, h("b", { class: p.overdue > 0 ? "bad" : "" }, p.overdue), "overdue"),
      h("span", null, h("b", null, p.dueSoon), "due soon"),
      h("span", null, h("b", null, p.doneThisWeek), "done this week")));
  if (clickable) el.addEventListener("click", () => setActAs(p.userId));
  return el;
}
function unassignedCard(u) {
  return h("div", { class: "workload-card" }, h("div", { class: "who" }, h("span", null, "Unassigned")),
    h("div", { class: "nums" },
      h("span", null, h("b", null, u.open), "open"),
      h("span", null, h("b", { class: u.overdue > 0 ? "bad" : "" }, u.overdue), "overdue"),
      h("span", null, h("b", null, u.dueSoon), "due soon")));
}

// =====================================================================
// Lists tab: overview + list detail
// =====================================================================

const listUI = { openId: null };

// One page: every list as a card across the top (stacked on a phone), and the
// selected list's tasks right below them.
async function renderLists() {
  const root = $("#tab-lists");
  mount(root, pageHead("Lists"), spinner());
  let lists;
  try { lists = await api("/api/lists"); }
  catch (e) { mount(root, pageHead("Lists"), errorCard(e, renderLists)); return; }

  const shared = lists.filter((l) => l.kind === "shared");
  const personal = lists.filter((l) => l.kind === "personal");
  const ordered = [...shared, ...personal];
  if (!ordered.some((l) => l.id === listUI.openId)) listUI.openId = ordered.length ? ordered[0].id : null;

  const cardsBox = h("div", { class: "list-cards" });
  const detailBox = h("div", { class: "list-detail" });

  function paintCounts(fresh) {
    fresh.forEach((l) => {
      const card = cardsBox.querySelector(`.list-card[data-id="${l.id}"]`);
      if (!card) return;
      $(".count-badge", card).textContent = `${l.openCount} open`;
      $(".lc-name", card).textContent = l.name;
    });
  }
  async function refreshCounts() {
    try { paintCounts(await api("/api/lists")); } catch (e) { /* counts are cosmetic */ }
  }

  function select(id) {
    listUI.openId = id;
    cardsBox.querySelectorAll(".list-card").forEach((c) => {
      const on = c.dataset.id === id;
      c.classList.toggle("active", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
    openDetail();
  }
  function openDetail() {
    const list = ordered.find((l) => l.id === listUI.openId);
    if (!list) { mount(detailBox, h("div", { class: "card empty" }, "No lists yet — use “+ New list” to make one.")); return; }
    renderListDetail(list, detailBox, {
      onCounts: refreshCounts,
      onRenamed: () => renderLists(),
      onDeleted: () => { listUI.openId = null; renderLists(); },
    });
  }

  function group(kind, items) {
    // display:contents keeps both groups in ONE grid while each stays its own
    // drag-and-drop container (shared and personal lists are ordered separately)
    const box = h("div", { class: "list-cards-group", dataset: { kind } });
    items.forEach((l) => box.appendChild(listCard(l, items.length > 1, select)));
    if (items.length > 1) {
      makeSortable(box, {
        itemSelector: ".list-card", handleSelector: ".grip", grid: true,
        onReorder: async (after) => {
          try { await api("/api/lists/reorder", { method: "POST", body: { kind, ordered_ids: after } }); }
          catch (e) { fail(e); renderLists(); }
        },
      });
    }
    return box;
  }
  cardsBox.append(group("shared", shared), group("personal", personal));

  const newBtn = h("button", { class: "btn-primary", type: "button", onclick: () => newListModal() }, "+ New list");
  mount(root, h("div", { class: "page-head" }, h("h2", null, "Lists"), newBtn), cardsBox, detailBox);
  openDetail();
}

function listCard(l, canDrag, onSelect) {
  const active = l.id === listUI.openId;
  const card = h("div", { class: "list-card" + (active ? " active" : ""), dataset: { id: l.id }, tabindex: "0", role: "button", "aria-pressed": active ? "true" : "false" },
    h("div", { class: "lc-body" },
      h("div", { class: "lc-name" }, l.name),
      h("div", { class: "lc-meta" },
        h("span", { class: "chip" }, l.kind === "shared" ? "Shared" : "Only me"),
        h("span", { class: "count-badge", title: "Open tasks" }, `${l.openCount} open`))),
    canDrag ? h("button", { class: "grip", type: "button", title: "Drag to reorder (or focus and press Alt+arrow keys)", "aria-label": `Reorder ${l.name}` }, "⠿") : null);
  card.addEventListener("click", (e) => { if (!e.target.closest(".grip")) onSelect(l.id); });
  card.addEventListener("keydown", (e) => {
    if (e.target === card && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onSelect(l.id); }
  });
  return card;
}

function newListModal() {
  const name = h("input", { type: "text", maxlength: "60", placeholder: "List name", "aria-label": "New list name" });
  let kind = "personal";
  const seg = segmented([["personal", "Only me"], ["shared", "Shared"]], kind, (v) => { kind = v; });
  const err = h("div", { class: "error-text" });
  const ok = h("button", { class: "btn-primary", type: "button" }, "Create list");
  const m = openModal("New list", h("div", null,
    h("div", { class: "form-row" }, name),
    h("div", { class: "form-row" }, seg.el),
    h("div", { class: "hint" }, "“Only me” lists are private to you; shared lists are visible to the whole household."),
    err, h("div", { class: "actions" }, ok)), { sheet: true });
  const go = async () => {
    err.textContent = "";
    if (!name.value.trim()) { name.focus(); return; }
    ok.disabled = true;
    try {
      const l = await api("/api/lists", { method: "POST", body: { name: name.value.trim(), kind } });
      listUI.openId = l.id;
      m.close();
      renderLists();
    } catch (e) { err.textContent = e.message; ok.disabled = false; }
  };
  ok.addEventListener("click", go);
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  setTimeout(() => name.focus(), 0);
}

function segmented(options, current, onChange) {
  const buttons = options.map(([value, label]) => {
    const b = h("button", { type: "button", class: value === current ? "active" : "" }, label);
    b.addEventListener("click", () => {
      buttons.forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      onChange(value);
    });
    return b;
  });
  return { el: h("div", { class: "segmented" }, buttons), buttons };
}

// ---- list detail ----

const DEFAULT_VIEW = { show: "open", sort: null, assignee: "", priority: "", type: "", place: "", completion: "" };
function loadView(listId) {
  try { return Object.assign({}, DEFAULT_VIEW, JSON.parse(lsGet("taskView:" + listId) || "{}")); }
  catch (e) { return Object.assign({}, DEFAULT_VIEW); }
}
function saveView(listId, v) { lsSet("taskView:" + listId, JSON.stringify(v)); }
function activeFilters(v) { return ["assignee", "priority", "type", "place", "completion"].filter((k) => v[k]); }

async function renderListDetail(list, box, { onCounts = () => {}, onRenamed = () => {}, onDeleted = () => {} } = {}) {
  const listId = list.id;
  const ctx = { id: list.id, kind: list.kind, ownerUserId: list.ownerUserId };
  const view = loadView(listId);

  const rename = () => {
    const input = h("input", { type: "text", maxlength: "60", value: list.name, "aria-label": "List name" });
    const err = h("div", { class: "error-text" });
    const ok = h("button", { class: "btn-primary", type: "button" }, "Rename");
    const body = h("div", null, h("div", { class: "form-row" }, input), err, h("div", { class: "actions" }, ok));
    const m = openModal("Rename list", body, { sheet: true });
    const go = async () => {
      try { await api(`/api/lists/${listId}`, { method: "PATCH", body: { name: input.value } }); m.close(); onRenamed(); }
      catch (e) { err.textContent = e.message; }
    };
    ok.addEventListener("click", go);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  };
  const del = async () => {
    const open = list.openCount ? ` (${list.openCount} open task${list.openCount === 1 ? "" : "s"})` : "";
    if (!confirm(`Delete the list “${list.name}” and every task in it${open}? This can't be undone.`)) return;
    try { await api(`/api/lists/${listId}`, { method: "DELETE" }); toast("List deleted"); onDeleted(); } catch (e) { fail(e); }
  };

  const header = h("div", { class: "page-head list-detail-head" },
    h("h3", null, list.name, " ", h("span", { class: "chip" }, list.kind === "shared" ? "Shared" : "Only me")),
    h("div", { class: "list-actions" },
      h("button", { class: "btn-secondary btn-small", type: "button", title: "Rename this list", onclick: rename }, "✎ Rename"),
      h("button", { class: "btn-danger btn-small", type: "button", title: "Delete this list and its tasks", onclick: del }, "🗑 Delete list")));

  const toolbarBox = h("div");
  const taskArea = h("div");
  const quickBox = h("div");

  const sortFor = () => view.sort || (view.show === "completed" ? "completed" : "manual");
  const dragOk = () => view.show === "open" && sortFor() === "manual" && activeFilters(view).length === 0;

  async function loadTasks() {
    const q = new URLSearchParams({ show: view.show, sort: sortFor() });
    ["assignee", "priority", "type", "place", "completion"].forEach((k) => { if (view[k]) q.set(k, view[k]); });
    try {
      const tasks = await api(`/api/lists/${listId}/tasks?${q}`);
      renderTasks(tasks);
      onCounts();
    } catch (e) { mount(taskArea, errorCard(e, loadTasks)); }
  }

  function renderTasks(tasks) {
    const drag = dragOk();
    const box = h("div", { class: "task-list" });
    tasks.forEach((t) => box.appendChild(taskRow(t, { onChange: loadTasks, grip: drag && tasks.length > 1 })));
    const notes = [];
    if (view.show === "completed") notes.push(h("div", { class: "hint", style: "margin-bottom:8px" }, "Completed tasks are cleared automatically 60 days after they were completed."));
    else if (!drag && tasks.length > 1) notes.push(h("div", { class: "hint", style: "margin-bottom:8px" }, "Dragging is available with the Manual sort and no filters."));
    if (!tasks.length) {
      const filtered = activeFilters(view).length > 0;
      mount(taskArea, ...notes, h("div", { class: "card empty" },
        view.show === "completed" ? (filtered ? "No completed tasks match those filters." : "Nothing completed yet.")
          : (filtered ? "No tasks match those filters." : "No tasks yet — add one above.")));
      return;
    }
    if (drag && tasks.length > 1) {
      makeSortable(box, {
        itemSelector: ".task-row", handleSelector: ".grip",
        onReorder: async (after) => {
          try { await api(`/api/lists/${listId}/tasks/reorder`, { method: "POST", body: { ordered_ids: after } }); }
          catch (e) { fail(e); loadTasks(); }
        },
      });
    }
    mount(taskArea, ...notes, box);
  }

  function renderToolbar() {
    const change = (key) => (e) => { view[key] = e.target.value; saveView(listId, view); renderToolbar(); loadTasks(); };
    const sel = (key, options, label) =>
      h("select", { value: view[key], "aria-label": label, title: label, onchange: change(key) }, options.map(([v, l]) => h("option", { value: v }, l)));

    const seg = segmented([["open", "Upcoming"], ["completed", "Completed"]], view.show, (v) => {
      view.show = v;
      if (view.sort && ((v === "open" && view.sort === "completed") || (v === "completed" && view.sort === "manual"))) view.sort = null;
      saveView(listId, view);
      renderToolbar();
      renderQuick();
      loadTasks();
    });

    const sortOpts = view.show === "completed"
      ? [["completed", "Recently completed"], ["due", "Due date"], ["priority", "Priority"], ["assignee", "Assignee"], ["created", "Recently added"]]
      : [["manual", "Manual order"], ["due", "Due date"], ["priority", "Priority"], ["assignee", "Assignee"], ["created", "Recently added"]];
    const sortSel = h("select", { value: sortFor(), "aria-label": "Sort", title: "Sort", onchange: (e) => { view.sort = e.target.value; saveView(listId, view); renderToolbar(); loadTasks(); } },
      sortOpts.map(([v, l]) => h("option", { value: v }, "Sort: " + l)));

    const people = state.users.filter((u) => !u.disabled || u.id === view.assignee);
    const filters = [
      sel("assignee", [["", "Anyone"], ["me", "Me"], ["unassigned", "Unassigned"], ...people.map((u) => [u.id, u.name])], "Assignee"),
      sel("priority", [["", "Any priority"], ["high", "High"], ["medium", "Medium"], ["low", "Low"], ["none", "No priority"]], "Priority"),
      sel("type", [["", "Any type"], ["none", "No type"], ...state.types.map((t) => [t.id, (t.icon ? t.icon + " " : "") + t.name])], "Type"),
      sel("place", [["", "Any place"], ...state.places.map((p) => [p.id, p.name])], "Place"),
      sel("completion", [["", "Any completion"], ["required", "Required"], ["optional", "Optional"]], "Completion"),
    ];
    const clearBtn = activeFilters(view).length
      ? h("button", { class: "link-btn", type: "button", onclick: () => { ["assignee", "priority", "type", "place", "completion"].forEach((k) => { view[k] = ""; }); saveView(listId, view); renderToolbar(); loadTasks(); } }, "Clear filters")
      : null;
    mount(toolbarBox, h("div", { class: "toolbar" }, seg.el, sortSel, filters, clearBtn));
  }

  function renderQuick() {
    if (view.show === "completed") { clear(quickBox); return; }
    mount(quickBox, quickAddCard({ list: ctx, onAdded: loadTasks }));
  }

  mount(box, header, quickBox, toolbarBox, taskArea);
  renderQuick();
  renderToolbar();
  await loadTasks();
}

// =====================================================================
// Schedule exceptions — used by both the Calendar day panel and the Schedule tab
// =====================================================================

function exceptionForm({ itemId, kind, date, onDone, onCancel }) {
  const reason = h("input", { type: "text", maxlength: "60", placeholder: "Reason (optional), e.g. Holiday", "aria-label": "Reason" });
  const toDate = kind === "move" ? h("input", { type: "date", min: todayIso(), "aria-label": "Move to" }) : null;
  const pickDate = kind === "add" && !date ? h("input", { type: "date", min: todayIso(), "aria-label": "Extra date" }) : null;
  const err = h("span", { class: "error-text", style: "flex-basis:100%" });
  const go = h("button", { class: "btn-primary btn-small", type: "button" }, kind === "skip" ? "Skip it" : kind === "move" ? "Move it" : "Add it");
  const cancel = h("button", { class: "btn-ghost btn-small", type: "button", onclick: onCancel }, "Cancel");
  go.addEventListener("click", async () => {
    err.textContent = "";
    const body = { kind, date };
    if (pickDate) {
      if (!pickDate.value) { err.textContent = "Pick the extra date."; return; }
      body.date = pickDate.value;
    }
    if (toDate) {
      if (!toDate.value) { err.textContent = "Pick the date to move it to."; return; }
      body.to_date = toDate.value;
    }
    if (reason.value.trim()) body.reason = reason.value.trim();
    go.disabled = true;
    try {
      await api(`/api/schedule/${itemId}/exceptions`, { method: "POST", body });
      toast(kind === "skip" ? "Skipped" : kind === "move" ? "Moved" : "Extra date added");
      onDone();
    } catch (e) { err.textContent = e.message; go.disabled = false; }
  });
  return h("div", { class: "inline-form", style: "flex:1 0 100%" },
    pickDate ? h("span", null, "Extra date") : null, pickDate,
    toDate ? h("span", null, "Move to") : null, toDate, reason, go, cancel, err);
}

// Buttons for one occurrence: Skip / Move… when it is normal, Undo when it carries an exception.
function pickupActions(itemId, e, onDone) {
  if (e.date < todayIso()) return null;
  const wrap = h("span", { style: "display:contents" });
  const holder = h("span", { style: "display:contents" });
  const showForm = (kind) => {
    mount(holder, exceptionForm({ itemId, kind, date: e.date, onDone, onCancel: () => clear(holder) }));
  };
  const undo = h("button", { class: "btn-ghost btn-small", type: "button" }, "Undo");
  undo.addEventListener("click", async () => {
    undo.disabled = true;
    try { await api(`/api/schedule/${itemId}/exceptions/${e.exceptionId}`, { method: "DELETE" }); toast("Undone"); onDone(); }
    catch (err) { fail(err); undo.disabled = false; }
  });
  if (e.status === "normal") {
    wrap.append(h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => showForm("skip") }, "Skip"),
      h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => showForm("move") }, "Move…"));
  } else if (e.exceptionId) {
    wrap.append(undo);
  }
  wrap.append(holder);
  return wrap;
}

function statusChip(e) {
  switch (e.status) {
    case "skipped": return h("span", { class: "chip" }, "Skipped");
    case "moved_away": return h("span", { class: "chip" }, `Moved to ${fmtDate(e.movedTo)}`);
    case "moved_here": return h("span", { class: "chip sched" }, `Moved from ${fmtDate(e.movedFrom)}`);
    case "added": return h("span", { class: "chip sched" }, "Extra");
    default: return null;
  }
}

// =====================================================================
// Calendar
// =====================================================================

const cal = {
  cursor: null,                    // first day of the shown month, "YYYY-MM-01"
  view: null,                      // "month" | "agenda"
  selected: null,                  // selected day (ISO)
  type: "", assignee: "", showCompleted: false,
  raw: null, data: null, overdue: 0,
};

// Which schedule items the calendar shows — remembered per person in this browser. Recurring items can
// crowd out the one appointment you're looking for, so any of them (or all) can be hidden here.
function schedPrefKey() { return "calendarSchedule:" + (actingUserId() || ""); }
function schedPref() {
  try { const p = JSON.parse(lsGet(schedPrefKey()) || "null"); if (p && Array.isArray(p.hidden)) return { all: p.all !== false, hidden: p.hidden, maint: p.maint !== false }; } catch (e) { /* ignore */ }
  return { all: true, hidden: [], maint: true };
}
function setSchedPref(p) { lsSet(schedPrefKey(), JSON.stringify(p)); }
function schedVisible(itemId) { const p = schedPref(); return p.all && !p.hidden.includes(itemId); }
function applyScheduleFilter() {
  cal.data = cal.raw ? Object.assign({}, cal.raw, { schedule: cal.raw.schedule.filter((e) => schedVisible(e.itemId)) }) : null;
}

function weekStartIndex() { return sundayFirst() ? 0 : 1; }

function calRange() {
  if (cal.view === "agenda") return { from: todayIso(), to: addDays(todayIso(), 29) };
  const first = parseIso(cal.cursor);
  const shift = (first.getDay() - weekStartIndex() + 7) % 7;
  const from = addDays(cal.cursor, -shift);
  return { from, to: addDays(from, 41) };       // six visible weeks = 41 days apart, well under the 92-day cap
}

async function renderCalendar() {
  const root = $("#tab-calendar");
  if (!cal.view) cal.view = window.matchMedia("(max-width: 760px)").matches ? "agenda" : "month";
  if (!cal.cursor) cal.cursor = todayIso().slice(0, 8) + "01";
  if (!cal.selected) cal.selected = todayIso();
  mount(root, pageHead("Calendar"), spinner());
  await loadCalendar();
}

async function loadCalendar() {
  const root = $("#tab-calendar");
  const { from, to } = calRange();
  const q = new URLSearchParams({ from, to, show: cal.showCompleted ? "all" : "open", schedule: schedPref().all ? "1" : "0",
    maintenance: schedPref().maint ? "1" : "0" });
  if (cal.type) q.set("type", cal.type);
  if (cal.assignee) q.set("assignee", cal.assignee);
  try {
    const [data, dash] = await Promise.all([api(`/api/calendar?${q}`), api("/api/dashboard").catch(() => null)]);
    state.today = data.today;
    cal.raw = data;
    applyScheduleFilter();
    cal.overdue = dash ? dash.counts.overdue : 0;
    paintCalendar();
  } catch (e) { mount(root, pageHead("Calendar"), errorCard(e, loadCalendar)); }
}

function groupByDate(data) {
  const by = {};
  const slot = (d) => (by[d] = by[d] || { tasks: [], sched: [] });
  data.tasks.forEach((t) => slot(t.dueDate).tasks.push(t));
  data.schedule.forEach((s) => slot(s.date).sched.push(s));
  (data.maintenance || []).forEach((m) => { const x = slot(m.date); (x.maint = x.maint || []).push(m); });
  return by;
}

function calTaskChip(t) {
  const cls = "cal-chip" + (t.overdue ? " overdue" : "") + (t.past ? " past" : "") + (t.completed ? " done" : "");
  return h("span", { class: cls, title: t.title, style: t.type && t.type.color ? `border-left-color:${t.type.color}` : null },
    t.dueTime ? t.dueTime + " " : "", t.type && t.type.icon ? t.type.icon + " " : "", t.title);
}
function calSchedChip(s) {
  const struck = s.status === "skipped" || s.status === "moved_away";
  const tag = s.status === "added" ? " (extra)" : s.status === "moved_here" ? " (moved)" : s.status === "skipped" ? " (skipped)" : "";
  const range = timeRange(s);
  const title = [range, s.name + tag, s.assigneeName ? `for ${s.assigneeName}` : null, s.private ? "private" : null].filter(Boolean).join(" · ");
  // A month cell is narrow: a timed chip puts the time range (and 🔒) on a
  // second line and drops the glyph, so the name stays readable.
  const lock = s.private ? "🔒 " : "";
  return h("span", { class: "cal-chip sched" + (struck ? " struck" : ""), title },
    range ? null : [lock, scheduleGlyph(s.icon), " "],
    s.assigneeName ? h("span", { class: "cal-initial", "aria-label": s.assigneeName }, initialOf(s.assigneeName)) : null,
    s.name, tag, range ? h("span", { class: "cal-chip-time" }, lock, range) : null);
}

// One day's chips, tasks and schedule entries merged: all-day first, then by time
function dayChips(slot) {
  const keyed = [
    ...slot.tasks.map((t, i) => [t.dueTime || "", 0, i, calTaskChip(t)]),
    ...slot.sched.map((e, i) => [e.startTime || "", 1, i, calSchedChip(e)]),
    ...(slot.maint || []).map((e, i) => ["", 2, i, calMaintChip(e)]),
  ];
  keyed.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1] || a[2] - b[2]));
  return keyed.map((k) => k[3]);
}

async function scheduleFilterDialog() {
  let items = [];
  try { items = await api("/api/schedule"); } catch (e) { fail(e); return; }
  const pref = schedPref();
  const master = h("input", { type: "checkbox", checked: pref.all });
  const boxes = items.map((it) => {
    const cb = h("input", { type: "checkbox", checked: !pref.hidden.includes(it.id) });
    cb.dataset.id = it.id;
    const who = it.assignedTo ? (state.users.find((u) => u.id === it.assignedTo) || {}).name : null;
    return h("label", { class: "sched-pick-row" }, cb, scheduleGlyph(it.icon),
      h("span", null, it.name, who ? h("span", { class: "hint" }, ` · for ${who}`) : null, it.private ? h("span", { class: "hint" }, " · 🔒") : null));
  });
  const list = h("div", { class: "sched-pick" }, boxes);
  const sync = () => { list.classList.toggle("off", !master.checked); boxes.forEach((b) => { b.querySelector("input").disabled = !master.checked; }); };
  const save = () => {
    const hidden = boxes.map((b) => b.querySelector("input")).filter((cb) => !cb.checked).map((cb) => cb.dataset.id);
    const before = schedPref();
    const maintOn = maintBox ? maintBox.checked : before.maint;
    setSchedPref({ all: master.checked, hidden, maint: maintOn });
    if (before.all !== master.checked || before.maint !== maintOn) loadCalendar();          // the server leaves them out entirely when all are hidden
    else { applyScheduleFilter(); paintCalendar(); }
  };
  const maintBox = maintEnabled() ? h("input", { type: "checkbox", checked: pref.maint }) : null;
  if (maintBox) maintBox.addEventListener("change", save);
  master.addEventListener("change", () => { sync(); save(); });
  boxes.forEach((b) => b.querySelector("input").addEventListener("change", save));
  const setAll = (on) => { boxes.forEach((b) => { b.querySelector("input").checked = on; }); save(); };
  sync();
  openModal("Schedule items in the calendar", h("div", null,
    h("p", { class: "hint" }, "Hide recurring items (like trash day) to find tasks and appointments more easily. Only the calendar changes — the items, their reminders and sensors stay as they are. Remembered on this device."),
    h("label", { class: "sched-pick-row master" }, master, h("strong", null, "Show schedule items")),
    items.length ? h("div", { class: "sched-pick-btns" },
      h("button", { class: "btn-ghost", type: "button", onclick: () => setAll(true) }, "Show all"),
      h("button", { class: "btn-ghost", type: "button", onclick: () => setAll(false) }, "Hide all")) : null,
    items.length ? list : h("p", { class: "hint" }, "There are no schedule items."),
    maintBox ? h("label", { class: "sched-pick-row master", style: "margin-top:10px" }, maintBox, h("strong", null, "🔧 Show maintenance")) : null));
}

function paintCalendar() {
  const root = $("#tab-calendar");
  const data = cal.data;
  const by = groupByDate(data);
  const again = () => loadCalendar();

  // ---- toolbar ----
  const monthTitle = MONTHS_FULL[parseIso(cal.cursor).getMonth()] + " " + parseIso(cal.cursor).getFullYear();
  const shiftMonth = (n) => { const d = parseIso(cal.cursor); d.setMonth(d.getMonth() + n, 1); cal.cursor = isoOf(d); loadCalendar(); };
  const viewSeg = segmented([["month", "Month"], ["agenda", "Agenda"]], cal.view, (v) => { cal.view = v; loadCalendar(); });
  const typeSel = h("select", { value: cal.type, "aria-label": "Type", onchange: (e) => { cal.type = e.target.value; loadCalendar(); } },
    h("option", { value: "" }, "Any type"), state.types.map((t) => h("option", { value: t.id }, (t.icon ? t.icon + " " : "") + t.name)));
  const asnSel = h("select", { value: cal.assignee, "aria-label": "Assignee", onchange: (e) => { cal.assignee = e.target.value; loadCalendar(); } },
    h("option", { value: "" }, "Anyone"), h("option", { value: "me" }, "Me"), h("option", { value: "unassigned" }, "Unassigned"),
    state.users.filter((u) => !u.disabled).map((u) => h("option", { value: u.id }, u.name)));
  const weekSel = h("select", { value: sundayFirst() ? "sunday" : "monday", "aria-label": "Week starts on", title: "Week starts on",
    onchange: (e) => { lsSet("calendarWeekStart", e.target.value); loadCalendar(); } },
    h("option", { value: "sunday" }, "Week starts Sun"), h("option", { value: "monday" }, "Week starts Mon"));
  const showDone = h("input", { type: "checkbox", checked: cal.showCompleted });
  showDone.addEventListener("change", () => { cal.showCompleted = showDone.checked; loadCalendar(); });
  const pref = schedPref();
  const schedLabel = (!pref.all ? "Schedule items: hidden" : pref.hidden.length ? `Schedule items: ${pref.hidden.length} hidden` : "Schedule items: all")
    + (maintEnabled() && !pref.maint ? " · no maintenance" : "");
  const schedBtn = h("button", { class: "btn-ghost sched-filter" + (!pref.all || pref.hidden.length || (maintEnabled() && !pref.maint) ? " on" : ""), type: "button",
    onclick: scheduleFilterDialog }, "🗓 " + schedLabel + " ▾");

  const nav = cal.view === "month"
    ? h("div", { class: "cal-toolbar" },
      h("button", { class: "btn-ghost", type: "button", "aria-label": "Previous month", onclick: () => shiftMonth(-1) }, "‹"),
      h("span", { class: "title" }, monthTitle),
      h("button", { class: "btn-ghost", type: "button", "aria-label": "Next month", onclick: () => shiftMonth(1) }, "›"),
      h("button", { class: "btn-ghost", type: "button", onclick: () => { cal.cursor = todayIso().slice(0, 8) + "01"; cal.selected = todayIso(); loadCalendar(); } }, "Today"),
      viewSeg.el, weekSel)
    : h("div", { class: "cal-toolbar" }, h("span", { class: "title", style: "text-align:left" }, "Next 30 days"), viewSeg.el);
  const filters = h("div", { class: "cal-toolbar" }, typeSel, asnSel,
    h("label", { class: "mini-toggle" }, showDone, "Show completed"),
    schedBtn);

  const banner = cal.overdue > 0
    ? h("div", { class: "cal-banner" }, `${cal.overdue} overdue — `, h("button", { class: "link-btn", type: "button", onclick: () => showTab("dashboard") }, "see the Dashboard"))
    : null;
  const undated = data.undatedCount > 0
    ? h("div", { class: "hint", style: "margin-top:10px" }, `${data.undatedCount} open task${data.undatedCount === 1 ? " has" : "s have"} no date — `,
      h("button", { class: "link-btn", type: "button", onclick: () => { listUI.openId = null; showTab("lists"); } }, "see your lists"))
    : null;

  // ---- body ----
  let body;
  if (cal.view === "month") {
    const { from } = calRange();
    const monthNo = parseIso(cal.cursor).getMonth();
    const dow = [];
    for (let i = 0; i < 7; i++) dow.push(h("div", { class: "cal-dow" }, DOW[(weekStartIndex() + i) % 7]));
    const cells = [];
    for (let i = 0; i < 42; i++) {
      const date = addDays(from, i);
      const slot = by[date] || { tasks: [], sched: [], maint: [] };
      const chips = dayChips(slot);
      const shown = chips.slice(0, 3);
      const cell = h("div", {
        class: "cal-cell" + (parseIso(date).getMonth() !== monthNo ? " other" : "") + (date === todayIso() ? " today" : "") + (date === cal.selected ? " selected" : ""),
        dataset: { date }, tabindex: "0", role: "button", "aria-label": fmtDate(date, { year: true }),
      }, h("div", { class: "num" }, parseIso(date).getDate()), shown, chips.length > 3 ? h("div", { class: "cal-more" }, `+${chips.length - 3} more`) : null);
      const select = () => { cal.selected = date; paintCalendar(); };
      cell.addEventListener("click", select);
      cell.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } });
      cells.push(cell);
    }
    body = h("div", { class: "cal-layout" },
      h("div", { class: "cal-main" }, h("div", { class: "cal-grid" }, dow, cells)),
      h("div", { class: "day-panel" }, dayPanel(cal.selected, by[cal.selected] || { tasks: [], sched: [] }, again)));
  } else {
    const days = [];
    for (let i = 0; i < 30; i++) {
      const date = addDays(todayIso(), i);
      const slot = by[date];
      if (!slot || (!slot.tasks.length && !slot.sched.length && !(slot.maint || []).length)) continue;
      days.push(h("div", { class: "agenda-day" + (date === todayIso() ? " today" : "") },
        h("h4", null, `${fmtDate(date)} · ${relLabel(date)}`), dayContent(date, slot, again)));
    }
    body = days.length ? h("div", { class: "card" }, days) : h("div", { class: "card empty" }, "Nothing dated in the next 30 days.");
  }
  mount(root, pageHead("Calendar"), banner, nav, filters, body, undated);
}

function dayContent(date, slot, again) {
  const parts = [];
  if (slot.tasks.length) parts.push(h("div", { class: "task-list" }, slot.tasks.map((t) => taskRow(t, { onChange: again, showList: true }))));
  slot.sched.forEach((s) => {
    const struck = s.status === "skipped" || s.status === "moved_away";
    const range = timeRange(s);
    parts.push(h("div", { class: "sched-entry" + (struck ? " struck" : "") },
      h("span", null, scheduleGlyph(s.icon)), h("span", { class: "grow" }, h("b", null, s.name)),
      range ? h("span", { class: "chip" }, "🕘 ", range) : null,
      s.assigneeName ? h("span", { class: "chip" }, "👤 ", s.assigneeName) : null,
      s.private ? h("span", { class: "chip", title: "Only you can see this" }, "🔒") : null,
      linkChip(s.url),
      statusChip(s), pickupActions(s.itemId, s, again)));
  });
  (slot.maint || []).forEach((m) => parts.push(maintDayEntry(m, again)));
  return parts;
}

function dayPanel(date, slot, again) {
  const title = h("input", { type: "text", maxlength: "200", placeholder: "Add a task on this date…", "aria-label": "Add a task on this date" });
  const add = h("button", { class: "btn-primary btn-small", type: "button" }, "Add");
  const go = async () => {
    const t = title.value.trim();
    if (!t) return;
    add.disabled = true;
    try { await api("/api/tasks", { method: "POST", body: { title: t, due_date: date } }); toast("Added to My Tasks"); again(); }
    catch (e) { fail(e); add.disabled = false; }
  };
  add.addEventListener("click", go);
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const empty = !slot.tasks.length && !slot.sched.length && !(slot.maint || []).length;
  return h("div", { class: "card" },
    h("h3", null, h("span", null, fmtDate(date, { year: true })), h("span", { class: "hint" }, relLabel(date))),
    h("div", { class: "quick-add", style: "margin-bottom:10px" }, title, add),
    empty ? h("div", { class: "hint" }, "Nothing on this day.") : dayContent(date, slot, again),
    slot.sched.length ? h("div", { class: "hint", style: "margin-top:8px" }, h("button", { class: "link-btn", type: "button", onclick: () => showTab("schedule") }, "Open the Schedule tab")) : null);
}

// =====================================================================
// Schedule tab — recurring, household-wide items that become HA sensors
// =====================================================================

const WEEKDAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];      // ISO: Mon=1 … Sun=7
const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const NTH_LABELS = [["1", "1st"], ["2", "2nd"], ["3", "3rd"], ["4", "4th"], ["-1", "Last"]];
const schedUI = { open: new Set(), filter: lsGet("scheduleFilter") || "" };   // "" | "me" | "unassigned"
const SCHEDULE_FILTERS = [["", "All"], ["me", "Mine"], ["unassigned", "Household"]];

// "18:00–19:00" for a timed schedule item / entry, else null
function timeRange(x) { return x.startTime && x.endTime ? `${x.startTime}–${x.endTime}` : null; }
function initialOf(name) { return name ? name.trim().charAt(0).toUpperCase() : ""; }

function isoWeekday(iso) { return parseIso(iso).getDay() || 7; }
function nextSelectedWeekday(days) {
  if (!days.length) return todayIso();
  for (let i = 0; i < 7; i++) {
    const d = addDays(todayIso(), i);
    if (days.includes(isoWeekday(d))) return d;
  }
  return todayIso();
}

// The rule grammar (§8a) is assembled here and only here; the server is the only validator.
function buildRule(s) {
  switch (s.kind) {
    case "daily": return "daily";
    case "weekly": return `weeks:1:${[...s.days].sort().join(",")}`;
    case "weeksN": return `weeks:${s.n}:${[...s.days].sort().join(",")}`;
    case "monthly": return s.months === 1 ? `monthly:${s.dom}` : `months:${s.months}:${s.dom}`;
    case "yearly": return `years:${s.years}`;
    case "nth": return `nth:${s.nth}:${s.wd}`;
    case "everyDays": return `every:${s.n}`;
    default: return "daily";
  }
}
function parseRule(rule) {
  const s = { kind: "daily", n: 2, days: [1], dom: 1, nth: 1, wd: 1, months: 1, years: 1 };
  let m;
  if ((m = /^weeks:(\d+):([\d,]+)$/.exec(rule))) {
    s.kind = Number(m[1]) === 1 ? "weekly" : "weeksN";
    s.n = Number(m[1]) === 1 ? 2 : Number(m[1]);
    s.days = m[2].split(",").map(Number);
  } else if ((m = /^every:(\d+)$/.exec(rule))) { s.kind = "everyDays"; s.n = Number(m[1]); }
  else if ((m = /^monthly:(\d+)$/.exec(rule))) { s.kind = "monthly"; s.dom = Number(m[1]); s.months = 1; }
  else if ((m = /^months:(\d+):(\d+)$/.exec(rule))) { s.kind = "monthly"; s.months = Number(m[1]); s.dom = Number(m[2]); }
  else if ((m = /^years:(\d+)$/.exec(rule))) { s.kind = "yearly"; s.years = Number(m[1]); }
  else if ((m = /^nth:(-?\d+):(\d)$/.exec(rule))) { s.kind = "nth"; s.nth = Number(m[1]); s.wd = Number(m[2]); }
  return s;
}
// Smallest gap between two consecutive occurrences, for the "always on" warning.
function minGapDays(s) {
  if (s.kind === "daily") return 1;
  if (s.kind === "everyDays") return s.n;
  if (s.kind === "monthly") return 28 * s.months;
  if (s.kind === "yearly") return 365 * s.years;
  if (s.kind === "nth") return 28;
  const days = [...s.days].sort((a, b) => a - b);
  const period = 7 * (s.kind === "weekly" ? 1 : s.n);
  if (!days.length) return period;
  let gap = period - (days[days.length - 1] - days[0]);
  for (let i = 1; i < days.length; i++) gap = Math.min(gap, days[i] - days[i - 1]);
  return gap;
}

function leadLabel(n) {
  if (n === 0) return "Day of only";
  return `${n} day${n === 1 ? "" : "s"} before (on for ${n + 1} days)`;
}

async function renderSchedule() {
  const root = $("#tab-schedule");
  mount(root, pageHead("Schedule"), spinner());
  try {
    if (!SCHEDULE_FILTERS.some(([v]) => v === schedUI.filter)) schedUI.filter = "";
    const items = await api("/api/schedule" + (schedUI.filter ? `?assignee=${schedUI.filter}` : ""));
    const again = () => renderSchedule();
    const newBtn = h("button", { class: "btn-primary", type: "button", onclick: () => openScheduleForm(null, again) }, "+ New item");
    const filter = segmented(SCHEDULE_FILTERS, schedUI.filter, (v) => { schedUI.filter = v; lsSet("scheduleFilter", v); again(); });
    const intro = h("div", { class: "hint", style: "margin-bottom:12px" },
      "Things that repeat and are never “completed” — bin day, recycling, the gutter clean, or one person's weekly class. Each can become a Home Assistant sensor to build automations from: an all-day item switches on ahead of the day, a timed one only while it's happening. An item that's for one person is included in their reminders and can be kept private to them.");
    const empty = schedUI.filter === "me" ? "Nothing on the schedule is for you yet."
      : schedUI.filter === "unassigned" ? "No household items yet."
        : "No schedule items yet. Add “Trash pickup — every Monday” to get started.";
    const box = items.length
      ? h("div", { class: "card" }, items.map((it) => scheduleRow(it, again)))
      : h("div", { class: "card empty" }, empty);
    mount(root, h("div", { class: "page-head" }, h("h2", null, "Schedule"), newBtn), intro,
      h("div", { class: "toolbar" }, filter.el), box);
    const mcard = await scheduleMaintCard();            // recurring upkeep has its own group, below
    if (mcard) root.appendChild(mcard);
  } catch (e) { mount(root, pageHead("Schedule"), errorCard(e, renderSchedule)); }
}

function scheduleRow(it, again) {
  const open = schedUI.open.has(it.id);
  const next = it.nextDate ? `${fmtDate(it.nextDate)} · ${relLabel(it.nextDate)}` : "No upcoming date";
  const toggle = h("button", { class: "btn-ghost btn-small", type: "button", "aria-expanded": open ? "true" : "false" }, open ? "Hide next dates ▴" : "Next dates ▾");
  toggle.addEventListener("click", () => { if (schedUI.open.has(it.id)) schedUI.open.delete(it.id); else schedUI.open.add(it.id); again(); });

  const range = timeRange(it);
  const chips = [
    it.assigneeName ? h("span", { class: "chip" }, "👤 ", it.assigneeName) : null,
    it.rotation && it.rotation.length ? h("span", { class: "chip", title: "Taken in turns, in this order" }, "🔁 ", it.rotationNames.join(" → ")) : null,
    it.nextTurnName ? h("span", { class: "chip on", title: "Whose turn the next date is" }, "Next: ", it.nextTurnName) : null,
    range ? h("span", { class: "chip" }, "🕘 ", range) : null,
    it.place ? h("a", { class: "chip btnlike", href: mapsUrl(it.place.address), target: "_blank", rel: "noopener noreferrer", title: `Open ${it.place.address} in maps` }, "📍 ", it.place.name) : null,
    it.place && range ? driveChip({ place: it.place, dueTime: it.startTime }) : null,
    it.visibility === "private" ? h("span", { class: "chip", title: `Only ${it.assigneeName || "its owner"} can see this` }, "🔒 Private") : null,
    linkChip(it.url),
  ].filter(Boolean);
  const sensorTitle = range ? `Sensor is on from ${it.startTime} to ${it.endTime} on each date`
    : `Sensor turns on ${it.leadDays} day${it.leadDays === 1 ? "" : "s"} ahead`;
  const head = h("div", { class: "sched-head" },
    h("span", { class: "sched-icon" }, scheduleGlyph(it.icon)),
    h("div", { class: "sched-info" },
      h("div", { class: "name" }, it.name),
      h("div", { class: "sub" }, it.ruleLabel, " · next ", next),
      chips.length ? h("div", { class: "task-meta" }, chips) : null,
      it.notes ? h("div", { class: "sub" }, it.notes) : null,
      it.published
        ? h("div", { class: "sub" }, h("code", { class: "entity" }, it.entityId), " ",
          h("button", { class: "icon-btn", type: "button", title: "Copy entity id", "aria-label": "Copy entity id", onclick: () => copyText(it.entityId) }, "⧉"))
        : null),
    it.published
      ? h("span", { class: "chip " + (it.sensorOn ? "on" : "off"), title: sensorTitle }, it.sensorOn ? "On now" : "Off")
      : h("span", { class: "chip off", title: "No Home Assistant sensor for this item" }, "Not published"),
    toggle,
    h("button", { class: "btn-secondary btn-small", type: "button", title: "Edit", "aria-label": `Edit ${it.name}`, onclick: () => openScheduleForm(it, again) }, "✎ Edit"),
    h("button", { class: "btn-danger btn-small", type: "button", title: "Delete", "aria-label": `Delete ${it.name}`, onclick: () => deleteScheduleItem(it, again) }, "🗑 Delete"));

  const row = h("div", { class: "sched-row" }, head);
  if (open) {
    const holder = h("div", { style: "display:contents" });
    const addBtn = h("button", { class: "btn-ghost btn-small", type: "button" }, "Add extra date…");
    addBtn.addEventListener("click", () => {
      mount(holder, exceptionForm({ itemId: it.id, kind: "add", date: null, onDone: again, onCancel: () => clear(holder) }));
    });
    row.appendChild(h("div", { class: "pickups" },
      it.upcoming.length ? it.upcoming.map((e) => {
        const struck = e.status === "skipped" || e.status === "moved_away";
        return h("div", { class: "pickup" + (struck ? " struck" : "") },
          h("span", { class: "pdate" }, fmtDate(e.date)), h("span", { class: "hint" }, relLabel(e.date)),
          statusChip(e), e.reason ? h("span", { class: "hint" }, `“${e.reason}”`) : null,
          e.turnName && !struck ? turnPicker(it, e, again) : null,
          pickupActions(it.id, e, again));
      }) : h("div", { class: "hint" }, "No upcoming dates."),
      h("div", { class: "pickup" }, addBtn, holder)));
  }
  return row;
}

// Taking turns (§5.4b): whose turn a date is, and handing it to someone else
function turnPicker(it, e, again) {
  const day = e.movedFrom || e.date;
  const people = state.users.filter((u) => !u.disabled || u.id === e.turn);
  const sel = h("select", { class: "turn-select" + (e.handedOver ? " handed" : ""), "aria-label": `Whose turn ${fmtDate(e.date)} is`,
    title: e.handedOver ? "Handed over — choose the usual person to undo" : "Whose turn — choose someone else to hand it over" },
    people.map((u) => h("option", { value: u.id }, `${u.name}'s turn`)));
  sel.value = e.turn;
  sel.addEventListener("change", async () => {
    try {
      await api(`/api/schedule/${it.id}/turns/${day}`, { method: "PUT", body: { user_id: sel.value } });
      toast(`${userName(sel.value) || "They"} ha${sel.value === actingUserId() ? "ve" : "s"} ${fmtDate(e.date)}`);
      again();
    } catch (err) { fail(err); sel.value = e.turn; }
  });
  return sel;
}

function scheduleDeleteQuestion(it) {
  return `Delete “${it.name}”?` + (it.published ? `\n\nIts Home Assistant entity (${it.entityId}) will be removed too.` : "");
}

async function deleteScheduleItem(it, again) {
  if (!confirm(scheduleDeleteQuestion(it))) return;
  try { await api(`/api/schedule/${it.id}`, { method: "DELETE" }); toast("Deleted"); again(); } catch (e) { fail(e); }
}

function openScheduleForm(item, onSaved) {
  const editing = !!item;
  const s = editing ? parseRule(item.rule) : { kind: "weekly", n: 2, days: [1], dom: 1, nth: 1, wd: 1, months: 1, years: 1 };
  let anchorTouched = editing;
  const originalRule = editing ? item.rule : null, originalAnchor = editing ? item.anchorDate : null;

  const name = h("input", { type: "text", maxlength: "60", value: editing ? item.name : "", placeholder: "e.g. Trash pickup" });
  const kindSel = h("select", { value: s.kind, "aria-label": "Repeats" },
    [["daily", "Daily"], ["weekly", "Weekly"], ["weeksN", "Every N weeks"], ["monthly", "Every N months on a day"], ["yearly", "Every N years"], ["nth", "Nth weekday of the month"], ["everyDays", "Every N days"]]
      .map(([v, l]) => h("option", { value: v }, l)));

  const dayBtns = WEEKDAY_LETTERS.map((letter, i) => {
    const b = h("button", { type: "button", class: s.days.includes(i + 1) ? "on" : "", title: WEEKDAY_NAMES[i], "aria-pressed": s.days.includes(i + 1) ? "true" : "false" }, letter);
    b.addEventListener("click", () => {
      const day = i + 1;
      s.days = s.days.includes(day) ? s.days.filter((d) => d !== day) : [...s.days, day];
      b.classList.toggle("on", s.days.includes(day));
      b.setAttribute("aria-pressed", s.days.includes(day) ? "true" : "false");
      snapAnchor();
      refresh();
    });
    return b;
  });
  const weeksN = h("select", { value: String(s.n), "aria-label": "Every how many weeks" }, Array.from({ length: 51 }, (_, i) => i + 2).map((n) => h("option", { value: String(n) }, String(n))));
  const daysN = h("input", { type: "number", min: "1", max: "999", value: String(s.kind === "everyDays" ? s.n : 3), "aria-label": "Every how many days" });
  const monthsN = h("select", { value: String(s.months), "aria-label": "Every how many months" },
    Array.from({ length: 36 }, (_, i) => i + 1).map((n) => h("option", { value: String(n) }, n === 1 ? "1 (every month)" : String(n))));
  const yearsN = h("select", { value: String(s.years), "aria-label": "Every how many years" },
    Array.from({ length: 10 }, (_, i) => i + 1).map((n) => h("option", { value: String(n) }, n === 1 ? "1 (every year)" : String(n))));
  const dom = h("select", { value: String(s.dom), "aria-label": "Day of month" }, Array.from({ length: 31 }, (_, i) => i + 1).map((n) => h("option", { value: String(n) }, String(n))));
  const nth = h("select", { value: String(s.nth), "aria-label": "Which one" }, NTH_LABELS.map(([v, l]) => h("option", { value: v }, l)));
  const nthWd = h("select", { value: String(s.wd), "aria-label": "Weekday" }, WEEKDAY_NAMES.map((n, i) => h("option", { value: String(i + 1) }, n)));

  const rowWeekdays = h("div", { class: "field wide" }, "On", h("div", { class: "weekday-buttons" }, dayBtns));
  const rowWeeksN = h("label", { class: "field" }, "Every … weeks", weeksN);
  const rowDaysN = h("label", { class: "field" }, "Every … days", daysN);
  const rowMonthsN = h("label", { class: "field" }, "Every … months", monthsN);
  const rowYearsN = h("label", { class: "field" }, "Every … years", yearsN);
  const rowDom = h("label", { class: "field" }, "Day of the month", dom);
  const rowNth = h("div", { class: "form-row", style: "flex:1 0 100%" }, h("label", { class: "field" }, "Which", nth), h("label", { class: "field" }, "Weekday", nthWd));

  const today = todayIso();
  const anchor = h("input", { type: "date", value: editing ? item.anchorDate : today });
  const anchorLabel = h("span", null, "Starting from");
  anchor.addEventListener("input", () => { anchorTouched = true; });
  const lead = h("select", { value: String(editing ? item.leadDays : 1), "aria-label": "Turn the sensor on in advance" },
    Array.from({ length: 8 }, (_, i) => h("option", { value: String(i) }, leadLabel(i))));
  const knownPreset = editing && item.icon && ICON_PRESETS.some((p) => p[0] === item.icon);
  const iconSel = h("select", { value: editing && item.icon ? (knownPreset ? item.icon : "custom") : "mdi:calendar-clock", "aria-label": "Icon" },
    ICON_PRESETS.map(([v, g, l]) => h("option", { value: v }, `${g} ${l}`)), h("option", { value: "custom" }, "Custom mdi: name…"));
  const iconCustom = h("input", { type: "text", maxlength: "60", placeholder: "mdi:trash-can-outline", value: editing && item.icon && !knownPreset ? item.icon : "", "aria-label": "Custom icon name" });
  const notes = h("textarea", { maxlength: "500", placeholder: "Notes (optional)", style: "min-height:52px" }, editing && item.notes ? item.notes : "");
  const urlIn = h("input", { type: "url", maxlength: "2000", inputmode: "url", placeholder: "https://…", value: editing && item.url ? item.url : "", "aria-label": "Link" });

  // who it's for, a time window, a place, privacy and the HA switch
  const people = state.users.filter((u) => !u.disabled || (editing && u.id === item.assignedTo));
  const forSel = h("select", { value: editing && item.assignedTo ? item.assignedTo : "", "aria-label": "For" },
    h("option", { value: "" }, "Household"), people.map((u) => h("option", { value: u.id }, u.name + (u.disabled ? " (disabled)" : ""))));
  // taking turns (§5.4b): household items only; the order people are picked in is the order of the turns
  const turns = editing && item.rotation ? [...item.rotation] : [];
  const turnsBox = h("div", { class: "turns-pick", role: "group", "aria-label": "Take turns" });
  function drawTurns() {
    mount(turnsBox, people.filter((u) => !u.disabled || turns.includes(u.id)).map((u) => {
      const at = turns.indexOf(u.id);
      return h("button", { type: "button", class: at >= 0 ? "on" : "", "aria-pressed": at >= 0 ? "true" : "false",
        onclick: () => { if (at >= 0) turns.splice(at, 1); else turns.push(u.id); drawTurns(); refresh(); } },
        at >= 0 ? `${at + 1}. ${u.name}` : u.name);
    }));
  }
  const turnsRow = h("div", { class: "field wide" }, "Take turns (optional) — pick people in order", turnsBox,
    h("div", { class: "hint" }, "Each date goes to the next person, counted from the first occurrence. Skipping a date doesn't change whose the next one is; you can hand a single date to someone else under Next dates."));
  const startIn = h("input", { type: "time", value: editing && item.startTime ? item.startTime : "", "aria-label": "Start time" });
  const endIn = h("input", { type: "time", value: editing && item.endTime ? item.endTime : "", "aria-label": "End time" });
  const place = placePicker(editing ? item : null);
  const privateCb = h("input", { type: "checkbox", checked: editing && item.visibility === "private" });
  const privateRow = h("label", { class: "mini-toggle" }, privateCb, "Only visible to me");
  const publishCb = h("input", { type: "checkbox", checked: editing ? item.exposeSensor : true });
  let publishTouched = editing;
  publishCb.addEventListener("change", () => { publishTouched = true; });
  const timesHint = h("div", { class: "hint" });
  const reminderHint = h("div", { class: "hint" });
  let myOffsets = null;          // the signed-in person's "before" reminders, for the hint below
  if (!isActingAsOther()) api("/api/prefs").then((p) => { myOffsets = p.reminderOffsets; refresh(); }).catch(() => { /* no hint */ });
  const privacyWarn = h("div", { class: "hint warn" });
  const leadField = h("label", { class: "field" }, "Turn the sensor on in advance", lead);
  const warn = h("div", { class: "hint warn" });
  const yearlyHint = h("div", { class: "hint" }, "Repeats on this date's month and day every N years — pick the date it is next due.");
  const err = h("div", { class: "error-text" });
  const save = h("button", { class: "btn-primary", type: "button" }, editing ? "Save" : "Create");
  const delBtn = h("button", { class: "btn-danger", type: "button" }, "Delete item");

  function nextDayOfMonth(dayOfMonth) {
    for (let i = 0; i < 63; i++) {
      const iso = addDays(today, i), dt = parseIso(iso);
      const last = new Date(dt.getFullYear(), dt.getMonth() + 1, 0).getDate();
      if (dt.getDate() === Math.min(dayOfMonth, last)) return iso;
    }
    return today;
  }
  function snapAnchor() {
    if (anchorTouched) return;
    if (s.kind === "weekly" || s.kind === "weeksN") anchor.value = nextSelectedWeekday(s.days);
    else if (s.kind === "monthly") anchor.value = nextDayOfMonth(s.dom);
    else anchor.value = today;
  }
  function readState() {
    s.kind = kindSel.value;
    s.n = s.kind === "weeksN" ? Number(weeksN.value) : Number(daysN.value) || 1;
    s.months = Number(monthsN.value); s.years = Number(yearsN.value);
    s.dom = Number(dom.value); s.nth = Number(nth.value); s.wd = Number(nthWd.value);
  }
  function refresh() {
    readState();
    const weekly = s.kind === "weekly" || s.kind === "weeksN";
    rowWeekdays.hidden = !weekly;
    rowWeeksN.hidden = s.kind !== "weeksN";
    rowDaysN.hidden = s.kind !== "everyDays";
    rowMonthsN.hidden = s.kind !== "monthly";
    rowYearsN.hidden = s.kind !== "yearly";
    rowDom.hidden = s.kind !== "monthly";
    rowNth.hidden = s.kind !== "nth";
    // weeks: / every: rules count from the first date, so it is labelled differently (§8a)
    anchorLabel.textContent = weekly || s.kind === "everyDays" || s.kind === "yearly" || (s.kind === "monthly" && s.months > 1) ? "First occurrence" : "Starting from";
    yearlyHint.hidden = s.kind !== "yearly";
    // a timed item's sensor is on only during its window, so the lead time doesn't apply
    const timed = !!(startIn.value && endIn.value);
    leadField.hidden = timed;
    timesHint.textContent = timed ? `The sensor is on only from ${startIn.value} to ${endIn.value} on each date.`
      : startIn.value || endIn.value ? "Set both a start and an end time, or leave both empty for an all-day item." : "";
    const gap = minGapDays(s);
    warn.textContent = !timed && Number(lead.value) >= gap ? "This sensor will always be on — the lead time is at least as long as the gap between occurrences." : "";
    // only the person an item is for can make it private (the server enforces this too)
    const forMe = !!forSel.value && forSel.value === actingUserId();
    turnsRow.hidden = !!forSel.value;
    privateRow.hidden = !forMe;
    if (!forMe) privateCb.checked = false;
    // who is reminded before it starts, and why not
    const timedNow = !!(startIn.value && endIn.value);
    if (!forSel.value && turns.length >= 2) reminderHint.textContent = "🔔 Whoever's turn it is gets it in their digest and their own reminders before it starts.";
    else if (!forSel.value) reminderHint.textContent = "🔕 Household items don't send reminders — choose a person under For to remind them, or have people take turns.";
    else if (!timedNow) reminderHint.textContent = forMe ? "🔔 All-day: it's in your daily digest (if that's on). Reminders before it starts need a start time." : "";
    else if (forMe && myOffsets !== null) {
      reminderHint.textContent = myOffsets.length
        ? `🔔 You'll be reminded ${myOffsets.map((m) => formatOffset(m).replace(/ before$/, "")).join(" and ")} before it starts.`
        : "🔕 You have no “before it starts” reminders — add them in Settings → Reminders → Per-task reminders.";
    } else reminderHint.textContent = forMe ? "" : `🔔 ${userName(forSel.value) || "They"} get their own reminders before it starts, as set in their Settings.`;
    privacyWarn.textContent = privateCb.checked && publishCb.checked
      ? "Anyone who can use Home Assistant can see this item's sensor — its name, times and who it's for — even though it's hidden here." : "";
  }
  [kindSel, weeksN, daysN, monthsN, yearsN, dom, nth, nthWd, lead, startIn, endIn, forSel].forEach((el) => el.addEventListener("input", () => { readState(); snapAnchor(); refresh(); }));
  privateCb.addEventListener("change", () => {
    // any HA user can read sensors, so a new private item isn't published unless you choose to
    if (!publishTouched) publishCb.checked = !privateCb.checked;
    refresh();
  });
  publishCb.addEventListener("change", refresh);
  iconSel.addEventListener("input", () => { iconCustom.hidden = iconSel.value !== "custom"; });
  iconCustom.hidden = iconSel.value !== "custom";
  drawTurns();
  snapAnchor();
  refresh();

  const form = h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Name", name)),
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Repeats", kindSel), rowWeeksN, rowDaysN, rowMonthsN, rowYearsN, rowDom),
    rowNth,
    h("div", { class: "form-row" }, rowWeekdays),
    h("div", { class: "form-row" },
      h("label", { class: "field" }, anchorLabel, anchor),
      h("label", { class: "field" }, "Start", startIn),
      h("label", { class: "field" }, "End", endIn)),
    timesHint,
    h("div", { class: "form-row" },
      h("label", { class: "field" }, "For", forSel),
      h("div", { class: "field wide" }, "Place", place.control, place.mapsLink),
      leadField),
    place.newBox,
    turnsRow,
    reminderHint,
    h("div", { class: "form-row" }, privateRow, h("label", { class: "mini-toggle" }, publishCb, "Publish to Home Assistant")),
    privacyWarn,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Icon", iconSel), h("label", { class: "field" }, " ", iconCustom)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Link (optional)", urlIn)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Notes", notes)),
    warn, yearlyHint,
    editing ? h("div", { class: "hint" }, "Changing the repeat rule or first occurrence clears this item's skips and moves (extra dates are kept).") : null,
    err, h("div", { class: "actions" }, editing ? delBtn : null, save));
  const modal = openModal(editing ? "Edit schedule item" : "New schedule item", form);
  delBtn.addEventListener("click", async () => {
    if (!confirm(scheduleDeleteQuestion(item))) return;
    try { await api(`/api/schedule/${item.id}`, { method: "DELETE" }); modal.close(); toast("Deleted"); onSaved(); } catch (e) { err.textContent = e.message; }
  });

  save.addEventListener("click", async () => {
    err.textContent = "";
    readState();
    const weekly = s.kind === "weekly" || s.kind === "weeksN";
    if (weekly && !s.days.length) { err.textContent = "Pick at least one weekday."; return; }
    if (!startIn.value !== !endIn.value) { err.textContent = "Set both a start and an end time, or neither."; return; }
    const rule = buildRule(s);
    const icon = iconSel.value === "custom" ? (iconCustom.value.trim() || null) : iconSel.value;
    const body = {
      name: name.value.trim(), rule, anchor_date: anchor.value, lead_days: Number(lead.value), icon, notes: notes.value.trim() || null,
      url: urlIn.value.trim() || null,
      assigned_to: forSel.value || null, start_time: startIn.value || null, end_time: endIn.value || null,
      visibility: forSel.value && privateCb.checked ? "private" : "household", expose_sensor: publishCb.checked,
      rotation: !forSel.value && turns.length ? [...turns] : null,
    };
    if (body.rotation && body.rotation.length < 2) { err.textContent = "Taking turns needs at least two people."; return; }
    if (editing && (rule !== originalRule || anchor.value !== originalAnchor) && !confirm("Changing the repeat rule or first occurrence clears this item's skips and moves. Continue?")) return;
    save.disabled = true;
    try {
      body.place_id = await place.resolve();
      if (editing) await api(`/api/schedule/${item.id}`, { method: "PATCH", body });
      else await api("/api/schedule", { method: "POST", body });
      modal.close();
      toast(editing ? "Saved" : body.expose_sensor ? "Created — the sensor is on its way to Home Assistant" : "Created");
      onSaved();
    } catch (e) { err.textContent = e.message; save.disabled = false; }
  });
}

// =====================================================================
// Places — the household address book
// =====================================================================

function mapsUrl(address) {
  return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(address);
}
function telUrl(phone) {
  return "tel:" + phone.replace(/[^\d+]/g, "");
}

const placesUI = { q: "" };

async function renderPlaces() {
  const root = $("#tab-places");
  mount(root, pageHead("Places"), spinner());
  const listBox = h("div");
  const search = h("input", { type: "search", placeholder: "Search places…", value: placesUI.q, "aria-label": "Search places", style: "flex:1;min-width:160px" });
  let timer = null;
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { placesUI.q = search.value; loadPlaces(); }, 200); });

  async function loadPlaces() {
    try {
      const places = await api("/api/places" + (placesUI.q.trim() ? `?q=${encodeURIComponent(placesUI.q.trim())}` : ""));
      state.places = placesUI.q.trim() ? state.places : places;
      mount(listBox, places.length
        ? h("div", { class: "card" }, places.map((p) => placeRow(p, loadPlaces)))
        : h("div", { class: "card empty" }, placesUI.q.trim() ? "No places match that search." : "No places yet. Add the hospital, the school, the vet — then pick them from any task."));
    } catch (e) { mount(listBox, errorCard(e, loadPlaces)); }
  }

  // Adding a place is minimised to a button + dialog, like adding a list.
  function newPlaceModal() {
    const name = h("input", { type: "text", maxlength: "60", placeholder: "Name, e.g. Hospital", "aria-label": "Place name" });
    const addr = h("textarea", { maxlength: "300", placeholder: "Address", style: "min-height:64px", "aria-label": "Address" });
    const phone = h("input", { type: "tel", maxlength: "30", placeholder: "Phone (optional)", "aria-label": "Phone number" });
    const err = h("div", { class: "error-text" });
    const ok = h("button", { class: "btn-primary", type: "button" }, "Add place");
    const m = openModal("New place", h("div", null,
      h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Name", name)),
      h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Address", addr)),
      h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Phone", phone)),
      h("div", { class: "hint" }, "Places are shared with the whole household."),
      err, h("div", { class: "actions" }, ok)), { sheet: true });
    ok.addEventListener("click", async () => {
      err.textContent = "";
      ok.disabled = true;
      try {
        await api("/api/places", { method: "POST", body: { name: name.value, address: addr.value, phone: phone.value.trim() || null } });
        await refreshPlaces();
        m.close();
        toast("Place added");
        loadPlaces();
      } catch (e) { err.textContent = e.message; ok.disabled = false; }
    });
    setTimeout(() => name.focus(), 0);
  }

  const newBtn = h("button", { class: "btn-primary", type: "button", onclick: newPlaceModal }, "+ New place");
  mount(root, h("div", { class: "page-head" }, h("h2", null, "Places"), newBtn),
    h("div", { class: "form-row" }, search), listBox);
  await loadPlaces();
}

// The Places tab's drive-time line (§8n), including whether the estimate
// avoids toll roads (the avoid_tolls App setting).
function driveTimeText(p) {
  if (p.driveMinutes == null) return "🚗 Drive time not calculated yet";
  let text = `🚗 ~${p.driveMinutes} min from home`;
  if (p.driveTollsAvoided) text += " · no toll roads";
  else if (p.driveAvoidTolls) text += " · uses toll roads (no toll-free route found)";
  return text;
}

// The "Drive times" App setting: off = no drive line or Recalculate button
// (the server sends no drive times then either).
function driveTimesOn() { return !!(state.me && state.me.driveTimes && state.me.driveTimes.enabled); }

function placeRow(p, again) {
  const drive = driveTimesOn();
  const recalcBtn = !drive ? null : h("button", { class: "btn-ghost btn-small", type: "button", title: "Look up this address again and recompute its estimated drive time from home" },
    "🔄 Recalculate drive time");
  if (recalcBtn) recalcBtn.addEventListener("click", async () => {
    recalcBtn.disabled = true;
    const label = recalcBtn.textContent;
    recalcBtn.textContent = "Calculating…";
    try {
      await api(`/api/places/${p.id}/recalculate-drive-time`, { method: "POST" });
      await refreshPlaces();
      toast("Drive time updated");
      again();
    } catch (e) {
      fail(e);
      recalcBtn.disabled = false;
      recalcBtn.textContent = label;
    }
  });
  return h("div", { class: "place-row" },
    h("div", { class: "info" },
      h("div", { class: "name" }, p.name, h("span", { class: "hint" }, `used by ${p.usageCount} task${p.usageCount === 1 ? "" : "s"}`)),
      h("div", { class: "addr" }, p.address),
      p.phone ? h("div", { class: "addr" }, h("a", { href: telUrl(p.phone) }, "📞 ", p.phone)) : null,
      drive ? h("div", { class: "addr hint" }, driveTimeText(p)) : null),
    h("div", { class: "actions" },
      h("a", { class: "btn-ghost btn-small", href: mapsUrl(p.address), target: "_blank", rel: "noopener noreferrer" }, "Open in maps"),
      h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => copyText(p.address) }, "Copy address"),
      recalcBtn,
      h("button", { class: "icon-btn", type: "button", title: "Edit", "aria-label": `Edit ${p.name}`, onclick: () => editPlace(p, again) }, "✎"),
      h("button", { class: "icon-btn danger", type: "button", title: "Delete", "aria-label": `Delete ${p.name}`, onclick: async () => {
        const msg = p.usageCount ? `${p.usageCount} task${p.usageCount === 1 ? "" : "s"} will lose their reference to it.` : "No tasks use it.";
        if (!confirm(`Delete “${p.name}”? ${msg}`)) return;
        try { await api(`/api/places/${p.id}`, { method: "DELETE" }); await refreshPlaces(); toast("Deleted"); again(); } catch (e) { fail(e); }
      } }, "🗑")));
}

function editPlace(p, again) {
  const name = h("input", { type: "text", maxlength: "60", value: p.name });
  const addr = h("textarea", { maxlength: "300", style: "min-height:64px" }, p.address);
  const phone = h("input", { type: "tel", maxlength: "30", value: p.phone || "", placeholder: "Phone (optional)" });
  const err = h("div", { class: "error-text" });
  const ok = h("button", { class: "btn-primary", type: "button" }, "Save");
  const m = openModal("Edit place", h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Name", name)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Address", addr)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Phone", phone)),
    h("div", { class: "hint" }, "Shared with the whole household."),
    err, h("div", { class: "actions" }, ok)), { sheet: true });
  ok.addEventListener("click", async () => {
    try { await api(`/api/places/${p.id}`, { method: "PATCH", body: { name: name.value, address: addr.value, phone: phone.value.trim() || null } }); await refreshPlaces(); m.close(); toast("Saved"); again(); }
    catch (e) { err.textContent = e.message; }
  });
}

// =====================================================================
// Settings: reminders (per user), task types, appearance
// =====================================================================

const DOW_OPTIONS = [[1, "Monday"], [2, "Tuesday"], [3, "Wednesday"], [4, "Thursday"], [5, "Friday"], [6, "Saturday"], [7, "Sunday"]];
const OFFSET_UNITS = [["hours", 60], ["minutes", 1], ["days", 1440]];
const MAX_REMINDER_OFFSETS = 5;

function formatOffset(minutes) {
  if (minutes % 1440 === 0) { const n = minutes / 1440; return `${n} day${n === 1 ? "" : "s"} before`; }
  if (minutes % 60 === 0) { const n = minutes / 60; return `${n} hour${n === 1 ? "" : "s"} before`; }
  return `${minutes} minute${minutes === 1 ? "" : "s"} before`;
}

// The page theme (common/theme-boot.js): every Theme menu shows and changes the same choice.
function themeSelect(extra = {}) {
  return HouseholdTheme.bindSelect(h("select", { ...extra, "aria-label": "Theme" }));
}

async function renderSettings() {
  const root = $("#tab-settings");
  mount(root, pageHead("Settings"), spinner());
  const cards = [];
  try {
    cards.push(await remindersCard());
    const assistant = await assistantCard();
    if (assistant) cards.push(assistant);
    cards.push(await whoamiCard());
    cards.push(await typesCard());
    cards.push(h("div", { class: "card mobile-only" }, h("h3", null, "Appearance"), themeSelect({ style: "width:100%" })));
    mount(root, pageHead("Settings"), cards);
  } catch (e) { mount(root, pageHead("Settings"), errorCard(e, renderSettings)); }
}

// "How the app sees you": exactly what Home Assistant sent, whether it matched
// admin_users, and whether an admin linked a notify service (Admin → Users). Always the real signed-in person, even while an
// admin is acting as someone else. Read-only; shows counts, never the lists. Drawn by common/whoami.js.
async function whoamiCard() {
  const w = await api("/api/whoami", { asSelf: true });
  return h("div", { class: "card", id: "whoamiCard" }, h("h3", null, "How the app sees you"),
    HouseholdWhoami.panel(w, {
      appName: "Household Todo",
      adviceTag: "div", adviceStyle: "margin-top:8px",
      onCopy: (text) => copyText(text),
    }));
}

async function remindersCard() {
  if (isActingAsOther()) {
    return h("div", { class: "card" }, h("h3", null, "Reminders"),
      h("div", { class: "hint" }, `You're acting as ${userName(state.actAs) || "another user"}. Reminder settings can only be changed as yourself — switch back to yourself first.`));
  }
  const p = await api("/api/prefs");
  const card = h("div", { class: "card" }, h("h3", null, "Reminders"));
  const body = h("div");
  card.appendChild(body);

  const paint = (prefs) => {
    const put = async (patch, control) => {
      try { const np = await api("/api/prefs", { method: "PUT", body: patch }); paint(np); toast("Saved"); }
      catch (e) { fail(e); paint(prefs); }
    };
    const status = prefs.canNotify ? null : h("div", { class: "warn-box", style: "color:var(--warn);border-color:var(--warn);background:var(--warn-soft)" },
      prefs.canNotifyReason || "Reminders aren't available for your account yet.");
    const lead = h("select", { value: String(prefs.leadDays), "aria-label": "Days ahead", disabled: !prefs.canNotify && !prefs.notificationsEnabled,
      onchange: (e) => put({ leadDays: Number(e.target.value) }) },
      Array.from({ length: 8 }, (_, i) => h("option", { value: String(i) }, i === 0 ? "On the day" : `${i} day${i === 1 ? "" : "s"} ahead`)));
    const test = h("button", { class: "btn-secondary", type: "button", disabled: !prefs.canNotify }, "Send test notification");
    test.addEventListener("click", async () => {
      test.disabled = true;
      try { await api("/api/prefs/test-notification", { method: "POST", body: {} }); toast("Test notification sent"); }
      catch (e) { fail(e); }
      test.disabled = !prefs.canNotify;
    });

    // Per-task reminders: this person's own list of "N before due" offsets,
    // applied automatically to every task with a due time that's theirs.
    const offsetDisabled = !prefs.canNotify;       // works on its own, without the daily digest
    const offsetChips = prefs.reminderOffsets.length
      ? h("div", { class: "chip-row", style: "display:flex;flex-wrap:wrap;gap:6px;margin:8px 0" },
          prefs.reminderOffsets.map((m) => h("span", { class: "chip" }, formatOffset(m),
            h("button", { class: "icon-btn", type: "button", title: "Remove", "aria-label": `Remove ${formatOffset(m)}`, disabled: offsetDisabled,
              onclick: () => put({ reminderOffsets: prefs.reminderOffsets.filter((x) => x !== m) }) }, "✕"))))
      : h("div", { class: "hint", style: "margin:8px 0" }, "None set — only the daily digest applies.");
    const amountInput = h("input", { type: "number", min: "1", max: "10080", value: "1", style: "width:70px", disabled: offsetDisabled, "aria-label": "Amount" });
    const unitSelect = h("select", { "aria-label": "Unit", disabled: offsetDisabled }, OFFSET_UNITS.map(([label, mult]) => h("option", { value: String(mult) }, label)));
    const addOffsetBtn = h("button", { class: "btn-secondary", type: "button", disabled: offsetDisabled || prefs.reminderOffsets.length >= MAX_REMINDER_OFFSETS }, "+ Add");
    addOffsetBtn.addEventListener("click", () => {
      const n = Math.round(Number(amountInput.value) * Number(unitSelect.value));
      if (!n || n < 1 || n > 10080) { toast("Enter a valid amount"); return; }
      if (prefs.reminderOffsets.includes(n)) { toast("That reminder already exists"); return; }
      if (prefs.reminderOffsets.length >= MAX_REMINDER_OFFSETS) { toast(`Up to ${MAX_REMINDER_OFFSETS} reminders per person`); return; }
      put({ reminderOffsets: [...prefs.reminderOffsets, n].sort((a, b) => b - a) });
    });
    const offsetsSection = h("div", { class: "switch-row", style: "flex-direction:column;align-items:stretch" },
      h("div", null, h("div", null, "Per-task reminders"),
        h("div", { class: "sub" }, `Pings before a task's due time or a schedule item's start — e.g. 2 hours before, then 15 minutes before. Only for tasks and schedule items that are yours and have a time, up to ${MAX_REMINDER_OFFSETS}. Sent even with the daily digest off.`)),
      offsetChips,
      h("div", { style: "display:flex;gap:6px;align-items:center" }, amountInput, unitSelect, addOffsetBtn));

    // Weekly summary: a per-user day-of-week + time, independent of the daily digest.
    const ws = prefs.weeklySummary;
    const weeklyDisabled = !prefs.canNotify && !ws.enabled;
    const dowSelect = h("select", { value: String(ws.dayOfWeek), "aria-label": "Day", disabled: weeklyDisabled,
      onchange: (e) => put({ weeklySummary: { dayOfWeek: Number(e.target.value) } }) },
      DOW_OPTIONS.map(([v, l]) => h("option", { value: String(v) }, l)));
    const timeInput = h("input", { type: "time", value: ws.time, "aria-label": "Time", disabled: weeklyDisabled,
      onchange: (e) => put({ weeklySummary: { time: e.target.value } }) });
    const weeklySection = h("div", { class: "switch-row" },
      h("div", null, h("div", null, "Weekly summary"), h("div", { class: "sub" }, "A preview of next week's tasks, once a week.")),
      toggleSwitch(ws.enabled, (on) => put({ weeklySummary: { enabled: on } }), { disabled: weeklyDisabled, label: "Weekly summary" }));
    const weeklyTimeRow = h("div", { class: "switch-row" },
      h("div", null, "Send on"),
      h("div", { style: "display:flex;gap:6px" }, dowSelect, timeInput));

    const digestDisabled = !prefs.canNotify && !prefs.notificationsEnabled;
    const digestTimeInput = h("input", { type: "time", value: prefs.digestTime, "aria-label": "Daily reminder time", disabled: digestDisabled,
      onchange: (e) => put({ digestTime: e.target.value }) });

    mount(body,
      status,
      h("div", { class: "hint", style: "margin-bottom:6px" }, "Each kind below has its own switch. The daily digest, weekly summary and per-task reminders also cover schedule items that are for you — private ones included. Per-task reminders count back from a schedule item's start time, so they need one."),
      h("div", { class: "switch-row" }, h("div", null, h("div", null, "Daily reminder digest"), h("div", { class: "sub" }, "One notification a day listing what's due.")),
        toggleSwitch(prefs.notificationsEnabled, (on) => put({ notificationsEnabled: on }), { disabled: !prefs.canNotify && !prefs.notificationsEnabled, label: "Daily reminder digest" })),
      h("div", { class: "switch-row" }, h("div", null, h("div", null, "Send at"), h("div", { class: "sub" }, "When your daily digest goes out (08:00 unless you change it).")), digestTimeInput),
      h("div", { class: "switch-row" }, h("div", null, h("div", null, "Include tasks due"), h("div", { class: "sub" }, "How many days ahead to look.")), lead),
      h("div", { class: "switch-row" }, h("div", null, h("div", null, "Tell me when someone assigns me a task"), h("div", { class: "sub" }, "Sent straight away.")),
        toggleSwitch(prefs.notifyOnAssign, (on) => put({ notifyOnAssign: on }), { disabled: !prefs.canNotify && !prefs.notifyOnAssign, label: "Notify on assignment" })),
      offsetsSection,
      maintPrefRow(prefs, put),
      weeklySection,
      weeklyTimeRow,
      h("div", { class: "actions", style: "justify-content:flex-start" }, test));
  };
  paint(p);
  return card;
}

// "Let the Household Assistant answer for me" — shown while an admin lets the Household Assistant ask Todo.
async function assistantCard() {
  if (isActingAsOther()) return null;
  const p = await api("/api/prefs");
  if (!p.assistant) return null;
  return h("div", { class: "card", id: "assistantCard" }, h("h3", null, "Household Assistant"),
    h("div", { class: "switch-row" },
      h("div", null, h("div", null, "Let the Household Assistant answer for me"),
        h("div", { class: "sub" }, "When you ask the Household Assistant app, it can tell you your tasks, lists and what's coming up, and add a task when you tap to confirm.")),
      toggleSwitch(p.assistantOk, async (on) => {
        try { await api("/api/prefs", { method: "PUT", body: { assistantOk: on } }); toast("Saved"); }
        catch (e) { toast(e.message, true); renderSettings(); }
      }, { label: "Let the Household Assistant answer for me" })));
}

async function typesCard() {
  await refreshTypes();
  const card = h("div", { class: "card" }, h("h3", null, "Task types"),
    h("div", { class: "hint", style: "margin-bottom:8px" }, "A type is a label like “Doctor appointment” with an optional emoji and colour. It's shared by the whole household."));
  const list = h("div");
  const paint = () => {
    mount(list, ...state.types.map((t) => h("div", { class: "row" },
      h("span", null, t.color ? h("span", { class: "color-dot", style: `background:${t.color}` }) : null, " ", t.icon ? t.icon + " " : "", h("span", { class: "name" }, t.name),
        " ", h("span", { class: "hint" }, `${t.usageCount} task${t.usageCount === 1 ? "" : "s"}`)),
      h("span", null,
        h("button", { class: "icon-btn", type: "button", title: "Edit", "aria-label": `Edit ${t.name}`, onclick: () => typeForm(t, again) }, "✎"),
        h("button", { class: "icon-btn danger", type: "button", title: "Delete", "aria-label": `Delete ${t.name}`, onclick: async () => {
          const msg = t.usageCount ? `${t.usageCount} task${t.usageCount === 1 ? "" : "s"} will become untyped.` : "No tasks use it.";
          if (!confirm(`Delete the type “${t.name}”? ${msg}`)) return;
          try { await api(`/api/task-types/${t.id}`, { method: "DELETE" }); toast("Deleted"); again(); } catch (e) { fail(e); }
        } }, "🗑")))));
    if (!state.types.length) mount(list, h("div", { class: "empty" }, "No types yet."));
  };
  const again = async () => { try { await refreshTypes(); paint(); } catch (e) { fail(e); } };
  paint();
  card.append(list, h("div", { class: "actions", style: "justify-content:flex-start" },
    h("button", { class: "btn-secondary", type: "button", onclick: () => typeForm(null, again) }, "+ New type")));
  return card;
}

function typeForm(t, again) {
  const editing = !!t;
  const name = h("input", { type: "text", maxlength: "30", value: editing ? t.name : "", placeholder: "e.g. Vet visit" });
  const icon = h("input", { type: "text", maxlength: "8", value: editing && t.icon ? t.icon : "", placeholder: "🐕", style: "width:70px" });
  const color = h("input", { type: "color", value: editing && t.color ? t.color : "#5ec8b6", "aria-label": "Colour" });
  const noColor = h("input", { type: "checkbox", checked: !(editing && t.color) });
  const err = h("div", { class: "error-text" });
  const ok = h("button", { class: "btn-primary", type: "button" }, editing ? "Save" : "Create");
  const m = openModal(editing ? "Edit type" : "New task type", h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Name", name), h("label", { class: "field narrow" }, "Emoji", icon)),
    h("div", { class: "form-row" }, h("label", { class: "field narrow" }, "Colour", color), h("label", { class: "mini-toggle" }, noColor, "No colour")),
    err, h("div", { class: "actions" }, ok)), { sheet: true });
  color.addEventListener("input", () => { noColor.checked = false; });
  ok.addEventListener("click", async () => {
    const body = { name: name.value, icon: icon.value.trim() || null, color: noColor.checked ? null : color.value };
    try {
      if (editing) await api(`/api/task-types/${t.id}`, { method: "PATCH", body }); else await api("/api/task-types", { method: "POST", body });
      m.close(); toast("Saved"); again();
    } catch (e) { err.textContent = e.message; }
  });
}

// =====================================================================
// Admin (admins only; the server enforces it): App settings | Users | Storage
// =====================================================================

const ADMIN_TABS = [["settings", "App settings"], ["users", "Users"], ["maintenance", "Maintenance"], ["storage", "Storage"]];
const NOTIFY_RE = /^notify\.[a-z0-9_]+$/;

function adminApi(path, opts = {}) { return api(path, { ...opts, asSelf: true }); }

function renderAdmin(sub) {
  const root = $("#tab-admin");
  if (!isAdmin()) {
    mount(root, pageHead("Admin"), h("div", { class: "card", id: "adminDenied" },
      h("h3", null, "Only admins can open this page"),
      h("div", { class: "hint" }, "App settings, Users, Maintenance and Storage are for the people listed in the ",
        h("code", null, "admin_users"), " option on the app's Configuration tab.")));
    return;
  }
  if (!ADMIN_TABS.some(([k]) => k === sub)) sub = "settings";
  state.adminTab = sub;
  const tabs = h("div", { class: "admin-tabs", role: "tablist", "aria-label": "Admin sections" },
    ADMIN_TABS.map(([key, label]) => h("button", {
      type: "button", role: "tab", class: key === sub ? "active" : "", "aria-selected": key === sub ? "true" : "false",
      dataset: { adminTab: key }, onclick: () => showTab("admin", { sub: key }),
    }, label)));
  const body = h("div", { class: "admin-body" });
  mount(root, pageHead("Admin"), tabs, body);
  ({ settings: renderAppSettings, users: renderUsers, maintenance: renderAdminMaintenance, storage: renderStorage })[sub](body);
}

// ---------- App settings ----------
function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return url; }
}

// Drawn by common/settings.js from the server's description of each setting; this app adds the drive-time
// privacy note and the maintenance files folder (checked before it is saved).
async function renderAppSettings(box) {
  mount(box, spinner());
  let data;
  try { data = await adminApi("/api/admin/settings"); }
  catch (e) { mount(box, errorCard(e, () => renderAppSettings(box))); return; }
  const folder = { check: null };
  // Connected apps (APP_MESSAGES_SPEC §5): a read-only card under the settings, drawn by common/connected-apps.js
  const settingsBox = h("div"), appsBox = h("div", { class: "connected-apps-wrap" });
  mount(box, settingsBox, appsBox);
  adminApi("/api/admin/connected-apps").then((d) => mount(appsBox, ConnectedApps.card(d, { h }))).catch(() => {});
  await SettingsPage.render(settingsBox, {
    data,
    load: () => adminApi("/api/admin/settings"),
    save: (body) => adminApi("/api/admin/settings", { method: "PUT", body }),
    intro: "Each person picks their own daily reminder time under Settings → Reminders → Send at (08:00 until they change it).",
    footer: () => h("span", null, "Who is an admin is set in the app's Configuration tab (", h("code", null, "admin_users"), ") — that's how the first admin is known."),
    fields: {
      drive_times_enabled: {
        after: (page) => {
          const d = page.data.defaults;
          return h("div", { class: "hint", id: "drivePrivacyNote", style: "margin:4px 0 0" },
            "Privacy: with this on, your home address and every saved place's address are sent to the address lookup server (",
            h("code", null, hostOf(page.values.nominatim_url || d.nominatim_url)), ") to find them on the map, and the resulting coordinates to the routing server (",
            h("code", null, hostOf(page.values.osrm_url || d.osrm_url)), "). By default these are OpenStreetMap's public services, with their own usage policies. Nothing else is sent: no task titles, notes or names. To keep addresses at home, run your own servers and enter their addresses below. Off: nothing is sent and no drive times are shown.");
        },
      },
      maintenance_files_path: {
        hidden: (page) => !page.data.maintenanceFiles,
        control: (page) => maintFolderControl(page, folder),
      },
    },
    beforeSave: async (body) => {
      if ("maintenance_files_path" in body) {
        // like Household Chat's files folder — refused / confirmed as Check says; never moves files
        const info = await folder.check();
        if (!info || info.refused) return null;
        if (info.needsConfirm && !confirm(info.message + "\n\nUse this folder anyway?")) return null;
        body.confirm = !!info.needsConfirm;
      }
      return body;
    },
    afterSave: (res, body) => {
      if (state.me) state.me.driveTimes = { enabled: !!res.values.drive_times_enabled };
      const msg = "maintenance_files_path" in body ? (res.values.maintenance_files_path ? "Files folder saved" : "Attaching files is off") : "Settings saved";
      toast(msg);
      return msg;
    },
  });
}

// ---------- Users ----------
// The shared people page (common/people.js) with this app's enable switch.
async function renderUsers(box) {
  mount(box, spinner());
  let data;
  try { data = await adminApi("/api/admin/users"); }
  catch (e) { mount(box, errorCard(e, () => renderUsers(box))); return; }
  // The notify list comes from Home Assistant; the page still works without it.
  let avail;
  try { avail = await adminApi("/api/admin/notify-services"); }
  catch (e) { avail = { available: false, services: [], entities: [], error: e.message }; }
  const again = () => renderUsers(box);
  PeoplePage.render(box, {
    people: data.users,
    intro: ["Everyone who has ever opened Household Todo. A disabled person can't be assigned new tasks; their existing tasks stay where they are.",
      "📱 Phones come from Home Assistant: Settings → People → (the person) → Track device, picking their phone with the Home Assistant Companion app. Set a phone up there once and every household app uses it. Add an extra notify service below only for something else (a speaker, a second service). People still switch their own reminders on under Settings."],
    checkAgain: async () => { try { await adminApi("/api/admin/users?refresh=1"); again(); toast("Read from Home Assistant"); } catch (e) { fail(e); } },
    person: (u) => {
      const self = u.id === state.me.haUserId;
      return {
        badges: [self ? ["you"] : null],
        sub: (u.username ? `login ${u.username} · ` : "") + `first seen ${fmtStamp(u.createdAt)}`,
        controls: h("label", { class: "pp-toggle" }, u.disabled ? "Disabled" : "Active",
          PeoplePage.accessSwitch(!u.disabled, async (on, input) => {
            try {
              await adminApi(`/api/users/${encodeURIComponent(u.id)}`, { method: "PATCH", body: { disabled: !on } });
              toast(on ? "Enabled" : "Disabled");
              state.users = await api("/api/users");
              again();
            } catch (e) { input.checked = !on; fail(e); }
          }, { disabled: self, label: `Enable ${u.name}` })),
      };
    },
    notify: {
      api: adminApi, services: avail, toast: (m, err) => toast(m, !!err), fail,
      path: (u) => `/api/admin/users/${encodeURIComponent(u.id)}/notify`,
      testPath: (u) => `/api/admin/users/${encodeURIComponent(u.id)}/notify/test`,
      retry: async () => { try { await adminApi("/api/admin/notify-services?refresh=1"); } catch (e) { /* shown again */ } again(); },
      texts: { none: (u, phones) => (phones ? "None" : "None — no reminders until a phone is linked in Home Assistant or a service is added here.") },
    },
    empty: "Nobody has opened Household Todo yet.",
  });
}

// ---------- Storage ----------
function renderStorage(box) {
  const file = h("input", { type: "file", accept: ".db,application/vnd.sqlite3,application/octet-stream", "aria-label": "Backup file" });
  const err = h("div", { class: "error-text" });
  const btn = h("button", { class: "btn-danger", type: "button" }, "Import database (.db)");
  btn.addEventListener("click", async () => {
    err.textContent = "";
    if (!file.files.length) { err.textContent = "Choose a .db backup file first."; return; }
    if (!confirm("This REPLACES the whole Household Todo database with the uploaded file — every list, task, place, schedule item and App setting. There is no merge and no undo.\n\nContinue?")) return;
    const fd = new FormData();
    fd.append("file", file.files[0]);
    btn.disabled = true;
    try {
      await adminApi("/api/admin-storage-import-db", { method: "POST", formData: fd });
      toast("Database restored");
      file.value = "";
      await refreshMaintFlag();
      await loadLookups();
      listUI.openId = null;
    } catch (e) { err.textContent = e.message; }
    btn.disabled = false;
  });
  mount(box,
    h("div", { class: "card" }, h("h3", null, "Backup"),
      h("div", { class: "hint", style: "margin-bottom:10px" }, "A complete snapshot of the household's data: every person's lists, tasks, places and schedule items, plus the App settings and notify services."),
      h("a", { class: "btn-primary", href: "api/admin-storage-download-db", download: "" }, "Download backup (.db)")),
    h("div", { class: "card" }, h("h3", null, "Restore"),
      h("div", { class: "warn-box" }, "Restoring replaces everything currently in the app with the contents of the file. Download a backup first if you are unsure."),
      h("div", { class: "form-row" }, file, btn), err));
}

// =====================================================================
// Acting as (admins) — the server enforces it; hiding the select is a convenience
// =====================================================================

function actAsSelect(extra = {}) {
  const me = state.me;
  const sel = h("select", { ...extra, "data-actas-select": "1", value: actingUserId(), "aria-label": "Acting as" },
    h("option", { value: me.haUserId }, `Myself (${me.haDisplayName})`),
    state.users.filter((u) => u.id !== me.haUserId).map((u) => h("option", { value: u.id }, u.name + (u.disabled ? " (disabled)" : ""))));
  sel.addEventListener("change", () => setActAs(sel.value));
  return sel;
}

function syncActingUI() {
  if (!state.me) return;
  const holder = $("#actAsField");
  holder.hidden = !isAdmin();
  const old = $("#actAsSelect");
  if (isAdmin()) {
    const fresh = actAsSelect({ id: "actAsSelect", title: "Act as another household member" });
    old.replaceWith(fresh);
  }
  const banner = $("#actingBanner");
  if (isActingAsOther()) {
    banner.hidden = false;
    mount(banner, h("span", null, `Acting as ${userName(state.actAs) || "another user"} — everything you add or change is recorded as them.`),
      h("button", { class: "btn-secondary", type: "button", onclick: () => setActAs(null) }, "Switch back to me"));
  } else {
    banner.hidden = true;
    clear(banner);
  }
}

async function setActAs(id) {
  const target = id && id !== state.me.haUserId ? id : null;
  state.actAs = target;
  ssSet("actAsUserId", target);
  listUI.openId = null;
  try { await loadLookups(); }
  catch (e) {
    if (e.status === 404) { state.actAs = null; ssSet("actAsUserId", null); await loadLookups(); toast("That person isn't known any more — back to yourself.", true); }
    else fail(e);
  }
  syncActingUI();
  if (target) toast(`Now acting as ${userName(target) || "them"}`);
  showTab(state.tab, { force: true });
}

// =====================================================================
// Tabs & start-up
// =====================================================================

const RENDERERS = {
  dashboard: renderDashboard, calendar: renderCalendar, schedule: renderSchedule, lists: renderLists,
  maintenance: () => renderMaintenance(),                    // maintenance.js
  places: renderPlaces, settings: renderSettings, admin: () => renderAdmin(state.adminTab),
};

// Deep links: #/calendar, #/lists, … and #/admin/settings | #/admin/users |
// #/admin/storage. The short links #/users, #/storage and #/app-settings
// also open the matching Admin tab. A non-admin who lands on an admin link sees
// "Only admins can open this page" (the server refuses the data anyway).
const LEGACY_ROUTES = { users: ["admin", "users"], storage: ["admin", "storage"], "app-settings": ["admin", "settings"] };

function parseHash(hash) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  let [tab, sub] = parts;
  if (LEGACY_ROUTES[tab]) [tab, sub] = LEGACY_ROUTES[tab];
  if (!TABS.includes(tab)) return null;
  if (tab === "admin") sub = ADMIN_TABS.some(([k]) => k === sub) ? sub : "settings";
  else if (tab === "lists") sub = /^[A-Za-z0-9_-]{1,64}$/.test(sub || "") ? sub : null;   // #/lists/<id>: that list
  else sub = null;
  return { tab, sub };
}

function routeHash(tab, sub) { return tab === "admin" ? `#/admin/${sub || "settings"}` : `#/${tab}`; }

function showTab(tab, opts = {}) {
  if (LEGACY_ROUTES[tab]) { opts = { ...opts, sub: LEGACY_ROUTES[tab][1] }; tab = "admin"; }
  if (!TABS.includes(tab)) tab = "calendar";
  if (tab === "admin" && opts.sub) state.adminTab = opts.sub;
  if (tab === "lists" && opts.sub) listUI.openId = opts.sub;      // renderLists falls back to the first list
  state.tab = tab;
  const want = routeHash(tab, state.adminTab);
  if (location.hash !== want) {
    try { history.replaceState(null, "", want); } catch (e) { /* sandboxed frame: fine without deep links */ }
  }
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  TABS.forEach((t) => $("#tab-" + t).classList.toggle("active", t === tab));
  RENDERERS[tab]();
}

window.addEventListener("hashchange", () => {
  if (!state.me) return;
  const r = parseHash(location.hash);
  if (r && routeHash(r.tab, r.sub) !== routeHash(state.tab, state.adminTab)) showTab(r.tab, { sub: r.sub });
});

function wireChrome() {
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  const collapse = $("#sidebarCollapseBtn");
  const syncCollapse = () => {
    const collapsed = document.documentElement.getAttribute("data-sidebar") === "collapsed";
    collapse.textContent = collapsed ? "›" : "‹";
    collapse.title = collapse.ariaLabel = collapsed ? "Expand sidebar" : "Collapse sidebar";
  };
  collapse.addEventListener("click", () => {
    HouseholdTheme.setSidebarCollapsed(!HouseholdTheme.sidebarCollapsed());
    syncCollapse();
  });
  syncCollapse();
  HouseholdTheme.bindSelect($("#theme-select"));
}

// A fresh install has nobody in admin_users, so nobody can open App settings.
// Every page says how to fix that (for everyone); nobody is ever auto-promoted.
function openWhoamiCard() {
  showTab("settings");
  // the Settings tab renders asynchronously; wait for the card, then scroll to it
  const t0 = Date.now();
  (function look() {
    const c = $("#whoamiCard");
    if (c) c.scrollIntoView({ behavior: "smooth", block: "start" });
    else if (Date.now() - t0 < 3000) setTimeout(look, 100);
  })();
}

function syncNoAdminBanner() {
  HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), state.me && state.me.noAdmin,
    state.me && (state.me.nameSent ? state.me.haUsername : state.me.haUserId), { onOpen: openWhoamiCard, linkId: "noAdminWhoami" });
}

async function init() {
  wireChrome();
  try {
    state.me = await api("/api/whoami", { asSelf: true });
  } catch (e) {
    const box = $("#fatal");
    box.hidden = false;
    mount(box, h("h3", null, "Can't open Household Todo"), h("div", null, e.message));
    return;
  }
  const chip = $("#sidebarUser");
  chip.textContent = state.me.haDisplayName + (state.me.isAdmin ? " · admin" : "");
  chip.title = "How the app sees you";
  chip.addEventListener("click", openWhoamiCard);
  document.querySelectorAll(".admin-only").forEach((el) => { if (el.id !== "actAsField") el.hidden = !state.me.isAdmin; });
  syncNoAdminBanner();
  syncMaintNav();
  if (maintEnabled()) api("/api/maintenance").then((d) => syncMaintNav(d.overdueCount)).catch(() => { /* the tab shows the reason */ });
  try {
    await loadLookups();
    const saved = ssGet("actAsUserId");
    if (state.me.isAdmin && saved && saved !== state.me.haUserId && state.users.some((u) => u.id === saved)) {
      state.actAs = saved;
      await loadLookups();
    }
  } catch (e) { fail(e); }
  syncActingUI();
  let start = parseHash(location.hash);
  // A link from Home Assistant opens "/<page>/lists/<id>", "/<page>/dashboard", … (common/static/deeplink.js): that
  // page now, and any tapped later while the app is open
  HouseholdDeepLink.start(state.me.page, (route) => parseHash("#" + route), (r) => {
    if (!start) start = r;
    else showTab(r.tab, { sub: r.sub });
  });
  showTab(start ? start.tab : "calendar", { force: true, sub: start && start.sub });
  initBackNav();
}

// Android's back gesture in the Home Assistant app (see backnav.js): Back
// closes the top dialog first, then returns to the Calendar (the start page);
// only Back on the Calendar with nothing open leaves the app.
function initBackNav() {
  if (!window.BackNav) return;
  BackNav.init({
    atHome: () => state.tab === "calendar",
    goHome: () => showTab("calendar"),   // showTab keeps the hash in sync with replaceState
    openLayers: () => UI.dialogs(),
    closeLayer: (m) => m.close(),         // the modal's own close, so onClose runs
  });
}

// maintenance.js is loaded after this file: start once every script has run
document.addEventListener("DOMContentLoaded", init);
