let state = {
  // Provisional UTC-slice values, good only until loadToday() below
  // resolves and corrects them to Home Assistant's actual local date —
  // used here just so the very first synchronous render has *something*.
  date: new Date().toISOString().slice(0, 10),
  summaryMonth: new Date().toISOString().slice(0, 7),
  today: new Date().toISOString().slice(0, 10),
  savedFoods: [],
  historyGranularity: "daily",
  weightHistoryGranularity: "daily",
  selfId: null,
  actingUserId: null,
  isAdmin: false,
  ai: null,        // GET /api/ai/status: configured, provider, privacy, …
};

// Appends as_user=<acting user> to a request path, preserving any existing
// query string. Every data endpoint (goals/weight/saved-foods/logs/summary/
// history) is scoped server-side by this — it's how the switcher works.
function withUser(path) {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}as_user=${encodeURIComponent(state.actingUserId)}`;
}

// ---------- helpers ----------
// IMPORTANT: Home Assistant's Ingress serves this app under a per-session
// sub-path (e.g. /api/hassio_ingress/<token>/). An absolute fetch like
// fetch("/api/me") ignores that sub-path entirely and hits Home Assistant's
// own frontend at the domain root instead of this app — which returns
// its own unrelated 404. Stripping the leading slash makes every request
// relative to the current page, which keeps it under the ingress prefix.
// api(path, fetchOptions): the options go to fetch() as they are (callers JSON.stringify their own bodies).
const api = UI.makeApi({
  init: (opts) => ({ headers: { "Content-Type": "application/json" }, ...opts }),
  networkError: null,
  message: (body, res) => (body && body.detail) || res.statusText,
  makeError: (msg) => new Error(msg),
});

function fmt(n) {
  if (n === null || n === undefined || isNaN(n)) return "0";
  return Math.round(n * 10) / 10;
}
// calories may be left out (null): shown as a dash, not 0
function kcalText(n) { return n === null || n === undefined ? "—" : fmt(n); }
function tagChips(tags) {
  return tags && tags.length ? `<span class="food-tags">${tags.map(t => `<span class="food-tag">${escapeHtml(t)}</span>`).join("")}</span>` : "";
}
const MEAL_TIMES = { breakfast: "08:00", lunch: "12:30", dinner: "19:00", snack: "15:30" };

const $ = (sel) => UI.$(sel);

// Every button that saves something goes through here: when the server refuses (a 422 for a number out of range, a
// 404, a 429 …) the person sees its message instead of nothing happening.
function withErrors(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (err) {
      alert(err.message || "Something went wrong.");
    }
  };
}
function $all(sel) { return document.querySelectorAll(sel); }

// Escapes all five HTML-significant characters, quotes included (common/ui.js): several
// templates put user text inside attribute values (title="…", data-copy="…").
const escapeHtml = UI.escapeHtml;

// ---------- "enlarge" popup for long details/notes text ----------
function openDetailsModal(title, text) {
  $("#detailsModalTitle").textContent = title;
  $("#detailsModalBody").textContent = text;
  $("#detailsModal").hidden = false;
}
function closeDetailsModal() {
  $("#detailsModal").hidden = true;
}
$("#detailsModalClose").addEventListener("click", closeDetailsModal);
$("#detailsModal").addEventListener("click", (e) => {
  if (e.target === $("#detailsModal")) closeDetailsModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#detailsModal").hidden) closeDetailsModal();
});

// Full details/notes text for the currently-rendered tables, keyed so the
// "enlarge" popup can look it up by key rather than round-tripping raw text
// (which may contain quotes/newlines) through an HTML attribute.
const detailsStore = {};

// A short, single-line snippet of a details/notes field, with an "enlarge"
// button when there's anything to show (even short text can still get a
// clean expanded view rather than fighting a narrow table cell).
function detailsSnippetHtml(text, title, key) {
  if (!text) return `<span class="hint">—</span>`;
  detailsStore[key] = { title, text };
  const snippet = text.length > 60 ? text.slice(0, 60).trimEnd() + "…" : text;
  return `<span class="details-snippet">` +
    `<span class="snippet-text" title="${escapeHtml(text)}">${escapeHtml(snippet)}</span>` +
    `<button type="button" class="enlarge-btn" data-enlarge-key="${escapeHtml(key)}" title="View full details">⤢</button>` +
    `</span>`;
}
function bindEnlargeButtons(root) {
  root.querySelectorAll("[data-enlarge-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const entry = detailsStore[btn.dataset.enlargeKey];
      if (entry) openDetailsModal(entry.title, entry.text);
    });
  });
}

// ---------- theme switcher and sidebar collapse ----------
// common/theme-boot.js (in <head>) already applied the saved theme and the
// collapsed state before first paint; this wires the <select> and the toggle.
HouseholdTheme.bindSelect(document.getElementById("theme-select"));
(function () {
  const btn = document.getElementById("sidebarCollapseBtn");
  if (!btn) return;

  function syncButton() {
    const collapsed = HouseholdTheme.sidebarCollapsed();
    btn.textContent = collapsed ? "›" : "‹";
    btn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
    btn.setAttribute("aria-label", btn.title);
  }

  btn.addEventListener("click", () => {
    HouseholdTheme.setSidebarCollapsed(!HouseholdTheme.sidebarCollapsed());
    syncButton();
  });

  syncButton();
})();

// ---------- tabs ----------
// The date bar only means anything on Food Log, which is the only tab
// still scoped by state.date (the day's log + "Day Summary" cards).
// Dashboard's own cards are independent of it now — the history chart is
// driven by state.historyGranularity (Daily/Weekly/Monthly) and the
// Monthly Breakdown card by its own state.summaryMonth/month-bar — so
// showing the day bar there was a leftover control that did nothing.
// Every other tab (Saved Foods, Weight, Goals, AI Assistant, Admin, and the
// whoami page) hides it entirely for the same reason.
const DATE_BAR_TABS = new Set(["foodlog", "symptoms"]);

function updateDateBarVisibility(tab) {
  $("#dateBar").hidden = !DATE_BAR_TABS.has(tab);
}

// Every page has an address: #/foodlog, #/symptoms, #/dashboard, #/weight, #/savedfoods, #/goals, #/ai, #/whoami,
// #/admin/<settings|users|storage> (#/users, #/storage and #/settings open the matching Admin tab). Navigation goes
// through BackNav (common/backnav.js), so the phone's Back retraces it and a reload keeps your place.
const TABS = ["foodlog", "symptoms", "dashboard", "weight", "savedfoods", "goals", "ai", "admin", "whoami"];
const TAB_TITLES = {
  foodlog: "Food Log", symptoms: "Symptoms", dashboard: "Dashboard", weight: "Weight", savedfoods: "Saved Foods",
  goals: "Goals", ai: "AI Assistant", admin: "Admin", whoami: "How the app sees you",
};

function activateTab(tab) {
  $all(".tab-btn[data-tab]").forEach(b => {
    const on = b.dataset.tab === tab;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  closeMore();
  closeMeMenu();
  const inMore = !!document.querySelector(`#navMore .tab-btn[data-tab="${tab}"]`);
  $("#moreBtn").classList.toggle("active", inMore);
  $all(".tab-panel").forEach(p => p.classList.remove("active"));
  $(`#tab-${tab}`).classList.add("active");
  $("#pageTitle").textContent = TAB_TITLES[tab] || "Calorie Tracker";
  document.title = `${TAB_TITLES[tab] || ""} · Calorie Tracker`.replace(/^ · /, "");
  updateDateBarVisibility(tab);
}

// Opens a tab's page and loads what it needs (no navigation: routeFromHash calls this for the address shown).
function openTab(tab) {
  if (tab === "whoami") { activateTab("whoami"); loadWhoami(); return; }
  activateTab(tab);
  if (tab === "ai") refreshAiStatus();
  if (tab === "weight") loadWeightHistory();
  if (tab === "symptoms") { loadSymptomDay(); loadPatterns(); }
  // The history canvas sizes itself from its container's clientWidth, which is 0 while its tab-panel is
  // display:none — so it has to re-measure once Dashboard is what's actually visible.
  if (tab === "dashboard") { loadHistory(); loadDashTop(); }
}

// Nav buttons and links: go to a tab's address (a new trail starts, see BackNav's isTab).
function showTab(tab) {
  if (tab === "admin") { navigateAdmin(adminSubtab); return; }
  if (LEGACY_ADMIN_TABS[tab]) { navigateAdmin(LEGACY_ADMIN_TABS[tab]); return; }
  BackNav.go(`#/${tab}`);
}

$all(".tab-btn[data-tab]").forEach(btn => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

// ⋯ More (phones): the pages used now and then, in a small panel above the bottom bar
function closeMore() {
  $(".side-nav").classList.remove("more-open");
  $("#moreBtn").setAttribute("aria-expanded", "false");
}
$("#moreBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const open = !$(".side-nav").classList.contains("more-open");
  closeMeMenu();
  $(".side-nav").classList.toggle("more-open", open);
  $("#moreBtn").setAttribute("aria-expanded", open ? "true" : "false");
  if (open) { const first = document.querySelector("#navMore .tab-btn:not([hidden])"); if (first) first.focus(); }
});
document.addEventListener("click", (e) => { if (!e.target.closest || !e.target.closest("#navMore")) closeMore(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $(".side-nav").classList.contains("more-open")) { closeMore(); $("#moreBtn").focus(); } });

// 👤 in the top bar (phones; on a computer the sidebar has the user chip and the theme): How the app sees you,
// Theme and Admin.
function closeMeMenu() {
  $("#meMenu").hidden = true;
  $("#whoamiBtn").setAttribute("aria-expanded", "false");
}
$("#whoamiBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const open = $("#meMenu").hidden;
  closeMore();
  $("#meMenu").hidden = !open;
  $("#whoamiBtn").setAttribute("aria-expanded", open ? "true" : "false");
  if (open) $("#meWhoamiBtn").focus();
});
$("#meMenu").addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("click", closeMeMenu);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#meMenu").hidden) { closeMeMenu(); $("#whoamiBtn").focus(); } });
HouseholdTheme.bindSelect($("#meThemeSelect"));
$("#meWhoamiBtn").addEventListener("click", () => { closeMeMenu(); openWhoami(); });
$("#meAdminBtn").addEventListener("click", () => { closeMeMenu(); showTab("admin"); });

// ---------- Admin (admins only: App settings | Users | Storage) ----------
// One sidebar item, hidden for non-admins (the server enforces every admin API on its own anyway).
const ADMIN_SUBTABS = ["settings", "users", "storage"];
const LEGACY_ADMIN_TABS = { users: "users", storage: "storage", settings: "settings" };
let adminSubtab = "settings";

// From elsewhere: a new trail (isTab). Between Admin's own tabs: replace, so Back leaves Admin in one step.
function navigateAdmin(sub, opts) {
  const hash = `#/admin/${sub}`;
  if (location.hash === hash) { routeFromHash(); return; }
  if (opts && opts.replace) BackNav.replace(hash); else BackNav.go(hash);
}

function clearRouteHash() {
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
}

