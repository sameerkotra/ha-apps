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
function spinner() { return UI.loading(); }
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
function errorCard(e, retry) { return UI.errorBox(e, retry); }
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
// What the Games page shows before a game's files are loaded (they load when it is opened): its help line, from
// games/manifest.js. Null for a game this page doesn't have.
function gameInfo(id) {
  const loaded = registryGame(id);
  if (loaded) return loaded;
  if (!window.ArcadeGames || typeof window.ArcadeGames.info !== "function") return null;
  for (const name of GAME_ALIASES[id] || [id]) {
    try { const g = window.ArcadeGames.info(name); if (g) return g; } catch (e) { /* ignore */ }
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
  const r = await api("api/games");
  state.games = r.games;
  state.favourites = r.favourites || [];
  return state.games;
}

// =====================================================================
// Home (the Games page)
// =====================================================================
// Read once on entering the page (me, the games and today's challenges together, behind one spinner). The ★, the
// view picker, the search and the tag chips redraw from what is already here: no spinner, nothing fetched.
const home = { daily: null };
async function renderHome() {
  const root = $("#tab-home");
  mount(root, pageHead("Games"), spinner());
  try {
    const got = await Promise.all([refreshMe(), refreshGames(), api("api/daily").catch(() => null)]);
    home.daily = got[2];
  } catch (e) { mount(root, pageHead("Games"), errorCard(e, renderHome)); return; }
  if (state.tab !== "home") return;
  drawHome();
  if (window.Together && (state.arg === "join" || state.arg === "decline" || state.arg === "turn")) Together.afterHome(state.arg, state.arg2);   // a phone notification's button
}
function drawHome() {
  const root = $("#tab-home");
  const me = state.me;
  const parts = [pageHead("Games", timeChip(me.playTime), state.games.length ? viewPicker() : null)];
  // search and tag chips straight under the page's title (they stay at the top while scrolling on a phone)
  if (state.games.length > 1) parts.push(lobbyTools());
  if (me.disabled) {
    parts.push(h("div", { class: "card banner-card danger" }, "An admin has switched you off in Household Arcade, so you can't play just now."));
  } else if (me.playTime.isChild && !me.playTime.canStart) {
    parts.push(h("div", { class: "card banner-card warn", id: "breakBanner" }, h("strong", null, me.playTime.reason)));
  }
  if (me.lowTimeChildren && me.lowTimeChildren.length) parts.push(lowTimeCard(me.lowTimeChildren));
  if (!state.games.length) {
    parts.push(h("div", { class: "card empty" }, me.playTime.isChild ? "No games are switched on for you yet." : "Every game is switched off. An admin can turn them on under Admin → App settings."));
    mount(root, parts);
    return;
  }
  if (!me.disabled) {
    // Continue: saved games and the turn-by-turn matches waiting for my move (those come from Play together)
    const cont = continueSection();
    parts.push(cont);
    if (window.Together) parts.push(Together.homeCards({ onMyTurns: (list) => fillTurns(cont, list) }));      // invites and matches (spec §13.1)
    if (me.dailyChallenges && home.daily) parts.push(dailyCard(home.daily));     // Today's challenges (only while an admin has them on)
  }
  parts.push(recentSection());
  parts.push(h("div", { id: "gameLists" }, gameLists()));
  mount(root, parts);
  applySearch();
}

// Saved games, then (filled in by Play together) the matches where it's my move.
function continueSection() {
  const saved = state.games.filter((g) => g.saved);
  const turns = h("div", { class: "lobby-turns", id: "continueTurns" });
  const sec = h("section", { class: "lobby-section lobby-extra", id: "continueSection", "aria-labelledby": "continueHead", hidden: !saved.length },
    h("h3", { class: "games-section", id: "continueHead" }, "Continue"),
    h("div", { class: "lobby-row", id: "continueRow" },
      saved.map((g) => miniTile(g.icon, g.name, `Saved · ${g.saved.modeLabel} · score ${fmtNum(g.saved.score)}`, () => showTab("play", { arg: g.id }), { game: g.id, cls: "saved" })),
      turns));
  return sec;
}
function fillTurns(sec, list) {
  const box = sec.querySelector("#continueTurns");
  if (!box) return;
  mount(box, list.map((m) => {
    const others = (m.players || []).filter((p) => !p.you).map((p) => Together.first(p.name));
    return miniTile(m.icon, m.gameName, `Your move · with ${others.join(", ")}`, () => Together.openMatch(m), { cls: "my-turn", match: m.id });
  }));
  sec.hidden = !sec.querySelector(".mini-tile");
}
// The few games played last (only worth a row when there are many games)
function recentSection() {
  const played = state.games.filter((g) => g.lastPlayed).sort((a, b) => String(b.lastPlayed).localeCompare(String(a.lastPlayed))).slice(0, 6);
  if (state.games.length < 10 || !played.length) return null;
  return h("section", { class: "lobby-section lobby-extra", id: "recentSection", "aria-labelledby": "recentHead" },
    h("h3", { class: "games-section", id: "recentHead" }, "Recently played"),
    h("div", { class: "lobby-row", id: "recentRow" }, played.map((g) => miniTile(g.icon, g.name, g.lastPlayed ? shortDate(g.lastPlayed) : "",
      () => showTab("play", { arg: g.id }), { game: g.id }))));
}
function miniTile(icon, name, sub, onclick, o = {}) {
  return h("button", { type: "button", class: "mini-tile" + (o.cls ? " " + o.cls : ""), dataset: o.game ? { game: o.game } : o.match ? { match: o.match } : null,
    onclick, "aria-label": `${name}${sub ? ", " + sub : ""}` },
    h("span", { class: "gc-icon", "aria-hidden": "true" }, icon),
    h("span", { class: "mini-text" }, h("span", { class: "mini-name" }, name), sub ? h("span", { class: "mini-sub" }, sub) : null));
}
// Favourites first (in the order marked), then every other game (spec §9)
function gameLists() {
  const view = gamesView();
  const favs = (state.favourites || []).map((id) => state.games.find((g) => g.id === id)).filter(Boolean);
  const rest = state.games.filter((g) => !g.favourite);
  return [
    favs.length ? h("h3", { class: "games-section", id: "favHead" }, "★ Favourites") : null,
    favs.length ? h("div", { class: "game-grid view-" + view, id: "favGrid", dataset: { view } }, favs.map((g) => gameTile(g, view))) : null,
    rest.length ? h("h3", { class: "games-section", id: "allHead" }, `${favs.length ? "All other games" : "All games"} · ${rest.length}`) : null,
    rest.length ? h("div", { class: "game-grid view-" + view, id: "gameGrid", dataset: { view } }, rest.map((g, i) => gameTile(g, view, !favs.length && i === 0))) : null,
  ];
}
// The lists again (a ★ or a view changed): the focus goes back to the same game's star when it had it.
function redrawLists(focusFav) {
  const box = document.getElementById("gameLists");
  if (!box) return;
  mount(box, gameLists());
  applySearch();
  if (focusFav) { const b = box.querySelector(`.fav-btn[data-fav="${typeof CSS !== "undefined" && CSS.escape ? CSS.escape(focusFav) : focusFav}"]`); if (b) b.focus({ preventScroll: false }); }
}

// The Games page search and tag chips (spec §9): filter the games in the browser as you type or tap, in every view.
// While they filter, Continue and Recently played step aside; the cards above the list stay. What was typed and the
// chip are kept for this visit only (sessionStorage), never sent anywhere.
const SEARCH_KEY = "arcade.gamesSearch";
const TAG_KEY = "arcade.gamesTag";
const TAG_ORDER = ["arcade", "puzzle", "word", "board", "cards", "dice", "two players", "turn by turn", "levels", "gentle"];
function sessGet(k) { try { return sessionStorage.getItem(k) || ""; } catch (e) { return ""; } }
function sessSet(k, v) { try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch (e) { /* storage blocked */ } }
function searchText() { return sessGet(SEARCH_KEY); }
function keepSearch(v) { sessSet(SEARCH_KEY, v); }
function lobbyTools() {
  return h("div", { class: "lobby-tools", id: "lobbyTools" }, searchBox(), tagChips());
}
function searchBox() {
  const input = h("input", { type: "search", id: "gameSearch", class: "search-input", placeholder: `Search ${state.games.length} games`, autocomplete: "off",
    spellcheck: "false", "aria-describedby": "searchCount", value: searchText() });
  input.addEventListener("input", () => { keepSearch(input.value); applySearch(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      if (input.value) { input.value = ""; keepSearch(""); applySearch(); } else input.blur();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const shown = visibleGames();
      if (shown.length === 1) showTab("play", { arg: shown[0] });
    }
  });
  return h("div", { class: "game-search", role: "search" },
    h("label", { class: "sr-only", for: "gameSearch" }, "Search games"),
    h("span", { class: "search-icon", "aria-hidden": "true" }, "🔍"), input,
    h("div", { class: "hint search-count", id: "searchCount", "aria-live": "polite" }));
}
function tagChips() {
  const have = new Set();
  state.games.forEach((g) => (g.tags || []).forEach((t) => have.add(t)));
  const tags = TAG_ORDER.filter((t) => have.has(t)).concat([...have].filter((t) => TAG_ORDER.indexOf(t) < 0));
  if (!tags.length) return null;
  if (sessGet(TAG_KEY) && !have.has(sessGet(TAG_KEY))) sessSet(TAG_KEY, "");
  const cur = sessGet(TAG_KEY);
  const row = h("div", { class: "tag-chips", id: "tagChips", role: "group", "aria-label": "Show games tagged" });
  const chip = (value, label) => h("button", { type: "button", class: "chip-filter" + (value === cur ? " on" : ""), "aria-pressed": value === cur ? "true" : "false",
    dataset: { tag: value }, onclick: () => {
      const next = value && sessGet(TAG_KEY) === value ? "" : value;
      sessSet(TAG_KEY, next);
      row.querySelectorAll(".chip-filter").forEach((b) => { const on = b.dataset.tag === next; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
      applySearch();
    } }, label);
  mount(row, chip("", "All"), tags.map((t) => chip(t, t.charAt(0).toUpperCase() + t.slice(1))));
  return row;
}
function visibleGames() {
  const seen = [];
  for (const c of document.querySelectorAll("#tab-home .game-cell")) if (!c.hidden && seen.indexOf(c.dataset.game) < 0) seen.push(c.dataset.game);
  return seen;
}
function applySearch() {
  const input = document.getElementById("gameSearch");
  const count = document.getElementById("searchCount");
  const q = input ? input.value : "";
  const tag = document.getElementById("tagChips") ? sessGet(TAG_KEY) : "";
  const typed = !!(window.GameSearch && GameSearch.words(q).length);
  const filtering = typed || !!tag;
  const results = {};
  for (const g of state.games) {
    const r = typed ? GameSearch.match(g, q) : { ok: true, via: null };
    results[g.id] = tag && (g.tags || []).indexOf(tag) < 0 ? { ok: false, via: null } : r;
  }
  const home_ = document.getElementById("tab-home");
  if (home_) home_.classList.toggle("filtering", filtering);
  for (const cell of document.querySelectorAll("#tab-home .game-cell")) {
    const r = results[cell.dataset.game] || { ok: true, via: null };
    cell.hidden = !r.ok;
    const card = cell.querySelector(".game-card");
    let via = cell.querySelector(".gc-via");
    if (card && cell.closest(".view-large")) {
      if (r.via && !via) { via = h("span", { class: "gc-via" }); const name = card.querySelector(".gc-name"); if (name) name.after(via); }
      if (via) { via.textContent = r.via || ""; via.hidden = !r.via; }
    }
  }
  for (const id of ["favGrid", "gameGrid"]) {
    const g = document.getElementById(id);
    if (g) g.hidden = ![...g.querySelectorAll(".game-cell")].some((c) => !c.hidden);
  }
  const favHead = document.getElementById("favHead"), fav = document.getElementById("favGrid");
  if (favHead && fav) favHead.hidden = fav.hidden;
  const allHead = document.getElementById("allHead"), grid = document.getElementById("gameGrid");
  if (allHead && grid) allHead.hidden = grid.hidden;
  if (count) {
    const n = Object.values(results).filter((r) => r.ok).length;
    const clearAll = () => { if (input) input.value = ""; keepSearch(""); sessSet(TAG_KEY, ""); const chips = document.getElementById("tagChips");
      if (chips) chips.querySelectorAll(".chip-filter").forEach((b) => { const on = b.dataset.tag === ""; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
      applySearch(); if (input) input.focus(); };
    if (!filtering) mount(count);
    else if (n) mount(count, h("span", null, `${n} of ${state.games.length} games`));
    else mount(count, h("span", null, typed ? `No game matches “${q.trim()}”${tag ? ` in ${tag}` : ""}` : `No ${tag} games`),
      h("button", { class: "btn-ghost btn-small", type: "button", id: "searchClear", onclick: clearAll }, "Clear"));
  }
}
// "/" puts the cursor in the search box when no other field has it
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || state.tab !== "home") return;
  const a = document.activeElement;
  if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT" || a.isContentEditable)) return;
  if (document.querySelector("dialog[open], .modal-backdrop")) return;
  const input = document.getElementById("gameSearch");
  if (input) { e.preventDefault(); input.focus(); }
});

// Today's challenges: three games with the same puzzle for everyone, one ranked try each (Practice any time). All
// played: one line with the total and the way to today's board.
function dailyCard(d) {
  if (!d || !d.challenges || !d.challenges.length) return null;
  const n = d.challenges.length, done = d.challenges.filter((c) => c.played).length;
  if (done === n) {
    const total = d.challenges.reduce((a, c) => a + (Number(c.score) || 0), 0);
    return h("div", { class: "card daily-strip done", id: "dailyCard" },
      h("span", null, h("strong", null, "Today's challenges"), ` · ${done} of ${n} done · total ${fmtNum(total)}`),
      state.me.leaderboard ? h("button", { class: "link-btn", type: "button", id: "dailyBoardLink",
        onclick: () => showTab("leaderboard", { arg: d.challenges[0].game, arg2: "daily" }) }, "See today's board ›") : null);
  }
  const firstOpen = d.challenges.find((c) => !c.played);
  return h("div", { class: "card daily-strip", id: "dailyCard" },
    h("div", { class: "daily-head" }, h("strong", null, "Today's challenges"),
      h("span", { class: "hint" }, `${done} of ${n} done · the same puzzle for everyone, one ranked try each`,
        d.daysPlayed ? ` · ${d.daysPlayed} day${d.daysPlayed === 1 ? "" : "s"} this month` : "")),
    h("div", { class: "daily-list" }, d.challenges.map((c) => h("div", { class: "daily-row", dataset: { game: c.game } },
      h("span", { class: "gc-icon", "aria-hidden": "true" }, c.icon),
      h("span", { class: "daily-main" }, h("strong", null, c.name), h("span", { class: "hint" }, c.modeLabel,
        c.played ? (c.score !== null && c.score !== undefined ? ` · your score ${fmtNum(c.score)}` : " · played") : "")),
      h("button", { class: c === firstOpen ? "btn-primary btn-small" : "btn-secondary btn-small", type: "button",
        "aria-label": `${c.played ? "Practice" : "Play"} today's ${c.name} challenge`,
        onclick: () => showTab("play", { arg: c.game, arg2: "daily" }) }, c.played ? "Practice" : "Play")))));
}

// The Games page has three views, remembered on this device: a list, small squares (icon and name) and
// large squares (everything: best, modes, levels, a saved game, how often played, the controls). A phone starts
// with small squares.
const VIEWS = [["list", "☰", "List"], ["small", "▦", "Small squares"], ["large", "◼", "Large squares"]];
function gamesView() {
  let v = null;
  try { v = localStorage.getItem("arcade.gamesView"); } catch (e) { /* storage blocked */ }
  if (VIEWS.some(([k]) => k === v)) return v;
  return window.matchMedia && window.matchMedia("(max-width: 760px)").matches ? "small" : "large";
}
function viewPicker() {
  const cur = gamesView();
  const wrap = h("div", { class: "seg view-picker", role: "group", "aria-label": "How to show the games" });
  mount(wrap, VIEWS.map(([k, glyph, label]) => h("button", {
    type: "button", class: k === cur ? "active" : "", title: label, "aria-label": label, "aria-pressed": k === cur ? "true" : "false",
    dataset: { view: k }, onclick: () => {
      try { localStorage.setItem("arcade.gamesView", k); } catch (e) { /* ignore */ }
      wrap.querySelectorAll("button").forEach((b) => { const on = b.dataset.view === k; b.classList.toggle("active", on); b.setAttribute("aria-pressed", on ? "true" : "false"); });
      redrawLists();
    },
  }, glyph)));
  return wrap;
}
// The star on a game card: mark or unmark a favourite (spec §9) without opening the game.
function favButton(g, tip) {
  const on = !!g.favourite;
  return h("button", { type: "button", class: "fav-btn" + (on ? " on" : ""), dataset: { fav: g.id },
    title: on ? `Remove ${g.name} from favourites` : (tip ? "Mark favourites to keep them at the top" : `Add ${g.name} to favourites`),
    "aria-label": on ? `Remove ${g.name} from favourites` : `Add ${g.name} to favourites`, "aria-pressed": on ? "true" : "false",
    onclick: (ev) => { ev.stopPropagation(); toggleFavourite(g); } }, on ? "★" : "☆");
}
async function toggleFavourite(g) {
  try {
    const r = await api(`api/favourites/${encodeURIComponent(g.id)}`, { method: "PUT", body: { on: !g.favourite } });
    g.favourite = !g.favourite;
    state.favourites = r.favourites;
    for (const x of state.games) x.favourite = r.favourites.indexOf(x.id) >= 0;
    if (state.tab === "home") redrawLists(g.id); else if (window.Play && Play.favouriteChanged) Play.favouriteChanged();
  } catch (e) { fail(e); }
}
// "Next: level 9 of 30" for the game's default mode when it carries on from the next level (spec §14)
function progressText(g, mode) {
  const p = g.progress && g.progress[mode || g.defaultMode];
  if (!p || !p.total) return null;
  if (p.cleared >= p.total) return `All ${p.total} levels cleared ✓`;
  return p.cleared ? `Next: level ${p.next} of ${p.total}` : null;
}
function gameTile(g, view, tip) {
  return h("div", { class: "game-cell", dataset: { game: g.id } }, gameCard(g, view), favButton(g, tip));
}
function gameCard(g, view) {
  const def = gameInfo(g.id), ok = !!def;
  const open = () => { if (ok) showTab("play", { arg: g.id }); else toast(`${g.name} couldn't be loaded in this browser.`, true); };
  const best = g.best !== null && g.best !== undefined ? ["Best ", h("b", null, fmtNum(g.best))] : "Not played yet";
  const savedChip = g.saved ? h("span", { class: "chip on" }, "Saved game") : null;
  const attrs = { type: "button", class: "game-card" + (ok ? "" : " unavailable"), dataset: { game: g.id }, "aria-label": `Play ${g.name}${g.saved ? " (saved game)" : ""}`, onclick: open };
  const icon = h("span", { class: "gc-icon", "aria-hidden": "true" }, g.icon);
  const name = h("span", { class: "gc-name" }, g.name);
  const broken = ok ? null : h("span", { class: "hint warn" }, "Couldn't be loaded");
  if (view === "small") return h("button", attrs, g.saved ? h("span", { class: "gc-saved-dot", title: "Saved game", "aria-hidden": "true" }, "▶") : null, icon, name, broken);
  if (view === "list") {
    const next = progressText(g);
    return h("button", attrs, icon, h("span", { class: "gc-main" }, name, h("span", { class: "gc-best" }, best, next ? ` · ${next}` : "")), savedChip,
      h("span", { class: "gc-go", "aria-hidden": "true" }, "›"), broken);
  }
  const facts = [];
  facts.push(`${g.modes.length} mode${g.modes.length === 1 ? "" : "s"}: ${g.modes.map((m) => m.label).join(", ")}`);
  if (g.levels) facts.push(`${g.levels} level${g.levels === 1 ? "" : "s"}${g.levelModes.length ? ` (${g.levelModes.join(", ")})` : ""}`);
  if (g.plays) facts.push(`Played ${fmtNum(g.plays)} time${g.plays === 1 ? "" : "s"}` + (g.lastPlayed ? `, last ${shortDate(g.lastPlayed)}` : ""));
  const prog = Object.keys(g.progress || {}).map((m) => {
    const t = progressText(g, m);
    return t ? (Object.keys(g.progress).length > 1 || m !== g.defaultMode ? `${(g.modes.find((x) => x.id === m) || {}).label || m}: ${t}` : t) : null;
  }).filter(Boolean);
  for (const t of prog) facts.push(t);
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
  let data, prog = { progress: [] }, daily = null;
  try {
    [data, prog, daily] = await Promise.all([api("api/scores/mine"), api("api/progress"),
      state.me && state.me.dailyChallenges ? api("api/daily").catch(() => null) : null, state.games.length ? null : refreshGames()]);
  } catch (e) { mount(root, pageHead("My scores"), errorCard(e, renderScores)); return; }
  // the figures at the top: personal bests, household records held, daily-challenge days this month, the game played most
  const most = state.games.filter((g) => g.plays).sort((a, b) => b.plays - a.plays)[0];
  const tile = (value, label, id) => h("div", { class: "stat-card", id }, h("div", { class: "value" }, value), h("div", { class: "label" }, label));
  const tiles = h("div", { class: "card-grid", id: "scoreTiles" },
    tile(fmtNum(data.bests.length), "Personal bests", "tileBests"),
    tile(fmtNum(data.totals.recordsHeld || 0), "Household records", "tileRecords"),
    daily ? tile(fmtNum(daily.daysPlayed || 0), "Daily days this month", "tileDaily") : null,
    most ? tile(most.icon, `Most played: ${most.name}`, "tileMost") : null,
    tile(fmtNum(data.totals.games), "Games saved", "tileGames"),
    tile(fmtDuration(data.totals.secondsThisWeek), "Played this week", "tileWeek"));
  // personal bests grouped by game (the game's name once, its modes under it), sorted by name
  const byGame = new Map();
  data.bests.forEach((b) => { if (!byGame.has(b.game)) byGame.set(b.game, []); byGame.get(b.game).push(b); });
  const groups = [...byGame.entries()].sort((a, b) => gameName(a[0]).localeCompare(gameName(b[0])));
  const bests = groups.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "data", id: "bestsTable" },
        h("thead", null, h("tr", null, h("th", null, "Game"), h("th", null, "Mode"), h("th", { class: "num" }, "Best"), h("th", { class: "num" }, "Games"))),
        groups.map(([game, rows]) => h("tbody", { class: "best-group", dataset: { game } }, rows.map((b, i) => h("tr", null,
          i === 0 ? h("th", { scope: "rowgroup", rowspan: String(rows.length), class: "best-game" }, gameName(game)) : null,
          h("td", null, b.modeLabel),
          h("td", { class: "num" }, h("strong", null, fmtNum(b.score)), b.record ? h("span", { class: "record-mark", title: "Household record", "aria-label": "household record" }, " 🏆") : null),
          h("td", { class: "num" }, b.games)))))))
    : UI.empty("No scores yet", "Your best score in each game and mode shows here.", { label: "Play a game", onclick: () => showTab("home") });
  const recent = data.recent.length
    ? h("div", { class: "table-wrap" }, h("table", { class: "data", id: "recentTable" },
        h("thead", null, h("tr", null, h("th", null, "When"), h("th", null, "Game"), h("th", { class: "num" }, "Score"), h("th", { class: "num" }, "Level"), h("th", { class: "num" }, "Time"), h("th", null, h("span", { class: "sr-only" }, "Delete")))),
        h("tbody", null, data.recent.map((r) => h("tr", null, h("td", { class: "r-when" }, fmtStamp(r.at)),
          h("td", { class: "r-game" }, `${gameName(r.game)} · ${r.modeLabel}${r.startLevel > 1 ? ` · from level ${r.startLevel}` : ""}`),
          h("td", { class: "num r-score" }, fmtNum(r.score)), h("td", { class: "num r-level" }, h("span", { class: "r-label" }, "level "), r.level),
          h("td", { class: "num r-time" }, fmtDuration(r.seconds)),
          h("td", { class: "r-del" }, h("button", { class: "icon-btn danger", type: "button", title: "Delete this score", "aria-label": "Delete this score", onclick: async () => {
            if (!(await confirmDialog("Delete score", `Delete your ${gameName(r.game)} score of ${fmtNum(r.score)}? This can't be undone.`))) return;
            try { await api(`api/scores/${r.id}`, { method: "DELETE" }); toast("Score deleted"); renderScores(); } catch (e) { fail(e); }
          } }, "🗑")))))))
    : h("div", { class: "empty" }, "Your last 20 games show here.");
  mount(root, pageHead("My scores"), tiles,
    h("div", { class: "card", id: "bestsCard" }, h("h3", null, "Personal bests"), bests),
    progressCard(prog.progress || []),
    h("div", { class: "card", id: "recentCard" }, h("h3", null, "Last 20 games"), recent),
    window.Together ? Together.againstCard() : null);
}

