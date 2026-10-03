"use strict";
/* Household Arcade — the shell: pages, navigation, settings. Plain JS, no build step, no libraries.
   Every fetch path is RELATIVE (no leading slash): Home Assistant's Ingress serves this app under a
   per-session sub-path. Every piece of text from the server or a person is inserted with textContent
   (via h()), never innerHTML. The games themselves are in games/ (window.ArcadeGames); the game page
   is play.js, the admin pages admin.js. */

const state = {
  me: null,            // GET /api/me
  games: [],           // GET /api/games — the games that are on (and allowed, for a child)
  tab: "home",
  arg: null,           // play: game id; admin: sub page
  arg2: null,          // admin users: a person's id
};

const TABS = ["home", "play", "scores", "leaderboard", "settings", "admin"];

// ---------- tiny DOM helper ----------
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
function mount(el, ...kids) {
  clear(el);
  const add = (k) => { if (!k) return; if (Array.isArray(k)) k.forEach(add); else el.appendChild(k); };
  kids.forEach(add);
  return el;
}
function spinner() { return h("div", { class: "spinner" }, "Loading…"); }
function toggleSwitch(checked, onChange, opts = {}) {
  const input = h("input", { type: "checkbox", checked: !!checked, disabled: !!opts.disabled, "aria-label": opts.label || "toggle" });
  input.addEventListener("change", () => onChange(input.checked, input));
  return h("label", { class: "toggle-switch" }, input, h("span", { class: "track" }, h("span", { class: "thumb" })));
}
function segmented(options, current, onChange, label) {
  const wrap = h("div", { class: "segmented", role: "group", "aria-label": label || null });
  options.forEach(([v, l]) => wrap.appendChild(h("button", {
    type: "button", class: v === current ? "active" : "", "aria-pressed": v === current ? "true" : "false",
    onclick: () => { wrap.querySelectorAll("button").forEach((b) => { b.classList.remove("active"); b.setAttribute("aria-pressed", "false"); });
      const me = wrap.children[options.findIndex((o) => o[0] === v)]; me.classList.add("active"); me.setAttribute("aria-pressed", "true"); onChange(v); },
  }, l)));
  return wrap;
}

// ---------- safe storage ----------
function lsGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
function lsSet(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* ignore */ } }

// ---------- API ----------
function errorMessage(detail, status) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) return detail.map((d) => (d && d.msg) || String(d)).join("; ");
  return `Something went wrong (HTTP ${status}).`;
}
async function api(path, opts = {}) {
  const { method = "GET", body, formData, keepalive = false } = opts;
  const url = path.replace(/^\//, "");
  const init = { method, headers: {}, keepalive };
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  else if (formData) init.body = formData;
  let res;
  try { res = await fetch(url, init); }
  catch (e) { throw new Error("Can't reach the app. Check your connection and try again."); }
  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail; } catch (e) { /* not JSON */ }
    const err = new Error(errorMessage(detail, res.status));
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------- toasts & modals ----------
function toast(msg, isError = false) {
  const el = h("div", { class: "toast" + (isError ? " error" : "") }, msg);
  $("#toastRoot").appendChild(el);
  setTimeout(() => el.remove(), isError ? 6000 : 3500);
}
function fail(e) { toast(e && e.message ? e.message : String(e), true); }

let modalStack = [];
function openModal(title, content, opts = {}) {
  const closeBtn = h("button", { class: "icon-btn", "aria-label": "Close", type: "button" }, "✕");
  const modal = h("div", { class: "modal" + (opts.sheet ? " sheet" : ""), role: "dialog", "aria-modal": "true", "aria-label": title },
    h("h3", null, h("span", null, title), closeBtn), content);
  const backdrop = h("div", { class: "modal-backdrop" }, modal);
  let downOnBackdrop = false;
  backdrop.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === backdrop; });
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop && downOnBackdrop) close(); });
  const handle = { close, el: modal };
  function close() {
    backdrop.remove();
    modalStack = modalStack.filter((m) => m !== handle);
    if (opts.onClose) opts.onClose();
  }
  closeBtn.addEventListener("click", close);
  modalStack.push(handle);
  $("#modalRoot").appendChild(backdrop);
  closeBtn.focus();
  return handle;
}
function confirmDialog(title, text, okLabel = "Delete") {
  return new Promise((resolve) => {
    let answered = false;
    const ok = h("button", { class: "btn-danger", type: "button" }, okLabel);
    const cancel = h("button", { class: "btn-ghost", type: "button" }, "Cancel");
    const m = openModal(title, h("div", null, h("p", null, text), h("div", { class: "actions" }, cancel, ok)),
      { sheet: true, onClose: () => { if (!answered) resolve(false); } });
    ok.addEventListener("click", () => { answered = true; m.close(); resolve(true); });
    cancel.addEventListener("click", () => { answered = true; m.close(); resolve(false); });
    ok.focus();
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modalStack.length) modalStack[modalStack.length - 1].close();
});