// Opens the page the address names. Returns false for an address that isn't a page (e.g. "#/foodlog/<date>",
// which init() hands to openLink).
function routeFromHash() {
  const hash = location.hash;
  if (!hash || hash === "#" || hash === "#/") { openTab("foodlog"); return true; }
  const m = hash.match(/^#\/([a-z]+)(?:\/([a-z]+))?\/?$/);
  if (!m) return false;
  let top = m[1], sub = m[2];
  if (top !== "admin" && LEGACY_ADMIN_TABS[top]) {
    sub = LEGACY_ADMIN_TABS[top];
    top = "admin";
    history.replaceState(null, "", `#/admin/${sub}`);
  }
  if (top === "admin") { openAdmin(ADMIN_SUBTABS.includes(sub) ? sub : "settings"); return true; }
  if (!TABS.includes(top) || sub) return false;
  openTab(top);
  return true;
}

function openAdmin(sub) {
  adminSubtab = sub;
  activateTab("admin");
  const allowed = state.isAdmin;
  $("#adminDenied").hidden = allowed;
  $("#adminBody").hidden = !allowed;
  if (!allowed) return;
  $all(".admin-tab-btn").forEach(b => {
    const on = b.dataset.adminTab === sub;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  $all(".admin-panel").forEach(p => { p.hidden = p.id !== `admin-${sub}`; });
  if (sub === "settings") loadAppSettings();
  if (sub === "users") loadUsers();
}

$all(".admin-tab-btn").forEach(btn => {
  btn.addEventListener("click", () => navigateAdmin(btn.dataset.adminTab, { replace: true }));
});

window.addEventListener("hashchange", () => {
  if (!state.selfId) return;                 // not signed in (yet): init() routes once it knows who's here
  if (!routeFromHash()) {
    const link = linkRoute(location.hash.replace(/^#/, ""));   // "#/foodlog/<date>"
    if (link) { BackNav.replace(`#/${link.tab}`); if (link.date && link.date <= todayStr()) setDate(link.date); }
    else openTab("foodlog");
  }
});

// Matches whichever tab-panel loaded with "active" in index.html (Food Log
// today), so the date bar's initial visibility is correct without having
// to hardcode that default a second time here.
updateDateBarVisibility(document.querySelector(".tab-btn.active")?.dataset.tab);

// ---------- "How the app sees you" ----------
// Read-only diagnostic: exactly which user name / id Home Assistant sent, and
// whether they match the admin_users list. Reached from the user chip in the
// sidebar and the 👤 button in the top bar (phones have no sidebar chip).
function openWhoami() { BackNav.go("#/whoami"); }

async function loadWhoami() {
  const body = $("#whoamiBody");
  let w;
  UI.loading(body);
  try { w = await api("/api/whoami"); }
  catch (e) { body.replaceChildren(UI.errorBox(e, loadWhoami)); return; }
  // common/whoami.js: the same rows and wording as every other app
  body.replaceChildren(HouseholdWhoami.panel(w, {
    appName: "Calorie Tracker",
    classes: { copy: "btn-secondary kv-copy" },
  }));
}

$("#sidebarUser").addEventListener("click", openWhoami);

// ---------- current user (from Home Assistant, read-only) ----------
async function loadMe() {
  try {
    const me = await api("/api/me");
    state.selfId = me.id;
    state.actingUserId = me.id; // default: the user coming from HA
    state.isAdmin = me.is_admin;
    $("#sidebarUser").textContent = `Logged in as ${me.name}`;
    state.page = me.page || null;      // the sidebar page (deeplink.js)

    // Non-admins can only see/log their own data — no switcher and no
    // Admin page (App settings, Users, Storage are all admin-only
    // server-side too; the Admin nav item starts out hidden in index.html).
    if (!state.isAdmin) $("#userSelect").style.display = "none";
    $('.tab-btn[data-tab="admin"]').hidden = !state.isAdmin;
    $("#meAdminBtn").hidden = !state.isAdmin;
    // First run: nobody is an admin yet, so nobody could open App settings.
    // Everyone sees how to fix it (never auto-promoted).
    HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), me.noAdmin, me.username || me.id,
      { onOpen: openWhoami, linkId: "noAdminWhoamiLink" });
  } catch (e) {
    // A themed card in the page (the nav stays; every page needs a signed-in user, so none is shown).
    $("#sidebarUser").textContent = "Not signed in";
    $all(".tab-panel").forEach(p => p.classList.remove("active"));
    $("#dateBar").hidden = true;
    $("#pageTitle").textContent = "Not signed in";
    const box = $("#signinError");
    box.replaceChildren(
      UI.h("h3", null, "Can't identify a Home Assistant user"),
      UI.errorBox(e, () => location.reload()),
      UI.h("p", { class: "hint" }, "Open Calorie Tracker from its panel in the Home Assistant sidebar."));
    box.hidden = false;
    throw e;
  }
}

// ---------- user switcher + Users tab ----------
let allUsers = [];

async function loadUsers() {
  if (!state.isAdmin) return;
  try { allUsers = await api("/api/users"); }
  catch (e) { $("#usersList").replaceChildren(UI.errorBox(e, loadUsers)); return; }
  renderUserSelect();
  renderUsersTab();
}

function renderUserSelect() {
  const select = $("#userSelect");
  // Always include yourself even if someone disabled you; otherwise only
  // enabled users show up in the switcher.
  const visible = allUsers.filter(u => u.enabled || u.id === state.selfId);
  select.innerHTML = visible.map(u =>
    `<option value="${u.id}">${escapeHtml(u.name)}${u.id === state.selfId ? " (you)" : ""}</option>`
  ).join("");
  select.value = state.actingUserId;
}

$("#userSelect").addEventListener("change", async (e) => {
  state.actingUserId = e.target.value;
  await refreshAll();
});

// The shared people list (common/people.js): each person with their switcher on/off switch.
// Each card's ⋯ Data menu and the Import button above the list are common/person-data.js (export, import and
// delete one person's data; admin only, checked by the server too).
function personDataCfg() {
  return {
    people: allUsers.map(u => ({ id: u.id, name: u.name })),
    onDone: async () => {
      await loadUsers();
      // someone forgotten can't stay the person being viewed; an import may have added to the one shown
      if (!allUsers.some(u => u.id === state.actingUserId)) state.actingUserId = state.selfId;
      renderUserSelect();
      await refreshAll();
    },
  };
}

function renderUsersTab() {
  const dataCfg = personDataCfg();
  PeoplePage.render($("#usersList"), {
    people: allUsers,
    cardClass: "",
    empty: "No one else has opened this app yet.",
    intro: h("p", null, PeopleData.importButton(dataCfg)),
    person: (u) => ({
      actions: PeopleData.menu({ id: u.id, name: u.name }, dataCfg),
      badges: [u.id === state.selfId ? ["you"] : null],
      controls: h("label", { class: "pp-toggle", title: u.enabled ? "Enabled — visible in switcher" : "Disabled — hidden from switcher" },
        u.enabled ? "Enabled" : "Disabled",
        PeoplePage.accessSwitch(u.enabled, async (enabled, input) => {
          try {
            await api(`/api/users/${encodeURIComponent(u.id)}`, { method: "PUT", body: JSON.stringify({ enabled }) });
            await loadUsers();
          } catch (err) {
            alert("Could not update user: " + err.message);
            input.checked = !enabled;
          }
        }, { label: `${u.name} in the switcher` })),
    }),
  });
}

// ---------- date ----------
// "Today" comes from the backend (GET /api/today, Home Assistant's own time
// zone — see config.py), not the viewing device's clock: a browser and the
// app container can disagree about what time zone they're in, and it's
// the household's calendar day that matters for food logs, not whichever
// device happens to be looking at the app right now. loadToday() below
// keeps state.today current; everything else just reads it.
function todayStr() {
  return state.today;
}

// Refreshes state.today from the backend. If the visible date/month was
// showing "today" before the refresh, it's advanced along with it (and its
// data reloaded) so a tab left open past local midnight rolls over on its
// own instead of quietly showing yesterday. Returns true if state.today
// changed.
async function loadToday() {
  let today;
  try {
    ({ today } = await api("/api/today"));
  } catch (e) {
    return false; // transient failure — keep whatever we already have
  }
  if (today === state.today) return false;
  const wasOnToday = state.date === state.today;
  const monthWasCurrent = state.summaryMonth === state.today.slice(0, 7);
  state.today = today;
  if (wasOnToday) state.date = today;
  if (monthWasCurrent) state.summaryMonth = today.slice(0, 7);
  renderDateBar();
  if (wasOnToday) await refreshDateScoped();
  return true;
}

// Adds `deltaDays` to a "YYYY-MM-DD" string. Parses/re-serializes in UTC
// specifically so this is immune to local-timezone DST transitions — a
// local-time Date object can land on the same calendar day twice (or skip
// one) across a DST boundary, which would otherwise make "next day" from
// the day before a DST change occasionally not advance the date at all.
function shiftDate(dateStr, deltaDays) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + deltaDays);
  return d.toISOString().slice(0, 10);
}

// Human-friendly "Today / Yesterday / Tomorrow / <weekday>" plus a short
// "Mon 15" (or "Mon 15, 2025" once it's not this year) for the date bar.
function describeDate(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  const t = new Date(`${todayStr()}T00:00:00Z`);
  const diffDays = Math.round((d - t) / 86400000);
  let sub;
  if (diffDays === 0) sub = "Today";
  else if (diffDays === -1) sub = "Yesterday";
  else if (diffDays === 1) sub = "Tomorrow";
  else sub = d.toLocaleDateString(undefined, { weekday: "long", timeZone: "UTC" });
  const sameYear = d.getUTCFullYear() === t.getUTCFullYear();
  const main = d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
    timeZone: "UTC",
  });
  return { main, sub };
}

function renderDateBar() {
  const { main, sub } = describeDate(state.date);
  $("#dateLabelMain").textContent = main;
  $("#dateLabelSub").textContent = sub;
  $("#dateInput").value = state.date;
  $("#dateTodayBtn").disabled = state.date === todayStr();
  // Browsing is capped at today — there's nothing to show past it, and it
  // keeps "Today" a meaningful right-hand boundary rather than one stop
  // among many.
  $("#dateNextBtn").disabled = state.date >= todayStr();
}

async function setDate(newDate) {
  if (newDate === state.date) return;
  state.date = newDate;
  state.editingLogId = null;
  renderDateBar();
  UI.loading($("#foodLogList"));
  await refreshDateScoped();
}

renderDateBar();
$("#datePrevBtn").addEventListener("click", () => setDate(shiftDate(state.date, -1)));
$("#dateNextBtn").addEventListener("click", () => {
  if ($("#dateNextBtn").disabled) return;
  setDate(shiftDate(state.date, 1));
});
$("#dateTodayBtn").addEventListener("click", () => setDate(todayStr()));
$("#dateInput").addEventListener("change", (e) => {
  if (e.target.value) setDate(e.target.value);
});
// The 📅 buttons open the browser's picker for their hidden input.
function openPicker(el) {
  if (typeof el.showPicker === "function") {
    try {
      el.showPicker();
      return;
    } catch (err) {
      // falls through to focus() (showPicker can throw if not triggered by a
      // direct user gesture, or isn't supported)
    }
  }
  el.focus();
}
$("#datePickerBtn").addEventListener("click", () => openPicker($("#dateInput")));

// ---------- month summary (Dashboard's Monthly Breakdown card) ----------
// Independent of the day bar above: state.date picks one day for Food Log/
// the day summary, state.summaryMonth picks one calendar month for this
// card. They're deliberately not linked — browsing food-log days doesn't
// jump the month card around underneath you.
function shiftMonth(monthStr, deltaMonths) {
  const [y, m] = monthStr.split("-").map(Number);
  const idx = (m - 1) + deltaMonths;
  const year = y + Math.floor(idx / 12);
  const month = ((idx % 12) + 12) % 12 + 1;
  return `${year}-${String(month).padStart(2, "0")}`;
}

function renderMonthSummary(data) {
  $("#monthLabelMain").textContent = data.label;
  $("#monthInput").value = data.month;
  // Same "capped at the present" rule as the day bar's next-day button —
  // browsing into a month with no data yet isn't useful.
  $("#monthNextBtn").disabled = data.is_current || data.is_future;

  const tiles = [
    { kind: "calories", label: "Calories / day", value: fmt(data.avg_per_day.calories), goal: data.goal.calorie_goal },
    { kind: "protein", label: "Protein (g) / day", value: fmt(data.avg_per_day.protein), goal: data.goal.protein_goal },
    { kind: "carbs", label: "Carbs (g) / day", value: fmt(data.avg_per_day.carbs), goal: data.goal.carb_goal },
    { kind: "fat", label: "Fat (g) / day", value: fmt(data.avg_per_day.fat), goal: data.goal.fat_goal },
  ];
  const weightChange = data.weight.change_kg;
  const weightSub = weightChange == null
    ? ""
    : ` (${weightChange > 0 ? "+" : ""}${fmt(weightChange)} kg)`;

  // protein is a minimum: reaching it is good ("met"); only calories, carbs and fat are flagged "over"
  $("#monthSummaryGrid").innerHTML = tiles.map(t => {
    const st = data.days_elapsed > 0 ? goalState(t.kind, t.value, t.goal) : "";
    return `<div class="stat-card ${st}">
      <div class="value">${t.value}</div>
      <div class="label">${t.label} / ${fmt(t.goal)}</div>
    </div>`;
  }).join("") + `<div class="stat-card">
      <div class="value">${data.weight.avg_kg != null ? fmt(data.weight.avg_kg) + " kg" : "—"}</div>
      <div class="label">Avg Weight${weightSub}</div>
    </div>`;

  $("#monthSummaryHint").textContent = data.days_elapsed > 0
    ? `Averages over ${data.days_elapsed} of ${data.days_in_month} day${data.days_in_month === 1 ? "" : "s"} so far this month.`
    : "No data logged yet for this month.";
}

async function loadMonthSummary() {
  let data;
  try { data = await api(withUser(`/api/summary/month?month=${state.summaryMonth}`)); }
  catch (e) { $("#monthSummaryGrid").replaceChildren(UI.errorBox(e, loadMonthSummary)); return; }
  renderMonthSummary(data);
}

async function setSummaryMonth(newMonth) {
  if (newMonth === state.summaryMonth) return;
  state.summaryMonth = newMonth;
  await loadMonthSummary();
}

$("#monthPrevBtn").addEventListener("click", () => setSummaryMonth(shiftMonth(state.summaryMonth, -1)));
$("#monthNextBtn").addEventListener("click", () => {
  if ($("#monthNextBtn").disabled) return;
  setSummaryMonth(shiftMonth(state.summaryMonth, 1));
});
$("#monthInput").addEventListener("change", (e) => {
  if (e.target.value) setSummaryMonth(e.target.value);
});
$("#monthPickerBtn").addEventListener("click", () => openPicker($("#monthInput")));

// ---------- Day Summary (Food Log, under the date bar) ----------
// Protein is a minimum (reaching it is good); calories, carbs and fat are limits (going over is flagged).
const GOAL_KIND = { calories: "limit", protein: "min", carbs: "limit", fat: "limit" };
function goalState(kind, value, goal) {
  if (!(goal > 0)) return "";
  if (GOAL_KIND[kind] === "min") return value >= goal ? "met" : "";
  return value > goal ? "over" : "";
}

