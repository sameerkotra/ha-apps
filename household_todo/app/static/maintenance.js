"use strict";
/* Household Todo — Maintenance. Loaded after app.js and uses its helpers (h, api, openModal, …).
   Recurring upkeep (marked done, can be overdue), one-off jobs (tasks in the Maintenance list),
   suggestions from a catalogue, history, and files kept in a folder under /share. */

const maint = {
  data: null,
  sugg: null,
  hist: null,
  histYear: "",
  histCat: "",
  showHidden: false,
};
const MAINT_SECTIONS = [["needs", "Needs doing"], ["coming", "Coming up"], ["jobs", "Open jobs"], ["suggestions", "Suggestions"],
  ["all", "All upkeep"], ["history", "History"]];
const UNIT_LABELS = [["day", "days"], ["week", "weeks"], ["month", "months"], ["year", "years"]];
const UNIT_MAX = { day: 365, week: 52, month: 36, year: 10 };
const LEAD_OPTIONS = [0, 1, 3, 7, 14, 30, 60];
const OVERDUE_OPTIONS = [[0, "Never"], [3, "Every 3 days"], [7, "Every week"], [14, "Every 2 weeks"]];
const SUGG_FIRST = 8;
const SEASON_NAMES = { spring: "Spring", summer: "Summer", autumn: "Autumn", winter: "Winter" };

function maintEnabled() { return !!(state.me && state.me.maintenance && state.me.maintenance.enabled); }

// The nav button shows only while Maintenance is on; its badge is the overdue count.
function syncMaintNav(overdue) {
  const btn = document.querySelector('.side-nav .tab-btn[data-tab="maintenance"]');
  if (!btn) return;
  btn.hidden = !maintEnabled();
  let badge = btn.querySelector(".nav-badge");
  if (overdue === undefined) return;
  if (!badge) { badge = h("span", { class: "nav-badge" }); btn.appendChild(badge); }
  badge.textContent = overdue > 0 ? String(overdue) : "";
  badge.hidden = !(overdue > 0);
  badge.title = overdue > 0 ? `${overdue} overdue` : "";
}

async function refreshMaintFlag() {
  try { state.me = await api("/api/whoami", { asSelf: true }); } catch (e) { /* keep the old one */ }
  syncMaintNav();
}

function collapsedSet() {
  try {
    const v = JSON.parse(lsGet("maintCollapsed") || "null");
    if (Array.isArray(v)) return new Set(v);
  } catch (e) { /* ignore */ }
  // first visit: on a phone only "Needs doing" is open
  return new Set((window.matchMedia("(max-width: 760px)").matches ? MAINT_SECTIONS.map(([k]) => k).filter((k) => k !== "needs") : ["history"])
    .concat(["schedule-maint"]));      // the Schedule tab's Maintenance group starts collapsed
}
function setCollapsed(key, collapsed) {
  const s = collapsedSet();
  if (collapsed) s.add(key); else s.delete(key);
  lsSet("maintCollapsed", JSON.stringify([...s]));
}

function section(key, title, count, body, extra) {
  const collapsed = collapsedSet().has(key);
  const head = h("button", { class: "maint-sec-head", type: "button", "aria-expanded": collapsed ? "false" : "true" },
    h("span", { class: "maint-sec-caret" }, collapsed ? "▸" : "▾"), h("span", { class: "maint-sec-title" }, title),
    count !== null && count !== undefined ? h("span", { class: "count-badge" }, String(count)) : null);
  const content = h("div", { class: "maint-sec-body", hidden: collapsed }, body);
  head.addEventListener("click", () => {
    const now = !content.hidden;
    content.hidden = now;
    head.setAttribute("aria-expanded", now ? "false" : "true");
    head.querySelector(".maint-sec-caret").textContent = now ? "▸" : "▾";
    setCollapsed(key, now);
  });
  return h("section", { class: "card maint-sec", dataset: { section: key } }, h("div", { class: "maint-sec-bar" }, head, extra || null), content);
}

function money(v, cur) {
  if (v === null || v === undefined) return "";
  const c = cur || (maint.data && maint.data.currency) || "";
  try { if (c) return new Intl.NumberFormat(undefined, { style: "currency", currency: c }).format(v); } catch (e) { /* unknown code */ }
  return (c ? c + " " : "") + Number(v).toFixed(2);
}

function maintStatusChip(it) {
  if (it.status === "paused") return h("span", { class: "chip off" }, "Paused");
  const n = it.daysUntil;
  if (it.status === "overdue") return h("span", { class: "chip overdue" }, `${-n} day${n === -1 ? "" : "s"} overdue`);
  const label = n === 0 ? "Due today" : n === 1 ? "Due tomorrow" : `Due ${fmtDate(it.dueDate)}`;
  return h("span", { class: "chip " + (it.status === "due" ? "today" : "off"), title: relLabel(it.dueDate) }, label);
}

function maintIcon(icon) { return h("span", { class: "maint-icon", "aria-hidden": "true" }, icon || "🔧"); }

// ---------------------------------------------------------------------------------------------------
// The tab
// ---------------------------------------------------------------------------------------------------
async function renderMaintenance() {
  const root = $("#tab-maintenance");
  if (!maintEnabled()) {
    mount(root, pageHead("Maintenance"), h("div", { class: "card empty" }, "Maintenance is turned off. ",
      isAdmin() ? h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { sub: "maintenance" }) }, "Turn it on in Admin → Maintenance") : "An admin can turn it on."));
    return;
  }
  if (!maint.data) mount(root, pageHead("Maintenance"), spinner());
  try {
    const [data, sugg] = await Promise.all([api("/api/maintenance"), api("/api/maintenance/suggestions")]);
    state.today = data.today;
    maint.data = data;
    maint.sugg = sugg;
  } catch (e) {
    if (e.status === 409) { await refreshMaintFlag(); showTab("calendar"); return; }
    mount(root, pageHead("Maintenance"), errorCard(e, renderMaintenance));
    return;
  }
  paintMaintenance();
}

