/* Household Assistant — the page (HOUSEHOLD_ASSISTANT_SPEC.md §2.1).
 *
 * One column: the conversation (each question, its answer as short Markdown — lists and bold only, built as DOM
 * nodes, never HTML — its Sources, "What was shared" and any proposed actions), suggestions, and the ask box.
 * A question is answered on the server; the page polls GET api/ask/<id> every second until it is done.
 * 🔊 reads an answer aloud with the browser's speech synthesis; a question asked with 🎤 is read aloud when done.
 * Admins also get Admin: Apps, App settings, People, Usage, Storage (and Connected apps).
 */
(function () {
  "use strict";
  const { h, $, $$, clear, mount } = UI;

  const api = UI.makeApi({
    networkError: "Couldn't reach the app. Check the connection.",
    message: (body, res) => UI.errorMessage(body && body.detail, res.status),
  });
  const toast = (msg, error) => UI.toast(msg, { error: !!error });

  const state = { me: null, polling: new Map(), oldest: null, more: false, heard: null, spoken: new Set() };

  HouseholdTheme.bindSelect($("#theme-select"));

  // ---------- Markdown: paragraphs, "-"/"*"/"1." lists and **bold**, as text nodes only ----------
  function inline(text) {
    const out = [];
    const re = /\*\*([^*]+)\*\*/g;
    let last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(text.slice(last, m.index));
      out.push(h("strong", null, m[1]));
      last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  }

  function markdown(text) {
    const box = h("div", { class: "md" });
    let list = null, para = [];
    const flush = () => {
      if (para.length) { box.append(h("p", null, inline(para.join(" ")))); para = []; }
    };
    for (const raw of String(text || "").split("\n")) {
      const line = raw.trim();
      const bullet = /^[-*•]\s+(.*)$/.exec(line);
      const num = /^\d+[.)]\s+(.*)$/.exec(line);
      if (bullet || num) {
        flush();
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== tag) { list = h(tag); box.append(list); }
        list.append(h("li", null, inline((bullet || num)[1])));
      } else if (!line) {
        flush(); list = null;
      } else {
        list = null;
        para.push(line.replace(/^#+\s*/, ""));
      }
    }
    flush();
    return box;
  }

  // ---------- reading answers aloud: the browser's own speech, nothing leaves the device ----------
  const Voice = window.speechSynthesis && window.SpeechSynthesisUtterance ? window.speechSynthesis : null;

  function plainText(text) {
    return String(text || "").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/^\s*(?:[-*•]|\d+[.)])\s+/gm, "")
      .replace(/^#+\s*/gm, "").replace(/\n{2,}/g, ".\n");
  }

  function speak(text) {
    if (!Voice) return;
    Voice.cancel();
    const u = new SpeechSynthesisUtterance(plainText(text));
    u.lang = navigator.language || "en-US";
    Voice.speak(u);
  }

  function readAloudBtn(q) {
    if (!Voice) return null;
    const b = h("button", { type: "button", class: "link-btn read-aloud", title: "Read aloud", "aria-label": "Read the answer aloud" }, "🔊");
    b.addEventListener("click", () => { if (Voice.speaking) Voice.cancel(); else speak(q.answer); });
    return b;
  }

  // ---------- links back to the apps (§6.3) ----------
  function linkTo(link, label) {
    const path = link.panel ? ConnectedApps.safePath(link.panel, link.target || null) : null;
    if (!path) return h("span", { class: "chip plain", title: `Open ${link.appName || "the app"} from the sidebar` }, label);
    return h("a", {
      class: "chip", href: path, target: "_top",
      onclick: (e) => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return; e.preventDefault(); ConnectedApps.openAppPage(path); },
    }, label);
  }

  function sourcesLine(sources) {
    if (!sources.length) return null;
    return h("div", { class: "sources" }, h("span", { class: "dim" }, "Sources: "),
      sources.map((s) => linkTo(s, s.label && s.label !== s.appName ? `${s.appName} → ${s.label.replace(` in ${s.appName}`, "")}` : s.appName)));
  }

  function argsText(args) {
    const parts = Object.entries(args || {}).map(([k, v]) => `${k}: ${v}`);
    return parts.length ? ` (${parts.join(", ")})` : "";
  }

  function sharedBlock(q) {
    if (!q.shared.length) return null;
    const body = q.shared.map((c) => h("div", { class: "shared-call" },
      h("div", { class: "shared-head" }, h("strong", null, c.appName), ` · ${c.tool}${argsText(c.args)}`),
      c.problem ? h("div", { class: "dim" }, c.problem)
        : c.state === "sent" ? h("div", { class: "dim" }, "Waiting for the answer…")
          : h("pre", { class: "shared-text" }, [c.text || "", c.items.length ? "\n" + JSON.stringify(c.items, null, 1) : ""].join(""))));
    return h("details", { class: "shared", open: state.me && state.me.sharedOpen },
      h("summary", null, "What was shared"), body);
  }

  function actionBlock(q, a) {
    if (a.state === "proposed") {
      const btn = h("button", { type: "button", class: "btn-primary" }, a.say);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const r = await api(`api/ask/${q.id}/act/${a.id}`, { method: "POST" });
          const box = btn.closest(".action");
          box.replaceWith(actionBlock(q, r.result));
        } catch (e) { btn.disabled = false; toast(e.message, true); }
      });
      return h("div", { class: "action" }, btn, h("span", { class: "dim" }, ` in ${a.appName} — nothing changes until you tap.`));
    }
    if (a.state === "sent") return h("div", { class: "action dim" }, `${a.say}: sending…`);
    if (a.state === "ok") {
      return h("div", { class: "action done" }, "✓ ", a.text || a.say, " ",
        (a.links || []).slice(0, 1).map((l) => linkTo({ ...l, appName: a.appName }, "Open")));
    }
    return h("div", { class: "action failed" }, `✗ ${a.say}: ${a.problem || "it didn't work."}`);
  }

  function questionNode(q) {
    const node = h("article", { class: "turn", dataset: { id: q.id } });
    fillQuestion(node, q);
    return node;
  }

  function fillQuestion(node, q) {
    const running = ["planning", "calling", "answering"].includes(q.state);
    const parts = [h("div", { class: "question" }, q.text)];
    if (running) {
      const stop = h("button", { type: "button", class: "link-btn" }, "Stop");
      stop.addEventListener("click", () => api(`api/ask/${q.id}/stop`, { method: "POST" }).then(() => poll(q.id)).catch((e) => toast(e.message, true)));
      parts.push(h("div", { class: "progress" }, h("span", { class: "spinner" }), " ", q.progress || "Working…", " ", stop));
    } else if (q.state === "done") {
      parts.push(h("div", { class: "answer" }, readAloudBtn(q), markdown(q.answer)));
    } else if (q.state === "stopped") {
      parts.push(h("div", { class: "answer dim" }, "Stopped."));
    } else {
      parts.push(h("div", { class: "answer error" }, q.error || "Something went wrong."));
    }
    if (!running) {
      parts.push((q.actions || []).map((a) => actionBlock(q, a)));
      parts.push(sourcesLine(q.sources || []));
      parts.push(sharedBlock(q));
    }
    mount(clear(node), parts);
  }

  // ---------- asking ----------
  async function poll(id) {
    if (state.polling.has(id)) return;
    state.polling.set(id, true);
    try {
      for (;;) {
        const q = await api(`api/ask/${id}`);
        const node = $(`.turn[data-id="${id}"]`);
        if (node) fillQuestion(node, q);
        if (!["planning", "calling", "answering"].includes(q.state)) {
          if (state.spoken.delete(id) && q.state === "done") speak(q.answer);   // asked by voice: answered by voice
          break;
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    } catch (e) { toast(e.message, true); }
    finally { state.polling.delete(id); scrollDown(); }
  }

  function scrollDown() { window.scrollTo(0, document.body.scrollHeight); }

  async function ask(text) {
    text = (text || "").trim();
    if (!text) return;
    $("#askBtn").disabled = true;
    try {
      const { id } = await api("api/ask", { method: "POST", body: { text } });
      if (state.heard && state.heard === text) state.spoken.add(id);
      state.heard = null;
      $("#askInput").value = "";
      autosize();
      $("#suggestions").hidden = true;
      const q = await api(`api/ask/${id}`);
      $("#conversation").append(questionNode(q));
      scrollDown();
      poll(id);
    } catch (e) { toast(e.message, true); }
    finally { $("#askBtn").disabled = false; }
  }

  function autosize() {
    const t = $("#askInput");
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 160) + "px";
  }

  $("#askForm").addEventListener("submit", (e) => { e.preventDefault(); ask($("#askInput").value); });
  $("#askInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask($("#askInput").value); }
  });
  $("#askInput").addEventListener("input", autosize);

  // The browser's speech recognition, where it exists (nothing is recorded by the app).
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (Speech) {
    $("#micBtn").hidden = false;
    $("#micBtn").addEventListener("click", () => {
      const rec = new Speech();
      rec.lang = navigator.language || "en-US";
      rec.onresult = (e) => { state.heard = e.results[0][0].transcript.trim(); $("#askInput").value = state.heard; autosize(); };
      rec.onerror = () => toast("Couldn't hear that.", true);
      rec.start();
    });
  }

  async function loadHistory(more) {
    const qs = more && state.oldest ? `?before=${encodeURIComponent(state.oldest)}` : "";
    const r = await api(`api/history${qs}`);
    const box = $("#conversation");
    const nodes = r.questions.slice().reverse().map(questionNode);
    if (more) {
      const btn = $("#olderBtn");
      if (btn) btn.remove();
      box.prepend(...nodes);
    } else {
      mount(clear(box), nodes);
    }
    if (r.questions.length) state.oldest = r.questions[r.questions.length - 1].askedAt;
    if (r.more) {
      const older = h("button", { type: "button", class: "link-btn", id: "olderBtn" }, "Earlier questions");
      older.addEventListener("click", () => loadHistory(true));
      box.prepend(older);
    }
    r.questions.filter((q) => ["planning", "calling", "answering"].includes(q.state)).forEach((q) => poll(q.id));
    $("#suggestions").hidden = r.questions.length > 0;
  }

  function showSuggestions(list) {
    mount(clear($("#suggestions")), list.map((s) => {
      const b = h("button", { type: "button", class: "chip" }, s);
      b.addEventListener("click", () => { $("#askInput").value = s; ask(s); });
      return b;
    }));
  }

  $("#clearBtn").addEventListener("click", async () => {
    if (!(await UI.confirmDialog("Clear my questions", "Delete all your questions and answers now?", { okLabel: "Clear" }))) return;
    try { await api("api/history", { method: "DELETE" }); await loadHistory(false); $("#suggestions").hidden = false; }
    catch (e) { toast(e.message, true); }
  });

  $("#whatBtn").addEventListener("click", async () => {
    let r;
    try { r = await api("api/tools"); } catch (e) { toast(e.message, true); return; }
    const byApp = new Map();
    r.tools.forEach((t) => { if (!byApp.has(t.appName)) byApp.set(t.appName, []); byApp.get(t.appName).push(t); });
    const body = byApp.size ? [...byApp].map(([app, list]) => h("div", { class: "what-app" }, h("h4", null, app),
      h("ul", null, list.map((t) => h("li", null, t.what, t.acts ? h("span", { class: "dim" }, " (asks you first)") : null,
        t.examples.length ? h("div", { class: "dim" }, "e.g. “" + t.examples[0] + "”") : null)))))
      : h("p", null, "No household apps answer the assistant yet.");
    UI.openModal("What can I ask?", body);
  });

  // ---------- who am I ----------
  async function openWhoami() {
    const body = h("div", null, "Loading…");
    UI.openModal("How the app sees you", body);
    try {
      const w = await api("api/whoami");
      mount(clear(body), HouseholdWhoami.panel(w, { appName: "Household Assistant", classes: { copy: "btn-ghost" } }));
    } catch (e) { mount(clear(body), h("p", { class: "error" }, e.message)); }
  }
  $("#userBtn").addEventListener("click", openWhoami);

  // ---------- admin ----------
  function showView(name) {
    $("#askView").hidden = name !== "ask";
    $("#adminView").hidden = name !== "admin";
    if (name === "admin") openTab(currentTab);
  }
  let currentTab = "apps";
  $("#homeBtn").addEventListener("click", () => showView("ask"));
  $("#adminBtn").addEventListener("click", () => showView("admin"));
  $$("#adminTabs .tab").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

  function openTab(tab) {
    currentTab = tab;
    $$("#adminTabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $$(".tab-body").forEach((s) => { s.hidden = s.id !== `tab-${tab}`; });
    ({ apps: loadApps, settings: loadSettings, people: loadPeople, usage: loadUsage, storage: () => {} })[tab]();
  }

  async function loadApps() {
    const box = $("#tab-apps");
    let r, ca;
    try { [r, ca] = await Promise.all([api("api/admin/tools"), api("api/admin/connected-apps").catch(() => null)]); }
    catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); return; }
    const refresh = h("button", { type: "button", class: "btn-secondary" }, "Refresh");
    refresh.addEventListener("click", async () => {
      try { const a = await api("api/admin/tools/refresh", { method: "POST" }); toast(a.asked.length ? `Asked ${a.asked.length} app(s) for their tools.` : "No connected app answers the assistant yet."); setTimeout(loadApps, 2000); }
      catch (e) { toast(e.message, true); }
    });
    const cards = r.apps.map((a) => {
      const sw = h("input", { type: "checkbox", checked: a.enabled });
      sw.addEventListener("change", async () => {
        try { await api(`api/admin/tools/${encodeURIComponent(a.app)}`, { method: "PUT", body: { enabled: sw.checked } }); }
        catch (e) { sw.checked = !sw.checked; toast(e.message, true); }
      });
      const notes = [];
      if (!a.appOn) notes.push(`Its own Answer the Household Assistant is off (in ${a.name}'s App settings).`);
      if (!a.active) notes.push("Not heard from in the last day.");
      if (a.error) notes.push(a.error);
      return h("div", { class: "card app-card" + (a.active ? "" : " greyed") },
        h("label", { class: "app-head" }, sw, " ", h("strong", null, a.name), a.version ? h("span", { class: "dim" }, ` ${a.version}`) : null),
        notes.map((n) => h("p", { class: "hint warn-text" }, n)),
        h("ul", { class: "tool-list" }, a.tools.map((t) => h("li", null, t.what, t.acts ? h("span", { class: "dim" }, " — changes data, only after a tap") : null))));
    });
    mount(clear(box),
      h("p", { class: "hint" }, "The apps that answer the assistant, and what each offers. Both switches must be on: this one, and the app's own Answer the Household Assistant (its App settings)."),
      h("p", null, refresh),
      cards.length ? cards : h("p", { class: "hint" }, "No app has offered its tools yet. Apps that answer say hello on Home Assistant's event bus; then Refresh."),
      ca ? ConnectedApps.card(ca, { h }) : null);
  }

  function aiTestBlock(page) {
    const out = h("div", { class: "hint", id: "aiTestStatus" });
    const btn = h("button", { type: "button", class: "btn-secondary" }, "Test connection");
    btn.addEventListener("click", async () => {
      btn.disabled = true; out.textContent = "Asking…";
      try {
        const body = {};
        for (const k of ["ai_provider", "ai_url", "ai_model", "ai_api_key"]) body[k] = page.value(k);
        const r = await api("api/admin/settings/test-ai", { method: "POST", body });
        out.textContent = (r.ok ? "✓ " : "✗ ") + r.message;
      } catch (e) { out.textContent = "✗ " + e.message; }
      btn.disabled = false;
    });
    return h("div", { class: "ai-test" }, btn, out);
  }

  async function loadSettings() {
    const box = $("#tab-settings");
    try {
      await SettingsPage.render(box, {
        load: () => api("api/admin/settings"),
        save: (body) => api("api/admin/settings", { method: "PUT", body }),
        classes: { card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" },
        groups: { ai: { bottom: aiTestBlock } },
        footer: "Changes apply right away. Who is an admin is set on the app's Configuration tab (admin_users).",
        toast: (m, err) => toast(m, err),
        afterSave: () => loadMe(),
      });
    } catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); }
  }

  async function loadPeople() {
    const box = $("#tab-people");
    let r;
    try { r = await api("api/admin/people"); } catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); return; }
    const row = (p) => {
      const on = h("input", { type: "checkbox", checked: p.enabled });
      const child = h("input", { type: "checkbox", checked: p.isChild });
      const send = async (body, el) => {
        try { await api(`api/admin/people/${encodeURIComponent(p.id)}`, { method: "PUT", body }); }
        catch (e) { el.checked = !el.checked; toast(e.message, true); }
      };
      on.addEventListener("change", () => send({ enabled: on.checked }, on));
      child.addEventListener("change", () => send({ isChild: child.checked }, child));
      return h("tr", null, h("td", null, p.name), h("td", null, h("label", null, on, " May ask")),
        h("td", null, h("label", null, child, " Child")));
    };
    mount(clear(box), h("div", { class: "card" },
      h("p", { class: "hint" }, "Everyone who has opened the assistant. A child asks only when App settings → Children may ask is on, and then only about their own things. Nobody, admins included, can see anyone else's questions."),
      h("table", { class: "people" }, h("tbody", null, r.people.map(row)))));
  }

  async function loadUsage() {
    const box = $("#tab-usage");
    let r;
    try { r = await api("api/admin/usage"); } catch (e) { mount(clear(box), h("p", { class: "error" }, e.message)); return; }
    const total = r.days.reduce((t, d) => ({ q: t.q + d.questions, c: t.c + d.calls, i: t.i + d.input_tokens, o: t.o + d.output_tokens }), { q: 0, c: 0, i: 0, o: 0 });
    mount(clear(box), h("div", { class: "card" },
      h("p", null, `Last 30 days: ${total.q} questions, ${total.c} tool calls, ${total.i} tokens in, ${total.o} out.`),
      r.ai && r.ai.model ? h("p", { class: "hint" }, `Model: ${r.ai.providerLabel || r.ai.provider} ${r.ai.model}.`) : null,
      h("table", { class: "usage" }, h("thead", null, h("tr", null, ["Day (UTC)", "Questions", "Tool calls", "Tokens in", "Tokens out"].map((t) => h("th", null, t)))),
        h("tbody", null, r.days.map((d) => h("tr", null, [d.day, d.questions, d.calls, d.input_tokens, d.output_tokens].map((v) => h("td", null, String(v)))))))));
  }

  $("#importDb").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (!(await UI.confirmDialog("Restore a backup", "This replaces everyone's questions, the tool lists and the App settings with the backup's. Continue?", { okLabel: "Restore" }))) return;
    const form = new FormData();
    form.append("file", file);
    try { await api("api/admin-storage-import-db", { method: "POST", formData: form }); toast("Restored."); loadMe(); }
    catch (err) { toast(err.message, true); }
  });

  // ---------- start ----------
  async function loadMe() {
    const me = await api("api/me");
    state.me = me;
    $("#userBtn").textContent = me.user.name;
    $("#adminBtn").hidden = !me.user.isAdmin;
    HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), me.noAdmin, me.user.name, { onOpen: openWhoami });
    const rec = $("#recorderBanner");
    rec.hidden = !me.recorderWarning;
    if (me.recorderWarning) {
      mount(rec, h("span", null, "The apps' answers travel over Home Assistant's event bus. Exclude ", h("code", null, "household_apps"),
        " from Home Assistant's recorder (see the documentation), then tick it in Admin → App settings → Privacy."));
    }
    const priv = $("#privacyNote");
    priv.hidden = !me.privacy;
    if (me.privacy) priv.textContent = `Questions and what the apps answer are sent to ${me.privacy.label} (${me.privacy.host}), outside your home network.`;
    const cant = $("#cantAsk");
    cant.hidden = me.canAsk;
    cant.textContent = me.why || "";
    $("#askInput").disabled = !me.canAsk;
    $("#askBtn").disabled = !me.canAsk;
    showSuggestions(me.suggestions);
    return me;
  }

  // ?q=… (Chat's "Ask the assistant"): the question typed in, from this page's address or the sidebar page's.
  function typedQuestion() {
    const read = (search) => { try { return new URLSearchParams(search).get("q"); } catch (e) { return null; } };
    let q = read(location.search);
    if (!q) { try { q = read(window.parent.location.search); } catch (e) { q = null; } }
    return q ? q.slice(0, 1000) : null;
  }

  BackNav.init({
    atHome: () => !$("#askView").hidden,
    goHome: () => showView("ask"),
    openLayers: () => $$("#modalRoot > *"),
    closeLayer: (layer) => { const x = layer.querySelector(".modal-close, [aria-label='Close']"); if (x) x.click(); else layer.remove(); },
  });

  (async () => {
    try {
      await loadMe();
      await loadHistory(false);
      const q = typedQuestion();
      if (q && state.me.canAsk) { $("#askInput").value = q; autosize(); $("#askInput").focus(); }
      scrollDown();
    } catch (e) {
      mount(clear($("#conversation")), h("div", { class: "note warn" }, h("h3", null, "Can't identify a Home Assistant user"),
        h("p", null, e.message), h("p", null, "Open the assistant from its page in the Home Assistant sidebar.")));
    }
  })();
})();