// ---------- formatting ----------
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtNum(n) { return n === null || n === undefined ? "—" : Number(n).toLocaleString(); }
function fmtStamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return `${sec} s`;
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return s ? `${m} min ${s} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
function fmtLeft(sec) {
  if (sec === null || sec === undefined) return null;
  const m = Math.ceil(sec / 60);
  return sec <= 0 ? "No time left today" : `${m} minute${m === 1 ? "" : "s"} left today`;
}
function isAdmin() { return !!(state.me && state.me.isAdmin); }
function gameName(id) { const g = state.games.find((x) => x.id === id); return g ? g.name : id; }
function pageHead(title, ...right) {
  return h("div", { class: "page-head" }, h("h2", null, title), right.length ? h("div", { class: "list-actions" }, right) : null);
}
function errorCard(e, retry) {
  return h("div", { class: "card fatal" }, h("div", null, e.message || String(e)),
    retry ? h("button", { class: "btn-secondary", type: "button", style: "margin-top:10px", onclick: retry }, "Try again") : null);
}
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Couldn't copy", true));
}

// The registry in games/ (the Games builder's files). A game the server lists but the browser can't
// find is shown as unavailable rather than breaking the page.
const GAME_ALIASES = { brick: ["brick", "brick-breaker", "brickbreaker", "brick_breaker"] };
function registryGame(id) {
  if (!window.ArcadeGames || typeof window.ArcadeGames.get !== "function") return null;
  for (const name of GAME_ALIASES[id] || [id]) {
    try { const g = window.ArcadeGames.get(name); if (g) return g; } catch (e) { /* ignore */ }
  }
  return null;
}

// ---------- play time (children) ----------
function timeChip(pt) {
  if (!pt || !pt.isChild) return null;
  if (!pt.canStart) return h("span", { class: "time-chip out", id: "timeChip" }, "⏸ ", pt.quiet ? `Quiet hours until ${pt.quietUntil}` : "No time left today");
  if (pt.leftSeconds === null) return h("span", { class: "time-chip", id: "timeChip" }, "⏱ No time limit today");
  return h("span", { class: "time-chip" + (pt.leftSeconds <= 300 ? " low" : ""), id: "timeChip" }, "⏱ ", fmtLeft(pt.leftSeconds));
}
async function refreshMe() {
  state.me = await api("api/me");
  applyPersonalLook();
  return state.me;
}
async function refreshGames() {
  state.games = (await api("api/games")).games;
  return state.games;
}

// =====================================================================
// Home
// =====================================================================
async function renderHome() {
  const root = $("#tab-home");
  mount(root, pageHead("Games"), spinner());
  try { await Promise.all([refreshMe(), refreshGames()]); }
  catch (e) { mount(root, pageHead("Games"), errorCard(e, renderHome)); return; }
  const me = state.me;
  const parts = [pageHead("Games", timeChip(me.playTime), state.games.length ? viewPicker() : null)];
  if (me.disabled) {
    parts.push(h("div", { class: "card banner-card danger" }, "An admin has switched you off in Household Arcade, so you can't play just now."));
  } else if (me.playTime.isChild && !me.playTime.canStart) {
    parts.push(h("div", { class: "card banner-card warn", id: "breakBanner" }, h("strong", null, me.playTime.reason)));
  }
  if (me.lowTimeChildren && me.lowTimeChildren.length) parts.push(lowTimeCard(me.lowTimeChildren));
  if (!state.games.length) {
    parts.push(h("div", { class: "card empty" }, me.playTime.isChild ? "No games are switched on for you yet." : "Every game is switched off. An admin can turn them on under Admin → App settings."));
  } else {
    const view = gamesView();
    parts.push(h("div", { class: "game-grid view-" + view, id: "gameGrid", dataset: { view } }, state.games.map((g) => gameTile(g, view))));
  }
  mount(root, parts);
}

// The Games page has three views, remembered on this device: a list, small squares (icon and name) and
// large squares (everything: best, modes, levels, a saved game, how often played, the controls).
const VIEWS = [["list", "☰", "List"], ["small", "▦", "Small squares"], ["large", "◼", "Large squares"]];
function gamesView() {
  let v = null;
  try { v = localStorage.getItem("arcade.gamesView"); } catch (e) { /* storage blocked */ }
  return VIEWS.some(([k]) => k === v) ? v : "large";
}
function viewPicker() {
  const cur = gamesView();
  return h("div", { class: "seg view-picker", role: "group", "aria-label": "How to show the games" },
    VIEWS.map(([k, glyph, label]) => h("button", {
      type: "button", class: k === cur ? "active" : "", title: label, "aria-label": label, "aria-pressed": k === cur ? "true" : "false",
      dataset: { view: k }, onclick: () => { try { localStorage.setItem("arcade.gamesView", k); } catch (e) { /* ignore */ } renderHome(); },
    }, glyph)));
}
function gameTile(g, view) {
  const def = registryGame(g.id), ok = !!def;
  const open = () => { if (ok) showTab("play", { arg: g.id }); else toast(`${g.name} couldn't be loaded in this browser.`, true); };
  const best = g.best !== null && g.best !== undefined ? ["Best ", h("b", null, fmtNum(g.best))] : "Not played yet";
  const savedChip = g.saved ? h("span", { class: "chip on" }, "Saved game") : null;
  const attrs = { type: "button", class: "game-card" + (ok ? "" : " unavailable"), dataset: { game: g.id }, "aria-label": `Play ${g.name}`, onclick: open };
  const icon = h("span", { class: "gc-icon", "aria-hidden": "true" }, g.icon);
  const name = h("span", { class: "gc-name" }, g.name);
  const broken = ok ? null : h("span", { class: "hint warn" }, "Couldn't be loaded");
  if (view === "small") return h("button", attrs, icon, name, broken);
  if (view === "list") {
    return h("button", attrs, icon, h("span", { class: "gc-main" }, name, h("span", { class: "gc-best" }, best)), savedChip,
      h("span", { class: "gc-go", "aria-hidden": "true" }, "›"), broken);
  }
  const facts = [];
  facts.push(`${g.modes.length} mode${g.modes.length === 1 ? "" : "s"}: ${g.modes.map((m) => m.label).join(", ")}`);
  if (g.levels) facts.push(`${g.levels} level${g.levels === 1 ? "" : "s"}${g.levelModes.length ? ` (${g.levelModes.join(", ")})` : ""}`);
  if (g.plays) facts.push(`Played ${fmtNum(g.plays)} time${g.plays === 1 ? "" : "s"}` + (g.lastPlayed ? `, last ${shortDate(g.lastPlayed)}` : ""));
  return h("button", attrs, h("span", { class: "gc-top" }, icon, savedChip), name,
    h("span", { class: "gc-best" }, best),
    h("span", { class: "gc-facts" }, facts.map((f) => h("span", null, f))),
    def && def.help ? h("span", { class: "gc-help" }, def.help) : null, broken);
}
function shortDate(iso) {
  try { return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" }); } catch (e) { return ""; }
}

function lowTimeCard(kids) {
  return h("div", { class: "card", id: "lowTimeCard" }, h("h3", null, "Nearly out of play time"),
    kids.map((k) => h("div", { class: "low-kid" }, h("strong", null, k.name), h("span", { class: "hint" }, fmtLeft(k.leftSeconds)),
      h("button", { class: "btn-secondary btn-small", type: "button", onclick: async (ev) => {
        ev.target.disabled = true;
        try { await api(`api/admin/users/${encodeURIComponent(k.id)}/extra-time`, { method: "POST", body: { minutes: 15 } }); toast(`${k.name}: 15 minutes added`); renderHome(); }
        catch (e) { fail(e); ev.target.disabled = false; }
      } }, "Add 15 minutes"))));
}

// =====================================================================
// My scores
// =====================================================================
async function renderScores() {
  const root = $("#tab-scores");
  mount(root, pageHead("My scores"), spinner());
  let data;
  try { [data] = await Promise.all([api("api/scores/mine"), state.games.length ? null : refreshGames()]); }
  catch (e) { mount(root, pageHead("My scores"), errorCard(e, renderScores)); return; }
  const tiles = h("div", { class: "card-grid" },
    h("div", { class: "stat-card" }, h("div", { class: "value" }, fmtNum(data.totals.games)), h("div", { class: "label" }, "Games saved")),
    h("div", { class: "stat-card" }, h("div", { class: "value" }, fmtDuration(data.totals.secondsThisWeek)), h("div", { class: "label" }, "Played this week")));
  const bests = data.bests.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "data", id: "bestsTable" },
        h("thead", null, h("tr", null, h("th", null, "Game"), h("th", null, "Mode"), h("th", { class: "num" }, "Best"), h("th", { class: "num" }, "Games"))),
        h("tbody", null, data.bests.map((b) => h("tr", null, h("td", null, gameName(b.game)), h("td", null, b.modeLabel),
          h("td", { class: "num" }, h("strong", null, fmtNum(b.score))), h("td", { class: "num" }, b.games))))))
    : h("div", { class: "empty" }, "No scores yet — play a game!");
  const recent = data.recent.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "data", id: "recentTable" },
        h("thead", null, h("tr", null, h("th", null, "When"), h("th", null, "Game"), h("th", { class: "num" }, "Score"), h("th", { class: "num" }, "Level"), h("th", { class: "num" }, "Time"), h("th", null, h("span", { class: "sr-only" }, "Delete")))),
        h("tbody", null, data.recent.map((r) => h("tr", null, h("td", null, fmtStamp(r.at)), h("td", null, `${gameName(r.game)} · ${r.modeLabel}`),
          h("td", { class: "num" }, fmtNum(r.score)), h("td", { class: "num" }, r.level), h("td", { class: "num" }, fmtDuration(r.seconds)),
          h("td", null, h("button", { class: "icon-btn danger", type: "button", title: "Delete this score", "aria-label": "Delete this score", onclick: async () => {
            if (!(await confirmDialog("Delete score", `Delete your ${gameName(r.game)} score of ${fmtNum(r.score)}? This can't be undone.`))) return;
            try { await api(`api/scores/${r.id}`, { method: "DELETE" }); toast("Score deleted"); renderScores(); } catch (e) { fail(e); }
          } }, "🗑")))))))
    : h("div", { class: "empty" }, "Your last 20 games show here.");
  mount(root, pageHead("My scores"), tiles,
    h("div", { class: "card" }, h("h3", null, "Personal bests"), bests),
    h("div", { class: "card" }, h("h3", null, "Last 20 games"), recent));
}

