"use strict";
/* Admin (admins only; the server enforces it, hiding the menu item is a convenience):
   App settings (with holidays), Users (people, children and their limits, extra time, play history,
   notify services) and Storage (backup and restore). */

const Admin = (() => {
  const TABS = [["settings", "App settings"], ["levels", "Levels"], ["ai", "AI usage"], ["users", "Users"], ["storage", "Storage"]];
  const DAYS = [[1, "M", "Monday"], [2, "T", "Tuesday"], [3, "W", "Wednesday"], [4, "T", "Thursday"], [5, "F", "Friday"], [6, "S", "Saturday"], [7, "S", "Sunday"]];
  const KEEP = [[0, "Forever"], [1, "1 year"], [2, "2 years"], [5, "5 years"]];
  const LB = [["full", "Shown, with full names"], ["first", "Shown, first names only"], ["hidden", "Hidden"]];

  function render(sub, arg2) {
    const root = $("#tab-admin");
    if (!isAdmin()) {
      mount(root, pageHead("Admin"), h("div", { class: "card", id: "adminDenied" },
        h("h3", null, "Only admins can open this page"),
        h("div", { class: "hint" }, "App settings, Levels, AI usage, Users and Storage are for the people listed in the ",
          h("code", null, "admin_users"), " option on the app's Configuration tab.")));
      return;
    }
    if (!TABS.some(([k]) => k === sub)) sub = "settings";
    const tabs = h("div", { class: "admin-tabs", role: "tablist", "aria-label": "Admin sections" },
      TABS.map(([key, label]) => h("button", {
        type: "button", role: "tab", class: key === sub ? "active" : "", "aria-selected": key === sub ? "true" : "false",
        dataset: { adminTab: key }, onclick: () => showTab("admin", { arg: key }),
      }, label)));
    const body = h("div", { class: "admin-body" });
    mount(root, pageHead("Admin"), tabs, body);
    stopPolling();
    ({ settings: renderSettings, levels: renderLevels, ai: renderUsage, users: renderUsers, storage: renderStorage })[sub](body, arg2);
  }

  // =====================================================================
  // App settings
  // =====================================================================
  async function renderSettings(box) {
    mount(box, spinner());
    let data, hol;
    try { [data, hol] = await Promise.all([api("api/admin/settings"), api("api/admin/holidays")]); }
    catch (e) { mount(box, errorCard(e, () => renderSettings(box))); return; }
    const saved = JSON.parse(JSON.stringify(data.values));
    const cur = JSON.parse(JSON.stringify(data.values));
    const err = h("div", { class: "error-text", id: "settingsError", role: "alert" });
    const saveBtn = h("button", { class: "btn-primary", type: "button", id: "saveSettings", disabled: true }, "Save");
    const revertBtn = h("button", { class: "btn-ghost", type: "button", disabled: true, onclick: () => renderSettings(box) }, "Discard changes");
    const extra = {};          // secrets to clear (an empty value is otherwise "unchanged")
    const changes = () => {
      const out = {};
      for (const k of Object.keys(cur)) if (JSON.stringify(cur[k]) !== JSON.stringify(saved[k])) out[k] = cur[k];
      return Object.assign(out, extra);
    };
    const dirty = () => { const n = Object.keys(changes()).length > 0; saveBtn.disabled = !n; revertBtn.disabled = !n; };
    const set = (k, v) => { cur[k] = v; dirty(); };
    const row = (label, sub, control, id) => h("div", { class: "setting-row", id: id || null },
      h("div", null, h("div", null, label), sub ? h("div", { class: "sub" }, sub) : null), control);
    const sw = (k, label) => { const w = toggleSwitch(cur[k], (v) => set(k, v), { label }); w.querySelector("input").id = "set-" + k; return w; };

    // games
    const gameRows = data.games.map((g) => {
      const w = toggleSwitch(!cur.disabled_games.includes(g.id), (on) => {
        const off = new Set(cur.disabled_games);
        if (on) off.delete(g.id); else off.add(g.id);
        set("disabled_games", data.games.map((x) => x.id).filter((id) => off.has(id)));
      }, { label: g.name });
      w.querySelector("input").id = "set-game-" + g.id;
      return row(g.name, null, w);
    });
    const lookSel = h("select", { id: "set-default_look", "aria-label": "Default look", value: cur.default_look },
      data.looks.map((l) => h("option", { value: l.id }, l.label)));
    lookSel.addEventListener("change", () => set("default_look", lookSel.value));
    const keepSel = h("select", { id: "set-keep_scores_years", "aria-label": "Keep scores for", value: String(cur.keep_scores_years) },
      KEEP.map(([v, l]) => h("option", { value: String(v) }, l)));
    keepSel.addEventListener("change", () => set("keep_scores_years", Number(keepSel.value)));
    const hintsIn = h("input", { type: "number", id: "set-sudoku_hints", min: "0", max: "20", step: "1", value: String(cur.sudoku_hints), "aria-label": "Sudoku hints per puzzle" });
    hintsIn.addEventListener("input", () => { const n = Number(hintsIn.value); if (hintsIn.value !== "" && Number.isInteger(n) && n >= 0 && n <= 20) set("sudoku_hints", n); });
    const days = h("div", { class: "weekday-buttons", id: "schoolDays", role: "group", "aria-label": "School days" },
      DAYS.map(([n, letter, name]) => {
        const b = h("button", { type: "button", class: cur.school_days.includes(n) ? "on" : "", title: name, "aria-label": name,
          "aria-pressed": cur.school_days.includes(n) ? "true" : "false", dataset: { day: String(n) } }, letter);
        b.addEventListener("click", () => {
          const s = new Set(cur.school_days);
          if (s.has(n)) s.delete(n); else s.add(n);
          b.classList.toggle("on", s.has(n)); b.setAttribute("aria-pressed", s.has(n) ? "true" : "false");
          set("school_days", [...s].sort((a, b2) => a - b2));
        });
        return b;
      }));
    const adminChecks = h("div", { class: "check-list", id: "warnAdmins" }, data.admins.length ? data.admins.map((a) => {
      const box_ = h("input", { type: "checkbox", checked: cur.limit_warning_admins.includes(a.id), dataset: { id: a.id } });
      box_.addEventListener("change", () => {
        const s = new Set(cur.limit_warning_admins);
        if (box_.checked) s.add(a.id); else s.delete(a.id);
        set("limit_warning_admins", data.admins.map((x) => x.id).filter((id) => s.has(id)));
      });
      return h("label", null, box_, a.name);
    }) : h("span", { class: "hint" }, "Admins appear here after they have opened the app once."));

    saveBtn.addEventListener("click", async () => {
      err.textContent = "";
      saveBtn.disabled = true;
      try { await api("api/admin/settings", { method: "PUT", body: changes() }); toast("Settings saved"); await refreshMe(); await refreshGames(); renderSettings(box); }
      catch (e) { err.textContent = e.message; saveBtn.disabled = false; }
    });

    mount(box,
      h("div", { class: "card" }, h("h3", null, "Games"), h("div", { class: "hint" }, "A game that's off is hidden; its scores are kept."), gameRows,
        row("Brick Breaker power-ups", "Off: only Classic play (no wider paddle, slower ball, extra ball or extra life).", sw("brick_powerups", "Brick Breaker power-ups")),
        row("Sudoku hints per puzzle", "How many hints a ranked Sudoku allows (0 = none). Practice always has as many as you like. Each hint adds 30 seconds.", hintsIn),
        row("Show daily challenges", "Off by default. On: Home shows three games with the same puzzle for everyone each day, one ranked try each. Turning it off hides them and keeps the scores.", sw("show_daily_challenges", "Show daily challenges"))),
      h("div", { class: "card" }, h("h3", null, "Looks and scores"),
        row("Default look", "For everyone who hasn't picked their own on Settings.", lookSel),
        row("Leaderboard", "Off: only personal bests are shown.", sw("leaderboard", "Leaderboard")),
        row("Keep scores for", "Older games are removed; each person's best per game and mode is always kept.", keepSel)),
      h("div", { class: "card" }, h("h3", null, "Children"),
        h("div", { class: "hint" }, "Mark a person as a child and set their limits on ", h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { arg: "users" }) }, "Users"), "."),
        row("School days", "Days that use school-day limits. Every other day, and every holiday below, uses weekend limits.", days),
        holidaysBlock(hol.holidays),
        row("Limit warnings to parents", "A phone notice when a child has 5 minutes left today, with “Add 15 minutes”.", sw("limit_warnings", "Limit warnings to parents")),
        h("div", { class: "field wide" }, "Who gets limit warnings (none ticked = every admin)", adminChecks)),
      h("div", { class: "card" }, h("h3", null, "Home Assistant"),
        row("Notify new records", "A phone notification to the household when someone sets a new record. Each person can opt out on Settings.", sw("notify_records", "Notify new records")),
        row("Invites by phone notification", "A phone notification when someone invites another person to play together (with Join and Not now). The invite also shows on the Games page. Each person can opt out on Settings.", sw("notify_invites", "Invites by phone notification")),
        row("Home Assistant sensors", h("span", null, "Publishes ", h("code", null, "sensor.household_arcade_<game>_record"), ", ",
          h("code", null, "sensor.household_arcade_<person>_played_today"), " and ", h("code", null, "binary_sensor.household_arcade_<person>_playing"), "."),
          sw("ha_sensors", "Home Assistant sensors")),
        data.hasToken ? null : h("div", { class: "hint warn" }, "This app can't reach Home Assistant right now (no Supervisor token), so sensors and notifications can't be sent."),
        h("div", { class: "hint" }, `Days and times use Home Assistant's time zone (${data.timeZone}).`)),
      aiCard(data, cur, set, sw, row, extra, dirty),
      err, h("div", { class: "actions" }, revertBtn, saveBtn));
  }

  // ---------- App settings → AI levels ----------
  function aiCard(data, cur, set, sw, row, extra, dirty) {
    const provSel = h("select", { id: "set-ai_provider", "aria-label": "Provider", value: cur.ai_provider },
      data.providers.map((p) => h("option", { value: p.id }, p.label)));
    const urlIn = h("input", { type: "url", id: "set-ai_url", value: cur.ai_url, "aria-label": "Address", spellcheck: "false" });
    const placeholder = () => {
      const p = data.providers.find((x) => x.id === provSel.value);
      urlIn.placeholder = p && p.defaultUrl ? `${p.defaultUrl} (leave empty for this)` : "http://<ollama host>:11434";
    };
    placeholder();
    provSel.addEventListener("change", () => { set("ai_provider", provSel.value); placeholder(); });
    urlIn.addEventListener("input", () => set("ai_url", urlIn.value.trim()));
    const models = h("datalist", { id: "aiModels" });
    const modelIn = h("input", { type: "text", id: "set-ai_model", value: cur.ai_model, list: "aiModels", "aria-label": "Model", spellcheck: "false", placeholder: "e.g. a model you have pulled" });
    modelIn.addEventListener("input", () => set("ai_model", modelIn.value.trim()));
    const keySet = data.secretsSet.ai_api_key;
    const keyIn = h("input", { type: "password", id: "set-ai_api_key", autocomplete: "new-password", "aria-label": "Access key",
      placeholder: keySet ? "Saved — type to replace" : "Not set (only Anthropic Claude and some services need one)" });
    keyIn.addEventListener("input", () => { delete extra.ai_api_key; set("ai_api_key", keyIn.value); });
    const clearKey = keySet ? h("button", { class: "btn-ghost btn-small", type: "button", id: "clearAiKey", onclick: () => {
      keyIn.value = ""; set("ai_api_key", ""); extra.ai_api_key = ""; dirty(); keyIn.placeholder = "Will be removed when you save";
    } }, "Remove key") : null;
    const num = (k, min, max, label) => {
      const el = h("input", { type: "number", id: "set-" + k, min: String(min), max: String(max), step: "1", value: String(cur[k]), "aria-label": label, class: "num-input" });
      el.addEventListener("input", () => { const n = Number(el.value); set(k, Number.isInteger(n) ? n : el.value); });
      return el;
    };
    const price = (k, label) => {
      const el = h("input", { type: "number", id: "set-" + k, min: "0", max: "1000", step: "0.01", value: String(cur[k]), "aria-label": label, class: "num-input" });
      el.addEventListener("input", () => { const n = Number(el.value); set(k, el.value !== "" && Number.isFinite(n) ? n : el.value); });
      return el;
    };
    const result = h("div", { class: "hint", id: "aiTestResult", role: "status" });
    const test = h("button", { class: "btn-secondary", type: "button", id: "aiTest" }, "Test connection");
    test.addEventListener("click", async () => {
      test.disabled = true; result.textContent = "Testing…"; result.className = "hint";
      try {
        const r = await api("api/admin/ai/test", { method: "POST", body: { provider: provSel.value, url: urlIn.value.trim(), model: modelIn.value.trim(), apiKey: keyIn.value } });
        if (r.models) mount(models, r.models.map((m) => h("option", { value: m })));
        if (r.ok) {
          result.className = "hint ok";
          result.textContent = `Connected. ${r.models.length} model(s) offered.` + (r.answer !== null ? ` The model answered: ${r.answer}` : " Pick a model to test it too.");
        } else { result.className = "hint warn"; result.textContent = r.error; }
      } catch (e) { result.className = "hint warn"; result.textContent = e.message; }
      test.disabled = false;
    });
    return h("div", { class: "card", id: "aiCard" }, h("h3", null, "AI levels"),
      h("div", { class: "hint" }, "More levels for every game, made by an AI model: your own (Ollama) or a service. ",
        "Only the game's rules and recent levels are sent, never anything about the people here. Every level is checked before anyone plays it. ",
        h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { arg: "levels" }) }, "See the levels"), "."),
      row("AI levels", "Off: games play their built-in levels only.", sw("ai_levels_enabled", "AI levels")),
      h("div", { class: "limits-grid" },
        h("label", { class: "field" }, "Provider", provSel),
        h("label", { class: "field" }, "Address", urlIn),
        h("label", { class: "field" }, "Model", modelIn, models),
        h("div", { class: "field" }, "Access key", h("div", { class: "inline-times" }, keyIn, clearKey)),
        h("label", { class: "field" }, "Most the model may write (tokens)", num("ai_max_output_tokens", 256, 64000, "Most the model may write"))),
      h("div", { class: "form-row" }, test), result,
      row("Build ahead automatically", "When someone nears the last level, the next ones are built in the background.", sw("ai_levels_auto", "Build ahead automatically")),
      h("div", { class: "limits-grid" },
        h("label", { class: "field" }, "Start building this many levels before the end (1–5)", num("ai_levels_ahead", 1, 5, "Levels before the end")),
        h("label", { class: "field" }, "Levels per build (1–20)", num("ai_levels_batch", 1, 20, "Levels per build")),
        h("label", { class: "field" }, "Most levels built a day (0 = no limit)", num("ai_levels_daily_limit", 0, 500, "Most levels built a day"))),
      row("Check new levels before they're played", "New levels wait on Admin → Levels until an admin approves them.", sw("ai_levels_review", "Check new levels first")),
      h("div", { class: "hint" }, "Prices are only for the cost estimate on ",
        h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { arg: "ai" }) }, "AI usage"),
        " — copy them from your provider's price list. Leave 0 for your own model."),
      h("div", { class: "limits-grid" },
        h("label", { class: "field" }, "Price per million input tokens", price("ai_price_in", "Price per million input tokens")),
        h("label", { class: "field" }, "Price per million output tokens", price("ai_price_out", "Price per million output tokens"))));
  }

  // =====================================================================
  // Levels
  // =====================================================================
  let pollTimer = null;
  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

  async function renderLevels(box) {
    stopPolling();
    if (!box.firstChild) mount(box, spinner());
    let data;
    try { data = await api("api/admin/levels"); }
    catch (e) { mount(box, errorCard(e, () => renderLevels(box))); return; }
    if (!document.body.contains(box)) return;
    const ai = data.ai;
    const status = ai.problem
      ? h("div", { class: "card banner-card warn", id: "aiProblem" }, ai.problem, " ",
        h("button", { class: "link-btn", type: "button", onclick: () => showTab("admin", { arg: "settings" }) }, "Open App settings"))
      : h("div", { class: "card", id: "aiStatus" }, h("strong", null, `${ai.provider} · ${ai.model}`),
        h("div", { class: "hint" }, `Levels made today: ${ai.madeToday}${ai.dailyLimit ? ` of ${ai.dailyLimit}` : ""}. `,
          ai.auto ? `New levels are built automatically ${ai.ahead} level(s) before the end, ${ai.batch} at a time.` : "Automatic building is off.",
          ai.review ? " New levels wait for an admin's OK." : ""));
    mount(box, h("div", { class: "hint", style: "margin-bottom:10px" },
      "Every game with levels, the levels it plays in order, and building more. Built-in levels came with the app; AI levels were made by the model and checked by the app."),
      status, data.lists.map((list) => listCard(list, ai, box)));
    if (data.lists.some((l) => l.building)) pollTimer = setTimeout(() => { if (document.body.contains(box)) renderLevels(box); }, 3000);
  }

  function listCard(list, ai, box) {
    const c = list.counts;
    const lb = list.lastBuild;
    const count = h("input", { type: "number", min: "1", max: "20", step: "1", value: String(ai.batch), class: "num-input", "aria-label": `How many ${list.label} levels to build`, dataset: { buildCount: list.id } });
    const err = h("div", { class: "error-text", role: "alert" });
    const btn = h("button", { class: "btn-primary", type: "button", dataset: { build: list.id }, disabled: !!ai.problem || list.building || ai.leftToday === 0 },
      list.building ? "Building…" : "Build more levels");
    btn.addEventListener("click", async () => {
      err.textContent = "";
      const n = Number(count.value);
      if (!Number.isInteger(n) || n < 1 || n > 20) { err.textContent = "Build 1 to 20 levels at a time."; return; }
      btn.disabled = true;
      try { await api("api/admin/levels/build", { method: "POST", body: { game: list.id, count: n } }); toast(`Building ${n} ${list.label} level(s)`); renderLevels(box); }
      catch (e) { err.textContent = e.message; btn.disabled = false; }
    });
    const build = lb ? h("div", { class: "sub-block build-line", dataset: { buildStatus: lb.status } },
      h("div", null, h("strong", null, ({ queued: "Waiting to start", running: "Building", done: "Last build", failed: "Last build failed" })[lb.status] || lb.status),
        ` · ${fmtStamp(lb.createdAt)} · asked by ${lb.requestedBy === "auto" ? "the app (automatic)" : lb.requestedBy} · `,
        `${lb.made} of ${lb.count} made${lb.rejected ? `, ${lb.rejected} turned down` : ""}`,
        lb.tokensIn || lb.tokensOut ? ` · ${fmtNum(lb.tokensIn)} tokens in, ${fmtNum(lb.tokensOut)} out` : ""),
      lb.error ? h("div", { class: "hint warn" }, lb.error) : null,
      lb.log.length ? h("details", null, h("summary", null, "Notes"), h("ul", { class: "build-log" }, lb.log.map((l) => h("li", null, l)))) : null) : null;
    return h("div", { class: "card level-list", id: "levels-" + list.id },
      h("h3", null, list.label),
      h("div", { class: "hint" }, `${c.ready} playable · ${c.builtin} built-in · ${c.ai} made by AI`,
        c.waiting ? ` · ${c.waiting} waiting for an OK` : "", c.retired ? ` · ${c.retired} retired` : "",
        list.reached ? ` · furthest: level ${list.reached.level} (${list.reached.name})` : " · nobody has played these yet"),
      h("div", { class: "form-row" }, h("label", { class: "field" }, "How many", count), btn), err, build,
      levelsBlock(list, box));
  }

  // The level pictures fold away (closed at first: with many games and levels the page would be very long).
  // What is open stays open while the page refreshes during a build.
  const openLists = new Set();
  function levelsBlock(list, box) {
    const c = list.counts;
    const waiting = c.waiting ? ` · ${c.waiting} waiting for an OK` : "";
    const det = h("details", { class: "level-fold", dataset: { levels: list.id }, open: openLists.has(list.id) || null },
      h("summary", null, `Levels (${list.levels.length})${waiting}`));
    det.addEventListener("toggle", () => {
      if (det.open) {
        openLists.add(list.id);
        if (!det.querySelector(".level-grid")) det.appendChild(h("div", { class: "level-grid" }, list.levels.map((lv) => levelTile(list, lv, box))));
      } else openLists.delete(list.id);
    });
    if (det.open) det.appendChild(h("div", { class: "level-grid" }, list.levels.map((lv) => levelTile(list, lv, box))));
    return det;
  }

  function levelTile(list, lv, box) {
    const act = async (what) => {
      try { await api(`api/admin/levels/${encodeURIComponent(lv.id)}/${what}`, { method: "POST", body: {} }); renderLevels(box); }
      catch (e) { fail(e); }
    };
    const total = list.levels.length;
    const moveTo = async (to) => {
      if (!Number.isInteger(to) || to < 1 || to > total || to === lv.number) return;
      try { await api(`api/admin/levels/${encodeURIComponent(lv.id)}/move`, { method: "POST", body: { to } }); renderLevels(box); }
      catch (e) { fail(e); }
    };
    const place = h("input", { type: "number", min: "1", max: String(total), step: "1", value: String(lv.number), class: "place-input",
      "aria-label": `Place of ${lv.name} (1–${total})`, title: "Move to this place" });
    place.addEventListener("change", () => moveTo(Number(place.value)));
    const order = h("div", { class: "level-order" },
      h("button", { class: "icon-btn", type: "button", title: "Earlier", "aria-label": `Move ${lv.name} earlier`, disabled: lv.number <= 1, dataset: { up: lv.id }, onclick: () => moveTo(lv.number - 1) }, "◀"),
      place,
      h("button", { class: "icon-btn", type: "button", title: "Later", "aria-label": `Move ${lv.name} later`, disabled: lv.number >= total, dataset: { down: lv.id }, onclick: () => moveTo(lv.number + 1) }, "▶"));
    const actions = [];
    if (lv.status === "waiting") actions.push(h("button", { class: "btn-primary btn-small", type: "button", dataset: { approve: lv.id }, onclick: () => act("approve") }, "Approve"));
    if (lv.status !== "retired") actions.push(h("button", { class: "btn-ghost btn-small", type: "button", dataset: { retire: lv.id }, onclick: () => act("retire") }, "Retire"));
    else actions.push(h("button", { class: "btn-secondary btn-small", type: "button", dataset: { restore: lv.id }, onclick: () => act("restore") }, "Use again"));
    if (lv.source === "ai") actions.push(h("button", { class: "btn-ghost btn-small danger", type: "button", dataset: { delete: lv.id }, onclick: async () => {
      if (!(await confirmDialog(`Delete level ${lv.number}?`, `“${lv.name}” is removed from ${list.label} for good; the levels after it move up. Scores already played stay.`, "Delete"))) return;
      try { await api(`api/admin/levels/${encodeURIComponent(lv.id)}`, { method: "DELETE" }); toast(`“${lv.name}” deleted`); renderLevels(box); }
      catch (e) { fail(e); }
    } }, "Delete"));
    return h("div", { class: "level-tile" + (lv.status !== "ready" ? " " + lv.status : ""), dataset: { level: String(lv.number) } },
      preview(list.id, lv.data),
      h("div", { class: "level-name" }, `${lv.number}. ${lv.name}`),
      h("div", { class: "chip-row" },
        h("span", { class: "chip" + (lv.source === "ai" ? " on" : "") }, lv.source === "ai" ? "AI" : "built-in"),
        lv.status !== "ready" ? h("span", { class: "chip" }, lv.status === "waiting" ? "waiting" : "retired") : null,
        h("span", { class: "chip", title: "How hard it is, 0–100" }, `difficulty ${lv.difficulty}`)),
      order,
      h("div", { class: "list-actions" }, actions));
  }

  // A small drawing of a level, in the page's own colours: bricks by hits, maze walls, and for every other game
  // its grid field (a map, a shape, a formation…) with a line of its numbers.
  function preview(listId, data) {
    const css = getComputedStyle(document.documentElement);
    const v = (n, d) => (css.getPropertyValue(n) || "").trim() || d;
    const canvas = document.createElement("canvas");
    canvas.className = "level-preview";
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", data.name);
    const g = canvas.getContext("2d");
    if (listId === "brick") {
      canvas.width = 96; canvas.height = 60;
      g.fillStyle = v("--panel-alt", "#232834"); g.fillRect(0, 0, 96, 60);
      const colours = [null, v("--accent-dim", "#3d8f81"), v("--accent", "#5ec8b6"), v("--warn", "#f0b34a")];
      data.rows.forEach((row, r) => [...row].forEach((ch, c) => {
        if (ch === ".") return;
        g.fillStyle = colours[+ch]; g.fillRect(2 + c * 11.5, 3 + r * 5.5, 10.5, 4.5);
      }));
      return canvas;
    }
    if (listId === "snake") {
      canvas.width = 80; canvas.height = 80;
      g.fillStyle = v("--panel-alt", "#232834"); g.fillRect(0, 0, 80, 80);
      g.fillStyle = v("--text-dim", "#9099ad");
      data.walls.forEach((row, y) => [...row].forEach((ch, x) => { if (ch === "#") g.fillRect(x * 4, y * 4, 4, 4); }));
      g.fillStyle = v("--accent", "#5ec8b6");
      g.fillRect(6 * 4, 10 * 4, 16, 4);
      return canvas;
    }
    const isGrid = (x) => Array.isArray(x) && x.length > 0 && x.every((r) => typeof r === "string");
    const gridKey = Object.keys(data).find((k) => isGrid(data[k]));
    const facts = Object.keys(data).filter((k) => k !== "name" && k !== gridKey).map((k) => {
      const x = data[k];
      return `${k} ${Array.isArray(x) ? x.length : typeof x === "boolean" ? (x ? "yes" : "no") : x}`;
    }).join(" · ");
    const box = h("div", { class: "level-preview-box" });
    if (gridKey) {
      const rows = data[gridKey], w = Math.max(...rows.map((r) => r.length)), hgt = rows.length;
      const cell = Math.max(2, Math.floor(Math.min(96 / w, 64 / hgt)));
      canvas.width = w * cell; canvas.height = hgt * cell;
      g.fillStyle = v("--panel-alt", "#232834"); g.fillRect(0, 0, canvas.width, canvas.height);
      const palette = [v("--text-dim", "#9099ad"), v("--accent", "#5ec8b6"), v("--warn", "#f0b34a"), v("--accent-dim", "#3d8f81"), v("--danger", "#e5484d")];
      const kinds = [...new Set(rows.join("").replace(/[.\s]/g, ""))];
      rows.forEach((row, y) => [...row].forEach((ch, x) => {
        if (ch === "." || ch === " ") return;
        g.fillStyle = palette[kinds.indexOf(ch) % palette.length];
        g.fillRect(x * cell, y * cell, cell - (cell > 3 ? 1 : 0), cell - (cell > 3 ? 1 : 0));
      }));
      box.appendChild(canvas);
    }
    if (facts) box.appendChild(h("div", { class: "level-facts" }, facts));
    return box;
  }

  // =====================================================================
  // AI usage
  // =====================================================================
  async function renderUsage(box, days) {
    days = [7, 30, 90].includes(Number(days)) ? Number(days) : 30;
    mount(box, spinner());
    let u;
    try { u = await api(`api/admin/ai/usage?days=${days}`); }
    catch (e) { mount(box, errorCard(e, () => renderUsage(box, days))); return; }
    if (!document.body.contains(box)) return;
    const n = (x) => Number(x || 0).toLocaleString();
    const priced = !!(u.prices.in || u.prices.out);
    const money = (c) => (c === null || c === undefined ? null : c > 0 && c < 0.01 ? "< 0.01" : c.toFixed(2));
    const tile = (label, t, id) => h("div", { class: "usage-tile", id },
      h("div", { class: "usage-label" }, label),
      h("div", { class: "usage-big" }, n(t.tokensIn + t.tokensOut), h("span", { class: "usage-unit" }, " tokens")),
      h("div", { class: "hint" }, `${n(t.calls)} request${t.calls === 1 ? "" : "s"}${t.failed ? `, ${n(t.failed)} failed` : ""} · ${n(t.levels)} level${t.levels === 1 ? "" : "s"} made`),
      h("div", { class: "hint" }, `in ${n(t.tokensIn)} · out ${n(t.tokensOut)}`),
      priced ? h("div", { class: "usage-cost" }, `about ${money(t.cost) || "0.00"}`) : null);
    const top = Math.max(1, ...u.byDay.map((d) => d.tokensIn + d.tokensOut));
    const chart = h("div", { class: "usage-chart", role: "img", "aria-label": `Tokens a day over the last ${days} days` },
      u.byDay.map((d) => {
        const total = d.tokensIn + d.tokensOut;
        return h("div", { class: "usage-bar-wrap", title: `${d.date}: ${n(total)} tokens, ${n(d.calls)} request(s), ${n(d.levels)} level(s)` },
          h("div", { class: "usage-bar" + (d.failed && !total ? " failed" : ""), style: `height:${total ? Math.max(3, Math.round(total / top * 100)) : d.calls ? 3 : 0}%` }));
      }));
    const table = (rows, head, cells, id, text = 1) => (rows.length
      ? h("div", { class: "table-scroll" }, h("table", { class: "usage-table", id },
        h("thead", null, h("tr", null, head.map((x, i) => h("th", { class: i >= text ? "num" : "" }, x)))),
        h("tbody", null, rows.map((r) => h("tr", null, cells(r).map((x, i) => h("td", { class: i >= text ? "num" : "" }, x)))))))
      : h("div", { class: "hint" }, "Nothing yet."));
    const costHead = priced ? ["Cost"] : [];
    const costCell = (r) => (priced ? [money(r.cost) || "0.00"] : []);
    const picker = h("div", { class: "seg", role: "group", "aria-label": "Period" },
      [7, 30, 90].map((d) => h("button", { type: "button", class: d === days ? "active" : "", "aria-pressed": d === days ? "true" : "false", onclick: () => renderUsage(box, d) }, `${d} days`)));
    mount(box,
      h("div", { class: "hint", style: "margin-bottom:10px" }, "Every request this app sent to the AI model: level builds and connection tests. ",
        priced ? "Costs are estimates from the prices on App settings." : "Add your provider's prices on App settings to see an estimated cost.",
        ` Today ${n(u.totals.today.levels)} level(s) were made` + (u.dailyLimit ? ` of the ${n(u.dailyLimit)} allowed a day.` : ".")),
      h("div", { class: "usage-tiles" }, tile("Today", u.totals.today, "usageToday"), tile("Last 7 days", u.totals.week, "usageWeek"),
        tile("Last 30 days", u.totals.month, "usageMonth"), tile("All time", u.totals.all, "usageAll")),
      h("div", { class: "card" }, h("div", { class: "card-head-row" }, h("h3", null, "Tokens a day"), picker), chart,
        h("div", { class: "usage-axis" }, h("span", null, u.byDay[0].date), h("span", null, u.byDay[u.byDay.length - 1].date))),
      h("div", { class: "card" }, h("h3", null, `By game · ${days} days`),
        table(u.byGame, ["Game", "Requests", "Levels", "Turned down", "Tokens"].concat(costHead),
          (r) => [r.name, n(r.calls), n(r.levels), n(r.rejected), n(r.tokensIn + r.tokensOut)].concat(costCell(r)), "usageByGame")),
      h("div", { class: "card" }, h("h3", null, `By model · ${days} days`),
        table(u.byModel, ["Model", "Requests", "Failed", "Tokens in", "Tokens out"].concat(costHead),
          (r) => [`${r.model || "?"} (${r.provider})`, n(r.calls), n(r.failed), n(r.tokensIn), n(r.tokensOut)].concat(costCell(r)), "usageByModel")),
      h("div", { class: "card" }, h("h3", null, "Latest requests"),
        table(u.recent, ["When", "What", "Result", "Tokens", "Took"],
          (r) => [whenText(r.at), r.purpose === "test" ? "Connection test" : `Levels · ${r.name || r.game}`,
            r.ok ? (r.purpose === "build" ? `${r.made} made${r.rejected ? `, ${r.rejected} turned down` : ""}` : "OK") : `Failed: ${r.error || "?"}`,
            n(r.tokensIn + r.tokensOut), `${(r.ms / 1000).toFixed(1)} s`], "usageRecent", 3)));
  }
  function whenText(iso) {
    try { return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return iso; }
  }

  function holidaysBlock(list) {
    const wrap = h("div", { class: "sub-block", id: "holidays" });
    const draw = (items) => {
      const from = h("input", { type: "date", id: "holFrom", "aria-label": "From" });
      const to = h("input", { type: "date", id: "holTo", "aria-label": "To (optional)" });
      const name = h("input", { type: "text", id: "holName", maxlength: "80", placeholder: "e.g. Autumn break", "aria-label": "Name" });
      const add = h("button", { class: "btn-secondary", type: "button", id: "holAdd" }, "Add");
      const err = h("div", { class: "error-text" });
      add.addEventListener("click", async () => {
        err.textContent = "";
        if (!from.value) { err.textContent = "Pick a date first."; return; }
        try { const r = await api("api/admin/holidays", { method: "POST", body: { from: from.value, to: to.value || null, name: name.value } }); draw(r.holidays); toast("Holiday added"); }
        catch (e) { err.textContent = e.message; }
      });
      const today = state.me.today;
      const upcoming = items.filter((x) => x.date >= today);
      mount(wrap, h("div", null, "Holidays"), h("div", { class: "sub hint" }, "Dates when children get weekend limits. Past dates are kept but not listed."),
        h("div", { class: "holiday-list" }, upcoming.length ? upcoming.map((x) => h("span", { class: "chip" }, `${x.date}${x.name ? " · " + x.name : ""}`,
          h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${x.date}`, title: "Remove", onclick: async () => {
            try { const r = await api(`api/admin/holidays/${x.date}`, { method: "DELETE" }); draw(r.holidays); } catch (e) { fail(e); }
          } }, "✕"))) : h("span", { class: "hint" }, "No holidays coming up.")),
        h("div", { class: "form-row" }, h("label", { class: "field" }, "From", from), h("label", { class: "field" }, "To (optional)", to),
          h("label", { class: "field" }, "Name (optional)", name), add), err);
    };
    draw(list);
    return wrap;
  }

  // =====================================================================
  // Users
  // =====================================================================
  async function renderUsers(box, focusId) {
    mount(box, spinner());
    let data;
    try { data = await api("api/admin/users"); }
    catch (e) { mount(box, errorCard(e, () => renderUsers(box, focusId))); return; }
    const list = h("div", { id: "userList" });
    const again = () => renderUsers(box, focusId);
    data.users.forEach((u) => list.appendChild(userCard(u, data.games, again, u.id === focusId)));
    mount(box, h("div", { class: "hint", style: "margin-bottom:10px" },
      "Everyone who has opened the app is here. People switched off can't play and don't appear on the leaderboard."),
      data.users.length ? list : h("div", { class: "card empty" }, "Nobody has opened the app yet."));
    if (focusId) {
      const card = document.getElementById("user-" + focusId);
      if (card) setTimeout(() => card.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
      else toast("That person isn't known to this app.", true);
    }
  }

  function userCard(u, games, again, highlight) {
    const self = state.me && u.id === state.me.id;
    const card = h("div", { class: "user-card" + (highlight ? " highlight" : ""), id: "user-" + u.id, dataset: { userId: u.id } });
    const patch = async (body) => {
      try { const r = await api(`api/admin/users/${encodeURIComponent(u.id)}`, { method: "PATCH", body }); card.replaceWith(userCard(r, games, again, highlight)); }
      catch (e) { fail(e); again(); }
    };
    const badges = h("span", { class: "user-badges" },
      self ? h("span", { class: "badge-you" }, "you") : null,
      u.isAdmin ? h("span", { class: "chip on" }, "admin") : null,
      u.isChild ? h("span", { class: "chip on" }, "child") : null,
      u.disabled ? h("span", { class: "chip" }, "switched off") : null);
    const enabled = toggleSwitch(!u.disabled, (on) => patch({ disabled: !on }), { label: `${u.name} can play`, disabled: self });
    const child = toggleSwitch(u.isChild, (on) => patch({ isChild: on }), { label: `${u.name} is a child`, disabled: u.isAdmin });
    const parts = [
      h("div", { class: "user-head" },
        h("span", null, h("span", { class: "name" }, u.name), badges,
          h("div", { class: "hint" }, u.username ? `login ${u.username} · ` : "", `last seen ${fmtStamp(u.lastSeen)}`)),
        h("div", { class: "list-actions" },
          h("label", { class: "mini-toggle" }, "Can play", enabled),
          h("label", { class: "mini-toggle", title: u.isAdmin ? "Admins can't be marked as children" : "" }, "Child", child))),
    ];
    if (u.isAdmin) parts.push(h("div", { class: "hint" }, "Admins can't be marked as children; limits never apply to them."));
    if (u.isChild) parts.push(childBlock(u, games, card, again, highlight));
    parts.push(notifyBlock(u, card, games, again, highlight));
    mount(card, parts);
    return card;
  }

  function childBlock(u, games, card, again, highlight) {
    const l = u.limits;
    const pt = u.playTime;
    const num = (v, label, id) => h("input", { type: "number", min: "0", max: "1440", step: "5", id, value: v === null ? "" : String(v), placeholder: "No limit", "aria-label": label });
    const time = (v, label, id) => h("input", { type: "time", id, value: v || "", "aria-label": label });
    const sid = u.id.replace(/[^A-Za-z0-9_-]/g, "");
    const mSchool = num(l.minutesSchool, "Minutes on school days", "ms-" + sid);
    const mWeekend = num(l.minutesWeekend, "Minutes at weekends", "mw-" + sid);
    const qsf = time(l.quietSchoolFrom, "School nights quiet from", "qsf-" + sid), qst = time(l.quietSchoolTo, "School nights quiet to", "qst-" + sid);
    const qwf = time(l.quietWeekendFrom, "Weekend quiet from", "qwf-" + sid), qwt = time(l.quietWeekendTo, "Weekend quiet to", "qwt-" + sid);
    const all = h("input", { type: "checkbox", checked: l.allowedGames === null });
    const gameBoxes = games.map((g) => ({ id: g.id, el: h("input", { type: "checkbox", checked: l.allowedGames === null || l.allowedGames.includes(g.id), disabled: l.allowedGames === null, dataset: { game: g.id } }), name: g.name }));
    all.addEventListener("change", () => gameBoxes.forEach((b) => { b.el.disabled = all.checked; if (all.checked) b.el.checked = true; }));
    const lb = h("select", { "aria-label": "Leaderboard", value: l.leaderboard }, LB.map(([v, label]) => h("option", { value: v }, label)));
    const err = h("div", { class: "error-text", role: "alert" });
    const save = h("button", { class: "btn-primary", type: "button", dataset: { save: "limits" } }, "Save limits");
    const minutes = (el) => (el.value.trim() === "" ? null : Number(el.value));
    save.addEventListener("click", async () => {
      err.textContent = "";
      const body = {
        minutesSchool: minutes(mSchool), minutesWeekend: minutes(mWeekend),
        quietSchoolFrom: qsf.value || null, quietSchoolTo: qst.value || null,
        quietWeekendFrom: qwf.value || null, quietWeekendTo: qwt.value || null,
        allowedGames: all.checked ? null : gameBoxes.filter((b) => b.el.checked).map((b) => b.id),
        leaderboard: lb.value,
      };
      for (const k of ["minutesSchool", "minutesWeekend"]) if (body[k] !== null && !Number.isInteger(body[k])) { err.textContent = "Minutes must be whole numbers."; return; }
      save.disabled = true;
      try { const r = await api(`api/admin/users/${encodeURIComponent(u.id)}/limits`, { method: "PUT", body }); toast(`${u.name}'s limits saved`); card.replaceWith(userCard(r, games, again, highlight)); }
      catch (e) { err.textContent = e.message; save.disabled = false; }
    });
    const extra = [15, 30, 60].map((m) => h("button", { class: "btn-secondary btn-small", type: "button", dataset: { extra: String(m) }, onclick: async (ev) => {
      ev.target.disabled = true;
      try { const r = await api(`api/admin/users/${encodeURIComponent(u.id)}/extra-time`, { method: "POST", body: { minutes: m } }); toast(`${u.name}: ${m} minutes added for today`); card.replaceWith(userCard(r, games, again, highlight)); }
      catch (e) { fail(e); ev.target.disabled = false; }
    } }, `Add ${m} minutes`));
    const played = pt.usedSeconds ? `Played ${fmtDuration(pt.usedSeconds)} today` : "Nothing played today yet";
    const today = pt.leftSeconds === null ? `${played} · no limit today`
      : `${played} · ${fmtLeft(pt.leftSeconds)}${pt.extraMinutes ? ` (including ${pt.extraMinutes} extra)` : ""}`;
    return h("div", { class: "sub-block" },
      h("div", { class: "notify-line" }, h("strong", null, today), pt.quiet ? h("span", { class: "chip" }, `quiet hours until ${pt.quietUntil}`) : null),
      h("div", { class: "notify-line", style: "margin-top:6px" }, h("span", { class: "notify-label" }, "Extra time today"), extra,
        h("button", { class: "btn-ghost btn-small", type: "button", dataset: { history: "1" }, onclick: () => historyDialog(u) }, "Play history")),
      h("div", { class: "limits-grid" },
        h("label", { class: "field" }, "Minutes a day — school days", mSchool),
        h("label", { class: "field" }, "Minutes a day — weekends and holidays", mWeekend),
        h("div", { class: "field" }, "Quiet hours — school nights", h("div", { class: "inline-times" }, qsf, "–", qst)),
        h("div", { class: "field" }, "Quiet hours — weekends", h("div", { class: "inline-times" }, qwf, "–", qwt)),
        h("div", { class: "field" }, "Games", h("div", { class: "check-list" }, h("label", null, all, "All games"),
          gameBoxes.map((b) => h("label", null, b.el, b.name)))),
        h("label", { class: "field" }, "Leaderboard", lb)),
      h("div", { class: "hint" }, "Empty minutes = no limit. Quiet hours like 21:00 – 07:00 run past midnight; the school-night times apply to the night before a school day."),
      err, h("div", { class: "actions" }, save));
  }

  async function historyDialog(u) {
    const body = h("div", null, spinner());
    openModal(`Play history — ${u.name}`, body);
    let d;
    try { d = await api(`api/admin/users/${encodeURIComponent(u.id)}/history`); }
    catch (e) { mount(body, errorCard(e)); return; }
    mount(body,
      h("div", { class: "hint" }, "The last 14 days. Time counts only while a game was running; Practice games count too."),
      d.days.length ? h("div", { class: "holiday-list" }, d.days.map((x) => h("span", { class: "chip" }, `${x.date}: ${x.minutes} min`))) : null,
      d.sessions.length ? h("div", { class: "table-wrap" }, h("table", { class: "data", id: "historyTable" },
        h("thead", null, h("tr", null, h("th", null, "Started"), h("th", null, "Game"), h("th", { class: "num" }, "Played"), h("th", { class: "num" }, "Score"))),
        h("tbody", null, d.sessions.map((s) => h("tr", null, h("td", null, fmtStamp(s.startedAt)),
          h("td", null, `${s.gameName} · ${s.modeLabel}${s.practice ? " · Practice" : ""}`),
          h("td", { class: "num" }, fmtDuration(s.seconds)), h("td", { class: "num" }, s.score === null ? "—" : fmtNum(s.score))))))) :
        h("div", { class: "empty" }, "Nothing played in the last 14 days."),
      d.extraTime.length ? h("div", { class: "hint" }, "Extra time given: ", d.extraTime.map((e) => `${e.date} +${e.minutes} min`).join(", ")) : null);
  }

  function notifyBlock(u, card, games, again, highlight) {
    const phones = u.ha.phones.length
      ? u.ha.phones.map((p) => h("span", { class: "chip notify-chip", title: p.service || "" }, "📱 ", p.label))
      : [h("span", { class: "hint" }, u.ha.known ? "No phone linked in Home Assistant (Settings → People → Track device)." : "Home Assistant's people couldn't be read.")];
    const chips = u.notify.length ? u.notify.map((s) => h("span", { class: "chip notify-chip" }, s,
      h("button", { class: "icon-btn", type: "button", "aria-label": `Remove ${s}`, onclick: async () => {
        try { const r = await api(`api/admin/users/${encodeURIComponent(u.id)}/notify/${encodeURIComponent(s)}`, { method: "DELETE" }); card.replaceWith(userCard(r, games, again, highlight)); }
        catch (e) { fail(e); }
      } }, "✕"))) : [h("span", { class: "hint" }, "none")];
    const input = h("input", { type: "text", placeholder: "notify.mobile_app_…", "aria-label": `Add a notify service for ${u.name}` });
    const add = h("button", { class: "btn-secondary btn-small", type: "button", onclick: async () => {
      if (!input.value.trim()) return;
      try { const r = await api(`api/admin/users/${encodeURIComponent(u.id)}/notify`, { method: "POST", body: { service: input.value.trim() } }); card.replaceWith(userCard(r, games, again, highlight)); }
      catch (e) { fail(e); }
    } }, "Add");
    const test = h("button", { class: "btn-ghost btn-small", type: "button", onclick: async () => {
      try { await api(`api/admin/users/${encodeURIComponent(u.id)}/notify/test`, { method: "POST", body: {} }); toast("Test sent"); } catch (e) { fail(e); }
    } }, "Send test");
    return h("div", { class: "sub-block" },
      h("div", { class: "notify-line" }, h("span", { class: "notify-label" }, "Phones"), h("div", { class: "chip-row" }, phones)),
      h("div", { class: "notify-line", style: "margin-top:6px" }, h("span", { class: "notify-label" }, "Also"), h("div", { class: "chip-row" }, chips)),
      h("div", { class: "notify-add", style: "margin-top:6px" }, input, add, test));
  }

  // =====================================================================
  // Storage
  // =====================================================================
  function renderStorage(box) {
    const file = h("input", { type: "file", accept: ".db,application/vnd.sqlite3,application/octet-stream", "aria-label": "Backup file" });
    const err = h("div", { class: "error-text" });
    const btn = h("button", { class: "btn-danger", type: "button" }, "Import database (.db)");
    btn.addEventListener("click", async () => {
      err.textContent = "";
      if (!file.files.length) { err.textContent = "Choose a .db backup file first."; return; }
      if (!(await confirmDialog("Import database", "This REPLACES the whole Household Arcade database with the uploaded file — every score, person, limit and App setting. There is no merge and no undo.", "Replace everything"))) return;
      const fd = new FormData();
      fd.append("file", file.files[0]);
      btn.disabled = true;
      try { await api("api/admin-storage-import-db", { method: "POST", formData: fd }); toast("Database restored"); file.value = ""; await refreshMe(); await refreshGames(); }
      catch (e) { err.textContent = e.message; }
      btn.disabled = false;
    });
    mount(box,
      h("div", { class: "card" }, h("h3", null, "Backup"),
        h("div", { class: "hint", style: "margin-bottom:10px" }, "A complete snapshot: everyone's scores and play history, children's limits, holidays, App settings and notify services. Home Assistant's own backups include it too."),
        h("a", { class: "btn-primary", href: "api/admin-storage-download-db", download: "", id: "downloadDb" }, "Download database (.db)")),
      h("div", { class: "card" }, h("h3", null, "Restore"),
        h("div", { class: "warn-box" }, "Importing replaces everything in the app with the contents of the file. A backup from an older version is brought up to date automatically. Download a backup first if you are unsure."),
        h("div", { class: "form-row" }, file, btn), err));
  }

  return { render };
})();
window.Admin = Admin;