function paintMaintenance() {
  const root = $("#tab-maintenance");
  const d = maint.data;
  const again = () => renderMaintenance();
  syncMaintNav(d.overdueCount);
  const needs = d.items.filter((x) => x.status === "overdue" || x.status === "due");
  const horizon = addDays(d.today, d.upcomingDays);
  const coming = d.items.filter((x) => x.status === "upcoming" && x.dueDate <= horizon).sort((a, b) => (a.dueDate < b.dueDate ? -1 : 1));

  const newBtn = h("button", { class: "btn-primary", type: "button", onclick: () => openMaintForm(null, again) }, "+ New item");
  const filesNote = d.files.configured && !d.files.online
    ? h("div", { class: "warn-box soft" }, "📎 Files: ", d.files.reason) : null;

  // ---- needs doing ----
  const needsBody = needs.length ? h("div", null, needs.map((it) => maintRow(it, again, { actions: true })))
    : h("div", { class: "empty" }, "Nothing needs doing right now. 🎉");

  // ---- coming up, by month ----
  let comingBody;
  if (coming.length) {
    const byMonth = {};
    coming.forEach((it) => { const k = it.dueDate.slice(0, 7); (byMonth[k] = byMonth[k] || []).push(it); });
    comingBody = h("div", null, Object.keys(byMonth).sort().map((k) => {
      const dt = parseIso(k + "-01");
      return h("div", null, h("div", { class: "group-head" }, MONTHS_FULL[dt.getMonth()] + " " + dt.getFullYear()),
        byMonth[k].map((it) => maintRow(it, again, { actions: false })));
    }));
  } else comingBody = h("div", { class: "empty" }, `Nothing else is due in the next ${d.upcomingDays} days.`);

  // ---- jobs ----
  const jobList = { id: d.listId, kind: "shared", name: "Maintenance", ownerUserId: null };
  const jobsBody = h("div", null,
    d.listId ? quickAddCard({ list: jobList, onAdded: again }) : null,
    d.jobs.length ? h("div", { class: "task-list", style: "margin-top:10px" }, d.jobs.map((t) => taskRow(t, { onChange: again })))
      : h("div", { class: "empty" }, "No open jobs. Add one-off jobs like “Fix the dripping tap” here — they're tasks in the Maintenance list."));

  // ---- suggestions ----
  const suggBody = suggestionsBody(again);

  // ---- all upkeep, by category ----
  let allBody;
  if (d.items.length) {
    const byCat = {};
    d.items.forEach((it) => { (byCat[it.categoryLabel] = byCat[it.categoryLabel] || []).push(it); });
    allBody = h("div", null, Object.keys(byCat).sort().map((c) => h("div", null, h("div", { class: "group-head" }, c),
      byCat[c].sort((a, b) => a.name.localeCompare(b.name)).map((it) => maintRow(it, again, { manage: true })))));
  } else allBody = h("div", { class: "empty" }, "No upkeep set up yet. Add one from Suggestions, or “+ New item”.");

  // ---- history ----
  const histBox = h("div", null, spinner());
  loadHistory(histBox, again);

  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Maintenance"), newBtn),
    filesNote,
    section("needs", "Needs doing", needs.length, needsBody),
    section("coming", "Coming up", coming.length, comingBody),
    section("jobs", "Open jobs", d.jobs.length, jobsBody),
    section("suggestions", `Suggestions · ${maint.sugg.seasonLabel}`, maint.sugg.items.length, suggBody),
    section("all", "All upkeep", d.items.length, allBody),
    section("history", "History", null, histBox));
}

function maintRow(it, again, opts = {}) {
  const chips = [
    maintStatusChip(it),
    it.snoozedTo ? h("span", { class: "chip", title: "Snoozed" }, "💤 ", fmtDate(it.snoozedTo)) : null,
    it.assigneeName ? h("span", { class: "chip" }, "👤 ", it.assigneeName) : null,
    it.place ? h("a", { class: "chip btnlike", href: mapsUrl(it.place.address), target: "_blank", rel: "noopener noreferrer" }, "📍 ", it.place.name) : null,
    it.files.length ? h("span", { class: "chip", title: "Files" }, "📎 ", String(it.files.length)) : null,
    linkChip(it.url),
  ].filter(Boolean);
  const last = it.lastDone ? `last done ${fmtDate(it.lastDone, { year: true })}` : "never done";
  const openIt = () => openMaintItem(it, again);
  const name = h("div", { class: "task-title", tabindex: "0", role: "button" }, it.name);
  name.addEventListener("click", openIt);
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") openIt(); });
  const btns = [];
  if (opts.actions || opts.manage) {
    if (it.status !== "paused") btns.push(h("button", { class: "btn-primary btn-small", type: "button", onclick: () => markDoneDialog(it, again) }, "✓ Done"));
    if (opts.actions && it.status !== "paused") btns.push(h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => snoozeDialog(it, again) }, "💤 Snooze"));
  }
  if (opts.manage) btns.push(h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => openMaintForm(it, again) }, "✎ Edit"));
  return h("div", { class: "maint-row" + (it.status === "overdue" ? " overdue-row" : ""), dataset: { id: it.id } },
    maintIcon(it.icon),
    h("div", { class: "task-main" }, name,
      h("div", { class: "sub hint" }, it.repeatLabel, " · ", last),
      h("div", { class: "task-meta" }, chips)),
    btns.length ? h("div", { class: "maint-actions" }, btns) : null);
}

// ---------------------------------------------------------------------------------------------------
// One item: details, files, its history
// ---------------------------------------------------------------------------------------------------
async function openMaintItem(it, again) {
  let changed = false;
  const box = h("div", null, spinner());
  const modal = openModal(`${it.icon || "🔧"} ${it.name}`, box, { onClose: () => { if (changed) again(); } });
  const reload = async () => {
    changed = true;
    try {
      const d = await api("/api/maintenance");
      maint.data = d;
      const fresh = d.items.find((x) => x.id === it.id);
      if (!fresh) { modal.close(); return; }
      it = fresh;
      paint();
    } catch (e) { fail(e); }
  };
  async function paint() {
    let hist = [];
    try { hist = (await api(`/api/maintenance/history?item=${encodeURIComponent(it.id)}`)).records; } catch (e) { /* shown empty */ }
    const kv = (label, value) => value ? h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, label), h("div", { class: "kv-value" }, value)) : null;
    const told = it.recipientsMode === "custom" ? it.recipientNames.join(", ") || "Nobody chosen" : `Household default (${it.recipientNames.join(", ") || "nobody yet"})`;
    const actions = h("div", { class: "actions", style: "justify-content:flex-start" },
      it.status !== "paused" ? h("button", { class: "btn-primary", type: "button", onclick: () => markDoneDialog(it, reload) }, "✓ Mark done") : null,
      it.status !== "paused" ? h("button", { class: "btn-ghost", type: "button", onclick: () => snoozeDialog(it, reload) }, "💤 Snooze") : null,
      h("button", { class: "btn-ghost", type: "button", onclick: async () => {
        try { await api(`/api/maintenance/items/${it.id}`, { method: "PATCH", body: { paused: it.status !== "paused" } }); toast(it.status === "paused" ? "Resumed" : "Paused"); reload(); }
        catch (e) { fail(e); }
      } }, it.status === "paused" ? "▶ Resume" : "⏸ Pause"),
      h("button", { class: "btn-secondary", type: "button", onclick: () => openMaintForm(it, reload) }, "✎ Edit"));
    mount(box,
      h("div", { class: "task-meta", style: "margin-bottom:10px" }, maintStatusChip(it), h("span", { class: "chip" }, it.categoryLabel)),
      h("div", { class: "kv" },
        kv("Repeats", it.repeatLabel),
        kv("Due", it.dueDate ? `${fmtDate(it.dueDate, { year: true })} · ${relLabel(it.dueDate)}` : null),
        kv("Last done", it.lastDone ? fmtDate(it.lastDone, { year: true }) + (it.lastRecord && it.lastRecord.by ? ` by ${it.lastRecord.by}` : "") : "Never"),
        kv("Remind ahead", it.leadDays ? `${it.leadDays} day${it.leadDays === 1 ? "" : "s"}` : "On the day"),
        kv("Who is told", told),
        kv("Assigned to", it.assigneeName),
        kv("Place", it.place ? it.place.name : null),
        kv("Home Assistant", it.exposeSensor ? h("code", { class: "entity" }, it.entityId) : null)),
      it.notes ? h("div", { class: "task-notes", style: "margin-top:10px" }, it.notes) : null,
      it.howto ? h("details", { class: "maint-howto" }, h("summary", null, "How to"), h("div", { class: "task-notes" }, it.howto)) : null,
      it.url ? h("div", { style: "margin-top:8px" }, linkChip(it.url)) : null,
      actions,
      h("h4", { class: "maint-sub" }, "Manuals and papers"),
      filesBox({ files: it.files, owner: { item_id: it.id }, onChange: reload }),
      h("h4", { class: "maint-sub" }, "History"),
      hist.length ? h("div", null, hist.map((r) => historyRow(r, reload, { showItem: false }))) : h("div", { class: "hint" }, "Not marked done yet."),
      h("div", { class: "actions" },
        h("button", { class: "btn-danger", type: "button", onclick: async () => {
          if (!confirm(`Delete “${it.name}” and its history?` + (it.files.length || hist.some((r) => r.files.length) ? "\n\nIts files move to _deleted in the files folder (kept 30 days)." : ""))) return;
          try { await api(`/api/maintenance/items/${it.id}`, { method: "DELETE" }); changed = true; modal.close(); toast("Deleted"); }
          catch (e) { fail(e); }
        } }, "🗑 Delete")));
  }
  paint();
}