// =====================================================================
// Leaderboard
// =====================================================================
const board = { game: null, mode: null, period: "all" };
async function renderLeaderboard() {
  const root = $("#tab-leaderboard");
  mount(root, pageHead("Leaderboard"), spinner());
  try { await Promise.all([refreshMe(), refreshGames()]); }
  catch (e) { mount(root, pageHead("Leaderboard"), errorCard(e, renderLeaderboard)); return; }
  if (!state.me.leaderboard) {
    mount(root, pageHead("Leaderboard"), h("div", { class: "card empty", id: "boardOff" },
      state.me.leaderboardNames === "hidden" ? "The leaderboard isn't shown for you. Your own bests are under My scores." : "The leaderboard is switched off. Your own bests are under My scores."));
    return;
  }
  if (!state.games.length) { mount(root, pageHead("Leaderboard"), h("div", { class: "card empty" }, "No games are switched on.")); return; }
  if (!state.games.some((g) => g.id === board.game)) board.game = state.games[0].id;
  const g = state.games.find((x) => x.id === board.game);
  if (!g.modes.some((m) => m.id === board.mode)) {
    const last = lsGet("arcadeMode:" + g.id);           // the mode last played on this device
    board.mode = g.modes.some((m) => m.id === last) ? last : g.defaultMode;
  }
  const gameSel = h("select", { "aria-label": "Game", id: "boardGame", value: board.game }, state.games.map((x) => h("option", { value: x.id }, x.name)));
  gameSel.addEventListener("change", () => { board.game = gameSel.value; board.mode = null; renderLeaderboard(); });
  const modeSel = h("select", { "aria-label": "Mode", id: "boardMode", value: board.mode }, g.modes.map((m) => h("option", { value: m.id }, m.label)));
  modeSel.addEventListener("change", () => { board.mode = modeSel.value; loadBoard(box); });
  const period = segmented([["all", "All time"], ["month", "This month"]], board.period, (v) => { board.period = v; loadBoard(box); }, "Period");
  const box = h("div", { class: "card", id: "boardCard" }, spinner());
  mount(root, pageHead("Leaderboard"), h("div", { class: "filters" }, gameSel, modeSel, period), box);
  loadBoard(box);
}
async function loadBoard(box) {
  mount(box, spinner());
  let data;
  try { data = await api(`api/leaderboard?game=${encodeURIComponent(board.game)}&mode=${encodeURIComponent(board.mode)}&period=${board.period}`); }
  catch (e) { mount(box, errorCard(e, () => loadBoard(box))); return; }
  if (!data.rows.length) { mount(box, h("div", { class: "empty" }, board.period === "month" ? "No scores this month yet." : "No scores yet — be the first!")); return; }
  mount(box, h("div", { class: "table-wrap" }, h("table", { class: "data", id: "boardTable" },
    h("thead", null, h("tr", null, h("th", null, "#"), h("th", null, "Player"), h("th", { class: "num" }, "Score"), h("th", { class: "num" }, "Level"), h("th", null, "When"),
      data.canDelete ? h("th", null, h("span", { class: "sr-only" }, "Delete")) : null)),
    h("tbody", null, data.rows.map((r) => h("tr", { class: r.me ? "me" : "" },
      h("td", { class: "rank r" + r.rank }, r.rank === 1 ? "🏆" : r.rank), h("td", null, r.name, r.me ? h("span", { class: "badge-you" }, "you") : null),
      h("td", { class: "num" }, h("strong", null, fmtNum(r.score))), h("td", { class: "num" }, r.level), h("td", null, fmtStamp(r.at)),
      data.canDelete ? h("td", null, h("button", { class: "icon-btn danger", type: "button", title: "Delete this score (admin)", "aria-label": `Delete ${r.name}'s score`, onclick: async () => {
        if (!(await confirmDialog("Delete score", `Delete ${r.name}'s score of ${fmtNum(r.score)}? The leaderboard updates at once.`))) return;
        try { await api(`api/scores/${r.scoreId}`, { method: "DELETE" }); toast("Score deleted"); loadBoard(box); } catch (e) { fail(e); }
      } }, "🗑")) : null))))));
}