function dayBar(kind, label, value, goal, unit, color) {
  const st = goalState(kind, value, goal);
  const pct = Math.min(100, (value / (goal || 1)) * 100);
  const left = goal > 0 ? (GOAL_KIND[kind] === "min"
    ? (value >= goal ? "goal reached" : `${fmt(goal - value)} ${unit} to go`)
    : (value > goal ? `${fmt(value - goal)} ${unit} over` : `${fmt(goal - value)} ${unit} left`)) : "";
  return `<div class="ds-row ${st}">
      <div class="ds-label"><span class="ds-name">${label}</span><span class="ds-val">${fmt(value)} / ${fmt(goal)} ${unit}</span><span class="ds-left">${left}</span></div>
      <div class="macro-bar-track"><div class="macro-bar-fill" style="width:${pct}%; background:${st === "over" ? "var(--danger)" : color}"></div></div>
    </div>`;
}

async function loadSummary() {
  const box = $("#daySummary");
  let summary;
  try {
    summary = await api(withUser(`/api/summary?date=${state.date}`));
  } catch (e) {
    box.replaceChildren(UI.errorBox(e, loadSummary));
    return;
  }
  const t = summary.totals, g = summary.goal;
  const n = summary.no_calories || 0;
  box.innerHTML = `
    <div class="ds-head"><h3>Day Summary</h3><a href="#/goals" class="goals-link">Edit goals</a></div>
    ${dayBar("calories", "Calories", t.calories, g.calorie_goal, "kcal", "var(--accent)")}
    <div class="ds-macros">
      ${dayBar("protein", "Protein", t.protein, g.protein_goal, "g", "var(--protein)")}
      ${dayBar("carbs", "Carbs", t.carbs, g.carb_goal, "g", "var(--carbs)")}
      ${dayBar("fat", "Fat", t.fat, g.fat_goal, "g", "var(--fat)")}
    </div>
    ${n ? `<p class="hint ds-note">${n} ${n === 1 ? "food" : "foods"} logged without calories ${n === 1 ? "isn't" : "aren't"} counted in these totals.</p>` : ""}`;
}

// ---------- Dashboard: top row (today, 7-day average, latest weight) ----------
async function loadDashTop() {
  const box = $("#dashTop");
  try {
    const [today, hist] = await Promise.all([
      api(withUser(`/api/summary?date=${todayStr()}`)),
      api(withUser(`/api/history?granularity=daily`)),
    ]);
    const last7 = hist.points.slice(-7);
    const avg7 = last7.length ? last7.reduce((a, p) => a + p.calories, 0) / last7.length : 0;
    const kcal = today.totals.calories, goal = today.goal.calorie_goal;
    const kpi = (label, value, cls, title) => `<div class="kpi"${title ? ` title="${escapeHtml(title)}"` : ""}>
        <div class="kpi-label">${label}</div><div class="kpi-value ${cls || ""}">${value}</div></div>`;
    box.innerHTML =
      kpi("Today", `${Math.round(kcal).toLocaleString()} / ${Math.round(goal).toLocaleString()} kcal`, kcal > goal ? "neg" : "") +
      kpi("7-day average", `${Math.round(avg7).toLocaleString()} kcal`, avg7 > goal ? "neg" : "",
        "Average calories per day over the last 7 days (days with nothing logged count as 0)") +
      kpi("Latest weight", today.latest_weight_kg ? `${fmt(today.latest_weight_kg)} kg` : "—");
  } catch (e) {
    box.replaceChildren(UI.errorBox(e, loadDashTop));
  }
}

// ---------- chart helpers (shared by the history + weight canvas charts) ----------
// Both charts are hand-rolled with plain canvas (no charting library), and
// until now neither had axis reference numbers nor any hover/keyboard
// interaction — a bar's only readable value was to guess by eye against
// the unlabeled y-axis. This section adds both, shared so the two charts
// stay consistent rather than growing two slightly-different versions.

// Reads a theme color straight off :root so the charts render correctly
// under every entry in the theme switcher (see the [data-theme=...] blocks
// at the top of style.css), including the light "daylight" theme — the
// previous hardcoded hex values (e.g. "#93a0b8" for axis text) were tuned
// for the dark themes only and would have been low-contrast in daylight.
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// A "nice" step size (1/2/5 × a power of ten) so axis ticks land on round
// numbers like 0/500/1000/1500 instead of whatever the data happens to be.
function niceTickStep(range, targetCount) {
  if (!(range > 0)) return 1;
  const rough = range / targetCount;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const step = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
  return step * mag;
}

// Builds clean tick values spanning at least [minVal, maxVal].
// Horizontal gridlines with their values on the left (both charts).
function drawGrid(ctx, ticks, yFor, label, left, right, gridColor, textColor) {
  ctx.font = "10px sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 1;
  ticks.forEach(t => {
    const y = yFor(t);
    ctx.strokeStyle = gridColor;
    ctx.beginPath();
    ctx.moveTo(left, Math.round(y) + 0.5);
    ctx.lineTo(right, Math.round(y) + 0.5);
    ctx.stroke();
    ctx.fillStyle = textColor;
    ctx.fillText(label(t), left - 8, y);
  });
}

function niceTicks(minVal, maxVal, targetCount = 4) {
  const step = niceTickStep(maxVal - minVal, targetCount);
  const niceMin = Math.floor(minVal / step) * step;
  const niceMax = Math.ceil(maxVal / step) * step;
  const ticks = [];
  for (let v = niceMin; v <= niceMax + step / 1000; v += step) {
    ticks.push(Math.round(v * 1000) / 1000);
  }
  return ticks;
}

// Wires pointer + keyboard interaction for one of these charts. `getRegions`
// is called fresh on every event (not captured once) so it always reflects
// whatever the chart most recently drew — a granularity switch or a new
// date range just works with no re-attaching needed. Each region describes
// one bar/point: { xStart, xEnd, tipX, tipY, index, valueText, subText },
// in canvas pixel space (xStart/xEnd is the hit band along the x-axis).
function attachChartHover(canvas, tooltip, getRegions, onHighlight) {
  function hide() {
    if (tooltip.hidden) return;
    tooltip.hidden = true;
    onHighlight(-1);
  }
  function show(region) {
    onHighlight(region.index);
    tooltip.textContent = "";
    const value = document.createElement("div");
    value.className = "chart-tooltip-value";
    value.textContent = region.valueText;
    const sub = document.createElement("div");
    sub.className = "chart-tooltip-label";
    sub.textContent = region.subText;
    tooltip.appendChild(value);
    tooltip.appendChild(sub);
    tooltip.hidden = false;
    const scaleX = canvas.clientWidth / canvas.width;
    const scaleY = canvas.clientHeight / canvas.height;
    const cssX = region.tipX * scaleX;
    const cssY = region.tipY * scaleY;
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    const margin = 4;
    // Measured after the content is set and the element is visible, then
    // clamped to the chart's own box — centering the tooltip over the bar/
    // point by default, but never letting it run off the left/right edge
    // (a real bug on mobile: the last bar's tooltip used to get cut off by
    // the viewport) or above the top of the chart.
    const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
    let left = cssX - tw / 2;
    left = Math.max(margin, Math.min(left, cw - tw - margin));
    let top = cssY - th - 8;
    if (top < margin) top = Math.min(cssY + 8, ch - th - margin);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }
  function regionAtClientX(clientX) {
    const rect = canvas.getBoundingClientRect();
    const x = (clientX - rect.left) * (canvas.width / rect.width);
    return getRegions().find(r => x >= r.xStart && x < r.xEnd) || null;
  }
  canvas.addEventListener("pointermove", (e) => {
    const region = regionAtClientX(e.clientX);
    if (region) show(region); else hide();
  });
  canvas.addEventListener("pointerleave", hide);
  // Keyboard equivalent of hover (interaction.md: "same details on keyboard
  // focus as on hover") — Tab to the chart, then arrow through the values.
  canvas.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const regions = getRegions();
    if (!regions.length) return;
    let idx = regions.findIndex(r => r.index === canvas._focusIndex);
    if (idx === -1) idx = e.key === "ArrowRight" ? 0 : regions.length - 1;
    else idx = e.key === "ArrowRight" ? Math.min(regions.length - 1, idx + 1) : Math.max(0, idx - 1);
    e.preventDefault();
    canvas._focusIndex = regions[idx].index;
    show(regions[idx]);
  });
  canvas.addEventListener("blur", () => { canvas._focusIndex = null; hide(); });
}

// Traces a rounded-top ("4px data-end") bar path whose base sits on
// `baseY`, without filling or stroking it — callers do that afterward, so
// the same path can be filled (the bar itself) and/or stroked (the hover
// outline) independently.
function traceBarPath(ctx, x, y, barW, baseY, radius) {
  const r = Math.max(0, Math.min(radius, barW / 2, baseY - y));
  ctx.beginPath();
  if (r <= 0) {
    ctx.rect(x, y, barW, baseY - y);
  } else {
    ctx.moveTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
    ctx.lineTo(x + barW - r, y);
    ctx.arcTo(x + barW, y, x + barW, y + r, r);
    ctx.lineTo(x + barW, baseY);
    ctx.lineTo(x, baseY);
    ctx.closePath();
  }
}

// ---------- calorie history chart ----------
$all(".cal-hist-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    $all(".cal-hist-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.historyGranularity = btn.dataset.granularity;
    await loadHistory();
  });
});

let lastHistoryPoints = [];
let lastHistoryGoal = 0;
let hoveredHistoryIndex = -1;
let historyRegions = [];

async function loadHistory() {
  const data = await api(withUser(`/api/history?granularity=${state.historyGranularity}`));
  lastHistoryPoints = data.points;
  lastHistoryGoal = data.goal;
  drawHistoryChart();
  const hintMap = {
    daily: "Calories per day vs your goal (dashed line). Last 14 days.",
    weekly: "Average calories per day, by week (unlogged days count as 0). Last 8 weeks.",
    monthly: "Average calories per day, by month (unlogged days count as 0). Last 6 months.",
  };
  $("#historyHint").textContent = hintMap[state.historyGranularity] || "";
}

function drawHistoryChart() {
  const points = lastHistoryPoints, goal = lastHistoryGoal;
  const canvas = $("#historyChart");
  const ctx = canvas.getContext("2d");
  canvas.width = canvas.clientWidth;
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  historyRegions = [];
  if (!points.length) return;

  const textDim = cssVar("--text-dim", "#93a0b8");
  const gridColor = cssVar("--border", "#2b3549");
  const accent = cssVar("--accent", "#5ee6a8");
  const goalColor = cssVar("--carbs", "#f2c94c");
  const overColor = cssVar("--danger", "#ef7b7b");
  const highlightColor = cssVar("--text", "#e7ecf5");

  const dataMax = Math.max(goal, ...points.map(p => p.calories), 1);
  const ticks = niceTicks(0, dataMax, 4);
  const maxVal = ticks[ticks.length - 1] || 1;

  const padL = 46, padR = 10, padT = 12, padB = 26;
  const chartW = w - padL - padR;
  const chartH = h - padT - padB;
  const baseY = padT + chartH;
  const barGap = 6;
  const barW = Math.max(2, (chartW / points.length) - barGap);

  // Gridlines + y-axis reference numbers.
  drawGrid(ctx, ticks, t => padT + chartH - (t / maxVal) * chartH, t => Math.round(t).toLocaleString(), padL, w - padR, gridColor, textDim);

  // Goal line (dashed), drawn on top of the gridlines.
  const goalY = padT + chartH - (goal / maxVal) * chartH;
  ctx.strokeStyle = goalColor;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(padL, goalY);
  ctx.lineTo(w - padR, goalY);
  ctx.stroke();
  ctx.setLineDash([]);

  points.forEach((p, i) => {
    const x = padL + i * (barW + barGap);
    const barH = Math.max(0, (p.calories / maxVal) * chartH);
    const y = baseY - barH;
    const over = p.calories > goal;
    if (barH > 0) {
      traceBarPath(ctx, x, y, barW, baseY, 4);
      ctx.fillStyle = over ? overColor : accent;
      ctx.fill();
      if (i === hoveredHistoryIndex) {
        traceBarPath(ctx, x + 0.75, y + 0.75, barW - 1.5, baseY, 4);
        ctx.strokeStyle = highlightColor;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    }

    ctx.fillStyle = textDim;
    ctx.font = "9px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    if (points.length <= 10 || i % 2 === 0) {
      ctx.fillText(p.label, x + barW / 2, h - 8);
    }

    const diff = Math.round(p.calories - goal);
    historyRegions.push({
      xStart: x - barGap / 2,
      xEnd: x + barW + barGap / 2,
      tipX: x + barW / 2,
      tipY: y,
      index: i,
      valueText: `${Math.round(p.calories).toLocaleString()} cal`,
      subText: `${p.label} · ${diff > 0 ? `${diff.toLocaleString()} over goal` : diff < 0 ? `${(-diff).toLocaleString()} under goal` : "at goal"}`,
    });
  });
}

attachChartHover(
  $("#historyChart"),
  $("#historyTooltip"),
  () => historyRegions,
  (index) => {
    if (index === hoveredHistoryIndex) return;
    hoveredHistoryIndex = index;
    drawHistoryChart();
  }
);