function historyRow(r, onChange, opts = {}) {
  const undo = r.latest ? h("button", { class: "btn-ghost btn-small", type: "button", title: "Take back this Mark done" }, "↶ Undo") : null;
  if (undo) {
    undo.addEventListener("click", async () => {
      if (!confirm(`Undo marking “${r.itemName}” done on ${fmtDate(r.date, { year: true })}?` + (r.files.length ? " Its files move to _deleted." : ""))) return;
      undo.disabled = true;
      try { await api(`/api/maintenance/items/${r.itemId}/undo`, { method: "POST", body: {} }); toast("Undone"); onChange(); }
      catch (e) { fail(e); undo.disabled = false; }
    });
  }
  return h("div", { class: "maint-hist-row" },
    h("div", { class: "maint-hist-date" }, fmtDate(r.date, { year: true })),
    h("div", { class: "task-main" },
      opts.showItem ? h("div", { class: "task-title plain" }, (r.itemIcon || "🔧") + " " + r.itemName) : null,
      h("div", { class: "task-meta" },
        r.by ? h("span", { class: "chip" }, "👤 ", r.by) : null,
        r.cost !== null ? h("span", { class: "chip" }, "💰 ", money(r.cost)) : null,
        r.dueWas && r.dueWas < r.date ? h("span", { class: "chip", title: `Was due ${fmtDate(r.dueWas, { year: true })}` }, `${daysBetween(r.dueWas, r.date)} days late`) : null),
      r.note ? h("div", { class: "task-notes" }, r.note) : null,
      r.files.length ? filesBox({ files: r.files, owner: null, onChange, readOnly: true }) : null),
    undo);
}

async function loadHistory(box, again) {
  try {
    const q = new URLSearchParams();
    if (maint.histYear) q.set("year", maint.histYear);
    if (maint.histCat) q.set("category", maint.histCat);
    const data = await api("/api/maintenance/history?" + q);
    const yearSel = h("select", { "aria-label": "Year", value: maint.histYear, onchange: (e) => { maint.histYear = e.target.value; loadHistory(box, again); } },
      h("option", { value: "" }, "All years"), data.years.map((y) => h("option", { value: y }, y)));
    const catSel = h("select", { "aria-label": "Category", value: maint.histCat, onchange: (e) => { maint.histCat = e.target.value; loadHistory(box, again); } },
      h("option", { value: "" }, "All categories"), (maint.data ? maint.data.categories : []).map((c) => h("option", { value: c.key }, c.label)));
    const csvHref = withUser("api/maintenance/history.csv" + (maint.histYear ? `?year=${maint.histYear}` : ""));
    const total = maint.histYear ? data.yearTotals[maint.histYear] : null;
    mount(box,
      h("div", { class: "toolbar" }, yearSel, catSel,
        h("a", { class: "btn-ghost btn-small", href: csvHref, download: "" }, "⬇ Export CSV"),
        total ? h("span", { class: "hint" }, `Spent in ${maint.histYear}: ${money(total, data.currency)}`) : null),
      data.records.length ? h("div", null, data.records.map((r) => historyRow(r, again, { showItem: true })))
        : h("div", { class: "empty" }, "Nothing marked done yet."));
  } catch (e) { mount(box, errorCard(e, () => loadHistory(box, again))); }
}

// ---------------------------------------------------------------------------------------------------
// Mark done / Snooze
// ---------------------------------------------------------------------------------------------------
function markDoneDialog(it, onDone) {
  const date = h("input", { type: "date", value: todayIso(), max: todayIso(), "aria-label": "Done on" });
  const note = h("textarea", { maxlength: "500", placeholder: "Note (optional) — e.g. filter size, who came, what they found", style: "min-height:52px" });
  const cur = maint.data ? maint.data.currency : "";
  const cost = h("input", { type: "number", min: "0", step: "0.01", inputmode: "decimal", placeholder: "0.00", "aria-label": "Cost", style: "width:130px" });
  const picker = pendingFiles();
  const err = h("div", { class: "error-text" });
  const ok = h("button", { class: "btn-primary", type: "button" }, "✓ Mark done");
  const m = openModal(`Mark “${it.name}” done`, h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Done on", date),
      h("label", { class: "field narrow" }, "Cost" + (cur ? ` (${cur})` : "") + " — optional", cost)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Note", note)),
    h("div", { class: "field" }, "Receipt or photos (optional)", picker.el),
    h("div", { class: "hint" }, it.mode === "interval" ? "The next due date is worked out from this date." : "Clears this due date; the dates after it don't move."),
    err, h("div", { class: "actions" }, ok)));
  ok.addEventListener("click", async () => {
    err.textContent = "";
    const body = { date: date.value || todayIso() };
    if (note.value.trim()) body.note = note.value.trim();
    if (cost.value.trim() !== "") {
      const v = Number(cost.value);
      if (!isFinite(v) || v < 0) { err.textContent = "The cost must be a number."; return; }
      body.cost = v;
    }
    ok.disabled = true;
    try {
      const r = await api(`/api/maintenance/items/${it.id}/done`, { method: "POST", body });
      if (picker.files().length) {
        ok.textContent = "Uploading…";
        const failed = await uploadAll(picker.files(), { done_id: r.recordId }, picker.progress);
        if (failed) toast(`Marked done, but ${failed} file${failed === 1 ? "" : "s"} couldn't be uploaded`, true);
      }
      m.close();
      toast(r.item.dueDate ? `Done — next due ${fmtDate(r.item.dueDate, { year: true })}` : "Done");
      onDone();
    } catch (e) { err.textContent = e.message; ok.disabled = false; ok.textContent = "✓ Mark done"; }
  });
}