// =====================================================================
// Settings (everyone)
// =====================================================================
const THEME_OPTIONS = [["ink", "🌑 Ink"], ["slate", "🌆 Slate"], ["daylight", "☀️ Daylight"]];
function currentTheme() { return document.documentElement.getAttribute("data-theme") || "ink"; }
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  lsSet("theme", theme);
  document.querySelectorAll("select[data-theme-select]").forEach((s) => { s.value = theme; });
  if (window.Play && Play.themeChanged) Play.themeChanged();
}
function themeSelect(extra = {}) {
  const sel = h("select", { ...extra, "data-theme-select": "1", value: currentTheme(), "aria-label": "Theme" },
    THEME_OPTIONS.map(([v, l]) => h("option", { value: v }, l)));
  sel.addEventListener("change", () => applyTheme(sel.value));
  return sel;
}
function lookOptions(withDefault) {
  const me = state.me;
  const defLabel = (me.looks.find((l) => l.id === me.defaultLook) || {}).label || me.defaultLook;
  return [withDefault ? h("option", { value: "" }, `Household default (${defLabel})`) : null,
    me.looks.map((l) => h("option", { value: l.id }, l.label))];
}
function applyPersonalLook() {
  if (!state.me) return;
  document.documentElement.setAttribute("data-look", state.me.prefs.effectiveLook);
}
async function savePrefs(change) {
  const prefs = await api("api/prefs", { method: "PUT", body: change });
  state.me.prefs = prefs;
  applyPersonalLook();
  return prefs;
}