// ---------- row ⋯ menus ----------
// One small menu at a time, next to the ⋯ button that opened it: items [{label, onclick, danger}].
let rowMenuAnchor = null;
let rowMenuOpenedAt = 0;
function closeRowMenu() {
  const menu = $("#rowMenu");
  if (menu.hidden) return;
  menu.hidden = true;
  menu.replaceChildren();
  if (rowMenuAnchor) { rowMenuAnchor.setAttribute("aria-expanded", "false"); rowMenuAnchor = null; }
}
function openRowMenu(anchor, items) {
  const same = rowMenuAnchor === anchor;
  closeRowMenu();
  if (same) return;
  const menu = $("#rowMenu");
  menu.replaceChildren(...items.map(it => {
    const b = UI.h("button", { type: "button", role: "menuitem", class: "row-menu-item" + (it.danger ? " danger" : "") }, it.label);
    b.addEventListener("click", (e) => { e.stopPropagation(); closeRowMenu(); it.onclick(); });
    return b;
  }));
  menu.hidden = false;
  rowMenuOpenedAt = Date.now();
  rowMenuAnchor = anchor;
  anchor.setAttribute("aria-expanded", "true");
  // fixed, under the button (above it near the bottom of the screen), kept on screen
  const r = anchor.getBoundingClientRect();
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  let left = Math.min(r.right - mw, window.innerWidth - mw - 8);
  left = Math.max(8, left);
  let top = r.bottom + 4;
  if (top + mh > window.innerHeight - 70) top = Math.max(8, r.top - mh - 4);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  const first = menu.querySelector("button");
  if (first) first.focus();
}
$("#rowMenu").addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("click", closeRowMenu);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#rowMenu").hidden) { const a = rowMenuAnchor; closeRowMenu(); if (a) a.focus(); }
});
// scrolling the page moves the button away from the menu (but not the scroll that brought the button into view)
window.addEventListener("scroll", () => { if (Date.now() - rowMenuOpenedAt > 400) closeRowMenu(); }, { passive: true });

// ---------- food log ----------
// Phones (below 760px): cards grouped by meal with a subtotal; wider: a table. Tapping an entry (or ⋯ → Edit / ✎)
// edits it in place (PUT /api/logs/{id}).
const PHONE = window.matchMedia("(max-width: 760px)");
const MEALS = ["breakfast", "lunch", "dinner", "snack"];
const MEAL_LABEL = { breakfast: "Breakfast", lunch: "Lunch", dinner: "Dinner", snack: "Snack" };
state.logs = [];
state.editingLogId = null;

async function loadFoodLog() {
  const list = $("#foodLogList");
  try {
    state.logs = await api(withUser(`/api/logs?date=${state.date}`));
  } catch (e) {
    list.replaceChildren(UI.errorBox(e, loadFoodLog));
    return;
  }
  if (!state.logs.some(l => l.id === state.editingLogId)) state.editingLogId = null;
  renderFoodLog();
}

function macroText(l) {
  return `${l.calories === null ? "— kcal" : `${fmt(l.calories)} kcal`} · P ${fmt(l.protein)} / C ${fmt(l.carbs)} / F ${fmt(l.fat)} g`;
}

function logEditForm(l) {
  const opt = (m) => `<option value="${m}"${m === l.meal_type ? " selected" : ""}>${MEAL_LABEL[m]}</option>`;
  const num = (id, v, label, max, step) => `<label>${label}<input class="le-${id}" type="number" step="${step}" min="0" max="${max}" value="${v === null ? "" : v}"></label>`;
  return `<div class="log-edit" data-id="${l.id}">
      <div class="form-row">
        <select class="le-meal" aria-label="Meal">${MEALS.map(opt).join("")}</select>
        <input class="le-time" type="time" value="${escapeHtml(l.eaten_at || "")}" aria-label="Time eaten">
        <label class="le-serv-label">Servings<input class="le-servings" type="number" step="0.1" min="0.1" max="1000" value="${l.servings}"></label>
      </div>
      <div class="form-row"><textarea class="le-name food-name" rows="2" maxlength="1000" aria-label="What you ate">${escapeHtml(l.food_name)}</textarea></div>
      <div class="form-row">
        ${num("calories", l.calories, "Calories (all servings)", 50000, 1)}
        ${num("protein", l.protein, "Protein (g)", 5000, 0.1)}
        ${num("carbs", l.carbs, "Carbs (g)", 5000, 0.1)}
        ${num("fat", l.fat, "Fat (g)", 5000, 0.1)}
      </div>
      <div class="form-row form-actions">
        <button type="button" class="btn-secondary le-cancel">Cancel</button>
        <button type="button" class="btn-primary le-save">Save</button>
      </div>
    </div>`;
}

