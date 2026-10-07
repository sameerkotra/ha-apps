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

const $ = (sel) => UI.$(sel);
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
const DATE_BAR_TABS = new Set(["foodlog"]);

function updateDateBarVisibility(tab) {
  $("#dateBar").hidden = !DATE_BAR_TABS.has(tab);
}

function activateTab(tab) {
  $all(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $all(".tab-panel").forEach(p => p.classList.remove("active"));
  $(`#tab-${tab}`).classList.add("active");
  updateDateBarVisibility(tab);
}

function showTab(tab) {
  if (tab === "admin") { navigateAdmin(adminSubtab); return; }
  if (LEGACY_ADMIN_TABS[tab]) { navigateAdmin(LEGACY_ADMIN_TABS[tab]); return; }
  clearRouteHash();
  activateTab(tab);
  if (tab === "ai") refreshAiStatus();
  if (tab === "weight") loadWeightHistory();
  // The history canvas sizes itself from its container's clientWidth,
  // which is 0 while its tab-panel is display:none — so it has to
  // re-measure once Dashboard is what's actually visible. This only
  // started mattering once Food Log (not Dashboard) became the tab that
  // opens by default; before that, Dashboard was always visible at the
  // one moment loadHistory() ran automatically (page load).
  if (tab === "dashboard") loadHistory();
}

$all(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

// ---------- Admin (admins only: App settings | Users | Storage) ----------
// One sidebar item, hidden for non-admins (the server enforces every admin
// API on its own anyway). Deep-linkable as #/admin/settings, #/admin/users,
// #/admin/storage; #/users and #/storage open the matching Admin tab too.
const ADMIN_SUBTABS = ["settings", "users", "storage"];
const LEGACY_ADMIN_TABS = { users: "users", storage: "storage", settings: "settings" };
let adminSubtab = "settings";

function navigateAdmin(sub) {
  const hash = `#/admin/${sub}`;
  // BackNav.go: no history entry of its own (backnav.js), still → hashchange → routeFromHash()
  if (location.hash !== hash) BackNav.go(hash);
  else routeFromHash();
}

function clearRouteHash() {
  if (location.hash.startsWith("#/")) history.replaceState(null, "", location.pathname + location.search);
}

// Returns true if the hash named a page (and it was opened).
function routeFromHash() {
  const m = location.hash.match(/^#\/([a-z]+)(?:\/([a-z]+))?\/?$/);
  if (!m) return false;
  let top = m[1], sub = m[2];
  if (top !== "admin") {
    if (!LEGACY_ADMIN_TABS[top]) return false;
    sub = LEGACY_ADMIN_TABS[top];
    history.replaceState(null, "", `#/admin/${sub}`);
  }
  openAdmin(ADMIN_SUBTABS.includes(sub) ? sub : "settings");
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
  btn.addEventListener("click", () => navigateAdmin(btn.dataset.adminTab));
});

window.addEventListener("hashchange", () => {
  // Back button from #/admin/... to the plain page: leave the Admin page.
  if (!routeFromHash() && $("#tab-admin").classList.contains("active")) showTab("foodlog");
});

// Matches whichever tab-panel loaded with "active" in index.html (Food Log
// today), so the date bar's initial visibility is correct without having
// to hardcode that default a second time here.
updateDateBarVisibility(document.querySelector(".tab-btn.active")?.dataset.tab);

// ---------- "How the app sees you" ----------
// Read-only diagnostic: exactly which user name / id Home Assistant sent, and
// whether they match the admin_users list. Reached from the user chip in the
// sidebar and the 👤 button in the top bar (phones have no sidebar chip).
function openWhoami() {
  clearRouteHash();
  $all(".tab-btn").forEach(b => b.classList.remove("active"));
  $all(".tab-panel").forEach(p => p.classList.remove("active"));
  $("#tab-whoami").classList.add("active");
  updateDateBarVisibility("whoami");
  loadWhoami();
}

async function loadWhoami() {
  const body = $("#whoamiBody");
  let w;
  try { w = await api("/api/whoami"); }
  catch (e) { body.replaceChildren(Object.assign(document.createElement("p"), { className: "hint", textContent: e.message })); return; }
  // common/whoami.js: the same rows and wording as every other app
  body.replaceChildren(HouseholdWhoami.panel(w, {
    appName: "Calorie Tracker",
    classes: { copy: "btn-secondary kv-copy" },
  }));
}

$("#sidebarUser").addEventListener("click", openWhoami);
$("#whoamiBtn").addEventListener("click", openWhoami);

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
    // First run: nobody is an admin yet, so nobody could open App settings.
    // Everyone sees how to fix it (never auto-promoted).
    HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), me.noAdmin, me.username || me.id,
      { onOpen: openWhoami, linkId: "noAdminWhoamiLink" });
  } catch (e) {
    $("#sidebarUser").textContent = "Not signed in";
    document.body.innerHTML = `<div style="padding:40px;font-family:sans-serif;color:#e7ecf5;background:#10151c;min-height:100vh;">
      <h2>Can't identify a Home Assistant user</h2>
      <p>${escapeHtml(e.message)}</p>
      <p>Open Calorie Tracker from its panel in the Home Assistant sidebar.</p>
    </div>`;
    throw e;
  }
}