async function renderSettings() {
  const root = $("#tab-settings");
  mount(root, pageHead("Settings"), spinner());
  let who;
  try { [who] = await Promise.all([api("api/whoami"), refreshMe()]); }
  catch (e) { mount(root, pageHead("Settings"), errorCard(e, renderSettings)); return; }
  const p = state.me.prefs;
  const save = (change) => savePrefs(change).then(() => toast("Saved")).catch((e) => { fail(e); renderSettings(); });
  const look = h("select", { id: "prefLook", "aria-label": "Look", value: p.look || "" }, lookOptions(true));
  look.addEventListener("change", () => save({ look: look.value || null }));
  const row = (label, sub, control) => h("div", { class: "setting-row" }, h("div", null, h("div", null, label), sub ? h("div", { class: "sub" }, sub) : null), control);
  const cards = [
    h("div", { class: "card", id: "prefsCard" }, h("h3", null, "Playing"),
      row("Look", "Colours and style of every game. Scores stay comparable: looks never change the rules.", look),
      row("Sound", "Short sound effects. Off by default.", toggleSwitch(p.sound, (v) => save({ sound: v }), { label: "Sound" })),
      row("On-screen controls", "Which hand the arrow pad sits under.",
        segmented([["right", "Right-handed"], ["left", "Left-handed"]], p.handedness, (v) => save({ handedness: v }), "On-screen controls")),
      row("Reduce motion", "No glow pulses, trails or screen shake. Also follows your device's own setting.",
        toggleSwitch(p.reduceMotion, (v) => save({ reduceMotion: v }), { label: "Reduce motion" })),
      row("Receive notifications", "New household records (when an admin has switched record notifications on).",
        toggleSwitch(p.receiveNotifications, (v) => save({ receiveNotifications: v }), { label: "Receive notifications" }))),
  ];
  if (state.me.isChild) cards.push(limitsCard(state.me));
  cards.push(whoamiCard(who));
  cards.push(h("div", { class: "card mobile-only" }, h("h3", null, "Appearance"), themeSelect({ style: "width:100%" })));
  mount(root, pageHead("Settings"), cards);
}