function renderFoodLog() {
  const list = $("#foodLogList");
  const logs = state.logs;
  closeRowMenu();
  if (logs.length === 0) {
    list.replaceChildren(UI.empty("Nothing logged for this day", "Add what you ate above.",
      { label: "Add food", class: "btn-secondary", onclick: () => $("#foodName").focus() }));
    return;
  }
  if (PHONE.matches) {
    list.innerHTML = MEALS.filter(m => logs.some(l => l.meal_type === m)).map(m => {
      const items = logs.filter(l => l.meal_type === m);
      const kcal = items.reduce((a, l) => a + (l.calories || 0), 0);
      return `<div class="ui-group-head">${MEAL_LABEL[m]}<span class="ui-group-total">${fmt(kcal)} kcal</span></div>` +
        items.map(l => l.id === state.editingLogId ? logEditForm(l) : `
        <div class="log-card" data-id="${l.id}" tabindex="0" role="button" aria-label="Edit ${escapeHtml(l.food_name)}">
          <span class="tag meal-chip">${escapeHtml(l.meal_type)}</span>
          <div class="log-card-main">
            <span class="fname">${escapeHtml(l.food_name)}</span>
            <span class="meta">${macroText(l)}${l.servings !== 1 ? ` · ${fmt(l.servings)}×` : ""}${l.eaten_at ? ` · ${escapeHtml(l.eaten_at)}` : ""}</span>
            ${tagChips(l.tags)}
          </div>
          <button type="button" class="kebab tap44 log-menu-btn" data-id="${l.id}" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${escapeHtml(l.food_name)}">⋯</button>
        </div>`).join("");
    }).join("");
  } else {
    list.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Time</th><th>Meal</th><th>Food</th><th>Servings</th><th>Calories</th><th>P</th><th>C</th><th>F</th><th></th>
        </tr>
      </thead>
      <tbody>
        ${logs.map(l => l.id === state.editingLogId ? `<tr class="edit-row"><td colspan="9">${logEditForm(l)}</td></tr>` : `
          <tr>
            <td class="col-time">${escapeHtml(l.eaten_at || "")}</td>
            <td><span class="tag">${escapeHtml(l.meal_type)}</span></td>
            <td class="col-name"><span class="fname" title="${escapeHtml(l.food_name)}">${escapeHtml(l.food_name)}</span>${tagChips(l.tags)}</td>
            <td>${fmt(l.servings)}×</td>
            <td>${kcalText(l.calories)}</td>
            <td>${fmt(l.protein)}</td>
            <td>${fmt(l.carbs)}</td>
            <td>${fmt(l.fat)}</td>
            <td class="col-actions">
              <div class="item-actions">
                <button data-id="${l.id}" class="edit-log-btn" title="Edit" aria-label="Edit ${escapeHtml(l.food_name)}">✎</button>
                <button data-id="${l.id}" class="del-log-btn" title="Delete" aria-label="Delete ${escapeHtml(l.food_name)}">✕</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
    list.querySelectorAll(".fname").forEach(el => el.addEventListener("click", () => el.classList.toggle("open")));
  }
  const entry = (id) => logs.find(l => String(l.id) === String(id));
  list.querySelectorAll(".edit-log-btn").forEach(btn => btn.addEventListener("click", () => startLogEdit(btn.dataset.id)));
  $all(".del-log-btn").forEach(btn => btn.addEventListener("click", withErrors(async () => {
    await deleteLogEntry(entry(btn.dataset.id));
  })));
  list.querySelectorAll(".log-menu-btn").forEach(btn => btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const l = entry(btn.dataset.id);
    openRowMenu(btn, [
      { label: "✎ Edit", onclick: () => startLogEdit(l.id) },
      { label: "✕ Delete", danger: true, onclick: withErrors(() => deleteLogEntry(l)) },
    ]);
  }));
  list.querySelectorAll(".log-card").forEach(card => {
    card.addEventListener("click", () => startLogEdit(card.dataset.id));
    card.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === card) { e.preventDefault(); startLogEdit(card.dataset.id); } });
  });
  list.querySelectorAll(".log-edit").forEach(bindLogEdit);
}
PHONE.addEventListener("change", () => { renderFoodLog(); if (state.savedFoods) renderSavedFoods(); });

async function deleteLogEntry(entry) {
  if (!entry) return;
  if (!confirm(`Delete "${entry.food_name}" from the log?`)) return;
  await api(withUser(`/api/logs/${entry.id}`), { method: "DELETE" });
  if (state.editingLogId === entry.id) state.editingLogId = null;
  await Promise.all([loadFoodLog(), loadSummary(), loadHistory(), loadSymptomDay()]);
}

function startLogEdit(id) {
  state.editingLogId = Number(id);
  renderFoodLog();
  const name = document.querySelector(`.log-edit[data-id="${id}"] .le-name`);
  if (name) { name.focus(); name.scrollIntoView({ block: "center", behavior: "smooth" }); }
}

function bindLogEdit(box) {
  const l = state.logs.find(x => String(x.id) === box.dataset.id);
  if (!l) return;
  const q = (c) => box.querySelector(c);
  // Changing the servings scales the totals with it (they're for all the servings).
  let lastServings = Number(l.servings) || 1;
  q(".le-servings").addEventListener("change", () => {
    const n = parseFloat(q(".le-servings").value);
    if (!(n > 0)) return;
    const k = n / lastServings;
    for (const f of ["calories", "protein", "carbs", "fat"]) {
      const inp = q(`.le-${f}`);
      if (inp.value !== "") inp.value = Math.round(parseFloat(inp.value) * k * 10) / 10;
    }
    lastServings = n;
  });
  q(".le-cancel").addEventListener("click", () => { state.editingLogId = null; renderFoodLog(); });
  q(".le-save").addEventListener("click", withErrors(async () => {
    const name = q(".le-name").value.trim();
    if (!name) return alert("Type what you ate.");
    const kcal = parseFloat(q(".le-calories").value);
    const servings = parseFloat(q(".le-servings").value);
    await api(withUser(`/api/logs/${l.id}`), {
      method: "PUT",
      body: JSON.stringify({
        date: l.date,
        meal_type: q(".le-meal").value,
        food_name: name,
        servings: servings > 0 ? servings : 1,
        calories: isNaN(kcal) ? null : kcal,
        protein: parseFloat(q(".le-protein").value) || 0,
        carbs: parseFloat(q(".le-carbs").value) || 0,
        fat: parseFloat(q(".le-fat").value) || 0,
        saved_food_id: l.saved_food_id ?? null,
        eaten_at: q(".le-time").value || null,
        tags: l.tags || [],
      }),
    });
    state.editingLogId = null;
    await Promise.all([loadFoodLog(), loadSummary(), loadHistory(), loadSymptomDay()]);
  }));
}

function clearFoodForm() {
  $("#foodName").value = "";
  $("#foodServings").value = "1";
  $("#foodCalories").value = "";
  $("#foodProtein").value = "";
  $("#foodCarbs").value = "";
  $("#foodFat").value = "";
  $("#foodTime").value = "";
  foodTags.set([]);
  state.pickedFood = null;
  $("#foodNutrition").open = false;
  hideFoodSuggest();
  $("#aiFillStatus").textContent = "";
}

// The form's calories and macros are for ONE serving (a saved food's own numbers, an AI estimate); a food-log
// entry keeps the totals for all the servings eaten — what the day adds up, and what the Household Assistant
// divides back to one serving.
function scaleByServings(perServing, servings) {
  const n = servings > 0 ? servings : 1;
  const out = {};
  for (const k of ["calories", "protein", "carbs", "fat"]) out[k] = Math.round((Number(perServing[k]) || 0) * n * 10) / 10;
  if (perServing.calories === null || perServing.calories === undefined) out.calories = null;   // left out: stays out
  return out;
}

$("#addFoodBtn").addEventListener("click", withErrors(async () => {
  const name = $("#foodName").value.trim();
  const kcalIn = parseFloat($("#foodCalories").value);
  const calories = isNaN(kcalIn) ? null : kcalIn;          // optional
  if (!name) return alert("Type what you ate.");
  // a saved food picked from the suggestions counts only while its name is still what's in the box
  const savedFoodId = state.pickedFood && state.pickedFood.name === name ? state.pickedFood.id : null;
  const servings = parseFloat($("#foodServings").value) > 0 ? parseFloat($("#foodServings").value) : 1;
  const totals = scaleByServings({
    calories,
    protein: parseFloat($("#foodProtein").value) || 0,
    carbs: parseFloat($("#foodCarbs").value) || 0,
    fat: parseFloat($("#foodFat").value) || 0,
  }, servings);
  await api(withUser(`/api/logs`), {
    method: "POST",
    body: JSON.stringify({
      date: state.date,
      meal_type: $("#mealType").value,
      food_name: name,
      servings,
      ...totals,
      saved_food_id: savedFoodId,
      eaten_at: $("#foodTime").value || null,
      tags: foodTags.get(),
    }),
  });
  clearFoodForm();
  await Promise.all([loadFoodLog(), loadSummary(), loadHistory(), loadSymptomDay(), loadChoices(), loadSavedFoodPicker()]);
}));

// ---------- Add Food: type-ahead over saved foods, and recent / frequent chips ----------
state.pickedFood = null;
let suggestIndex = -1;

function savedFoodLabel(f) {
  return `${f.calories === null ? "" : fmt(f.calories) + " kcal / "}${fmt(f.serving_size)} ${f.serving_unit}`;
}

function pickSavedFood(food) {
  state.pickedFood = food;
  $("#foodName").value = food.name;
  $("#foodServings").value = "1";
  $("#foodCalories").value = food.calories === null ? "" : food.calories;
  $("#foodProtein").value = food.protein;
  $("#foodCarbs").value = food.carbs;
  $("#foodFat").value = food.fat;
  foodTags.set(food.tags || []);
  $("#foodNutrition").open = true;        // show what it filled in
  hideFoodSuggest();
}

function hideFoodSuggest() {
  $("#foodSuggest").hidden = true;
  $("#foodSuggest").replaceChildren();
  $("#foodName").removeAttribute("aria-activedescendant");
  suggestIndex = -1;
}

function drawFoodSuggest() {
  const q = $("#foodName").value.trim().toLowerCase();
  const box = $("#foodSuggest");
  if (!q || (state.pickedFood && state.pickedFood.name.toLowerCase() === q)) { hideFoodSuggest(); return; }
  const foods = (state.savedFoods || []).filter(f => f.name.toLowerCase().includes(q))
    .sort((a, b) => (b.name.toLowerCase().startsWith(q) - a.name.toLowerCase().startsWith(q)) || (b.uses || 0) - (a.uses || 0))
    .slice(0, 6);
  if (!foods.length) { hideFoodSuggest(); return; }
  suggestIndex = -1;
  box.replaceChildren(...foods.map((f, i) => {
    const opt = UI.h("div", { class: "suggest-item", role: "option", id: `foodSuggest-${i}`, "aria-selected": "false" },
      UI.h("span", { class: "suggest-name" }, f.name), UI.h("span", { class: "suggest-meta" }, savedFoodLabel(f)));
    opt.addEventListener("mousedown", (e) => e.preventDefault());     // keep the focus in the box
    opt.addEventListener("click", () => pickSavedFood(f));
    opt._food = f;
    return opt;
  }));
  box.hidden = false;
}

$("#foodName").addEventListener("input", () => {
  if (state.pickedFood && $("#foodName").value.trim() !== state.pickedFood.name) state.pickedFood = null;
  drawFoodSuggest();
});
$("#foodName").addEventListener("blur", () => setTimeout(hideFoodSuggest, 150));
$("#foodName").addEventListener("keydown", (e) => {
  const box = $("#foodSuggest");
  if (box.hidden) return;
  const items = [...box.children];
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    suggestIndex = (suggestIndex + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((it, i) => it.setAttribute("aria-selected", i === suggestIndex ? "true" : "false"));
    $("#foodName").setAttribute("aria-activedescendant", items[suggestIndex].id);
  } else if (e.key === "Enter" && suggestIndex >= 0) {
    e.preventDefault();
    pickSavedFood(items[suggestIndex]._food);
  } else if (e.key === "Escape") {
    e.stopPropagation();
    hideFoodSuggest();
  }
});

// Up to six saved foods: the most used first, then the most recently used.
function drawQuickFoods() {
  const foods = (state.savedFoods || []).filter(f => f.uses > 0)
    .sort((a, b) => (b.uses - a.uses) || String(b.last_used || "").localeCompare(String(a.last_used || "")))
    .slice(0, 6);
  $("#quickFoods").hidden = !foods.length;
  $("#quickFoodChips").replaceChildren(...foods.map(f => {
    const b = UI.h("button", { type: "button", class: "chip quick-chip", title: `Log one serving of ${f.name} (${savedFoodLabel(f)})` },
      "+ " + (f.name.length > 28 ? f.name.slice(0, 27) + "…" : f.name));
    b.addEventListener("click", withErrors(async () => {
      b.disabled = true;
      try {
        await api(withUser(`/api/logs`), {
          method: "POST",
          body: JSON.stringify({
            date: state.date, meal_type: $("#mealType").value, food_name: f.name, servings: 1,
            calories: f.calories, protein: f.protein, carbs: f.carbs, fat: f.fat,
            saved_food_id: f.id, tags: f.tags || [],
          }),
        });
        $("#aiFillStatus").textContent = `Added "${f.name}" (${MEAL_LABEL[$("#mealType").value].toLowerCase()}).`;
        await Promise.all([loadFoodLog(), loadSummary(), loadHistory(), loadSymptomDay(), loadSavedFoodPicker()]);
      } finally {
        b.disabled = false;
      }
    }));
    return b;
  }));
}

// After logging: the use counts changed (quick chips, Saved Foods' "Most used").
async function loadSavedFoodPicker() {
  await loadSavedFoodsList();
}

// ---------- saved foods (repeated foods) ----------
// The list first (search, sort A–Z / most used); "+ New food" or Edit opens the form above it. Phones: cards
// (name, kcal per serving, "+ log", ⋯ with Edit / Details / Delete); wider: a table.
state.editingSavedFoodId = null;

async function loadSavedFoodsList() {
  const list = $("#savedFoodsList");
  try {
    state.savedFoods = await api(withUser(`/api/saved-foods`));
  } catch (e) {
    list.replaceChildren(UI.errorBox(e, loadSavedFoodsList));
    return;
  }
  renderSavedFoods();
  drawQuickFoods();
}

$("#sfSearch").addEventListener("input", UI.debounce(() => renderSavedFoods(), 120));
$("#sfSort").addEventListener("change", () => { UI.lsSet("calorie.sfSort", $("#sfSort").value); renderSavedFoods(); });
if (UI.lsGet("calorie.sfSort") === "used") $("#sfSort").value = "used";
$("#sfNewBtn").addEventListener("click", () => { cancelSavedFoodEdit(); openSavedFoodForm(); });

function renderSavedFoods() {
  const all = state.savedFoods || [];
  const list = $("#savedFoodsList");
  closeRowMenu();
  if (all.length === 0) {
    list.replaceChildren(UI.empty("No saved foods yet",
      "Save foods you eat often (a protein shake, your usual breakfast) and log them in one tap.",
      { label: "+ New food", onclick: () => { cancelSavedFoodEdit(); openSavedFoodForm(); } }));
    return;
  }
  const q = $("#sfSearch").value.trim().toLowerCase();
  let foods = q ? all.filter(f => f.name.toLowerCase().includes(q) || (f.tags || []).some(t => t.includes(q))) : all.slice();
  if ($("#sfSort").value === "used") foods.sort((a, b) => (b.uses || 0) - (a.uses || 0) || a.name.localeCompare(b.name));
  else foods.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  if (!foods.length) {
    list.replaceChildren(UI.empty("No saved food matches", `Nothing called "${$("#sfSearch").value.trim()}".`));
    return;
  }
  const usesText = (f) => f.uses ? `logged ${f.uses}×` : "not logged yet";
  if (PHONE.matches) {
    list.innerHTML = foods.map(f => `
      <div class="sf-card" data-id="${f.id}" tabindex="0" role="button" aria-label="Edit ${escapeHtml(f.name)}">
        <div class="log-card-main">
          <span class="fname">${escapeHtml(f.name)}</span>
          <span class="meta">${kcalText(f.calories)} kcal / ${fmt(f.serving_size)} ${escapeHtml(f.serving_unit)} · ${usesText(f)}</span>
          ${tagChips(f.tags)}
        </div>
        <button class="btn-secondary use-btn tap44" data-id="${f.id}" title="Add one serving to today's log">＋ log</button>
        <button type="button" class="kebab tap44 sf-menu-btn" data-id="${f.id}" aria-haspopup="menu" aria-expanded="false" aria-label="More for ${escapeHtml(f.name)}">⋯</button>
      </div>`).join("");
  } else {
    list.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Name</th><th>Serving</th><th>Calories</th><th>P</th><th>C</th><th>F</th><th>Used</th><th>Details</th><th></th>
        </tr>
      </thead>
      <tbody>
        ${foods.map(f => `
          <tr>
            <td class="col-name"><span class="fname" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>${tagChips(f.tags)}</td>
            <td>${fmt(f.serving_size)} ${escapeHtml(f.serving_unit)}</td>
            <td>${kcalText(f.calories)}</td>
            <td>${fmt(f.protein)}</td>
            <td>${fmt(f.carbs)}</td>
            <td>${fmt(f.fat)}</td>
            <td>${f.uses || 0}×</td>
            <td class="col-details">${detailsSnippetHtml(f.notes, f.name, `sf-${f.id}`)}</td>
            <td class="col-actions">
              <div class="item-actions">
                <button class="use-btn" data-id="${f.id}" title="Add to today's log">＋ log</button>
                <button class="edit-sf-btn" data-id="${f.id}" title="Edit" aria-label="Edit ${escapeHtml(f.name)}">✎</button>
                <button class="del-sf-btn" data-id="${f.id}" title="Delete" aria-label="Delete ${escapeHtml(f.name)}">✕</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
    bindEnlargeButtons(list);
    list.querySelectorAll(".fname").forEach(el => el.addEventListener("click", () => el.classList.toggle("open")));
  }
  const byId = (id) => foods.find(f => f.id === parseInt(id));

  $all(".del-sf-btn").forEach(btn => btn.addEventListener("click", withErrors(async () => {
    await deleteSavedFood(byId(btn.dataset.id));
  })));

  $all(".edit-sf-btn").forEach(btn => btn.addEventListener("click", () => {
    const food = byId(btn.dataset.id);
    if (food) startSavedFoodEdit(food);
  }));

  list.querySelectorAll(".sf-card").forEach(card => {
    card.addEventListener("click", () => { const f = byId(card.dataset.id); if (f) startSavedFoodEdit(f); });
    card.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === card) { e.preventDefault(); const f = byId(card.dataset.id); if (f) startSavedFoodEdit(f); } });
  });
  list.querySelectorAll(".sf-menu-btn").forEach(btn => btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const f = byId(btn.dataset.id);
    const items = [{ label: "✎ Edit", onclick: () => startSavedFoodEdit(f) }];
    if (f.notes) items.push({ label: "⤢ Details", onclick: () => openDetailsModal(f.name, f.notes) });
    items.push({ label: "✕ Delete", danger: true, onclick: withErrors(() => deleteSavedFood(f)) });
    openRowMenu(btn, items);
  }));

  // "＋ log": one serving on TODAY's log (what the button says), whatever day the Food Log is showing.
  $all(".use-btn").forEach(btn => btn.addEventListener("click", withErrors(async (e) => {
    if (e) e.stopPropagation();
    const food = byId(btn.dataset.id);
    if (!food) return;
    const day = todayStr();
    await api(withUser(`/api/logs`), {
      method: "POST",
      body: JSON.stringify({
        date: day,
        meal_type: "snack",
        food_name: food.name,
        servings: 1,
        calories: food.calories,
        protein: food.protein,
        carbs: food.carbs,
        fat: food.fat,
        saved_food_id: food.id,
        tags: food.tags || [],
      }),
    });
    alert(`Added "${food.name}" to today's log (${day}).`);
    await Promise.all([loadSummary(), loadHistory(), loadSavedFoodsList(), state.date === day ? loadFoodLog() : null]);
  })));
}

async function deleteSavedFood(food) {
  if (!food) return;
  if (!confirm(`Delete the saved food "${food.name}"?`)) return;
  await api(withUser(`/api/saved-foods/${food.id}`), { method: "DELETE" });
  if (state.editingSavedFoodId === food.id) cancelSavedFoodEdit();
  await loadSavedFoodsList();
}

function openSavedFoodForm() {
  $("#sfFormCard").hidden = false;
  $("#sfFormCard").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#sfName").focus({ preventScroll: true });
}

function startSavedFoodEdit(food) {
  state.editingSavedFoodId = food.id;
  $("#sfName").value = food.name;
  $("#sfServingSize").value = food.serving_size;
  $("#sfServingUnit").value = food.serving_unit;
  $("#sfCalories").value = food.calories === null ? "" : food.calories;
  sfTags.set(food.tags || []);
  $("#sfProtein").value = food.protein;
  $("#sfCarbs").value = food.carbs;
  $("#sfFat").value = food.fat;
  $("#sfNotes").value = food.notes || "";
  $("#sfFormTitle").textContent = `Editing "${food.name}"`;
  $("#sfSaveBtn").textContent = "Update Food";
  openSavedFoodForm();
}

// Cancel (and after saving): empty the form and fold it away.
function cancelSavedFoodEdit() {
  state.editingSavedFoodId = null;
  ["sfName", "sfCalories", "sfProtein", "sfCarbs", "sfFat", "sfNotes"].forEach(id => $("#" + id).value = "");
  sfTags.set([]);
  $("#sfServingSize").value = "1";
  $("#sfServingUnit").value = "serving";
  $("#sfFormTitle").textContent = "New saved food";
  $("#sfSaveBtn").textContent = "Save Food";
  $("#sfFormCard").hidden = true;
}

$("#sfCancelEditBtn").addEventListener("click", cancelSavedFoodEdit);

$("#sfSaveBtn").addEventListener("click", withErrors(async () => {
  const name = $("#sfName").value.trim();
  const kcalIn = parseFloat($("#sfCalories").value);
  const calories = isNaN(kcalIn) ? null : kcalIn;          // optional
  if (!name) return alert("Give the food a name.");
  const body = {
    name,
    serving_size: parseFloat($("#sfServingSize").value) || 1,
    serving_unit: $("#sfServingUnit").value.trim() || "serving",
    calories,
    protein: parseFloat($("#sfProtein").value) || 0,
    carbs: parseFloat($("#sfCarbs").value) || 0,
    fat: parseFloat($("#sfFat").value) || 0,
    notes: $("#sfNotes").value.trim() || null,
    tags: sfTags.get(),
  };
  if (state.editingSavedFoodId) {
    await api(withUser(`/api/saved-foods/${state.editingSavedFoodId}`), {
      method: "PUT",
      body: JSON.stringify(body),
    });
  } else {
    await api(withUser(`/api/saved-foods`), {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  cancelSavedFoodEdit();
  await Promise.all([loadSavedFoodsList(), loadChoices()]);
}));

// ---------- food tags, symptoms and patterns ----------
// Tags say what's in a food ("dairy", "spicy"): Patterns compares them as well as foods. App settings → Symptoms and
// patterns lists the ones offered; anyone can add their own (Enter in the box).
state.choices = { symptoms: [], tags: [] };

function tagPicker(root) {
  // A dropdown: the button shows what's chosen; the panel has a tick box per tag and a box to add your own.
  let chosen = [];
  const label = UI.h("span", { class: "tag-dd-label" });
  const btn = UI.h("button", { type: "button", class: "tag-dd-btn", "aria-haspopup": "true", "aria-expanded": "false" },
    UI.h("span", { "aria-hidden": "true" }, "🏷️"), label, UI.h("span", { class: "tag-dd-caret", "aria-hidden": "true" }, "▾"));
  const list = UI.h("div", { class: "tag-dd-list" });
  const input = UI.h("input", { class: "tag-add", placeholder: "Add your own tag…", maxlength: "30", "aria-label": "Add your own tag" });
  const addBtn = UI.h("button", { type: "button", class: "btn-secondary tag-add-btn" }, "Add");
  const panel = UI.h("div", { class: "tag-dd-panel", hidden: true, role: "group", "aria-label": "Food tags" },
    list, UI.h("div", { class: "tag-dd-add" }, input, addBtn));
  root.replaceChildren(btn, panel);
  root.classList.add("tag-dd");
  const open = (on) => { panel.hidden = !on; btn.setAttribute("aria-expanded", on ? "true" : "false"); root.classList.toggle("open", on); };
  btn.addEventListener("click", (e) => { e.stopPropagation(); open(panel.hidden); });
  panel.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", () => open(false));
  root.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) { open(false); btn.focus(); } });
  function drawLabel() {
    label.textContent = chosen.length ? chosen.join(", ") : "Tags (optional)";
    label.classList.toggle("empty", !chosen.length);
  }
  function draw() {
    const all = state.choices.tags.slice();
    for (const t of chosen) if (!all.includes(t)) all.push(t);
    list.replaceChildren(...all.map(t => {
      const cb = UI.h("input", { type: "checkbox", checked: chosen.includes(t) });
      cb.addEventListener("change", () => {
        if (cb.checked && chosen.length >= 12) { cb.checked = false; return alert("At most 12 tags on a food."); }
        chosen = cb.checked ? chosen.concat(t) : chosen.filter(x => x !== t);
        drawLabel();
      });
      return UI.h("label", { class: "tag-dd-item" }, cb, UI.h("span", null, t));
    }));
    drawLabel();
  }
  function addTyped() {
    const t = input.value.replace(/,/g, " ").trim().replace(/\s+/g, " ").toLowerCase().slice(0, 30);
    input.value = "";
    if (!t) return;
    if (!chosen.includes(t)) {
      if (chosen.length >= 12) return alert("At most 12 tags on a food.");
      chosen = chosen.concat(t);
    }
    draw();
    input.focus();
  }
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTyped(); } });
  addBtn.addEventListener("click", addTyped);
  draw();
  return { get: () => chosen.slice(), set: (tags) => { chosen = (tags || []).slice(); draw(); }, redraw: draw };
}
const foodTags = tagPicker($("#foodTags"));
const sfTags = tagPicker($("#sfTags"));

