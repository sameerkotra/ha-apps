/* Household Assistant — the page (HOUSEHOLD_ASSISTANT_SPEC.md §2.1).
 *
 * One column: the ask box at the top, then the suggestions and the conversation, newest first (each question, its
 * answer as short Markdown — lists and bold only, built as DOM nodes, never HTML — its Sources, "What was shared"
 * and any proposed actions).
 * A question is answered on the server; the page follows it live (GET api/ask/<id>/events, Server-Sent Events)
 * until it is done, or polls GET api/ask/<id> every second where the stream can't be opened.
 * 🔊 reads an answer aloud with the browser's speech synthesis; a question asked with 🎤 is read aloud when done.
 * Admins also get Admin: Apps, App settings, Quick answers, People, Usage, Storage (and Connected apps).
 * Addresses: #/ the ask page, #/admin/<tab> an Admin tab (common/backnav.js keeps Back inside the app).
 */
(function () {
  "use strict";
  const { h, $, $$, clear, mount } = UI;

  const api = UI.makeApi({
    networkError: "Couldn't reach the app. Check the connection.",
    message: (body, res) => UI.errorMessage(body && body.detail, res.status),
  });
  const toast = (msg, error) => UI.toast(msg, { error: !!error });

  const RUNNING = ["planning", "calling", "answering"];
  const state = { me: null, polling: new Map(), oldest: null, more: false, heard: null, spoken: new Set() };

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
    return h("span", { class: "sources" }, h("span", { class: "dim" }, "Sources: "),
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
    // The button says the exact change ("<app> · <tool> (<args>)", made by the app, not the model); the model's own
    // words are only a note under it.
    const label = a.label || a.say;
    const note = a.say ? h("div", { class: "dim action-say" }, a.say) : null;
    if (a.state === "proposed") {
      const btn = h("button", { type: "button", class: "btn-primary" }, label);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const r = await api(`api/ask/${q.id}/act/${a.id}`, { method: "POST" });
          const box = btn.closest(".action");
          box.replaceWith(actionBlock(q, r.result));
        } catch (e) { btn.disabled = false; toast(e.message, true); }
      });
      return h("div", { class: "action" }, btn, h("span", { class: "dim" }, " — nothing changes until you tap."), note);
    }
    if (a.state === "sent") return h("div", { class: "action dim" }, `${label}: sending…`);
    if (a.state === "ok") {
      return h("div", { class: "action done" }, "✓ ", a.text || label, " ",
        (a.links || []).slice(0, 1).map((l) => linkTo({ ...l, appName: a.appName }, "Open")));
    }
    return h("div", { class: "action failed" }, `✗ ${label}: ${a.problem || "it didn't work."}`);
  }

  // "Took 42 s" / "Took 1 min 12 s" under an answer (the question to the end of the answer).
  function took(seconds) {
    if (seconds === null || seconds === undefined) return null;
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return seconds < 10 ? `${(Math.round(seconds * 10) / 10).toString()} s` : `${s} s`;
    const m = Math.floor(s / 60);
    return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  }

  // One line under an answer: "⚡ what was understood · ⏱ 2 s · Sources: … · What was shared ▸".
  function footLine(q) {
    const bits = [];
    if (q.state === "done" && q.understood) bits.push(understoodLine(q));
    const t = took(q.seconds);
    if (t) bits.push(h("span", { class: "took", title: "From asking to the answer" }, `⏱ ${t}`));
    bits.push(sourcesLine(q.sources || []), sharedBlock(q));
    const out = [];
    bits.filter(Boolean).forEach((b) => { if (out.length) out.push(h("span", { class: "sep", "aria-hidden": "true" }, "·")); out.push(b); });
    return out.length ? h("div", { class: "answer-foot dim" }, out) : null;
  }

  // ---------- the conversation's day dividers: Today, Yesterday, then the date ----------
  function dayLabel(iso) {
    const d = iso ? new Date(iso) : null;
    if (!d || isNaN(d.getTime())) return "";
    const now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(now) - day(d)) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short",
      year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
  }

  // Each turn that starts a new day carries its label (drawn above it by the stylesheet, so .turn stays first).
  function markDays() {
    let last = null;
    $$("#conversation > .turn").forEach((t) => {
      const label = dayLabel(t.dataset.at);
      if (label && label !== last) t.dataset.day = label; else delete t.dataset.day;
      if (label) last = label;
    });
  }

  // ---------- quick answers (§17): what was understood, Not what I asked, Did you mean ----------
  function understoodLine(q) {
    const line = h("span", { class: "understood" }, "⚡ ", q.understood);
    if (q.notThis) {
      const b = h("button", { type: "button", class: "link-btn" }, "Not what I asked");
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const { id } = await api(`api/ask/${q.id}/not-this`, { method: "POST" });
          const nq = await api(`api/ask/${id}`);
          $("#conversation").prepend(questionNode(nq));
          markDays();
          poll(id);
        } catch (e) { b.disabled = false; toast(e.message, true); }
      });
      line.append(" · ", b);
    }
    return line;
  }

  function choicesBlock(q) {
    const choices = q.choices || [], suggest = q.suggest || [];
    if (!choices.length && !suggest.length) return null;
    const box = h("div", { class: "choices" });
    choices.forEach((c) => {
      const b = h("button", { type: "button", class: "chip" }, c.label);
      b.addEventListener("click", async () => {
        $$("button", box).forEach((x) => { x.disabled = true; });
        try {
          const nq = await api(`api/ask/${q.id}/choose`, { method: "POST", body: { choice: c.index } });
          update(q.id, nq);
          poll(q.id);
        } catch (e) { $$("button", box).forEach((x) => { x.disabled = false; }); toast(e.message, true); }
      });
      box.append(b);
    });
    if (!choices.length) {
      suggest.forEach((text) => {
        const b = h("button", { type: "button", class: "chip plain-chip" }, text);
        b.addEventListener("click", () => ask(text));
        box.append(b);
      });
    }
    return box;
  }

  function questionNode(q) {
    const node = h("article", { class: "turn", dataset: { id: q.id, at: q.askedAt || "" } });
    fillQuestion(node, q);
    return node;
  }

  function fillQuestion(node, q) {
    const running = RUNNING.includes(q.state);
    const parts = [h("div", { class: "question" }, q.text)];
    if (running) {
      const stop = h("button", { type: "button", class: "link-btn" }, "Stop");
      stop.addEventListener("click", () => api(`api/ask/${q.id}/stop`, { method: "POST" }).then(() => poll(q.id)).catch((e) => toast(e.message, true)));
      parts.push(h("div", { class: "progress" }, h("span", { class: "spinner" }), " ", q.progress || "Working…", " ", stop));
    } else if (q.state === "done") {
      parts.push(h("div", { class: "answer" }, readAloudBtn(q), markdown(q.answer)));
      parts.push(choicesBlock(q));
    } else if (q.state === "stopped") {
      parts.push(h("div", { class: "answer dim" }, "Stopped."));
    } else {
      parts.push(h("div", { class: "answer error" }, q.error || "Something went wrong."));
    }
    if (!running) {
      parts.push((q.actions || []).map((a) => actionBlock(q, a)));    // the buttons right after the answer
      parts.push(footLine(q));
    }
    mount(clear(node), parts);
  }

  // ---------- asking ----------
  // Show the question as it is now; true once it has ended (answered, failed or stopped).
  function update(id, q) {
    const node = $(`.turn[data-id="${id}"]`);
    if (node) fillQuestion(node, q);
    if (RUNNING.includes(q.state)) return false;
    if (state.spoken.delete(id) && q.state === "done") speak(q.answer);   // asked by voice: answered by voice
    return true;
  }

  // The server sends the question each time it changes; resolves true when it ended, false to poll instead.
  function follow(id) {
    return new Promise((resolve) => {
      const es = new EventSource(`api/ask/${id}/events`);
      let ended = false;
      es.addEventListener("question", (e) => {
        if (update(id, JSON.parse(e.data))) { ended = true; es.close(); resolve(true); }
      });
      es.onerror = () => { if (!ended) { es.close(); resolve(false); } };
    });
  }

  async function poll(id) {
    if (state.polling.has(id)) return;
    state.polling.set(id, true);
    try {
      if (window.EventSource && await follow(id)) return;
      for (;;) {                                   // no stream (an old browser, a proxy that buffers): every second
        if (update(id, await api(`api/ask/${id}`))) break;
        await new Promise((r) => setTimeout(r, 1000));
      }
    } catch (e) { toast(e.message, true); }
    finally { state.polling.delete(id); }
  }

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
      const q = await api(`api/ask/${id}`);
      $("#conversation").prepend(questionNode(q));          // newest first, just under the ask box
      markDays();
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
    const nodes = r.questions.map(questionNode);              // newest first, as the server sends them
    if (more) {
      const btn = $("#olderBtn");
      if (btn) btn.remove();
      box.append(...nodes);
    } else {
      mount(clear(box), nodes);
    }
    if (r.questions.length) state.oldest = r.questions[r.questions.length - 1].askedAt;
    if (r.more) {
      const older = h("button", { type: "button", class: "link-btn", id: "olderBtn" }, "Earlier questions");
      older.addEventListener("click", () => loadHistory(true));
      box.append(older);
    }
    markDays();
    r.questions.filter((q) => RUNNING.includes(q.state)).forEach((q) => poll(q.id));
  }

  // Always a few suggestions, in one row under the ask box (it scrolls sideways on a phone).
  function showSuggestions(list, why) {
    mount(clear($("#suggestions")), (list || []).slice(0, 4).map((s) => {
      const habit = why && why[s];
      const b = h("button", { type: "button", class: habit ? "chip habit" : "chip", title: habit || null }, s);
      b.addEventListener("click", () => { $("#askInput").value = s; ask(s); });
      return b;
    }));
  }

  $("#clearBtn").addEventListener("click", async () => {
    if (!(await UI.confirmDialog("Clear my questions", "Delete all your questions and answers now?", { okLabel: "Clear" }))) return;
    try { await api("api/history", { method: "DELETE" }); await loadHistory(false); }
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

  // ---------- the briefings (per person: morning and evening on/off and time, days, a speaker; sent to their phones) ----------
  const PART_WORDS = { "todo.tasks": "today's tasks and schedule", "tree.birthdays": "birthdays this week",
    "finance.bills": "bills due in the next 3 days", "splitpot.balances": "money owed in Splitpot" };
  const EVENING_WORDS = { "todo.tasks": "tomorrow's tasks and schedule", "tree.birthdays": "birthdays today and tomorrow",
    "finance.bills": "bills due by tomorrow" };

  async function openBriefing() {
    let b;
    try { b = await api("api/briefing"); } catch (e) { toast(e.message, true); return; }
    const on = h("input", { type: "checkbox", id: "brOn" });
    on.checked = b.on;
    const time = h("input", { type: "time", id: "brTime", value: b.time, step: "60" });
    const days = h("select", { id: "brDays" }, h("option", { value: "every" }, "Every day"), h("option", { value: "weekdays" }, "Weekdays"));
    days.value = b.days;
    const evOn = h("input", { type: "checkbox", id: "brEvOn" });
    evOn.checked = b.eveningOn;
    const evTime = h("input", { type: "time", id: "brEvTime", value: b.eveningTime, step: "60" });
    const list = (parts, words) => parts.length
      ? h("ul", null, parts.map((p) => h("li", null, words[p.tool] || p.tool, h("span", { class: "dim" }, ` · ${p.appName}`))))
      : h("p", { class: "dim" }, "No household app you use answers these yet (Todo, Family Tree, Finance, Splitpot).");
    const phones = b.phones.length
      ? h("p", null, "Sent to: " + b.phones.map((p) => p.label).join(", "))
      : h("p", { class: "note warn" }, "No phone is linked to you in Home Assistant yet: Settings → People → you → Track device (the Home Assistant app on your phone).");
    const speaker = h("select", { id: "brSpeaker" }, h("option", { value: "" }, "No speaker"),
      b.speakers.map((p) => h("option", { value: p.entity }, p.name)));
    if (b.speaker && !b.speakers.some((p) => p.entity === b.speaker)) speaker.appendChild(h("option", { value: b.speaker }, b.speaker));
    speaker.value = b.speaker || "";
    const save = h("button", { type: "button", class: "btn-primary" }, "Save");
    const nowBtn = (kind, label) => h("button", { type: "button", class: "link-btn send-now", dataset: { kind } }, label);
    const now = nowBtn("morning", "Send a morning one now"), nowEv = nowBtn("evening", "Send an evening one now");
    const body = h("div", { class: "briefing" },
      h("p", { class: "hint" }, "The assistant asks the household apps about your day and sends a short summary to your phone. It needs no AI model, and appears in your questions too."),
      b.why ? h("p", { class: "note warn" }, b.why) : null,
      h("label", { class: "check" }, on, " 🌅 A morning briefing"),
      h("div", { class: "row" }, h("label", { for: "brTime" }, "At "), time, " ", days),
      list(b.parts, PART_WORDS),
      h("p", { class: "send-row" }, now),
      h("label", { class: "check" }, evOn, " 🌙 An evening briefing, for tomorrow"),
      h("div", { class: "row" }, h("label", { for: "brEvTime" }, "At "), evTime, h("span", { class: "dim" }, " (the same days)")),
      list(b.eveningParts, EVENING_WORDS),
      h("p", { class: "send-row" }, nowEv),
      h("h4", null, "Where it goes"), phones,
      h("div", { class: "row" }, h("label", { for: "brSpeaker" }, "Also read it aloud on "), speaker),
      b.tts ? null : h("p", { class: "dim" }, "Reading aloud needs a text-to-speech engine in Home Assistant (Settings → Voice assistants)."),
      b.quiet && b.quiet.from && b.quiet.until && b.quiet.from !== b.quiet.until
        ? h("p", { class: "dim" }, `Never read aloud between ${b.quiet.from} and ${b.quiet.until} (quiet hours); your phone still gets it.`) : null,
      h("div", { class: "actions" }, save));
    const m = UI.openModal("🌅 Briefings", body);
    save.addEventListener("click", async () => {
      try {
        await api("api/briefing", { method: "PUT", body: { on: on.checked, time: time.value, days: days.value,
          eveningOn: evOn.checked, eveningTime: evTime.value, speaker: speaker.value } });
        const said = [on.checked ? `morning at ${time.value}` : null, evOn.checked ? `evening at ${evTime.value}` : null].filter(Boolean);
        toast(said.length ? `Briefings: ${said.join(", ")}.` : "Briefings off.");
        m.close();
      } catch (e) { toast(e.message, true); }
    });
    for (const btn of [now, nowEv]) {
      btn.addEventListener("click", async () => {
        const label = btn.textContent;
        btn.disabled = true;
        btn.textContent = "Asking the apps…";
        try {
          const r = await api(`api/briefing/send?kind=${btn.dataset.kind}`, { method: "POST" });
          const ok = Object.values(r.sent).filter(Boolean).length;
          const aloud = r.quiet ? " Not read aloud: it's quiet hours." : r.spoken === null ? "" : r.spoken ? " Read aloud too." : " Reading it aloud failed.";
          toast((r.phones.length ? `Sent to ${ok} of ${r.phones.length} phone${r.phones.length === 1 ? "" : "s"}.` : "Made — it's in your questions (no phone to send it to).") + aloud,
            (r.phones.length > 0 && ok === 0) || r.spoken === false);
          m.close();
          await loadHistory(false);
        } catch (e) { toast(e.message, true); btn.disabled = false; btn.textContent = label; }
      });
    }
  }
  $("#briefingBtn").addEventListener("click", openBriefing);

  // ---------- you: the theme, and how the app sees you (the name button in the top bar) ----------
  function initials(name) {
    const words = String(name || "").split(/\s+/).filter(Boolean);
    return words.slice(0, 2).map((w) => [...w][0].toUpperCase()).join("") || "?";
  }

  async function openWhoami() {
    const body = h("div");
    UI.loading(body);
    const theme = h("select", { id: "theme-select", class: "theme-select", "aria-label": "Theme" });
    HouseholdTheme.bindSelect(theme);
    UI.openModal("You", h("div", { class: "user-menu" },
      h("div", { class: "theme-row" }, h("label", { for: "theme-select" }, "Theme"), theme),
      h("h4", null, "How the app sees you"), body));
    try {
      const w = await api("api/whoami");
      mount(clear(body), HouseholdWhoami.panel(w, { appName: "Household Assistant", classes: { copy: "btn-ghost" } }));
    } catch (e) { mount(clear(body), UI.errorBox(e)); }
  }
  $("#userBtn").addEventListener("click", openWhoami);

  // ---------- admin: #/admin/<tab> (a reload keeps the tab; Back returns to where you came from) ----------
  const ADMIN_TABS = ["apps", "settings", "quick", "people", "usage", "storage"];
  let currentTab = "apps";

  function showView(name) {
    $("#askView").hidden = name !== "ask";
    $("#adminView").hidden = name !== "admin";
    const ab = $("#adminBtn");
    ab.classList.toggle("active", name === "admin");
    if (name === "admin") ab.setAttribute("aria-current", "page"); else ab.removeAttribute("aria-current");
  }

  function route() {
    const r = location.hash.replace(/^#/, "") || "/";
    const m = /^\/admin(?:\/([a-z]+))?$/.exec(r);
    if (m && state.me && state.me.user.isAdmin) {
      showView("admin");
      openTab(ADMIN_TABS.includes(m[1]) ? m[1] : "apps");
      return;
    }
    showView("ask");
  }

  $("#homeBtn").addEventListener("click", () => BackNav.go("#/"));
  $("#adminBtn").addEventListener("click", () => BackNav.go(`#/admin/${currentTab}`));
  $("#adminBackBtn").addEventListener("click", () => { if (!BackNav.back()) BackNav.go("#/"); });
  $$("#adminTabs .tab").forEach((b) => b.addEventListener("click", () => BackNav.replace(`#/admin/${b.dataset.tab}`)));

  function openTab(tab) {
    currentTab = tab;
    $$("#adminTabs .tab").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle("active", on);
      if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    $$(".tab-body").forEach((s) => { s.hidden = s.id !== `tab-${tab}`; });
    ({ apps: loadApps, settings: loadSettings, quick: loadQuick, people: loadPeople, usage: loadUsage, storage: loadStorage })[tab](true);
  }

  // A spinner while a tab loads: when it's opened, or when it has nothing yet (a re-draw after a change keeps the old one).
  function busy(box, fresh) { if (fresh || !box.firstChild) UI.loading(box); }
  const failed = (box, e, retry) => mount(clear(box), UI.errorBox(e, retry));
  const num = (n) => Number(n || 0).toLocaleString();
  const kpi = (label, value) => h("div", { class: "kpi" }, h("span", { class: "kpi-label" }, label), h("span", { class: "kpi-value" }, value));
  const tableWrap = (table) => h("div", { class: "table-wrap" }, table);
  const about = (line, more) => [h("p", { class: "hint" }, line), h("details", { class: "about" }, h("summary", null, "More"), h("p", null, more))];

  async function loadApps(fresh) {
    const box = $("#tab-apps");
    busy(box, fresh);
    let r, ca;
    try { [r, ca] = await Promise.all([api("api/admin/tools"), api("api/admin/connected-apps").catch(() => null)]); }
    catch (e) { failed(box, e, () => loadApps(true)); return; }
    const refresh = h("button", { type: "button", class: "btn-secondary" }, "Refresh");
    refresh.addEventListener("click", async () => {
      try { const a = await api("api/admin/tools/refresh", { method: "POST" }); toast(a.asked.length ? `Asked ${a.asked.length} app(s) for their tools.` : "No connected app answers the assistant yet."); setTimeout(loadApps, 2000); }
      catch (e) { toast(e.message, true); }
    });
    const apps = r.apps.slice().sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0));    // the inactive ones last
    const toolCount = r.apps.reduce((n, a) => n + a.tools.length, 0);
    const cards = apps.map((a) => {
      const sw = h("input", { type: "checkbox", checked: a.enabled });
      sw.addEventListener("change", async () => {
        try { await api(`api/admin/tools/${encodeURIComponent(a.app)}`, { method: "PUT", body: { enabled: sw.checked } }); }
        catch (e) { sw.checked = !sw.checked; toast(e.message, true); }
      });
      const notes = [];
      if (!a.appOn) notes.push(`Its own Answer the Household Assistant is off (in ${a.name}'s App settings).`);
      if (!a.active) notes.push("Not heard from in the last day.");
      if (a.error) notes.push(a.error);
      const n = a.tools.length;
      return h("div", { class: "card app-card" + (a.active ? "" : " greyed") },
        h("label", { class: "app-head" }, sw, " ", h("strong", null, a.name), a.version ? h("span", { class: "dim" }, ` ${a.version}`) : null),
        notes.map((x) => h("p", { class: "hint warn-text" }, x)),
        n ? h("details", { class: "tool-fold" }, h("summary", null, `${n} tool${n === 1 ? "" : "s"}`),
          h("ul", { class: "tool-list" }, a.tools.map((t) => h("li", null, t.what, t.acts ? h("span", { class: "dim" }, " — changes data, only after a tap") : null))))
          : h("p", { class: "hint" }, "No tools offered."));
    });
    mount(clear(box),
      about("The apps that answer the assistant, and what each offers.",
        "Both switches must be on: this one, and the app's own Answer the Household Assistant (its App settings)."),
      r.apps.length ? h("div", { class: "summary-strip" }, kpi("Apps", num(r.apps.length)),
        kpi("Active", num(r.apps.filter((a) => a.active).length)), kpi("Tools", num(toolCount))) : null,
      h("p", null, refresh),
      cards.length ? cards : UI.empty("No app has offered its tools yet", "Apps that answer say hello on Home Assistant's event bus; then Refresh."),
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

  async function loadSettings(fresh) {
    const box = $("#tab-settings");
    busy(box, fresh);
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
    } catch (e) { failed(box, e, () => loadSettings(true)); }
  }

  // ---------- Admin → Quick answers (§17.8, §17.11): Test, your phrases, what wasn't answered quickly ----------
  const SOURCE_WORDS = { admin: "added here", tapped: "from a choice", rephrased: "asked again in other words", ai: "taught by the AI" };

  async function loadQuick(fresh) {
    const box = $("#tab-quick");
    busy(box, fresh);
    let ph, ms, its, wd;
    try {
      [ph, ms, its, wd] = await Promise.all([api("api/admin/quick/phrases"), api("api/admin/quick/misses"),
        api("api/admin/quick/intents"), api("api/admin/quick/words")]);
    } catch (e) { failed(box, e, () => loadQuick(true)); return; }
    const intentName = Object.fromEntries(its.intents.map((i) => [i.intent, i.question]));

    // Test: how a question is understood (calls nothing)
    const testIn = h("input", { type: "text", maxlength: "1000", placeholder: "e.g. whats on the costco list tmrw", "aria-label": "A question to test" });
    const testOut = h("div", { class: "quick-test" });
    const testBtn = h("button", { type: "button", class: "btn-secondary" }, "Test");
    const runTest = async () => {
      if (!testIn.value.trim()) return;
      try {
        const r = await api("api/admin/quick/test", { method: "POST", body: { text: testIn.value } });
        const verdict = { answer: "Answered quickly", choices: "Did you mean (a close call)", none: "Not placed", model: "Needs the AI" }[r.result] || r.result;
        mount(clear(testOut),
          h("p", null, h("strong", null, verdict), r.understood.length ? ": " + r.understood.map((u) => u.label).join(" · ") : ""),
          r.learned ? h("p", { class: "dim" }, `A learned phrase decides it (${SOURCE_WORDS[r.learned.source] || r.learned.source}).`) : null,
          h("pre", null, [
            `words: ${r.words.join(" ")}`,
            `pattern: ${r.template}`,
            `concepts: ${Object.entries(r.concepts).map(([k, v]) => `${k} (${v.join(", ")})`).join("; ") || "—"}`,
            `names: ${Object.entries(r.names).map(([k, v]) => `${k}: ${v.join(", ")}`).join("; ") || "—"}`,
            `when: ${Object.entries(r.slots).map(([k, v]) => `${k} ${v}`).join(", ") || "—"}`,
            `scores: ${r.scores.slice(0, 6).map((x) => `${x.intent} ${x.score}`).join(", ") || "—"}`,
            ...r.understood.map((u) => `calls: ${u.calls.map((c) => `${c.tool} ${JSON.stringify(c.args)}`).join("; ")}`),
          ].join("\n")));
      } catch (e) { mount(clear(testOut), h("p", { class: "error" }, e.message)); }
    };
    testBtn.addEventListener("click", runTest);
    testIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runTest(); } });

    // Add a phrase
    const words = h("input", { type: "text", maxlength: "200", placeholder: "e.g. trash day", "aria-label": "The words" });
    const intent = h("select", { "aria-label": "What they mean" }, its.intents.filter((i) => !i.acts).map((i) =>
      h("option", { value: i.intent, disabled: !i.available }, i.question + (i.available ? "" : " (no app answers it now)"))));
    const fixed = h("input", { type: "text", maxlength: "200", placeholder: "optional: group=Flat, days=14", "aria-label": "Fixed values" });
    const add = h("button", { type: "button", class: "btn-primary" }, "Add phrase");
    add.addEventListener("click", async () => {
      const args = {};
      for (const part of fixed.value.split(",").map((x) => x.trim()).filter(Boolean)) {
        const [k, ...v] = part.split("=");
        const val = v.join("=").trim();
        args[k.trim()] = /^-?\d+(\.\d+)?$/.test(val) ? Number(val) : val;
      }
      try {
        const r = await api("api/admin/quick/phrases", { method: "POST", body: { words: words.value, intent: intent.value, args } });
        toast(`Added: “${r.phrase}”.`);
        loadQuick();
      } catch (e) { toast(e.message, true); }
    });

    const phraseRows = ph.phrases.map((p) => {
      const del = h("button", { type: "button", class: "btn-ghost", title: "Remove" }, "✕");
      del.addEventListener("click", async () => {
        try { await api(`api/admin/quick/phrases/${encodeURIComponent(p.id)}`, { method: "DELETE" }); loadQuick(); }
        catch (e) { toast(e.message, true); }
      });
      const fixedArgs = Object.entries(p.args).filter(([k]) => !p.slot_args.includes(k)).map(([k, v]) => `${k}=${v}`).join(", ");
      return h("tr", null, h("td", null, `“${p.phrase}”`), h("td", null, intentName[p.intent] || p.intent, fixedArgs ? h("span", { class: "dim" }, ` (${fixedArgs})`) : null),
        h("td", { class: "dim" }, `${SOURCE_WORDS[p.source] || p.source}${(p.source === "ai" || p.source === "rephrased") && p.seen < 2 ? " — not used until seen twice" : ""}`),
        h("td", { class: "dim" }, `${p.hits} used`), h("td", null, del));
    });

    const missRows = ms.misses.map((m) => {
      const use = h("button", { type: "button", class: "btn-ghost" }, "Add as a phrase");
      use.addEventListener("click", () => { words.value = m.phrase; words.focus(); words.scrollIntoView({ block: "center" }); });
      const test = h("button", { type: "button", class: "btn-ghost" }, "Test");
      test.addEventListener("click", () => { testIn.value = m.phrase; runTest(); testIn.scrollIntoView({ block: "center" }); });
      return h("tr", null, h("td", null, `“${m.phrase}”`), h("td", { class: "dim" }, `${m.count}×`), h("td", null, test, " ", use));
    });
    const clearMisses = h("button", { type: "button", class: "btn-ghost" }, "Clear the list");
    clearMisses.addEventListener("click", async () => {
      try { await api("api/admin/quick/misses", { method: "DELETE" }); loadQuick(); } catch (e) { toast(e.message, true); }
    });

    // Words: a word means a kind of question, or is another name for something
    const conceptLabel = Object.fromEntries(wd.concepts.map((c) => [c.concept, c.label]));
    const meansWords = (m) => {
      const [kind, ...rest] = m.split(":");
      return rest.length ? `another name for ${kind} “${rest.join(":")}”` : conceptLabel[m] || m;
    };
    const wordIn = h("input", { type: "text", maxlength: "60", placeholder: "e.g. chores", "aria-label": "The word" });
    const meansSel = h("select", { "aria-label": "What it means" },
      h("optgroup", { label: "A kind of question" }, wd.concepts.map((c) => h("option", { value: c.concept }, c.label))),
      h("optgroup", { label: "Another name for…" }, wd.kinds.map((k) => h("option", { value: `name:${k}` }, `a ${k}`))));
    const nameIn = h("input", { type: "text", maxlength: "80", placeholder: "the name it stands for, e.g. Curd", "aria-label": "The name", hidden: true });
    meansSel.addEventListener("change", () => { nameIn.hidden = !meansSel.value.startsWith("name:"); });
    const addWord = h("button", { type: "button", class: "btn-primary" }, "Add word");
    const sendWord = async (word, means) => {
      try { await api("api/admin/quick/words", { method: "POST", body: { word, means } }); toast(`Added: “${word}”.`); loadQuick(); }
      catch (e) { toast(e.message, true); }
    };
    addWord.addEventListener("click", () => {
      const v = meansSel.value;
      sendWord(wordIn.value, v.startsWith("name:") ? `${v.slice(5)}:${nameIn.value.trim()}` : v);
    });
    const wordRows = wd.words.map((w) => {
      const del = h("button", { type: "button", class: "btn-ghost", title: "Remove" }, "✕");
      del.addEventListener("click", async () => {
        try { await api(`api/admin/quick/words/${encodeURIComponent(w.word)}`, { method: "DELETE" }); loadQuick(); }
        catch (e) { toast(e.message, true); }
      });
      return h("tr", null, h("td", null, `“${w.word}”`), h("td", null, meansWords(w.means)), h("td", null, del));
    });
    const suggestRows = wd.suggestions.map((g) => {
      const yes = h("button", { type: "button", class: "btn-secondary" }, "Yes");
      yes.addEventListener("click", () => sendWord(g.word, g.means));
      const test = h("button", { type: "button", class: "btn-ghost" }, "Test");
      test.addEventListener("click", () => { testIn.value = g.example; runTest(); testIn.scrollIntoView({ block: "center" }); });
      return h("tr", null, h("td", null, `Treat “${g.word}” as ${conceptLabel[g.means] || g.means}?`),
        h("td", { class: "dim" }, `answers ${g.placed} of ${g.misses} misses`), h("td", null, yes, " ", test));
    });

    // Replay: the last answers understood again by today's rules
    const replayOut = h("div");
    const replayBtn = h("button", { type: "button", class: "btn-secondary" }, "Replay the last 100");
    replayBtn.addEventListener("click", async () => {
      replayBtn.disabled = true;
      try {
        const r = await api("api/admin/quick/replay", { method: "POST" });
        mount(clear(replayOut),
          h("p", null, `${r.checked} checked: ${r.same} read the same, ${r.changed.length} differently.`),
          r.changed.length ? h("table", { class: "quick-table" }, h("tbody", null, r.changed.map((c) => h("tr", null,
            h("td", null, `“${c.template}”`), h("td", { class: "dim" }, `was: ${c.wasExample.join(" · ")}`),
            h("td", null, `now: ${c.nowExample.join(" · ")}`))))) : null);
      } catch (e) { mount(clear(replayOut), h("p", { class: "error" }, e.message)); }
      replayBtn.disabled = false;
    });

    mount(clear(box),
      h("div", { class: "card" }, h("h3", null, "Asked but not answered quickly"),
        h("p", { class: "hint" }, "Questions the rules couldn't place, in their cleaned-up words, with how often — never who asked. Add a phrase for the ones that should be quick."),
        missRows.length ? [tableWrap(h("table", { class: "quick-table" }, h("tbody", null, missRows))), h("p", null, clearMisses)] : h("p", { class: "dim" }, "Nothing yet.")),
      h("div", { class: "card" }, h("h3", null, "Test a question"),
        h("p", { class: "hint" }, "How a question is understood — its words after clean-up, what they mean, and which answer wins. Nothing is asked."),
        h("div", { class: "row" }, testIn, " ", testBtn), testOut),
      h("div", { class: "card" }, h("h3", null, "Phrases"),
        h("p", { class: "hint" }, "Your household's own ways of asking. Times and names in a phrase work for any time or name (“trash day” also answers “when is trash day”). Phrases learned from choices or from the AI are dropped when nobody has used them for 180 days."),
        h("div", { class: "row wrap" }, words, " ", intent, " ", fixed, " ", add),
        phraseRows.length ? tableWrap(h("table", { class: "quick-table" }, h("tbody", null, phraseRows))) : h("p", { class: "dim" }, "No phrases yet.")),
      h("div", { class: "card" }, h("h3", null, "Words"),
        h("p", { class: "hint" }, "A word your household uses for a kind of question (“chores” for tasks), or another name for something (“dahi” for the list's Curd). The words in what wasn't answered quickly are suggested here when they'd answer it."),
        suggestRows.length ? h("table", { class: "quick-table" }, h("tbody", null, suggestRows)) : null,
        h("div", { class: "row wrap" }, wordIn, " ", meansSel, " ", nameIn, " ", addWord),
        wordRows.length ? h("table", { class: "quick-table" }, h("tbody", null, wordRows)) : h("p", { class: "dim" }, "No words yet.")),
      h("div", { class: "card" }, h("h3", null, "Replay"),
        h("p", { class: "hint" }, "Reads the household's last 100 quick answers again with today's rules, phrases and words, and lists the ones that would now be understood differently — handy after an update. Nothing is asked; each is shown as its pattern, never with who asked."),
        h("p", null, replayBtn), replayOut));
  }

  async function loadPeople(fresh) {
    const box = $("#tab-people");
    busy(box, fresh === true);
    let r;
    try { r = await api("api/admin/people"); } catch (e) { failed(box, e, () => loadPeople(true)); return; }
    const row = (p) => {
      const on = h("input", { type: "checkbox", checked: p.enabled });
      const child = h("input", { type: "checkbox", checked: p.isChild });
      const send = async (body, el) => {
        try { await api(`api/admin/people/${encodeURIComponent(p.id)}`, { method: "PUT", body }); }
        catch (e) { el.checked = !el.checked; toast(e.message, true); }
      };
      on.addEventListener("change", () => send({ enabled: on.checked }, on).then(() => loadPeople()));
      child.addEventListener("change", () => send({ isChild: child.checked }, child).then(() => loadPeople()));
      let isNew = null;
      if (p.new) {
        const ok = h("button", { type: "button", class: "btn-ghost" }, "Looks right");
        ok.addEventListener("click", () => send({ reviewed: true }, ok).then(() => loadPeople()));
        isNew = h("span", null, h("span", { class: "badge-new", title: "New since an admin last looked: check May ask and Child" }, "New"), " ", ok);
      }
      // the words beside each box are for phones, where the header row is hidden
      return h("tr", { class: p.new ? "new-person" : "" }, h("td", { class: "person-name" }, p.name, isNew ? " " : null, isNew),
        h("td", null, h("label", null, on, h("span", { class: "cell-label" }, " May ask"))),
        h("td", null, h("label", null, child, h("span", { class: "cell-label" }, " Child"))),
        h("td", null, PeopleData.menu({ id: p.id, name: p.name }, dataCfg)));
    };
    // Export, import or delete one person's data (the shared dialogs, common/person-data.js)
    const dataCfg = { people: r.people.map((p) => ({ id: p.id, name: p.name })), onDone: () => loadPeople() };
    const people = r.people.slice().sort((a, b) => (b.new ? 1 : 0) - (a.new ? 1 : 0));     // the new ones first
    const newCount = people.filter((p) => p.new).length;
    mount(clear(box), h("div", { class: "card" },
      about("Everyone who has opened the assistant.",
        "A child asks only when App settings → Children may ask is on, and then only about their own things. Nobody, admins included, can see anyone else's questions — unless an admin exports a person's data with their questions and answers ticked, which the app's log records."),
      newCount ? h("p", { class: "note warn" }, `${newCount} new ${newCount === 1 ? "person" : "people"}: everyone new may ask and counts as an adult until you change it. Check them, then tap Looks right.`) : null,
      h("p", null, PeopleData.importButton(dataCfg)),
      people.length
        ? h("table", { class: "people" }, h("thead", null, h("tr", null, ["Person", "May ask", "Child", "Data"].map((t) => h("th", { scope: "col" }, t)))),
          h("tbody", null, people.map(row)))
        : UI.empty("Nobody yet", "People appear here once they open the assistant.")));
  }

  async function loadUsage(fresh) {
    const box = $("#tab-usage");
    busy(box, fresh);
    let r, sp, us;
    try {
      [r, sp, us] = await Promise.all([api("api/admin/usage"), api("api/admin/quick/apps"), api("api/admin/quick/usage")]);
    } catch (e) { failed(box, e, () => loadUsage(true)); return; }
    const total = r.days.reduce((t, d) => ({ q: t.q + d.questions, c: t.c + d.calls, i: t.i + d.input_tokens, o: t.o + d.output_tokens }), { q: 0, c: 0, i: 0, o: 0 });
    const days = r.days.filter((d) => d.questions || d.calls || d.input_tokens || d.output_tokens);   // days with no activity left out
    const most = Math.max(1, ...days.map((d) => d.questions));
    const dayRows = days.map((d) => h("tr", null, h("td", null, d.day),
      h("td", { class: "bar-cell", "aria-hidden": "true" }, h("span", { class: "bar", style: `width: ${Math.round((d.questions / most) * 100)}%` })),
      [d.questions, d.calls, d.input_tokens, d.output_tokens].map((v) => h("td", { class: "num" }, num(v)))));

    // How fast each app answers
    const secs = (x) => (x === null || x === undefined ? "—" : `${x.toFixed(x < 10 ? 1 : 0)} s`);
    const speedRows = sp.apps.map((a) => h("tr", { class: a.timeouts || (a.slow !== null && a.slow > 5) ? "slow-app" : "" },
      h("td", null, a.appName), h("td", null, num(a.calls)), h("td", null, secs(a.typical)), h("td", null, secs(a.slow)),
      h("td", null, num(a.refused)), h("td", null, num(a.timeouts))));

    // Who asks, and what the household asks (counts only)
    const peopleRows = us.people.filter((p) => p.asked).map((p) => h("tr", null, h("td", null, p.name), h("td", null, num(p.asked)),
      h("td", null, p.asked ? `${Math.round((p.quick / p.asked) * 100)}%` : "—")));
    const kindRows = us.kinds.slice(0, 12).map((k) => h("tr", null, h("td", null, k.example), h("td", null, num(k.count))));

    mount(clear(box),
      h("div", { class: "summary-strip" }, kpi("Questions · 30 days", num(total.q)), kpi("Tool calls", num(total.c)),
        kpi("Tokens in", num(total.i)), kpi("Tokens out", num(total.o))),
      h("div", { class: "card" }, h("h3", null, "Each day"),
        r.ai && r.ai.model ? h("p", { class: "hint" }, `Model: ${r.ai.providerLabel || r.ai.provider} ${r.ai.model}.`) : null,
        days.length
          ? tableWrap(h("table", { class: "usage" }, h("thead", null, h("tr", null, ["Day", "", "Questions", "Tool calls", "Tokens in", "Tokens out"].map((t) => h("th", null, t)))),
            h("tbody", null, dayRows)))
          : h("p", { class: "dim" }, "No questions in the last 30 days.")),
      h("div", { class: "card" }, h("h3", null, "How fast the apps answer"),
        h("p", { class: "hint" }, `The last ${sp.days} days: each app's calls, its typical and its slowest answers (9 in 10 were faster), and how many it refused or didn't answer in time.`),
        speedRows.length ? tableWrap(h("table", { class: "quick-table speeds" }, h("thead", null, h("tr", null, ["App", "Calls", "Typical", "Slow", "Refused", "Timed out"].map((t) => h("th", null, t)))),
          h("tbody", null, speedRows))) : h("p", { class: "dim" }, "No calls yet.")),
      h("div", { class: "card" }, h("h3", null, "Who asks"),
        h("p", { class: "hint" }, `The last ${us.days} days: how many questions each person asked and how many had a quick answer — counts only, never what anyone asked. The kinds of question are the household's, all together.`),
        peopleRows.length ? h("table", { class: "quick-table" }, h("thead", null, h("tr", null, ["Person", "Questions", "Quick"].map((t) => h("th", null, t)))),
          h("tbody", null, peopleRows)) : h("p", { class: "dim" }, "No questions yet."),
        kindRows.length ? h("table", { class: "quick-table" }, h("thead", null, h("tr", null, ["Asked most", "Times"].map((t) => h("th", null, t)))),
          h("tbody", null, kindRows)) : null));
  }

  // ---------- Admin → Storage: backups, the quick answers' phrases and words, and (apart) Restore ----------
  function loadStorage() {
    const importIn = h("input", { type: "file", accept: ".json,application/json", hidden: true });
    importIn.addEventListener("change", async () => {
      const file = importIn.files[0];
      importIn.value = "";
      if (!file) return;
      try {
        const body = JSON.parse(await file.text());
        const r = await api("api/admin/quick/import", { method: "POST", body });
        toast(`Imported ${r.phrases} phrases and ${r.words} words${r.skipped ? `; ${r.skipped} skipped` : ""}.`);
      } catch (e) { toast(e.message || "That file couldn't be read.", true); }
    });
    mount(clear($("#storageQuick")),
      h("div", { class: "card" }, h("h3", null, "Move to another install"),
        h("p", { class: "hint" }, "The quick answers' phrases and words as a file, to bring to another Household Assistant (or keep). Phrases are patterns: no names of who asked. Importing adds to the ones here."),
        h("p", { class: "btn-row" }, h("a", { class: "btn-secondary", href: "api/admin/quick/export" }, "Download phrases and words"), " ",
          h("label", { class: "btn-secondary" }, "Import a file… ", importIn))));
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
    $("#userBtn .user-name").textContent = me.user.name;
    $("#userBtn .user-initials").textContent = initials(me.user.name);
    $("#userBtn").setAttribute("aria-label", `${me.user.name}: theme, and how the app sees you`);
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
    const qo = $("#quickOnly");
    qo.hidden = !(me.canAsk && me.quickOnly);
    $("#askInput").disabled = !me.canAsk;
    $("#askBtn").disabled = !me.canAsk;
    showSuggestions(me.suggestions, me.suggestionWhy);
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
    goHome: () => { try { history.replaceState(history.state, "", "#/"); } catch (e) { /* sandboxed */ } route(); },
    openLayers: () => $$("#modalRoot > *"),
    closeLayer: (layer) => { const x = layer.querySelector(".modal-close, [aria-label='Close']"); if (x) x.click(); else layer.remove(); },
    isTab: (hash) => hash === "" || hash === "#" || hash === "#/" || hash === "#/admin",
    parentOf: (hash) => (/^#\/admin\/[a-z]+$/.test(hash) ? "#/admin" : null),
  });
  window.addEventListener("hashchange", route);

  (async () => {
    try {
      await loadMe();
      route();                                         // #/admin/<tab> after a reload (admins)
      await loadHistory(false);
      const q = typedQuestion();
      if (q && state.me.canAsk) { $("#askInput").value = q; autosize(); $("#askInput").focus(); }
    } catch (e) {
      mount(clear($("#conversation")), h("div", { class: "note warn" }, h("h3", null, "Can't identify a Home Assistant user"),
        h("p", null, e.message), h("p", null, "Open the assistant from its page in the Home Assistant sidebar.")));
    }
  })();
})();