function limitsText(l, pt) {
  const mins = (m) => (m === null || m === undefined ? "No limit" : `${m} minutes`);
  const quiet = (f, t) => (f && t ? `${f} – ${t}` : "None");
  const games = l.allowedGames === null ? "All games" : (l.allowedGames.map(gameName).join(", ") || "None");
  return [
    ["Today", pt.leftSeconds === null ? "No time limit" : fmtLeft(pt.leftSeconds)],
    ["School days", mins(l.minutesSchool)], ["Weekends and holidays", mins(l.minutesWeekend)],
    ["Quiet hours, school nights", quiet(l.quietSchoolFrom, l.quietSchoolTo)],
    ["Quiet hours, weekends", quiet(l.quietWeekendFrom, l.quietWeekendTo)],
    ["Games", games],
  ];
}
function limitsCard(me) {
  return h("div", { class: "card", id: "myLimitsCard" }, h("h3", null, "Your limits"),
    h("div", { class: "kv" }, limitsText(me.limits, me.playTime).map(([k, v]) =>
      h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, k), h("div", { class: "kv-value" }, v)))),
    h("div", { class: "hint", style: "margin-top:8px" }, "Time counts only while a game is running. An admin sets these on Admin → Users."));
}

// "How the app sees you": exactly what Home Assistant sent and whether it matched admin_users.
// Read-only; shows counts, never the list.
function whoamiCard(w) {
  const name = w.nameSent ? w.haUsername : null;
  const row = (label, value, copy) => h("div", { class: "kv-row" },
    h("div", { class: "kv-label" }, label),
    h("div", { class: "kv-value" }, value,
      copy ? h("button", { class: "icon-btn", type: "button", title: "Copy", "aria-label": `Copy ${label}`, onclick: () => copyText(copy) }, "⧉") : null));
  const yesNo = (v) => h("strong", null, v ? "Yes" : "No");
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  let advice;
  if (w.isAdmin) advice = "You are an administrator.";
  else if (w.adminEntries === 0) {
    advice = h("span", null, "The ", h("code", null, "admin_users"), " list is ", h("strong", null, "empty"),
      " in the running app. If you have filled it in, the app hasn't picked it up yet: changes on the app's Configuration tab only take effect after it is ",
      h("strong", null, "restarted"), " (Settings → Apps → Household Arcade → Information → Restart).");
  } else {
    advice = h("span", null, `Neither your user name nor your user id above matches any of the ${plural(w.adminEntries, "name")} in the `,
      h("code", null, "admin_users"), " list. Add ", h("strong", null, name || w.haUserId), " (or ", h("strong", null, w.haUserId),
      ") exactly as shown, save, and ", h("strong", null, "restart"), " the app — the list is only read when the app starts. Upper and lower case don't matter.");
  }
  return h("div", { class: "card", id: "whoamiCard" }, h("h3", null, "How the app sees you"),
    h("div", { class: "kv" },
      row("User name (sent by Home Assistant)", name || "not sent", name),
      row("User id (sent by Home Assistant)", h("code", null, w.haUserId), w.haUserId),
      row("Display name (not used for matching)", w.haDisplayName),
      row("Administrator in this app", yesNo(w.isAdmin)),
      row("Names in the app's admin_users", String(w.adminEntries)),
      row("Phone linked for notifications", yesNo(w.notifyLinked))),
    h("div", { class: "hint", style: "margin-top:8px" }, advice));
}