async function loadChoices() {
  state.choices = await api(withUser(`/api/symptoms/choices`));
  foodTags.redraw();
  sfTags.redraw();
  $("#symNames").replaceChildren(...state.choices.symptoms.map(s => UI.h("option", { value: s })));
  $("#symQuick").replaceChildren(...state.choices.symptoms.map(s => {
    const b = UI.h("button", { type: "button", class: "chip" }, s);
    b.addEventListener("click", () => {
      $("#symName").value = s;
      $all("#symQuick .chip").forEach(x => x.classList.toggle("on", x === b));
    });
    return b;
  }));
}

const SEVERITY = ["", "Mild", "Noticeable", "Moderate", "Bad", "Severe"];
state.severity = 3;
function drawSeverity() {
  $("#symSevText").textContent = SEVERITY[state.severity];
  $("#symSeverity").replaceChildren(...[1, 2, 3, 4, 5].map(n => {
    const b = UI.h("button", { type: "button", role: "radio", class: "sev sev-" + n + (n === state.severity ? " on" : ""),
      "aria-checked": n === state.severity ? "true" : "false", title: SEVERITY[n] },
      UI.h("span", { class: "sev-n" }, String(n)), UI.h("span", { class: "sev-label" }, SEVERITY[n]));
    b.addEventListener("click", () => { state.severity = n; drawSeverity(); });
    return b;
  }));
}
drawSeverity();

function nowHHMM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

$("#symAddBtn").addEventListener("click", withErrors(async () => {
  const symptom = $("#symName").value.trim();
  if (!symptom) return alert("Pick or type a symptom.");
  const time = $("#symTime").value || (state.date === todayStr() ? nowHHMM() : "");
  if (!time) return alert("When did it start? Pick a time.");
  await api(withUser(`/api/symptoms`), {
    method: "POST",
    body: JSON.stringify({ date: state.date, time, symptom, severity: state.severity, note: $("#symNote").value.trim() || null }),
  });
  $("#symName").value = "";
  $("#symNote").value = "";
  $("#symTime").value = "";
  $all("#symQuick .chip").forEach(x => x.classList.remove("on"));
  await Promise.all([loadSymptomDay(), loadChoices(), loadPatterns()]);
}));

async function loadSymptomDay() {
  let foods, syms;
  try {
    [foods, syms] = await Promise.all([
      api(withUser(`/api/logs?date=${state.date}`)),
      api(withUser(`/api/symptoms?date=${state.date}`)),
    ]);
  } catch (e) {
    $("#dayTimeline").replaceChildren(UI.errorBox(e, loadSymptomDay));
    return;
  }
  $("#symTime").placeholder = "";
  const items = foods.map(f => ({ t: f.eaten_at || MEAL_TIMES[f.meal_type] || "", kind: "food", f }))
    .concat(syms.map(s => ({ t: s.time, kind: "sym", s })))
    .sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : a.kind === "food" ? -1 : 1));
  const list = $("#dayTimeline");
  if (!items.length) {
    list.innerHTML = `<p class="hint">Nothing logged for this day yet.</p>`;
    return;
  }
  list.innerHTML = `<ul class="timeline">${items.map(i => i.kind === "food" ? `
    <li class="tl-food">
      <span class="tl-time">${escapeHtml(i.t)}</span><span class="tl-icon" aria-hidden="true">🍽️</span>
      <span class="tl-main"><span class="tl-name">${escapeHtml(i.f.food_name)}</span>
        <span class="tl-sub">${escapeHtml(i.f.meal_type)}${i.f.servings !== 1 ? ` · ${fmt(i.f.servings)}×` : ""}${i.f.calories !== null ? ` · ${fmt(i.f.calories)} kcal` : ""}</span>${tagChips(i.f.tags)}</span>
    </li>` : `
    <li class="tl-sym">
      <span class="tl-time">${escapeHtml(i.t)}</span><span class="tl-icon" aria-hidden="true">🩺</span>
      <span class="tl-main"><span class="tl-name">${escapeHtml(i.s.symptom)}</span>
        <span class="tl-sub"><span class="sev-dot sev-${i.s.severity}">${i.s.severity}</span> ${SEVERITY[i.s.severity]}${i.s.note ? " · " + escapeHtml(i.s.note) : ""}</span></span>
      <button class="del-sym-btn btn-icon" data-id="${i.s.id}" title="Delete" aria-label="Delete ${escapeHtml(i.s.symptom)}">✕</button>
    </li>`).join("")}</ul>`;
  list.querySelectorAll(".del-sym-btn").forEach(btn => btn.addEventListener("click", withErrors(async () => {
    const s = syms.find(x => String(x.id) === btn.dataset.id);
    if (!confirm(`Delete "${s ? s.symptom : "this symptom"}"?`)) return;
    await api(withUser(`/api/symptoms/${btn.dataset.id}`), { method: "DELETE" });
    await Promise.all([loadSymptomDay(), loadPatterns()]);
  })));
}

function patternLine(x, key) {
  const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
  return `<li><strong>${escapeHtml(x[key])}</strong>
    <span class="hint">before ${x.before} of ${x.of} times (${pct(x.before, x.of)}%) · other days ${x.otherOf ? `${x.other} of ${x.otherOf} (${pct(x.other, x.otherOf)}%)` : "none to compare"}</span></li>`;
}

async function loadPatterns() {
  const days = $("#patternDays").value;
  $("#journalCsv").href = withUser(`api/journal.csv?days=${days}`);
  const list = $("#patternList");
  let p;
  try { p = await api(withUser(`/api/patterns?days=${days}`)); }
  catch (e) { list.replaceChildren(UI.errorBox(e, loadPatterns)); return; }
  $("#patternIntro").textContent = `${p.loggedDays} day${p.loggedDays === 1 ? "" : "s"} with food logged in this time. Tap a symptom for what came before it.`;
  $("#patternAbout").textContent = `For each symptom, what you ate in the ${p.hours} hours before it is compared with the same hours on days without it. ` +
    "This shows what came together, not what causes what — talk to a doctor about symptoms that worry you.";
  if (!p.symptoms.length && !p.tooFew.length) {
    list.innerHTML = `<p class="hint">No symptoms logged in this time. Log symptoms (and what you eat) for a few weeks and patterns show up here.</p>`;
    return;
  }
  const strip = (series) => `<div class="sev-strip" aria-hidden="true">${series.map(v => `<span class="sev-bar sev-${v}" style="height:${v ? 20 + v * 16 : 4}%"></span>`).join("")}</div>`;
  // one line and the severity strip per symptom; tap it for what came before it
  list.innerHTML = p.symptoms.map(s => `
    <details class="pattern">
      <summary>
        <div class="pattern-head"><h4>${escapeHtml(s.name)}</h4>
          <span class="hint">${s.count} time${s.count === 1 ? "" : "s"} on ${s.days} day${s.days === 1 ? "" : "s"} · usually around ${escapeHtml(s.usualTime)} · average ${s.avgSeverity} of 5</span></div>
        ${strip(s.series)}
      </summary>
      ${s.tags.length || s.foods.length ? `
        <div class="pattern-cols">
          ${s.tags.length ? `<div><div class="pattern-sub">Tags often before it</div><ul class="pattern-list">${s.tags.map(x => patternLine(x, "tag")).join("")}</ul></div>` : ""}
          ${s.foods.length ? `<div><div class="pattern-sub">Foods and ingredients often before it</div><ul class="pattern-list">${s.foods.map(x => patternLine(x, "food")).join("")}</ul></div>` : ""}
        </div>` : `<p class="hint">Nothing you ate stands out before it${s.otherDays ? "" : " — there are no days without it to compare with yet"}.</p>`}
    </details>`).join("") + (p.tooFew.length ? `
    <p class="hint">Logged too few times to compare (${p.minCount} needed): ${p.tooFew.map(s => `${escapeHtml(s.name)} (${s.count})`).join(", ")}.</p>` : "");
}
$("#patternDays").addEventListener("change", withErrors(loadPatterns));

// ---------- weight ----------
$("#weightDate").value = state.date;

// The stat strip (latest, 7-day and 30-day change) uses the newest WEIGHT_FETCHED weigh-ins; the list shows the
// newest WEIGHT_SHOWN, and "Show all" loads and lists every one.
const WEIGHT_FETCHED = 100;
const WEIGHT_SHOWN = 10;
state.weightAll = false;
$("#weightShowAllBtn").addEventListener("click", withErrors(async () => { state.weightAll = true; await loadWeight(); }));