function snoozeDialog(it, onDone) {
  const go = async (body, label) => {
    try { await api(`/api/maintenance/items/${it.id}/snooze`, { method: "POST", body }); m.close(); toast(label); onDone(); }
    catch (e) { fail(e); }
  };
  const dateIn = h("input", { type: "date", min: addDays(todayIso(), 1), "aria-label": "Snooze until" });
  const m = openModal(`Snooze “${it.name}”`, h("div", null,
    h("div", { class: "hint", style: "margin-bottom:10px" }, "Moves this one due date without marking it done."),
    h("div", { class: "actions", style: "justify-content:flex-start" },
      h("button", { class: "btn-secondary", type: "button", onclick: () => go({ days: 1 }, "Snoozed a day") }, "1 day"),
      h("button", { class: "btn-secondary", type: "button", onclick: () => go({ days: 7 }, "Snoozed a week") }, "1 week"),
      h("button", { class: "btn-secondary", type: "button", onclick: () => go({ days: 30 }, "Snoozed a month") }, "1 month")),
    h("div", { class: "form-row", style: "margin-top:10px" }, h("label", { class: "field" }, "Or until", dateIn),
      h("button", { class: "btn-primary", type: "button", onclick: () => { if (dateIn.value) go({ until: dateIn.value }, "Snoozed"); else toast("Pick a date", true); } }, "Snooze")),
    it.snoozedTo ? h("div", { class: "actions", style: "justify-content:flex-start" },
      h("button", { class: "btn-ghost", type: "button", onclick: () => go({ until: null }, "Snooze cancelled") }, "Cancel the snooze")) : null), { sheet: true });
}

// ---------------------------------------------------------------------------------------------------
// New / edit item
// ---------------------------------------------------------------------------------------------------
function calendarState(item) {
  // The calendar mode's simple form: every N months (on the first date's day) or every N years
  const s = { n: 6, unit: "month", ok: true };
  if (!item || item.mode !== "calendar") return s;
  let m;
  if ((m = /^monthly:(\d+)$/.exec(item.rule))) { s.n = 1; s.unit = "month"; }
  else if ((m = /^months:(\d+):(\d+)$/.exec(item.rule))) { s.n = Number(m[1]); s.unit = "month"; }
  else if ((m = /^years:(\d+)$/.exec(item.rule))) { s.n = Number(m[1]); s.unit = "year"; }
  else s.ok = false;
  return s;
}

function openMaintForm(item, onSaved, prefill = null) {
  const editing = !!item;
  const src = item || prefill || {};
  const d = maint.data || { categories: [] };
  const name = h("input", { type: "text", maxlength: "60", value: src.name || "", placeholder: "e.g. Replace the furnace filter" });
  const icon = h("input", { type: "text", maxlength: "8", value: src.icon || "", placeholder: "🔧", style: "width:70px", "aria-label": "Icon (emoji)" });
  const catSel = h("select", { value: src.category || "other", "aria-label": "Category" }, d.categories.map((c) => h("option", { value: c.key }, c.label)));
  let mode = src.mode || "interval";
  const modeSeg = segmented([["interval", "After it's done"], ["calendar", "On set dates"]], mode, (v) => { mode = v; refresh(); });

  // interval
  const everyN = h("input", { type: "number", min: "1", max: "365", value: String(src.everyN || 3), style: "width:80px", "aria-label": "Every" });
  const everyUnit = h("select", { value: src.everyUnit || "month", "aria-label": "Unit" }, UNIT_LABELS.map(([v, l]) => h("option", { value: v }, l)));
  const lastDone = h("input", { type: "date", max: todayIso(), value: editing ? (src.lastDone || "") : "", "aria-label": "Last done" });
  const never = h("input", { type: "checkbox", checked: editing ? !src.lastDone : true });
  const firstDue = h("input", { type: "date", value: editing && !src.lastDone ? (src.startDate || todayIso()) : todayIso(), "aria-label": "First due" });
  // calendar
  const cs = calendarState(item);
  const calN = h("input", { type: "number", min: "1", max: "36", value: String(cs.n), style: "width:80px", "aria-label": "Every" });
  const calUnit = h("select", { value: cs.unit, "aria-label": "Unit" }, h("option", { value: "month" }, "months"), h("option", { value: "year" }, "years"));
  const anchor = h("input", { type: "date", value: src.anchorDate || src.anchor_date || todayIso(), "aria-label": "First date" });
  let calTouched = !editing || src.mode !== "calendar";
  [calN, calUnit, anchor].forEach((el) => el.addEventListener("input", () => { calTouched = true; refresh(); }));
  const calNote = h("div", { class: "hint" });

  const lead = h("select", { value: String(src.leadDays !== undefined ? src.leadDays : 7), "aria-label": "Remind ahead" },
    LEAD_OPTIONS.concat(src.leadDays !== undefined && !LEAD_OPTIONS.includes(src.leadDays) ? [src.leadDays] : []).sort((a, b) => a - b)
      .map((n) => h("option", { value: String(n) }, n === 0 ? "On the day" : `${n} day${n === 1 ? "" : "s"} before`)));
  const overdue = h("select", { value: String(src.overdueEvery !== undefined ? src.overdueEvery : 7), "aria-label": "Overdue reminders" },
    OVERDUE_OPTIONS.map(([v, l]) => h("option", { value: String(v) }, l)));
  const people = state.users.filter((u) => !u.disabled || (editing && u.id === src.assignedTo));
  const asn = h("select", { value: src.assignedTo || "", "aria-label": "Assigned to" },
    h("option", { value: "" }, "Nobody"), people.map((u) => h("option", { value: u.id }, u.name)));
  let rmode = src.recipientsMode || "default";
  const chosen = new Set(editing && src.recipientsMode === "custom" ? src.recipients : []);
  const recBoxes = state.users.filter((u) => !u.disabled).map((u) => {
    const cb = h("input", { type: "checkbox", checked: chosen.has(u.id) });
    cb.addEventListener("change", () => { if (cb.checked) chosen.add(u.id); else chosen.delete(u.id); });
    return h("label", { class: "mini-toggle" }, cb, u.name);
  });
  const recWrap = h("div", { class: "chip-row" }, recBoxes);
  const recSeg = segmented([["default", "Household default"], ["custom", "Chosen people"]], rmode, (v) => { rmode = v; refresh(); });
  const place = placePicker(editing ? { placeId: src.placeId, place: src.place } : null);
  const urlIn = h("input", { type: "url", maxlength: "2000", inputmode: "url", placeholder: "https://… (a manual, a product page)", value: src.url || "", "aria-label": "Link" });
  const notes = h("textarea", { maxlength: "1000", placeholder: "Notes — filter size, model number, who to call", style: "min-height:52px" }, src.notes || "");
  const howto = h("textarea", { maxlength: "2000", placeholder: "How to (steps)", style: "min-height:70px" }, src.howto || "");
  const sensor = h("input", { type: "checkbox", checked: !!src.exposeSensor });
  const err = h("div", { class: "error-text" });
  const save = h("button", { class: "btn-primary", type: "button" }, editing ? "Save" : "Add");

  const intervalBox = h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Every", h("span", { class: "inline-pair" }, everyN, everyUnit))),
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Last done", lastDone),
      h("label", { class: "mini-toggle" }, never, "Never / not sure"),
      h("label", { class: "field" }, "First due", firstDue)),
    h("div", { class: "hint" }, "It's due again this long after each Mark done."));
  const calendarBox = h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Every", h("span", { class: "inline-pair" }, calN, calUnit)),
      h("label", { class: "field" }, "First date", anchor)),
    calNote);
  function refresh() {
    intervalBox.hidden = mode !== "interval";
    calendarBox.hidden = mode !== "calendar";
    lastDone.disabled = never.checked;
    firstDue.parentElement.hidden = !never.checked;
    everyN.max = String(UNIT_MAX[everyUnit.value]);
    recWrap.hidden = rmode !== "custom";
    const dt = parseIso(anchor.value || todayIso());
    calNote.textContent = !cs.ok && !calTouched ? `Currently: ${item.repeatLabel}. Change the fields to replace it.`
      : calUnit.value === "year" ? `Due every ${calN.value === "1" ? "" : calN.value + " "}year${calN.value === "1" ? "" : "s"} on ${dt.getDate()} ${MONTHS[dt.getMonth()]}, starting ${fmtDate(anchor.value || todayIso(), { year: true })}.`
        : `Due every ${calN.value === "1" ? "month" : calN.value + " months"} on day ${dt.getDate()}, starting ${fmtDate(anchor.value || todayIso(), { year: true })}.`;
  }
  never.addEventListener("change", refresh);
  everyUnit.addEventListener("change", refresh);
  refresh();

  const form = h("div", null,
    prefill && prefill.why ? h("div", { class: "hint", style: "margin-bottom:8px" }, prefill.why) : null,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Name", name), h("label", { class: "field narrow" }, "Icon", icon)),
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Category", catSel)),
    h("div", { class: "field", style: "margin-bottom:8px" }, "Repeats", modeSeg.el),
    intervalBox, calendarBox,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Remind", lead), h("label", { class: "field" }, "While overdue, remind", overdue),
      h("label", { class: "field" }, "Assigned to", asn)),
    h("div", { class: "field", style: "margin-bottom:8px" }, "Who is told", recSeg.el, recWrap,
      h("span", { class: "hint" }, "The assignee is always told. People can turn maintenance off for themselves in Settings.")),
    h("div", { class: "form-row" }, h("div", { class: "field wide" }, "Place", place.control, place.mapsLink)),
    place.newBox,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Link (optional)", urlIn)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Notes", notes)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "How to", howto)),
    h("div", { class: "form-row" }, h("label", { class: "mini-toggle" }, sensor, "Publish to Home Assistant (on while it's due or overdue)")),
    err, h("div", { class: "actions" }, save));
  const modal = openModal(editing ? "Edit maintenance item" : prefill ? "Add suggested item" : "New maintenance item", form);

  save.addEventListener("click", async () => {
    err.textContent = "";
    const body = {
      name: name.value.trim(), icon: icon.value.trim() || null, category: catSel.value, mode,
      lead_days: Number(lead.value), overdue_every: Number(overdue.value), assigned_to: asn.value || null,
      recipients_mode: rmode, url: urlIn.value.trim() || null, notes: notes.value.trim() || null,
      howto: howto.value.trim() || null, expose_sensor: sensor.checked,
    };
    if (rmode === "custom") body.recipients = [...chosen];
    if (mode === "interval") {
      body.every_n = Number(everyN.value);
      body.every_unit = everyUnit.value;
      if (!editing || src.mode !== "interval" || lastDone.value !== (src.lastDone || "") || never.checked !== !src.lastDone) {
        body.last_done = never.checked ? null : (lastDone.value || null);
        if (never.checked) body.start_date = firstDue.value || todayIso();
        if (!never.checked && !lastDone.value) { err.textContent = "Pick when it was last done, or tick “Never / not sure”."; return; }
      }
    } else if (calTouched) {
      if (!anchor.value) { err.textContent = "Pick the first date."; return; }
      const n = Number(calN.value);
      const day = parseIso(anchor.value).getDate();
      body.rule = calUnit.value === "year" ? `years:${n}` : n === 1 ? `monthly:${day}` : `months:${n}:${day}`;
      body.anchor_date = anchor.value;
    }
    if (!editing && prefill && prefill.key) body.suggestion_key = prefill.key;
    save.disabled = true;
    try {
      body.place_id = await place.resolve();
      if (editing) await api(`/api/maintenance/items/${item.id}`, { method: "PATCH", body });
      else await api("/api/maintenance/items", { method: "POST", body });
      modal.close();
      toast(editing ? "Saved" : "Added");
      onSaved();
    } catch (e) { err.textContent = e.message; save.disabled = false; }
  });
}