// =====================================================================
// Navigation
// =====================================================================
const RENDERERS = {
  home: renderHome, scores: renderScores, leaderboard: renderLeaderboard, settings: renderSettings,
  play: () => Play.render(state.arg),
  admin: () => Admin.render(state.arg, state.arg2),
};

function parseHash(hash) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [tab, arg, arg2] = parts;
  if (!TABS.includes(tab)) return null;
  return { tab, arg: arg || null, arg2: arg2 || null };
}
function routeHash(tab, arg, arg2) {
  return "#/" + [tab, arg, arg2].filter(Boolean).map(encodeURIComponent).join("/");
}

function showTab(tab, opts = {}) {
  if (!TABS.includes(tab)) tab = "home";
  const arg = opts.arg || null, arg2 = opts.arg2 || null;
  if (state.tab === "play" && (tab !== "play" || arg !== state.arg) && window.Play) Play.leave();
  state.tab = tab; state.arg = arg; state.arg2 = arg2;
  const want = routeHash(tab, arg, arg2);
  if (location.hash !== want) {
    try { history.replaceState(history.state, "", want); } catch (e) { /* sandboxed frame */ }
  }
  document.body.dataset.route = tab;
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === (tab === "play" ? "home" : tab)));
  TABS.forEach((t) => $("#tab-" + t).classList.toggle("active", t === tab));
  RENDERERS[tab]();
}