// Level progress (spec §14): the games that carry on from the next level, with Start over.
function progressCard(rows) {
  if (!rows.length) return null;
  return h("div", { class: "card", id: "progressCard" }, h("h3", null, "Level progress"),
    h("div", { class: "hint" }, "These games start at your next level. Bests and the leaderboard count only games played from level 1."),
    h("div", { class: "table-wrap" }, h("table", { class: "data", id: "progressTable" },
      h("thead", null, h("tr", null, h("th", null, "Game"), h("th", null, "Mode"), h("th", { class: "num" }, "Cleared"), h("th", null, "Next"), h("th", null, h("span", { class: "sr-only" }, "Start over")))),
      h("tbody", null, rows.map((p) => h("tr", null, h("td", null, `${p.icon} ${p.name}`), h("td", null, p.modeLabel),
        h("td", { class: "num" }, p.total ? `${p.cleared} of ${p.total}` : String(p.cleared)),
        h("td", null, p.total && p.cleared >= p.total ? "All cleared ✓" : `Level ${p.next}`),
        h("td", null, h("button", { class: "btn-ghost btn-small", type: "button", onclick: async () => {
          if (!(await confirmDialog("Start over", `Start ${p.name} · ${p.modeLabel} from level 1 again? Your scores are kept.`, "Start over"))) return;
          try { await api(`api/progress/${encodeURIComponent(p.game)}/${encodeURIComponent(p.mode)}`, { method: "DELETE" }); toast("Back to level 1"); await refreshGames(); renderScores(); }
          catch (e) { fail(e); }
        } }, "Start over"))))))));
}

