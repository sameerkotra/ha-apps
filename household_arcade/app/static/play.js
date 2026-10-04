"use strict";
/* The game page: start screen (mode, look, Practice), the canvas, on-screen controls, keyboard and
   game controller, pause and game-over overlays, play time for children, and the play session with the
   server (start → heartbeats → score / end).

   The game itself (games/, window.ArcadeGames) only draws on the canvas and follows input(); this file
   owns everything around it (the games contract, spec/GAMES.md). Keys are captured only while a game is
   on screen; the game never runs in the background: hiding the page or the back gesture pauses it. */

const Play = (() => {
  const BEAT_MS = 15000;
  // Two-player games (def.players === 2): the arrows, Space and Enter are player 1; W A S D and Q/E player 2.
  const KEYMAP2 = {
    ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Space: "fire", Enter: "fire",
    KeyW: "up2", KeyS: "down2", KeyA: "left2", KeyD: "right2", KeyQ: "fire2", KeyE: "fire2",
  };
  const KEYMAP = {
    ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
    KeyW: "up", KeyS: "down", KeyA: "left", KeyD: "right",
    Space: "fire", KeyX: "fire",
    KeyC: "alt", KeyF: "alt", ShiftLeft: "alt", ShiftRight: "alt",
  };
  const S = {
    gameId: null, server: null, def: null, inst: null, session: null,
    phase: "idle",            // idle (start screen) | starting | running | paused | over
    mode: null, practice: false,
    activeMs: 0, runSince: null,
    beatTimer: null, tickTimer: null,
    leftBase: null, activeAtBase: 0, warned: false, timeUpShown: false,
    held: new Set(), pads: {}, gpFrame: null,
    el: {}, resizeObs: null, lastFit: "",
  };

  // ---------- helpers ----------
  function keymap() { return S.def && S.def.players === 2 ? KEYMAP2 : KEYMAP; }
  const now = () => performance.now();
  function activeSeconds() { return (S.activeMs + (S.runSince !== null ? now() - S.runSince : 0)) / 1000; }
  function reduceMotion() {
    const media = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return !!((state.me && state.me.prefs.reduceMotion) || media);
  }
  function look() { return state.me ? state.me.prefs.effectiveLook : "modern"; }
  function sound() { return !!(state.me && state.me.prefs.sound); }
  function hand() { return state.me ? state.me.prefs.handedness : "right"; }
  function safe(fn) { try { return fn(); } catch (e) { console.error(e); return undefined; } }   // eslint-disable-line no-console
  function modeKey(id) { return "arcadeMode:" + id; }
  function modeLabel(id) { const m = S.server && S.server.modes.find((x) => x.id === id); return m ? m.label : id; }
  function isRunning() { return state.tab === "play" && S.phase === "running"; }
  function onScreen() { return state.tab === "play" && !!S.inst && (S.phase === "running" || S.phase === "paused"); }

  // ---------- page ----------
  async function render(gameId) {
    stopEverything(false);
    S.gameId = gameId;
    const root = $("#tab-play");
    mount(root, spinner());
    try {
      if (!state.games.length || !state.games.some((g) => g.id === gameId)) await refreshGames();
      if (!state.me) await refreshMe();
    } catch (e) { mount(root, errorCard(e, () => render(gameId))); return; }
    S.server = state.games.find((g) => g.id === gameId) || null;
    S.def = registryGame(gameId);
    if (!S.server) {
      mount(root, pageHead("Game"), h("div", { class: "card empty", id: "gameMissing" }, "That game isn't switched on for you.",
        h("div", null, h("button", { class: "btn-secondary", type: "button", style: "margin-top:10px", onclick: () => showTab("home") }, "Back to games"))));
      return;
    }
    if (!S.def) {
      mount(root, pageHead(S.server.name), h("div", { class: "card fatal", id: "gameMissing" }, `${S.server.name} couldn't be loaded in this browser. Try reloading the page.`));
      return;
    }
    const saved = lsGet(modeKey(gameId));
    S.mode = S.server.modes.some((m) => m.id === saved) ? saved : S.server.defaultMode;
    if (window.ArcadeSound) safe(() => ArcadeSound.setEnabled(sound()));

    const el = S.el = {};
    el.canvas = h("canvas", { id: "gameCanvas", width: "240", height: "300", "aria-label": `${S.server.name} game`, role: "img" });
    el.overlay = h("div", { class: "overlay", id: "gameOverlay" });
    el.stage = h("div", { class: "stage", id: "stage", tabindex: "0" }, el.canvas, el.overlay);
    el.soundBtn = h("button", { class: "icon-btn", type: "button", id: "soundBtn", onclick: toggleSound });
    el.pauseBtn = h("button", { class: "icon-btn", type: "button", id: "pauseBtn", title: "Pause (P)", "aria-label": "Pause", onclick: () => (S.phase === "paused" ? resume() : pause()) }, "⏸");
    el.timeSlot = h("span", { id: "playTime" });
    // How to play: folded away by default so the game gets the room; ? opens it (remembered per device).
    const helpText = (S.def.help || "") + (/paus/i.test(S.def.help || "") ? "" : " P or Esc pauses.");
    el.help = h("div", { class: "play-help", id: "playHelp", hidden: lsGet("arcade.helpOpen") !== "1" }, helpText.trim());
    el.helpBtn = h("button", { class: "icon-btn", type: "button", id: "helpBtn", title: "How to play", "aria-label": "How to play",
      "aria-controls": "playHelp", "aria-expanded": String(!el.help.hidden), onclick: toggleHelp }, "?");
    el.pad = buildPad();
    el.area = h("div", { class: "play-area", id: "playArea", dataset: { hand: hand(), controls: S.def.controls } }, el.stage, el.pad);
    mount(root,
      h("div", { class: "play-head" },
        h("button", { class: "icon-btn", type: "button", id: "backBtn", title: "Back to games", "aria-label": "Back to games", onclick: () => showTab("home") }, "‹"),
        h("h2", null, `${S.server.icon} ${S.server.name}`), el.timeSlot, el.helpBtn, el.soundBtn, el.pauseBtn),
      el.help,
      el.area);
    syncSoundBtn();
    wireStage();
    // Watch the page, not the stage: the stage's size is what fitStage() sets.
    if (window.ResizeObserver) { S.resizeObs = new ResizeObserver(() => resize()); S.resizeObs.observe(el.area); }
    S.lastFit = "";
    fitStage(true);
    requestAnimationFrame(() => fitStage(true));
    showStart();
  }

  // ---------- overlays ----------
  function setOverlay(...kids) {
    const o = S.el.overlay;
    if (!o) return;
    o.classList.remove("start");
    if (!kids.length) { o.hidden = true; clear(o); syncButtons(); return; }
    mount(o, kids);
    o.hidden = false;
    syncButtons();
  }
  function syncButtons() {
    if (!S.el.pauseBtn) return;
    S.el.pauseBtn.hidden = !(S.phase === "running" || S.phase === "paused");
    S.el.pauseBtn.textContent = S.phase === "paused" ? "▶" : "⏸";
    S.el.pauseBtn.setAttribute("aria-label", S.phase === "paused" ? "Resume" : "Pause");
    S.el.pauseBtn.title = S.phase === "paused" ? "Resume (P)" : "Pause (P)";
  }
  function bestLine() {
    const b = S.server.bestByMode ? S.server.bestByMode[S.mode] : null;
    return h("div", { id: "startBest" }, b !== null && b !== undefined ? ["Your best: ", h("strong", null, fmtNum(b))] : "No score yet in this mode");
  }
  function blockReason() {
    const me = state.me;
    if (me.disabled) return "An admin has switched you off in Household Arcade.";
    if (me.playTime.isChild && !me.playTime.canStart) return me.playTime.reason;
    return null;
  }
  // A still picture of the game in the chosen mode and look behind the start screen, so a look can be
  // tried before playing. It never runs: Play makes a fresh game.
  function preview() {
    destroyInstance();
    if (!S.def || !S.el.canvas) return;
    try {
      S.inst = S.def.create(S.el.canvas, { mode: S.mode, look: look(), sound: false, reduceMotion: reduceMotion(),
        handedness: hand(), seed: 1, best: (S.server.bestByMode && S.server.bestByMode[S.mode]) || 0 });
      safe(() => S.inst.resize());
    } catch (e) { console.error(e); S.inst = null; }   // eslint-disable-line no-console
  }
  function showStart(message) {
    S.phase = "idle";
    syncTime();
    preview();
    const modeSel = h("select", { id: "startMode", "aria-label": "Mode", value: S.mode },
      S.server.modes.map((m) => h("option", { value: m.id }, m.label)));
    modeSel.addEventListener("change", () => { S.mode = modeSel.value; lsSet(modeKey(S.gameId), S.mode); showStart(); });
    const lookSel = h("select", { id: "startLook", "aria-label": "Look", value: look() }, lookOptions(false));
    lookSel.addEventListener("change", async () => {
      const chosen = lookSel.value;
      if (S.inst) safe(() => S.inst.setLook(chosen));        // show it at once
      try { await savePrefs({ look: chosen }); if (S.inst) safe(() => S.inst.setLook(look())); }
      catch (e) { fail(e); }
    });
    const practice = h("input", { type: "checkbox", id: "practiceBox", checked: S.practice });
    practice.addEventListener("change", () => { S.practice = practice.checked; });
    const blocked = blockReason();
    const playBtn = h("button", { class: S.server.saved ? "btn-secondary" : "btn-primary", type: "button", id: "playBtn", disabled: !!blocked, onclick: () => startGame() }, S.server.saved ? "▶ New game" : "▶ Play");
    setOverlay(
      h("div", { class: "start-card" },
        h("h3", null, S.server.name),
        savedBlock(blocked),
        bestLine(),
        h("div", { class: "start-fields" },
          S.server.modes.length > 1 ? h("label", { class: "field" }, "Mode", modeSel) : null,
          h("label", { class: "field" }, "Look", lookSel)),
        h("label", { class: "mini-toggle", title: "Nothing is saved in Practice" }, practice, "Practice (not saved)"),
        message ? h("div", { class: "hint warn", role: "alert" }, message) : null,
        blocked ? h("div", { class: "hint warn", id: "startBlocked", role: "alert" }, blocked) : null,
        h("div", { class: "ov-row" }, playBtn)));
    S.el.overlay.classList.add("start");
    if (!blocked) playBtn.focus({ preventScroll: true });
  }
  function savedBlock(blocked) {
    const sv = S.server.saved;
    if (!sv) return null;
    const end = async (keepScore) => {
      const what = keepScore ? (sv.practice ? "It was Practice, so no score is kept." : `Its score (${fmtNum(sv.score)}) is kept.`) : "Its score isn't kept.";
      if (!(await confirmDialog(keepScore ? "End the saved game?" : "Throw the saved game away?", what, keepScore ? "End it" : "Throw away"))) return;
      try {
        const r = await api(`api/saved/${encodeURIComponent(S.gameId)}?keepScore=${keepScore ? "true" : "false"}`, { method: "DELETE" });
        toast(r.scoreSaved ? `Score ${fmtNum(r.score)} saved` : "Saved game removed");
        await refreshGames(); S.server = state.games.find((g) => g.id === S.gameId) || S.server; showStart();
      } catch (e) { fail(e); }
    };
    return h("div", { class: "saved-game", id: "savedGame" },
      h("div", null, h("strong", null, "Saved game"), ` · ${sv.modeLabel}${sv.practice ? " · Practice" : ""} · score ${fmtNum(sv.score)} · level ${sv.level} · ${fmtDuration(sv.seconds)}`),
      sv.canResume ? null : h("div", { class: "hint warn" }, "Made by an older version: it can't be continued, but its score can be kept."),
      h("div", { class: "ov-row" },
        sv.canResume ? h("button", { class: "btn-primary", type: "button", id: "continueBtn", disabled: !!blocked, onclick: () => startGame({ resume: true }) }, "▶ Continue") : null,
        h("button", { class: "btn-ghost btn-small", type: "button", id: "endSavedBtn", onclick: () => end(true) }, "End it"),
        h("button", { class: "btn-ghost btn-small", type: "button", id: "dropSavedBtn", onclick: () => end(false) }, "Throw away")));
  }

  function showPaused() {
    const canSave = !!(S.server.canSave && S.inst && S.inst.canSave);
    setOverlay(h("h3", null, "Paused"),
      h("div", { class: "hint" }, `${modeLabel(S.mode)}${S.practice ? " · Practice" : ""}`),
      h("div", { class: "ov-row" },
        h("button", { class: "btn-primary", type: "button", id: "resumeBtn", onclick: resume }, "▶ Resume")),
      h("div", { class: "ov-row" },
        canSave ? h("button", { class: "btn-secondary", type: "button", id: "saveBtn", onclick: saveForLater }, "💾 Save for later") : null,
        h("button", { class: "btn-ghost", type: "button", id: "quitBtn", onclick: quit }, S.practice ? "End game" : "End game (score kept)")));
    const b = $("#resumeBtn");
    if (b) b.focus({ preventScroll: true });
  }
  function showOver(result, saved, error) {
    const badges = [];
    if (saved && saved.householdRecord) badges.push(h("span", { class: "badge record" }, "🏆 New household record!"));
    else if (saved && saved.personalBest) badges.push(h("span", { class: "badge best" }, "⭐ New personal best!"));
    if (saved && saved.reason === "practice") badges.push(h("span", { class: "badge note" }, "Practice — not saved"));
    if (saved && saved.reason === "short") badges.push(h("span", { class: "badge note" }, "Under 3 seconds — not saved"));
    if (error) badges.push(h("span", { class: "badge note" }, "Not saved: " + error));
    if (result.stats && result.stats.won && result.stats.mazesCleared) badges.push(h("span", { class: "badge best" }, "🏁 Every maze cleared!"));
    if (saved && saved.levelsComing) badges.push(h("span", { class: "badge note", id: "levelsComing" }, "✨ New levels are on the way"));
    const blocked = blockReason();
    setOverlay(h("h3", null, "Game over"),
      h("div", { class: "big", id: "finalScore" }, fmtNum(result.score)),
      h("div", { class: "hint" }, `Level ${result.level || 1} · ${fmtDuration(result.seconds)} · ${modeLabel(S.mode)}`),
      badges.length ? h("div", { class: "ov-row", id: "overBadges" }, badges) : null,
      saved && saved.best !== null && saved.best !== undefined ? h("div", { class: "hint" }, "Your best: ", h("strong", null, fmtNum(saved.best))) : null,
      blocked ? h("div", { class: "hint warn", role: "alert", id: "overBlocked" }, blocked) : null,
      h("div", { class: "ov-row" },
        h("button", { class: "btn-primary", type: "button", id: "againBtn", disabled: !!blocked, onclick: startGame }, "↻ Play again"),
        h("button", { class: "btn-secondary", type: "button", id: "changeBtn", onclick: () => showStart() }, "Change mode"),
        h("button", { class: "btn-ghost", type: "button", onclick: () => showTab("home") }, "Games")));
    const again = $("#againBtn");
    if (again && !again.disabled) again.focus({ preventScroll: true });
  }

  // ---------- play time ----------
  function leftNow() {
    if (S.leftBase === null || S.leftBase === undefined) return null;
    return S.leftBase - (activeSeconds() - S.activeAtBase);
  }
  function syncTime() {
    if (!S.el.timeSlot) return;
    const pt = state.me && state.me.playTime;
    if (!pt || !pt.isChild) { clear(S.el.timeSlot); return; }
    if (S.session && (S.phase === "running" || S.phase === "paused")) {
      const left = leftNow();
      mount(S.el.timeSlot, left === null ? timeChip(pt)
        : h("span", { class: "time-chip" + (left <= 0 ? " out" : left <= 300 ? " low" : ""), id: "timeChip" }, "⏱ ",
          left <= 0 ? "Time's up" : `${Math.ceil(left / 60)} min left`));
      if (left !== null && left <= 300 && left > 0 && !S.warned) {
        S.warned = true;
        toast("5 minutes of play time left today.");
      }
      if (left !== null && left <= 0 && !S.timeUpShown) {
        S.timeUpShown = true;
        toast("Play time is up for today — this game finishes, then that's it until tomorrow.");
      }
    } else mount(S.el.timeSlot, timeChip(pt));
  }
  function applyPlayTime(pt) {
    if (!pt || !state.me) return;
    state.me.playTime = pt;
    S.leftBase = pt.leftSeconds;
    S.activeAtBase = activeSeconds();
    syncTime();
  }

  // ---------- session ----------
  async function startGame(how = {}) {
    if (S.phase === "starting") return;
    const prev = S.phase;
    S.phase = "starting";
    let session;
    try {
      session = await api("api/sessions", { method: "POST", body: how.resume ? { game: S.gameId, resume: true }
        : { game: S.gameId, mode: S.mode, practice: S.practice } });
    } catch (e) {
      S.phase = prev === "over" ? "over" : "idle";
      try { await refreshMe(); } catch (e2) { /* keep the old picture */ }
      showStart(e.message);
      return;
    }
    destroyInstance();
    if (session.saved) { S.mode = session.mode; S.practice = !!session.practice; session.resumedFrom = true; }
    S.session = session;
    S.activeMs = 0; S.runSince = null;
    S.warned = false; S.timeUpShown = false;
    S.leftBase = session.playTime.leftSeconds; S.activeAtBase = 0;
    state.me.playTime = session.playTime;
    const opts = {
      mode: S.mode, look: look(), sound: sound(), reduceMotion: reduceMotion(), handedness: hand(), seed: session.seed,
      best: session.best || 0,
      levels: session.levels || undefined,   // the level list this game plays through (fixed for the game)
      restore: session.saved ? { state: session.saved.state, seconds: session.saved.seconds } : undefined,
      onScore: () => {},
      onEnd: (result) => finish(result),
      onEvent: (type, data) => { if (type === "pause") gamePaused(!!(data && data.paused)); },
    };
    try {
      S.inst = S.def.create(S.el.canvas, opts);
      setOverlay();
      S.phase = "running";
      S.runSince = now();
      safe(() => S.inst.resize());
      S.inst.start();
    } catch (e) {
      console.error(e);      // eslint-disable-line no-console
      S.phase = "idle";
      endSession(false);
      showStart(`${S.server.name} couldn't start: ${e.message || e}`);
      return;
    }
    S.beatTimer = setInterval(beat, BEAT_MS);
    S.tickTimer = setInterval(syncTime, 1000);
    syncButtons();
    syncTime();
    startGamepad();
    S.el.stage.focus({ preventScroll: true });
  }
  async function beat() {
    if (!S.session || !(S.phase === "running" || S.phase === "paused")) return;
    try {
      const level = S.inst ? safe(() => S.inst.level) : undefined;
      const r = await api(`api/sessions/${S.session.id}/beat`, { method: "POST", body: { activeSeconds: activeSeconds(), level } });
      if (r && r.playTime) applyPlayTime(r.playTime);
    } catch (e) { /* the next beat tries again */ }
  }
  // Leaving the page in the middle of a game ends it, and its score so far counts like a finished game.
  function endWithScore(keepalive) {
    if (!S.session) return;
    const r = S.inst ? safe(() => currentResult()) : null;
    if (!r || (r.score <= 0 && r.seconds < 3)) { endSession(keepalive); return; }
    const id = S.session.id;
    S.session = null;
    api("api/scores", { method: "POST", body: { sessionId: id, score: r.score, level: r.level, seconds: r.seconds }, keepalive })
      .catch(() => { /* the game is over either way */ });
  }
  function endSession(keepalive) {
    if (!S.session) return;
    const id = S.session.id;
    const secs = activeSeconds();
    S.session = null;
    api(`api/sessions/${id}/end`, { method: "POST", body: { activeSeconds: secs }, keepalive }).catch(() => { /* housekeeping closes it */ });
  }
  async function finish(result) {
    if (S.phase !== "running" && S.phase !== "paused") return;
    if (S.runSince !== null) { S.activeMs += now() - S.runSince; S.runSince = null; }
    S.phase = "over";
    stopTimers();
    releaseAll();
    const session = S.session;
    S.session = null;
    result = result || {};
    const seconds = typeof result.seconds === "number" ? result.seconds : S.activeMs / 1000;
    let saved = null, error = null;
    if (session) {
      try {
        saved = await api("api/scores", { method: "POST", body: { sessionId: session.id, score: result.score || 0, level: result.level || 1, seconds } });
        if (saved.playTime) state.me.playTime = saved.playTime;
        if (saved.best !== null && saved.best !== undefined && S.server.bestByMode) S.server.bestByMode[S.mode] = saved.best;
      } catch (e) { error = e.message; }
    }
    try { await refreshMe(); } catch (e) { /* keep the old picture */ }
    if (state.tab !== "play" || S.phase !== "over") return;
    syncTime();
    showOver({ score: result.score || 0, level: result.level || 1, seconds, stats: result.stats || {} }, saved, error);
  }

  // ---------- pause / resume ----------
  function markPaused() {
    if (S.runSince !== null) { S.activeMs += now() - S.runSince; S.runSince = null; }
    S.phase = "paused";
    releaseAll();
    showPaused();
    beat();
  }
  function pause() {
    if (S.phase !== "running" || !S.inst) return;
    safe(() => S.inst.pause());
    markPaused();
  }
  function resume() {
    if (S.phase !== "paused" || !S.inst) return;
    S.phase = "running";
    S.runSince = now();
    setOverlay();
    safe(() => S.inst.resume());
    S.el.stage.focus({ preventScroll: true });
    beat();
  }
  function gamePaused(paused) {
    // the game's own pause (e.g. it noticed the page hiding) — keep the shell in step
    if (paused && S.phase === "running") markPaused();
    else if (!paused && S.phase === "paused") { S.phase = "running"; S.runSince = now(); setOverlay(); }
  }
  // End the game where it is: the score so far is saved like a finished game.
  function quit() {
    if (!S.inst || !(S.phase === "running" || S.phase === "paused")) return;
    const r = currentResult();
    finish(Object.assign(r, { stats: { quit: true } }));
  }
  function currentResult() {
    return { score: S.inst.score || 0, level: S.inst.level || 1, seconds: S.inst.seconds || 0 };
  }

  // Put the game aside to finish later (one saved game per kind; saving replaces the last one, whose score is kept).
  async function saveForLater() {
    if (!S.inst || !S.session || S.phase !== "paused") return;
    const old = S.server.saved;
    if (old && !S.session.resumedFrom) {
      const ok = await confirmDialog("Replace your saved game?",
        `You already have a saved ${S.server.name} game (${old.modeLabel}, score ${fmtNum(old.score)}). Saving this one replaces it; ` +
        (old.practice ? "it was Practice, so its score isn't kept." : "its score is kept as if you had ended it there."), "Save this one");
      if (!ok) return;
    }
    const snap = safe(() => S.inst.save());
    if (!snap) { toast("This game can't be saved.", true); return; }
    let r;
    try {
      r = await api(`api/sessions/${S.session.id}/save`, { method: "POST", body: Object.assign({ stateVersion: S.def.stateVersion }, snap) });
    } catch (e) { fail(e); return; }
    if (S.runSince !== null) { S.activeMs += now() - S.runSince; S.runSince = null; }
    S.session = null;
    stopTimers(); releaseAll();
    if (r.playTime && state.me) state.me.playTime = r.playTime;
    S.server.saved = r.saved;
    toast(r.replaced && r.replaced.scoreSaved ? `Saved. Your earlier game's score (${fmtNum(r.replaced.score)}) was kept.` : "Saved — continue it any time.");
    try { await refreshGames(); S.server = state.games.find((g) => g.id === S.gameId) || S.server; } catch (e) { /* keep the old picture */ }
    if (state.tab === "play") showStart();
  }

  // ---------- leaving ----------
  function stopTimers() {
    clearInterval(S.beatTimer); clearInterval(S.tickTimer);
    S.beatTimer = S.tickTimer = null;
    if (S.gpFrame) cancelAnimationFrame(S.gpFrame);
    S.gpFrame = null;
  }
  function destroyInstance() {
    if (S.inst) safe(() => S.inst.destroy());
    S.inst = null;
  }
  function stopEverything(keepalive) {
    if (S.runSince !== null) { S.activeMs += now() - S.runSince; S.runSince = null; }
    if (S.phase === "running" || S.phase === "paused") endWithScore(keepalive);
    stopTimers();
    releaseAll();
    destroyInstance();
    if (S.resizeObs) { S.resizeObs.disconnect(); S.resizeObs = null; }
    S.phase = "idle";
    S.session = null;
  }
  function leave() { stopEverything(false); }

  // ---------- input ----------
  function input(action, down) {
    if (!S.inst || S.phase !== "running") return;
    safe(() => S.inst.input(action, down));
  }
  function releaseAll() {
    if (S.inst) S.held.forEach((a) => safe(() => S.inst.input(a, false)));
    S.held.clear();
    document.querySelectorAll(".pad .pressed").forEach((b) => b.classList.remove("pressed"));
  }
  function holdButton(btn, action) {
    const up = (e) => {
      if (!btn.classList.contains("pressed")) return;
      btn.classList.remove("pressed");
      S.held.delete(action);
      input(action, false);
      if (e) e.preventDefault();
    };
    btn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      try { btn.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      btn.classList.add("pressed");
      S.held.add(action);
      if (S.phase === "running") input(action, true);
    });
    btn.addEventListener("pointerup", up);
    btn.addEventListener("pointercancel", up);
    btn.addEventListener("lostpointercapture", up);
    btn.addEventListener("contextmenu", (e) => e.preventDefault());
    // keyboard users: Enter / Space on a focused pad button is a tap
    btn.addEventListener("click", (e) => { if (e.detail === 0) { input(action, true); setTimeout(() => input(action, false), 80); } });
  }
  // The on-screen controls, centred under (or beside) the game. Pause is the button at the top.
  function buildPad() {
    if (S.def.controls === "paddle") {
      const strip = h("div", { class: "drag-strip", id: "dragStrip", "aria-hidden": "true" }, "◀ drag here or on the game ▶");
      wireDragStrip(strip);
      const label = S.def.padLabel || "Launch";
      const launch = h("button", { class: "pad-btn launch", type: "button", id: "launchBtn", "aria-label": label }, label);
      holdButton(launch, "fire");
      return h("div", { class: "pad paddle-pad", id: "pad" }, launch, strip);
    }
    if (S.def.controls === "buttons" || S.def.controls === "touch") {
      const list = S.def.buttons || [];
      // buttons with a place [col, row, colSpan, rowSpan] are laid out on a grid; otherwise one wrapping row
      const grid = list.length > 0 && list.every((b) => b.place);
      const row = h("div", { class: "btn-pad" + (grid ? " grid" : ""), id: "btnPad", role: "group", "aria-label": "Game buttons" },
        ...list.map((b) => {
          const el = h("button", { class: "pad-btn" + (b.wide ? " wide" : "") + (b.action === "fire" ? " launch" : ""), type: "button",
            "aria-label": b.aria || b.label, dataset: { action: b.action } }, b.label);
          if (grid) el.style.gridArea = `${b.place[1]} / ${b.place[0]} / span ${b.place[3]} / span ${b.place[2]}`;
          holdButton(el, b.action);
          return el;
        }));
      return h("div", { class: "pad buttons-pad" + (list.length ? "" : " empty"), id: "pad" }, row);
    }
    const btn = (dir, label, glyph) => { const b = h("button", { class: dir, type: "button", "aria-label": label, dataset: { dir } }, glyph); holdButton(b, dir); return b; };
    return h("div", { class: "pad", id: "pad" },
      h("div", { class: "dpad", id: "dpad", role: "group", "aria-label": "Arrow pad" },
        btn("up", "Up", "▲"), btn("left", "Left", "◀"), btn("right", "Right", "▶"), btn("down", "Down", "▼")));
  }
  function canvasPoint(e) {
    const r = S.el.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  function wireDragStrip(strip) {
    const send = (kind, e) => {
      if (!S.inst || S.phase !== "running") return;
      const sr = strip.getBoundingClientRect();
      const cr = S.el.canvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(cr.width, ((e.clientX - sr.left) / Math.max(1, sr.width)) * cr.width));
      safe(() => S.inst.pointer(kind, x, cr.height - 1));
    };
    strip.addEventListener("pointerdown", (e) => { e.preventDefault(); try { strip.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ } send("move", e); });
    strip.addEventListener("pointermove", (e) => { if (e.buttons || e.pointerType !== "mouse") send("move", e); });
    strip.addEventListener("pointerup", (e) => send("up", e));
  }
  function wireStage() {
    const c = S.el.canvas;
    let swipe = null;
    c.addEventListener("pointerdown", (e) => {
      if (S.phase !== "running") return;
      e.preventDefault();
      try { c.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      S.el.stage.focus({ preventScroll: true });
      if (S.def.controls !== "dpad") { const [x, y] = canvasPoint(e); safe(() => S.inst.pointer("down", x, y)); }
      else swipe = { x: e.clientX, y: e.clientY };
    });
    c.addEventListener("pointermove", (e) => {
      if (S.phase !== "running") return;
      if (S.def.controls === "paddle" || ((S.def.controls === "buttons" || S.def.controls === "touch") && (e.buttons || e.pointerType !== "mouse"))) {
        const [x, y] = canvasPoint(e); safe(() => S.inst.pointer("move", x, y)); return;
      }
      if (S.def.controls !== "dpad") return;
      if (!swipe) return;
      const dx = e.clientX - swipe.x, dy = e.clientY - swipe.y;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 14) return;
      const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
      input(dir, true); input(dir, false);
      swipe = { x: e.clientX, y: e.clientY, moved: true };
    });
    const up = (e) => {
      // dpad games with a tap action (Road Hop: hop forward): a touch that didn't swipe
      if (e.type === "pointerup" && swipe && !swipe.moved && S.phase === "running" && S.def.controls === "dpad" && S.def.tap) {
        input(S.def.tap, true); input(S.def.tap, false);
      }
      swipe = null;
      if (S.phase === "running" && S.def.controls !== "dpad") { const [x, y] = canvasPoint(e); safe(() => S.inst.pointer("up", x, y)); }
    };
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", up);
    c.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  function typingTarget(t) {
    return t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable);
  }
  document.addEventListener("keydown", (e) => {
    if (state.tab !== "play" || !S.server || modalStack.length || e.ctrlKey || e.metaKey || e.altKey) return;
    const pauseKey = e.code === "KeyP" || e.key === "Escape";
    if (S.phase === "running") {
      if (pauseKey) { e.preventDefault(); pause(); return; }
      const action = keymap()[e.code];
      if (!action) return;
      e.preventDefault();                 // arrows and Space don't scroll Home Assistant's page
      if (S.held.has(action)) return;
      S.held.add(action);
      input(action, true);
      return;
    }
    if (S.phase === "paused") {
      if (pauseKey || e.code === "Space" || e.key === "Enter") {
        if (e.target && e.target.tagName === "BUTTON" && !pauseKey) return;   // the focused button handles it
        e.preventDefault(); resume();
      } else if (keymap()[e.code]) e.preventDefault();
      return;
    }
    if ((S.phase === "idle" || S.phase === "over") && (e.code === "Space" || e.key === "Enter")) {
      if (typingTarget(e.target) || (e.target && e.target.tagName === "BUTTON")) return;
      const b = $("#playBtn") || $("#againBtn");
      if (b && !b.disabled) { e.preventDefault(); startGame(); }
    }
  });
  document.addEventListener("keyup", (e) => {
    const action = keymap()[e.code];
    if (!action || !S.held.has(action)) return;
    S.held.delete(action);
    if (state.tab === "play") e.preventDefault();
    if (S.inst) safe(() => S.inst.input(action, false));
  });

  // the game never runs in the background: hiding the page (another tab or app, screen off) pauses it
  document.addEventListener("visibilitychange", () => { if (document.hidden) pause(); });
  window.addEventListener("pagehide", () => { pause(); if (S.session) endWithScore(true); });
  window.addEventListener("blur", () => releaseAll());

  // ---------- game controller (optional) ----------
  const GP = { 12: "up", 13: "down", 14: "left", 15: "right", 0: "fire", 1: "fire", 2: "alt", 3: "alt" };
  function startGamepad() {
    if (!navigator.getGamepads || S.gpFrame) return;
    const prev = {};
    const loop = () => {
      S.gpFrame = null;
      if (!onScreen() || document.hidden) return;
      const pads = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean) : [];
      const now_ = {};
      // In a two-player game the second controller is player 2 (up2, down2 …); otherwise every controller is player 1.
      const two = S.def && S.def.players === 2;
      pads.forEach((p, n) => {
        const suffix = two && n === 1 ? "2" : "";
        for (const [i, a] of Object.entries(GP)) if (p.buttons[i] && p.buttons[i].pressed) now_[a + suffix] = true;
        if (p.axes.length >= 2) {
          if (p.axes[0] < -0.5) now_["left" + suffix] = true;
          if (p.axes[0] > 0.5) now_["right" + suffix] = true;
          if (p.axes[1] < -0.5) now_["up" + suffix] = true;
          if (p.axes[1] > 0.5) now_["down" + suffix] = true;
        }
        if (p.buttons[9] && p.buttons[9].pressed) now_.start = true;
      });
      if (now_.start && !prev.start) { if (S.phase === "paused") resume(); else pause(); }
      for (const a of ["up", "down", "left", "right", "fire", "alt", "up2", "down2", "left2", "right2", "fire2"]) {
        if (now_[a] && !prev[a]) input(a, true);
        if (!now_[a] && prev[a]) input(a, false);
      }
      Object.keys(prev).forEach((k) => delete prev[k]);
      Object.assign(prev, now_);
      S.gpFrame = requestAnimationFrame(loop);
    };
    if (pads().length) S.gpFrame = requestAnimationFrame(loop);
    function pads() { try { return Array.from(navigator.getGamepads()).filter(Boolean); } catch (e) { return []; } }
  }
  window.addEventListener("gamepadconnected", () => { if (onScreen()) startGamepad(); });

  // ---------- misc ----------
  function syncSoundBtn() {
    if (!S.el.soundBtn) return;
    const on = sound();
    S.el.soundBtn.textContent = on ? "🔊" : "🔇";
    S.el.soundBtn.setAttribute("aria-label", on ? "Sound on — turn off" : "Sound off — turn on");
    S.el.soundBtn.title = on ? "Sound on" : "Sound off";
    S.el.soundBtn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  async function toggleSound() {
    const on = !sound();
    if (window.ArcadeSound) safe(() => ArcadeSound.setEnabled(on));
    if (S.inst) safe(() => S.inst.setSound(on));
    state.me.prefs.sound = on;
    syncSoundBtn();
    try { await savePrefs({ sound: on }); } catch (e) { fail(e); }
  }
  function toggleHelp() {
    const el = S.el;
    if (!el.help) return;
    el.help.hidden = !el.help.hidden;
    el.helpBtn.setAttribute("aria-expanded", String(!el.help.hidden));
    lsSet("arcade.helpOpen", el.help.hidden ? "0" : "1");
    fitStage(true);
  }
  // ---------- fitting the game to the screen ----------
  // The game keeps its 4:5 shape and is made as big as the window allows: a folded phone, an
  // unfolded one, a phone on its side, a tablet or a browser window of any size. The controls go
  // under the game or beside it, whichever leaves the game bigger. Runs again whenever the window,
  // the visible viewport (a folding phone opening or closing, the address bar) or the page changes.
  const RATIO = 240 / 300;
  function px(v) { const n = parseFloat(v); return isFinite(n) ? n : 0; }
  function fitStage(force) {
    const el = S.el;
    if (!el.area || !el.stage || !el.area.isConnected) return;
    const vv = window.visualViewport;
    const vh = Math.floor(vv ? vv.height : window.innerHeight);
    // a little short of the full width, so rounding or a scrollbar never pushes a button off the edge
    const availW = Math.floor(Math.min(el.area.clientWidth, document.documentElement.clientWidth - el.area.getBoundingClientRect().left) - 6);
    const key = `${vh}x${availW}x${window.innerWidth}`;
    if (!force && key === S.lastFit) return;
    S.lastFit = key;
    // room under the play area: the page's own bottom padding (the help, when open, sits above it)
    const main = el.area.closest("main"), col = el.area.closest(".main-col");
    let below = 16;
    if (main) below += px(getComputedStyle(main).paddingBottom);
    if (col) below += px(getComputedStyle(col).paddingBottom);
    const top = el.area.getBoundingClientRect().top + (window.scrollY || 0);
    const availH = Math.max(160, vh - top - below);
    const gap = px(getComputedStyle(el.area).columnGap) || 14;
    const hasPad = !!(el.pad && !el.pad.classList.contains("empty"));
    const tryLayout = (layout) => {
      el.area.dataset.layout = layout;
      // the controls' real size: the box around every button they draw (a button grid can be
      // wider than the pad's own box), and never less than the pad itself
      const [pw, ph] = hasPad ? padExtent() : [0, 0];
      if (layout === "below") return Math.min(availW, (availH - (hasPad ? ph + gap : 0)) * RATIO);
      return Math.min(availW - (hasPad ? pw + gap : 0), availH * RATIO);
    };
    const wBelow = tryLayout("below"), wSide = tryLayout("side");
    const layout = wSide > wBelow + 8 ? "side" : "below";
    el.area.dataset.layout = layout;
    const w = Math.max(200, Math.floor(layout === "side" ? wSide : wBelow));
    el.stage.style.width = `${w}px`;
    // Last check against the real screen: if any control still ends past the right or bottom edge
    // (a browser that sizes things its own way), shrink the game by that much and look again.
    for (let i = 0; i < 3 && hasPad; i++) {
      const vw = document.documentElement.clientWidth;
      let over = 0;
      for (const b of el.pad.querySelectorAll("button, .drag-strip")) {
        const r = b.getBoundingClientRect();
        if (r.width) over = Math.max(over, r.right - (vw - 4), layout === "below" ? r.bottom - (vh - 4) : 0);
      }
      const cur = parseFloat(el.stage.style.width);
      if (over <= 0 || cur <= 200) break;
      el.stage.style.width = `${Math.max(200, Math.floor(cur - over * (layout === "below" ? RATIO : 1) - 2))}px`;
    }
    if (S.inst) safe(() => S.inst.resize());
  }
  function padExtent() {
    const el = S.el, box = el.pad.getBoundingClientRect();
    let l = box.left, r = box.right, t = box.top, b = box.bottom;
    for (const e of el.pad.querySelectorAll("button, .drag-strip, .dpad, .btn-pad")) {
      const x = e.getBoundingClientRect();
      if (!x.width) continue;
      l = Math.min(l, x.left); r = Math.max(r, x.right); t = Math.min(t, x.top); b = Math.max(b, x.bottom);
    }
    return [Math.ceil(r - l), Math.ceil(b - t)];
  }
  function resize() { fitStage(false); if (S.inst) safe(() => S.inst.resize()); }
  function themeChanged() { if (S.inst) safe(() => S.inst.setLook(look())); }
  window.addEventListener("resize", resize);
  window.addEventListener("orientationchange", () => setTimeout(() => fitStage(true), 150));
  if (window.visualViewport) window.visualViewport.addEventListener("resize", resize);

  return { render, leave, pause, resume, isRunning, resize, themeChanged, _state: S };
})();
window.Play = Play;
