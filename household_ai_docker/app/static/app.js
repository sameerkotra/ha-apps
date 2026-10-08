/* Household AI — the admin page (SPEC.md §2).
 *
 * Tabs: Status (the model server, the machine, what is loaded, the queue, On my network, the log), Models
 * (recommended list, downloads with progress, delete, Keep loaded), Use it in other apps (the values to paste,
 * the callers seen, Answer first), Access keys, App settings (common/settings.js), Usage, Storage.
 * Everything is built as DOM nodes (UI.h), never HTML strings. Status refreshes every 5 s while open.
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
  const fail = (box, e) => mount(clear(box), h("p", { class: "error" }, e.message));

  HouseholdTheme.bindSelect($("#theme-select"));

  let current = "status";
  let timer = null;

  function openTab(tab) {
    current = tab;
    $$("#tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".tab-body").forEach((s) => { s.hidden = s.id !== `tab-${tab}`; });
    clearInterval(timer);
    timer = null;
    ({ status: loadStatus, models: loadModels, use: loadUse, keys: loadKeys, settings: loadSettings,
       usage: loadUsage, storage: () => {} })[tab]();
    if (tab === "status") timer = setInterval(() => { if (!document.hidden) loadStatus(true); }, 5000);
  }
  $$("#tabs .tab").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

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

  async function loadStatus(quiet) {
    const box = $("#tab-status");
    let s, log;
    try { [s, log] = await Promise.all([api("api/status"), api("api/log")]); }
    catch (e) { if (!quiet) fail(box, e); return; }
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

    mount(clear(box),
      card("Model server",
        h("label", { class: "switch-row" }, onSwitch, " ", h("strong", null, "On")),
        kv("State", stateText),
        sv.problem ? h("p", { class: "error" }, sv.problem) : null,
        kv("Ollama", sv.version || "—"),
        sv.state === "ready" ? h("p", null, button("Unload now", async () => {
          const r = await api("api/unload", { method: "POST" });
          toast(r.unloaded.length ? `Unloaded ${r.unloaded.join(", ")} until the next request.` : "Nothing was loaded.");
          loadStatus(true);
        })) : null,
        !sv.on ? h("p", { class: "hint" }, "Off: the model server isn't running and the other apps get \"unreachable\". Nothing is downloaded until you choose a model on the Models tab.") : null),
      card("Loaded now", loaded,
        kv("Keep loaded", keepPick), kv("Now", sched.day ? "Daytime" : "Night"),
        sched.manualUnload ? h("p", { class: "hint" }, "Unloaded by hand: nothing loads again until the next request or the start of the day.") : null,
        kv("Queue", queueText),
        s.switchesToday >= 20 ? h("p", { class: "warn-text" }, `Models were switched ${s.switchesToday} times today — consider using one model in more apps, or 2 models loaded at once.`) : null),
      card("This machine",
        kv("Memory", `${gb(m.memory.free)} free of ${gb(m.memory.total)}`),
        kv("CPU cores", String(m.cores)),
        kv("Disk (/data)", `${gb(m.disk.free)} free of ${gb(m.disk.total)}`, m.sdCard ? "On an SD card: loading a model takes up to a minute. An SSD is much faster." : null),
        m.temperature !== null ? kv("CPU temperature", `${m.temperature} °C`, m.hot ? "Hot: the CPU may slow down while a model works. A fan or a case with cooling helps." : null) : null,
        h("p", { class: "hint" }, "Without a graphics card a model is slow: seconds for a short text answer, from about 30 seconds to several minutes for a receipt photo or a statement page. With an NVIDIA card given to the container (docker-compose.yml), Ollama uses it on its own and answers are many times faster.")),
      card("On my network", netBody,
        h("p", { class: "hint" }, "The port is set in docker-compose.yml (ports: \"11434:11434\"). Keep it to your own network: never forward it on your router.")),
      h("details", { class: "card log" }, h("summary", null, "Model server log (last 50 lines)"),
        h("pre", { class: "log-lines" }, log.lines.join("\n") || "—")));
  }

  // ---------------------------------------------------------------- Models
  const pulls = new Map();          // id → interval

  async function loadModels() {
    const box = $("#tab-models");
    let r;
    try { r = await api("api/models"); } catch (e) { fail(box, e); return; }
    const free = r.memory.free || 0;
    const downloaded = new Set(r.downloaded.map((d) => d.name));
    const ramWarn = (need) => (need && free && need * GB > free
      ? `This model needs about ${need} GB; this machine has ${gb(free)} free — the machine may slow down or the model server may be stopped.` : null);

    const progress = h("div", null, r.pulls.filter((p) => !p.done).map((p) => pullRow(p)));
    const rec = h("table", { class: "grid" },
      h("thead", null, h("tr", null, ["Model", "Kind", "Download", "Memory", "Suits", ""].map((t) => h("th", null, t)))),
      h("tbody", null, r.recommended.map((m) => {
        const warn = ramWarn(m.ram);
        return h("tr", null,
          h("td", null, h("code", null, m.name), m.default ? h("span", { class: "badge" }, "suggested") : null),
          h("td", null, m.kind === "vision" ? "Vision" : "Text"),
          h("td", { class: "nowrap" }, `${m.download} GB`), h("td", { class: "nowrap" }, `~${m.ram} GB`),
          h("td", { class: "hint" }, m.suits, warn ? h("div", { class: "warn-text" }, warn) : null),
          h("td", null, m.downloaded ? h("span", { class: "dim" }, "Downloaded")
            : button("Download", () => startPull(m.name, progress), "btn-primary")));
      })));
    const other = h("input", { type: "text", placeholder: "e.g. gemma3:4b", maxlength: 240, "aria-label": "Other model" });
    const mine = r.downloaded.length ? h("table", { class: "grid" },
      h("thead", null, h("tr", null, ["Downloaded", "Kind", "Size", "Keep loaded", ""].map((t) => h("th", null, t)))),
      h("tbody", null, r.downloaded.map((d) => {
        const radio = h("input", { type: "radio", name: "keep", checked: d.kept, disabled: d.kind !== "text" });
        radio.addEventListener("change", () => setKeep(d.name));
        return h("tr", null, h("td", null, h("code", null, d.name)), h("td", null, d.kind === "vision" ? "Vision" : "Text"),
          h("td", null, gb(d.size)), h("td", null, h("label", null, radio, d.kept ? " kept in the day" : "")),
          h("td", null, button("Delete", async () => {
            if (!(await UI.confirmDialog("Delete model", `Delete ${d.name}? Apps using it get "not found" until it is downloaded again.`, { okLabel: "Delete" }))) return;
            await api(`api/models/${encodeURIComponent(d.name)}`, { method: "DELETE" });
            toast(`Deleted ${d.name}.`);
            loadModels();
          })));
      }))) : h("p", { class: "hint" }, "No model downloaded yet.");
    const none = h("input", { type: "radio", name: "keep", checked: r.keepSetting === "none" });
    none.addEventListener("change", () => setKeep("none"));

    mount(clear(box),
      !r.serverReady ? h("div", { class: "note warn" }, "Turn the model server on (Status tab) to download or delete models.") : null,
      r.missing.length ? h("div", { class: "note warn" }, `Missing after a restore: ${r.missing.join(", ")}. `,
        r.missing.map((n) => button(`Download ${n} again`, () => startPull(n, progress)))) : null,
      card("Your models", mine, r.downloaded.length ? h("label", { class: "hint" }, none, " Keep no model loaded") : null,
        h("p", { class: "hint" }, `Disk: ${gb(r.disk.free)} free. Models live in /data/models (the Docker volume) and are not in this page's backups.`)),
      card("Recommended", h("p", { class: "hint" }, "One text model for everything text and one vision model is usually enough. Memory free now: " + gb(free) + "."),
        rec, h("div", { class: "row" }, other, button("Download other model…", () => startPull(other.value.trim(), progress))),
        h("p", { class: "hint" }, "Any model name from Ollama's library."), progress));
  }

  async function setKeep(name) {
    try { await api("api/models/keep", { method: "PUT", body: { name } }); toast(name === "none" ? "No model is kept loaded." : `${name} stays loaded in the day.`); }
    catch (e) { toast(e.message, true); }
    loadModels();
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
          if (current === "models") loadModels();
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
  }

  // ---------------------------------------------------------------- Use it in other apps
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
    let r;
    try { r = await api("api/callers"); } catch (e) { fail(box, e); return; }
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
    mount(clear(box),
      card("Values for another app's AI settings",
        h("p", { class: "hint" }, "In each household app in Home Assistant: Admin → App settings → AI. The address is this machine as your browser reached this page; use its IP address if Home Assistant can't resolve the name."), values,
        h("p", { class: "hint" }, "Every household app reaches this machine from Home Assistant's address, so without keys they are all listed as that one computer. To see each app on its own (and to tick Answer first for the Household Assistant), turn on Require an access key and give each app its own key."),
        h("p", { class: "hint" }, "An app's timeout covers its wait in the queue as well as the answer: allow more than one vision job (several minutes) for apps that share the model with Receipt Price Intelligence or Finance.")),
      card("Apps that have used it", rows.length ? h("table", { class: "grid" },
        h("thead", null, h("tr", null, ["App", "Last used", "Requests this month", "Tokens this month", ""].map((t) => h("th", null, t)))),
        h("tbody", null, rows)) : h("p", { class: "hint" }, "None yet."),
        h("p", { class: "hint" }, "Answer first: that app's waiting requests go ahead of the others (a request already running is never interrupted). Tick it for the Household Assistant's key, because a person is waiting on its page.")));
  }

  // ---------------------------------------------------------------- Access keys
  async function loadKeys() {
    const box = $("#tab-keys");
    let r;
    try { r = await api("api/keys"); } catch (e) { fail(box, e); return; }
    const label = h("input", { type: "text", placeholder: "e.g. Receipt Price Intelligence", maxlength: 80, "aria-label": "Label" });
    const list = r.keys.filter((k) => !k.revokedAt);
    mount(clear(box),
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
              loadKeys();
            })))))) : h("p", { class: "hint" }, "No keys."),
        h("div", { class: "row" }, label, button("New key", async () => {
          const r2 = await api("api/keys", { method: "POST", body: { label: label.value.trim() } });
          UI.openModal("New access key", h("div", null,
            h("p", null, "Paste it into the app's AI settings now: it is shown only this once."),
            h("p", null, copyable(r2.key))));
          loadKeys();
        }, "btn-primary"))));
  }

  // ---------------------------------------------------------------- App settings
  async function loadSettings() {
    const box = $("#tab-settings");
    try {
      await SettingsPage.render(box, {
        load: () => api("api/admin/settings"),
        save: (body) => api("api/admin/settings", { method: "PUT", body }),
        classes: { card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" },
        footer: "Changes apply right away (Models loaded at once and Context length restart the model server). Who is an admin is set on the app's Configuration tab (admin_users).",
        toast: (m, err) => toast(m, err),
      });
    } catch (e) { fail(box, e); }
  }

  // ---------------------------------------------------------------- Usage
  async function loadUsage() {
    const box = $("#tab-usage");
    let r;
    try { r = await api("api/usage"); } catch (e) { fail(box, e); return; }
    mount(clear(box), card("Usage, last 30 days",
      h("p", { class: "hint" }, "Per app, per model, per day. Prompts, images and answers are never kept."),
      r.days.length ? h("table", { class: "grid" },
        h("thead", null, h("tr", null, ["Day", "App", "Model", "Requests", "Tokens in", "Tokens out", "Seconds", "Waited (s)"].map((t) => h("th", null, t)))),
        h("tbody", null, r.days.map((d) => h("tr", null, [d.day, d.name, d.model, d.requests, d.inputTokens, d.outputTokens, d.seconds, d.waited]
          .map((v) => h("td", null, String(v)))))))
        : h("p", { class: "hint" }, "Nothing yet.")));
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
    goHome: () => openTab("status"),
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
    openTab("status");
  })();
})();