window.addEventListener("hashchange", () => {
  if (!state.me) return;
  const r = parseHash(location.hash);
  if (r && routeHash(r.tab, r.arg, r.arg2) !== routeHash(state.tab, state.arg, state.arg2)) showTab(r.tab, r);
});

function wireChrome() {
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  const collapse = $("#sidebarCollapseBtn");
  const syncCollapse = () => {
    const collapsed = document.documentElement.getAttribute("data-sidebar") === "collapsed";
    collapse.textContent = collapsed ? "›" : "‹";
    collapse.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
    collapse.setAttribute("aria-label", collapse.title);
  };
  collapse.addEventListener("click", () => {
    const collapsed = document.documentElement.getAttribute("data-sidebar") === "collapsed";
    if (collapsed) document.documentElement.removeAttribute("data-sidebar"); else document.documentElement.setAttribute("data-sidebar", "collapsed");
    lsSet("sidebarCollapsed", collapsed ? "0" : "1");
    syncCollapse();
    if (window.Play && Play.resize) Play.resize();
  });
  syncCollapse();
  const themeSel = $("#theme-select");
  themeSel.dataset.themeSelect = "1";
  themeSel.value = currentTheme();
  themeSel.addEventListener("change", () => applyTheme(themeSel.value));
}

// A fresh install has nobody in admin_users, so nobody can open Admin. Every page says how to fix
// that (for everyone); nobody is ever auto-promoted.
function openWhoamiCard() {
  showTab("settings");
  const t0 = Date.now();
  (function look() {
    const c = $("#whoamiCard");
    if (c) c.scrollIntoView({ behavior: "smooth", block: "start" });
    else if (Date.now() - t0 < 3000) setTimeout(look, 100);
  })();
}
function syncNoAdminBanner() {
  const banner = $("#noAdminBanner");
  if (!state.me || !state.me.noAdmins) { banner.hidden = true; clear(banner); return; }
  const who = state.me.nameSent ? state.me.username : state.me.id;
  banner.hidden = false;
  mount(banner, h("span", null, "No admin yet — add your Home Assistant user name (", h("strong", null, who),
    ") to ", h("code", null, "admin_users"), " in the app's Configuration tab, save, and restart the app. "),
    h("button", { class: "link-btn", type: "button", id: "noAdminWhoami", onclick: openWhoamiCard }, "How the app sees you"));
}

async function init() {
  wireChrome();
  try { await refreshMe(); }
  catch (e) {
    const box = $("#fatal");
    box.hidden = false;
    mount(box, h("h3", null, "Can't open Household Arcade"), h("div", null, e.message));
    return;
  }
  const chip = $("#sidebarUser");
  chip.textContent = state.me.name + (state.me.isAdmin ? " · admin" : "");
  chip.title = "How the app sees you";
  chip.addEventListener("click", openWhoamiCard);
  document.querySelectorAll(".admin-only").forEach((el) => { el.hidden = !state.me.isAdmin; });
  syncNoAdminBanner();
  try { await refreshGames(); } catch (e) { fail(e); }
  const start = parseHash(location.hash);
  showTab(start ? start.tab : "home", start || {});
  initBackNav();
}

// The back gesture (backnav.js): a running game pauses first; then Back leaves the game (or closes a
// dialog / goes back to Games); only Back on Games with nothing open leaves the app.
function initBackNav() {
  if (!window.BackNav) return;
  BackNav.init({
    atHome: () => state.tab === "home",
    goHome: () => showTab("home"),
    openLayers: () => modalStack.slice().concat(window.Play && Play.isRunning() ? ["game"] : []),
    closeLayer: (layer) => { if (layer === "game") Play.pause(); else layer.close(); },
  });
}

document.addEventListener("DOMContentLoaded", init);
