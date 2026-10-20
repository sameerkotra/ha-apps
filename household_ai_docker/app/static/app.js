/* Household AI — the admin page (SPEC.md §2).
 *
 * Tabs: Status (the model server, the machine, what is loaded, the queue, On my network, the log), Models
 * (downloads in progress, recommended list, delete, Keep loaded), Use it (in other apps: the values to paste,
 * the callers seen, Answer first), Access keys, App settings (common/settings.js), Usage, Storage.
 * Everything is built as DOM nodes (UI.h), never HTML strings. Status refreshes every 5 s while open,
 * updating its parts in place. Each tab has an address (#/models …), so a reload keeps the tab and Back
 * returns to the tab before (and then Status).
 */
(function () {
  "use strict";
  const { h, $, $$, clear, mount } = UI;

  const api = UI.makeApi({
    networkError: "Couldn't reach the app. Check the connection.",
    message: (body, res) => UI.errorMessage(body && body.detail, res.status),
  });
  const toast = (msg, error) => UI.toast(msg, { error: !!error });
  const GB = 1024 ** 3;
  const gb = (n) => (n || n === 0 ? (n / GB).toFixed(1) + " GB" : "—");
  const kindLabel = (k) => (k === "vision" ? "Vision" : k === "both" ? "Text + vision" : "Text");
  // A tab that couldn't load: the error and a Retry button (instead of a toast over a blank or stale tab).
  const fail = (box, e, retry) => mount(box, UI.errorBox(e, retry));

  HouseholdTheme.bindSelect($("#theme-select"));

  const TABS = ["status", "models", "use", "keys", "settings", "usage", "storage"];
  // "#/models" → "models"; anything else (no address, an unknown one) → Status
  const tabOf = (hash) => { const m = /^#\/([a-z]+)$/.exec(hash || ""); return m && TABS.includes(m[1]) ? m[1] : "status"; };
  let current = "status";
  let timer = null;

  function openTab(tab) {
    current = tab;
    $$("#tabs .tab").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle("active", on);
      if (on) { b.setAttribute("aria-current", "page"); b.scrollIntoView({ block: "nearest", inline: "nearest" }); }
      else b.removeAttribute("aria-current");
    });
    $$(".tab-body").forEach((s) => { s.hidden = s.id !== `tab-${tab}`; });
    clearInterval(timer);
    timer = null;
    ({ status: loadStatus, models: loadModels, use: loadUse, keys: loadKeys, settings: loadSettings,
       usage: loadUsage, storage: () => {} })[tab]();
    if (tab === "status") timer = setInterval(() => { if (!document.hidden) loadStatus(true); }, 5000);
  }
  // Tabs are addresses: BackNav.go remembers the tab you leave, so Back returns to it (Status starts a new trail).
  $$("#tabs .tab").forEach((b) => b.addEventListener("click", () => BackNav.go("#/" + b.dataset.tab)));
  window.addEventListener("hashchange", () => {
    if ($("#tabs").hidden) return;
    const hash = location.hash || "";
    if (hash && !hash.startsWith("#/")) return;          // an in-page link (e.g. a settings group) isn't a tab
    openTab(tabOf(hash));
  });

  function card(title, ...body) { return h("div", { class: "card" }, title ? h("h3", null, title) : null, ...body); }
  function kv(label, value, hint) {
    return h("div", { class: "kv" }, h("span", { class: "k" }, label), h("span", { class: "v" }, value),
      hint ? h("span", { class: "hint kv-hint" }, hint) : null);
  }
  function button(label, onClick, cls) {
    const b = h("button", { type: "button", class: cls || "btn-secondary" }, label);
    b.addEventListener("click", async () => {
      b.disabled = true;
      try { await onClick(); } catch (e) { toast(e.message, true); }
      b.disabled = false;
    });
    return b;
  }

  // ---------------------------------------------------------------- Status
  const STATE = { ready: "Ready", starting: "Starting", stopped: "Stopped" };

  let statusView = null;          // the Status tab's parts, kept between refreshes and updated in place

  // Replace a part's content only when it changed, so a refresh keeps focus, selections and open sections.
  function patch(slot, ...kids) {
    const next = h("div", null, ...kids);
    const a = Array.from(slot.childNodes), b = Array.from(next.childNodes);
    if (a.length === b.length && a.every((n, i) => n.isEqualNode(b[i]))) return;
    mount(slot, b);
  }
  // The log keeps its scroll position (and follows new lines only while scrolled to the bottom), and isn't
  // touched while text in it is selected.
  function setLog(pre, text) {
    if (pre.textContent === text) return;
    const sel = window.getSelection ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && pre.contains(sel.anchorNode)) return;
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 8;
    const top = pre.scrollTop;
    pre.textContent = text;
    pre.scrollTop = atBottom ? pre.scrollHeight : top;
  }
  function kpi(label, value, attrs) {
    return h("div", Object.assign({ class: "kpi" }, attrs || {}), h("span", { class: "kpi-label" }, label),
      h("span", { class: "kpi-value" }, value));
  }
  function buildStatus(box, s) {
    const v = { strip: h("div", { class: "summary-strip" }), server: h("div"), loaded: h("div"), queue: h("div"),
      machine: h("div"), network: h("div"), pre: h("pre", { class: "log-lines" }) };
    const net = s.network;
    const machine = h("details", { class: "card fold", open: !!(s.machine.hot || s.machine.sdCard) },
      h("summary", null, "This machine"), v.machine);
    const network = h("details", { class: "card fold", open: !!(net.known && net.port && !net.requireKey) },
      h("summary", null, "On my network"), v.network);
    const log = h("details", { class: "card log" }, h("summary", null, "Model server log (last 50 lines)"), v.pre);
    log.addEventListener("toggle", () => { if (log.open) v.pre.scrollTop = v.pre.scrollHeight; });
    mount(box, v.strip,
      card("Model server", v.server),
      card("Loaded now", v.loaded, v.queue),
      machine, network, log);
    return v;
  }

  async function loadStatus(quiet) {
    const box = $("#tab-status");
    if (!quiet) { statusView = null; UI.loading(box); }
    let s, log;
    try { [s, log] = await Promise.all([api("api/status"), api("api/log")]); }
    catch (e) { if (!quiet || !statusView) { statusView = null; fail(box, e, () => loadStatus()); } return; }
    const sv = s.server;
    const stateText = (STATE[sv.state] || sv.state) + (sv.reason ? ` — ${sv.reason}` : "");
    const onSwitch = h("input", { type: "checkbox", checked: sv.on });
    onSwitch.addEventListener("change", async () => {
      onSwitch.disabled = true;
      try { await api("api/server", { method: "PUT", body: { on: onSwitch.checked } }); }
      catch (e) { toast(e.message, true); onSwitch.checked = !onSwitch.checked; }
      onSwitch.disabled = false;
      loadStatus(true);
    });
    const m = s.machine;
    const loaded = s.loaded.length
      ? h("ul", { class: "plain-list" }, s.loaded.map((l) => h("li", null, h("code", null, l.name), " — ", l.rule)))
      : h("p", { class: "hint" }, sv.state === "ready" ? "No model is loaded." : "—");
    const sched = s.schedule;
    const q = s.queue;
    const queueText = q.running
      ? `${q.running.caller} is using ${q.running.model} (${Math.round(q.running.seconds)} s); ${q.waiting.length} waiting.`
      : "Idle.";
    const net = s.network;
    let netBody;
    if (!net.known) netBody = h("p", { class: "hint" }, "Couldn't read the app's network settings.");
    else if (!net.port) netBody = h("p", null, "The gateway isn't published.");
    else netBody = h("p", { class: net.requireKey ? null : "warn-text" },
      `Published on port ${net.port} of this Docker host: Home Assistant and other computers on your network use the model there.`,
      net.requireKey ? "" : " Anyone on your network can use the model and its CPU — turn on Require an access key (App settings → Access). Plain HTTP shows a key to anyone listening on the network.");
    const keepPick = sched.kept ? `${sched.kept} — kept ${sched.dayStart}–${sched.dayEnd}` : "None";

    const v = statusView && box.contains(statusView.strip) ? statusView : (statusView = buildStatus(box, s));
    const names = s.loaded.map((l) => l.name);
    patch(v.strip,
      kpi("Model server", h("span", { class: `pill state-${sv.state}` }, STATE[sv.state] || sv.state)),
      kpi("Loaded model", names.length ? names.join(", ") : sv.state === "ready" ? "None" : "—"),
      kpi("Queue", q.running ? `Busy · ${q.waiting.length} waiting` : "Idle"),
      kpi("Memory free", gb(m.memory.free), { title: `of ${gb(m.memory.total)}` }));
    patch(v.server,
        h("label", { class: "switch-row" }, onSwitch, " ", h("strong", null, "On")),
        kv("State", stateText),
        sv.problem ? h("p", { class: "error" }, sv.problem) : null,
        kv("Ollama", sv.version || "—"),
        sv.state === "ready" ? h("p", null, button("Unload now", async () => {
          const r = await api("api/unload", { method: "POST" });
          toast(r.unloaded.length ? `Unloaded ${r.unloaded.join(", ")} until the next request.` : "Nothing was loaded.");
          loadStatus(true);
        })) : null,
        !sv.on ? h("p", { class: "hint" }, "Off: the model server isn't running and the other apps get \"unreachable\". Nothing is downloaded until you choose a model on the Models tab.") : null);
    patch(v.loaded, loaded,
        kv("Keep loaded", keepPick), kv("Now", sched.day ? "Daytime" : "Night"),
        sched.manualUnload ? h("p", { class: "hint" }, "Unloaded by hand: nothing loads again until the next request or the start of the day.") : null);
    patch(v.queue,
        kv("Queue", queueText),
        s.switchesToday >= 20 ? h("p", { class: "warn-text" }, `Models were switched ${s.switchesToday} times today — consider using one model in more apps, or 2 models loaded at once.`) : null);
    patch(v.machine,
        kv("Memory", `${gb(m.memory.free)} free of ${gb(m.memory.total)}`),
        kv("CPU cores", String(m.cores)),
        kv("Disk (/data)", `${gb(m.disk.free)} free of ${gb(m.disk.total)}`, m.sdCard ? "On an SD card: loading a model takes up to a minute. An SSD is much faster." : null),
        m.temperature !== null ? kv("CPU temperature", `${m.temperature} °C`, m.hot ? "Hot: the CPU may slow down while a model works. A fan or a case with cooling helps." : null) : null,
        h("p", { class: "hint" }, "Without a graphics card a model is slow: seconds for a short text answer, from about 30 seconds to several minutes for a receipt photo or a statement page. With an NVIDIA card given to the container (docker-compose.yml), Ollama uses it on its own and answers are many times faster."));
    patch(v.network, netBody,
        h("p", { class: "hint" }, "The port is set in docker-compose.yml (ports: \"11434:11434\"). Keep it to your own network: never forward it on your router."));
    setLog(v.pre, log.lines.join("\n") || "—");
  }

  // ---------------------------------------------------------------- Models
  const pulls = new Map();          // id → interval

  async function loadModels(quiet) {
    const box = $("#tab-models");
    if (!quiet) UI.loading(box);
    let r;
    try { r = await api("api/models"); } catch (e) { fail(box, e, () => loadModels()); return; }
    const free = r.memory.free || 0;
    const downloaded = new Set(r.downloaded.map((d) => d.name));
    const ramWarn = (need) => (need && free && need * GB > free
      ? `This model needs about ${need} GB; this machine has ${gb(free)} free — the machine may slow down or the model server may be stopped.` : null);

    // Downloads in progress: at the top of the tab, hidden while there are none.
    const active = r.pulls.filter((p) => !p.done);
    const progress = h("div", { class: "pulls" }, active.map((p) => pullRow(p)));
    const downloads = h("div", { class: "card downloads", hidden: !active.length }, h("h3", null, "Downloading"), progress);
    const rec = h("table", { class: "grid stack" },
      h("thead", null, h("tr", null, ["Model", "Kind", "Download", "Memory", "Suits", ""].map((t) => h("th", null, t)))),
      h("tbody", null, r.recommended.map((m) => {
        const warn = ramWarn(m.ram);
        return h("tr", null,
          h("td", { class: "stack-title" }, h("code", null, m.name), m.default ? h("span", { class: "badge" }, "suggested") : null),
          h("td", { "data-label": "Kind" }, kindLabel(m.kind)),
          h("td", { class: "nowrap", "data-label": "Download" }, `${m.download} GB`),
          h("td", { class: "nowrap", "data-label": "Memory" }, `~${m.ram} GB`),
          h("td", { class: "hint stack-wide" }, m.suits, warn ? h("div", { class: "warn-text" }, warn) : null),
          h("td", { class: "stack-action" }, m.downloaded ? h("span", { class: "dim" }, "Downloaded")
            : button("Download", () => startPull(m.name, progress), "btn-primary")));
      })));
    const other = h("input", { type: "text", placeholder: "e.g. gemma3:4b", maxlength: 240, "aria-label": "Other model" });
    const mine = r.downloaded.length ? h("table", { class: "grid" },
      h("thead", null, h("tr", null, ["Downloaded", "Kind", "Size", "Keep loaded", ""].map((t) => h("th", null, t)))),
      h("tbody", null, r.downloaded.map((d) => {
        const radio = h("input", { type: "radio", name: "keep", checked: d.kept, disabled: d.kind === "vision" });
        radio.addEventListener("change", () => setKeep(d.name));
        return h("tr", null, h("td", null, h("code", null, d.name)), h("td", null, kindLabel(d.kind)),
          h("td", null, gb(d.size)), h("td", null, h("label", null, radio, d.kept ? " kept in the day" : "")),
          h("td", null, button("Delete", async () => {
            if (!(await UI.confirmDialog("Delete model", `Delete ${d.name}? Apps using it get "not found" until it is downloaded again.`, { okLabel: "Delete" }))) return;
            await api(`api/downloaded/${encodeURIComponent(d.name)}`, { method: "DELETE" });
            toast(`Deleted ${d.name}.`);
            loadModels(true);
          })));
      }))) : UI.empty("No model downloaded yet", "Download one from Recommended below.");
    const none = h("input", { type: "radio", name: "keep", checked: r.keepSetting === "none" });
    none.addEventListener("change", () => setKeep("none"));

    mount(box,
      !r.serverReady ? h("div", { class: "note warn" }, "Turn the model server on (Status tab) to download or delete models.") : null,
      r.missing.length ? h("div", { class: "note warn" }, `Missing after a restore: ${r.missing.join(", ")}. `,
        r.missing.map((n) => button(`Download ${n} again`, () => startPull(n, progress)))) : null,
      downloads,
      card("Your models", mine, r.downloaded.length ? h("label", { class: "hint" }, none, " Keep no model loaded") : null,
        h("p", { class: "hint" }, `Disk: ${gb(r.disk.free)} free. Models live in /data/models (the Docker volume) and are not in this page's backups.`)),
      card("Recommended", h("p", { class: "hint" }, "One text model for everything text and one vision model is usually enough — or one Gemma 4 model (Text + vision) for both. Memory free now: " + gb(free) + "."),
        rec, h("div", { class: "row" }, other, button("Download other model…", () => startPull(other.value.trim(), progress))),
        h("p", { class: "hint" }, "Any model name from Ollama's library.")));
  }

  async function setKeep(name) {
    try { await api("api/models/keep", { method: "PUT", body: { name } }); toast(name === "none" ? "No model is kept loaded." : `${name} stays loaded in the day.`); }
    catch (e) { toast(e.message, true); }
    loadModels(true);
  }

  function pullRow(p) {
    const bar = h("progress", { max: 100, value: p.percent || 0 });
    const text = h("span", { class: "hint" }, p.status);
    const cancel = button("Cancel", () => api(`api/models/pull/${p.id}`, { method: "DELETE" }), "btn-ghost");
    const row = h("div", { class: "pull" }, h("code", null, p.name), bar, text, cancel);
    if (!pulls.has(p.id)) {
      const t = setInterval(async () => {
        let v;
        try { v = await api(`api/models/pull/${p.id}`); } catch (e) { clearInterval(t); pulls.delete(p.id); return; }
        bar.value = v.percent || 0;
        text.textContent = v.total ? `${v.status} — ${gb(v.completed)} of ${gb(v.total)}` : v.status;
        if (v.done) {
          clearInterval(t); pulls.delete(p.id);
          if (v.error) toast(`${v.name}: ${v.error}`, true);
          else if (!v.cancelled) toast(`Downloaded ${v.name}.`);
          if (current === "models") loadModels(true);
        }
      }, 1000);
      pulls.set(p.id, t);
    }
    return row;
  }

  async function startPull(name, progressBox) {
    if (!name) { toast("Type a model name.", true); return; }
    const p = await api("api/models/pull", { method: "POST", body: { name } });
    progressBox.append(pullRow(p));
    const wrap = progressBox.closest(".downloads");
    if (wrap) { wrap.hidden = false; wrap.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
  }

  // ---------------------------------------------------------------- Use it (in other apps)
  function copyable(value) {
    const code = h("code", { class: "copy" }, value);
    const btn = h("button", { type: "button", class: "btn-ghost small" }, "Copy");
    btn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(value); toast("Copied."); } catch (e) { toast("Select it and copy it by hand.", true); }
    });
    return h("span", null, code, " ", btn);
  }

  async function loadUse() {
    const box = $("#tab-use");
    UI.loading(box);
    let r;
    try { r = await api("api/callers"); } catch (e) { fail(box, e, loadUse); return; }
    const c = r.connection;
    const values = h("table", { class: "grid" }, h("tbody", null,
      h("tr", null, h("th", null, "Provider"), h("td", null, h("strong", null, "Ollama"), h("span", { class: "hint" }, " (recommended; OpenAI-compatible also works — then use the /v1 address)"))),
      h("tr", null, h("th", null, "Address"), h("td", null, copyable(c.address))),
      h("tr", null, h("th", null, "Address for OpenAI-compatible"), h("td", null, copyable(c.openaiAddress))),
      h("tr", null, h("th", null, "Access key"), h("td", null, r.requireKey ? "A key from the Access keys tab" : "Leave empty")),
      h("tr", null, h("th", null, "Model / Vision model"), h("td", null, "One of the downloaded models (Test connection lists them)"))));
    const rows = r.callers.map((x) => {
      const first = h("input", { type: "checkbox", checked: x.answerFirst });
      first.addEventListener("change", async () => {
        try { await api(`api/callers/${encodeURIComponent(x.caller)}/answer-first`, { method: "PUT", body: { on: first.checked } }); }
        catch (e) { first.checked = !first.checked; toast(e.message, true); }
      });
      return h("tr", null, h("td", null, x.name, x.advice ? h("div", { class: "hint" }, x.advice) : null),
        h("td", null, x.lastUsedAt ? new Date(x.lastUsedAt).toLocaleString() : "—"),
        h("td", null, String(x.requests)), h("td", null, String(x.tokens)),
        h("td", null, h("label", null, first, " Answer first")));
    });
    mount(box,
      card("Values for another app's AI settings",
        h("p", { class: "hint" }, "In each household app in Home Assistant: Admin → App settings → AI. The address is this machine as your browser reached this page; use its IP address if Home Assistant can't resolve the name."), values,
        c.local ? h("p", { class: "warn-text" }, "This page was opened on this machine itself, so it can't tell the address Home Assistant reaches it by: put this machine's IP address (e.g. 192.168.1.10) in the address.") : null,
        h("p", { class: "hint" }, "Without an access key, every household app shares 6 waiting places in the queue (they all come from Home Assistant's address); with one key per app, each has 2 of its own."),
        h("p", { class: "hint" }, "Every household app reaches this machine from Home Assistant's address, so without keys they are all listed as that one computer. To see each app on its own (and to tick Answer first for the Household Assistant), turn on Require an access key and give each app its own key."),
        h("p", { class: "hint" }, "An app's timeout covers its wait in the queue as well as the answer: allow more than one vision job (several minutes) for apps that share the model with Receipt Price Intelligence or Finance.")),
      card("Apps that have used it", rows.length ? h("table", { class: "grid" },
        h("thead", null, h("tr", null, ["App", "Last used", "Requests this month", "Tokens this month", ""].map((t) => h("th", null, t)))),
        h("tbody", null, rows)) : UI.empty("No app has used it yet", "Apps show up here after their first request."),
        h("p", { class: "hint" }, "Answer first: that app's waiting requests go ahead of the others (a request already running is never interrupted). Tick it for the Household Assistant's key, because a person is waiting on its page.")));
  }

  // ---------------------------------------------------------------- Access keys
  async function loadKeys(quiet) {
    const box = $("#tab-keys");
    if (!quiet) UI.loading(box);
    let r;
    try { r = await api("api/keys"); } catch (e) { fail(box, e, () => loadKeys()); return; }
    const label = h("input", { type: "text", placeholder: "e.g. Receipt Price Intelligence", maxlength: 80, "aria-label": "Label" });
    // Answer first: offered for the Household Assistant's key (a person waits on its page) — ticked when the label names it.
    const first = h("input", { type: "checkbox" });
    label.addEventListener("input", () => { first.checked = /assistant/i.test(label.value); });
    const list = r.keys.filter((k) => !k.revokedAt);
    mount(box,
      card("Access keys",
        h("p", { class: r.requireKey ? null : "hint" }, r.requireKey
          ? "Require an access key is on: every app needs one of these keys in its AI settings (Access key)."
          : "Require an access key is off (App settings → Access): any computer that can reach port 11434 may use the model, and keys aren't checked. Turning it on is recommended here — and one key per app also lets this page tell the apps apart."),
        list.length ? h("table", { class: "grid" },
          h("thead", null, h("tr", null, ["Label", "Created", "Last used", "Models", ""].map((t) => h("th", null, t)))),
          h("tbody", null, list.map((k) => h("tr", null, h("td", null, k.label),
            h("td", null, new Date(k.createdAt).toLocaleDateString()),
            h("td", null, k.lastUsedAt ? new Date(k.lastUsedAt).toLocaleString() : "—"),
            h("td", null, k.models ? k.models.join(", ") : "All"),
            h("td", null, button("Revoke", async () => {
              if (!(await UI.confirmDialog("Revoke key", `Revoke "${k.label}"? The app using it gets "access key not valid" at once.`, { okLabel: "Revoke" }))) return;
              await api(`api/keys/${encodeURIComponent(k.id)}`, { method: "DELETE" });
              loadKeys(true);
            })))))) : UI.empty("No keys", "Make one per app below."),
        h("div", { class: "row" }, label, h("label", { title: "Its requests go ahead of the other apps' (a request already running is never interrupted)" }, first, " Answer first"), button("New key", async () => {
          const r2 = await api("api/keys", { method: "POST", body: { label: label.value.trim(), answerFirst: first.checked } });
          UI.openModal("New access key", h("div", null,
            h("p", null, "Paste it into the app's AI settings now: it is shown only this once."),
            h("p", null, copyable(r2.key))));
          loadKeys(true);
        }, "btn-primary"))));
  }

  // ---------------------------------------------------------------- App settings
  async function loadSettings() {
    const box = $("#tab-settings");
    UI.loading(box);
    try {
      await SettingsPage.render(box, {
        load: () => api("api/admin/settings"),
        save: (body) => api("api/admin/settings", { method: "PUT", body }),
        classes: { card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" },
        footer: "Changes apply right away (Models loaded at once and Context length restart the model server). Who is an admin is set on the app's Configuration tab (admin_users).",
        toast: (m, err) => toast(m, err),
      });
    } catch (e) { fail(box, e, loadSettings); }
  }

  // ---------------------------------------------------------------- Usage
  const num = (n) => (Math.round(n * 10) / 10).toLocaleString();
  function dayLabel(day) {
    const d = new Date(day + "T00:00:00");
    return isNaN(d) ? day : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  }
  async function loadUsage() {
    const box = $("#tab-usage");
    UI.loading(box);
    let r;
    try { r = await api("api/usage"); } catch (e) { fail(box, e, loadUsage); return; }
    if (!r.days.length) {
      mount(box, card("Usage, last 30 days", UI.empty("Nothing yet", "Requests from the other apps show up here, per app, per model and per day.")));
      return;
    }
    // Per app first (all models, the 30 days), then the days, newest first, each with its totals.
    const perApp = new Map();
    const perDay = new Map();
    for (const d of r.days) {
      const a = perApp.get(d.name) || { name: d.name, requests: 0, inputTokens: 0, outputTokens: 0, seconds: 0, waited: 0, models: new Set() };
      a.requests += d.requests; a.inputTokens += d.inputTokens; a.outputTokens += d.outputTokens;
      a.seconds += d.seconds; a.waited += d.waited; a.models.add(d.model);
      perApp.set(d.name, a);
      if (!perDay.has(d.day)) perDay.set(d.day, []);
      perDay.get(d.day).push(d);
    }
    const apps = [...perApp.values()].sort((x, y) => y.requests - x.requests);
    const totals = h("table", { class: "grid" },
      h("thead", null, h("tr", null, ["App", "Models", "Requests", "Tokens in", "Tokens out", "Seconds", "Waited (s)"].map((t) => h("th", null, t)))),
      h("tbody", null, apps.map((a) => h("tr", null, [a.name, [...a.models].join(", "), num(a.requests), num(a.inputTokens),
        num(a.outputTokens), num(a.seconds), num(a.waited)].map((v) => h("td", null, String(v)))))));
    const days = [...perDay.entries()].map(([day, rows]) => {
      const req = rows.reduce((t, d) => t + d.requests, 0);
      const tok = rows.reduce((t, d) => t + d.inputTokens + d.outputTokens, 0);
      return [h("div", { class: "ui-group-head" }, h("span", null, dayLabel(day)),
          h("span", { class: "ui-group-total" }, `${num(req)} request${req === 1 ? "" : "s"} · ${num(tok)} tokens`)),
        h("table", { class: "grid" },
          h("thead", null, h("tr", null, ["App", "Model", "Requests", "Tokens in", "Tokens out", "Seconds", "Waited (s)"].map((t) => h("th", null, t)))),
          h("tbody", null, rows.map((d) => h("tr", null, [d.name, d.model, num(d.requests), num(d.inputTokens), num(d.outputTokens), num(d.seconds), num(d.waited)]
            .map((v) => h("td", null, String(v)))))))];
    });
    mount(box,
      card("Per app, last 30 days", h("p", { class: "hint" }, "Prompts, images and answers are never kept."), totals),
      card("By day", days));
  }

  // ---------------------------------------------------------------- Storage
  $("#importDb").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (!(await UI.confirmDialog("Restore a backup", "This replaces the App settings, the access keys and the usage with the backup's. Continue?", { okLabel: "Restore" }))) return;
    const form = new FormData();
    form.append("file", file);
    try { await api("api/admin-storage-import-db", { method: "POST", formData: form }); toast("Restored."); }
    catch (err) { toast(err.message, true); }
  });

  // ---------------------------------------------------------------- who am I, start

  BackNav.init({
    atHome: () => current === "status",
    goHome: () => BackNav.replace("#/status"),
    isTab: (hash) => tabOf(hash) === "status",           // Status starts a new trail; other tabs are remembered
    openLayers: () => $$("#modalRoot > *"),
    closeLayer: (layer) => { const x = layer.querySelector(".modal-close, [aria-label='Close']"); if (x) x.click(); else layer.remove(); },
  });

  (async () => {
    let me;
    try { me = await api("api/me"); }
    catch (e) {
      mount($("#notAdmin"), h("h3", null, "Can't reach Household AI"), h("p", null, e.message));
      $("#notAdmin").hidden = false;
      return;
    }
    $("#version").textContent = "v" + me.version;
    $("#tabs").hidden = false;
    openTab(tabOf(location.hash));
  })();
})();