// ---------- user switcher + Users tab ----------
let allUsers = [];

async function loadUsers() {
  if (!state.isAdmin) return;
  allUsers = await api("/api/users");
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
function renderUsersTab() {
  PeoplePage.render($("#usersList"), {
    people: allUsers,
    cardClass: "",
    empty: "No one else has opened this app yet.",
    person: (u) => ({
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
  renderDateBar();
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
    { label: "Calories / day", value: fmt(data.avg_per_day.calories), goal: data.goal.calorie_goal },
    { label: "Protein (g) / day", value: fmt(data.avg_per_day.protein), goal: data.goal.protein_goal },
    { label: "Carbs (g) / day", value: fmt(data.avg_per_day.carbs), goal: data.goal.carb_goal },
    { label: "Fat (g) / day", value: fmt(data.avg_per_day.fat), goal: data.goal.fat_goal },
  ];
  const weightChange = data.weight.change_kg;
  const weightSub = weightChange == null
    ? ""
    : ` (${weightChange > 0 ? "+" : ""}${fmt(weightChange)} kg)`;

  $("#monthSummaryGrid").innerHTML = tiles.map(t => {
    const over = t.value > t.goal;
    return `<div class="stat-card ${over ? "over" : ""}">
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
  const data = await api(withUser(`/api/summary/month?month=${state.summaryMonth}`));
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

// ---------- dashboard ----------
async function loadSummary() {
  const summary = await api(withUser(`/api/summary?date=${state.date}`));
  const cards = [
    { label: "Calories", value: fmt(summary.totals.calories), goal: summary.goal.calorie_goal },
    { label: "Protein (g)", value: fmt(summary.totals.protein), goal: summary.goal.protein_goal },
    { label: "Carbs (g)", value: fmt(summary.totals.carbs), goal: summary.goal.carb_goal },
    { label: "Fat (g)", value: fmt(summary.totals.fat), goal: summary.goal.fat_goal },
  ];
  $("#summaryCards").innerHTML = cards.map(c => {
    const over = c.value > c.goal;
    return `<div class="stat-card ${over ? "over" : ""}">
      <div class="value">${c.value}</div>
      <div class="label">${c.label} / ${fmt(c.goal)}</div>
    </div>`;
  }).join("") + `<div class="stat-card">
      <div class="value">${summary.latest_weight_kg ? fmt(summary.latest_weight_kg) + " kg" : "—"}</div>
      <div class="label">Latest Weight</div>
    </div>`;

  const macros = [
    { name: "Protein", val: summary.totals.protein, goal: summary.goal.protein_goal, color: "var(--protein)" },
    { name: "Carbs", val: summary.totals.carbs, goal: summary.goal.carb_goal, color: "var(--carbs)" },
    { name: "Fat", val: summary.totals.fat, goal: summary.goal.fat_goal, color: "var(--fat)" },
  ];
  $("#macroBars").innerHTML = macros.map(m => {
    const pct = Math.min(100, (m.val / (m.goal || 1)) * 100);
    return `<div class="macro-bar-row">
      <div class="macro-bar-label"><span>${m.name}</span><span>${fmt(m.val)} / ${fmt(m.goal)} g</span></div>
      <div class="macro-bar-track"><div class="macro-bar-fill" style="width:${pct}%; background:${m.color}"></div></div>
    </div>`;
  }).join("");
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

// ---------- food log ----------
async function loadFoodLog() {
  const logs = await api(withUser(`/api/logs?date=${state.date}`));
  const list = $("#foodLogList");
  if (logs.length === 0) {
    list.innerHTML = `<p class="hint">Nothing logged yet for this day.</p>`;
    return;
  }
  list.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Meal</th>
          <th>Food</th>
          <th>Servings</th>
          <th>Calories</th>
          <th>P</th>
          <th>C</th>
          <th>F</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${logs.map(l => `
          <tr>
            <td><span class="tag">${escapeHtml(l.meal_type)}</span></td>
            <td class="col-name">${escapeHtml(l.food_name)}</td>
            <td>${fmt(l.servings)}×</td>
            <td>${fmt(l.calories)}</td>
            <td>${fmt(l.protein)}</td>
            <td>${fmt(l.carbs)}</td>
            <td>${fmt(l.fat)}</td>
            <td class="col-actions">
              <div class="item-actions">
                <button data-id="${l.id}" class="del-log-btn" title="Delete">✕</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  $all(".del-log-btn").forEach(btn => btn.addEventListener("click", async () => {
    await api(withUser(`/api/logs/${btn.dataset.id}`), { method: "DELETE" });
    await loadFoodLog();
    await loadSummary();
  }));
}

function clearFoodForm() {
  $("#foodName").value = "";
  $("#foodServings").value = "1";
  $("#foodCalories").value = "";
  $("#foodProtein").value = "";
  $("#foodCarbs").value = "";
  $("#foodFat").value = "";
  $("#savedFoodPicker").value = "";
  $("#aiFillStatus").textContent = "";
}

$("#addFoodBtn").addEventListener("click", async () => {
  const name = $("#foodName").value.trim();
  const calories = parseFloat($("#foodCalories").value);
  if (!name || isNaN(calories)) return alert("Food name and calories are required.");
  const savedFoodId = $("#savedFoodPicker").value ? parseInt($("#savedFoodPicker").value) : null;
  await api(withUser(`/api/logs`), {
    method: "POST",
    body: JSON.stringify({
      date: state.date,
      meal_type: $("#mealType").value,
      food_name: name,
      servings: parseFloat($("#foodServings").value) || 1,
      calories,
      protein: parseFloat($("#foodProtein").value) || 0,
      carbs: parseFloat($("#foodCarbs").value) || 0,
      fat: parseFloat($("#foodFat").value) || 0,
      saved_food_id: savedFoodId,
    }),
  });
  clearFoodForm();
  await loadFoodLog();
  await loadSummary();
  await loadHistory();
});

async function loadSavedFoodPicker() {
  state.savedFoods = await api(withUser(`/api/saved-foods`));
  const picker = $("#savedFoodPicker");
  picker.innerHTML = `<option value="">— pick a saved food —</option>` +
    state.savedFoods.map(f => `<option value="${f.id}">${escapeHtml(f.name)} (${fmt(f.calories)} kcal / ${fmt(f.serving_size)} ${escapeHtml(f.serving_unit)})</option>`).join("");
}

$("#savedFoodPicker").addEventListener("change", (e) => {
  const id = parseInt(e.target.value);
  const food = state.savedFoods.find(f => f.id === id);
  if (!food) return;
  $("#foodName").value = food.name;
  $("#foodServings").value = "1";
  $("#foodCalories").value = food.calories;
  $("#foodProtein").value = food.protein;
  $("#foodCarbs").value = food.carbs;
  $("#foodFat").value = food.fat;
});

// ---------- saved foods (repeated foods) ----------
state.editingSavedFoodId = null;

async function loadSavedFoodsList() {
  const foods = await api(withUser(`/api/saved-foods`));
  state.savedFoods = foods;
  const list = $("#savedFoodsList");
  if (foods.length === 0) {
    list.innerHTML = `<p class="hint">No saved foods yet. Build one above.</p>`;
    return;
  }
  list.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Serving</th>
          <th>Calories</th>
          <th>P</th>
          <th>C</th>
          <th>F</th>
          <th>Details</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        ${foods.map(f => `
          <tr>
            <td class="col-name">${escapeHtml(f.name)}</td>
            <td>${fmt(f.serving_size)} ${escapeHtml(f.serving_unit)}</td>
            <td>${fmt(f.calories)}</td>
            <td>${fmt(f.protein)}</td>
            <td>${fmt(f.carbs)}</td>
            <td>${fmt(f.fat)}</td>
            <td class="col-details">${detailsSnippetHtml(f.notes, f.name, `sf-${f.id}`)}</td>
            <td class="col-actions">
              <div class="item-actions">
                <button class="use-btn" data-id="${f.id}" title="Add to today's log">＋ log</button>
                <button class="edit-sf-btn" data-id="${f.id}" title="Edit">✎</button>
                <button class="del-sf-btn" data-id="${f.id}" title="Delete">✕</button>
              </div>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  bindEnlargeButtons(list);

  $all(".del-sf-btn").forEach(btn => btn.addEventListener("click", async () => {
    if (!confirm("Delete this saved food?")) return;
    await api(withUser(`/api/saved-foods/${btn.dataset.id}`), { method: "DELETE" });
    if (state.editingSavedFoodId === parseInt(btn.dataset.id)) cancelSavedFoodEdit();
    await loadSavedFoodsList();
    await loadSavedFoodPicker();
  }));

  $all(".edit-sf-btn").forEach(btn => btn.addEventListener("click", () => {
    const food = foods.find(f => f.id === parseInt(btn.dataset.id));
    if (food) startSavedFoodEdit(food);
  }));

  $all(".use-btn").forEach(btn => btn.addEventListener("click", async () => {
    const food = foods.find(f => f.id === parseInt(btn.dataset.id));
    if (!food) return;
    await api(withUser(`/api/logs`), {
      method: "POST",
      body: JSON.stringify({
        date: state.date,
        meal_type: "snack",
        food_name: food.name,
        servings: 1,
        calories: food.calories,
        protein: food.protein,
        carbs: food.carbs,
        fat: food.fat,
        saved_food_id: food.id,
      }),
    });
    alert(`Added "${food.name}" to today's log.`);
    await loadSummary();
    await loadHistory();
  }));
}

function startSavedFoodEdit(food) {
  state.editingSavedFoodId = food.id;
  $("#sfName").value = food.name;
  $("#sfServingSize").value = food.serving_size;
  $("#sfServingUnit").value = food.serving_unit;
  $("#sfCalories").value = food.calories;
  $("#sfProtein").value = food.protein;
  $("#sfCarbs").value = food.carbs;
  $("#sfFat").value = food.fat;
  $("#sfNotes").value = food.notes || "";
  $("#sfFormTitle").textContent = `Editing "${food.name}"`;
  $("#sfSaveBtn").textContent = "Update Food";
  $("#sfCancelEditBtn").hidden = false;
  $("#sfName").scrollIntoView({ behavior: "smooth", block: "center" });
}

function cancelSavedFoodEdit() {
  state.editingSavedFoodId = null;
  ["sfName", "sfCalories", "sfProtein", "sfCarbs", "sfFat", "sfNotes"].forEach(id => $("#" + id).value = "");
  $("#sfServingSize").value = "1";
  $("#sfServingUnit").value = "serving";
  $("#sfFormTitle").textContent = "Build a Repeated Food";
  $("#sfSaveBtn").textContent = "Save Food";
  $("#sfCancelEditBtn").hidden = true;
}

$("#sfCancelEditBtn").addEventListener("click", cancelSavedFoodEdit);

$("#sfSaveBtn").addEventListener("click", async () => {
  const name = $("#sfName").value.trim();
  const calories = parseFloat($("#sfCalories").value);
  if (!name || isNaN(calories)) return alert("Name and calories are required.");
  const body = {
    name,
    serving_size: parseFloat($("#sfServingSize").value) || 1,
    serving_unit: $("#sfServingUnit").value.trim() || "serving",
    calories,
    protein: parseFloat($("#sfProtein").value) || 0,
    carbs: parseFloat($("#sfCarbs").value) || 0,
    fat: parseFloat($("#sfFat").value) || 0,
    notes: $("#sfNotes").value.trim() || null,
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
  await loadSavedFoodsList();
  await loadSavedFoodPicker();
});

// ---------- weight ----------
$("#weightDate").value = state.date;

async function loadWeight() {
  const entries = await api(withUser(`/api/weight`));
  const list = $("#weightList");
  if (entries.length === 0) {
    list.innerHTML = `<p class="hint">No weight entries yet.</p>`;
  } else {
    list.innerHTML = entries.slice().reverse().map(w => `
      <div class="weight-item">
        <div class="log-item-main">
          <span class="name">${fmt(w.weight_kg)} kg</span>
          <span class="meta">${w.date}${w.note ? " · " + escapeHtml(w.note) : ""}</span>
        </div>
        <div class="item-actions">
          <button class="del-w-btn" data-id="${w.id}" title="Delete">✕</button>
        </div>
      </div>
    `).join("");
    $all(".del-w-btn").forEach(btn => btn.addEventListener("click", async () => {
      await api(withUser(`/api/weight/${btn.dataset.id}`), { method: "DELETE" });
      await loadWeight();
      await loadWeightHistory();
    }));
  }
}

$("#addWeightBtn").addEventListener("click", async () => {
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
  await loadWeight();
  await loadWeightHistory();
  await loadSummary();
});

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

$("#saveGoalsBtn").addEventListener("click", async () => {
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
  await loadSummary();
  await loadHistory();
});

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
    $(`#${prefix}Name`).value = est.food_name;
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

function appendChat(role, text) {
  const div = document.createElement("div");
  div.className = `chat-msg ${role}`;
  div.textContent = text;
  $("#chatHistory").appendChild(div);
  $("#chatHistory").scrollTop = $("#chatHistory").scrollHeight;
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
async function refreshDateScoped() {
  await Promise.all([loadSummary(), loadFoodLog(), loadSavedFoodPicker(), loadHistory()]);
}

async function refreshAll() {
  await Promise.all([
    loadSummary(),
    loadFoodLog(),
    loadSavedFoodsList(),
    loadSavedFoodPicker(),
    loadWeight(),
    loadWeightHistory(),
    loadGoals(),
    loadHistory(),
    loadMonthSummary(),
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
// Start page = Food Log with no #/ route (what the app opens on). Back closes
// the details popup first, then returns to Food Log; only Back on Food Log
// with nothing open leaves the app. Deep links (#/admin/...) open later in
// init() and are noticed by BackNav's DOM observer.
BackNav.init({
  atHome: () => $("#tab-foodlog").classList.contains("active") && !location.hash.startsWith("#/"),
  goHome: () => showTab("foodlog"),
  openLayers: () => ($("#detailsModal").hidden ? [] : [$("#detailsModal")]),
  closeLayer: () => closeDetailsModal(),
});

// A link from Home Assistant (the Household Assistant) opens "/<page>/foodlog[/<date>]", "/<page>/dashboard", …
const LINK_TABS = ["foodlog", "dashboard", "savedfoods", "weight", "goals"];
function linkRoute(route) {
  const m = /^\/([a-z]+)(?:\/(\d{4}-\d{2}-\d{2}))?$/.exec(route);
  if (!m || !LINK_TABS.includes(m[1]) || (m[2] && m[1] !== "foodlog")) return null;
  return { tab: m[1], date: m[2] || null };
}
function openLink(r) {
  showTab(r.tab);
  if (r.date && r.date <= todayStr()) setDate(r.date);
}

(async function init() {
  await loadToday();
  await loadMe();
  routeFromHash();   // deep links: #/admin/settings, #/storage, …
  HouseholdDeepLink.start(state.page, linkRoute, openLink);
  const hashLink = linkRoute(location.hash.replace(/^#/, ""));   // "#/foodlog/<date>": the server's redirect of a link
  if (hashLink) { clearRouteHash(); openLink(hashLink); }
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