// ---------------------------------------------------------------------------------------------------
// Suggestions
// ---------------------------------------------------------------------------------------------------
function suggestionsBody(again) {
  const s = maint.sugg;
  const card = (x, hidden) => {
    const add = h("button", { class: "btn-primary btn-small", type: "button" }, "+ Add");
    add.addEventListener("click", () => openMaintForm(null, again, {
      key: x.key, name: x.name, icon: x.icon, category: x.category, why: x.why,
      howto: (x.steps || []).map((t, i) => `${i + 1}. ${t}`).join("\n"),
      mode: x.defaults.mode, everyN: x.defaults.every_n, everyUnit: x.defaults.every_unit, anchorDate: x.defaults.anchor_date,
    }));
    const hide = h("button", { class: "btn-ghost btn-small", type: "button" }, hidden ? "Show again" : "Not for us");
    hide.addEventListener("click", async () => {
      hide.disabled = true;
      try { await api(`/api/maintenance/suggestions/${encodeURIComponent(x.key)}/hidden`, { method: hidden ? "DELETE" : "PUT" }); again(); }
      catch (e) { fail(e); hide.disabled = false; }
    });
    return h("div", { class: "maint-sugg" + (hidden ? " hidden-sugg" : "") },
      maintIcon(x.icon),
      h("div", { class: "task-main" },
        h("div", { class: "task-title plain" }, x.name),
        h("div", { class: "task-meta" },
          x.inSeason ? h("span", { class: "chip today" }, "In season") : null,
          h("span", { class: "chip" }, x.repeatLabel),
          h("span", { class: "chip" }, x.who === "pro" ? "Call a pro" : "DIY", x.minutes ? ` · ~${x.minutes < 60 ? x.minutes + " min" : Math.round(x.minutes / 60 * 10) / 10 + " h"}` : ""),
          x.custom ? h("span", { class: "chip sched" }, "Ours") : null),
        x.why ? h("div", { class: "task-notes" }, x.why) : null,
        x.steps && x.steps.length ? h("details", { class: "maint-howto" }, h("summary", null, "How"),
          h("ol", null, x.steps.map((t) => h("li", null, t)))) : null),
      h("div", { class: "maint-actions" }, hidden ? null : add, hide));
  };
  return h("div", null,
    h("div", { class: "hint", style: "margin-bottom:8px" },
      `It's ${s.seasonLabel.toLowerCase()} here${s.south ? " (southern hemisphere)" : ""}; jobs for this season or the next month come first. `,
      "Suggestions follow what the house has — ",
      isAdmin() ? h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { sub: "maintenance" }) }, "the home profile") : "the home profile an admin sets", "."),
    s.items.length ? h("div", null, (maint.showAllSugg ? s.items : s.items.slice(0, SUGG_FIRST)).map((x) => card(x, false))) : h("div", { class: "empty" }, "No more suggestions — everything that fits is set up or hidden."),
    s.items.length > SUGG_FIRST ? h("div", { style: "margin-top:8px" }, h("button", { class: "link-btn", type: "button",
      onclick: () => { maint.showAllSugg = !maint.showAllSugg; paintMaintenance(); } }, maint.showAllSugg ? "Show fewer" : `Show all ${s.items.length}`)) : null,
    s.hidden.length ? h("div", { style: "margin-top:8px" },
      h("button", { class: "link-btn", type: "button", onclick: () => { maint.showHidden = !maint.showHidden; paintMaintenance(); } },
        maint.showHidden ? "Hide the hidden ones" : `Show hidden (${s.hidden.length})`),
      maint.showHidden ? h("div", null, s.hidden.map((x) => card(x, true))) : null) : null);
}

