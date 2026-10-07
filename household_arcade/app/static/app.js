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

// ---------- tiny DOM helpers (common/ui.js) ----------
const { h, $, clear, mount, lsGet, lsSet } = UI;
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

// ---------- API (common/ui.js) ----------
const errorMessage = UI.errorMessage;
const api = UI.makeApi();

// ---------- toasts & modals (common/ui.js) ----------
function toast(msg, isError = false) { UI.toast(msg, { error: isError, ms: isError ? 6000 : 3500 }); }
function fail(e) { toast(e && e.message ? e.message : String(e), true); }

function openModal(title, content, opts = {}) {
  return UI.openModal(title, content, { modalClass: opts.sheet ? "sheet" : "", onClose: opts.onClose, escape: "stack", focus: "close" });
}
function confirmDialog(title, text, okLabel = "Delete") {
  return UI.confirmDialog(title, text, { okLabel, okClass: "btn-danger", cancelClass: "btn-ghost", focusOk: true,
    modal: { modalClass: "sheet", escape: "stack", focus: "close" } });
}

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
  if (window.Together && !me.disabled) parts.push(Together.homeCards());       // invites and "Waiting for you" (spec §13.1)
  if (me.dailyChallenges && !me.disabled) {                                      // Today's challenges (only while an admin has them on)
    try { parts.push(dailyCard(await api("api/daily"))); } catch (e) { /* off just now, or not reachable: the page is fine without it */ }
  }
  if (!state.games.length) {
    parts.push(h("div", { class: "card empty" }, me.playTime.isChild ? "No games are switched on for you yet." : "Every game is switched off. An admin can turn them on under Admin → App settings."));
  } else {
    const view = gamesView();
    parts.push(h("div", { class: "game-grid view-" + view, id: "gameGrid", dataset: { view } }, state.games.map((g) => gameTile(g, view))));
  }
  mount(root, parts);
  if (window.Together && (state.arg === "join" || state.arg === "decline" || state.arg === "turn")) Together.afterHome(state.arg, state.arg2);   // a phone notification's button
}

// Today's challenges: three games with the same puzzle for everyone, one ranked try each (Practice any time).
function dailyCard(d) {
  if (!d.challenges.length) return null;
  return h("div", { class: "card", id: "dailyCard" },
    h("h3", null, "Today's challenges"),
    h("div", { class: "hint" }, "The same puzzle for everyone today, with one ranked try each. ",
      d.daysPlayed ? `You've played on ${d.daysPlayed} day${d.daysPlayed === 1 ? "" : "s"} this month.` : "Play any one to start your month."),
    h("div", { class: "daily-list" }, d.challenges.map((c) => h("div", { class: "daily-row", dataset: { game: c.game } },
      h("span", { class: "gc-icon", "aria-hidden": "true" }, c.icon),
      h("span", { class: "daily-main" }, h("strong", null, c.name), h("span", { class: "hint" }, c.modeLabel,
        c.played ? (c.score !== null && c.score !== undefined ? ` · your score ${fmtNum(c.score)}` : " · played") : " · one ranked try")),
      h("button", { class: c.played ? "btn-secondary btn-small" : "btn-primary btn-small", type: "button",
        "aria-label": `${c.played ? "Practice" : "Play"} today's ${c.name} challenge`,
        onclick: () => showTab("play", { arg: c.game, arg2: "daily" }) }, c.played ? "Practice" : "Play")))));
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
    h("div", { class: "card" }, h("h3", null, "Last 20 games"), recent),
    window.Together ? Together.againstCard() : null);
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
  if (board.mode !== "daily" && !g.modes.some((m) => m.id === board.mode)) {
    const last = lsGet("arcadeMode:" + g.id);           // the mode last played on this device
    board.mode = g.modes.some((m) => m.id === last) ? last : g.defaultMode;
  }
  const gameSel = h("select", { "aria-label": "Game", id: "boardGame", value: board.game }, state.games.map((x) => h("option", { value: x.id }, x.name)));
  gameSel.addEventListener("change", () => { board.game = gameSel.value; board.mode = null; renderLeaderboard(); });
  if (board.mode === "daily" && !(state.me.dailyChallenges && g.daily)) board.mode = g.defaultMode;
  const modeSel = h("select", { "aria-label": "Mode", id: "boardMode", value: board.mode },
    g.modes.map((m) => h("option", { value: m.id }, m.label)),
    state.me.dailyChallenges && g.daily ? h("option", { value: "daily" }, "Today's challenge") : null);
  modeSel.addEventListener("change", () => { board.mode = modeSel.value; loadBoard(box); });
  const period = segmented([["all", "All time"], ["month", "This month"]], board.period, (v) => { board.period = v; loadBoard(box); }, "Period");
  const box = h("div", { class: "card", id: "boardCard" }, spinner());
  mount(root, pageHead("Leaderboard"), h("div", { class: "filters" }, gameSel, modeSel, period), box);
  loadBoard(box);
}
async function loadBoard(box) {
  mount(box, spinner());
  let data;
  const daily = board.mode === "daily";
  try { data = await api(daily ? `api/daily/board?game=${encodeURIComponent(board.game)}` : `api/leaderboard?game=${encodeURIComponent(board.game)}&mode=${encodeURIComponent(board.mode)}&period=${board.period}`); }
  catch (e) { mount(box, errorCard(e, () => loadBoard(box))); return; }
  if (daily && !data.rows.length) { mount(box, h("div", { class: "empty", id: "boardEmpty" }, "No one has played today's challenge yet — be the first!"), daysBoard(data.days)); return; }
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
      } }, "🗑")) : null))))), daily ? daysBoard(data.days) : null);
}
// Daily challenges: who has played on the most days this month
function daysBoard(days) {
  if (!days || !days.length) return null;
  return h("div", { id: "daysBoard" }, h("h3", null, "Days played this month"),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", null, h("tr", null, h("th", null, "#"), h("th", null, "Player"), h("th", { class: "num" }, "Days"))),
      h("tbody", null, days.map((r) => h("tr", { class: r.me ? "me" : "" }, h("td", null, r.rank), h("td", null, r.name, r.me ? h("span", { class: "badge-you" }, "you") : null), h("td", { class: "num" }, r.days)))))));
}