// latest weight minus the newest weigh-in on or before `days` days before the latest one's date
function weightChange(entries, days) {
  if (entries.length < 2) return null;
  const last = entries[entries.length - 1];
  const cutoff = shiftDate(last.date, -days);
  for (let i = entries.length - 2; i >= 0; i--) {
    if (entries[i].date <= cutoff) return last.weight_kg - entries[i].weight_kg;
  }
  return null;
}

function drawWeightStats(entries) {
  const last = entries[entries.length - 1];
  const change = (d) => {
    const c = weightChange(entries, d);
    if (c === null) return { text: "—", cls: "" };
    return { text: `${c > 0 ? "+" : ""}${fmt(c)} kg`, cls: "" };
  };
  const c7 = change(7), c30 = change(30);
  const kpi = (label, v, cls, title) => `<div class="kpi"${title ? ` title="${escapeHtml(title)}"` : ""}><div class="kpi-label">${label}</div><div class="kpi-value ${cls}">${v}</div></div>`;
  $("#weightStats").innerHTML =
    kpi("Latest", last ? `${fmt(last.weight_kg)} kg` : "—", "", last ? last.date : "") +
    kpi("7-day change", c7.text, c7.cls, "Since the weigh-in a week or more before the latest") +
    kpi("30-day change", c30.text, c30.cls, "Since the weigh-in 30 days or more before the latest");
}

async function loadWeight() {
  const list = $("#weightList");
  let entries;
  try {
    entries = await api(withUser(state.weightAll ? `/api/weight` : `/api/weight?limit=${WEIGHT_FETCHED}`));
  } catch (e) {
    list.replaceChildren(UI.errorBox(e, loadWeight));
    return;
  }
  drawWeightStats(entries);
  const shown = state.weightAll ? entries : entries.slice(-WEIGHT_SHOWN);
  $("#weightShowAllBtn").hidden = state.weightAll || entries.length <= WEIGHT_SHOWN;
  if (entries.length === 0) {
    list.replaceChildren(UI.empty("No weigh-ins yet", "Log your weight above to see your trend."));
  } else {
    list.innerHTML = shown.slice().reverse().map(w => `
      <div class="weight-item">
        <div class="log-item-main">
          <span class="name">${fmt(w.weight_kg)} kg</span>
          <span class="meta">${w.date}${w.note ? " · " + escapeHtml(w.note) : ""}</span>
        </div>
        <div class="item-actions">
          <button class="del-w-btn tap44" data-id="${w.id}" title="Delete" aria-label="Delete the weigh-in of ${w.date}">✕</button>
        </div>
      </div>
    `).join("");
    $all(".del-w-btn").forEach(btn => btn.addEventListener("click", withErrors(async () => {
      if (!confirm("Delete this weigh-in?")) return;
      await api(withUser(`/api/weight/${btn.dataset.id}`), { method: "DELETE" });
      await Promise.all([loadWeight(), loadWeightHistory(), loadSummary()]);
    })));
  }
}

$("#addWeightBtn").addEventListener("click", withErrors(async () => {
  const weight = parseFloat($("#weightValue").value);
  if (isNaN(weight)) return alert("Enter a weight value.");
  await api(withUser(`/api/weight`), {
    method: "POST",
    body: JSON.stringify({
      date: $("#weightDate").value || state.date,
      weight_kg: weight,
      note: $("#weightNote").value.trim() || null,
    }),
  });
  $("#weightValue").value = "";
  $("#weightNote").value = "";
  await Promise.all([loadWeight(), loadWeightHistory(), loadSummary()]);
}));

// ---------- weight history chart (daily/weekly/monthly) ----------
$all(".wt-hist-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    $all(".wt-hist-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    state.weightHistoryGranularity = btn.dataset.granularity;
    await loadWeightHistory();
  });
});

let lastWeightPoints = [];
let lastWeightTarget = null;
let hoveredWeightIndex = -1;
let weightRegions = [];

async function loadWeightHistory() {
  const data = await api(withUser(`/api/weight/history?granularity=${state.weightHistoryGranularity}`));
  lastWeightPoints = data.points;
  lastWeightTarget = data.target;
  drawWeightHistoryChart();
  const hintMap = {
    daily: "Logged weight over the last 30 days.",
    weekly: "Average weight per week (weeks with no entry are skipped). Last 12 weeks.",
    monthly: "Average weight per month (months with no entry are skipped). Last 12 months.",
  };
  let hint = hintMap[state.weightHistoryGranularity] || "";
  if (data.target) hint += ` Target: ${fmt(data.target)} kg (dashed line).`;
  $("#weightHistoryHint").textContent = data.points.length === 0
    ? "No weight entries in this range yet."
    : hint;
}

function drawWeightHistoryChart() {
  const points = lastWeightPoints, target = lastWeightTarget;
  const canvas = $("#weightChart");
  const ctx = canvas.getContext("2d");
  canvas.width = canvas.clientWidth;
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  weightRegions = [];
  if (points.length === 0) return;

  const textDim = cssVar("--text-dim", "#93a0b8");
  const gridColor = cssVar("--border", "#2b3549");
  const accent = cssVar("--accent", "#5ee6a8");
  const targetColor = cssVar("--carbs", "#f2c94c");
  const highlightColor = cssVar("--text", "#e7ecf5");
  const surfaceColor = cssVar("--panel", "#1a2130");

  const weights = points.map(p => p.weight);
  const rawMin = Math.min(...weights, target ?? weights[0]);
  const rawMax = Math.max(...weights, target ?? weights[0]);
  const pad = (rawMax - rawMin) * 0.1 || 1;
  const ticks = niceTicks(rawMin - pad, rawMax + pad, 4);
  const min = ticks[0], max = ticks[ticks.length - 1];

  const padL = 46, padR = 10, padT = 12, padB = 26;
  const chartW = w - padL - padR;
  const chartH = h - padT - padB;
  const range = (max - min) || 1;

  const xFor = i => points.length > 1 ? padL + (i / (points.length - 1)) * chartW : padL + chartW / 2;
  const yFor = val => padT + chartH - ((val - min) / range) * chartH;

  // Gridlines + y-axis reference numbers (in kg).
  drawGrid(ctx, ticks, yFor, fmt, padL, w - padR, gridColor, textDim);

  if (target !== null && target !== undefined) {
    const ty = yFor(target);
    ctx.strokeStyle = targetColor;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padL, ty);
    ctx.lineTo(w - padR, ty);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  if (points.length >= 2) {
    ctx.beginPath();
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    points.forEach((p, i) => {
      const x = xFor(i), y = yFor(p.weight);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  points.forEach((p, i) => {
    const x = xFor(i), y = yFor(p.weight);
    const isHovered = i === hoveredWeightIndex;
    ctx.fillStyle = accent;
    ctx.beginPath();
    ctx.arc(x, y, isHovered ? 5 : 3, 0, Math.PI * 2);
    ctx.fill();
    // A surface-color ring keeps the dot legible where the line crosses
    // through/behind it, and doubles as the hover highlight ring.
    ctx.strokeStyle = isHovered ? highlightColor : surfaceColor;
    ctx.lineWidth = isHovered ? 1.5 : 2;
    ctx.stroke();
  });

  ctx.fillStyle = textDim;
  ctx.font = "9px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  points.forEach((p, i) => {
    if (points.length <= 10 || i % 2 === 0) {
      ctx.fillText(p.label, xFor(i), h - 8);
    }
  });

  // Hit bands split the x-axis at the midpoints between points (a simple
  // 1D Voronoi), so the pointer only has to be closest to a point rather
  // than land exactly on its small dot.
  points.forEach((p, i) => {
    const x = xFor(i);
    const prevX = i > 0 ? xFor(i - 1) : padL - chartW;
    const nextX = i < points.length - 1 ? xFor(i + 1) : w + chartW;
    weightRegions.push({
      xStart: (prevX + x) / 2,
      xEnd: (x + nextX) / 2,
      tipX: x,
      tipY: yFor(p.weight),
      index: i,
      valueText: `${fmt(p.weight)} kg`,
      subText: p.label,
    });
  });
}

attachChartHover(
  $("#weightChart"),
  $("#weightTooltip"),
  () => weightRegions,
  (index) => {
    if (index === hoveredWeightIndex) return;
    hoveredWeightIndex = index;
    drawWeightHistoryChart();
  }
);

// ---------- goals ----------
async function loadGoals() {
  const goal = await api(withUser(`/api/goals`));
  $("#goalCalories").value = goal.calorie_goal;
  $("#goalProtein").value = goal.protein_goal;
  $("#goalCarbs").value = goal.carb_goal;
  $("#goalFat").value = goal.fat_goal;
  $("#goalStartWeight").value = goal.starting_weight_kg ?? "";
  $("#goalTargetWeight").value = goal.target_weight_kg ?? "";
}

$("#saveGoalsBtn").addEventListener("click", withErrors(async () => {
  await api(withUser(`/api/goals`), {
    method: "PUT",
    body: JSON.stringify({
      calorie_goal: parseFloat($("#goalCalories").value) || 0,
      protein_goal: parseFloat($("#goalProtein").value) || 0,
      carb_goal: parseFloat($("#goalCarbs").value) || 0,
      fat_goal: parseFloat($("#goalFat").value) || 0,
      starting_weight_kg: $("#goalStartWeight").value ? parseFloat($("#goalStartWeight").value) : null,
      target_weight_kg: $("#goalTargetWeight").value ? parseFloat($("#goalTargetWeight").value) : null,
    }),
  });
  alert("Goals saved.");
  await Promise.all([loadSummary(), loadHistory(), loadMonthSummary(), loadWeightHistory(), loadDashTop()]);
}));

// ---------- AI ----------
// Builds the notices shown wherever AI is used (Food Log and Saved Foods
// estimate cards, AI Assistant, App settings): "AI isn't set up — …" while
// there's no usable setup, and the privacy notice while the configured
// address is outside the home network. DOM nodes + textContent only.
function aiNoticeNodes(st) {
  const nodes = [];
  if (!st) return nodes;
  if (!st.configured) {
    const p = document.createElement("p");
    p.className = "notice-warn";
    p.textContent = st.message || "AI isn't set up — an admin can set it up in Admin → App settings.";
    nodes.push(p);
  }
  if (st.privacy) {
    const p = document.createElement("p");
    p.className = "notice-privacy";
    const strong = document.createElement("strong");
    strong.textContent = `AI requests go to ${st.privacy.label}. `;
    const code = document.createElement("code");
    code.textContent = st.privacy.host;
    p.append(strong, "The food descriptions you send with “✨ Estimate with AI” and the messages you type on AI Assistant are sent to ",
      code, " — outside your home network. That service's own privacy and data-retention rules apply. Nothing else from the app (your log, weight or goals) is sent.");
    if (state.isAdmin) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "link-btn";
      b.textContent = "Change in Admin → App settings";
      b.addEventListener("click", () => navigateAdmin("settings"));
      p.append(" ", b);
    }
    nodes.push(p);
  }
  return nodes;
}

function renderAiState() {
  const st = state.ai;
  $all(".ai-notice").forEach(box => {
    const nodes = aiNoticeNodes(st);
    box.replaceChildren(...nodes);
    box.hidden = nodes.length === 0;
  });
  const ready = !!(st && st.configured);
  ["#aiFillBtn", "#sfAiFillBtn", "#chatSendBtn", "#chatInput"].forEach(sel => { $(sel).disabled = !ready; });
  $("#aiWarmupBtn").hidden = !(ready && st.warmup);
}

function describeLastRequest(r) {
  if (!r || r.error) return "";
  const tok = (r.input_tokens != null || r.output_tokens != null)
    ? `${r.input_tokens ?? "?"} tokens in, ${r.output_tokens ?? "?"} out, ` : "";
  return `Last request (${r.purpose}): ${tok}${r.seconds} s.`;
}

async function refreshAiStatus() {
  const dot = $("#aiStatusDot");
  const text = $("#aiStatusText");
  try {
    const st = await api("/api/ai/status");
    state.ai = st;
    renderAiState();
    const usage = describeLastRequest(st.last_request);
    $("#aiUsageText").textContent = usage;
    $("#aiUsageText").hidden = !usage;
    if (!st.configured) {
      dot.className = "status-dot err";
      text.textContent = st.message;
    } else if (!st.admin) {
      // the address, model and last error are for admins (GET /api/ai/status sends them only to them)
      dot.className = st.last_ok ? "status-dot ok" : "status-dot";
      text.textContent = `${st.providerLabel} is set up.` + (st.warmup && !st.last_ok
        ? ` Not warmed up yet — click "Wake up model" (or just ask; the first answer takes longer).` : "");
    } else if (st.last_error) {
      dot.className = "status-dot err";
      text.textContent = `${st.providerLabel} · ${st.model} at ${st.url}. Last error: ${st.last_error}`;
    } else if (st.last_ok) {
      dot.className = "status-dot ok";
      text.textContent = `Connected to ${st.providerLabel} · ${st.model} at ${st.url}.`;
    } else {
      dot.className = "status-dot";
      text.textContent = `Set up for ${st.providerLabel} · ${st.model} at ${st.url}.` +
        (st.warmup ? ` Not warmed up yet — click "Wake up model" (or just ask; the first answer takes longer).` : "");
    }
  } catch (e) {
    dot.className = "status-dot err";
    text.textContent = "Could not reach the backend.";
  }
  $("#aiStatusChip").title = text.textContent;     // the chip is one line: the whole text on hover
}