// ---------------------------------------------------------------------------------------------------
// Files: a list with previews, and an upload queue (several at once, progress, drag & drop)
// ---------------------------------------------------------------------------------------------------
function fileUrl(id, thumb) { return withUser(`api/maintenance/files/${encodeURIComponent(id)}` + (thumb ? "?thumb=1" : "")); }
function fmtSize(n) { return n < 1024 ? `${n} B` : n < 1048576 ? `${Math.round(n / 1024)} KB` : `${(n / 1048576).toFixed(1)} MB`; }

function filesBox({ files, owner, onChange, readOnly = false }) {
  const st = (maint.data && maint.data.files) || { configured: false, online: false };
  const list = h("div", { class: "maint-files" }, files.map((f) => {
    const del = readOnly ? null : h("button", { class: "icon-btn", type: "button", title: "Delete", "aria-label": `Delete ${f.name}` }, "✕");
    if (del) {
      del.addEventListener("click", async (e) => {
        e.preventDefault();
        if (!confirm(`Delete ${f.name}? It moves to _deleted in the files folder for 30 days.`)) return;
        try { await api(`/api/maintenance/files/${f.id}`, { method: "DELETE" }); toast("Deleted"); onChange(); } catch (err) { fail(err); }
      });
    }
    return h("div", { class: "maint-file" },
      h("a", { href: fileUrl(f.id), target: "_blank", rel: "noopener", title: `${f.name} · ${fmtSize(f.size)}` },
        f.thumb ? h("img", { src: fileUrl(f.id, true), alt: "", loading: "lazy" }) : h("span", { class: "maint-file-ico" }, f.mime === "application/pdf" ? "📄" : "📎"),
        h("span", { class: "maint-file-name" }, f.name)),
      del);
  }));
  if (readOnly || !owner) return files.length ? list : null;
  if (!st.configured) return h("div", null, list, h("div", { class: "hint" }, "Attaching files is off — ", isAdmin()
    ? h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { sub: "settings" }) }, "choose a files folder in App settings") : "an admin can choose a files folder", "."));
  if (!st.online) return h("div", null, list, h("div", { class: "hint warn" }, "📎 ", st.reason || "The files folder isn't connected."));
  const picker = pendingFiles({ immediate: async (fs, progress) => {
    const failed = await uploadAll(fs, owner, progress);
    if (failed) toast(`${failed} file${failed === 1 ? "" : "s"} couldn't be uploaded`, true); else toast("Uploaded");
    onChange();
  } });
  return h("div", null, files.length ? list : h("div", { class: "hint" }, "No files yet."), picker.el);
}