// =====================================================================
// Settings (everyone)
// =====================================================================
// The page theme (common/theme-boot.js): every Theme menu shows and changes the same choice.
HouseholdTheme.onChange(() => { if (window.Play && Play.themeChanged) Play.themeChanged(); });
function themeSelect(extra = {}) {
  return HouseholdTheme.bindSelect(h("select", { ...extra, "aria-label": "Theme" }));
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
        toggleSwitch(p.receiveNotifications, (v) => save({ receiveNotifications: v }), { label: "Receive notifications" })),
      state.me.assistant ? row("Let the Household Assistant answer for me", "It can tell you the leaderboard and your own bests when you ask it.",
        toggleSwitch(p.assistantOk, (v) => save({ assistantOk: v }), { label: "Let the Household Assistant answer for me" })) : null),
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
// Read-only; shows counts, never the list. Drawn by common/whoami.js (the same in every app).
function whoamiCard(w) {
  return h("div", { class: "card", id: "whoamiCard" }, h("h3", null, "How the app sees you"),
    HouseholdWhoami.panel(w, {
      appName: "Household Arcade",
      adviceTag: "div", adviceStyle: "margin-top:8px",
      onCopy: (text) => copyText(text),
    }));
}

// =====================================================================
// Navigation
// =====================================================================
const RENDERERS = {
  home: renderHome, scores: renderScores, leaderboard: renderLeaderboard, settings: renderSettings,
  play: () => Play.render(state.arg, state.arg2),
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
    HouseholdTheme.setSidebarCollapsed(!HouseholdTheme.sidebarCollapsed());
    syncCollapse();
    if (window.Play && Play.resize) Play.resize();
  });
  syncCollapse();
  HouseholdTheme.bindSelect($("#theme-select"));
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
  HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), state.me && state.me.noAdmin,
    state.me && (state.me.nameSent ? state.me.username : state.me.id), { onOpen: openWhoamiCard, linkId: "noAdminWhoami" });
}

// ---------- links from phone notifications (SPEC §7.3) ----------
// A notification opens the app's sidebar page in Home Assistant with the app's route as a sub-path
// (/local_household_arcade/home/join/<id>; app/panel.py says why not a #/ fragment). Home Assistant hands the rest
// of the path to this page: current versions in a "home-assistant/properties" message (route.path, after the page
// asks with "home-assistant/subscribe-properties"); in any version the top page's own address is readable from
// here (the same origin), so the path after `panel` is taken from it. Once used, the top page's address is put
// back to the bare page, so a reload doesn't open the link again.
function deepLinkHash(panel, routePath) {
  const ok = typeof panel === "string" && /^\/[a-z0-9_]{1,80}$/.test(panel) ? panel : null;
  const fromPath = (p) => {
    if (typeof p !== "string") return null;
    const sub = ok && (p === ok || p.startsWith(ok + "/")) ? p.slice(ok.length) : null;
    if (!sub || sub === "/") return null;
    const r = parseHash("#" + sub.replace(/\/+$/, ""));
    return r ? routeHash(r.tab, r.arg, r.arg2) : null;
  };
  if (routePath !== undefined) return fromPath(routePath);
  try { return window.parent !== window ? fromPath(window.parent.location.pathname) : null; } catch (e) { return null; }
}
function forgetDeepLink(panel) {
  try {
    if (window.parent === window || !panel) return;
    const pp = window.parent.location.pathname;
    if (pp !== panel && pp.startsWith(panel + "/")) window.parent.history.replaceState(window.parent.history.state, "", panel);
  } catch (e) { /* the top page isn't reachable */ }
}
// Kept listening for as long as the page is open: a notification tapped while the app is already open in Home
// Assistant changes the page's route without reloading it.
function listenForHaRoute(panel, handled) {
  if (window.parent === window) return;
  let last = handled || null;
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.source !== window.parent) return;
    const d = e.data;
    if (!d || d.type !== "home-assistant/properties" || !d.route || typeof d.route.path !== "string") return;
    const hash = deepLinkHash(panel, d.route.path);
    if (!hash) { last = null; return; }                  // back on the bare page: the next link counts again
    if (hash === last) return;
    last = hash;
    forgetDeepLink(panel);
    const r = parseHash(hash);
    if (r && state.me) showTab(r.tab, r);
  });
  try { window.parent.postMessage({ type: "home-assistant/subscribe-properties" }, location.origin); } catch (e) { /* older frames */ }
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
  if (window.Together && !state.me.disabled) Together.watch();         // an invite for me pops up wherever I am
  const panel = state.me.panel || null;
  const deep = deepLinkHash(panel);                                   // a phone notification's link (above)
  if (deep) { try { history.replaceState(history.state, "", deep); } catch (e) { /* sandboxed frame */ } forgetDeepLink(panel); }
  listenForHaRoute(panel, deep);
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
    openLayers: () => UI.dialogs().concat(window.Play && Play.isRunning() ? ["game"] : []),
    closeLayer: (layer) => { if (layer === "game") Play.pause(); else layer.close(); },
  });
}

document.addEventListener("DOMContentLoaded", init);