// =====================================================================
// Leaderboard
// =====================================================================
// Its address says the game and mode (#/leaderboard/<game>/<mode>, mode "daily" for today's challenge), so a game's
// Leaderboard button opens it there and a reload keeps it. Without one: the game played last.
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
  if (state.arg && state.games.some((g) => g.id === state.arg)) {
    if (state.arg !== board.game) board.mode = null;
    board.game = state.arg;
    if (state.arg2) board.mode = state.arg2;
  }
  if (!state.games.some((g) => g.id === board.game)) {
    const last = state.games.filter((g) => g.lastPlayed).sort((a, b) => String(b.lastPlayed).localeCompare(String(a.lastPlayed)))[0];
    board.game = (last || state.games[0]).id;
    board.mode = null;
  }
  const g = state.games.find((x) => x.id === board.game);
  if (board.mode === "daily" && !(state.me.dailyChallenges && g.daily)) board.mode = null;
  if (board.mode !== "daily" && !g.modes.some((m) => m.id === board.mode)) {
    const last = lsGet("arcadeMode:" + g.id);           // the mode last played on this device
    board.mode = g.modes.some((m) => m.id === last) ? last : g.defaultMode;
  }
  keepBoardAddress();
  const gameSel = h("select", { "aria-label": "Game", id: "boardGame", value: board.game }, state.games.map((x) => h("option", { value: x.id }, x.name)));
  gameSel.addEventListener("change", () => { board.game = gameSel.value; board.mode = null; state.arg = board.game; state.arg2 = null; renderLeaderboard(); });
  const modeSel = h("select", { "aria-label": "Mode", id: "boardMode", value: board.mode },
    g.modes.map((m) => h("option", { value: m.id }, m.label)),
    state.me.dailyChallenges && g.daily ? h("option", { value: "daily" }, "Today's challenge") : null);
  modeSel.addEventListener("change", () => { board.mode = modeSel.value; keepBoardAddress(); loadBoard(box, daysBox); });
  const period = segmented([["all", "All time"], ["month", "This month"]], board.period, (v) => { board.period = v; loadBoard(box, daysBox); }, "Period");
  const box = h("div", { class: "card", id: "boardCard" }, spinner());
  const daysBox = h("div", { class: "card", id: "daysCard", hidden: true });
  mount(root, pageHead("Leaderboard"), h("div", { class: "filters" }, gameSel, modeSel, period), box, daysBox);
  loadBoard(box, daysBox);
}
// the address follows the game and mode chosen here (no new Back step for each)
function keepBoardAddress() {
  if (state.tab !== "leaderboard") return;
  state.arg = board.game; state.arg2 = board.mode;
  const want = routeHash("leaderboard", board.game, board.mode);
  if (location.hash === want) return;
  if (window.BackNav) BackNav.replace(want);
  else { try { history.replaceState(history.state, "", want); } catch (e) { /* sandboxed frame */ } }
}
async function loadBoard(box, daysBox) {
  mount(box, spinner());
  if (daysBox) { daysBox.hidden = true; clear(daysBox); }
  let data;
  const daily = board.mode === "daily";
  try { data = await api(daily ? `api/daily/board?game=${encodeURIComponent(board.game)}` : `api/leaderboard?game=${encodeURIComponent(board.game)}&mode=${encodeURIComponent(board.mode)}&period=${board.period}`); }
  catch (e) { mount(box, errorCard(e, () => loadBoard(box, daysBox))); return; }
  // Daily challenges: who has played on the most days this month, in its own card
  const days = daily ? daysBoard(data.days) : null;
  if (daysBox && days) { mount(daysBox, days); daysBox.hidden = false; }
  if (daily && !data.rows.length) { mount(box, h("div", { class: "empty", id: "boardEmpty" }, "No one has played today's challenge yet — be the first!")); return; }
  if (!data.rows.length) { mount(box, h("div", { class: "empty" }, board.period === "month" ? "No scores this month yet." : "No scores yet — be the first!")); return; }
  mount(box, youLine(data.rows), h("div", { class: "table-wrap" }, h("table", { class: "data", id: "boardTable" },
    h("thead", null, h("tr", null, h("th", null, "#"), h("th", null, "Player"), h("th", { class: "num" }, "Score"), h("th", { class: "num" }, "Level"), h("th", null, "When"),
      data.canDelete ? h("th", null, h("span", { class: "sr-only" }, "Delete")) : null)),
    h("tbody", null, data.rows.map((r) => h("tr", { class: r.me ? "me" : "" },
      h("td", { class: "rank r" + r.rank }, r.rank === 1 ? "🏆" : r.rank), h("td", null, r.name, r.me ? h("span", { class: "badge-you" }, "you") : null),
      h("td", { class: "num" }, h("strong", null, fmtNum(r.score))), h("td", { class: "num" }, r.level), h("td", null, fmtStamp(r.at)),
      data.canDelete ? h("td", null, h("button", { class: "icon-btn danger", type: "button", title: "Delete this score (admin)", "aria-label": `Delete ${r.name}'s score`, onclick: async () => {
        if (!(await confirmDialog("Delete score", `Delete ${r.name}'s score of ${fmtNum(r.score)}? The leaderboard updates at once.`))) return;
        try { await api(`api/scores/${r.scoreId}`, { method: "DELETE" }); toast("Score deleted"); loadBoard(box, daysBox); } catch (e) { fail(e); }
      } }, "🗑")) : null))))));
}
// "You: #4 of 9 · 320 behind #3"
function youLine(rows) {
  const mine = rows.find((r) => r.me);
  if (!mine) return h("div", { class: "you-line hint", id: "youLine" }, "You aren't on this board yet.");
  const above = rows.filter((r) => r.rank < mine.rank).pop();
  return h("div", { class: "you-line", id: "youLine" }, h("strong", null, `You: #${mine.rank} of ${rows.length}`),
    above ? ` · ${fmtNum(above.score - mine.score)} behind #${above.rank}` : mine.rank === 1 ? " · top of the board" : "");
}
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
  const look = h("select", { id: "prefLook", "aria-label": "Game look", value: p.look || "" }, lookOptions(true));
  look.addEventListener("change", () => save({ look: look.value || null }));
  const row = (label, sub, control) => h("div", { class: "setting-row" }, h("div", null, h("div", null, label), sub ? h("div", { class: "sub" }, sub) : null), control);
  // Appearance first: the app's theme (this device) and the games' look (yours, everywhere). On a computer the theme
  // is in the sidebar too.
  const appearance = h("div", { class: "card", id: "appearanceCard" }, h("h3", null, "Appearance"),
    row("App theme", "The colours of these pages, on this device.", themeSelect({ id: "prefTheme" })),
    row("Game look", "Colours and style of every game. Looks never change the rules, so scores stay comparable.", look));
  const cards = [
    state.me.isChild ? limitsCard(state.me) : null,        // a child's own limits come first
    appearance,
    h("div", { class: "card", id: "prefsCard" }, h("h3", null, "Playing"),
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
  cards.push(whoamiCard(who));          // last, folded
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
  return h("details", { class: "card whoami-fold", id: "whoamiCard" }, h("summary", null, h("strong", null, "How the app sees you")),
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

// opts: arg, arg2; replace (no Back step: a sub-tab or filter); force (leave a game being played without asking)
function showTab(tab, opts = {}) {
  if (!TABS.includes(tab)) tab = "home";
  const arg = opts.arg || null, arg2 = opts.arg2 || null;
  if (state.tab === "play" && (tab !== "play" || arg !== state.arg) && window.Play) {
    // a game being played pauses and asks (Save & exit / End & exit) instead of ending at once
    if (!opts.force && Play.holdLeave && Play.holdLeave({ tab, opts: { arg, arg2 } })) {
      const here = routeHash(state.tab, state.arg, state.arg2);
      if (location.hash !== here) { try { history.replaceState(history.state, "", here); } catch (e) { /* sandboxed frame */ } }
      return;
    }
    Play.leave();
  }
  state.tab = tab; state.arg = arg; state.arg2 = arg2;
  const want = routeHash(tab, arg, arg2);
  if (location.hash !== want) {
    // through the back trail (backnav.js), so Back retraces in-app steps; plain replace before it is set up
    if (window.BackNav && navReady) BackNav.go(want, { replace: !!opts.replace });
    else { try { history.replaceState(history.state, "", want); } catch (e) { /* sandboxed frame */ } }
  }
  document.body.dataset.route = tab;
  document.querySelectorAll(".side-nav .tab-btn").forEach((b) => {
    const on = b.dataset.tab === (tab === "play" ? "home" : tab);
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
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
    if (c) { c.open = true; c.scrollIntoView({ behavior: "smooth", block: "start" }); }
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

// The back gesture (backnav.js): a game being played (running or paused) is a layer: Back pauses it and asks
// (Save & exit / End & exit) and never just leaves it; then Back closes a dialog, retraces the in-app steps, goes to a
// page's parent, and only Back on Games with nothing open leaves the app.
let navReady = false;
function isTabHash(hash) {
  const r = parseHash(hash);
  return !!(r && r.tab !== "play" && !r.arg);
}
function parentHash(hash) {
  const r = parseHash(hash);
  if (!r) return null;
  if (r.tab === "play") return "#/home";
  if (r.tab === "admin" && r.arg2) return routeHash("admin", r.arg);
  if (r.arg && r.tab !== "home") return routeHash(r.tab);
  return null;
}
function initBackNav() {
  if (!window.BackNav) return;
  BackNav.init({
    atHome: () => state.tab === "home",
    goHome: () => showTab("home"),
    isTab: isTabHash,
    parentOf: parentHash,
    openLayers: () => UI.dialogs().concat(window.Play && Play.inGame() ? ["game"] : []),
    closeLayer: (layer) => { if (layer === "game") Play.backPressed(); else layer.close(); },
  });
  navReady = true;
}

document.addEventListener("DOMContentLoaded", init);