$("#aiWarmupBtn").addEventListener("click", async () => {
  $("#aiStatusText").textContent = "Sending 'hi' to wake the model…";
  $("#aiStatusDot").className = "status-dot";
  try {
    await api("/api/ai/warmup", { method: "POST" });
    await refreshAiStatus();
  } catch (e) {
    $("#aiStatusDot").className = "status-dot err";
    $("#aiStatusText").textContent = "Warmup failed: " + e.message;
  }
});

async function aiEstimateInto(prefix) {
  const nameField = $(`#${prefix}Name`);
  const description = nameField.value.trim() || prompt("Describe the food (e.g. '1 cup cooked white rice'):");
  if (!description) return;
  const statusEl = prefix === "food" ? $("#aiFillStatus") : null;
  if (statusEl) statusEl.textContent = "Estimating… this can take a while on first use.";
  try {
    const est = await api("/api/ai/estimate", { method: "POST", body: JSON.stringify({ description }) });
    if (!nameField.value.trim()) nameField.value = est.food_name;    // what was typed (an ingredient list) stays
    if (prefix === "food") {
      $("#foodServings").value = "1";
    } else {
      $("#sfServingSize").value = est.serving_size;
      $("#sfServingUnit").value = est.serving_unit;
    }
    $(`#${prefix}Calories`).value = est.calories;
    $(`#${prefix}Protein`).value = est.protein;
    $(`#${prefix}Carbs`).value = est.carbs;
    $(`#${prefix}Fat`).value = est.fat;
    if (statusEl) statusEl.textContent = est.note ? `AI note: ${est.note}` : "Estimate filled in — review before saving.";
    refreshAiStatus();
  } catch (e) {
    if (statusEl) statusEl.textContent = "AI estimate failed: " + e.message;
    else alert("AI estimate failed: " + e.message);
  }
}

$("#aiFillBtn").addEventListener("click", () => aiEstimateInto("food"));
$("#sfAiFillBtn").addEventListener("click", () => aiEstimateInto("sf"));

// An empty chat offers a few questions to start with.
const CHAT_SUGGESTIONS = [
  "How much protein should I eat a day?",
  "Ideas for a high-protein vegetarian breakfast",
  "Is it OK to eat late at night?",
  "What's a healthy snack under 200 kcal?",
];
function drawChatSuggest() {
  const box = $("#chatSuggest");
  const empty = !$("#chatHistory").children.length;
  box.hidden = !empty;
  if (!empty) return;
  box.replaceChildren(...CHAT_SUGGESTIONS.map(q => {
    const b = UI.h("button", { type: "button", class: "chip" }, q);
    b.addEventListener("click", () => {
      if ($("#chatSendBtn").disabled) { $("#chatInput").value = q; return; }
      $("#chatInput").value = q;
      sendChat();
    });
    return b;
  }));
}
drawChatSuggest();

function appendChat(role, text) {
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  div.textContent = text;
  $("#chatHistory").appendChild(div);
  $("#chatHistory").scrollTop = $("#chatHistory").scrollHeight;
  drawChatSuggest();
  return div;
}

$("#chatSendBtn").addEventListener("click", sendChat);
$("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

async function sendChat() {
  const input = $("#chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendChat("user", message);
  const loadingEl = appendChat("ai loading", "Thinking…");
  try {
    const res = await api("/api/ai/chat", { method: "POST", body: JSON.stringify({ message }) });
    loadingEl.textContent = res.reply;
    loadingEl.className = "chat-msg ai";
  } catch (e) {
    loadingEl.textContent = "Error: " + e.message;
    loadingEl.className = "chat-msg ai";
  }
  refreshAiStatus();
}

// ---------- refresh orchestration ----------
// What depends on the day shown (the saved-food picker doesn't: it's loaded with everything else and when a saved
// food changes).
async function refreshDateScoped() {
  await Promise.all([loadSummary(), loadFoodLog(), loadHistory(), loadSymptomDay()]);
}

// Charts are drawn at the canvas's width: draw them again when the window (or the phone's orientation) changes.
window.addEventListener("resize", UI.debounce(() => {
  drawHistoryChart();
  drawWeightHistoryChart();
}, 150));

async function refreshAll() {
  await Promise.all([
    loadSummary(),
    loadFoodLog(),
    loadSavedFoodsList(),
    loadWeight(),
    loadWeightHistory(),
    loadGoals(),
    loadHistory(),
    loadMonthSummary(),
    $("#tab-dashboard").classList.contains("active") ? loadDashTop() : null,
    loadChoices(),
    loadSymptomDay(),
    $("#tab-symptoms").classList.contains("active") ? loadPatterns() : null,
  ]);
}

// ---------- admin: App settings ----------
// Drawn by common/settings.js from GET /api/admin/settings (labels, help, limits and defaults come from
// the server). This app adds the AI notice, "Test connection", and the provider/address behaviour.
const h = UI.h;

function setStatus(el, text, kind) {
  el.textContent = text;
  el.classList.toggle("ok", kind === "ok");
  el.classList.toggle("err", kind === "err");
}

function urlPlaceholder(page, provider) {
  const info = (page.data.providers || {})[provider];
  return (info && info.defaultUrl) || "http://192.168.1.10:11434";
}

// What "Test connection" sends: what's typed on the page, saved or not (an empty key box = the saved key).
function settingsFormValues(page) {
  const maxTokens = page.value("ai_max_tokens");
  const body = {
    ai_provider: page.value("ai_provider"),
    ai_url: page.value("ai_url"),
    ai_model: page.value("ai_model"),
    ai_max_tokens: Number.isInteger(maxTokens) ? maxTokens : page.values.ai_max_tokens,
    expose_daily_calories_sensor: page.value("expose_daily_calories_sensor"),
  };
  if (page.edits.ai_api_key) body.ai_api_key = page.edits.ai_api_key;   // empty box = keep the saved key
  if (page.extra.clear_ai_api_key) body.clear_ai_api_key = true;
  return body;
}

function aiTestBlock(page) {
  const status = h("div", { id: "settingsTestStatus", class: "hint settings-status", role: "status" });
  const btn = h("button", { type: "button", id: "settingsTestBtn", class: "btn-secondary" }, "🔌 Test connection");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    setStatus(status, "Asking the provider which models it offers…", null);
    try {
      // Tests what's in the form, even if it isn't saved yet.
      const r = await api("/api/admin/settings/test-ai", { method: "POST", body: JSON.stringify(settingsFormValues(page)) });
      setStatus(status, (r.ok ? "✓ " : "✗ ") + r.message, r.ok ? "ok" : "err");
    } catch (e) {
      setStatus(status, "✗ " + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  });
  return [btn,
    h("p", { class: "hint", style: "margin:6px 0 0" }, "Uses what's typed above, before saving (an empty key box uses the saved key). Only asks the provider which models it offers — nothing is generated, so it costs nothing."),
    status];
}

async function loadAppSettings() {
  const box = $("#appSettingsBox");
  try {
    await SettingsPage.render(box, {
      load: () => api("/api/admin/settings"),
      save: (body) => api("/api/admin/settings", { method: "PUT", body: JSON.stringify(body) }),
      groups: { ai: { top: () => h("div", { class: "ai-notice", hidden: true }), bottom: aiTestBlock } },
      footer: () => h("p", null, "Changes apply right away — no restart needed. Only who counts as an admin is set in the app's Configuration tab (",
        h("code", null, "admin_users"), ")."),
      saveLabel: "Save settings",
      onChange: (key, value, page) => {
        // A result shown for the old values would be misleading once the form changes.
        const test = $("#settingsTestStatus");
        if (test) setStatus(test, "", null);
        // Another provider's address would be wrong for this one: start blank (= its
        // standard address), and bring back the saved address when switching back.
        if (key === "ai_provider") {
          const input = page.row("ai_url").querySelector("input");
          input.value = value === page.values.ai_provider ? page.values.ai_url : "";
          input.placeholder = urlPlaceholder(page, value);
          page.set("ai_url", input.value);
        }
      },
      afterSave: (data, body, page) => {
        let msg = "✓ Saved. Changes apply right away.";
        if ("expose_daily_calories_sensor" in body) {
          msg += data.values.expose_daily_calories_sensor
            ? " Publishing everyone's daily-calories sensor to Home Assistant now."
            : " Removing the daily-calories sensors from Home Assistant now.";
        }
        refreshAiStatus();
        return msg;
      },
      afterDraw: (page) => {
        page.row("ai_url").querySelector("input").placeholder = urlPlaceholder(page, page.values.ai_provider);
      },
    });
  } catch (e) {
    UI.mount(box, h("p", { class: "hint settings-status err" }, "Could not load settings: " + e.message));
  }
  refreshAiStatus();
}

// ---------- admin: restore database from backup ----------
(function () {
  const btn = document.getElementById("restoreDbBtn");
  const fileInput = document.getElementById("restoreDbFile");
  const status = document.getElementById("restoreDbStatus");
  if (!btn || !fileInput) return;
  btn.addEventListener("click", async () => {
    const file = fileInput.files[0];
    if (!file) {
      status.textContent = "Choose a .db file first.";
      return;
    }
    if (!confirm("This will REPLACE all current data with the contents of this file and cannot be undone. Continue?")) {
      return;
    }
    btn.disabled = true;
    status.textContent = "Importing…";
    try {
      const formData = new FormData();
      formData.append("file", file);
      // headers: {} overrides api()'s default JSON content-type so the
      // browser can set its own multipart boundary for the file upload.
      await api("/api/admin-storage-import-db", { method: "POST", body: formData, headers: {} });
      status.textContent = "Import complete. Reloading…";
      setTimeout(() => location.reload(), 1200);
    } catch (err) {
      status.textContent = err.message || "Import failed.";
      btn.disabled = false;
    }
  });
})();

// ---------- Back gesture in Home Assistant (backnav.js) ----------
// Start page = Food Log (no address, or #/foodlog). Back closes the open menu / popup first, then retraces the
// pages opened from a tab, then goes to the page's parent (an Admin tab → #/admin), then to Food Log; only Back on
// Food Log with nothing open leaves the app.
BackNav.init({
  atHome: () => $("#tab-foodlog").classList.contains("active") && ["", "#", "#/", "#/foodlog"].includes(location.hash),
  goHome: () => { clearRouteHash(); openTab("foodlog"); },
  openLayers: () => [
    $("#navMore").closest(".side-nav").classList.contains("more-open") ? $("#navMore") : null,
    $("#meMenu").hidden ? null : $("#meMenu"),
    $("#rowMenu").hidden ? null : $("#rowMenu"),
    $("#detailsModal").hidden ? null : $("#detailsModal"),
  ].filter(Boolean),
  closeLayer: (layer) => {
    if (layer === $("#detailsModal")) closeDetailsModal();
    else if (layer === $("#rowMenu")) closeRowMenu();
    else if (layer === $("#meMenu")) closeMeMenu();
    else closeMore();
  },
  isTab: (hash) => /^#\/(foodlog|symptoms|dashboard|weight|savedfoods|goals|ai|whoami|admin(\/[a-z]+)?)$/.test(hash),
  parentOf: (hash) => (/^#\/admin\/[a-z]+$/.test(hash) ? "#/admin" : null),
});

// A link from Home Assistant (the Household Assistant) opens "/<page>/foodlog[/<date>]", "/<page>/dashboard", …
const LINK_TABS = ["foodlog", "dashboard", "savedfoods", "weight", "goals", "symptoms"];
function linkRoute(route) {
  const m = /^\/([a-z]+)(?:\/(\d{4}-\d{2}-\d{2}))?$/.exec(route);
  if (!m || !LINK_TABS.includes(m[1]) || (m[2] && m[1] !== "foodlog" && m[1] !== "symptoms")) return null;
  return { tab: m[1], date: m[2] || null };
}
function openLink(r) {
  showTab(r.tab);
  if (r.date && r.date <= todayStr()) setDate(r.date);
}

// First paint: a loading hint in every list until its data is in.
["#foodLogList", "#savedFoodsList", "#weightList", "#dayTimeline", "#patternList", "#usersList", "#monthSummaryGrid"]
  .forEach(sel => UI.loading($(sel)));

(async function init() {
  await loadToday();
  await loadMe();
  const hashLink = linkRoute(location.hash.replace(/^#/, ""));   // "#/foodlog/<date>": the server's redirect of a link
  if (!hashLink) routeFromHash();   // the address shown: #/weight, #/admin/settings, #/storage, …
  HouseholdDeepLink.start(state.page, linkRoute, openLink);
  if (hashLink) { BackNav.replace(`#/${hashLink.tab}`); if (hashLink.date && hashLink.date <= todayStr()) setDate(hashLink.date); }
  await loadUsers();
  await refreshAll();
  refreshAiStatus();
  // Keeps "today" correct across local midnight for a tab left open, and
  // catches up immediately if the tab was backgrounded across it.
  setInterval(loadToday, 5 * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadToday();
  });
})();