// A file chooser + drop zone. With `immediate`, files upload as soon as they're chosen; otherwise they wait
// in a list (Mark done uploads them once the record exists).
function pendingFiles({ immediate = null } = {}) {
  const st = (maint.data && maint.data.files) || { configured: false, online: false, maxBytes: 25 * 1048576 };
  let queued = [];
  const input = h("input", { type: "file", multiple: true, hidden: true });
  const names = h("div", { class: "chip-row" });
  const bar = h("div", { class: "maint-progress", hidden: true }, h("i"));
  const label = h("span", { class: "hint" });
  const btn = h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => input.click() }, "📎 Add files…");
  const zone = h("div", { class: "maint-drop" }, btn, h("span", { class: "hint" }, " or drop them here"), names, bar, label);
  const disabled = !st.configured || !st.online;
  if (disabled) {
    return { el: h("div", { class: "hint" }, !st.configured ? "Attaching files is off (no files folder)." : "📎 " + (st.reason || "The files folder isn't connected.")),
      files: () => [], progress: () => {} };
  }
  const paint = () => mount(names, queued.map((f, i) => h("span", { class: "chip" }, f.name,
    h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${f.name}`, onclick: () => { queued.splice(i, 1); paint(); } }, "✕"))));
  const take = (list) => {
    const fs = [...list].filter((f) => {
      if (f.size > st.maxBytes) { toast(`${f.name} is larger than ${Math.round(st.maxBytes / 1048576)} MB`, true); return false; }
      return true;
    });
    if (!fs.length) return;
    if (immediate) immediate(fs, progress); else { queued = queued.concat(fs); paint(); }
  };
  function progress(done, total, pct) {
    bar.hidden = total === 0 || done >= total;
    bar.firstChild.style.width = `${Math.round(((done + (pct || 0)) / Math.max(total, 1)) * 100)}%`;
    label.textContent = total && done < total ? `Uploading ${done + 1} of ${total}…` : "";
  }
  input.addEventListener("change", () => { take(input.files); input.value = ""; });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); if (e.dataTransfer) take(e.dataTransfer.files); });
  return { el: h("div", null, input, zone), files: () => queued.slice(), progress };
}

function uploadOne(file, owner, onPct) {
  return new Promise((resolve, reject) => {
    const q = new URLSearchParams(owner).toString();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", withUser("api/maintenance/files?" + q));
    xhr.upload.addEventListener("progress", (e) => { if (e.lengthComputable) onPct(e.loaded / e.total); });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else {
        let msg = `HTTP ${xhr.status}`;
        try { msg = errorMessage(JSON.parse(xhr.responseText).detail, xhr.status); } catch (e) { /* not JSON */ }
        reject(new Error(msg));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Can't reach the app.")));
    const fd = new FormData();
    fd.append("file", file, file.name);
    xhr.send(fd);
  });
}

async function uploadAll(files, owner, progress) {
  let failed = 0;
  for (let i = 0; i < files.length; i++) {
    progress(i, files.length, 0);
    try { await uploadOne(files[i], owner, (p) => progress(i, files.length, p)); }
    catch (e) { failed++; toast(`${files[i].name}: ${e.message}`, true); }
  }
  progress(files.length, files.length, 0);
  return failed;
}

// Files on a one-off job (the task modal, for tasks in the Maintenance list)
function jobFilesSection(task) {
  const box = h("div", { class: "field" }, "Files", spinner());
  const load = async () => {
    try {
      if (!maint.data) maint.data = await api("/api/maintenance");
      const files = await api(`/api/maintenance/files?task_id=${encodeURIComponent(task.id)}`);
      mount(box, h("span", null, "Files"), filesBox({ files, owner: { task_id: task.id }, onChange: load }));
    } catch (e) { mount(box, h("span", null, "Files"), h("div", { class: "hint" }, e.message)); }
  };
  load();
  return box;
}

// ---------------------------------------------------------------------------------------------------
// Calendar and Schedule tab pieces
// ---------------------------------------------------------------------------------------------------
function calMaintChip(e) {
  const tag = e.projected ? "" : e.status === "overdue" ? " (overdue)" : "";
  return h("span", { class: "cal-chip maint" + (e.projected ? " projected" : "") + (e.status === "overdue" ? " overdue" : ""),
    title: `${e.name}${e.projected ? " — if done on time" : ""}${e.assigneeName ? " · " + e.assigneeName : ""}` },
    (e.icon || "🔧") + " ", e.name, tag);
}

function maintDayEntry(e, again) {
  const done = !e.projected && e.status !== "paused"
    ? h("button", { class: "btn-primary btn-small", type: "button", onclick: async () => {
      try {
        if (!maint.data) maint.data = await api("/api/maintenance");
        const it = maint.data.items.find((x) => x.id === e.itemId);
        if (it) markDoneDialog(it, again);
      } catch (err) { fail(err); }
    } }, "✓ Done") : null;
  return h("div", { class: "sched-entry" + (e.projected ? " struck-soft" : "") },
    h("span", null, e.icon || "🔧"), h("span", { class: "grow" }, h("b", null, e.name)),
    e.projected ? h("span", { class: "chip", title: "When it would be due next if each one is done on time" }, "Expected")
      : e.status === "overdue" ? h("span", { class: "chip overdue" }, `Overdue since ${fmtDate(e.dueDate)}`) : h("span", { class: "chip today" }, "Maintenance"),
    e.assigneeName ? h("span", { class: "chip" }, "👤 ", e.assigneeName) : null,
    done,
    h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => showTab("maintenance") }, "Open"));
}

async function scheduleMaintCard() {
  if (!maintEnabled()) return null;
  let d;
  try { d = await api("/api/maintenance"); } catch (e) { return null; }
  maint.data = d;
  const body = d.items.length ? h("div", null, d.items.map((it) => h("div", { class: "sched-entry" },
    h("span", null, it.icon || "🔧"), h("span", { class: "grow" }, h("b", null, it.name), h("span", { class: "hint" }, " · ", it.repeatLabel)),
    maintStatusChip(it))))
    : h("div", { class: "hint" }, "No upkeep set up yet.");
  return section("schedule-maint", "🔧 Maintenance", d.items.length, h("div", null, body,
    h("div", { class: "hint", style: "margin-top:8px" }, "Recurring upkeep is marked done on the ",
      h("button", { class: "link-btn", type: "button", onclick: () => showTab("maintenance") }, "Maintenance tab"), ".")));
}

// ---------------------------------------------------------------------------------------------------
// Settings → Reminders: the recipient's own switch
// ---------------------------------------------------------------------------------------------------
function maintPrefRow(prefs, put) {
  if (!maintEnabled()) return null;
  return h("div", { class: "switch-row" },
    h("div", null, h("div", null, "Maintenance notifications"),
      h("div", { class: "sub" }, prefs.maintenanceRecipient
        ? "You're told when house upkeep is due or overdue, at your daily time. Turn it off to stop them for you."
        : "You aren't told about any maintenance at the moment; an admin chooses who is.")),
    toggleSwitch(prefs.maintenanceNotify, (on) => put({ maintenanceNotify: on }), { label: "Maintenance notifications" }));
}

// ---------------------------------------------------------------------------------------------------
// Admin → Maintenance
// ---------------------------------------------------------------------------------------------------
async function renderAdminMaintenance(box) {
  mount(box, spinner());
  let a;
  try { a = await adminApi("/api/admin/maintenance"); } catch (e) { mount(box, errorCard(e, () => renderAdminMaintenance(box))); return; }
  const again = () => renderAdminMaintenance(box);
  const put = async (body, msg) => {
    try {
      a = await adminApi("/api/admin/maintenance", { method: "PUT", body });
      toast(msg || "Saved");
      await refreshMaintFlag();
      again();
    } catch (e) { fail(e); again(); }
  };
  const people = state.users.filter((u) => !u.disabled);
  const rec = new Set(a.recipients);
  const recRows = people.map((u) => {
    const cb = h("input", { type: "checkbox", checked: rec.has(u.id) });
    cb.addEventListener("change", () => { if (cb.checked) rec.add(u.id); else rec.delete(u.id); put({ recipients: [...rec] }, "Recipients saved"); });
    return h("label", { class: "sched-pick-row" }, cb, u.name);
  });
  const prof = new Set(a.profile);
  const profRows = a.features.map((f) => {
    const cb = h("input", { type: "checkbox", checked: prof.has(f.key) });
    cb.addEventListener("change", () => { if (cb.checked) prof.add(f.key); else prof.delete(f.key); put({ profile: [...prof] }, "Home profile saved"); });
    return h("label", { class: "sched-pick-row" }, cb, f.label);
  });
  const fs = a.files;
  mount(box,
    h("div", { class: "card" }, h("h3", null, "Maintenance"),
      h("div", { class: "setting-row" }, h("div", null, h("div", null, "Maintenance tab"),
        h("div", { class: "sub" }, "House upkeep suggestions, recurring jobs that can be marked done, one-off jobs in a Maintenance list, and reminders. Turning it off hides the tab and stops its notifications; nothing is deleted.")),
        toggleSwitch(a.enabled, (on) => put({ enabled: on }, on ? "Maintenance is on" : "Maintenance is off"), { label: "Maintenance" }))),
    a.enabled ? h("div", { class: "card" }, h("h3", null, "Who gets maintenance notifications"),
      h("div", { class: "hint", style: "margin-bottom:8px" }, "The household default. Each item can choose its own people instead, and its assignee is always told. Everyone can turn maintenance off for themselves in Settings."),
      people.length ? h("div", { class: "sched-pick" }, recRows) : h("div", { class: "hint" }, "Nobody yet.")) : null,
    a.enabled ? h("div", { class: "card" }, h("h3", null, "Home profile"),
      h("div", { class: "hint", style: "margin-bottom:8px" }, "Tick what the house has; suggestions that need something else stay out of the way."),
      h("div", { class: "maint-profile" }, profRows)) : null,
    a.enabled ? h("div", { class: "card" }, h("h3", null, "Home Assistant"),
      h("div", { class: "setting-row" }, h("div", null, h("div", null, "Overdue maintenance sensor"),
        h("div", { class: "sub" }, h("code", null, a.sensorEntity), " — how many items are overdue, with their names as attributes.")),
        toggleSwitch(a.sensor, (on) => put({ sensor: on }), { label: "Overdue sensor" }))) : null,
    a.enabled ? h("div", { class: "card" }, h("h3", null, "Files folder"),
      h("div", { class: "hint" }, fs.configured ? [fs.path, " — ", h("strong", null, fs.online ? "Connected" : "Not connected"), fs.online ? null : [": ", fs.reason]]
        : "Not set: attaching files is off."),
      h("div", { class: "actions", style: "justify-content:flex-start" },
        h("button", { class: "btn-secondary", type: "button", onclick: () => showTab("admin", { sub: "settings" }) }, "Choose it in App settings"))) : null,
    a.enabled ? customSuggestionsCard(a, again) : null);
}

function customSuggestionsCard(a, again) {
  const rows = a.custom.map((s) => h("div", { class: "row" },
    h("span", null, (s.icon || "🔧") + " ", h("span", { class: "name" }, s.name), h("span", { class: "hint" }, ` · ${s.repeatLabel} · ${s.categoryLabel}`)),
    h("span", null,
      h("button", { class: "icon-btn", type: "button", title: "Edit", "aria-label": `Edit ${s.name}`, onclick: () => customSuggestionForm(s, a, again) }, "✎"),
      h("button", { class: "icon-btn danger", type: "button", title: "Delete", "aria-label": `Delete ${s.name}`, onclick: async () => {
        if (!confirm(`Delete the suggestion “${s.name}”? Items already added from it stay.`)) return;
        try { await adminApi(`/api/admin/maintenance/suggestions/${s.id}`, { method: "DELETE" }); toast("Deleted"); again(); } catch (e) { fail(e); }
      } }, "🗑"))));
  return h("div", { class: "card" }, h("h3", null, "Our own suggestions"),
    h("div", { class: "hint", style: "margin-bottom:8px" }, "Added to the built-in ones for everyone. Built-in suggestions can only be hidden (“Not for us” on the Maintenance tab)."),
    rows.length ? rows : h("div", { class: "hint" }, "None yet."),
    h("div", { class: "actions", style: "justify-content:flex-start" },
      h("button", { class: "btn-secondary", type: "button", onclick: () => customSuggestionForm(null, a, again) }, "+ New suggestion")));
}

function customSuggestionForm(s, a, again) {
  const editing = !!s;
  const name = h("input", { type: "text", maxlength: "60", value: editing ? s.name : "", placeholder: "e.g. Descale the kettle" });
  const icon = h("input", { type: "text", maxlength: "8", value: editing && s.icon ? s.icon : "", placeholder: "🫖", style: "width:70px" });
  const catSel = h("select", { value: editing ? s.category : "other" }, a.categories.map((c) => h("option", { value: c.key }, c.label)));
  const needs = h("select", { value: editing && s.needs ? s.needs : "" }, h("option", { value: "" }, "Every home"), a.features.map((f) => h("option", { value: f.key }, f.label)));
  let kind = editing && s.seasons ? "seasons" : "every";
  const seg = segmented([["every", "After it's done"], ["seasons", "Each season"]], kind, (v) => { kind = v; refresh(); });
  const n = h("input", { type: "number", min: "1", max: "365", value: String(editing && s.everyN ? s.everyN : 6), style: "width:80px" });
  const unit = h("select", { value: editing && s.everyUnit ? s.everyUnit : "month" }, UNIT_LABELS.map(([v, l]) => h("option", { value: v }, l)));
  const seasons = new Set(editing && s.seasons ? s.seasons : ["spring"]);
  const seasonBoxes = Object.keys(SEASON_NAMES).map((k) => {
    const cb = h("input", { type: "checkbox", checked: seasons.has(k) });
    cb.addEventListener("change", () => { if (cb.checked) seasons.add(k); else seasons.delete(k); });
    return h("label", { class: "mini-toggle" }, cb, SEASON_NAMES[k]);
  });
  const who = h("select", { value: editing ? s.who : "diy" }, h("option", { value: "diy" }, "DIY"), h("option", { value: "pro" }, "Call a pro"));
  const minutes = h("input", { type: "number", min: "1", max: "10000", value: editing && s.minutes ? String(s.minutes) : "", style: "width:90px", placeholder: "30" });
  const why = h("input", { type: "text", maxlength: "300", value: editing && s.why ? s.why : "", placeholder: "Why it matters (one sentence)" });
  const howto = h("textarea", { maxlength: "2000", placeholder: "Steps, one per line", style: "min-height:70px" }, editing ? (s.steps || []).join("\n") : "");
  const everyRow = h("div", { class: "form-row" }, h("label", { class: "field" }, "Every", h("span", { class: "inline-pair" }, n, unit)));
  const seasonRow = h("div", { class: "form-row" }, seasonBoxes);
  const refresh = () => { everyRow.hidden = kind !== "every"; seasonRow.hidden = kind !== "seasons"; };
  refresh();
  const err = h("div", { class: "error-text" });
  const ok = h("button", { class: "btn-primary", type: "button" }, editing ? "Save" : "Add");
  const m = openModal(editing ? "Edit suggestion" : "New suggestion", h("div", null,
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Name", name), h("label", { class: "field narrow" }, "Icon", icon)),
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Category", catSel), h("label", { class: "field" }, "Only for homes with", needs)),
    h("div", { class: "field", style: "margin-bottom:8px" }, "Repeats", seg.el), everyRow, seasonRow,
    h("div", { class: "form-row" }, h("label", { class: "field" }, "Who", who), h("label", { class: "field narrow" }, "Minutes", minutes)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "Why", why)),
    h("div", { class: "form-row" }, h("label", { class: "field wide" }, "How", howto)),
    err, h("div", { class: "actions" }, ok)));
  ok.addEventListener("click", async () => {
    err.textContent = "";
    const body = { name: name.value.trim(), icon: icon.value.trim() || null, category: catSel.value, needs: needs.value || null,
      who: who.value, minutes: minutes.value ? Number(minutes.value) : null, why: why.value.trim() || null, howto: howto.value.trim() || null };
    if (kind === "every") { body.every_n = Number(n.value); body.every_unit = unit.value; } else body.seasons = [...seasons];
    try {
      if (editing) await adminApi(`/api/admin/maintenance/suggestions/${s.id}`, { method: "PATCH", body });
      else await adminApi("/api/admin/maintenance/suggestions", { method: "POST", body });
      m.close(); toast("Saved"); again();
    } catch (e) { err.textContent = e.message; }
  });
}

// ---------------------------------------------------------------------------------------------------
// App settings: the maintenance files folder (checked before saving; never moves files)
// ---------------------------------------------------------------------------------------------------
// The maintenance files folder on Admin → App settings (common/settings.js draws the label and help):
// whether it's connected, Check, and the check's verdict. folder.check() is used again before saving.
function maintFolderControl(page, folder) {
  const input = h("input", { type: "text", id: "set-maintenance_files_path", value: page.value("maintenance_files_path") || "",
    placeholder: "/share/household/maintenance", "aria-label": "Maintenance files folder", spellcheck: "false" });
  const verdict = h("div", { class: "hint" });
  const err = h("div", { class: "error-text" });
  const checkBtn = h("button", { class: "btn-ghost", type: "button" }, "Check");
  const statusLine = h("div", { class: "hint", style: "margin:6px 0" });
  const paintStatus = (s) => mount(statusLine, h("span", null, !s.configured ? "Not set — attaching files is off."
    : [h("strong", { style: s.online ? "color:var(--accent)" : "color:var(--danger)" }, s.online ? "● Connected" : "● Not connected"),
      s.online ? (s.freeBytes ? ` · ${fmtSize(s.freeBytes)} free` : "") : [" — ", s.reason],
      " ", h("button", { class: "link-btn", type: "button", onclick: async () => {
        try { paintStatus(await adminApi("/api/admin/maintenance/files/check", { method: "POST", body: {} })); } catch (e) { fail(e); }
      } }, "Check again"),
      !s.online ? [" · ", h("button", { class: "link-btn", type: "button", onclick: async () => {
        if (!confirm("Set this folder up for maintenance files (creating what the app needs)?")) return;
        try { paintStatus(await adminApi("/api/admin/maintenance/files/check", { method: "POST", body: { useThisFolder: true, confirm: true } })); } catch (e) { fail(e); }
      } }, "Use this folder")] : null]));
  if (page.data.maintenanceFiles) paintStatus(page.data.maintenanceFiles);
  folder.check = async () => {
    err.textContent = "";
    try {
      const info = await adminApi("/api/admin/settings/check-maintenance-folder", { method: "POST", body: { path: input.value.trim() } });
      verdict.textContent = info.message;
      return info;
    } catch (e) { err.textContent = e.message; return null; }
  };
  checkBtn.addEventListener("click", () => folder.check());
  input.addEventListener("input", () => { verdict.textContent = ""; err.textContent = ""; page.set("maintenance_files_path", input.value.trim()); });
  return h("div", { class: "sp-folder", id: "maintFolderCard" }, statusLine,
    h("div", { class: "sp-folder-row" }, input, checkBtn), verdict, err);
}
